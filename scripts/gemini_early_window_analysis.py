"""FROZEN PREREGISTERED ANALYSIS of the BTC5m early window. Read-only.

PREREGISTERED means: this file is written and fingerprinted BEFORE any
profitability number from the new recorder has been inspected, and it is then
run UNCHANGED on each tranche. If a later tranche disagrees with an earlier
one, that is the finding. Editing this file after seeing a tranche's
profitability and rerunning it is exactly the thing preregistration exists to
prevent; the fingerprint in PREREGISTRATION.md is what makes that detectable.

THE PRIMARY QUESTION, fixed here: during T+15 .. T+120, can the causal
observed state D_t = (published LRRR_XBT reference - strike), its trajectory,
and the contemporaneous executable book state identify positive expectancy
BEFORE the winning side becomes unavailable?

TEN THINGS THIS FILE COMMITS TO, each enforced in code rather than asserted
in prose:

 1. ECONOMIC_TWO_SIDED is separate from structural TWO_SIDED and is reported
    at EVERY predeclared size level, never at a selected one. Structural =
    both sides have >= 1 level. Economic(S) = BOTH sides can actually fill S
    contracts. Two depth variants, both reported: TOUCH (best level's own
    size) and CUMULATIVE (walking the ladder).
 2. D_t is computed from the latest published reference whose venue event
    time is <= the checkpoint's own wall clock. It is NOT called Z, and it is
    NOT normalised by anything, because the LRRR statistic is not identified.
 3. Settlement probability is reported by checkpoint x sign/magnitude of D_t
    x executable-ask bucket, separately for UP and DOWN.
 4. Every probability is reported against the ACTUAL executable ask/VWAP plus
    the published taker fee, as an edge in percentage points and as EV per
    share. A win rate without its breakeven is never reported alone.
 5. Per-window and per-day economics divide by ALL observed windows,
    including NO_TRADE ones. The denominator is stated on every such figure.
 6. No cutoff is optimised. The bins below are fixed constants and the script
    has no search, no argmax over thresholds, and no "best cell" output.
 7. The window set is split chronologically and the OOS partition is SEALED:
    `analyse()` refuses to touch it without an explicit unseal argument, and
    the refusal is recorded in the report.
 8. Trajectory features are causal by construction and the script ASSERTS
    that no future label (the eventual transition, the winner, the
    disappearance time) appears among them.
 9. Z REMAINS NOT IDENTIFIED. D_t is an observed difference, not a
    normalised state.
10. No order path exists in this file.

Accounting label: analysis of recorded public market data. No order was sent.
"""
from __future__ import annotations

import argparse
import collections
import glob
import gzip
import hashlib
import json
import math
import os
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

LABEL = "GEMINI_EARLY_WINDOW_ANALYSIS_V1_FROZEN"

# ---- published taker fee schedule (gemini.com/fees/predictions, recorded in
# ---- src/gemini_execution/rebate_program.py 2026-09-19). NOT reconciled
# ---- against our own fills, so zero-fee breakeven is reported beside it.
TAKER_RATE = 0.07

#: PREDECLARED. Reported in full; never selected from.
SIZE_LEVELS: Tuple[int, ...] = (1, 5, 10, 25, 50, 100)
DEPTH_VARIANTS: Tuple[str, ...] = ("TOUCH", "CUMULATIVE")

#: the primary question's region, fixed
PRIMARY_CHECKPOINTS: Tuple[float, ...] = (15, 30, 45, 60, 90, 120)
ALL_EARLY: Tuple[float, ...] = (5, 15, 30, 45, 60, 90, 120, 150, 180)

#: D_t bins in DOLLARS, signed. Chosen before any outcome was read, to span
#: the strike symmetrically on a ~$86k underlying.
D_BINS_USD: Tuple[float, ...] = (-1e9, -200, -100, -50, -20, -5, 0,
                                 5, 20, 50, 100, 200, 1e9)
#: and in basis points of the strike, because dollars do not travel across
#: price levels
D_BINS_BPS: Tuple[float, ...] = (-1e9, -25, -12, -6, -2, 0, 2, 6, 12, 25, 1e9)

