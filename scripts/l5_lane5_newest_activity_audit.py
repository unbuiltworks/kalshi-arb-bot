#!/usr/bin/env python3
"""Lane 5: re-test the L5 behavioral baseline against the newest available corpus.

RESEARCH ONLY. Reads the existing raw corpus written by the canonical collector
(`polymarket_l5.cli fetch` -> `polymarket_l5.cache.RawCache`, envelope
url/params/status/payload/fetched_at/tag) plus the Sep 23-25 harvest written by
scripts/l5_harvest_sep_activity.py. No network, no venue writes.

Every number is reported with its period, record count and date range. Periods
are calendar-month chronological splits; nothing is pooled across periods and
called an update.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import re
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "l5" / "raw"
OUT = ROOT / "analysis" / "l5"
NAME = "l5Zn1bWoM8eTsK"
ADDRESS = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"


def owns(record_envelope_params, item) -> bool:
    """Identity test.

    The baseline filtered on ``item['name'] == 'l5Zn1bWoM8eTsK'``. That is not
    sound: the /activity feed omits the profile block (name/proxyWallet/bio) on
    some pages, so a name-only filter silently discards L5's own fills. Identity
    here comes from PROVENANCE -- the envelope's own ``params.user`` is L5's
    address, so every record in that response is L5's activity -- and a
    contradicting name is what disqualifies a record.
    """
    name = item.get("name")
    if name is not None:
        return name == NAME
    return (record_envelope_params or {}).get("user", "").lower() == ADDRESS

ENVELOPE_SOURCES = [
    ("activity.jsonl", False),
    ("activity_gap.jsonl.gz", True),
    ("trades.jsonl", False),
    ("trades_takeronly.jsonl", False),
]
TRAILING_INT = re.compile(r"-(\d{6,})$")


def _envelope_rows(path: Path, gz: bool):
    opener = (lambda p: gzip.open(p, "rt", encoding="utf-8")) if gz else (
        lambda p: open(p, "r", encoding="utf-8")
    )
    with opener(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_fills():
    """Return (fills, provenance). Dedupe on (transactionHash, asset, timestamp)."""
    dedup = {}
    cashflows = []  # REDEEM / MAKER_REBATE / TAKER_REBATE / REWARD
    prov = {"envelope_files": {}, "sep_files": {}, "raw_payload_records": 0}
    for fname, gz in ENVELOPE_SOURCES:
        path = RAW / fname
        if not path.exists():
            continue
        rows = 0
        statuses = Counter()
        fetched = Counter()
        kept = 0
        kept_by_name = 0
        for rec in _envelope_rows(path, gz):
            rows += 1
            statuses[rec.get("status")] += 1
            fetched[(rec.get("fetched_at") or "")[:10]] += 1
            if rec.get("status") != 200:
                continue
            for x in rec.get("payload") or []:
                prov["raw_payload_records"] += 1
                if not owns(rec.get("params"), x):
                    continue
                if x.get("name") == NAME:
                    kept_by_name += 1
                kind = x.get("type")
                if kind not in (None, "TRADE"):
                    # settlement/incentive cashflow, not a fill
                    y = dict(x)
                    y["_src"] = fname
                    cashflows.append(y)
                    continue
                if not x.get("side"):
                    continue
                key = (x.get("transactionHash"), x.get("asset"), x.get("timestamp"))
                kept += 1
                if key not in dedup:
                    x = dict(x)
                    x["_src"] = fname
                    dedup[key] = x
        prov["envelope_files"][fname] = {
            "envelope_rows": rows,
            "statuses": {str(k): v for k, v in statuses.items()},
            "fetched_at_days": dict(fetched),
            "own_payload_records_provenance_identity": kept,
            "own_payload_records_name_filter_only": kept_by_name,
        }
    # Sep harvest (separate script, same record shape)
    sep = RAW / "sep" / "l5_sep23_25_daily.json"
    redeems = []
    if sep.exists():
        daily = json.loads(sep.read_text())
        for day, kinds in daily.items():
            counts = {k: len(v) for k, v in kinds.items()}
            kept = 0
            for x in kinds.get("TRADE", []):
                prov["raw_payload_records"] += 1
                if x.get("name") != NAME:
                    continue
                kept += 1
                key = (x.get("transactionHash"), x.get("asset"), x.get("timestamp"))
                if key not in dedup:
                    x = dict(x)
                    x["_src"] = "sep/l5_sep23_25_daily.json"
                    dedup[key] = x
            for kind in ("REDEEM", "REWARD", "MAKER_REBATE", "TAKER_REBATE"):
                for x in kinds.get(kind, []):
                    if x.get("name") == NAME:
                        y = dict(x)
                        y["_src"] = "sep/l5_sep23_25_daily.json"
                        cashflows.append(y)
                        if kind == "REDEEM":
                            redeems.append(x)
            counts["own_TRADE_records"] = kept
            prov["sep_files"][day] = counts
    fills = sorted(dedup.values(), key=lambda x: (x.get("timestamp") or 0))
    prov["deduplicated_fills"] = len(fills)
    prov["redeem_records"] = len(redeems)
    prov["cashflow_records"] = len(cashflows)
    return fills, prov, redeems, cashflows


def period_of(ts: int) -> str:
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m")


def window_epoch(slug: str):
    m = TRAILING_INT.search(slug or "")
    return int(m.group(1)) if m else None


def slug_prefix(slug: str) -> str:
    s = TRAILING_INT.sub("", slug or "")
    # strip trailing date-ish / team-ish tails conservatively: keep first 3 tokens
    toks = [t for t in s.split("-") if t]
    return "-".join(toks[:3]) if toks else "(none)"


def duration_tag(slug: str) -> str:
    m = re.search(r"(\d+)(m|h)\b", slug or "")
    return m.group(0) if m else "(none)"


def q(vals, p):
    if not vals:
        return None
    vs = sorted(vals)
    i = max(0, min(len(vs) - 1, int(round(p * (len(vs) - 1)))))
    return vs[i]


def describe(vals, nd=3):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "median": round(st.median(vals), nd),
        "mean": round(st.fmean(vals), nd),
        "p10": round(q(vals, 0.10), nd),
        "p90": round(q(vals, 0.90), nd),
        "min": round(min(vals), nd),
        "max": round(max(vals), nd),
    }


def build_events(fills):
    ev = defaultdict(list)
    for f in fills:
        ev[f.get("eventSlug") or "(none)"].append(f)
    out = {}
    for slug, fs in ev.items():
        fs.sort(key=lambda x: (x.get("timestamp") or 0, x.get("transactionHash") or ""))
        out[slug] = fs
    return out


def event_metrics(slug, fs):
    buys = [f for f in fs if (f.get("side") or "").upper() == "BUY"]
    if not buys:
        return None
    first = buys[0]
    epoch = window_epoch(slug)
    first_idx = first.get("outcomeIndex")
    m = {
        "slug": slug,
        "prefix": slug_prefix(slug),
        "duration": duration_tag(slug),
        "period": period_of(first["timestamp"]),
        "day": dt.datetime.utcfromtimestamp(first["timestamp"]).strftime("%Y-%m-%d"),
        "hour_utc": dt.datetime.utcfromtimestamp(first["timestamp"]).hour,
        "first_price": float(first.get("price") or 0),
        "first_size": float(first.get("size") or 0),
        "first_usdc": float(first.get("usdcSize") or 0) or None,
        "own_fills": len(fs),
        "own_buys": len(buys),
        "own_sells": len(fs) - len(buys),
        "t_into_window": (first["timestamp"] - epoch) if epoch else None,
        "total_size": sum(float(f.get("size") or 0) for f in fs),
        "buy_sizes": [float(f.get("size") or 0) for f in buys],
        "distinct_prices": len({round(float(f.get("price") or 0), 2) for f in buys}),
        "first_ts": first["timestamp"],
        "last_ts": fs[-1]["timestamp"],
    }
    m["favored"] = m["first_price"] > 0.50
    # complement = first BUY on the opposite outcomeIndex after the first buy
    comp = next(
        (b for b in buys[1:] if b.get("outcomeIndex") != first_idx), None
    )
    m["complemented"] = comp is not None
    m["time_to_complement"] = (comp["timestamp"] - first["timestamp"]) if comp else None
    m["complement_price"] = float(comp.get("price") or 0) if comp else None
    if comp:
        m["pair_cost"] = round(m["first_price"] + m["complement_price"], 4)
    # same-side EXTENDs before complementing
    extends = 0
    for b in buys[1:]:
        if b.get("outcomeIndex") != first_idx:
            break
        extends += 1
    m["extends_before_complement"] = extends
    return m


def group_stats(rows, label_key):
    groups = defaultdict(list)
    for r in rows:
        groups[r[label_key]].append(r)
    res = {}
    for g, rs in sorted(groups.items()):
        days = sorted({r["day"] for r in rs})
        comp = [r for r in rs if r["complemented"]]
        res[g] = {
            "events": len(rs),
            "own_fills": sum(r["own_fills"] for r in rs),
            "date_range": [days[0], days[-1]] if days else None,
            "first_price": describe([r["first_price"] for r in rs]),
            "pct_favored": round(100.0 * sum(r["favored"] for r in rs) / len(rs), 1),
            "t_into_window_s": describe([r["t_into_window"] for r in rs], 0),
            "pct_complemented": round(100.0 * len(comp) / len(rs), 1),
            "time_to_complement_s": describe(
                [r["time_to_complement"] for r in comp], 0
            ),
            "extends_before_complement": describe(
                [r["extends_before_complement"] for r in rs], 2
            ),
            "pct_with_extend": round(
                100.0 * sum(r["extends_before_complement"] >= 1 for r in rs) / len(rs), 1
            ),
            "own_fills_per_event": describe([r["own_fills"] for r in rs], 1),
            "first_fill_size": describe([r["first_size"] for r in rs], 1),
            "per_fill_size": describe(
                [s for r in rs for s in r["buy_sizes"]], 1
            ),
            "event_total_size": describe([r["total_size"] for r in rs], 1),
            "pair_cost": describe([r["pair_cost"] for r in comp if r.get("pair_cost")], 4),
            "slug_prefixes": dict(Counter(r["prefix"] for r in rs).most_common(8)),
            "durations": dict(Counter(r["duration"] for r in rs).most_common(8)),
        }
    return res


def concurrency(events):
    """Max overlapping events per period, by [first_ts, last_ts] intervals."""
    per = defaultdict(list)
    for m in events:
        per[m["period"]].append((m["first_ts"], m["last_ts"]))
    out = {}
    for p, ivs in sorted(per.items()):
        pts = sorted([(a, 1) for a, _ in ivs] + [(b, -1) for _, b in ivs])
        cur = mx = 0
        for _, d in pts:
            cur += d
            mx = max(mx, cur)
        out[p] = {"events": len(ivs), "max_concurrent_open_events": mx}
    return out


def main():
    fills, prov, redeems, cashflows = load_fills()
    if not fills:
        raise SystemExit("no fills")
    tss = [f["timestamp"] for f in fills]
    prov["fill_date_range_utc"] = [
        dt.datetime.utcfromtimestamp(min(tss)).isoformat() + "Z",
        dt.datetime.utcfromtimestamp(max(tss)).isoformat() + "Z",
    ]
    prov["fills_by_period"] = dict(sorted(Counter(period_of(t) for t in tss).items()))
    prov["fetch_attempt_2026_10_05"] = {
        "collector": "polymarket_l5.cli fetch (canonical)",
        "raw_dir": "data/l5/raw_2026_10_05_attempt",
        "result": "status 0, transport error: Tunnel connection failed: 403 Forbidden",
        "hosts_blocked": [
            "data-api.polymarket.com:443",
            "gamma-api.polymarket.com:443",
            "clob.polymarket.com:443",
        ],
    }

    events = build_events(fills)
    metrics = [m for m in (event_metrics(s, fs) for s, fs in events.items()) if m]
    metrics.sort(key=lambda m: m["first_ts"])

    report = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "label": "historical replay / observational audit of ANOTHER TRADER'S public activity",
        "profit_attribution": (
            "NONE. No number here is realized profit for our account. All of it is "
            "public on-chain/API activity of Polymarket trader l5zn1bwom8etsk."
        ),
        "provenance": prov,
        "total_events": len(metrics),
        "by_period": group_stats(metrics, "period"),
        "by_day_recent": group_stats(
            [m for m in metrics if m["day"] >= "2026-09-01"], "day"
        ),
        "concurrency_by_period": concurrency(metrics),
        "prefix_by_period": {
            p: dict(Counter(m["prefix"] for m in metrics if m["period"] == p).most_common(12))
            for p in sorted({m["period"] for m in metrics})
        },
        "duration_by_period": {
            p: dict(Counter(m["duration"] for m in metrics if m["period"] == p).most_common(12))
            for p in sorted({m["period"] for m in metrics})
        },
        "hour_utc_by_period": {
            p: dict(sorted(Counter(m["hour_utc"] for m in metrics if m["period"] == p).items()))
            for p in sorted({m["period"] for m in metrics})
        },
    }

    # --- coverage holes: hour-level gaps per day ---------------------------
    hours_seen = defaultdict(set)
    for f in fills:
        d = dt.datetime.utcfromtimestamp(f["timestamp"])
        hours_seen[d.strftime("%Y-%m-%d")].add(d.hour)
    report["coverage_gaps_by_day"] = {
        d: {"hours_with_fills": len(hs), "missing_hours_utc": sorted(set(range(24)) - hs)}
        for d, hs in sorted(hours_seen.items())
        if len(hs) < 24
    }

    # --- cashflows: settlement + incentive records in the public feed -------
    cf = defaultdict(lambda: defaultdict(list))
    for c in cashflows:
        if not c.get("timestamp"):
            continue
        cf[period_of(c["timestamp"])][c.get("type") or "?"].append(c)
    report["cashflows_by_period"] = {
        p: {
            k: {
                "records": len(v),
                "usdc_size": describe([float(x.get("usdcSize") or 0) for x in v], 2),
            }
            for k, v in sorted(d.items())
        }
        for p, d in sorted(cf.items())
    }
    report["cashflow_note"] = (
        "REDEEM/REWARD/MAKER_REBATE/TAKER_REBATE are ANOTHER trader's settlement and "
        "incentive cashflows observed in the public feed. Not our realized P&L, and "
        "not evidence that equivalent incentives apply to our account."
    )

    # --- extra mechanics not in the baseline -------------------------------
    report["buy_vs_sell_by_period"] = {
        p: {
            "buy_fills": sum(
                1
                for f in fills
                if period_of(f["timestamp"]) == p
                and (f.get("side") or "").upper() == "BUY"
            ),
            "sell_fills": sum(
                1
                for f in fills
                if period_of(f["timestamp"]) == p
                and (f.get("side") or "").upper() == "SELL"
            ),
        }
        for p in sorted({period_of(f["timestamp"]) for f in fills})
    }
    # 1. price-level clustering of first acquisitions
    report["first_price_cents_top_by_period"] = {
        p: dict(
            Counter(
                round(m["first_price"], 2) for m in metrics if m["period"] == p
            ).most_common(10)
        )
        for p in sorted({m["period"] for m in metrics})
    }
    # 2. size ladder: is the Nth buy bigger or smaller than the 1st?
    ladder = defaultdict(lambda: defaultdict(list))
    for m in metrics:
        for i, s in enumerate(m["buy_sizes"][:6]):
            ladder[m["period"]][i + 1].append(s)
    report["buy_size_by_ordinal_by_period"] = {
        p: {str(k): describe(v, 1) for k, v in sorted(d.items())}
        for p, d in sorted(ladder.items())
    }
    # 3. round-lot / repeated size quantization
    report["buy_size_mode_by_period"] = {
        p: dict(
            Counter(
                round(s, 1) for m in metrics if m["period"] == p for s in m["buy_sizes"]
            ).most_common(10)
        )
        for p in sorted({m["period"] for m in metrics})
    }
    # 4. sells / side-switching presence
    report["sell_activity_by_period"] = {
        p: {
            "events": sum(1 for m in metrics if m["period"] == p),
            "events_with_sell": sum(
                1 for m in metrics if m["period"] == p and m["own_sells"] > 0
            ),
            "total_sell_fills": sum(
                m["own_sells"] for m in metrics if m["period"] == p
            ),
        }
        for p in sorted({m["period"] for m in metrics})
    }
    # 5. redemption / settlement behavior (Sep harvest only)
    if redeems:
        rts = [r["timestamp"] for r in redeems if r.get("timestamp")]
        report["redemption_sep"] = {
            "records": len(redeems),
            "date_range": [
                dt.datetime.utcfromtimestamp(min(rts)).isoformat() + "Z",
                dt.datetime.utcfromtimestamp(max(rts)).isoformat() + "Z",
            ],
            "usdc_size": describe([float(r.get("usdcSize") or 0) for r in redeems], 2),
            "size": describe([float(r.get("size") or 0) for r in redeems], 1),
            "per_day": dict(
                sorted(
                    Counter(
                        dt.datetime.utcfromtimestamp(r["timestamp"]).strftime("%Y-%m-%d")
                        for r in redeems
                        if r.get("timestamp")
                    ).items()
                )
            ),
            "note": "REDEEM is a settlement cashflow of ANOTHER trader; not our realized P&L.",
        }
    # 6. maker-vs-taker: trades_takeronly is a taker-only view of the same tape
    taker_keys = set()
    for rec in _envelope_rows(RAW / "trades_takeronly.jsonl", False):
        if rec.get("status") == 200:
            for x in rec.get("payload") or []:
                if x.get("name") == NAME:
                    taker_keys.add(
                        (x.get("transactionHash"), x.get("asset"), x.get("timestamp"))
                    )
    overlap_window = [
        f
        for f in fills
        if 1787875200 <= f["timestamp"] < 1788220000  # 2026-08-28 .. 2026-08-31
    ]
    if overlap_window:
        tk = sum(
            1
            for f in overlap_window
            if (f.get("transactionHash"), f.get("asset"), f.get("timestamp")) in taker_keys
        )
        report["maker_taker_proxy_aug28_31"] = {
            "fills_in_window": len(overlap_window),
            "also_in_taker_only_feed": tk,
            "taker_share_pct_LOWER_BOUND": round(100.0 * tk / len(overlap_window), 1),
            "caveat": (
                "LOWER BOUND ONLY, not an estimate. Derived from which fills also appear "
                "in the taker-only endpoint's cached pages for the same window. That feed "
                "is itself offset-capped and covers the window only partially, so any fill "
                "it missed is counted as maker here. The /activity feed carries no "
                "liquidity flag, so a true maker/taker split is not available from this "
                "corpus."
            ),
        }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "lane5_newest_activity_audit.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print("wrote", path)
    print("events", len(metrics), "fills", prov["deduplicated_fills"])
    print("range", prov["fill_date_range_utc"])
    for p, s in report["by_period"].items():
        print(
            f"{p} ev={s['events']:5d} fills={s['own_fills']:7d} "
            f"p1={s['first_price']['median']} fav={s['pct_favored']}% "
            f"T+={s['t_into_window_s']['median']} comp={s['pct_complemented']}% "
            f"t2c={s['time_to_complement_s']['median']} ext={s['extends_before_complement']['median']} "
            f"sz={s['per_fill_size']['median']}"
        )


if __name__ == "__main__":
    main()
