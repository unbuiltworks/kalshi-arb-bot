"""Forward maker-shadow outcomes into the existing ARB command-center ledger.

Only research events are emitted. This module never writes TRADE_REALIZED and always
uses mode=SHADOW, so simulated maker P&L cannot enter realized live accounting.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("kalshi_arb.maker_shadow_ledger")
FORWARD_EVENTS = {"SIMULATED_FILL", "FILL_HEDGE_UNAVAILABLE", "HEDGE_LATENCY_STRESS"}
D = Decimal


def resolve_ledger_url() -> str | None:
    explicit = (os.getenv("ARB_LEDGER_EVENTS_URL") or "").strip()
    if explicit:
        return explicit
    base = (os.getenv("RAILWAY_SERVICE_ARB_COMMAND_CENTER_URL") or "").strip()
    if not base:
        return None
    return base.rstrip("/") + "/api/events"


def stable_event_id(row: dict[str, Any]) -> str:
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return "maker-shadow-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _event_ts(row: dict[str, Any]) -> str:
    ms = int(row.get("receive_ts_ms") or row.get("trade_ts_ms") or 0)
    if ms <= 0:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def row_to_trade_event(row: dict[str, Any]) -> dict[str, Any] | None:
    event = str(row.get("event") or "")
    if event not in FORWARD_EVENTS:
        return None

    event_id = stable_event_id(row)
    asset = str(row.get("asset") or "").upper() or None
    ticker = str(row.get("kalshi_ticker") or "") or None
    key = str(row.get("key") or "unknown")
    opened = str(
        row.get("quote_opened_ms")
        or row.get("fill_receive_ts_ms")
        or row.get("trade_ts_ms")
        or row.get("receive_ts_ms")
        or "0"
    )
    trade_id = f"maker-shadow:{asset or 'NA'}:{ticker or 'NA'}:{key}:{opened}"

    quantity = row.get("fill_qty")
    maker_price = row.get("maker_price")
    maker_fee = float(row.get("maker_fee") or 0)
    hedge_fee = 0.0
    opportunity = None
    legs_filled = 1

    if event == "SIMULATED_FILL":
        status = "SHADOW_FILLED_HEDGEABLE"
        hedge_fee = float(row.get("hedge_fee") or 0)
        opportunity = row.get("simulated_fill_profit")
        event_type = "SHADOW_MAKER_FILL"
        legs_filled = 2
    elif event == "FILL_HEDGE_UNAVAILABLE":
        status = "SHADOW_FILLED_HEDGE_UNAVAILABLE"
        event_type = "SHADOW_MAKER_HEDGE_UNAVAILABLE"
    else:
        hedge_fee = float(row.get("hedge_fee") or 0)
        opportunity = row.get("stressed_fill_profit")
        event_type = "SHADOW_MAKER_HEDGE_STRESS"
        status = (
            "SHADOW_HEDGE_STRESS_TARGET_SURVIVED"
            if bool(row.get("survives_target"))
            else "SHADOW_HEDGE_STRESS_TARGET_FAILED"
        )
        legs_filled = 2 if bool(row.get("hedge_depth_ok")) else 1

    amount = None
    try:
        if quantity is not None and maker_price is not None:
            amount = float(quantity) * float(maker_price)
    except (TypeError, ValueError):
        amount = None

    metadata = dict(row)
    metadata.update({
        "execution_style": "MAKER_TRIGGERED_HEDGE",
        "shadow_only": True,
        "realized": False,
        "source": "gemini_kalshi_maker_shadow",
    })

    return {
        "event_id": event_id,
        "trade_id": trade_id,
        "event_type": event_type,
        "event_ts": _event_ts(row),
        "strategy": "maker_triggered_hedge_shadow",
        "mode": "SHADOW",
        "market": ticker,
        "asset": asset,
        "venues": ["kalshi", "gemini"],
        "side": row.get("direction"),
        "status": status,
        "quantity": quantity,
        "price": maker_price,
        "amount": amount,
        "fee": maker_fee + hedge_fee,
        "gross_pnl": None,
        "net_pnl": None,
        "legs_required": 2,
        "legs_filled": legs_filled,
        "opportunity_net_profit": opportunity,
        "metadata": metadata,
    }


class JsonlLedgerForwarder:
    def __init__(self, directory: Path, *, poll_s: float = 0.25) -> None:
        self.directory = directory
        self.poll_s = max(0.05, poll_s)
        self.offsets: dict[Path, int] = {}
        self.latest_quotes: dict[tuple[str, str], dict[str, Any]] = {}
        self.forwarded = 0
        self.failures = 0

    @staticmethod
    def _quote_identity(row: dict[str, Any]) -> tuple[str, str]:
        return (
            str(row.get("asset") or "").upper(),
            str(row.get("key") or ""),
        )

    def _with_quote_context(self, row: dict[str, Any]) -> dict[str, Any]:
        event = str(row.get("event") or "")
        ident = self._quote_identity(row)
        if event == "OPEN":
            self.latest_quotes[ident] = dict(row)
            return row
        if event in {"REPRICE", "CANCEL"}:
            self.latest_quotes.pop(ident, None)
            return row
        if event not in {"SIMULATED_FILL", "FILL_HEDGE_UNAVAILABLE"}:
            return row

        opened = self.latest_quotes.pop(ident, None)
        if not opened:
            return row
        enriched = dict(row)
        enriched["quote_open_best_bid"] = opened.get("best_bid")
        enriched["quote_open_best_ask"] = opened.get("best_ask")
        enriched["quote_open_economic_max"] = opened.get("economic_max")
        enriched["quote_open_queue_ahead"] = opened.get("queue_ahead")
        try:
            open_ask = D(str(opened["best_ask"]))
            economic_max = D(str(opened["economic_max"]))
            maker_price = D(str(row["maker_price"]))
            enriched["maker_price_advantage_vs_open_ask"] = str(open_ask - maker_price)
            # Conservative attribution: if the displayed taker ask was already above
            # the maximum maker price that met the target, the target was unavailable
            # by price alone before even charging the higher taker fee.
            enriched["maker_created_target_by_price_alone"] = open_ask > economic_max
        except (KeyError, TypeError, ValueError):
            pass
        return enriched

    def _new_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        self.directory.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.directory.glob("maker_*.jsonl")):
            offset = self.offsets.get(path, 0)
            try:
                size = path.stat().st_size
                if size < offset:
                    offset = 0
                with path.open("r", encoding="utf-8") as fh:
                    fh.seek(offset)
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(row, dict):
                            rows.append(self._with_quote_context(row))
                    self.offsets[path] = fh.tell()
            except FileNotFoundError:
                self.offsets.pop(path, None)
        return rows

    async def run(self) -> None:
        url = resolve_ledger_url()
        token = (os.getenv("LEDGER_INGEST_TOKEN") or "").strip()
        if not url or not token:
            logger.warning(
                "MAKER_LEDGER_DISABLED url_present=%s token_present=%s; local shadow files still collect",
                bool(url), bool(token),
            )
            while True:
                await asyncio.sleep(30)

        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                for row in self._new_rows():
                    payload = row_to_trade_event(row)
                    if payload is None:
                        continue
                    try:
                        response = await client.post(url, json=payload, headers=headers)
                        response.raise_for_status()
                        self.forwarded += 1
                    except Exception as exc:
                        self.failures += 1
                        logger.warning(
                            "MAKER_LEDGER_FORWARD_FAILED type=%s error=%s event_id=%s",
                            type(exc).__name__, str(exc)[:180], payload["event_id"],
                        )
                await asyncio.sleep(self.poll_s)
