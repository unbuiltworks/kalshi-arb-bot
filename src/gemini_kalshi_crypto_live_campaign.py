"""Sequential bounded live campaign for Gemini/Kalshi 15-minute crypto.

The supervisor never submits orders itself.  It launches the proven one-attempt v2
canary as a child, waits for its persistent execution result, records actual fills,
waits for both official venue settlements, writes one authoritative TRADE_REALIZED
event to ARB Command Center, and only then permits the next child to arm.

Fail-closed campaign rules:
- BTC/ETH/SOL/XRP compete for each child attempt;
- child retains q=1, >=3c after-fee edge, >=10bp boundary, 50ms survival/skew gates;
- at most one unresolved trade at a time;
- max 5 attempts and $5 cumulative campaign capital;
- any one-leg/ambiguous execution, missing result, dashboard ingest failure,
  settlement timeout, or Gemini/Kalshi outcome disagreement halts the campaign;
- dashboard realization is idempotent via deterministic event IDs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any

import httpx

from src import gemini_kalshi_sol_live_canary_v2 as v2
from src.gemini_kalshi_fast_overlap import GEMINI_REST, KALSHI_REST

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_live_campaign")

CAMPAIGN_ID_ENV = "CRYPTO_CANARY_CAMPAIGN_ID"
MAX_TRADES_ENV = "CRYPTO_CANARY_CAMPAIGN_MAX_TRADES"
HARD_BUDGET_ENV = "CRYPTO_CANARY_CAMPAIGN_HARD_BUDGET_USD"
LEDGER_URL_ENV = "ARB_LEDGER_EVENTS_URL"
LEDGER_TOKEN_ENV = "LEDGER_INGEST_TOKEN"
SETTLEMENT_TIMEOUT_S = 1800
SETTLEMENT_POLL_S = 10
CHILD_RESULT_POLL_S = 0.25


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _utcnow().isoformat()


def _clean_campaign_id(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(raw).strip()).strip("-")
    if not value or len(value) > 24:
        raise ValueError("campaign id must be 1-24 safe characters")
    return value


def _run_id(campaign_id: str, number: int) -> str:
    return v2._clean_run_id(f"{campaign_id}-{int(number):02d}")


def _state_path(campaign_id: str) -> Path:
    return v2._state_dir() / f"{campaign_id}.campaign.json"


def _write_state(path: Path, state: dict[str, Any]) -> None:
    v2._write_json_atomic(path, state)


def _load_state(path: Path, campaign_id: str, max_trades: int, hard_budget: Decimal) -> dict[str, Any]:
    if path.is_file():
        with path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
        if str(state.get("campaign_id")) != campaign_id:
            raise RuntimeError("campaign state id mismatch")
        return state
    return {
        "campaign_id": campaign_id,
        "created_at_utc": _iso(),
        "updated_at_utc": _iso(),
        "status": "RUNNING",
        "max_trades": int(max_trades),
        "hard_budget_usd": str(hard_budget),
        "attempts": [],
        "cumulative_capital_usd": "0",
        "cumulative_realized_net_pnl_usd": "0",
    }


def _event_ticker_from_symbol(symbol: str) -> str:
    value = str(symbol).upper().strip()
    if value.startswith("GEMI-"):
        value = value[5:]
    if "-" not in value:
        raise ValueError(f"cannot derive Gemini event ticker from {symbol!r}")
    return value.rsplit("-", 1)[0]


def _asset_from_event_ticker(ticker: str) -> str:
    match = re.match(r"^(BTC|ETH|SOL|XRP)", str(ticker).upper())
    if not match:
        raise ValueError(f"unsupported event ticker {ticker!r}")
    return match.group(1)


def _purchased_kalshi_side(payload: dict[str, Any]) -> str:
    side = str(payload.get("side") or "").lower()
    if side == "bid":
        return "yes"
    if side == "ask":
        return "no"
    raise ValueError(f"unsupported Kalshi order side {side!r}")


def _costs(record: dict[str, Any]) -> dict[str, Decimal]:
    result = record["result"]
    gemini = result["gemini"]
    kalshi = result["kalshi"]
    kalshi_payload = record["kalshi_payload"]

    g_qty = D(str(gemini["filled_quantity"]))
    k_qty = D(str(kalshi["filled_quantity"]))
    if g_qty != D("1") or k_qty != D("1"):
        raise RuntimeError(f"unexpected live quantities Gemini={g_qty} Kalshi={k_qty}")

    g_price = D(str(gemini["average_fill_price"]))
    g_fee = D(str(gemini.get("fee_paid") or "0"))
    k_yes_price = D(str(kalshi["average_fill_price"]))
    k_fee = D(str(kalshi.get("fee_paid") or "0"))
    k_side = _purchased_kalshi_side(kalshi_payload)
    k_cost_price = k_yes_price if k_side == "yes" else D("1") - k_yes_price

    g_raw = g_price * g_qty
    k_raw = k_cost_price * k_qty
    return {
        "gemini_price": g_price,
        "gemini_fee": g_fee,
        "gemini_amount": g_raw + g_fee,
        "kalshi_yes_price": k_yes_price,
        "kalshi_cost_price": k_cost_price,
        "kalshi_fee": k_fee,
        "kalshi_amount": k_raw + k_fee,
        "raw_cost": g_raw + k_raw,
        "fees": g_fee + k_fee,
        "capital": g_raw + k_raw + g_fee + k_fee,
    }


async def _post_event(http: httpx.AsyncClient, event: dict[str, Any]) -> None:
    url = os.getenv(LEDGER_URL_ENV, "").strip()
    token = os.getenv(LEDGER_TOKEN_ENV, "").strip()
    if not url or not token:
        raise RuntimeError("ARB dashboard ingest URL/token not configured")
    last_error = ""
    for attempt in range(6):
        try:
            response = await http.post(
                url,
                json=event,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if 200 <= response.status_code < 300:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:160]}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        await asyncio.sleep(min(10.0, 0.75 * (2 ** attempt)))
    raise RuntimeError(f"dashboard ingest failed: {last_error}")


async def _emit_execution_events(http: httpx.AsyncClient, run_id: str, record: dict[str, Any]) -> dict[str, Decimal]:
    result = record["result"]
    gp = record.get("gemini_payload") or {}
    kp = record.get("kalshi_payload") or {}
    symbol = str(gp.get("symbol") or "")
    event_ticker = _event_ticker_from_symbol(symbol)
    asset = _asset_from_event_ticker(event_ticker)
    kalshi_ticker = str(kp.get("ticker") or "")
    trade_id = f"gk15m-{run_id}"
    completed = str(record.get("completed_at_utc") or _iso())
    legs_filled = sum(
        int(str(result.get(venue, {}).get("status") or "").upper() == "FILLED")
        for venue in ("gemini", "kalshi")
    )
    common = {
        "trade_id": trade_id,
        "event_ts": completed,
        "strategy": "gemini_kalshi_15m_crypto",
        "mode": "LIVE_PROBE",
        "market": f"{event_ticker}|{kalshi_ticker}",
        "asset": asset,
        "venues": ["Gemini", "Kalshi"],
        "quantity": "1",
        "legs_required": 2,
        "legs_filled": legs_filled,
    }
    await _post_event(http, {
        **common,
        "event_id": f"{trade_id}-submitted",
        "event_type": "ORDER_SUBMITTED",
        "status": str(result.get("outcome") or "SUBMITTED"),
        "opportunity_net_profit": str(record.get("modeled_locked_profit") or "" ) or None,
        "metadata": {"run_id": run_id, "gemini_symbol": symbol, "kalshi_ticker": kalshi_ticker},
    })

    if str(result.get("outcome")) != "BOTH_FILLED":
        # Record any venue fill we actually know about, then let the campaign halt.
        for venue in ("gemini", "kalshi"):
            leg = result.get(venue) or {}
            if str(leg.get("status") or "").upper() != "FILLED":
                continue
            await _post_event(http, {
                **common,
                "event_id": f"{trade_id}-fill-{venue}",
                "event_type": "FILL",
                "status": "FILLED",
                "price": str(leg.get("average_fill_price")),
                "amount": None,
                "fee": str(leg.get("fee_paid") or "0"),
                "side": venue,
                "metadata": {"client_order_id": leg.get("client_order_id")},
            })
        raise RuntimeError(f"execution did not finish BOTH_FILLED: {result.get('outcome')}")

    costs = _costs(record)
    g = result["gemini"]
    k = result["kalshi"]
    gemini_side = str(gp.get("eventOutcome") or "").lower()
    kalshi_side = _purchased_kalshi_side(kp)
    await _post_event(http, {
        **common,
        "event_id": f"{trade_id}-fill-gemini",
        "event_type": "FILL",
        "status": "FILLED",
        "price": str(costs["gemini_price"]),
        "amount": str(costs["gemini_amount"]),
        "fee": str(costs["gemini_fee"]),
        "side": f"Gemini {gemini_side.upper()}",
        "metadata": {"client_order_id": g.get("client_order_id"), "instrument_symbol": symbol},
    })
    await _post_event(http, {
        **common,
        "event_id": f"{trade_id}-fill-kalshi",
        "event_type": "FILL",
        "status": "FILLED",
        "price": str(costs["kalshi_cost_price"]),
        "amount": str(costs["kalshi_amount"]),
        "fee": str(costs["kalshi_fee"]),
        "side": f"Kalshi {kalshi_side.upper()}",
        "metadata": {
            "client_order_id": k.get("client_order_id"),
            "ticker": kalshi_ticker,
            "yes_space_average_fill_price": str(costs["kalshi_yes_price"]),
        },
    })
    return costs


async def _official_settlement(
    http: httpx.AsyncClient,
    *,
    gemini_symbol: str,
    kalshi_ticker: str,
) -> dict[str, Any]:
    event_ticker = _event_ticker_from_symbol(gemini_symbol)
    deadline = _utcnow() + timedelta(seconds=SETTLEMENT_TIMEOUT_S)
    while _utcnow() < deadline:
        gemini_outcome = None
        kalshi_outcome = None
        gemini_settlement_value = None

        try:
            response = await http.get(f"{GEMINI_REST}/events/{event_ticker}", timeout=10.0)
            if response.status_code == 200:
                event = response.json()
                contract = next(
                    (
                        row for row in event.get("contracts", [])
                        if isinstance(row, dict)
                        and str(row.get("instrumentSymbol") or "").upper() == gemini_symbol.upper()
                    ),
                    None,
                )
                if contract is not None:
                    resolution = str(contract.get("resolutionSide") or "").lower()
                    if resolution in {"yes", "no"}:
                        gemini_outcome = resolution
                    gemini_settlement_value = contract.get("settlementValue")
                    if gemini_settlement_value is None:
                        settlement = event.get("settlement") or {}
                        if isinstance(settlement, dict):
                            gemini_settlement_value = settlement.get("value")
        except Exception:
            logger.exception("CRYPTO_LIVE_CAMPAIGN_GEMINI_SETTLEMENT_POLL_ERROR event=%s", event_ticker)

        try:
            response = await http.get(f"{KALSHI_REST}/markets/{kalshi_ticker}", timeout=10.0)
            if response.status_code == 200:
                payload = response.json()
                market = payload.get("market", payload)
                result = str(market.get("result") or "").lower()
                status = str(market.get("status") or "").lower()
                if result in {"yes", "no"} and status in {"settled", "closed", "finalized"}:
                    kalshi_outcome = result
                elif result in {"yes", "no"}:
                    # Kalshi result is authoritative once populated even if the API's
                    # status label lags settlement bookkeeping by a short interval.
                    kalshi_outcome = result
        except Exception:
            logger.exception("CRYPTO_LIVE_CAMPAIGN_KALSHI_SETTLEMENT_POLL_ERROR ticker=%s", kalshi_ticker)

        if gemini_outcome and kalshi_outcome:
            return {
                "event_ticker": event_ticker,
                "gemini_symbol": gemini_symbol,
                "kalshi_ticker": kalshi_ticker,
                "gemini_outcome": gemini_outcome,
                "kalshi_outcome": kalshi_outcome,
                "gemini_settlement_value": gemini_settlement_value,
                "venue_outcomes_agree": gemini_outcome == kalshi_outcome,
                "observed_at_utc": _iso(),
            }
        await asyncio.sleep(SETTLEMENT_POLL_S)
    raise TimeoutError(f"settlement timeout for {event_ticker} / {kalshi_ticker}")


async def _emit_realized_event(
    http: httpx.AsyncClient,
    *,
    run_id: str,
    record: dict[str, Any],
    costs: dict[str, Decimal],
    settlement: dict[str, Any],
) -> Decimal:
    result = record["result"]
    gp = record["gemini_payload"]
    kp = record["kalshi_payload"]
    g_side = str(gp.get("eventOutcome") or "").lower()
    k_side = _purchased_kalshi_side(kp)
    g_out = str(settlement["gemini_outcome"]).lower()
    k_out = str(settlement["kalshi_outcome"]).lower()
    wins = int(g_side == g_out) + int(k_side == k_out)
    payout = D(wins)
    gross_pnl = payout - costs["raw_cost"]
    net_pnl = payout - costs["capital"]
    trade_id = f"gk15m-{run_id}"
    asset = _asset_from_event_ticker(settlement["event_ticker"])
    status = "REALIZED" if settlement["venue_outcomes_agree"] else "REALIZED_INDEX_MISMATCH"
    await _post_event(http, {
        "event_id": f"{trade_id}-realized",
        "trade_id": trade_id,
        "event_type": "TRADE_REALIZED",
        "event_ts": settlement["observed_at_utc"],
        "strategy": "gemini_kalshi_15m_crypto",
        "mode": "LIVE_PROBE",
        "market": f"{settlement['event_ticker']}|{settlement['kalshi_ticker']}",
        "asset": asset,
        "venues": ["Gemini", "Kalshi"],
        "side": f"Gemini {g_side.upper()} / Kalshi {k_side.upper()}",
        "status": status,
        "quantity": "1",
        "fee": "0",
        "gross_pnl": str(gross_pnl),
        "net_pnl": str(net_pnl),
        "legs_required": 2,
        "legs_filled": 2,
        "metadata": {
            "run_id": run_id,
            "execution_outcome": result.get("outcome"),
            "settlement_payout": str(payout),
            "actual_capital": str(costs["capital"]),
            "gemini_outcome": g_out,
            "kalshi_outcome": k_out,
            "venue_outcomes_agree": settlement["venue_outcomes_agree"],
            "gemini_settlement_value": settlement.get("gemini_settlement_value"),
            "settlement_source": "EXACT_PUBLIC_VENUE_ENDPOINTS",
        },
    })
    return net_pnl


async def _stop_child(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _obtain_execution(run_id: str, arm_deadline: datetime) -> dict[str, Any]:
    arm, claim, result_path = v2._paths(run_id)
    if result_path.is_file():
        with result_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    if claim.is_file():
        raise RuntimeError(f"attempt claim exists without result for {run_id}")

    child_env = os.environ.copy()
    child_env[v2.RUN_ID_ENV] = run_id
    child_env[v2.ARM_ENV] = "YES"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.gemini_kalshi_crypto_campaign_child",
        env=child_env,
    )
    wait_task = asyncio.create_task(process.wait())
    try:
        while _utcnow() <= arm_deadline:
            if result_path.is_file():
                with result_path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
            if wait_task.done():
                code = wait_task.result()
                raise RuntimeError(f"canary child exited {code} before persistent result")
            await asyncio.sleep(CHILD_RESULT_POLL_S)
        raise TimeoutError(f"campaign arm deadline reached before execution for {run_id}")
    finally:
        await _stop_child(process)
        if not wait_task.done():
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        # An unclaimed arm is no longer valid after this child lifecycle.
        if arm.is_file() and not claim.is_file():
            try:
                arm.unlink()
            except FileNotFoundError:
                pass


def _disarm_bootstrap_run(campaign_id: str) -> None:
    bootstrap = os.getenv(v2.RUN_ID_ENV, "").strip()
    if not bootstrap:
        return
    bootstrap = v2._clean_run_id(bootstrap)
    if bootstrap.startswith(campaign_id):
        return
    arm, claim, result = v2._paths(bootstrap)
    if claim.is_file() or result.is_file():
        raise RuntimeError(
            f"pre-campaign run {bootstrap} has an attempt/result; refusing to stack another live trade"
        )
    if arm.is_file():
        arm.unlink()
        logger.warning("CRYPTO_LIVE_CAMPAIGN_DISARMED_BOOTSTRAP run_id=%s", bootstrap)


async def run() -> None:
    campaign_id = _clean_campaign_id(os.getenv(CAMPAIGN_ID_ENV, ""))
    max_trades = int(os.getenv(MAX_TRADES_ENV, "5"))
    hard_budget = D(os.getenv(HARD_BUDGET_ENV, "5"))
    if not 1 <= max_trades <= 5:
        raise ValueError("campaign max trades must be 1..5")
    if hard_budget <= 0 or hard_budget > D("5"):
        raise ValueError("campaign hard budget must be >0 and <=5")
    if os.getenv(v2.ARM_ENV, "NO").strip().upper() != "YES":
        raise PermissionError("campaign requires explicit SOL_CANARY_V2_ARM=YES")
    if os.getenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO").strip().upper() != "YES":
        raise PermissionError("campaign requires ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES")

    deadline_raw = os.getenv("SOL_CANARY_V2_ARM_NOT_AFTER_UTC", "").strip()
    if not deadline_raw:
        raise RuntimeError("SOL_CANARY_V2_ARM_NOT_AFTER_UTC is required")
    arm_deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    if _utcnow() > arm_deadline:
        raise RuntimeError("campaign arm deadline already expired")

    _disarm_bootstrap_run(campaign_id)
    path = _state_path(campaign_id)
    state = _load_state(path, campaign_id, max_trades, hard_budget)
    logger.warning(
        "CRYPTO_LIVE_CAMPAIGN_ENABLED campaign_id=%s max_trades=%s hard_budget=%s "
        "quantity=1 min_profit=0.03 min_double_boundary_bps=10 execution_delay_ms=50 "
        "one_unresolved_trade=true dashboard_realization_required=true",
        campaign_id, max_trades, hard_budget,
    )

    async with httpx.AsyncClient(timeout=20.0) as http:
        while len(state["attempts"]) < max_trades and _utcnow() <= arm_deadline:
            cumulative_capital = D(str(state.get("cumulative_capital_usd") or "0"))
            if cumulative_capital + D("1") > hard_budget:
                state["status"] = "COMPLETE_BUDGET_RESERVE"
                state["updated_at_utc"] = _iso()
                _write_state(path, state)
                break

            number = len(state["attempts"]) + 1
            run_id = _run_id(campaign_id, number)
            state["status"] = "WAITING_EXECUTION"
            state["active_run_id"] = run_id
            state["updated_at_utc"] = _iso()
            _write_state(path, state)
            logger.warning("CRYPTO_LIVE_CAMPAIGN_ARMING_CHILD run_id=%s attempt=%s", run_id, number)

            try:
                record = await _obtain_execution(run_id, arm_deadline)
                result = record.get("result") or {}
                if str(result.get("outcome") or "") != "BOTH_FILLED" or bool(result.get("hard_halt_required")):
                    await _emit_execution_events(http, run_id, record)
                    raise RuntimeError(f"live execution unsafe outcome={result.get('outcome')}")

                costs = await _emit_execution_events(http, run_id, record)
                state["status"] = "WAITING_SETTLEMENT"
                state["updated_at_utc"] = _iso()
                state["pending_capital_usd"] = str(costs["capital"])
                _write_state(path, state)

                gp = record.get("gemini_payload") or {}
                kp = record.get("kalshi_payload") or {}
                settlement = await _official_settlement(
                    http,
                    gemini_symbol=str(gp.get("symbol") or ""),
                    kalshi_ticker=str(kp.get("ticker") or ""),
                )
                net_pnl = await _emit_realized_event(
                    http,
                    run_id=run_id,
                    record=record,
                    costs=costs,
                    settlement=settlement,
                )

                state["attempts"].append({
                    "attempt": number,
                    "run_id": run_id,
                    "completed_at_utc": str(record.get("completed_at_utc") or _iso()),
                    "execution_outcome": result.get("outcome"),
                    "capital_usd": str(costs["capital"]),
                    "realized_net_pnl_usd": str(net_pnl),
                    "settlement": settlement,
                })
                state["cumulative_capital_usd"] = str(cumulative_capital + costs["capital"])
                state["cumulative_realized_net_pnl_usd"] = str(
                    D(str(state.get("cumulative_realized_net_pnl_usd") or "0")) + net_pnl
                )
                state.pop("pending_capital_usd", None)
                state.pop("active_run_id", None)
                state["status"] = "RUNNING"
                state["updated_at_utc"] = _iso()
                _write_state(path, state)
                logger.warning(
                    "CRYPTO_LIVE_CAMPAIGN_REALIZED run_id=%s net_pnl=%s capital=%s "
                    "venue_outcomes_agree=%s completed_attempts=%s",
                    run_id, net_pnl, costs["capital"], settlement["venue_outcomes_agree"], len(state["attempts"]),
                )

                if not bool(settlement["venue_outcomes_agree"]):
                    state["status"] = "HALTED_SETTLEMENT_DISAGREEMENT"
                    state["updated_at_utc"] = _iso()
                    _write_state(path, state)
                    logger.critical("CRYPTO_LIVE_CAMPAIGN_HALT settlement_disagreement run_id=%s", run_id)
                    return
            except Exception as exc:
                state["status"] = "HALTED_FAIL_CLOSED"
                state["halt_reason"] = f"{type(exc).__name__}: {str(exc)[:400]}"
                state["updated_at_utc"] = _iso()
                _write_state(path, state)
                logger.exception("CRYPTO_LIVE_CAMPAIGN_HALT_FAIL_CLOSED run_id=%s", run_id)
                return

    if state.get("status") == "RUNNING":
        state["status"] = "COMPLETE_MAX_TRADES" if len(state["attempts"]) >= max_trades else "COMPLETE_DEADLINE"
        state["updated_at_utc"] = _iso()
        _write_state(path, state)
    logger.warning(
        "CRYPTO_LIVE_CAMPAIGN_COMPLETE campaign_id=%s status=%s attempts=%s capital=%s realized_net_pnl=%s",
        campaign_id,
        state.get("status"),
        len(state.get("attempts", [])),
        state.get("cumulative_capital_usd"),
        state.get("cumulative_realized_net_pnl_usd"),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
