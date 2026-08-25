"""Fill-conversion hotfix: executable proximity first with bounded sticky lifecycle.

The prior v2 wrapper accidentally hard-coded a six-hour maker lifecycle. That could
occupy the only live maker slot on a quote many cents from the contra ask even though
the inner loop was otherwise healthy. This version keeps the account-authoritative
startup cleanup and all existing economics/risk gates, but:

* caps one resting maker lifecycle at 180 seconds;
* admits live maker rows only when the safe quote is within two reported ticks of
  the current contra ask;
* keeps price distance first and activity only as a tie-breaker;
* derives an exact q1 taker+taker census from the same two-sided book snapshot,
  including both current rounded taker fees, without extra market reads;
* preserves exact >=3c after-fee floor, q1 sizing, participation certification,
  one-maker exposure, hedge rechecks, flatten paths, and shared $5 risk ceiling.

A quote farther than two ticks may still be mathematically valid, but it is not a
useful live fill-conversion candidate and does not consume the maker slot.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import src.sports_poly_live_fill_conversion_hotfix as base
import src.sports_poly_structural_live_supervisor_recovered as recovered
from src.sports_poly_current_fees import taker_fee
from src.sports_poly_fast_orphan_cleanup import cleanup_current_revenue_orders_fast

STICKY_LIFECYCLE_SECONDS = 180.0
MAX_FILLABLE_GAP_TICKS = base.D("2")
TAKER_TAKER_TARGET = base.D("0.03")


def _near_touch_activity_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    maker = base._d(row.get("activity_maker_shares"))
    hedge = base._d(row.get("activity_hedge_shares"))
    combined = maker + hedge
    both = min(maker, hedge)
    gap = base._d(row.get("maker_gap_to_ask") or "1")
    tick = base._d(row.get("reported_maker_tick") or "0.01")
    if tick <= 0:
        tick = base.D("0.01")
    near_touch = gap <= tick
    return (
        not near_touch,
        gap,
        -both,
        -combined,
        not bool(row.get("improves_bid")),
        -base._d(row.get("hedge_depth")),
        -base._d(row.get("quote_locked_profit")),
    )


def _fillable_gap(row: Mapping[str, Any]) -> bool:
    gap = base._d(row.get("maker_gap_to_ask") or "1")
    tick = base._d(row.get("reported_maker_tick") or "0")
    if tick <= 0 or gap <= 0:
        return False
    return gap <= tick * MAX_FILLABLE_GAP_TICKS


def _taker_taker_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return exact q1 YES(low)+NO(high) FOK/FOK candidates from one book snapshot.

    Each strict pair normally yields two maker-frontier directions. The low-maker
    direction exposes low-YES ask + high-NO ask/depth; the reverse direction exposes
    low-YES depth. Requiring both directions therefore gives both executable q1 depths
    without another book request. Fee math uses the audited current rounded taker fee
    helper on both legs.
    """
    by_pair: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        low_slug = str(row.get("low_slug") or "")
        high_slug = str(row.get("high_slug") or "")
        direction = str(row.get("direction") or "")
        if not low_slug or not high_slug or direction not in {
            "MAKER_LOW_HEDGE_HIGH",
            "MAKER_HIGH_HEDGE_LOW",
        }:
            continue
        by_pair.setdefault((low_slug, high_slug), {})[direction] = row

    out: list[dict[str, Any]] = []
    for (low_slug, high_slug), directions in by_pair.items():
        low_row = directions.get("MAKER_LOW_HEDGE_HIGH")
        high_row = directions.get("MAKER_HIGH_HEDGE_LOW")
        if low_row is None or high_row is None:
            continue
        try:
            low_yes_ask = base._d(low_row.get("maker_ask"))
            high_no_ask = base._d(low_row.get("hedge_ask"))
            high_no_depth = base._d(low_row.get("hedge_depth"))
            low_yes_depth = base._d(high_row.get("hedge_depth"))
            reverse_high_no_ask = base._d(high_row.get("maker_ask"))
            reverse_low_yes_ask = base._d(high_row.get("hedge_ask"))
            if (
                low_yes_ask <= 0
                or high_no_ask <= 0
                or low_yes_depth < base.D("1")
                or high_no_depth < base.D("1")
            ):
                continue
            # Fail closed if the two directional rows are not the same book snapshot.
            if reverse_high_no_ask != high_no_ask or reverse_low_yes_ask != low_yes_ask:
                continue
            low_fee = taker_fee(1, low_yes_ask)
            high_fee = taker_fee(1, high_no_ask)
            all_in = low_yes_ask + high_no_ask + low_fee + high_fee
            locked = base.D("1") - all_in
        except Exception:
            continue
        if locked < TAKER_TAKER_TARGET:
            continue
        out.append(
            {
                "low_slug": low_slug,
                "high_slug": high_slug,
                "family": low_row.get("family"),
                "player": low_row.get("player"),
                "low_yes_ask": low_yes_ask,
                "low_yes_depth": low_yes_depth,
                "high_no_ask": high_no_ask,
                "high_no_depth": high_no_depth,
                "low_taker_fee": low_fee,
                "high_taker_fee": high_fee,
                "all_in_cost": all_in,
                "locked_profit": locked,
            }
        )
    out.sort(
        key=lambda r: (
            -base._d(r.get("locked_profit")),
            -min(base._d(r.get("low_yes_depth")), base._d(r.get("high_no_depth"))),
        )
    )
    return out


