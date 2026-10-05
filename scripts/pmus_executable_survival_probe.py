"""What historical EXECUTABLE price data does PMUS still serve, if any?

The gating question for the underdog price x time calibration. A calibration
needs, at a checkpoint inside a window that has already closed, the ASK a buyer
could actually have paid at that moment. Three sources could in principle supply
that, and this probe establishes which of them exist rather than assuming:

  1. a historical BOOK or BBO route on the venue, for a market that has settled;
  2. a trade-history route (NOT executable -- a print is not an ask -- but its
     existence or absence is worth recording);
  3. our own recorded WebSocket tape, which is bounded by the recorder volume's
     12 GiB retention and is inventoried separately.

For each candidate route the status code is what matters, and 404 versus 200 is
the whole answer. Bodies are kept short but verbatim.

NOTHING IS INFERRED FROM THIS PROBE. It does not price anything. It records what
the venue serves so that the calibration can be built only on data that really
exists, and so that any window lacking a causal executable quote is EXCLUDED
rather than filled in from a midpoint, a settlement row, a last trade or a later
book state.

Unauthenticated and GET-only.
"""
from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request

GATEWAY = "https://gateway.polymarket.us"
UA = "pmus-executable-survival-probe (read-only)"
CTX = ssl.create_default_context()

#: Markets chosen to span the two regimes, and chosen BEFORE any result was
#: seen: one settled window from the settlement-only region (no recorded tape
#: exists for it at all) and one from the region our recorder covered.
TARGETS = [
    ("settlement_only_region", "cpc-btc-updown-15m-2026-09-25-1800z"),
    ("recorded_tape_region", "cpc-btc-updown-15m-2026-10-03-1800z"),
]

#: Candidate routes. Some are known to exist for LIVE markets; the question
#: here is whether they answer for a market that has already settled.
ROUTES = [
    "/v1/markets/%s/book",
    "/v1/markets/%s/bbo",
    "/v1/markets/%s/trades",
    "/v1/markets/%s/history",
    "/v1/markets/%s/prices-history",
    "/v1/markets/%s/price-history",
    "/v1/markets/%s/candles",
    "/v1/markets/%s/ohlc",
    "/v1/book/%s",
    "/v1/trades/%s",
    "/v1/trades?market=%s",
    "/v1/prices-history?market=%s",
]


def get(path: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(GATEWAY + path, headers={"User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
            body = (r.read() or b"").decode("utf-8", "replace")
            return {"path": path, "status": int(r.status),
                    "ms": int((time.time() - t0) * 1000),
                    "bytes": len(body), "body": body[:1200]}
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read() or b""
        except Exception:                                      # noqa: BLE001
            pass
        return {"path": path, "status": int(exc.code),
                "ms": int((time.time() - t0) * 1000),
                "body": body.decode("utf-8", "replace")[:600]}
    except Exception as exc:                                   # noqa: BLE001
        return {"path": path, "status": None,
                "ms": int((time.time() - t0) * 1000),
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def main() -> int:
    out = {"LABEL": "PMUS_EXECUTABLE_SURVIVAL_PROBE",
           "authenticated": False, "method": "GET", "results": []}
    for regime, slug in TARGETS:
        for tmpl in ROUTES:
            rec = get(tmpl % slug)
            rec["regime"] = regime
            rec["slug"] = slug
            out["results"].append(rec)
            print("%-22s %-3s %s" % (regime, rec.get("status"), rec["path"]),
                  flush=True)
            if rec.get("status") == 200:
                print("    BODY %s" % (rec.get("body") or "")[:900], flush=True)
            time.sleep(0.2)

    served = sorted({r["path"].split("?")[0] for r in out["results"]
                     if r.get("status") == 200})
    print("\nROUTES ANSWERING 200: %s" % (served or "NONE"), flush=True)
    with open("executable_survival_probe.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
