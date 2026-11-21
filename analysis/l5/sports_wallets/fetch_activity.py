"""Pull one wallet's public activity (wallet = first argument) from Polymarket's data API, newest
first, back to a cut-off. READ-ONLY, public data, one wallet, one request a
second. Writes one compact JSON row per activity line.

usage: fetch_activity.py <out.jsonl> <stop_before_utc_iso> [types]
"""
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request

W = sys.argv.pop(1)
BASE = "https://data-api.polymarket.com/v2/activity"
KEEP = ("timestamp", "type", "side", "size", "usdc_size", "price",
        "condition_id", "token_id", "outcome", "outcome_index", "slug",
        "event_slug", "transaction_hash")


def main():
    out, stop = sys.argv[1], dt.datetime.fromisoformat(
        sys.argv[2].replace("Z", "+00:00")).timestamp()
    types = sys.argv[3] if len(sys.argv) > 3 else None
    cursor, n, pages = None, 0, 0
    with open(out, "w") as fh:
        while True:
            q = {"user": W, "limit": 1000}
            if types:
                q["type"] = types
            if cursor:
                q["cursor"] = cursor
            url = BASE + "?" + urllib.parse.urlencode(q)
            for attempt in range(5):
                try:
                    req = urllib.request.Request(
                        url, headers={"Accept": "application/json",
                                      "User-Agent": "research-client"})
                    with urllib.request.urlopen(req, timeout=40) as r:
                        doc = json.loads(r.read().decode("utf-8"))
                    break
                except Exception as exc:               # noqa: BLE001
                    print("retry", attempt, type(exc).__name__, flush=True)
                    time.sleep(3 * (attempt + 1))
            else:
                print("GAVE_UP pages", pages, flush=True)
                return 1
            rows = doc.get("data") or []
            pages += 1
            done = False
            for r in rows:
                if r["timestamp"] < stop:
                    done = True
                    break
                fh.write(json.dumps({k: r.get(k) for k in KEEP},
                                    separators=(",", ":")) + "\n")
                n += 1
            pg = doc.get("pagination") or {}
            cursor = pg.get("next_cursor")
            if pages % 10 == 0:
                print("pages", pages, "rows", n, "oldest", dt.datetime
                      .fromtimestamp(rows[-1]["timestamp"], dt.timezone.utc)
                      .isoformat() if rows else None, flush=True)
            if done or not rows or not pg.get("has_more") or not cursor:
                break
            time.sleep(1.0)
    print("DONE pages", pages, "rows", n, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
