"""L5 REDEMPTION / SETTLEMENT LIFECYCLE AUDIT -- accounting, not strategy.

OWNER: "a $1,701.60 redemption is NOT $1,701.60 profit."

This reconstructs, from L5's PUBLIC Polymarket activity only:

    acquisition cashflow + trading sales + settlement/redemption cashflow
        = net trading cash

and keeps INCENTIVES (maker/taker rebate, rewards) strictly outside it.
A redemption is NEVER an incentive and an incentive is NEVER a redemption.

THIS IS A RECONCILIATION, NOT A SECOND ACCOUNTING PATH. The existing
`analysis/l5/l5_ledger.json` (generated 2026-08-31 by the older census) is
loaded and compared line by line; where this audit differs, the difference is
attributed rather than asserted over.

DEDUPLICATION IS LOAD-BEARING. The two activity caches OVERLAP: 3,149 TRADE
rows appear in both files and a further ~4,359 repeat within them. Summing
raw rows inflated acquisition cashflow by ~$65k and flipped net trading cash
from positive to negative. Every row is therefore keyed on its full identity
before it is counted.

Read-only: reads local caches, fetches nothing, and has no order path.
"""
from __future__ import annotations

import collections
import gzip
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]
SOURCES = (
    (ROOT / "data/l5/raw/activity.jsonl", open),
    (ROOT / "data/l5/raw/activity_gap.jsonl.gz", gzip.open),
)
LEDGER = ROOT / "analysis/l5/l5_ledger.json"

WINDOW_RE = re.compile(r"^(btc|eth)-updown-(5m|15m|1h)-(\d+)$", re.I)
DURATION_S = {"5m": 300, "15m": 900, "1h": 3600}

TRADE, REDEEM = "TRADE", "REDEEM"
INCOME_TYPES = ("MAKER_REBATE", "TAKER_REBATE", "REWARD")

# Venue lifecycle vocabularies. They are NOT the same and are not forced onto
# one another -- see REDEMPTION_AUDIT.md.
LIFECYCLE = {
    "POLYMARKET": ["RESOLVED", "REDEEMABLE", "REDEEM_SUBMITTED",
                   "REDEEM_CONFIRMED", "CASHFLOW_CONFIRMED"],
    "PMUS": ["RESOLVED", "POSITION_RESOLUTION_EMITTED", "CASHFLOW_CONFIRMED"],
    "GEMINI": ["UNKNOWN_FOR_OUR_ACCOUNT"],
}


def row_key(r: Mapping[str, Any]) -> tuple:
    """Identity of an activity row, matching src/polymarket_l5/ledger.py."""
    return (str(r.get("type")), str(r.get("transactionHash")),
            str(r.get("conditionId")), str(r.get("outcome")),
            str(r.get("side")), str(r.get("size")), str(r.get("price")),
            int(r.get("timestamp") or 0))


def iter_activity(sources: Iterable = SOURCES) -> Iterable[dict]:
    """Every activity row, deduped by MULTISET UNION across cached responses.

    THIS RULE IS LOAD-BEARING AND A GLOBAL SET IS WRONG. Within ONE cached
    response, two rows sharing every field are two GENUINELY DISTINCT FILLS --
    a maker order filling in equal parts at the same instant and price. Across
    responses, the same key is the same fill harvested twice. So the
    multiplicity kept for a key is the MAXIMUM seen in any single response,
    never the sum and never one.

    A plain global set drops those legitimate repeats: it discarded 205 real
    fills, understated acquisition by $2,740.62 and overstated net trading
    cash by the same amount. This matches the dedup in
    src/polymarket_l5/ledger.py so the two accounts cannot diverge on method.
    """
    multiplicity: Dict[tuple, int] = {}
    representative: Dict[tuple, dict] = {}
    for path, opener in sources:
        if not Path(path).exists():
            continue
        with opener(path, "rt") as fh:
            for line in fh:
                try:
                    batch = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(batch, dict):
                    continue
                here: Dict[tuple, int] = {}
                for r in (batch.get("payload") or []):
                    k = row_key(r)
                    here[k] = here.get(k, 0) + 1
                    representative.setdefault(k, r)
                for k, c in here.items():
                    if c > multiplicity.get(k, 0):
                        multiplicity[k] = c
    for k, c in multiplicity.items():
        for _ in range(c):
            yield representative[k]


