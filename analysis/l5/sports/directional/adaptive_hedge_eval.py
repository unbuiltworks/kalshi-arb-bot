"""Adaptive hedge (PMUS_RN1_TF_ADAPTIVE_HEDGE / policy `adaptive_hedge`) vs
today's completion rules on recorded two-venue sports tapes.

OFFLINE, THEORETICAL: recorded books + international fair, modelled fills
(touch / through), 3 s latency, production pauses, maker fee 0, rewards NOT
modelled (pre-reward settled trading P&L only). Nothing here is realized.

  IN-SAMPLE      21 Nov two-venue tape (the tape every earlier trading-first
                 grid was run on; the adaptive grid below is chosen here).
  OUT-OF-SAMPLE  28 Nov recordings, closed games only. Nothing is tuned on
                 it: the grid is fixed before it is read and the "chosen"
                 row is the in-sample choice.

usage: adaptive_hedge_eval.py [oos_dir]   (oos_dir holds
       cross_venue_20261128_*.jsonl copies; omitted = in-sample only)
"""
import functools, glob, hashlib, itertools, json, math, os, sys
import multiprocessing as mp
import urllib.request
print = functools.partial(print, flush=True)
sys.path.insert(0, '.')
from src import pmus_directional_backtest as B

HERE = "analysis/l5/sports/directional/"
REC = ["analysis/l5/sports_wallets/cross_venue_20261121_2027_2217.jsonl.gz",
       "analysis/l5/sports_wallets/cross_venue_20261121_2218_0015_sizes.jsonl.gz"]
TF = {"require_fair": True, "edge_min": "0", "ev_fill_loss": "0.02"}
# (give_min, edge_k) x (give_max, risk_aversion). edge_k 0 = no discount for
# held edge (give_min then never binds, so it is listed once).
GRID = [{"give_min": gmin, "edge_k": ek, "give_max": gmax,
         "risk_aversion": lam}
        for (gmin, ek), (gmax, lam) in itertools.product(
            (("-0.02", "1"), ("-0.01", "1"), ("0", "1"), ("0", "0")),
            (("0.01", "0.005"), ("0.03", "0.005"), ("0.03", "0.05")))]


def ah_name(c):
    return "adaptive give [%s,%s] edge_k %s lam %s" % (
        c["give_min"], c["give_max"], c["edge_k"], c["risk_aversion"])


def policies(adm, tag):
    out = [(tag + " | hedge at pair cap", dict(adm)),
           (tag + " | hedge fair+1c reprice", dict(
               adm, hedge_give="0.01", hedge_mode="reprice"))]
    for c in GRID:
        out.append((tag + " | " + ah_name(c), dict(adm, adaptive_hedge=c)))
    return out


def outcome_meta(slugs, cache_path):
    """Settlement from the PMUS public market record: closed and
    outcomePrices ["1","0"] = YES won (fallback: the long side's price)."""
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    for s in sorted(slugs):
        if (cache.get(s) or {}).get("outcome_yes") is not None:
            continue
        try:
            req = urllib.request.Request(
                "https://gateway.polymarket.us/v1/market/slug/" + s,
                headers={"User-Agent": "research-client"})
            with urllib.request.urlopen(req, timeout=15) as r:
                m = (json.load(r) or {}).get("market") or {}
            out = None
            if m.get("closed"):
                op = m.get("outcomePrices")
                if isinstance(op, str):
                    op = json.loads(op)
                op = [str(x) for x in (op or [])]
                if op in (["1", "0"], ["0", "1"]):
                    out = 1 if op[0] == "1" else 0
                else:
                    for sd in m.get("marketSides") or []:
                        if sd.get("long") and str(sd.get("price")) in (
                                "0", "1", "0.0000", "1.0000"):
                            out = int(float(sd["price"]))
            cache[s] = {"tick": str(m.get("orderPriceMinTickSize") or "0.01"),
                        "outcome_yes": out, "closed": m.get("closed")}
        except Exception as exc:                          # noqa: BLE001
            cache.setdefault(s, {"tick": "0.01", "outcome_yes": None,
                                 "error": type(exc).__name__})
    json.dump(cache, open(cache_path, "w"), indent=1, sort_keys=True)
    return cache


def load(paths, meta):
    fr = B.load_cross_venue(paths)
    meta = json.loads(json.dumps(meta))
    for s, f in fr.items():
        al = B.align_intl(f)
        meta.setdefault(s, {})['flip'] = None if al is None else (not al)
    return fr, meta


def stats(d):
    n = len(d)
    m = sum(d) / n if n else 0.0
    se = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1) / n) if n > 1 else 0.0
    return sum(d), m, se, n


def paired(rr, br):
    return stats([float(rr[s]['trading_pnl']) - float(br[s]['trading_pnl'])
                  for s in br if int(br[s]['fills']) or int(rr[s]['fills'])])


_G = {}


def _run(pol):
    r = B.run(_G["fr"], _G["meta"], dict(pol, directional=False), _G["sim"])
    return r


