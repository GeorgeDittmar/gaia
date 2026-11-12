"""Local embedding via sentence-transformers.

Uses ``all-MiniLM-L6-v2`` — 23 MB, 384-dim, runs fast on CPU.
Downloads on first import if not cached.

All stores call ``generate_embedding(text)`` → list[float].
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import List

logger = logging.getLogger("gaia.embedding")

_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None


def _load_model():
    """Load (or reuse) the sentence-transformers model."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("loading embedding model '%s' …", _MODEL_NAME)
            _model = SentenceTransformer(_MODEL_NAME)
        except ImportError:
            raise RuntimeError(
                "sentence_transformers is not installed. "
                "Install it with: uv add sentence-transformers"
            )
    return _model


@lru_cache(maxsize=4096)
def generate_embedding(text: str) -> List[float]:
    """Return a 384-dim embedding for *text*.

    Results are cached (lru_cache) so repeated queries are free.
    Text is stripped and lowercased before embedding for cache hits.
    """
    text = text.strip()
    if not text:
        # Return zero vector for empty text
        return [0.0] * 384

    model = _load_model()
    vec = model.encode(text, show_progress_bar=False, normalize_embeddings=True)
    return vec.tolist()


def embedding_dim() -> int:
    """Return the dimensionality of embeddings."""
    return 384
