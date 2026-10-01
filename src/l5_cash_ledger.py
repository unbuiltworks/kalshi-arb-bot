"""A Decimal cash ledger for the L5 account, built from raw venue records.

EVERY NUMBER HERE IS CASH THAT MOVED, or it is excluded and said to be.

THE FIVE DECISIONS THIS MODULE ENCODES, each of which changes the headline:

1. `usdcSize` IS THE CASH. `price` and `size` are descriptive and disagree with
   it on 11,076 of 71,984 trades, by up to $1.43 on a single row and by
   **$3,789.35 = 52.0 bps** in aggregate -- 75% of the magnitude of the reported
   September trading net. A ledger built on `price * size` is wrong by that much.
   The product is retained only as a cross-check and never as cash.

2. THE FEED PAGINATES WITH OVERLAP. 3,980 of 78,000 payload rows (5.1%) are
   duplicates. Dedupe is by content, not by cursor.

3. MEMBERSHIP IS DECIDED BY RESOLUTION, NEVER BY REDEMPTION. A market that
   resolved with no redemption contributes `-spend`. This is the survivorship
   rule already enforced in `src/l5_resolved_pnl.py`, which this module defers
   to rather than reimplementing.

4. THE WINNER IS READ, NEVER INFERRED. `outcomePrices` from the market record is
   authoritative. No price, position or P&L heuristic may decide an outcome.

5. ACCOUNT-LEVEL INCENTIVES ARE NEVER ALLOCATED TO MARKETS. Every incentive row
   the venue publishes carries an EMPTY `conditionId`, so per-market attribution
   does not exist and is not invented.

ARITHMETIC. Decimal only, constructed from `str()` of the source value. The
source serves JSON **floats**, so the venue's own decimal value is already lost
at the boundary; `Decimal(str(x))` recovers the shortest round-tripping decimal,
which is the best available and is NOT the same as source fidelity. That ceiling
is reported rather than papered over.
"""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

ZERO = D("0")

#: cash sign per row type, from the account's point of view
SIGN: Dict[str, D] = {
    "TRADE_BUY": D(-1),      # spend
    "TRADE_SELL": D(1),      # proceeds
    "REDEEM": D(1),          # authoritative redemption cash
    "MAKER_REBATE": D(1),
    "TAKER_REBATE": D(1),
    "REWARD": D(1),
}
#: incentive lines, which are account-level and unattributable to a market
INCENTIVE_KINDS = ("MAKER_REBATE", "TAKER_REBATE", "REWARD")
TRADING_KINDS = ("TRADE_BUY", "TRADE_SELL", "REDEEM")


class LedgerError(ValueError):
    """An invariant failed. The ledger refuses to produce a number."""


def dec(v: Any) -> D:
    """Decimal from the source value. Never float arithmetic."""
    if v is None:
        raise LedgerError("missing numeric where cash was expected")
    return D(str(v))


