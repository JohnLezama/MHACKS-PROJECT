# WhiteBoardOS Retrieval/Action Agent v2.7

This build closes three gaps found by the natural-language full-stack test:

- folder-aware hierarchical retrieval (`school classes` -> `Classes/`)
- safe action execution from the same ordinary-language prompt
- warm-on-fallback instead of paying an Ollama warmup on every invocation

It also adds non-destructive file consolidation and keeps all action mutations inside the WhiteBoardOS catalog/sync path.

Run `wb-full-test` for the standardized end-to-end demo or pass any ordinary prompt as its argument.
