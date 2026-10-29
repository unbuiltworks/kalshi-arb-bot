"""Fill-level microstructure tests grounded in the Sep 1 Activity-UI evidence.

The owner's screenshots (BTC 9:45-10:00 / 10:00-10:15 ET, Sep 1) are treated
as hypothesis targets, validated here against the API-sourced canonical
sequence (which has exact timestamps; the UI is minute-coarse):

  1. size_by_price       — share quantity vs price AND dollar notional vs
                           price, conditional on side relative to inventory
                           (deficit-side "completion" vs surplus-side
                           "extension" fills).
  2. completion_tendency — P(next fill lands on the deficit side | a deficit
                           exists), by deficit-magnitude bucket, and whether
                           that opposite-side fill creates a sub-$1
                           incremental pair (fill price + wavg cost of the
                           other side < $1).
  3. fragmentation       — runs of consecutive same-side fills at an
                           identical price (resting-order-executed-in-
                           fragments signature; maker/taker is only VERIFIED
                           inside Aug 28-31 takerOnly coverage, reported per
                           run where known).
  4. pair_cost_trajectory— effective pair cost at fill-count quartiles of
                           the window, distribution across windows (not only
                           the final value).
  5. extreme_bands       — participation and role of <5c / 5-15c / 85-95c /
                           >95c fills: are cheap fills completion legs or
                           standalone longshots? (pre-fill inventory decides)
  6. concurrency         — overlapping 5m+15m windows: how many windows are
                           active at once and how notional splits when
                           windows run concurrently vs solo.

Offline, deterministic, reads only l5_canonical_sequence.csv.gz.
Fact vs inference: everything reported is a measured frequency; intent
language is confined to the "interpretation" strings.

    python3 -m src.polymarket_l5.microstructure
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import config

SEQUENCE_CSV = config.OUTPUT_DIR / "l5_canonical_sequence.csv.gz"
OUT = config.OUTPUT_DIR / "l5_microstructure.json"

BANDS = ((0.05, "<5c"), (0.15, "5-15c"), (0.30, "15-30c"), (0.50, "30-50c"),
         (0.70, "50-70c"), (0.85, "70-85c"), (0.95, "85-95c"), (10.0, ">95c"))
DEFICIT_BUCKETS = ((1.0, "none"), (10.0, "1-10sh"), (50.0, "10-50sh"),
                   (1e12, ">50sh"))


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _band(p: float) -> str:
    for hi, label in BANDS:
        if p < hi:
            return label
    return BANDS[-1][1]


def _bucket(v: float, edges) -> str:
    for hi, label in edges:
        if v < hi:
            return label
    return edges[-1][1]


def _median(xs: List[float]) -> Optional[float]:
    xs = sorted(xs)
    return round(xs[len(xs) // 2], 4) if xs else None


def _windows() -> Iterable[List[Dict[str, str]]]:
    with gzip.open(SEQUENCE_CSV, "rt", newline="", encoding="utf-8") as fh:
        cur: List[Dict[str, str]] = []
        wid = None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if cur:
                    yield cur
                cur, wid = [], row["window_id"]
            cur.append(row)
        if cur:
            yield cur


def main() -> int:
    size_by = defaultdict(lambda: {"qty": [], "usd": []})   # (band, role)
    completion = defaultdict(lambda: Counter())             # deficit bucket
    sub_dollar = Counter()
    frag_runs: List[int] = []
    frag_mt = Counter()
    traj = {"q25": [], "q50": [], "q75": [], "final": []}
    extreme = defaultdict(Counter)
    win_meta: List[Dict[str, Any]] = []

    for rows in _windows():
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"] or 0)))
        run_len, run_key = 0, None
        for i, r in enumerate(rows):
            price, qty = _f(r["price"]), _f(r["quantity"])
            usd = _f(r["dollars"])
            band = _band(price)
            # pre-fill inventory = state after previous row
            prev_resid = _f(rows[i - 1]["residual_qty_signed"]) if i else 0.0
            deficit_side = ("Down" if prev_resid > 1e-9 else
                            "Up" if prev_resid < -1e-9 else None)
            if r["side"] == "BUY":
                role = ("open" if deficit_side is None else
                        "completion" if r["outcome"] == deficit_side else
                        "extension")
                size_by[(band, role)]["qty"].append(qty)
                size_by[(band, role)]["usd"].append(usd)
                extreme[band][role] += 1
                if deficit_side is not None:
                    db = _bucket(abs(prev_resid), DEFICIT_BUCKETS)
                    completion[db][role] += 1
                    if role == "completion":
                        # wavg cost of the surplus (other) side before this fill
                        pr = rows[i - 1]
                        if deficit_side == "Down":
                            oq, osp = _f(pr["cum_up_qty"]), _f(pr["cum_up_spend"])
                        else:
                            oq, osp = _f(pr["cum_down_qty"]), _f(pr["cum_down_spend"])
                        if oq > 1e-9:
                            sub_dollar["pairs_checked"] += 1
                            if price + osp / oq < 1.0:
                                sub_dollar["sub_1_dollar"] += 1
            # fragmentation runs: same side+outcome at identical price
            key = (r["outcome"], r["side"], round(price, 4))
            if key == run_key:
                run_len += 1
            else:
                if run_len >= 2:
                    frag_runs.append(run_len)
                run_key, run_len = key, 1
            if run_len >= 2 and r["maker_taker"] in ("maker", "taker"):
                frag_mt[r["maker_taker"]] += 1
        if run_len >= 2:
            frag_runs.append(run_len)
        for label, frac in (("q25", .25), ("q50", .50), ("q75", .75),
                            ("final", 1.0)):
            idx = len(rows) - 1 if frac == 1.0 else min(int(frac * len(rows)),
                                                        len(rows) - 1)
            pc = rows[idx]["matched_pair_unit_cost"]
            if pc:
                traj[label].append(_f(pc))
        first_ts = _f(rows[0]["timestamp"])
        end_ts = first_ts + _f(rows[0]["seconds_to_settlement"])
        win_meta.append({"id": rows[0]["window_id"],
                         "dur": rows[0]["duration_minutes"],
                         "start": first_ts, "end": end_ts,
                         "usd": sum(_f(r["dollars"]) for r in rows
                                    if r["side"] == "BUY")})

    # concurrency: overlap of [first_fill, settlement] spans
    events = [(m["start"], 1, m) for m in win_meta] + \
             [(m["end"], -1, m) for m in win_meta]
    events.sort(key=lambda e: (e[0], e[1]))
    live = 0
    concurrency_hist = Counter()
    for _, kind, m in events:
        if kind == 1:
            live += 1
            m["concurrent_at_entry"] = live
        else:
            live -= 1
        concurrency_hist[max(live, 0)] += 1
    solo = [m["usd"] for m in win_meta if m.get("concurrent_at_entry", 1) == 1]
    concur = [m["usd"] for m in win_meta if m.get("concurrent_at_entry", 1) > 1]

    out: Dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(SEQUENCE_CSV),
        "size_by_price_and_role": {
            f"{band}|{role}": {
                "n": len(v["qty"]),
                "qty_median": _median(v["qty"]),
                "usd_median": _median(v["usd"]),
            }
            for (band, role), v in sorted(size_by.items())},
        "completion_tendency": {
            "note": ("given a deficit of the bucketed size existed before the "
                     "fill, share of next buys per role"),
            "by_deficit": {
                db: {"n": sum(c.values()),
                     "completion_pct": round(100 * c["completion"] /
                                             max(sum(c.values()), 1), 1),
                     "extension_pct": round(100 * c["extension"] /
                                            max(sum(c.values()), 1), 1)}
                for db, c in sorted(completion.items())},
            "sub_dollar_incremental_pairs": {
                "checked": sub_dollar["pairs_checked"],
                "sub_1_dollar_pct": round(
                    100 * sub_dollar["sub_1_dollar"] /
                    max(sub_dollar["pairs_checked"], 1), 1)},
        },
        "fragmentation": {
            "runs_ge2_same_price_same_side": len(frag_runs),
            "run_length_median": _median([float(x) for x in frag_runs]),
            "run_length_max": max(frag_runs) if frag_runs else None,
            "fills_in_runs_maker_taker_verified": dict(frag_mt),
            "note": ("consecutive same-side fills at identical price — the "
                     "resting-order-fragment signature the UI shows; "
                     "maker/taker verified only inside Aug 28-31 coverage"),
        },
        "pair_cost_trajectory": {
            k: {"median": _median(v),
                "p25": _median(sorted(v)[:max(len(v) // 2, 1)]),
                "n": len(v)}
            for k, v in traj.items()},
        "extreme_bands": {
            band: {"n": sum(c.values()),
                   "completion_pct": round(100 * c["completion"] /
                                           max(sum(c.values()), 1), 1),
                   "extension_pct": round(100 * c["extension"] /
                                          max(sum(c.values()), 1), 1),
                   "open_pct": round(100 * c["open"] /
                                     max(sum(c.values()), 1), 1)}
            for band, c in sorted(extreme.items())
            if band in ("<5c", "5-15c", "85-95c", ">95c")},
        "concurrency": {
            "windows": len(win_meta),
            "entries_while_another_window_live_pct": round(
                100 * len(concur) / max(len(win_meta), 1), 1),
            "window_usd_median_solo": _median(solo),
            "window_usd_median_concurrent": _median(concur),
            "max_concurrent_live": max(concurrency_hist) if concurrency_hist else 0,
        },
        "interpretation": {
            "cheap_fills": ("measured below: whether <5c/5-15c buys are "
                            "predominantly completion legs (pre-fill deficit "
                            "on that side) — inference about intent stays "
                            "out of the numbers"),
            "ui_check": ("UI-observed patterns (dense ladders, complementary "
                         "prices, big cheap fills / small expensive fills) "
                         "are validated only where these frequencies "
                         "support them"),
        },
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in
                      ("completion_tendency", "fragmentation",
                       "extreme_bands", "concurrency")}, indent=1))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