def kind_of(row: Mapping[str, Any]) -> str:
    t = str(row.get("type") or "")
    if t == "TRADE":
        side = str(row.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            raise LedgerError(f"TRADE with unusable side {side!r}")
        return f"TRADE_{side}"
    if t not in SIGN:
        raise LedgerError(f"unknown cash row type {t!r}")
    return t


def dedupe_key(row: Mapping[str, Any]) -> Tuple:
    """Content identity. The feed repeats rows across paginated responses, so a
    cursor-based or append-only reader double counts 5.1% of the cashflow."""
    return (str(row.get("transactionHash")), str(row.get("conditionId")),
            str(row.get("type")), str(row.get("side")),
            str(row.get("outcome")), str(row.get("price")),
            str(row.get("size")), str(row.get("usdcSize")),
            str(row.get("timestamp")))


def iter_rows(path: str) -> Iterator[Dict[str, Any]]:
    """Unwrap the response log. Each line is one API response, not one row."""
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            pay = r.get("payload")
            if isinstance(pay, list):
                for x in pay:
                    if isinstance(x, dict):
                        yield x
            elif isinstance(pay, dict):
                yield pay


class CashLedger:
    """Deduped, signed, Decimal cash events with their reconciliation."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self._seen: set = set()
        self.duplicates = 0
        self.rejected: List[Tuple[str, Any]] = []
        #: cross-check only, never cash
        self.price_size_product = ZERO
        self.trade_usdc_abs = ZERO
        self.usdc_total_abs = ZERO

    def add(self, row: Mapping[str, Any]) -> bool:
        k = dedupe_key(row)
        if k in self._seen:
            self.duplicates += 1
            return False
        self._seen.add(k)
        try:
            kind = kind_of(row)
            usdc = dec(row.get("usdcSize"))
        except LedgerError as exc:
            self.rejected.append((str(exc), row.get("transactionHash")))
            return False
        if usdc < 0:
            raise LedgerError(
                f"negative usdcSize {usdc} on {kind}; sign is carried by the "
                f"ledger, not by the source value")
        ev = {
            "kind": kind, "cash": SIGN[kind] * usdc, "abs": usdc,
            "condition_id": str(row.get("conditionId") or ""),
            "slug": str(row.get("slug") or ""),
            "outcome": str(row.get("outcome") or ""),
            "ts": int(row.get("timestamp") or 0),
            "tx": str(row.get("transactionHash") or ""),
        }
        if kind.startswith("TRADE"):
            self.price_size_product += dec(row.get("price")) * dec(row.get("size"))
            #: TRADE-ONLY, so the cross-check compares like with like. Summing
            #: every kind here (redemptions, incentives) against a TRADE-only
            #: product produced a meaningless 5,055 bps "drift".
            self.trade_usdc_abs += usdc
        self.usdc_total_abs += usdc
        self.events.append(ev)
        return True

    # ------------------------------------------------------------- rollups

    def by_kind(self) -> Dict[str, D]:
        out: Dict[str, D] = defaultdict(lambda: ZERO)
        for e in self.events:
            out[e["kind"]] += e["cash"]
        return dict(out)

    def account_cash(self) -> D:
        return sum((e["cash"] for e in self.events), ZERO)

    def by_market(self) -> Dict[str, Dict[str, D]]:
        """Trading cash per market. INCENTIVES ARE EXCLUDED BY CONSTRUCTION:
        their conditionId is empty, so attributing them to a market would be
        inventing a fact the venue does not publish."""
        out: Dict[str, Dict[str, D]] = defaultdict(
            lambda: defaultdict(lambda: ZERO))
        for e in self.events:
            if e["kind"] in INCENTIVE_KINDS:
                continue
            if not e["condition_id"]:
                raise LedgerError(
                    f"{e['kind']} row with empty conditionId cannot be placed "
                    f"in a market rollup")
            out[e["condition_id"]][e["kind"]] += e["cash"]
        return {k: dict(v) for k, v in out.items()}

    def by_day(self) -> Dict[str, Dict[str, D]]:
        import datetime as dt
        out: Dict[str, Dict[str, D]] = defaultdict(
            lambda: defaultdict(lambda: ZERO))
        for e in self.events:
            #: UTC date of the venue timestamp. A local-time bucket shifts
            #: cash across the midnight boundary the reward pool settles on.
            day = dt.datetime.fromtimestamp(
                e["ts"], dt.timezone.utc).strftime("%Y-%m-%d")
            out[day][e["kind"]] += e["cash"]
        return {k: dict(v) for k, v in out.items()}

    def incentive_attribution_check(self) -> Dict[str, Any]:
        """Every incentive row must be unattributable. If one ever carries a
        conditionId, per-market allocation becomes possible and this says so
        rather than silently continuing to aggregate at account level."""
        total = sum(1 for e in self.events if e["kind"] in INCENTIVE_KINDS)
        attributed = sum(1 for e in self.events
                         if e["kind"] in INCENTIVE_KINDS and e["condition_id"])
        return {"incentive_rows": total, "with_condition_id": attributed,
                "attributable": attributed > 0}

    def reconcile(self) -> Dict[str, Any]:
        """The identity: account cash equals the sum of its parts, exactly."""
        parts = self.by_kind()
        total = self.account_cash()
        recomputed = sum(parts.values(), ZERO)
        residual = total - recomputed
        if residual != ZERO:
            raise LedgerError(
                f"account cash {total} != sum of kinds {recomputed}; "
                f"residual {residual}")
        drift = self.trade_usdc_abs - self.price_size_product
        return {
            "by_kind": {k: str(v) for k, v in sorted(parts.items())},
            "account_cash": str(total),
            "residual_vs_parts": str(residual),
            "events": len(self.events),
            "duplicates_dropped": self.duplicates,
            "rejected_rows": len(self.rejected),
            "price_size_product": str(self.price_size_product),
            "trade_usdc_abs": str(self.trade_usdc_abs),
            "usdc_abs_total_all_kinds": str(self.usdc_total_abs),
            "price_size_vs_usdc_drift": str(drift),
            "drift_bps_of_trade_usdc": (
                str((drift / self.trade_usdc_abs) * D(10000))
                if self.trade_usdc_abs else None),
            "drift_note": ("price*size is NOT cash and is never used as cash; "
                           "this is the size of the error a price*size ledger "
                           "would carry"),
        }


def load(path: str) -> CashLedger:
    led = CashLedger()
    for row in iter_rows(path):
        led.add(row)
    return led
