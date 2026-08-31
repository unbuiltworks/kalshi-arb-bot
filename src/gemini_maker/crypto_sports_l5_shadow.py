"""Unified Gemini L5-style Crypto + Sports live shadow.

Public-data only. No credentials, balances, order entry, cancels, or account state.

Purpose: start measuring the L5-shaped passive-liquidity mechanism across the full
short-crypto universe plus high-flow Sports contracts expiring within 24 hours.
The collector stands virtual q1 bids on YES and NO simultaneously at the current
join, credits maker fills only from public prints against a fresh book, holds
residual inventory, and records 5s/15s/30s executable markouts.

Sports are deliberately fail-closed for simulated exposure. We still record top
same-day Sports books/trades when rules are not yet certified, but those markets
are telemetry-only and cannot generate virtual fills. The initial v0 certifier is
conservative: simple two-outcome tennis/baseball winner markets only, excluding
spread/total/prop/futures/tie-like language. This lets evidence start accumulating
immediately without pretending ambiguous Sports settlement is safe.

Classification: SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Iterable, Mapping

import httpx
import websockets

from src.gemini_maker.completion_frontier import MAKER_FEE_RATE, _fee, _rebate

REST = "https://api.gemini.com"
PRED_WS = "wss://ws.gemini.com"
CRYPTO_RE = re.compile(r"^(BTC|ETH|SOL|XRP)(05|15)M(\d{10})$")
BOOK_MAX_AGE_S = 2.0
NO_NEW_QUOTES_TTE_S = 15.0
MAX_SPEND_PER_MARKET = 10.0
QUOTE_SIZE = 1.0
MARKOUT_HORIZONS_S = (5.0, 15.0, 30.0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _subcategory(row: Mapping[str, Any]) -> str:
    value = row.get("subcategory")
    if isinstance(value, Mapping):
        return str(value.get("name") or value.get("title") or value.get("slug") or "")
    return str(value or "")


def _text_blob(row: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    keys = (
        "title", "name", "description", "question", "subtitle", "outcome",
        "contractName", "marketType", "type", "ticker", "instrumentSymbol",
    )
    values: list[str] = [_subcategory(row)]
    for obj in (row, contract):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, (str, int, float)):
                values.append(str(value))
    return " ".join(values).lower()


SPORT_OK_CUES = (
    "tennis", "atp", "wta", "us open", "baseball", "mlb",
)
SPORT_BAD_CUES = (
    "spread", "handicap", "total", "over ", "under ", "points", "run line",
    "runs total", "goals", "sets total", "games total", "margin", "prop",
    "futures", "future", "series winner", "champion", "championship", "season",
    "playoffs", "draw", " tie", "ties ",
)
WIN_CUES = (" win", "winner", "moneyline", "defeat", " beat")


def certify_sports(
    row: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    active_contract_count: int,
) -> tuple[bool, str]:
    """Conservative v0 Sports rule gate for shadow exposure.

    This is intentionally narrower than the discovery universe. A rejected market
    is still recorded as telemetry, but it cannot create simulated fills.
    """
    text = f" {_text_blob(row, contract)} "
    if not any(cue in text for cue in SPORT_OK_CUES):
        return False, "SPORT_NOT_IN_V0_ALLOWLIST"
    if any(cue in text for cue in SPORT_BAD_CUES):
        return False, "COMPLEX_OR_AMBIGUOUS_SPORTS_MARKET"
    if active_contract_count != 2:
        return False, "NOT_TWO_ACTIVE_OUTCOMES"
    if not (any(cue in text for cue in WIN_CUES) or "tennis" in text or "baseball" in text or "mlb" in text):
        return False, "WINNER_SEMANTICS_NOT_EXPLICIT"
    return True, "CERTIFIED_SIMPLE_TWO_OUTCOME_WINNER_V0"


@dataclass(frozen=True)
class Market:
    category: str
    ticker: str
    symbol: str
    title: str
    subcategory: str
    expiry_ms: int
    effective_ms: int | None
    price_increment: float
    volume24h: float
    certified: bool
    cert_reason: str

    @property
    def seconds_to_expiry(self) -> float:
        return (self.expiry_ms - int(time.time() * 1000)) / 1000.0

    @property
    def shadow_enabled(self) -> bool:
        return self.category == "Crypto" or self.certified


@dataclass
class Fill:
    side: str
    price: float
    qty: float
    mono_t: float
    observed_at: str
    category: str
    markouts: dict[str, float] = field(default_factory=dict)


@dataclass
class MarketState:
    market: Market
    yes_bid: float | None = None
    yes_ask: float | None = None
    book_mono_t: float | None = None
    consumed: set[str] = field(default_factory=set)
    yes_qty: float = 0.0
    yes_cost: float = 0.0
    no_qty: float = 0.0
    no_cost: float = 0.0
    spend: float = 0.0
    fills: list[Fill] = field(default_factory=list)
    book_updates: int = 0
    trade_updates: int = 0

    @property
    def no_bid(self) -> float | None:
        return None if self.yes_ask is None else 1.0 - self.yes_ask

    def on_book(self, bid: float, ask: float, now_mono: float) -> list[dict[str, Any]]:
        if not (0.0 < bid < ask < 1.0):
            return []
        self.yes_bid = bid
        self.yes_ask = ask
        self.book_mono_t = now_mono
        self.consumed.clear()
        self.book_updates += 1
        return self._markout_rows(now_mono)

    def _markout_rows(self, now_mono: float) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.yes_bid is None or self.yes_ask is None:
            return rows
        current = {"yes": self.yes_bid, "no": 1.0 - self.yes_ask}
        for fill in self.fills:
            for horizon in MARKOUT_HORIZONS_S:
                key = str(int(horizon))
                if key in fill.markouts or now_mono - fill.mono_t < horizon:
                    continue
                value = current[fill.side] - fill.price
                fill.markouts[key] = value
                rows.append({
                    "type": "L5_SHADOW_MARKOUT",
                    "observed_at": _now_iso(),
                    "category": self.market.category,
                    "ticker": self.market.ticker,
                    "symbol": self.market.symbol,
                    "side": fill.side,
                    "fill_price": round(fill.price, 6),
                    "horizon_s": int(horizon),
                    "executable_bid": round(current[fill.side], 6),
                    "markout_per_contract": round(value, 6),
                    "read_only": True,
                    "order_entry": False,
                })
        return rows

    def on_trade(
        self,
        *,
        price: float,
        qty: float,
        buyer_is_maker: bool,
        now_mono: float,
    ) -> list[dict[str, Any]]:
        self.trade_updates += 1
        if not self.market.shadow_enabled:
            return []
        if self.book_mono_t is None or self.yes_bid is None or self.yes_ask is None:
            return []
        book_age = now_mono - self.book_mono_t
        if book_age < 0 or book_age > BOOK_MAX_AGE_S:
            return []
        if self.market.seconds_to_expiry <= NO_NEW_QUOTES_TTE_S:
            return []
        if self.spend >= MAX_SPEND_PER_MARKET:
            return []
        if not (0.0 < price < 1.0) or qty <= 0:
            return []

        # Same public-print convention as the sealed Gemini frontier.
        # buyer_is_maker=True => taker sold YES into resting YES bids.
        # buyer_is_maker=False => taker bought YES, equivalent to selling NO.
        candidates: list[tuple[str, float, bool]] = [
            ("yes", self.yes_bid, bool(buyer_is_maker)),
            ("no", 1.0 - self.yes_ask, not bool(buyer_is_maker)),
        ]
        rows: list[dict[str, Any]] = []
        for side, quote, crosses in candidates:
            if not crosses or side in self.consumed or not (0.0 < quote < 1.0):
                continue
            effective_trade_price = price if side == "yes" else 1.0 - price
            if quote + 1e-12 < effective_trade_price:
                continue
            max_affordable = (MAX_SPEND_PER_MARKET - self.spend) / quote
            fill_qty = min(QUOTE_SIZE, qty, max_affordable)
            if fill_qty <= 1e-9:
                continue
            cost = fill_qty * quote
            if side == "yes":
                self.yes_qty += fill_qty
                self.yes_cost += cost
            else:
                self.no_qty += fill_qty
                self.no_cost += cost
            self.spend += cost
            self.consumed.add(side)
            fill = Fill(
                side=side,
                price=quote,
                qty=fill_qty,
                mono_t=now_mono,
                observed_at=_now_iso(),
                category=self.market.category,
            )
            self.fills.append(fill)
            modeled_rebate = _rebate(fill_qty, quote) if self.market.category == "Crypto" else None
            rows.append({
                "type": "L5_SHADOW_PROXY_FILL",
                "observed_at": fill.observed_at,
                "category": self.market.category,
                "subcategory": self.market.subcategory,
                "ticker": self.market.ticker,
                "symbol": self.market.symbol,
                "certification": self.market.cert_reason,
                "side": side,
                "price": round(quote, 6),
                "quantity": round(fill_qty, 6),
                "book_age_ms": round(book_age * 1000.0, 3),
                "maker_fee_current_model": round(_fee(fill_qty, quote, MAKER_FEE_RATE), 6),
                "modeled_crypto_rebate_not_realized": None if modeled_rebate is None else round(modeled_rebate, 6),
                "sports_subsidy_eligibility": "UNKNOWN" if self.market.category == "Sports" else None,
                "evidence_class": "SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE",
                "read_only": True,
                "order_entry": False,
                "realized": False,
            })
            break
        return rows

    def summary(self) -> dict[str, Any]:
        matched = min(self.yes_qty, self.no_qty)
        total = self.yes_qty + self.no_qty
        yes_avg = self.yes_cost / self.yes_qty if self.yes_qty else None
        no_avg = self.no_cost / self.no_qty if self.no_qty else None
        pair_cost = (yes_avg + no_avg) if yes_avg is not None and no_avg is not None else None
        pair_locked = matched * (1.0 - pair_cost) if pair_cost is not None else 0.0
        residual_qty = abs(self.yes_qty - self.no_qty)
        if self.yes_qty > self.no_qty and yes_avg is not None:
            residual_cost = residual_qty * yes_avg
            residual_side = "yes"
        elif self.no_qty > self.yes_qty and no_avg is not None:
            residual_cost = residual_qty * no_avg
            residual_side = "no"
        else:
            residual_cost = 0.0
            residual_side = None
        return {
            "category": self.market.category,
            "subcategory": self.market.subcategory,
            "ticker": self.market.ticker,
            "symbol": self.market.symbol,
            "certified": self.market.certified,
            "cert_reason": self.market.cert_reason,
            "shadow_enabled": self.market.shadow_enabled,
            "seconds_to_expiry": round(self.market.seconds_to_expiry, 1),
            "fills": len(self.fills),
            "turnover": round(self.spend, 6),
            "yes_qty": round(self.yes_qty, 6),
            "no_qty": round(self.no_qty, 6),
            "matched_qty": round(matched, 6),
            "matched_pct": round((2.0 * matched / total), 6) if total else None,
            "pair_cost": round(pair_cost, 6) if pair_cost is not None else None,
            "pair_locked": round(pair_locked, 6),
            "residual_side": residual_side,
            "residual_qty": round(residual_qty, 6),
            "residual_acquisition_cost": round(residual_cost, 6),
            "book_updates": self.book_updates,
            "trade_updates": self.trade_updates,
        }


def _level(raw: Any) -> tuple[float, float] | None:
    if not isinstance(raw, Mapping):
        return None
    p = _float(raw.get("price"), -1.0)
    q = _float(raw.get("amount") if raw.get("amount") is not None else raw.get("quantity"), 0.0)
    if not (0.0 < p < 1.0) or q <= 0:
        return None
    return p, q


def parse_book(payload: Mapping[str, Any]) -> tuple[float, float] | None:
    bids = [x for x in (_level(v) for v in (payload.get("bids") or [])) if x]
    asks = [x for x in (_level(v) for v in (payload.get("asks") or [])) if x]
    if not bids or not asks:
        return None
    bid = max(bids, key=lambda z: z[0])[0]
    ask = min(asks, key=lambda z: z[0])[0]
    return (bid, ask) if 0.0 < bid < ask < 1.0 else None


async def _fetch_category(http: httpx.AsyncClient, category: str) -> list[dict[str, Any]]:
    rows_all: list[dict[str, Any]] = []
    for offset in range(0, 3000, 500):
        response = await http.get(
            f"{REST}/v1/prediction-markets/events",
            params=[("status", "active"), ("category", category), ("limit", "500"), ("offset", str(offset))],
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or [] if isinstance(payload, Mapping) else []
        rows = [dict(r) for r in rows if isinstance(r, Mapping)]
        rows_all.extend(rows)
        if len(rows) < 500:
            break
    return rows_all


def _active_contracts(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for contract in row.get("contracts") or []:
        if not isinstance(contract, Mapping):
            continue
        state = str(contract.get("marketState") or contract.get("status") or "").lower()
        symbol = str(contract.get("instrumentSymbol") or "").strip()
        if symbol and state in {"open", "active", "approved"}:
            out.append(contract)
    return out


async def discover(
    http: httpx.AsyncClient,
    *,
    max_sports_symbols: int,
    sports_hours: float,
) -> tuple[list[Market], list[dict[str, Any]]]:
    now_ms = int(time.time() * 1000)
    horizon_ms = int(max(1.0, sports_hours) * 3600_000)
    crypto_rows, sports_rows = await asyncio.gather(
        _fetch_category(http, "Crypto"),
        _fetch_category(http, "Sports"),
    )
    markets: list[Market] = []
    discovery_rows: list[dict[str, Any]] = []

    for row in crypto_rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        if not CRYPTO_RE.match(ticker):
            continue
        for contract in _active_contracts(row):
            symbol = str(contract.get("instrumentSymbol") or "").strip()
            if not symbol.upper().endswith("-UP"):
                continue
            expiry = _iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
            effective = _iso_ms(contract.get("effectiveDate") or row.get("effectiveDate"))
            if expiry is None or expiry <= now_ms:
                continue
            if effective is not None and effective > now_ms + 10 * 60_000:
                continue
            markets.append(Market(
                category="Crypto",
                ticker=ticker,
                symbol=symbol,
                title=str(row.get("title") or ""),
                subcategory=_subcategory(row),
                expiry_ms=expiry,
                effective_ms=effective,
                price_increment=_float(contract.get("priceIncrement"), 0.01),
                volume24h=_float(row.get("volume24h"), 0.0),
                certified=True,
                cert_reason="CRYPTO_SHORT_WINDOW",
            ))

    sports_candidates: list[Market] = []
    for row in sports_rows:
        active = _active_contracts(row)
        active_count = len(active)
        for contract in active:
            symbol = str(contract.get("instrumentSymbol") or "").strip()
            expiry = _iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
            effective = _iso_ms(contract.get("effectiveDate") or row.get("effectiveDate"))
            if not symbol or expiry is None or expiry <= now_ms or expiry - now_ms > horizon_ms:
                continue
            certified, reason = certify_sports(row, contract, active_contract_count=active_count)
            market = Market(
                category="Sports",
                ticker=str(row.get("ticker") or "").upper().strip(),
                symbol=symbol,
                title=str(row.get("title") or contract.get("title") or ""),
                subcategory=_subcategory(row),
                expiry_ms=expiry,
                effective_ms=effective,
                price_increment=_float(contract.get("priceIncrement"), 0.01),
                volume24h=_float(row.get("volume24h"), _float(row.get("volume"), 0.0)),
                certified=certified,
                cert_reason=reason,
            )
            sports_candidates.append(market)

    sports_candidates.sort(key=lambda m: (not m.certified, -m.volume24h, m.expiry_ms))
    selected_sports = sports_candidates[: max(0, max_sports_symbols)]
    markets.extend(selected_sports)
    for market in selected_sports:
        discovery_rows.append({
            "type": "L5_SHADOW_MARKET_DISCOVERED",
            "observed_at": _now_iso(),
            "category": market.category,
            "subcategory": market.subcategory,
            "ticker": market.ticker,
            "symbol": market.symbol,
            "title": market.title,
            "expiry_ms": market.expiry_ms,
            "volume24h_contracts": market.volume24h,
            "certified": market.certified,
            "cert_reason": market.cert_reason,
            "shadow_enabled": market.shadow_enabled,
            "read_only": True,
            "order_entry": False,
        })
    return markets, discovery_rows


def _atomic_json(path: Path | None, payload: Mapping[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _aggregate(states: Iterable[MarketState], category: str | None = None) -> dict[str, Any]:
    selected = [s for s in states if category is None or s.market.category == category]
    enabled = [s for s in selected if s.market.shadow_enabled]
    fills = [f for s in enabled for f in s.fills]
    matched = sum(min(s.yes_qty, s.no_qty) for s in enabled)
    total_qty = sum(s.yes_qty + s.no_qty for s in enabled)
    turnover = sum(s.spend for s in enabled)
    pair_locked = sum(s.summary()["pair_locked"] for s in enabled)
    residual_cost = sum(s.summary()["residual_acquisition_cost"] for s in enabled)
    markouts: dict[str, list[float]] = {str(int(h)): [] for h in MARKOUT_HORIZONS_S}
    for fill in fills:
        for key, value in fill.markouts.items():
            markouts.setdefault(key, []).append(value)
    return {
        "markets_recorded": len(selected),
        "markets_shadow_enabled": len(enabled),
        "fills": len(fills),
        "maker_turnover_dollars": round(turnover, 6),
        "matched_quantity": round(matched, 6),
        "matched_quantity_pct": round(2.0 * matched / total_qty, 6) if total_qty else None,
        "pair_locked_margin": round(pair_locked, 6),
        "one_sided_residual_acquisition_cost": round(residual_cost, 6),
        "markout_mean_per_contract": {
            key: (round(sum(values) / len(values), 6) if values else None)
            for key, values in markouts.items()
        },
        "markout_samples": {key: len(values) for key, values in markouts.items()},
    }


def build_summary(
    states: Mapping[str, MarketState],
    stats: Mapping[str, int],
    *,
    status: str,
    seconds: float,
    max_sports_symbols: int,
) -> dict[str, Any]:
    values = list(states.values())
    return {
        "type": "GEMINI_L5_CRYPTO_SPORTS_SHADOW",
        "observed_at": _now_iso(),
        "status": status,
        "classification": "SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE",
        "seconds_requested": seconds,
        "max_sports_symbols": max_sports_symbols,
        "quote_size": QUOTE_SIZE,
        "max_spend_per_market": MAX_SPEND_PER_MARKET,
        "book_max_age_s": BOOK_MAX_AGE_S,
        "stats": dict(stats),
        "crypto": _aggregate(values, "Crypto"),
        "sports": _aggregate(values, "Sports"),
        "combined": _aggregate(values, None),
        "sports_certified_markets": sum(1 for s in values if s.market.category == "Sports" and s.market.certified),
        "sports_telemetry_only_markets": sum(1 for s in values if s.market.category == "Sports" and not s.market.certified),
        "realized_pnl": None,
        "modeled_subsidy_is_realized": False,
        "sports_subsidy_eligibility": "UNKNOWN_PENDING_SEP2_VERIFICATION",
        "credentials_loaded": False,
        "read_only": True,
        "order_entry": False,
        "warnings": [
            "Proxy fills ignore actual queue position and are not executable fills.",
            "Sports v0 rule certification is intentionally narrow and telemetry-only when uncertain.",
            "Sports subsidy eligibility is not assumed.",
            "Settlement-scored realized P&L is unavailable until authoritative labels arrive.",
        ],
        "markets": {symbol: state.summary() for symbol, state in states.items()},
    }


async def _seed_book(http: httpx.AsyncClient, state: MarketState) -> None:
    response = await http.get(
        f"{REST}/v1/book/{state.market.symbol}",
        params={"limit_bids": 20, "limit_asks": 20},
        headers={"Cache-Control": "no-cache"},
    )
    response.raise_for_status()
    payload = response.json()
    parsed = parse_book(payload if isinstance(payload, Mapping) else {})
    if parsed is None:
        raise RuntimeError("incomplete book")
    state.on_book(parsed[0], parsed[1], time.monotonic())


async def capture(
    *,
    seconds: float,
    output: Path,
    summary_output: Path | None,
    max_sports_symbols: int,
    sports_hours: float,
    discover_s: float,
    checkpoint_s: float,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(1.0, seconds)
    states: dict[str, MarketState] = {}
    stats = {
        "ws_connections": 0,
        "ws_reconnects": 0,
        "messages": 0,
        "book_tickers": 0,
        "trades": 0,
        "proxy_fills": 0,
        "errors": 0,
        "discoveries": 0,
        "subscriptions": 0,
        "rest_seeds": 0,
        "control_messages": 0,
    }
    last_checkpoint = 0.0
    last_discover = 0.0
    known_discovery_rows: set[str] = set()

    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": "arb-gemini-l5-crypto-sports-shadow/1.0"},
    ) as http:
        with output.open("a", encoding="utf-8") as fh:
            def write(row: Mapping[str, Any]) -> None:
                fh.write(json.dumps(dict(row), separators=(",", ":"), default=str) + "\n")
                fh.flush()

            write({
                "type": "L5_SHADOW_START",
                "observed_at": _now_iso(),
                "seconds": seconds,
                "max_sports_symbols": max_sports_symbols,
                "sports_hours": sports_hours,
                "credentials_loaded": False,
                "read_only": True,
                "order_entry": False,
            })

            while time.monotonic() < deadline:
                try:
                    markets, discovery_rows = await discover(
                        http,
                        max_sports_symbols=max_sports_symbols,
                        sports_hours=sports_hours,
                    )
                    stats["discoveries"] += 1
                    for row in discovery_rows:
                        key = str(row.get("symbol")) + ":" + str(row.get("cert_reason"))
                        if key not in known_discovery_rows:
                            known_discovery_rows.add(key)
                            write(row)
                    for market in markets:
                        key = market.symbol.upper()
                        if key not in states:
                            states[key] = MarketState(market=market)
                            try:
                                await _seed_book(http, states[key])
                                stats["rest_seeds"] += 1
                            except Exception as exc:
                                stats["errors"] += 1
                                write({
                                    "type": "L5_SHADOW_SEED_ERROR",
                                    "observed_at": _now_iso(),
                                    "symbol": market.symbol,
                                    "error_type": type(exc).__name__,
                                    "error": str(exc)[:240],
                                    "read_only": True,
                                    "order_entry": False,
                                })
                    desired = [m.symbol for m in markets]
                    if not desired:
                        await asyncio.sleep(2.0)
                        continue

                    stats["ws_connections"] += 1
                    if stats["ws_connections"] > 1:
                        stats["ws_reconnects"] += 1
                    async with websockets.connect(
                        PRED_WS,
                        open_timeout=5,
                        ping_interval=15,
                        ping_timeout=8,
                        max_queue=20000,
                    ) as ws:
                        streams = [f"{s}@trade" for s in desired] + [f"{s}@bookTicker" for s in desired]
                        for i in range(0, len(streams), 20):
                            batch = streams[i:i + 20]
                            await ws.send(json.dumps({"id": f"l5cs-{i}", "method": "SUBSCRIBE", "params": batch}))
                            stats["subscriptions"] += len(batch)
                            await asyncio.sleep(0.15)
                        session_end = min(deadline, time.monotonic() + max(60.0, discover_s))
                        while time.monotonic() < session_end:
                            now = time.monotonic()
                            if now - last_checkpoint >= max(5.0, checkpoint_s):
                                _atomic_json(summary_output, build_summary(
                                    states, stats, status="RUNNING", seconds=seconds,
                                    max_sports_symbols=max_sports_symbols,
                                ))
                                last_checkpoint = now
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            except asyncio.TimeoutError:
                                # Markouts mature even during quiet periods.
                                for state in states.values():
                                    for row in state._markout_rows(time.monotonic()):
                                        write(row)
                                continue
                            try:
                                message = json.loads(raw)
                            except Exception:
                                stats["errors"] += 1
                                continue
                            if not isinstance(message, Mapping):
                                continue
                            stats["messages"] += 1
                            symbol = str(message.get("s") or "").upper()
                            state = states.get(symbol)
                            if state is None:
                                stats["control_messages"] += 1
                                status_code = message.get("status")
                                if isinstance(status_code, int) and status_code >= 400:
                                    stats["errors"] += 1
                                    write({"type": "L5_SHADOW_WS_CONTROL", "observed_at": _now_iso(), "message": dict(message)})
                                continue
                            now_mono = time.monotonic()
                            if "b" in message and "a" in message and "u" in message:
                                bid = _float(message.get("b"), -1.0)
                                ask = _float(message.get("a"), 2.0)
                                rows = state.on_book(bid, ask, now_mono)
                                stats["book_tickers"] += 1
                                for row in rows:
                                    write(row)
                            elif "p" in message and "q" in message and "m" in message:
                                price = _float(message.get("p"), -1.0)
                                qty = _float(message.get("q"), 0.0)
                                rows = state.on_trade(
                                    price=price,
                                    qty=qty,
                                    buyer_is_maker=bool(message.get("m")),
                                    now_mono=now_mono,
                                )
                                stats["trades"] += 1
                                stats["proxy_fills"] += sum(1 for r in rows if r.get("type") == "L5_SHADOW_PROXY_FILL")
                                write({
                                    "type": "L5_SHADOW_PUBLIC_TRADE",
                                    "observed_at": _now_iso(),
                                    "category": state.market.category,
                                    "ticker": state.market.ticker,
                                    "symbol": state.market.symbol,
                                    "price": price,
                                    "quantity": qty,
                                    "buyer_is_maker": bool(message.get("m")),
                                    "trade_id": message.get("t"),
                                    "shadow_enabled": state.market.shadow_enabled,
                                    "read_only": True,
                                    "order_entry": False,
                                })
                                for row in rows:
                                    write(row)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    stats["errors"] += 1
                    write({
                        "type": "L5_SHADOW_RECONNECT",
                        "observed_at": _now_iso(),
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                        "read_only": True,
                        "order_entry": False,
                    })
                    await asyncio.sleep(1.0)

    final = build_summary(
        states, stats, status="COMPLETED", seconds=seconds,
        max_sports_symbols=max_sports_symbols,
    )
    _atomic_json(summary_output, final)
    print(json.dumps(final, indent=2, sort_keys=True), flush=True)
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=21600.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/gemini-l5-crypto-sports-shadow.jsonl"))
    parser.add_argument("--summary-output", type=Path, default=Path("artifacts/gemini-l5-crypto-sports-shadow-summary.json"))
    parser.add_argument("--max-sports-symbols", type=int, default=50)
    parser.add_argument("--sports-hours", type=float, default=24.0)
    parser.add_argument("--discover-s", type=float, default=600.0)
    parser.add_argument("--checkpoint-s", type=float, default=30.0)
    args = parser.parse_args()
    asyncio.run(capture(
        seconds=args.seconds,
        output=args.output,
        summary_output=args.summary_output,
        max_sports_symbols=args.max_sports_symbols,
        sports_hours=args.sports_hours,
        discover_s=args.discover_s,
        checkpoint_s=args.checkpoint_s,
    ))


if __name__ == "__main__":
    main()
