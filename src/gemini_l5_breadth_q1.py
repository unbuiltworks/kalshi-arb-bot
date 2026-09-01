"""Bounded multi-market Gemini L5-style q1 breadth canary.

Aggressive in breadth, not per-market directional exposure:
- nearest BTC/ETH/SOL/XRP 5m markets only;
- at most one q1 YES maker bid and one q1 NO maker bid per market;
- quotes one cent behind each current top maker bid to favor completion economics;
- requires >=2c fee-adjusted complete-pair edge at entry;
- max residual is exactly one contract per market;
- no chase, rescue, sell, unwind, or automatic restart;
- unmatched inventory is held to Gemini settlement;
- exact client IDs only; no session-wide cancel fallback.

Live mutation requires dual-key arming. Importing or running while disarmed makes no
venue calls.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping

import httpx

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_kalshi_live_service import _gemini_available_usd, _gemini_terms_ok
from src.gemini_prediction_auth import private_rest_request

ONE = D("1")
ZERO = D("0")
TICK = D("0.01")
ASSETS = ("BTC", "ETH", "SOL", "XRP")
EVENT_RE = re.compile(r"^(BTC|ETH|SOL|XRP)05M(\d{10})$")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
QUOTE_BACKOFF = D("0.01")
MIN_PAIR_EDGE = D("0.02")
MIN_TTE = 105.0
MAX_MARKETS = 4
REST_SECONDS = 95.0
MIN_BALANCE = D("10")

LIVE_ENV = "GEMINI_L5_BREADTH_LIVE_Q1"
SID_ENV = "GEMINI_L5_BREADTH_SESSION_ID"
ARM_ENV = "GEMINI_L5_BREADTH_ARM"
STATE_ENV = "GEMINI_L5_BREADTH_STATE_FILE"


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_L5_BREADTH_Q1 "
        + json.dumps(
            {"stage": stage, "observed_at_utc": datetime.now(timezone.utc).isoformat(), **fields},
            separators=(",", ":"), default=str,
        ),
        flush=True,
    )


def armed() -> bool:
    sid = os.getenv(SID_ENV, "").strip()
    return (
        os.getenv(LIVE_ENV, "").strip().upper() == "YES"
        and bool(sid)
        and os.getenv(ARM_ENV, "").strip() == sid
    )


def _state(payload: Mapping[str, Any]) -> None:
    path = Path(os.getenv(STATE_ENV, "/state/gemini-l5-breadth-q1.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as handle:
        json.dump(dict(payload), handle, sort_keys=True, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def client_id(sid: str, asset: str, side: str) -> str:
    digest = hashlib.sha256(f"{sid}:{asset}:{side}".encode()).hexdigest()[:20]
    out = f"l5br-{digest}-{asset.lower()}-{side[0].lower()}"
    assert len(out) <= 36
    return out


def q1_supported(event: base.Event) -> bool:
    if event.quantity_minimum <= ZERO or event.quantity_increment <= ZERO:
        return False
    if event.quantity_minimum > ONE or event.price_increment != TICK:
        return False
    units = ONE / event.quantity_increment
    return units == units.to_integral_value()


def quote_pair(bid: D, ask: D) -> dict[str, D]:
    yes = (D(str(bid)) - QUOTE_BACKOFF).quantize(TICK)
    no = (ONE - D(str(ask)) - QUOTE_BACKOFF).quantize(TICK)
    yes_fee = gemini_predictions_maker_fee(yes, ONE) if yes > ZERO else ONE
    no_fee = gemini_predictions_maker_fee(no, ONE) if no > ZERO else ONE
    edge = ONE - yes - no - yes_fee - no_fee
    return {
        "yes": yes,
        "no": no,
        "yes_fee": yes_fee,
        "no_fee": no_fee,
        "edge": edge,
    }


def safe_pair(model: Mapping[str, D]) -> bool:
    yes, no, edge = model["yes"], model["no"], model["edge"]
    return (
        MIN_PRICE <= yes <= MAX_PRICE
        and MIN_PRICE <= no <= MAX_PRICE
        and edge >= MIN_PAIR_EDGE
    )


async def discover_5m() -> list[tuple[str, base.Event]]:
    """Nearest eligible 5m event per asset, returned in deterministic asset order."""
    found: dict[str, list[base.Event]] = {asset: [] for asset in ASSETS}
    async with httpx.AsyncClient(
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": "arb-gemini-l5-breadth-q1/1.0"},
    ) as http:
        offset = 0
        while offset < 5000:
            response = await http.get(
                f"{base.REST}/v1/prediction-markets/events",
                params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") or [] if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                ticker = str(row.get("ticker") or "").strip().upper()
                match = EVENT_RE.match(ticker)
                if not match:
                    continue
                asset = match.group(1)
                for contract in row.get("contracts") or []:
                    if not isinstance(contract, Mapping):
                        continue
                    symbol = str(contract.get("instrumentSymbol") or "").strip()
                    state = str(contract.get("marketState") or contract.get("status") or "").lower()
                    if not symbol.upper().endswith("-UP") or state not in {"open", "active"}:
                        continue
                    expiry = base._iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
                    if expiry is None or expiry <= int(time.time() * 1000):
                        continue
                    event = base.Event(
                        ticker=ticker,
                        symbol=symbol,
                        expiry_ms=expiry,
                        quantity_minimum=D(str(contract.get("quantityMinimum") or "1")),
                        quantity_increment=D(str(contract.get("quantityIncrement") or "1")),
                        price_increment=D(str(contract.get("priceIncrement") or "0.01")),
                    )
                    if event.seconds_to_expiry >= MIN_TTE and q1_supported(event):
                        found[asset].append(event)
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            total = int((pagination or {}).get("total") or len(rows))
            offset += len(rows)
            if not rows or offset >= total:
                break
    out: list[tuple[str, base.Event]] = []
    for asset in ASSETS:
        if found[asset]:
            out.append((asset, min(found[asset], key=lambda event: event.expiry_ms)))
    return out[:MAX_MARKETS]


async def fresh_top_retry(symbol: str, attempts: int = 4) -> tuple[D, D, D, D]:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await base.fresh_top(symbol)
        except RuntimeError as exc:
            last = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(0.35)
    raise RuntimeError(f"no fresh depth for {symbol}") from last


async def _find_exact_row(client: str, symbol: str) -> dict[str, Any] | None:
    """Find exact accepted order without the old 120-second terminal-history bound."""
    queries = [
        ("/v1/prediction-markets/orders/active", {"symbol": symbol, "limit": 100, "offset": 0}),
        ("/v1/prediction-markets/orders/history", {"status": "filled", "symbol": symbol, "limit": 100}),
        ("/v1/prediction-markets/orders/history", {"status": "cancelled", "symbol": symbol, "limit": 100}),
    ]
    for _ in range(8):
        for path, params in queries:
            response = await private_rest_request("POST", path, params=params, timeout=5.0)
            if response.status_code != 200:
                continue
            body = response.json()
            rows = body.get("orders", []) if isinstance(body, Mapping) else []
            for row in rows:
                if isinstance(row, Mapping) and str(row.get("clientOrderId") or "") == client:
                    return dict(row)
            await asyncio.sleep(0.03)
        await asyncio.sleep(0.08)
    return None


async def _cancel_exact(maker: base.GeminiMakerSession, spec: dict[str, Any]) -> dict[str, Any]:
    row = spec.get("row")
    if not isinstance(row, Mapping):
        # Never fall through to base._cancel_maker's session-wide cancellation.
        row = await _find_exact_row(spec["id"], spec["symbol"])
        if row is not None:
            spec["row"] = row
    if not isinstance(row, Mapping):
        return {"ok": False, "reason": "EXACT_ROW_NOT_FOUND"}
    return await base._cancel_maker(maker, row=row)


async def _authoritative_rows(specs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Three private calls total; filter globally to this campaign's exact IDs."""
    wanted = {spec["id"] for spec in specs}
    found: dict[str, dict[str, Any]] = {}
    queries = [
        ("/v1/prediction-markets/orders/active", {"limit": 1000, "offset": 0}),
        ("/v1/prediction-markets/orders/history", {"status": "filled", "limit": 1000}),
        ("/v1/prediction-markets/orders/history", {"status": "cancelled", "limit": 1000}),
    ]
    for path, params in queries:
        response = await private_rest_request("POST", path, params=params, timeout=6.0)
        if response.status_code != 200:
            continue
        body = response.json()
        rows = body.get("orders", []) if isinstance(body, Mapping) else []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            client = str(row.get("clientOrderId") or "")
            if client not in wanted:
                continue
            candidate = dict(row)
            prior = found.get(client)
            if prior is None or base._filled(candidate) >= base._filled(prior):
                found[client] = candidate
        await asyncio.sleep(0.08)
    return found


