"""Each fill judged on its own, held to settlement, apart from pairing.

For every fill in fills_context.json: what that one purchase was worth at
settlement (the side's final value minus the price paid, times quantity),
grouped by what the fill was for: new exposure (open / extend) or
completion. Pairing does not enter: a completion is scored as the
stand-alone purchase it also is.

Outcomes are the venue's settlement as booked by the account ledger
(yes value 1 or 0). A market with no outcome on record is left out and
listed. Run: python3 settlement_by_role.py
"""
import json

#: slug -> final value of YES. Sources: ledger L5_LIVE_SETTLED lines
#: (robstr-tiaper 17:59:47Z yes_value 1.0; gabdeb-sankop 20:06:36Z
#: yes_value 1.0) and the 22 Nov campaign report (NO won).
YES_VALUE = {
    "aec-nba-det-atl-2026-11-22": 0.0,
    "aec-atp-robstr-tiaper-2026-11-23": 1.0,
    "aec-atp-gabdeb-sankop-2026-11-23": 1.0,
}

F = json.load(open("fills_context.json"))
tot = {"new": [0.0, 0.0, 0], "complete": [0.0, 0.0, 0]}
missing = []
print("%-9s %-34s %-4s %-5s %-4s %-9s %8s" % (
    "time", "market", "side", "px", "qty", "role", "result"))
for r in F:
    role = str(r.get("role") or r.get("stage") or "")
    kind = "new" if ("open" in role or "extend" in role) else "complete"
    px, qty = float(r["px"]), float(r["qty"])
    yv = YES_VALUE.get(r["slug"])
    if yv is None:
        missing.append((r["slug"], r["side"], px, qty, kind))
        continue
    val = yv if r["side"] == "YES" else 1.0 - yv
    res = (val - px) * qty
    tot[kind][0] += res
    tot[kind][1] += px * qty
    tot[kind][2] += 1
    print("%-9s %-34s %-4s %-5.2f %-4g %-9s %+8.2f" % (
        str(r["fill_time"])[11:19], r["slug"][4:], r["side"], px, qty,
        kind, res))
print()
for k, (res, cost, n) in tot.items():
    print("%-9s fills %d  cost $%.2f  held-to-settlement result %+.2f "
          "(%+.1f%% of cost)" % (k, n, cost, res,
                                 100 * res / cost if cost else 0))
print("both      result %+.2f" % (tot["new"][0] + tot["complete"][0]))
if missing:
    print("\nno outcome on record, left out:")
    for m in missing:
        print("  ", m)
print("\nEach fill is close to a fair-odds bet of about $1, so one result "
      "swings by about $1 either way.\nWith %d fills the totals above are "
      "noise: they cannot show an edge or a loss of a few cents per "
      "contract." % sum(v[2] for v in tot.values()))
