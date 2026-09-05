"""L5's April ramp, reconstructed from the raw activity feed.

The derived `l5_all_trades.csv` stops at 2026-04-21 -- the sequencing
pipeline's gap floor -- which made April look like 11 sporadic days. The
RAW feed (`data/l5/raw/activity.jsonl` + `activity_gap.jsonl.gz`) has 20
active April days and 38,091 items, and May/June/July besides. Every
number here comes from the raw feed.

    python3 -m src.polymarket_l5.april_ramp --month 2026-04

READ ONLY: parses local files, performs no network call.

Honest limits, stated once and repeated in the artifact:
  * maker/taker is NOT in the activity feed. The takerOnly feed that
    supports the maker label covers late August only, so every April
    action is `unknown` and is never counted as maker.
  * `usdcSize` is the executed cash; there is no order-level record, so
    quoting, cancels and queue position are unobservable.
  * REDEEM rows are settlement receipts. Trading cash and incentives are
    kept in separate streams and never netted.
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

RAW = Path("data/l5/raw")
TRADE, REDEEM = "TRADE", "REDEEM"
INCENTIVE_TYPES = ("MAKER_REBATE", "TAKER_REBATE", "REWARD")


def _day(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")


def iter_activity(root: Path = RAW) -> Iterator[dict]:
    """Every unique activity row across both raw dumps.

    The two files overlap (a forward sweep and a gap sweep), so rows are
    de-duplicated on the venue's own identifiers rather than trusting
    either file to be exclusive.
    """
    seen: set = set()
    files = [(root / "activity.jsonl", open),
             (root / "activity_gap.jsonl.gz", lambda p: gzip.open(p, "rt"))]
    for path, opener in files:
        if not path.exists():
            continue
        with opener(path) as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for row in env.get("payload") or []:
                    ts = row.get("timestamp")
                    if not ts:
                        continue
                    key = (row.get("transactionHash"), row.get("asset"),
                           ts, row.get("size"), row.get("type"))
                    if key in seen:
                        continue
                    seen.add(key)
                    yield row


def _window_of(row: Mapping[str, Any]) -> str:
    """A window is the venue's own event slug -- btc-updown-15m-<epoch>."""
    return str(row.get("eventSlug") or row.get("slug") or "unknown")


def _category(slug: str) -> str:
    parts = slug.split("-")
    if len(parts) >= 3 and parts[1] == "updown":
        return f"{parts[0]}-{parts[2]}"          # btc-15m
    return parts[0] if parts else "unknown"


def daily(rows: Iterator[dict], month: Optional[str] = None) -> dict:
    """Per-day behaviour, one pass. Trading cash and incentives separate."""
    days: dict = defaultdict(lambda: {
        "trades": 0, "redeems": 0, "windows": set(), "markets": set(),
        "categories": Counter(), "buy_usdc": 0.0, "sell_usdc": 0.0,
        "redeem_usdc": 0.0, "incentives_usdc": 0.0,
        "contracts": 0.0, "prices": [], "sizes": [], "usdc": [],
        "per_window": defaultdict(list), "maker_taker": Counter(),
    })
    for r in rows:
        d = _day(r["timestamp"])
        if month and not d.startswith(month):
            continue
        t = r.get("type")
        rec = days[d]
        usdc = float(r.get("usdcSize") or 0.0)
        if t in INCENTIVE_TYPES:
            rec["incentives_usdc"] += usdc
            continue
        if t == REDEEM:
            rec["redeems"] += 1
            rec["redeem_usdc"] += usdc
            continue
        if t != TRADE:
            continue
        w = _window_of(r)
        size = float(r.get("size") or 0.0)
        rec["trades"] += 1
        rec["windows"].add(w)
        rec["markets"].add(str(r.get("conditionId") or w))
        rec["categories"][_category(w)] += 1
        rec["contracts"] += size
        rec["prices"].append(float(r.get("price") or 0.0))
        rec["sizes"].append(size)
        rec["usdc"].append(usdc)
        # the feed carries no maker/taker; never guess one
        rec["maker_taker"]["unknown"] += 1
        if str(r.get("side") or "").upper() == "SELL":
            rec["sell_usdc"] += usdc
        else:
            rec["buy_usdc"] += usdc
        rec["per_window"][w].append(
            (int(r["timestamp"]), str(r.get("outcome") or ""),
             float(r.get("price") or 0.0), size, usdc))
    return days


def _pct(vals: list, p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(len(s) * p))], 4)


def concurrency(per_window: Mapping[str, list]) -> dict:
    """How many windows were open at once.

    A window is 'open' from its first to its last action. The earlier
    ramp analysis reported max concurrency 1 for the whole spring, but it
    was computed on data truncated at Apr 21 -- this recomputes it over
    the full month.
    """
    spans = []
    for w, acts in per_window.items():
        ts = [a[0] for a in acts]
        spans.append((min(ts), max(ts)))
    if not spans:
        return {"max": 0, "mean_at_action": 0.0}
    events = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    # concurrency seen at each window start, a fairer 'typical' figure
    at_start = []
    for s, _ in spans:
        at_start.append(sum(1 for a, b in spans if a <= s <= b))
    return {"max": peak,
            "mean_at_action": round(sum(at_start) / len(at_start), 2)}


