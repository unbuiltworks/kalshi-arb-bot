"""Compatibility entrypoint for the isolated MLB prop hot scanner.

The Railway shard services already point at this module. Keep that stable command and
route it through the corrected v2 wrapper. Install the lossless two-venue fan-in first
so simultaneous Polymarket/Kalshi wakeups cannot discard a market trigger.

Read-only: no order manager or order transport is imported here.
"""
from __future__ import annotations

from src import strict_mlb_prop_event_shadow as hot
from src.lossless_event_fanin import install

install(hot)

from src.strict_mlb_prop_event_shadow_fast_v2 import main


if __name__ == "__main__":
    main()
