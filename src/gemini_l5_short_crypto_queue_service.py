"""READ-ONLY BTC 5m + 15m entrypoint for the Gemini L5 queue shadow.

This wraps the existing queue/multi-fill research service and only replaces its
public-symbol generator. Trading remains impossible: no credentials or order
transport are introduced.
"""
from __future__ import annotations

from src import gemini_l5_queue_service as queue_service
from src import gemini_l5_shadow_v2 as gs
from src.gemini_l5_short_crypto_universe import symbols


def main() -> int:
    gs.nearest_windows = symbols
    return queue_service.main()


if __name__ == "__main__":
    raise SystemExit(main())
