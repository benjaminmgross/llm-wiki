"""mdwiki — folder-local CLI that turns any directory of markdown into an LLM-maintained wiki.

Built on Karpathy's llm-wiki pattern. v1.0.0 replaced the legacy markdown-consolidator
CLI; legacy modules (chunker, clustering, synthesis, etc.) became internals. v1.1.0
adds multi-filetype ingest (md/txt/code/csv/pdf/docx/html/image), Anthropic Batch API
support for `init --bootstrap-batch`, an OpenAI-compatible provider for vLLM/llama.cpp/
OpenRouter/Together, interactive `lint --fix`, and the `rebuild-log` recovery utility.
v1.2.0 adds corpus-aware profiles (init --profile=initiative|transcripts|framework),
fallback chunker tiers, transcripts-aware quote-anchor mode, page version chain
(previous_hash:), profile-aware plan validation, runtime `mdwiki skill`, and Phase-5
quality primitives (rejections, cost_ledger, [unverified-quote] lint).
"""

__version__ = "1.2.0"

from .chunker import MarkdownChunker, Section
from .clustering import cluster_files
from .consolidator import ConsolidationResult, consolidate
from .inventory import analyze_file, inventory_directory
from .relationships import analyze_relationships
from .synthesis import synthesize_cluster

__all__ = [
    "__version__",
    "MarkdownChunker",
    "Section",
    "cluster_files",
    "ConsolidationResult",
    "consolidate",
    "analyze_file",
    "inventory_directory",
    "analyze_relationships",
    "synthesize_cluster",
]
