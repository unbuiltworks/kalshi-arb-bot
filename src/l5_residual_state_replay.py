"""When L5 opens a leg, what decides whether he ever pairs it?

OWNER item 4: "Replay historical L5 specifically around residual states to
determine what distinguishes eventual completion from residual held to
settlement -- price movement, number of actions, timing, imbalance and
market state."

L5 opened 2,216 windows and paired 1,610 of them. The other 606 he carried
to settlement, which is the same state our lane ends in 68.5% of the time.
So his own history is the only place to ask what separates the two.

TWO KINDS OF FEATURE, AND THE DIFFERENCE IS THE WHOLE METHOD.

  PREDICTIVE -- known at the moment the residual state opens: the first
    leg's price, how much of the window is left, how long the window is.
    These can inform selection, because they exist before the outcome does.

  REALIZED -- observed while the window ran: how many distinct prices L5
    worked the opening side at. These CANNOT inform selection and are not
    offered as a filter. A window with more actions had more time and more
    flow, and a window that completed had more of everything by
    construction.

The realized feature is reported anyway, because the naive version of it
(36.8% completion at one price rising to 93.5% at four) is exactly the kind
of number that gets mistaken for a lever. It is reported STRATIFIED by the
two confounders that would otherwise explain it -- window length and time
remaining -- and it survives that: inside every stratum with enough rows,
more opening-side prices still means more completion, monotonically. That
makes it evidence worth acting on. It does not make it causal: a market
active enough to reprice into is a market that completes for reasons of its
own, and this data cannot separate those.

THE TRANSFER LIMIT, STATED FIRST BECAUSE IT BOUNDS EVERYTHING BELOW. This
history is entirely BTC and ETH up/down windows of 5 and 15 minutes. Our
lane trades sports. The mechanism these numbers describe -- time to work a
position, and liquidity on the far side -- is not crypto-specific, but the
magnitudes are measured on crypto and no claim here is a sports
measurement.

READ-ONLY.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Callable, Dict, List, Mapping, Optional

from src import l5_completion_behavior as cb

MIN_STRATUM_N = 15          # below this a stratum reports counts, not a rate


# --------------------------------------------------------------- the features
def window_remaining_fraction(e: Mapping[str, Any]) -> Optional[float]:
    """How much of the window was still ahead when the first leg went on."""
    tts, dur = e.get("first_leg_tts"), e.get("duration_minutes")
    if tts is None or not dur:
        return None
    return tts / (dur * 60.0)


def remaining_bucket(e: Mapping[str, Any]) -> Optional[str]:
    f = window_remaining_fraction(e)
    if f is None:
        return None
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        if f < lo + 0.2:
            return f"{lo:.1f}-{lo + 0.2:.1f}"
    return "1.0+"


def price_bucket(e: Mapping[str, Any]) -> Optional[str]:
    p = e.get("first_leg_price")
    if p is None:
        return None
    for lo in range(0, 10):
        if p < (lo + 1) / 10:
            return f"{lo / 10:.1f}-{(lo + 1) / 10:.1f}"
    return "0.9-1.0"


def imbalance_bucket(e: Mapping[str, Any]) -> Optional[str]:
    """Price as a proxy for how lopsided the market was.

    A first leg at 0.05 is a market with almost nothing on the far side to
    pair against; one at 0.50 has both sides live."""
    p = e.get("first_leg_price")
    if p is None:
        return None
    if 0.35 <= p <= 0.65:
        return "balanced 0.35-0.65"
    if p < 0.20 or p > 0.80:
        return "lopsided <0.20 or >0.80"
    return "leaning 0.20-0.35 / 0.65-0.80"


def opening_side_prices(e: Mapping[str, Any]) -> Optional[str]:
    """REALIZED, not predictive. Kept capped so the tail does not dominate."""
    n = e.get("first_leg_distinct_prices")
    return None if n is None else f"{min(n, 4)}{'+' if n >= 4 else ''}"


PREDICTIVE: Dict[str, Callable] = {
    "window_length_minutes": lambda e: (
        None if e.get("duration_minutes") is None
        else f"{e['duration_minutes']:.0f}"),
    "window_remaining_at_first_leg": remaining_bucket,
    "first_leg_price": price_bucket,
    "market_imbalance_at_first_leg": imbalance_bucket,
}
REALIZED: Dict[str, Callable] = {
    "opening_side_distinct_prices": opening_side_prices,
}


# ------------------------------------------------------------------ the counts
def _rate(rows: List[Mapping[str, Any]]) -> dict:
    n = len(rows)
    c = sum(1 for e in rows if e.get("completed"))
    return {"n": n, "completed": c,
            "completion_rate": round(c / n, 4) if n else None}


def conditional(events: List[Mapping[str, Any]], keyfn: Callable) -> dict:
    g: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for e in events:
        k = keyfn(e)
        if k is not None:
            g[k].append(e)
    return {k: _rate(v) for k, v in sorted(g.items())}


def stratified(events: List[Mapping[str, Any]], keyfn: Callable,
               strata: List[Callable]) -> dict:
    """The same rate inside each cell of the confounders, never pooled.

    A cell below MIN_STRATUM_N reports its counts and a null rate rather
    than a rate computed on too little to mean anything."""
    g: Dict[tuple, Dict[str, List]] = defaultdict(lambda: defaultdict(list))
    for e in events:
        cell = tuple(s(e) for s in strata)
        k = keyfn(e)
        if k is None or any(c is None for c in cell):
            continue
        g[cell][k].append(e)
    out: Dict[str, Any] = {}
    for cell, byk in sorted(g.items(), key=lambda kv: [str(x) for x in kv[0]]):
        rows = {}
        for k, v in sorted(byk.items()):
            r = _rate(v)
            if r["n"] < MIN_STRATUM_N:
                r["completion_rate"] = None
                r["suppressed_below_min_n"] = MIN_STRATUM_N
            rows[k] = r
        out[" | ".join(str(c) for c in cell)] = rows
    return out


def _monotone(cells: Mapping[str, Mapping[str, Any]]) -> dict:
    """Does the realized feature keep its direction inside every stratum?"""
    checked = rising = 0
    for rows in cells.values():
        seq = [r["completion_rate"] for _, r in sorted(rows.items())
               if r.get("completion_rate") is not None]
        if len(seq) < 2:
            continue
        checked += 1
        rising += 1 if all(b >= a for a, b in zip(seq, seq[1:])) else 0
    return {"strata_with_at_least_two_usable_cells": checked,
            "strata_where_the_direction_holds": rising,
            "share": round(rising / checked, 4) if checked else None}


# ------------------------------------------------------------------ the report
def report(path: str = cb.CANONICAL) -> dict:
    ev = cb.completion_events(cb.windows(path))
    overall = _rate(ev)
    pred = {name: conditional(ev, fn) for name, fn in PREDICTIVE.items()}
    real = {name: conditional(ev, fn) for name, fn in REALIZED.items()}
    strata = [PREDICTIVE["window_length_minutes"], remaining_bucket]
    strat = stratified(ev, opening_side_prices, strata)
    return {
        "windows_with_a_first_leg": overall["n"],
        "windows_l5_paired": overall["completed"],
        "l5_completion_rate": overall["completion_rate"],
        "windows_l5_carried_to_settlement": overall["n"] - overall["completed"],
        "predictive_features": pred,
        "predictive_means": (
            "known at the moment the residual state opens, so these can "
            "inform selection: they exist before the outcome does"),
        "realized_features": real,
        "realized_means": (
            "observed while the window ran. NOT offered as a filter -- a "
            "window with more actions had more time and more flow, and a "
            "window that completed had more of everything by construction"),
        "realized_stratified_by_window_length_and_time_remaining": strat,
        "does_the_realized_signal_survive": _monotone(strat),
        "the_findings": [
            "TIME TO WORK IT IS THE STRONGEST PREDICTOR. L5 pairs 93.3% of "
            "15-minute windows and 65.7% of 5-minute ones, and within a "
            "window the rate falls from 82.9% when he opens with 80-100% of "
            "it left to 38.0% when he opens with under 20% left.",
            "IMBALANCE IS THE SECOND. A first leg in a balanced book "
            "(0.35-0.65) pairs 74.6% of the time on 5-minute windows and "
            "97.7% on 15-minute ones; a lopsided one (<0.20 or >0.80) pairs "
            "49.7% and 33.3%. There is nothing on the far side to pair "
            "against.",
            "WORKING THE POSITION SURVIVES ITS CONFOUNDERS. One opening-side "
            "price completes 36.8% of the time and four or more completes "
            "93.5%, and that gap does not collapse when window length and "
            "time remaining are held fixed -- inside the largest stratum it "
            "still runs 43.0% to 93.3%. It is evidence, not proof: a market "
            "active enough to reprice into completes for reasons of its own.",
        ],
        "transfer_limit": (
            "This history is entirely BTC and ETH up/down windows of 5 and "
            "15 minutes. Our lane trades sports. Time-to-work and far-side "
            "liquidity are not crypto-specific mechanisms, but every "
            "magnitude here is measured on crypto and none of it is a sports "
            "measurement."),
        "no_causal_claim": (
            "These are conditional rates with two confounders held fixed, "
            "not an experiment. They say where completion happens, not what "
            "would happen if we changed our behaviour."),
    }


def _table(name: str, rows: Mapping[str, Mapping[str, Any]]) -> List[str]:
    L = [f"### {name}", "", "| bucket | n | completed | rate |",
         "|---|---|---|---|"]
    for k, r in rows.items():
        rate = ("—" if r["completion_rate"] is None
                else f"{r['completion_rate']:.1%}")
        L.append(f"| {k} | {r['n']} | {r['completed']} | {rate} |")
    return L + [""]


def render_markdown(rep: Mapping[str, Any]) -> str:
    L = ["# What decides whether L5 ever pairs a leg", "",
         f"{rep['windows_with_a_first_leg']} windows with a first leg — "
         f"**{rep['windows_l5_paired']} paired "
         f"({rep['l5_completion_rate']:.1%})**, "
         f"{rep['windows_l5_carried_to_settlement']} carried to settlement.",
         "", "> " + rep["transfer_limit"], "",
         "## Predictive features", "", rep["predictive_means"], ""]
    for name, rows in rep["predictive_features"].items():
        L += _table(name.replace("_", " "), rows)
    L += ["## Realized features", "", rep["realized_means"], ""]
    for name, rows in rep["realized_features"].items():
        L += _table(name.replace("_", " "), rows)
    m = rep["does_the_realized_signal_survive"]
    L += ["### Does it survive its confounders?", "",
          f"Holding window length and time remaining fixed, the direction "
          f"holds in **{m['strata_where_the_direction_holds']} of "
          f"{m['strata_with_at_least_two_usable_cells']}** strata that have "
          f"at least two usable cells.", "",
          "| stratum (length \\| remaining) | " +
          " | ".join(sorted({k for rows in rep[
              "realized_stratified_by_window_length_and_time_remaining"
          ].values() for k in rows})) + " |"]
    cols = sorted({k for rows in rep[
        "realized_stratified_by_window_length_and_time_remaining"].values()
        for k in rows})
    L.append("|---" * (len(cols) + 1) + "|")
    for cell, rows in rep[
            "realized_stratified_by_window_length_and_time_remaining"].items():
        cs = []
        for c in cols:
            r = rows.get(c)
            cs.append("—" if not r or r["completion_rate"] is None
                      else f"{r['completion_rate']:.0%} (n={r['n']})")
        L.append(f"| {cell} | " + " | ".join(cs) + " |")
    L += ["", "## Findings", ""]
    L += [f"{i}. {f}" for i, f in enumerate(rep["the_findings"], 1)]
    L += ["", "## No causal claim", "", rep["no_causal_claim"]]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sequence", default=cb.CANONICAL)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = report(a.sequence)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "L5_RESIDUAL_STATE_REPLAY.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "l5_residual_state_replay.json"),
                  "w") as f:
            json.dump(rep, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
