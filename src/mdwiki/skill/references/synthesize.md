# Synthesize

```bash
mdwiki synthesize "fine-tuning vs RAG decision framework"   # explicit topic
mdwiki synthesize --auto                                     # walk the cross-ref graph, propose one page per cluster
```

A synthesis page braids ≥ 3 existing pages into a comparison, framework, or through-line; it cites them with relative links and lands in `wiki/syntheses/`. The model may refuse with `INSUFFICIENT_COVERAGE:` or `DUPLICATE_OF:`; a refusal is a valid outcome. `--auto` evaluates connected clusters of mutually linked pages and only proposes a page when the cluster shares a non-obvious through-line.
