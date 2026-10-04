"""How could the sequential pairs actually be CAPTURED? Taker, maker, hybrid.

The path study established that complementary price travel exists in the tape
(analysis/l5/path_economics/PATH_ECONOMICS_RESULT.md). A hindsight pair
opportunity is not a fill, so this measures the four capture modes on the SAME
mechanism and the same sub-second tape:

    TT  taker first leg  / taker complement
    MT  maker first leg  / taker complement
    TM  taker first leg  / maker complement
    MM  maker both legs

MAKER IS NOT ASSUMED BETTER. It is structurally cheaper -- a maker buy rests at
the bid rather than paying the ask, and pays no taker fee -- and structurally
less certain, because it may never fill. Which effect dominates is the question,
not the premise.

TOUCH IS NEVER A FILL. The maker contract is `src/l5_causal_sim`'s, reused rather
than reinvented, and its rules are the ones that make a passive fill defensible:

  * queue-ahead is initialised from the depth DISPLAYED AT OUR EXACT PRICE at
    placement; if the book does not show our level the queue is unknowable and
    we do not rest;
  * the queue is decremented ONLY by prints at or through our price -- a print
    at a better price never reached us;
  * one print consumes queue first and whatever remains of THAT SAME print fills
    us, so partial fills are normal;
  * displayed liquidity, by itself, never fills anything.

NO RE-PRICING, DECLARED RATHER THAN TUNED. A resting order stays at its
placement price until it fills or the window ends. Re-pricing would send us to
the back of a new queue and would need a policy for when to do it; that policy
would be a fitted parameter, and this study fits none. So maker fill rates here
are the PATIENT case.

REBATES AND REWARDS ARE ZERO. `pmus_fees` returns a maker rebate as a positive
credit and never nets it against cost, and the taker rebate tiers start far above
this account's volume. The one sourced maker credit in this repository -- 25
passiveExecution prints, 119.49 contracts, $0.42, i.e. $0.003515/contract, in
`l5_causal_sim.SOURCED_REBATE_PER_CONTRACT` -- is reported as a SEPARATE
sensitivity line, because it was credited for a particular product and period and
its attribution to these simulated orders is not established.

NO STRATEGY IS OPTIMISED. The mechanism is the path study's: acquire a first leg,
complete when the all-in locks below the $1.00 a matched pair settles for, repeat.
Lots are FIFO, matched tranches are never re-paired, and depth is consumed.
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
from src.l5_causal_sim import SOURCED_REBATE_PER_CONTRACT         # noqa: E402

ONE, ZERO = D(1), D(0)
CLIP = ONE                      # one unit per leg, as in the path study
MODES = ("TT", "MT", "TM", "MM")


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def own_price(side: str, px: D) -> D:
    """Our price terms for a print quoted in UP terms."""
    return px if side == "UP" else ONE - px


def load_events(path):
    """slug -> chronological book and trade events, sub-second."""
    by = collections.defaultdict(list)
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                   # noqa: BLE001
                continue
            k = r.get("kind")
            if k not in ("book", "trade") or not r.get("slug"):
                continue
            by[r["slug"]].append(r)
    for v in by.values():
        v.sort(key=lambda r: float(r["t"]))
    return by


class Resting:
    """A passive order under the reused causal contract."""

    def __init__(self, side, px, qty, ahead, t):
        self.side, self.px, self.qty = side, px, qty
        self.ahead = ahead
        self.ahead0 = ahead
        self.filled = ZERO
        self.t = t

    def remaining(self):
        return self.qty - self.filled


def run_mode(events, outcome_up, mode, first_side="UP"):
    """One window, one capture mode. Strictly forward over the event stream."""
    book = None
    lots = []                   # open (unmatched) lots, FIFO
    tranches = []
    rest = None                 # at most one resting order
    cash = ZERO
    peak = ZERO
    want_first = True
    maker_rest_times = []
    n_maker_placed = n_maker_filled = 0
    n_taker_fills = 0

    def exe_cost(side):
        """Taker all-in unit cost, and displayed depth at that touch."""
        if book is None:
            return None
        bid, ask = book
        if side == "UP":
            px, dep = ask[0], ask[1]
        else:
            px, dep = ONE - bid[0], bid[1]
        if px <= 0 or px > D("0.99") or dep <= 0:
            return None
        return px + FEES.taker_fee(px, CLIP, cents=True), px, dep

    def maker_px(side):
        """Where a passive buy of `side` rests, and the queue displayed there."""
        if book is None:
            return None
        bid, ask = book
        if side == "UP":
            px, q = bid[0], bid[1]          # join the UP bid
        else:
            px, q = ONE - ask[0], ask[1]    # buying DOWN rests at 1 - UP ask
        if px <= 0 or px > D("0.99") or q <= 0:
            return None
        return px, q

    for r in events:
        t = float(r["t"])
        if r["kind"] == "book":
            try:
                bid, ask = D(str(r["bid"])), D(str(r["ask"]))
                bq = D(str(r.get("bid_qty") or 0))
                aq = D(str(r.get("ask_qty") or 0))
            except Exception:                                   # noqa: BLE001
                continue
            if bid <= 0 or ask <= 0:
                continue
            book = ((bid, bq), (ask, aq))

            # ---- taker actions happen on book state
            if want_first and mode in ("TT", "TM"):
                c = exe_cost(first_side)
                if c is not None:
                    allin, px, dep = c
                    q = min(CLIP, dep)
                    lots.append({"side": first_side, "cost": allin * q,
                                 "qty": q, "t": t})
                    cash += allin * q
                    peak = max(peak, cash)
                    n_taker_fills += 1
                    want_first = False
            elif want_first and mode in ("MT", "MM") and rest is None:
                m = maker_px(first_side)
                if m is not None:
                    rest = Resting(first_side, m[0], CLIP, m[1], t)
                    n_maker_placed += 1
            if (not want_first) and lots and mode in ("TT", "MT"):
                lot = lots[0]
                opp = "DOWN" if lot["side"] == "UP" else "UP"
                c = exe_cost(opp)
                if c is not None:
                    allin, px, dep = c
                    unit = lot["cost"] / lot["qty"]
                    if unit + allin < ONE:
                        q = min(lot["qty"], dep)
                        if q > 0:
                            cash += allin * q
                            peak = max(peak, cash)
                            n_taker_fills += 1
                            tranches.append({
                                "qty": str(q), "all_in": str(unit + allin),
                                "locked_net": str((ONE - unit - allin) * q),
                                "mode_second": "taker"})
                            lot["cost"] -= unit * q
                            lot["qty"] -= q
                            if lot["qty"] <= 0:
                                lots.pop(0)
                            want_first = True
                            rest = None
            elif (not want_first) and lots and mode in ("TM", "MM") \
                    and rest is None:
                lot = lots[0]
                opp = "DOWN" if lot["side"] == "UP" else "UP"
                m = maker_px(opp)
                if m is not None:
                    unit = lot["cost"] / lot["qty"]
                    # a passive complement pays no taker fee; rebate stays 0
                    if unit + m[0] < ONE:
                        rest = Resting(opp, m[0], lot["qty"], m[1], t)
                        n_maker_placed += 1
            continue

        # ---- a real print: the ONLY thing that can fill a resting order
        if rest is None:
            continue
        try:
            px = D(str(r["px"]))
            qty = D(str(r["qty"]))
        except Exception:                                       # noqa: BLE001
            continue
        if qty <= 0:
            continue
        if own_price(rest.side, px) > rest.px:
            continue                        # printed away from us
        if rest.ahead > 0:
            take = qty if qty < rest.ahead else rest.ahead
            rest.ahead -= take
            qty -= take
            if qty <= 0:
                continue                    # the queue absorbed the whole print
        got = qty if qty < rest.remaining() else rest.remaining()
        if got <= 0:
            continue
        rest.filled += got
        cost = rest.px * got                # no taker fee on a passive fill
        cash += cost
        peak = max(peak, cash)
        if want_first:
            lots.append({"side": rest.side, "cost": cost, "qty": got, "t": t})
            if rest.remaining() <= 0:
                n_maker_filled += 1
                maker_rest_times.append(t - rest.t)
                want_first = False
                rest = None
        else:
            lot = lots[0]
            unit = lot["cost"] / lot["qty"]
            tranches.append({
                "qty": str(got), "all_in": str(unit + rest.px),
                "locked_net": str((ONE - unit - rest.px) * got),
                "mode_second": "maker"})
            lot["cost"] -= unit * got
            lot["qty"] -= got
            if lot["qty"] <= 0:
                lots.pop(0)
            if rest.remaining() <= 0:
                n_maker_filled += 1
                maker_rest_times.append(t - rest.t)
                want_first = True
                rest = None

    matched = sum(D(x["qty"]) for x in tranches)
    locked = sum(D(x["locked_net"]) for x in tranches)
    resid_pnl = ZERO
    resid_qty = ZERO
    for lot in lots:
        won = (outcome_up == 1) if lot["side"] == "UP" else (outcome_up == 0)
        resid_pnl += (lot["qty"] if won else ZERO) - lot["cost"]
        resid_qty += lot["qty"]
    maker_fills = sum(1 for x in tranches if x["mode_second"] == "maker")
    return {"matched": matched, "locked": locked, "resid_pnl": resid_pnl,
            "resid_qty": resid_qty, "net": locked + resid_pnl,
            "cash": cash, "peak": peak, "tranches": len(tranches),
            "maker_placed": n_maker_placed, "maker_filled": n_maker_filled,
            "maker_complement_fills": maker_fills,
            "taker_fills": n_taker_fills,
            "maker_rest_s": maker_rest_times}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", required=True)
    ap.add_argument("--state", required=True,
                    help="frozen state table, for the settlement label only")
    ap.add_argument("--label", default="OOS")
    ap.add_argument("--out", default="analysis/l5/path_economics")
    a = ap.parse_args()

    outcome = {}
    with _open(a.state) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                   # noqa: BLE001
                continue
            if r.get("slug") and r.get("outcome_up") is not None:
                outcome[r["slug"]] = r["outcome_up"]

    ev = load_events(a.books)
    slugs = sorted(set(ev) & set(outcome))

    res = {}
    for mode in MODES:
        rows = [run_mode(ev[s], outcome[s], mode) for s in slugs]
        nets = [float(x["net"]) for x in rows]
        placed = sum(x["maker_placed"] for x in rows)
        filled = sum(x["maker_filled"] for x in rows)
        rest_s = [v for x in rows for v in x["maker_rest_s"]]
        matched = sum(x["matched"] for x in rows)
        res[mode] = {
            "windows": len(rows),
            "windows_with_a_matched_tranche": sum(1 for x in rows
                                                  if x["tranches"] > 0),
            "matched_qty": str(matched),
            "tranches_total": sum(x["tranches"] for x in rows),
            "locked_net_total": round(sum(float(x["locked"]) for x in rows), 4),
            "residual_pnl_total": round(sum(float(x["resid_pnl"])
                                            for x in rows), 4),
            "residual_qty_total": str(sum(x["resid_qty"] for x in rows)),
            "TOTAL_NET": round(sum(nets), 4),
            "net_per_window_mean": (round(statistics.mean(nets), 5)
                                    if nets else None),
            "net_per_window_median": (round(statistics.median(nets), 5)
                                      if nets else None),
            "pct_windows_positive": (round(100.0 * sum(1 for v in nets if v > 0)
                                           / len(nets), 2) if nets else None),
            "maker_orders_placed": placed,
            "maker_orders_filled": filled,
            "maker_fill_rate_pct": (round(100.0 * filled / placed, 2)
                                    if placed else None),
            "maker_rest_seconds": {
                "median": round(statistics.median(rest_s), 2) if rest_s else None,
                "p90": (round(sorted(rest_s)[int(.9 * (len(rest_s) - 1))], 2)
                        if rest_s else None)},
            "taker_fills": sum(x["taker_fills"] for x in rows),
            "peak_capital_mean": round(statistics.mean(
                [float(x["peak"]) for x in rows]), 4) if rows else None,
            "REBATE_SENSITIVITY_not_in_primary": {
                "sourced_rate_per_contract": str(
                    round(SOURCED_REBATE_PER_CONTRACT, 6)),
                "provenance": ("25 passiveExecution prints, 119.49 contracts, "
                               "$0.42 credited; a different product/period, "
                               "attribution to these orders NOT established"),
                "if_applied_to_passive_fills_only": round(
                    float(SOURCED_REBATE_PER_CONTRACT) * float(matched)
                    * (1.0 if mode == "MM" else 0.5 if mode in ("MT", "TM")
                       else 0.0), 4)},
        }
    out = {"sample": a.label, "books": a.books, "windows": len(slugs),
           "maker_contract": "src/l5_causal_sim -- touch is never a fill",
           "rebate_policy": "ZERO in every primary number",
           "repricing": "none; a resting order never re-prices (patient case)",
           "by_mode": res}
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "capture_mechanics_%s.json" % a.label.lower())
    with open(p, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(out, indent=1, default=str))
    print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
