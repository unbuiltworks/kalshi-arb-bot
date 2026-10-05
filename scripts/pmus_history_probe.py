"""PROBE: what do PMUS's public historical BTC15m routes actually answer?

Phase-0 reconnaissance for the settlement-history harvest. It answers four
questions with raw bytes rather than assumption:

  1. which slug form the venue serves -- `cpc-btc-updown-15m-...` (the market
     slug every execution path here uses) or the bare `btc-updown-15m-...`
     form the app's Past-markets URLs show;
  2. which route is the existence oracle, and what it returns for a window
     that should not exist at all;
  3. whether the settled OUTCOME (priceToBeat, final price, settlementPx) is
     carried on the market row itself, or only on /bbo -- this decides whether
     the backward walk needs one request per window or two;
  4. how far back the records go, sampled coarsely before anything is walked
     exhaustively.

UNAUTHENTICATED AND GET-ONLY. Every request is a plain urllib GET to the
gateway with no credential of any kind, so this cannot touch an account,
cannot place an order, and needs no secret. Raw response bodies are printed
in full so the record is auditable.
"""
from __future__ import annotations

import datetime as dt
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

GATEWAY = "https://gateway.polymarket.us"
UA = "pmus-history-probe (read-only public settlement history)"
CTX = ssl.create_default_context()


def get(path: str, timeout: float = 20.0) -> dict:
    """One public GET. Returns a dict describing the OUTCOME, never raises.

    The distinction this whole study depends on starts here: `status` 404 is
    "the venue does not serve this", while `error` is "we failed to ask".
    They are never conflated.
    """
    req = urllib.request.Request(GATEWAY + path, headers={"User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
            raw = r.read() or b""
            return {"path": path, "status": r.status,
                    "ms": int((time.time() - t0) * 1000),
                    "body": raw.decode("utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read() or b""
        except Exception:                                      # noqa: BLE001
            pass
        return {"path": path, "status": int(exc.code),
                "ms": int((time.time() - t0) * 1000),
                "body": raw.decode("utf-8", "replace")[:2000]}
    except Exception as exc:                                   # noqa: BLE001
        return {"path": path, "status": None,
                "ms": int((time.time() - t0) * 1000),
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def slugs_for(when: dt.datetime) -> list:
    stamp = "%04d-%02d-%02d-%02d%02dz" % (when.year, when.month, when.day,
                                          when.hour, when.minute)
    return ["cpc-btc-updown-15m-" + stamp, "btc-updown-15m-" + stamp]


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    now = now - dt.timedelta(minutes=now.minute % 15)

    # A settled window a few hours back, a known-good one from the committed
    # evidence, coarse samples going back a year, and a window far enough in
    # the future that it should NOT exist -- so the "does not exist" response
    # is observed rather than inferred.
    probes = [("recent_settled", now - dt.timedelta(hours=4)),
              ("known_evidence", dt.datetime(2026, 10, 2, 4, 0,
                                             tzinfo=dt.timezone.utc)),
              ("minus_7d", now - dt.timedelta(days=7)),
              ("minus_30d", now - dt.timedelta(days=30)),
              ("minus_90d", now - dt.timedelta(days=90)),
              ("minus_180d", now - dt.timedelta(days=180)),
              ("minus_365d", now - dt.timedelta(days=365)),
              ("future_should_not_exist", now + dt.timedelta(days=30))]

    out = {"LABEL": "PMUS_HISTORY_PROBE", "authenticated": False,
           "method": "GET", "now": now.isoformat(), "probes": []}

    for name, when in probes:
        for slug in slugs_for(when):
            rec = {"probe": name, "when": when.isoformat(), "slug": slug}
            rec["market_row"] = get("/v1/market/slug/%s" % slug)
            # Only ask the follow-up routes when the row itself answered, so
            # a non-existent window costs one request instead of three.
            if rec["market_row"].get("status") == 200:
                rec["bbo"] = get("/v1/markets/%s/bbo" % slug)
                rec["event"] = get("/v1/events/slug/%s"
                                   % (slug[4:] if slug.startswith("cpc-")
                                      else slug))
            out["probes"].append(rec)
            print(json.dumps(rec)[:4000], flush=True)
            time.sleep(0.25)

    print("PROBE_DOC " + json.dumps(out)[:200], flush=True)
    with open("history_probe.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