def summarize(specs: list[dict[str, Any]], rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    per_market: dict[str, dict[str, Any]] = {}
    total_turnover = ZERO
    total_fees = ZERO
    total_matched = ZERO
    total_residual = ZERO
    for spec in specs:
        row = rows.get(spec["id"])
        qty = base._filled(row)
        spec["row"] = dict(row) if isinstance(row, Mapping) else spec.get("row")
        market = per_market.setdefault(
            spec["asset"],
            {
                "asset": spec["asset"],
                "event": spec["event"],
                "symbol": spec["symbol"],
                "yes_qty": ZERO,
                "no_qty": ZERO,
                "yes_cost": ZERO,
                "no_cost": ZERO,
                "fees": ZERO,
                "fills": [],
            },
        )
        if qty <= ZERO:
            continue
        fee = gemini_predictions_maker_fee(spec["price"], qty)
        cost = spec["price"] * qty
        key_qty = "yes_qty" if spec["side"] == "YES" else "no_qty"
        key_cost = "yes_cost" if spec["side"] == "YES" else "no_cost"
        market[key_qty] += qty
        market[key_cost] += cost
        market["fees"] += fee
        market["fills"].append(
            {"side": spec["side"], "price": str(spec["price"]), "qty": str(qty), "fee": str(fee)}
        )
    output = []
    for asset in ASSETS:
        market = per_market.get(asset)
        if market is None:
            continue
        yes_qty = market["yes_qty"]
        no_qty = market["no_qty"]
        matched = min(yes_qty, no_qty)
        residual = abs(yes_qty - no_qty)
        turnover = market["yes_cost"] + market["no_cost"]
        total_turnover += turnover
        total_fees += market["fees"]
        total_matched += matched
        total_residual += residual
        output.append(
            {
                "asset": asset,
                "event": market["event"],
                "symbol": market["symbol"],
                "yes_qty": str(yes_qty),
                "no_qty": str(no_qty),
                "matched_qty": str(matched),
                "residual_qty": str(residual),
                "turnover": str(turnover),
                "maker_fees": str(market["fees"]),
                "fills": market["fills"],
            }
        )
    return {
        "markets": output,
        "maker_turnover": str(total_turnover),
        "maker_fees": str(total_fees),
        "matched_qty": str(total_matched),
        "residual_qty": str(total_residual),
    }


async def run() -> None:
    # Critical invariant: absolutely no venue calls before dual-key arming.
    if not armed():
        emit(
            "DISARMED",
            max_markets=MAX_MARKETS,
            assets=list(ASSETS),
            q_per_outcome=1,
            max_residual_per_market=1,
            order_entry=False,
            realized_pnl=None,
        )
        return

    if not await _gemini_terms_ok():
        raise RuntimeError("Gemini prediction terms not accepted")
    if await _gemini_available_usd() < MIN_BALANCE:
        raise RuntimeError("insufficient bounded breadth-canary balance")

    discovered = await discover_5m()
    if not discovered:
        emit("STOP", reason="NO_ELIGIBLE_5M_MARKETS", order_entry=False, realized_pnl=None)
        return

    candidates: list[dict[str, Any]] = []
    for asset, event in discovered:
        bid, bid_size, ask, ask_size = await fresh_top_retry(event.symbol)
        model = quote_pair(bid, ask)
        if not safe_pair(model):
            emit(
                "MARKET_SKIPPED",
                asset=asset,
                symbol=event.symbol,
                yes=str(model["yes"]),
                no=str(model["no"]),
                modeled_pair_edge=str(model["edge"]),
                reason="PAIR_EDGE_OR_PRICE_BAND",
                order_entry=False,
            )
            continue
        candidates.append(
            {
                "asset": asset,
                "event_obj": event,
                "event": event.ticker,
                "symbol": event.symbol,
                "yes": model["yes"],
                "no": model["no"],
                "edge": model["edge"],
                "yes_queue_hint": bid_size,
                "no_queue_hint": ask_size,
            }
        )

    if not candidates:
        emit("STOP", reason="NO_SAFE_BREADTH_CANDIDATES", order_entry=False, realized_pnl=None)
        return

    sid = os.getenv(SID_ENV, "").strip()
    specs: list[dict[str, Any]] = []
    for candidate in candidates[:MAX_MARKETS]:
        for side, price_key in (("YES", "yes"), ("NO", "no")):
            specs.append(
                {
                    "asset": candidate["asset"],
                    "event": candidate["event"],
                    "symbol": candidate["symbol"],
                    "expiry_ms": candidate["event_obj"].expiry_ms,
                    "side": side,
                    "price": candidate[price_key],
                    "id": client_id(sid, candidate["asset"], side),
                    "row": None,
                }
            )

    state = {
        "terminal": False,
        "sid": sid,
        "markets": [
            {
                "asset": candidate["asset"],
                "event": candidate["event"],
                "symbol": candidate["symbol"],
                "yes": str(candidate["yes"]),
                "no": str(candidate["no"]),
                "edge": str(candidate["edge"]),
            }
            for candidate in candidates[:MAX_MARKETS]
        ],
        "orders": [
            {k: (str(v) if k == "price" else v) for k, v in spec.items() if k not in {"row", "expiry_ms"}}
            for spec in specs
        ],
    }
    _state(state)
    emit(
        "START",
        n_markets=len(candidates[:MAX_MARKETS]),
        max_residual_per_market=1,
        max_total_residual_contracts=len(candidates[:MAX_MARKETS]),
        markets=state["markets"],
        order_entry=True,
        realized_pnl=None,
    )

    maker = base.GeminiMakerSession()
    await maker.connect()
    posted: list[dict[str, Any]] = []
    try:
        for spec in specs:
            payload = {
                "symbol": spec["symbol"],
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "MOC",
                "price": str(spec["price"]),
                "quantity": "1",
                "eventOutcome": spec["side"],
                "clientOrderId": spec["id"],
            }
            ack = await maker.request("order.place", payload, timeout=4.0)
            if int(ack.get("status", 0)) != 200:
                for prior in posted:
                    await _cancel_exact(maker, prior)
                await asyncio.sleep(0.25)
                rows = await _authoritative_rows(specs)
                summary = summarize(specs, rows)
                state.update({"terminal": True, "status": "POST_REJECT", **summary})
                _state(state)
                emit("COMPLETE", status="POST_REJECT", **summary, order_entry=False, realized_pnl=None)
                return
            row = await _find_exact_row(spec["id"], spec["symbol"])
            if row is None:
                for prior in posted:
                    await _cancel_exact(maker, prior)
                raise RuntimeError(f"accepted breadth order not discoverable: {spec['id']}")
            spec["row"] = row
            posted.append(spec)
            await asyncio.sleep(0.04)

        emit("BREADTH_POSTED", n_orders=len(posted), n_markets=len(candidates[:MAX_MARKETS]), order_entry=True)

        # Observe fills only. There is no same-side sibling to cancel; the opposite
        # q1 maker remains resting to create a natural complete set if flow reverses.
        ids = {spec["id"]: spec for spec in specs}
        first_fills: set[str] = set()
        earliest_expiry = min(spec["expiry_ms"] for spec in specs)
        expiry_cutoff = max(0.0, (earliest_expiry - int(time.time() * 1000)) / 1000.0 - 25.0)
        observe_s = min(REST_SECONDS, expiry_cutoff)
        deadline = time.monotonic() + max(1.0, observe_s)
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(maker.events.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if not isinstance(event, Mapping):
                continue
            client = str(event.get("c") or "")
            qty = D(str(event.get("Z") or "0"))
            status = str(event.get("X") or "").upper()
            if client not in ids or qty <= ZERO or status not in {"FILLED", "PARTIALLY_FILLED"}:
                continue
            if client not in first_fills:
                first_fills.add(client)
                spec = ids[client]
                emit(
                    "FILL",
                    asset=spec["asset"],
                    side=spec["side"],
                    client_id=client,
                    reported_qty=str(qty),
                    action="KEEP_ONLY_COMPLEMENT_MAKER_RESTING",
                    order_entry=True,
                )

        for spec in posted:
            await _cancel_exact(maker, spec)
        await asyncio.sleep(0.35)
        rows = await _authoritative_rows(specs)
        summary = summarize(specs, rows)

        # Hard safety assertion: with exactly one q1 order per side, each market's
        # directional residual can never exceed one contract.
        if any(D(market["residual_qty"]) > ONE for market in summary["markets"]):
            raise RuntimeError("breadth residual exceeded q1 per-market hard cap")

        any_fill = any(D(market["yes_qty"]) > ZERO or D(market["no_qty"]) > ZERO for market in summary["markets"])
        all_matched = bool(summary["markets"]) and all(
            D(market["yes_qty"]) == D(market["no_qty"]) == ONE for market in summary["markets"]
        )
        status = "ALL_MATCHED" if all_matched else "MIXED_RESIDUAL" if any_fill else "NO_FILL_TIMEOUT"
        state.update({"terminal": True, "status": status, **summary})
        _state(state)
        emit(
            "COMPLETE",
            status=status,
            **summary,
            settlement_required=any_fill,
            order_entry=False,
            realized_pnl=None,
        )
    finally:
        await maker.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
