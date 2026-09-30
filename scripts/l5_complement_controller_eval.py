"""Baseline vs inventory-aware complement controller.

DEVELOPMENT COHORT: the frozen 25 windows only. The 49 out-of-sample windows
are held out and this module must be run with `--cohort oos` explicitly, once,
as the validation test.

Sourced executable incentives are reported on their OWN line and never inside
net. The only rate used is the one actually credited to this account:
`analysis/l5/venue_rebate_credits.json` -- 25 passiveExecution prints, 119.49
contracts, $0.42 -> $0.003515 per MAKER contract. Modeled rebates are not used.
"""
from __future__ import annotations

import json
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

from src import pmus_native_parity as P

#: credited, sourced, maker-only. Derived from the credit ledger, not chosen.
REBATE_JSON = "analysis/l5/venue_rebate_credits.json"


def sourced_rate() -> D:
    doc = json.load(open(REBATE_JSON))
    rows = doc["rows"] if isinstance(doc, dict) else doc
    qty = sum(D(str(r["qty"])) for r in rows)
    usd = sum(-D(str(r["our_commission"])) for r in rows)
    return usd / qty


def frozen_cohort():
    from scripts.audit_economic_complete import load, USABLE
    return load(), dict(USABLE)


def oos_cohort():
    import scripts.pmus_completeness_rule as CR
    from scripts.audit_economic_complete import USABLE
    by = CR.load_tape("data/pmus_btc15m_tape_oos/oos_tape_ordered.jsonl")
    win = json.load(open("analysis/l5/r7_oos_settlement_authoritative.json"))
    win = win["winners"]
    coh = {s: win[s] for s in sorted(by)
           if CR.evaluate(s, by[s]).get("tape_test_passes")
           and s not in USABLE and s in win}
    return by, coh


def arm(by, coh, *, clip: int, controller: bool, rate: D) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for slug, winner in coh.items():
        r = P.replay_window(by[slug], winner=winner, clip=D(str(clip)),
                            complement_controller=controller)
        net = P.net_of(r)
        mk = D(str(r["maker_qty"]))
        rows.append({
            "window": slug[-5:], "net": net,
            "matched_pnl": D(str(r["matched_pnl"])),
            "resid_pnl": D(str(r["resid_pnl"])),
            "fees": D(str(r["fees"])),
            "matched_qty": D(str(r["matched_qty"])),
            "resid_qty": D(str(r["resid_qty"])),
            "maker_qty": mk, "taker_qty": D(str(r["taker_qty"])),
            "incentive": mk * rate,
            "realistic_net": net + mk * rate})
    n = len(rows)
    tot = lambda k: sum(x[k] for x in rows)          # noqa: E731
    nets = sorted(x["net"] for x in rows)
    rnets = sorted(x["realistic_net"] for x in rows)
    mq, rq = tot("matched_qty"), tot("resid_qty")
    return {
        "n": n, "clip": clip, "controller": controller,
        "mean_net": tot("net") / n,
        "median_net": nets[n // 2] if n % 2 else (nets[n//2-1]+nets[n//2])/2,
        "mean_matched_pnl": tot("matched_pnl") / n,
        "mean_resid_pnl": tot("resid_pnl") / n,
        "mean_fees": tot("fees") / n,
        "mean_incentive": tot("incentive") / n,
        "mean_realistic_net": tot("realistic_net") / n,
        "median_realistic_net": (rnets[n // 2] if n % 2
                                 else (rnets[n//2-1]+rnets[n//2])/2),
        "worst": nets[0], "best": nets[-1],
        "worst_realistic": rnets[0],
        "losing": sum(1 for x in rows if x["net"] < 0),
        "losing_realistic": sum(1 for x in rows if x["realistic_net"] < 0),
        "ge5": sum(1 for x in rows if x["net"] >= 5),
        "ge5_realistic": sum(1 for x in rows if x["realistic_net"] >= 5),
        "completion_rate": (2 * mq / (2 * mq + rq)) if (mq or rq) else None,
        "fully_completed": sum(1 for x in rows if x["resid_qty"] == 0),
        "zero_matched": sum(1 for x in rows if x["matched_qty"] == 0),
        "rows": rows}


def show(a: Dict[str, Any], b: Dict[str, Any]) -> None:
    f = lambda v: ("%+.4f" % float(v)) if v is not None else "n/a"  # noqa: E731
    print("  %-26s %14s %14s" % ("", "BASELINE", "CONTROLLER"))
    for label, key in (
            ("mean net", "mean_net"), ("median net", "median_net"),
            ("matched-pair P&L/win", "mean_matched_pnl"),
            ("residual P&L/win", "mean_resid_pnl"),
            ("fees/win", "mean_fees"),
            ("sourced incentive/win", "mean_incentive"),
            ("REALISTIC net/win", "mean_realistic_net"),
            ("REALISTIC median", "median_realistic_net"),
            ("worst window", "worst"),
            ("worst realistic", "worst_realistic"),
            ("best window", "best")):
        print("  %-26s %14s %14s" % (label, f(a[key]), f(b[key])))
    for label, key in (("losing windows", "losing"),
                       ("losing (realistic)", "losing_realistic"),
                       (">=$5 windows", "ge5"),
                       (">=$5 (realistic)", "ge5_realistic"),
                       ("fully completed", "fully_completed"),
                       ("zero-matched windows", "zero_matched")):
        print("  %-26s %14s %14s" % (label, "%d/%d" % (a[key], a["n"]),
                                     "%d/%d" % (b[key], b["n"])))
    print("  %-26s %14s %14s" % ("completion rate",
                                 "%.4f" % float(a["completion_rate"]),
                                 "%.4f" % float(b["completion_rate"])))


if __name__ == "__main__":
    which = "oos" if "--cohort=oos" in sys.argv else "frozen"
    clips = [int(x) for x in sys.argv[1:] if x.isdigit()] or [10]
    by, coh = oos_cohort() if which == "oos" else frozen_cohort()
    rate = sourced_rate()
    print("COHORT %s  n=%d  sourced maker rebate $%.6f/contract"
          % (which, len(coh), rate), flush=True)
    doc = {"cohort": which, "sourced_rate": str(rate), "arms": []}
    for clip in clips:
        a = arm(by, coh, clip=clip, controller=False, rate=rate)
        b = arm(by, coh, clip=clip, controller=True, rate=rate)
        print("\n=== clip q%d ===" % clip, flush=True)
        show(a, b)
        for x in (a, b):
            doc["arms"].append({k: (str(v) if isinstance(v, D) else v)
                                for k, v in x.items() if k != "rows"})
            doc["arms"][-1]["rows"] = [
                {kk: str(vv) for kk, vv in r.items()} for r in x["rows"]]
    out = "analysis/l5/r8_complement_controller_%s.json" % which
    json.dump(doc, open(out, "w"), indent=1)
    print("\nWROTE " + out, flush=True)
