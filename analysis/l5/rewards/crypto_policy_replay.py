"""Read-only replay of the crypto reward lane's own quoting rule.

  record <out.jsonl.gz> <minutes> [slug ...]   public books every ~10 s
  replay <file.jsonl.gz>                        policy table

Public keyless GETs only. No orders, no credentials. `replay` runs
src/pmus_crypto_rewards.quotes -- the function the engine calls -- on every
snapshot, for several sizes and depths, and reports per market:

  quotable   share of snapshots in which the policy would rest both sides
  usd_day    MODELLED reward: mean share over ALL snapshots x $150 / 3
  traded     contracts the venue reports traded in the market over the file
  fill_qty   UPPER BOUND on contracts filled against us: at the next
             snapshot the best price on our side is below our bid AND the
             venue's traded count rose; we are given all of that volume up
             to our size (the tape does not show who was hit)
  mo5m_usd   those contracts marked at the mid five minutes later, minus
             cost (the file's last mid near its end). Negative = picked off
  worst_leg  WORST-CASE inventory loss of the policy in that market: the
             dearest bid it rested, filled alone in full and settled at zero

Everything is MODELLED against displayed books. Nothing here is earned.
Accepts full-book rows ({"t","slug","book"}) and the 22 Nov summary rows
(depth_sampler.py), which are rebuilt into a three-level book that keeps
each side's best price, top size, raw size, walk end and score.
"""
import gzip
import json
import sys
import time
import urllib.request
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src import pmus_crypto_rewards as CR  # noqa: E402

GATEWAY = "https://gateway.polymarket.us"
T = D("0.01")
PROG = {"df": 0.3, "targetSize": 2000.0, "maxSpread": 0.05, "pool": 150.0,
        "period_s": 86400.0, "M_program": 3}
POLICIES = [("1 tick back", {"allow_touch": False}),
            ("touch if needed", {"allow_touch": True}),
            ("at touch", {"depth_ticks": 0})]
SIZES = (25, 44, 61, 100)


def get(path):
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"User-Agent": "research-client"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def current_slugs():
    d = get("/v1/events?active=true&closed=false&limit=200&categories=crypto")
    out = {}
    for ev in d.get("events") or []:
        for m in ev.get("markets") or []:
            s = str(m.get("slug") or "")
            a = CR.asset_of(s)
            if a and (a not in out or (m.get("endDate") or "") <
                      out[a][1]):
                out[a] = (s, m.get("endDate") or "")
    return [v[0] for v in out.values()]


def record(path, minutes, slugs):
    slugs = slugs or current_slugs()
    end = time.time() + 60 * float(minutes)
    n = 0
    with gzip.open(path, "at") as f:
        while time.time() < end:
            for s in slugs:
                try:
                    md = get("/v1/markets/%s/book" % s)["marketData"]
                    f.write(json.dumps({"t": time.time(), "slug": s, "book": {
                        "bids": md.get("bids"), "offers": md.get("offers")},
                        "shares": float((md.get("stats") or {}).get(
                            "sharesTraded") or 0)},
                        separators=(",", ":")) + "\n")
                    n += 1
                except Exception as exc:                 # noqa: BLE001
                    print("error", s, exc, file=sys.stderr)
            f.flush()
            time.sleep(10)
    print("recorded", n, "snapshots of", slugs)


def _synth_side(best, top, raw, adj, score, sign):
    k = int(round(abs(adj - best) / 0.01))
    if k == 0 or raw <= top:
        return [(best, raw)]
    rest = raw - top
    if k == 1:
        return [(best, top), (adj, rest)]
    far = 0.3 ** k
    x = (score - top - far * rest) / (0.3 - far)
    x = min(max(x, 0.0), rest)
    lv = [(best, top)]
    if x > 0:
        lv.append((round(best + sign * 0.01, 2), x))
    if rest - x > 0:
        lv.append((adj, rest - x))
    return lv


def book_of(row):
    if "book" in row:
        return row["book"]
    def rows(lv):
        return [{"px": {"value": str(p)}, "qty": str(q)} for p, q in lv
                if q > 0]
    return {"bids": rows(_synth_side(row["best_bid"], row["bid_top_qty"],
                                     row["bid_raw"], row["bid_adj_px"],
                                     row["bid_score"], -1)),
            "offers": rows(_synth_side(row["best_ask"], row["ask_top_qty"],
                                       row["ask_raw"], row["ask_adj_px"],
                                       row["ask_score"], +1))}


def replay(path):
    by = {}
    for ln in gzip.open(path, "rt"):
        r = json.loads(ln)
        if CR.tradable(r["slug"]):
            by.setdefault(r["slug"], []).append(r)
    print("file:", path)
    for slug, rows in sorted(by.items()):
        rows.sort(key=lambda r: r["t"])
        hours = (rows[-1]["t"] - rows[0]["t"]) / 3600
        vol = [float(r.get("shares") or 0) for r in rows]
        print("\n%s  %d snapshots, %.2f h, traded %.0f contracts" % (
            slug, len(rows), hours, vol[-1] - vol[0]))
        print("  %-16s %5s %9s %9s %9s %10s %10s" % (
            "policy", "qty", "quotable", "usd_day", "fill_qty", "mo5m_usd",
            "worst_leg"))
        books = [book_of(r) for r in rows]
        tops = [CR.assess(bk, PROG, T) for bk in books]
        mids = [None if a["best_bid_ex_own"] is None or
                a["best_ask_ex_own"] is None else
                (a["best_bid_ex_own"] + a["best_ask_ex_own"]) / 2
                for a in tops]
        ts = [r["t"] for r in rows]

        def mid_after(i, secs=300):
            j = i
            while j < len(ts) - 1 and ts[j] - ts[i] < secs:
                j += 1
            while j > i and mids[j] is None:
                j -= 1
            return mids[j]
        for name, c in POLICIES:
            for qty in SIZES:
                ok = 0
                share = mark = fills = worst = 0.0
                prev = None
                for i, bk in enumerate(books):
                    q = CR.quotes(book_md=bk, program=PROG, tick=T,
                                  pos={"YES": 0, "NO": 0},
                                  cost={"YES": 0, "NO": 0},
                                  c={**c, "qty": qty, "min_qty": 1})
                    if prev:
                        bb = tops[i]["best_bid_ex_own"]
                        ba = tops[i]["best_ask_ex_own"]
                        mid = mid_after(i)
                        for side, px in prev.items():
                            now_best = bb if side == "YES" else (
                                None if ba is None else 1 - ba)
                            got = min(float(qty), vol[i] - vol[i - 1])
                            if now_best is not None and mid is not None \
                                    and now_best < px - 1e-9 and got > 0:
                                fills += got
                                fair = mid if side == "YES" else 1 - mid
                                mark += (fair - px) * got
                    prev = None
                    if q["YES"] and q["NO"]:
                        ok += 1
                        share += q["model"]["share"]
                        prev = {s: float(q[s]["px"]) for s in ("YES", "NO")}
                        worst = max(worst, max(prev.values()) * qty)
                n = len(books)
                print("  %-16s %5d %8.0f%% %9.2f %9.1f %10.2f %10.2f" % (
                    name, qty, 100 * ok / n, 50 * share / n, fills, mark,
                    worst))


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "record":
        record(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif len(sys.argv) == 3 and sys.argv[1] == "replay":
        replay(sys.argv[2])
    else:
        sys.exit(__doc__)
