"""L5 vs our engine, decision by decision, over the 527-window trajectory benchmark.

VENUE: PMUS (Polymarket US) both sides. L5's fills come from the committed
data/l5/raw/activity.jsonl via the EXISTING pipeline
(analysis/l5/inventory_trajectory/trajectory.py); nothing new is collected.

WHAT THIS ANSWERS, per the owner's request: for every actual L5 opening,
extension and completion -- his side, price, quantity, timing and inventory --
what would OUR engine have decided at that same state, and when it refuses,
WHICH gate refused it. The cap is attributed separately from every other gate so
"the cap is the problem" can be confirmed or denied rather than assumed.

GATE VALUES ARE THE DEPLOYED ONES, read from L5_EFFECTIVE_RUNTIME_CONFIG emitted
by deployment 55a11bf3/bab0bfaa, not from a replay harness:
    maker_pair_ceiling            0.96   (TS.max_pair_cost)       <- the cap under test
    ts_min_first_leg_own_price    0.20
    ts_opening_leg_max_contracts  2
    ts_entry_cutoff_s             600    -> an OPENING needs tte_s >= 300
    ts_complete_cutoff_s          720    -> a COMPLETION needs tte_s >= 180
    opening_notional_budget_usd   0.80
    max_live_orders               1
    max_unmatched_contracts       5
    max_cash_deployed_usd         5

HONEST LIMIT, stated here rather than in a footnote. This compares DECISIONS, not
executions. Whether a permitted action would actually have FILLED needs the PMUS
book at L5's timestamps, and the PMUS tape carries no print rows and is on a
volume this container cannot reach. So "permitted" means our gates allow it, NOT
that we would have got it. Settlement economics are reported separately and are
L5's realized outcome, never ours.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from decimal import Decimal as D

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analysis", "l5", "inventory_trajectory"))

import trajectory as TJ                                    # the existing pipeline

CAP_PROD = D("0.96")
CAP_ALT = D("0.99")
MIN_FIRST_LEG_PX = D("0.20")
OPEN_MAX_CT = D("2")
OPEN_NOTIONAL = D("0.80")
ENTRY_MIN_TTE = 300.0
COMPLETE_MIN_TTE = 180.0
MAX_UNMATCHED = D("5")
MAX_CASH = D("5")


def classify(prev, row):
    """OPENING / EXTENSION / COMPLETION from the inventory BEFORE the fill."""
    pu = D(str(prev["cum_UP_qty"])) if prev else D(0)
    pd_ = D(str(prev["cum_DOWN_qty"])) if prev else D(0)
    if pu == 0 and pd_ == 0:
        return "OPENING", pu, pd_
    before = abs(pu - pd_)
    side = row["side"]
    nu = pu + (D(str(row["size"])) if side == "Up" else D(0))
    nd = pd_ + (D(str(row["size"])) if side != "Up" else D(0))
    after = abs(nu - nd)
    return ("COMPLETION" if after < before else "EXTENSION"), pu, pd_


def held_vwap(prev, need_side):
    """Per-unit cost of the leg already held, the quantity our ceiling tests."""
    if not prev:
        return None
    v = prev["cum_DOWN_VWAP"] if need_side == "Up" else prev["cum_UP_VWAP"]
    return D(str(v)) if v is not None else None


def evaluate(prev, row, kind, cap):
    """Our engine's verdict at L5's state. Returns (verdict, gate, pair_cost)."""
    px = D(str(row["price"]))
    size = D(str(row["size"]))
    tte = float(row["tte_s"])
    pu = D(str(prev["cum_UP_qty"])) if prev else D(0)
    pd_ = D(str(prev["cum_DOWN_qty"])) if prev else D(0)

    if kind == "OPENING":
        if tte < ENTRY_MIN_TTE:
            return "REFUSED", "ENTRY_WINDOW_CLOSED", None
        if px < MIN_FIRST_LEG_PX:
            return "REFUSED", "MIN_FIRST_LEG_PRICE", None
        if size > OPEN_MAX_CT:
            return "REFUSED", "OPENING_LEG_MAX_CONTRACTS", None
        if size * px > OPEN_NOTIONAL:
            return "REFUSED", "OPENING_NOTIONAL_BUDGET", None
        return "REPRODUCED", None, None

    if kind == "COMPLETION":
        if tte < COMPLETE_MIN_TTE:
            return "REFUSED", "COMPLETE_CUTOFF", None
        held = held_vwap(prev, row["side"])
        if held is None:
            return "REFUSED", "NO_HELD_LEG", None
        pair = held + px
        if pair > cap:
            return "REFUSED", "PAIR_CEILING", pair
        if max(pu, pd_) + size > MAX_UNMATCHED:
            return "REFUSED", "MAX_UNMATCHED_CONTRACTS", pair
        return "REPRODUCED", None, pair

    # EXTENSION -- adds to the side already held, so it grows the residual.
    if abs(pu - pd_) + size > MAX_UNMATCHED:
        return "REFUSED", "MAX_UNMATCHED_CONTRACTS", None
    cash = pu * (D(str(prev["cum_UP_VWAP"])) if prev["cum_UP_VWAP"] else D(0)) \
        + pd_ * (D(str(prev["cum_DOWN_VWAP"])) if prev["cum_DOWN_VWAP"] else D(0))
    if cash + size * px > MAX_CASH:
        return "REFUSED", "MAX_CASH_DEPLOYED", None
    if size > OPEN_MAX_CT:
        return "REFUSED", "OPENING_LEG_MAX_CONTRACTS", None
    return "REPRODUCED", None, None


def main():
    wins = TJ.load(os.path.join(ROOT, "data", "l5", "raw", "activity.jsonl"))
    traj = []
    for slug, fills in sorted(wins.items()):
        t = TJ.trajectory(slug, fills)
        if t:
            traj.append((slug, t))
    traj.sort(key=lambda s: s[1][0]["t"])              # CHRONOLOGICAL

    rows = []
    for slug, t in traj:
        prev = None
        for r in t:
            kind, _, _ = classify(prev, r)
            v96, g96, pair = evaluate(prev, r, kind, CAP_PROD)
            v99, g99, _ = evaluate(prev, r, kind, CAP_ALT)
            rows.append(dict(slug=slug, t=r["t"], tte_s=r["tte_s"], n=r["n"],
                             kind=kind, side=r["side"], price=str(r["price"]),
                             size=str(r["size"]),
                             cum_UP=str(r["cum_UP_qty"]), cum_DN=str(r["cum_DOWN_qty"]),
                             residual=str(r["residual_qty"]),
                             pair_cost=(str(pair) if pair is not None else None),
                             v96=v96, gate96=g96, v99=v99, gate99=g99))
            prev = r
    return traj, rows


if __name__ == "__main__":
    traj, rows = main()
    out = os.path.join(ROOT, "analysis", "l5", "fidelity_cap")
    with open(os.path.join(out, "decisions.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print("windows %d  decisions %d" % (len(traj), len(rows)))
    print("span %s -> %s" % (traj[0][1][0]["t"], traj[-1][1][-1]["t"]))
    k = Counter(r["kind"] for r in rows)
    print("kinds:", dict(k))
