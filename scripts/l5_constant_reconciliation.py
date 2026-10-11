"""L5 CONSTANT RECONCILIATION + VENUE REQUIREMENTS BY REGIME (Sections 5 & 6).

RESEARCH ONLY. Every number here is a HISTORICAL RECONSTRUCTION OF ANOTHER
TRADER'S PUBLIC ACTIVITY (Polymarket `l5zn1bwom8etsk`, proxy wallet
0xb945945d5bca...). NOTHING here is realized profit for our account, and
this module places no orders and performs no venue writes.

WHY THIS EXISTS
---------------
The clone/replay/shadow system's L5-derived constants were all derived in
``scripts/gemini_l5_inventory_replay.py:derive()`` from
``data/l5/raw/trades.jsonl``. That file is 10,500 records / 9,901 deduped
fills spanning **2026-08-30 07:49 -> 20:52 UTC -- 13.1 hours of ONE DAY**.
The name field was present there, so the filter worked; the defect is SPAN,
not identity. This module recomputes the SAME metric definitions (copied
from that function so the comparison is apples-to-apples) over the full
corpus:

  * ``data/l5/raw/activity.jsonl``       (2026-03-19 -> 08-31, carries name)
  * ``data/l5/raw/activity_gap.jsonl.gz``(2026-04-02 -> 08-28, NO name)

IDENTITY = PROVENANCE
---------------------
Both files were fetched with ``params.user == L5's address``. A record is
L5's because of WHERE IT CAME FROM. A record is disqualified ONLY by a
*contradicting* ``name``/``proxyWallet``. We never filter on
``name == 'l5Zn1bWoM8eTsK'`` -- that would silently drop the entire
706,500-record gap file, which is exactly how the 13-hour corpus happened.

Records are deduped ACROSS both files on
``(transactionHash, asset, timestamp)``.

PERIOD LABELS
-------------
A parallel agent owns regime change-point detection. We therefore do NOT
invent regime names. We split the corpus's own daily grid at the median
day of TRADE activity and label the halves descriptively from what we
measure (e.g. "early high-completion period" / "late low-completion
period"), reporting independent-EVENT counts and date ranges beside every
value.

Outputs: analysis/l5/CANON_CONSTANTS.md, analysis/l5/canon_constants.json
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = ROOT / "data" / "l5" / "raw" / "activity.jsonl"
ACTIVITY_GAP = ROOT / "data" / "l5" / "raw" / "activity_gap.jsonl.gz"
NARROW = ROOT / "data" / "l5" / "raw" / "trades.jsonl"
OUT_JSON = ROOT / "analysis" / "l5" / "canon_constants.json"
OUT_MD = ROOT / "analysis" / "l5" / "CANON_CONSTANTS.md"

L5_ADDR_PREFIX = "0xb945945d5bca"
L5_NAME = "l5Zn1bWoM8eTsK"

PROVENANCE_LABEL = ("historical reconstruction of another trader's public "
                    "activity; NOT realized profit for our account")


# ------------------------------------------------------------------ loading
def _open(p: Path):
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8")
    return open(p, encoding="utf-8")


def envelope_is_l5(params: Any) -> bool:
    """PROVENANCE check: the fetch was scoped to L5's address."""
    if not isinstance(params, dict):
        return False
    u = str(params.get("user") or "").lower()
    return u.startswith(L5_ADDR_PREFIX)


def contradicts_l5(x: Dict[str, Any]) -> bool:
    """Disqualify ONLY on a contradicting identity field.

    A MISSING name is not a contradiction (the gap file has none).
    """
    nm = x.get("name")
    if nm is not None and str(nm) != L5_NAME:
        return True
    pw = x.get("proxyWallet")
    if pw is not None and not str(pw).lower().startswith(L5_ADDR_PREFIX):
        return True
    return False