def evaluate(title, fr, meta, chosen=None):
    print("\n" + "=" * 100 + "\n" + title)
    res = {}
    for fill in ("touch", "through"):
        sim = {"pauses": True, "fill": fill}
        allp = [("A", {})] + policies(TF, "TF edge 2c") + \
            policies({}, "baseline admission")
        _G.update(fr=fr, meta=meta, sim=sim)
        with mp.get_context("fork").Pool(4) as pool:
            done = dict(zip([n for n, _p in allp],
                            pool.map(_run, [p_ for _n, p_ in allp])))
        base = done["A"]
        brow = {x['slug']: x for x in base['rows']}
        print("\n[%s] settled markets replayed %d (skipped %d: %s)" % (
            fill, base['markets'], len(base['skipped']),
            dict((v, list(base['skipped'].values()).count(v))
                 for v in set(base['skipped'].values()))))
        hdr = ("  %-66s %7s %7s %7s %5s %3s %7s %8s | %-22s | %-22s" % (
            "policy", "settled", "matched", "resid", "fills", "mkt",
            "worstmk", "wc_sum", "paired vs baseline", "paired vs fair+1c"))
        for adm, tag in ((TF, "TF edge 2c"), ({}, "baseline admission")):
            print(hdr)
            pols = ([("A baseline (production core, no trading-first)", {})]
                    if adm else []) + policies(adm, tag)
            fixed = None
            for name, pol in pols:
                r = base if not pol else done[name]
                rows = {x['slug']: x for x in r['rows']}
                if name.endswith("fair+1c reprice"):
                    fixed = rows
                pm = [float(x['trading_pnl']) for x in r['rows']
                      if int(x['fills'])]
                wc = [min(float(x['pos']['YES']), float(x['pos']['NO'])) -
                      float(x['spend']) for x in r['rows'] if int(x['fills'])]
                pb = paired(rows, brow)
                pf = paired(rows, fixed) if fixed is not None else None
                dec = {}
                for x in r['rows']:
                    for k, v in x['decisions'].items():
                        if k.startswith('ah_') or k.startswith('hedge_'):
                            dec[k] = dec.get(k, 0) + v
                mark = " <== chosen in-sample" if chosen and \
                    name.endswith(chosen) else ""
                print("  %-66s %+7.2f %+7.2f %+7.2f %5d %3d %+7.2f %+8.2f | "
                      "%+6.2f %+.3f±%.3f n%-2d | %s%s" % (
                          name[:66],
                          float(r['1_settled_trading_pnl_after_fees_usd']),
                          float(r['6_matched_pnl_usd']),
                          float(r['6_residual_pnl_usd']), r['fills'],
                          r['markets_traded'], min(pm) if pm else 0.0,
                          sum(wc), pb[0], pb[1], pb[2], pb[3],
                          "%+6.2f %+.3f±%.3f n%-2d" % pf if pf else
                          " " * 22, mark))
                res[(fill, name)] = {
                    "settled": float(r['1_settled_trading_pnl_after_fees_usd']),
                    "vs_fixed": pf, "decisions": dec}
            print()
    return res


if __name__ == "__main__":
    print(__doc__)
    print("columns: settled/matched/resid = pre-reward settled trading P&L "
          "$ and its matched-pair / residual parts; worstmk = worst single "
          "market settled P&L; wc_sum = sum over traded markets of the "
          "P&L had every residual lost (downside); paired = sum, mean ± SE "
          "per market of (policy - reference) over markets either traded. "
          "ALL THEORETICAL.")
    print("grid (fixed before the out-of-sample tape was read): "
          "(give_min, edge_k) x (give_max, risk_aversion) = %d rules; other knobs at "
          "defaults %s" % (len(GRID), json.dumps(
              __import__('src.pmus_adaptive_hedge',
                         fromlist=['x']).DEFAULTS)))
    meta21 = json.load(open(HERE + "market_meta_20261121.json"))
    fr, meta = load(REC, meta21)
    res = evaluate("IN-SAMPLE: 21 Nov two-venue tape", fr, meta)
    # in-sample choice: best mean paired difference vs fair+1c, summed over
    # both fill models and both admission regimes (ties: first in grid)
    score = {}
    for c in GRID:
        nm = ah_name(c)
        score[nm] = sum(v["vs_fixed"][0] for (f, n), v in res.items()
                        if n.endswith(nm) and v["vs_fixed"])
    chosen = max(score, key=lambda k: score[k])
    print("in-sample choice (largest summed paired P&L vs fair+1c over both "
          "fill models and both admission regimes): %s  (%+.2f)" % (
              chosen, score[chosen]))
    print("in-sample scores: " + json.dumps(
        {k: round(v, 2) for k, v in score.items()}))
    if len(sys.argv) > 1:
        paths = sorted(glob.glob(os.path.join(
            sys.argv[1], "cross_venue_20261128_*.jsonl")))
        fr2 = B.load_cross_venue(paths)
        m28 = outcome_meta(fr2.keys(), HERE + "market_meta_20261128.json")
        fr2, meta2 = load(paths, m28)
        closed = [s for s in fr2 if (m28.get(s) or {}).get(
            "outcome_yes") is not None]
        print("\nOUT-OF-SAMPLE snapshot: %d files, %d markets recorded, %d "
              "closed with a settlement" % (len(paths), len(fr2), len(closed)))
        for p in paths:
            print("  %s  %d bytes  sha256 %s" % (
                os.path.basename(p), os.path.getsize(p),
                hashlib.sha256(open(p, 'rb').read()).hexdigest()[:16]))
        evaluate("OUT-OF-SAMPLE: 28 Nov recordings, closed games only "
                 "(n markets as shown; nothing tuned here)", fr2, meta2,
                 chosen=chosen)
