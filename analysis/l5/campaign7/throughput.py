#!/usr/bin/env python3
"""What the one-resting-order limit costs, measured not assumed.

`L5_MAX_LIVE_ORDERS=1` is both a throughput limit and a blast-radius
control. This measures the throughput half from Campaign 6's own tapes so
the trade can be argued from numbers.

THREE SEPARATE COSTS, and they are not interchangeable:

  BLOCKED SECOND LEG. The planner builds a PAIR and wants both legs resting
  at the plan's prices. With one slot it must place leg 1, wait, then price
  leg 2 against whatever book exists by then. 14 of Campaign 6's 23
  MAX_LIVE_ORDERS refusals are exactly this: decision_role FLAT_OPEN,
  legs_claimed {UP:false, DOWN:true}, leg_to_place UP. The cost is not idle
  time -- it is that the completing leg is repriced against a MOVED book,
  which is the adverse-selection channel measured in adverse_selection.py.

  BLOCKED NEW PAIR. 5 refusals, decision_role NEW_OPEN, wanted to start a
  fresh pair while the slot was held. Cost is forgone pairs per window.

  BLOCKED COMPLETION. 4 refusals, decision_role COMPLEMENT. The most
  expensive kind: held inventory waiting on a slot is the stranding channel
  that cost Campaign 5 -$2.9034.

WHAT THIS CANNOT SAY. It cannot say two slots would have produced more
pairs. A second resting order is not a fill, the counterfactual book is not
observable, and Campaign 6's queue_ahead ran 1,173-7,629 contracts deep.
What it can say is how often the limit bound, for how long, and what the
planned-versus-realised pair costs were on the pairs it serialised.
"""
import json
import pathlib
from collections import Counter
from decimal import Decimal as D

HERE = pathlib.Path(__file__).resolve().parent
C6 = HERE.parent / "campaign6"

WINDOW_S = 900
ENTRY_CUTOFF_S = 600
COMPLETE_CUTOFF_S = 720


def load(d, name):
    with (d / name).open() as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def secs(utc):
    """Seconds past the hour, from an ISO timestamp."""
    hh, mm, ss = utc[11:19].split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


def window_open_s(window):
    return int(window[:2]) * 3600 + int(window[2:4]) * 60


