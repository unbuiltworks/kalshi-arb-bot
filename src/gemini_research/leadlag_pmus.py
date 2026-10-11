"""PMUS BTC Up/Down as an EXTERNAL PREDICTION-MARKET layer. READ-ONLY.

It reuses the PROVEN read-only PMUS client (`pmus_sports_collector.
http_get_json`, GET-only, no credential, no order path) rather than a new
HTTP client, for the same reason layers 2 and 3 reuse the recorder: a second
unproven reader of the same venue is how two artifacts come to disagree.

THE LAYERS STAY DISTINCT, and PMUS adds to the THIRD, not the first:

  LAYER 1  underlying BTC spot/reference feeds        (leadlag_collector)
  LAYER 2  authoritative settlement references/strikes(per venue, separately)
  LAYER 3  EXECUTABLE prediction-market books         <- PMUS **and** Gemini

PMUS IS NOT A BTC PRICE FEED and is never folded into the external spot
consensus.

RAW UP/DOWN PRICES ARE NEVER COMPARED ACROSS VENUES. The windows, strikes,
settlement references and expiries differ, so a PMUS 22% and a Gemini 39%
are not the same contract and the two are never differenced. Synchronization
is by WALL CLOCK only, and every comparison is made AFTER conditioning on
the state that makes the two commensurable:

    authoritative strike; the current settlement/reference value;
    distance-to-strike in dollars AND bps; seconds to settlement;
    executable UP/DOWN VWAP at q1/q5/q10; spread; depth

so what is compared is a CONDITIONAL book state, not a displayed
percentage. No displayed-percentage arbitrage claim is expressible here:
nothing in this file subtracts one venue's price from another's.

CAPTURE IS RAW AND UNALIGNED. Source timestamps where the venue supplies
them, local receipt time always, sequence ids where published, and no
shifting or interpolation at collection.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

LABEL = "GEMINI_LEADLAG_PMUS_LAYER3_V1"
#: the predeclared executable sizes, matching the Gemini side exactly so the
#: two books are read at the same depths
SIZES: Tuple[int, ...] = (1, 5, 10)


def _d(v: Any) -> Optional[D]:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def cumulative_vwap(levels: Any, size: D) -> Optional[Dict[str, Any]]:
    """CUMULATIVE VWAP to fill `size`, or unfillable. Never a touch price."""
    need, cost, got = size, D("0"), D("0")
    for lv in (levels or []):
        if isinstance(lv, dict):
            px, qty = _d(lv.get("price")), _d(lv.get("size") or lv.get("qty"))
        else:
            px, qty = _d(lv[0]), _d(lv[1])
        if px is None or qty is None:
            continue
        take = min(need, qty)
        cost += px * take
        got += take
        need -= take
        if need <= 0:
            break
    if got < size:
        return {"fillable": False, "available": str(got)}
    return {"fillable": True, "vwap": str(cost / size), "available": str(got)}


def state_from_book(book: Dict[str, Any], bbo: Dict[str, Any],
                    meta: Dict[str, Any], now_s: float) -> Dict[str, Any]:
    """One PMUS market's CONDITIONAL state. No cross-venue arithmetic here."""
    md = (book or {}).get("marketData") or book or {}
    bids = md.get("bids") or md.get("buys") or []
    asks = md.get("asks") or md.get("sells") or []
    strike = _d(meta.get("strike") or meta.get("strikePrice"))
    ref = _d(meta.get("referenceValue") or meta.get("underlyingPrice"))
    exp = meta.get("endDate") or meta.get("expiryDate") or meta.get("endTime")
    exp_t = None
    if exp:
        try:
            from datetime import datetime
            exp_t = datetime.fromisoformat(
                str(exp).replace("Z", "+00:00")).timestamp()
        except Exception:                                      # noqa: BLE001
            exp_t = None
    dist_usd = (None if (strike is None or ref is None) else ref - strike)
    row: Dict[str, Any] = {
        "kind": "l3_pmus_state",
        "label": LABEL,
        "local_receipt_epoch_s": now_s,
        "slug": meta.get("slug"),
        "question": meta.get("question") or meta.get("title"),
        # LAYER 2, that venue's OWN authority -- never Gemini's
        "authoritative_strike": None if strike is None else str(strike),
        "settlement_reference_value": None if ref is None else str(ref),
        "settlement_reference_name": (meta.get("settlementSource")
                                      or meta.get("resolutionSource")),
        "distance_to_strike_usd": (None if dist_usd is None
                                   else str(dist_usd)),
        "distance_to_strike_bps": (
            None if (dist_usd is None or not strike)
            else str((dist_usd / strike) * D("10000"))),
        "seconds_to_settlement": (None if exp_t is None
                                  else round(exp_t - now_s, 3)),
        "expiry_iso": exp,
        # LAYER 3: the executable book, at the SAME depths as the Gemini side
        "executable": {},
        "touch": {"bid": (bids[0] if bids else None),
                  "ask": (asks[0] if asks else None)},
        "bbo_raw": bbo, "book_raw": book,
        "sequence_id": md.get("lastUpdateId") or md.get("sequence"),
        "source_timestamp_raw": md.get("timestamp") or md.get("updatedAt"),
        "no_cross_venue_arithmetic": (
            "this row contains PMUS state ONLY. No Gemini price is read, "
            "differenced or compared here; synchronization is by wall clock "
            "and comparison happens only after conditioning."),
    }
    for s in SIZES:
        row["executable"]["q%d" % s] = {
            "UP_buy": cumulative_vwap(asks, D(s)),
            "UP_sell": cumulative_vwap(bids, D(s)),
        }
    tb, ta = _d((bids[0] or {}).get("price") if isinstance(
        bids[0], dict) else (bids[0][0] if bids else None)) if bids else None, \
        _d((asks[0] or {}).get("price") if isinstance(
            asks[0], dict) else (asks[0][0] if asks else None)) if asks else None
    row["spread"] = (None if (tb is None or ta is None) else str(ta - tb))
    row["depth_levels"] = {"bids": len(bids), "asks": len(asks)}
    return row


