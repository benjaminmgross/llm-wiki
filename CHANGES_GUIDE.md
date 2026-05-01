# Comprehensive Changes Guide

This document outlines recent major changes to this repository.

## v1.1.0 — Multi-filetype ingest, batch API, local providers (2026-04-30)

**v1.1.0 broadens what `mdwiki` can ingest, where it can run, and how cheaply it can build a wiki.** All changes are additive — existing v1.0 wikis upgrade transparently (`pip install -U` is enough; no schema migration required).

### What ships

#### Multi-filetype ingest

`mdwiki init` and `mdwiki ingest` now accept eight filetypes via a new `Loader` registry:

| Extension | Loader | Notes |
|---|---|---|
| `.md`, `.markdown` | `MarkdownLoader` | Passthrough (v1.0 behavior) |
| `.txt`, `.log`, `.rst` | `TextLoader` | Wrapped in a fenced block — preserves whitespace + line structure |
| `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.rb`, `.sh`, `.sql`, … (35+ exts) | `CodeLoader` | Wrapped in a language-tagged fenced block |
| `.csv`, `.tsv` | `CsvLoader` | Markdown table; truncates to 100 rows; pipes in cells escaped |
| `.pdf` | `PdfLoader` | Text via `pypdfium2` (libpdfium); opt-in vision fallback for scanned PDFs |
| `.docx` | `DocxLoader` | Headings (H1–H6), paragraphs, and tables preserved |
| `.html`, `.htm` | `HtmlLoader` | Via `markdownify`; `<script>` / `<style>` / `<noscript>` stripped first |
| `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` | `ImageLoader` | **Opt-in only** (vision calls cost ~$0.10–0.50 each); enable with `[loaders.image] enabled = true` |

`raw/` is now a homogeneous markdown store regardless of original filetype — every loader converts to markdown text, the chunker sees uniform input. The sidecar (`raw/.sources.json`) records `loader: <ClassName>` per source for diagnostic visibility.

A new `InitResult.empty_load_skipped` counter tracks sources where the loader returned empty (e.g. scanned PDF without vision fallback enabled).

#### Anthropic Batch API support

`mdwiki init --bootstrap-batch` (mutually exclusive with `--bootstrap`) submits every pending source as one batch via `client.messages.batches.create`. **~50% cheaper** than the sync `--bootstrap` path, in exchange for **~1h wall-clock latency**. The CLI prints a cost estimate before submission and confirms with the user.

`Provider` ABC gains `batch_complete(requests, *, poll_interval, on_status)` and `estimate_batch_cost(requests)`. The default raises `NotImplementedError` so providers without batch APIs inherit cleanly.

#### OpenAI-compatible provider

New `OpenAICompatibleProvider` covers any endpoint that speaks OpenAI's chat-completions wire format: vLLM, llama.cpp `--api`, OpenRouter, Together, etc. — **a single adapter, configured by `base_url`**.

```toml
[llm]
provider = "openai-compatible"
model = "qwen2.5-72b-instruct"
[llm.openai_compatible]
base_url = "http://localhost:8000/v1"
api_key = "not-needed"   # optional; vLLM ignores it
vision_capable = false   # set true ONLY for vision-capable models (Qwen-VL, LLaVA)
```

