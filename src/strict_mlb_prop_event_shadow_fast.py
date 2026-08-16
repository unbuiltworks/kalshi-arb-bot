"""Compatibility entrypoint for the isolated MLB prop hot scanner.

The Railway shard services already point at this module. Keep that stable command,
install the lossless two-venue event fan-in, and route every shard through the
persistent hot layer: subscribe exact counterparts once, refresh participation in the
background, and evaluate fresh cached books only on relevant events.

Read-only: no order manager or order transport is imported here.
"""
from __future__ import annotations

from src import strict_mlb_prop_event_shadow as hot
from src.lossless_event_fanin import install

install(hot)

from src.strict_mlb_prop_event_shadow_persistent import main


if __name__ == "__main__":
    main()
