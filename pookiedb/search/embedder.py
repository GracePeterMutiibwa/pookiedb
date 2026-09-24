import json
import math
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from pookiedb.exceptions import EmbeddingError


@dataclass
class EmbeddingConfig:
    embed: Callable[[list], list]
    dimensions: int
    batch_size: int
    min_similarity: float


_config: Optional[EmbeddingConfig] = None


def embeddings(
    embed: Callable[[list], list],
    *,
    dimensions: int,
    batch_size: int = 100,
    min_similarity: float = 0.3,
):
    """
    Register the embedding callback used by searchable models.
    Call it before your models are defined (e.g. right after pookie.connect()).

    Contract for `embed`:
        embed(texts: list[str]) -> list[list[float]]
        - one vector per text, in the same order
        - every vector has exactly `dimensions` floats
        - anything else (an exception, None, wrong shape) raises EmbeddingError

    Usage:
        pookie.embeddings(my_embed, dimensions=1536)
        pookie.embeddings(pookie.openai_compatible(base_url, api_key, model), dimensions=1536)

    `min_similarity` is the default cosine-similarity cutoff for semantic search.
    It depends on the embedding model, so it lives here rather than on each query.
    """
    global _config
    if not callable(embed):
        raise TypeError("embeddings() expects a callable: embed(texts) -> list of vectors.")
    if not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError("dimensions must be a positive integer.")
    if batch_size < 1:
        raise ValueError("batch_size must be a positive integer.")
    _config = EmbeddingConfig(embed, dimensions, batch_size, min_similarity)


def get_config() -> Optional[EmbeddingConfig]:
    return _config


def reset():
    """Forget the registered embedding callback (mostly for tests)."""
    global _config
    _config = None


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Call the user's callback in batches and enforce the contract."""
    cfg = _config
    if cfg is None:
        raise EmbeddingError("No embedding callback registered. Call pookie.embeddings() first.")

    vectors = []
    for i in range(0, len(texts), cfg.batch_size):
        batch = texts[i : i + cfg.batch_size]
        try:
            result = cfg.embed(list(batch))
        except Exception as e:
            raise EmbeddingError(f"embed() raised {e.__class__.__name__}: {e}", cause=e) from e
        vectors.extend(_validate(result, len(batch), cfg.dimensions))
    return vectors


def _validate(result, expected: int, dimensions: int) -> list[list[float]]:
    if result is None:
        raise EmbeddingError("embed() returned None.")
    if not isinstance(result, (list, tuple)):
        raise EmbeddingError(f"embed() must return a list of vectors, got {type(result).__name__}.")
    if len(result) != expected:
        raise EmbeddingError(f"embed() returned {len(result)} vectors for {expected} texts.")

    clean = []
    for n, vec in enumerate(result):
        if not isinstance(vec, (list, tuple)):
            raise EmbeddingError(f"Vector {n} must be a list of floats, got {type(vec).__name__}.")
        if len(vec) != dimensions:
            raise EmbeddingError(
                f"Vector {n} has {len(vec)} dimensions, expected {dimensions}."
            )
        for x in vec:
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
                raise EmbeddingError(f"Vector {n} contains a non-numeric or non-finite value: {x!r}.")
        clean.append([float(x) for x in vec])
    return clean


def openai_compatible(
    base_url: str,
    api_key: str = None,
    model: str = None,
    *,
    dimensions: int = None,
    timeout: float = 30,
) -> Callable[[list], list]:
    """
    Build an embed() callback for any OpenAI-compatible /embeddings endpoint
    (OpenAI, OpenRouter, Ollama, vLLM, LM Studio, ...). Uses only the standard library.

    `dimensions` is sent to the API when given (for models that can shorten vectors,
    e.g. text-embedding-3-*); it doesn't replace pookie.embeddings(dimensions=...).
    """
    url = base_url.rstrip("/") + "/embeddings"

    def embed(texts: list[str]) -> list[list[float]]:
        body = {"model": model, "input": texts}
        if dimensions:
            body["dimensions"] = dimensions
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
        data = sorted(payload["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    return embed
