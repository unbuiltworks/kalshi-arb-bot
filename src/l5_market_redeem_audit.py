"""Read-only, market-scoped reconciliation of L5 Polymarket settlement P&L.

Purpose
-------
The older L5 census summed ``realizedPnl`` from Polymarket's public
``/closed-positions`` endpoint.  This audit independently reconstructs
settlement P&L from public ``/activity`` rows on the *same conditionIds*:

    redeem proceeds - net trade spend

Only markets carrying an internal proof are admitted to the validated set:

* exactly two closed-position outcome rows are present;
* activity retrieval is complete (no offset-cap truncation);
* no SPLIT/MERGE/CONVERSION inventory mutation is present;
* exactly one redeemed outcome is observed; and
* redeemed quantity matches reconstructed winning inventory within tolerance.

Everything is public GET-only.  There is no credential or order path here.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from statistics import median
from typing import Any, Iterable

WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
DATA_API = "https://data-api.polymarket.com"
UA = "ARB-read-only-l5-market-redeem-audit/1.0"
PAGE = 500
EPS = 1e-9


def _f(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _ts(value: Any) -> int:
    x = _f(value)
    return int(x or 0)


def _get_json(url: str, params: dict[str, Any], retries: int = 4) -> Any:
    query = urllib.parse.urlencode([(k, str(v)) for k, v in params.items() if v is not None])
    full = f"{url}?{query}" if query else url
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (408, 425, 429) and exc.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(min(8.0, 0.5 * (2**attempt)))
    raise RuntimeError(f"GET failed after retries: {full}: {last}")


def _exact_key(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def _is_15m(row: dict[str, Any]) -> bool:
    slug = str(row.get("eventSlug") or row.get("slug") or "").lower()
    title = str(row.get("title") or "").lower()
    return "15m" in slug or "15 min" in title or "15-minute" in title


def fetch_closed_candidates(max_offset: int = 5000) -> list[dict[str, Any]]:
    """Return two-row BTC/ETH 15m closed markets, deduped by outcome."""
    raw: list[dict[str, Any]] = []
    for title in ("Bitcoin Up or Down", "Ethereum Up or Down"):
        offset = 0
        while offset <= max_offset:
            page = _get_json(
                f"{DATA_API}/closed-positions",
                {
                    "user": WALLET,
                    "title": title,
                    "limit": 50,
                    "offset": offset,
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "DESC",
                },
            )
            if not isinstance(page, list):
                raise RuntimeError(f"unexpected closed-positions response: {type(page)}")
            raw.extend(page)
            if len(page) < 50:
                break
            offset += 50

    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in raw:
        if not _is_15m(row):
            continue
        cid = str(row.get("conditionId") or "")
        outcome = str(row.get("outcome") or row.get("asset") or "")
        if cid and outcome:
            dedup[(cid, outcome)] = row

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in dedup.values():
        grouped[str(row.get("conditionId"))].append(row)

    markets: list[dict[str, Any]] = []
    for cid, rows in grouped.items():
        if len(rows) != 2:
            continue
        outcomes = {str(r.get("outcome") or "").strip().upper() for r in rows}
        # Current crypto binary rows are Up/Down.  If the API shape changes,
        # exclude rather than silently remap.
        if outcomes != {"UP", "DOWN"}:
            continue
        title = str(rows[0].get("title") or "")
        cell = "BTC_15m" if title.lower().startswith("bitcoin") else "ETH_15m"
        markets.append(
            {
                "condition_id": cid,
                "cell": cell,
                "timestamp": max(_ts(r.get("timestamp")) for r in rows),
                "title": title,
                "closed_rows": rows,
                "closed_realized_pnl": sum(_f(r.get("realizedPnl")) or 0.0 for r in rows),
            }
        )
    markets.sort(key=lambda m: (m["timestamp"], m["condition_id"]), reverse=True)
    return markets


def fetch_market_activity(condition_id: str, max_offset: int = 5000) -> tuple[list[dict[str, Any]], bool, str]:
    """Fetch one market's activity. Returns (deduped rows, complete, note)."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while offset <= max_offset:
        try:
            page = _get_json(
                f"{DATA_API}/activity",
                {"user": WALLET, "market": condition_id, "limit": PAGE, "offset": offset},
            )
        except urllib.error.HTTPError as exc:
            if offset > 0 and exc.code in (400, 422):
                return _dedupe(rows), False, f"activity offset cap HTTP {exc.code} at {offset}"
            raise
        if not isinstance(page, list):
            return _dedupe(rows), False, f"unexpected activity response {type(page).__name__}"
        rows.extend(page)
        if len(page) < PAGE:
            return _dedupe(rows), True, ""
        offset += PAGE
    return _dedupe(rows), False, f"activity max offset {max_offset} reached"


def _dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = _exact_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


