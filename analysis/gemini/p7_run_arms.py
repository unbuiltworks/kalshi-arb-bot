"""The five preregistered arms, pre-rebate, across the frozen split."""
import json, sys, statistics
from collections import defaultdict
from fractions import Fraction as F

SP = "/tmp/claude-0/-home-user-kalshi-arb-bot/198d2e31-d6d4-505d-b6e2-8eb3a285708f/scratchpad"
sys.path.insert(0, SP)
sys.path.insert(0, "/home/user/kalshi-arb-bot")
import evaluate as E

data = json.load(open(SP + "/bulk.json"))
for c in data:
    c["prints"] = [[int(t), s, p, q] for t, s, p, q in (c.get("prints") or [])]

by_dur = defaultdict(list)
for c in data:
    by_dur[c["duration"]].append(c)

settled = [c for c in data if c.get("resolution")]
print(f"contracts pulled: {len(data)} | with a resolution: {len(settled)} | "
      f"with prints: {sum(1 for c in data if c['prints'])}")
for d in ("5m", "15m", "1h"):
    v = by_dur[d]
    print(f"   {d:>4}: {len(v):>5} contracts, {sum(1 for c in v if c['prints']):>5} traded, "
          f"days {sorted({c['day'] for c in v})}")

CAPTURES = (F(1), F(1, 2), F(1, 3), F(1, 4))


def arms(capture):
    out = []
    out.append(E.run_arm(by_dur["15m"], label="1. frozen L5 (always, CAP4)",
                         duration="15m", policy="always", max_residual=4,
                         max_legs=12, capture=capture))
    out.append(E.run_arm(by_dur["5m"], label="2. BTC5m  EV",
                         duration="5m", policy="ev", max_residual=4,
                         max_legs=12, capture=capture))
    out.append(E.run_arm(by_dur["15m"], label="3. BTC15m EV",
                         duration="15m", policy="ev", max_residual=4,
                         max_legs=12, capture=capture))
    out.append(E.run_arm(by_dur["1h"], label="4. BTC1h  EV nearest-money",
                         duration="1h", policy="ev", hourly_select=True,
                         max_residual=4, max_legs=12, capture=capture))
    comb = {"label": "5. combined 5m+15m+1h", "duration": "all", "rows": []}
    for a in out[1:]:
        comb["rows"] += a["rows"]
    out.append(comb)
    return out


for cap in CAPTURES:
    print(f"\n{'='*150}\nCAPTURE = {float(cap):.2f}"
          + ("   <- UPPER BOUND: assumes we win every qualifying print" if cap == 1 else ""))
    print("=" * 150)
    print(E.HDR, flush=True)
    for a in arms(cap):
        E.show(a)

print("\n\n=== TAIL, at capture 1.00 and capture 0.33 ===")
print(f"{'arm':>34} {'cap':>5} {'split':>5} {'worst mkt':>10} {'p05':>8} {'p25':>8} "
      f"{'median':>8} {'%losing':>8} {'streak':>7} {'maxDD':>8} {'residQty':>9}")
for cap in (F(1), F(1, 3)):
    for a in arms(cap):
        for sp in ("dev", "val", "oos"):
            t = E.tail(a, sp)
            if not t:
                continue
            print(f"{t['label']:>34} {float(cap):>5.2f} {sp:>5} {t['worst_market']:>10.3f} "
                  f"{t['p05']:>8.3f} {t['p25']:>8.3f} {t['median']:>8.3f} "
                  f"{t['pct_losing']:>8.1%} {t['losing_streak']:>7} {t['max_dd']:>8.2f} "
                  f"{t['residual_qty']:>9}", flush=True)

E.print_calibration(settled)