def poll(slugs: List[str], get=None) -> List[Dict[str, Any]]:
    """Read bbo + book + metadata for each slug. GET-only, no credential."""
    from src import pmus_sports_collector as col
    get = col.http_get_json if get is None else get
    out: List[Dict[str, Any]] = []
    for slug in slugs:
        t0 = time.time()
        rec: Dict[str, Any] = {"slug": slug, "local_send_epoch_s": t0}
        try:
            bbo = get("/v1/markets/%s/bbo" % slug)
            book = get("/v1/markets/%s/book" % slug)
            meta = get("/v1/markets/%s" % slug)
        except Exception as exc:                               # noqa: BLE001
            rec.update({"kind": "l3_pmus_error", "ok": False,
                        "why": "%s: %s" % (type(exc).__name__,
                                           str(exc)[:160])})
            out.append(rec)
            continue
        now = time.time()
        md = (meta or {}).get("market") or meta or {}
        row = state_from_book(book, bbo, md, now)
        row.update({"ok": True, "rtt_s": round(now - t0, 4),
                    "local_send_epoch_s": t0})
        out.append(row)
    return out


def discover_btc_updown(get=None) -> Dict[str, Any]:
    """Find PMUS BTC Up/Down markets. Reports what it found, never guesses."""
    from src import pmus_sports_collector as col
    get = col.http_get_json if get is None else get
    try:
        events, diag = col.fetch_live_events(get)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(exc).__name__,
                                                str(exc)[:200]),
                "slugs": []}
    hits = []
    for ev in (events or []):
        txt = json.dumps(ev).lower()
        if "bitcoin" not in txt and "btc" not in txt:
            continue
        for m in (ev.get("markets") or []):
            q = str(m.get("question") or m.get("title") or "").lower()
            if ("up or down" in q or "up/down" in q
                    or ("up" in q and "down" in q)):
                hits.append({"slug": m.get("slug"), "question": q[:120],
                             "event": ev.get("slug")})
    return {"ok": True, "n_live_events": len(events or []),
            "slugs": [h["slug"] for h in hits if h.get("slug")],
            "matches": hits[:20],
            "note": ("discovery is reported, not assumed. If no BTC Up/Down "
                     "market is live, the PMUS layer records that fact and "
                     "contributes nothing rather than substituting another "
                     "market.")}
