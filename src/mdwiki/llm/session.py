"""Session provider — completions through a local agent CLI.

The ``session`` provider shells out to Claude Code (``claude -p``), OpenAI
Codex (``codex exec``), or another command via ``cli = "custom"``. Built-in
CLIs use subscription login verification by default. The same wiki can be
ingested and queried through the user's configured agent CLI.

Session mode provides these safeguards:

1. Known API credential, gateway and cloud-provider environment variables are
   removed from the child process, except configured ``keep_env`` exemptions
   and variables needed for subscription authentication.
2. With ``verify_subscription`` enabled, built-in CLIs must report an accepted
   subscription login before the first completion.
3. The provider never falls back to another provider.

A ``custom`` command is scrubbed the same way but its login is not checked.
``verify_subscription = false`` disables verification, and ``keep_env`` can
retain paid credentials or gateway settings. CLI-managed credentials and
organization policy are outside this provider's control; it cannot verify
billing outcomes.

Native batch and tool-use constrained decoding are unsupported. Orchestration
routes on ``supports_batch`` / ``supports_tool_use``, so ingest uses the
free-form JSON plan path and bootstrap uses synchronous ingest.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mdwiki.llm.anthropic import OutputTruncatedError
from mdwiki.llm.base import CompleteResult, Message, PingResult, Provider

SessionErrorKind = Literal["not-installed", "not-logged-in", "config", "rate-limited", "timeout", "failed"]
Runner = Callable[..., "subprocess.CompletedProcess[str]"]

KNOWN_SESSION_CLIS: tuple[str, ...] = ("claude", "codex", "custom")
DEFAULT_TIMEOUT_SECONDS: float = 1500.0
DEFAULT_MAX_OUTPUT_TOKENS: int = 32000

DEFAULT_ACCEPTED_AUTH_METHODS: tuple[str, ...] = ("claude.ai",)

# Variables that carry API credentials, redirect a CLI to a gateway or cloud
# provider, or leak the parent agent session into the child. Matched without
# regard to case, by exact name or by prefix.
STRIPPED_ENV_NAMES: frozenset[str] = frozenset({"CLAUDECODE", "CLOUD_ML_REGION"})
STRIPPED_ENV_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_",
    "OPENAI_",
    "CODEX_",
    "CLAUDE_CODE_",
    "AWS_",
    "GOOGLE_",
    "AZURE_",
    "VERTEX",
)
# Needed for the CLI to find its subscription login; never stripped.
KEPT_ENV_NAMES: frozenset[str] = frozenset({"CODEX_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN"})

_DEFAULT_MODELS: dict[str, str | None] = {"claude": "sonnet", "codex": None, "custom": None}
_RECOVERABLE_KINDS: frozenset[str] = frozenset({"timeout", "failed"})
_SYSTEM_FILE_NAME = "system.txt"
_IMAGE_FILE_STEM = "image"
_RATE_LIMIT_MARKERS: tuple[str, ...] = ("usage limit", "rate limit", "rate-limit", "limit reached", "too many requests")
_LOGIN_MARKERS: tuple[str, ...] = ("/login", "not logged in", "logged out", "invalid api key", "unauthorized", "codex login", "sign in")
_MODEL_MARKERS: tuple[str, ...] = ("model is not supported", "model metadata", "selected model", "unknown model")
_LAST_MESSAGE_FILE_NAME = "last-message.txt"
_TRUNCATION_MARKER = "output token maximum"
_IMAGE_SYSTEM_PROMPT = (
    "You transcribe images into markdown for a knowledge base. Reproduce all visible text exactly, "
    "preserve headings, lists and tables, and describe any figure in one sentence. "
    "Output only the markdown, with no commentary."
)
_FLATTEN_SUFFIX = "Respond to the final user turn."


class SessionCliError(RuntimeError):
    """Raised when the agent CLI cannot produce a completion.

    Parameters
    ----------
    message : str
        Human-readable description, including the remediation where one exists.
    kind : SessionErrorKind
        ``not-installed``, ``not-logged-in``, ``config`` and ``rate-limited`` are
        systemic: every source would fail the same way, so bulk ingest aborts.
        ``timeout`` and ``failed`` are per-call and bulk ingest continues.
    """

    def __init__(self, message: str, *, kind: SessionErrorKind) -> None:
        super().__init__(message)
        self.kind: SessionErrorKind = kind


@dataclass(frozen=True)
class _Invocation:
    """One fully-resolved CLI call."""

    argv: list[str]
    prompt: str
    extra_env: dict[str, str]
    last_message_file: Path | None = None


def run_subprocess(
    argv: list[str],
    *,
    input: str | None,  # noqa: A002 - mirrors subprocess.run
    env: dict[str, str],
    cwd: str,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` without a shell, in its own process group, and capture its output.

    An agent CLI spawns helper processes. On timeout, cancellation or another
    communication error, kill the whole group and reap the child with bounded
    cleanup before propagating the original exception.

    Raises
    ------
    FileNotFoundError
        ``argv[0]`` is not installed.
    subprocess.TimeoutExpired
        The command ran longer than ``timeout`` seconds.
    """
    process = subprocess.Popen(  # noqa: S603 - argv is built from config, never from model output
        argv,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except BaseException:
        _kill_process_group(process)
        raise
    return subprocess.CompletedProcess(args=argv, returncode=process.returncode, stdout=stdout, stderr=stderr)


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        # start_new_session makes the child's PID its group ID. Use it even
        # when the leader has exited but helpers still hold the output pipes.
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        with suppress(OSError):
            process.kill()
    # Cleanup errors must not mask the original cancellation/failure. Waiting
    # separately also reaps the child if draining an output pipe raises.
    with suppress(Exception, KeyboardInterrupt):
        process.communicate(timeout=5)
    with suppress(Exception, KeyboardInterrupt):
        process.wait(timeout=5)


def scrub_environment(
    base_env: Mapping[str, str],
    *,
    extra_names: Sequence[str] = (),
    keep_names: Sequence[str] = (),
) -> dict[str, str]:
    """Return a copy of ``base_env`` without credentials, redirects or parent-session variables.

    Parameters
    ----------
    base_env : Mapping[str, str]
        Environment to start from, normally ``os.environ``.
    extra_names : Sequence[str], optional
        Additional variable names to remove (``[llm.session].strip_env``).
    keep_names : Sequence[str], optional
        Names exempt from removal (``[llm.session].keep_env``), for a custom CLI
        that needs a variable the default rules strip.
    """
    blocked = {name.upper() for name in STRIPPED_ENV_NAMES} | {name.upper() for name in extra_names}
    kept = {name.upper() for name in KEPT_ENV_NAMES} | {name.upper() for name in keep_names}

    def is_allowed(name: str) -> bool:
        upper = name.upper()
        if upper in kept:
            return True
        return upper not in blocked and not upper.startswith(STRIPPED_ENV_PREFIXES)

    return {name: value for name, value in base_env.items() if is_allowed(name)}


def toml_assignment(key: str, value: str) -> str:
    """Render ``key="value"`` as a TOML assignment with the value escaped as a basic string."""
    return f"{key}={json.dumps(value)}"


def flatten_messages(messages: Sequence[Message]) -> str:
    """Render a chat history as the single prompt an agent CLI accepts."""
    if len(messages) == 1:
        return messages[0].content
    turns = "\n\n".join(f"<{m.role}_turn>\n{m.content}\n</{m.role}_turn>" for m in messages)
    return f"{turns}\n\n{_FLATTEN_SUFFIX}"


class SessionProvider(Provider):
    """Provider backed by a locally installed agent CLI."""

    name: str = "session"
    supports_batch: bool = False
    supports_tool_use: bool = False

    def __init__(
        self,
        *,
        cli: str = "claude",
        model: str | None = None,
        command: Sequence[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        verify_subscription: bool = True,
        accepted_auth_methods: Sequence[str] = DEFAULT_ACCEPTED_AUTH_METHODS,
        strip_env: Sequence[str] = (),
        keep_env: Sequence[str] = (),
        runner: Runner | None = None,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        """Build a provider that shells out to ``cli``.

        Parameters
        ----------
        cli : str
            ``"claude"``, ``"codex"`` or ``"custom"``.
        model : str, optional
            Model name passed to the CLI. Defaults to ``"sonnet"`` for claude and
            to the CLI's own default for codex and custom.
        command : Sequence[str], optional
            Command template, required when ``cli == "custom"``. The placeholders
            ``{system_file}``, ``{model}`` and ``{image}`` are substituted; the
            prompt is written to stdin and stdout is taken as the completion.
        timeout : float
            Seconds allowed per call.
        max_output_tokens : int
            Output cap passed to the claude CLI. Per-call ``max_tokens`` is not
            forwarded, because the CLI discards a truncated response.
        verify_subscription : bool
            Refuse to run unless the CLI reports a subscription login. Has no
            effect for ``custom``.
        accepted_auth_methods : Sequence[str]
            Values of ``authMethod`` from ``claude auth status`` that count as a
            subscription login. Anything else, including a missing value, is refused.
        strip_env : Sequence[str]
            Extra environment variable names to remove from the child process.
        keep_env : Sequence[str]
            Variable names exempt from removal.
        runner : callable, optional
            Replacement for ``subprocess.run`` (test injection).
        base_env : Mapping[str, str], optional
            Environment to scrub; defaults to ``os.environ`` (test injection).

        Raises
        ------
        ValueError
            If ``cli`` is unknown, or ``cli == "custom"`` without ``command``.
        """
        if cli not in KNOWN_SESSION_CLIS:
            known = ", ".join(KNOWN_SESSION_CLIS)
            raise ValueError(f"Unknown session cli {cli!r}. Known values: {known}.")
        if cli == "custom" and not command:
            raise ValueError("session cli 'custom' requires a command template, e.g. ['my-agent', '--system', '{system_file}'].")
        self.cli = cli
        self._model = model or _DEFAULT_MODELS[cli]
        self.model = self._model or f"{cli} default"
        self._command = list(command or ())
        self._timeout = timeout
        self._max_output_tokens = max_output_tokens
        self._verify_subscription = verify_subscription and cli != "custom"
        self._accepted_auth_methods = tuple(accepted_auth_methods)
        self._strip_env = tuple(strip_env)
        self._keep_env = tuple(keep_env)
        self._runner: Runner = runner or run_subprocess
        self._base_env = base_env
        self._subscription_verified = False
        self._claude_restricted_verified = False

    def is_recoverable_error(self, exc: Exception) -> bool:
        """Return whether ``exc`` affects one call rather than every call."""
        return isinstance(exc, SessionCliError) and exc.kind in _RECOVERABLE_KINDS

    def ping(self) -> PingResult:
        """Verify the CLI is installed, signed in, and able to answer."""
        start = time.perf_counter()
        try:
            self.complete(system="Reply with the single word pong.", messages=[Message(role="user", content="ping")], max_tokens=16)
        except Exception as exc:  # noqa: BLE001 — ping must never raise
            return PingResult(provider=self.name, model=self.model, latency_ms=_elapsed_ms(start), ok=False, message=str(exc))
        login = "subscription login verified" if self._verify_subscription else "login not verified"
        return PingResult(
            provider=self.name,
            model=self.model,
            latency_ms=_elapsed_ms(start),
            ok=True,
            message=f"pong via {self._executable()} ({login})",
        )

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> CompleteResult:
        """Run one completion through the agent CLI.

        ``max_tokens``, ``tools`` and ``tool_choice`` are accepted for interface
        compatibility and ignored; see ``max_output_tokens`` on the constructor.

        Raises
        ------
        SessionCliError
            The CLI is missing, not signed in with a subscription, rate-limited,
            timed out, failed, or returned an empty completion.
        OutputTruncatedError
            The CLI reported that the response exceeded its output cap.
        """
        return self._run(system=system, prompt=flatten_messages(messages), image=None)

    def describe_image(self, image_path: Path) -> str:
        """Transcribe an image to markdown through the agent CLI.

        Raises
        ------
        NotImplementedError
            For ``custom`` commands whose template has no ``{image}`` placeholder.
        """
        if self.cli == "custom" and not any("{image}" in token for token in self._command):
            raise NotImplementedError(
                "session cli 'custom' does not support describe_image: add an {image} placeholder to "
                "[llm.session].command, or disable [loaders.image] and [loaders.pdf].vision_fallback."
            )
        return self._run(system=_IMAGE_SYSTEM_PROMPT, prompt="Transcribe this image to markdown.", image=image_path).text

    def _run(self, *, system: str, prompt: str, image: Path | None) -> CompleteResult:
        base_env = self._base_env if self._base_env is not None else os.environ
        env = scrub_environment(base_env, extra_names=self._strip_env, keep_names=self._keep_env)
        self._ensure_subscription(env)
        with tempfile.TemporaryDirectory(prefix="mdwiki-session-") as workdir_name:
            workdir = Path(workdir_name)
            if image is not None and self.cli == "claude":
                self._ensure_claude_restricted(env=env, cwd=workdir)
            system_file = workdir / _SYSTEM_FILE_NAME
            system_file.write_text(system, encoding="utf-8")
            local_image: Path | None = None
            if image is not None:
                # Fixed name: a source called ``system.txt`` must not replace the system prompt.
                local_image = workdir / f"{_IMAGE_FILE_STEM}{image.suffix.lower()}"
                shutil.copyfile(image, local_image)
            invocation = self._build_invocation(
                system=system, prompt=prompt, system_file=system_file, image=local_image, workdir=workdir
            )
            try:
                completed = self._call(invocation.argv, prompt=invocation.prompt, env={**env, **invocation.extra_env}, cwd=workdir)
                return self._parse(completed, invocation)
            except SessionCliError:
                # The login may have expired mid-run; check again before the next call.
                self._subscription_verified = False
                raise

    def _build_invocation(self, *, system: str, prompt: str, system_file: Path, image: Path | None, workdir: Path) -> _Invocation:
        if self.cli == "claude":
            return self._claude_invocation(prompt=prompt, system_file=system_file, image=image)
        if self.cli == "codex":
            return self._codex_invocation(prompt=prompt, system_file=system_file, image=image, workdir=workdir)
        return self._custom_invocation(system=system, prompt=prompt, system_file=system_file, image=image)

    def _claude_invocation(self, *, prompt: str, system_file: Path, image: Path | None) -> _Invocation:
        argv = ["claude", "-p", "--output-format", "json", "--system-prompt-file", str(system_file)]
        if self._model:
            argv += ["--model", self._model]
        if image is None:
            argv += ["--tools", ""]
        else:
            argv += ["--restricted", "--tools", "Read", "--allowedTools", "Read"]
            prompt = f"Read the image file {image} and transcribe it to markdown."
        argv += ["--strict-mcp-config", "--setting-sources", "", "--no-session-persistence"]
        return _Invocation(argv=argv, prompt=prompt, extra_env={"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(self._max_output_tokens)})

    def _codex_invocation(self, *, prompt: str, system_file: Path, image: Path | None, workdir: Path) -> _Invocation:
        last_message_file = workdir / _LAST_MESSAGE_FILE_NAME
        # The read-only sandbox permits host reads. Disable the model's local
        # file-reading tools; OCR uses the explicit -i attachment instead.
        argv = [
            "codex",
            "exec",
            "--disable",
            "shell_tool",
            "--disable",
            "view_image",
            "--json",
            "--color",
            "never",
            "-s",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "-C",
            str(workdir),
            "-c",
            'approval_policy="never"',
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            toml_assignment("model_instructions_file", str(system_file)),
        ]
        if self._model:
            argv += ["-m", self._model]
        if image is not None:
            argv += ["-i", str(image)]
        argv += ["-o", str(last_message_file), "-"]
        return _Invocation(argv=argv, prompt=prompt, extra_env={}, last_message_file=last_message_file)

    def _custom_invocation(self, *, system: str, prompt: str, system_file: Path, image: Path | None) -> _Invocation:
        has_system_placeholder = any("{system_file}" in token for token in self._command)
        substitutions = {"{system_file}": str(system_file), "{model}": self._model or "", "{image}": str(image) if image else ""}
        substituted = [_substitute(token, substitutions) for token in self._command]
        argv = _drop_empty_options(self._command, substituted)
        if not has_system_placeholder:
            prompt = f"{system}\n\n{prompt}"
        return _Invocation(argv=argv, prompt=prompt, extra_env={})

    def _call(self, argv: list[str], *, prompt: str | None, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(argv, input=prompt, env=env, cwd=str(cwd), timeout=self._timeout)
        except FileNotFoundError as exc:
            raise SessionCliError(
                f"agent CLI {argv[0]!r} is not installed or not found on PATH. Install it, or change [llm.session].cli.",
                kind="not-installed",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SessionCliError(
                f"agent CLI {argv[0]!r} timed out after {self._timeout:.0f}s. Raise [llm.session].timeout if the source is large.",
                kind="timeout",
            ) from exc

    def _ensure_subscription(self, env: dict[str, str]) -> None:
        if not self._verify_subscription or self._subscription_verified:
            return
        with tempfile.TemporaryDirectory(prefix="mdwiki-session-") as workdir_name:
            if self.cli == "claude":
                completed = self._call(["claude", "auth", "status"], prompt=None, env=env, cwd=Path(workdir_name))
                verified = _claude_login_is_subscription(completed, accepted=self._accepted_auth_methods)
                login_command = "claude /login"
            else:
                completed = self._call(["codex", "login", "status"], prompt=None, env=env, cwd=Path(workdir_name))
                verified = _codex_login_is_subscription(completed)
                login_command = "codex login"
        if not verified:
            reported = (completed.stdout or completed.stderr).strip()[:200] or "no output"
            raise SessionCliError(
                f"{self.cli} CLI is not signed in with a subscription login (it reported: {reported}). "
                f"Run `{login_command}` and sign in with your subscription, then re-run. "
                "Session mode will not run on an API key or an unrecognized login.",
                kind="not-logged-in",
            )
        self._subscription_verified = True

    def _ensure_claude_restricted(self, *, env: dict[str, str], cwd: Path) -> None:
        """Refuse vision unless the installed CLI documents confined file tools."""
        if self._claude_restricted_verified:
            return
        completed = self._call(["claude", "--help"], prompt=None, env=env, cwd=cwd)
        help_text = " ".join(completed.stdout.split())
        if (
            completed.returncode != 0
            or "--restricted" not in help_text
            or "confines the file tools to the working directories" not in help_text
        ):
            raise SessionCliError(
                "Claude vision requires --restricted support that confines file tools to the working directories. "
                "Update Claude Code, or disable image loaders and PDF vision fallback.",
                kind="config",
            )
        self._claude_restricted_verified = True

    def _parse(self, completed: subprocess.CompletedProcess[str], invocation: _Invocation) -> CompleteResult:
        if self.cli == "claude":
            return _parse_claude(completed)
        if self.cli == "codex":
            return _parse_codex(completed, invocation.last_message_file)
        return _parse_custom(completed, executable=invocation.argv[0])

    def _executable(self) -> str:
        return self._command[0] if self.cli == "custom" else self.cli


def _substitute(token: str, substitutions: Mapping[str, str]) -> str:
    for placeholder, value in substitutions.items():
        token = token.replace(placeholder, value)
    return token


def _drop_empty_options(template: Sequence[str], substituted: Sequence[str]) -> list[str]:
    """Remove tokens that substituted to nothing, together with the option flag before them.

    ``["--image", "{image}"]`` on a text call must vanish entirely rather than
    leave ``--image ""`` on the command line.
    """
    kept: list[str] = []
    previous_was_flag = False
    for original, value in zip(template, substituted, strict=True):
        if value:
            kept.append(value)
            previous_was_flag = original.startswith("-") and "{" not in original
            continue
        if previous_was_flag:
            kept.pop()
        previous_was_flag = False
    return kept


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _classify_failure_text(text: str) -> SessionErrorKind:
    """Map a CLI's failure message to systemic (login, limit, config) or per-call (failed)."""
    if _contains_any(text, _RATE_LIMIT_MARKERS):
        return "rate-limited"
    if _contains_any(text, _LOGIN_MARKERS):
        return "not-logged-in"
    if _contains_any(text, _MODEL_MARKERS):
        return "config"
    return "failed"


def _claude_login_is_subscription(completed: subprocess.CompletedProcess[str], *, accepted: Sequence[str]) -> bool:
    try:
        status = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(status, dict) or not status.get("loggedIn"):
        return False
    return status.get("authMethod") in accepted and not status.get("apiKeySource")


def _codex_login_is_subscription(completed: subprocess.CompletedProcess[str]) -> bool:
    report = f"{completed.stdout}\n{completed.stderr}".lower()
    return completed.returncode == 0 and "chatgpt" in report and "api key" not in report


def _parse_claude(completed: subprocess.CompletedProcess[str]) -> CompleteResult:
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        kind = _classify_failure_text(detail)
        raise SessionCliError(f"claude CLI returned output that is not JSON (exit {completed.returncode}): {detail}", kind=kind) from exc
    if not isinstance(payload, dict):
        raise SessionCliError(f"claude CLI returned unexpected JSON (exit {completed.returncode}).", kind="failed")
    result = payload.get("result")
    text = result if isinstance(result, str) else ""
    if payload.get("is_error") or completed.returncode != 0:
        if _TRUNCATION_MARKER in text:
            raise OutputTruncatedError(f"claude CLI response was cut off: {text} Raise [llm.session].max_output_tokens.")
        status = payload.get("api_error_status")
        if status == 429:
            raise SessionCliError(f"claude CLI reported a rate limit: {text}", kind="rate-limited")
        if status in (401, 403):
            raise SessionCliError(f"claude CLI was refused ({status}): {text} Run `claude /login`.", kind="not-logged-in")
        if status in (400, 404):
            raise SessionCliError(f"claude CLI rejected the request ({status}): {text} Check [llm.session].model.", kind="config")
        raise SessionCliError(f"claude CLI failed (exit {completed.returncode}): {text}", kind=_classify_failure_text(text))
    if not text.strip():
        subtype = payload.get("subtype") or "unknown"
        raise SessionCliError(f"claude CLI returned an empty completion (subtype {subtype}).", kind="failed")
    usage = payload.get("usage") or {}
    return CompleteResult(
        text=text,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
    )


def _parse_codex(completed: subprocess.CompletedProcess[str], last_message_file: Path | None) -> CompleteResult:
    events = _parse_json_lines(completed.stdout)
    usage: dict[str, Any] = {}
    failure = ""
    for event in events:
        if event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
        elif event.get("type") == "turn.failed":
            failure = str((event.get("error") or {}).get("message") or "")
        elif event.get("type") == "error" and not failure:
            failure = str(event.get("message") or "")
    if completed.returncode != 0 or last_message_file is None or not last_message_file.is_file():
        detail = failure or (completed.stderr or completed.stdout or "").strip()[:300] or "no output"
        raise SessionCliError(f"codex CLI failed (exit {completed.returncode}): {detail}", kind=_classify_failure_text(detail))
    text = last_message_file.read_text(encoding="utf-8")
    if not text.strip():
        raise SessionCliError("codex CLI returned an empty completion.", kind="failed")
    return CompleteResult(
        text=text,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cached_input_tokens") or 0),
    )


def _parse_custom(completed: subprocess.CompletedProcess[str], *, executable: str) -> CompleteResult:
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300] or "no output"
        raise SessionCliError(
            f"agent CLI {executable!r} failed (exit {completed.returncode}): {detail}", kind=_classify_failure_text(detail)
        )
    text = completed.stdout.strip()
    if not text:
        raise SessionCliError(f"agent CLI {executable!r} returned an empty completion.", kind="failed")
    return CompleteResult(text=text, input_tokens=0, output_tokens=0)


def _parse_json_lines(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0
