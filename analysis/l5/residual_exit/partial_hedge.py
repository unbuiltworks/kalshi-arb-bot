"""Does PARTIAL hedging of a stranded leg beat holding it, or fully exiting it?

THE QUESTION, and why it is not the same as the dwell gate's. `dwell_validation`
asks WHEN to offset a stranded leg and offsets ALL of it. The owner's proposal
is to offset only PART, keeping some directional exposure, on the reasoning that
a partial hedge "reduces downside without requiring the entire pair to complete".

REUSE, NOT REBUILD. Every economic primitive here is imported from
`dwell_validation` unchanged -- the loader, the eligibility rule, the as-of-tick
state reconstruction, the DWELL trigger, the PMUS fee curve, the sell-at-the-bid
assumption and the chronological train/OOS split. Nothing is retuned. The only
new thing is the hedge FRACTION.

WHAT A PARTIAL HEDGE PAYS, per unit of stranded inventory. Hedging fraction f
at the bid and carrying (1-f) to settlement realizes

    net(f) = f * (exit_price - cost - taker_fee) + (1 - f) * (settle - cost)

which is LINEAR in f. So the mean over any set of windows is linear in f too,
and a linear function on [0, 1] takes its maximum at an endpoint. That is an
identity, not a finding: under these settlement mechanics NO interior hedge
fraction can beat the better of {hold, full exit} on expected net. What an
interior f can do is buy a tighter loss tail at a proportional cost in mean,
and that trade is what this script measures rather than asserts.

THE ONE MECHANISM THAT COULD BREAK THE LINEARITY is execution: a smaller clip
may fill where a full-size one would not. This harness assumes the full size
fills at the bid, so it CANNOT see that effect, and no claim about it is made
here. It is called out in the report as the open question it is.

No network, no venue, no orders, no limit changed, no live behaviour altered.
"""
from __future__ import annotations

import json
import os
import statistics as st
from decimal import Decimal as D
from typing import Any, Dict, Sequence

from analysis.l5.residual_exit import dwell_validation as DV

#: hedge fractions scored. 0.0 is HOLD and 1.0 is the full DWELL exit, both
#: included so the linearity is visible rather than claimed.
FRACTIONS = (0.0, 0.25, 0.50, 0.75, 1.0)


def walk_partial(rows: Sequence, won_side: str, terminal: float,
                 frac: float, dwell_s: float = DV.DWELL_S) -> dict:
    """One window, one hedge fraction. The DWELL trigger decides WHEN; `frac`
    decides HOW MUCH. Reuses DV.walk for the trigger and both payoffs, so the
    partial can never disagree with the endpoints by construction."""
    r = DV.walk(rows, won_side, terminal, "DWELL", dwell_s=dwell_s)
    if not r:
        return {}
    hold_net = r["hold_net"]
    if not r.get("exited"):
        # nothing was offerable, so every fraction realizes the same thing
        return {"exited": False, "net": hold_net, "hold_net": hold_net,
                "frac": frac, "opportunity_cost": 0.0,
                "hedged_units": 0.0, "qty": r.get("qty"),
                "cost": r.get("cost")}
    exit_net = r["net"]
    net = frac * exit_net + (1.0 - frac) * hold_net
    return {"exited": frac > 0.0, "net": net, "hold_net": hold_net,
            "exit_net_full": exit_net, "frac": frac,
            "exit_t": r.get("exit_t"), "exit_pair": r.get("exit_pair"),
            "opportunity_cost": max(0.0, hold_net - net),
            "hedged_units": frac,
            "maker_became_reachable_after_exit":
                r.get("maker_became_reachable_after_exit"),
            "qty": r.get("qty"), "cost": r.get("cost")}


