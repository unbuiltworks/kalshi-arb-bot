"""LEAD/LAG feed admissibility probe. READ-ONLY. No order path, no auth.

IT DECIDES WHICH FEEDS MAY BE USED AT ALL, and it runs before any collection
because the standing rule is: REJECT FEEDS WHOSE CLOCK/TIMESTAMP SEMANTICS
CANNOT BE ESTABLISHED. A feed is ADMISSIBLE only if every one of these is
demonstrated from its own payloads, never assumed:

  1. reachable, with a 200 and a parseable body;
  2. carries a SOURCE timestamp -- the venue's own, not our receipt;
  3. that timestamp's UNIT is identifiable (s / ms / us / ns) by magnitude
     against our own clock, and resolves to a sane offset;
  4. carries BID and ASK with SIZES, not merely a last trade;
  5. a sequence id, where the venue publishes one (recorded as absent
     otherwise -- absence is a fact, not a disqualification).

Failing 1-4 REJECTS the feed. The probe states which requirement failed, so
a rejection is attributable rather than a shrug.

NOTHING HERE IS A PRICE SOURCE FOR SETTLEMENT. External spot never
substitutes for the authoritative settlement reference; the layers stay
distinct by construction (see leadlag_collector).
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

LABEL = "GEMINI_LEADLAG_FEED_PROBE_V1"
TIMEOUT = 15.0

#: Candidate public REST endpoints. REST is the admissibility probe only;
#: the collector prefers websockets where a venue offers them, and re-probes
#: the same requirements there.
CANDIDATES: Tuple[Dict[str, Any], ...] = (
    # ORDER-BOOK endpoints, not ticker endpoints. The V1 probe asked four
    # TICKER urls and all four were rejected: a ticker publishes either a
    # sized book or a source timestamp, never both (coinbase and
    # gemini_spot gave `time` with no sizes; kraken gave sizes with no
    # timestamp field at all). The admissibility RULE is unchanged and is
    # correct -- an unsized touch is not an executable book -- so the fix
    # is the source, which is what these urls change.
    {"venue": "coinbase", "instrument": "BTC-USD", "kind": "spot_book",
     "url": ("https://api.exchange.coinbase.com/products/BTC-USD/book"
             "?level=2"),
     "ts_fields": ("time",),
     "bid": ("bids", 0, 0), "ask": ("asks", 0, 0),
     "bid_size": ("bids", 0, 1), "ask_size": ("asks", 0, 1),
     "seq": ("sequence",)},
    {"venue": "kraken", "instrument": "XBT/USD", "kind": "spot_book",
     "url": "https://api.kraken.com/0/public/Depth?pair=XBTUSD&count=1",
     # each level carries its OWN unix timestamp, which is the source
     # timestamp the ticker endpoint lacked
     "ts_fields": (("result", "XXBTZUSD", "bids", 0, 2),),
     "bid": ("result", "XXBTZUSD", "bids", 0, 0),
     "ask": ("result", "XXBTZUSD", "asks", 0, 0),
     "bid_size": ("result", "XXBTZUSD", "bids", 0, 1),
     "ask_size": ("result", "XXBTZUSD", "asks", 0, 1),
     "seq": ()},
    {"venue": "gemini_spot", "instrument": "btcusd", "kind": "spot_book",
     "url": ("https://api.gemini.com/v1/book/btcusd"
             "?limit_bids=1&limit_asks=1"),
     "ts_fields": (("bids", 0, "timestamp"),),
     "bid": ("bids", 0, "price"), "ask": ("asks", 0, "price"),
     "bid_size": ("bids", 0, "amount"), "ask_size": ("asks", 0, "amount"),
     "seq": ()},
    {"venue": "bitstamp", "instrument": "btcusd", "kind": "spot_book",
     # USD-quoted, so it carries NO basis caveat, and it publishes both a
     # microsecond timestamp and sized levels
     "url": "https://www.bitstamp.net/api/v2/order_book/btcusd/",
     "ts_fields": ("microtimestamp", "timestamp"),
     "bid": ("bids", 0, 0), "ask": ("asks", 0, 0),
     "bid_size": ("bids", 0, 1), "ask_size": ("asks", 0, 1),
     "seq": ()},
    # RETAINED so the record shows it was not silently dropped: binance
    # answered HTTP 451 (unavailable for legal reasons) from the CI egress.
    # Its USDT quote also means it could never join a USD consensus
    # without an explicit basis layer.
    {"venue": "binance", "instrument": "BTCUSDT", "kind": "spot_book",
     "basis": ("USDT-quoted, NOT USD. Any comparison to a USD reference "
               "carries USDT/USD basis and is reported as a separate layer, "
               "never folded into a USD consensus without it."),
     "url": "https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=5",
     "ts_fields": (), "bid": ("bids", 0, 0), "ask": ("asks", 0, 0),
     "bid_size": ("bids", 0, 1), "ask_size": ("asks", 0, 1),
     "seq": ("lastUpdateId",)},
)


def _dig(obj: Any, path) -> Any:
    cur = obj
    for p in path:
        try:
            cur = cur[p]
        except Exception:                                      # noqa: BLE001
            return None
    return cur


def classify_unit(raw: Any, now_s: float) -> Dict[str, Any]:
    """Which epoch unit makes this timestamp land near NOW?

    The unit is IDENTIFIED by magnitude against our own clock rather than
    assumed from a field name: a venue that labels a field `timestamp` may
    publish seconds, milliseconds or an ISO string, and guessing wrong turns
    a lead/lag study into noise.
    """
    if raw is None:
        return {"identified": False, "why": "no source timestamp field"}
    if isinstance(raw, str):
        # A NUMERIC STRING is an epoch, not an ISO date. Several venues
        # publish "1791236835" or a microsecond string; the earlier version
        # tried ISO first and REJECTED those outright as unparseable, which
        # is a false rejection of a perfectly good timestamp. Try the
        # numeric reading first and fall through to the magnitude test
        # below, which is what actually identifies the unit.
        try:
            float(raw)
        except ValueError:
            try:
                from datetime import datetime
                t = datetime.fromisoformat(
                    raw.replace("Z", "+00:00")).timestamp()
                return {"identified": True, "unit": "iso8601",
                        "epoch_s": t, "offset_s": round(now_s - t, 4)}
            except Exception:                                  # noqa: BLE001
                return {"identified": False, "why": "unparseable string: %r"
                        % raw[:40]}
    try:
        v = float(raw)
    except Exception:                                          # noqa: BLE001
        return {"identified": False, "why": "non-numeric timestamp"}
    for unit, div in (("s", 1.0), ("ms", 1e3), ("us", 1e6), ("ns", 1e9)):
        t = v / div
        if abs(now_s - t) <= 86400:        # within a day of our clock
            return {"identified": True, "unit": unit, "epoch_s": t,
                    "offset_s": round(now_s - t, 4)}
    return {"identified": False,
            "why": "no epoch unit puts %r within a day of our clock" % raw}


def probe_one(c: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"venue": c["venue"], "instrument": c["instrument"],
                           "kind": c["kind"], "url": c["url"]}
    if c.get("basis"):
        out["basis_warning"] = c["basis"]
    t0 = time.time()
    try:
        req = urllib.request.Request(c["url"], headers={
            "User-Agent": "gemini-leadlag-probe/1 (read-only research)"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            out["status"] = r.status
    except Exception as exc:                                   # noqa: BLE001
        out.update({"reachable": False,
                    "why": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                    "admissible": False,
                    "failed_requirement": "1_reachable"})
        return out
    recv = time.time()
    out["rtt_s"] = round(recv - t0, 4)
    out["local_receipt_epoch_s"] = recv
    try:
        j = json.loads(body)
    except ValueError:
        out.update({"reachable": True, "admissible": False,
                    "failed_requirement": "1_parseable",
                    "why": "body was not JSON"})
        return out
    out["reachable"] = out.get("status") == 200
    raw_ts = None
    for f in (c.get("ts_fields") or ()):
        v = _dig(j, (f,)) if isinstance(f, str) else _dig(j, f)
        if v not in (None, ""):
            raw_ts = v
            out["source_timestamp_field"] = f
            break
    out["source_timestamp_raw"] = raw_ts
    out["clock"] = classify_unit(raw_ts, recv)
    bid, ask = _dig(j, c["bid"]), _dig(j, c["ask"])
    bs, as_ = (_dig(j, c["bid_size"]) if c.get("bid_size") else None,
               _dig(j, c["ask_size"]) if c.get("ask_size") else None)
    out.update({"bid": bid, "ask": ask, "bid_size": bs, "ask_size": as_,
                "sequence_id": (_dig(j, c["seq"]) if c.get("seq") else None),
                "sequence_id_published": bool(c.get("seq")
                                              and _dig(j, c["seq"])
                                              is not None)})
    reqs = {
        "1_reachable": bool(out["reachable"]),
        "2_source_timestamp_present": raw_ts is not None,
        "3_timestamp_unit_identified": bool(out["clock"].get("identified")),
        "4_book_with_sizes": all(x not in (None, "") for x in
                                 (bid, ask, bs, as_)),
    }
    out["requirements"] = reqs
    failed = [k for k, v in reqs.items() if not v]
    out["admissible"] = not failed
    out["failed_requirement"] = failed or None
    if not reqs["4_book_with_sizes"]:
        out["book_note"] = ("bid/ask present but SIZES missing -- a last "
                            "trade or an unsized touch is not an executable "
                            "book and is not admitted")
    return out


def run() -> Dict[str, Any]:
    probes = [probe_one(c) for c in CANDIDATES]
    adm = [p for p in probes if p.get("admissible")]
    return {
        "label": LABEL,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "read_only": "no order path, no credential, no mutation",
        "rule": ("a feed is admissible only if reachable, carrying a SOURCE "
                 "timestamp whose epoch unit is identifiable against our own "
                 "clock, with bid AND ask AND both sizes. Anything else is "
                 "REJECTED with the failed requirement named."),
        "probes": probes,
        "admissible_venues": [p["venue"] for p in adm],
        "rejected": {p["venue"]: p.get("failed_requirement")
                     for p in probes if not p.get("admissible")},
        "layer_rule": ("external spot/futures NEVER substitutes for the "
                       "authoritative settlement reference. The three layers "
                       "-- external consensus, published LRRR, Gemini "
                       "prediction book -- stay separate artifacts."),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True, default=str))