@dataclass
class MarketAudit:
    condition_id: str
    cell: str
    title: str
    timestamp: int
    valid: bool
    reason: str
    activity_rows: int
    trade_rows: int
    redeem_rows: int
    closed_realized_pnl: float
    redeem_derived_pnl: float | None
    delta_closed_minus_redeem: float | None
    net_trade_spend: float | None
    redeem_proceeds: float | None
    redeem_qty: float | None
    reconstructed_winner_qty: float | None
    winning_outcome: str | None
    sign_agrees: bool | None


def audit_market_activity(
    market: dict[str, Any], activity_rows: list[dict[str, Any]], complete: bool = True, note: str = ""
) -> MarketAudit:
    cid = str(market["condition_id"])
    cell = str(market["cell"])
    title = str(market["title"])
    stamp = int(market["timestamp"])
    closed = float(market["closed_realized_pnl"])

    def invalid(reason: str) -> MarketAudit:
        return MarketAudit(
            cid, cell, title, stamp, False, reason, len(activity_rows),
            sum(str(r.get("type") or "").upper() == "TRADE" for r in activity_rows),
            sum(str(r.get("type") or "").upper() == "REDEEM" for r in activity_rows),
            closed, None, None, None, None, None, None, None, None,
        )

    if not complete:
        return invalid(note or "activity retrieval incomplete")

    # Guard against accidentally receiving another market despite the filter.
    if any(str(r.get("conditionId") or "") not in ("", cid) for r in activity_rows):
        return invalid("activity response contained another conditionId")

    mutators = [
        r for r in activity_rows
        if str(r.get("type") or "").upper() in {"SPLIT", "MERGE", "CONVERSION"}
    ]
    if mutators:
        return invalid("inventory-mutating SPLIT/MERGE/CONVERSION present")

    trades = [r for r in activity_rows if str(r.get("type") or "").upper() == "TRADE"]
    redeems = [r for r in activity_rows if str(r.get("type") or "").upper() == "REDEEM"]
    if not trades:
        return invalid("no TRADE activity")
    if not redeems:
        return invalid("no REDEEM activity")

    inventory = {"UP": 0.0, "DOWN": 0.0}
    buy_spend = 0.0
    sell_proceeds = 0.0
    for row in trades:
        outcome = str(row.get("outcome") or "").strip().upper()
        side = str(row.get("side") or "").strip().upper()
        qty = _f(row.get("size"))
        dollars = _f(row.get("usdcSize"))
        price = _f(row.get("price"))
        if outcome not in inventory or side not in {"BUY", "SELL"} or qty is None:
            return invalid("unusable TRADE outcome/side/size")
        if dollars is None:
            if price is None:
                return invalid("TRADE missing both usdcSize and usable price")
            dollars = price * qty
        if side == "BUY":
            inventory[outcome] += qty
            buy_spend += dollars
        else:
            inventory[outcome] -= qty
            sell_proceeds += dollars

    redeem_outcomes = {str(r.get("outcome") or "").strip().upper() for r in redeems}
    if len(redeem_outcomes) != 1 or next(iter(redeem_outcomes)) not in inventory:
        return invalid("REDEEM does not identify exactly one UP/DOWN winner")
    winner = next(iter(redeem_outcomes))
    redeem_qty = 0.0
    redeem_proceeds = 0.0
    for row in redeems:
        qty = _f(row.get("size"))
        dollars = _f(row.get("usdcSize"))
        if qty is None or dollars is None:
            return invalid("REDEEM missing size/usdcSize")
        redeem_qty += qty
        redeem_proceeds += dollars

    winner_qty = inventory[winner]
    tolerance = max(1.0, 0.01 * max(abs(winner_qty), 1.0))
    if abs(redeem_qty - winner_qty) > tolerance:
        return invalid(
            f"redeem quantity mismatch: redeem={redeem_qty:.6f} inventory={winner_qty:.6f} tol={tolerance:.6f}"
        )

    net_spend = buy_spend - sell_proceeds
    derived = redeem_proceeds - net_spend
    delta = closed - derived

    def sign(x: float) -> int:
        return 1 if x > EPS else -1 if x < -EPS else 0

    return MarketAudit(
        cid, cell, title, stamp, True, "", len(activity_rows), len(trades), len(redeems),
        closed, derived, delta, net_spend, redeem_proceeds, redeem_qty, winner_qty,
        winner, sign(closed) == sign(derived),
    )


