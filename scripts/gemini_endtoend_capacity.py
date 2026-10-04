"""END-TO-END CAPACITY: the whole sequence executable at one size, or not.

THE CORRECTION THIS ENFORCES. The reachability pass reported "quantity
executable when reachable = q100" on both durations, which is true about
the COMPLEMENT and says nothing about capacity. A q100 complement does not
establish q100 capacity unless the OPEN -- and every EXTEND before it --
could also have been acquired at q100 under the frozen rules, against the
depth recorded at those earlier moments.

So capacity here means the COMPLETE sequential path:

    OPEN -> optional EXTEND(s) -> COMPLEMENT -> resulting inventory

with the ladder walked INDEPENDENTLY at every action, at the VWAP actually
available for that quantity at that instant. A leg the ladder could not
supply does not trade: the sequence simply does not reach the next action,
and that is recorded rather than skipped.

This reuses `gemini_capacity_replay.replay_window`, which already walks
each action against its own tick and refuses any action whose legs are not
fully executable at the size. There is no second simulator here -- a
second one would be a second set of assumptions.

WHAT IS SEPARATED, because they are different kinds of money:

  COMPLETED states   -- a matched pair exists. Its economics are known at
                        trade time: a pair pays exactly 1.00 whichever way
                        the market resolves, so matched P&L is
                        (1.00 - actual executable pair cost) x pairs.
  UNCOMPLETED states -- a directional residual survived the window. It is
                        settled against the venue's ACTUAL published
                        outcome, and reported on its own line.

                matched P&L + residual P&L - fees = total modeled net

CAPITAL REQUIRED is reported because it is the denominator the net has to
be judged against: the peak principal the sequence had outstanding.

.96 STAYS FROZEN. The sensitivity sweep gave no evidence to loosen it --
on BTC15m .96 already captures everything .94 and up can reach, and on
BTC5m the curve is flat to .98 and only moves at 1.00, where a pair costs
a dollar and locks nothing.

Accounting label: historical replay. No order was sent.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_execution import gemini_durations as DUR
from src.gemini_execution.ci15m_policy import POLICY
from src.gemini_execution.continuous_probe import COMPLETE, EXTEND, OPEN

from scripts.gemini_capacity_replay import (MEASURED_MAKER_FEE,
                                            MODELLED_TAKER_FEE,
                                            WINDOWS_PER_DAY, load,
                                            replay_window, resolutions)
from scripts.gemini_capacity_replay import _d as _dec

LABEL = "GEMINI_ENDTOEND_CAPACITY_V1"
SIZES = (D("1"), D("5"), D("10"), D("25"), D("50"), D("100"))


def _peak_capital(rep: Dict[str, Any], qty: D) -> D:
    """Peak principal outstanding across the sequence.

    Every action in this engine BUYS, so principal only accumulates: the
    peak is the total spent. Reported as the denominator the net must be
    judged against, not as a modelled margin requirement.
    """
    spent = D("0")
    for a in rep.get("actions") or []:
        spent += (_dec(a.get("price")) or D("0")) * qty
    return spent


def one_size(by: Dict[str, List[Dict[str, Any]]], qty: D,
             res: Dict[str, Optional[str]], duration: str) -> Dict[str, Any]:
    completed: List[Dict[str, Any]] = []
    uncompleted: List[Dict[str, Any]] = []
    no_trade: List[str] = []
    rows: List[Dict[str, Any]] = []

    for ticker, ticks in by.items():
        rep = replay_window(ticks, qty)
        n_open = rep["counts"].get(OPEN, 0)
        n_ext = rep["counts"].get(EXTEND, 0)
        n_cmp = rep["counts"].get(COMPLETE, 0)
        matched = _dec(rep["matched_qty"]) or D("0")
        capital = _peak_capital(rep, qty)
        n_fills = rep["n_actions"]
        fee_m = D(n_fills) * MEASURED_MAKER_FEE
        fee_t = D(n_fills) * MODELLED_TAKER_FEE

        # MATCHED: a pair pays 1.00 either way, so this is known at trade
        # time and does not depend on the outcome.
        matched_pnl = D("0")
        pair_cost = _dec(rep["matched_pair_cost"])
        if matched > 0 and pair_cost is not None:
            matched_pnl = matched * (D("1") - pair_cost)

        # RESIDUAL: settled against the venue's ACTUAL outcome.
        res_q = _dec(rep["residual_qty"]) or D("0")
        res_prin = _dec(rep["residual_principal"]) or D("0")
        res_side = rep["residual_side"]
        resolution = res.get(ticker)
        res_pnl: Optional[D] = D("0") if res_q == 0 else None
        if res_q > 0 and resolution is not None and res_side is not None:
            won = ((res_side == "UP" and resolution == "yes")
                   or (res_side == "DOWN" and resolution == "no"))
            res_pnl = (res_q if won else D("0")) - res_prin

        row = {
            "ticker": ticker, "n_open": n_open, "n_extend": n_ext,
            "n_complement": n_cmp,
            "path_executable_end_to_end": bool(n_open and n_cmp),
            "matched_qty": str(matched),
            "executable_pair_cost": (None if pair_cost is None
                                     else str(pair_cost)),
            "matched_pnl": str(matched_pnl),
            "residual_qty": str(res_q), "residual_side": res_side,
            "residual_principal": str(res_prin),
            "resolution_side": resolution,
            "residual_pnl": (None if res_pnl is None else str(res_pnl)),
            "residual_unsettled": res_pnl is None,
            "n_fills": n_fills,
            "fees_measured": str(fee_m),
            "fees_modelled_taker": str(fee_t),
            "capital_required": str(capital),
            "unavailable_ticks": rep["unavailable_ticks"],
        }
        rows.append(row)
        if n_open == 0:
            no_trade.append(ticker)
        elif matched > 0:
            completed.append(row)
        else:
            uncompleted.append(row)

    def _sum(rs: List[Dict[str, Any]], k: str) -> D:
        return sum((_dec(r[k]) or D("0")) for r in rs)

    traded = completed + uncompleted
    matched_total = _sum(completed, "matched_pnl")
    resid_total = sum((_dec(r["residual_pnl"]) or D("0")) for r in traded
                      if r["residual_pnl"] is not None)
    fees_total = _sum(traded, "fees_measured")
    fees_t_total = _sum(traded, "fees_modelled_taker")
    net = matched_total + resid_total - fees_total
    net_t = matched_total + resid_total - fees_t_total
    n_win = len(by)
    capital = _sum(traded, "capital_required")
    peak = max([_dec(r["capital_required"]) or D("0")
                for r in traded] or [D("0")])
    return {
        "qty": str(qty),
        "windows_attempted": n_win,
        "no_trade_windows": len(no_trade),
        "windows_traded": len(traded),
        "completed_states": len(completed),
        "uncompleted_states": len(uncompleted),
        "completion_pct": (None if not traded
                           else round(100.0 * len(completed) / len(traded), 1)),
        "end_to_end_executable_windows": sum(
            1 for r in rows if r["path_executable_end_to_end"]),
        "matched_pnl_total": str(matched_total),
        "residual_pnl_total": str(resid_total),
        "fees_measured_total": str(fees_total),
        "fees_modelled_taker_total": str(fees_t_total),
        "total_modeled_net_measured_fees": str(net),
        "total_modeled_net_modelled_taker_fees": str(net_t),
        "identity": "matched_pnl + residual_pnl - fees = total_modeled_net",
        "net_per_window": (None if not n_win else str(net / n_win)),
        "net_per_traded_window": (None if not traded
                                  else str(net / len(traded))),
        "est_net_per_day": (
            None if not n_win else
            str((net / n_win) * WINDOWS_PER_DAY.get(duration, 0))),
        "windows_per_day_assumed": WINDOWS_PER_DAY.get(duration),
        "capital_required_total": str(capital),
        "capital_required_peak_single_window": str(peak),
        "residual_unsettled_windows": sum(1 for r in traded
                                          if r["residual_unsettled"]),
        "windows": rows,
    }


def run(path: str, duration: str) -> Dict[str, Any]:
    by = load(path)
    res = resolutions(sorted(by), duration)
    per_size = {str(q): one_size(by, q, res, duration) for q in SIZES}
    # DEFENSIBLE CAPACITY: the largest size whose COMPLETE path executed at
    # least once AND whose total modeled net is positive. Both conditions,
    # because either alone is misleading.
    ok = [q for q in SIZES
          if per_size[str(q)]["completed_states"] > 0
          and D(per_size[str(q)]["total_modeled_net_measured_fees"]) > 0]
    return {
        "label": LABEL, "duration": duration, "tape": path,
        "n_windows": len(by),
        "effective_open_band": DUR.effective_open_band(duration),
        "frozen_pair_ceiling": str(POLICY["MAX_PAIR_COST"]),
        "per_size": per_size,
        "max_defensible_end_to_end_qty": (None if not ok else str(max(ok))),
        "accounting_label": "historical replay",
        "labels": (
            "CAPACITY MEANS THE WHOLE PATH. A complement executable at a "
            "size proves nothing unless the OPEN and every EXTEND before "
            "it were also executable at that size against the depth "
            "recorded at those moments. Sizes are priced at the walked "
            "VWAP, never at the touch. Matched and residual P&L are never "
            "summed into one figure without being shown separately, and "
            "the measured fee ($0.00 maker, the only authenticated "
            "observation) is never merged with the modelled taker fee."),
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", default=DUR.BTC15M)
    ap.add_argument("--tape", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    path = a.tape
    if path is None:
        c = sorted(glob.glob(os.path.join(DUR.dataset_root(a.duration),
                                          "capacity_tape_*.jsonl")))
        if not c:
            print("no tape for %s" % a.duration)
            return 2
        path = c[-1]
    rep = run(path, a.duration)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s  %s  windows=%d  band=%s  ceiling=%s  max_defensible=%s"
          % (rep["label"], rep["duration"], rep["n_windows"],
             rep["effective_open_band"]["band"], rep["frozen_pair_ceiling"],
             rep["max_defensible_end_to_end_qty"]))
    print("%4s %5s %6s %6s %5s %6s %9s %9s %7s %10s %10s %9s"
          % ("q", "win", "noTrd", "traded", "cmpl", "cmpl%",
             "matchPnL", "residPnL", "fees", "NET", "net/win", "capital"))
    for q, r in rep["per_size"].items():
        print("%4s %5s %6s %6s %5s %6s %9s %9s %7s %10s %10s %9s"
              % (q, r["windows_attempted"], r["no_trade_windows"],
                 r["windows_traded"], r["completed_states"],
                 (r["completion_pct"] if r["completion_pct"] is not None
                  else "-"),
                 r["matched_pnl_total"][:9], r["residual_pnl_total"][:9],
                 r["fees_measured_total"][:7],
                 r["total_modeled_net_measured_fees"][:10],
                 (r["net_per_window"] or "-")[:10],
                 r["capital_required_total"][:9]))
    print("\nidentity: matched + residual - fees = net   (.96 FROZEN)")
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
