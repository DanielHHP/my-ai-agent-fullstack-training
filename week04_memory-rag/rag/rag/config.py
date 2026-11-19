"""Project paths and environment-backed runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DEFAULT_CORPUS = ROOT / "corpus"
DEFAULT_STORE = ROOT / "storage"

DASHSCOPE_API_KEY_ENV = "DASHSCOPE_API_KEY"
DASHSCOPE_EMBEDDING_MODEL = os.getenv(
    "DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v3"
)
DASHSCOPE_EMBEDDING_BATCH_SIZE = int(
    os.getenv("DASHSCOPE_EMBEDDING_BATCH_SIZE", "25")
)

DEFAULT_CHUNK_SIZE = 384
DEFAULT_CHUNK_OVERLAP = 0
DEFAULT_TOP_K = 5
DEFAULT_CANDIDATE_K = 20
RRF_K = 60


def dashscope_api_key() -> str:
    """Return a non-empty DashScope API key or fail with actionable guidance."""
    key = os.getenv(DASHSCOPE_API_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"{DASHSCOPE_API_KEY_ENV} is not set. "
            "Put it in .env or export it before using vector/hybrid retrieval. "
            "BM25-only workflows do not require a key."
        )
    return key
