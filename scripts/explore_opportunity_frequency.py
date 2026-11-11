"""EXPLORATORY opportunity-frequency study. BBO / top-of-book ONLY.

SEPARATE FROM THE PREREGISTERED FULL-LADDER TEST. Results here are exploratory
and MUST NOT feed threshold selection for the preregistered OOS evaluation.
Size is never inferred beyond displayed top-of-book quantity.

For each observed acquisition state, walk FORWARD only and ask what the
executable complement would have cost, what pair economics that implies, how
long it took to appear, how long it lasted, and what size was displayed.
"""
from __future__ import annotations
import json, collections, datetime as dt
from decimal import Decimal as D

THETA_TAKER = D("0.0695")
SRC = "data/pmus_decision_tape/pmus_decision_states_20260927.jsonl"


def d(v):
    try:
        return D(str(v))
    except Exception:
        return None


def ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def load():
    by = collections.defaultdict(list)
    for line in open(SRC, encoding="utf-8"):
        r = json.loads(line)
        b = r.get("book") or {}
        bid, ask = d(b.get("bid")), d(b.get("ask"))
        if bid is None or ask is None or not (0 < bid < 1 and 0 < ask < 1):
            continue
        r["_t"] = ts(r.get("_log_ts"))
        r["_bid"], r["_ask"] = bid, ask
        r["_bsh"] = d(b.get("bid_shares")) or D(0)
        r["_ash"] = d(b.get("ask_shares")) or D(0)
        by[r["market"]].append(r)
    for v in by.values():
        v.sort(key=lambda x: x["_t"] or 0)
    return by


def acq_price(side, r):
    """Maker acquisition at the touch, in the side's own terms."""
    return r["_bid"] if side == "UP" else (D(1) - r["_ask"])


def comp_taker(side, r):
    """TAKER cost to buy `side` now, own terms, plus displayed size."""
    if side == "UP":
        return r["_ask"], r["_ash"]
    return (D(1) - r["_bid"]), r["_bsh"]


def fee(p, q=D(1)):
    return THETA_TAKER * q * p * (D(1) - p)


def study():
    by = load()
    eps = []
    for mkt, rows in by.items():
        for i, r in enumerate(rows):
            side = r.get("side")
            if side not in ("UP", "DOWN"):
                continue
            ap = acq_price(side, r)
            if ap is None or not (0 < ap < 1):
                continue
            other = "DOWN" if side == "UP" else "UP"
            best = None
            first = {}
            firstn = {}
            run = collections.defaultdict(int)
            bestrun = collections.defaultdict(int)
            runw = collections.defaultdict(float)
            bestrunw = collections.defaultdict(float)
            prev_t = r["_t"]
            for j in range(i + 1, len(rows)):
                s = rows[j]
                cp, size = comp_taker(other, s)
                if cp is None or not (0 < cp < 1):
                    continue
                pair = ap + cp
                gross = D(1) - pair
                net = gross - fee(cp)
                if best is None or pair < best["pair"]:
                    best = {"pair": pair, "cp": cp, "gross": gross, "net": net,
                            "size": size, "dt": (s["_t"] or 0) - (r["_t"] or 0),
                            "obs": j - i}
                for c in (1, 2, 3, 4, 5):
                    thr = D(c) / D(100)
                    ok = gross >= thr
                    okn = net >= thr
                    # GROSS and NET are tracked as SEPARATE nested measures.
                    # An earlier version recorded net-at-the-first-gross-state,
                    # which produced a non-monotonic net column (77, 62, 105,
                    # 110, 98) -- impossible for a nested threshold and a
                    # measurement artifact, not a result.
                    if ok and c not in first:
                        first[c] = {"dt": (s["_t"] or 0) - (r["_t"] or 0),
                                    "obs": j - i, "size": size, "pair": pair}
                    if okn and c not in firstn:
                        firstn[c] = {"dt": (s["_t"] or 0) - (r["_t"] or 0),
                                     "obs": j - i, "size": size, "pair": pair}
                    run[c] = run[c] + 1 if ok else 0
                    if run[c] > bestrun[c]:
                        bestrun[c] = run[c]
                    runw[c] = (runw[c] + ((s["_t"] or 0) - (prev_t or s["_t"] or 0))) if ok else 0.0
                    if runw[c] > bestrunw[c]:
                        bestrunw[c] = runw[c]
                prev_t = s["_t"]
            eps.append({"mkt": mkt, "side": side, "key": r.get("key"),
                        "acq": ap, "best": best, "first": first,
                        "firstn": firstn, "runs": dict(bestrun),
                        "runs_s": dict(bestrunw), "t": r["_t"]})
    return eps


