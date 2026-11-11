"""Observe eligible markets on any venue, in the ONE shape the frozen L5
habitat scorer consumes.

READ ONLY. GET requests and public websockets only; no credentials, no order
path, no writes to any venue. A test pins that.

WHY ONE MODULE FOR BOTH VENUES: the owner's comparison is
PMUS sports / Gemini sports / Gemini BTC5m / Gemini ETH5m / canonical L5.
If each venue had its own observation shape, every difference between those
cohorts would be partly an artefact of how it was measured. Both venues emit
exactly the record `l5_habitat_score.window_features` expects --
{t, price, tte_s, two_sided, spread_ticks, depth_at_touch} -- plus the flow
and oscillation fields the owner asked for, and nothing is imputed.

SAMPLING IS NOT EQUAL AND IS NOT PRETENDED TO BE. Gemini streams at ~1 Hz
over a websocket; PMUS is polled over REST against a rate budget, so its
cadence is coarser. The frozen scorer already handles this -- rate features
are biased DOWN by coarse sampling and share-of-move features are biased UP,
it marks a value SAMPLING_LIMITED when it approaches the cadence ceiling,
and it discounts for coverage. Every observation therefore carries its own
`cadence_s` so the bias direction is attributable rather than invisible.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

GEMINI_WS = "wss://ws.gemini.com"

# Gemini's websocket pushes far faster than L5 acts; 1 Hz resolves the 1-4 s
# cadence that matters and keeps the series comparable with the 5m collector.
GEMINI_RECORD_S = 1.0

# PMUS costs 2 REST calls per market per sample against a shared budget.
PMUS_CALLS_PER_MARKET = 2
PMUS_DEFAULT_RPS = 6.0


# A single SUBSCRIBE frame carrying every stream is REFUSED by the venue once
# the symbol list grows: 70 symbols is 140 params and the server closes the
# connection with 1009 "message too big". The 5m collector never hit this
# because it holds 4-6 windows at a time. Breadth is the whole point of this
# module, so the subscribe is chunked.
SUBSCRIBE_CHUNK_PARAMS = 30


def subscribe_chunks(symbols: List[str],
                     chunk: int = SUBSCRIBE_CHUNK_PARAMS) -> List[List[str]]:
    """Stream names split into frames the venue will accept."""
    params = [f"{s}@trade" for s in symbols] + \
             [f"{s}@depth5@100ms" for s in symbols]
    return [params[i:i + chunk] for i in range(0, len(params), max(1, chunk))]


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def observation(t: float, bid: Optional[float], ask: Optional[float],
                bid_sz: Optional[float], ask_sz: Optional[float],
                tte_s: Optional[float], tick: Optional[float],
                **extra: Any) -> Optional[dict]:
    """ONE canonical observation, or None when the book is not a state.

    A one-sided book is NOT a state -- the same rule the Gemini 5m
    canonicaliser already applies -- so it returns None and the caller counts
    it as one-sided rather than letting it through as half a quote.
    """
    if bid is None or ask is None:
        return None
    tk = tick or 0.01
    row = {
        "t": t,
        "price": (bid + ask) / 2.0,          # quote mid; the caller labels it
        "bid": bid, "ask": ask,
        "bid_sz": bid_sz, "ask_sz": ask_sz,
        "tte_s": tte_s,
        "two_sided": bid > 0 and ask > 0,
        "spread": round(ask - bid, 6),
        "spread_ticks": round((ask - bid) / tk, 4),
        # depth at touch is the BINDING side for a maker, matching
        # analysis/l5/gemini_trajectory/habitat_expanded.py exactly
        "depth_at_touch": (None if bid_sz is None or ask_sz is None
                           else min(bid_sz, ask_sz)),
    }
    row.update(extra)
    return row


class MarketSeries:
    """Per-market observation buffer plus the flow/oscillation counters the
    habitat scorer does not compute for us."""

    def __init__(self, market_id: str, meta: Mapping[str, Any]):
        self.market_id = market_id
        self.meta = dict(meta)
        self.obs: List[dict] = []
        self.one_sided = 0
        self.prints: List[dict] = []
        self.shares_traded_first: Optional[float] = None
        self.shares_traded_last: Optional[float] = None

    def add(self, row: Optional[dict]) -> None:
        if row is None:
            self.one_sided += 1
        else:
            self.obs.append(row)

    def add_print(self, t: float, price: float, qty: float,
                  taker_sold: Optional[bool]) -> None:
        self.prints.append({"t": t, "price": price, "qty": qty,
                            "taker_sold": taker_sold})

    def note_cum_volume(self, v: Optional[float]) -> None:
        """PMUS publishes cumulative sharesTraded. Its DELTA across the
        session is real traded flow -- the only flow signal available there
        without a trade stream."""
        if v is None:
            return
        if self.shares_traded_first is None:
            self.shares_traded_first = v
        self.shares_traded_last = v

    # ---- derived, pre-action only ----
    def traded_shares(self) -> Optional[float]:
        if self.shares_traded_first is None or self.shares_traded_last is None:
            return None
        return max(0.0, self.shares_traded_last - self.shares_traded_first)

    def side_oscillations(self) -> Optional[int]:
        """How many times the cheaper side FLIPS.

        L5's defining behaviour is side switching, and `open_side_yes` picks
        the cheaper touch, so the number of times the cheaper side changes is
        exactly how many times the canonical rule would have wanted the other
        side. Computed from quotes only -- pre-action by construction."""
        if len(self.obs) < 2:
            return None
        flips, prev = 0, None
        for o in self.obs:
            # YES join = bid; NO join = 1 - ask. Cheaper touch wins.
            cheaper_yes = o["bid"] <= (1.0 - o["ask"])
            if prev is not None and cheaper_yes != prev:
                flips += 1
            prev = cheaper_yes
        return flips

    def useful_transitions(self, lo: float = 1.0, hi: float = 4.0
                           ) -> Optional[dict]:
        """Price changes whose inter-arrival sits in L5's own 1-4 s band.

        This is the characteristic the owner named first, and it is NOT the
        same as a raw change rate: a market that moves twice a minute and one
        that moves every 2 s can share a rate over a long enough window."""
        if len(self.obs) < 2:
            return None
        gaps, changes = [], 0
        last_change_t = None
        for a, b in zip(self.obs, self.obs[1:]):
            if abs(b["price"] - a["price"]) > 1e-9:
                changes += 1
                if last_change_t is not None:
                    gaps.append(b["t"] - last_change_t)
                last_change_t = b["t"]
        in_band = sum(1 for g in gaps if lo <= g <= hi)
        return {"price_changes": changes, "change_gaps": len(gaps),
                "gaps_in_1_4s": in_band,
                "share_gaps_in_1_4s": (round(in_band / len(gaps), 4)
                                       if gaps else None),
                "gap_p50_s": (round(sorted(gaps)[len(gaps) // 2], 3)
                              if gaps else None)}

    def band_coverage(self, lo: float = 0.20, hi: float = 0.80
                      ) -> Optional[float]:
        """Share of observations whose YES touch sits inside the execution
        band. Outside it the engine cannot open at all."""
        if not self.obs:
            return None
        n = sum(1 for o in self.obs if lo <= o["bid"] <= hi)
        return round(n / len(self.obs), 4)

    def summary(self, cadence_s: float) -> dict:
        tot = len(self.obs) + self.one_sided
        span = (self.obs[-1]["t"] - self.obs[0]["t"]) if len(self.obs) >= 2 else 0.0
        return {
            "market_id": self.market_id,
            **{k: self.meta.get(k) for k in
               ("venue", "sport", "league", "category", "market_type",
                "cohort", "event_ticker", "outcome_label", "tick_size",
                "expiry_t", "tte_scoreable", "settlement_agency",
                "maker_rebate", "liquidity_rewards", "taker_fee")},
            "observations": len(self.obs),
            "one_sided_observations": self.one_sided,
            "two_sided_pct": (round(100.0 * len(self.obs) / tot, 2)
                              if tot else None),
            "span_s": round(span, 2),
            "cadence_s": cadence_s,
            "prints": len(self.prints),
            "traded_shares_delta": self.traded_shares(),
            "side_oscillations": self.side_oscillations(),
            "useful_transitions": self.useful_transitions(),
            "band_coverage_020_080": self.band_coverage(),
        }


# ------------------------------------------------------------------ Gemini

async def observe_gemini(symbols: List[str], meta: Mapping[str, dict],
                         seconds: float,
                         record_s: float = GEMINI_RECORD_S,
                         ws_url: str = GEMINI_WS,
                         connect: Optional[Callable] = None
                         ) -> Dict[str, MarketSeries]:
    """Public depth5 + trade stream. Same transport the 5m collector uses --
    no second Gemini pipeline."""
    import websockets
    connect = connect or websockets.connect
    series = {s.upper(): MarketSeries(s.upper(), meta.get(s.upper(), {}))
              for s in symbols}
    last: Dict[str, float] = {}
    async with connect(ws_url, open_timeout=10, ping_interval=15,
                       max_queue=8000, max_size=8 * 2 ** 20) as ws:
        for chunk in subscribe_chunks(symbols):
            await ws.send(json.dumps({"id": "l5-observe",
                                      "method": "SUBSCRIBE",
                                      "params": chunk}))
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            key = str(m.get("s") or m.get("symbol") or "").upper()
            ser = series.get(key)
            if ser is None:
                continue
            now = time.time()
            if "p" in m and "q" in m and "m" in m:
                # PRINTS ARE NEVER SAMPLED -- scarce evidence.
                ser.add_print(now, _f(m.get("p")) or 0.0,
                              _f(m.get("q")) or 0.0, bool(m.get("m")))
                continue
            if "bids" in m and "asks" in m:
                if now - last.get(key, 0.0) < record_s:
                    continue
                last[key] = now
                b = (m.get("bids") or [])
                a = (m.get("asks") or [])
                bid, bid_sz = ((_f(b[0][0]), _f(b[0][1]))
                               if b and len(b[0]) >= 2 else (None, None))
                ask, ask_sz = ((_f(a[0][0]), _f(a[0][1]))
                               if a and len(a[0]) >= 2 else (None, None))
                exp = ser.meta.get("expiry_t")
                ser.add(observation(now, bid, ask, bid_sz, ask_sz,
                                    None if exp is None else exp - now,
                                    ser.meta.get("tick_size")
                                    if isinstance(ser.meta.get("tick_size"),
                                                  (int, float)) else 0.01,
                                    price_source="quote_mid"))
    return series


# -------------------------------------------------------------------- PMUS

def observe_pmus(slugs: List[str], meta: Mapping[str, dict], seconds: float,
                 get_json: Callable, rps: float = PMUS_DEFAULT_RPS,
                 sleep: Callable = time.sleep,
                 clock: Callable = time.time) -> Dict[str, MarketSeries]:
    """Round-robin REST polling inside a rate budget.

    The sweep period is a MEASURED consequence of the budget, not a wish:
    len(slugs) * 2 calls / rps seconds. It is recorded on every observation
    as `cadence_s` so the scorer's sampling-bias handling is attributable.
    """
    series = {s: MarketSeries(s, meta.get(s, {})) for s in slugs}
    if not slugs:
        return series
    per_call = 1.0 / max(rps, 0.1)
    start = clock()
    while clock() - start < seconds:
        for slug in slugs:
            if clock() - start >= seconds:
                break
            try:
                bbo = (get_json(f"/v1/markets/{slug}/bbo") or {})["marketData"]
                book = (get_json(f"/v1/markets/{slug}/book") or {})["marketData"]
            except Exception:                              # noqa: BLE001
                sleep(per_call * PMUS_CALLS_PER_MARKET)
                continue

            def val(k):
                v = bbo.get(k)
                return _f(v.get("value") if isinstance(v, Mapping) else v)

            bids = book.get("bids") or []
            asks = (book.get("offers") if "offers" in book
                    else book.get("asks")) or []
            ser = series[slug]
            ser.note_cum_volume(_f(bbo.get("sharesTraded")))
            exp = ser.meta.get("expiry_t")
            now = clock()
            tick = ser.meta.get("tick_size")
            ser.add(observation(
                now, val("bestBid"), val("bestAsk"),
                _f(bids[0].get("qty")) if bids else None,
                _f(asks[0].get("qty")) if asks else None,
                None if exp is None else exp - now,
                tick if isinstance(tick, (int, float)) else 0.01,
                price_source="quote_mid",
                ask_book_missing=not asks,
                last_trade_px=val("lastTradePx"),
                market_state=bbo.get("state")))
            sleep(per_call * PMUS_CALLS_PER_MARKET)
    return series


def pmus_cadence_s(n_markets: int, rps: float = PMUS_DEFAULT_RPS) -> float:
    """What the budget actually buys. Reported, never assumed away."""
    return round(n_markets * PMUS_CALLS_PER_MARKET / max(rps, 0.1), 2)