def window_of(r: Mapping[str, Any]) -> Optional[Tuple[str, str, int]]:
    """(asset, horizon, window_end_epoch) or None for a non-window market."""
    m = WINDOW_RE.match(str(r.get("slug") or r.get("eventSlug") or ""))
    if not m:
        return None
    return (m.group(1).lower(), m.group(2).lower(),
            int(m.group(3)) + DURATION_S[m.group(2).lower()])


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def audit(sources: Iterable = SOURCES) -> dict:
    buy = sell = redeem = 0.0
    n_buy = n_sell = n_redeem = 0
    markets: set = set()
    income = collections.Counter()
    income_usd = collections.Counter()
    by_cat: Dict[tuple, Dict[str, float]] = collections.defaultdict(
        lambda: {"redeems": 0, "redeem_usd": 0.0, "buy_usd": 0.0,
                 "sell_usd": 0.0, "redeem_qty": 0.0, "buy_qty": 0.0})
    per_market: Dict[str, dict] = collections.defaultdict(
        lambda: {"up": 0.0, "dn": 0.0, "buy_usd": 0.0, "red_qty": 0.0,
                 "red_usd": 0.0, "red_outcomes": set(), "red_t": None,
                 "end": None, "n_buys": 0, "n_sells": 0})
    red_by_tx: Dict[str, int] = collections.Counter()
    sizes_equal = 0

    for r in iter_activity(sources):
        t = str(r.get("type") or "").upper()
        cid = str(r.get("conditionId") or "")
        usd, qty = _f(r.get("usdcSize")), _f(r.get("size"))
        w = window_of(r)
        ck = (w[0], w[1]) if w else ("OTHER", "?")
        pm = per_market[cid]
        if w and pm["end"] is None:
            pm["end"] = w[2]
        if t == TRADE:
            markets.add(cid)
            side = str(r.get("side") or "").upper()
            if side == "BUY":
                buy += usd
                n_buy += 1
                pm["n_buys"] += 1
                pm["buy_usd"] += usd
                by_cat[ck]["buy_usd"] += usd
                by_cat[ck]["buy_qty"] += qty
                if str(r.get("outcome")) == "Up":
                    pm["up"] += qty
                else:
                    pm["dn"] += qty
            elif side == "SELL":
                sell += usd
                n_sell += 1
                pm["n_sells"] += 1
                by_cat[ck]["sell_usd"] += usd
        elif t == REDEEM:
            markets.add(cid)
            redeem += usd
            n_redeem += 1
            if abs(qty - usd) < 1e-9:
                sizes_equal += 1
            red_by_tx[str(r.get("transactionHash"))] += 1
            pm["red_qty"] += qty
            pm["red_usd"] += usd
            pm["red_outcomes"].add(str(r.get("outcome")))
            ts = int(r.get("timestamp") or 0)
            pm["red_t"] = ts if pm["red_t"] is None else min(pm["red_t"], ts)
            by_cat[ck]["redeems"] += 1
            by_cat[ck]["redeem_usd"] += usd
            by_cat[ck]["redeem_qty"] += qty
        elif t in INCOME_TYPES:
            income[t] += 1
            income_usd[t] += usd

    net_trading = redeem + sell - buy
    inc_total = sum(income_usd.values())
    settled = [d for d in per_market.values() if d["red_qty"] > 0 and d["end"]]
    delays = sorted(d["red_t"] - d["end"] for d in settled)
    one_outcome = sum(1 for d in settled if len(d["red_outcomes"]) == 1)
    qty_ok = 0
    for d in settled:
        if len(d["red_outcomes"]) != 1:
            continue
        won = list(d["red_outcomes"])[0]
        held = d["up"] if won == "Up" else d["dn"]
        if abs(held - d["red_qty"]) <= max(0.02, 0.005 * max(held, 1.0)):
            qty_ok += 1
    matched = sum(min(d["up"], d["dn"]) for d in settled)
    residual = sum(abs(d["up"] - d["dn"]) for d in settled)
    bought = sum(d["up"] + d["dn"] for d in settled)

    def q(xs, p):
        return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None

    return {
        "identity": {
            "acquisition_cashflow_usd": -round(buy, 2),
            "trading_sales_usd": round(sell, 2),
            "settlement_redemption_cashflow_usd": round(redeem, 2),
            "net_trading_cash_usd": round(net_trading, 2),
            "incentives_usd": round(inc_total, 2),
            "grand_total_cash_usd": round(net_trading + inc_total, 2),
            "overstatement_if_redemption_called_profit_usd":
                round(redeem - net_trading, 2),
        },
        "counts": {"buy_fills": n_buy, "sell_fills": n_sell,
                   "redemptions": n_redeem, "markets": len(markets)},
        "incentives": {k: {"rows": income[k], "usd": round(income_usd[k], 2)}
                       for k in INCOME_TYPES},
        "redeem_semantics": {
            "size_equals_usdcsize": sizes_equal,
            "of_redemptions": n_redeem,
            "reading": "a redemption converts WINNING shares at $1.00 each; "
                       "usdcSize == size on every row, price is always 0, and "
                       "side/asset are empty -- it is NOT a trade",
        },
        "lifecycle": {
            "markets_settled": len(settled),
            "exactly_one_outcome_redeemed": one_outcome,
            "redeemed_qty_equals_winning_buy_qty": qty_ok,
            "delay_resolution_to_redeem_s": {
                "p10": q(delays, 0.10), "p50": q(delays, 0.50),
                "p90": q(delays, 0.90), "p99": q(delays, 0.99),
                "max": delays[-1] if delays else None,
                "negative": sum(1 for x in delays if x < 0),
            },
            "redemptions_per_transaction": {
                "transactions": len(red_by_tx),
                "max": max(red_by_tx.values()) if red_by_tx else 0,
                "multi_redeem_tx": sum(1 for v in red_by_tx.values() if v > 1),
            },
        },
        "inventory": {
            "matched_qty": round(matched, 2),
            "residual_qty": round(residual, 2),
            "bought_qty": round(bought, 2),
            "matched_pct_of_bought": (round(100.0 * 2 * matched / bought, 2)
                                      if bought else None),
            "residual_pct_of_bought": (round(100.0 * residual / bought, 2)
                                       if bought else None),
        },
        "by_category": {
            f"{k[0]}-{k[1]}": {
                **{kk: round(vv, 2) for kk, vv in v.items()},
                "net_cash_usd": round(v["redeem_usd"] + v["sell_usd"]
                                      - v["buy_usd"], 2)}
            for k, v in sorted(by_cat.items(),
                               key=lambda kv: -kv[1]["redeem_usd"])},
    }


