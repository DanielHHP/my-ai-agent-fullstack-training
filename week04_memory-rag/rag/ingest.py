"""Compatibility entry point: `python ingest.py ...` forwards to `rag build`."""

from __future__ import annotations

import sys

from rag.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["build", *sys.argv[1:]]))
