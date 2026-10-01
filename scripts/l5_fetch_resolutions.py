"""Fetch AUTHORITATIVE settlement for the audited September cash population.

Settlement is read from the CLOB market record, keyed by **conditionId**, which
carries a per-token `winner` flag:

    GET https://clob.polymarket.com/markets/{condition_id}
    tokens: [{token_id, outcome, price, winner}, ...]

NOTHING IS INFERRED. Not from price, not from last trade, not from the absence of
a redemption, not from slug text, not from timing. If the venue does not state a
winner, the market is UNRESOLVED and says so.

Raw responses are written verbatim with provenance (url, fetched_at, status) so
every later classification can be traced to the bytes it came from. Resumable:
re-running skips ids already captured.

    python3 scripts/l5_fetch_resolutions.py <ids.json> <out.jsonl> [limit]
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

URL = "https://clob.polymarket.com/markets/{cid}"
UA = {"User-Agent": "l5-settlement-audit/1.0 (read-only accounting audit)"}
SLEEP_S = 0.12
RETRIES = 3


def fetch(cid: str) -> dict:
    url = URL.format(cid=cid)
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=UA, method="GET")
            with urllib.request.urlopen(req, timeout=20) as fh:
                body = fh.read().decode("utf-8")
                return {"condition_id": cid, "url": url, "status": fh.status,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "payload": json.loads(body)}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"condition_id": cid, "url": url, "status": 404,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "payload": None, "error": "not found"}
            if attempt == RETRIES - 1:
                return {"condition_id": cid, "url": url, "status": exc.code,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "payload": None, "error": f"HTTP {exc.code}"}
        except Exception as exc:                                 # noqa: BLE001
            if attempt == RETRIES - 1:
                return {"condition_id": cid, "url": url, "status": None,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "payload": None,
                        "error": f"{type(exc).__name__}: {exc}"[:200]}
        time.sleep(1.0 * (attempt + 1))
    return {"condition_id": cid, "url": url, "status": None, "payload": None,
            "error": "unreachable"}


def main(argv) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    ids = json.load(open(argv[1]))
    if isinstance(ids, dict):
        ids = ids.get("ids") or ids.get("zero_redemption") or []
    out_path = argv[2]
    limit = int(argv[3]) if len(argv) > 3 else None
    done = set()
    if os.path.exists(out_path):
        for line in open(out_path):
            try:
                done.add(json.loads(line)["condition_id"])
            except Exception:                                    # noqa: BLE001
                continue
    todo = [c for c in ids if c not in done]
    if limit:
        todo = todo[:limit]
    print(f"{len(ids)} ids, {len(done)} already captured, fetching {len(todo)}",
          flush=True)
    ok = miss = err = 0
    with open(out_path, "a") as fh:
        for i, cid in enumerate(todo, 1):
            rec = fetch(cid)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            if rec.get("payload"):
                ok += 1
            elif rec.get("status") == 404:
                miss += 1
            else:
                err += 1
            if i % 250 == 0:
                print(f"  {i}/{len(todo)}  ok={ok} notfound={miss} err={err}",
                      flush=True)
            time.sleep(SLEEP_S)
    print(f"done: ok={ok} notfound={miss} err={err}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
