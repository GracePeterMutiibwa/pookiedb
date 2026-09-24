"""Deterministic fake embedder shared by the search tests."""

import hashlib
import math
import re

DIMS = 64

# Words that should land close together, so "money back" ~ "refund"
SYNONYMS = {
    "money": "refund", "reimbursement": "refund", "refunds": "refund", "back": "refund",
    "shipping": "delivery", "shipped": "delivery", "courier": "delivery",
    "login": "account", "password": "account", "signin": "account",
}


def fake_vector(text: str, dims: int = DIMS) -> list[float]:
    vec = [0.0] * dims
    for word in re.findall(r"\w+", text.lower()):
        word = SYNONYMS.get(word, word)
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % dims] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeEmbedder:
    """embed(texts) -> vectors, recording every call."""

    def __init__(self):
        self.calls = []
        self.fail_with = None

    def __call__(self, texts):
        self.calls.append(list(texts))
        if self.fail_with is not None:
            raise self.fail_with
        return [fake_vector(t) for t in texts]
