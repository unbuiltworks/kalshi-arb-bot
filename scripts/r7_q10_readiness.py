"""What q10 does on recorded BTC-15m windows, measured before R7 runs.

PURPOSE. R7 is one window. One window of anything is a data point, not a
result, so the point of this script is to know BEFORE it runs what the
frozen corrected engine at clip 10 typically does -- so that whatever R7
does can be read against a distribution rather than against a hope.

WHAT IS MEASURED, per window and in aggregate:
  * completion rate      matched legs / acquired legs at settlement
  * full completion      windows ending with residual EXACTLY zero
  * residual persistence how long the book sits unmatched, in seconds and as
                         a fraction of the window
  * matched-pair economics, residual P&L, fees, net
  * peak cash and peak residual, against the R7 envelope

NO REBATES ARE ASSUMED ANYWHERE. `fees` is the frozen fee model's charge.
The only credited rebate evidence we hold is $2.13 over 293 fills, and it is
not applied here or anywhere else in this file.

NOT A STRATEGY CHANGE. Every parameter is read from the frozen engine
module. clip is the ONLY thing varied, and only to show q10 against the
frozen q50 reference the parity suite pins.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P


def _t(row: Mapping[str, Any]) -> Optional[float]:
    s = row.get("t")
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s)).timestamp()
    except ValueError:
        return None


def walk(rows, *, winner, clip) -> Dict[str, Any]:
    """`replay_window`'s loop, instrumented. Decisions are NOT altered."""
    eng = E.NativeEngine(clip=D(str(clip)), prospective_cap=False)
    venue = P.ReplayVenue(fill_model="queue_aware")
    ladder = None

    fills: List[Dict[str, Any]] = []
    traj: List[Dict[str, Any]] = []          # (t, matched, residual, cash)
    t0 = None

    for row in rows:
        kind = row.get("kind")
        if kind == "book":
            ladder = row
            eng.ladder = row
            continue
        if kind != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue
        now = _t(row)
        if t0 is None and now is not None:
            t0 = now

        def _execute_taker(it):
            return it["qty"], it["cost"], it["fee"]

        before = dict(eng.bk.q)
        try:
            eng.on_book(row, ladder, execute_taker=_execute_taker)
        except E.StaleBook:
            continue
        if dict(eng.bk.q) != before:
            fills.append({"t": now, "how": "taker",
                          "up": str(eng.bk.q["up"]), "down": str(eng.bk.q["down"])})

        if eng.locked_at is None:
            for side in ("up", "down"):
                st = eng.resting.get(side)
                if not st:
                    continue
                got = venue.fills_for(eng, row, side, D(str(st["px"])),
                                      D(str(st["qty"])), ladder)
                if got is None:
                    continue
                qty, fpx = got
                if not eng._cap_permits(side, qty):
                    continue
                eng.on_fill(side, qty, qty * fpx, maker=True)
                fills.append({"t": now, "how": "maker", "side": side,
                              "qty": str(qty), "px": str(fpx)})

        up, dn = eng.bk.q["up"], eng.bk.q["down"]
        traj.append({"t": now, "matched": min(up, dn), "residual": up - dn,
                     "cash": sum(eng.bk.c.values(), D(0))})

    res = eng.settle(winner)
    t_end = traj[-1]["t"] if traj and traj[-1]["t"] is not None else None

    # ---- residual persistence: seconds spent with a non-zero residual,
    #      charged between consecutive observations.
    unmatched_s = 0.0
    spanned_s = 0.0
    peak_resid = D(0)
    peak_cash = D(0)
    for a, b in zip(traj, traj[1:]):
        if a["t"] is None or b["t"] is None:
            continue
        dtv = max(0.0, b["t"] - a["t"])
        spanned_s += dtv
        if a["residual"] != 0:
            unmatched_s += dtv
        peak_resid = max(peak_resid, abs(a["residual"]))
        peak_cash = max(peak_cash, a["cash"])
    if traj:
        peak_resid = max(peak_resid, abs(traj[-1]["residual"]))
        peak_cash = max(peak_cash, traj[-1]["cash"])

    up, dn = eng.bk.q["up"], eng.bk.q["down"]
    acquired = up + dn
    matched_legs = 2 * min(up, dn)
    return {
        "net": P.net_of(res),
        "matched_pnl": res.get("matched_pnl"),
        "resid_pnl": res.get("resid_pnl"),
        "fees": res.get("fees"),
        "completions": res.get("executable_states"),
        "up": up, "down": dn,
        "acquired_legs": acquired,
        "matched_legs": matched_legs,
        "completion_rate": (float(matched_legs) / float(acquired)
                            if acquired else None),
        "final_residual": up - dn,
        "fully_completed": (up == dn and acquired > 0),
        "traded": acquired > 0,
        "peak_residual": peak_resid,
        "peak_cash": peak_cash,
        "unmatched_s": round(unmatched_s, 1),
        "spanned_s": round(spanned_s, 1),
        "unmatched_frac": (round(unmatched_s / spanned_s, 4)
                           if spanned_s else None),
        "n_fills": len(fills),
        "t_first_fill": (fills[0]["t"] - t0
                         if fills and t0 is not None
                         and fills[0]["t"] is not None else None),
        "t_last_fill": (fills[-1]["t"] - t0
                        if fills and t0 is not None
                        and fills[-1]["t"] is not None else None),
        "window_s": (t_end - t0) if (t_end is not None and t0 is not None)
                    else None,
    }


def run(clip) -> Dict[str, Any]:
    from scripts.audit_economic_complete import load, USABLE
    by = load()
    per = []
    for slug, w in USABLE.items():
        r = walk(by[slug], winner=w, clip=clip)
        r["window"] = slug[-5:]
        r["winner"] = w
        per.append(r)

    traded = [r for r in per if r["traded"]]
    n = len(per) or 1
    nt = len(traded) or 1

    def mean(key, rows):
        vals = [r[key] for r in rows if r[key] is not None]
        return (sum(vals, D(0)) / len(vals)) if vals and isinstance(
            vals[0], D) else (sum(vals) / len(vals) if vals else None)

    rates = [r["completion_rate"] for r in traded
             if r["completion_rate"] is not None]
    return {
        "clip": str(clip),
        "windows": len(per),
        "windows_that_traded": len(traded),
        "mean_net": mean("net", per),
        "mean_matched_pnl": mean("matched_pnl", per),
        "mean_resid_pnl": mean("resid_pnl", per),
        "mean_fees": mean("fees", per),
        "total_completions": sum(int(r["completions"] or 0) for r in per),
        "completion_rate_mean": (sum(rates) / len(rates)) if rates else None,
        "fully_completed_windows": sum(1 for r in traded
                                       if r["fully_completed"]),
        "fully_completed_frac": (sum(1 for r in traded
                                     if r["fully_completed"]) / nt),
        "mean_unmatched_frac": mean("unmatched_frac", traded),
        "mean_unmatched_s": mean("unmatched_s", traded),
        "max_peak_residual": max((r["peak_residual"] for r in per),
                                 default=D(0)),
        "max_peak_cash": max((r["peak_cash"] for r in per), default=D(0)),
        "rebates_assumed": "NONE -- fees are the frozen charge only",
        "per_window": per,
    }


if __name__ == "__main__":
    out = {}
    for clip in (D(10), D(50)):
        out[str(clip)] = run(clip)
    print(json.dumps(out, indent=2, default=str))
