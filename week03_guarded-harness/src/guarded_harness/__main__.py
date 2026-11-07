"""Module entrypoint for ``python -m guarded_harness``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
