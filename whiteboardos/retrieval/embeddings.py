"""Embedding providers for WhiteBoardOS.

True semantic vectors come from an optional local Ollama embedding model. The
stdlib-only hashed provider is a deterministic fallback so retrieval continues
to work before an embedding model is installed.

The Ollama provider deliberately requests a long keep-alive and exposes a
``warm`` operation. WhiteBoardOS can therefore preload the embedding model at
boot and avoid making the user's first semantic query pay model-load latency.
"""
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.-]{1,}")
DEFAULT_MODEL = os.environ.get("WHITEBOARD_EMBED_MODEL", "all-minilm:latest")
DEFAULT_KEEP_ALIVE = os.environ.get("WHITEBOARD_EMBED_KEEP_ALIVE", "-1")


def normalize(vector):
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    if not norm:
        return [0.0 for _ in vector]
    return [float(x) / norm for x in vector]


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class HashedEmbedder:
    """Dependency-free lexical fallback, clearly reported as non-semantic."""
    provider = "hashed-fallback"
    model = "whiteboard-hash-v1"
    dimensions = 384
    semantic = False

    @staticmethod
    def _features(text):
        tokens = [m.group(0).casefold() for m in TOKEN_RE.finditer(text)]
        for token in tokens:
            yield token
        for left, right in zip(tokens, tokens[1:]):
            yield left + "::" + right

    def embed(self, text):
        counts = Counter(self._features(text))
        vector = [0.0] * self.dimensions
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            number = int.from_bytes(digest, "big")
            index = number % self.dimensions
            sign = -1.0 if (number >> 10) & 1 else 1.0
            vector[index] += sign * (1.0 + math.log(count))
        return normalize(vector)


class OllamaEmbedder:
    provider = "ollama"
    semantic = True

    def __init__(self, model=DEFAULT_MODEL, base_url="http://127.0.0.1:11434", timeout=30,
                 keep_alive=DEFAULT_KEEP_ALIVE):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.dimensions = 0

    def available(self):
        try:
            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=3) as response:
                payload = json.load(response)
            names = {item.get("name", "") for item in payload.get("models", [])}
            return self.model in names or self.model.removesuffix(":latest") in names
        except Exception:
            return False

    def _post(self, endpoint, payload):
        request = urllib.request.Request(
            self.base_url + endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def embed(self, text):
        # Do not send per-request keep_alive here. Older Ollama builds accept the
        # embedding endpoints but reject keep_alive with HTTP 400. Residency is
        # controlled server-side with OLLAMA_KEEP_ALIVE, which works across both
        # the old /api/embeddings and new /api/embed APIs.
        # Newer Ollama API.
        try:
            payload = self._post("/api/embed", {"model": self.model, "input": text, "truncate": True})
            values = (payload.get("embeddings") or [None])[0]
            if values:
                self.dimensions = len(values)
                return normalize(values)
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            pass
        # Compatibility with older Ollama releases.
        payload = self._post("/api/embeddings", {"model": self.model, "prompt": text})
        values = payload.get("embedding")
        if not values:
            raise ValueError("Ollama embedding response contained no vector")
        self.dimensions = len(values)
        return normalize(values)

    def warm(self):
        """Load the model now so the first user query does not pay cold-start cost."""
        start = time.monotonic()
        vector = self.embed("WhiteBoardOS semantic retrieval warmup")
        return {
            "ok": True,
            "provider": self.provider,
            "model": self.model,
            "dimensions": len(vector),
            "keep_alive": self.keep_alive,
            "elapsed_ms": round((time.monotonic() - start) * 1000, 2),
        }


class EmbeddingRouter:
    """Choose local semantic embeddings when installed, otherwise fallback cleanly."""
    def __init__(self, model=DEFAULT_MODEL):
        self.ollama = OllamaEmbedder(model=model)
        self.fallback = HashedEmbedder()
        self._semantic_available = None
        self._checked_at = 0.0
        self.last_error = None
        self.last_warm = None

    def _use_semantic(self, force_check=False):
        now = time.monotonic()
        if force_check or self._semantic_available is None or now - self._checked_at > 60:
            self._semantic_available = self.ollama.available()
            self._checked_at = now
        return self._semantic_available

    def embed(self, text):
        if self._use_semantic():
            try:
                vector = self.ollama.embed(text)
                self.last_error = None
                return {
                    "vector": vector,
                    "provider": self.ollama.provider,
                    "model": self.ollama.model,
                    "semantic": True,
                }
            except Exception as exc:
                self.last_error = type(exc).__name__ + ": " + str(exc)[:120]
                self._semantic_available = False
        vector = self.fallback.embed(text)
        return {
            "vector": vector,
            "provider": self.fallback.provider,
            "model": self.fallback.model,
            "semantic": False,
        }

    def warm(self):
        if not self._use_semantic(force_check=True):
            result = {
                "ok": False,
                "provider": self.fallback.provider,
                "model": self.fallback.model,
                "error": "Semantic embedding model is not installed or Ollama is unavailable",
            }
            self.last_warm = result
            return result
        try:
            result = self.ollama.warm()
            self.last_error = None
            self._semantic_available = True
        except Exception as exc:
            self.last_error = type(exc).__name__ + ": " + str(exc)[:160]
            self._semantic_available = False
            result = {"ok": False, "provider": "ollama", "model": self.ollama.model,
                      "error": self.last_error}
        self.last_warm = result
        return result

    def target_identity(self):
        """Return the provider/model new file embeddings should use right now."""
        if self._use_semantic():
            return {"provider": self.ollama.provider, "model": self.ollama.model, "semantic": True}
        return {"provider": self.fallback.provider, "model": self.fallback.model, "semantic": False}

    def status(self):
        available = self._use_semantic()
        return {
            "semantic_provider_available": bool(available),
            "preferred_model": self.ollama.model,
            "active_provider": "ollama" if available else self.fallback.provider,
            "fallback_model": self.fallback.model,
            "keep_alive": self.ollama.keep_alive,
            "last_error": self.last_error,
            "last_warm": self.last_warm,
        }
