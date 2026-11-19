"""Compatibility entry point: `python query.py ...` forwards to `rag search`."""

from __future__ import annotations

import sys

from rag.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["search", *sys.argv[1:]]))
