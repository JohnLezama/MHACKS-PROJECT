"""WhiteBoardOS retrieval stack.

The package is intentionally split into small layers so failures can be isolated:
- embeddings.py: file/query vectors and provider fallback
- graph.py: deterministic file-to-file relationships
- ranker.py: hybrid scoring + graph expansion
- context.py: compact LLM-facing JSON packets
- service.py: orchestration and SQLite persistence
"""
from .service import RetrievalService

__all__ = ["RetrievalService"]
