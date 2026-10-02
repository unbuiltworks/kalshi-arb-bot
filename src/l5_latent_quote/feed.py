"""Public read-only HTTP reads. There is deliberately no write path here.

Every function issues a plain GET to a public endpoint and holds no credentials.
"""
from __future__ import annotations
import json, time, urllib.parse, urllib.request
from . import config as C

_HDR = {"User-Agent": C.USER_AGENT, "Accept": "*/*"}


def _get(url: str, tries: int = 3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=_HDR)   # GET; no body, no method override
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except Exception as e:                                 # noqa: BLE001
            last = e
            time.sleep(1.5 ** i)
    raise RuntimeError(f"read failed: {url}: {last}")


def window_start(now: float | None = None) -> int:
    t = int(now if now is not None else time.time())
    return (t // C.WINDOW_SECONDS) * C.WINDOW_SECONDS


def market_for_window(ws: int) -> dict | None:
    """Resolve the 15m event to its two outcome token ids. Metadata only."""
    slug = f"{C.SERIES_SLUG_PREFIX}{ws}"
    ev = _get(f"{C.GAMMA}/events?slug={urllib.parse.quote(slug)}")
    if not ev:
        return None
    for m in (ev[0].get("markets") or []):
        toks = m.get("clobTokenIds")
        outs = m.get("outcomes")
        if not toks:
            continue
        toks = json.loads(toks) if isinstance(toks, str) else toks
        outs = json.loads(outs) if isinstance(outs, str) else outs
        if len(toks) < 2:
            continue
        idx = {str(o).lower(): i for i, o in enumerate(outs or [])}
        return {"slug": slug, "window_start": ws, "condition_id": m.get("conditionId"),
                "token_up": toks[idx.get("up", 0)], "token_dn": toks[idx.get("down", 1)],
                "closed": bool(m.get("closed"))}
    return None


def book(token_id: str) -> dict:
    """Full L2 ladder for one outcome token."""
    b = _get(f"{C.CLOB}/book?token_id={token_id}")
    bids = sorted(({"p": float(x["price"]), "q": float(x["size"])} for x in (b.get("bids") or [])),
                  key=lambda x: -x["p"])
    asks = sorted(({"p": float(x["price"]), "q": float(x["size"])} for x in (b.get("asks") or [])),
                  key=lambda x: x["p"])
    return {"t_ms": int(b.get("timestamp") or 0), "bids": bids, "asks": asks,
            "tick": float(b.get("tick_size") or C.TICK),
            "min_size": float(b.get("min_order_size") or C.MIN_ORDER_SIZE),
            "last_trade_price": b.get("last_trade_price"),
            "hash": b.get("hash")}


def market_trades(condition_id: str, limit: int = 500, offset: int = 0) -> list:
    """Market-wide TAKER-INDEXED trade tape. Taker-indexed: it carries the
    aggressor side, which is exactly what trade-through evidence needs."""
    return _get(f"{C.DATA}/trades?market={condition_id}&limit={limit}&offset={offset}")


def l5_activity(limit: int = 100) -> list:
    """L5's own public activity, for behavioural labels only."""
    return _get(f"{C.DATA}/activity?user={C.L5_WALLET}&limit={limit}&offset=0"
                f"&sortBy=TIMESTAMP&sortDirection=DESC")
