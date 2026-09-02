# The llm-wiki pattern

## Idea

An llm-wiki is a folder of immutable raw sources next to a wiki that a language model maintains on your behalf. The wiki is a persistent, compounding artifact: each new source is woven into existing pages instead of being filed as a standalone summary.

## Three layers

The pattern has three layers. Raw sources are never edited after they are registered. Wiki pages are owned by the model and rewritten whenever a source changes what they should say. A schema document written by the human decides which page kinds exist and when the model may create, update, or refuse.

## Origin

The pattern was described in Andrej Karpathy's llm-wiki gist in 2026, which framed the wiki as the model's long-term memory for a corpus.
