"""Production combo supervisor v23: learn correlated-family priority from RFQs.

v22 proved that valid same-game/different-family baskets can be identified before
MVE creation.  Because Kalshi market creation/RFQ capacity is finite, this layer
uses the *observed* conservative scalar-safe gap of prior manufactured baskets to
rank future correlated family pairs.  Family pairs that receive two-sided/useful
quotes and come closer to a positive lock earn more future manufacturing slots;
unseen pairs retain exploration capacity.

The feedback is process-local, diagnostic/manufacturing-order-only and resets on
restart.  It never changes settlement semantics, fee math, the >=3c live floor,
q=1, final revalidation, depth/capital checks, or order-entry authorization.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import json
import os
from typing import Any

from src import sports_kp_live_combo_supervisor_v22 as v22
from src import sports_kp_mve_combo_generator as g

logger = v22.logger
D = Decimal
ZERO = D("0")

# Preserve the daily 600 hard cap while spending it more selectively.
os.environ["MVE_GENERATOR_MAX_CREATIONS_PER_RUN"] = "8"

base = v22.v21.v20.v19.v18.v17.v16.base
_ORIGINAL_CANDIDATE_COMBOS = g._candidate_combos
_ORIGINAL_GENERATE = base.generate_mves

# signature -> observations.  best_lock is the best conservative lock seen in
# either scalar-safe direction (can be negative).
_FEEDBACK: dict[str, dict[str, Any]] = defaultdict(
    lambda: {"observations": 0, "quoted": 0, "best_lock": D("-9"), "last_lock": D("-9")}
)


def _event_parts(ticker: str) -> tuple[str, str]:
    text = str(ticker or "").upper()
    if "-" not in text:
        return text, text
    return tuple(text.split("-", 1))  # type: ignore[return-value]


def _family_signature_from_legs(legs: Any) -> str:
    families = []
    for leg in legs or []:
        ticker = leg.get("event_ticker") if isinstance(leg, dict) else getattr(leg, "event_ticker", "")
        family, _game = _event_parts(str(ticker or ""))
        families.append(family)
    return "+".join(sorted(families))


def _same_game_distinct_family(legs: tuple[Any, ...]) -> bool:
    parts = [_event_parts(getattr(leg, "event_ticker", "")) for leg in legs]
    games = {game for _family, game in parts}
    families = {family for family, _game in parts}
    return len(games) == 1 and len(families) >= 2


def _feedback_rank(signature: str) -> tuple[int, Decimal, int]:
    row = _FEEDBACK.get(signature)
    if not row or int(row["observations"]) == 0:
        # Unseen pairs get exploration priority after pairs already showing a
        # near-bound quote, but ahead of repeatedly poor pairs.
        return (1, D("-2"), 0)
    best = D(row["best_lock"])
    quoted = int(row["quoted"])
    observations = int(row["observations"])
    # Tier 0 = has approached within 10c of the bound; tier 1 unseen; tier 2 poor.
    tier = 0 if best >= D("-0.10") else 2
    return (tier, -best, -quoted * 1000 // max(1, observations))


def _candidate_combos_feedback_first(collection, event_legs, *, max_candidates, min_locked):
    expanded_cap = min(max(max_candidates * 5, max_candidates), 1200)
    raw = _ORIGINAL_CANDIDATE_COMBOS(
        collection,
        event_legs,
        max_candidates=expanded_cap,
        min_locked=min_locked,
    )
    correlated = [row for row in raw if _same_game_distinct_family(row[1])]
    baseline = [row for row in raw if not _same_game_distinct_family(row[1])]
    correlated.sort(
        key=lambda row: (
            _feedback_rank(_family_signature_from_legs(row[1])),
            len(row[1]),
            row[0],
            max(leg.close_time for leg in row[1]),
        )
    )

    out = []
    ci = bi = 0
    # 3 learned-correlated slots then one broad exploration slot.
    while len(out) < max_candidates and (ci < len(correlated) or bi < len(baseline)):
        for _ in range(3):
            if ci >= len(correlated) or len(out) >= max_candidates:
                break
            out.append(correlated[ci]); ci += 1
        if bi < len(baseline) and len(out) < max_candidates:
            out.append(baseline[bi]); bi += 1
        if ci >= len(correlated) and bi >= len(baseline):
            break

    logger.warning("SPORTS_MVE_RFQ_FEEDBACK_PRIORITY %s", json.dumps({
        "collection": collection.get("collection_ticker"),
        "correlated_candidates": len(correlated),
        "baseline_candidates": len(baseline),
        "returned_candidates": len(out),
        "learned_signatures": len(_FEEDBACK),
        "manufacturing_order_only": True,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


def _lock_value(row: dict[str, Any]) -> Decimal | None:
    vals: list[Decimal] = []
    raw = row.get("locked_profit_conservative")
    if raw not in (None, ""):
        try: vals.append(D(str(raw)))
        except Exception: pass
    inv = row.get("inverse_coverage") if isinstance(row.get("inverse_coverage"), dict) else {}
    raw_inv = inv.get("locked_profit_conservative")
    if raw_inv not in (None, ""):
        try: vals.append(D(str(raw_inv)))
        except Exception: pass
    return max(vals) if vals else None


async def _generate_with_feedback() -> dict[str, Any]:
    result = await _ORIGINAL_GENERATE()
    rows = [x for x in (result.get("ranked") or []) if isinstance(x, dict)] if isinstance(result, dict) else []
    cycle_summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        sig = _family_signature_from_legs(row.get("legs") or [])
        if not sig:
            continue
        fb = _FEEDBACK[sig]
        fb["observations"] = int(fb["observations"]) + 1
        if int(row.get("quote_count") or 0) > 0:
            fb["quoted"] = int(fb["quoted"]) + 1
        lock = _lock_value(row)
        if lock is not None:
            fb["last_lock"] = lock
            fb["best_lock"] = max(D(fb["best_lock"]), lock)
        cycle_summary[sig] = {
            "observations": int(fb["observations"]),
            "quoted": int(fb["quoted"]),
            "best_lock": str(fb["best_lock"]),
            "last_lock": str(fb["last_lock"]),
        }

    ranked_feedback = sorted(
        ({"signature": sig, **data} for sig, data in cycle_summary.items()),
        key=lambda x: D(str(x["best_lock"])),
        reverse=True,
    )[:12]
    logger.warning("SPORTS_MVE_RFQ_FEEDBACK_SUMMARY %s", json.dumps({
        "markets_scored": len(rows),
        "top_family_signatures": ranked_feedback,
        "created_today": result.get("created_today") if isinstance(result, dict) else None,
        "daily_cap": result.get("daily_cap") if isinstance(result, dict) else None,
        "feedback_is_execution_gate": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return result


g._candidate_combos = _candidate_combos_feedback_first
base.generate_mves = _generate_with_feedback

run = v22.run
main = v22.main

if __name__ == "__main__":
    main()
