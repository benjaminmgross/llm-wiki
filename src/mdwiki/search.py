"""Lexical search over wiki page sections — ``mdwiki search``.

An SQLite FTS5 virtual table (``page_chunks_fts``) inside ``.mdwiki/state.db``
indexes every semantic page as one row per heading-bounded section. It costs
no dependency (FTS5 is compiled into the bundled sqlite), calls no model and
no embedder, and complements the dense ``pages.embedding`` candidates:
embeddings catch paraphrase, FTS catches exact names, identifiers, and dates.

The index is a derived cache. ``IngestTransaction`` refreshes the rows for
every page it writes inside the same database transaction, ``undo`` and
``rebuild --pages`` rebuild it from disk, and ``search_pages`` reports how
many rows exist so callers can tell an empty wiki from a stale index.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.page_kinds import iter_wiki_page_paths
from mdwiki.version_chain import strip_frontmatter

SEARCH_TABLE: str = "page_chunks_fts"
SEARCH_SCHEMA_SQL: str = f"CREATE VIRTUAL TABLE IF NOT EXISTS {SEARCH_TABLE} USING fts5(path UNINDEXED, heading, body, tokenize = 'unicode61 remove_diacritics 2')"
DEFAULT_SEARCH_LIMIT: int = 10
_SNIPPET_TOKENS: int = 14

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_TOKEN_RE = re.compile(r"[\w][\w'’-]*", re.UNICODE)
_STOPWORDS: frozenset[str] = frozenset(
    "a an and are as at be by for from how in is it of on or that the this to was what when where which who will with".split()
)


@dataclass(frozen=True)
class SearchHit:
    """One ranked section match."""

    path: str
    heading: str
    snippet: str
    score: float

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "heading": self.heading, "snippet": self.snippet, "score": self.score}


@dataclass(frozen=True)
class SearchResult:
    """Ranked hits plus the index size, so callers can distinguish 'no match' from 'not indexed'."""

    hits: tuple[SearchHit, ...]
    indexed_rows: int
    match_query: str


def ensure_search_schema(conn: sqlite3.Connection) -> None:
    """Create the FTS5 table when an older ``state.db`` predates it."""
    conn.execute(SEARCH_SCHEMA_SQL)


def chunk_page_for_search(text: str) -> list[tuple[str, str]]:
    """Split a page body into ``(heading_path, body)`` rows, one per heading-bounded section.

    Frontmatter is stripped first. Headings inside fenced code blocks are
    ignored. The heading path joins ancestor headings with `` > `` so a hit
    tells the agent where in the page the words live.
    """
    body = strip_frontmatter(text)
    rows: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    in_fence = False

    def flush() -> None:
        content = "\n".join(buffer).strip()
        if content:
            rows.append((" > ".join(title for _, title in stack), content))
        buffer.clear()

    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            buffer.append(line)
            continue
        match = None if in_fence else _HEADING_RE.match(line)
        if match is None:
            buffer.append(line)
            continue
        flush()
        level = len(match.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, match.group(2).strip()))
    flush()
    return rows


def index_page(conn: sqlite3.Connection, *, rel_path: str, text: str) -> int:
    """Replace the index rows for one page with rows built from ``text``. Returns rows written."""
    conn.execute(f"DELETE FROM {SEARCH_TABLE} WHERE path = ?", (rel_path,))
    rows = chunk_page_for_search(text)
    conn.executemany(
        f"INSERT INTO {SEARCH_TABLE} (path, heading, body) VALUES (?, ?, ?)", [(rel_path, heading, body) for heading, body in rows]
    )
    return len(rows)


def remove_page(conn: sqlite3.Connection, *, rel_path: str) -> None:
    """Drop every index row for ``rel_path`` (page deleted)."""
    conn.execute(f"DELETE FROM {SEARCH_TABLE} WHERE path = ?", (rel_path,))


def rebuild_search_index(conn: sqlite3.Connection, *, wiki_root: Path) -> int:
    """Rebuild the whole index from the semantic pages on disk. Returns rows written."""
    ensure_search_schema(conn)
    conn.execute(f"DELETE FROM {SEARCH_TABLE}")
    written = 0
    for page_path in iter_wiki_page_paths(wiki_root):
        rel = page_path.relative_to(wiki_root).as_posix()
        written += index_page(conn, rel_path=rel, text=page_path.read_text())
    return written


def build_match_query(terms: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Every token becomes a quoted phrase term (so FTS operators and punctuation
    in user input cannot change the query shape); tokens are implicitly ANDed;
    the final token gets prefix expansion. A double-quoted span in the input
    is kept together as a phrase.
    """
    phrases: list[str] = []
    remainder = terms
    for quoted in re.findall(r'"([^"]+)"', terms):
        tokens = _TOKEN_RE.findall(quoted)
        if tokens:
            phrases.append('"' + " ".join(tok.replace('"', "") for tok in tokens) + '"')
        remainder = remainder.replace(f'"{quoted}"', " ")
    tokens = [tok for tok in _TOKEN_RE.findall(remainder) if tok.strip("'’-")]
    parts = [f'"{tok}"' for tok in tokens]
    if parts:
        parts[-1] = parts[-1] + "*"
    return " ".join(phrases + parts)


