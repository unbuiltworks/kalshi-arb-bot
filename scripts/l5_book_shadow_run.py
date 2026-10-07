"""Entrypoint for the TWO-TOKEN BOOK-STATE SHADOW COLLECTOR. READ-ONLY.

It evaluates the blocking boot gate first (see `src.l5_book_shadow.boot_gate`)
and exits 2 WITHOUT ISSUING A REQUEST if any order-capable module is
importable in the image. Exit 0 from `--gate-only` still means NO DATA AND NO
ORDER; this process can never place one.

    python3 -m scripts.l5_book_shadow_run --gate-only
    python3 -m scripts.l5_book_shadow_run              # runs forever

Configuration is environment-only (names in
`analysis/l5/book_shadow/RUNBOOK.md`); credentials are never required and are
reported by presence alone.
"""
from __future__ import annotations

import sys

from src.l5_book_shadow import main

if __name__ == "__main__":
    sys.exit(main())
