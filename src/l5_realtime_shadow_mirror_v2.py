from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from src.l5_realtime_shadow_mirror import (
    CLASSIFICATION,
    DATA_API,
    L5_WALLET,
    MirrorCollector,
    inventory_summary,
    is_target_trade,
    normalize_outcome,
    shadow_decision,
    trade_key,
    utc_now,
    fnum,
)


class MirrorCollectorV2(MirrorCollector):
    """Realtime-first collector: old trades seed context but never masquerade as live observations."""

    def __init__(self, *args, seed_lookback_s: int = 1200, **kwargs):
        super().__init__(*args, **kwargs)
        self.seed_lookback_s = seed_lookback_s
        self.seeded = False

    @staticmethod
    def _trade_ts(trade):
        ts = float(trade.get("timestamp") or utc_now())
        return ts / 1000.0 if ts > 10_000_000_000 else ts

    async def poll_trades_once(self) -> int:
        params = {"user": L5_WALLET, "limit": 200, "takerOnly": "false"}
        r = await self.client.get(f"{DATA_API}/trades", params=params)
        r.raise_for_status()
        rows = sorted(r.json(), key=lambda x: int(x.get("timestamp") or 0))

        if not self.seeded:
            cutoff = self.start_ts - self.seed_lookback_s
            seeded_trades = 0
            seeded_markets = set()
            for trade in rows:
                if not is_target_trade(trade):
                    continue
                self.seen_trades.add(trade_key(trade))
                if self._trade_ts(trade) < cutoff:
                    continue
                watch = await self.discover_from_trade(trade)
                condition = str(trade.get("conditionId") or "")
                if condition:
                    self.fills_by_condition[condition].append(trade)
                    seeded_markets.add(condition)
                    seeded_trades += 1
            self.seeded = True
            self.emit(
                {
                    "type": "REALTIME_SEED_COMPLETE",
                    "seed_lookback_s": self.seed_lookback_s,
                    "seeded_trades": seeded_trades,
                    "seeded_markets": len(seeded_markets),
                    "note": "Historical trades loaded only as context; not counted as live mirror comparisons.",
                }
            )
            return 0

        new_count = 0
        for trade in rows:
            if not is_target_trade(trade):
                continue
            key = trade_key(trade)
            if key in self.seen_trades:
                continue
            self.seen_trades.add(key)
            watch = await self.discover_from_trade(trade)
            condition = str(trade.get("conditionId") or "")
            trade_ts = self._trade_ts(trade)
            snap = self.nearest_pretrade_snapshot(condition, trade_ts)
            self.fills_by_condition[condition].append(trade)
            inv = inventory_summary(self.fills_by_condition[condition])
            copy_shadow = {
                "action": "PAPER_COPY_L5",
                "side": normalize_outcome(trade.get("outcome")),
                "price": fnum(trade.get("price")),
                "size": fnum(trade.get("size")),
            }
            ours = shadow_decision(snap, trade)
            comparison = {
                "type": "L5_TRADE_COMPARISON",
                "trade": trade,
                "pretrade_snapshot": snap,
                "l5_inventory": inv,
                "copy_shadow": copy_shadow,
                "our_shadow": ours,
                "same_side": ours.get("side") == copy_shadow.get("side") if ours.get("action") == "PAPER_BUY" else False,
                "pretrade_context_quality": "RECORDED_BEFORE_FILL" if snap else "FIRST_TRADE_IN_UNSEEN_MARKET",
            }
            self.paper_ledger.append(comparison)
            self.emit(comparison)
            new_count += 1
        return new_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/output")
    parser.add_argument("--duration-seconds", type=int, default=36000)
    parser.add_argument("--book-poll-seconds", type=float, default=1.0)
    parser.add_argument("--trade-poll-seconds", type=float, default=2.0)
    parser.add_argument("--seed-lookback-seconds", type=int, default=1200)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    collector = MirrorCollectorV2(
        Path(args.output_dir),
        duration_s=args.duration_seconds,
        book_poll_s=args.book_poll_seconds,
        trade_poll_s=args.trade_poll_seconds,
        seed_lookback_s=args.seed_lookback_seconds,
    )
    asyncio.run(collector.run())


if __name__ == "__main__":
    main()
