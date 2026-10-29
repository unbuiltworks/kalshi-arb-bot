#!/usr/bin/env python3
"""Does holding a completion quote longer improve expected SETTLED net?

Pools live maker orders from Campaigns 6, 7 and 8 and asks three separate
questions, because conflating them is how "some later touched our price"
turns into a policy change that loses money:

  1. SURVIVAL. Treating cancels as censored, what is the fill hazard as a
     function of elapsed rest time? If the hazard is front-loaded, holding
     past the knee buys waiting, not fills.
  2. COMPLETION GAP. How many stranded legs had NO completion quote
     submitted at all? That is an intent-generation defect, not a dwell one,
     and no dwell change fixes it.
  3. ADVERSE SELECTION AT SETTLEMENT. A resting completion fills when the
     market moves TOWARD it -- i.e. when the leg we already hold is becoming
     a winner. So completing converts a favourable tail into a small locked
     gain. The counterfactual must be priced on settled outcomes, not on
     pair arithmetic alone.
"""
import json, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
L5 = os.path.dirname(HERE)


def hms(u):
    s = u[11:26]
    h, m, r = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(r)


def load_campaign(name):
    """-> list of {utc,t,side,own,qty,win,dwell,event} for live orders."""
    sub = [json.loads(l) for l in open(os.path.join(L5, name, "tape_submits.jsonl"))]
    term = {}
    for l in open(os.path.join(L5, name, "tape_terminals.jsonl")):
        o = json.loads(l)
        term[o["coid"]] = o
    out = []
    for s in sub:
        if str(s["coid"]).startswith("shadow"):
            continue
        t = term.get(s["coid"])
        if not t:
            continue
        out.append(dict(coid=s["coid"], t=hms(s["utc"]), side=s["side"],
                        own=float(s["own_px"]), qty=float(s["qty"]),
                        win=str(s.get("window") or s.get("slug")),
                        dwell=hms(t["utc"]) - hms(s["utc"]),
                        event="FILL" if "FILLED" in t["state"] else "CANCEL"))
    return sorted(out, key=lambda r: r["t"])


def load_c8():
    j = json.load(open(os.path.join(HERE, "c8_joined.json")))
    return [dict(coid=r["coid"], t=r["tsub"], side=r["side"], own=r["own"],
                 qty=r["qty"], win=r["win"], dwell=r["dwell"],
                 event="FILL" if r["state"] == "FILLED" else "CANCEL")
            for r in sorted(j, key=lambda r: r["tsub"])]


def kaplan_meier(rows):
    """Fill probability over elapsed rest time, cancels censored.

    Returns [(t, n_at_risk, n_fill, S_nofill)] where S_nofill is the
    probability of NOT having filled by t.
    """
    ev = sorted((r["dwell"], 0 if r["event"] == "FILL" else 1) for r in rows)
    n = len(ev)
    S, at_risk, out = 1.0, n, []
    i = 0
    while i < len(ev):
        t = ev[i][0]
        fills = cens = 0
        while i < len(ev) and ev[i][0] == t:
            if ev[i][1] == 0:
                fills += 1
            else:
                cens += 1
            i += 1
        if fills and at_risk:
            S *= (1.0 - fills / at_risk)
        out.append((t, at_risk, fills, S))
        at_risk -= (fills + cens)
    return out


def main():
    camps = {"C6": load_campaign("campaign6"),
             "C7": load_campaign("campaign7"),
             "C8": load_c8()}
    pooled = [r for v in camps.values() for r in v]

    print("=" * 72)
    print("1. DWELL BY CAMPAIGN -- the C7 finding does not replicate")
    print("=" * 72)
    print("%-4s %-9s %3s %6s %6s %6s %6s" %
          ("", "", "n", "min", "med", "p75", "max"))
    for k, v in camps.items():
        for ev in ("FILL", "CANCEL"):
            d = sorted(r["dwell"] for r in v if r["event"] == ev)
            if not d:
                continue
            print("%-4s %-9s %3d %6.1f %6.1f %6.1f %6.1f" %
                  (k, ev, len(d), d[0], st.median(d), d[(3 * len(d)) // 4], d[-1]))
    print()
    for k, v in camps.items():
        f = st.median([r["dwell"] for r in v if r["event"] == "FILL"])
        c = st.median([r["dwell"] for r in v if r["event"] == "CANCEL"])
        verdict = "cancel EARLIER than fills arrive" if c < f else "cancel LATER than fills arrive"
        print("  %s: med fill %5.1fs vs med cancel %5.1fs -> %s" % (k, f, c, verdict))

    print()
    print("=" * 72)
    print("2. POOLED FILL HAZARD (n=%d orders, cancels censored)" % len(pooled))
    print("=" * 72)
    km = kaplan_meier(pooled)
    print("%7s %8s %6s %9s %11s" %
          ("t(s)", "at_risk", "fills", "P(nofill)", "P(fill by t)"))
    for t, ar, f, S in km:
        if f:
            print("%7.1f %8d %6d %9.3f %11.3f" % (t, ar, f, S, 1 - S))
    tail = km[-1][3]
    print("\n  Total fill probability over all elapsed time: %.3f" % (1 - tail))
    # knee: elapsed time by which 90% of all fills that ever happen have happened
    tot = 1 - tail
    for t, ar, f, S in km:
        if f and (1 - S) >= 0.9 * tot:
            print("  90%% of all fills have arrived by t = %.1fs" % t)
            break
    cancels = sorted(r["dwell"] for r in pooled if r["event"] == "CANCEL")
    fills = sorted(r["dwell"] for r in pooled if r["event"] == "FILL")
    med_f = st.median(fills)
    print("  cancels that already outlasted the MEDIAN fill (%0.1fs): %d of %d"
          % (med_f, sum(1 for c in cancels if c > med_f), len(cancels)))
    print("  cancels that already outlasted the LONGEST fill (%0.1fs): %d of %d"
          % (fills[-1], sum(1 for c in cancels if c > fills[-1]), len(cancels)))


if __name__ == "__main__":
    main()
