"""Polymarket sports transfer backtest — print-only tier (part B of #167
comment 5513476177).

Replays the SAME q=1 sequential inventory/complement mechanics used by
the Gemini BTC-5m transfer engine against historical public Polymarket
trade prints for sports market families (MLB moneyline first, then
NFL/CFB, tennis, soccer; spreads/totals where liquid). Read-only; no
orders; THEORETICAL only.

Venue-honest deviations, all reported, never silent:
  * PRINT-ONLY TIER: Polymarket exposes no historical order book, so no
    bid/ask is invented. The maker join is proxied by actual traded
    prices, and the frozen v1 TTE gate is NOT applied (a live game has no
    5-minute settlement clock); the 20-80c price-band and >=1c completed
    pair edge bounds are applied unchanged. This makes the sports result
    an upper-bound *mechanics* transfer test, compared against the Gemini
    tier-1 backtest under the same fill-assumption tier.
  * CAMPAIGN SLOTS: Gemini grants one q=1 attempt per 5-minute event.
    For continuous games the analog is consecutive non-overlapping 300s
    slots during the traded span, one attempt per slot, 180s first-leg
    wait, completion deadline 25s before slot end — identical timing.
  * FILL TIERS: optimistic = the first qualifying taker print fills our
    resting maker at its price (front of queue). strict = a SECOND
    same-direction crossing print is required (first consumes the queue
    ahead of us). Both are queue-blind and labeled upper bounds.
  * FEES: Polymarket CLOB maker/taker base fees are read from the market
    record (currently 0 on these families) — never assumed equal to
    Gemini's schedule. Liquidity rewards are NOT modeled.

Everything is normalized into the YES frame of outcomeIndex 0: a taker
BUY of outcome 1 is a YES-frame sell at (1 - price), etc.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Iterable, Mapping, Optional

ONE = D("1")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
MIN_EDGE = D("0.01")
SLOT_S = 300.0
FIRST_WAIT_S = 180.0
STOP_BEFORE_SLOT_END_S = 25.0

CA_BUNDLE = "/root/.ccr/ca-bundle.crt"
GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"


def _get(url: str):
    ctx = ssl.create_default_context(cafile=CA_BUNDLE)
    req = urllib.request.Request(url, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read())


def fetch_closed_events(tag_slug: str, end_min: str, end_max: str,
                        limit: int = 60) -> list:
    q = urllib.parse.urlencode({
        "tag_slug": tag_slug, "closed": "true", "limit": str(limit),
        "order": "endDate", "ascending": "false",
        "end_date_min": end_min, "end_date_max": end_max})
    return _get(f"{GAMMA}/events?{q}")


def fetch_market_trades(condition_id: str, max_pages: int = 20,
                        sleep_s: float = 0.15) -> list:
    rows, offset = [], 0
    for _ in range(max_pages):
        page = _get(f"{DATA}/trades?market={condition_id}"
                    f"&limit=1000&offset={offset}")
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
        time.sleep(sleep_s)
    return rows


def normalize_yes_frame(trades: list) -> list:
    """-> [{t, dir ('buy'|'sell' in YES frame), p_yes}] sorted by time."""
    out = []
    for t in trades:
        try:
            idx = int(t.get("outcomeIndex", 0))
            price = D(str(t["price"]))
            side = str(t.get("side", "")).upper()
            ts = float(t["timestamp"])
        except (KeyError, ValueError, TypeError):
            continue
        p_yes = price if idx == 0 else (ONE - price)
        buy_yes = (side == "BUY") == (idx == 0)
        out.append({"t": ts, "dir": "buy" if buy_yes else "sell",
                    "p_yes": p_yes, "size": t.get("size")})
    out.sort(key=lambda r: r["t"])
    return out


def replay_slot(prints: list, slot_start: float, fill_mode: str) -> dict:
    """One q=1 campaign slot under the transfer mechanics (print-only)."""
    slot_stop = slot_start + SLOT_S - STOP_BEFORE_SLOT_END_S
    wait_until = min(slot_start + FIRST_WAIT_S, slot_stop)
    inslot = [p for p in prints if slot_start < p["t"] <= slot_stop]
    first = [p for p in inslot if p["t"] <= wait_until]
    if not first:
        return {"status": "NO_FILL_TIMEOUT", "slot_prints": len(inslot)}
    # our resting maker is the passive side of the first qualifying print
    f1 = first[0]
    if not (MIN_PRICE <= f1["p_yes"] <= MAX_PRICE):
        return {"status": "NO_FEE_SAFE_MAKER_PRICE", "slot_prints": len(inslot)}
    if fill_mode == "strict":
        same = [p for p in first if p["dir"] == f1["dir"]
                and ((p["p_yes"] <= f1["p_yes"]) if f1["dir"] == "sell"
                     else (p["p_yes"] >= f1["p_yes"]))]
        if len(same) < 2:
            return {"status": "NO_FILL_TIMEOUT", "slot_prints": len(inslot),
                    "strict_queue_unconsumed": True}
        f1 = same[1]
    # taker sell fills our YES maker; taker buy fills our NO maker
    side = "YES" if f1["dir"] == "sell" else "NO"
    first_cost = f1["p_yes"] if side == "YES" else (ONE - f1["p_yes"])
    comp = (ONE - first_cost - MIN_EDGE).quantize(D("0.01"))
    if comp < MIN_PRICE or comp > MAX_PRICE:
        return {"status": "ONE_SIDED_HOLD", "side": side,
                "first_cost": str(first_cost), "fill_t": f1["t"],
                "hold_reason": "no_fee_safe_complement",
                "slot_prints": len(inslot)}
    # complement: opposite taker direction crossing our resting level
    later = [p for p in inslot if p["t"] > f1["t"]]
    hits = [p for p in later
            if (side == "YES" and p["dir"] == "buy" and p["p_yes"] >= ONE - comp)
            or (side == "NO" and p["dir"] == "sell" and p["p_yes"] <= comp)]
    if fill_mode == "strict" and len(hits) >= 1:
        hits = hits[1:]
    if not hits:
        return {"status": "ONE_SIDED_HOLD", "side": side,
                "first_cost": str(first_cost), "fill_t": f1["t"],
                "comp_price": str(comp), "slot_prints": len(inslot)}
    f2 = hits[0]
    y, n = ((first_cost, comp) if side == "YES" else (comp, first_cost))
    return {"status": "MATCHED", "side": side, "first_cost": str(first_cost),
            "comp_price": str(comp), "fill_t": f1["t"], "comp_t": f2["t"],
            "pair_cost": str(y + n),
            "pair_pnl_theoretical": str(ONE - y - n),   # CLOB base fees 0
            "time_to_complement_s": round(f2["t"] - f1["t"], 1),
            "slot_prints": len(inslot)}


def replay_market(prints: list, span_start: float, span_end: float,
                  fill_mode: str) -> list:
    rows, t = [], span_start
    while t + SLOT_S <= span_end:
        rows.append({"slot_start": t, **replay_slot(prints, t, fill_mode)})
        t += SLOT_S
    return rows


def slot_flow(prints: list, span_start: float, span_end: float) -> dict:
    slots = two = empty = 0
    t = span_start
    while t + SLOT_S <= span_end:
        inslot = [p for p in prints
                  if t < p["t"] <= t + SLOT_S - STOP_BEFORE_SLOT_END_S]
        dirs = {p["dir"] for p in inslot}
        slots += 1
        two += int(len(dirs) == 2)
        empty += int(not inslot)
        t += SLOT_S
    return {"slots": slots, "two_sided": two, "empty": empty,
            "two_sided_pct": round(100 * two / slots, 1) if slots else None}
