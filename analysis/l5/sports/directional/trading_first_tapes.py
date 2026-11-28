"""Trading-first vs current (underdog-heavy) accumulation on recorded sports
tapes. OFFLINE, THEORETICAL: recorded books + international fair, modelled
fills (touch / through), 3 s latency, maker fee 0, rewards NOT modelled
(pre-reward trading P&L only). Nothing here is realized."""
import bisect, json, math, sys
from collections import defaultdict
from decimal import Decimal as D
sys.path.insert(0, '.')
from src import pmus_directional_backtest as B

REC = ["analysis/l5/sports_wallets/cross_venue_20261121_2027_2217.jsonl.gz",
       "analysis/l5/sports_wallets/cross_venue_20261121_2218_0015_sizes.jsonl.gz"]
frames = B.load_cross_venue(REC)
meta = json.load(open('analysis/l5/sports/directional/market_meta_20261121.json'))
for s, fr in frames.items():
    al = B.align_intl(fr)
    meta.setdefault(s, {})['flip'] = None if al is None else (not al)
vt = {s: [f["t"] for f in fr] for s, fr in frames.items()}
vm = {s: [float((f["yes_bid"] + f["yes_ask"]) / 2) for f in fr]
      for s, fr in frames.items()}
EV = "0.042"   # behind-touch modelled fill loss (live markout calibration)
P = [
 ("A baseline: production pauses (6e6cc2d5d-like core)", {}),
 ("B falling-side filter (30 s, 2 ticks)", {"falling_s": 30, "falling_ticks": 2}),
 ("C require independent fair", {"require_fair": True}),
 ("D trading-first: fair + EV>=0 after fill loss", {"require_fair": True, "edge_min": "0", "ev_fill_loss": EV}),
 ("E D + favourite-only openings/adds (fair>=0.5)", {"require_fair": True, "edge_min": "0", "ev_fill_loss": EV, "fav_side": "favorite"}),
 ("F E + falling-side filter", {"require_fair": True, "edge_min": "0", "ev_fill_loss": EV, "fav_side": "favorite", "falling_s": 30, "falling_ticks": 2}),
 ("G control: underdog-only openings/adds + EV", {"require_fair": True, "edge_min": "0", "ev_fill_loss": EV, "fav_side": "underdog"}),
]
import os
if os.getenv("TF_SWEEP"):
    P = [("A baseline: production pauses (6e6cc2d5d-like core)", {})]
    for el in ("0", "0.01", "0.02"):
        for fs in (None, "favorite", "underdog"):
            for fall in (False, True):
                pol = {"require_fair": True, "edge_min": "0", "ev_fill_loss": el}
                if fs: pol["fav_side"] = fs
                if fall: pol.update(falling_s=30, falling_ticks=2)
                P.append((f"edge>={el} sides={fs or 'both'} falling={'on' if fall else 'off'}", pol))
HZ = (30, 120, 300)

def mk(slug, side, t, px, h):
    ts = vt[slug]; i = bisect.bisect_left(ts, t + h)
    if i >= len(ts):
        return None
    m = vm[slug][i]
    return (m if side == "YES" else 1 - m) - float(px)

print(__doc__)
for fill in ("touch", "through"):
    base = None
    for name, pol in P:
        r = B.run(frames, meta, dict(pol, directional=False),
                  {"pauses": True, "fill": fill, "keep_fills": True})
        rows = {x['slug']: x for x in r['rows']}
        if base is None:
            base = rows
        g = defaultdict(lambda: {"n": 0, "q": 0.0, "cost": 0.0, "settle": 0.0,
                                 **{h: [] for h in HZ}})
        for x in r['rows']:
            oy = meta[x['slug']]['outcome_yes']
            for f in x.get('fill_list', []):
                role = 'complete' if f['role'] == 'complete' else 'open/add'
                fs = f['fair_side']
                fav = 'nofair' if fs is None else ('fav' if fs >= D('0.5') else 'dog')
                k = (role, fav)
                q = float(f['qty']); px = float(f['px'])
                win = (oy == 1) == (f['side'] == 'YES')
                g[k]["n"] += 1; g[k]["q"] += q; g[k]["cost"] += px * q
                g[k]["settle"] += (1.0 if win else 0.0) * q
                for h in HZ:
                    v = mk(x['slug'], f['side'], f['t'], px, h)
                    if v is not None:
                        g[k][h].append(v)
        d = [float(rows[s]['trading_pnl']) - float(base[s]['trading_pnl'])
             for s in base if int(base[s]['fills']) or int(rows[s]['fills'])]
        n = len(d); m = sum(d) / n if n else 0
        se = math.sqrt(sum((v - m) ** 2 for v in d) / (n - 1) / n) if n > 1 else 0
        dec = defaultdict(int)
        for x in r['rows']:
            for kk, vv in x['decisions'].items():
                if kk.startswith('tf_'):
                    dec[kk] += vv
        print(f"\n[{fill}] {name}")
        print(f"  pre-reward settled trading {float(r['1_settled_trading_pnl_after_fees_usd']):+7.2f}"
              f" (matched {float(r['6_matched_pnl_usd']):+6.2f}, residual {float(r['6_residual_pnl_usd']):+7.2f})"
              f" fills {r['fills']} spend ${float(r['spend_usd']):.2f} traded {r['markets_traded']}"
              f" | paired vs A {m:+.3f}±{se:.3f} (n {n}) | refusals {dict(dec)}")
        for k in sorted(g):
            v = g[k]
            mo = " ".join(f"{h}s {sum(v[h])/len(v[h])*100:+5.1f}c" if v[h] else f"{h}s  n/a" for h in HZ)
            print(f"    {k[0]:8} {k[1]:6} fills {v['n']:3} qty {v['q']:5.0f} avg px {v['cost']/v['q']:.3f}"
                  f" settle-cost {v['settle']-v['cost']:+6.2f}  markout {mo}")