def main():
    submits = {s["coid"]: s for s in load(C6, "tape_submits.jsonl")}
    terms = {t["coid"]: t for t in load(C6, "tape_terminals.jsonl")}
    fills = {f["coid"]: f for f in load(C6, "tape_fills.jsonl")}
    refusals = load(C6, "tape_slot_refusals.jsonl")

    # Rest intervals, per window, for TRANSMITTED orders only.
    per_window = {}
    for coid, s in submits.items():
        if not s.get("transmitted") or not s.get("window"):
            continue
        t = terms.get(coid)
        if t is None:
            continue
        per_window.setdefault(s["window"], []).append({
            "coid": coid, "side": s["side"], "own_px": s["own_px"],
            "qty": s["qty"],
            "t0": secs(s["utc"]), "t1": secs(t["utc"]),
            "state": t["state"], "filled": coid in fills,
        })

    ref_by_window = {}
    for r in refusals:
        ref_by_window.setdefault(r["window"], []).append(r)

    print("=" * 78)
    print("ONE-RESTING-ORDER COST -- Campaign 6")
    print("=" * 78)

    tot_occ = tot_entry = 0.0
    for w in sorted(per_window):
        orders = sorted(per_window[w], key=lambda r: r["t0"])
        w0 = window_open_s(w)
        entry_end = w0 + ENTRY_CUTOFF_S
        # Occupancy inside the ENTRY period, which is the window in which a
        # new pair can still be started.
        occ = 0.0
        for o in orders:
            lo, hi = max(o["t0"], w0), min(o["t1"], entry_end)
            if hi > lo:
                occ += hi - lo
        first = min(o["t0"] for o in orders)
        entry_span = entry_end - w0
        tot_occ += occ
        tot_entry += entry_span

        refs = ref_by_window.get(w, [])
        roles = Counter(r["decision_role"] for r in refs)
        print("\n%s  orders=%d  fills=%d" % (
            w, len(orders), sum(1 for o in orders if o["filled"])))
        print("  armed-to-first-submit      %6.1fs" % (first - w0))
        print("  slot occupied in entry     %6.1fs of %.0fs  (%.0f%%)"
              % (occ, entry_span, 100.0 * occ / entry_span))
        print("  entry time with slot free  %6.1fs" % (entry_span - occ))
        print("  MAX_LIVE_ORDERS refusals   %6d   %s"
              % (len(refs), dict(roles) or "-"))
        # THE GAPS ARE THE REAL CONSTRAINT. With occupancy this low the slot
        # is not the limit; the stretches with NOTHING resting are. A gap is
        # eligible time in which no order could fill because none existed.
        gaps, cursor = [], w0
        for o in orders:
            if o["t0"] > cursor:
                gaps.append((cursor - w0, o["t0"] - w0, o["t0"] - cursor))
            cursor = max(cursor, o["t1"])
        if cursor < entry_end:
            gaps.append((cursor - w0, entry_end - w0, entry_end - cursor))
        gaps.sort(key=lambda g: -g[2])
        print("  idle gaps in entry (top 3) %s"
              % ", ".join("%.0fs @T+%.0f" % (g[2], g[0]) for g in gaps[:3]))
        print("  longest single idle gap    %6.1fs" % (gaps[0][2] if gaps
                                                       else 0.0))
        for o in orders:
            print("    %-28s %-4s own=%-7s %6.1fs-%6.1fs (%5.1fs) %s"
                  % (o["coid"], o["side"], o["own_px"],
                     o["t0"] - w0, o["t1"] - w0, o["t1"] - o["t0"],
                     "FILLED" if o["filled"] else o["state"]
                     .replace("ORDER_STATE_", "")))

    print("\n" + "-" * 78)
    print("campaign entry-period occupancy  %.1fs of %.0fs  (%.0f%%)"
          % (tot_occ, tot_entry, 100.0 * tot_occ / tot_entry))
    roles = Counter(r["decision_role"] for r in refusals)
    print("MAX_LIVE_ORDERS refusals total   %d" % len(refusals))
    for role, n in sorted(roles.items(), key=lambda kv: -kv[1]):
        kind = {"FLAT_OPEN": "blocked SECOND LEG of a pair plan",
                "NEW_OPEN": "blocked a NEW pair",
                "COMPLEMENT": "blocked a COMPLETION of held inventory"}
        print("  %-11s %2d   %s" % (role, n, kind.get(role, "")))

    # The serialisation channel, priced. A pair the limit serialised had its
    # completing leg priced against a later book; where that moved against
    # us the difference is the cost of not having both legs resting.
    print("\n" + "-" * 78)
    print("SERIALISATION COST ON PAIRS (planned pair cost was 0.95 at a")
    print("1-tick spread and depth 2; see ceiling_refusals.py)")
    plan = D("0.95")
    total = D(0)
    for w in sorted(per_window):
        legs = [o for o in sorted(per_window[w], key=lambda r: r["t0"])
                if o["filled"]]
        for i in range(0, len(legs) - 1, 2):
            a, b = legs[i], legs[i + 1]
            own_a = D(str(a["own_px"]))
            own_b = D(str(b["own_px"]))
            realised = own_a + own_b
            qty = min(D(str(a["qty"])), D(str(b["qty"])))
            delta = (plan - realised) * qty
            total += delta
            print("  %s  %s+%s = %-6s vs plan %s on %-5s  %+0.4f  gap %.0fs"
                  % (w, own_a, own_b, realised, plan, qty, delta,
                     b["t0"] - a["t1"]))
    print("  net versus resting both legs at plan      %+0.4f" % total)
    print("  (positive = the SERIALISED outcome beat the plan pair, because")
    print("   the completing leg was repriced against a CHEAPER book)")
    print()
    print("  READ THIS CAREFULLY. Positive is not a win for serialisation as")
    print("  a policy. It is directional: Campaign 6's windows fell, so a")
    print("  late complement was cheap. The same serialisation in Campaign 5")
    print("  cost -$2.9034 in stranded legs when the windows did not fall.")
    print("  Resting both legs at plan would have locked 0.95 and forfeited")
    print("  this upside AND avoided that downside -- it trades variance for")
    print("  a smaller steadier edge. It is also conditional on the second")
    print("  leg filling at the plan price, which is not observable here.")
    print("-" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