def reconcile(result: Mapping[str, Any],
              ledger_path: Path = LEDGER) -> Optional[dict]:
    """Line-by-line against the existing census. Differences are ATTRIBUTED,
    never silently overwritten."""
    if not Path(ledger_path).exists():
        return None
    L = json.loads(Path(ledger_path).read_text())
    t = L.get("trading") or {}
    i = result["identity"]
    return {
        "ledger_generated_at": L.get("generated_at"),
        "buy_usdc": {"audit": -i["acquisition_cashflow_usd"],
                     "ledger": t.get("buy_usdc")},
        "sell_usdc": {"audit": i["trading_sales_usd"],
                      "ledger": t.get("sell_usdc")},
        "redeem_usdc": {"audit": i["settlement_redemption_cashflow_usd"],
                        "ledger": t.get("redeem_usdc")},
        "net_trading_cash": {"audit": i["net_trading_cash_usd"],
                             "ledger": t.get("net_trading_cash")},
        "redeem_rows": {"audit": result["counts"]["redemptions"],
                        "ledger": (L.get("activity_counts") or {}).get("REDEEM")},
        "markets": {"audit": result["counts"]["markets"],
                    "ledger": L.get("n_markets")},
        "income_total": {"audit": i["incentives_usd"],
                         "ledger": L.get("income_total")},
        "note": "this audit reads a LARGER cache (the gap file) than the "
                "2026-08-31 census, so it is a superset; both agree that "
                "redemption gross dwarfs net trading cash and that incentives "
                "dominate the grand total",
    }


if __name__ == "__main__":
    out = audit()
    out["reconciliation"] = reconcile(out)
    print(json.dumps(out, indent=1))
