"""Best-effort reporting adapter for live sports Gemini/Kalshi probes.

This module never authorizes or submits an order itself. It wraps an already-
authorized ``submit_dual_probe`` call and mirrors the actual execution result into
ARB Command Center plus Pushover. Reporting failures are logged and swallowed so
they cannot alter the two-leg execution/flatten safety path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger("kalshi_arb.sports_trade_reporting")
D = Decimal

LEDGER_URL = os.getenv(
    "ARB_LEDGER_URL",
    "https://arb-command-center-production.up.railway.app/api/events",
).strip()
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
PUSHOVER_USER = os.getenv("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_TOKEN = os.getenv("PUSHOVER_APP_TOKEN", "").strip()

_CONTEXT: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def _decimal(value: Any, default: str = "0") -> D:
    try:
        return D(str(value))
    except Exception:
        return D(default)


class SubmissionContextCapture(logging.Handler):
    """Capture the canary's already-emitted SUBMITTING context without changing it."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            marker = "_SUBMITTING "
            if marker not in message:
                return
            payload = json.loads(message.split(marker, 1)[1])
            run_id = str(payload.get("run_id") or "")
            if run_id:
                _CONTEXT[run_id] = payload
        except Exception:
            logger.exception("SPORTS_REPORT_CONTEXT_CAPTURE_ERROR")


async def _post_event(event: dict[str, Any]) -> None:
    if not LEDGER_TOKEN:
        logger.warning("SPORTS_LEDGER_DISABLED missing=LEDGER_INGEST_TOKEN")
        return
    try:
        async with httpx.AsyncClient(timeout=2.5) as http:
            response = await http.post(
                LEDGER_URL,
                headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
                json=event,
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning("SPORTS_LEDGER_POST_FAILED event_type=%s error=%s", event.get("event_type"), type(exc).__name__)


async def _push(title: str, message: str) -> None:
    if not PUSHOVER_USER or not PUSHOVER_TOKEN:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as http:
            response = await http.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": PUSHOVER_TOKEN,
                    "user": PUSHOVER_USER,
                    "title": title[:250],
                    "message": message[:1024],
                    "priority": "0",
                },
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning("SPORTS_PUSHOVER_FAILED error=%s", type(exc).__name__)


def _economic_fill(venue: str, result: dict[str, Any], payload: dict[str, Any]) -> tuple[D, D, D]:
    qty = _decimal(result.get("filled_quantity"))
    avg = _decimal(result.get("average_fill_price"), str(payload.get("price") or "0"))
    if venue == "kalshi" and str(payload.get("side") or "") == "ask":
        # Kalshi V2 ask buys NO while the reported price is in YES space.
        contract_price = D("1") - avg
    else:
        contract_price = avg
    return qty, contract_price, qty * contract_price


