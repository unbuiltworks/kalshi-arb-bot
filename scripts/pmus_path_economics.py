"""Sequential path-economics of PMUS BTC15m: does MOVEMENT create pairs?

THE MECHANISM UNDER TEST, and what it is not. Buy one outcome; wait; if the
opposite outcome later becomes cheap enough that the ALL-IN pair cost (both
executable prices plus both venue-cent taker fees) falls below the $1.00 that a
matched pair settles for, buy it and lock the pair; possibly extend on later
oscillations; settle. NEITHER leg has to be a static mispricing. The question is
whether the path through time supplies the second leg.

This is NOT a rescue of STATE_CONDITIONAL_PAIR_V1, which was falsified on this
same tape (`analysis/l5/oos/OOS_ECONOMIC_RESULT.md`): rank correlation -0.084
and an inverted edge-bucket table. Its probability-EV signal, q - executable
price - fee, is deliberately ABSENT here. It is never computed, never consulted,
and never used to select a timestamp. Phase 1 considers EVERY causally eligible
second with no entry condition at all, which is the only entry rule that cannot
smuggle a selection signal back in.

WHAT IS AN UPPER BOUND AND MUST NOT BE READ AS A STRATEGY. Phase 1's "best
achievable subsequent pair cost" is the minimum over the REMAINDER OF THE
WINDOW, chosen with hindsight. No controller can achieve it, because it would
have to know the future minimum. It is an OPPORTUNITY MAP: it bounds what the
path makes available. Any claim that it is attainable would be a lookahead
error, and the Phase 2 numbers -- which are causal -- are the ones that describe
behaviour a controller could have.

ANTI-LOOKAHEAD. No future information selects a first purchase anywhere in this
file. In Phase 1 the first leg is every eligible second in turn, and the future
is used only to DESCRIBE what subsequently happened to that already-made
purchase. In Phase 2 the completion test at second j uses only rows at or before
j. The settlement label is applied after all decisions.

EXECUTION REALISM, deliberately pessimistic. Taker economics throughout: the
executable UP price is the displayed ask and the executable DOWN price is
1 - displayed bid, each with the depth displayed AT THAT TOUCH. There is no
touch=fill maker assumption anywhere, no queue model, and no fill at a price
that was not displayed. Fees are the venue CENT charge, which is what the
account is actually debited.

DEPTH IS TOUCH-ONLY, AND THAT IS A REAL LIMIT. The reconstructed tape carries
`up_ask_qty` and `down_qty`: displayed size at the touch. It does NOT carry a
ladder, because the recorder's fold keeps only level [0]. So capacity BEYOND the
touch is unknown and is never guessed here -- a size larger than the displayed
touch is reported as capacity-limited rather than filled deeper.

UP-FIRST AND DOWN-FIRST ARE SEPARATE HYPOTHETICALS. They are reported side by
side and never summed, because both would consume the same displayed liquidity.
Phase 2 consumes depth within a single simulation.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import io
import json
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_fees as FEES                                 # noqa: E402

ONE = D(1)
ZERO = D(0)

#: A matched pair pays exactly this at settlement, whichever side wins.
PAIR_PAYOUT = ONE

#: Reported pair-cost thresholds. Fixed by the question, not selected.
PAIR_BANDS = (D("1.00"), D("0.95"), D("0.90"), D("0.85"), D("0.80"),
              D("0.70"), D("0.60"))

#: Book staleness bound REUSED from the live feed's MAX_AGE_S, which
#: src/l5_live_book_feed.py pins to "the frozen rule's own bound". Not a tuned
#: parameter: the primary tables do not apply it, and it is reported only as a
#: sensitivity so that stale quotes cannot be what produces the result.
FRESH_BOOK_S = 2.0

ENTRY_BANDS = ((D("0.00"), D("0.20")), (D("0.20"), D("0.40")),
               (D("0.40"), D("0.60")), (D("0.60"), D("0.80")),
               (D("0.80"), D("1.00")))
TIME_BANDS = ((0, 150), (150, 300), (300, 450), (450, 600), (600, 750),
              (750, 901))
#: |normalized distance to strike|. z is the frozen table's own standardized
#: distance, so these are its units.
DIST_BANDS = ((0.0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0),
              (2.0, float("inf")))
#: 1-second realized vol in PRICE units, as the state table reports it.
VOL_BANDS = ((0.0, 2.0), (2.0, 4.0), (4.0, 8.0), (8.0, float("inf")))


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def band_of(v, bands):
    for lo, hi in bands:
        if lo <= v < hi:
            return "[%s,%s)" % (lo, hi)
    return "out_of_range"


def load_windows(path):
    """Rows grouped per window, time-ascending. One row per (window, second)."""
    by = collections.defaultdict(list)
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                   # noqa: BLE001
                continue
            if r.get("slug"):
                by[r["slug"]].append(r)
    for v in by.values():
        v.sort(key=lambda r: r["t"])
    return by


def eligible(r):
    """Causally usable for a hypothetical taker BUY of either side.

    Both sides must be quoted with displayed size. No price, time or state
    condition beyond executability -- an entry filter here would be the very
    selection this study is meant to avoid.
    """
    try:
        up, dn = D(r["up_ask"]), D(r["down_px"])
        uq, dq = D(r["up_ask_qty"]), D(r["down_qty"])
    except Exception:                                           # noqa: BLE001
        return None
    if up <= 0 or dn <= 0 or uq <= 0 or dq <= 0:
        return None
    if up > D("0.99") or dn > D("0.99"):
        return None
    return up, dn, uq, dq


def unit_cost(px: D, fee_cent: D) -> D:
    return px + fee_cent


def phase1(by, fresh_only=False, min_comp_secs_remaining=None):
    """Transition map. Every eligible second is a hypothetical first leg.

    `min_comp_secs_remaining` restricts the COMPLEMENT search (never the entry)
    to complements arriving before the window's final N seconds. The only value
    used is 60, which is the venue's OWN settlement-averaging window
    (`pmus_brti.STAT_N`), not a fitted horizon: inside it the outcome is already
    being determined and the losing side's ask collapses toward $0.01, so a
    "cheap complement" there is the settlement being announced rather than the
    price oscillating.
    """
    legs = []
    for slug, rows in sorted(by.items()):
        n = len(rows)
        e = [eligible(r) for r in rows]
        up_c = [None] * n
        dn_c = [None] * n
        for i, r in enumerate(rows):
            if e[i] is None:
                continue
            if fresh_only and float(r.get("book_age_s") or 0) > FRESH_BOOK_S:
                e[i] = None
                continue
            up_c[i] = unit_cost(e[i][0], D(r["fee_up_cent"]))
            dn_c[i] = unit_cost(e[i][1], D(r["fee_down_cent"]))

        # suffix minima of the COMPLEMENT cost, with the index achieving it.
        # Computed over strictly future seconds only.
        def allowed(k):
            if min_comp_secs_remaining is None:
                return True
            return rows[k]["seconds_remaining"] > min_comp_secs_remaining

        def suffix_min(costs):
            best = [None] * n          # (cost, index)
            run = None
            for i in range(n - 1, -1, -1):
                if (costs[i] is not None and allowed(i)
                        and (run is None or costs[i] < run[0])):
                    run = (costs[i], i)
                best[i] = run
            return best
        fut_dn = suffix_min(dn_c)      # cheapest future DOWN
        fut_up = suffix_min(up_c)      # cheapest future UP

        outcome_up = rows[0].get("outcome_up")
        for i, r in enumerate(rows):
            if e[i] is None:
                continue
            up_px, dn_px, up_q, dn_q = e[i]
            for side in ("UP", "DOWN"):
                if side == "UP":
                    cost, depth = up_c[i], up_q
                    nxt = fut_dn[i + 1] if i + 1 < n else None
                    won = (outcome_up == 1)
                else:
                    cost, depth = dn_c[i], dn_q
                    nxt = fut_up[i + 1] if i + 1 < n else None
                    won = (outcome_up == 0)
                entry_px = up_px if side == "UP" else dn_px
                # P&L if the complement NEVER arrives: hold to settlement.
                naked = (ONE if won else ZERO) - cost
                rec = {
                    "slug": slug, "t": r["t"], "side": side,
                    "entry_px": str(entry_px), "entry_cost": str(cost),
                    "entry_depth": str(depth),
                    "seconds_remaining": r["seconds_remaining"],
                    "brti": r.get("brti_now"),
                    "price_to_beat": r.get("price_to_beat"),
                    "raw_distance": r.get("raw_distance"),
                    "z": r.get("z"), "sigma_1s": r.get("sigma_1s"),
                    "book_age_s": r.get("book_age_s"),
                    "outcome_up": outcome_up,
                    "first_leg_won": bool(won),
                    "naked_pnl": str(naked),
                }
                if nxt is None:
                    rec.update({"best_pair": None, "paired": False})
                else:
                    comp_cost, j = nxt
                    all_in = cost + comp_cost
                    cj = rows[j]
                    comp_depth = (cj["down_qty"] if side == "UP"
                                  else cj["up_ask_qty"])
                    # How long the sub-$1 window stays open, in seconds that
                    # are themselves executable. Two measures: total count and
                    # the longest unbroken run.
                    opp = best_run = cur = 0
                    for k in range(i + 1, n):
                        ck = dn_c[k] if side == "UP" else up_c[k]
                        if ck is not None and allowed(k) and cost + ck < ONE:
                            opp += 1
                            cur += 1
                            best_run = max(best_run, cur)
                        else:
                            cur = 0
                    rec.update({
                        "best_pair": str(all_in),
                        "paired": all_in < ONE,
                        "comp_px": str(comp_cost),
                        "comp_t": cj["t"],
                        "seconds_to_comp": cj["t"] - r["t"],
                        "comp_depth": str(comp_depth),
                        "comp_seconds_remaining": cj["seconds_remaining"],
                        "sub1_seconds_available": opp,
                        "sub1_longest_run_s": best_run,
                    })
                legs.append(rec)
    return legs


def dist_table(legs, keyfn, label):
    out = {}
    for lg in legs:
        k = keyfn(lg)
        if k is None:
            continue
        d = out.setdefault(k, {"n": 0, "bands": {str(b): 0 for b in PAIR_BANDS},
                               "no_complement": 0})
        d["n"] += 1
        bp = lg.get("best_pair")
        if bp is None:
            d["no_complement"] += 1
            continue
        v = D(bp)
        hit = False
        for b in PAIR_BANDS:
            if v < b:
                d["bands"][str(b)] += 1
                hit = True
        if not hit:
            d["no_complement"] += 1
    for k, d in out.items():
        d["pct"] = {b: round(100.0 * c / d["n"], 2)
                    for b, c in d["bands"].items()}
    return {"by": label, "rows": out}


def summarize(legs, label):
    by_side = {}
    for side in ("UP", "DOWN"):
        s = [lg for lg in legs if lg["side"] == side]
        if not s:
            continue
        bands = {}
        for b in PAIR_BANDS:
            c = sum(1 for lg in s
                    if lg.get("best_pair") is not None and D(lg["best_pair"]) < b)
            bands[str(b)] = {"n": c, "pct": round(100.0 * c / len(s), 2)}
        ok = [lg for lg in s if lg.get("paired")]
        bad = [lg for lg in s if not lg.get("paired")]
        depths = [float(lg["comp_depth"]) for lg in ok if lg.get("comp_depth")]
        secs = [lg["seconds_to_comp"] for lg in ok if "seconds_to_comp" in lg]
        runs = [lg["sub1_longest_run_s"] for lg in ok]
        avail = [lg["sub1_seconds_available"] for lg in ok]
        gains = [float(ONE - D(lg["best_pair"])) for lg in ok]
        losses = [float(lg["naked_pnl"]) for lg in bad]
        by_side[side] = {
            "n_first_legs": len(s),
            "pair_cost_below": bands,
            "n_with_sub1_complement": len(ok),
            "pct_with_sub1_complement": round(100.0 * len(ok) / len(s), 2),
            "hindsight_best_pair_gain_per_unit": {
                "mean": round(statistics.mean(gains), 5) if gains else None,
                "median": round(statistics.median(gains), 5) if gains else None,
                "p90": (round(sorted(gains)[int(0.9 * (len(gains) - 1))], 5)
                        if gains else None),
                "max": round(max(gains), 5) if gains else None},
            "seconds_to_best_complement": {
                "median": statistics.median(secs) if secs else None,
                "p10": (sorted(secs)[int(0.1 * (len(secs) - 1))]
                        if secs else None),
                "p90": (sorted(secs)[int(0.9 * (len(secs) - 1))]
                        if secs else None)},
            "complement_touch_depth": {
                "median": round(statistics.median(depths), 2) if depths else None,
                "p10": (round(sorted(depths)[int(0.1 * (len(depths) - 1))], 2)
                        if depths else None)},
            "sub1_seconds_available": {
                "median": statistics.median(avail) if avail else None,
                "p90": (sorted(avail)[int(0.9 * (len(avail) - 1))]
                        if avail else None)},
            "sub1_longest_consecutive_run_s": {
                "median": statistics.median(runs) if runs else None,
                "p90": (sorted(runs)[int(0.9 * (len(runs) - 1))]
                        if runs else None)},
            "FAILED_first_legs_no_sub1_complement": {
                "n": len(bad),
                "pct": round(100.0 * len(bad) / len(s), 2),
                "naked_pnl_mean": (round(statistics.mean(losses), 5)
                                   if losses else None),
                "naked_pnl_median": (round(statistics.median(losses), 5)
                                     if losses else None),
                "naked_pnl_p10": (round(sorted(losses)[int(0.1 * (len(losses) - 1))], 5)
                                  if losses else None),
                "naked_pnl_worst": round(min(losses), 5) if losses else None,
                "pct_of_failures_losing": (
                    round(100.0 * sum(1 for v in losses if v < 0) / len(losses), 2)
                    if losses else None)},
        }
    return {"label": label, "by_side": by_side}



def decisive_diagnostics(legs):
    """The three checks that decide whether the Phase 1 map means anything.

    A matched UP+DOWN pair pays exactly $1.00 either way, so the complement is
    cheapest precisely when it is about to be WORTHLESS -- i.e. when the first
    leg is winning. Near expiry the losing side's ask collapses toward $0.01,
    which manufactures spectacular "sub-$1 pair costs" that are available only
    to a first leg that was already going to win. Completing then converts an
    already-winning naked position into a locked one and gains nothing but the
    extra fee.

    So the raw sub-$1 frequency is not evidence of a mechanism. These are:

      1. conditional frequency -- sub-$1 complement given the first leg WON vs
         given it LOST. A mechanism that only completes cheaply on winners is
         not insurance, it is a description of the outcome;
      2. pair versus naked -- for first legs that DID get a sub-$1 complement,
         locked pair gain against simply holding the first leg. If holding is at
         least as good, pairing adds nothing;
      3. WHEN the cheap complement arrives, by the complement's own
         seconds-remaining. Mass at the very end of the window is the terminal
         collapse rather than oscillation.
    """
    out = {}
    for side in ("UP", "DOWN"):
        s_ = [lg for lg in legs if lg["side"] == side]
        if not s_:
            continue
        won = [lg for lg in s_ if lg["first_leg_won"]]
        lost = [lg for lg in s_ if not lg["first_leg_won"]]

        def pct_sub1(g):
            return (round(100.0 * sum(1 for lg in g if lg.get("paired"))
                          / len(g), 2) if g else None)
        ok = [lg for lg in s_ if lg.get("paired")]
        locked = [float(ONE - D(lg["best_pair"])) for lg in ok]
        naked = [float(D(lg["naked_pnl"])) for lg in ok]
        when = collections.Counter()
        for lg in ok:
            sr = lg.get("comp_seconds_remaining")
            when[band_of(sr, TIME_BANDS)] += 1
        out[side] = {
            "1_conditional_sub1_frequency": {
                "first_leg_WON": {"n": len(won), "pct_sub1": pct_sub1(won)},
                "first_leg_LOST": {"n": len(lost), "pct_sub1": pct_sub1(lost)},
                "reading": ("a mechanism needs the complement to be reachable "
                            "when the first leg is LOSING; that is the column "
                            "that matters")},
            "2_pair_versus_naked_on_the_same_first_legs": {
                "n": len(ok),
                "mean_locked_pair_gain": (round(statistics.mean(locked), 5)
                                          if locked else None),
                "mean_naked_hold_pnl": (round(statistics.mean(naked), 5)
                                        if naked else None),
                "mean_advantage_of_pairing": (
                    round(statistics.mean(locked) - statistics.mean(naked), 5)
                    if locked else None),
                "pct_where_pairing_beats_holding": (
                    round(100.0 * sum(1 for a, b in zip(locked, naked) if a > b)
                          / len(ok), 2) if ok else None)},
            "3_when_the_cheap_complement_arrives": {
                "by_complement_seconds_remaining": dict(sorted(when.items())),
                "reading": ("mass in the lowest band is terminal collapse of "
                            "the losing side, not oscillation")},
        }
    return out


# ----------------------------------------------------------------- phase 2
def phase2(by, first_side):
    """Causal sequential mechanics. NO tuned parameter.

    One deterministic mechanism, run per window: enter at the FIRST eligible
    second (no selection), then at every later second complete the open lot if
    the all-in pair cost against THAT LOT'S cost is below $1.00, then allow the
    cycle to repeat -- an EXTEND. The only condition is the structural lock test
    `all_in < 1.00`; there is no threshold to sweep, no probability signal and
    no timing rule.

    Lots are paired FIFO and a matched tranche is NEVER re-paired. Locked
    economics stay locked; later purchases create new residual inventory.
    Depth is consumed, so the same displayed size is not spent twice.
    """
    per_window = []
    for slug, rows in sorted(by.items()):
        n = len(rows)
        outcome_up = rows[0].get("outcome_up")
        open_lots = []          # [{"side","cost","qty"}] unmatched
        tranches = []           # matched pairs, locked
        cash = ZERO             # cumulative cash out (cost + fees)
        peak = ZERO
        entered = False
        want = first_side
        for i, r in enumerate(rows):
            e = eligible(r)
            if e is None:
                continue
            up_px, dn_px, up_q, dn_q = e
            up_c = unit_cost(up_px, D(r["fee_up_cent"]))
            dn_c = unit_cost(dn_px, D(r["fee_down_cent"]))
            if want == "CHEAPER":
                side_now = "UP" if up_c <= dn_c else "DOWN"
            else:
                side_now = want
            if not entered:
                q = min(ONE, up_q if side_now == "UP" else dn_q)
                if q <= 0:
                    continue
                c = (up_c if side_now == "UP" else dn_c) * q
                open_lots.append({"side": side_now, "cost": c, "qty": q,
                                  "t": r["t"]})
                cash += c
                peak = max(peak, cash)
                entered = True
                continue
            # complete the oldest open lot if the pair locks below payout
            if open_lots:
                lot = open_lots[0]
                opp = "DOWN" if lot["side"] == "UP" else "UP"
                opp_c = dn_c if opp == "DOWN" else up_c
                opp_q = dn_q if opp == "DOWN" else up_q
                q = min(lot["qty"], opp_q)
                if q > 0:
                    unit_lot = lot["cost"] / lot["qty"]
                    all_in = unit_lot + opp_c
                    if all_in < PAIR_PAYOUT:
                        spend = opp_c * q
                        cash += spend
                        peak = max(peak, cash)
                        tranches.append({
                            "qty": str(q), "lot_side": lot["side"],
                            "unit_cost_first": str(unit_lot),
                            "unit_cost_second": str(opp_c),
                            "all_in": str(all_in),
                            "locked_net": str((PAIR_PAYOUT - all_in) * q),
                            "t_first": lot["t"], "t_second": r["t"]})
                        lot["qty"] -= q
                        lot["cost"] -= unit_lot * q
                        if lot["qty"] <= 0:
                            open_lots.pop(0)
                        # an EXTEND: start a new lot on the next eligible second
                        entered = False
                        continue
        matched_qty = sum(D(x["qty"]) for x in tranches)
        locked = sum(D(x["locked_net"]) for x in tranches)
        resid_pnl = ZERO
        resid_qty = ZERO
        for lot in open_lots:
            won = (outcome_up == 1) if lot["side"] == "UP" else (outcome_up == 0)
            resid_pnl += (lot["qty"] if won else ZERO) - lot["cost"]
            resid_qty += lot["qty"]
        per_window.append({
            "slug": slug, "outcome_up": outcome_up,
            # ADDITIVE reporting only: the mechanics above are untouched. The
            # settlement-lock diagnostic needs to know WHICH lots were left
            # unmatched and at what basis, not merely their total.
            "residual_lots": [
                {"side": lot["side"], "qty": str(lot["qty"]),
                 "cost": str(lot["cost"]),
                 "unit_cost": str(lot["cost"] / lot["qty"]),
                 "t": lot["t"]} for lot in open_lots],
            "matched_qty": str(matched_qty),
            "n_tranches": len(tranches),
            "locked_net": str(locked),
            "residual_qty": str(resid_qty),
            "residual_pnl": str(resid_pnl),
            "net": str(locked + resid_pnl),
            "capital_deployed": str(cash),
            "peak_capital": str(peak),
            "tranches": tranches})
    nets = [float(D(w["net"])) for w in per_window]
    locks = [float(D(w["locked_net"])) for w in per_window]
    res = [float(D(w["residual_pnl"])) for w in per_window]
    peaks = [float(D(w["peak_capital"])) for w in per_window]
    tr = [w["n_tranches"] for w in per_window]
    return {
        "first_side": first_side,
        "windows": len(per_window),
        "windows_with_a_matched_tranche": sum(1 for w in per_window
                                              if w["n_tranches"] > 0),
        "total_matched_qty": str(sum(D(w["matched_qty"]) for w in per_window)),
        "tranches_per_window": {
            "mean": round(statistics.mean(tr), 3) if tr else None,
            "median": statistics.median(tr) if tr else None,
            "max": max(tr) if tr else None},
        "locked_net_total": round(sum(locks), 4),
        "residual_pnl_total": round(sum(res), 4),
        "TOTAL_NET": round(sum(nets), 4),
        "net_per_window": {
            "mean": round(statistics.mean(nets), 5) if nets else None,
            "median": round(statistics.median(nets), 5) if nets else None,
            "pct_positive": (round(100.0 * sum(1 for v in nets if v > 0)
                                   / len(nets), 2) if nets else None),
            "worst": round(min(nets), 5) if nets else None,
            "best": round(max(nets), 5) if nets else None},
        "peak_capital_per_window": {
            "mean": round(statistics.mean(peaks), 4) if peaks else None,
            "max": round(max(peaks), 4) if peaks else None},
        "per_window": per_window,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--label", default="OOS")
    ap.add_argument("--out", default="analysis/l5/path_economics")
    a = ap.parse_args()
    by = load_windows(a.table)
    legs = phase1(by)
    fresh = phase1(by, fresh_only=True)

    out = {
        "sample": a.label,
        "table": a.table,
        "windows": len(by),
        "rows": sum(len(v) for v in by.values()),
        "eligible_seconds": len(legs) // 2,
        "NOTE_not_a_strategy": (
            "phase1 best_pair is the minimum over the REMAINDER of the window, "
            "chosen with hindsight. It is an opportunity upper bound. Phase 2 "
            "is the causal number."),
        "NOTE_separate_hypotheticals": (
            "UP-first and DOWN-first are independent hypotheticals over the "
            "same displayed liquidity and are never summed."),
        "NOTE_depth": "touch depth only; the tape carries no ladder.",
        "phase1": summarize(legs, "all eligible seconds"),
        "DECISIVE_diagnostics": decisive_diagnostics(legs),
        "phase1_excluding_final_60s": summarize(
            phase1(by, min_comp_secs_remaining=60),
            "complement must arrive before the venue settlement window"),
        "phase1_fresh_book_sensitivity": summarize(
            fresh, "book_age_s <= %.1fs" % FRESH_BOOK_S),
        "phase1_by_entry_price": dist_table(
            legs, lambda lg: "%s %s" % (lg["side"],
                                        band_of(D(lg["entry_px"]), ENTRY_BANDS)),
            "side + entry price"),
        "phase1_by_time_remaining": dist_table(
            legs, lambda lg: "%s %s" % (lg["side"],
                                        band_of(lg["seconds_remaining"], TIME_BANDS)),
            "side + seconds remaining"),
        "phase1_by_distance": dist_table(
            legs, lambda lg: ("%s %s" % (lg["side"],
                                         band_of(abs(float(lg["z"])), DIST_BANDS))
                              if lg.get("z") is not None else None),
            "side + |z| distance to strike"),
        "phase1_by_vol": dist_table(
            legs, lambda lg: ("%s %s" % (lg["side"],
                                         band_of(float(lg["sigma_1s"]), VOL_BANDS))
                              if lg.get("sigma_1s") is not None else None),
            "side + 1s realized vol"),
        "phase2_up_first": phase2(by, "UP"),
        "phase2_down_first": phase2(by, "DOWN"),
        "phase2_cheaper_side_first": phase2(by, "CHEAPER"),
    }
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "path_economics_%s.json" % a.label.lower())
    with open(p, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    slim = {k: v for k, v in out.items() if k != "phase2_up_first"}
    for k in ("phase2_up_first", "phase2_down_first",
              "phase2_cheaper_side_first"):
        slim[k] = {kk: vv for kk, vv in out[k].items() if kk != "per_window"}
    print(json.dumps(slim, indent=1, default=str))
    print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
