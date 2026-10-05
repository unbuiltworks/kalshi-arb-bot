"""GEMINI BTC UP/DOWN UNDERDOG MATRIX -- hold to settlement, executable only.

LANE SEPARATION. This is the GEMINI lane. No PMUS window, threshold, fee,
settlement assumption or validation sample enters here. The only inputs are
Gemini's own recorded executable books and Gemini's own published settlement
records.

THE EXECUTABLE PRICES, and why there are two of them for one instrument.
Gemini lists ONE contract per window (`...-UP`) with outcomes yes/no. The
venue publishes `prices.buy.{yes,no}` and `prices.sell.{yes,no}` and states
that a resting BUY on an outcome joins that outcome's SELL side. So:

    buy UP   (outcome yes) aggressively  ->  pays   the YES ASK
    buy DOWN (outcome no)  aggressively  ->  pays   1 - the YES BID

The second is not a derived convenience: buying NO and selling YES are the
same trade, so it consumes the YES BID ladder and its depth is the bid size.
The recorded tape satisfies `down_price == 1 - bid` in 669 of 669 rows with
both present, with ZERO crossed books, which is what makes that reading a
measurement rather than an assumption.

WHAT IS NOT USED AS EXECUTION: no midpoint, no UI probability, no last trade,
no touch-equals-fill beyond the recorded size at that level, no invented queue
position, no future quote, no post-settlement reference as an entry feature.
A side with no recorded ask is NOT EXECUTABLE and is counted as such rather
than filled at some other price.

FEES. Gemini's published schedule (gemini.com/fees/predictions, recorded in
`rebate_program.py` 2026-09-19) is taker 7% and maker 1.75% of
`q * p * (1 - p)`. The taker model is applied here. It is NOT fully
reconciled against our own authenticated fills -- q=1 maker fills come back
`fee_amount "0"` (consistent with rounding) but several larger rows do not
match either rate, so every number is also reported at ZERO fees and the gap
is stated rather than papered over. Rebates are excluded entirely: a taker
earns none.

BREAKEVEN. Buying q at executable price p and holding to settlement:
    cost    = q*p + 0.07*q*p*(1-p)
    payout  = q if the side settles in the money, else 0
    breakeven win rate = p + 0.07*p*(1-p)

THE STATISTICAL UNIT IS THE WINDOW, not the observation. Eleven checkpoints
of one window are eleven views of ONE coin flip, so a CI over observations
would be wrong by construction. Intervals here come from a bootstrap that
resamples WINDOWS.

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

LABEL = "GEMINI_UNDERDOG_MATRIX_V1"
TAKER_RATE = 0.07            # published; see module docstring
MAKER_RATE = 0.0175

#: the PREREGISTERED BTC5m checkpoints, frozen before any outcome was read.
#: 240s and 180s were requested and are NOT in the recorded grid; they are
#: reported as NOT COLLECTED rather than silently dropped or interpolated.
BTC5M_CHECKPOINTS_REQUESTED = (240, 180, 120, 90, 60, 45, 30, 15)
#: additional recorded checkpoints, reported apart from the frozen grid
BTC5M_CHECKPOINTS_EXTRA = (20, 10, 5, 2, 1)

BTC15M_CHECKPOINTS_REQUESTED = (840, 720, 600, 480, 360, 300, 240, 180, 120,
                                60)

BUCKETS = (("<10c", 0.00, 0.10), ("10-15c", 0.10, 0.15),
           ("15-20c", 0.15, 0.20), ("20-25c", 0.20, 0.25),
           ("25-30c", 0.25, 0.30), ("30-35c", 0.30, 0.35),
           ("35-40c", 0.35, 0.40), ("40-45c", 0.40, 0.45),
           ("45-50c", 0.45, 0.50))

WINDOWS_PER_DAY = {"BTC5M": 288, "BTC15M": 96}


def bucket_of(p: float) -> Optional[str]:
    for name, lo, hi in BUCKETS:
        if lo <= p < hi:
            return name
    return None


def breakeven(p: float, *, rate: float = TAKER_RATE) -> float:
    """Win rate at which a hold-to-settlement taker buy breaks even."""
    return p + rate * p * (1.0 - p)


def observations(rows: Sequence[Dict[str, Any]], *,
                 source_of: Optional[Dict[str, str]] = None
                 ) -> List[Dict[str, Any]]:
    """One record per (window, checkpoint), with provenance preserved.

    `up_won` is the venue's own resolution, used ONLY as the outcome label.
    It never enters an entry feature.
    """
    out: List[Dict[str, Any]] = []
    for r in rows:
        up_ask, dn_ask = r.get("up_ask"), r.get("down_price")
        sides = []
        # DEPTH IS None WHEN THE RECORDER DID NOT PRESERVE SIZES, and that
        # is NOT zero: a zero would read as "measured, and empty", which
        # would make an untestable size requirement look like a measured
        # depth block. The two are reported apart everywhere downstream.
        if up_ask is not None:
            sz = r.get("up_ask_sz")
            sides.append(("UP", float(up_ask),
                          None if sz is None else float(sz)))
        if dn_ask is not None:
            sz = r.get("down_sz")
            sides.append(("DOWN", float(dn_ask),
                          None if sz is None else float(sz)))
        if not sides:
            state = "NEITHER_SIDE_EXECUTABLE"
            cheap = None
        else:
            cheap = min(sides, key=lambda s: s[1])
            state = ("UNDERDOG" if cheap[1] < 0.50
                     else "NO_UNDERDOG_CHEAPEST_GE_50C")
        opp = None
        if cheap is not None:
            others = [s for s in sides if s[0] != cheap[0]]
            opp = others[0][1] if others else None
        won = r.get("up_won")
        rec: Dict[str, Any] = {
            "window": r["ticker"],
            "duration": "BTC5M" if r["ticker"].startswith("BTC05M")
                        else ("BTC15M" if r["ticker"].startswith("BTC15M")
                              else "OTHER"),
            "checkpoint_s": r["tte"],
            "causal_source_timestamp": r.get("t"),
            "observed": bool(r.get("observed")),
            "state": state,
            "both_sides_executable": len(sides) == 2,
            "cheaper_outcome": None if cheap is None else cheap[0],
            "executable_ask": None if cheap is None else cheap[1],
            "executable_depth": None if cheap is None else cheap[2],
            "opposite_side_executable_price": opp,
            "spread": (None if (up_ask is None or r.get("bid") is None)
                       else round(float(up_ask) - float(r["bid"]), 6)),
            "settlement_value": r.get("settlement_value"),
            "strike": r.get("strike"),
            "resolution_side": r.get("resolution_side"),
            "up_won": won,
            "reference_price_proxy": r.get("coinbase"),
            "reference_distance_from_strike_proxy": r.get("cb_dist"),
            "settlement_reference_index": (
                (source_of or {}).get(r["ticker"])),
            "provenance": ("analysis/gemini/info_edge/ie_tte_ladder.json "
                           "(Gemini /v1/book + contract resolution)"),
        }
        if cheap is not None and won is not None:
            rec["underdog_won"] = bool(
                (cheap[0] == "UP" and won) or (cheap[0] == "DOWN" and not won))
        else:
            rec["underdog_won"] = None
        rec["two_sided_ask_sum"] = (
            None if (up_ask is None or dn_ask is None)
            else round(float(up_ask) + float(dn_ask), 6))
        rec["bucket"] = (None if rec["executable_ask"] is None
                         else bucket_of(rec["executable_ask"]))
        out.append(rec)
    return out


# ---------------------------------------------------------------------
# THE BTC15M LOADER. A different recorder, so a different shape -- and one
# real difference that is NOT papered over: `spread_placement` records the
# TOUCH prices and the tte, but NOT the level sizes. So BTC15m observations
# carry `executable_depth: None`, which is honest rather than convenient:
# the $5 whole-contract test REQUIRES depth for the full quantity and
# therefore cannot be run on this duration from this tape. It is reported as
# untestable instead of being run against an assumed size.
BTC15M_SNAP_TOLERANCE_S = 30.0


def _snap(tte: float, grid: Sequence[int],
          tol: float = BTC15M_SNAP_TOLERANCE_S) -> Optional[int]:
    best = min(grid, key=lambda c: abs(tte - c))
    return best if abs(tte - best) <= tol else None


def load_btc15m(paths: Sequence[str], settle: Dict[str, str]
                ) -> List[Dict[str, Any]]:
    """Observations from the BTC15m live-cohort book recorder.

    `settle` maps event ticker -> resolution ("yes"/"no") from Gemini's own
    settled contract records. A window with no settlement record is DROPPED
    rather than guessed.
    """
    import glob as _glob
    import re as _re
    out: List[Dict[str, Any]] = []
    seen = set()
    for pat in paths:
        for f in sorted(_glob.glob(pat)):
            try:
                d = json.load(open(f, encoding="utf-8"))
            except Exception:                              # noqa: BLE001
                continue
            for w in (d.get("windows") or []):
                sym = ((w.get("discovery") or {}).get("symbol")
                       or (w.get("rollover_gate") or {}).get("symbol") or "")
                m = _re.search(r"(BTC15M\d{10})", sym)
                if not m:
                    continue
                tk = m.group(1)
                if tk not in settle:
                    continue
                up_won = settle[tk] == "yes"
                for sp in (w.get("spread_placement") or []):
                    if sp.get("bid") is None or sp.get("ask") is None:
                        continue
                    tte = float(sp["tte_s"])
                    key = (tk, round(tte, 3))
                    if key in seen:
                        continue          # both legs log the same book twice
                    seen.add(key)
                    cp = _snap(tte, BTC15M_CHECKPOINTS_REQUESTED)
                    if cp is None:
                        continue
                    out.append({"ticker": tk, "tte": cp,
                                "t": sp.get("utc"), "observed": True,
                                "up_ask": float(sp["ask"]),
                                "up_ask_sz": None,
                                "bid": float(sp["bid"]),
                                "down_price": round(1.0 - float(sp["bid"]), 6),
                                "down_sz": None,
                                "coinbase": None, "cb_dist": None,
                                "strike": None, "settlement_value": None,
                                "resolution_side": settle[tk],
                                "up_won": up_won,
                                "raw_tte_s": tte})
    return out


def _boot_ci(by_window: Dict[str, List[Tuple[float, bool]]], *,
             n: int = 2000, seed: int = 12345
             ) -> Tuple[Optional[float], Optional[float]]:
    """95% interval on the win rate, RESAMPLING WINDOWS.

    A window contributes all of its observations together, because they share
    one settlement outcome. Resampling observations would understate the
    interval by exactly the amount that matters.
    """
    keys = list(by_window)
    if len(keys) < 2:
        return (None, None)
    rng = random.Random(seed)
    rates = []
    for _ in range(n):
        wins = tot = 0
        for _ in keys:
            k = keys[rng.randrange(len(keys))]
            for _p, w in by_window[k]:
                tot += 1
                wins += 1 if w else 0
        if tot:
            rates.append(100.0 * wins / tot)
    if not rates:
        return (None, None)
    rates.sort()
    lo = rates[int(0.025 * len(rates))]
    hi = rates[min(len(rates) - 1, int(0.975 * len(rates)))]
    return (round(lo, 2), round(hi, 2))


def cell(recs: Sequence[Dict[str, Any]], duration: str) -> Dict[str, Any]:
    """One time x price cell. Every field the brief asks for, or None."""
    usable = [r for r in recs if r.get("underdog_won") is not None
              and r.get("executable_ask") is not None]
    if not usable:
        return {"observations": len(recs), "distinct_windows":
                len({r["window"] for r in recs}), "usable": 0}
    by_w: Dict[str, List[Tuple[float, bool]]] = collections.defaultdict(list)
    for r in usable:
        by_w[r["window"]].append((r["executable_ask"], r["underdog_won"]))
    asks = [r["executable_ask"] for r in usable]
    wins = sum(1 for r in usable if r["underdog_won"])
    avg = statistics.fmean(asks)
    win_pct = 100.0 * wins / len(usable)
    be0 = 100.0 * avg
    be = 100.0 * breakeven(avg)
    lo, hi = _boot_ci(by_w)
    # $1 of RISK, held to settlement: 1/p contracts, so a win returns
    # (1-p)/p per dollar risked and a loss returns -1, fee on the way in.
    pnl1 = []
    for r in usable:
        p = r["executable_ask"]
        if p <= 0:
            continue
        q = 1.0 / p
        fee = TAKER_RATE * q * p * (1.0 - p)
        pnl1.append((q * 1.0 - q * p - fee) if r["underdog_won"]
                    else (-q * p - fee))
    depths = [r["executable_depth"] for r in usable
              if r["executable_depth"] is not None]
    n_w = len(by_w)
    return {
        "distinct_windows": n_w,
        "observations": len(usable),
        "observations_in_cell_before_outcome_filter": len(recs),
        "avg_executable_ask": round(avg, 4),
        "actual_win_pct": round(win_pct, 2),
        "breakeven_pct_zero_fees": round(be0, 2),
        "breakeven_pct_taker_fees": round(be, 2),
        "edge_pp_vs_zero_fees": round(win_pct - be0, 2),
        "edge_pp_vs_taker_fees": round(win_pct - be, 2),
        "win_pct_ci95_window_bootstrap": [lo, hi],
        "edge_pp_ci95_low_vs_taker": (None if lo is None
                                      else round(lo - be, 2)),
        "pnl_per_dollar_risked_taker": (None if not pnl1
                                        else round(statistics.fmean(pnl1), 4)),
        "pnl_per_dollar_risked_total": (None if not pnl1
                                        else round(sum(pnl1), 2)),
        "opportunities_per_day": (
            None if not n_w else
            round(len(usable) / n_w * WINDOWS_PER_DAY[duration], 1)),
        "depth_contracts": {
            "median": (None if not depths
                       else round(statistics.median(depths), 1)),
            "p10": (None if not depths
                    else round(sorted(depths)[len(depths) // 10], 1)),
            "max": (None if not depths else round(max(depths), 1)),
        },
    }


def dollars5(recs: Sequence[Dict[str, Any]], duration: str, *,
             target: float = 5.0) -> Dict[str, Any]:
    """Smallest whole-contract size that nets >= $5 if the side settles.

    Sizing is a DETERMINISTIC FUNCTION OF ENTRY PRICE ONLY -- no martingale,
    no size change after a loss. Depth must cover the whole quantity or the
    opportunity is DEPTH-BLOCKED and is never partially filled.
    """
    def qty_for(p: float) -> Optional[int]:
        if p <= 0 or p >= 1:
            return None
        # net on a win = q*(1-p) - fee ; fee = rate*q*p*(1-p)
        per = (1.0 - p) * (1.0 - TAKER_RATE * p)
        if per <= 0:
            return None
        import math
        return max(1, int(math.ceil(target / per)))

    taken: List[Dict[str, Any]] = []
    blocked = 0
    unknown_depth = 0
    for r in sorted(recs, key=lambda x: (x["window"], -x["checkpoint_s"])):
        if r.get("underdog_won") is None or r.get("executable_ask") is None:
            continue
        if r["state"] != "UNDERDOG":
            continue
        p = r["executable_ask"]
        q = qty_for(p)
        if q is None:
            continue
        depth = r.get("executable_depth")
        if depth is None:
            unknown_depth += 1
            continue
        if depth < q:
            blocked += 1
            continue
        fee = TAKER_RATE * q * p * (1.0 - p)
        cost = q * p + fee
        net = (q * 1.0 - cost) if r["underdog_won"] else (-cost)
        taken.append({"window": r["window"], "checkpoint_s": r["checkpoint_s"],
                      "price": p, "contracts": q, "capital_at_risk": cost,
                      "won": r["underdog_won"], "net": net})
    if not taken:
        return {"trades": 0, "depth_blocked_opportunities": blocked,
                "opportunities_with_UNKNOWN_depth": unknown_depth,
                "why": "no underdog observation had depth for the required "
                       "whole-contract size"}
    nets = [t["net"] for t in taken]
    # streaks and drawdown in chronological window order
    cum = peak = dd = 0.0
    streak = worst_streak = 0
    for t in taken:
        cum += t["net"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
        streak = 0 if t["won"] else streak + 1
        worst_streak = max(worst_streak, streak)
    by_w = collections.defaultdict(float)
    for t in taken:
        by_w[t["window"]] += t["net"]
    concurrent = collections.defaultdict(float)
    for t in taken:
        concurrent[t["window"]] += t["capital_at_risk"]
    n_w = len(by_w)
    per_window = sum(nets) / n_w
    return {
        "trades": len(taken),
        "distinct_windows": n_w,
        "required_contracts": {
            "median": round(statistics.median([t["contracts"]
                                               for t in taken]), 1),
            "min": min(t["contracts"] for t in taken),
            "max": max(t["contracts"] for t in taken)},
        "capital_at_risk_usd": {
            "median": round(statistics.median([t["capital_at_risk"]
                                               for t in taken]), 2),
            "max": round(max(t["capital_at_risk"] for t in taken), 2)},
        "wins": sum(1 for t in taken if t["won"]),
        "losses": sum(1 for t in taken if not t["won"]),
        "total_net_usd": round(sum(nets), 2),
        "net_per_trade_usd": round(statistics.fmean(nets), 3),
        "net_per_window_usd": round(per_window, 3),
        "net_per_day_usd": round(per_window * WINDOWS_PER_DAY[duration], 2),
        "opportunities_per_day": round(len(taken) / n_w
                                       * WINDOWS_PER_DAY[duration], 1),
        "max_consecutive_losses": worst_streak,
        "max_drawdown_usd": round(dd, 2),
        "max_concurrent_capital_usd": round(max(concurrent.values()), 2),
        "depth_blocked_opportunities": blocked,
        "opportunities_with_UNKNOWN_depth": unknown_depth,
        "ratio_to_480_per_day": round(per_window
                                      * WINDOWS_PER_DAY[duration] / 480.0, 4),
        "sizing_rule": ("q = ceil(5 / ((1-p)*(1-0.07p))); a deterministic "
                        "function of entry price alone. No martingale, no "
                        "size increase after a loss."),
    }


def favorite(recs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The inverse question, on the SAME observations and the same discipline.

    This is NOT a recommendation to buy expensive favorites; it asks only
    whether the favored side's settlement frequency beats its fee-adjusted
    executable price.
    """
    out: Dict[str, Any] = {}
    fav_buckets = (("50-60c", .50, .60), ("60-70c", .60, .70),
                   ("70-80c", .70, .80), ("80-90c", .80, .90),
                   ("90-99c", .90, 1.00))
    rows = []
    for r in recs:
        if r.get("up_won") is None or not r["both_sides_executable"]:
            continue
        # the FAVORED side is the dearer executable one
        cheap, opp = r["executable_ask"], r["opposite_side_executable_price"]
        if cheap is None or opp is None or opp <= cheap:
            continue
        side = "DOWN" if r["cheaper_outcome"] == "UP" else "UP"
        won = (r["up_won"] if side == "UP" else not r["up_won"])
        rows.append((opp, won, r["window"]))
    for name, lo, hi in fav_buckets:
        sub = [x for x in rows if lo <= x[0] < hi]
        if not sub:
            out[name] = {"observations": 0}
            continue
        by_w = collections.defaultdict(list)
        for p, w, win in sub:
            by_w[win].append((p, w))
        avg = statistics.fmean([x[0] for x in sub])
        wp = 100.0 * sum(1 for x in sub if x[1]) / len(sub)
        be = 100.0 * breakeven(avg)
        clo, chi = _boot_ci(by_w)
        out[name] = {"observations": len(sub),
                     "distinct_windows": len(by_w),
                     "avg_executable_ask": round(avg, 4),
                     "actual_win_pct": round(wp, 2),
                     "breakeven_pct_taker_fees": round(be, 2),
                     "edge_pp_vs_taker_fees": round(wp - be, 2),
                     "win_pct_ci95_window_bootstrap": [clo, chi]}
    out["note"] = ("requires BOTH sides executable so the favorite is "
                   "identified from a real two-sided book, which is why n is "
                   "far smaller than the underdog matrix")
    return out


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder",
                    default="analysis/gemini/info_edge/ie_tte_ladder.json")
    ap.add_argument("--usable",
                    default="analysis/gemini/info_edge/ie_usable_windows.json")
    ap.add_argument("--bulk", default="analysis/gemini/p7_bulk_tape.json")
    ap.add_argument("--cohorts", default=(
        "analysis/gemini/cohort_run_2026*.json,"
        "analysis/gemini/cohort_window_2026*.json,"
        "analysis/gemini/continuous_run_2026*.json"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rows = json.load(open(a.ladder, encoding="utf-8"))
    settle: Dict[str, str] = {}
    if os.path.exists(a.bulk):
        for r in json.load(open(a.bulk, encoding="utf-8")):
            if r.get("resolution") in ("yes", "no") and r.get("event_ticker"):
                settle[r["event_ticker"]] = r["resolution"]
    rows = list(rows) + load_btc15m(a.cohorts.split(","), settle)
    src: Dict[str, str] = {}
    if os.path.exists(a.usable):
        for u in json.load(open(a.usable, encoding="utf-8")):
            if u.get("source"):
                src[u["ticker"]] = u["source"]
    recs = observations(rows, source_of=src)
    durations = sorted({r["duration"] for r in recs})
    rep: Dict[str, Any] = {
        "label": LABEL,
        "lane": "GEMINI -- no PMUS data, thresholds, fees or samples",
        "fee_model": {"taker_rate": TAKER_RATE, "maker_rate": MAKER_RATE,
                      "formula": "rate * q * p * (1 - p)",
                      "source": ("gemini.com/fees/predictions, recorded in "
                                 "src/gemini_execution/rebate_program.py "
                                 "2026-09-19"),
                      "reconciliation": ("NOT fully reconciled against our "
                                         "own authenticated fills; every "
                                         "cell also reports zero-fee "
                                         "breakeven")},
        "execution_discipline": (
            "executable ask only. UP pays the YES ask; DOWN pays 1 - the YES "
            "bid and consumes the bid ladder. No midpoint, no last trade, no "
            "UI probability, no touch-beyond-size, no queue assumption, no "
            "future quote, no post-settlement reference as an entry feature."),
        "statistical_unit": "WINDOW (bootstrap resamples windows, not ticks)",
        "durations_present": durations,
        "checkpoints": {
            "BTC5M_requested": list(BTC5M_CHECKPOINTS_REQUESTED),
            "BTC5M_recorded_extra": list(BTC5M_CHECKPOINTS_EXTRA),
            "BTC15M_requested": list(BTC15M_CHECKPOINTS_REQUESTED),
        },
        "by_duration": {},
        "accounting_label": ("historical replay on Gemini's own recorded "
                             "books and settlements; no order was sent"),
    }
    for dur in durations:
        if dur == "OTHER":
            continue
        drecs = [r for r in recs if r["duration"] == dur]
        grid = sorted({r["checkpoint_s"] for r in drecs}, reverse=True)
        want = (BTC5M_CHECKPOINTS_REQUESTED if dur == "BTC5M"
                else BTC15M_CHECKPOINTS_REQUESTED)
        state_counts = collections.Counter(r["state"] for r in drecs)
        out: Dict[str, Any] = {
            "windows": len({r["window"] for r in drecs}),
            "observations": len(drecs),
            "recorded_checkpoints": grid,
            "requested_checkpoints_not_collected": [
                c for c in want if c not in grid],
            "state_counts": dict(state_counts),
            "both_sides_executable_observations": sum(
                1 for r in drecs if r["both_sides_executable"]),
            "settlement_reference_index_split": dict(collections.Counter(
                r["settlement_reference_index"] for r in drecs)),
            "matrix": {},
            "by_checkpoint_all_buckets": {},
            "by_bucket_all_checkpoints": {},
        }
        sums = [r["two_sided_ask_sum"] for r in drecs
                if r["two_sided_ask_sum"] is not None]
        spr = [r["spread"] for r in drecs if r["spread"] is not None]
        out["structural_cost"] = {
            "two_sided_observations": len(sums),
            "ask_UP_plus_ask_DOWN": (
                None if not sums else {
                    "median": round(statistics.median(sums), 4),
                    "mean": round(statistics.fmean(sums), 4),
                    "min": round(min(sums), 4), "max": round(max(sums), 4),
                    "fraction_above_1": round(
                        sum(1 for x in sums if x > 1.0) / len(sums), 4)}),
            "up_touch_spread": (
                None if not spr else {
                    "median": round(statistics.median(spr), 4),
                    "p10": round(sorted(spr)[len(spr) // 10], 4),
                    "p90": round(sorted(spr)[9 * len(spr) // 10], 4)}),
            "why_it_matters": (
                "a taker pays half this spread on entry. When the two "
                "executable asks sum materially above 1.00, no directional "
                "taker bet on either side can be positive on average: the "
                "excess IS the cost, and it is charged before any view."),
        }
        und = [r for r in drecs if r["state"] == "UNDERDOG"]
        for cp in grid:
            row: Dict[str, Any] = {}
            at_cp = [r for r in und if r["checkpoint_s"] == cp]
            for name, lo, hi in BUCKETS:
                sub = [r for r in at_cp if r["bucket"] == name]
                row[name] = cell(sub, dur) if sub else {"observations": 0}
            out["matrix"]["%ds" % cp] = row
            out["by_checkpoint_all_buckets"]["%ds" % cp] = cell(at_cp, dur)
        for name, lo, hi in BUCKETS:
            sub = [r for r in und if r["bucket"] == name]
            out["by_bucket_all_checkpoints"][name] = (cell(sub, dur) if sub
                                                      else {"observations": 0})
        out["all_underdog_pooled"] = cell(und, dur)
        if all(r.get("executable_depth") is None for r in drecs
               if r.get("executable_ask") is not None):
            out["dollar5_test"] = {
                "testable": False,
                "why": ("this duration's recorder preserved the touch "
                        "prices but not the level sizes, and the $5 rule "
                        "requires displayed depth for the WHOLE quantity. "
                        "Running it against an assumed size would fabricate "
                        "the fill, so it is reported untestable and moved to "
                        "forward shadow collection.")}
        else:
            out["dollar5_test"] = dollars5(drecs, dur)
        out["favorite_test"] = favorite(drecs)
        rep["by_duration"][dur] = out

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    for dur, d in rep["by_duration"].items():
        print("%s %s windows=%d obs=%d underdog=%d not_collected=%s"
              % (LABEL, dur, d["windows"], d["observations"],
                 d["state_counts"].get("UNDERDOG", 0),
                 d["requested_checkpoints_not_collected"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
