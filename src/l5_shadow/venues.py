"""Live multi-level books and VENUE-DECLARED terms, for both venues, read-only.

WHY THIS FILE AND NOT THE EXISTING COLLECTORS. Two long-running Gemini shadow
services ask for the touch alone -- `gemini_l5_shadow_service.py:189` and
`gemini_l5_shadow_v2.py:1077` both request `?limit_bids=1&limit_asks=1`, which
measurably hides a MEDIAN 182 shares behind the best price (p95 720).

That truncation is real, but it was NOT the cause of the "depth 1" reading that
made the router decline a wanted 10: that path read a full, untruncated book
and saw a genuine 1-share touch. Both facts are measured separately in
analysis/l5/gemini_depth/. Measured here 2026-09-15 13:04Z, the same contract's
full book was:

    GEMI-BTC15M2609151315-UP   bids 0.72x1, 0.70x500, 0.67x223
                               asks 0.78x500, 0.81x789

500 shares one tick behind a 1-share touch. "The touch is thin" and "the venue
cannot supply 10" are different claims and the first was being reported as the
second. Nothing here ever requests a truncated book.

TERMS ARE READ FROM THE VENUE, NOT HARDCODED. Polymarket's crypto markets
declare their own fee schedule, minimum order size and tick on every market
object, and those are what decide whether an intent is even submittable:

    feeSchedule  {"rate": 0.07, "takerOnly": true, "rebateRate": 0.2}
    orderMinSize 5      orderPriceMinTickSize 0.01

`takerOnly: true` means a Polymarket maker pays NO fee; Gemini's maker pays
ceil-cent 0.0175*C*P*(1-P). That is a structural difference between the venues
and it has to come from the venue, per window, or the comparison is against a
remembered number. When the venue does not declare a term, this module reports
None and the evaluator refuses -- it never substitutes a default.

READ ONLY. Unauthenticated GETs. No order path is importable from here.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
PM_DATA = "https://data-api.polymarket.com"
GEMINI = "https://api.gemini.com"
UA = {"User-Agent": "l5-shadow/0.1 (read-only research)"}
CA_BUNDLE = "/root/.ccr/ca-bundle.crt"

DURATION_S = {"5m": 300, "15m": 900}
_GEMINI_DUR = {300: "05M", 900: "15M"}


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    try:
        c.load_verify_locations(CA_BUNDLE)
    except OSError:
        pass                      # not in the proxied environment; system CAs
    return c


def get(url: str, timeout: float = 15.0) -> Dict[str, Any]:
    """{ok, body, recv, err}. A failure is a RESULT with a reason attached.

    A venue we could not reach is "unknown", never "absent": the difference
    decides whether a router may treat a venue as unable to trade, and
    collapsing the two silently routes around a venue that was merely down.
    """
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as fh:
            return {"ok": True, "body": fh.read().decode("utf-8"),
                    "recv": time.time(), "rtt_s": round(time.time() - t0, 3),
                    "err": None}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return {"ok": False, "body": None, "recv": time.time(),
                "rtt_s": round(time.time() - t0, 3),
                "err": f"{type(e).__name__}: {e}"}


def _json(url: str, timeout: float = 15.0):
    r = get(url, timeout)
    if not r["ok"]:
        return None, r["err"]
    try:
        return json.loads(r["body"]), None
    except ValueError as e:
        return None, f"unparseable response: {e}"


# ------------------------------------------------------------------ Polymarket

def pm_window(market_key: str) -> Optional[Dict[str, Any]]:
    """Gamma market object for a window slug, with its DECLARED terms.

    `token_up` / `token_dn` are the CLOB asset ids for the two outcomes, taken
    from the market's own `outcomes` ordering rather than assumed: swapping
    them prices every Down intent against the Up book.
    """
    ms, err = _json(f"{GAMMA}/markets?slug={market_key}")
    if not isinstance(ms, list) or not ms:
        return None
    m = ms[0]
    try:
        toks = m.get("clobTokenIds")
        toks = json.loads(toks) if isinstance(toks, str) else (toks or [])
        outs = m.get("outcomes")
        outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
    except ValueError:
        return None
    if len(toks) < 2 or len(outs) < 2:
        return None
    by = {str(o): t for o, t in zip(outs, toks)}
    fee = m.get("feeSchedule") or {}
    return {
        "venue": "PMUS", "market_key": market_key,
        "condition_id": m.get("conditionId"),
        "token_up": by.get("Up"), "token_dn": by.get("Down"),
        "outcomes": outs,
        "accepting_orders": m.get("acceptingOrders"),
        "end_date": m.get("endDate"),
        # declared terms; None means the venue did not say, and the evaluator
        # must refuse rather than assume
        "fee_rate": fee.get("rate"),
        "fee_taker_only": fee.get("takerOnly"),
        "fee_rebate_rate": fee.get("rebateRate"),
        "order_min_size": m.get("orderMinSize"),
        "tick": m.get("orderPriceMinTickSize"),
        "rewards_min_size": m.get("rewardsMinSize"),
        "rewards_max_spread": m.get("rewardsMaxSpread"),
        "terms_source": f"{GAMMA}/markets?slug={market_key} (live market object)",
    }


def pm_settlement(market_key: str) -> Optional[Dict[str, Any]]:
    """How a window RESOLVED, or None while it has not.

    Gamma excludes closed markets from /markets unless `closed=true` is
    passed, so a settled window queried the ordinary way comes back EMPTY --
    indistinguishable from "no such market" and, if read carelessly, from
    "unresolved". Both states are asked for explicitly here.

    A resolution is only reported when the venue says `closed` AND one outcome
    price is at or above 0.99. An unresolved or disputed market returns
    `resolved: False`, and no P&L may be called realized on it.
    """
    for extra in ("&closed=true", ""):
        m, err = _json(f"{GAMMA}/markets?slug={market_key}{extra}")
        m = m[0] if isinstance(m, list) and m else m
        if not isinstance(m, dict):
            continue
        outs = m.get("outcomes")
        outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
        px = m.get("outcomePrices")
        px = json.loads(px) if isinstance(px, str) else (px or [])
        closed = bool(m.get("closed"))
        won = None
        if closed and len(px) == len(outs) == 2:
            try:
                vals = [float(x) for x in px]
            except (TypeError, ValueError):
                vals = []
            hits = [outs[i] for i, v in enumerate(vals) if v >= 0.99]
            won = hits[0] if len(hits) == 1 else None
        return {
            "market_key": market_key, "closed": closed,
            "uma_status": m.get("umaResolutionStatus"),
            "closed_time": m.get("closedTime"),
            "outcomes": outs, "outcome_prices": px,
            "resolved": bool(won), "winning_outcome": won,
            "resolution_source": ("gamma_outcome_prices" if won
                                  else "UNRESOLVED"),
        }
    return None


def pm_book(token_id: str) -> Optional[Dict[str, Any]]:
    """FULL CLOB book for one outcome token. Never truncated.

    The CLOB returns asks worst-first and bids worst-first; ordering is left
    exactly as served and normalised by `depth.parse_book`, so a change in
    venue ordering cannot silently invert a ladder here.
    """
    b, err = _json(f"{CLOB}/book?token_id={token_id}")
    if not isinstance(b, dict):
        return None
    src = b.get("timestamp")
    src = (float(src) / 1000.0) if src else None
    return {"venue": "PMUS", "asset_id": b.get("asset_id"),
            "bids": b.get("bids") or [], "asks": b.get("asks") or [],
            "source_ts": src, "observed_at": time.time(),
            "hash": b.get("hash"),
            "tick": b.get("tick_size"), "min_order_size": b.get("min_order_size"),
            "last_trade_price": b.get("last_trade_price"),
            "price_key": "price", "size_key": "size"}


def pm_trades(condition_id: str, limit: int = 100) -> Optional[List[dict]]:
    """Public prints for a market -- the ONLY thing that can evidence a fill.

    Returned rows are normalised to {t, price, size, side, taker} so the maker
    evidence test reads the same shape on both venues.
    """
    rows, err = _json(f"{PM_DATA}/trades?market={condition_id}&limit={limit}")
    if not isinstance(rows, list):
        return None
    out = []
    for x in rows:
        try:
            out.append({"t": float(x.get("timestamp") or 0),
                        "price": D(str(x.get("price"))),
                        "size": D(str(x.get("size"))),
                        "side": str(x.get("side") or "").upper(),
                        "asset": str(x.get("asset") or ""),
                        "venue": "PMUS"})
        except Exception:                                     # noqa: BLE001
            continue
    return sorted(out, key=lambda r: r["t"])


# ---------------------------------------------------------------------- Gemini

def gemini_symbol_for(market_key: str) -> Optional[str]:
    """Reuses the verified arithmetic mapping; no name matching anywhere."""
    from src.l5_adapters.gemini_mapping import gemini_symbol
    c = gemini_symbol(market_key)
    return c["symbol"] if c else None


_FEED = None


def gemini_feed():
    """Lazily-started websocket feed, shared by every caller in the process.

    Started on first use rather than at import, so a module that only needs the
    fee schedule never opens a socket.
    """
    global _FEED
    if _FEED is None:
        from src.l5_shadow.gemini_ws import GeminiFeed
        _FEED = GeminiFeed()
    return _FEED


def prune_gemini_feed(keep: Any) -> None:
    """Stop following windows that have expired, so sockets do not accumulate."""
    f = gemini_feed()
    keep = set(keep or ())
    for sym in f.watched():
        if sym not in keep:
            f.drop(sym)


def gemini_book_rest(symbol: str) -> Optional[Dict[str, Any]]:
    """FULL Gemini book over REST. Deliberately NOT `limit_bids=1`.

    Gemini stamps each level rather than the response, so the book's age is
    taken from the newest level. A book with no levels at all carries no
    timestamp and reports None -- an unknown age, not a zero one.
    """
    b, err = _json(f"{GEMINI}/v1/book/{symbol}")
    if not isinstance(b, dict):
        return None
    bids, asks = b.get("bids") or [], b.get("asks") or []
    ts = [float(l["timestamp"]) for l in list(bids) + list(asks)
          if isinstance(l, dict) and l.get("timestamp")]
    return {"venue": "GEMINI", "asset_id": symbol, "source": "rest",
            "bids": bids, "asks": asks,
            "source_ts": (max(ts) if ts else None), "observed_at": time.time(),
            "price_key": "price", "size_key": "amount"}


def gemini_book(symbol: str, *, prefer_ws: bool = True) -> Optional[Dict[str, Any]]:
    """The MAINTAINED book when we have it, a fresh REST snapshot otherwise.

    WHY WEBSOCKET FIRST. Measured 2026-09-15 13:33Z: eight consecutive REST
    calls 0.26-2.0 s apart returned SEVEN different books, and the socket shows
    why -- a 1-share quote at 0.39 cancelled and re-placed 25 MILLISECONDS
    apart, at ~35 messages a second. REST is not stale (no cache directive is
    served and level stamps are ~1 s old); it is a sample of something that
    moves faster than any polling rate. The maintained book is the venue's own
    state, with the age of the last applied message attached.

    The fallback is not a formality: a socket that has not yet delivered its
    `initial` snapshot, or one that hit a sequence gap, reports None rather
    than a half-built book, and a REST snapshot is then genuinely better than
    nothing. `source` says which path answered, on every row.
    """
    if prefer_ws:
        try:
            f = gemini_feed()
            f.watch(symbol)
            b = f.book(symbol)
            if b is not None:
                return b
        except Exception:                                     # noqa: BLE001
            pass          # a broken feed must never take the venue offline
    return gemini_book_rest(symbol)


def gemini_trades(symbol: str, limit: int = 100) -> Optional[List[dict]]:
    """Public prints for the maker evidence test, same normalised shape."""
    rows, err = _json(f"{GEMINI}/v1/trades/{symbol}?limit_trades={limit}")
    if not isinstance(rows, list):
        return None
    out = []
    for x in rows:
        try:
            out.append({"t": float(x.get("timestampms") or 0) / 1000.0
                        or float(x.get("timestamp") or 0),
                        "price": D(str(x.get("price"))),
                        "size": D(str(x.get("amount"))),
                        # Gemini reports the TAKER's side
                        "side": ("SELL" if str(x.get("type")).lower() == "sell"
                                 else "BUY"),
                        "asset": symbol, "venue": "GEMINI"})
        except Exception:                                     # noqa: BLE001
            continue
    return sorted(out, key=lambda r: r["t"])


def live_windows(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The BTC5m and BTC15m windows open right now, as venue-neutral keys.

    Both cohorts, always. Watching only BTC15m is how a venue that quotes 5m
    better than 15m stays invisible.
    """
    t = int(time.time() if now is None else now)
    out = []
    for label, dur in (("15m", 900), ("5m", 300)):
        start = (t // dur) * dur
        out.append({"cohort": f"BTC{label}", "market_key":
                    f"btc-updown-{label}-{start}",
                    "window_start": start, "window_end": start + dur})
    return out