def main():
    eps = study()
    n = len(eps)
    print("EXPLORATORY -- BBO/top-of-book only. NOT the preregistered test.")
    print("acquisition states analysed: %d (1615z+1630z, all two-sided)\n" % n)

    print("=== 1. OPPORTUNITY FREQUENCY: does a profitable complement appear? ===")
    print("  %-8s %10s %10s   %s" % ("gross>=", "GROSS n(%)", "NET n(%)",
                                     "median time-to-opportunity"))
    for c in (1, 2, 3, 4, 5):
        g = [e for e in eps if c in e["first"]]
        nt = [e for e in eps if c in e["firstn"]]
        dts = sorted(e["first"][c]["dt"] for e in g)
        med = dts[len(dts) // 2] if dts else None
        print("  %-8s %5d (%4.1f%%) %5d (%4.1f%%)   %s" % (
            "%dc" % c, len(g), 100.0 * len(g) / max(n, 1),
            len(nt), 100.0 * len(nt) / max(n, 1),
            ("%.1fs" % med) if med is not None else "n/a"))

    print("\n=== best pair cost reached, per acquisition ===")
    bp = sorted(float(e["best"]["pair"]) for e in eps if e["best"])
    if bp:
        q = lambda p: bp[int(p * (len(bp) - 1))]
        print("  min %.4f  p10 %.4f  median %.4f  p90 %.4f  max %.4f" % (
            bp[0], q(.10), q(.50), q(.90), bp[-1]))
        print("  below 1.00: %d of %d (%.1f%%)" % (
            sum(1 for x in bp if x < 1.0), len(bp),
            100.0 * sum(1 for x in bp if x < 1.0) / len(bp)))

    print("\n=== 2. CAPTURE GAP: what did B' actually do at these states? ===")
    kc = collections.Counter(e["key"] for e in eps)
    for k, v in kc.most_common():
        withop = sum(1 for e in eps if e["key"] == k and 1 in e["first"])
        print("  %-42s %4d states, %4d had a >=1c complement later" % (k, v, withop))

    print("\n=== 5. OPPORTUNITY LIFETIME (WALL-CLOCK seconds; see caveat) ===")
    for c in (1, 2, 3):
        rs = [e["runs_s"].get(c, 0.0) for e in eps if e["runs_s"].get(c, 0.0) > 0]
        if not rs:
            print("  >=%dc: none" % c)
            continue
        rs.sort()
        print("  >=%dc: n=%d  median %.1fs  p90 %.1fs  max %.1fs" % (
            c, len(rs), rs[len(rs) // 2], rs[int(.9 * (len(rs) - 1))], rs[-1]))
    print("  CAVEAT: this tape is DECISION-driven and bursty, not 2s-sampled.")
    print("  Sub-2s lifetime is UNKNOWN here and is measured on the new tape.")

    print("\n=== displayed top-of-book size at the best complement ===")
    sz = sorted(float(e["best"]["size"]) for e in eps if e["best"])
    if sz:
        q = lambda p: sz[int(p * (len(sz) - 1))]
        print("  min %.2f  p10 %.2f  median %.2f  p90 %.2f" % (
            sz[0], q(.10), q(.50), q(.90)))
        print("  NO size is inferred beyond this displayed quantity.")




def feasibility():
    """Outputs 3 and 4: capacity and what $5/window would require.

    Uses ONLY displayed top-of-book quantity. Nothing is inferred deeper.
    """
    eps = study()
    print("\n=== 3/4. CAPACITY AND $5 FEASIBILITY (top-of-book only) ===")
    print("  net/contract = gross - taker fee on the complement leg")
    print("  %-7s %9s %11s %14s %12s" % (
        "gross", "fee@px", "net/contract", "contracts for $5", "states w/ size"))
    for c in (1, 2, 3, 4, 5):
        qual = [e for e in eps if c in e["first"]]
        if not qual:
            continue
        # fee at the complement price actually observed at first qualifying
        fees = [float(fee(e["first"][c]["pair"] - e["acq"])) for e in qual]
        fees.sort()
        medfee = fees[len(fees) // 2]
        net = c / 100.0 - medfee
        need = (5.0 / net) if net > 0 else None
        sizes = sorted(float(e["first"][c]["size"]) for e in qual)
        medsize = sizes[len(sizes) // 2]
        enough = sum(1 for s in sizes if need is not None and s >= need)
        print("  %-7s %9.5f %11.5f %14s %12s" % (
            ">=%dc" % c, medfee, net,
            ("%.0f" % need) if need else "impossible",
            ("%d/%d show >= that" % (enough, len(sizes))) if need else "-"))
        print("          median displayed size at that state: %.0f" % medsize)


if __name__ == "__main__":
    feasibility()
