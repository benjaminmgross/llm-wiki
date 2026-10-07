"""Tests for ``SessionProvider`` — completions through a local agent CLI.

Provider tests use a fake ``runner`` that records argv, environment and stdin
and returns canned results. Process lifecycle tests launch only dummy Python
processes; no test invokes an agent CLI or calls a model.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from mdwiki.llm.anthropic import OutputTruncatedError
from mdwiki.llm.base import Message
from mdwiki.llm.session import SessionCliError, SessionProvider

CLAUDE_LOGIN_OK = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"})
CODEX_LOGIN_OK = "Logged in using ChatGPT\n"
CLAUDE_RESTRICTED_HELP = (
    "  --restricted   Restricted mode: removes tools that run commands.\n"
    "                 Also confines the file\n"
    "                 tools to the working directories (--add-dir included).\n"
)


def _claude_result(text: str = "pong", **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "type": "result",
        "is_error": False,
        "result": text,
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }
    payload.update(overrides)
    return json.dumps(payload)


class FakeRunner:
    """Records every call and answers from a queue of canned results."""

    def __init__(self, *responses: subprocess.CompletedProcess[str] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.files_seen: dict[str, str] = {}
        self.write_last_message: str | None = None

    def __call__(
        self,
        argv: list[str],
        *,
        input: str | None,  # noqa: A002 - mirrors subprocess.run
        env: dict[str, str],
        cwd: str,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append({"argv": list(argv), "input": input, "env": dict(env), "cwd": cwd, "timeout": timeout})
        for token in argv:
            candidate = token.split("=", 1)[1].strip('"') if token.startswith("model_instructions_file=") else token
            path = Path(candidate)
            if path.is_absolute() and path.is_file() and path.suffix == ".txt":
                self.files_seen[path.name] = path.read_text()
        if self.write_last_message is not None and "-o" in argv:
            Path(argv[argv.index("-o") + 1]).write_text(self.write_last_message)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _ok(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _claude_provider(runner: FakeRunner, **kwargs: Any) -> SessionProvider:
    return SessionProvider(cli="claude", runner=runner, base_env={"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-live"}, **kwargs)


@pytest.mark.unit
def test_session_provider_declares_no_batch_or_tool_capability() -> None:
    """Orchestration routes on these flags; session mode uses free-form JSON and sync ingest."""
    provider = _claude_provider(FakeRunner())
    assert provider.name == "session"
    assert provider.supports_batch is False
    assert provider.supports_tool_use is False
    assert provider.model == "sonnet"


@pytest.mark.unit
def test_claude_complete_returns_text_and_usage() -> None:
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(_claude_result("hello")))
    provider = _claude_provider(runner)

    result = provider.complete(system="SYSTEM PROMPT", messages=[Message(role="user", content="question")])

    assert result.text == "hello"
    assert result.input_tokens == 12
    assert result.output_tokens == 3
    assert result.tool_input is None


@pytest.mark.unit
def test_claude_complete_builds_tool_free_argv_and_passes_prompt_on_stdin() -> None:
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(_claude_result()))
    provider = _claude_provider(runner, model="opus")

    provider.complete(system="SYSTEM PROMPT", messages=[Message(role="user", content="question")])

    call = runner.calls[1]
    argv = call["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert call["input"] == "question"
    assert runner.files_seen["system.txt"] == "SYSTEM PROMPT"


@pytest.mark.unit
def test_api_key_variables_are_removed_from_the_child_environment() -> None:
    """A key in the parent shell must never reach the CLI, or the run bills the API."""
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(_claude_result()))
    provider = SessionProvider(
        cli="claude",
        runner=runner,
        strip_env=("MY_PROXY_TOKEN",),
        base_env={
            "PATH": "/usr/bin",
            "HOME": "/Users/someone",
            "ANTHROPIC_API_KEY": "sk-live",
            "ANTHROPIC_AUTH_TOKEN": "tok",
            "OPENAI_API_KEY": "sk-openai",
            "CODEX_API_KEY": "sk-codex",
            "CODEX_ACCESS_TOKEN": "tok",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "MY_PROXY_TOKEN": "secret",
        },
    )

    provider.complete(system="s", messages=[Message(role="user", content="q")])

    for call in runner.calls:
        env = call["env"]
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/Users/someone"
        for name in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "CODEX_ACCESS_TOKEN",
            "CLAUDECODE",
            "CLAUDE_CODE_ENTRYPOINT",
            "MY_PROXY_TOKEN",
        ):
            assert name not in env
    assert runner.calls[1]["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "32000"


@pytest.mark.unit
def test_subscription_is_verified_once_per_provider() -> None:
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(_claude_result()), _ok(_claude_result()))
    provider = _claude_provider(runner)

    provider.complete(system="s", messages=[Message(role="user", content="q1")])
    provider.complete(system="s", messages=[Message(role="user", content="q2")])

    assert [call["argv"][:3] for call in runner.calls][0] == ["claude", "auth", "status"]
    assert len(runner.calls) == 3


@pytest.mark.unit
def test_claude_refuses_to_run_when_cli_would_use_an_api_key() -> None:
    login = json.dumps({"loggedIn": True, "authMethod": "api_key", "apiKeySource": "ANTHROPIC_API_KEY"})
    runner = FakeRunner(_ok(login))
    provider = _claude_provider(runner)

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "not-logged-in"
    assert len(runner.calls) == 1


@pytest.mark.unit
def test_claude_refuses_to_run_when_not_logged_in() -> None:
    runner = FakeRunner(_ok(json.dumps({"loggedIn": False}), returncode=1))
    provider = _claude_provider(runner)

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "not-logged-in"
    assert "claude" in str(excinfo.value)


@pytest.mark.unit
def test_verify_subscription_can_be_disabled() -> None:
    runner = FakeRunner(_ok(_claude_result()))
    provider = _claude_provider(runner, verify_subscription=False)

    provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert len(runner.calls) == 1
    assert runner.calls[0]["argv"][:2] == ["claude", "-p"]


@pytest.mark.unit
def test_missing_cli_raises_not_installed() -> None:
    runner = FakeRunner(FileNotFoundError("claude"))
    provider = _claude_provider(runner)

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "not-installed"
    assert provider.is_recoverable_error(excinfo.value) is False


@pytest.mark.unit
def test_timeout_is_recoverable_so_bulk_ingest_continues() -> None:
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), subprocess.TimeoutExpired(cmd="claude", timeout=5))
    provider = _claude_provider(runner, timeout=5.0)

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "timeout"
    assert provider.is_recoverable_error(excinfo.value) is True
    assert runner.calls[1]["timeout"] == 5.0


@pytest.mark.unit
def test_claude_error_result_raises_failed() -> None:
    body = _claude_result("API Error: overloaded", is_error=True, api_error_status=529)
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(body, returncode=1))
    provider = _claude_provider(runner)

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "failed"
    assert "overloaded" in str(excinfo.value)
    assert provider.is_recoverable_error(excinfo.value) is True


@pytest.mark.unit
def test_claude_unknown_model_is_a_config_error_and_not_recoverable() -> None:
    body = _claude_result("There's an issue with the selected model (nope)", is_error=True, api_error_status=404)
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(body, returncode=1))
    provider = _claude_provider(runner, model="nope")

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "config"
    assert provider.is_recoverable_error(excinfo.value) is False


@pytest.mark.unit
def test_claude_truncation_raises_output_truncated_error() -> None:
    body = _claude_result(
        "API Error: Claude's response exceeded the 32000 output token maximum. To configure this behavior, set ...",
        is_error=True,
    )
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(body, returncode=1))
    provider = _claude_provider(runner)

    with pytest.raises(OutputTruncatedError):
        provider.complete(system="s", messages=[Message(role="user", content="q")])


@pytest.mark.unit
def test_unparseable_stdout_raises_failed() -> None:
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok("not json at all"))
    provider = _claude_provider(runner)

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "failed"


@pytest.mark.unit
def test_multi_message_history_is_flattened_into_one_prompt() -> None:
    """Corrective retries replay the prior answer; the CLI takes a single prompt."""
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(_claude_result()))
    provider = _claude_provider(runner)

    provider.complete(
        system="s",
        messages=[
            Message(role="user", content="first question"),
            Message(role="assistant", content="bad answer"),
            Message(role="user", content="please correct it"),
        ],
    )

    prompt = runner.calls[1]["input"]
    assert prompt.index("first question") < prompt.index("bad answer") < prompt.index("please correct it")
    assert "<assistant_turn>" in prompt


@pytest.mark.unit
def test_codex_complete_reads_last_message_file_and_usage() -> None:
    events = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 17000, "output_tokens": 7}}),
        ]
    )
    runner = FakeRunner(_ok(CODEX_LOGIN_OK), _ok(events))
    runner.write_last_message = "answer"
    provider = SessionProvider(cli="codex", runner=runner, base_env={"PATH": "/usr/bin", "OPENAI_API_KEY": "sk"})

    result = provider.complete(system="SYSTEM PROMPT", messages=[Message(role="user", content="question")])

    assert result.text == "answer"
    assert result.input_tokens == 17000
    assert result.output_tokens == 7
    assert provider.model == "codex default"


@pytest.mark.unit
def test_codex_argv_is_sandboxed_and_never_forces_a_login_method() -> None:
    """``forced_login_method`` deletes a ChatGPT login; it must never be passed."""
    runner = FakeRunner(_ok(CODEX_LOGIN_OK), _ok(json.dumps({"type": "turn.completed", "usage": {}})))
    runner.write_last_message = "ok"
    provider = SessionProvider(cli="codex", model="gpt-5", runner=runner, base_env={"PATH": "/usr/bin"})

    provider.complete(system="SYSTEM PROMPT", messages=[Message(role="user", content="question")])

    assert runner.calls[0]["argv"] == ["codex", "login", "status"]
    call = runner.calls[1]
    argv = call["argv"]
    assert argv[:2] == ["codex", "exec"]
    disabled = {argv[index + 1] for index, token in enumerate(argv[:-1]) if token == "--disable"}
    assert {"shell_tool", "view_image"} <= disabled
    assert argv[argv.index("-s") + 1] == "read-only"
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--skip-git-repo-check" in argv
    assert 'approval_policy="never"' in argv
    assert argv[argv.index("-m") + 1] == "gpt-5"
    assert argv[-1] == "-"
    assert not any("forced_login_method" in token for token in argv)
    assert call["input"] == "question"
    assert runner.files_seen["system.txt"] == "SYSTEM PROMPT"
    assert argv[argv.index("-C") + 1] == call["cwd"]


@pytest.mark.unit
def test_codex_refuses_to_run_when_logged_in_with_an_api_key() -> None:
    runner = FakeRunner(_ok("Logged in using an API key - sk-***\n"))
    provider = SessionProvider(cli="codex", runner=runner, base_env={"PATH": "/usr/bin"})

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "not-logged-in"


@pytest.mark.unit
def test_codex_failure_reports_the_turn_failed_message() -> None:
    events = "\n".join(
        [
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "turn.failed", "error": {"message": "model is not supported"}}),
        ]
    )
    runner = FakeRunner(_ok(CODEX_LOGIN_OK), _ok(events, returncode=1))
    provider = SessionProvider(cli="codex", runner=runner, base_env={"PATH": "/usr/bin"})

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert "model is not supported" in str(excinfo.value)


@pytest.mark.unit
def test_custom_command_fills_placeholders_and_returns_stdout() -> None:
    runner = FakeRunner(_ok("plain answer\n"))
    provider = SessionProvider(
        cli="custom",
        model="local-model",
        command=("my-agent", "--model", "{model}", "--system", "{system_file}"),
        runner=runner,
        base_env={"PATH": "/usr/bin"},
    )

    result = provider.complete(system="SYSTEM PROMPT", messages=[Message(role="user", content="question")])

    argv = runner.calls[0]["argv"]
    assert argv[:3] == ["my-agent", "--model", "local-model"]
    assert Path(argv[4]).name == "system.txt"
    assert runner.calls[0]["input"] == "question"
    assert result.text == "plain answer"
    assert len(runner.calls) == 1


@pytest.mark.unit
def test_custom_command_without_system_placeholder_prepends_the_system_prompt() -> None:
    runner = FakeRunner(_ok("answer"))
    provider = SessionProvider(cli="custom", command=("my-agent",), runner=runner, base_env={"PATH": "/usr/bin"})

    provider.complete(system="SYSTEM PROMPT", messages=[Message(role="user", content="question")])

    prompt = runner.calls[0]["input"]
    assert prompt.index("SYSTEM PROMPT") < prompt.index("question")


@pytest.mark.unit
def test_custom_cli_requires_a_command() -> None:
    with pytest.raises(ValueError, match="command"):
        SessionProvider(cli="custom", runner=FakeRunner())


@pytest.mark.unit
def test_unknown_cli_is_rejected() -> None:
    with pytest.raises(ValueError, match="gemini"):
        SessionProvider(cli="gemini", runner=FakeRunner())


@pytest.mark.unit
def test_ping_never_raises_and_reports_failure() -> None:
    runner = FakeRunner(FileNotFoundError("claude"))
    provider = _claude_provider(runner)

    ping = provider.ping()

    assert ping.ok is False
    assert ping.provider == "session"
    assert "not installed" in ping.message or "not found" in ping.message


@pytest.mark.unit
def test_ping_reports_success_and_names_the_cli() -> None:
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(_claude_result("pong")))
    provider = _claude_provider(runner)

    ping = provider.ping()

    assert ping.ok is True
    assert "claude" in ping.message
    assert "subscription" in ping.message


@pytest.mark.unit
def test_claude_describe_image_allows_only_the_read_tool(tmp_path: Path) -> None:
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG fake")
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(CLAUDE_RESTRICTED_HELP), _ok(_claude_result("# Page\n\nINVOICE 4721")))
    provider = _claude_provider(runner)

    text = provider.describe_image(image)

    assert runner.calls[1]["argv"] == ["claude", "--help"]
    call = runner.calls[2]
    argv = call["argv"]
    assert text == "# Page\n\nINVOICE 4721"
    assert argv[argv.index("--tools") + 1] == "Read"
    assert argv[argv.index("--allowedTools") + 1] == "Read"
    assert "--restricted" in argv
    assert "--add-dir" not in argv
    assert "--bare" not in argv
    assert "image.png" in call["input"]
    assert str(Path(call["cwd"])) in call["input"]


@pytest.mark.unit
def test_codex_describe_image_attaches_the_file(tmp_path: Path) -> None:
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG fake")
    runner = FakeRunner(_ok(CODEX_LOGIN_OK), _ok(json.dumps({"type": "turn.completed", "usage": {}})))
    runner.write_last_message = "INVOICE 4721"
    provider = SessionProvider(cli="codex", runner=runner, base_env={"PATH": "/usr/bin"})

    text = provider.describe_image(image)

    argv = runner.calls[1]["argv"]
    assert text == "INVOICE 4721"
    assert Path(argv[argv.index("-i") + 1]).name == "image.png"
    disabled = {argv[index + 1] for index, token in enumerate(argv[:-1]) if token == "--disable"}
    assert {"shell_tool", "view_image"} <= disabled


@pytest.mark.unit
def test_codex_does_not_retry_without_tool_restrictions_when_cli_rejects_them() -> None:
    runner = FakeRunner(_ok("", returncode=2, stderr="error: unknown feature: view_image"))
    provider = SessionProvider(cli="codex", runner=runner, base_env={}, verify_subscription=False)

    with pytest.raises(SessionCliError, match="unknown feature"):
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert len(runner.calls) == 1
    argv = runner.calls[0]["argv"]
    disabled = {argv[index + 1] for index, token in enumerate(argv[:-1]) if token == "--disable"}
    assert {"shell_tool", "view_image"} <= disabled


@pytest.mark.unit
def test_custom_describe_image_requires_an_image_placeholder(tmp_path: Path) -> None:
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG fake")
    provider = SessionProvider(cli="custom", command=("my-agent",), runner=FakeRunner(), base_env={"PATH": "/usr/bin"})

    with pytest.raises(NotImplementedError, match="image"):
        provider.describe_image(image)


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    [
        {"loggedIn": True, "authMethod": "console"},
        {"loggedIn": True, "authMethod": "bedrock"},
        {"loggedIn": True, "authMethod": "oauth_token"},
        {"loggedIn": True},
        {"loggedIn": True, "authMethod": "claude.ai", "apiKeySource": "ANTHROPIC_API_KEY"},
    ],
)
def test_claude_login_check_is_an_allowlist(status: dict[str, Any]) -> None:
    """Only a login known to be a subscription may run; anything unrecognized is refused."""
    runner = FakeRunner(_ok(json.dumps(status)))
    provider = _claude_provider(runner)

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "not-logged-in"
    assert len(runner.calls) == 1


@pytest.mark.unit
def test_accepted_auth_methods_can_be_extended_by_config() -> None:
    runner = FakeRunner(_ok(json.dumps({"loggedIn": True, "authMethod": "oauth_token"})), _ok(_claude_result()))
    provider = _claude_provider(runner, accepted_auth_methods=("claude.ai", "oauth_token"))

    assert provider.complete(system="s", messages=[Message(role="user", content="q")]).text == "pong"


@pytest.mark.unit
def test_variables_that_redirect_billing_are_removed_by_prefix_and_case() -> None:
    """A gateway URL or cloud credential can move billing without any API key being set."""
    from mdwiki.llm.session import scrub_environment

    env = scrub_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/Users/someone",
            "TMPDIR": "/tmp",
            "CODEX_HOME": "/Users/someone/.codex",
            "CLAUDE_CONFIG_DIR": "/Users/someone/.claude",
            "CLAUDE_CODE_OAUTH_TOKEN": "subscription-token",
            "HTTPS_PROXY": "http://proxy:8080",
            "ANTHROPIC_BASE_URL": "https://gateway",
            "ANTHROPIC_CUSTOM_HEADERS": "x: y",
            "ANTHROPIC_MODEL": "m",
            "ANTHROPIC_VERTEX_PROJECT_ID": "p",
            "anthropic_api_key": "sk-lower",
            "OPENAI_BASE_URL": "https://gateway",
            "OPENAI_ORG_ID": "org",
            "AWS_BEARER_TOKEN_BEDROCK": "t",
            "AWS_ACCESS_KEY_ID": "a",
            "GOOGLE_APPLICATION_CREDENTIALS": "/creds.json",
            "CLOUD_ML_REGION": "us-east5",
            "AZURE_OPENAI_API_KEY": "k",
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }
    )

    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/Users/someone",
        "TMPDIR": "/tmp",
        "CODEX_HOME": "/Users/someone/.codex",
        "CLAUDE_CONFIG_DIR": "/Users/someone/.claude",
        "CLAUDE_CODE_OAUTH_TOKEN": "subscription-token",
        "HTTPS_PROXY": "http://proxy:8080",
    }


@pytest.mark.unit
def test_keep_env_exempts_a_variable_a_custom_cli_needs() -> None:
    from mdwiki.llm.session import scrub_environment

    env = scrub_environment({"AWS_PROFILE": "work", "AWS_SECRET_ACCESS_KEY": "s"}, keep_names=("AWS_PROFILE",))

    assert env == {"AWS_PROFILE": "work"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "returncode", "kind"),
    [
        (_claude_result("API Error: rate limited", is_error=True, api_error_status=429), 1, "rate-limited"),
        (_claude_result("Claude usage limit reached. Your limit will reset at 7pm.", is_error=True), 1, "rate-limited"),
        ("Invalid API key · Please run /login", 1, "not-logged-in"),
        (_claude_result("Please run /login", is_error=True), 1, "not-logged-in"),
    ],
)
def test_claude_systemic_failures_abort_a_bulk_run(body: str, returncode: int, kind: str) -> None:
    """A usage limit or an expired login fails every source alike, so bulk ingest must stop."""
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(body, returncode=returncode))
    provider = _claude_provider(runner)

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == kind
    assert provider.is_recoverable_error(excinfo.value) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("message", "kind", "recoverable"),
    [
        ("You've hit your usage limit. Try again at 7pm.", "rate-limited", False),
        ("Not logged in. Run codex login.", "not-logged-in", False),
        ("The 'nope' model is not supported when using Codex with a ChatGPT account.", "config", False),
        ("stream disconnected before completion: file not found in cache", "failed", True),
    ],
)
def test_codex_failures_are_classified_by_cause(message: str, kind: str, recoverable: bool) -> None:
    events = json.dumps({"type": "turn.failed", "error": {"message": message}})
    runner = FakeRunner(_ok(CODEX_LOGIN_OK), _ok(events, returncode=1))
    provider = SessionProvider(cli="codex", runner=runner, base_env={"PATH": "/usr/bin"})

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == kind
    assert provider.is_recoverable_error(excinfo.value) is recoverable


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": False}),
        _claude_result(""),
        _claude_result("   "),
    ],
)
def test_claude_empty_completion_is_a_failure_not_an_answer(body: str) -> None:
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(body))
    provider = _claude_provider(runner)

    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q")])

    assert excinfo.value.kind == "failed"


@pytest.mark.unit
def test_codex_empty_last_message_is_a_failure() -> None:
    runner = FakeRunner(_ok(CODEX_LOGIN_OK), _ok(json.dumps({"type": "turn.completed", "usage": {}})))
    runner.write_last_message = ""
    provider = SessionProvider(cli="codex", runner=runner, base_env={"PATH": "/usr/bin"})

    with pytest.raises(SessionCliError):
        provider.complete(system="s", messages=[Message(role="user", content="q")])


@pytest.mark.unit
def test_login_is_reverified_after_a_failed_call() -> None:
    """A login that expires mid-run must be noticed on the next call, not assumed still valid."""
    failed = _claude_result("API Error: overloaded", is_error=True, api_error_status=529)
    logged_out = json.dumps({"loggedIn": False})
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(failed, returncode=1), _ok(logged_out, returncode=1))
    provider = _claude_provider(runner)

    with pytest.raises(SessionCliError):
        provider.complete(system="s", messages=[Message(role="user", content="q1")])
    with pytest.raises(SessionCliError) as excinfo:
        provider.complete(system="s", messages=[Message(role="user", content="q2")])

    assert excinfo.value.kind == "not-logged-in"
    assert runner.calls[2]["argv"][:3] == ["claude", "auth", "status"]


@pytest.mark.unit
def test_codex_instructions_path_is_escaped_as_a_toml_string() -> None:
    """A temp directory containing quotes or backslashes must still produce valid TOML."""
    import tomllib

    from mdwiki.llm.session import toml_assignment

    path = 'C:\\tmp\\a "b"\\system.txt'

    assignment = toml_assignment("model_instructions_file", path)

    assert tomllib.loads(assignment) == {"model_instructions_file": path}


@pytest.mark.unit
def test_image_named_like_the_system_prompt_cannot_overwrite_it(tmp_path: Path) -> None:
    image = tmp_path / "system.txt"
    image.write_bytes(b"not really an image")
    runner = FakeRunner(_ok(CLAUDE_LOGIN_OK), _ok(CLAUDE_RESTRICTED_HELP), _ok(_claude_result("text")))
    provider = _claude_provider(runner)

    provider.describe_image(image)

    assert "transcribe images" in runner.files_seen["system.txt"]


@pytest.mark.unit
@pytest.mark.parametrize("help_text,returncode", [("--tools Read", 0), ("--restricted  unrelated behavior", 0), (CLAUDE_RESTRICTED_HELP, 1)])
def test_claude_vision_refuses_without_a_verified_restricted_contract(tmp_path: Path, help_text: str, returncode: int) -> None:
    image = tmp_path / "scan.png"
    image.write_bytes(b"image")
    runner = FakeRunner(_ok(help_text, returncode=returncode))
    provider = _claude_provider(runner, verify_subscription=False)

    with pytest.raises(SessionCliError, match="restricted") as excinfo:
        provider.describe_image(image)

    assert excinfo.value.kind == "config"
    assert [call["argv"] for call in runner.calls] == [["claude", "--help"]]


@pytest.mark.unit
def test_claude_vision_checks_restricted_support_once_per_provider(tmp_path: Path) -> None:
    image = tmp_path / "scan.png"
    image.write_bytes(b"image")
    runner = FakeRunner(_ok(CLAUDE_RESTRICTED_HELP), _ok(_claude_result("one")), _ok(_claude_result("two")))
    provider = _claude_provider(runner, verify_subscription=False)

    assert provider.describe_image(image) == "one"
    assert provider.describe_image(image) == "two"

    assert sum(call["argv"] == ["claude", "--help"] for call in runner.calls) == 1
    assert all("--restricted" in call["argv"] for call in runner.calls[1:])


@pytest.mark.unit
def test_custom_command_drops_tokens_that_substitute_to_nothing() -> None:
    runner = FakeRunner(_ok("answer"))
    provider = SessionProvider(
        cli="custom",
        command=("my-agent", "{model}", "--image", "{image}", "--system", "{system_file}"),
        runner=runner,
        base_env={"PATH": "/usr/bin"},
    )

    provider.complete(system="s", messages=[Message(role="user", content="q")])

    argv = runner.calls[0]["argv"]
    assert "" not in argv
    assert argv[0] == "my-agent"
    assert argv[-2] == "--system"


@pytest.mark.integration
def test_run_subprocess_returns_output_and_passes_stdin(tmp_path: Path) -> None:
    import sys

    from mdwiki.llm.session import run_subprocess

    completed = run_subprocess(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        input="héllo",
        env={"PATH": "/usr/bin:/bin"},
        cwd=str(tmp_path),
        timeout=30.0,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "HÉLLO"


@pytest.mark.integration
def test_run_subprocess_kills_the_whole_process_group_on_timeout(tmp_path: Path) -> None:
    """An agent CLI spawns helpers; a timeout must not leave them holding the pipes."""
    import sys
    import time

    from mdwiki.llm.session import run_subprocess

    script = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"
    start = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        run_subprocess([sys.executable, "-c", script], input=None, env={"PATH": "/usr/bin:/bin"}, cwd=str(tmp_path), timeout=1.0)

    assert time.monotonic() - start < 15


@pytest.mark.integration
def test_run_subprocess_kills_and_reaps_child_on_keyboard_interrupt(tmp_path: Path) -> None:
    """SIGINT to mdwiki must stop the child started in an isolated session."""
    pid_path = tmp_path / "child.pid"
    child_script = (
        "import os, pathlib, time; "
        "pathlib.Path('child.tmp').write_text(str(os.getpid())); "
        "pathlib.Path('child.tmp').replace('child.pid'); time.sleep(60)"
    )
    wrapper_script = (
        "import os, sys\n"
        "from mdwiki.llm.session import run_subprocess\n"
        f"run_subprocess([sys.executable, '-c', {child_script!r}], input=None, env=dict(os.environ), cwd={str(tmp_path)!r}, timeout=60)\n"
    )
    wrapper = subprocess.Popen([sys.executable, "-c", wrapper_script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child_pid = None
    try:
        deadline = time.monotonic() + 10
        while not pid_path.exists() and wrapper.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_path.exists(), "dummy child did not become ready"
        child_pid = int(pid_path.read_text())
        os.kill(wrapper.pid, signal.SIGINT)
        _, stderr = wrapper.communicate(timeout=10)

        assert wrapper.returncode == -signal.SIGINT, stderr.decode()
        assert b"KeyboardInterrupt" in stderr
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        # The regression must not leak a running child even against broken code.
        if child_pid is None and pid_path.exists():
            child_pid = int(pid_path.read_text())
        if child_pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(child_pid, signal.SIGKILL)
        if wrapper.poll() is None:
            wrapper.kill()
        wrapper.communicate(timeout=5)


@pytest.mark.unit
def test_run_subprocess_preserves_communication_error_after_cleanup(tmp_path: Path, mocker: MockerFixture) -> None:
    from mdwiki.llm.session import run_subprocess

    process = mocker.patch("mdwiki.llm.session.subprocess.Popen").return_value
    process.pid = 12345
    error = OSError("broken output pipe")
    process.communicate.side_effect = [error, OSError("cleanup also failed")]
    killpg = mocker.patch("mdwiki.llm.session.os.killpg")

    with pytest.raises(OSError) as excinfo:
        run_subprocess(["dummy"], input=None, env={}, cwd=str(tmp_path), timeout=1)

    assert excinfo.value is error
    killpg.assert_called_once_with(process.pid, signal.SIGKILL)
    process.wait.assert_called_once_with(timeout=5)