def search_pages(wiki_root: Path, terms: str, *, limit: int = DEFAULT_SEARCH_LIMIT) -> SearchResult:
    """Rank page sections for ``terms`` by bm25. Read-only; never builds a provider or embedder."""
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    match_query = build_match_query(terms)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        table = conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (SEARCH_TABLE,)).fetchone()
        if table is None:
            return SearchResult(hits=(), indexed_rows=0, match_query=match_query)
        indexed_rows = int(conn.execute(f"SELECT COUNT(*) FROM {SEARCH_TABLE}").fetchone()[0])
        if not match_query or indexed_rows == 0:
            return SearchResult(hits=(), indexed_rows=indexed_rows, match_query=match_query)
        try:
            rows = conn.execute(
                f"SELECT path, heading, snippet({SEARCH_TABLE}, 2, '[', ']', '…', ?) AS snippet, bm25({SEARCH_TABLE}) AS score "
                f"FROM {SEARCH_TABLE} WHERE {SEARCH_TABLE} MATCH ? ORDER BY score LIMIT ?",
                (_SNIPPET_TOKENS, match_query, max(1, limit)),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    hits = tuple(
        SearchHit(
            path=row["path"], heading=row["heading"], snippet=" ".join(str(row["snippet"]).split()), score=round(-float(row["score"]), 4)
        )
        for row in rows
    )
    return SearchResult(hits=hits, indexed_rows=indexed_rows, match_query=match_query)


def lexical_candidates_for_text(wiki_root: Path, text: str, *, limit: int = 8) -> tuple[str, ...]:
    """Return distinct page paths that share distinctive words with ``text`` (headings and title first).

    Deterministic and model-free; used to seed candidate pages for native-session
    sub-agents that deliberately run without the embedder.
    """
    terms = _distinctive_terms(text)
    if not terms:
        return ()
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    query = " OR ".join(f'"{term}"' for term in terms)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (SEARCH_TABLE,)).fetchone() is None:
            return ()
        try:
            rows = conn.execute(
                f"SELECT path, bm25({SEARCH_TABLE}) AS score FROM {SEARCH_TABLE} WHERE {SEARCH_TABLE} MATCH ? ORDER BY score",
                (query,),
            ).fetchall()
        except sqlite3.OperationalError:
            return ()
    ordered: list[str] = []
    for row in rows:
        if row["path"] not in ordered:
            ordered.append(row["path"])
        if len(ordered) >= limit:
            break
    return tuple(ordered)


def _distinctive_terms(text: str, *, max_terms: int = 24) -> list[str]:
    """Heading words first, then capitalized body words, deduplicated, stopwords removed."""
    body = strip_frontmatter(text)
    heading_words: list[str] = []
    body_words: list[str] = []
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        target = heading_words if match else body_words
        source = match.group(2) if match else line
        for tok in _TOKEN_RE.findall(source):
            cleaned = tok.strip("'’-")
            if len(cleaned) < 3 or cleaned.lower() in _STOPWORDS or cleaned.isdigit():
                continue
            if target is body_words and not cleaned[0].isupper():
                continue
            target.append(cleaned)
    ordered: list[str] = []
    seen: set[str] = set()
    for word in heading_words + body_words:
        key = word.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(word)
        if len(ordered) >= max_terms:
            break
    return ordered
