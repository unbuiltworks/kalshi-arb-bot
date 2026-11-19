"""L5's own fill stream through OUR portfolio controller.

OFFLINE. Reads L5's venue-confirmed BTC15m fills and redemptions for the 118
windows settled between 2026-11-09 12:50Z and 2026-11-10 19:30Z
(`data/l5/recent/btc15m_stream_20261110.json`). Places nothing.

THE QUESTION (owner, 2026-11-10). How does L5 reach a low aggregate pair
cost, and what is the smallest change that lets our existing controller
keep the matched-inventory profit while controlling the residual?

THE METHOD. Each of L5's fills is treated as an OFFER: that side, at that
price, for that size, at that moment. The PRODUCTION decision function
(`src.l5_portfolio_controller.decide`) is asked, on OUR running inventory
and a one-tick book around his print, whether it would be quoting that
side. If it would, we take the offer; if not, we skip it. Settlement is the
venue's own redemption.

WHAT THIS ASSUMES, AND IT IS A LARGE ASSUMPTION: that we could have had the
fills L5 had -- his queue position, his venue, his size. It answers "which
of HIS fills should a controller take", not "which fills would WE get". On
PMUS's own book with conservative trade-through fills the same rule is
still negative (see L5_STREAM_REPLAY.md). Only our own venue fills can say
which world we are in.

WHY "AGGREGATE PAIR COST" IS NOT THE TARGET. It is average UP cost plus
average DOWN cost over EVERYTHING bought, so cheap contracts on the losing
side pull it down while losing money. The money is the sum over contracts
of (settlement - price); that is what is reported.
"""
from __future__ import annotations

import json
import os
import random
import statistics as st
from typing import Any, Callable, Dict, List, Optional, Sequence

from src import l5_portfolio_controller as PC

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "data", "l5",
                    "recent", "btc15m_stream_20261110.json")
UP, DOWN = PC.UP, PC.DOWN
BIG = PC.Limits(cash_usd=1e12, exposure_usd=1e12, max_orders=2)
SIDE = {"Up": UP, "Down": DOWN}


def load(path: str = DATA) -> List[Dict[str, Any]]:
    with open(path) as fh:
        return json.load(fh)["windows"]


def book_for(side: str, px: float) -> Dict[str, float]:
    """A one-tick UP book in which his fill price is that side's best bid."""
    if side == UP:
        bid = round(min(0.98, max(0.01, px)), 2)
        return {"bid": bid, "ask": round(bid + 0.01, 2)}
    ask = round(min(0.99, max(0.02, 1.0 - px)), 2)
    return {"bid": round(ask - 0.01, 2), "ask": ask}


def as_traded(**_kw: Any) -> float:
    return 1e12


def controller(params: PC.Params) -> Callable[..., float]:
    """Take an offer when the production controller would be quoting it."""
    def rule(*, t: float, side: str, px: float, port: Dict[str, Any],
             **_kw: Any) -> float:
        bk = book_for(side, px)
        dec = PC.decide(port, bid=bk["bid"], ask=bk["ask"], p_up=None,
                        t_off=t, params=params, limits=BIG)
        # quoting that side AT his price or better: a quote resting below
        # the price he paid would not have had his fill
        return 1e12 if any(q["side"] == side and q["price"] >= px - 1e-9
                           for q in dec["quotes"]) else 0.0
    return rule


def play(window: Dict[str, Any], rule: Callable[..., float]
         ) -> Dict[str, Any]:
    q = {UP: 0.0, DOWN: 0.0}
    c = {UP: 0.0, DOWN: 0.0}
    peak = 0.0
    took = 0
    for t, outcome, px, size in window["f"]:
        side = SIDE[outcome]
        port = PC.portfolio(up_qty=q[UP], up_cost=c[UP], dn_qty=q[DOWN],
                            dn_cost=c[DOWN])
        if rule(t=t, side=side, px=px, size=size, port=port) <= 0:
            continue
        q[side] += size
        c[side] += px * size
        took += 1
        peak = max(peak, abs(q[UP] - q[DOWN]))
    cost = c[UP] + c[DOWN]
    win = SIDE[window["winner"]]
    return {
        "net": q[win] - cost, "contracts": q[UP] + q[DOWN], "cost": cost,
        "fills": took, "matched": min(q[UP], q[DOWN]),
        "residual": abs(q[UP] - q[DOWN]), "peak_residual": peak,
        "residual_net": (abs(q[UP] - q[DOWN])
                         * ((1.0 if (q[UP] > q[DOWN]) == (win == UP) else 0.0)
                            - (c[UP] / q[UP] if q[UP] > q[DOWN]
                               else c[DOWN] / q[DOWN]))
                         if q[UP] != q[DOWN] else 0.0),
        "pair_cost": ((c[UP] / q[UP] + c[DOWN] / q[DOWN])
                      if q[UP] and q[DOWN] else None),
        "residual_won": ((q[UP] > q[DOWN]) == (win == UP)
                         if q[UP] != q[DOWN] else None)}


def _boot(rs: Sequence[Dict[str, Any]], den: Optional[str], n: int = 3000,
          seed: int = 3) -> List[float]:
    rng = random.Random(seed)
    k = len(rs)
    out = []
    for _ in range(n):
        a = b = 0.0
        for _i in range(k):
            x = rs[rng.randrange(k)]
            a += x["net"]
            b += x[den] if den else 1.0
        if b:
            out.append(a / b)
    out.sort()
    return [out[int(0.025 * len(out))], out[int(0.975 * len(out)) - 1]]


