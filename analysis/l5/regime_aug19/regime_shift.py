"""REGIME_2026-08-19_ON: what changed, metric by metric, across all four
canonical regimes.

HISTORICAL REPLAY / DESCRIPTIVE MEASUREMENT ONLY. Every figure here describes
the public Polymarket activity of trader `l5zn1bwom8etsk`. Nothing here is
shadow, submitted, filled, settled or realized, and **L5's cash is never our
realized profit**. No network, no venue write, no order path, no credentials.

WHAT THIS MODULE IS FOR
-----------------------
`analysis/l5/residual_frontier/OOS_RESULT.md` established that L5's
ex-incentive BASELINE decays across the repo's own canonical regime partition
and goes NEGATIVE in the newest segment. This module measures *the observables*
on the same population and the same partition so the sign flip can be
attributed.

THE REGIME PARTITION IS NOT RE-FIT HERE
---------------------------------------
`CANON_CHANGE_POINTS` is imported from
`analysis.l5.residual_frontier.replay`, which took it from
`analysis/l5/CANON_REGIMES.md` (`scripts/l5_regime_detect.py`). This module
**never** searches for a change point, never moves one, and never adds one.
`tests/test_regime_aug19.py` asserts the imported tuple against the literal
change points parsed out of `CANON_REGIMES.md` itself.

NO HINDSIGHT IN ANY CONTEMPORANEOUS METRIC
------------------------------------------
The one structural guard in this file: contemporaneous metrics are computed by
`contemporaneous_metrics()`, whose ONLY input is a list of `EventObservables`.
`EventObservables` is a frozen `__slots__` dataclass whose field set is exactly
`CONTEMPORANEOUS_FIELDS` and which **has no field carrying a settlement
outcome**. Settlement lives in a separate `EventOutcome` record, is built by a
separate function, and is consumed only by `outcome_metrics()`. There is no
object a contemporaneous metric could reach settlement through, because the
field does not exist on the object it is handed.

SELF-SELECTION
--------------
`OOS_RESULT.md` §4 established that a `REDEEM` record exists only if something
was redeemable, and that 99.61% of one-sided no-REDEEM events actually LOST.
Every cash figure in the output is therefore tagged
`SETTLEMENT_CONDITIONAL` (REDEEM-backed events only) or `POOLED` (all events,
no-REDEEM residual scored $0 as the feed shows). The two strata are reported
side by side and are **never averaged together**.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import statistics as stats
from dataclasses import dataclass, fields as dc_fields
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from analysis.l5.residual_frontier import oos_adapter
from analysis.l5.residual_frontier.replay import CANON_CHANGE_POINTS

PROFIT_LABEL = "historical replay / descriptive measurement"
NOT_OUR_PROFIT = (
    "Public activity of another trader. Not shadow, not a probe, not "
    "submitted, not filled, not settled, never realized.")

#: Fee constant used by the frozen Q1/frontier accounting. Reused, not
#: re-chosen: see analysis/l5/norebate/q1.py FEE_K.
FEE_K = 0.07

INCENTIVE_TYPES = ("MAKER_REBATE", "TAKER_REBATE", "REWARD")

#: Size buckets for the clip-mix series. Fixed here once; never re-cut per
#: regime.
SIZE_BUCKETS: Tuple[Tuple[str, float, float], ...] = (
    ("lt10", 0.0, 10.0),
    ("10_25", 10.0, 25.0),
    ("25_50", 25.0, 50.0),
    ("50_100", 50.0, 100.0),
    ("100_250", 100.0, 250.0),
    ("ge250", 250.0, float("inf")),
)


# ------------------------------------------------------------------ regimes

def regime_labels(change_points: Sequence[str] = CANON_CHANGE_POINTS
                  ) -> List[str]:
    """Human labels for the partition induced by `change_points`.

    Pure function of the change points. Nothing is detected.
    """
    cps = list(change_points)
    out = ["REGIME_PRE_%s" % cps[0]]
    for a, b in zip(cps, cps[1:]):
        out.append("REGIME_%s_TO_%s" % (a, _prev_day(b)))
    out.append("REGIME_%s_ON" % cps[-1])
    return out


def _prev_day(day_str: str) -> str:
    d = dt.date.fromisoformat(day_str) - dt.timedelta(days=1)
    return d.isoformat()


def regime_of(day_str: str,
              change_points: Sequence[str] = CANON_CHANGE_POINTS) -> str:
    """Which canonical regime a UTC calendar day falls in."""
    labels = regime_labels(change_points)
    for i, cp in enumerate(change_points):
        if day_str < cp:
            return labels[i]
    return labels[-1]


def day_of(ts: int) -> str:
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")


# ------------------------------------------------- the contemporaneous view

#: The complete field set of `EventObservables`. NO SETTLEMENT FIELD APPEARS
#: HERE, and the dataclass's __slots__ is built from this tuple, so the class
#: physically cannot carry one.
CONTEMPORANEOUS_FIELDS: Tuple[str, ...] = (
    "slug", "asset", "duration_s", "day", "regime",
    "n_fills", "turnover_usd",
    "first_price", "first_size", "first_is_up", "first_tte_s",
    "shares_up", "shares_down", "vwap_up", "vwap_down",
    "n_open", "n_extend", "n_switch", "n_complete",
    "run_lengths", "fill_sizes",
    "peak_abs_residual", "terminal_abs_residual",
    "t_to_match_s", "span_s",
    "price_travel", "price_range",
)

#: Field names anywhere in this module that are DERIVED FROM SETTLEMENT and
#: are therefore forbidden from the contemporaneous view. The test asserts
#: this set is disjoint from CONTEMPORANEOUS_FIELDS and from the metric keys
#: that `contemporaneous_metrics` emits.
SETTLEMENT_DERIVED_FIELDS: Tuple[str, ...] = (
    "winner_index", "has_redeem", "redeem_usd", "residual_pnl_usd",
    "settled_net_usd", "one_sided_lost",
)


@dataclass(frozen=True)
class EventObservables:
    """Everything observable about one window-event without knowing how it
    resolved. Field set is exactly `CONTEMPORANEOUS_FIELDS`."""
    __slots__ = CONTEMPORANEOUS_FIELDS

    slug: str
    asset: str
    duration_s: int
    day: str
    regime: str
    n_fills: int
    turnover_usd: float
    first_price: float
    first_size: float
    first_is_up: bool
    first_tte_s: float
    shares_up: float
    shares_down: float
    vwap_up: Optional[float]
    vwap_down: Optional[float]
    n_open: int
    n_extend: int
    n_switch: int
    n_complete: int
    run_lengths: Tuple[int, ...]
    fill_sizes: Tuple[float, ...]
    peak_abs_residual: float
    terminal_abs_residual: float
    t_to_match_s: Optional[float]
    span_s: float
    price_travel: float
    price_range: float

    # -- derived views, functions of the fields above ONLY --
    @property
    def pair_cost(self) -> Optional[float]:
        if self.vwap_up is None or self.vwap_down is None:
            return None
        return self.vwap_up + self.vwap_down

    @property
    def matched_shares(self) -> float:
        return min(self.shares_up, self.shares_down)

    @property
    def gross_shares(self) -> float:
        return self.shares_up + self.shares_down

    @property
    def is_complete(self) -> bool:
        return self.matched_shares > 0.0

    @property
    def terminal_residual_frac(self) -> float:
        g = self.gross_shares
        return (self.terminal_abs_residual / g) if g > 0 else 0.0


@dataclass(frozen=True)
class EventOutcome:
    """SETTLEMENT ONLY. Built by a separate function, read only by
    `outcome_metrics`. Never reachable from `EventObservables`."""
    slug: str
    regime: str
    has_redeem: bool
    winner_index: Optional[int]
    redeem_usd: float


# ------------------------------------------------------------- corpus build

def _parse_slug(slug: str) -> Tuple[str, int, int]:
    """`<asset>-updown-<5m|15m>-<open epoch>` -> (asset, duration_s, epoch).

    Same parse as `replay._duration_and_epoch`, plus the asset token, which
    the composition series needs. Raises on anything else, so an out-of-family
    slug can never be given an invented duration.
    """
    parts = slug.split("-")
    if len(parts) != 4 or parts[1] != "updown" or parts[2] not in ("5m", "15m"):
        raise ValueError("out-of-family slug: %r" % slug)
    return parts[0], (300 if parts[2] == "5m" else 900), int(parts[3])


def build_corpus(records: Iterable[Dict[str, Any]],
                 exclude_slugs: Optional[set] = None,
                 change_points: Sequence[str] = CANON_CHANGE_POINTS,
                 ) -> Tuple[List[EventObservables], List[EventOutcome],
                            List[Dict[str, Any]], Dict[str, Any]]:
    """Records -> (observables, outcomes, incentive records, diagnostics).

    Population rule is the OOS adapter's, unchanged: key on
    `eventSlug or conditionId`, keep only in-family `<asset>-updown-<5m|15m>-<epoch>`
    slugs, exclude the frozen in-sample slugs. BUY fills only (L5 is
    effectively buy-only; the two SELLs in the whole corpus are excluded by
    the frozen replay too).

    Incentive records carry an EMPTY slug and are therefore NOT in the event
    population at all; they are returned separately so the incentive series
    can be built without touching the ex-incentive accounting.
    """
    exclude_slugs = exclude_slugs or set()
    buckets: Dict[str, Dict[str, Any]] = collections.defaultdict(
        lambda: {"fills": [], "redeem_idx": set(), "redeem_usd": 0.0})
    incentives: List[Dict[str, Any]] = []
    diag = collections.Counter()

    for r in records:
        typ = r.get("type")
        if typ in INCENTIVE_TYPES:
            incentives.append(r)
            diag["incentive_records"] += 1
            continue
        slug = r.get("eventSlug") or r.get("conditionId") or ""
        if not slug:
            diag["dropped_no_key"] += 1
            continue
        try:
            _parse_slug(slug)
        except ValueError:
            diag["dropped_out_of_family"] += 1
            continue
        if slug in exclude_slugs:
            diag["dropped_in_sample_leak"] += 1
            continue
        if typ == "TRADE":
            if r.get("side") != "BUY":
                diag["dropped_non_buy"] += 1
                continue
            buckets[slug]["fills"].append(r)
        elif typ == "REDEEM":
            buckets[slug]["redeem_idx"].add(r.get("outcomeIndex"))
            buckets[slug]["redeem_usd"] += float(r.get("usdcSize") or 0.0)

    obs: List[EventObservables] = []
    outs: List[EventOutcome] = []
    for slug, d in buckets.items():
        if not d["fills"]:
            diag["dropped_event_no_fills"] += 1
            continue
        asset, dur, epoch = _parse_slug(slug)
        fills = sorted(d["fills"], key=lambda r: r["timestamp"])
        o = _observe(slug, asset, dur, epoch, fills, change_points)
        obs.append(o)
        idx = d["redeem_idx"]
        outs.append(EventOutcome(
            slug=slug, regime=o.regime, has_redeem=bool(idx),
            winner_index=(list(idx)[0] if len(idx) == 1 else None),
            redeem_usd=d["redeem_usd"]))
    obs.sort(key=lambda e: (e.day, e.slug))
    outs.sort(key=lambda e: e.slug)
    return obs, outs, incentives, dict(diag)


def _observe(slug: str, asset: str, dur: int, epoch: int,
             fills: List[Dict[str, Any]],
             change_points: Sequence[str]) -> EventObservables:
    """Build the contemporaneous view of one window-event.

    Action labels follow `analysis/l5/CANON_REGIMES.md` §4 exactly:
      OPEN      first acquisition
      EXTEND    same outcome as the immediately preceding fill
      COMPLETE  other outcome AND the bought leg is strictly the lighter one
      SWITCH    other outcome but NOT reducing imbalance
    Every label is decided from state *before* the fill. Nothing later than
    the fill's own timestamp enters any field.
    """
    up = dn = 0.0
    # FEE-EXCLUSIVE cost, i.e. price*size, which is what the frozen
    # `replay._score_event` uses for its VWAPs and pair cost. `usdcSize`
    # includes the venue fee and is accumulated separately as turnover.
    up_cost = dn_cost = 0.0
    turnover = 0.0
    n_open = n_extend = n_switch = n_complete = 0
    peak = 0.0
    runs: List[int] = []
    cur_run = 0
    prev_is_up: Optional[bool] = None
    t_to_match: Optional[float] = None
    sizes: List[float] = []
    prices: List[float] = []
    travel = 0.0
    prev_price: Optional[float] = None

    for i, r in enumerate(fills):
        is_up = (r.get("outcomeIndex") == 0)
        size = float(r["size"])
        price = float(r["price"])
        usdc = float(r["usdcSize"])
        sizes.append(size)
        prices.append(price)
        if prev_price is not None:
            travel += abs(price - prev_price)
        prev_price = price

        if i == 0:
            n_open += 1
        elif is_up == prev_is_up:
            n_extend += 1
        else:
            own = up if is_up else dn
            other = dn if is_up else up
            if own < other:
                n_complete += 1
            else:
                n_switch += 1

        if prev_is_up is None or is_up == prev_is_up:
            cur_run += 1
        else:
            runs.append(cur_run)
            cur_run = 1
        prev_is_up = is_up

        turnover += usdc
        if is_up:
            up += size
            up_cost += price * size
        else:
            dn += size
            dn_cost += price * size
        if min(up, dn) > 0 and t_to_match is None:
            t_to_match = float(r["timestamp"] - fills[0]["timestamp"])
        peak = max(peak, abs(up - dn))
    runs.append(cur_run)

    f0 = fills[0]
    return EventObservables(
        slug=slug, asset=asset, duration_s=dur,
        day=day_of(f0["timestamp"]),
        regime=regime_of(day_of(f0["timestamp"]), change_points),
        n_fills=len(fills),
        turnover_usd=turnover,
        first_price=float(f0["price"]),
        first_size=float(f0["size"]),
        first_is_up=(f0.get("outcomeIndex") == 0),
        first_tte_s=float(epoch + dur - f0["timestamp"]),
        shares_up=up, shares_down=dn,
        vwap_up=(up_cost / up if up > 0 else None),
        vwap_down=(dn_cost / dn if dn > 0 else None),
        n_open=n_open, n_extend=n_extend, n_switch=n_switch,
        n_complete=n_complete,
        run_lengths=tuple(runs), fill_sizes=tuple(sizes),
        peak_abs_residual=peak, terminal_abs_residual=abs(up - dn),
        t_to_match_s=t_to_match,
        span_s=float(fills[-1]["timestamp"] - f0["timestamp"]),
        price_travel=travel,
        price_range=(max(prices) - min(prices)) if prices else 0.0,
    )


# ------------------------------------------------------------- stat helpers

def _q(vals: Sequence[float], p: float) -> Optional[float]:
    v = sorted(vals)
    if not v:
        return None
    if len(v) == 1:
        return float(v[0])
    k = p * (len(v) - 1)
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return float(v[lo] + (v[hi] - v[lo]) * (k - lo))


def _dist(vals: Sequence[float]) -> Dict[str, Any]:
    if not vals:
        return {"n": 0, "UNAVAILABLE": "no observations"}
    return {
        "n": len(vals),
        "mean": round(stats.fmean(vals), 6),
        "p10": round(_q(vals, 0.10), 6), "p25": round(_q(vals, 0.25), 6),
        "median": round(_q(vals, 0.50), 6), "p75": round(_q(vals, 0.75), 6),
        "p90": round(_q(vals, 0.90), 6), "p95": round(_q(vals, 0.95), 6),
        "p99": round(_q(vals, 0.99), 6),
    }


def _frac(vals: Sequence[float], pred) -> Optional[float]:
    if not vals:
        return None
    return round(sum(1 for v in vals if pred(v)) / len(vals), 6)


def _shares(counter: Dict[str, float]) -> Dict[str, float]:
    tot = sum(counter.values())
    if tot <= 0:
        return {}
    return {k: round(v / tot, 6) for k, v in
            sorted(counter.items(), key=lambda kv: -kv[1])}


# -------------------------------------------------- contemporaneous metrics

def contemporaneous_metrics(events: Sequence[EventObservables]
                            ) -> Dict[str, Any]:
    """Every metric observable WITHOUT knowing how any market resolved.

    The only argument is a sequence of `EventObservables`, which has no
    settlement field. This function therefore cannot read a settlement
    outcome: there is nothing to read it from.
    """
    if not events:
        return {"n_events": 0, "UNAVAILABLE": "no events in this regime"}

    days = sorted({e.day for e in events})
    fills = [s for e in events for s in e.fill_sizes]
    turnover = sum(e.turnover_usd for e in events)
    n_fills = sum(e.n_fills for e in events)

    acts = {k: sum(getattr(e, "n_" + k) for e in events)
            for k in ("open", "extend", "switch", "complete")}
    n_acts = sum(acts.values()) or 1

    pairs = [e.pair_cost for e in events if e.pair_cost is not None]
    runs = [r for e in events for r in e.run_lengths]
    t2m = [e.t_to_match_s for e in events if e.t_to_match_s is not None]

    size_bucket_fills = {name: 0 for name, _, _ in SIZE_BUCKETS}
    size_bucket_notional = {name: 0.0 for name, _, _ in SIZE_BUCKETS}
    # Share-weighted mix: `fill_sizes` holds share counts, so the second
    # mix below is an exact share-of-shares, not a dollar share.
    for e in events:
        for s in e.fill_sizes:
            for name, lo, hi in SIZE_BUCKETS:
                if lo <= s < hi:
                    size_bucket_fills[name] += 1
                    size_bucket_notional[name] += s
                    break

    asset_events: Dict[str, float] = collections.Counter()
    asset_turnover: Dict[str, float] = collections.Counter()
    dur_events: Dict[str, float] = collections.Counter()
    dur_turnover: Dict[str, float] = collections.Counter()
    for e in events:
        asset_events[e.asset] += 1
        asset_turnover[e.asset] += e.turnover_usd
        key = "5m" if e.duration_s == 300 else "15m"
        dur_events[key] += 1
        dur_turnover[key] += e.turnover_usd

    return {
        "n_events": len(events),
        "n_fills": n_fills,
        "observed_days": len(days),
        "first_day": days[0], "last_day": days[-1],
        "turnover_usd": round(turnover, 2),
        "events_per_observed_day": round(len(events) / len(days), 3),
        "turnover_per_observed_day_usd": round(turnover / len(days), 2),
        "fills_per_event": round(n_fills / len(events), 4),
        "notional_per_event_usd": round(turnover / len(events), 4),

        "clip_size_shares": _dist(fills),
        "clip_size_mix_frac_of_fills": {
            k: round(v / max(1, len(fills)), 6)
            for k, v in size_bucket_fills.items()},
        "clip_size_mix_frac_of_shares": {
            k: round(v / max(1e-9, sum(size_bucket_notional.values())), 6)
            for k, v in size_bucket_notional.items()},
        "first_size_shares": _dist([e.first_size for e in events]),

        "first_price": _dist([e.first_price for e in events]),
        "first_favored_frac": _frac(
            [e.first_price for e in events], lambda p: p > 0.5),
        "first_tte_s": _dist([e.first_tte_s for e in events]),

        "action_mix_frac": {k: round(v / n_acts, 6) for k, v in acts.items()},
        "action_counts": acts,
        "extend_per_event": round(acts["extend"] / len(events), 4),
        "switch_per_event": round(acts["switch"] / len(events), 4),
        "complete_per_event": round(acts["complete"] / len(events), 4),
        "same_side_run_length": _dist([float(r) for r in runs]),

        "pair_cost": _dist(pairs),
        "pair_cost_frac_le_0_96": _frac(pairs, lambda c: c <= 0.96),
        "pair_cost_frac_lt_1_00": _frac(pairs, lambda c: c < 1.00),
        "pair_cost_frac_gt_1_00": _frac(pairs, lambda c: c > 1.00),
        "pair_cost_frac_gt_1_05": _frac(pairs, lambda c: c > 1.05),
        "pair_cost_frac_gt_1_15": _frac(pairs, lambda c: c > 1.15),

        "completion_rate": round(
            sum(1 for e in events if e.is_complete) / len(events), 6),
        "completion_ratio": _dist([
            round(2 * e.matched_shares / e.gross_shares, 6)
            for e in events if e.gross_shares > 0]),
        "t_to_match_s": _dist(t2m),
        "event_span_s": _dist([e.span_s for e in events]),

        "peak_abs_residual_shares": _dist(
            [e.peak_abs_residual for e in events]),
        "terminal_abs_residual_shares": _dist(
            [e.terminal_abs_residual for e in events]),
        "terminal_residual_frac": _dist(
            [e.terminal_residual_frac for e in events]),
        "one_sided_event_frac": round(
            sum(1 for e in events if not e.is_complete) / len(events), 6),

        "composition_asset_frac_of_events": _shares(asset_events),
        "composition_asset_frac_of_turnover": _shares(asset_turnover),
        "composition_duration_frac_of_events": _shares(dur_events),
        "composition_duration_frac_of_turnover": _shares(dur_turnover),

        "VOL_PROXY_price_travel_per_fill": _dist([
            e.price_travel / max(1, e.n_fills - 1) for e in events
            if e.n_fills > 1]),
        "VOL_PROXY_price_range_within_event": _dist(
            [e.price_range for e in events]),
        "VOL_PROXY_NOTE": (
            "L5's OWN fill prices, not a venue mid and not the underlying. "
            "An EVENT-LEVEL DESCRIPTIVE, not a decision-time feature. True "
            "implied/realized volatility of the underlying is UNAVAILABLE "
            "across regimes: data/btc_spot_proxy/btc_1m.json covers only "
            "2026-08-15..2026-08-20."),

        "MAKER_TAKER_MIX": {
            "status": "UNAVAILABLE",
            "reason": (
                "Liquidity role requires `execution.isAggressor` from the "
                "data-api /trades shape. Neither holdout source carries it: "
                "data/l5/raw/activity_gap.jsonl.gz records have exactly "
                "{conditionId,eventSlug,outcome,outcomeIndex,price,side,size,"
                "slug,timestamp,transactionHash,type,usdcSize} and "
                "data/l5/raw/sep/l5_sep23_25.json adds only profile fields. "
                "NOT INFERRED, and the bare boolean `aggressor` is not used "
                "anywhere in this module."),
        },
        "SPREAD": {
            "status": "UNAVAILABLE",
            "reason": (
                "Spread needs a resting book. analysis/l5/BOOK_ACQUISITION.md "
                "establishes that the only historical Polymarket L2 on disk is "
                "2026-08-21..2026-08-24 (3.5 of 166 days, 2.1%), entirely "
                "inside REGIME_2026-08-19_ON, so no cross-regime spread "
                "series can be formed. Pair cost is reported instead and is a "
                "different quantity."),
        },
    }


# ------------------------------------------------------------ outcome block

def outcome_metrics(outcomes: Sequence[EventOutcome]) -> Dict[str, Any]:
    """SETTLEMENT-DERIVED. Outcome only; never a contemporaneous feature."""
    if not outcomes:
        return {"n_events": 0, "UNAVAILABLE": "no events in this regime"}
    n = len(outcomes)
    red = [o for o in outcomes if o.has_redeem]
    return {
        "LABEL": "SETTLEMENT-DERIVED OUTCOME. Not observable at decision time.",
        "n_events": n,
        "events_with_redeem": len(red),
        "redeem_coverage_frac": round(len(red) / n, 6),
        "events_without_redeem": n - len(red),
        "redeem_cashflow_usd": round(sum(o.redeem_usd for o in red), 2),
        "SELF_SELECTION_WARNING": (
            "A REDEEM record exists only if something was redeemable. "
            "OOS_RESULT.md §4 measured 99.61% of one-sided no-REDEEM events "
            "as actual LOSSES. The no-REDEEM stratum is therefore a "
            "loss-enriched stratum, never to be pooled with the "
            "REDEEM-backed one."),
    }


# ---------------------------------------------------------- incentive block

def incentive_metrics(records: Sequence[Dict[str, Any]],
                      change_points: Sequence[str] = CANON_CHANGE_POINTS,
                      ) -> Dict[str, Any]:
    """When the CONFIRMED incentive records appear, by regime and by day.

    CONFIRMED-ONLY: a dollar is counted here iff a record exists. Nothing is
    modelled, imputed or per-contract-inferred. These are *L5's* credits and
    are not evidence that equivalent incentives would accrue to us.
    """
    by_regime: Dict[str, Dict[str, Any]] = {}
    for lab in regime_labels(change_points):
        by_regime[lab] = {t: {"n": 0, "usd": 0.0} for t in INCENTIVE_TYPES}
        by_regime[lab]["days_with_any_credit"] = set()
    first_seen: Dict[str, Optional[str]] = {t: None for t in INCENTIVE_TYPES}
    last_seen: Dict[str, Optional[str]] = {t: None for t in INCENTIVE_TYPES}
    hour_hist: Dict[int, int] = collections.Counter()

    for r in records:
        t = r["type"]
        d = day_of(r["timestamp"])
        lab = regime_of(d, change_points)
        by_regime[lab][t]["n"] += 1
        by_regime[lab][t]["usd"] += float(r.get("usdcSize") or 0.0)
        by_regime[lab]["days_with_any_credit"].add(d)
        hour_hist[dt.datetime.utcfromtimestamp(r["timestamp"]).hour] += 1
        if first_seen[t] is None or d < first_seen[t]:
            first_seen[t] = d
        if last_seen[t] is None or d > last_seen[t]:
            last_seen[t] = d

    out: Dict[str, Any] = {"by_regime": {}, "first_record_day": first_seen,
                           "last_record_day": last_seen,
                           "credit_hour_utc_histogram": dict(
                               sorted(hour_hist.items()))}
    for lab, d in by_regime.items():
        days = sorted(d.pop("days_with_any_credit"))
        row = {t: {"n": d[t]["n"], "usd": round(d[t]["usd"], 2)}
               for t in INCENTIVE_TYPES}
        row["days_with_any_credit"] = len(days)
        row["credit_days"] = days
        row["total_usd"] = round(sum(d[t]["usd"] for t in INCENTIVE_TYPES), 2)
        out["by_regime"][lab] = row
    out["CONFIRMED_ONLY"] = (
        "Counted solely where a record exists. Never modelled or imputed. "
        "These are L5's credits, not ours, and they are EXCLUDED from every "
        "ex-incentive figure quoted from OOS_RESULT.json.")
    return out


# -------------------------------------- outcome P&L and the shift-share split
# EVERYTHING BELOW IS SETTLEMENT-DERIVED. It is an OUTCOME decomposition, and
# none of it is, or feeds, a contemporaneous metric. The functions take the
# outcome records explicitly so the dependency is visible in the signature.

def event_pnl(e: EventObservables, o: EventOutcome,
              fills_cost: Dict[str, float]) -> Dict[str, float]:
    """Per-event ex-incentive cash, using the FROZEN replay's own formulas
    (`analysis/l5/residual_frontier/replay._score_event`, BASELINE cell):

        matched_usd = min(up,dn) * (1 - pair_cost)
        residual_usd = (|resid| if the residual leg won else 0) - vwap*|resid|
        fees_usd = sum(usdcSize - price*size)
        net = matched + residual - fees

    Settlement enters ONLY in `residual_usd`, exactly as the frozen replay
    does it, and only after every fill has been accounted.
    """
    matched = e.matched_shares
    pc = e.pair_cost
    matched_usd = matched * (1.0 - pc) if (matched > 0 and pc is not None) else 0.0
    resid = e.shares_up - e.shares_down
    r_side = 0 if resid > 0 else 1
    vwap = (e.vwap_up if r_side == 0 else e.vwap_down) or 0.0
    r_cost = vwap * abs(resid)
    r_payout = (abs(resid) if (o.winner_index is not None
                               and r_side == o.winner_index) else 0.0)
    resid_usd = r_payout - r_cost
    fees = fills_cost.get(e.slug, 0.0)
    return {"matched_usd": matched_usd, "residual_usd": resid_usd,
            "fees_usd": fees,
            "net_usd": matched_usd + resid_usd - fees,
            "turnover_usd": e.turnover_usd}


def cell_of(e: EventObservables) -> str:
    return "%s_%s" % (e.asset, "5m" if e.duration_s == 300 else "15m")


def shift_share(a: Dict[str, Dict[str, float]],
                b: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    """Decompose the change in net-bp-of-turnover from regime `a` to `b`.

        d_total = d_composition + d_behaviour + d_interaction
        d_composition = sum_c (w_b,c - w_a,c) * r_a,c     (same rates, new mix)
        d_behaviour   = sum_c  w_a,c * (r_b,c - r_a,c)    (same mix, new rates)
        d_interaction = sum_c (w_b,c - w_a,c) * (r_b,c - r_a,c)

    `w` is the cell's share of turnover, `r` its net in bp of its own
    turnover. Cells are asset x window-length. A cell absent from one regime
    contributes its full weight to the composition term, which is the honest
    reading: entering a new market family IS composition.
    """
    cells = sorted(set(a) | set(b))
    ta = sum(v["turnover_usd"] for v in a.values()) or 1.0
    tb = sum(v["turnover_usd"] for v in b.values()) or 1.0

    def w(d, c, t):
        return d.get(c, {}).get("turnover_usd", 0.0) / t

    def r(d, c):
        v = d.get(c)
        if not v or not v["turnover_usd"]:
            return 0.0
        return 1e4 * v["net_usd"] / v["turnover_usd"]

    comp = sum((w(b, c, tb) - w(a, c, ta)) * r(a, c) for c in cells)
    beh = sum(w(a, c, ta) * (r(b, c) - r(a, c)) for c in cells)
    inter = sum((w(b, c, tb) - w(a, c, ta)) * (r(b, c) - r(a, c))
                for c in cells)
    tot = (1e4 * sum(v["net_usd"] for v in b.values()) / tb
           - 1e4 * sum(v["net_usd"] for v in a.values()) / ta)
    return {
        "unit": "bp of turnover",
        "net_bp_from": round(1e4 * sum(v["net_usd"] for v in a.values()) / ta, 2),
        "net_bp_to": round(1e4 * sum(v["net_usd"] for v in b.values()) / tb, 2),
        "d_total_bp": round(tot, 2),
        "d_composition_bp": round(comp, 2),
        "d_behaviour_bp": round(beh, 2),
        "d_interaction_bp": round(inter, 2),
        "composition_share_of_move": (round(comp / tot, 4) if tot else None),
        "behaviour_share_of_move": (round(beh / tot, 4) if tot else None),
        "per_cell": {c: {
            "w_from": round(w(a, c, ta), 6), "w_to": round(w(b, c, tb), 6),
            "bp_from": round(r(a, c), 2), "bp_to": round(r(b, c), 2),
        } for c in cells},
    }


def pnl_block(obs: Sequence[EventObservables],
              outs: Sequence[EventOutcome],
              fills_cost: Dict[str, float],
              change_points: Sequence[str] = CANON_CHANGE_POINTS,
              ) -> Dict[str, Any]:
    """Per-regime ex-incentive cash, split into the two settlement strata and
    into asset x window-length cells, plus the shift-share between
    consecutive regimes.

    Rebates and rewards are exactly $0 here, as in the frozen replay.
    """
    o_by = {o.slug: o for o in outs}
    labels = regime_labels(change_points)
    # stratum -> regime -> cell -> accumulator
    acc: Dict[str, Dict[str, Dict[str, Dict[str, float]]]] = {
        "SETTLEMENT_CONDITIONAL_redeem_backed": collections.defaultdict(
            lambda: collections.defaultdict(
                lambda: collections.defaultdict(float))),
        "POOLED_all_events": collections.defaultdict(
            lambda: collections.defaultdict(
                lambda: collections.defaultdict(float))),
        "NO_REDEEM_loss_enriched": collections.defaultdict(
            lambda: collections.defaultdict(
                lambda: collections.defaultdict(float))),
    }
    for e in obs:
        o = o_by.get(e.slug)
        if o is None:
            continue
        p = event_pnl(e, o, fills_cost)
        strata = ["POOLED_all_events"]
        strata.append("SETTLEMENT_CONDITIONAL_redeem_backed" if o.has_redeem
                      else "NO_REDEEM_loss_enriched")
        for s in strata:
            for key in (cell_of(e), "ALL"):
                d = acc[s][e.regime][key]
                for k, v in p.items():
                    d[k] += v
                d["events"] += 1

    out: Dict[str, Any] = {
        "LABEL": ("SETTLEMENT-DERIVED OUTCOME, ex incentives (rebates and "
                  "rewards are exactly $0). HISTORICAL REPLAY -- not our "
                  "realized profit."),
        "STRATA_NEVER_POOLED": (
            "SETTLEMENT_CONDITIONAL is conditional on a source-backed REDEEM "
            "record and is UPWARD-BIASED by self-selection. "
            "NO_REDEEM_loss_enriched is the complementary, loss-enriched "
            "stratum. POOLED_all_events scores an absent REDEEM as $0 payout, "
            "which is the unbiased reading of what the feed shows. The three "
            "are reported separately and are never averaged."),
        "by_stratum": {}, "shift_share": {},
    }
    for s, per_regime in acc.items():
        blk = {}
        for lab in labels:
            cells = per_regime.get(lab, {})
            allc = cells.get("ALL")
            if not allc:
                blk[lab] = {"UNAVAILABLE": "no events"}
                continue
            t = allc["turnover_usd"] or 1.0
            blk[lab] = {
                "events": int(allc["events"]),
                "matched_usd": round(allc["matched_usd"], 2),
                "residual_usd": round(allc["residual_usd"], 2),
                "fees_usd": round(allc["fees_usd"], 2),
                "net_ex_incentives_usd": round(allc["net_usd"], 2),
                "turnover_usd": round(t, 2),
                "matched_bp": round(1e4 * allc["matched_usd"] / t, 2),
                "residual_bp": round(1e4 * allc["residual_usd"] / t, 2),
                "fees_bp": round(-1e4 * allc["fees_usd"] / t, 2),
                "net_bp": round(1e4 * allc["net_usd"] / t, 2),
                "per_cell": {c: {
                    "events": int(v["events"]),
                    "turnover_usd": round(v["turnover_usd"], 2),
                    "net_usd": round(v["net_usd"], 2),
                    "net_bp": (round(1e4 * v["net_usd"] / v["turnover_usd"], 2)
                               if v["turnover_usd"] else None),
                    "residual_bp": (
                        round(1e4 * v["residual_usd"] / v["turnover_usd"], 2)
                        if v["turnover_usd"] else None),
                    "matched_bp": (
                        round(1e4 * v["matched_usd"] / v["turnover_usd"], 2)
                        if v["turnover_usd"] else None),
                } for c, v in sorted(cells.items()) if c != "ALL"},
            }
        out["by_stratum"][s] = blk
        ss = {}
        for a, b in zip(labels, labels[1:]):
            ca = {c: v for c, v in per_regime.get(a, {}).items() if c != "ALL"}
            cb = {c: v for c, v in per_regime.get(b, {}).items() if c != "ALL"}
            if ca and cb:
                ss["%s__to__%s" % (a, b)] = shift_share(ca, cb)
        out["shift_share"][s] = ss
    return out


def fee_by_slug(records: Iterable[Dict[str, Any]],
                keep: Optional[set] = None) -> Dict[str, float]:
    """Venue fee per event = sum(usdcSize - price*size) over BUY fills.

    Contemporaneous (it is on the fill record), but kept out of
    `EventObservables` because it is only used by the outcome block.
    """
    out: Dict[str, float] = collections.defaultdict(float)
    for r in records:
        if r.get("type") != "TRADE" or r.get("side") != "BUY":
            continue
        slug = r.get("eventSlug") or r.get("conditionId") or ""
        if keep is not None and slug not in keep:
            continue
        out[slug] += max(0.0, float(r["usdcSize"])
                         - float(r["price"]) * float(r["size"]))
    return dict(out)


#: The DOCUMENTED venue change nearest the final regime boundary: Polymarket
#: moved crypto up/down markets to TWAP settlement and attached a $1,000,000
#: August liquidity-reward pool on this date (see
#: analysis/l5/AUGUST8_PROGRAM_EVENT_STUDY.md; L5's first-ever REWARD credit
#: is the following day). It is used ONLY to cut a disclosed diagnostic
#: sub-window inside the canonical third regime, so that "did the metric move
#: at the program change or at the detected boundary?" can be answered. It is
#: NOT a change point, is never fitted, and never replaces or moves one.
DOCUMENTED_PROGRAM_CHANGE = dt.date(2026, 8, 7)


def residual_quality(obs: Sequence[EventObservables],
                     outs: Sequence[EventOutcome],
                     change_points: Sequence[str] = CANON_CHANGE_POINTS,
                     ) -> Dict[str, Any]:
    """Why the residual term moved: exposure vs realized quality.

    Per regime and per settlement stratum, all normalised by turnover so the
    5x change in scale does not confound:
      resid_cost_bp    the directional leg's cost basis, bp of turnover
      resid_payout_bp  what settlement actually paid on it, bp of turnover
      resid_net_bp     the difference -- the drag itself
      resid_entry_vwap his own average entry price on the residual leg
                       (CONTEMPORANEOUS)
      share_weighted_hit_rate   payout per residual share (SETTLEMENT)
      hit_minus_entry_price     the per-share edge; 0 means the residual was
                                priced fairly in hindsight
    """
    o_by = {o.slug: o for o in outs}
    acc: Dict[str, Dict[str, Dict[str, float]]] = {
        s: collections.defaultdict(lambda: collections.defaultdict(float))
        for s in ("SETTLEMENT_CONDITIONAL_redeem_backed", "POOLED_all_events")}
    for e in obs:
        o = o_by.get(e.slug)
        if o is None:
            continue
        resid = e.shares_up - e.shares_down
        side = 0 if resid > 0 else 1
        v = (e.vwap_up if side == 0 else e.vwap_down) or 0.0
        pay = (abs(resid) if (o.winner_index is not None
                              and side == o.winner_index) else 0.0)
        strata = ["POOLED_all_events"]
        if o.has_redeem:
            strata.append("SETTLEMENT_CONDITIONAL_redeem_backed")
        for st in strata:
            a = acc[st][e.regime]
            a["turn"] += e.turnover_usd
            a["sh"] += abs(resid)
            a["cost"] += v * abs(resid)
            a["pay"] += pay
            a["n"] += 1
    out: Dict[str, Any] = {"LABEL": (
        "resid_cost_bp and resid_entry_vwap are CONTEMPORANEOUS; "
        "resid_payout_bp, share_weighted_hit_rate and hit_minus_entry_price "
        "are SETTLEMENT-DERIVED OUTCOMES and are not features.")}
    for st, per in acc.items():
        blk = {}
        for lab in regime_labels(change_points):
            a = per.get(lab)
            if not a or not a["turn"] or not a["sh"]:
                blk[lab] = {"UNAVAILABLE": "no residual exposure"}
                continue
            hit = a["pay"] / a["sh"]
            vwap = a["cost"] / a["sh"]
            blk[lab] = {
                "events": int(a["n"]),
                "resid_shares_per_usd_turnover": round(a["sh"] / a["turn"], 6),
                "resid_cost_bp": round(1e4 * a["cost"] / a["turn"], 2),
                "resid_payout_bp": round(1e4 * a["pay"] / a["turn"], 2),
                "resid_net_bp": round(1e4 * (a["pay"] - a["cost"]) / a["turn"], 2),
                "resid_entry_vwap": round(vwap, 6),
                "share_weighted_hit_rate": round(hit, 6),
                "hit_minus_entry_price": round(hit - vwap, 6),
            }
        out[st] = blk
    return out


def program_window_ramp(obs: Sequence[EventObservables],
                        outs: Sequence[EventOutcome],
                        fills_cost: Dict[str, float],
                        incentives: Sequence[Dict[str, Any]],
                        change_points: Sequence[str] = CANON_CHANGE_POINTS,
                        ) -> Dict[str, Any]:
    """Did the metric move at the DOCUMENTED program change, or at the
    DETECTED boundary? Three windows, in order:

      A  third canonical regime, BEFORE  DOCUMENTED_PROGRAM_CHANGE
      B  third canonical regime, ON/AFTER DOCUMENTED_PROGRAM_CHANGE
      C  the final canonical regime, unchanged

    A/B is a DISCLOSED DIAGNOSTIC SPLIT at a published venue date. The
    canonical partition is untouched: C is exactly the repo's final regime and
    A+B is exactly the repo's third one.
    """
    labs = regime_labels(change_points)
    cut = DOCUMENTED_PROGRAM_CHANGE.isoformat()
    o_by = {o.slug: o for o in outs}

    def win(e: EventObservables) -> Optional[str]:
        if e.regime == labs[-2]:
            return "A_regime3_pre_program" if e.day < cut                 else "B_regime3_post_program"
        if e.regime == labs[-1]:
            return "C_regime4_detected_boundary"
        return None

    acc: Dict[str, Dict[str, float]] = collections.defaultdict(
        lambda: collections.defaultdict(float))
    days: Dict[str, set] = collections.defaultdict(set)
    for e in obs:
        w = win(e)
        if w is None:
            continue
        o = o_by.get(e.slug)
        if o is None:
            continue
        p = event_pnl(e, o, fills_cost)
        for k, v in p.items():
            acc[w][k] += v
        acc[w]["events"] += 1
        days[w].add(e.day)
    inc: Dict[str, float] = collections.defaultdict(float)
    for r in incentives:
        d = day_of(r["timestamp"])
        lab = regime_of(d, change_points)
        if lab == labs[-2]:
            w = "A_regime3_pre_program" if d < cut else "B_regime3_post_program"
        elif lab == labs[-1]:
            w = "C_regime4_detected_boundary"
        else:
            continue
        inc[w] += float(r.get("usdcSize") or 0.0)

    out: Dict[str, Any] = {
        "DISCLOSED_DIAGNOSTIC_SPLIT": (
            "A/B split at the published venue date %s. No change point was "
            "fitted, moved or added; the canonical partition is unchanged."
            % cut),
        "windows": {},
    }
    for w in ("A_regime3_pre_program", "B_regime3_post_program",
              "C_regime4_detected_boundary"):
        a = acc.get(w)
        if not a:
            continue
        t = a["turnover_usd"] or 1.0
        nd = max(1, len(days[w]))
        out["windows"][w] = {
            "observed_days": len(days[w]),
            "events": int(a["events"]),
            "turnover_usd": round(t, 2),
            "net_ex_incentives_usd": round(a["net_usd"], 2),
            "net_bp": round(1e4 * a["net_usd"] / t, 2),
            "matched_bp": round(1e4 * a["matched_usd"] / t, 2),
            "residual_bp": round(1e4 * a["residual_usd"] / t, 2),
            "fees_bp": round(-1e4 * a["fees_usd"] / t, 2),
            "net_ex_incentives_per_day_usd": round(a["net_usd"] / nd, 2),
            "CONFIRMED_incentive_usd": round(inc[w], 2),
            "CONFIRMED_incentive_bp_of_turnover": round(1e4 * inc[w] / t, 2),
            "CONFIRMED_incentive_per_day_usd": round(inc[w] / nd, 2),
            "LABEL_POOLED": (
                "POOLED/unbiased: a no-REDEEM residual is scored $0, which is "
                "what the feed shows. Not conditional on settlement cover."),
        }
    return out


def daily_series(obs: Sequence[EventObservables],
                 outs: Sequence[EventOutcome],
                 fills_cost: Dict[str, float],
                 incentives: Sequence[Dict[str, Any]],
                 change_points: Sequence[str] = CANON_CHANGE_POINTS,
                 ) -> List[Dict[str, Any]]:
    """One row per observed UTC day. This is the SHAPE evidence: a monotone
    ramp and a single discontinuity look different here, and the brief asks
    which it is. No change point is fitted from it."""
    o_by = {o.slug: o for o in outs}
    per: Dict[str, Dict[str, Any]] = collections.defaultdict(
        lambda: {"acc": collections.defaultdict(float), "pc": [],
                 "t2m": [], "compl": []})
    for e in obs:
        o = o_by.get(e.slug)
        if o is None:
            continue
        d = per[e.day]
        p = event_pnl(e, o, fills_cost)
        for k, v in p.items():
            d["acc"][k] += v
        d["acc"]["events"] += 1
        if e.pair_cost is not None:
            d["pc"].append(e.pair_cost)
        if e.t_to_match_s is not None:
            d["t2m"].append(e.t_to_match_s)
        d["compl"].append(1.0 if e.is_complete else 0.0)
    inc: Dict[str, float] = collections.defaultdict(float)
    for r in incentives:
        inc[day_of(r["timestamp"])] += float(r.get("usdcSize") or 0.0)
    rows = []
    for day in sorted(per):
        d = per[day]
        a = d["acc"]
        t = a["turnover_usd"] or 1.0
        rows.append({
            "day": day,
            "regime": regime_of(day, change_points),
            "events": int(a["events"]),
            "turnover_usd": round(t, 2),
            "net_bp_POOLED": round(1e4 * a["net_usd"] / t, 2),
            "matched_bp": round(1e4 * a["matched_usd"] / t, 2),
            "residual_bp_POOLED": round(1e4 * a["residual_usd"] / t, 2),
            "pair_cost_p90": (round(_q(d["pc"], 0.90), 6) if d["pc"] else None),
            "pair_cost_frac_gt_1_05": _frac(d["pc"], lambda c: c > 1.05),
            "completion_rate": round(stats.fmean(d["compl"]), 6),
            "t_to_match_median_s": (round(_q(d["t2m"], 0.50), 2)
                                    if d["t2m"] else None),
            "CONFIRMED_incentive_usd": round(inc.get(day, 0.0), 2),
        })
    return rows


# ------------------------------------------------------------------- driver

def _baseline_strata(path: str) -> Dict[str, Dict[str, Any]]:
    """Per-regime ex-incentive BASELINE cash, COPIED from the frozen OOS
    result. Not recomputed here, so this module cannot restate it."""
    d = json.load(open(path, encoding="utf-8"))
    out: Dict[str, Dict[str, Any]] = {}
    for tag in ("PRIMARY", "SECONDARY"):
        strata = d[tag + "_frontier"]["cells"]["BASELINE"]["strata"]
        out[tag] = {k: v for k, v in strata.items()}
    return out


def run(change_points: Sequence[str] = CANON_CHANGE_POINTS,
        oos_result: str = "analysis/l5/residual_frontier/OOS_RESULT.json",
        ) -> Dict[str, Any]:
    excl = oos_adapter.in_sample_slugs()
    prim = oos_adapter._dedupe(oos_adapter.read_primary())
    sec = oos_adapter._dedupe(oos_adapter.read_secondary())
    p_obs, p_out, p_inc, p_diag = build_corpus(prim, excl, change_points)
    # the one slug shared between the two harvests is assigned to PRIMARY, as
    # the OOS adapter does, so the populations stay disjoint.
    sec_excl = excl | {e.slug for e in p_obs}
    s_obs, s_out, s_inc, s_diag = build_corpus(sec, sec_excl, change_points)
    p_fees = fee_by_slug(prim, {e.slug for e in p_obs})
    s_fees = fee_by_slug(sec, {e.slug for e in s_obs})

    labels = regime_labels(change_points)

    def by_regime(obs, outs):
        o_idx = collections.defaultdict(list)
        for e in obs:
            o_idx[e.regime].append(e)
        s_idx = collections.defaultdict(list)
        by_slug = {e.slug: e.regime for e in obs}
        for o in outs:
            s_idx[by_slug.get(o.slug, o.regime)].append(o)
        return (
            {lab: contemporaneous_metrics(o_idx[lab]) for lab in labels},
            {lab: outcome_metrics(s_idx[lab]) for lab in labels},
        )

    p_cont, p_outm = by_regime(p_obs, p_out)
    s_cont, s_outm = by_regime(s_obs, s_out)

    return {
        "protocol": "REGIME-AUG19-ATTRIBUTION",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profit_label": PROFIT_LABEL,
        "NOT_OUR_REALIZED_PROFIT": NOT_OUR_PROFIT,
        "change_points": list(change_points),
        "change_points_source": (
            "analysis/l5/CANON_REGIMES.md via "
            "analysis.l5.residual_frontier.replay.CANON_CHANGE_POINTS. NOT "
            "re-fit, NOT moved, NOT added to by this module."),
        "regime_labels": labels,
        "contemporaneous_fields": list(CONTEMPORANEOUS_FIELDS),
        "settlement_derived_fields": list(SETTLEMENT_DERIVED_FIELDS),
        "no_hindsight_clause": (
            "Contemporaneous metrics are computed by contemporaneous_metrics(), "
            "whose only input is EventObservables, a frozen __slots__ "
            "dataclass with no settlement field. Settlement lives in a "
            "separate EventOutcome record read only by outcome_metrics()."),
        "PRIMARY": {
            "source": oos_adapter.PRIMARY,
            "diagnostics": p_diag,
            "n_events": len(p_obs),
            "n_fills": sum(e.n_fills for e in p_obs),
            "contemporaneous_by_regime": p_cont,
            "outcome_by_regime": p_outm,
            "incentives": incentive_metrics(p_inc, change_points),
            "outcome_pnl": pnl_block(
                p_obs, p_out, p_fees, change_points),
            "residual_quality": residual_quality(p_obs, p_out, change_points),
            "daily_series": daily_series(
                p_obs, p_out, p_fees, p_inc, change_points),
            "PROGRAM_WINDOW_RAMP": program_window_ramp(
                p_obs, p_out, p_fees, p_inc, change_points),
        },
        "SECONDARY": {
            "source": oos_adapter.SECONDARY,
            "diagnostics": s_diag,
            "n_events": len(s_obs),
            "n_fills": sum(e.n_fills for e in s_obs),
            "contemporaneous_by_regime": s_cont,
            "outcome_by_regime": s_outm,
            "incentives": incentive_metrics(s_inc, change_points),
            "outcome_pnl": pnl_block(
                s_obs, s_out, s_fees, change_points),
            "residual_quality": residual_quality(s_obs, s_out, change_points),
            "daily_series": daily_series(
                s_obs, s_out, s_fees, s_inc, change_points),
        },
        "EX_INCENTIVE_BASELINE_COPIED_FROM_OOS": _baseline_strata(oos_result),
        "EX_INCENTIVE_BASELINE_PROVENANCE": (
            "Copied verbatim from %s BASELINE strata. Not recomputed here."
            % oos_result),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="analysis/l5/regime_aug19/REGIME_SHIFT.json")
    a = ap.parse_args()
    res = run()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, sort_keys=False, default=str)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
