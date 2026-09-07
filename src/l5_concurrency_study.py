"""L5's OWN concurrency and market-switching behaviour, measured from the
canonical history.

OWNER DECISION #167 5564173746 (2026-09-07T02:23:51Z): "use the canonical L5
history to quantify L5's own concurrency and market-switching behavior:
simultaneous active markets over time, actions/fills per market episode, dwell
time, switches before inventory completion, overlap of residual books,
concurrency by era/turnover, and whether BTC/ETH 5m/15m behavior was
effectively concentrated or sprayed. Preserve source provenance."

This module only MEASURES. It never changes a cap, a rung, the concurrency the
live engine runs at, or any gate. Concurrency >1 is a separate money decision
for the owner; this is the evidence half of it.

PROVENANCE AND LIMITS (carried into the report, never dropped):
  * source: analysis/l5/l5_canonical_sequence.csv.gz (+ .meta.json sha), which
    is itself built from the public Polymarket activity feed. Every number here
    is derived from EXECUTED TRADES.
  * a fill is not a quote. L5 may have had resting orders in markets where it
    never traded; those are invisible to this feed. So every concurrency number
    below is a LOWER BOUND on L5's quoting concurrency and an exact measure of
    its FILL concurrency. Do not report it as the other thing.
  * maker/taker is asserted only inside the takerOnly feed's coverage window;
    outside it the label is "unknown" and is never treated as maker.
  * window_id <-> market_slug is 1:1 in this file, so one window is one market
    episode.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import statistics
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

CANONICAL_PATH = os.path.join("analysis", "l5", "l5_canonical_sequence.csv.gz")
META_PATH = os.path.join("analysis", "l5", "l5_canonical_sequence.meta.json")

# shares; residual below this is dust from float pricing, not inventory
RESIDUAL_TOL = 0.01


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_rows(path: str = CANONICAL_PATH) -> List[dict]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def provenance(rows: Sequence[dict], meta_path: str = META_PATH) -> dict:
    meta = {}
    try:
        with open(meta_path) as fh:
            meta = json.load(fh)
    except OSError:
        pass
    labels = Counter(r.get("maker_taker", "") for r in rows)
    return {
        "source_file": CANONICAL_PATH,
        "source_meta": meta_path,
        "content_sha256_16": meta.get("content_sha256_16"),
        "meta_rows": meta.get("rows"),
        "rows_read": len(rows),
        "upstream_sources": meta.get("sources", []),
        "maker_taker_counts": dict(labels),
        "limits": [
            "derived from EXECUTED TRADES on the public activity feed: a fill is not a quote",
            "concurrency here is FILL concurrency, a lower bound on quoting concurrency",
            "'unknown' maker_taker is outside takerOnly coverage and is never treated as maker",
        ],
    }


def episodes(rows: Iterable[dict]) -> List[dict]:
    """One entry per market episode (window), in first-trade order."""
    by_win: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_win[r["window_id"]].append(r)
    out: List[dict] = []
    for wid, rs in by_win.items():
        rs.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"])))
        first, last = rs[0], rs[-1]
        t_first, t_last = int(first["timestamp"]), int(last["timestamp"])
        settle = t_last + int(_f(last.get("seconds_to_settlement")))
        labels = Counter(r.get("maker_taker", "") for r in rs)
        resid = _f(last.get("residual_qty_signed"))
        out.append({
            "window_id": wid,
            "market_slug": last["market_slug"],
            "asset": last.get("asset", ""),
            "duration_minutes": int(_f(last.get("duration_minutes"))),
            "t_first": t_first,
            "t_last": t_last,
            "t_settlement": max(settle, t_last),
            "dwell_s": t_last - t_first,
            "actions": len(rs),
            "maker_actions": labels.get("maker", 0),
            "taker_actions": labels.get("taker", 0),
            "unknown_actions": labels.get("unknown", 0),
            "gross_dollars": round(sum(_f(r.get("dollars")) for r in rs), 6),
            "final_residual_qty_signed": round(resid, 6),
            "final_matched_qty": round(_f(last.get("matched_qty")), 6),
            "completed_inventory": abs(resid) <= RESIDUAL_TOL,
        })
    out.sort(key=lambda e: (e["t_first"], e["window_id"]))
    return out


def _sweep(intervals: Sequence[Tuple[int, int]]) -> Dict[int, int]:
    """Seconds spent at each simultaneous-interval count. Zero-length
    intervals contribute no time and are reported separately by the caller."""
    events: List[Tuple[int, int]] = []
    for a, b in intervals:
        if b > a:
            events.append((a, 1))
            events.append((b, -1))
    if not events:
        return {}
    events.sort()
    held: Dict[int, int] = defaultdict(int)
    level = 0
    prev = events[0][0]
    for t, delta in events:
        if t > prev and level > 0:
            held[level] += t - prev
        prev = t
        level += delta
    return dict(held)


def _profile(intervals: Sequence[Tuple[int, int]], label: str) -> dict:
    held = _sweep(intervals)
    active = sum(held.values())
    point = sum(1 for a, b in intervals if b <= a)
    share = {str(k): round(v / active, 6) for k, v in sorted(held.items())} if active else {}
    mean = (sum(k * v for k, v in held.items()) / active) if active else 0.0
    # time-weighted median concurrency
    med = 0
    seen = 0
    for k in sorted(held):
        seen += held[k]
        if active and seen >= active / 2:
            med = k
            break
    return {
        "label": label,
        "intervals": len(intervals),
        "zero_length_intervals": point,
        "active_seconds": active,
        "max_simultaneous": max(held) if held else 0,
        "mean_simultaneous_time_weighted": round(mean, 4),
        "median_simultaneous_time_weighted": med,
        "share_of_active_time_at_level": share,
        "share_at_1": share.get("1", 0.0),
        "share_at_2_or_more": round(sum(v for k, v in share.items() if int(k) >= 2), 6),
        "share_at_3_or_more": round(sum(v for k, v in share.items() if int(k) >= 3), 6),
        "seconds_at_level": {str(k): v for k, v in sorted(held.items())},
    }


def concurrency_profiles(eps: Sequence[dict]) -> dict:
    """Three readings of 'how many markets at once', each an honest question:

    trading  -- first fill to last fill: markets L5 was actively working.
    capital  -- first fill to settlement: markets holding L5's money.
    residual -- capital, restricted to episodes that ended with open inventory:
                the overlap of RESIDUAL books, which is the exposure our
                hold-to-settlement rule creates.
    """
    trading = [(e["t_first"], e["t_last"]) for e in eps]
    capital = [(e["t_first"], e["t_settlement"]) for e in eps]
    residual = [(e["t_first"], e["t_settlement"]) for e in eps if not e["completed_inventory"]]
    return {
        "trading": _profile(trading, "first fill -> last fill (actively worked)"),
        "capital": _profile(capital, "first fill -> settlement (money committed)"),
        "residual_books": _profile(residual, "episodes ending with open inventory"),
    }


def spray(rows: Iterable[dict], bucket_s: int) -> dict:
    """Distinct markets traded per fixed wall-clock bucket. This is the direct
    'concentrated or sprayed' reading: it needs no interval assumption."""
    buckets: Dict[int, set] = defaultdict(set)
    for r in rows:
        buckets[int(r["timestamp"]) // bucket_s].add(r["window_id"])
    counts = sorted(len(v) for v in buckets.values())
    n = len(counts)
    def q(p):
        return counts[min(n - 1, int(p * n))] if n else 0
    return {
        "bucket_seconds": bucket_s,
        "buckets_with_activity": n,
        "distinct_markets_per_bucket": {
            "mean": round(sum(counts) / n, 4) if n else 0.0,
            "p50": q(0.50), "p75": q(0.75), "p90": q(0.90), "p99": q(0.99),
            "max": counts[-1] if n else 0,
        },
        "share_of_buckets_with_one_market": round(
            sum(1 for c in counts if c == 1) / n, 6) if n else 0.0,
        "share_of_buckets_with_2_or_more": round(
            sum(1 for c in counts if c >= 2) / n, 6) if n else 0.0,
        "histogram": dict(sorted(Counter(counts).items())),
    }


def switching(rows: Sequence[dict], tol: float = RESIDUAL_TOL) -> dict:
    """Walk L5's action stream in time order and count how it moved between
    markets: runs of consecutive actions in one market, revisits, and the
    switches it made while that market still had OPEN inventory and more
    actions still to come there."""
    stream = sorted(rows, key=lambda r: (int(r["timestamp"]), r["window_id"],
                                         int(r["trade_seq"])))
    remaining: Counter = Counter(r["window_id"] for r in stream)
    runs: List[dict] = []
    switches_open_inventory = 0
    switches_flat = 0
    for r in stream:
        wid = r["window_id"]
        remaining[wid] -= 1
        if runs and runs[-1]["window_id"] == wid:
            runs[-1]["actions"] += 1
            runs[-1]["t_end"] = int(r["timestamp"])
            runs[-1]["residual_at_end"] = _f(r.get("residual_qty_signed"))
            runs[-1]["more_actions_later"] = remaining[wid] > 0
            continue
        runs.append({
            "window_id": wid, "actions": 1,
            "t_start": int(r["timestamp"]), "t_end": int(r["timestamp"]),
            "residual_at_end": _f(r.get("residual_qty_signed")),
            "more_actions_later": remaining[wid] > 0,
        })
    for prev in runs[:-1]:
        if prev["more_actions_later"] and abs(prev["residual_at_end"]) > tol:
            switches_open_inventory += 1
        else:
            switches_flat += 1
    visits: Counter = Counter(run["window_id"] for run in runs)
    revisited = sum(1 for v in visits.values() if v > 1)
    run_actions = sorted(run["actions"] for run in runs)
    gaps = [runs[i + 1]["t_start"] - runs[i]["t_end"] for i in range(len(runs) - 1)]
    gaps.sort()
    def med(xs):
        return statistics.median(xs) if xs else 0
    return {
        "actions": len(stream),
        "episodes": len(visits),
        "runs": len(runs),
        "switches": max(0, len(runs) - 1),
        "switches_leaving_open_inventory": switches_open_inventory,
        "switches_leaving_nothing_to_return_to": switches_flat,
        "share_of_switches_leaving_open_inventory": round(
            switches_open_inventory / max(1, len(runs) - 1), 6),
        "episodes_revisited_after_leaving": revisited,
        "share_of_episodes_revisited": round(revisited / max(1, len(visits)), 6),
        "visits_per_episode": {
            "mean": round(len(runs) / max(1, len(visits)), 4),
            "p50": med(sorted(visits.values())),
            "max": max(visits.values()) if visits else 0,
        },
        "actions_per_run": {
            "mean": round(len(stream) / max(1, len(runs)), 4),
            "p50": med(run_actions),
            "p90": run_actions[min(len(run_actions) - 1, int(0.9 * len(run_actions)))] if run_actions else 0,
            "max": run_actions[-1] if run_actions else 0,
        },
        "seconds_between_runs": {"p50": med(gaps),
                                 "p90": gaps[min(len(gaps) - 1, int(0.9 * len(gaps)))] if gaps else 0},
        "note": "ties inside one second are ordered by window_id, so run counts "
                "are a lower bound on interleaving at sub-second resolution",
    }


def _dist(xs: Sequence[float]) -> dict:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return {"n": 0}
    def q(p):
        return xs[min(n - 1, int(p * n))]
    return {"n": n, "mean": round(sum(xs) / n, 4), "p10": q(0.10), "p25": q(0.25),
            "p50": q(0.50), "p75": q(0.75), "p90": q(0.90), "max": xs[-1]}


def episode_shape(eps: Sequence[dict]) -> dict:
    return {
        "episodes": len(eps),
        "actions_per_episode": _dist([e["actions"] for e in eps]),
        "dwell_seconds": _dist([e["dwell_s"] for e in eps]),
        "gross_dollars_per_episode": _dist([e["gross_dollars"] for e in eps]),
        "single_action_episodes": sum(1 for e in eps if e["actions"] == 1),
        "episodes_completing_inventory": sum(1 for e in eps if e["completed_inventory"]),
        "share_completing_inventory": round(
            sum(1 for e in eps if e["completed_inventory"]) / max(1, len(eps)), 6),
        "abs_final_residual": _dist([abs(e["final_residual_qty_signed"]) for e in eps]),
    }


def by_cohort(rows: Sequence[dict], eps: Sequence[dict], key) -> dict:
    """Split every measure by a cohort key over episodes (era, asset/duration)."""
    ep_groups: Dict[str, List[dict]] = defaultdict(list)
    for e in eps:
        ep_groups[key(e)].append(e)
    wid_group = {e["window_id"]: key(e) for e in eps}
    row_groups: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        row_groups[wid_group[r["window_id"]]].append(r)
    out = {}
    for name in sorted(ep_groups):
        g_eps, g_rows = ep_groups[name], row_groups[name]
        span = max(e["t_last"] for e in g_eps) - min(e["t_first"] for e in g_eps)
        out[name] = {
            "episodes": len(g_eps),
            "actions": len(g_rows),
            "gross_dollars": round(sum(e["gross_dollars"] for e in g_eps), 2),
            "span_days": round(span / 86400.0, 2),
            "episodes_per_active_day": round(len(g_eps) / max(1.0, span / 86400.0), 2),
            "concurrency": concurrency_profiles(g_eps),
            "shape": episode_shape(g_eps),
            "switching": switching(g_rows),
        }
    return out


def coverage(eps: Sequence[dict]) -> dict:
    """Which calendar days the harvested history actually covers. The file
    spans March to August but is NOT continuous: silence in it is missing
    data, never evidence that L5 was idle."""
    import datetime as _dt
    days = sorted({_dt.datetime.utcfromtimestamp(e["t_first"]).strftime("%Y-%m-%d")
                   for e in eps})
    gaps = []
    for a, b in zip(days, days[1:]):
        da, db = _dt.date.fromisoformat(a), _dt.date.fromisoformat(b)
        if (db - da).days > 1:
            gaps.append({"after": a, "before": b, "days": (db - da).days})
    return {
        "distinct_trading_days": len(days),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "gaps_over_one_day": gaps,
        "note": "a gap is absence of harvested data, not proof of absence of trading",
    }


def _week(e: dict) -> str:
    import datetime as _dt
    d = _dt.datetime.utcfromtimestamp(e["t_first"])
    return (d - _dt.timedelta(days=d.weekday())).strftime("%Y-W%W (%Y-%m-%d)")


def _era(e: dict) -> str:
    import datetime as _dt
    return _dt.datetime.utcfromtimestamp(e["t_first"]).strftime("%Y-%m")


def _lane(e: dict) -> str:
    return "%s-%dm" % (e["asset"], e["duration_minutes"])


def study(rows: Optional[Sequence[dict]] = None, path: str = CANONICAL_PATH) -> dict:
    rows = list(rows) if rows is not None else load_rows(path)
    eps = episodes(rows)
    return {
        "owner_decision": "#167 5564173746",
        "provenance": provenance(rows),
        "coverage": coverage(eps),
        "overall": {
            "episodes": len(eps),
            "actions": len(rows),
            "span_utc": [eps[0]["t_first"], eps[-1]["t_last"]] if eps else [],
            "concurrency": concurrency_profiles(eps),
            "shape": episode_shape(eps),
            "switching": switching(rows),
            "lane_concurrency": {
                "trading": lane_concurrency(eps, "trading"),
                "capital": lane_concurrency(eps, "capital"),
            },
            "overlap_composition": {
                "trading": overlap_composition(eps, "trading"),
                "capital": overlap_composition(eps, "capital"),
            },
            "spray_5m": spray(rows, 300),
            "spray_15m": spray(rows, 900),
            "spray_1h": spray(rows, 3600),
        },
        "by_era": by_cohort(rows, eps, _era),
        "by_week": by_cohort(rows, eps, _week),
        "by_lane": by_cohort(rows, eps, _lane),
    }


def overlap_composition(eps: Sequence[dict], mode: str = "trading") -> dict:
    """When two markets were open at once, WERE THEY THE SAME PRODUCT?

    A BTC-5m window and a BTC-15m window overlap by construction -- they are
    different products on the same clock -- so 'concurrency 2' could be a
    structural artefact rather than a decision to spray. This pairs every
    overlapping episode and splits the overlap seconds into same-lane
    (two markets in the SAME asset/duration lane running at once: a genuine
    parallel-inventory choice) and cross-lane.
    """
    end = "t_last" if mode == "trading" else "t_settlement"
    ivs = sorted(((e["t_first"], e[end], _lane(e), e["window_id"]) for e in eps),
                 key=lambda x: x[0])
    by_pair: Dict[str, int] = defaultdict(int)
    same = cross = 0
    active: List[tuple] = []
    for a, b, lane, wid in ivs:
        active = [x for x in active if x[1] > a]
        for oa, ob, olane, owid in active:
            secs = min(ob, b) - a
            if secs <= 0:
                continue
            key = " + ".join(sorted((olane, lane)))
            by_pair[key] += secs
            if olane == lane:
                same += secs
            else:
                cross += secs
        if b > a:
            active.append((a, b, lane, wid))
    total = same + cross
    return {
        "mode": mode,
        "pairwise_overlap_seconds": total,
        "same_lane_seconds": same,
        "cross_lane_seconds": cross,
        "same_lane_share": round(same / total, 6) if total else 0.0,
        "by_lane_pair_seconds": dict(sorted(by_pair.items(), key=lambda kv: -kv[1])),
    }


def lane_concurrency(eps: Sequence[dict], mode: str = "trading") -> dict:
    """Concurrency counted in PRODUCT LANES rather than markets.

    Same-lane overlap is impossible on this venue: a BTC-5m window's life is
    five minutes and the next one begins after it, so within a lane L5 could
    only ever be in one market at a time. Its concurrency was therefore a
    choice of HOW MANY LANES to run, each lane sequential. Counting lanes is
    the number that transfers to a slate of independent sports markets;
    counting markets is not.
    """
    end = "t_last" if mode == "trading" else "t_settlement"
    per_lane: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    for e in eps:
        per_lane[_lane(e)].append((e["t_first"], e[end]))
    # a lane is "active" over the union of its episodes' intervals
    lane_ivs: List[Tuple[int, int]] = []
    max_in_lane = 0
    for lane, ivs in per_lane.items():
        ivs.sort()
        cur_a, cur_b = ivs[0]
        overlaps = 0
        for a, b in ivs[1:]:
            if a < cur_b:
                overlaps += 1
            if a <= cur_b:
                cur_b = max(cur_b, b)
            else:
                lane_ivs.append((cur_a, cur_b))
                cur_a, cur_b = a, b
        lane_ivs.append((cur_a, cur_b))
        max_in_lane = max(max_in_lane, 1 + (1 if overlaps else 0))
    prof = _profile(lane_ivs, "simultaneous product LANES (%s)" % mode)
    prof["lanes"] = sorted(per_lane)
    prof["max_markets_open_within_one_lane"] = max_in_lane
    return prof
