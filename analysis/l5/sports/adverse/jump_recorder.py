"""Read-only: every ~2 seconds, the best bid/ask (with sizes) and traded
volume of every IN-PLAY sports market on the PMUS public gateway whose bid
is between 0.20 and 0.80. Used to measure what the price does after a jump.
No credentials; nothing is placed.
usage: jump_recorder.py <out.jsonl> <until_epoch> [max_markets]"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

G = "https://gateway.polymarket.us"
SPORTS = ("atp", "wta", "nba", "nfl", "nhl", "sea", "epl", "lal", "cbb",
          "cfb", "lmx", "ucl", "mlb")


def get(path, t=8):
    r = urllib.request.Request(G + path, headers={
        "User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=t) as f:
        return json.load(f)


def px(x):
    try:
        return float((x or {}).get("value"))
    except Exception:
        return None


def discover(limit):
    """In-play markets: events flagged live, priced inside the band."""
    out = []
    try:
        d = get("/v1/events?active=true&closed=false&categories=sports"
                "&limit=500&live=true", t=20)
    except Exception:
        return out
    for ev in (d.get("events") if isinstance(d, dict) else d) or []:
        if not ev.get("live"):
            continue
        for m in ev.get("markets") or []:
            s = str(m.get("slug") or "")
            parts = s.split("-")
            if len(parts) < 3 or parts[1] not in SPORTS:
                continue
            b = px(m.get("bestBidQuote"))
            if b is None or not (0.20 <= b <= 0.80):
                continue
            out.append(s)
    return sorted(set(out))[:limit]


def one(slug):
    row = {"t": round(time.time(), 2), "s": slug}
    try:
        md = get("/v1/markets/%s/bbo" % slug)["marketData"]
        row.update(b=px(md.get("bestBid")), a=px(md.get("bestAsk")),
                   bq=md.get("bidDepth"), aq=md.get("askDepth"),
                   v=md.get("sharesTraded"), st=md.get("state"),
                   lp=px(md.get("lastTradePx")))
    except Exception as e:
        row["err"] = type(e).__name__
    return row


def main():
    out, until = sys.argv[1], float(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 16
    ex = ThreadPoolExecutor(8)
    slugs, last_disc = [], 0.0
    with open(out, "a") as f:
        while time.time() < until:
            t0 = time.time()
            if t0 - last_disc > 180 or not slugs:
                found = discover(limit)
                if found:
                    slugs = found
                last_disc = t0
                f.write(json.dumps({"t": round(t0, 2), "disc": slugs}) + "\n")
            for row in ex.map(one, slugs):
                f.write(json.dumps(row) + "\n")
            f.flush()
            time.sleep(max(0.2, 2.0 - (time.time() - t0)))


if __name__ == "__main__":
    main()
