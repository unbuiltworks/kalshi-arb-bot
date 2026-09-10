"""Rank CURRENTLY ACTIVE sports GAME markets on L5-relevant signals.

OWNER 2026-09-10: "Rank currently active sports/game markets by L5-relevant
flow, two-sided activity, movement, spread/depth, completion opportunity,
time remaining, and current incentive eligibility. Prioritize actual games
over futures. Do not optimize for raw session count or liquidity rewards."

WHAT IS DELIBERATELY NOT A SCORING TERM. Reward pool, program eligibility,
target size, discount factor. `incentive_program` is carried as a REPORTED
column so a reader can see it; it never moves a market up the ranking. Nor
does anything that rewards attempt count -- the ordering key is opportunity
per market, not markets per hour.

THE TWO MEASUREMENTS THAT ARE NOT PROXIES
-----------------------------------------
1. FLOW is real traded notional. `/v1/markets/<slug>/book` carries a `stats`
   block with cumulative `notionalTraded`; differencing it across two
   snapshots minutes apart gives dollars actually traded per minute. The
   existing universe ranker (src/pmus_universe_rank.py) documents this term
   as unavailable on the public gateway and scores it 0. It is available.

2. COMPLETION MARGIN is the spread itself, not a proxy for it. PMUS quotes
   on YES, and a NO buy at p is the complement of a YES sell at 1-p -- the
   convention pmus_q1_executor_v4.outcome_bbo already encodes. So a maker
   joining both touches pays

       maker_pair_cost = best_bid + (1 - best_ask) = 1 - spread

   and the spread IS the matched-pair margin of a <=$1.00 completion. A
   one-cent spread is a one-cent margin on a one-dollar pair.

WHY `offers`, NOT `asks`. The venue serves sell depth under `offers`.
Reading `asks` returns [] on every market and is indistinguishable from a
genuinely one-sided book -- the repo already fixed this once in
src/pmus_sports_offers_hotfix.py (OWNER 5534675078). A book parsed the wrong
way here would silently disqualify the entire universe.

This module RANKS. It selects nothing, submits nothing, and is not on the
probe's deploy watch path.
"""
from __future__ import annotations

import datetime
import statistics as st
from typing import Any, Mapping, Optional, Sequence

# R5F's own entry band, mirrored here so the ranking answers "what could the
# LIVE engine actually enter", not "what looks good in the abstract".
BAND_LO, BAND_HI = 0.25, 0.75
MIN_BAND_FRACTION = 0.5
PAIR_CEILING = 1.00


def _f(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ts(x: Any) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def touch(side_levels: Optional[Sequence[Mapping[str, Any]]]) -> tuple:
    """(price, qty) at the touch, or (None, None). An empty or unparseable
    level is absent, never zero -- a 0.00 price would read as a free side."""
    if not side_levels:
        return None, None
    lvl = side_levels[0]
    px = lvl.get("px")
    px = px.get("value") if isinstance(px, Mapping) else px
    return _f(px), _f(lvl.get("qty"))


def maker_pair_cost(best_bid: Optional[float],
                    best_ask: Optional[float]) -> Optional[float]:
    """What a maker pays to hold one matched pair by joining BOTH touches.

    Returns None unless both sides exist: a one-sided book has no pair cost,
    and defaulting it to a number would let an uncompletable market rank.
    """
    if best_bid is None or best_ask is None:
        return None
    return best_bid + (1.0 - best_ask)


def flow_usd_per_min(snap_a: Mapping[str, Any],
                     snap_b: Mapping[str, Any],
                     min_window_min: float = 0.5) -> Optional[float]:
    """Real traded notional per minute between two book snapshots.

    None -- not 0.0 -- when it cannot be established: a missing counter and
    a genuinely quiet market must not collapse into the same answer, because
    the first is ignorance and the second is evidence.
    """
    na, nb = _f(snap_a.get("notional_traded")), _f(snap_b.get("notional_traded"))
    ta, tb = _ts(snap_a.get("transact_time")), _ts(snap_b.get("transact_time"))
    if None in (na, nb) or ta is None or tb is None:
        return None
    mins = (tb - ta).total_seconds() / 60.0
    if mins < min_window_min:
        return None
    return max(0.0, nb - na) / mins


def hour_shape(history: Sequence[Mapping[str, Any]]) -> dict:
    """Movement and band occupancy from per-minute quote history.

    `longPrice` is the Yes display price from the best ask; `shortPrice` is
    one minus the best bid. So yes_bid = 1 - shortPrice and yes_ask =
    longPrice, and their gap is the spread the venue says these two
    deliberately preserve.
    """
    mids, spreads = [], []
    for p in history or []:
        lp, sp = _f(p.get("longPrice")), _f(p.get("shortPrice"))
        if lp is None or sp is None:
            continue
        yes_bid, yes_ask = 1.0 - sp, lp
        mids.append((yes_bid + yes_ask) / 2.0)
        spreads.append(yes_ask - yes_bid)
    if not mids:
        return {"points": 0, "mid_stdev": None, "mid_range": None,
                "median_spread": None, "in_band_fraction": None}
    return {
        "points": len(mids),
        "mid_stdev": st.pstdev(mids) if len(mids) > 2 else None,
        "mid_range": max(mids) - min(mids),
        "median_spread": st.median(spreads),
        "in_band_fraction": sum(1 for m in mids
                                if BAND_LO <= m <= BAND_HI) / len(mids),
    }


def is_game(market: Mapping[str, Any]) -> bool:
    """Actual games only. OWNER: "Prioritize actual games over futures."
    A future is excluded by TYPE, never by how far away its date is -- a
    season-long future carries a gameStartTime like everything else."""
    return not str(market.get("sportsMarketTypeV2") or "").endswith("FUTURE")


def eligibility(row: Mapping[str, Any]) -> dict:
    """R5F-eligibility, each gate reported by name so a zero-length result
    says WHICH gate emptied it rather than just being empty."""
    checks = {
        "state_open": row.get("state") == "MARKET_STATE_OPEN",
        "two_sided": row.get("bid") is not None and row.get("ask") is not None,
        "pair_at_or_below_ceiling":
            (row.get("maker_pair_cost") is not None
             and row["maker_pair_cost"] <= PAIR_CEILING),
        "in_band": (row.get("in_band_fraction") or 0.0) >= MIN_BAND_FRACTION,
        "flow_observed": (row.get("flow_usd_per_min") or 0.0) > 0.0,
    }
    return {"eligible": all(checks.values()), "checks": checks,
            "failed": sorted(k for k, v in checks.items() if not v)}


def rank(rows: Sequence[Mapping[str, Any]]) -> list:
    """Eligible markets, most contemporaneous flow first.

    Flow is the ordering key on purpose. The other signals are GATES or
    reported context: a market either can be entered and completed or it
    cannot, and among those that can, the one where dollars are actually
    changing hands is where a first leg gets filled and a complement gets
    met. Blending them into one weighted score would hide which term
    actually moved a market up.
    """
    elig = [r for r in rows if eligibility(r)["eligible"]]
    return sorted(elig, key=lambda r: -(r.get("flow_usd_per_min") or 0.0))