def summarize(audits: list[MarketAudit], discovered: int, selected: int) -> dict[str, Any]:
    valid = [a for a in audits if a.valid and a.redeem_derived_pnl is not None]
    excluded = [a for a in audits if not a.valid]
    closed_sum = sum(a.closed_realized_pnl for a in valid)
    redeem_sum = sum(float(a.redeem_derived_pnl) for a in valid)
    deltas = [float(a.delta_closed_minus_redeem) for a in valid if a.delta_closed_minus_redeem is not None]
    abs_deltas = [abs(x) for x in deltas]

    by_cell: dict[str, dict[str, Any]] = {}
    for cell in sorted({a.cell for a in audits}):
        xs = [a for a in valid if a.cell == cell]
        by_cell[cell] = {
            "validated": len(xs),
            "closed_realized_pnl": round(sum(a.closed_realized_pnl for a in xs), 6),
            "redeem_derived_pnl": round(sum(float(a.redeem_derived_pnl) for a in xs), 6),
            "sign_agreement_rate": round(sum(bool(a.sign_agrees) for a in xs) / len(xs), 6) if xs else None,
        }

    reasons = Counter(a.reason.split(":", 1)[0] for a in excluded)
    worst = sorted(
        valid,
        key=lambda a: abs(float(a.delta_closed_minus_redeem or 0.0)),
        reverse=True,
    )[:12]

    return {
        "classification": "READ_ONLY_L5_MARKET_SCOPED_REDEEM_RECONCILIATION",
        "wallet": WALLET,
        "discovered_two_row_15m_markets": discovered,
        "selected_markets": selected,
        "validated_markets": len(valid),
        "excluded_markets": len(excluded),
        "validation_rate": round(len(valid) / len(audits), 6) if audits else None,
        "closed_realized_pnl_same_validated_set": round(closed_sum, 6),
        "redeem_derived_pnl_same_validated_set": round(redeem_sum, 6),
        "closed_minus_redeem_delta": round(closed_sum - redeem_sum, 6),
        "per_market_sign_agreement_rate": round(sum(bool(a.sign_agrees) for a in valid) / len(valid), 6) if valid else None,
        "delta_abs_median": round(median(abs_deltas), 6) if abs_deltas else None,
        "delta_abs_max": round(max(abs_deltas), 6) if abs_deltas else None,
        "by_cell": by_cell,
        "exclusion_reasons": dict(reasons),
        "largest_abs_deltas": [
            {
                "condition_id": a.condition_id,
                "title": a.title,
                "closed": round(a.closed_realized_pnl, 6),
                "redeem": round(float(a.redeem_derived_pnl), 6),
                "delta": round(float(a.delta_closed_minus_redeem), 6),
            }
            for a in worst
        ],
    }


def choose_markets(markets: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Deterministic recency-stratified BTC/ETH selection.

    Alternates cells when possible, preserving recency inside each cell. This
    avoids a one-asset sample if the global latest list happens to cluster.
    """
    if limit <= 0 or limit >= len(markets):
        return markets
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for market in markets:
        buckets[str(market["cell"])].append(market)
    cells = sorted(buckets)
    out: list[dict[str, Any]] = []
    indexes = {c: 0 for c in cells}
    while len(out) < limit:
        progressed = False
        for cell in cells:
            idx = indexes[cell]
            if idx < len(buckets[cell]) and len(out) < limit:
                out.append(buckets[cell][idx])
                indexes[cell] += 1
                progressed = True
        if not progressed:
            break
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-markets", type=int, default=300)
    parser.add_argument("--closed-max-offset", type=int, default=5000)
    parser.add_argument("--activity-max-offset", type=int, default=5000)
    parser.add_argument("--sleep", type=float, default=0.03, help="polite delay between market requests")
    args = parser.parse_args(argv)

    markets = fetch_closed_candidates(args.closed_max_offset)
    selected = choose_markets(markets, args.max_markets)
    audits: list[MarketAudit] = []
    for index, market in enumerate(selected, 1):
        try:
            rows, complete, note = fetch_market_activity(market["condition_id"], args.activity_max_offset)
            audit = audit_market_activity(market, rows, complete, note)
        except Exception as exc:  # one public endpoint failure should be an exclusion, not fake P&L
            audit = MarketAudit(
                str(market["condition_id"]), str(market["cell"]), str(market["title"]), int(market["timestamp"]),
                False, f"fetch error {type(exc).__name__}: {exc}", 0, 0, 0,
                float(market["closed_realized_pnl"]), None, None, None, None, None, None, None, None,
            )
        audits.append(audit)
        if index % 25 == 0:
            print(f"L5_REDEEM_PROGRESS={index}/{len(selected)} validated={sum(a.valid for a in audits)}", flush=True)
        if args.sleep > 0:
            time.sleep(args.sleep)

    result = summarize(audits, len(markets), len(selected))
    print("L5_MARKET_REDEEM_AUDIT=" + json.dumps(result, separators=(",", ":"), default=str))
    print("L5_MARKET_REDEEM_ROWS=" + json.dumps([asdict(a) for a in audits], separators=(",", ":"), default=str))
    return 0 if result["validated_markets"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
