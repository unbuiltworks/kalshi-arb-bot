"""Live full-L2 recorder off the PUBLIC gateway. No credentials, no deploy.

WHY THIS EXISTS. The authenticated WS tape on the recorder's volume is richer
(individual prints with venue ids) but can only be exported ~360 KB at a time
through the container-file API. The PUBLIC gateway, by contrast, serves the full
ladder to anyone:

    GET /v1/markets/{slug}/book   -> bids[] and offers[], 30-44 levels a side,
                                     plus the venue's own `transactTime`
    GET /v1/markets/{slug}/bbo    -> lastTradePx and CUMULATIVE sharesTraded

Round trip measured at ~0.2 s, so a few polls a second are comfortable.

WHAT THIS CANNOT DO, stated plainly because it decides how the output may be
used: the public feed does NOT carry individual trade prints. It gives a
cumulative `sharesTraded` counter and a last price. So between two polls we know
HOW MUCH traded and the last price it traded at -- we do not know how that volume
was split across prints or across price levels, and there is no venue trade id
and no taker direction.

For a causal fill model that means volume attribution at a non-touch level is an
INFERENCE, not an observation. Fills derived from it must be labelled as such and
must never be presented alongside print-derived fills as though they were the
same evidence.

Read-only: two GETs. It places no order and holds no credential.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

GATEWAY = "https://gateway.polymarket.us"
OUT_DIR = "data/pmus_public_l2"
POLL_S = float(os.getenv("L5_PUBLIC_POLL_S", "0.5"))


def _get(slug: str, path: str):
    r = subprocess.run(["curl", "-sS", "--max-time", "10",
                        "%s/v1/markets/%s/%s" % (GATEWAY, slug, path)],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout).get("marketData") or {}
    except Exception:                                        # noqa: BLE001
        return {}


def current_slug(now: float) -> str:
    t = datetime.fromtimestamp(now, tz=timezone.utc)
    m = (t.minute // 15) * 15
    return "cpc-btc-updown-15m-%04d-%02d-%02d-%02d%02dz" % (
        t.year, t.month, t.day, t.hour, m)


def run(minutes: float) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    stop = time.time() + minutes * 60
    path = os.path.join(OUT_DIR, "public_l2_%s.jsonl"
                        % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    n = 0
    with open(path, "a", encoding="utf-8") as fh:
        while time.time() < stop:
            t0 = time.time()
            slug = current_slug(t0)
            book = _get(slug, "book")
            bbo = _get(slug, "bbo")
            row = {"recv_t": t0, "slug": slug,
                   "transact_time": book.get("transactTime"),
                   "state": book.get("state"),
                   "bids": book.get("bids"), "offers": book.get("offers"),
                   "last_trade_px": (bbo.get("lastTradePx") or {}).get("value"),
                   "shares_traded": bbo.get("sharesTraded"),
                   "source": "PUBLIC_GATEWAY",
                   "labels": ("full L2; cumulative volume only -- NO individual "
                              "prints, no trade id, no taker direction")}
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            fh.flush()
            n += 1
            if n % 120 == 0:
                print("PUBLIC_L2 %d rows %s" % (n, slug), flush=True)
            time.sleep(max(0.0, POLL_S - (time.time() - t0)))
    print("PUBLIC_L2_DONE rows=%d path=%s" % (n, path), flush=True)


if __name__ == "__main__":
    run(float(sys.argv[1]) if len(sys.argv) > 1 else 20.0)
