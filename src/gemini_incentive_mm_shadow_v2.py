"""Reconnect-safe online Gemini q10 incentive/MM shadow V2.

Public-data only. This collector applies the same rebate-first, two-sided q10 policy
used by ``gemini_incentive_mm_replay_v2`` directly to live Gemini Predictions
books/trades while maintaining two independent virtual fill ledgers:

* TAPE_TOUCH_OPTIMISTIC: public maker-side prints can fill resting virtual size.
* STRICT_TRADE_THROUGH: a quote fills only after public top-of-book moves fully
  through the previously resting quote.

Trade/settlement edge is accounted separately from PROJECTED maker rebates.
Liquidity-reward eligibility is measured as quote-shape/update eligibility only;
no reward share is projected. No credentials, balances, order entry, or cancels.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal as D
import json
from pathlib import Path
import time
from typing import Any

import httpx
import websockets

from src.gemini_incentive_mm_replay_v2 import Market as PolicyMarket
from src.gemini_l5_tape_dynamic_shadow import WS_URL, _iso, _parse_book_ticker, _parse_trade, _write
from src.gemini_l5_tape_dynamic_shadow_v3 import write_checkpoint
from src.gemini_l5_threshold_shadow import Event, book, discover

ZERO = D("0")


@dataclass
class OnlineMarket:
    event: Event
    policy: PolicyMarket

    @classmethod
    def create(cls, event: Event) -> "OnlineMarket":
        return cls(event=event, policy=PolicyMarket(ticker=event.ticker))

    def identity(self) -> dict[str, Any]:
        return {
            "ticker": self.event.ticker,
            "symbol": self.event.symbol,
            "asset": self.event.asset,
            "horizon": self.event.horizon,
        }

    @staticmethod
    def _new_fill_rows(
        *,
        ledger_name: str,
        before: int,
        ledger: Any,
        identity: dict[str, Any],
        observed_at: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for fill in ledger.fills[before:]:
            rows.append(
                {
                    "type": "INCENTIVE_MM_SHADOW_FILL",
                    "observed_at": observed_at,
                    **identity,
                    "ledger": ledger_name,
                    "side": fill.side,
                    "price": str(fill.price),
                    "quantity": str(fill.quantity),
                    "maker_fee": str(fill.fee),
                    "projected_maker_rebate_not_realized": str(fill.projected_rebate),
                    "evidence": fill.evidence,
                    "read_only": True,
                    "order_entry": False,
                }
            )
        return rows

    def on_book(self, bid: D, ask: D, observed_at: str) -> list[dict[str, Any]]:
        before_tape = len(self.policy.tape_touch.fills)
        before_strict = len(self.policy.strict_trade_through.fills)
        self.policy.on_book(D(str(bid)), D(str(ask)))
        ident = self.identity()
        rows = self._new_fill_rows(
            ledger_name="TAPE_TOUCH_OPTIMISTIC",
            before=before_tape,
            ledger=self.policy.tape_touch,
            identity=ident,
            observed_at=observed_at,
        )
        rows.extend(
            self._new_fill_rows(
                ledger_name="STRICT_TRADE_THROUGH",
                before=before_strict,
                ledger=self.policy.strict_trade_through,
                identity=ident,
                observed_at=observed_at,
            )
        )
        rows.append(
            {
                "type": "INCENTIVE_MM_SHADOW_PLAN",
                "observed_at": observed_at,
                **ident,
                "yes_bid": str(bid),
                "yes_ask": str(ask),
                "tape_touch": self._plan(self.policy.tape_touch),
                "strict_trade_through": self._plan(self.policy.strict_trade_through),
                "quote_quantity": "10",
                "read_only": True,
                "order_entry": False,
            }
        )
        return rows

    def on_trade(self, price: D, quantity: D, buyer_maker: bool, observed_at: str) -> list[dict[str, Any]]:
        before = len(self.policy.tape_touch.fills)
        self.policy.on_trade(D(str(price)), D(str(quantity)), bool(buyer_maker))
        return self._new_fill_rows(
            ledger_name="TAPE_TOUCH_OPTIMISTIC",
            before=before,
            ledger=self.policy.tape_touch,
            identity=self.identity(),
            observed_at=observed_at,
        )

    @staticmethod
    def _plan(ledger: Any) -> dict[str, Any]:
        return {
            "yes_quote": str(ledger.yes_quote) if ledger.yes_quote is not None else None,
            "no_quote": str(ledger.no_quote) if ledger.no_quote is not None else None,
            "quote_size": str(ledger.quote_size),
            "residual_side": ledger.residual_side,
            "residual_qty": str(ledger.residual_qty),
            "blocked_unprofitable_completion": ledger.blocked_unprofitable_completion,
        }


def _aggregate(states: dict[str, OnlineMarket], ledger_attr: str) -> dict[str, Any]:
    ledgers = [getattr(state.policy, ledger_attr) for state in states.values()]
    summaries = [ledger.summary() for ledger in ledgers]
    locked = sum((D(s["locked_trade_edge_after_fees"]) for s in summaries), ZERO)
    projected = sum((D(s["projected_maker_rebates_not_realized"]) for s in summaries), ZERO)
    matched = sum((D(s["matched_quantity"]) for s in summaries), ZERO)
    residual_qty = sum((D(s["residual_qty"]) for s in summaries), ZERO)
    residual_cost = ZERO
    book_updates = 0
    eligible_updates = 0
    for ledger in ledgers:
        book_updates += ledger.book_updates
        eligible_updates += ledger.eligible_book_updates
        if ledger.residual_side is not None and ledger.residual_unit_price is not None:
            residual_cost += ledger.residual_qty * ledger.residual_unit_price
            if ledger.residual_unit_fee is not None:
                residual_cost += ledger.residual_unit_fee
    return {
        "markets_with_fills": sum(1 for s in summaries if s["fills"]),
        "markets_with_pairs": sum(1 for s in summaries if s["completed_pairs"]),
        "fill_count": sum(int(s["fills"]) for s in summaries),
        "fill_quantity": str(sum((D(s["fill_quantity"]) for s in summaries), ZERO)),
        "matched_quantity": str(matched),
        "residual_quantity": str(residual_qty),
        "residual_acquisition_cost_plus_known_fees": str(residual_cost),
        "locked_trade_edge_after_fees": str(locked),
        "projected_maker_rebates_not_realized": str(projected),
        "projected_combined_if_rebates_paid": str(locked + projected),
        "book_updates": book_updates,
        "liquidity_reward_rule_eligible_book_updates": eligible_updates,
        "liquidity_reward_rule_eligible_book_update_fraction": (
            eligible_updates / book_updates if book_updates else 0.0
        ),
        "blocked_unprofitable_completion": sum(
            int(s["blocked_unprofitable_completion"]) for s in summaries
        ),
    }


def build_summary(
    *,
    stats: dict[str, int],
    states: dict[str, OnlineMarket],
    seconds: float,
    discover_s: float,
    status: str,
) -> dict[str, Any]:
    return {
        "type": "GEMINI_INCENTIVE_MM_ONLINE_SHADOW_V2",
        **stats,
        "status": status,
        "updated_at": _iso(),
        "seconds_requested": seconds,
        "discover_s": discover_s,
        "markets": len(states),
        "quote_quantity": "10",
        "tape_touch": _aggregate(states, "tape_touch"),
        "strict_trade_through": _aggregate(states, "strict_trade_through"),
        "markets_detail": {
            state.event.ticker: {
                **state.identity(),
                "tape_touch": state.policy.tape_touch.summary(),
                "strict_trade_through": state.policy.strict_trade_through.summary(),
            }
            for state in states.values()
        },
        "read_only": True,
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "classification": "SHADOW_NOT_REALIZED_PROJECTED_REWARDS_SEPARATE",
        "warnings": [
            "Tape-touch ignores queue position and may over-credit fills.",
            "Strict trade-through is conservative but still does not prove real queue position.",
            "Maker rebates are projected only and never included in realized P&L.",
            "Liquidity reward update eligibility is not a payout estimate or exact minute-scored uptime.",
        ],
    }


async def capture(
    *,
    seconds: float,
    discover_s: float,
    output: Path,
    summary_output: Path | None = None,
    checkpoint_s: float = 5.0,
    reconnect_s: float = 1.0,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)
    stats: dict[str, int] = {
        "messages": 0,
        "book_tickers": 0,
        "trades": 0,
        "errors": 0,
        "discoveries": 0,
        "subscriptions": 0,
        "rest_seeds": 0,
        "control_messages": 0,
        "subscription_errors": 0,
        "ws_connections": 0,
        "ws_disconnects": 0,
        "ws_reconnects": 0,
        "checkpoints": 0,
    }
    states: dict[str, OnlineMarket] = {}
    last_checkpoint = 0.0

    def checkpoint(status: str, *, force: bool = False) -> None:
        nonlocal last_checkpoint
        now = time.monotonic()
        if not force and now - last_checkpoint < max(1.0, checkpoint_s):
            return
        write_checkpoint(
            summary_output,
            build_summary(
                stats=stats,
                states=states,
                seconds=seconds,
                discover_s=discover_s,
                status=status,
            ),
        )
        last_checkpoint = now
        stats["checkpoints"] += 1

    async with httpx.AsyncClient(
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": "arb-gemini-incentive-mm-shadow/2.0"},
    ) as http:
        with output.open("w", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                subscribed: set[str] = set()
                last_discover = 0.0
                try:
                    async with websockets.connect(
                        WS_URL,
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=5,
                        max_size=2**20,
                    ) as ws:
                        stats["ws_connections"] += 1
                        if stats["ws_connections"] > 1:
                            stats["ws_reconnects"] += 1
                        _write(fh, {"type": "WS_CONNECTED", "observed_at": _iso(), "connection_number": stats["ws_connections"]})

                        while time.monotonic() < deadline:
                            now = time.monotonic()
                            if now - last_discover >= discover_s or not subscribed:
                                try:
                                    events = await discover(http)
                                    stats["discoveries"] += 1
                                    desired: list[str] = []
                                    for event in events.values():
                                        key = event.symbol.upper()
                                        if key not in states:
                                            state = OnlineMarket.create(event)
                                            states[key] = state
                                            try:
                                                bid, _, ask, _ = await book(http, event.symbol)
                                                for row in state.on_book(bid, ask, _iso()):
                                                    _write(fh, row)
                                                stats["rest_seeds"] += 1
                                            except Exception as exc:
                                                stats["errors"] += 1
                                                _write(fh, {"type": "REST_SEED_ERROR", "observed_at": _iso(), **state.identity(), "error_type": type(exc).__name__, "error": str(exc)[:240]})
                                        desired.extend([f"{event.symbol}@bookTicker", f"{event.symbol}@trade"])
                                    new_streams = [s for s in desired if s not in subscribed]
                                    if new_streams:
                                        request_id = f"{stats['ws_connections']}-{stats['subscriptions'] + 1}"
                                        await ws.send(json.dumps({"id": request_id, "method": "SUBSCRIBE", "params": new_streams}))
                                        _write(fh, {"type": "WS_SUBSCRIBE_SENT", "observed_at": _iso(), "id": request_id, "streams": new_streams})
                                        subscribed.update(new_streams)
                                        stats["subscriptions"] += len(new_streams)
                                    last_discover = now
                                except Exception as exc:
                                    stats["errors"] += 1
                                    _write(fh, {"type": "SHADOW_DISCOVERY_ERROR", "observed_at": _iso(), "error_type": type(exc).__name__, "error": str(exc)[:240]})

                            checkpoint("RUNNING")
                            timeout = min(1.0, max(0.05, deadline - time.monotonic()))
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                            except asyncio.TimeoutError:
                                continue
                            try:
                                message = json.loads(raw)
                            except Exception:
                                stats["errors"] += 1
                                _write(fh, {"type": "WS_NON_JSON", "observed_at": _iso(), "raw": str(raw)[:500]})
                                continue
                            if not isinstance(message, dict):
                                continue

                            stats["messages"] += 1
                            symbol = str(message.get("s") or "").upper()
                            state = states.get(symbol)
                            if state is None:
                                stats["control_messages"] += 1
                                status_code = message.get("status")
                                if isinstance(status_code, int) and status_code >= 400:
                                    stats["subscription_errors"] += 1
                                _write(fh, {"type": "WS_CONTROL", "observed_at": _iso(), "message": message})
                                continue

                            observed_at = _iso()
                            trade = _parse_trade(message)
                            if trade is not None and "t" in message:
                                stats["trades"] += 1
                                for row in state.on_trade(*trade, observed_at):
                                    _write(fh, row)
                                _write(fh, {"type": "PUBLIC_TRADE", "observed_at": observed_at, **state.identity(), "price": str(trade[0]), "quantity": str(trade[1]), "buyer_maker": trade[2], "trade_id": message.get("t")})
                            else:
                                book_tick = _parse_book_ticker(message)
                                if book_tick is not None:
                                    stats["book_tickers"] += 1
                                    for row in state.on_book(*book_tick, observed_at):
                                        _write(fh, row)

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    stats["ws_disconnects"] += 1
                    stats["errors"] += 1
                    _write(fh, {"type": "WS_DISCONNECTED_RETRYING", "observed_at": _iso(), "error_type": type(exc).__name__, "error": str(exc)[:500], "retry_in_s": reconnect_s})
                    checkpoint("RECONNECTING", force=True)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(max(0.05, reconnect_s), remaining))

            final = build_summary(
                stats=stats,
                states=states,
                seconds=seconds,
                discover_s=discover_s,
                status="COMPLETED",
            )
            write_checkpoint(summary_output, final)
            print(json.dumps(final, indent=2, sort_keys=True), flush=True)
            return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=7200.0)
    parser.add_argument("--discover-s", type=float, default=10.0)
    parser.add_argument("--checkpoint-s", type=float, default=5.0)
    parser.add_argument("--reconnect-s", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/gemini-incentive-mm-shadow-v2.jsonl"))
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()
    asyncio.run(capture(seconds=args.seconds, discover_s=args.discover_s, output=args.output, summary_output=args.summary_output, checkpoint_s=args.checkpoint_s, reconnect_s=args.reconnect_s))


if __name__ == "__main__":
    main()
