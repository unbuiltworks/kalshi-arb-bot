"""Compatibility entrypoint for the isolated MLB prop hot scanner.

The Railway shard services already point at this module. Keep that stable command and
route it through the corrected v2 wrapper, where REST seeding is warm-start only and
every participation-cleared counterpart is subscribed on both venue WebSockets.

Read-only: no order manager or order transport is imported here.
"""
from __future__ import annotations

from src.strict_mlb_prop_event_shadow_fast_v2 import main


if __name__ == "__main__":
    main()