def _stats(rs: Sequence[dict]) -> Dict[str, Any]:
    rs = [r for r in rs if r]
    nets = [r["net"] for r in rs]
    m, lo, hi = DV._ci(nets)
    s = sorted(nets)
    return {
        "n": len(rs),
        "mean_net_per_unit": round(m, 5),
        "ci95": [round(lo, 5), round(hi, 5)],
        "median": round(st.median(nets), 5),
        "p05": round(s[max(0, round(0.05 * (len(s) - 1)))], 5),
        "worst": round(s[0], 5),
        "stdev": round(st.pstdev(nets), 5) if len(nets) > 1 else None,
        "frac_below_-0.30": round(sum(1 for x in nets if x < -0.30)
                                  / len(nets), 4),
        "frac_below_-0.15": round(sum(1 for x in nets if x < -0.15)
                                  / len(nets), 4),
        "mean_opportunity_cost": round(
            st.mean([r.get("opportunity_cost", 0.0) for r in rs]), 5),
    }


def evaluate(minutes: str, terminal: float,
             train_frac: float = 0.70) -> Dict[str, Any]:
    outc, seq = DV.load(minutes)
    elig = [w for w in sorted(seq)
            if DV.eligible(seq[w], terminal) is not None]
    k = int(len(elig) * train_frac)
    out: Dict[str, Any] = {
        "minutes": minutes, "terminal_s": terminal,
        "windows_eligible": len(elig),
        "train_eligible": k, "oos_eligible": len(elig) - k,
        "eligibility": ("inherited verbatim from dwell_validation.eligible: "
                        "a leg became unmatched and the profitable completion "
                        "was not already reachable at that moment"),
        "fractions": list(FRACTIONS),
        "splits": {},
    }
    for name, pool in (("train", elig[:k]), ("oos", elig[k:])):
        runs = {f: [walk_partial(seq[w], outc[w], terminal, f) for w in pool]
                for f in FRACTIONS}
        res: Dict[str, Any] = {str(f): _stats(runs[f]) for f in FRACTIONS}
        # PAIRED against HOLD (f = 0), the only honest comparison: the same
        # window, the same path, the same settlement.
        res["paired_vs_hold"] = {}
        base = {w: r for w, r in zip(pool, runs[0.0]) if r}
        for f in FRACTIONS[1:]:
            cur = {w: r for w, r in zip(pool, runs[f]) if r}
            ks = sorted(set(base) & set(cur))
            d = [cur[w]["net"] - base[w]["net"] for w in ks]
            if len(d) < 2:
                continue
            m, lo, hi = DV._ci(d)
            sd = st.pstdev(d)
            res["paired_vs_hold"][str(f)] = {
                "n": len(d), "mean": round(m, 5),
                "ci95": [round(lo, 5), round(hi, 5)],
                "t": round(m / (sd / len(d) ** 0.5), 3) if sd > 0 else None,
                "significant_at_95": bool(lo > 0 or hi < 0),
                "better_in_frac": round(
                    sum(1 for x in d if x > 0) / len(d), 4),
            }
        # the linearity CHECK: mean(f) must equal (1-f)*mean(0) + f*mean(1)
        m0 = res[str(0.0)]["mean_net_per_unit"]
        m1 = res[str(1.0)]["mean_net_per_unit"]
        res["linearity_check"] = {
            str(f): {"observed": res[str(f)]["mean_net_per_unit"],
                     "predicted_linear": round((1 - f) * m0 + f * m1, 5)}
            for f in FRACTIONS}
        out["splits"][name] = res
    return out


def report() -> Dict[str, Any]:
    return {
        "btc15m_like": evaluate("15", 780.0),
        "assumptions": dict(
            inherited_from="analysis/l5/residual_exit/dwell_validation.py",
            trigger="DWELL (30s after the leg went off flat, to the terminal)",
            hedge_fill="the full hedged fraction is ASSUMED to fill at the "
                       "bid; clip-size effects on fill probability are NOT "
                       "modelled and no claim is made about them",
            payoff="net(f) = f*(bid - cost - taker_fee) + (1-f)*(settle - cost)",
            linearity="net is linear in f by construction, so the mean is "
                      "maximised at f=0 or f=1; an interior f trades mean "
                      "for tail",
            split="chronological, 70/30, never shuffled",
            lookahead="side, size and cost basis are as-of-tick; only the "
                      "settlement outcome comes from window end",
        ),
    }


def main() -> int:
    out = report()
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "partial_hedge_btc15m.json")
    with open(p, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(json.dumps(out["btc15m_like"]["splits"]["oos"], indent=2,
                     sort_keys=True))
    print("written", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
