"""What a first-leg fill has been worth on THIS venue, and what pricing the
first opening would have done.

Every figure is from this account's venue-settled BTC15m markets
(data/l5/recent/wide_20261111/settled.json, read back from the service's
own L5_LIVE_SETTLED lines, 2026-10-25 to 2026-11-11). Nothing is modelled:
no book, no queue, no fill assumption, no fee or rebate beyond what the
venue charged or credited (zero on every fill).

A "first-leg contract" is a contract that took a first fill: it either
became half of a pair (matched) or stayed unmatched to settlement.

  P(pair | first fill)   matched contracts / (matched + unmatched)
  pair net               matched P&L / matched contracts
  unmatched net          unmatched P&L / unmatched contracts

NOT IDENTIFIED HERE, and stated rather than guessed: P(first-leg fill) --
the settled record holds fills, not the orders that never filled -- and
P(PROFITABLE completion | first fill) per order, because the ledger keeps
a window's pair cost, not each completion's.

THE WALK-FORWARD at the end replays `L5_TS_FIRST_OPENING_PRICED` in time
order: before each window it computes, from the windows settled BEFORE it
only, the figure the engine's own-settled-record test uses, and records
whether that window's first opening would have been refused.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from src import l5_entry_economics as EE          # noqa: E402
from src import l5_unmatched_disposition as UD    # noqa: E402

DATA = os.path.join(HERE, "..", "..", "data", "l5", "recent", "wide_20261111")


def rows() -> List[Dict[str, Any]]:
    s = json.load(open(os.path.join(DATA, "settled.json")))
    out = []
    for k in sorted(s):
        r = s[k]
        if not k or "cpc-btc-updown-15m" not in k:
            continue
        up, dn = D(str(r["up_qty"])), D(str(r["dn_qty"]))
        if up + dn == 0:
            continue
        cost = D(str(r["cost_total"]))
        out.append({
            "slug": k, "up": up, "dn": dn,
            "matched": D(str(r.get("matched_qty") or 0)),
            "unmatched": D(str(r.get("residual_qty") or 0)),
            "matched_pnl": D(str(r.get("matched_pnl") or 0)),
            "unmatched_pnl": D(str(r.get("residual_pnl") or 0)),
            "pnl": D(str(r["settlement_pnl"])),
            # booked at the $1/share bound: no measured cost
            "measured": cost < (up + dn) * D("0.99")})
    return out


def cohort(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
    m = sum(r["matched"] for r in rs)
    u = sum(r["unmatched"] for r in rs)
    if m + u == 0 or m == 0 or u == 0:
        return {"n": len(rs), "status": "UNKNOWN"}
    p = m / (m + u)
    gain = sum(r["matched_pnl"] for r in rs) / m
    loss = sum(r["unmatched_pnl"] for r in rs) / u
    ev = EE.evaluate_opening(
        completed_net_per_contract=gain, residual_net_per_contract=loss,
        completion_probability_given_first_fill=p,
        minimum_net_edge_per_contract=UD.REOPEN_MIN_EDGE_USD)
    return {"n": len(rs), "first_leg_contracts": str(m + u),
            "p_pair_given_first_fill": str(p.quantize(D("0.001"))),
            "pair_net": str(gain.quantize(D("0.0001"))),
            "unmatched_net": str(loss.quantize(D("0.0001"))),
            "ev_per_first_leg_contract": str(D(
                ev["conditional_expected_net_per_contract"]).quantize(
                    D("0.0001"))) if ev["status"] != "UNKNOWN" else None,
            "break_even_p_pair": (str(D(
                ev["break_even_completion_probability"]).quantize(
                    D("0.001"))) if ev["status"] != "UNKNOWN" else None),
            "clears_the_floor": ev.get("scenario_clears_edge"),
            "settled_total": str(sum(r["pnl"] for r in rs))}


def walk_forward(rs: List[Dict[str, Any]], last_n: int = 50
                 ) -> Dict[str, Any]:
    refused, opened, undecided = [], [], []
    for i, r in enumerate(rs):
        prior = rs[max(0, i - last_n):i]
        if len(prior) < UD.OWN_EVIDENCE_MIN_WINDOWS:
            undecided.append(r)
            continue
        contracts = sum(max(x["up"], x["dn"]) for x in prior)
        per = sum(x["pnl"] for x in prior) / contracts
        # the engine's test, per contract: q * per > floor * q
        (opened if per > UD.REOPEN_MIN_EDGE_USD else refused).append(r)

    def tot(xs: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"windows": len(xs),
                "settled": str(sum(x["pnl"] for x in xs)),
                "profitable_windows": sum(x["pnl"] > 0 for x in xs),
                "losing_windows": sum(x["pnl"] < 0 for x in xs),
                "profit_in_profitable": str(sum(
                    x["pnl"] for x in xs if x["pnl"] > 0)),
                "loss_in_losing": str(sum(
                    x["pnl"] for x in xs if x["pnl"] < 0))}
    return {"too_early_to_decide": tot(undecided),
            "would_have_opened": tot(opened),
            "would_have_refused": tot(refused)}


def main() -> None:
    allr = rows()
    rs = [r for r in allr if r["measured"]]
    h = len(rs) // 2
    out = {"all_settled_including_bound_rows": cohort(allr),
           "measured": cohort(rs), "earlier_half": cohort(rs[:h]),
           "later_half": cohort(rs[h:]),
           "the_gate_today": {
               "p_pair": str(UD.REOPEN_P_PAIR),
               "unmatched_net": str(UD.REOPEN_SINGLE_OUTCOME_USD),
               "source": ("pairing from 11 of our own rounds; unmatched "
                          "value from L5's Polymarket fills")},
           "walk_forward": walk_forward(rs)}
    for k in ("all_settled_including_bound_rows", "measured",
              "earlier_half", "later_half"):
        c = out[k]
        print("%-34s n %3d  P(pair|first fill) %s  pair %s  unmatched %s  "
              "EV/contract %s  break-even P %s  clears floor %s  settled %s"
              % (k, c["n"], c.get("p_pair_given_first_fill"),
                 c.get("pair_net"), c.get("unmatched_net"),
                 c.get("ev_per_first_leg_contract"),
                 c.get("break_even_p_pair"), c.get("clears_the_floor"),
                 c.get("settled_total")))
    w = out["walk_forward"]
    for k in ("too_early_to_decide", "would_have_opened",
              "would_have_refused"):
        print("walk-forward %-20s %s" % (k, json.dumps(w[k])))
    with open(os.path.join(HERE, "opening_economics_20261111.json"), "w") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
