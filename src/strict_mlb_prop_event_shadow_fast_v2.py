"""Compatibility entrypoint for Railway MLB prop hot shards.

Some Railway services still launch this historical ``fast_v2`` module directly.
Route that command to the validated persistent hot layer and install the lossless
Polymarket/Kalshi event fan-in first. This keeps behavior identical regardless of
which compatibility command Railway has snapshotted.

Read-only: no order manager or order transport is imported here.
"""
from __future__ import annotations

from src import strict_mlb_prop_event_shadow as hot
from src.lossless_event_fanin import install

install(hot)

from src.strict_mlb_prop_event_shadow_persistent import main


if __name__ == "__main__":
    main()
