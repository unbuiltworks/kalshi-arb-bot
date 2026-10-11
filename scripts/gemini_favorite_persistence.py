"""FAVORITE PERSISTENCE -- descriptive only, BTC5m and BTC15m separately.

THE QUESTION. Not "how often does the favorite win" but how often it wins
CONDITIONAL on its executable price and the time remaining, and -- the part
that tests the five-minutes-is-too-short-to-reverse intuition directly -- how
STICKY leadership becomes as the clock runs down.

NO STRATEGY OR THRESHOLD CHANGES. Nothing here selects a cell, fits a cutoff,
or alters the frozen policy. The checkpoints and the price buckets were fixed
by the brief before any outcome was read, and the table is reported as
produced, thin cells and all.

WHO IS THE FAVORITE. The side whose EXECUTABLE ASK is the dearer of the
executable sides, and only when that ask is >= 0.50. "Offered at an
executable 80c" is the whole premise, so a side with no recorded ask is not a
favorite at any price -- it is NOT EXECUTABLE and is counted as such. On
Gemini one instrument carries both outcomes:

    buy UP   (yes) -> pays the YES ASK
    buy DOWN (no)  -> pays 1 - the YES BID, consuming the bid ladder

Because the two executable asks sum well above 1.00 on this venue, BOTH sides
can exceed 0.50 at once. That is counted (`both_sides_ge_50c`) rather than
hidden, and the dearer one is taken as the market's favorite.

LEADERSHIP, for flip counting, is a separate and deliberately weaker notion
than "executable favorite", because a checkpoint can have leadership
information without an executable ask. Fixed precedence, recorded per
observation:

    1. both touches present -> UP leads iff mid > 0.50
    2. only the bid        -> UP leads iff bid > 0.50
    3. only the ask        -> UP leads iff ask > 0.50
    4. neither            -> UNKNOWN; the checkpoint contributes no
                             leadership observation and any flip across it
                             is marked as spanning a gap

A flip is a change of leader between consecutive KNOWN leadership
observations, later in the same window. Gaps are reported, never interpolated.

BREAKEVEN. Buying q at executable p and holding to settlement:
    breakeven win rate = p + 0.07*p*(1-p)      (published taker rate)
Zero-fee breakeven (= p) is reported beside it, because the published
schedule is not reconciled against our own authenticated fills.

THE STATISTICAL UNIT IS THE WINDOW. Several checkpoints of one window share
one settlement, so every interval comes from a bootstrap that resamples
WINDOWS.

Accounting label: historical replay on Gemini's own recorded books and
settlements. No order was sent. Nothing here is realized profit.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import random
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

LABEL = "GEMINI_FAVORITE_PERSISTENCE_V1"
TAKER_RATE = 0.07

#: the brief's buckets, unchanged
BUCKETS = (("50-55c", 0.50, 0.55), ("55-60c", 0.55, 0.60),
           ("60-70c", 0.60, 0.70), ("70-80c", 0.70, 0.80),
           ("80-90c", 0.80, 0.90), ("90-95c", 0.90, 0.95),
           ("95-99c", 0.95, 0.99))

BTC5M_CHECKPOINTS = (240, 180, 120, 90, 60, 45, 30, 15)
BTC15M_CHECKPOINTS = (840, 720, 600, 480, 360, 300, 240, 180, 120, 60)

WINDOWS_PER_DAY = {"BTC5M": 288, "BTC15M": 96}


def breakeven(p: float) -> float:
    return p + TAKER_RATE * p * (1.0 - p)


def bucket_of(p: float) -> Optional[str]:
    for name, lo, hi in BUCKETS:
        if lo <= p < hi:
            return name
    return None


def _leader(up_ask: Optional[float], bid: Optional[float]
            ) -> Tuple[Optional[str], str]:
    """Leadership under the fixed precedence. Returns (leader, basis)."""
    if up_ask is not None and bid is not None:
        mid = (up_ask + bid) / 2.0
        return ("UP" if mid > 0.50 else "DOWN", "MID")
    if bid is not None:
        return ("UP" if bid > 0.50 else "DOWN", "BID_ONLY")
    if up_ask is not None:
        return ("UP" if up_ask > 0.50 else "DOWN", "ASK_ONLY")
    return (None, "UNKNOWN")


def _boot_ci(by_window: Dict[str, List[bool]], *, n: int = 4000,
             seed: int = 20261005) -> List[Optional[float]]:
    keys = list(by_window)
    if len(keys) < 2:
        return [None, None]
    rng = random.Random(seed)
    rates: List[float] = []
    for _ in range(n):
        wins = tot = 0
        for _ in keys:
            for w in by_window[keys[rng.randrange(len(keys))]]:
                tot += 1
                wins += 1 if w else 0
        if tot:
            rates.append(100.0 * wins / tot)
    if not rates:
        return [None, None]
    rates.sort()
    return [round(rates[int(0.025 * len(rates))], 2),
            round(rates[min(len(rates) - 1, int(0.975 * len(rates)))], 2)]


def build(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One observation per (window, checkpoint), with the forward leadership
    path attached. `up_won` is the venue's resolution and is a LABEL only."""
    by_w: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        if r.get("tte") is None:
            continue
        by_w[r["ticker"]].append(r)
    out: List[Dict[str, Any]] = []
    for tk, rs in by_w.items():
        rs = sorted(rs, key=lambda x: -float(x["tte"]))     # earliest first
        lead = []
        for r in rs:
            up_ask = (None if r.get("up_ask") is None
                      else float(r["up_ask"]))
            bid = None if r.get("bid") is None else float(r["bid"])
            ld, basis = _leader(up_ask, bid)
            lead.append((float(r["tte"]), ld, basis))
        for i, r in enumerate(rs):
            up_won = r.get("up_won")
            if up_won is None:
                continue
            up_ask = (None if r.get("up_ask") is None
                      else float(r["up_ask"]))
            bid = None if r.get("bid") is None else float(r["bid"])
            dn_ask = None if bid is None else round(1.0 - bid, 6)
            sides = []
            if up_ask is not None:
                sz = r.get("up_ask_sz")
                sides.append(("UP", up_ask, None if sz is None else float(sz)))
            if dn_ask is not None:
                sz = r.get("down_sz")
                sides.append(("DOWN", dn_ask,
                              None if sz is None else float(sz)))
            fav = None
            if sides:
                dear = max(sides, key=lambda s: s[1])
                if dear[1] >= 0.50:
                    fav = dear
            # ---- the forward leadership path, strictly LATER in the window
            fwd = [x for x in lead[i + 1:] if x[1] is not None]
            here = lead[i][1]
            flips = 0
            prev = here
            if prev is None and fwd:
                prev = fwd[0][1]
            for _tte, ld, _b in fwd:
                if prev is not None and ld != prev:
                    flips += 1
                prev = ld
            gaps = sum(1 for x in lead[i + 1:] if x[1] is None)
            final_leader = fwd[-1][1] if fwd else here
            winner = "UP" if up_won else "DOWN"
            out.append({
                "window": tk,
                "checkpoint_s": float(r["tte"]),
                "leader_here": here,
                "leader_basis": lead[i][2],
                "executable_sides": len(sides),
                "both_sides_ge_50c": sum(1 for s in sides if s[1] >= 0.50) == 2,
                "favorite_side": None if fav is None else fav[0],
                "favorite_ask": None if fav is None else fav[1],
                "favorite_depth": None if fav is None else fav[2],
                "favorite_bucket": (None if fav is None
                                    else bucket_of(fav[1])),
                "subsequent_flips": flips,
                "ever_flips": flips > 0,
                "leadership_gaps_after": gaps,
                "final_leader": final_leader,
                "winner": winner,
                "favorite_won": (None if fav is None
                                 else fav[0] == winner),
                "leader_won": (None if here is None else here == winner),
                "opposite_settled_after_a_flip": (
                    None if fav is None
                    else bool(flips > 0 and fav[0] != winner)),
            })
    return out


