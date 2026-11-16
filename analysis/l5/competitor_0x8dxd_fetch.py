"""READ-ONLY capture of 0x8dxd's venue-reported history.

Identity VERIFIED before capture: data-api v2 returns name "0x8dxd" and
pseudonym "Blushing-Fine" for proxy_wallet
0x63ce342161250d705dc0b16df89036c8e5f9ba9a on the activity, trades and
positions routes. The association was checked, not assumed.

v1 is retired (410 Gone, migration -> /v2/). v2 wraps rows in {"data":
[...]} and is snake_case; `asset` became `token_id`.
"""
import json, os, sys, time, urllib.parse, urllib.request

BASE = "https://data-api.polymarket.com/v2"
W = "0x63ce342161250d705dc0b16df89036c8e5f9ba9a"
OUT = "/home/user/kalshi-arb-bot/data/l5/competitor_0x8dxd"
PAGE = 500


def get(url, tries=4):
    last = None
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers={
                "Accept": "application/json", "User-Agent": "research-client"})
            with urllib.request.urlopen(r, timeout=25) as h:
                return json.load(h)
        except Exception as exc:
            last = exc
            time.sleep(1.5 * (i + 1))
    raise last


def page_all(route):
    """Cursor pagination. `offset` is REJECTED (400) on v2; the envelope
    carries pagination.next_cursor and has_more, so that is the contract."""
    rows, cur, pages = [], None, 0
    while True:
        url = "%s/%s?user=%s&limit=%d" % (BASE, route, W, PAGE)
        if cur:
            url += "&cursor=" + urllib.parse.quote(cur, safe="")
        d = get(url)
        got = d.get("data") if isinstance(d, dict) else d
        if not isinstance(got, list):
            break
        rows.extend(got)
        pg = (d.get("pagination") or {}) if isinstance(d, dict) else {}
        pages += 1
        if pages % 10 == 1 or not pg.get("has_more"):
            print("  %s page %d  +%d  total %d  has_more=%s"
                  % (route, pages, len(got), len(rows), pg.get("has_more")))
            sys.stdout.flush()
        if not pg.get("has_more"):
            break
        cur = pg.get("next_cursor")
        if not cur:
            break
        if pages > 300:
            print("  %s: page cap reached" % route); break
        time.sleep(0.2)
    return rows


for route in ("trades", "activity", "positions"):
    try:
        rows = page_all(route)
    except Exception as exc:
        print("%s FAILED: %s %s" % (route, type(exc).__name__, exc)); continue
    p = os.path.join(OUT, "%s.jsonl" % route)
    with open(p, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print("%s -> %d rows -> %s" % (route, len(rows), p))
