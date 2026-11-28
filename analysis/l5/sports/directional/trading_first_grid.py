"""Trading-first grid on the recorded 21 Nov two-venue tape. THEORETICAL:
recorded books + international fair, modelled fills, 3 s latency, maker fee 0,
rewards NOT modelled (pre-reward settled trading P&L). Admission grid first,
then hedge economics on the best admission rules. Each row: both fill models,
both recording halves, paired difference vs the baseline per market."""
import itertools, json, math, sys, time
from decimal import Decimal as D
sys.path.insert(0, '.')
from src import pmus_directional_backtest as B

REC = ["analysis/l5/sports_wallets/cross_venue_20261121_2027_2217.jsonl.gz",
       "analysis/l5/sports_wallets/cross_venue_20261121_2218_0015_sizes.jsonl.gz"]
META = json.load(open('analysis/l5/sports/directional/market_meta_20261121.json'))

def load(paths):
    fr = B.load_cross_venue(paths); meta = json.loads(json.dumps(META))
    for s, f in fr.items():
        al = B.align_intl(f); meta.setdefault(s, {})['flip'] = None if al is None else (not al)
    return fr, meta
SETS = {"all": load(REC), "h1": load(REC[:1]), "h2": load(REC[1:])}
BASE = {}
def run(pol, fill, part):
    fr, meta = SETS[part]
    return B.run(fr, meta, dict(pol, directional=False), {"pauses": True, "fill": fill})
for fill in ("touch", "through"):
    for part in SETS:
        BASE[(fill, part)] = run({}, fill, part)

def row_str(arg):
    name, pol = arg
    if name == "__HDR__":
        return "\n== hedge economics on the admission rules (give = max hedge EV cost vs fair; cap = tighter pair cap; frac = share of imbalance per order)"
    return _row(name, pol)

def row(name, pol):
    JOBS.append((name, pol))

JOBS = []

def _row(name, pol):
    out = [name]
    for fill in ("touch", "through"):
        r = run(pol, fill, "all"); b = BASE[(fill, "all")]
        br = {x['slug']: x for x in b['rows']}; rr = {x['slug']: x for x in r['rows']}
        d = [float(rr[s]['trading_pnl']) - float(br[s]['trading_pnl']) for s in br
             if int(br[s]['fills']) or int(rr[s]['fills'])]
        n = len(d); m = sum(d) / n if n else 0
        se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n) if n > 1 else 0
        halves = [float(run(pol, fill, h)['1_settled_trading_pnl_after_fees_usd']) for h in ("h1", "h2")]
        sp = float(r['spend_usd'])
        out.append(f"{fill}: {float(r['1_settled_trading_pnl_after_fees_usd']):+6.2f} (m {float(r['6_matched_pnl_usd']):+5.2f} r {float(r['6_residual_pnl_usd']):+6.2f}) "
                   f"f {r['fills']:3} spend {sp:6.2f} ret {(float(r['1_settled_trading_pnl_after_fees_usd'])/sp*100 if sp else 0):+5.1f}% "
                   f"halves {halves[0]:+5.2f}/{halves[1]:+5.2f} paired {m:+.3f}±{se:.3f}")
    return " | ".join(out)

if __name__ != "__main__":
    raise SystemExit
print(__doc__)
for fill in ("touch", "through"):
    b = BASE[(fill, "all")]
    print(f"baseline {fill}: {float(b['1_settled_trading_pnl_after_fees_usd']):+.2f} fills {b['fills']} spend {float(b['spend_usd']):.2f}")
print("\n== admission grid (edge = bid below intl fair; dog = extra edge for underdogs; weaken = fair drop over 300 s)")
for e, dog, wk in itertools.product(("0.01", "0.015", "0.02", "0.025"), ("0", "0.01", "0.02"), ("0", "0.02")):
    row(f"edge {e} dog+{dog} weaken {wk}", {"require_fair": True, "edge_min": "0", "ev_fill_loss": e, "dog_extra": dog, "weaken": wk})
JOBS.append(("__HDR__", None)); HDR2 = ("\n== hedge economics on the admission rules (give = max hedge EV cost vs fair; cap = tighter pair cap; frac = share of imbalance per order)")
for adm in (("0.02", "0", "0"), ("0.015", "0.01", "0.02"), ("0.01", "0.01", "0.02")):
    for hg, hpc, hf in itertools.product((None, "0", "0.01"), (None, "0.95"), (None, "0.5")):
        if hg is None and hpc is None and hf is None:
            continue
        pol = {"require_fair": True, "edge_min": "0", "ev_fill_loss": adm[0], "dog_extra": adm[1], "weaken": adm[2]}
        if hg is not None: pol["hedge_give"] = hg
        if hpc is not None: pol["hedge_pair_cap"] = hpc
        if hf is not None: pol["hedge_frac"] = hf
        row(f"edge {adm[0]} dog+{adm[1]} weaken {adm[2]} | hedge give {hg} cap {hpc} frac {hf}", pol)

import multiprocessing as mp
with mp.get_context("fork").Pool(3) as pool:
    for line in pool.imap(row_str, JOBS):
        print(line, flush=True)
