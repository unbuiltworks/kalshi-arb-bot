"""P9-D: measure every Gemini family by the completion-centric criterion.

    python3 analysis/gemini/p9d_families.py <out.json>        # collect
    python3 analysis/gemini/p9d_families.py <out.json> report # measure

RAW VOLUME IS NO LONGER THE RANKING CRITERION. P8 showed a market can print
plenty and still never offer us the other side, and P9-A/B showed that the
other side arriving is the thing that decides the economics. So a family is
ranked by how often a first leg would later have a complement it could pair
with, how long that takes, and at what pair cost.

PUBLIC ENDPOINTS ONLY, rate limited globally, no credential, no order:
  GET /v1/prediction-markets/events?limit&offset&category
  GET /v1/trades/{instrumentSymbol}

A CAVEAT THAT MUST TRAVEL WITH THE NUMBERS. `expiryDate` does not mean the
same thing in every family: a BTC5m contract expires when its window closes,
while a sports contract's expiry is an administrative settlement date weeks
after the game. Time-to-expiry is therefore NOT comparable across families and
is left out of the cross-family table; the completion measures, which depend
only on the tape's own order, are comparable.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from src.gemini_execution import completion_labels as CL       # noqa: E402
from src.gemini_execution import tape_normalize as TN          # noqa: E402

BASE = "https://api.gemini.com"
RATE = 5.0
_lock = threading.Lock()
_next = [0.0]


def _slot():
    with _lock:
        now = time.time()
        t = max(now, _next[0])
        _next[0] = t + 1.0 / RATE
    d = t - time.time()
    if d > 0:
        time.sleep(d)


def get(path, tries=3):
    for i in range(tries):
        _slot()
        try:
            with urllib.request.urlopen(BASE + path, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return {"__error__": f"HTTP {e.code}"}
            if i == tries - 1:
                return {"__error__": f"HTTP {e.code}"}
            time.sleep(1.0 * (i + 1))
        except Exception as e:                                  # noqa: BLE001
            if i == tries - 1:
                return {"__error__": str(e)}
            time.sleep(1.0 * (i + 1))
    return {"__error__": "unreachable"}


def events(category=None, cap=800):
    out, off = [], 0
    while off < cap:
        q = f"/v1/prediction-markets/events?limit=100&offset={off}"
        if category:
            q += f"&category={category}"
        d = get(q)
        if not isinstance(d, dict) or "data" not in d:
            break
        rows = d["data"]
        out += rows
        tot = (d.get("pagination") or {}).get("total") or 0
        off += 100
        if off >= tot or not rows:
            break
    return out


def trades(sym):
    d = get(f"/v1/trades/{sym}?limit_trades=500")
    if isinstance(d, dict):
        return None
    return [[int(t["timestamp"]), str(t.get("type", "")).lower(),
             str(t["price"]), str(t["amount"])]
            for t in d if t.get("price") and t.get("amount")]


def collect(out_path):
    cats = sorted({e.get("category") for e in events(cap=200)} - {None})
    print(f"categories: {cats}", file=sys.stderr, flush=True)
    evs, seen = [], set()
    for c in cats:
        got = events(category=c)
        for e in got:
            if e.get("ticker") in seen:
                continue
            seen.add(e.get("ticker"))
            evs.append(e)
        print(f"  {c}: {len(got)} events", file=sys.stderr, flush=True)
    # only events that traded at all -- a market with no volume cannot answer
    # a question about whether it offers both sides
    live = [e for e in evs if float(e.get("volume") or 0) > 0]
    print(f"events {len(evs)}, with volume {len(live)}", file=sys.stderr, flush=True)

    jobs = []
    for e in live:
        for c in (e.get("contracts") or []):
            sym = c.get("instrumentSymbol")
            if sym:
                jobs.append((e, c, sym))
    print(f"contracts to pull: {len(jobs)}", file=sys.stderr, flush=True)

    def one(job):
        e, c, sym = job
        tp = trades(sym)
        sub = e.get("subcategory") or {}
        return {
            "symbol": sym, "event_ticker": e.get("ticker"),
            "category": e.get("category"),
            "subcategory": (sub.get("name") if isinstance(sub, dict) else None),
            "template": e.get("template"), "title": e.get("title"),
            "duration": None,
            "day": (str(e.get("startTime") or e.get("effectiveDate") or "")[:10]
                    or None),
            "hour": 0,
            "event_volume": e.get("volume"),
            "start": c.get("effectiveDate") or e.get("effectiveDate"),
            "expiry": c.get("expiryDate") or e.get("expiryDate"),
            "resolution": c.get("resolutionSide"),
            "status": c.get("status"),
            "price_increment": c.get("priceIncrement"),
            "qty_min": c.get("quantityMinimum"),
            "prints": tp or [], "tape_readable": tp is not None,
        }

    rows, done = [], 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(one, jobs):
            done += 1
            rows.append(r)
            if done % 200 == 0:
                print(f"  {done}/{len(jobs)}", file=sys.stderr, flush=True)
    json.dump(rows, open(out_path, "w"))
    print(f"wrote {len(rows)} contracts", file=sys.stderr)


# --------------------------------------------------------------------------
def measure(path):
    rows = json.load(open(path))
    for r in rows:
        kept, _dropped = TN.dedupe_prints(r.get("prints") or [])
        r["prints"] = kept

    agg = defaultdict(lambda: {
        "contracts": 0, "traded": 0, "prints": 0, "opens": 0,
        "comp_any": 0, "le100": 0, "le098": 0, "le095": 0, "n3": 0,
        "lat": [], "best_pairs": [], "two_sided": 0, "maker_band": 0,
        "settled": 0, "notional": F(0),
    })
    for r in rows:
        key = (r.get("category") or "?", r.get("subcategory") or "?")
        a = agg[key]
        a["contracts"] += 1
        pr = r.get("prints") or []
        if not pr:
            continue
        a["traded"] += 1
        a["prints"] += len(pr)
        a["notional"] += sum(F(str(p[2])) * F(str(p[3])) for p in pr)
        sides = {("YES" if p[1] == "sell" else "NO") for p in pr}
        a["two_sided"] += int(len(sides) > 1)
        a["settled"] += int(bool(r.get("resolution")))
        for row in CL.label_opens(r):
            a["opens"] += 1
            a["comp_any"] += int(row["y_comp_any"])
            a["le100"] += int(row["y_pair_le_1.00"])
            a["le098"] += int(row["y_pair_le_0.98"])
            a["le095"] += int(row["y_pair_le_0.95"])
            a["n3"] += int(row["y_n_qualifying_1.00"] >= 3)
            # maker-rebate eligibility is a PRICE BAND, reported separately
            # from economics and never added into it
            a["maker_band"] += int(F(20, 100) <= row["price"] <= F(80, 100))
            if row["y_latency_1.00_s"] is not None:
                a["lat"].append(row["y_latency_1.00_s"])
            if row["y_best_pair"] is not None:
                a["best_pairs"].append(float(row["y_best_pair"]))

    def pct(n, d):
        return f"{n / d:.1%}" if d else "-"

    print(f"\n{'='*140}\nP9-D  every Gemini family, ranked by whether it gives "
          f"us the other side\n{'='*140}")
    print(f"{'category':>14} {'subcategory':>22} {'ctr':>5} {'trd':>5} "
          f"{'prints':>7} {'opens':>7} {'2-sided':>8} {'comp ever':>10} "
          f"{'pair<=1.00':>11} {'pair<=0.95':>11} {'>=3@1.00':>9} "
          f"{'med lat':>8} {'med best pair':>13} {'maker band':>11} {'$notional':>11}")
    out = []
    for (cat, sub), a in agg.items():
        if a["traded"] < 5:
            continue
        lat = sorted(a["lat"])
        bp = sorted(a["best_pairs"])
        out.append((a["le100"] / a["opens"] if a["opens"] else 0, cat, sub, a, lat, bp))
    for _k, cat, sub, a, lat, bp in sorted(out, reverse=True):
        print(f"{cat:>14} {sub[:22]:>22} {a['contracts']:>5} {a['traded']:>5} "
              f"{a['prints']:>7} {a['opens']:>7} "
              f"{pct(a['two_sided'], a['traded']):>8} "
              f"{pct(a['comp_any'], a['opens']):>10} "
              f"{pct(a['le100'], a['opens']):>11} "
              f"{pct(a['le095'], a['opens']):>11} "
              f"{pct(a['n3'], a['opens']):>9} "
              f"{(f'{lat[len(lat)//2]:.0f}s' if lat else '-'):>8} "
              f"{(f'{bp[len(bp)//2]:.3f}' if bp else '-'):>13} "
              f"{pct(a['maker_band'], a['opens']):>11} "
              f"{float(a['notional']):>11.0f}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "report":
        measure(sys.argv[1])
    else:
        collect(sys.argv[1])