#: executable-ask buckets, for the side being bought
ASK_BINS: Tuple[float, ...] = (0.0, 0.10, 0.20, 0.30, 0.40, 0.50,
                               0.60, 0.70, 0.80, 0.90, 1.0)

WINDOWS_PER_DAY = 288
#: chronological split. The LAST fraction is SEALED.
OOS_FRACTION = 0.40


def self_fingerprint() -> str:
    return hashlib.sha256(
        open(os.path.abspath(__file__), "rb").read()).hexdigest()


def breakeven(p: float) -> float:
    """Win rate at which a hold-to-settlement taker buy breaks even."""
    return p + TAKER_RATE * p * (1.0 - p)


def _bin(x: Optional[float], edges: Sequence[float]) -> Optional[str]:
    if x is None:
        return None
    for i in range(len(edges) - 1):
        if edges[i] <= x < edges[i + 1]:
            lo = "-inf" if edges[i] <= -1e8 else ("%g" % edges[i])
            hi = "+inf" if edges[i + 1] >= 1e8 else ("%g" % edges[i + 1])
            return "[%s,%s)" % (lo, hi)
    return None


# --------------------------------------------------------------- the ladder
def walk(levels: Sequence[Sequence[Any]], size: float
         ) -> Dict[str, Any]:
    """Consume `size` from an ordered ladder. Reports shortfall, never fills
    what is not displayed."""
    filled = 0.0
    cost = 0.0
    used = 0
    for lvl in levels:
        if filled >= size - 1e-12:
            break
        try:
            px, qty = float(lvl[0]), float(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if qty <= 0:
            continue
        take = min(qty, size - filled)
        filled += take
        cost += take * px
        used += 1
    return {"filled": round(filled, 6),
            "fully_executable": filled >= size - 1e-12,
            "vwap": (None if filled <= 0 else round(cost / filled, 8)),
            "levels_used": used,
            "shortfall": round(max(0.0, size - filled), 6),
            "displayed_total": round(sum(
                float(l[1]) for l in levels
                if len(l) > 1 and str(l[1]).replace(".", "").isdigit()), 6)}


def acquisition(cp: Dict[str, Any], side: str, size: float,
                variant: str) -> Dict[str, Any]:
    """What it costs to BUY `size` of `side`, from the recorded ladders.

    UP pays the YES ask ladder. DOWN pays the DERIVED NO ask ladder, which is
    1 - the YES bid at the YES bid's size -- buying NO and selling YES are the
    same trade, so DOWN consumes the bid side.
    """
    yes = cp.get("yes_ladder") or {}
    if side == "UP":
        levels = list(yes.get("asks") or [])
    else:
        levels = list((cp.get("no_ladder_derived") or {}).get("asks") or [])
    if variant == "TOUCH":
        levels = levels[:1]
    got = walk(levels, size)
    return dict(got, side=side, size=size, depth_variant=variant,
                touch_price=(None if not levels else float(levels[0][0])),
                touch_size=(None if not levels else float(levels[0][1])))


def economic_two_sided(cp: Dict[str, Any], size: float,
                       variant: str) -> bool:
    """BOTH sides can actually fill `size`. Distinct from structural."""
    return (acquisition(cp, "UP", size, variant)["fully_executable"]
            and acquisition(cp, "DOWN", size, variant)["fully_executable"])


# ------------------------------------------------------------- tape loading
def load_tapes(pattern: str) -> Dict[str, Any]:
    """Every recorder row, plus the published reference series."""
    cps: List[Dict[str, Any]] = []
    settle: Dict[str, Dict[str, Any]] = {}
    windows: Dict[str, Dict[str, Any]] = {}
    refs: List[Tuple[float, float]] = []
    files = sorted(glob.glob(pattern))
    for f in files:
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                k = r.get("kind")
                if k == "checkpoint":
                    cps.append(r)
                elif k == "index_price":
                    e, v = r.get("parsed_event_time"), r.get("parsed_value")
                    if e is None or v is None:
                        continue
                    try:
                        refs.append((float(e), float(v)))
                    except (TypeError, ValueError):
                        continue
                elif k == "settlement":
                    ev = r.get("raw_event") or {}
                    for c in (ev.get("contracts") or []):
                        sym = c.get("instrumentSymbol")
                        if not sym:
                            continue
                        settle[sym] = {
                            "resolution_side": c.get("resolutionSide"),
                            "settlement_value": c.get("settlementValue"),
                            "strike": (c.get("strike") or {}).get("value"),
                            "availableAt": (c.get("strike")
                                            or {}).get("availableAt"),
                            "ticker": ev.get("ticker")}
                elif k in ("window", "window_poll"):
                    sym = r.get("market")
                    if sym and sym not in windows:
                        windows[sym] = {
                            "strike": r.get("strike"),
                            "availableAt": r.get("availableAt"),
                            "expiry": r.get("expiry"),
                            "resolved_index": r.get("resolved_index"),
                            "ticker": r.get("ticker")}
                    elif sym and r.get("strike") and not windows[sym].get(
                            "strike"):
                        windows[sym]["strike"] = r.get("strike")
    refs.sort()
    return {"checkpoints": cps, "settlements": settle, "windows": windows,
            "reference_series": refs, "files": files}


def reference_at(refs: Sequence[Tuple[float, float]],
                 wall_s: float) -> Dict[str, Any]:
    """CAUSAL: the latest published reference whose venue event time <= now.

    A bisect on an ascending list. If the newest such observation is older
    than 10s the state is reported STALE and the age travels with it; it is
    never silently used as if fresh, and never interpolated forward.
    """
    import bisect
    i = bisect.bisect_right([e for e, _ in refs], wall_s) - 1
    if i < 0:
        return {"value": None, "age_s": None, "status": "NO_REFERENCE_YET"}
    e, v = refs[i]
    age = wall_s - e
    return {"value": v, "event_time": e, "age_s": round(age, 3),
            "status": ("FRESH" if age <= 10.0 else "STALE"),
            "note": "latest published value with E <= checkpoint wall clock"}


# ------------------------------------------------------------ observations
def observations(tapes: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One record per (window, early checkpoint). Causal features only."""
    refs = tapes["reference_series"]
    out: List[Dict[str, Any]] = []
    for cp in tapes["checkpoints"]:
        if cp.get("duration") != "BTC05M":
            continue
        if cp.get("checkpoint_family") != "EARLY_FROM_T0":
            continue
        sym = cp.get("market")
        st = tapes["settlements"].get(sym)
        win = tapes["windows"].get(sym) or {}
        strike = (st or {}).get("strike") or win.get("strike")
        if strike in (None, ""):
            continue
        wall = float(cp["t_ns"]) / 1e9
        ref = reference_at(refs, wall)
        d_usd = (None if ref["value"] is None
                 else ref["value"] - float(strike))
        d_bps = (None if d_usd is None
                 else 10000.0 * d_usd / float(strike))
        rec: Dict[str, Any] = {
            "window": sym, "ticker": cp.get("ticker"),
            "checkpoint_s": float(cp["checkpoint_s"]),
            "wall_s": wall,
            "expiry_epoch": cp.get("expiry_epoch"),
            "structural_book_state": cp.get("book_state"),
            "book_integrity": cp.get("book_integrity"),
            "quote_age_s": cp.get("quote_age_s"),
            "event_age_s": cp.get("event_age_s"),
            "t0_source": cp.get("t0_source"),
            "strike": float(strike),
            # ---- the causal observed state. NOT Z.
            "reference_value": ref["value"],
            "reference_age_s": ref["age_s"],
            "reference_status": ref["status"],
            "D_t_usd": None if d_usd is None else round(d_usd, 4),
            "D_t_bps": None if d_bps is None else round(d_bps, 4),
            "D_t_sign": (None if d_usd is None
                         else ("POSITIVE" if d_usd > 0
                               else ("NEGATIVE" if d_usd < 0 else "ZERO"))),
            "D_t_bin_usd": _bin(d_usd, D_BINS_USD),
            "D_t_bin_bps": _bin(d_bps, D_BINS_BPS),
            "_ladders": {"yes_ladder": cp.get("yes_ladder"),
                         "no_ladder_derived": cp.get("no_ladder_derived")},
            # ---- outcome LABELS, never features
            "resolution_side": (st or {}).get("resolution_side"),
            "up_won": (None if not st or not st.get("resolution_side")
                       else st["resolution_side"] == "yes"),
            "settlement_value": (st or {}).get("settlement_value"),
        }
        for size in SIZE_LEVELS:
            for variant in DEPTH_VARIANTS:
                key = "%s_%d" % (variant.lower(), size)
                up = acquisition(rec["_ladders"], "UP", size, variant)
                dn = acquisition(rec["_ladders"], "DOWN", size, variant)
                rec["econ_two_sided_" + key] = (up["fully_executable"]
                                                and dn["fully_executable"])
                rec["up_vwap_" + key] = up["vwap"] if up[
                    "fully_executable"] else None
                rec["down_vwap_" + key] = dn["vwap"] if dn[
                    "fully_executable"] else None
                rec["up_shortfall_" + key] = up["shortfall"]
                rec["down_shortfall_" + key] = dn["shortfall"]
        yl = (cp.get("yes_ladder") or {})
        rec["yes_levels_bids"] = len(yl.get("bids") or [])
        rec["yes_levels_asks"] = len(yl.get("asks") or [])
        rec["yes_displayed_bid_size"] = sum(
            float(l[1]) for l in (yl.get("bids") or []) if len(l) > 1)
        rec["yes_displayed_ask_size"] = sum(
            float(l[1]) for l in (yl.get("asks") or []) if len(l) > 1)
        out.append(rec)
    return out


CAUSAL_FEATURE_KEYS = (
    "checkpoint_s", "structural_book_state", "book_integrity", "quote_age_s",
    "event_age_s", "reference_value", "reference_age_s", "reference_status",
    "D_t_usd", "D_t_bps", "D_t_sign", "D_t_bin_usd", "D_t_bin_bps",
    "yes_levels_bids", "yes_levels_asks", "yes_displayed_bid_size",
    "yes_displayed_ask_size", "strike",
)
FORBIDDEN_FEATURE_SUBSTRINGS = ("won", "resolution", "settlement",
                                "eventual", "winner", "disappear", "future")


def assert_no_leakage(feature_keys: Sequence[str]) -> None:
    """Commitment 8, enforced. A future label may not be a feature."""
    for k in feature_keys:
        low = k.lower()
        for bad in FORBIDDEN_FEATURE_SUBSTRINGS:
            if bad in low:
                raise AssertionError(
                    "feature %r contains forbidden substring %r" % (k, bad))


# ------------------------------------------------------------------ scoring
def cell(rows: Sequence[Dict[str, Any]], side: str, key: str,
         n_windows_all: int) -> Dict[str, Any]:
    """Buy `side` at its executable VWAP, hold to settlement.

    Reports the win rate ONLY beside its fee-adjusted breakeven, per
    commitment 4, and divides per-window economics by ALL windows, per
    commitment 5.
    """
    tr = []
    for r in rows:
        if r.get("up_won") is None:
            continue
        p = r.get("%s_vwap_%s" % (side.lower(), key))
        if p is None or not (0.0 < p < 1.0):
            continue
        fee = TAKER_RATE * p * (1.0 - p)
        won = (r["up_won"] if side == "UP" else (not r["up_won"]))
        tr.append({"window": r["window"], "p": p, "won": bool(won),
                   "net": (1.0 - p - fee) if won else (-p - fee),
                   "fee": fee})
    if not tr:
        return {"trades": 0, "windows_all": n_windows_all}
    wins = sum(1 for t in tr if t["won"])
    avg = statistics.fmean([t["p"] for t in tr])
    wp = 100.0 * wins / len(tr)
    be = 100.0 * breakeven(avg)
    nets = [t["net"] for t in tr]
    n_w = len({t["window"] for t in tr})
    return {
        "trades": len(tr),
        "distinct_windows_traded": n_w,
        "windows_all": n_windows_all,
        "avg_executable_vwap": round(avg, 4),
        "settlement_win_pct": round(wp, 2),
        "breakeven_pct_zero_fees": round(100.0 * avg, 2),
        "breakeven_pct_taker_fees": round(be, 2),
        "win_minus_breakeven_pp": round(wp - be, 2),
        "ev_per_trade_usd_per_share": round(statistics.fmean(nets), 5),
        "ev_total_usd_per_share": round(sum(nets), 4),
        "fee_per_trade_usd_per_share": round(
            statistics.fmean([t["fee"] for t in tr]), 5),
        "opportunity_rate_per_window_all": round(len(tr)
                                                 / max(1, n_windows_all), 4),
        "ev_per_window_ALL_WINDOWS_usd_per_share": round(
            sum(nets) / max(1, n_windows_all), 5),
        "projected_ev_per_day_usd_per_share": round(
            sum(nets) / max(1, n_windows_all) * WINDOWS_PER_DAY, 3),
        "denominator_note": ("per-window and per-day figures divide by ALL "
                             "observed windows (%d), NO_TRADE windows "
                             "included" % n_windows_all),
        "size_note": ("per SHARE. Multiply by the size level in `key` for the "
                      "notional figure; the VWAP already reflects that size"),
    }


def trajectory(obs: Sequence[Dict[str, Any]], key: str
               ) -> Dict[str, Any]:
    """Commitment 8: what precedes the ECONOMIC two-sided -> one-sided move.

    Causal only. The transition itself is detected per window for DESCRIPTION,
    and the features that describe its run-up use nothing later than the
    checkpoint they belong to.
    """
    by_w: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in obs:
        by_w[r["window"]].append(r)
    out: List[Dict[str, Any]] = []
    for w, rows in by_w.items():
        rows = sorted(rows, key=lambda r: r["checkpoint_s"])
        econ = [bool(r.get("econ_two_sided_" + key)) for r in rows]
        trans_at = None
        for i in range(1, len(rows)):
            if econ[i - 1] and not econ[i]:
                trans_at = rows[i]["checkpoint_s"]
                break
        ds = [r["D_t_usd"] for r in rows]
        for i, r in enumerate(rows):
            prev = [x for x in ds[:i + 1] if x is not None]
            mom = (None if len(prev) < 2 else round(prev[-1] - prev[0], 4))
            step = (None if len(prev) < 2 else round(prev[-1] - prev[-2], 4))
            out.append({
                "window": w,
                "causal_features": {
                    "checkpoint_s": r["checkpoint_s"],
                    "D_t_usd": r["D_t_usd"], "D_t_bps": r["D_t_bps"],
                    "D_t_momentum_since_first_obs_usd": mom,
                    "D_t_step_since_prev_obs_usd": step,
                    "abs_D_t_usd": (None if r["D_t_usd"] is None
                                    else abs(r["D_t_usd"])),
                    "structural_book_state": r["structural_book_state"],
                    "economically_two_sided_now": econ[i],
                    "yes_displayed_bid_size": r["yes_displayed_bid_size"],
                    "yes_displayed_ask_size": r["yes_displayed_ask_size"],
                    "yes_levels_bids": r["yes_levels_bids"],
                    "yes_levels_asks": r["yes_levels_asks"],
                    "quote_age_s": r["quote_age_s"],
                },
                "DESCRIPTIVE_LABEL_NOT_A_FEATURE": {
                    "econ_transition_checkpoint_s": trans_at,
                    "warning": ("retrospective; quarantined out of "
                                "causal_features and asserted so"),
                },
            })
    for row in out:
        assert_no_leakage(list(row["causal_features"]))
        assert "econ_transition_checkpoint_s" not in row["causal_features"]
    trans = [r["DESCRIPTIVE_LABEL_NOT_A_FEATURE"]["econ_transition_checkpoint_s"]
             for r in out]
    seen = {}
    for r in out:
        seen[r["window"]] = r[
            "DESCRIPTIVE_LABEL_NOT_A_FEATURE"]["econ_transition_checkpoint_s"]
    return {"size_key": key, "rows": out,
            "windows": len(seen),
            "windows_with_an_econ_transition": sum(
                1 for v in seen.values() if v is not None),
            "transition_checkpoint_distribution": dict(collections.Counter(
                v for v in seen.values() if v is not None))}


# ------------------------------------------------------------------- report
def analyse(obs: List[Dict[str, Any]], *, tranche: str,
            unseal_oos: bool = False) -> Dict[str, Any]:
    wins_all = sorted({r["window"] for r in obs
                       if r.get("up_won") is not None},
                      key=lambda w: min(
                          x["wall_s"] for x in obs if x["window"] == w))
    n_all = len(wins_all)
    cut = int(math.floor(n_all * (1.0 - OOS_FRACTION)))
    discovery, sealed = wins_all[:cut], wins_all[cut:]
    if not unseal_oos:
        use = set(discovery)
        seal_note = ("the last %d of %d completed windows are SEALED as OOS "
                     "and were NOT read; pass unseal_oos=True only to freeze "
                     "a candidate" % (len(sealed), n_all))
    else:
        use = set(wins_all)
        seal_note = "OOS EXPLICITLY UNSEALED by the caller"
    rows = [r for r in obs if r["window"] in use]
    n_w = len({r["window"] for r in rows})

    structural = collections.Counter(r["structural_book_state"] for r in rows)
    econ_counts: Dict[str, Any] = {}
    for size in SIZE_LEVELS:
        for variant in DEPTH_VARIANTS:
            key = "%s_%d" % (variant.lower(), size)
            econ_counts[key] = {
                "observations": len(rows),
                "economically_two_sided": sum(
                    1 for r in rows if r.get("econ_two_sided_" + key)),
                "share_pct": (None if not rows else round(
                    100.0 * sum(1 for r in rows
                                if r.get("econ_two_sided_" + key))
                    / len(rows), 2)),
            }

    tables: Dict[str, Any] = {}
    for size in SIZE_LEVELS:
        for variant in DEPTH_VARIANTS:
            key = "%s_%d" % (variant.lower(), size)
            prim = [r for r in rows
                    if r["checkpoint_s"] in PRIMARY_CHECKPOINTS]
            blk: Dict[str, Any] = {"pooled_primary_T15_T120": {}}
            for side in ("UP", "DOWN"):
                blk["pooled_primary_T15_T120"][side] = cell(prim, side, key,
                                                            n_w)
            blk["by_checkpoint"] = {}
            for cp in ALL_EARLY:
                at = [r for r in rows if r["checkpoint_s"] == cp]
                blk["by_checkpoint"]["T+%gs" % cp] = {
                    s: cell(at, s, key, n_w) for s in ("UP", "DOWN")}
            blk["by_D_t_sign"] = {}
            for sg in ("NEGATIVE", "ZERO", "POSITIVE"):
                sub = [r for r in prim if r["D_t_sign"] == sg]
                blk["by_D_t_sign"][sg] = {
                    s: cell(sub, s, key, n_w) for s in ("UP", "DOWN")}
            blk["by_D_t_bin_usd"] = {}
            for b in sorted({r["D_t_bin_usd"] for r in prim
                             if r["D_t_bin_usd"]}):
                sub = [r for r in prim if r["D_t_bin_usd"] == b]
                blk["by_D_t_bin_usd"][b] = {
                    s: cell(sub, s, key, n_w) for s in ("UP", "DOWN")}
            blk["by_D_t_bin_bps"] = {}
            for b in sorted({r["D_t_bin_bps"] for r in prim
                             if r["D_t_bin_bps"]}):
                sub = [r for r in prim if r["D_t_bin_bps"] == b]
                blk["by_D_t_bin_bps"][b] = {
                    s: cell(sub, s, key, n_w) for s in ("UP", "DOWN")}
            blk["by_ask_bucket"] = {}
            for side in ("UP", "DOWN"):
                per: Dict[str, Any] = {}
                for i in range(len(ASK_BINS) - 1):
                    lo, hi = ASK_BINS[i], ASK_BINS[i + 1]
                    sub = [r for r in prim
                           if (r.get("%s_vwap_%s" % (side.lower(), key))
                               is not None)
                           and lo <= r["%s_vwap_%s"
                                       % (side.lower(), key)] < hi]
                    per["[%.2f,%.2f)" % (lo, hi)] = cell(sub, side, key, n_w)
                blk["by_ask_bucket"][side] = per
            blk["economically_two_sided_only"] = {
                s: cell([r for r in prim if r.get("econ_two_sided_" + key)],
                        s, key, n_w) for s in ("UP", "DOWN")}
            tables[key] = blk

    return {
        "label": LABEL,
        "analysis_fingerprint_sha256": self_fingerprint(),
        "tranche": tranche,
        "preregistered": ("this analysis was written and fingerprinted before "
                          "any profitability number from the recorder was "
                          "inspected, and is run UNCHANGED per tranche"),
        "no_threshold_optimization": ("the bins are fixed constants; there is "
                                      "no search, no argmax over thresholds "
                                      "and no best-cell output"),
        "Z_status": ("NOT IDENTIFIED. D_t is an observed difference "
                     "(published LRRR_XBT reference minus strike), not a "
                     "normalised state, because the LRRR statistic's "
                     "mechanics are not established."),
        "primary_question": ("during T+15..T+120, can causal D_t, its "
                             "trajectory and the contemporaneous executable "
                             "book state identify positive expectancy before "
                             "the winning side becomes unavailable"),
        "windows_completed_total": n_all,
        "windows_used_discovery": len(discovery),
        "windows_sealed_oos": len(sealed),
        "oos_seal": seal_note,
        "observations_used": len(rows),
        "structural_book_states": dict(structural),
        "economic_two_sided_census": econ_counts,
        "size_levels": list(SIZE_LEVELS),
        "depth_variants": list(DEPTH_VARIANTS),
        "fee_model": {"taker_rate": TAKER_RATE,
                      "formula": "rate * q * p * (1 - p)",
                      "breakeven": "p + 0.07*p*(1-p)",
                      "reconciliation": ("published schedule, NOT reconciled "
                                         "against our own fills; zero-fee "
                                         "breakeven reported beside it")},
        "tables": tables,
        "trajectory_touch_1": trajectory(rows, "touch_1"),
        "trajectory_cumulative_25": trajectory(rows, "cumulative_25"),
        "accounting_label": ("analysis of recorded public market data; no "
                             "order was sent"),
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes",
                    default="analysis/gemini/btc5m_rec/btc5m_rec_*.jsonl*")
    ap.add_argument("--tranche", default="unlabelled")
    ap.add_argument("--unseal-oos", action="store_true",
                    help="read the SEALED OOS partition; only to freeze a "
                         "candidate")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    tapes = load_tapes(a.tapes)
    obs = observations(tapes)
    assert_no_leakage(CAUSAL_FEATURE_KEYS)
    for r in obs:
        r.pop("_ladders", None)
    rep = analyse(obs, tranche=a.tranche, unseal_oos=a.unseal_oos)
    rep["tapes"] = [os.path.basename(f) for f in tapes["files"]]
    rep["reference_series_points"] = len(tapes["reference_series"])
    rep["settlements_seen"] = len(tapes["settlements"])
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print(json.dumps({
        "label": LABEL, "fingerprint": rep["analysis_fingerprint_sha256"],
        "tranche": a.tranche,
        "windows_completed_total": rep["windows_completed_total"],
        "windows_used_discovery": rep["windows_used_discovery"],
        "windows_sealed_oos": rep["windows_sealed_oos"],
        "observations_used": rep["observations_used"],
        "reference_series_points": rep["reference_series_points"],
        "structural_book_states": rep["structural_book_states"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
