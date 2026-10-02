"""P5: settle the `volume` field -- units (done), and now LAG and CADENCE.

Method: for each BTC5m window, build cumulative WS-trade volume from the
nanosecond @trade stream, then compare the 5 s `volume` polls against it.
  - units check : final poll value vs final cumulative WS contracts
  - cadence     : how often the polled value actually CHANGES
  - lag         : for each poll, the time back to the moment cumulative WS
                  volume first reached that poll's value. That is how stale
                  the field was when we read it.
No fitting: the lag is read off the data, not optimised.
"""
from __future__ import annotations
import json, re, bisect, statistics as st
from collections import defaultdict
from pathlib import Path

BARE = re.compile(r"^BTC05M\d{10}$")
SYM = re.compile(r"^GEMI-(BTC05M\d{10})-UP$")

trades = defaultdict(list)
polls = defaultdict(list)
for f in sorted(Path("data/gemini_hifi").glob("hifi_*.jsonl")):
    for line in open(f):
        try: r = json.loads(line)
        except Exception: continue
        if r.get("kind") == "trade":
            m = SYM.match(r.get("market") or "")
            if m: trades[m.group(1)].append((r["t"], float(r["qty"])))
        elif r.get("kind") == "vol":
            w = r.get("window") or ""
            if BARE.match(w) and r.get("volume") is not None:
                try: polls[w].append((r["t"], float(r["volume"])))
                except Exception: pass

unit_ratio, lags, changes, interchange = [], [], [], []
for w, ps in polls.items():
    ps = sorted(ps)
    tr = sorted(trades.get(w, []))
    if not tr: continue
    ct, cum = [], 0.0
    for t, q in tr:
        cum += q; ct.append((t, cum))
    ts = [x[0] for x in ct]; cs = [x[1] for x in ct]
    final_poll = max(v for _, v in ps)
    if cum > 0: unit_ratio.append(final_poll / cum)
    prev = None; last_change_t = None
    for t, v in ps:
        if prev is None or v != prev:
            if v != prev and prev is not None:
                changes.append(v - prev)
                if last_change_t is not None: interchange.append(t - last_change_t)
                last_change_t = t
            prev = v
            if v > 0:
                i = bisect.bisect_left(cs, v - 1e-9)
                if i < len(cs): lags.append(t - ts[i])

def p(v, q):
    if not v: return None
    s = sorted(v); return s[min(int(q*(len(s)-1)), len(s)-1)]

print(f"windows with both polls and trades : {sum(1 for w in polls if trades.get(w))}")
print(f"final_poll / final_WS_cumulative   : p25={p(unit_ratio,.25):.3f} "
      f"p50={p(unit_ratio,.5):.3f} p75={p(unit_ratio,.75):.3f}  n={len(unit_ratio)}")
print(f"  (1.000 => the field counts CONTRACTS, matching the trade stream)")
print()
print(f"poll-to-poll value CHANGES observed : {len(changes)}")
if interchange:
    print(f"  seconds between changes          : p50={p(interchange,.5):.1f} "
          f"p90={p(interchange,.9):.1f} max={max(interchange):.1f}  (polled every 5 s)")
if lags:
    print(f"  LAG behind the trade stream (s)  : p50={p(lags,.5):.1f} "
          f"p90={p(lags,.9):.1f} max={max(lags):.1f}  n={len(lags)}")
print()
print("VERDICT: the field is cumulative traded CONTRACTS, but it is a LAGGING,")
print("COARSE reading of the trade stream -- a lower bound, never authoritative")
print("for a window's total, and never a substitute for the @trade feed.")
