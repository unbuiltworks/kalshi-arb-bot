"""P11-A: which constraint actually stopped an L5 action, per family.

    python3 analysis/gemini/audit/p11a_blocked_census.py

READ-ONLY, DIAGNOSTIC. It changes no parameter and scores no gate.

The census it reads (`l5f_actions_20260922.tsv`) is the live shadow's own
per-window `action_census`, whose `blocked` field splits plan()'s holds by the
constraint that produced them: OSCILLATOR (the L5 surface withholding a rung --
BEHAVIOUR), ENVELOPE_MAX_IMBALANCE and ENVELOPE_MAX_LEG (the CAP4 dollar and
inventory bounds -- SAFETY), and END_OF_WINDOW (the <60s OPEN/EXTEND
prohibition -- SAFETY). `policy_vetoed` is the GEMINI_RECYCLE adaptation and is
0 by construction under FROZEN_L5_NATIVE.

So this file can answer, with counts rather than inference, the question the
audit is for: is our configuration suppressing L5, and if so WHICH setting.
"""
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "l5_actions_20260922.tsv")
if not os.path.exists(SRC):
    SRC = os.path.join(HERE, "l5f_actions_20260922.tsv")


def load(path=SRC):
    out = []
    for ln in open(path):
        if ln.startswith("#") or not ln.strip():
            continue
        f = ln.rstrip("\n").split("\t")
        if len(f) < 9:
            continue
        out.append({
            "utc": f[0], "fam": f[1],
            "req": {"OPEN": int(f[2]), "EXTEND": int(f[3]),
                    "COMPLETE": int(f[4])},
            "fil": {"OPEN": int(f[5]), "EXTEND": int(f[6]),
                    "COMPLETE": int(f[7])},
            "blocked": ({} if f[8] == "-" else {
                (p.split(":")[0], p.split(":")[1]): int(p.split(":")[2])
                for p in f[8].split(";")}),
        })
    return out


def main():
    rows = load()
    fams = ("5M", "15M")
    print("LIVE SHADOW ACTION CENSUS -- %d windows, policy FROZEN_L5_NATIVE"
          % len(rows))
    print()
    print(f"{'':<8}{'windows':>8}{'reqOPEN':>9}{'reqEXT':>8}{'reqCOMP':>9}"
          f"{'filOPEN':>9}{'filEXT':>8}{'filCOMP':>9}")
    tot = Counter()
    for fam in fams:
        r = [x for x in rows if x["fam"] == fam]
        c = Counter()
        for x in r:
            for k in ("OPEN", "EXTEND", "COMPLETE"):
                c["req" + k] += x["req"][k]
                c["fil" + k] += x["fil"][k]
                tot["req" + k] += x["req"][k]
                tot["fil" + k] += x["fil"][k]
        print(f"{fam:<8}{len(r):>8}{c['reqOPEN']:>9}{c['reqEXTEND']:>8}"
              f"{c['reqCOMPLETE']:>9}{c['filOPEN']:>9}{c['filEXTEND']:>8}"
              f"{c['filCOMPLETE']:>9}")
    print(f"{'ALL':<8}{len(rows):>8}{tot['reqOPEN']:>9}{tot['reqEXTEND']:>8}"
          f"{tot['reqCOMPLETE']:>9}{tot['filOPEN']:>9}{tot['filEXTEND']:>8}"
          f"{tot['filCOMPLETE']:>9}")

    print("\nBLOCKED, by constraint and by the action it would have been:")
    by = defaultdict(Counter)
    wins = Counter()
    for x in rows:
        seen = set()
        for (kind, byc), n in x["blocked"].items():
            by[byc][kind] += n
            by[byc]["_total"] += n
            seen.add(byc)
        for byc in seen:
            wins[byc] += 1
    every = ("OSCILLATOR", "ENVELOPE_MAX_IMBALANCE", "ENVELOPE_MAX_LEG",
             "END_OF_WINDOW")
    print(f"{'constraint':<26}{'OPEN':>7}{'EXTEND':>8}{'COMPLETE':>10}"
          f"{'total':>8}{'windows':>9}")
    for byc in every:
        c = by.get(byc, Counter())
        print(f"{byc:<26}{c['OPEN']:>7}{c['EXTEND']:>8}{c['COMPLETE']:>10}"
              f"{c['_total']:>8}{wins[byc]:>9}")
    other = [k for k in by if k not in every]
    for byc in other:
        c = by[byc]
        print(f"{byc+' (unlisted)':<26}{c['OPEN']:>7}{c['EXTEND']:>8}"
              f"{c['COMPLETE']:>10}{c['_total']:>8}{wins[byc]:>9}")

    print("\nBLOCKED by family:")
    for fam in fams:
        r = [x for x in rows if x["fam"] == fam]
        c = Counter()
        for x in r:
            for (kind, byc), n in x["blocked"].items():
                c[(byc, kind)] += n
        if not c:
            print(f"  {fam}: nothing blocked in {len(r)} windows")
            continue
        print(f"  {fam}: " + ", ".join(
            f"{byc}/{kind} {n}" for (byc, kind), n in sorted(c.items())))

    print("\nWHERE THE FUNNEL ACTUALLY NARROWS (all families):")
    rq = tot["reqOPEN"] + tot["reqEXTEND"] + tot["reqCOMPLETE"]
    fl = tot["filOPEN"] + tot["filEXTEND"] + tot["filCOMPLETE"]
    blk = sum(by[b]["_total"] for b in by)
    print(f"  requested {rq}   blocked by any constraint {blk} "
          f"({blk / rq:.2%})   filled {fl} ({fl / rq:.3%})")
    nofill = sum(1 for x in rows if sum(x["fil"].values()) == 0)
    print(f"  windows with zero fills: {nofill} of {len(rows)} "
          f"({nofill / len(rows):.1%})")
    ext = sum(1 for x in rows if x["req"]["EXTEND"] > 0)
    print(f"  windows where an EXTEND was ever requested: {ext} of "
          f"{len(rows)} ({ext / len(rows):.1%})")


if __name__ == "__main__":
    main()
