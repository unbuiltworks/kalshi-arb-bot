"""Read-only NFL Gemini<->Kalshi hot depth/freshness/survival probe.

This probe watches only exact G/K pairs that were positive in a prior fee-aware REST
snapshot. It does not discover new identities, certify settlement rules, or contain
any order-entry client. The purpose is to answer the next execution question:

    did the apparent q=1 after-fee edge exist on live books, with actual top-level
    depth, sufficiently fresh/skew-aligned quotes, and did the same executable
    price limits survive 50ms?

All results remain SHADOW_ONLY_RULES_UNCERTIFIED regardless of economics.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
import json
import logging
import time
from typing import Any

import websockets

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks
from src.gemini_kalshi_execution_shadow import (
    ShadowCandidate,
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker

D = Decimal
ONE = D("1")
logger = logging.getLogger("kalshi_arb.nfl_gk_hot_depth_probe")

# Exact direct G/K pairs from the 2026-08-20 fee-aware NFL snapshot.  These are
# market IDs only; their existence here is NOT a rule certification.
PAIR_SPECS: tuple[dict[str, str], ...] = (
    {"key":"football|nfl|BUF,HOU|spread|BUF|GT|4.5","gemini":"GEMI-NFL-2609131700-BUF-HOU-S-BUF4","kalshi":"KXNFLSPREAD-26SEP13BUFHOU-BUF5"},
    {"key":"football|nfl|DEN,KC|spread|KC|GT|6.5","gemini":"GEMI-NFL-2609150015-DEN-KC-S-KC6","kalshi":"KXNFLSPREAD-26SEP14DENKC-KC7"},
    {"key":"football|nfl|NYJ,TEN|spread|TEN|GT|4.5","gemini":"GEMI-NFL-2609131700-NYJ-TEN-S-TEN4","kalshi":"KXNFLSPREAD-26SEP13NYJTEN-TEN5"},
    {"key":"football|nfl|DET,NO|spread|DET|GT|10.5","gemini":"GEMI-NFL-2609131700-NO-DET-S-DET10","kalshi":"KXNFLSPREAD-26SEP13NODET-DET11"},
    {"key":"football|nfl|PHI,WAS|spread|PHI|GT|6.5","gemini":"GEMI-NFL-2609132025-WAS-PHI-S-PHI6","kalshi":"KXNFLSPREAD-26SEP13WASPHI-PHI7"},
    {"key":"football|nfl|PHI,WAS|spread|PHI|GT|7.5","gemini":"GEMI-NFL-2609132025-WAS-PHI-S-PHI7","kalshi":"KXNFLSPREAD-26SEP13WASPHI-PHI8"},
    {"key":"football|nfl|LV,MIA|spread|LV|GT|1.5","gemini":"GEMI-NFL-2609132025-MIA-LV-S-LV1","kalshi":"KXNFLSPREAD-26SEP13MIALV-LV2"},
    {"key":"football|nfl|DAL,NYG|game_total|contest|GT|50.5","gemini":"GEMI-NFL-2609140020-DAL-NYG-T-O50","kalshi":"KXNFLTOTAL-26SEP13DALNYG-51"},
    {"key":"football|nfl|BAL,IND|game_total|contest|GT|50.5","gemini":"GEMI-NFL-2609131700-BAL-IND-T-O50","kalshi":"KXNFLTOTAL-26SEP13BALIND-51"},
    {"key":"football|nfl|DET,NO|spread|DET|GT|4.5","gemini":"GEMI-NFL-2609131700-NO-DET-S-DET4","kalshi":"KXNFLSPREAD-26SEP13NODET-DET5"},
    {"key":"football|nfl|PHI,WAS|spread|PHI|GT|1.5","gemini":"GEMI-NFL-2609132025-WAS-PHI-S-PHI1","kalshi":"KXNFLSPREAD-26SEP13WASPHI-PHI2"},
    {"key":"football|nfl|DEN,KC|spread|KC|GT|3.5","gemini":"GEMI-NFL-2609150015-DEN-KC-S-KC3","kalshi":"KXNFLSPREAD-26SEP14DENKC-KC4"},
    {"key":"football|nfl|CIN,TB|spread|CIN|GT|6.5","gemini":"GEMI-NFL-2609131700-TB-CIN-S-CIN6","kalshi":"KXNFLSPREAD-26SEP13TBCIN-CIN7"},
    {"key":"football|nfl|PHI,WAS|spread|PHI|GT|3.5","gemini":"GEMI-NFL-2609132025-WAS-PHI-S-PHI3","kalshi":"KXNFLSPREAD-26SEP13WASPHI-PHI4"},
)


def _ceil_centicent(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("fee cannot be negative")
    return (value * D("10000")).to_integral_value(rounding=ROUND_CEILING) / D("10000")


def nfl_kalshi_taker_fee(price: Decimal, quantity: Decimal) -> Decimal:
    """Audited NFL taker formula for KXNFLGAME/SPREAD/TOTAL, multiplier M=1.

    This is intentionally local to the NFL research probe. Maker fees are irrelevant
    because the proposed arb leg is an immediate FOK taker order.
    """
    if quantity < 0 or price < 0 or price > 1:
        raise ValueError("invalid fee input")
    return _ceil_centicent(D("0.07") * quantity * price * (ONE - price))


class GeminiNflBooks:
    """Small public Gemini bookTicker store for a fixed research cohort."""

    def __init__(self, symbols: list[str], *, max_queue: int = 10000) -> None:
        self.symbols = sorted(set(symbols))
        self.books: dict[str, TopOfBook] = {}
        self.updates: asyncio.Queue[BookUpdate] = asyncio.Queue(maxsize=max_queue)
        self.connected = False
        self.messages = 0
        self.book_updates = 0
        self.dropped_updates = 0
        self.last_message_ms = 0

    def quote(self, symbol: str, *, max_age_ms: int) -> TopOfBook | None:
        row = self.books.get(symbol)
        if row is None or int(time.time() * 1000) - row.ts_ms > max_age_ms:
            return None
        return row

    def _notify(self, symbol: str, received_ms: int) -> None:
        event = BookUpdate("gemini", symbol, received_ms, time.perf_counter_ns(), "bookTicker")
        try:
            self.updates.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self.updates.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped_updates += 1
            try:
                self.updates.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped_updates += 1

    async def run(self) -> None:
        wanted = {s.lower(): s for s in self.symbols}
        backoff = 0.5
        while True:
            try:
                async with websockets.connect(
                    GEMINI_WS,
                    open_timeout=5.0,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    await ws.send(json.dumps({
                        "id":"nfl-gk-hot-depth",
                        "method":"SUBSCRIBE",
                        "params":[f"{s}@bookTicker" for s in self.symbols],
                    }, separators=(",", ":")))
                    self.connected = True
                    backoff = 0.5
                    async for raw in ws:
                        received_ms = int(time.time() * 1000)
                        self.last_message_ms = received_ms
                        self.messages += 1
                        payload = json.loads(raw)
                        if payload.get("status") is not None:
                            if int(payload.get("status")) >= 400:
                                raise RuntimeError(f"Gemini subscription rejected: {payload}")
                            continue
                        symbol = wanted.get(str(payload.get("s") or "").lower())
                        if symbol is None:
                            continue
                        try:
                            book = parse_gemini_book_ticker(payload, received_ms=received_ms)
                        except (KeyError, TypeError, ValueError):
                            continue
                        self.books[symbol] = book
                        self.book_updates += 1
                        self._notify(symbol, received_ms)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                logger.warning("NFL_GK_GEMINI_RECONNECT type=%s retry_s=%.1f", type(exc).__name__, backoff)
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 2)


def kalshi_tob(quote: Any, ticker: str) -> TopOfBook:
    return TopOfBook(
        venue="kalshi",
        market_id=ticker,
        ts_ms=int(quote.received_ms),
        yes_bid=ONE - D(str(quote.no_ask)),
        yes_bid_size=D(str(quote.no_depth)),
        yes_ask=D(str(quote.yes_ask)),
        yes_ask_size=D(str(quote.yes_depth)),
    )


@dataclass
class PairMetrics:
    key: str
    gemini: str
    kalshi: str
    triggers: int = 0
    both_books_seen: int = 0
    q1_depth_seen: int = 0
    strict_evaluations: int = 0
    strict_positive_observations: int = 0
    relaxed_positive_observations: int = 0
    rechecks_started: int = 0
    rechecks_survived: int = 0
    max_profit: Decimal = D("0")
    max_strict_profit: Decimal = D("0")
    min_skew_ms: int | None = None
    max_skew_ms: int = 0
    last_direction: str | None = None
    last_profit: Decimal = D("0")
    positive_open_mono: float | None = None
    positive_total_ms: float = 0.0
    positive_longest_ms: float = 0.0
    positive_opens: int = 0
    positive_closes: int = 0

    def observe_positive_state(self, positive: bool, now: float) -> None:
        if positive and self.positive_open_mono is None:
            self.positive_open_mono = now
            self.positive_opens += 1
        elif not positive and self.positive_open_mono is not None:
            duration = (now - self.positive_open_mono) * 1000.0
            self.positive_total_ms += duration
            self.positive_longest_ms = max(self.positive_longest_ms, duration)
            self.positive_open_mono = None
            self.positive_closes += 1

    def finalize(self, now: float) -> None:
        if self.positive_open_mono is not None:
            duration = (now - self.positive_open_mono) * 1000.0
            self.positive_total_ms += duration
            self.positive_longest_ms = max(self.positive_longest_ms, duration)
            self.positive_open_mono = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key":self.key,"gemini":self.gemini,"kalshi":self.kalshi,
            "triggers":self.triggers,"both_books_seen":self.both_books_seen,
            "q1_depth_seen":self.q1_depth_seen,"strict_evaluations":self.strict_evaluations,
            "strict_positive_observations":self.strict_positive_observations,
            "relaxed_positive_observations":self.relaxed_positive_observations,
            "rechecks_started":self.rechecks_started,"rechecks_survived":self.rechecks_survived,
            "recheck_survival_rate":(self.rechecks_survived/self.rechecks_started if self.rechecks_started else None),
            "max_profit":str(self.max_profit),"max_strict_profit":str(self.max_strict_profit),
            "min_skew_ms":self.min_skew_ms,"max_skew_ms":self.max_skew_ms,
            "last_direction":self.last_direction,"last_profit":str(self.last_profit),
            "positive_opens":self.positive_opens,"positive_closes":self.positive_closes,
            "positive_total_ms":round(self.positive_total_ms,3),
            "positive_longest_ms":round(self.positive_longest_ms,3),
        }


def best_candidate(g: TopOfBook, k: TopOfBook, *, max_skew_ms: int) -> ShadowCandidate | None:
    rows = evaluate_pair(
        g, k,
        gemini_fee=gemini_predictions_taker_fee,
        kalshi_fee=nfl_kalshi_taker_fee,
        max_quote_skew_ms=max_skew_ms,
        max_quantity=ONE,
    )
    return max(rows, key=lambda r: r.locked_profit) if rows else None


def same_limits(direction: str, eg: TopOfBook, ek: TopOfBook, g: TopOfBook, k: TopOfBook) -> bool:
    if direction == "GEMINI_YES_KALSHI_NO":
        return bool(g.yes_ask <= eg.yes_ask and g.yes_ask_size >= ONE and k.no_ask <= ek.no_ask and k.no_ask_size >= ONE)
    if direction == "KALSHI_YES_GEMINI_NO":
        return bool(k.yes_ask <= ek.yes_ask and k.yes_ask_size >= ONE and g.no_ask <= eg.no_ask and g.no_ask_size >= ONE)
    return False


async def _next_update(gemini: GeminiNflBooks, kalshi: EventingKalshiCrossBooks) -> BookUpdate:
    gt = asyncio.create_task(gemini.updates.get())
    kt = asyncio.create_task(kalshi.updates.get())
    done, pending = await asyncio.wait((gt,kt), return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


async def run(seconds: float) -> dict[str, Any]:
    specs = list(PAIR_SPECS)
    symbols = [x["gemini"] for x in specs]
    tickers = [x["kalshi"] for x in specs]
    gemini = GeminiNflBooks(symbols)
    kalshi = EventingKalshiCrossBooks(tickers)
    by_marker: dict[tuple[str,str], list[int]] = {}
    for i,s in enumerate(specs):
        by_marker.setdefault(("gemini",s["gemini"]),[]).append(i)
        by_marker.setdefault(("kalshi",s["kalshi"]),[]).append(i)
    metrics = [PairMetrics(s["key"],s["gemini"],s["kalshi"]) for s in specs]
    pending_rechecks: dict[int, asyncio.Task] = {}

    async def recheck(i: int, direction: str, eg: TopOfBook, ek: TopOfBook) -> None:
        m = metrics[i]
        m.rechecks_started += 1
        await asyncio.sleep(0.050)
        s = specs[i]
        g2 = gemini.quote(s["gemini"], max_age_ms=100)
        kq2 = await kalshi.quote(s["kalshi"], max_age_ms=100)
        if g2 is None or kq2 is None:
            return
        k2 = kalshi_tob(kq2,s["kalshi"])
        if abs(g2.ts_ms-k2.ts_ms)>50 or not same_limits(direction,eg,ek,g2,k2):
            return
        c2 = best_candidate(g2,k2,max_skew_ms=50)
        if c2 is not None and c2.direction==direction and c2.locked_profit>0:
            m.rechecks_survived += 1

    tasks = [asyncio.create_task(gemini.run()),asyncio.create_task(kalshi.run())]
    started = time.monotonic()
    deadline = started + max(5.0,seconds)
    logger.info("NFL_GK_HOT_DEPTH_START %s",json.dumps({
        "pairs":len(specs),"seconds":seconds,"strict_book_age_ms":100,"strict_quote_skew_ms":50,
        "revalidate_ms":50,"quantity":"1","classification":"SHADOW_ONLY_RULES_UNCERTIFIED",
        "read_only":True,"order_entry":False,
    },separators=(",",":")))
    try:
        while time.monotonic()<deadline:
            if any(t.done() and t.exception() for t in tasks):
                raise RuntimeError("market data task exited")
            try:
                trigger = await asyncio.wait_for(_next_update(gemini,kalshi),timeout=1.0)
            except asyncio.TimeoutError:
                continue
            for i in by_marker.get((trigger.venue,trigger.market_id),[]):
                s=specs[i]; m=metrics[i]; m.triggers+=1
                # Relaxed diagnostics establish whether a raw live-book edge exists.
                g_rel=gemini.quote(s["gemini"],max_age_ms=1000)
                kq_rel=await kalshi.quote(s["kalshi"],max_age_ms=1000)
                if g_rel is None or kq_rel is None:
                    m.observe_positive_state(False,time.monotonic())
                    continue
                k_rel=kalshi_tob(kq_rel,s["kalshi"])
                m.both_books_seen+=1
                skew=abs(g_rel.ts_ms-k_rel.ts_ms)
                m.min_skew_ms=skew if m.min_skew_ms is None else min(m.min_skew_ms,skew)
                m.max_skew_ms=max(m.max_skew_ms,skew)
                q1_depth=bool(
                    min(g_rel.yes_ask_size,g_rel.no_ask_size,k_rel.yes_ask_size,k_rel.no_ask_size)>=ONE
                )
                if q1_depth:
                    m.q1_depth_seen+=1
                relaxed=best_candidate(g_rel,k_rel,max_skew_ms=250)
                if relaxed is not None:
                    m.relaxed_positive_observations+=1
                    m.max_profit=max(m.max_profit,relaxed.locked_profit)

                g=gemini.quote(s["gemini"],max_age_ms=100)
                kq=await kalshi.quote(s["kalshi"],max_age_ms=100)
                if g is None or kq is None:
                    m.observe_positive_state(False,time.monotonic())
                    continue
                k=kalshi_tob(kq,s["kalshi"])
                if abs(g.ts_ms-k.ts_ms)>50:
                    m.observe_positive_state(False,time.monotonic())
                    continue
                m.strict_evaluations+=1
                c=best_candidate(g,k,max_skew_ms=50)
                strict_positive=c is not None
                m.observe_positive_state(strict_positive,time.monotonic())
                if c is None:
                    continue
                m.strict_positive_observations+=1
                m.max_strict_profit=max(m.max_strict_profit,c.locked_profit)
                m.last_direction=c.direction; m.last_profit=c.locked_profit
                if i not in pending_rechecks or pending_rechecks[i].done():
                    pending_rechecks[i]=asyncio.create_task(recheck(i,c.direction,g,k))
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if pending_rechecks:
            await asyncio.gather(*pending_rechecks.values(),return_exceptions=True)
        await kalshi.close()

    now=time.monotonic()
    for m in metrics:
        m.finalize(now)
    rows=[m.as_dict() for m in metrics]
    strict_positive_pairs=sum(1 for m in metrics if m.strict_positive_observations>0)
    relaxed_positive_pairs=sum(1 for m in metrics if m.relaxed_positive_observations>0)
    pairs_with_q1=sum(1 for m in metrics if m.q1_depth_seen>0)
    result={
        "status":"NFL_GK_HOT_DEPTH_SUMMARY",
        "pairs":len(specs),"runtime_s":round(time.monotonic()-started,3),
        "gemini_connected":gemini.connected,"gemini_book_updates":gemini.book_updates,
        "gemini_dropped_updates":gemini.dropped_updates,
        "kalshi_initialized_books":kalshi.initialized_count,
        "kalshi_dropped_updates":kalshi.dropped_updates,
        "pairs_with_q1_depth":pairs_with_q1,
        "pairs_with_relaxed_live_positive":relaxed_positive_pairs,
        "pairs_with_strict_100ms_50ms_positive":strict_positive_pairs,
        "strict_positive_observations":sum(m.strict_positive_observations for m in metrics),
        "rechecks_started":sum(m.rechecks_started for m in metrics),
        "rechecks_survived":sum(m.rechecks_survived for m in metrics),
        "max_strict_profit":str(max((m.max_strict_profit for m in metrics),default=D("0"))),
        "top_pairs":sorted(rows,key=lambda r:D(r["max_profit"]),reverse=True),
        "classification":"SHADOW_ONLY_RULES_UNCERTIFIED_NO_ORDER_ENTRY",
        "realized_pnl":"0","read_only":True,"order_entry":False,
    }
    logger.info("NFL_GK_HOT_DEPTH_SUMMARY %s",json.dumps(result,separators=(",",":")))
    return result


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--seconds",type=float,default=120.0)
    args=p.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.seconds))


if __name__=="__main__":
    main()
