#!/usr/bin/env python3
"""Why did a window end with an UNMATCHED leg? Read-only, from the tapes.

THE QUESTION. Matched pairs carry essentially all of the expectancy
(+$0.1933/window, t=3.32 over 17 settled windows) and residual carries
essentially all of the variance (+$0.0303/window against sd 0.6183, t=0.12).
So the thing worth buying is COMPLETED PAIRS, and the thing worth not paying
for is an unmatched leg carried into settlement.

This does not ask whether quotes should rest longer -- that was answered NOT
SUPPORTED, with a settled break-even completion pair cost of 0.9532 against
an achieved 0.9530, a dead heat. It asks the prior question: when a leg was
left unmatched, HAD WE EVEN QUOTED THE OTHER SIDE?

A window that strands with zero completion submissions did not lose a race.
It never entered one.

Classification is from the tape alone:
  * inventory is rebuilt fill-by-fill in UTC order, per window;
  * a submission whose side OPPOSES the net inventory at the moment it was
    sent is a COMPLETION quote; anything else is an OPEN or an EXTEND;
  * a window ends STRANDED if net inventory is non-zero at the last event.

`qty` is in contracts, prices are own-side. Nothing here is a fill that did
not happen: every number comes from a transmitted order or a venue fill.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[3]
CAMPAIGNS = ("campaign6", "campaign7")


def rows(camp: str, name: str):
    p = ROOT / "analysis" / "l5" / camp / ("%s.jsonl" % name)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def signed(side: str, qty: D) -> D:
    """UP is +, DOWN is -. A matched pair sums to zero."""
    return qty if str(side).upper() == "UP" else -qty


def main() -> int:
    report = {}
    for camp in CAMPAIGNS:
        subs = rows(camp, "tape_submits")
        fills = {r["coid"]: r for r in rows(camp, "tape_fills")}
        if not subs:
            continue
        by_coid = {r["coid"]: r for r in subs}
        # one timeline per window, submissions and fills interleaved by utc
        events = []
        for r in subs:
            events.append((r["utc"], "SUB", r))
        for coid, f in fills.items():
            s = by_coid.get(coid)
            if s is not None:
                events.append((f["utc"], "FILL", {"coid": coid,
                                                  "side": s["side"],
                                                  "qty": f["qty"],
                                                  "window": s["window"],
                                                  "own_px": s["own_px"]}))
        events.sort(key=lambda e: e[0])

        inv = defaultdict(lambda: D("0"))
        w = defaultdict(lambda: {"open": 0, "complete": 0, "fills": 0,
                                 "completion_fills": 0,
                                 "own_cost": D("0")})
        for utc, kind, r in events:
            win = r["window"]
            cur = inv[win]
            if kind == "SUB":
                s = signed(r["side"], D("1"))
                # opposing the net inventory is the only thing that can
                # close a pair; with flat inventory nothing can.
                is_completion = cur != 0 and ((cur > 0) != (s > 0))
                w[win]["complete" if is_completion else "open"] += 1
            else:
                q = D(str(r["qty"]))
                s = signed(r["side"], q)
                was_completion = cur != 0 and ((cur > 0) != (s > 0))
                inv[win] = cur + s
                w[win]["fills"] += 1
                if was_completion:
                    w[win]["completion_fills"] += 1
                w[win]["own_cost"] += q * D(str(r["own_px"]))

        out = {}
        for win in sorted(w):
            net = inv[win]
            d = w[win]
            out[win] = {
                "submissions_open_or_extend": d["open"],
                "submissions_completion": d["complete"],
                "fills": d["fills"],
                "completion_fills": d["completion_fills"],
                "net_contracts_at_close": str(net),
                "stranded": net != 0,
                # the loss a stranded leg can take is bounded by what it
                # cost: a settled losing leg returns $0.
                "stranded_abs_contracts": str(abs(net)),
            }
        report[camp] = out

    # ---- the finding, across campaigns
    stranded = [(c, w, d) for c, ws in report.items()
                for w, d in ws.items() if d["stranded"]]
    starved = [x for x in stranded if x[2]["submissions_completion"] == 0]
    print(json.dumps({
        "windows": report,
        "totals": {
            "windows": sum(len(ws) for ws in report.values()),
            "stranded_windows": len(stranded),
            "stranded_with_zero_completion_quotes": len(starved),
            "starved_windows": ["%s/%s" % (c, w) for c, w, _ in starved],
            "stranded_contracts_starved": str(
                sum(D(x[2]["stranded_abs_contracts"]) for x in starved)),
            "stranded_contracts_contested": str(
                sum(D(x[2]["stranded_abs_contracts"])
                    for x in stranded
                    if x[2]["submissions_completion"] > 0)),
        },
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