def sequences(per_window: Mapping[str, list]) -> dict:
    """OPEN -> EXTEND -> COMPLETE shape, from inventory alone.

    Relation is derived the canonical way: the first action in a window
    is an OPEN, a later action on the SAME outcome is an EXTEND, and one
    on the opposite outcome while holding is a COMPLETE.
    """
    rel = Counter()
    completed_windows = 0
    residual_units = []
    matched_units = []
    for w, acts in per_window.items():
        acts = sorted(acts, key=lambda a: a[0])
        held: Counter = Counter()
        saw_complete = False
        for i, (_, outcome, _, size, _) in enumerate(acts):
            if i == 0 or not held:
                rel["open"] += 1
            elif held.get(outcome):
                rel["extend"] += 1
            elif held:
                rel["complete"] += 1
                saw_complete = True
            held[outcome] += size
        completed_windows += 1 if saw_complete else 0
        sides = list(held.values())
        if len(held) == 2:
            matched_units.append(min(sides))
            residual_units.append(abs(sides[0] - sides[1]))
        elif sides:
            residual_units.append(sides[0])
    return {
        "relation_counts": dict(rel),
        "windows": len(per_window),
        "windows_reaching_complete": completed_windows,
        "complete_rate": (round(completed_windows / len(per_window), 4)
                          if per_window else None),
        "median_matched_units": _pct(matched_units, 0.5),
        "median_residual_units": _pct(residual_units, 0.5),
        "p90_residual_units": _pct(residual_units, 0.9),
        "max_residual_units": (round(max(residual_units), 2)
                               if residual_units else None),
    }


def build(month: str = "2026-04", root: Path = RAW) -> dict:
    days = daily(iter_activity(root), month)
    out = []
    cum_trading = 0.0
    cum_incentive = 0.0
    for d in sorted(days):
        rec = days[d]
        # trading cash flow: redeems + sells in, buys out. NOT realized
        # P&L per position -- it is the day's net cash from trading.
        net_cash = rec["redeem_usdc"] + rec["sell_usdc"] - rec["buy_usdc"]
        cum_trading += net_cash
        cum_incentive += rec["incentives_usdc"]
        seq = sequences(rec["per_window"])
        con = concurrency(rec["per_window"])
        actions_per_window = [len(v) for v in rec["per_window"].values()]
        out.append({
            "day": d,
            "trades": rec["trades"],
            "redeems": rec["redeems"],
            "windows": len(rec["windows"]),
            "markets": len(rec["markets"]),
            "actions_per_window_median": _pct(actions_per_window, 0.5),
            "actions_per_window_p90": _pct(actions_per_window, 0.9),
            "contracts": round(rec["contracts"], 1),
            "turnover_usdc": round(rec["buy_usdc"] + rec["sell_usdc"], 2),
            "buy_usdc": round(rec["buy_usdc"], 2),
            "redeem_usdc": round(rec["redeem_usdc"], 2),
            "net_trading_cash_usdc": round(net_cash, 2),
            "cum_trading_cash_usdc": round(cum_trading, 2),
            "incentives_usdc": round(rec["incentives_usdc"], 2),
            "cum_incentives_usdc": round(cum_incentive, 2),
            "median_size_contracts": _pct(rec["sizes"], 0.5),
            "p90_size_contracts": _pct(rec["sizes"], 0.9),
            "median_action_usdc": _pct(rec["usdc"], 0.5),
            "p90_action_usdc": _pct(rec["usdc"], 0.9),
            "median_price": _pct(rec["prices"], 0.5),
            "concurrency_max": con["max"],
            "concurrency_mean_at_window_start": con["mean_at_action"],
            "top_categories": dict(rec["categories"].most_common(4)),
            **{k: seq[k] for k in ("relation_counts", "complete_rate",
                                   "median_matched_units",
                                   "median_residual_units",
                                   "p90_residual_units",
                                   "max_residual_units")},
            "maker_taker": dict(rec["maker_taker"]),
        })
    return {
        "kind": "L5_APRIL_RAMP",
        "month": month,
        "source": "data/l5/raw/activity.jsonl + activity_gap.jsonl.gz",
        "active_days": len(out),
        "days": out,
        "limits": [
            "maker/taker absent from the activity feed; every April action "
            "is unknown and is never counted as maker",
            "no order-level record: quoting, cancels and queue position "
            "are unobservable",
            "net_trading_cash is the day's cash from trading (redeems + "
            "sells - buys), NOT per-position realized P&L",
            "incentives are a separate stream and are never netted into "
            "trading cash",
        ],
    }


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--month", default="2026-04")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    doc = build(args.month)
    text = json.dumps(doc, indent=1, default=str)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(doc['days'])} days)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