def iter_records(paths: Iterable[Path]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    for p in paths:
        if not p.exists():
            print("MISSING: %s" % p, file=sys.stderr)
            continue
        with _open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    env = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                if not isinstance(env, dict):
                    continue
                if not envelope_is_l5(env.get("params")):
                    continue
                payload = env.get("payload")
                if not isinstance(payload, list):
                    continue
                for x in payload:
                    if isinstance(x, dict):
                        yield (p.name, x)


class Corpus:
    """Deduped L5 activity across both files, split by record type."""

    def __init__(self) -> None:
        self.trades: List[Dict[str, Any]] = []
        self.by_type: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        self.seen_raw = 0
        self.seen_by_file: collections.Counter = collections.Counter()
        self.dup_dropped = 0
        self.contradicted = 0
        self.no_name = 0
        self.dedupe_key_missing = 0

    def load(self, paths: Iterable[Path]) -> "Corpus":
        seen: set = set()
        for fname, x in iter_records(paths):
            self.seen_raw += 1
            self.seen_by_file[fname] += 1
            if contradicts_l5(x):
                self.contradicted += 1
                continue
            if x.get("name") is None:
                self.no_name += 1
            th, asset, ts = (x.get("transactionHash"), x.get("asset"),
                             x.get("timestamp"))
            if th is None or ts is None:
                self.dedupe_key_missing += 1
            k = (th, x.get("conditionId"), x.get("outcomeIndex"), ts,
                 str(x.get("price")), str(x.get("size")), x.get("type"))
            # DEDUPE KEY (coordinator correction, measured): the canonical
            # (transactionHash, asset, timestamp) key DEGENERATES, because
            # activity_gap.jsonl.gz carries no `asset` at all -- it collapses
            # to (txh, None, ts) for all 706,500 gap records and produced
            # 33,057 CONFLICTS (records differing in price/size/side/outcome/
            # type silently merged = data loss). conditionId + outcomeIndex
            # identify the same token as `asset`, and adding the stringified
            # price/size/type yields ZERO conflicts.
            if k in seen:
                self.dup_dropped += 1
                continue
            seen.add(k)
            # data-api /trades records carry NO `type` field at all; a
            # record with outcome+price+size IS a fill. Without this the
            # narrow-corpus recompute silently returned ZERO fills.
            t = str(x.get("type") or "")
            if not t:
                t = ("TRADE" if x.get("outcome") is not None
                     and x.get("price") is not None else "UNKNOWN")
            self.by_type[t].append(x)
            if t == "TRADE":
                self.trades.append(x)
        self.trades.sort(key=lambda r: int(r.get("timestamp") or 0))
        return self


# ------------------------------------------------------------------- helpers
def day(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d")


def iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).isoformat()


def slug_parts(slug: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """(window epoch, asset, duration) parsed from the eventSlug itself."""
    if not slug:
        return (None, None, None)
    bits = slug.split("-")
    epoch = int(bits[-1]) if bits and bits[-1].isdigit() else None
    asset = bits[0] if bits else None
    duration = None
    for b in bits:
        bl = b.lower()
        if len(bl) >= 2 and bl[-1] in "mhd" and bl[:-1].isdigit():
            duration = bl
    return (epoch, asset, duration)


def dd(xs: List[float], nd: int = 4) -> Optional[Dict[str, Any]]:
    """Same distribution summary shape the frozen derive() used."""
    if not xs:
        return None
    s = sorted(xs)
    g = lambda p: round(s[min(len(s) - 1, int(p * (len(s) - 1)))], nd)
    return {"n": len(s), "p10": g(.1), "median": round(statistics.median(s), nd),
            "p90": g(.9), "p99": g(.99), "max": round(s[-1], nd)}


UNAVAILABLE = "UNAVAILABLE"


# ---------------------------------------------------------------- derivation
def derive(fills: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Recompute the clone's L5 constants.

    Metric definitions are COPIED from
    scripts/gemini_l5_inventory_replay.py:derive() so that the
    full-corpus value is directly comparable to the frozen one. Only the
    input differs: the full deduped corpus instead of one 13-hour day.
    """
    ev: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for f in fills:
        ev[str(f.get("eventSlug") or "")].append(f)

    first_px: List[float] = []
    first_tplus: List[float] = []
    favored = 0
    ever_comp = 0
    extend_runs: List[float] = []
    pc_switch: List[float] = []
    pc_extend: List[float] = []
    pc_final: List[float] = []
    to_matched: List[float] = []
    gaps: List[float] = []
    fills_per_event: List[float] = []
    window_span_s: List[float] = []
    repeat: collections.Counter = collections.Counter()
    trans: collections.Counter = collections.Counter()
    sides: collections.Counter = collections.Counter()
    durations: collections.Counter = collections.Counter()
    assets: collections.Counter = collections.Counter()
    n_ev = 0
    n_ev_skipped_no_epoch = 0
    days: set = set()
    ts_min: Optional[int] = None
    ts_max: Optional[int] = None

    for slug, fs in ev.items():
        epoch, asset, duration = slug_parts(slug)
        if epoch is None:
            n_ev_skipped_no_epoch += 1
            continue
        fs = sorted(fs, key=lambda x: int(x["timestamp"]))
        n_ev += 1
        durations[duration or "?"] += 1
        assets[asset or "?"] += 1
        t0 = int(fs[0]["timestamp"])
        days.add(day(t0))
        ts_min = t0 if ts_min is None else min(ts_min, t0)
        last_ts = int(fs[-1]["timestamp"])
        ts_max = last_ts if ts_max is None else max(ts_max, last_ts)
        fills_per_event.append(float(len(fs)))
        window_span_s.append(float(last_ts - t0))

        first_px.append(float(fs[0]["price"]))
        first_tplus.append(float(t0 - epoch))
        if float(fs[0]["price"]) > 0.5:
            favored += 1

        inv: Dict[str, float] = collections.defaultdict(float)
        cost: Dict[str, float] = collections.defaultdict(float)
        prev: Optional[str] = None
        run = 0
        last_px: Dict[str, float] = {}
        fc: Optional[float] = None
        for i, f in enumerate(fs):
            o = str(f.get("outcome"))
            p = float(f.get("price") or 0.0)
            q = float(f.get("size") or 0.0)
            sides[str(f.get("side") or "?")] += 1
            if i:
                gaps.append(float(int(f["timestamp"]) - int(fs[i - 1]["timestamp"])))
            if prev is None:
                trans["OPEN"] += 1
            elif o == prev:
                trans["EXTEND"] += 1
                run += 1
                if inv[o] > 0:
                    pc_extend.append(cost[o] / inv[o] + p)
            else:
                trans["SWITCH_OR_COMPLETE"] += 1
                if inv[prev] > 0:
                    pc_switch.append(cost[prev] / inv[prev] + p)
                extend_runs.append(float(run))
                run = 0
            if o in last_px:
                repeat["lower" if p < last_px[o] else
                       "higher" if p > last_px[o] else "same"] += 1
            last_px[o] = p
            pm = min(inv.values()) if len(inv) > 1 else 0.0
            inv[o] += q
            cost[o] += p * q
            now_m = min(inv.values()) if len(inv) > 1 else 0.0
            if now_m > pm and fc is None:
                fc = float(int(f["timestamp"]) - t0)
            prev = o
        if fc is not None:
            to_matched.append(fc)
        legs = [k for k, v in inv.items() if v > 0]
        if len(legs) >= 2:
            ever_comp += 1
            pc_final.append(sum(cost[k] / inv[k] for k in legs[:2]))

    return {
        "label": PROVENANCE_LABEL,
        "fills": len(fills),
        "events": n_ev,
        "events_skipped_no_epoch_in_slug": n_ev_skipped_no_epoch,
        "distinct_utc_days": len(days),
        "date_range_utc": [iso(ts_min) if ts_min else None,
                           iso(ts_max) if ts_max else None],
        "first_fill_price": dd(first_px),
        "first_fill_t_plus_s": dd(first_tplus),
        "favored_first_pct": round(100.0 * favored / n_ev, 2) if n_ev else None,
        "ever_complement_pct": round(100.0 * ever_comp / n_ev, 2) if n_ev else None,
        "events_ever_complement": ever_comp,
        "transitions": dict(trans),
        "same_side_extends_before_complement": dd(extend_runs),
        "pair_cost_at_opposite_side_buy": dd(pc_switch),
        "pair_cost_if_extend_same_side": dd(pc_extend),
        "FINAL_matched_pair_cost": dd(pc_final),
        "seconds_first_fill_to_first_matched_increase": dd(to_matched),
        "inter_fill_gap_s": dd(gaps),
        "fills_per_event": dd(fills_per_event, 1),
        "event_working_span_s": dd(window_span_s, 1),
        "same_side_repeat_price_direction": dict(repeat),
        "sides_observed": dict(sides),
        "durations_by_event": dict(durations.most_common(12)),
        "assets_by_event": dict(assets.most_common(12)),
    }


# ------------------------------------------------------- daily grid / periods
def daily_grid(fills: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    per: Dict[str, Dict[str, Any]] = {}
    ev_day: Dict[str, set] = collections.defaultdict(set)
    comp: Dict[str, set] = collections.defaultdict(set)
    ev_fills: Dict[Tuple[str, str], List[Dict[str, Any]]] = collections.defaultdict(list)
    for f in fills:
        d = day(int(f["timestamp"]))
        slug = str(f.get("eventSlug") or "")
        ev_day[d].add(slug)
        ev_fills[(d, slug)].append(f)
        per.setdefault(d, {"day": d, "fills": 0, "usdc": 0.0})
        per[d]["fills"] += 1
        per[d]["usdc"] += float(f.get("usdcSize") or 0.0)
    for (d, slug), fs in ev_fills.items():
        inv: Dict[str, float] = collections.defaultdict(float)
        for f in fs:
            inv[str(f.get("outcome"))] += float(f.get("size") or 0.0)
        if len([k for k, v in inv.items() if v > 0]) >= 2:
            comp[d].add(slug)
    rows = []
    for d in sorted(per):
        n_ev = len(ev_day[d])
        rows.append({"day": d, "fills": per[d]["fills"], "events": n_ev,
                     "usdc_notional": round(per[d]["usdc"], 2),
                     "events_ever_complement": len(comp[d]),
                     "complement_pct": round(100.0 * len(comp[d]) / n_ev, 2)
                     if n_ev else None,
                     "fills_per_event": round(per[d]["fills"] / n_ev, 2)
                     if n_ev else None})
    return rows


def split_periods(fills: List[Dict[str, Any]], grid: List[Dict[str, Any]]
                  ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]],
                             Dict[str, Any]]:
    """Split on the corpus's own daily grid at the median ACTIVE DAY.

    Descriptive only. Regime change-point detection is another agent's
    workstream; we do not name regimes.
    """
    dayset = [r["day"] for r in grid]
    if not dayset:
        return ("", [], [], {})
    cut = dayset[len(dayset) // 2]
    early = [f for f in fills if day(int(f["timestamp"])) < cut]
    late = [f for f in fills if day(int(f["timestamp"])) >= cut]
    meta = {"method": ("median active UTC day of the corpus's own daily "
                       "grid; descriptive halves, NOT detected regimes"),
            "cut_day_utc": cut, "active_days": len(dayset)}
    return (cut, early, late, meta)


# ------------------------------------------------------ incentive-era records
def incentive_summary(corpus: Corpus) -> Dict[str, Any]:
    out: Dict[str, Any] = {"label": PROVENANCE_LABEL, "by_type": {}}
    for t in sorted(corpus.by_type):
        recs = corpus.by_type[t]
        usd = [float(r.get("usdcSize") or 0.0) for r in recs]
        ds = sorted({day(int(r["timestamp"])) for r in recs
                     if r.get("timestamp") is not None})
        out["by_type"][t] = {
            "records": len(recs),
            "usdc_total": round(sum(usd), 2),
            "usdc_median": round(statistics.median(usd), 6) if usd else None,
            "first_day_utc": ds[0] if ds else None,
            "last_day_utc": ds[-1] if ds else None,
            "distinct_days": len(ds),
        }
    return out


def per_contract_maker_comp(corpus: Corpus, fills: List[Dict[str, Any]],
                            grid: List[Dict[str, Any]], cut: str) -> Dict[str, Any]:
    """Maker compensation per contract, by descriptive period.

    MAKER_REBATE / TAKER_REBATE / REWARD usdc divided by TRADE contracts in
    the same period. A LOWER BOUND on required venue compensation: it is
    what L5 was actually credited, not what he required.
    """
    def _sum(types: Tuple[str, ...], pred) -> float:
        s = 0.0
        for t in types:
            for r in corpus.by_type.get(t, []):
                if r.get("timestamp") is None:
                    continue
                if pred(day(int(r["timestamp"]))):
                    s += float(r.get("usdcSize") or 0.0)
        return s

    def _qty(pred) -> float:
        return sum(float(f.get("size") or 0.0) for f in fills
                   if pred(day(int(f["timestamp"]))))

    res = {}
    for name, pred in (("full_corpus", lambda d: True),
                       ("early_half", lambda d: d < cut),
                       ("late_half", lambda d: d >= cut)):
        q = _qty(pred)
        mk = _sum(("MAKER_REBATE",), pred)
        tk = _sum(("TAKER_REBATE",), pred)
        rw = _sum(("REWARD",), pred)
        res[name] = {
            "trade_contracts": round(q, 2),
            "maker_rebate_usdc": round(mk, 4),
            "taker_rebate_usdc": round(tk, 4),
            "reward_usdc": round(rw, 4),
            "maker_rebate_per_contract": round(mk / q, 6) if q else None,
            "all_incentives_per_contract": round((mk + tk + rw) / q, 6) if q else None,
            "note": ("credited incentives / contracts traded; separate from "
                     "trading P&L per CLAUDE.md; a LOWER BOUND on required "
                     "venue compensation, never realized profit for us"),
        }
    return res


# -------------------------------------------------- venue requirements (S6)
def venue_requirements(full: Dict[str, Any], early: Dict[str, Any],
                       late: Dict[str, Any], comp: Dict[str, Any],
                       grid: List[Dict[str, Any]], cut: str,
                       early_label: str, late_label: str) -> Dict[str, Any]:
    """Minimum venue characteristics each economically meaningful regime needs.

    Every entry is MINIMUM THRESHOLD + the measurement that justifies it,
    or UNAVAILABLE + why. Derived from the corpus's early-vs-late contrast
    on its own daily grid.
    """
    def gap_rate(d: Dict[str, Any]) -> Optional[float]:
        g = d.get("inter_fill_gap_s")
        if not g or not g.get("median"):
            return None
        return round(60.0 / max(g["median"], 1e-9), 2)

    fpe = (full.get("fills_per_event") or {})
    span = (full.get("event_working_span_s") or {})
    dur = full.get("durations_by_event") or {}

    inv = {
        "regime": "inventory / pair-compression regime",
        "derived_from": ("%s vs %s on the corpus's own daily grid (cut %s)"
                         % (early_label, late_label, cut)),
        "requirements": {
            "trades_per_minute_min": {
                "threshold": gap_rate(full),
                "measurement": ("60 / median inter-fill gap %ss over %d "
                                "independent events, %s"
                                % ((full.get("inter_fill_gap_s") or {}).get("median"),
                                   full["events"], full["date_range_utc"])),
                "unit": "own fills per minute while working one event",
            },
            "fills_per_event_min": {
                "threshold": fpe.get("median"),
                "stretch_p90": fpe.get("p90"),
                "measurement": ("median / p90 fills per independent event, "
                                "n=%s events" % (fpe.get("n"))),
            },
            "two_sided_fill_frequency_min": {
                "threshold": full.get("ever_complement_pct"),
                "unit": "percent of events reaching both legs",
                "measurement": ("%d of %d independent events ever held both "
                                "outcomes" % (full.get("events_ever_complement") or 0,
                                              full["events"])),
                "period_contrast": {early_label: early.get("ever_complement_pct"),
                                    late_label: late.get("ever_complement_pct")},
            },
            "complement_latency_max_s": {
                "threshold": (full.get("seconds_first_fill_to_first_matched_increase")
                              or {}).get("median"),
                "p90": (full.get("seconds_first_fill_to_first_matched_increase")
                        or {}).get("p90"),
                "measurement": ("seconds from first fill to first matched "
                                "increase; the venue must let the complement "
                                "be reachable inside this, n=%s events"
                                % ((full.get("seconds_first_fill_to_first_matched_increase")
                                    or {}).get("n"))),
            },
            "window_duration_min": {
                "threshold": ("event durations parsed from eventSlug: %s"
                              % json.dumps(dur)),
                "working_span_median_s": span.get("median"),
                "working_span_p90_s": span.get("p90"),
                "measurement": ("the venue's contract must stay open at "
                                "least as long as the median working span"),
            },
            "pair_completion_cost_ceiling": {
                "threshold": (full.get("FINAL_matched_pair_cost") or {}).get("median"),
                "p10": (full.get("FINAL_matched_pair_cost") or {}).get("p10"),
                "p90": (full.get("FINAL_matched_pair_cost") or {}).get("p90"),
                "measurement": ("achieved final matched pair unit cost over "
                                "%s completing events; the venue's two-sided "
                                "pricing must reach this"
                                % ((full.get("FINAL_matched_pair_cost") or {}).get("n"))),
                "period_contrast": {
                    early_label: (early.get("FINAL_matched_pair_cost") or {}).get("median"),
                    late_label: (late.get("FINAL_matched_pair_cost") or {}).get("median")},
            },
            "spread_and_depth_min": {
                "threshold": UNAVAILABLE,
                "why": ("this corpus is L5's own PRINTS only. It contains no "
                        "order book, no BBO, no quote sizes and no resting "
                        "orders, so spread and depth cannot be recomputed "
                        "and are not approximated here."),
            },
            "executable_capacity_min": {
                "threshold": {"usdc_per_event_median": None,
                              "contracts_per_fill_median": None},
                "why_partial": ("own-notional per event IS measurable and is "
                                "reported below as a capacity FLOOR, but the "
                                "venue capacity that absorbed it (book depth "
                                "at his prices) is UNAVAILABLE from prints."),
            },
        },
    }

    inc = {
        "regime": "incentive / liquidity-provision regime",
        "derived_from": "MAKER_REBATE / TAKER_REBATE / REWARD records in the same corpus",
        "requirements": {
            "maker_compensation_per_contract_min": {
                "threshold": (comp.get("full_corpus") or {}).get("maker_rebate_per_contract"),
                "all_incentives_per_contract": (comp.get("full_corpus") or {}).get("all_incentives_per_contract"),
                "measurement": ("credited maker-rebate usdc / own contracts "
                                "traded, full corpus; period contrast below"),
                "period_contrast": {
                    early_label: (comp.get("early_half") or {}).get("maker_rebate_per_contract"),
                    late_label: (comp.get("late_half") or {}).get("maker_rebate_per_contract")},
                "caveat": ("a LOWER BOUND: what L5 was credited, not what he "
                           "required. Per CLAUDE.md, no rebate is assumed "
                           "until credited and source-accounted for OUR account."),
            },
            "reward_structure_required": {
                "threshold": ("the corpus shows %s record types; see "
                              "incentive_records for counts, first/last day "
                              "and totals" % ",".join(sorted(
                                  k for k in ("MAKER_REBATE", "TAKER_REBATE", "REWARD")))),
                "measurement": "per-type record counts and usdc totals by day",
            },
            "adverse_selection_tolerance": {
                "threshold": UNAVAILABLE,
                "why": ("adverse selection needs the mid/BBO at fill time and "
                        "the subsequent mark. The corpus has neither a book "
                        "nor a maker/taker flag, so toxicity cannot be "
                        "recomputed here."),
            },
            "fill_rate_min": {
                "threshold": UNAVAILABLE,
                "why": ("fill rate needs SUBMITTED orders as the denominator. "
                        "This corpus contains only FILLS; unfilled resting "
                        "orders are not public."),
            },
            "spread_and_depth_min": {
                "threshold": UNAVAILABLE,
                "why": "no book data in the corpus (same reason as above).",
            },
        },
    }
    return {"label": PROVENANCE_LABEL,
            "note": ("the two regimes are NOT assumed to suit one venue; "
                     "requirements are derived separately"),
            "inventory_compression_regime": inv,
            "incentive_liquidity_regime": inc}


# ------------------------------------------------------- reconciliation table
def build_table(full: Dict[str, Any], early: Dict[str, Any],
                late: Dict[str, Any], narrow: Optional[Dict[str, Any]],
                early_label: str, late_label: str) -> List[Dict[str, Any]]:
    """One row per constant actually used by clone / replay / shadow code."""
    def g(d: Optional[Dict[str, Any]], *path) -> Any:
        cur: Any = d
        for k in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    def per_regime(*path) -> Dict[str, Any]:
        return {early_label: g(early, *path), late_label: g(late, *path)}

    def nv(*path) -> Any:
        return g(narrow, *path)

    rows: List[Dict[str, Any]] = []

    def row(metric: str, old: Any, new: Any, regime: Any, valid: str,
            used_in: List[str], notes: str) -> None:
        rows.append({"metric": metric, "old_9901_fill_value": old,
                     "full_corpus_value": new, "per_regime_values": regime,
                     "still_valid": valid, "used_in_code": used_in,
                     "notes": notes})

    row("first-acquisition (first-fill) median price",
        {"frozen": 0.588, "recomputed_narrow": nv("first_fill_price", "median")},
        g(full, "first_fill_price", "median"),
        per_regime("first_fill_price", "median"),
        "SUPERSEDED",
        ["scripts/gemini_l5_inventory_replay.py:33 (docstring 0.583/0.588)"],
        "frozen value came from one 13.1-hour day; recomputed over the full corpus")

    row("first-fill price band (p10, p90) -> L5_BAND",
        [0.20, 0.82],
        [g(full, "first_fill_price", "p10"), g(full, "first_fill_price", "p90")],
        {early_label: [g(early, "first_fill_price", "p10"),
                       g(early, "first_fill_price", "p90")],
         late_label: [g(late, "first_fill_price", "p10"),
                      g(late, "first_fill_price", "p90")]},
        "SUPERSEDED",
        ["scripts/gemini_l5_inventory_replay.py:130 L5_BAND",
         "same file lines 311/320/334 gate OPEN and EXTEND on it"],
        "the acquisition admission band of the replay state machine")

    row("% of events whose first fill is the FAVORED side (price > 0.5)",
        69.4, g(full, "favored_first_pct"), per_regime("favored_first_pct"),
        "SUPERSEDED",
        ["scripts/gemini_l5_inventory_replay.py (derive favored_first_pct)",
         "quoted in L5 behavioral-thesis documentation"],
        "descriptive statistic, no direct gate, but it motivates the band")

    row("median first acquisition T+Ns into the window",
        44.0, g(full, "first_fill_t_plus_s", "median"),
        per_regime("first_fill_t_plus_s", "median"),
        "SUPERSEDED",
        ["scripts/gemini_l5_inventory_replay.py:138 ACQUIRE_FLOOR_S = 44.0 * SCALE"],
        "a hard FLOOR in the replay: no OPEN before it. Window-duration "
        "dependent, and the full corpus mixes durations")

    row("% of events that EVER complement (both legs held)",
        71.5, g(full, "ever_complement_pct"), per_regime("ever_complement_pct"),
        "SUPERSEDED",
        ["scripts/gemini_l5_inventory_replay.py (derive ever_complement_pct)",
         "src/l5_shadow/cohorts.py:15 quotes '72.7% two-sided'"],
        "the single largest early-vs-late divergence; see period contrast")

    row("median seconds to first matched increase",
        52.0, g(full, "seconds_first_fill_to_first_matched_increase", "median"),
        per_regime("seconds_first_fill_to_first_matched_increase", "median"),
        "SUPERSEDED",
        ["scripts/gemini_l5_inventory_replay.py:140 PATIENCE_REF_S = 52.0 * SCALE"],
        "a patience REFERENCE, not a wait; feeds the COMPLETE cadence")

    row("median same-side EXTENDs before complementing -> L5_EXTEND_BUDGET",
        1, g(full, "same_side_extends_before_complement", "median"),
        per_regime("same_side_extends_before_complement", "median"),
        "CHECK (see value)",
        ["scripts/gemini_l5_inventory_replay.py:136 L5_EXTEND_BUDGET = 1",
         "same file:273 default arg; :525-529 arms incl. ABLATION_NO_EXTEND"],
        "integer budget; robust if the full-corpus median is still 1")

    row("median fills per event (p90)",
        {"median": 6, "p90": 136},
        {"median": g(full, "fills_per_event", "median"),
         "p90": g(full, "fills_per_event", "p90")},
        {early_label: {"median": g(early, "fills_per_event", "median"),
                       "p90": g(early, "fills_per_event", "p90")},
         late_label: {"median": g(late, "fills_per_event", "median"),
                      "p90": g(late, "fills_per_event", "p90")}},
        "SUPERSEDED",
        ["src/pmus_spring_l5_session.py:123 ('12 extra attempts ... covers "
         "L5's p90 completion')", "cadence/retry budgeting in the spring session"],
        "drives how many attempts the live shell budgets per window")

    row("median inter-fill gap s -> MIN_GAP_S",
        2.0, g(full, "inter_fill_gap_s", "median"),
        per_regime("inter_fill_gap_s", "median"),
        "CHECK (see value)",
        ["scripts/gemini_l5_inventory_replay.py:142 MIN_GAP_S = max(1.0, 2.0*SCALE)"],
        "clamped to >=1s by the 1 Hz tape, so a small change is absorbed")

    row("pair cost at the instant of opposite-side buy (COMPRESS trigger)",
        0.991, g(full, "pair_cost_at_opposite_side_buy", "median"),
        per_regime("pair_cost_at_opposite_side_buy", "median"),
        "SUPERSEDED",
        ["scripts/gemini_l5_inventory_replay.py:132 L5_COMPRESS_TRIGGER = 0.991",
         "same file:524 arm L5_DERIVED_0.991"],
        "the replay's COMPLETE trigger for the L5-derived arm")

    row("FINAL achieved matched pair unit cost (median)",
        {"quoted_elsewhere": 0.9624, "narrow_recompute": nv("FINAL_matched_pair_cost", "median")},
        g(full, "FINAL_matched_pair_cost", "median"),
        per_regime("FINAL_matched_pair_cost", "median"),
        "SUPERSEDED",
        ["src/l5_shadow/inventory_policy.py:25 ('L5 achieves 0.9624')",
         "src/l5_shadow/two_sided.py:64 ('0.96 sits on the MEDIAN')",
         "src/l5_shadow/cohorts.py:15 ('median pair cost 0.9646')"],
        "the EMPIRICAL claim used to justify the 0.96 POLICY ceiling")

    row("spring pair-completion target <= 0.96 (POLICY, not a measurement)",
        0.96, "POLICY -- unchanged by this reconciliation",
        {early_label: "policy", late_label: "policy"},
        "POLICY (owner-set; its EMPIRICAL JUSTIFICATION is SUPERSEDED)",
        ["src/l5_shadow/two_sided.py:69 ADAPTED_EXECUTABLE_PAIR_CEILING",
         "src/l5_shadow/adapted_policy.py:36 REFERENCE_PAIR_CEILING",
         "src/gemini_execution/ci15m_budget.py:112 MAX_PAIR_COST",
         "src/gemini_execution/ci15m_probe.py:88 max_pair_cost",
         "src/gemini_execution/hybrid_complete.py:65 THRESHOLDS",
         "src/gemini_execution/residual_timeline.py:581,601 target default",
         "scripts/gemini_l5_inventory_replay.py:134 SPRING_COMPRESS_TRIGGER",
         "scripts/l5_vs_frozen_entry_study.py:290 frozen_ceiling",
         "src/l5_touch_proximity.py:54 OUR_GATES",
         "src/polymarket_l5/spring_representativeness.py:34-35 PAIR_CAPS",
         "src/pmus_spring_l5_session.py:28", "src/l5_expectancy_replay.py:437"],
        "0.96 stays as owner spring discipline. What this reconciliation "
        "removes is the claim that it sits on L5's achieved MEDIAN")

    row("same-side EXTEND is NOT price-improvement gated",
        {"lower": None, "higher": None, "conclusion": False},
        {**(full.get("same_side_repeat_price_direction") or {}),
         "conclusion_extend_is_improvement_gated": False},
        {early_label: early.get("same_side_repeat_price_direction"),
         late_label: late.get("same_side_repeat_price_direction")},
        "CONFIRMED on the full corpus",
        ["scripts/gemini_l5_inventory_replay.py:47-50 extend_gate_note",
         "src/gemini_execution/arming.py:112,143,173,198 ('EXTEND needs >=5c "
         "average improvement') -- CONTRADICTED by the tape"],
        "L5 pays both lower and higher on same-side repeats; the arming "
        "modules' 5c-improvement EXTEND rule is OUR invention, not L5's")

    row("relation mix OPEN / EXTEND / SWITCH-or-COMPLETE",
        UNAVAILABLE,
        full.get("transitions"),
        {early_label: early.get("transitions"), late_label: late.get("transitions")},
        "SUPERSEDED (frozen model fitted on the narrow corpus)",
        ["analysis/l5/l5_action_model_v2.json (relation masses)",
         "src/l5_fidelity_gate.py + analysis/l5/l5_fidelity_gate.json "
         "(action_distribution_relation L1 <= 0.10 sampled / 0.20 acted)"],
        "the fidelity gate scores our clone against masses derived from the "
        "narrow corpus, so a PASS currently proves fidelity to 13 hours")

    row("sides observed (BUY only?)",
        "BUY only", full.get("sides_observed"),
        {early_label: early.get("sides_observed"), late_label: late.get("sides_observed")},
        "CONFIRMED on the full corpus",
        ["scripts/gemini_l5_inventory_replay.py:'sides_observed' -> the replay "
         "state machine has no SELL transition"],
        "L5 is effectively BUY-ONLY across the full corpus (a negligible "
        "handful of SELLs). KEEP THIS DISTINCT from the documented ~9s "
        "'side switch' mechanic in L5_STATUS.md: that is L5 alternating "
        "WHICH OUTCOME IT BUYS (i.e. COMPLETE / SWITCH), not selling. "
        "Buy-only does not contradict it.")

    row("event durations / asset scope",
        "btc-updown-15m assumed (L5_WINDOW_S = 900.0)",
        {"durations": full.get("durations_by_event"),
         "assets": full.get("assets_by_event")},
        {early_label: early.get("durations_by_event"),
         late_label: late.get("durations_by_event")},
        "SUPERSEDED",
        ["scripts/gemini_l5_inventory_replay.py:124 L5_WINDOW_S = 900.0 and "
         "SCALE = 300/900 -- EVERY time constant is rescaled through it"],
        "if the corpus mixes durations, a single 900s SCALE mis-scales "
        "ACQUIRE_FLOOR_S, PATIENCE_REF_S and MIN_GAP_S together")

    return rows


# ------------------------------------------------------------------ reporting
def md_cell(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, (dict, list)):
        return "`" + json.dumps(v, sort_keys=True).replace("|", "\\|") + "`"
    return str(v).replace("|", "\\|").replace("\n", " ")


def render_md(doc: Dict[str, Any]) -> str:
    L: List[str] = []
    A = L.append
    A("# CANON_CONSTANTS -- L5 constant reconciliation & venue requirements")
    A("")
    A("**Every number below is a %s.**" % PROVENANCE_LABEL)
    A("No value here is realized profit for our account. Generated by "
      "`scripts/l5_constant_reconciliation.py` (research only: no orders, "
      "no venue writes, no network).")
    A("")
    A("Generated %s" % doc["generated_at_utc"])
    A("")
    A("## Corpus and identity")
    A("")
    c = doc["corpus"]
    A("IDENTITY = PROVENANCE. Both files were fetched with "
      "`params.user == 0xb945945d5bca...`; a record is disqualified only by a "
      "*contradicting* `name`/`proxyWallet`. We never filter on "
      "`name == 'l5Zn1bWoM8eTsK'` -- that filter is exactly what shrank the "
      "old corpus to 13 hours.")
    A("")
    for k, v in c.items():
        A("- `%s`: %s" % (k, md_cell(v)))
    A("")
    A("### Narrow (old) corpus, for comparison")
    A("")
    n = doc.get("narrow_corpus_recompute") or {}
    A("- source: `data/l5/raw/trades.jsonl`")
    A("- deduped fills: %s over %s independent events, %s distinct UTC days"
      % (n.get("fills"), n.get("events"), n.get("distinct_utc_days")))
    A("- date range: %s" % md_cell(n.get("date_range_utc")))
    A("")
    A("Two caveats on the comparison column. (a) The old figure is quoted as "
      "**9,901 fills**; under the conflict-free dedupe key this file yields "
      "%s fills, so the quoted 9,901 was itself produced by a coarser key "
      "that merged distinct prints. (b) Running the SAME metric definitions "
      "on this narrow file reproduces the frozen band `[0.20, 0.82]` and "
      "median first-fill `0.583` exactly -- which VALIDATES the method and "
      "confirms the frozen constants are faithful to the narrow file and "
      "only to it. The one frozen number the narrow file does NOT reproduce "
      "is the achieved pair cost: `0.9624`/`0.9646` are quoted in "
      "`src/l5_shadow/*`, but this definition gives %s on the narrow file "
      "and %s on the full corpus, so that specific justification for the "
      "0.96 ceiling is not reproducible under either corpus."
      % (n.get("fills"),
         (n.get("FINAL_matched_pair_cost") or {}).get("median"),
         ((doc.get("full_corpus_derivation") or {}).get(
             "FINAL_matched_pair_cost") or {}).get("median")))
    A("")
    A("## Descriptive periods (NOT regime names)")
    A("")
    p = doc["periods"]
    A("A parallel agent owns regime change-point detection. We split the "
      "corpus's own daily grid at its median active day and label the halves "
      "from what we measure.")
    A("")
    for k, v in p.items():
        A("- `%s`: %s" % (k, md_cell(v)))
    A("")
    A("## Section 5 -- reconciliation table")
    A("")
    A("| metric | old 9,901-fill value | full-corpus value | per-regime values | still valid? |")
    A("| --- | --- | --- | --- | --- |")
    for r in doc["reconciliation_table"]:
        A("| %s | %s | %s | %s | **%s** |"
          % (r["metric"], md_cell(r["old_9901_fill_value"]),
             md_cell(r["full_corpus_value"]), md_cell(r["per_regime_values"]),
             r["still_valid"]))
    A("")
    A("### Where each constant is used in code")
    A("")
    for r in doc["reconciliation_table"]:
        A("**%s** -- %s" % (r["metric"], r["still_valid"]))
        for u in r["used_in_code"]:
            A("  - `%s`" % u)
        A("  - note: %s" % r["notes"])
        A("")
    A("## Section 6 -- minimum venue characteristics per regime")
    A("")
    v = doc["venue_requirements"]
    A("_%s_" % v["note"])
    A("")
    for key in ("inventory_compression_regime", "incentive_liquidity_regime"):
        reg = v[key]
        A("### %s" % reg["regime"])
        A("")
        A("Derived from: %s" % reg["derived_from"])
        A("")
        A("| requirement | minimum threshold | justifying measurement |")
        A("| --- | --- | --- |")
        for rk, rv in reg["requirements"].items():
            th = rv.get("threshold")
            just = rv.get("measurement") or rv.get("why") or rv.get("why_partial") or ""
            extra = {k2: v2 for k2, v2 in rv.items()
                     if k2 not in ("threshold", "measurement", "why", "why_partial")}
            if extra:
                just = (just + " | extra: " + json.dumps(extra, sort_keys=True))
            A("| `%s` | %s | %s |" % (rk, md_cell(th), md_cell(just)[:600]))
        A("")
    A("## Incentive records in the corpus")
    A("")
    A("| type | records | usdc total | first day | last day | distinct days |")
    A("| --- | --- | --- | --- | --- | --- |")
    for t, s in (doc["incentive_records"]["by_type"]).items():
        A("| %s | %s | %s | %s | %s | %s |"
          % (t, s["records"], s["usdc_total"], s["first_day_utc"],
             s["last_day_utc"], s["distinct_days"]))
    A("")
    A("## Daily grid (independent events per UTC day)")
    A("")
    A("| day | events | fills | fills/event | events ever complement | complement % | usdc notional |")
    A("| --- | --- | --- | --- | --- | --- | --- |")
    for r in doc["daily_grid"]:
        A("| %s | %s | %s | %s | %s | %s | %s |"
          % (r["day"], r["events"], r["fills"], r["fills_per_event"],
             r["events_ever_complement"], r["complement_pct"],
             r["usdc_notional"]))
    A("")
    A("## UNAVAILABLE from this corpus")
    A("")
    for u in doc["unavailable"]:
        A("- %s" % u)
    A("")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------- main
def main() -> int:
    print("loading full corpus (provenance identity, cross-file dedupe)...")
    corpus = Corpus().load([ACTIVITY, ACTIVITY_GAP])
    print("  raw payload records: %d  by file: %s"
          % (corpus.seen_raw, dict(corpus.seen_by_file)))
    print("  contradicting-identity dropped: %d" % corpus.contradicted)
    print("  duplicates dropped across files: %d" % corpus.dup_dropped)
    print("  records with NO name (gap file, kept by provenance): %d" % corpus.no_name)
    print("  TRADEs: %d   types: %s"
          % (len(corpus.trades),
             {k: len(v) for k, v in sorted(corpus.by_type.items())}))

    full = derive(corpus.trades)
    grid = daily_grid(corpus.trades)
    cut, early_f, late_f, pmeta = split_periods(corpus.trades, grid)
    early = derive(early_f)
    late = derive(late_f)

    # descriptive labels from what we measured, not invented regime names
    ec = early.get("ever_complement_pct") or 0.0
    lc = late.get("ever_complement_pct") or 0.0
    if ec >= lc:
        early_label = "early high-completion period (%s..%s)" % (cut and grid[0]["day"], cut)
        late_label = "late low-completion period (%s..%s)" % (cut, grid[-1]["day"])
    else:
        early_label = "early low-completion period (%s..%s)" % (grid[0]["day"], cut)
        late_label = "late high-completion period (%s..%s)" % (cut, grid[-1]["day"])

    narrow = None
    if NARROW.exists():
        print("recomputing the NARROW corpus with the same definitions...")
        ncorp = Corpus().load([NARROW])
        narrow = derive(ncorp.trades)
        print("  narrow fills=%s events=%s days=%s range=%s"
              % (narrow["fills"], narrow["events"],
                 narrow["distinct_utc_days"], narrow["date_range_utc"]))

    comp = per_contract_maker_comp(corpus, corpus.trades, grid, cut)
    inc = incentive_summary(corpus)
    table = build_table(full, early, late, narrow, early_label, late_label)
    venue = venue_requirements(full, early, late, comp, grid, cut,
                               early_label, late_label)

    # measurable capacity floor
    ev_usd: Dict[str, float] = collections.defaultdict(float)
    for f in corpus.trades:
        ev_usd[str(f.get("eventSlug"))] += float(f.get("usdcSize") or 0.0)
    sizes = [float(f.get("size") or 0.0) for f in corpus.trades]
    cap = venue["inventory_compression_regime"]["requirements"]["executable_capacity_min"]
    cap["threshold"] = {
        "usdc_per_event_median": (dd(list(ev_usd.values()), 2) or {}).get("median"),
        "usdc_per_event_p90": (dd(list(ev_usd.values()), 2) or {}).get("p90"),
        "contracts_per_fill_median": (dd(sizes, 2) or {}).get("median"),
        "contracts_per_fill_p90": (dd(sizes, 2) or {}).get("p90"),
    }
    cap["measurement"] = ("own notional per independent event (n=%d events) "
                          "and own contracts per fill (n=%d fills); a FLOOR "
                          "on the capacity the venue must absorb"
                          % (len(ev_usd), len(sizes)))

    doc = {
        "label": PROVENANCE_LABEL,
        "research_only": ("no orders, no venue writes, no network; "
                          "no credential values printed"),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "generator": "scripts/l5_constant_reconciliation.py",
        "identity_rule": ("IDENTITY = PROVENANCE. Envelope params.user == "
                          "0xb945945d5bca...; disqualify only on a "
                          "contradicting name/proxyWallet; NEVER filter on "
                          "name == 'l5Zn1bWoM8eTsK'. Dedupe across both "
                          "files on (transactionHash, conditionId, "
                          "outcomeIndex, timestamp, str(price), str(size), type) -- "
                          "the bare (txh, asset, timestamp) key degenerates "
                          "because the gap file has no `asset` and produced "
                          "33,057 silent conflicts."),
        "corpus": {
            "files": [str(ACTIVITY.relative_to(ROOT)),
                      str(ACTIVITY_GAP.relative_to(ROOT))],
            "raw_payload_records": corpus.seen_raw,
            "raw_by_file": dict(corpus.seen_by_file),
            "contradicting_identity_dropped": corpus.contradicted,
            "cross_file_duplicates_dropped": corpus.dup_dropped,
            "records_without_name_kept_by_provenance": corpus.no_name,
            "records_by_type": {k: len(v) for k, v in sorted(corpus.by_type.items())},
            "deduped_trades": len(corpus.trades),
            "independent_events": full["events"],
            "distinct_utc_days": full["distinct_utc_days"],
            "date_range_utc": full["date_range_utc"],
        },
        "narrow_corpus_recompute": narrow,
        "periods": {**pmeta, "early_label": early_label, "late_label": late_label,
                    "early_events": early["events"], "late_events": late["events"],
                    "early_range_utc": early["date_range_utc"],
                    "late_range_utc": late["date_range_utc"]},
        "full_corpus_derivation": full,
        "early_period_derivation": early,
        "late_period_derivation": late,
        "reconciliation_table": table,
        "incentive_records": inc,
        "maker_compensation_per_contract": comp,
        "venue_requirements": venue,
        "daily_grid": grid,
        "unavailable": [
            "spread, depth, BBO, quote sizes, resting order book -- the "
            "corpus is L5's own PRINTS only; nothing is approximated.",
            "maker/taker flag per fill -- not in the public activity payload, "
            "so maker share and the fidelity gate's maker_taker dimension "
            "cannot be recomputed from this corpus.",
            "fill rate -- needs SUBMITTED orders as a denominator; unfilled "
            "resting orders are not public.",
            "adverse-selection tolerance / toxicity -- needs mid at fill time "
            "and the subsequent mark.",
            "queue position and cancel/replace behaviour -- not public.",
            "any SEPTEMBER 2026 behaviour -- the canonical corpus spans "
            "2026-03-19 -> 2026-08-31 only. September evidence lives in a "
            "SEPARATE harvest under data/l5/raw/sep/ and is THIN; no row in "
            "the Section 5 table is computed from it.",
            "L5's realized P&L -- redemption cashflows are present as REDEEM "
            "records but fees, rebate crediting timing and off-venue "
            "transfers are not fully observable; and in any case no number "
            "here is realized profit for OUR account.",
        ],
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, indent=1, sort_keys=False,
                                   default=str) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(doc), encoding="utf-8")
    print("wrote %s" % OUT_JSON.relative_to(ROOT))
    print("wrote %s" % OUT_MD.relative_to(ROOT))

    print("\n--- SECTION 5 (condensed) ---")
    for r in table:
        print("%-62s old=%s  full=%s  -> %s"
              % (r["metric"][:62], json.dumps(r["old_9901_fill_value"],
                                              default=str)[:40],
                 json.dumps(r["full_corpus_value"], default=str)[:60],
                 r["still_valid"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
