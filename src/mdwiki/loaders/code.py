"""Source-code loader — wraps source files in a language-tagged fenced block.

The language tag (```python``, ```javascript``, ...) is the conventional markdown
hint that tells syntax highlighters and downstream LLMs the file's language. Tags
follow the most-recognized aliases (e.g. ``bash`` for ``.sh``, ``ruby`` for ``.rb``).
"""

from __future__ import annotations

from pathlib import Path

from mdwiki.loaders.base import Loader

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".fish": "fish",
    ".ps1": "powershell",
    ".sql": "sql",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".php": "php",
    ".lua": "lua",
    ".r": "r",
    ".jl": "julia",
    ".scala": "scala",
    ".clj": "clojure",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".elm": "elm",
    ".dart": "dart",
    ".vue": "vue",
    ".tf": "hcl",
    ".dockerfile": "dockerfile",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".xml": "xml",
}


class CodeLoader(Loader):
    """Wrap source code in a language-tagged markdown fenced block."""

    name: str = "CodeLoader"

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in _EXT_TO_LANG

    def load_to_markdown(self, path: Path) -> str:
        lang = _EXT_TO_LANG[path.suffix.lower()]
        body = path.read_text()
        if not body.endswith("\n"):
            body += "\n"
        return f"```{lang}\n{body}```\n"
