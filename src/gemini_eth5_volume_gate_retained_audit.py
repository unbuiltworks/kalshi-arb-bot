"""Audit retained ETH5 L5 windows against causal public Gemini trade flow.

This is an ex-post *audit* with an ex-ante entry rule: final volume is reported only
for diagnosis. Gate admission is computed strictly from public trades that had
already occurred by each 5-second decision timestamp.

The script intentionally does not rerun settlement or claim realized P&L. It asks a
narrow question: would a causal high-flow gate have admitted or rejected the L5
windows/fills already retained by the repository?
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any

import httpx

from src.gemini_l5_volume_gate import FiveMinuteVolumeGate, VolumeGateConfig

REST = "https://api.gemini.com"
WINDOW_SECONDS = 300
ZERO = D("0")


def expiry_from_ticker(ticker: str) -> datetime:
    prefix = "ETH05M"
    if not ticker.startswith(prefix):
        raise ValueError(f"not an ETH5 ticker: {ticker}")
    return datetime.strptime(ticker[len(prefix):], "%y%m%d%H%M").replace(tzinfo=timezone.utc)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _find_replays(root: Path) -> list[Path]:
    return sorted(root.rglob("gemini-l5-multiwindow-replay.json"))


def _settlement_map(replay_path: Path) -> dict[str, dict[str, Any]]:
    p = replay_path.with_name("gemini-l5-multiwindow-settlement.json")
    if not p.exists():
        return {}
    payload = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for key in ("settled_detail", "unresolved_detail"):
        for row in payload.get(key) or []:
            ticker = row.get("ticker")
            if ticker:
                out[str(ticker)] = row
    return out


def _market_rows(replay_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(replay_path.read_text(encoding="utf-8"))
    return [
        row for row in (payload.get("markets_detail") or [])
        if row.get("asset") == "ETH" and row.get("horizon") == "5m" and row.get("ticker")
    ]


async def fetch_window_trades(http: httpx.AsyncClient, ticker: str) -> tuple[list[dict[str, Any]], bool]:
    expiry = expiry_from_ticker(ticker)
    start = expiry - timedelta(seconds=WINDOW_SECONDS)
    start_ms = int(start.timestamp() * 1000)
    expiry_ms = int(expiry.timestamp() * 1000)
    symbol = f"GEMI-{ticker}-UP"
    response = await http.get(
        f"{REST}/v1/trades/{symbol}",
        params={"timestamp": start_ms, "limit_trades": 500},
        headers={"Cache-Control": "no-cache"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected trade-history response for {symbol}")
    rows: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        try:
            ts_ms = int(raw.get("timestampms"))
            price = D(str(raw.get("price")))
            quantity = D(str(raw.get("amount")))
        except (TypeError, ValueError, ArithmeticError):
            continue
        if not (start_ms <= ts_ms < expiry_ms):
            continue
        if not (ZERO < price < D("1")) or quantity <= ZERO:
            continue
        rows.append({"ts_ms": ts_ms, "price": price, "quantity": quantity, "tid": raw.get("tid")})
    rows.sort(key=lambda row: (row["ts_ms"], str(row.get("tid") or "")))
    return rows, len(payload) >= 500


def causal_gate_timeline(
    *,
    ticker: str,
    trades: list[dict[str, Any]],
    cfg: VolumeGateConfig,
    sample_seconds: int = 5,
) -> list[dict[str, Any]]:
    expiry = expiry_from_ticker(ticker)
    start = expiry - timedelta(seconds=WINDOW_SECONDS)
    gate = FiveMinuteVolumeGate(cfg)
    cumulative = ZERO
    cash = ZERO
    cursor = 0
    out: list[dict[str, Any]] = []
    for elapsed in range(0, WINDOW_SECONDS, sample_seconds):
        sample_at = start + timedelta(seconds=elapsed)
        sample_ms = int(sample_at.timestamp() * 1000)
        while cursor < len(trades) and trades[cursor]["ts_ms"] <= sample_ms:
            trade = trades[cursor]
            cumulative += trade["quantity"]
            cash += trade["price"] * trade["quantity"]
            cursor += 1
        decision = gate.observe(volume_contracts=cumulative, seconds_to_expiry=WINDOW_SECONDS - elapsed)
        out.append({
            "sample_at": sample_at.isoformat(),
            "sample_ms": sample_ms,
            "elapsed_seconds": elapsed,
            "cumulative_contracts": str(cumulative),
            "cumulative_cash_turnover_usd": str(cash),
            "gate_open": decision.gate_open,
            "reason": decision.reason,
            "velocity_contracts_per_second": str(decision.velocity_contracts_per_second),
            "projected_final_contracts": str(decision.projected_final_contracts),
            "tier": decision.tier,
        })
    return out


def _gate_before(timeline: list[dict[str, Any]], when: datetime) -> tuple[bool, dict[str, Any] | None]:
    target_ms = int(when.timestamp() * 1000)
    prior = [row for row in timeline if row["sample_ms"] <= target_ms]
    if not prior:
        return False, None
    row = prior[-1]
    return bool(row["gate_open"]), row


async def audit(root: Path, cfg: VolumeGateConfig) -> dict[str, Any]:
    replay_paths = _find_replays(root)
    if not replay_paths:
        raise RuntimeError(f"no retained replay files under {root}")

    windows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={"User-Agent":"arb-gemini-eth5-volume-gate-retained-audit/1.0"}) as http:
        for replay_path in replay_paths:
            settlements = _settlement_map(replay_path)
            for market in _market_rows(replay_path):
                ticker = str(market["ticker"])
                try:
                    trades, truncated = await fetch_window_trades(http, ticker)
                except Exception as exc:
                    windows.append({
                        "ticker": ticker,
                        "source_replay": str(replay_path),
                        "fetch_error": f"{type(exc).__name__}: {str(exc)[:240]}",
                    })
                    continue

                timeline = causal_gate_timeline(ticker=ticker, trades=trades, cfg=cfg)
                total_contracts = sum((row["quantity"] for row in trades), ZERO)
                total_cash = sum((row["price"] * row["quantity"] for row in trades), ZERO)
                open_rows = [row for row in timeline if row["gate_open"]]
                first_open = open_rows[0] if open_rows else None

                fill_audit: list[dict[str, Any]] = []
                for fill in market.get("fill_chronology") or []:
                    when = _parse_iso(str(fill["observed_at"]))
                    admitted, gate_row = _gate_before(timeline, when)
                    fill_audit.append({
                        "observed_at": fill["observed_at"],
                        "side": fill.get("side"),
                        "price": fill.get("price"),
                        "quantity": fill.get("quantity"),
                        "causal_gate_open_before_fill": admitted,
                        "latest_gate_sample": gate_row,
                    })

                settlement = settlements.get(ticker) or {}
                windows.append({
                    "ticker": ticker,
                    "source_replay": str(replay_path),
                    "public_trade_count": len(trades),
                    "history_truncated_possible": truncated,
                    "final_contract_volume": str(total_contracts),
                    "final_cash_turnover_usd": str(total_cash),
                    "gate_ever_opened": bool(first_open),
                    "first_gate_open_at": first_open["sample_at"] if first_open else None,
                    "first_gate_open_elapsed_seconds": first_open["elapsed_seconds"] if first_open else None,
                    "contracts_at_first_open": first_open["cumulative_contracts"] if first_open else None,
                    "projected_contracts_at_first_open": first_open["projected_final_contracts"] if first_open else None,
                    "original_shadow_fill_count": int(market.get("fills") or 0),
                    "original_shadow_fill_audit": fill_audit,
                    "settlement_resolution": settlement.get("resolution_side"),
                    "settled_shadow_trading_pnl": settlement.get("settled_shadow_trading_pnl"),
                    "classification": "RETAINED_SHADOW_AUDIT_NOT_REALIZED",
                })

    usable = [w for w in windows if not w.get("fetch_error")]
    fills = [f for w in usable for f in w.get("original_shadow_fill_audit", [])]
    opened = [w for w in usable if w.get("gate_ever_opened")]
    hot_fills = [f for f in fills if f.get("causal_gate_open_before_fill")]
    cold_fills = [f for f in fills if not f.get("causal_gate_open_before_fill")]
    positive_settled = [
        w for w in usable
        if w.get("settled_shadow_trading_pnl") is not None and D(str(w["settled_shadow_trading_pnl"])) > ZERO
    ]
    negative_settled = [
        w for w in usable
        if w.get("settled_shadow_trading_pnl") is not None and D(str(w["settled_shadow_trading_pnl"])) < ZERO
    ]

    return {
        "type": "GEMINI_ETH5_RETAINED_CAUSAL_VOLUME_GATE_AUDIT_V1",
        "classification": "RETAINED_SHADOW_AUDIT_NOT_REALIZED",
        "realized_pnl": None,
        "gate_config": {
            "warmup_seconds": cfg.warmup_seconds,
            "velocity_window_seconds": cfg.velocity_window_seconds,
            "enter_volume_contracts": str(cfg.enter_volume_contracts),
            "enter_projected_contracts": str(cfg.enter_projected_contracts),
            "enter_velocity_contracts_per_second": str(cfg.enter_velocity_contracts_per_second),
            "exit_projected_contracts": str(cfg.exit_projected_contracts),
            "exit_velocity_contracts_per_second": str(cfg.exit_velocity_contracts_per_second),
        },
        "retained_replays": len(replay_paths),
        "eth5_windows": len(windows),
        "usable_trade_histories": len(usable),
        "fetch_errors": len(windows) - len(usable),
        "gate_open_windows": len(opened),
        "original_shadow_fills": len(fills),
        "fills_with_gate_open_before_fill": len(hot_fills),
        "fills_with_gate_closed_before_fill": len(cold_fills),
        "positive_settled_shadow_windows": len(positive_settled),
        "negative_settled_shadow_windows": len(negative_settled),
        "warnings": [
            "Final volume is diagnostic only; admission uses only trades observed by each 5-second sample.",
            "Original L5 fills are shadow/replay fills and are not realized executions.",
            "Public trade history capped at 500 rows is flagged as potentially truncated.",
        ],
        "windows": windows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--enter-volume-contracts", type=D, default=D("150"))
    parser.add_argument("--enter-projected-contracts", type=D, default=D("500"))
    parser.add_argument("--enter-velocity", type=D, default=D("1.50"))
    args = parser.parse_args()
    cfg = VolumeGateConfig(
        enter_volume_contracts=args.enter_volume_contracts,
        enter_projected_contracts=args.enter_projected_contracts,
        enter_velocity_contracts_per_second=args.enter_velocity,
    )
    result = __import__("asyncio").run(audit(args.root, cfg))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
