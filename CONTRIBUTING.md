# Contributing to llm-wiki (`mdwiki`)

## Development setup

```bash
git clone https://github.com/benjaminmgross/llm-wiki.git
cd llm-wiki
uv sync
uv run mdwiki --version
```

The environment is `uv`-managed (Python ≥ 3.12). Always run tools through `uv run`.

## Checks before a pull request

```bash
uv run pytest            # full suite with coverage (see pyproject [tool.pytest.ini_options])
uv run ruff check .      # lint + import order
uv run ruff format .     # formatting
uv run mypy src          # type check the package
```

## Conventions

- Typed functions, keyword-only arguments for new parameters, immutable dataclasses for results.
- Every durable wiki write goes through `IngestTransaction`; never write `wiki/`, `state.db`, `raw/.sources.json`, or `log.md` outside it.
- Behavioral tests first (RED → GREEN → REFACTOR). Tests live in `tests/test_<module>.py`, use `init_wiki(tmp_path)` fixtures, mock the provider at `mdwiki.llm.anthropic.AnthropicProvider.complete`, and assert on files and `state.db` rows, not on mocks.
- The plan JSON contract is defined twice on purpose: `src/mdwiki/plan.py` (parser, source of truth) and `src/mdwiki/ingest_tool.py` (constrained-decoding schema). Change both together.
- The agent-facing skill is package data under `src/mdwiki/skill/`; the repository-root `skill/` is a symlink to it. Edit the package copy only.
- Do not hard-wrap prose in Markdown files.
- Record user-visible changes in `CHANGES_GUIDE.md` and the README highlights block; bump `__version__` in `src/mdwiki/__init__.py` and `pyproject.toml` together.

## Pull request process

1. Branch from `main`, keep the change focused, and include tests.
2. Run the checks above; CI is the same commands.
3. Describe the user-visible behavior change and any migration note in the PR body.