def install(
    canary_module: Any,
    *,
    strategy: str,
    run_id_env: str,
    default_family: str | None = None,
) -> None:
    """Wrap the canary module's imported submit function in-place."""
    canary_module.logger.addHandler(SubmissionContextCapture())
    original: Callable[..., Awaitable[dict[str, Any]]] = canary_module.submit_dual_probe

    async def reported_submit(
        gemini: Any,
        kalshi: Any,
        gemini_payload: dict[str, Any],
        kalshi_payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        run_id = os.getenv(run_id_env, "unknown-run").strip() or "unknown-run"
        ctx = dict(_CONTEXT.get(run_id) or {})
        family = str(ctx.get("family") or default_family or "mlb_player_prop")
        player = str(ctx.get("player") or "Unknown player")
        settlement_key = str(ctx.get("settlement_key") or f"{gemini_payload.get('symbol')}|{kalshi_payload.get('ticker')}")
        trade_id = _safe_id(f"sports-gk:{run_id}")
        modeled = _decimal(kwargs.get("modeled_locked_profit"))
        base_meta = {
            **ctx,
            "classification": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB",
            "family": family,
            "player": player,
            "gemini_payload": gemini_payload,
            "kalshi_payload": kalshi_payload,
        }

        await _post_event({
            "event_id": _safe_id(f"{trade_id}:submitted"),
            "trade_id": trade_id,
            "event_type": "ORDER_SUBMITTED",
            "event_ts": _now(),
            "strategy": strategy,
            "mode": "LIVE_PROBE",
            "market": settlement_key,
            "asset": player,
            "venues": ["gemini", "kalshi"],
            "side": str(ctx.get("direction") or "COMPLEMENTARY"),
            "status": "SUBMITTED",
            "quantity": "1",
            "legs_required": 2,
            "legs_filled": 0,
            "opportunity_net_profit": str(modeled),
            "metadata": base_meta,
        })

        result = await original(
            gemini,
            kalshi,
            gemini_payload,
            kalshi_payload,
            **kwargs,
        )

        filled = 0
        fees_total = D("0")
        for venue, payload in (("gemini", gemini_payload), ("kalshi", kalshi_payload)):
            row = dict(result.get(venue) or {})
            qty, price, amount = _economic_fill(venue, row, payload)
            fee = _decimal(row.get("fee_paid"))
            fees_total += fee
            if qty <= 0:
                continue
            filled += 1
            side = (
                f"BUY {str(gemini_payload.get('eventOutcome') or '').upper()}"
                if venue == "gemini"
                else ("BUY YES" if str(kalshi_payload.get("side")) == "bid" else "BUY NO")
            )
            await _post_event({
                "event_id": _safe_id(f"{trade_id}:fill:{venue}"),
                "trade_id": trade_id,
                "event_type": "FILL",
                "event_ts": _now(),
                "strategy": strategy,
                "mode": "LIVE_PROBE",
                "market": settlement_key,
                "asset": player,
                "venues": [venue],
                "side": side,
                "status": str(row.get("status") or "FILLED"),
                "quantity": str(qty),
                "price": str(price),
                "amount": str(amount),
                "fee": str(fee),
                "legs_required": 2,
                "legs_filled": filled,
                "metadata": {
                    **base_meta,
                    "venue": venue,
                    "client_order_id": row.get("client_order_id"),
                    "raw_execution": row,
                },
            })

        outcome = str(result.get("outcome") or "UNKNOWN")
        status = "AWAITING_SETTLEMENT" if outcome == "BOTH_FILLED" else outcome
        execution_meta = {
            **base_meta,
            "execution": result,
            "fees_observed": str(fees_total),
        }
        await _post_event({
            "event_id": _safe_id(f"{trade_id}:execution-result"),
            "trade_id": trade_id,
            "event_type": "EXECUTION_RESULT",
            "event_ts": _now(),
            "strategy": strategy,
            "mode": "LIVE_PROBE",
            "market": settlement_key,
            "asset": player,
            "venues": ["gemini", "kalshi"],
            "side": str(ctx.get("direction") or "COMPLEMENTARY"),
            "status": status,
            "quantity": "1",
            "legs_required": 2,
            "legs_filled": filled,
            "opportunity_net_profit": str(modeled),
            "metadata": execution_meta,
        })

        if outcome == "BOTH_FILLED":
            await _push(
                f"MLB LIVE FILLED — {player}",
                "\n".join((
                    f"{family.replace('baseball_player_', '').replace('_', ' ').title()}",
                    "Gemini + Kalshi: BOTH FILLED",
                    f"Modeled edge: +${modeled}",
                    f"Capital: see ARB Command Center",
                    "Status: awaiting both venue settlements",
                    "LIVE RULE-RISK PROBE — not realized P&L yet",
                )),
            )
        else:
            await _push(
                f"MLB LIVE EXECUTION — {outcome}",
                f"{player}\n{family}\nLegs filled: {filled}/2\nHard halt: {bool(result.get('hard_halt_required'))}\nCheck ARB Command Center / Railway.",
            )
        return result

    canary_module.submit_dual_probe = reported_submit
