"""Read-only sampler: what would a resting order earn under the PMUS Liquidity Incentive Program?

Public keyless GETs only (gateway /v1/markets/<slug>/book). No orders, no credentials.
For every snapshot and each side it applies the venue's published rule
(docs.polymarket.us/incentives/liquidity):

  * walk from the best price outward until Target Size (raw contracts) is reached;
    orders inside the walk score DF^(ticks from best) x size, orders beyond do not;
  * a side that never reaches Target Size does not qualify;
  * where the programme has a Max Spread, both sides must qualify and their
    size-adjusted prices must be no more than 2 x Max Spread apart, else the second pays nobody.

Output: one JSON line per market per sample. `summarise` turns a file into the share a
hypothetical X contracts resting at the best price (or one tick behind) would have taken.
These are MODELLED shares against the book as it stood - not earnings, not fills.
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

GATEWAY = "https://gateway.polymarket.us"
TICK = 0.01


def get(path, timeout=15):
    req = urllib.request.Request(GATEWAY + path, headers={"User-Agent": "research-client"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def levels(rows):
    return [(float(r["px"]["value"]), float(r["qty"])) for r in rows or []]


def walk(lv, target, df, tick=TICK):
    """lv sorted best-first. Returns (qualifies, size_adjusted_px, score, raw_in_walk)."""
    if not lv:
        return False, None, 0.0, 0.0
    best = lv[0][0]
    raw = score = 0.0
    for px, q in lv:
        ticks = round(abs(px - best) / tick)
        raw += q
        score += (df ** ticks) * q
        if raw >= target:
            return True, px, score, raw
    return False, lv[-1][0], score, raw


def measure(slug, prog):
    md = get("/v1/markets/%s/book" % slug)["marketData"]
    bids = sorted(levels(md.get("bids")), key=lambda x: -x[0])
    asks = sorted(levels(md.get("offers")), key=lambda x: x[0])
    df, tgt, ms = float(prog["discountFactor"]), float(prog["targetSize"]), prog.get("maxSpread")
    ms = float(ms) if ms is not None else None
    bq, bpx, bs, braw = walk(bids, tgt, df)
    aq, apx, as_, araw = walk(asks, tgt, df)
    pays_bid, pays_ask = bq, aq
    if ms is not None:
        ok = bq and aq and (apx - bpx) <= 2 * ms + 1e-9
        pays_bid = pays_ask = ok
    st = md.get("stats") or {}
    # contracts already resting within Max Spread of the simple mid: what the book lacks to pay
    band_bid = band_ask = None
    if ms is not None and bids and asks:
        mid = (bids[0][0] + asks[0][0]) / 2
        band_bid = sum(q for px, q in bids if px >= mid - ms - 1e-9)
        band_ask = sum(q for px, q in asks if px <= mid + ms + 1e-9)
    return {
        "band_bid_raw": band_bid, "band_ask_raw": band_ask,
        "t": time.time(), "slug": slug, "program": prog["programId"], "state": md.get("state"),
        "best_bid": bids[0][0] if bids else None, "best_ask": asks[0][0] if asks else None,
        "bid_top_qty": bids[0][1] if bids else 0, "ask_top_qty": asks[0][1] if asks else 0,
        "bid_score": round(bs, 2), "ask_score": round(as_, 2),
        "bid_raw": braw, "ask_raw": araw, "bid_adj_px": bpx, "ask_adj_px": apx,
        "pays_bid": pays_bid, "pays_ask": pays_ask,
        "notional": float(((st.get("notionalTraded") or {}).get("value")) or 0),
        "shares": float(st.get("sharesTraded") or 0),
    }


def run(spec_path, out_path, until_epoch):
    spec = json.load(open(spec_path))   # [{"slug":..., "every_s":..., "program":{...}}]
    due = {s["slug"]: 0.0 for s in spec}
    pool = ThreadPoolExecutor(8)
    with open(out_path, "a") as out:
        while time.time() < until_epoch:
            now = time.time()
            todo = [s for s in spec if due[s["slug"]] <= now]
            for s in todo:
                due[s["slug"]] = now + s["every_s"]

            def one(s):
                try:
                    return measure(s["slug"], s["program"])
                except Exception as e:   # market not open yet / closed / transient
                    return {"t": time.time(), "slug": s["slug"], "error": type(e).__name__}
            for row in pool.map(one, todo):
                out.write(json.dumps(row) + "\n")
            out.flush()
            time.sleep(1.0)


def summarise(path, pools, sizes=(100, 500, 1000, 2500)):
    """pools: {programId: (pool_usd_per_period, n_markets_sharing)}."""
    by = {}
    for line in open(path):
        r = json.loads(line)
        if "error" in r:
            continue
        by.setdefault(r["slug"], []).append(r)
    out = {}
    for slug, rows in sorted(by.items()):
        n = len(rows)
        d = {"samples": n, "program": rows[0]["program"],
             "first": rows[0]["t"], "last": rows[-1]["t"],
             "pays_bid_frac": sum(r["pays_bid"] for r in rows) / n,
             "pays_ask_frac": sum(r["pays_ask"] for r in rows) / n,
             "median_bid_score": sorted(r["bid_score"] for r in rows)[n // 2],
             "median_ask_score": sorted(r["ask_score"] for r in rows)[n // 2],
             "median_bid_top": sorted(r["bid_top_qty"] for r in rows)[n // 2],
             "median_ask_top": sorted(r["ask_top_qty"] for r in rows)[n // 2],
             "notional_traded_in_window": rows[-1]["notional"] - rows[0]["notional"],
             "shares_traded_in_window": rows[-1]["shares"] - rows[0]["shares"]}
        for x in sizes:
            # mean over ALL seconds of (pays ? x/(score+x) : 0), each side worth half the period
            sb = sum((x / (r["bid_score"] + x)) if r["pays_bid"] else 0 for r in rows) / n
            sa = sum((x / (r["ask_score"] + x)) if r["pays_ask"] else 0 for r in rows) / n
            d["share_of_pool_at_best_%d" % x] = round((sb + sa) / 2, 4)
        out[slug] = d
    return out


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3], float(sys.argv[4]))
    else:
        print(json.dumps(summarise(sys.argv[2], {}), indent=1))
