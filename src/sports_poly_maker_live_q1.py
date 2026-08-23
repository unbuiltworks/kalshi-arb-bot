"""Bounded q=1 Polymarket-US same-venue nested MLB maker live probe.

The probe is intentionally narrow:
- MLB player props only;
- same game + same player + same stat + ordered integer thresholds;
- q=1 and instrument lot size exactly 1 on both markets;
- maker leg is post-only (participateDontInitiate) and short-lived GTD;
- hedge is submitted only after a confirmed full maker fill and fresh q=1 depth;
- >=5c modeled lock before posting; >=3c after maker fill/final revalidation;
- shared sports cumulative capital ceiling is enforced from the authoritative ledger;
- zero pre-existing position on either market;
- no sportsbook, Kalshi, Gemini, crypto, or MVE mutation.

A no-fill cancels/releases. Unexpected partial fill or failed hedge triggers immediate
flatten attempt and hard-halt reporting. A successful pair remains FILLED_NOT_SETTLED
until authoritative settlement reconciliation; it is not recorded as realized P&L here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
import json
import os
import time
from typing import Any, Mapping

import httpx
from polymarket_us import PolymarketUS

from src.sports_poly_current_fees import maker_then_taker_locked_profit
from src.sports_poly_maker_trigger_census import _ask, _bid, run_once as maker_census
from src.strict_mlb_prop_adapters import _name, _poly_identity
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book

D = Decimal
ONE = D("1")
ZERO = D("0")
LIVE_ENV = "ENABLE_SPORTS_POLY_MAKER_LIVE"
PROBE_ID_ENV = "SPORTS_POLY_MAKER_PROBE_ID"
STRATEGY = "sports_poly_nested_maker_q1"
PREPOST_MIN_LOCK = D("0.05")
FINAL_MIN_LOCK = D("0.03")
CUMULATIVE_BUDGET = D(os.getenv("SPORTS_LIVE_CUMULATIVE_BUDGET_USD", "20"))
MAKER_WAIT_SECONDS = int(os.getenv("SPORTS_POLY_MAKER_WAIT_SECONDS", "30"))
POLL_SECONDS = D("0.10")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ingest_url() -> str:
    return os.getenv("ARB_LEDGER_INGEST_URL", "").strip()


def _token() -> str:
    return os.getenv("LEDGER_INGEST_TOKEN", "").strip()


def _trade_id() -> str:
    probe = os.getenv(PROBE_ID_ENV, "").strip()
    if not probe:
        raise RuntimeError(f"{PROBE_ID_ENV} missing")
    safe = "".join(ch for ch in probe if ch.isalnum() or ch in "-_.")[:80]
    if not safe:
        raise RuntimeError("invalid maker probe id")
    return f"sports-poly-maker:{safe}"


def live_enabled() -> bool:
    return os.getenv(LIVE_ENV, "NO").strip().upper() == "YES"


def _native_yes_space(price: D, buy_yes: bool) -> D:
    return price if buy_yes else ONE - price


def _native_side(row: Mapping[str, Any], maker: bool) -> tuple[str, bool]:
    direction = str(row["direction"])
    if direction == "MAKER_LOW_HEDGE_HIGH":
        slug = str(row["low_slug"] if maker else row["high_slug"])
        side = str(row["maker_buy_side"] if maker else row["hedge_buy_side"])
    elif direction == "MAKER_HIGH_HEDGE_LOW":
        slug = str(row["high_slug"] if maker else row["low_slug"])
        side = str(row["maker_buy_side"] if maker else row["hedge_buy_side"])
    else:
        raise ValueError("unknown maker direction")
    if side not in {"YES", "NO"}:
        raise ValueError("unknown outcome side")
    return slug, side == "YES"


def _book_native(book: TopBook, buy_yes: bool) -> tuple[D, D, D]:
    ask, depth = _ask(book, buy_yes)
    bid = _bid(book, buy_yes)
    return bid, ask, depth


def _aligned_floor(value: D, tick: D) -> D:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def choose_maker_price(*, maker_book: TopBook, maker_buy_yes: bool, hedge_ask: D, tick: D) -> D | None:
    """Highest post-only native-outcome price that preserves >=5c at current hedge."""
    if tick <= 0:
        return None
    bid, ask, _ = _book_native(maker_book, maker_buy_yes)
    if ask <= tick:
        return None
    max_post_only = _aligned_floor(ask - tick, tick)
    start = max(tick, _aligned_floor(bid, tick))
    if max_post_only < start:
        return None
    chosen: D | None = None
    p = start
    # Price grids are bounded to [0.01,0.99], so this loop is tiny.
    while p <= max_post_only and p < ONE:
        econ = maker_then_taker_locked_profit(maker_price=p, taker_price=hedge_ask, quantity=1)
        if econ["locked_profit"] >= PREPOST_MIN_LOCK:
            chosen = p
        p += tick
    return chosen


def _strict_rule_certified(low_market: Mapping[str, Any], high_market: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
    low = _poly_identity(low_market)
    high = _poly_identity(high_market)
    if low is None or high is None:
        raise RuntimeError("strict Polymarket MLB identity missing")
    lf, lp, lt, ld, lth = low
    hf, hp, ht, hd, hth = high
    if lf != hf or lf != str(expected.get("family") or ""):
        raise RuntimeError("player-prop family mismatch")
    if _name(lp) != _name(hp) or _name(lp) != _name(str(expected.get("player") or "")):
        raise RuntimeError("player mismatch")
    if tuple(sorted(lt)) != tuple(sorted(ht)) or ld != hd:
        raise RuntimeError("game identity mismatch")
    if lth != D(str(expected["low_threshold"])) or hth != D(str(expected["high_threshold"])) or not lth < hth:
        raise RuntimeError("threshold identity mismatch")
    for market in (low_market, high_market):
        if not bool(market.get("active")) or bool(market.get("closed")) or bool(market.get("archived")):
            raise RuntimeError("market not actively tradeable")
    low_game = str(low_market.get("gameId") or "")
    high_game = str(high_market.get("gameId") or "")
    if low_game and high_game and low_game != high_game:
        raise RuntimeError("provider gameId mismatch")
    low_start = str(low_market.get("gameStartTime") or low_market.get("startDate") or "")
    high_start = str(high_market.get("gameStartTime") or high_market.get("startDate") or "")
    if low_start and high_start and low_start != high_start:
        raise RuntimeError("game start mismatch")
    low_type = str(low_market.get("sportsMarketType") or low_market.get("sportsMarketTypeV2") or "")
    high_type = str(high_market.get("sportsMarketType") or high_market.get("sportsMarketTypeV2") or "")
    if low_type and high_type and low_type != high_type:
        raise RuntimeError("sports market type mismatch")
    return {
        "same_venue": True,
        "same_game": True,
        "same_player": True,
        "same_stat": True,
        "ordered_thresholds": [str(lth), str(hth)],
        "family": lf,
        "game_date": ld,
        "teams": list(sorted(lt)),
        "provider_game_id": low_game or high_game or None,
        "market_type": low_type or high_type or None,
        "logical_identity": "YES(stat>=low) + NO(stat>=high) >= 1",
    }


def _minimum_qty(market: Mapping[str, Any]) -> D:
    value = market.get("minimumTradeQty")
    if value is None:
        raise RuntimeError("minimumTradeQty absent")
    return D(str(value))


def _tick(market: Mapping[str, Any]) -> D:
    value = market.get("orderPriceMinTickSize") or market.get("tickSize")
    if value is None:
        raise RuntimeError("tick size absent")
    return D(str(value))


def _position_qty(payload: Any, slug: str) -> D:
    if not isinstance(payload, Mapping):
        return ZERO
    positions = payload.get("positions") or {}
    row: Any = None
    if isinstance(positions, Mapping):
        row = positions.get(slug)
    elif isinstance(positions, list):
        row = next((x for x in positions if isinstance(x, Mapping) and str(x.get("marketSlug") or x.get("market") or "") == slug), None)
    if not isinstance(row, Mapping):
        return ZERO
    return D(str(row.get("netPosition") or "0"))


def _buying_power(payload: Any) -> D:
    if not isinstance(payload, Mapping):
        return ZERO
    balances = payload.get("balances")
    rows = balances if isinstance(balances, list) else [payload]
    for row in rows:
        if isinstance(row, Mapping) and str(row.get("currency") or "USD").upper() == "USD":
            try:
                return D(str(row.get("buyingPower") or "0"))
            except Exception:
                return ZERO
    return ZERO


def _order_qty(order: Mapping[str, Any]) -> D:
    for key in ("cumQuantity", "filledQuantity", "executedQuantity"):
        if order.get(key) is not None:
            return D(str(order[key]))
    qty = ZERO
    for execution in order.get("executions") or []:
        if isinstance(execution, Mapping):
            inner = execution.get("order") or {}
            if isinstance(inner, Mapping):
                qty = max(qty, D(str(inner.get("cumQuantity") or "0")))
    return qty


def _order_state(order: Mapping[str, Any]) -> str:
    return str(order.get("state") or order.get("status") or "UNKNOWN").upper()


async def _ledger_rows() -> list[dict[str, Any]]:
    ingest = _ingest_url()
    if not ingest:
        raise RuntimeError("ARB_LEDGER_INGEST_URL missing")
    base = ingest.rsplit("/api/events", 1)[0] if "/api/events" in ingest else ingest.rstrip("/")
    async with httpx.AsyncClient(timeout=5.0) as http:
        r = await http.get(f"{base}/api/trades", params={"limit": 1000})
        r.raise_for_status()
        body = r.json()
    if not isinstance(body, list):
        raise RuntimeError("authoritative trades response not a list")
    return [x for x in body if isinstance(x, dict)]


def _sports_budget_used(rows: list[dict[str, Any]]) -> D:
    used = ZERO
    for row in rows:
        if str(row.get("mode") or "") not in {"LIVE", "LIVE_PROBE"}:
            continue
        strategy = str(row.get("strategy") or "")
        if not (strategy.startswith("sports_gk_") or strategy.startswith("sports_final_lag_") or strategy == STRATEGY):
            continue
        actual = D(str(row.get("capital_filled") or "0"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        reserved = D(str(metadata.get("reserved_usd") or "0"))
        if str(row.get("status") or "").upper() == "CAPITAL_RELEASED":
            reserved = ZERO
        used += max(actual, reserved)
    return used


def _unresolved_sports(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    terminal = {"REALIZED", "TRADE_REALIZED", "SETTLED", "SETTLED_COMPATIBLE", "CAPITAL_RELEASED", "NO_FILL_RELEASED"}
    for row in rows:
        if str(row.get("mode") or "") not in {"LIVE", "LIVE_PROBE"}:
            continue
        strategy = str(row.get("strategy") or "")
        if not (strategy.startswith("sports_gk_") or strategy.startswith("sports_final_lag_") or strategy == STRATEGY):
            continue
        if str(row.get("status") or "").upper() in terminal:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        if D(str(row.get("capital_filled") or "0")) > 0 or D(str(metadata.get("reserved_usd") or "0")) > 0:
            return row
    return None


async def _post_event(event_type: str, *, trade_id: str, status: str, market: str, amount: D | None = None, metadata: Mapping[str, Any] | None = None, side: str | None = None) -> None:
    url, token = _ingest_url(), _token()
    if not url or not token:
        raise RuntimeError("authoritative ledger ingest config missing")
    event = {
        "event_id": f"{trade_id}:{event_type.lower()}:{int(time.time()*1000)}",
        "trade_id": trade_id,
        "event_type": event_type,
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": market,
        "asset": "MLB_PLAYER_PROP",
        "venues": ["polymarket"],
        "status": status,
        "legs_required": 2,
        "side": side,
        "metadata": dict(metadata or {}),
    }
    if amount is not None:
        event["amount"] = str(amount)
    async with httpx.AsyncClient(timeout=5.0) as http:
        r = await http.post(url, headers={"Authorization": f"Bearer {token}"}, json=event)
        r.raise_for_status()
        body = r.json()
    if not isinstance(body, Mapping) or not body.get("ok"):
        raise RuntimeError("authoritative ledger write not acknowledged")


def _maker_payload(slug: str, buy_yes: bool, native_price: D, good_till: str) -> dict[str, Any]:
    return {
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG" if buy_yes else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(_native_yes_space(native_price, buy_yes)), "currency": "USD"},
        "quantity": 1,
        "tif": "TIME_IN_FORCE_GOOD_TILL_DATE",
        "participateDontInitiate": True,
        "goodTillTime": good_till,
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        "synchronousExecution": False,
    }


def _hedge_payload(slug: str, buy_yes: bool, native_price: D) -> dict[str, Any]:
    return {
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG" if buy_yes else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(_native_yes_space(native_price, buy_yes)), "currency": "USD"},
        "quantity": 1,
        "tif": "TIME_IN_FORCE_FILL_OR_KILL",
        "participateDontInitiate": False,
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        "synchronousExecution": True,
        "maxBlockTime": "2",
    }


async def _flatten(client: PolymarketUS, slug: str, yes_space_price: D) -> bool:
    try:
        await asyncio.to_thread(client.orders.close_position, {
            "marketSlug": slug,
            "slippageTolerance": {
                "currentPrice": {"value": str(yes_space_price), "currency": "USD"},
                "ticks": 20,
            },
        })
        for _ in range(30):
            pos = await asyncio.to_thread(client.portfolio.positions, {"market": slug, "limit": 10})
            if _position_qty(pos, slug) == ZERO:
                return True
            await asyncio.sleep(0.1)
    except Exception:
        return False
    return False


async def _select_ready(client: PolymarketUS) -> tuple[dict[str, Any], dict[str, Any]]:
    census = await maker_census()
    candidates = [
        r for r in (census.get("top_candidates") or [])
        if isinstance(r, dict) and r.get("kind") == "MLB_PLAYER_PROP"
        and D(str(r.get("locked_profit_after_hypothetical_maker_fill") or "0")) >= D("0.10")
    ]
    for row in candidates:
        low_slug, high_slug = str(row["low_slug"]), str(row["high_slug"])
        try:
            low_market, high_market = await asyncio.gather(
                asyncio.to_thread(client.markets.retrieve_by_slug, low_slug),
                asyncio.to_thread(client.markets.retrieve_by_slug, high_slug),
            )
            audit = _strict_rule_certified(low_market, high_market, row)
            if _minimum_qty(low_market) != ONE or _minimum_qty(high_market) != ONE:
                continue
            maker_slug, maker_buy_yes = _native_side(row, True)
            hedge_slug, hedge_buy_yes = _native_side(row, False)
            if _position_qty(await asyncio.to_thread(client.portfolio.positions, {"market": maker_slug, "limit": 10}), maker_slug) != ZERO:
                continue
            if _position_qty(await asyncio.to_thread(client.portfolio.positions, {"market": hedge_slug, "limit": 10}), hedge_slug) != ZERO:
                continue
            maker_book_raw, hedge_book_raw = await asyncio.gather(
                asyncio.to_thread(client.markets.book, maker_slug),
                asyncio.to_thread(client.markets.book, hedge_slug),
            )
            maker_book, hedge_book = poly_top_book(maker_book_raw), poly_top_book(hedge_book_raw)
            if maker_book is None or hedge_book is None:
                continue
            _, _, hedge_depth = _book_native(hedge_book, hedge_buy_yes)
            if hedge_depth < ONE:
                continue
            hedge_ask = _book_native(hedge_book, hedge_buy_yes)[1]
            maker_market = low_market if maker_slug == low_slug else high_market
            maker_px = choose_maker_price(
                maker_book=maker_book,
                maker_buy_yes=maker_buy_yes,
                hedge_ask=hedge_ask,
                tick=_tick(maker_market),
            )
            if maker_px is None:
                continue
            econ = maker_then_taker_locked_profit(maker_price=maker_px, taker_price=hedge_ask, quantity=1)
            if econ["locked_profit"] < PREPOST_MIN_LOCK:
                continue
            ready = {
                **row,
                "maker_slug": maker_slug,
                "maker_buy_side": "YES" if maker_buy_yes else "NO",
                "maker_post_price": str(maker_px),
                "hedge_slug": hedge_slug,
                "hedge_buy_side": "YES" if hedge_buy_yes else "NO",
                "hedge_current_ask": str(hedge_ask),
                "hedge_current_depth": str(hedge_depth),
                "prepost_locked_profit": str(econ["locked_profit"]),
                "rule_audit": audit,
            }
            return ready, {"low": low_market, "high": high_market}
        except Exception:
            continue
    raise RuntimeError("no strict MLB maker candidate >=10c with q1 lot/depth is ready now")


async def run_once() -> dict[str, Any]:
    trade_id = _trade_id()
    client = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=5.0,
    )
    try:
        rows = await _ledger_rows()
        if any(str(r.get("trade_id") or "") == trade_id for r in rows):
            raise RuntimeError("one-shot probe id already exists in authoritative ledger")
        unresolved = _unresolved_sports(rows)
        if unresolved is not None:
            raise RuntimeError(f"unresolved sports exposure blocks maker probe: {unresolved.get('trade_id')}")
        used = _sports_budget_used(rows)
        if used + ONE > CUMULATIVE_BUDGET:
            raise RuntimeError(f"sports budget exhausted used={used} requested=1 ceiling={CUMULATIVE_BUDGET}")
        balances = await asyncio.to_thread(client.account.balances)
        buying_power = _buying_power(balances)
        if buying_power < ONE:
            raise RuntimeError(f"insufficient Polymarket buying power for bounded q1 probe: {buying_power}")

        ready, _markets = await _select_ready(client)
        maker_slug, maker_buy_yes = str(ready["maker_slug"]), ready["maker_buy_side"] == "YES"
        hedge_slug, hedge_buy_yes = str(ready["hedge_slug"]), ready["hedge_buy_side"] == "YES"
        maker_px = D(str(ready["maker_post_price"]))
        hedge_px = D(str(ready["hedge_current_ask"]))
        good_till = (datetime.now(timezone.utc) + timedelta(seconds=MAKER_WAIT_SECONDS)).isoformat().replace("+00:00", "Z")
        maker_payload = _maker_payload(maker_slug, maker_buy_yes, maker_px, good_till)
        # Preview is non-mutating and validates account/order shape before any reservation.
        preview = await asyncio.to_thread(client.orders.preview, maker_payload)
        preflight = {
            "status": "SPORTS_POLY_MAKER_Q1_READY",
            "trade_id": trade_id,
            "live_enabled": live_enabled(),
            "buying_power": str(buying_power),
            "sports_budget_used": str(used),
            "sports_budget_ceiling": str(CUMULATIVE_BUDGET),
            "candidate": ready,
            "maker_good_till": good_till,
            "preview_ok": isinstance(preview, Mapping),
        }
        print("SPORTS_POLY_MAKER_Q1_PREFLIGHT " + json.dumps(preflight, separators=(",", ":"), default=str), flush=True)
        if not live_enabled():
            return preflight

        await _post_event(
            "CAPITAL_RESERVED", trade_id=trade_id, status="CAPITAL_RESERVED", market=f"{maker_slug}|{hedge_slug}", amount=ONE,
            metadata={"reserved_usd": "1", "cumulative_budget_usd": str(CUMULATIVE_BUDGET), "candidate": ready, "rule_certified": True},
            side=f"MAKER {ready['maker_buy_side']} THEN HEDGE {ready['hedge_buy_side']}",
        )

        created = await asyncio.to_thread(client.orders.create, maker_payload)
        order_id = str((created or {}).get("id") or "")
        if not order_id:
            raise RuntimeError("maker create returned no order id")
        await _post_event(
            "ORDER_SUBMITTED", trade_id=trade_id, status="MAKER_RESTING", market=maker_slug, amount=maker_px,
            metadata={"order_id": order_id, "post_only": True, "good_till": good_till, "candidate": ready},
            side=f"BUY {ready['maker_buy_side']}",
        )

        deadline = time.monotonic() + MAKER_WAIT_SECONDS + 3
        maker_filled = False
        maker_state = "UNKNOWN"
        maker_qty = ZERO
        while time.monotonic() < deadline:
            order = await asyncio.to_thread(client.orders.retrieve, order_id)
            maker_state, maker_qty = _order_state(order), _order_qty(order)
            if maker_qty == ONE or "FILLED" in maker_state and "PARTIALLY" not in maker_state:
                maker_filled = True
                maker_qty = ONE
                break
            if maker_qty not in {ZERO, ONE}:
                await asyncio.to_thread(client.orders.cancel, order_id, {"marketSlug": maker_slug})
                flat = await _flatten(client, maker_slug, _native_yes_space(maker_px, maker_buy_yes))
                await _post_event("EXECUTION_RESULT", trade_id=trade_id, status="UNEXPECTED_PARTIAL_HARD_HALT", market=maker_slug, amount=maker_qty, metadata={"maker_state": maker_state, "maker_qty": str(maker_qty), "flattened": flat})
                return {"status": "UNEXPECTED_PARTIAL_HARD_HALT", "flattened": flat, "maker_qty": str(maker_qty)}
            if any(x in maker_state for x in ("CANCELED", "REJECTED", "EXPIRED")):
                break
            await asyncio.sleep(float(POLL_SECONDS))

        if not maker_filled:
            try:
                await asyncio.to_thread(client.orders.cancel, order_id, {"marketSlug": maker_slug})
            except Exception:
                pass
            pos = _position_qty(await asyncio.to_thread(client.portfolio.positions, {"market": maker_slug, "limit": 10}), maker_slug)
            if pos != ZERO:
                flat = await _flatten(client, maker_slug, _native_yes_space(maker_px, maker_buy_yes))
                await _post_event("EXECUTION_RESULT", trade_id=trade_id, status="MAKER_TIMEOUT_POSITION_HARD_HALT", market=maker_slug, amount=pos, metadata={"flattened": flat, "maker_state": maker_state})
                return {"status": "MAKER_TIMEOUT_POSITION_HARD_HALT", "flattened": flat}
            await _post_event("CAPITAL_RELEASED", trade_id=trade_id, status="NO_FILL_RELEASED", market=maker_slug, amount=ZERO, metadata={"reserved_usd": "0", "maker_state": maker_state})
            result = {"status": "MAKER_NO_FILL", "maker_state": maker_state, "order_id": order_id}
            print("SPORTS_POLY_MAKER_Q1_RESULT " + json.dumps(result, separators=(",", ":")), flush=True)
            return result

        await _post_event("FILL", trade_id=trade_id, status="MAKER_FILLED", market=maker_slug, amount=maker_px, metadata={"order_id": order_id, "quantity": "1", "maker": True}, side=f"BUY {ready['maker_buy_side']}")

        # Exact hedge revalidation after confirmed maker fill.
        started = time.perf_counter()
        hedge_book_raw = await asyncio.to_thread(client.markets.book, hedge_slug)
        revalidation_ms = (time.perf_counter() - started) * 1000
        hedge_book = poly_top_book(hedge_book_raw)
        if hedge_book is None:
            hedge_book = None
        if hedge_book is not None:
            _, fresh_hedge_ask, fresh_depth = _book_native(hedge_book, hedge_buy_yes)
            final_econ = maker_then_taker_locked_profit(maker_price=maker_px, taker_price=fresh_hedge_ask, quantity=1)
        else:
            fresh_hedge_ask, fresh_depth = ONE, ZERO
            final_econ = {"locked_profit": D("-1"), "taker_fee": ZERO, "all_in_cost": D("2"), "maker_rebate": ZERO}

        if hedge_book is None or fresh_depth < ONE or final_econ["locked_profit"] < FINAL_MIN_LOCK or revalidation_ms > 100:
            flat = await _flatten(client, maker_slug, _native_yes_space(maker_px, maker_buy_yes))
            await _post_event("EXECUTION_RESULT", trade_id=trade_id, status="HEDGE_REVALIDATION_FAILED_HARD_HALT", market=f"{maker_slug}|{hedge_slug}", amount=maker_px, metadata={"flattened": flat, "fresh_hedge_ask": str(fresh_hedge_ask), "fresh_depth": str(fresh_depth), "locked_profit": str(final_econ["locked_profit"]), "revalidation_ms": revalidation_ms})
            return {"status": "HEDGE_REVALIDATION_FAILED_HARD_HALT", "flattened": flat, "locked_profit": str(final_econ["locked_profit"])}

        hedge_payload = _hedge_payload(hedge_slug, hedge_buy_yes, fresh_hedge_ask)
        hedge_result = await asyncio.to_thread(client.orders.create, hedge_payload)
        hedge_qty = _order_qty(hedge_result if isinstance(hedge_result, Mapping) else {})
        hedge_state = _order_state(hedge_result if isinstance(hedge_result, Mapping) else {})
        hedge_order_id = str((hedge_result or {}).get("id") or "") if isinstance(hedge_result, Mapping) else ""
        if hedge_qty != ONE:
            if hedge_order_id:
                try:
                    detail = await asyncio.to_thread(client.orders.retrieve, hedge_order_id)
                    hedge_qty, hedge_state = _order_qty(detail), _order_state(detail)
                except Exception:
                    pass
        if hedge_qty != ONE:
            flat = await _flatten(client, maker_slug, _native_yes_space(maker_px, maker_buy_yes))
            await _post_event("EXECUTION_RESULT", trade_id=trade_id, status="HEDGE_NOT_FILLED_HARD_HALT", market=f"{maker_slug}|{hedge_slug}", amount=maker_px, metadata={"flattened": flat, "hedge_state": hedge_state, "hedge_qty": str(hedge_qty), "hedge_order_id": hedge_order_id})
            return {"status": "HEDGE_NOT_FILLED_HARD_HALT", "flattened": flat, "hedge_state": hedge_state}

        await _post_event("FILL", trade_id=trade_id, status="HEDGE_FILLED", market=hedge_slug, amount=fresh_hedge_ask, metadata={"order_id": hedge_order_id, "quantity": "1", "maker": False, "taker_fee": str(final_econ["taker_fee"])}, side=f"BUY {ready['hedge_buy_side']}")
        await _post_event("EXECUTION_RESULT", trade_id=trade_id, status="FILLED_HEDGED_AWAITING_SETTLEMENT", market=f"{maker_slug}|{hedge_slug}", amount=final_econ["all_in_cost"], metadata={"candidate": ready, "maker_order_id": order_id, "hedge_order_id": hedge_order_id, "maker_price": str(maker_px), "hedge_price": str(fresh_hedge_ask), "taker_fee": str(final_econ["taker_fee"]), "maker_rebate": str(final_econ["maker_rebate"]), "locked_min_profit_before_settlement": str(final_econ["locked_profit"]), "revalidation_ms": revalidation_ms, "classification": "LIVE_SAME_VENUE_NESTED_ARB_FILLED_NOT_SETTLED", "rule_certified": True})
        result = {"status": "FILLED_HEDGED_AWAITING_SETTLEMENT", "trade_id": trade_id, "maker_slug": maker_slug, "maker_side": ready["maker_buy_side"], "maker_price": str(maker_px), "hedge_slug": hedge_slug, "hedge_side": ready["hedge_buy_side"], "hedge_price": str(fresh_hedge_ask), "locked_min_profit_before_settlement": str(final_econ["locked_profit"]), "revalidation_ms": revalidation_ms}
        print("SPORTS_POLY_MAKER_Q1_RESULT " + json.dumps(result, separators=(",", ":")), flush=True)
        return result
    finally:
        client.close()


def main() -> None:
    result = asyncio.run(run_once())
    print("SPORTS_POLY_MAKER_Q1_DONE " + json.dumps(result, separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    main()
