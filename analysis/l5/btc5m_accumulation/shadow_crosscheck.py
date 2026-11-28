"""Does the SHADOW's logic reproduce the REPLAY's baseline on the same tape?

RESULT, 2026-11-13: yes, exactly. 187 windows, BOTH 10.2% (19), ONE 86.6%
(162), NONE 3.2% (6), net +3.74 c/w, se 2.25, t +1.67 -- identical to the
replay's figures to the decimal place.

WHY IT WAS WORTH CHECKING. The live shadow's first ten windows came back
0 pairs and -8.00 c/w against a +3.74 baseline. That is either bad luck
(34% chance of zero pairs in ten at a 10.2% rate) or a broken instrument,
and seven defects had already been found in this collector by running it.
An unfavourable reading from a broken instrument is worthless; an
unfavourable reading from a sound one is data.

IT ALSO VALIDATES SOMETHING I HAD ONLY ASSUMED. The replay steps every
0.5s with carry-forward; this feeds the shadow raw quotes at their real
timestamps with none. Identical answers, so poll-based sampling is not
itself introducing a difference.

WHAT THIS DOES NOT VALIDATE: queue position. Both the shadow and the
baseline fill whenever the touch reaches the resting price, with no size
and no queue. That is optimistic in BOTH, so comparisons between them are
fair even though the absolute pair rate may not be.

If yes, the shadow is measuring what it claims and the live divergence is
the market. If no, the shadow is broken and its 10 windows mean nothing.

The shadow is fed the tape's quotes at their real timestamps, exactly as it
would see them from the venue -- no carry-forward, no 0.5s stepping.
"""
import json, math, statistics, collections, importlib.util, sys, os
from decimal import Decimal as D

sys.path.insert(0, os.getcwd())
os.environ["L5_BTC5M_CONTROLLER"] = "ON"

spec = importlib.util.spec_from_file_location(
    "sh", "analysis/l5/btc5m_accumulation/straddle_shadow.py")
sh = importlib.util.module_from_spec(spec); sys.modules["sh"] = spec and sh
spec.loader.exec_module(sh)

PRICE = D("0.08")
CUT = 300.0 - 30.0            # the shadow's own cancel lead

per = collections.defaultdict(list)
for line in open("analysis/l5/btc5m_accumulation/pmus_btc5m_quotes_20261112.jsonl"):
    r = json.loads(line)
    per[r["slug"]].append(r)

def dec(v):
    return None if v is None else D(str(v))

def tape_outcome(rows):
    """The replay's rule: the venue's own terminal quote."""
    for q in reversed(rows):
        if q["t"] < CUT:
            break
        b, a = dec(q["bid"]), dec(q["ask"])
        if b is not None and b >= D("0.90"):
            return sh.UP
        if a is not None and a <= D("0.10"):
            return sh.DOWN
    return None

cases = collections.Counter(); pnls = []
for slug, rows in per.items():
    rows = sorted(rows, key=lambda r: r["t"])
    won = tape_outcome(rows)
    if won is None:
        continue
    #: THE SHADOW'S OWN LOOP, fed real quotes in real order
    filled = {}
    for q in rows:
        if q["t"] < 0 or q["t"] >= CUT:
            continue
        bid, ask = dec(q["bid"]), dec(q["ask"])
        if bid is None or ask is None:
            continue
        for side in (sh.UP, sh.DOWN):
            p = sh.own_price(side, bid, ask)
            if p is None or side in filled:
                continue
            if p <= PRICE:
                filled[side] = q["t"]
    case = {2: "BOTH", 1: "ONE", 0: "NONE"}[len(filled)]
    cases[case] += 1
    pnl = D(0)
    for side in filled:
        pnl += (D(1) if side == won else D(0)) - PRICE
    pnls.append(float(pnl))

n = len(pnls)
m = statistics.fmean(pnls)
se = statistics.stdev(pnls)/math.sqrt(n)
print("SHADOW LOGIC over the committed tape, %d windows" % n)
print()

print("  BOTH  %3d = %5.1f%%     replay says 10.2%%" % (cases["BOTH"], 100*cases["BOTH"]/n))
print("  ONE   %3d = %5.1f%%     replay says 86.6%%" % (cases["ONE"], 100*cases["ONE"]/n))
print("  NONE  %3d = %5.1f%%     replay says  3.2%%" % (cases["NONE"], 100*cases["NONE"]/n))
print()
print("  net %+.2f c/w  se %.2f  t %+.2f     replay says +3.74 c/w, t +1.67"
      % (100*m, 100*se, m/se))
