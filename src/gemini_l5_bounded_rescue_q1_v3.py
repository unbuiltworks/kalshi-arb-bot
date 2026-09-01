"""Low-latency bounded rescue entry point for Gemini crypto-5m q1.

V2 proved the 5c FOK loss cap and exact-id cancellation gates live, but its first
canary also proved that waiting through profit/breakeven maker stages is too slow
for a volatile five-minute book.  V3 keeps every V2 safety invariant while changing
one priority after the first authoritative fill:

    cancel exact resting sibling -> prove inactive/race-reconcile -> bounded FOK

There is no breakeven-maker repost before the emergency decision.  The launcher
also shortens the pre-fill quote TTL so an unfilled pair is cancelled and rescanned
instead of remaining stale long enough to be picked off.

This module intentionally reuses V2's tested FOK implementation and V1's fee/loss
math.  No wider size, no broader cancellation, and no higher loss cap are added.
"""
from __future__ import annotations

from typing import Any

from src import gemini_l5_pair_q1 as pair
from src import gemini_l5_bounded_rescue_q1 as v1
from src import gemini_l5_bounded_rescue_q1_v2 as v2


async def _skip_breakeven_maker(*args: Any, **kwargs: Any) -> None:
    """V3 goes directly from exact-sibling cancellation to bounded FOK."""
    return None


def install() -> None:
    """Install V2 safety hooks, then remove delay-only maker rescue stages."""
    v2.install()
    # Stage-1 wait is intentionally zero.  V2 still cancels the exact sibling,
    # proves it inactive and performs the authoritative terminal-race check before
    # any FOK client ID can exist.
    v1.PROFIT_STAGE_SECONDS = 0.0
    v1.BREAKEVEN_STAGE_SECONDS = 0.0
    v2._post_breakeven_maker = _skip_breakeven_maker
    pair._dynamic_complement_rescue = v2._dynamic_complement_rescue


def main() -> None:
    install()
    from src import gemini_l5_dynamic_q1 as owner

    owner.MATCHED_STATUSES = frozenset(
        set(owner.MATCHED_STATUSES) | {"MATCHED_BOUNDED_FOK"}
    )
    owner.main()


if __name__ == "__main__":
    main()