Vision is opt-in via `vision_capable = true` (default raises `NotImplementedError`). `batch_complete` raises `NotImplementedError` (most local servers don't offer a batch API).

#### Vision OCR (opt-in)

`Provider.describe_image(path)` is the new vision seam. `AnthropicProvider` implements it via Claude vision (Sonnet 4.6+ supports image content blocks). Used by:

- `ImageLoader` — when `[loaders.image] enabled = true`, raster images route through `describe_image`. The model returns a heading + description + transcribed text under a `### Text` subsection.
- `PdfLoader` vision fallback — when `[loaders.pdf] vision_fallback = true` AND text extraction returns empty, every page renders to PNG via `pypdfium2` and gets fed to `describe_image`.

Both are off by default for cost.

#### `mdwiki lint --fix [=full]`

Two interactive remediation modes, both routing each fix through `IngestTransaction` so they're undoable via `mdwiki undo`:

- **default mode** — deterministic only. `broken-ref` findings get the link replaced with bare text (preserves the link's display text as prose, drops the broken target). No LLM calls.
- **`--fix=full`** — also re-ingests `stale` and `coverage-gap` findings via `ingest_source(force=True)`. One LLM call per re-ingest.

Orphans are always left alone — fixing them requires human judgment about WHERE to add cross-refs.

#### `mdwiki rebuild-log`

Recovery utility for the rare `KeyboardInterrupt`-between-DB-commit-and-log-write window noted as round-2 S6 in the v1.0 review. Reads the `events` table ordered by ts, emits one line per event with a non-null `transaction_id` (lint events are excluded by design), uses the same `- <iso_ts> [<tx_id>] <summary>` format as the live append path so the regenerated file is byte-compatible.

### Schema + prompt tuning

End-to-end Karpathy-rubric testing on a mixed-filetype corpus surfaced a regression: v1.0's "STRONG PREFERENCE FOR UPDATE" framing produced one-bloated-concept-page wikis when the LLM saw multiple sources about overlapping topics. Two structured fixes:

- **`DEFAULT_SCHEMA`** rewritten with a three-action framework (update existing concepts, REQUIRED-create entity pages for named subjects, create concept pages only for new concepts) plus an explicit "When to refuse — RARELY" section. Topical-overlap is no longer grounds for `low-quality`.
- **`INGEST_SYSTEM_PROMPT`** balanced "license to refuse" with "EXPLICIT MANDATE to create entity pages." Quote-format rules rewritten as plain prose (no nested bullets, no backticks) after the bulleted version triggered Invalid-JSON failures.

End-to-end Karpathy rubric on `~/Desktop/llm-wiki-test-v2` (13 files, 8 filetypes): **47/100 → 79/100** across 4 iterations of prompt/schema tuning.

### New dependencies

- `pypdf>=5.0.0` (PDF reading; kept for test fixture compat)
- `pypdfium2>=4.30.0` (PDF text extraction + page rendering)
- `python-docx>=1.1.0` (DOCX parsing)
- `markdownify>=0.13.0` (HTML → markdown; pulls `beautifulsoup4` transitively)
- `openai>=1.40.0` (OpenAI-compatible provider)

### Migration

v1.0 → v1.1 is a clean upgrade. Schema is forward-compatible (no breaking changes); existing wikis just gain access to new commands and loaders once the binary is upgraded. The `loader` field added to `raw/.sources.json` is additive; older sidecars without it work unchanged.

### Known limitations (deferred)

- **Streaming Batch API status** — `--bootstrap-batch` polls every 60s and reports progress but doesn't expose the underlying batch object
- **Loader registry pluggable via entry_points** — third-party loaders are v1.2+
- **Audio / video / xlsx loaders** — punted
- **Multi-provider dispatch within one wiki** — config still names ONE provider
- **`--verbose` flag** — first-time `init --bootstrap` is too silent; planned for v1.1.1

### Stats

- 387 tests (up from v1.0's 297); ruff clean
- 8 supported filetypes (up from 1)
- 2 provider implementations (anthropic + openai-compatible)
- 13 commands (`init` gains `--bootstrap-batch`; new: `rebuild-log`, `lint --fix`)

---

## v1.0.0 — Pivot to mdwiki (2026-04-30)

**The repository now ships `mdwiki`, a Karpathy-pattern LLM-maintained wiki, replacing the legacy `markdown-consolidator` CLI.** This is a v1.0.0 release under a new package name; the legacy modules (chunker, embedder, clustering, synthesis, etc.) are now internals of `mdwiki`.

### Breaking changes

- **Package rename**: `src/markdown_consolidator/` → `src/mdwiki/`. Imports change from `from markdown_consolidator import ...` to `from mdwiki import ...`.
- **CLI replacement**: `mdconsolidate` and the `mdconsolidate-*` subcommands are removed. The new entry point is `mdwiki`.
- **No backwards-compat shims**: there is no transitional alias for the old CLI. Pin to the previous release if you need it.

### What `mdwiki` ships

The Karpathy llm-wiki loop, end-to-end:

- **`mdwiki init [--bootstrap]`** — scaffold `.mdwiki/` and register every `.md` as a pending source. With `--bootstrap`, chain `ingest --pending --yes`.
- **`mdwiki ingest <source> | --pending | --all`** — interactive (or bulk) ingest. Each source goes through chunk → embed → ANN candidates → LLM call → quote verification → user approval → atomic transactional apply.
- **`mdwiki query "<q>" [--file]`** — cited Q&A drawn from existing wiki pages; `--file` files the answer as a synthesis page (channel A).
- **`mdwiki synthesize "<topic>" | --auto`** — explicit synthesis pages from a topic, or auto-discovered from cross-ref clusters (channel B).
- **`mdwiki lint`** — broken refs, orphans, stale pages, coverage gaps.
- **`mdwiki undo [N]`** — atomic rollback of the last N transactions (files + DB rows).
- **`mdwiki status` / `source` / `rebuild` / `doctor`** — read-only inspection + pre-flight tooling.

### Architecture highlights

- **Three-layer**: immutable `raw/` (with `.sources.json` sidecar) + LLM-owned `wiki/` + user-controlled `.mdwiki/schema.md`.
- **Hallucination guards**: schema-validated JSON + cite-or-refuse + quote-anchor verification + anti-sycophancy framing. LLM has explicit license to refuse low-quality / out-of-scope / duplicate sources.
- **Provider seam**: `src/mdwiki/llm/` ABC + `anthropic.py` adapter. v1.1 will add Qwen/Kimi local providers as drop-in files.
- **Atomic transactions**: every wiki write goes through `IngestTransaction` with per-tx undo snapshot directories and `transaction_inverses` SQL replay.

### Spec + plan

- Locked spec: [`docs/mdwiki-design.md`](docs/mdwiki-design.md) (v1.0.4)
- Implementation plan: `thoughts/plans/2026.04.29-bmg-mdwiki-v1.md`

### v1.1 deferred

- Multi-filetype ingest (PDF, DOCX, CSV, HTML, code, images via vision OCR)
- Batch API for bootstrap (50% cost reduction, ~1h async turnaround)
- Local model providers
- Lint `--fix` interactive remediation

---

## Pre-v1.0.0 changes (legacy `markdown-consolidator`)

The sections below document the final pre-pivot state of the legacy package. These features are preserved as internals of `mdwiki` but no longer have a public CLI surface.

## Overview

Three major features have been added to the markdown consolidator:
1. **Encapsulation Scoring & Rechunking** - [MERGED TO DEV] Analyze and improve header-content alignment
2. **Header Coherence Analysis** - [ON FEATURE BRANCH] Analyze how well headers describe their content
3. **Outline-Based Consolidation** - [ON FEATURE BRANCH] Template-driven consolidation using outline files

---

## Feature 1: Encapsulation Scoring & Rechunking (MERGED TO DEV)

### Purpose
Score how well section headers encapsulate their content using semantic similarity, and automatically split poorly-encapsulated sections using LLM.

### Merge Details
- **PR**: #1 (feature/add-encapsulation → dev)
- **Commits**: 11 commits (2647867..5a1dfb0)
- **Status**: ✅ Merged to dev branch

### Commits
- `2647867` - feat: Phase 1 - implement EncapsulationScorer class
- `2433b79` - test: Phase 1 - add tests for core encapsulation scoring
- `49ee35f` - feat: Phase 2 - implement encapsulate_sections batch method
- `73be22c` - test: Phase 2 - add tests for batch section processing
- `6334b6f` - feat: Phase 3 - add --encapsulate CLI flag
- `2809d5d` - test: Phase 3 - add test for CLI --encapsulate flag
- `3a0acea` - feat: Phase 4 - implement Rechunker for LLM-based section splitting
- `bc826d7` - test: Phase 4 - add tests for Rechunker class
- `9d893c9` - test: Phase 5 - add encapsulation workflow integration test
- `b638d06` - Merge pull request #1
- `5a1dfb0` - chore: remove .claude/ and CLAUDE.md files from repo

### Files Added

#### 1. `src/markdown_consolidator/encapsulation.py` (237 lines)
**Purpose**: Score header-content encapsulation and rechunk poorly-encapsulated sections

**Classes**:

**`EncapsulationScorer`** - Score how well headers describe content
- Uses sentence-transformers for semantic similarity
- Cosine similarity between header and content embeddings
- Configurable threshold (default: 0.5)

**Key Methods**:
```python
def score_encapsulation(*, header: str, content: str) -> float:
    """
    Compute encapsulation score between header and content.

    Returns score between 0.0 and 1.0, higher means better encapsulation.
    Uses cosine similarity of sentence embeddings.
    """
```

```python
def encapsulate_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Add encapsulation scores to all sections.

    Adds 'encapsulation_score' key to each section dict.
    """
```

**`Rechunker`** - Split poorly-encapsulated sections using LLM
- Uses Ollama API for LLM-based section splitting
- Only rechunks sections below threshold
- Generates 2-4 focused sub-sections

**Key Methods**:
```python
def rechunk_section(section: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Split a poorly-encapsulated section into multiple sections.

    Uses LLM to analyze content and create logical sub-sections.
    Returns original section if well-encapsulated (score >= threshold).
    """
```

```python
def rechunk_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Rechunk all poorly-encapsulated sections.

    Processes all sections and splits those below threshold.
    """
```

**Configuration**:
- `model_name`: HuggingFace sentence-transformers model (default: "sentence-transformers/all-MiniLM-L6-v2")
- `threshold`: Score below which section is considered poorly encapsulated (default: 0.5)
- `model`: Ollama model for rechunking (default: "llama3.2:3b")
- `base_url`: Ollama API URL (default: "http://localhost:11434")
- `timeout`: Request timeout in seconds (default: 60.0)

#### 2. `tests/test_encapsulation.py` (150 lines)
**Purpose**: Comprehensive test suite for encapsulation

**Test Coverage**:
- Basic encapsulation scoring (high vs low similarity)
- Empty header/content handling
- Batch section processing
- Rechunking workflow
- LLM integration
- JSON parsing edge cases

#### 3. `tests/test_integration.py` (64 lines)
**Purpose**: End-to-end integration tests

**Test Coverage**:
- Full encapsulation workflow
- Rechunking integration with section analysis
- Combined with clustering and manifest generation

#### 4. `tests/test_cli_commands.py` (43 lines)
**Purpose**: CLI command tests

**Test Coverage**:
- `--encapsulate` flag
- `--rechunk` flag with threshold
- Integration with analyze-sections command

### Files Modified

#### 5. `src/markdown_consolidator/cli.py`
**Changes**: Added encapsulation flags to `analyze_sections_cmd()` (35 new lines)

**New CLI Flags**:
- `--encapsulate` - Calculate encapsulation scores for each section
- `--rechunk` - Use LLM to split poorly-encapsulated sections
- `--rechunk-threshold` - Score below which to rechunk (default: 0.5)

**Integration Point**: Between keyword extraction and summarization

**Example Usage**:
```bash
# Score encapsulation only
mdconsolidate-analyze-sections ./docs --encapsulate

# Score and rechunk poor sections
mdconsolidate-analyze-sections ./docs --encapsulate --rechunk

# Custom rechunk threshold
mdconsolidate-analyze-sections ./docs --rechunk --rechunk-threshold 0.6
```

**Output Example**:
```
Analyzing /path/to/docs...
  Chunking files by H2...
  Found 45 sections
  Generating embeddings...
  Extracting keywords...
  Scoring encapsulation...
  Rechunking sections below 0.5...
  Now have 52 sections (was 45)
  Writing manifest to manifest.yaml...
```

#### 6. `src/markdown_consolidator/tree_builder.py`
**Changes**: Minor adjustment for encapsulation score handling (2 lines)

#### 7. `.gitignore`
**Changes**: Added patterns to ignore Claude AI files
```
# Claude AI
.claude/
CLAUDE.md
CLAUDE.md.*
```

### Workflow Integration

The encapsulation feature integrates into the section analysis workflow:

```
1. Chunk files by H2
2. Generate embeddings
3. Extract keywords
4. → Score encapsulation (NEW - optional)
5. → Rechunk poor sections (NEW - optional)
6. Summarize (optional)
7. Build hierarchy
8. Generate manifest
```

### Technical Details

**Encapsulation Score Calculation**:
1. Encode header and content (first 2000 chars) using sentence-transformers
2. Calculate cosine similarity: `dot(header_emb, content_emb) / (||header_emb|| * ||content_emb||)`
3. Return score between 0.0 (no similarity) and 1.0 (perfect match)

**Rechunking Process**:
1. Identify sections with `encapsulation_score < threshold`
2. Send content + header to LLM with prompt to split into 2-4 sub-sections
3. Parse JSON response with new headers and content
4. Create new section dicts with `rechunked_from` metadata
5. Replace original section with new sub-sections

**LLM Prompt Template**:
```
Analyze this documentation section and split it into logical sub-sections.

Current Header: {heading}

Content:
{content}

This section mixes multiple topics. Split it into 2-4 focused sections.

Return ONLY a JSON array with objects containing "header" and "content" keys:
[{"header": "New Header 1", "content": "Content for section 1..."}, ...]

JSON:
```

---

## Feature 2: Header Coherence Analysis (ON FEATURE BRANCH)

### Status
- **Branch**: `feature/header-coherence`
- **Not yet merged to dev**

### Purpose
Analyze markdown files to determine if section headers accurately describe the content beneath them, and generate better headers when needed.

### Commits
- `701ab5e` - feat: add header coherence analysis feature
- `298466d` - feat: add automatic header suggestion for incoherent sections
- `8bdf82c` - feat: simplify header suggestion to content-based only

### Files Added

#### 1. `src/markdown_consolidator/header_coherence.py` (668 lines)
**Purpose**: Core module for header coherence analysis and suggestion

**Key Functions**:
- `analyze_file_coherence()` - Analyze a single markdown file
  - Parameters: filepath, use_embeddings, generate_suggestions, use_llm_suggestions
  - Returns: HeaderCoherenceResult with scores and problematic sections

- `analyze_directory_coherence()` - Analyze all markdown files in a directory
  - Parameters: directory, exclude_patterns, use_embeddings, generate_suggestions, use_llm_suggestions
  - Returns: List of HeaderCoherenceResult

- `suggest_better_header()` - Generate improved header based on content
  - Two modes: keyword extraction (default) or LLM generation via Ollama
  - Parameters: content, current_header, use_llm, model
  - Returns: Suggested header string

- `generate_coherence_report()` - Create markdown report from analysis results
  - Parameters: results, output_file
  - Returns: Report string with statistics and recommendations

**Key Classes**:
- `CoherenceScore` (TypedDict): Contains similarity score, assessment, and suggestion
- `HeaderCoherenceResult` (TypedDict): Analysis result for a file

**Analysis Methods**:
- Semantic similarity using sentence-transformers embeddings
- Keyword-based similarity as fallback (TF-IDF)
- Assessment categories: excellent (>0.7), good (0.55-0.7), fair (0.45-0.55), poor (<0.45)

**Header Suggestion**:
- Keyword extraction with stopword filtering
- Technical term identification (API, SDK, CLI, etc.)
- Optional LLM generation via Ollama

#### 2. `tests/test_header_coherence.py` (549 lines)
**Purpose**: Comprehensive test suite for header coherence

**Test Coverage** (23 tests):
- Basic coherence analysis (good vs poor headers)
- Keyword extraction from various content types
- Header generation with different strategies
- LLM suggestion integration
- Directory analysis
- Edge cases: empty content, code blocks, frontmatter
- Suggestion enabling/disabling
- Report generation

**Coverage**: 82% of header_coherence.py module

### Files Modified

#### 3. `src/markdown_consolidator/__init__.py`
**Changes**: Added exports for header coherence functions
```python
from .header_coherence import (
    CoherenceScore,
    HeaderCoherenceResult,
    analyze_directory_coherence,
    analyze_file_coherence,
    generate_coherence_report,
    suggest_better_header,
)
```

#### 4. `src/markdown_consolidator/cli.py`
**Changes**: Added `analyze_coherence_cmd()` function (86 lines)

**New CLI Command**: `mdconsolidate-analyze-coherence`

**Arguments**:
- `source` - File or directory to analyze
- `--output, -o` - Output report file (markdown format)
- `--no-embeddings` - Use keyword-based similarity instead of embeddings
- `--suggest-headers` - Generate header suggestions for problematic sections
- `--use-llm` - Use LLM (Ollama) for suggestions
- `--threshold, -t` - Coherence threshold (default: 0.45)
- `--exclude, -e` - Patterns to exclude

**Example Usage**:
```bash
# Analyze single file
mdconsolidate-analyze-coherence document.md

# Analyze directory with suggestions
mdconsolidate-analyze-coherence ./docs --suggest-headers --output report.md

# Use LLM for suggestions
mdconsolidate-analyze-coherence ./docs --suggest-headers --use-llm
```

#### 5. `pyproject.toml`
**Changes**: Added CLI entry point
```toml
mdconsolidate-analyze-coherence = "markdown_consolidator.cli:analyze_coherence_cmd"
```

---

## Feature 3: Outline-Based Consolidation (ON FEATURE BRANCH)

### Status
- **Branch**: `feature/header-coherence`
- **Not yet merged to dev**

### Purpose
Implement template-based consolidation where an outline file with "Content Location:" mappings is populated with content from source files. This is "Option 2" from the LOO consolidation strategy.

### Commit
- `7e209f5` - feat: add outline-based consolidation (Option 2)

### Files Added

#### 1. `src/markdown_consolidator/outline_consolidator.py` (477 lines)
**Purpose**: Template-based consolidation using outline files

**Key Functions**:

- `parse_outline_file()` - Parse outline markdown file
  - Extracts section structure and Content Location mappings
  - Parameters: filepath
  - Returns: List of OutlineSection dicts
  - Format support: `Content Location:` or `**Content Location:**`
  - Parses file paths from: `- path/to/file.md` or `` - `path/to/file.md` ``

- `extract_content_from_sources()` - Extract content from source files
  - Uses MarkdownChunker to get H2 sections
  - Calculates relevance scores for matching
  - Parameters: source_files, source_dir, section_title
  - Returns: List of content dicts with metadata

- `_calculate_relevance()` - Match content to sections
  - Uses Jaccard similarity on normalized words
  - Filters stopwords: 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'
  - Returns: Float between 0.0 and 1.0

- `_deduplicate_content()` - Remove similar content
  - Checks substring matches
  - Checks word overlap (>80% similarity threshold)
  - Returns: Deduplicated list of content items

- `merge_content_with_outline()` - Merge sources into outline
  - Combines outline structure with extracted content
  - Adds source attribution comments
  - Supports 'comprehensive' (keep all) and 'authority' (prefer sources) strategies
  - Parameters: outline_sections, source_dir, strategy
  - Returns: Consolidated markdown string

- `consolidate_from_outline()` - Main orchestration function
  - Parses outline, extracts content, merges, writes output
  - Generates frontmatter with metadata
  - Calculates coverage percentage
  - Parameters: outline_file, source_dir, output_file, strategy
  - Returns: ConsolidationResult with statistics

**Key Classes**:
- `ContentLocationMapping` (TypedDict): Section with source file mappings
- `OutlineSection` (TypedDict): Parsed section from outline
- `ConsolidationResult` (TypedDict): Result metadata

**Outline Format**:
```markdown
## Section Title

**Content Location:**
- `path/to/source1.md`
- `path/to/source2.md`

Optional existing content in the outline.
```

**Output Format**:
```markdown
---
title: Consolidated Documentation
consolidated_from_outline: outline.md
source_directory: /path/to/sources
consolidated_at: 2026-01-01T12:00:00
strategy: comprehensive
total_sections: 10
sections_with_mappings: 8
---

## Section Title

<!-- SOURCE: path/to/source1.md, path/to/source2.md -->

### Sub-heading from Source

Content extracted from source files...

<!-- Original outline content: -->

Existing outline content (if strategy is comprehensive)...
```

#### 2. `tests/test_outline_consolidator.py` (317 lines)
**Purpose**: Comprehensive test suite for outline consolidation

**Test Coverage** (12 tests):
- Outline parsing with different formats
- Content extraction from source files
- Missing file handling
- Full consolidation workflow
- Strategy differences (comprehensive vs authority)
- Relevance scoring
- Deduplication logic
- Coverage calculation
- Output directory creation
- Edge cases (empty outlines)

**Coverage**: 96% of outline_consolidator.py module

### Files Modified

#### 3. `src/markdown_consolidator/__init__.py`
**Changes**: Added export for outline consolidation function
```python
from .outline_consolidator import consolidate_from_outline

__all__ = [
    # ... existing exports ...
    "consolidate_from_outline",
]
```

#### 4. `src/markdown_consolidator/cli.py`
**Changes**: Added `consolidate_outline_cmd()` function (76 lines)

**New CLI Command**: `mdconsolidate-from-outline`

**Arguments**:
- `outline` - Outline markdown file with Content Location mappings
- `source_dir` - Source directory containing markdown files
- `output` - Output file path
- `--strategy, -s` - Merge strategy: 'comprehensive' or 'authority' (default: comprehensive)

**Example Usage**:
```bash
# Basic usage
mdconsolidate-from-outline outline.md ./docs output.md

# Use authority strategy (prefer sources over outline content)
mdconsolidate-from-outline outline.md ./docs output.md --strategy authority

# Real-world example
mdconsolidate-from-outline LOO-Outline.md ./source-docs LOO-CONSOLIDATED.md
```

**Output Example**:
```
╔══════════════════════════════════════════════════════════╗
║  Outline-Based Consolidation                             ║
╚══════════════════════════════════════════════════════════╝
  Outline:     outline.md
  Source Dir:  ./docs
  Output:      output.md
  Strategy:    comprehensive

Parsing outline: outline.md
Found 238 sections in outline
Sections with Content Location mappings: 45
Merging content from sources in: ./docs

✓ Consolidation complete!
  Output: output.md
  Sections: 238
  Populated: 45
  Coverage: 18.9%
  Sources used: 46
```

#### 5. `pyproject.toml`
**Changes**: Added CLI entry point
```toml
mdconsolidate-from-outline = "markdown_consolidator.cli:consolidate_outline_cmd"
```

---

## Summary Statistics

### Changes Merged to Dev (Encapsulation Feature)
- **4 files** changed
- **546 lines** added, 4,240 deleted (mostly cleanup)

### Changes on Feature Branch (Header Coherence + Outline)
- **7 files** changed
- **2,210 lines** added

### Total Changes (All Features)
- **11 files** modified/added
- **2,756 lines** added
- **4,240 lines** removed (cleanup)

### Breakdown by File
| File | Lines | Status | Purpose |
|------|-------|--------|---------|
| `encapsulation.py` | 237 | ✅ Merged | Encapsulation scoring and rechunking |
| `test_encapsulation.py` | 150 | ✅ Merged | Tests for encapsulation |
| `test_integration.py` | 64 | ✅ Merged | Integration tests |
| `test_cli_commands.py` | 43 | ✅ Merged | CLI tests |
| `header_coherence.py` | 668 | 🔄 Feature branch | Header analysis and suggestion |
| `outline_consolidator.py` | 477 | 🔄 Feature branch | Template-based consolidation |
| `test_header_coherence.py` | 549 | 🔄 Feature branch | Tests for header coherence |
| `test_outline_consolidator.py` | 317 | 🔄 Feature branch | Tests for outline consolidation |
| `cli.py` | 216 | Both | CLI commands (3 new commands) |
| `__init__.py` | 16 | 🔄 Feature branch | Module exports |
| `pyproject.toml` | 2 | 🔄 Feature branch | CLI entry points |

### Test Coverage
- **Encapsulation**: Tests added, integrated
- **Header Coherence**: 23 tests, 82% coverage
- **Outline Consolidation**: 12 tests, 96% coverage
- **Total**: 35+ new tests

### New CLI Commands
1. `mdconsolidate-analyze-sections --encapsulate --rechunk` - Score and rechunk sections (✅ merged)
2. `mdconsolidate-analyze-coherence` - Analyze header coherence (🔄 on branch)
3. `mdconsolidate-from-outline` - Template-based consolidation (🔄 on branch)

---

## Relationship Between Features

### Encapsulation vs Header Coherence

While both features analyze header-content relationships, they serve different purposes:

| Aspect | Encapsulation | Header Coherence |
|--------|---------------|------------------|
| **Purpose** | Find sections mixing multiple topics | Find headers that don't describe content |
| **Method** | Semantic similarity (embeddings) | Semantic or keyword similarity |
| **Action** | Split into sub-sections | Suggest better headers |
| **Use Case** | Improve section focus | Improve header clarity |
| **Integration** | Part of analyze-sections workflow | Standalone analysis tool |
| **Output** | Rechunked sections | Coherence report + suggestions |

### Potential Combined Usage

These features can complement each other:

```bash
# 1. First, improve section focus with encapsulation
mdconsolidate-analyze-sections ./docs --rechunk --output manifest-rechunked.yaml

# 2. Then, analyze header quality
mdconsolidate-analyze-coherence ./docs --suggest-headers --output coherence-report.md

# 3. Finally, consolidate using outline
mdconsolidate-from-outline outline.md ./docs consolidated.md
```

---

## Integration with Existing Components

All features leverage existing markdown-consolidator components:

### Shared Dependencies
- `MarkdownChunker` - For extracting H2 sections
- `sentence-transformers` - For semantic similarity
- `Ollama API` - For LLM-based operations (optional)

### No Breaking Changes
- All changes are additive
- Existing functionality unchanged
- Backward compatible
- New optional dependencies handled gracefully

---

## Next Steps

### To Use Merged Features (Dev Branch)
```bash
# Already on dev branch with encapsulation feature
mdconsolidate-analyze-sections ./docs --encapsulate --rechunk
```

### To Use Feature Branch Features
```bash
# Switch to feature branch
git checkout feature/header-coherence

# Use header coherence
mdconsolidate-analyze-coherence ./docs --suggest-headers

# Use outline consolidation
mdconsolidate-from-outline outline.md ./docs output.md
```

### To Merge Feature Branch to Dev
```bash
# Ensure feature branch is up to date with dev
git checkout feature/header-coherence
git merge dev  # May need to resolve conflicts
git push origin feature/header-coherence

# Create pull request to merge into dev
```

---

## Dependencies

### No New Required Dependencies
All features use existing dependencies:
- `pyyaml>=6.0`
- `sentence-transformers>=2.2.0` (for embeddings)
- `numpy>=1.24.0`
- `scikit-learn>=1.3.0`
- `httpx>=0.25.0` (for Ollama API)

### Optional
- Ollama with llama3.2:3b model (for LLM features)

---

## Documentation

All new functions include:
- Comprehensive docstrings
- Parameter descriptions with types
- Return value documentation
- Usage examples
- Type hints throughout

---

## Testing

Run tests for features:

```bash
# All tests
pytest

# Encapsulation tests (merged)
pytest tests/test_encapsulation.py -v
pytest tests/test_integration.py -v

# Header coherence tests (feature branch)
git checkout feature/header-coherence
pytest tests/test_header_coherence.py -v

# Outline consolidation tests (feature branch)
pytest tests/test_outline_consolidator.py -v

# With coverage
pytest --cov=markdown_consolidator --cov-report=term-missing
```

---

## Repository Status

### Main/Dev Branch
- ✅ Encapsulation scoring and rechunking feature
- ✅ Tests passing
- ✅ Ready for use

### Feature Branch (feature/header-coherence)
- 🔄 Header coherence analysis
- 🔄 Outline-based consolidation
- 🔄 Tests passing
- 🔄 Ready for merge review

### Clean-up Completed
- ✅ Removed `.claude/` directory
- ✅ Removed `CLAUDE.md` files
- ✅ Updated `.gitignore`

---

## Contact

For questions about these changes:
- Review test files for usage examples
- Check function docstrings for detailed documentation
- See CLI help: `mdconsolidate-[command] --help`
