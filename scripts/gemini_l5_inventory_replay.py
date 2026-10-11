"""LANE 2 -- dynamic L5-style inventory replay on Gemini BTC5m.

RESEARCH ONLY. No orders, no venue writes, no credentials. Every fill in
this file is a MODELLED fill against a captured executable ladder and is
never realized profit.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
It is NOT another fixed first-entry price threshold. The earlier Gemini
studies all reduce to "cross when the cheap side is at or below X", and
that is not what L5 does. Here the action family is a STATE MACHINE over
inventory:

    FLAT -> ACQUIRE -> (EXTEND same side) -> (SWITCH side)
         -> COMPRESS matched cost -> manage residual -> settle

and -- the point of the lane -- pair cost is NOT required to be below $1
immediately after leg 1. L5 routinely holds a pair cost above $1 and
compresses it with later fills; its own tape says so (median FINAL matched
pair cost 0.977, p90 1.256, n=223 completed events). So the economics here
are judged at the END of the window, never after leg 1.

WHERE THE TRIGGERS COME FROM
----------------------------
From L5's own corpus (`data/l5/raw/trades.jsonl`, fills filtered to
name == 'l5Zn1bWoM8eTsK', deduped on (transactionHash, asset, timestamp)),
NOT from Gemini outcomes. `--derive` recomputes them and the numbers
reproduce the established facts, which is the check that the derivation is
reading the right thing:

    reproduced here            established fact
    312 events, 9901 fills     --
    first-fill price med 0.583 0.588
    68.3% favored first        69.4%
    first fill T+44s           T+44s
    71.5% ever complement      71.5%
    52s to 1st matched incr.   52s
    median 1 same-side EXTEND  1

Two derivations matter beyond those:

  * COMPRESS trigger. L5's pair cost AT the moment it buys the opposite
    side is p10 0.743 / median 0.991 / p90 1.237. The median, 0.991, is
    used as the compress trigger. It is L5's own number, not a sweep. The
    spring-discipline 0.96 target is run as a SECOND ARM, not substituted.

  * EXTEND is NOT price-improvement-gated, and this study says so rather
    than inventing a gate. Across 9369 same-side repeats L5 paid a LOWER
    price 3780 times, a HIGHER price 3366 times, the same 2220. There is
    no "only extend on improvement" rule in the tape. EXTEND is therefore
    gated on band membership and an extend budget only.

TIME SCALING -- STATED, AS REQUIRED
-----------------------------------
L5's window is Polymarket's 900s; Gemini BTC5m is 300s. Every L5 timing is
scaled by 300/900 = 1/3:

    first acquisition T+44s   -> T+14.7s   (we use T+15, which also
                                            coincides with the frozen
                                            first-leg eligibility floor)
    52s to first matched incr -> T+17.3s after leg 1 (a patience
                                            reference, not a hard wait)
    median inter-fill gap 2s  -> 0.67s -> one 1 Hz tick

CAUSALITY RULES, ENFORCED IN CODE
---------------------------------
  * event-by-event forward walk on the 1 Hz p_snapshot series;
  * every decision reads only rows with t <= decision time;
  * REACTION_LATENCY_S = 0.197 between decision and execution -- the fill
    is priced at the FIRST observation at or after decision+latency, at
    THAT observation's captured ladder;
  * no touch=fill: a fill needs `vwap()` to clear the requested size out of
    cumulative captured depth, and we pay that VWAP;
  * no per-window path selection, no best-path-per-window, no hindsight.

FEES
----
`fee()` from the forensic: taker 0.07*q*p*(1-p), cent-rounded. Maker is
0.0175*q*p*(1-p) and is NOT used, because this replay models only
marketable BUYs. The buy-side fee is the reconciled one.

SELLS
-----
NONE. L5's corpus is 9901 fills and 9901 of them are BUY -- it never sells.
So "reduce directional exposure near settlement" cannot be reproduced by
selling, and the sell-side fee is unreconciled anyway. Residual is carried
to settlement and reported as residual. No result here depends on a SELL.

    python3 scripts/gemini_l5_inventory_replay.py --derive
    python3 scripts/gemini_l5_inventory_replay.py
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "scripts")
from gemini_complement_forensic import (  # noqa: E402
    REACTION_LATENCY_S, fee, load)

LABEL = "GEMINI_L5_INVENTORY_REPLAY_V1"

TAPES = (
    "analysis/gemini/btc5m_rec/btc5m_rec_20261005T14*.jsonl.gz",
    "analysis/gemini/btc5m_snap/btc5m_rec_20261005T19*.jsonl.gz",
    "analysis/gemini/btc5m_snap/btc5m_rec_20261005T17*.jsonl.gz",
)
#: sealed OOS -- outcomes never read
SEALED = ("BTC05M2610051845", "BTC05M2610051850", "BTC05M2610051855",
          "BTC05M2610051900", "BTC05M2610051905", "BTC05M2610051910")
#: live-probe contaminated
PROBE = ("BTC05M2610051550", "BTC05M2610051555", "BTC05M2610051600",
         "BTC05M2610051605", "BTC05M2610051610", "BTC05M2610051730",
         "BTC05M2610051755", "BTC05M2610051800", "BTC05M2610051805",
         "BTC05M2610051810")
EXCLUDE = set(SEALED) | set(PROBE)

L5_WINDOW_S = 900.0
GEM_WINDOW_S = 300.0
SCALE = GEM_WINDOW_S / L5_WINDOW_S

# ---- L5-derived constants (see --derive; recomputed from the corpus) ----
#: L5 first-fill price p10/p90 -> the acquisition band
L5_BAND = (D("0.20"), D("0.82"))
#: L5 median pair cost at the instant it buys the opposite side
L5_COMPRESS_TRIGGER = D("0.991")
#: spring-discipline arm, kept separate, never substituted
SPRING_COMPRESS_TRIGGER = D("0.96")
#: L5 median same-side EXTENDs before complementing
L5_EXTEND_BUDGET = 1
#: L5 first acquisition T+44s on 900s -> scaled
ACQUIRE_FLOOR_S = 44.0 * SCALE
#: L5 52s to first matched increase -> scaled; a reference, not a wait
PATIENCE_REF_S = 52.0 * SCALE
#: L5 median inter-fill gap 2s -> scaled -> one 1 Hz tick
MIN_GAP_S = max(1.0, 2.0 * SCALE)


# --------------------------------------------------------------- derivation
def derive(path: str = "data/l5/raw/trades.jsonl") -> Dict[str, Any]:
    """Recompute the action family's triggers from L5's corpus alone."""
    seen, fills = set(), []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            for x in (rec.get("payload") or []):
                if not isinstance(x, dict):
                    continue
                if x.get("name") != "l5Zn1bWoM8eTsK":
                    continue
                k = (x.get("transactionHash"), x.get("asset"),
                     x.get("timestamp"))
                if k in seen:
                    continue
                seen.add(k)
                fills.append(x)
    ev: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for f in fills:
        ev[f.get("eventSlug") or ""].append(f)

    first_px, first_tplus, favored, ever_comp = [], [], 0, 0
    extend_runs, pc_switch, pc_extend, pc_final, to_matched = [], [], [], [], []
    gaps, repeat = [], collections.Counter()
    trans: collections.Counter = collections.Counter()
    n_ev = 0
    for slug, fs in ev.items():
        tail = slug.rsplit("-", 1)[-1]
        if not tail.isdigit():
            continue
        t_open = int(tail)
        fs = sorted(fs, key=lambda x: x["timestamp"])
        n_ev += 1
        first_px.append(float(fs[0]["price"]))
        first_tplus.append(float(fs[0]["timestamp"] - t_open))
        if float(fs[0]["price"]) > 0.5:
            favored += 1
        inv = {"Up": 0.0, "Down": 0.0}
        cost = {"Up": 0.0, "Down": 0.0}
        prev, run, last_px, fc = None, 0, {}, None
        for i, f in enumerate(fs):
            o, p, q = f["outcome"], float(f["price"]), float(f["size"])
            if i:
                gaps.append(float(f["timestamp"] - fs[i - 1]["timestamp"]))
            if prev is None:
                trans["OPEN"] += 1
            elif o == prev:
                trans["EXTEND"] += 1
                run += 1
                if inv[o] > 0:
                    pc_extend.append(cost[o] / inv[o] + p)
            else:
                trans["SWITCH_OR_COMPLETE"] += 1
                if inv[prev] > 0:
                    pc_switch.append(cost[prev] / inv[prev] + p)
                extend_runs.append(float(run))
                run = 0
            if o in last_px:
                repeat["lower" if p < last_px[o] else
                       "higher" if p > last_px[o] else "same"] += 1
            last_px[o] = p
            pm = min(inv["Up"], inv["Down"])
            inv[o] += q
            cost[o] += p * q
            if min(inv["Up"], inv["Down"]) > pm and fc is None:
                fc = float(f["timestamp"] - fs[0]["timestamp"])
            prev = o
        if fc is not None:
            to_matched.append(fc)
        if inv["Up"] > 0 and inv["Down"] > 0:
            ever_comp += 1
            pc_final.append(cost["Up"] / inv["Up"] + cost["Down"] / inv["Down"])

    def dd(xs: List[float]) -> Optional[Dict[str, Any]]:
        if not xs:
            return None
        s = sorted(xs)
        g = lambda p: round(s[min(len(s) - 1, int(p * (len(s) - 1)))], 4)
        return {"n": len(s), "p10": g(.1), "median": round(
            statistics.median(s), 4), "p90": g(.9)}

    return {
        "source": path,
        "fills": len(fills), "events": n_ev,
        "first_fill_price": dd(first_px),
        "first_fill_t_plus_s": dd(first_tplus),
        "favored_first_pct": round(100.0 * favored / n_ev, 2) if n_ev else None,
        "ever_complement_pct": (round(100.0 * ever_comp / n_ev, 2)
                                if n_ev else None),
        "transitions": dict(trans),
        "same_side_extends_before_complement": dd(extend_runs),
        "pair_cost_at_opposite_side_buy": dd(pc_switch),
        "pair_cost_if_extend_same_side": dd(pc_extend),
        "FINAL_matched_pair_cost": dd(pc_final),
        "seconds_first_fill_to_first_matched_increase": dd(to_matched),
        "inter_fill_gap_s": dd(gaps),
        "same_side_repeat_price_direction": dict(repeat),
        "extend_is_price_improvement_gated": False,
        "extend_gate_note": (
            "L5 paid a LOWER price on %d same-side repeats and a HIGHER "
            "price on %d. There is no improvement-only extend rule in the "
            "tape, so none is invented here." % (repeat["lower"],
                                                 repeat["higher"])),
        "sides_observed": "BUY only (L5 never sells in this corpus)",
    }


# ------------------------------------------------------------------ replay
def mark(series: List[Dict[str, Any]], i: int, side: str, q: int
         ) -> Optional[D]:
    return series[i].get("%s_q%d" % (side, q))


def exec_index(series: List[Dict[str, Any]], i: int) -> Optional[int]:
    """First observation at or after decision time + reaction latency."""
    t_go = series[i]["t"] + REACTION_LATENCY_S
    for j in range(i, len(series)):
        if series[j]["t"] >= t_go:
            return j
    return None


def replay(market: str, series: List[Dict[str, Any]], t_open: float,
           q: int, compress_trigger: D,
           extend_budget: int = L5_EXTEND_BUDGET) -> Dict[str, Any]:
    """Forward state-machine replay. Contemporaneous information only."""
    inv = {"UP": 0, "DOWN": 0}
    cost = {"UP": D("0"), "DOWN": D("0")}
    fees = {"UP": D("0"), "DOWN": D("0")}
    actions: List[Dict[str, Any]] = []
    state = "FLAT"
    extends = 0
    last_fill_t = -1e18
    pc_after_leg1: Optional[D] = None
    pc_went_above_1 = False

    def avg(s: str) -> Optional[D]:
        return (cost[s] / D(inv[s])) if inv[s] else None

    def basis(s: str) -> Optional[D]:
        """All-in per-share cost of side s, fees included."""
        if not inv[s]:
            return None
        return (cost[s] + fees[s]) / D(inv[s])

    i = 0
    while i < len(series):
        row = series[i]
        t_rel = row["t"] - t_open
        if t_rel < ACQUIRE_FLOOR_S or row["t"] - last_fill_t < MIN_GAP_S:
            i += 1
            continue
        up, dn = mark(series, i, "UP", q), mark(series, i, "DOWN", q)
        if up is None or dn is None or up == dn:
            i += 1
            continue

        want: Optional[Tuple[str, str]] = None   # (side, transition)
        if state == "FLAT":
            # L5: FAVORED side (the dearer executable side), inside the band
            side = "UP" if up > dn else "DOWN"
            px = up if side == "UP" else dn
            if L5_BAND[0] <= px <= L5_BAND[1]:
                want = (side, "ACQUIRE")
        else:
            long_s = "UP" if inv["UP"] > inv["DOWN"] else (
                "DOWN" if inv["DOWN"] > inv["UP"] else None)
            if long_s is None:
                # matched. L5 keeps working inventory: re-ACQUIRE favored.
                side = "UP" if up > dn else "DOWN"
                px = up if side == "UP" else dn
                if L5_BAND[0] <= px <= L5_BAND[1]:
                    want = (side, "ACQUIRE")
                    extends = 0
            else:
                other = "DOWN" if long_s == "UP" else "UP"
                c = up if other == "UP" else dn
                b = basis(long_s)
                # COMPRESS: buying the opposite side now would put the
                # all-in pair cost at or under L5's own trigger.
                pc = (b + c + fee(c, D(q)) / D(q)) if b is not None else None
                if pc is not None and pc <= compress_trigger:
                    want = (other, "COMPRESS")
                elif extends < extend_budget:
                    px = up if long_s == "UP" else dn
                    if L5_BAND[0] <= px <= L5_BAND[1]:
                        want = (long_s, "EXTEND")
        if want is None:
            i += 1
            continue

        side, kind = want
        j = exec_index(series, i)
        if j is None:
            break
        px = mark(series, j, side, q)
        if px is None:                       # depth gone after latency
            i = j + 1
            continue
        f = fee(px, D(q))
        inv[side] += q
        cost[side] += px * D(q)
        fees[side] += f
        last_fill_t = series[j]["t"]
        if kind == "EXTEND":
            extends += 1
        if kind == "COMPRESS":
            extends = 0
        state = "WORKING"
        b_up, b_dn = basis("UP"), basis("DOWN")
        pc_now = ((b_up + b_dn) if (b_up is not None and b_dn is not None)
                  else None)
        if len(actions) == 0:
            # pair cost after leg 1 = leg-1 all-in + the contemporaneous
            # executable complement, i.e. what completing right then would
            # have cost. This is the quantity L5 is allowed to leave above
            # $1 and compress later.
            o = "DOWN" if side == "UP" else "UP"
            oc = mark(series, j, o, q)
            if oc is not None:
                pc_after_leg1 = (px + f / D(q)) + oc + fee(oc, D(q)) / D(q)
                if pc_after_leg1 > D("1"):
                    pc_went_above_1 = True
        if pc_now is not None and pc_now > D("1"):
            pc_went_above_1 = True
        actions.append({
            "t": round(series[j]["t"], 3),
            "t_plus_s": round(series[j]["t"] - t_open, 3),
            "transition": kind, "side": side, "size": q,
            "price": str(px), "fee": str(f),
            "inventory_after": {"UP": inv["UP"], "DOWN": inv["DOWN"]},
            "pair_cost_after": (str(pc_now.quantize(D("0.0001")))
                                if pc_now is not None else None),
        })
        i = j + 1

    matched = min(inv["UP"], inv["DOWN"])
    b_up, b_dn = basis("UP"), basis("DOWN")
    # (a) matched-inventory economics -- fee-inclusive, settlement-neutral
    matched_pnl = D("0")
    final_pair_cost = None
    if matched:
        final_pair_cost = b_up + b_dn
        matched_pnl = (D("1") - final_pair_cost) * D(matched)
    # (b) directional residual -- kept strictly separate
    surplus_side = ("UP" if inv["UP"] > inv["DOWN"] else
                    "DOWN" if inv["DOWN"] > inv["UP"] else None)
    residual_q = abs(inv["UP"] - inv["DOWN"])
    return {
        "market": market, "size": q,
        "actions": actions,
        "n_actions": len(actions),
        "ended": ("FLAT_NO_ACTION" if not actions else
                  "MATCHED" if surplus_side is None else "RESIDUAL"),
        "inventory_final": dict(inv),
        "matched_qty": matched,
        "final_matched_pair_cost": (str(final_pair_cost)
                                    if final_pair_cost is not None else None),
        "matched_pnl": str(matched_pnl),
        "pair_cost_after_leg1": (str(pc_after_leg1)
                                 if pc_after_leg1 is not None else None),
        "pair_cost_exceeded_1_at_some_point": pc_went_above_1,
        "compressed_below_1": (final_pair_cost is not None
                               and final_pair_cost < D("1")),
        "residual_side": surplus_side, "residual_qty": residual_q,
        "residual_basis": (str(basis(surplus_side))
                           if surplus_side else None),
        "fees_total": str(fees["UP"] + fees["DOWN"]),
        # which transitions bought the shares that ended up UNMATCHED
        "unmatched_shares_by_transition": _unmatched_by_transition(
            actions, surplus_side, residual_q),
    }


def _unmatched_by_transition(actions: List[Dict[str, Any]],
                             surplus_side: Optional[str], residual_q: int
                             ) -> Dict[str, int]:
    """Attribute the residual to the transitions that created it.

    LIFO on the surplus side: the LAST fills on that side are the ones left
    unmatched, because every earlier one was paired by a COMPRESS.
    """
    if not surplus_side or not residual_q:
        return {}
    out: collections.Counter = collections.Counter()
    need = residual_q
    for x in reversed(actions):
        if x["side"] != surplus_side:
            continue
        take = min(need, int(x["size"]))
        out[x["transition"]] += take
        need -= take
        if need <= 0:
            break
    return dict(out)


def settle_residual(rep: Dict[str, Any], settle_up: Optional[str]) -> None:
    """Attach residual settlement P&L. Held to settlement -- no SELL."""
    if not rep["residual_qty"] or settle_up not in ("yes", "no"):
        rep["residual_pnl"] = "0" if not rep["residual_qty"] else None
        rep["net_pnl"] = (str(D(rep["matched_pnl"]))
                          if not rep["residual_qty"] else None)
        return
    s = rep["residual_side"]
    won = (s == "UP" and settle_up == "yes") or (s == "DOWN"
                                                 and settle_up == "no")
    b = D(rep["residual_basis"])
    r = ((D("1") if won else D("0")) - b) * D(rep["residual_qty"])
    rep["residual_pnl"] = str(r)
    rep["residual_won"] = won
    rep["net_pnl"] = str(D(rep["matched_pnl"]) + r)


def boot(vals: List[float], n: int = 20000, seed: int = 20261005
         ) -> Dict[str, Any]:
    """WINDOW-LEVEL bootstrap: the window is the independent unit."""
    if not vals:
        return {"n": 0}
    rnd = random.Random(seed)
    k = len(vals)
    means = sorted(statistics.fmean(rnd.choices(vals, k=k)) for _ in range(n))
    lo = means[int(0.025 * (n - 1))]
    hi = means[int(0.975 * (n - 1))]
    return {"independent_windows": k, "mean": round(statistics.fmean(vals), 4),
            "median": round(statistics.median(vals), 4),
            "ci95_lo": round(lo, 4), "ci95_hi": round(hi, 4),
            "resamples": n, "unit": "$ per window"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--derive", action="store_true")
    ap.add_argument("--size", type=int, default=1)
    ap.add_argument("--out", default=("analysis/gemini/updown/"
                                      "L5_INVENTORY_REPLAY.json"))
    a = ap.parse_args(argv)

    corpus = derive()
    if a.derive:
        print(json.dumps(corpus, indent=2, default=str))
        return 0

    ser: Dict[str, Any] = {}
    t0: Dict[str, float] = {}
    exp: Dict[str, float] = {}
    st: Dict[str, str] = {}
    for pat in TAPES:
        s1, a1, _k1, e1, z1 = load(pat)
        for m, v in s1.items():
            if m not in ser or len(v) > len(ser[m]):
                ser[m] = v
        t0.update(a1)
        exp.update(e1)
        st.update(z1)

    usable: List[Tuple[str, Any, str]] = []
    skipped: collections.Counter = collections.Counter()
    for m, s in sorted(ser.items()):
        tk = m[5:-3]
        if tk in EXCLUDE:
            skipped["excluded_by_name"] += 1
            continue
        if m not in t0:
            skipped["no_window_open"] += 1
            continue
        if len(s) < 30:
            skipped["series_too_short"] += 1
            continue
        if st.get(tk) not in ("yes", "no"):
            skipped["no_settlement"] += 1
            continue
        usable.append((m, s, st[tk]))

    arms: Dict[str, Any] = {}
    ARMS = (("L5_DERIVED_0.991", L5_COMPRESS_TRIGGER, L5_EXTEND_BUDGET),
            ("SPRING_0.96", SPRING_COMPRESS_TRIGGER, L5_EXTEND_BUDGET),
            # ABLATION, not a competing candidate: the same machine with
            # the EXTEND transition removed, to localize where the money
            # goes. Reported as an ablation, never selected on its result.
            ("ABLATION_NO_EXTEND", L5_COMPRESS_TRIGGER, 0))
    for arm, trig, budget in ARMS:
        per = []
        for m, s, z in usable:
            r = replay(m, s, t0[m], a.size, trig, budget)
            settle_residual(r, z)
            per.append(r)
        acted = [r for r in per if r["n_actions"]]
        nets = [float(r["net_pnl"]) for r in per if r["net_pnl"] is not None]
        mat = [float(r["matched_pnl"]) for r in per]
        res = [float(r["residual_pnl"]) for r in per
               if r.get("residual_pnl") is not None]
        leg1_above = [r for r in per
                      if r["pair_cost_after_leg1"] is not None
                      and D(r["pair_cost_after_leg1"]) > D("1")]
        leg1_below = [r for r in per
                      if r["pair_cost_after_leg1"] is not None
                      and D(r["pair_cost_after_leg1"]) <= D("1")]
        ever_above = [r for r in per if r["pair_cost_exceeded_1_at_some_point"]]
        rsd = [r for r in per if r["residual_qty"]]
        unm: collections.Counter = collections.Counter()
        for r in per:
            unm.update(r["unmatched_shares_by_transition"])
        tr: collections.Counter = collections.Counter()
        trpnl: Dict[str, List[float]] = collections.defaultdict(list)
        for r in per:
            for x in r["actions"]:
                tr[x["transition"]] += 1
            # attribute window net to its terminal structure
            trpnl[r["ended"]].append(float(r["net_pnl"] or 0))
        arms[arm] = {
            "compress_trigger": str(trig),
            "extend_budget": budget,
            "is_ablation": arm.startswith("ABLATION"),
            "usable_independent_windows": len(usable),
            "windows_with_at_least_one_action": len(acted),
            "windows_ended_FLAT_no_action": sum(
                1 for r in per if r["ended"] == "FLAT_NO_ACTION"),
            "windows_ended_MATCHED_flat_inventory": sum(
                1 for r in per if r["ended"] == "MATCHED"),
            "windows_ended_holding_RESIDUAL": sum(
                1 for r in per if r["ended"] == "RESIDUAL"),
            "transition_counts": dict(tr),
            "THE_TOLERANCE_TEST_pair_cost_after_leg1": {
                "note": ("the lane's premise: leg 1 is NOT required to "
                         "leave pair cost below $1. This is what happened "
                         "to the ones that started above it."),
                "windows_above_1_after_leg1": len(leg1_above),
                "of_which_later_compressed_below_1": sum(
                    1 for r in leg1_above if r["compressed_below_1"]),
                "of_which_matched_but_still_at_or_above_1": sum(
                    1 for r in leg1_above
                    if r["matched_qty"] and not r["compressed_below_1"]),
                "of_which_never_matched_at_all": sum(
                    1 for r in leg1_above if not r["matched_qty"]),
                "windows_at_or_below_1_after_leg1": len(leg1_below),
                "net_mean_if_above_1_after_leg1": (round(statistics.fmean(
                    float(r["net_pnl"]) for r in leg1_above), 4)
                    if leg1_above else None),
                "net_mean_if_below_1_after_leg1": (round(statistics.fmean(
                    float(r["net_pnl"]) for r in leg1_below), 4)
                    if leg1_below else None),
            },
            "pair_cost_exceeded_1_at_any_point": {
                "windows": len(ever_above),
                "later_compressed_below_1": sum(
                    1 for r in ever_above if r["compressed_below_1"]),
                "never_compressed": sum(
                    1 for r in ever_above if not r["compressed_below_1"]),
            },
            "RESIDUAL_CALIBRATION_DIAGNOSTIC": {
                "note": ("descriptive only. Acting on this by inverting the "
                         "ACQUIRE side would be fitting a side choice to "
                         "these very outcomes, so it is NOT done here."),
                "windows_holding_residual": len(rsd),
                "residual_settled_in_our_favour": sum(
                    1 for r in rsd if r.get("residual_won")),
                "residual_win_rate_pct": (round(100.0 * sum(
                    1 for r in rsd if r.get("residual_won")) / len(rsd), 2)
                    if rsd else None),
                "median_residual_basis": (round(statistics.median(
                    float(r["residual_basis"]) for r in rsd), 4)
                    if rsd else None),
                "implied_vs_realized": (
                    "the residual is the FAVORED side; its basis is what "
                    "the book said it was worth, and the win rate is what "
                    "it was actually worth"),
            },
            "unmatched_shares_by_transition": dict(unm),
            "MODELLED_net_per_window": boot(nets),
            "MODELLED_matched_inventory_economics_per_window": boot(mat),
            "MODELLED_directional_residual_per_window": boot(res),
            "net_by_terminal_state": {
                k: {"windows": len(v),
                    "total": round(sum(v), 4),
                    "mean": round(statistics.fmean(v), 4)}
                for k, v in sorted(trpnl.items()) if v},
            "total_fees": round(sum(float(r["fees_total"]) for r in per), 4),
            "per_window": per,
        }

    rep = {
        "label": LABEL,
        "status": ("RESEARCH ONLY. Every fill is MODELLED against a "
                   "captured executable ladder. NOTHING here is realized "
                   "profit. No orders were placed."),
        "sells_modelled": 0,
        "sell_note": ("ZERO sells. L5's corpus is 9901 fills, all BUY, so "
                      "end-of-window exposure reduction cannot be "
                      "reproduced by selling; the sell-side fee is "
                      "unreconciled in any case. Residual is carried to "
                      "settlement. No result here depends on a SELL."),
        "tapes": list(TAPES),
        "excluded_sealed_oos": list(SEALED),
        "excluded_probe_contaminated": list(PROBE),
        "exclusion_note": ("sealed OOS outcomes are never read -- those "
                           "windows are dropped before any computation"),
        "skipped_window_reasons": dict(skipped),
        "reaction_latency_s": REACTION_LATENCY_S,
        "fill_model": ("marketable BUY only; vwap() must clear the full "
                       "requested size out of cumulative captured depth at "
                       "the first observation at or after decision + "
                       "0.197s, and that VWAP is the price paid. No "
                       "touch=fill."),
        "size": a.size,
        "time_scaling": {
            "l5_window_s": L5_WINDOW_S, "gemini_window_s": GEM_WINDOW_S,
            "scale": round(SCALE, 4),
            "acquire_floor_s": round(ACQUIRE_FLOOR_S, 3),
            "patience_reference_s": round(PATIENCE_REF_S, 3),
            "min_gap_s": MIN_GAP_S,
            "note": ("L5 timings scaled by 300/900 = 1/3 and stated as "
                     "required: T+44s -> T+14.7s, 52s -> 17.3s, 2s -> "
                     "one 1 Hz tick."),
        },
        "action_family": {
            "states": ["FLAT", "ACQUIRE", "EXTEND", "SWITCH", "COMPRESS",
                       "RESIDUAL", "SETTLE"],
            "ACQUIRE": ("favored (dearer) executable side, price in L5's "
                        "own first-fill band [%s, %s], at or after T+%.1fs"
                        % (L5_BAND[0], L5_BAND[1], ACQUIRE_FLOOR_S)),
            "EXTEND": ("same side again, band-gated, budget %d = L5's "
                       "median same-side extends before complementing. NOT "
                       "price-improvement gated -- L5's tape shows no such "
                       "rule." % L5_EXTEND_BUDGET),
            "COMPRESS": ("buy the deficit side when all-in pair cost would "
                         "land at or under the trigger. Pair cost is NOT "
                         "required below 1 after leg 1."),
            "SWITCH": ("implicit: COMPRESS on the opposite side is the "
                       "switch, and once matched the machine re-ACQUIREs "
                       "the favored side, which is how L5 keeps working "
                       "inventory."),
            "RESIDUAL": "carried to settlement; no SELL exists to reduce it",
        },
        "l5_corpus_derivation": corpus,
        "arms": arms,
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    brief = {k: v for k, v in rep.items()
             if k not in ("arms", "l5_corpus_derivation")}
    brief["arms"] = {k: {kk: vv for kk, vv in v.items() if kk != "per_window"}
                     for k, v in arms.items()}
    print(json.dumps(brief, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