async def _fillable_ranked(
    feed: Mapping[str, Mapping[str, Any]],
    pairs: list[tuple[str, str]],
    *,
    require_near_touch: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, health = await base._activity_ranked(
        feed,
        pairs,
        require_near_touch=require_near_touch,
    )
    taker_rows = _taker_taker_rows(rows)
    admitted = [row for row in rows if _fillable_gap(row)]
    health = dict(health)
    health["pre_fillability_rows"] = len(rows)
    health["fillable_rows"] = len(admitted)
    health["far_gap_rows_suppressed"] = len(rows) - len(admitted)
    health["max_fillable_gap_ticks"] = str(MAX_FILLABLE_GAP_TICKS)
    health["taker_taker_3c_candidates"] = len(taker_rows)
    if taker_rows:
        best = taker_rows[0]
        health["taker_taker_best_locked_profit"] = str(best["locked_profit"])
        health["taker_taker_best_low_slug"] = str(best["low_slug"])
        health["taker_taker_best_high_slug"] = str(best["high_slug"])
    # Log only broad scans; persistence checks on one pair would otherwise spam.
    if len(pairs) > 10:
        print(
            "SPORTS_POLY_MLB_TAKER_TAKER_SCAN "
            + json.dumps(
                {
                    "certified_pairs_scanned": len(pairs),
                    "candidate_count": len(taker_rows),
                    "target_after_fee": str(TAKER_TAKER_TARGET),
                    "best": (
                        {
                            k: str(v) if isinstance(v, base.D) else v
                            for k, v in taker_rows[0].items()
                        }
                        if taker_rows
                        else None
                    ),
                    "order_entry": False,
                    "realized": False,
                },
                separators=(",", ":"),
                default=str,
            ),
            flush=True,
        )
    return admitted, health


def _cleanup_clear(result: Mapping[str, Any]) -> bool:
    return int(result.get("unresolved") or 0) == 0 and int(result.get("partial_unresolved") or 0) == 0


def configure() -> None:
    # Keep v1's safe-continue states and activity telemetry, then impose the
    # production fillability gate, taker/taker census, and bounded lifecycle at
    # the outermost layer.
    base._activity_sort_key = _near_touch_activity_sort_key
    base.configure()
    base.mlb_v3.STICKY_REST_SECONDS = STICKY_LIFECYCLE_SECONDS
    base.mlb_v3._fresh_ranked = _fillable_ranked


async def main() -> None:
    configure()
    try:
        cleanup = await cleanup_current_revenue_orders_fast()
    except Exception as exc:
        print(
            "SPORTS_STRUCTURAL_SUPERVISOR "
            + json.dumps(
                {
                    "status": "PRE_RECOVERY_ACCOUNT_CLEANUP_EXCEPTION_HARD_HALT",
                    "error_type": type(exc).__name__,
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return
    print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps(cleanup, separators=(",", ":"), default=str), flush=True)
    if not _cleanup_clear(cleanup):
        print(
            "SPORTS_STRUCTURAL_SUPERVISOR "
            + json.dumps(
                {
                    "status": "PRE_RECOVERY_ACCOUNT_CLEANUP_AMBIGUOUS_HARD_HALT",
                    "unresolved": cleanup.get("unresolved"),
                    "partial_unresolved": cleanup.get("partial_unresolved"),
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return
    await recovered.main()


if __name__ == "__main__":
    asyncio.run(main())
