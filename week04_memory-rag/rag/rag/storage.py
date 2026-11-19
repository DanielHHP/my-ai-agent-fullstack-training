"""Index-store manifest handling and validation."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import IndexError

SCHEMA_VERSION = 1
MANIFEST_FILE = "manifest.json"
BM25_NODES_FILE = "bm25_nodes.json"


def load_manifest(store_dir: Path) -> dict[str, Any]:
    path = store_dir / MANIFEST_FILE
    if not path.is_file():
        raise IndexError(
            f"Index manifest is missing under {store_dir}. Run `rag build` first."
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise IndexError(f"Index manifest is unreadable: {path}") from exc

    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise IndexError(
            f"Unsupported index schema {manifest.get('schema_version')!r}; rebuild the index."
        )
    return manifest


def validate_backend(store_dir: Path, manifest: dict[str, Any], backend: str) -> None:
    backends = set(manifest.get("backends", []))
    if backend not in backends:
        raise IndexError(
            f"The persisted index does not contain the `{backend}` backend. "
            f"Available backends: {', '.join(sorted(backends)) or 'none'}."
        )
    if backend == "bm25" and not (store_dir / BM25_NODES_FILE).is_file():
        raise IndexError(f"BM25 node file is missing under {store_dir}.")


def write_manifest(store_dir: Path, manifest: dict[str, Any]) -> None:
    path = store_dir / MANIFEST_FILE
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{MANIFEST_FILE}.",
        suffix=".tmp",
        dir=store_dir,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