def cell(recs: Sequence[Dict[str, Any]], duration: str) -> Dict[str, Any]:
    sub = [r for r in recs if r["favorite_ask"] is not None
           and r["favorite_won"] is not None]
    if not sub:
        return {"observations": 0}
    by_w: Dict[str, List[bool]] = collections.defaultdict(list)
    for r in sub:
        by_w[r["window"]].append(bool(r["favorite_won"]))
    asks = [r["favorite_ask"] for r in sub]
    avg = statistics.fmean(asks)
    wins = sum(1 for r in sub if r["favorite_won"])
    wp = 100.0 * wins / len(sub)
    be = 100.0 * breakeven(avg)
    ci = _boot_ci(by_w)
    flips = [r["subsequent_flips"] for r in sub]
    flipped = [r for r in sub if r["ever_flips"]]
    depths = [r["favorite_depth"] for r in sub
              if r["favorite_depth"] is not None]
    ev = [(1.0 - r["favorite_ask"]
           - TAKER_RATE * r["favorite_ask"] * (1.0 - r["favorite_ask"]))
          if r["favorite_won"]
          else (-r["favorite_ask"]
                - TAKER_RATE * r["favorite_ask"] * (1.0 - r["favorite_ask"]))
          for r in sub]
    return {
        "distinct_windows": len(by_w),
        "observations": len(sub),
        "avg_executable_ask": round(avg, 4),
        "favorite_win_pct": round(wp, 2),
        "breakeven_pct_zero_fees": round(100.0 * avg, 2),
        "breakeven_pct_taker_fees": round(be, 2),
        "win_minus_breakeven_pp": round(wp - be, 2),
        "win_pct_ci95_window_bootstrap": ci,
        "ci_low_minus_breakeven_pp": (None if ci[0] is None
                                      else round(ci[0] - be, 2)),
        "established_positive": bool(ci[0] is not None and ci[0] > be),
        "ev_per_share_usd": round(statistics.fmean(ev), 4),
        "p_leadership_flips_at_least_once": round(
            100.0 * len(flipped) / len(sub), 2),
        "p_opposite_settles_after_a_flip": round(
            100.0 * sum(1 for r in sub
                        if r["opposite_settled_after_a_flip"]) / len(sub), 2),
        "p_opposite_settles_GIVEN_a_flip": (
            None if not flipped else round(
                100.0 * sum(1 for r in flipped
                            if r["favorite_won"] is False) / len(flipped), 2)),
        "subsequent_flips_mean": round(statistics.fmean(flips), 3),
        "subsequent_flips_median": statistics.median(flips),
        "both_sides_ge_50c_observations": sum(1 for r in sub
                                              if r["both_sides_ge_50c"]),
        "leadership_gaps_after_mean": round(statistics.fmean(
            [r["leadership_gaps_after"] for r in sub]), 2),
        "depth_contracts": (
            {"recorded": False,
             "why": "this duration's recorder preserved touch prices only"}
            if not depths else
            {"recorded": True,
             "median": round(statistics.median(depths), 1),
             "p10": round(sorted(depths)[len(depths) // 10], 1),
             "max": round(max(depths), 1),
             "capacity_usd_at_median_depth": round(
                 statistics.median(depths) * avg, 2)}),
        "opportunities_per_day": round(
            len(sub) / len(by_w) * WINDOWS_PER_DAY[duration], 1),
    }


def unconditional(recs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Of the CURRENT LEADER (not necessarily executable), how many win?"""
    sub = [r for r in recs if r["leader_won"] is not None]
    if not sub:
        return {"observations": 0}
    by_w: Dict[str, List[bool]] = collections.defaultdict(list)
    for r in sub:
        by_w[r["window"]].append(bool(r["leader_won"]))
    wp = 100.0 * sum(1 for r in sub if r["leader_won"]) / len(sub)
    return {
        "observations": len(sub),
        "distinct_windows": len(by_w),
        "current_leader_wins_pct": round(wp, 2),
        "ci95_window_bootstrap": _boot_ci(by_w),
        "p_leadership_flips_at_least_once": round(
            100.0 * sum(1 for r in sub if r["ever_flips"]) / len(sub), 2),
        "subsequent_flips_mean": round(statistics.fmean(
            [r["subsequent_flips"] for r in sub]), 3),
        "leader_basis": dict(collections.Counter(r["leader_basis"]
                                                 for r in sub)),
        "favorite_executable_observations": sum(
            1 for r in sub if r["favorite_ask"] is not None),
        "note": ("the LEADER is identified from whatever price information "
                 "exists (mid, bid-only or ask-only); it is NOT required to "
                 "be executable, which is why n exceeds the favorite table"),
    }


def table(recs: Sequence[Dict[str, Any]], duration: str,
          checkpoints: Sequence[int]) -> Dict[str, Any]:
    grid = sorted({r["checkpoint_s"] for r in recs}, reverse=True)
    out: Dict[str, Any] = {
        "windows": len({r["window"] for r in recs}),
        "observations": len(recs),
        "recorded_checkpoints": grid,
        "requested_checkpoints_not_collected": [
            c for c in checkpoints if float(c) not in grid],
        "matrix": {}, "by_checkpoint_all_buckets": {},
        "by_bucket_all_checkpoints": {},
        "unconditional_by_checkpoint": {},
    }
    for cp in grid:
        at = [r for r in recs if r["checkpoint_s"] == cp]
        row = {}
        for name, lo, hi in BUCKETS:
            sub = [r for r in at if r["favorite_bucket"] == name]
            row[name] = cell(sub, duration) if sub else {"observations": 0}
        out["matrix"]["%ds" % cp] = row
        out["by_checkpoint_all_buckets"]["%ds" % cp] = cell(at, duration)
        out["unconditional_by_checkpoint"]["%ds" % cp] = unconditional(at)
    for name, lo, hi in BUCKETS:
        sub = [r for r in recs if r["favorite_bucket"] == name]
        out["by_bucket_all_checkpoints"][name] = (cell(sub, duration) if sub
                                                  else {"observations": 0})
    out["pooled_favorite"] = cell(recs, duration)
    out["pooled_unconditional"] = unconditional(recs)
    return out


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder",
                    default="analysis/gemini/info_edge/ie_tte_ladder.json")
    ap.add_argument("--bulk", default="analysis/gemini/p7_bulk_tape.json")
    ap.add_argument("--cohorts", default=(
        "analysis/gemini/cohort_run_2026*.json,"
        "analysis/gemini/cohort_window_2026*.json,"
        "analysis/gemini/continuous_run_2026*.json"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    # BTC5m: the checkpointed ladder tape. BTC15m: the live-cohort book
    # recorder, loaded by the matrix module so there is ONE loader, not two.
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    from scripts.gemini_underdog_matrix import load_btc15m
    raw5 = [r for r in json.load(open(a.ladder, encoding="utf-8"))
            if str(r.get("ticker", "")).startswith("BTC05M")]
    settle: Dict[str, str] = {}
    if os.path.exists(a.bulk):
        for r in json.load(open(a.bulk, encoding="utf-8")):
            if r.get("resolution") in ("yes", "no") and r.get("event_ticker"):
                settle[r["event_ticker"]] = r["resolution"]
    raw15 = load_btc15m(a.cohorts.split(","), settle)

    rep: Dict[str, Any] = {
        "label": LABEL,
        "lane": "GEMINI -- no PMUS data, thresholds, fees or samples",
        "descriptive_only": ("no threshold is selected, no cell is promoted, "
                             "and no strategy parameter is touched"),
        "favorite_definition": ("the dearer EXECUTABLE side, and only when "
                                "its executable ask is >= 0.50; a side with "
                                "no recorded ask is not a favorite at any "
                                "price"),
        "leadership_definition": ("fixed precedence MID -> BID_ONLY -> "
                                  "ASK_ONLY -> UNKNOWN; UNKNOWN contributes "
                                  "no leadership observation and gaps are "
                                  "counted, never interpolated"),
        "fee_model": {"taker_rate": TAKER_RATE,
                      "formula": "rate * q * p * (1 - p)",
                      "breakeven": "p + 0.07*p*(1-p)",
                      "reconciliation": ("published schedule; NOT reconciled "
                                         "against our own authenticated "
                                         "fills, so zero-fee breakeven is "
                                         "reported beside it")},
        "statistical_unit": "WINDOW",
        "BTC5M": table(build(raw5), "BTC5M", BTC5M_CHECKPOINTS),
        "BTC15M": table(build(raw15), "BTC15M", BTC15M_CHECKPOINTS),
        "durations_never_pooled": True,
        "accounting_label": ("historical replay on Gemini's own recorded "
                             "books and settlements; no order was sent"),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    for dur in ("BTC5M", "BTC15M"):
        t = rep[dur]
        print("%s %s windows=%d obs=%d fav_obs=%d not_collected=%s"
              % (LABEL, dur, t["windows"], t["observations"],
                 t["pooled_favorite"].get("observations", 0),
                 t["requested_checkpoints_not_collected"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