def summarise(rs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    tot = sum(r["contracts"] for r in rs)
    net = sum(r["net"] for r in rs)
    pcs = [r["pair_cost"] for r in rs if r["pair_cost"]]
    won = [r["residual_won"] for r in rs if r["residual_won"] is not None]
    matched_net = sum(r["net"] - r["residual_net"] for r in rs)
    return {
        "windows": len(rs), "contracts_per_window": tot / len(rs),
        "fills_per_window": sum(r["fills"] for r in rs) / len(rs),
        "net_total": net,
        "net_per_contract": (net / tot) if tot else None,
        "net_per_contract_ci95": _boot(rs, "contracts") if tot else None,
        "net_per_window": net / len(rs),
        "net_per_window_ci95": _boot(rs, None),
        "matched_share": (2 * sum(r["matched"] for r in rs) / tot) if tot
        else None,
        "matched_net_total": matched_net,
        "residual_net_total": sum(r["residual_net"] for r in rs),
        "aggregate_pair_cost_median": st.median(pcs) if pcs else None,
        "peak_residual_median": st.median(r["peak_residual"] for r in rs),
        "peak_residual_max": max(r["peak_residual"] for r in rs),
        "residual_on_winner_share": (sum(won) / len(won)) if won else None,
        "worst_window": min(r["net"] for r in rs),
    }


def candidates() -> Dict[str, Callable[..., float]]:
    bal = PC.Params(**PC.BALANCE).checked()
    return {
        "L5 as traded": as_traded,
        "controller, MODEL_EDGE profile (current)":
            controller(PC.Params(**PC.PRODUCTION).checked()),
        "controller, BALANCE profile": controller(bal),
        "BALANCE without the 0.30 floor":
            controller(PC.with_overrides(bal, add_floor=0.0)),
        "BALANCE without late hedging (stop at 600s)":
            controller(PC.with_overrides(bal, complete_cutoff_s=600.0)),
        "BALANCE with a 1.00 pair ceiling on hedges":
            controller(PC.with_overrides(bal, pair_ceiling=1.00)),
        "BALANCE with a 0.96 pair ceiling on hedges":
            controller(PC.with_overrides(bal, pair_ceiling=0.96)),
        "BALANCE without balance-first (no residual cap)":
            controller(PC.with_overrides(bal, balance_first=False,
                                         max_unmatched=1e9)),
    }


def run() -> Dict[str, Any]:
    wins = load()
    cut = int(len(wins) * 0.6)
    halves = {"IS": wins[:cut], "OOS": wins[cut:], "ALL": wins}
    res: Dict[str, Any] = {
        "evidence_class": ("L5's VENUE-CONFIRMED Polymarket fills and "
                           "redemptions, taken or skipped by our controller "
                           "-- assumes we could have had his fills; not "
                           "PMUS fills and not our own"),
        "windows": {h: len(v) for h, v in halves.items()}, "rows": {},
        "vs_as_traded": {}}
    base = {h: [play(w, as_traded) for w in ws] for h, ws in halves.items()}
    for name, rule in candidates().items():
        res["rows"][name] = {}
        res["vs_as_traded"][name] = {}
        for h, ws in halves.items():
            rs = [play(w, rule) for w in ws]
            res["rows"][name][h] = summarise(rs)
            d = [{"net": a["net"] - b["net"]} for a, b in zip(rs, base[h])]
            res["vs_as_traded"][name][h] = {
                "mean": sum(x["net"] for x in d) / len(d),
                "ci95": _boot(d, None)}
    return res


def _f(x: Any, w: int = 8, p: int = 4) -> str:
    return (" " * (w - 3) + "n/a") if x is None else ("%+*.*f" % (w, p, x))


def main() -> None:
    r = run()
    with open(os.path.join(HERE, "l5_stream_replay.json"), "w") as fh:
        json.dump(r, fh, indent=1, sort_keys=True)
    print("evidence:", r["evidence_class"])
    for half in ("OOS", "IS", "ALL"):
        print("== %s (%d windows) ==" % (half, r["windows"][half]))
        for name, by in r["rows"].items():
            s = by[half]
            ci = s["net_per_contract_ci95"] or [0.0, 0.0]
            wci = s["net_per_window_ci95"]
            print("%-46s ctr/win %6.0f  $/ctr %s [%+.4f, %+.4f]  $/win "
                  "%+7.2f [%+7.2f, %+7.2f]  matched %3.0f%% (%+8.0f) "
                  "residual %+8.0f  pairC %s  peak %4.0f  worst %+8.2f" % (
                      name, s["contracts_per_window"],
                      _f(s["net_per_contract"]), ci[0], ci[1],
                      s["net_per_window"], wci[0], wci[1],
                      100 * (s["matched_share"] or 0),
                      s["matched_net_total"], s["residual_net_total"],
                      ("%.3f" % s["aggregate_pair_cost_median"])
                      if s["aggregate_pair_cost_median"] else " n/a ",
                      s["peak_residual_median"], s["worst_window"]))


if __name__ == "__main__":
    main()
