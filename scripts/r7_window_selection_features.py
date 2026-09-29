"""Which windows are worth trading? Decision-time features only.

THE SHIFT. Clip was the wrong primary variable: the in-sample q25 knee did
not replicate out of sample, while completion rate separated good windows
from bad in BOTH samples. This asks whether a window's completion -- and so
its economics -- is PREDICTABLE from what is observable before the strategy
commits.

THE DISCIPLINE THAT MAKES IT HONEST. Every feature is computed from the
FIRST `EARLY_S` seconds of the window and from nothing else. No feature
reads the window's later book, its outcome, or its settlement. A feature
that needed the whole window would predict beautifully and be worthless,
because at decision time it does not exist.

TRAIN / TEST SEPARATION IS BY DATASET, NOT BY SPLIT. Train is the frozen 25
(2026-09-27/28 18:15-02:45z). Test is the 49 mechanically admitted
out-of-sample windows (2026-09-28 02:45-14:45z). They do not overlap and
were recorded on different stretches, so a variable that separates in both
is saying something about the market rather than about the sample.

NO GATE IS FITTED HERE. This reports which variables separate and by how
much. Choosing a threshold is a later, separate decision -- fitting one now,
on the same data that selected the variable, is how the q25 knee happened.

Labels are the frozen engine's own replay outcome per window:
  completion_rate      settlement-INDEPENDENT, exact
  net                  needs a winner; supplied by the read-only venue
                       settlement sweep, absent for unresolved windows
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

import scripts.pmus_completeness_rule as CR
from scripts.audit_economic_complete import USABLE, TAPE as FROZEN_TAPE
from scripts.r7_q10_readiness import walk

OOS_TAPE = "data/pmus_btc15m_tape_oos/oos_tape.jsonl"

#: how much of the window a feature may see. 180s of a 900s window: enough
#: for the book to establish itself, early enough that ~80% of the window
#: remains to trade.
EARLY_S = 180.0

OPEN = "MARKET_STATE_OPEN"


def _t(s) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def features(slug: str, rows: List[Mapping[str, Any]]) -> Optional[Dict]:
    """What is knowable `EARLY_S` into the window, and nothing more."""
    close = CR.scheduled_close(slug)
    start = close - dt.timedelta(seconds=900)
    cutoff = start + dt.timedelta(seconds=EARLY_S)

    bbo = []
    for r in rows:
        if r.get("kind") != "bbo":
            continue
        t = _t(r.get("t"))
        if t is None or t < start or t > cutoff:
            continue
        bbo.append((t, r))
    bbo.sort(key=lambda x: x[0])
    open_rows = [(t, r) for t, r in bbo if r.get("state") == OPEN]
    if len(open_rows) < 3:
        return None

    mids, spreads, bdep, adep, bsh, ash = [], [], [], [], [], []
    two_sided = 0
    for _t_, r in open_rows:
        bid, ask = _f(r.get("bid")), _f(r.get("ask"))
        if bid is not None and ask is not None and ask >= bid:
            mids.append((bid + ask) / 2.0)
            spreads.append(ask - bid)
        if r.get("two_sided"):
            two_sided += 1
        for key, acc in (("bid_depth", bdep), ("ask_depth", adep),
                         ("bid_shares", bsh), ("ask_shares", ash)):
            v = _f(r.get(key))
            if v is not None:
                acc.append(v)

    # traded volume per minute, from the collector's own lower bound
    deltas = [_f(r.get("shares_traded_delta_lower_bound")) or 0.0
              for _t_, r in open_rows]
    span_s = (open_rows[-1][0] - open_rows[0][0]).total_seconds() or 1.0
    shares_per_min = sum(deltas) * 60.0 / span_s

    if len(mids) < 3:
        return None
    mid0, mid1 = mids[0], mids[-1]
    rets = [b - a for a, b in zip(mids, mids[1:])]

    return {
        "slug": slug, "window": slug[-5:],
        # 1. time of day
        "hour_utc": close.hour + close.minute / 60.0,
        # 2. traded volume
        "shares_per_min": shares_per_min,
        "obs_per_min": len(open_rows) * 60.0 / span_s,
        # 3. spread
        "spread_median": statistics.median(spreads),
        "spread_max": max(spreads),
        # 4. depth
        "bid_shares_median": statistics.median(bsh) if bsh else None,
        "ask_shares_median": statistics.median(ash) if ash else None,
        "depth_levels_median": (statistics.median(bdep + adep)
                                if (bdep or adep) else None),
        # 5. volatility and directional move, both EARLY only
        "mid_vol": statistics.pstdev(mids) if len(mids) > 1 else 0.0,
        "mid_absmove": abs(mid1 - mid0),
        "mid_move": mid1 - mid0,
        "tick_vol": (statistics.pstdev(rets) if len(rets) > 1 else 0.0),
        # 6. early book imbalance
        "book_imbalance": ((statistics.median(bsh) - statistics.median(ash))
                           / (statistics.median(bsh) + statistics.median(ash))
                           if (bsh and ash
                               and (statistics.median(bsh)
                                    + statistics.median(ash)) > 0) else None),
        # 7. favored-side probability: how far the market already is from even
        "favored_prob": max(mid1, 1.0 - mid1),
        "dist_from_even": abs(mid1 - 0.5),
        # 8. seconds to settlement at the decision point
        "s_to_settle": (close - open_rows[-1][0]).total_seconds(),
        # 9. two-sided frequency
        "two_sided_frac": two_sided / len(open_rows),
    }


def labelled(tape: str, *, exclude_frozen: bool, clip: int,
             winners: Optional[Mapping[str, str]] = None) -> List[Dict]:
    by = CR.load_tape(tape)
    out = []
    for slug, rows in sorted(by.items()):
        if exclude_frozen and slug in USABLE:
            continue
        if not exclude_frozen and slug not in USABLE:
            continue
        if not CR.evaluate(slug, rows).get("tape_test_passes"):
            continue
        f = features(slug, rows)
        if f is None:
            continue
        w = (USABLE.get(slug) if not exclude_frozen
             else (winners or {}).get(slug))
        if w is None:
            f["net"] = None
            f["completion_rate"] = walk(rows, winner="up", clip=D(clip)
                                        )["completion_rate"]
            f["fully_completed"] = walk(rows, winner="up", clip=D(clip)
                                        )["fully_completed"]
        else:
            r = walk(rows, winner=w, clip=D(clip))
            f["net"] = float(r["net"])
            f["completion_rate"] = r["completion_rate"]
            f["fully_completed"] = r["fully_completed"]
            f["winner"] = w
        out.append(f)
    return out


FEATURES = ("hour_utc", "shares_per_min", "obs_per_min", "spread_median",
            "spread_max", "bid_shares_median", "ask_shares_median",
            "depth_levels_median", "mid_vol", "mid_absmove", "tick_vol",
            "book_imbalance", "favored_prob", "dist_from_even",
            "s_to_settle", "two_sided_frac")


def separation(rows: List[Dict], key: str, *, threshold=None) -> Dict:
    """Rank-biserial style separation of `key` between good and bad windows.

    Non-parametric on purpose: n is small and these distributions are not
    normal. Reports the AUC of the feature as a one-variable classifier,
    which is invariant to any monotone rescaling and needs no fitted cutoff.
    """
    good, bad = [], []
    for r in rows:
        lab = r.get(key)
        if lab is None:
            continue
        (good if lab else bad).append(r)
    out = {}
    for f in FEATURES:
        g = [r[f] for r in good if r.get(f) is not None]
        b = [r[f] for r in bad if r.get(f) is not None]
        if len(g) < 3 or len(b) < 3:
            out[f] = None
            continue
        wins = ties = 0
        for x in g:
            for y in b:
                if x > y:
                    wins += 1
                elif x == y:
                    ties += 1
        auc = (wins + 0.5 * ties) / (len(g) * len(b))
        out[f] = {"auc": round(auc, 3), "n_good": len(g), "n_bad": len(b),
                  "median_good": round(statistics.median(g), 4),
                  "median_bad": round(statistics.median(b), 4)}
    return out
