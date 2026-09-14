"""L5 HABITAT SCORE: how much does an environment resemble L5's, before we act?

OWNER: "build a durable L5 Habitat Score from variables observable before
strategy action. It must be market/venue agnostic and must not use settlement
outcome or our subsequent fills. Derive its features from canonical L5 rather
than choosing arbitrary thresholds ... Explicitly expose missing-data
penalties/UNKNOWN rather than imputing favorable values."

READ-ONLY and PRE-ACTION BY CONSTRUCTION. Nothing here submits, prices,
cancels or deploys, and nothing it reads is downstream of a decision: no
settlement outcome, no realized P&L, none of our own fills, no order state.
A test asserts the forbidden fields cannot reach it.

IT DOES NOT PREDICT PROFIT AND IS NOT CALIBRATED TO. It answers one question:
how much does this environment resemble the environments in which L5 actually
generated his inventory cycles?

------------------------------------------------------------------------
THE THRESHOLDS ARE L5's, NOT MINE
------------------------------------------------------------------------
Every calibrated feature is scored against the EMPIRICAL DISTRIBUTION of that
feature across L5's own 1,893 canonical windows. Reaching his median scores
0.5; reaching his p90 scores 0.9. No cut-off is chosen by hand, and
`derive_reference` regenerates the whole reference from the canonical
sequence so it can be audited and re-derived rather than trusted.

------------------------------------------------------------------------
THE ASYMMETRY THAT DECIDES HOW THIS MAY BE READ
------------------------------------------------------------------------
L5's canonical tape records HIS FILLS, not his market's order flow. His fill
cadence is therefore a LOWER BOUND on what his markets offered -- he traded at
least that fast, and the market supplied at least that much.

So the inference runs ONE WAY ONLY:

  candidate BELOW the L5 reference   ->  conclusive habitat deficit. The
                                         market cannot even supply what L5
                                         consumed.
  candidate ABOVE the L5 reference   ->  NOT conclusive sufficiency. A market
                                         can move constantly and still never
                                         present a completable opposite side.

THE SCORE IS A CEILING ON HABITAT ADEQUACY, NEVER AN ESTIMATE OF IT. A high
score licenses further observation; it never licenses capital.

------------------------------------------------------------------------
THREE KINDS OF "WE DON'T KNOW", KEPT APART
------------------------------------------------------------------------
UNKNOWN        the candidate did not supply the field, or supplied it
               saturated/unusable (a counter pinned at its cap is UNKNOWN,
               never a floor read as a measurement).
UNCALIBRATED   L5's public tape cannot produce a reference for it at all --
               spread, depth, book-update rate, queue and trade-through are
               invisible in a fills-only record. These are REPORTED for the
               candidate and excluded from the score, because scoring them
               against an invented reference is the failure this module
               exists to avoid.
NOT_APPLICABLE the feature is meaningless for this venue class.

None of the three is ever imputed to a favourable value. Missing calibrated
features leave the denominator, and `coverage` plus
`coverage_discounted_score` make the loss visible instead of silently
flattering a thinly observed market.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

CAPACITY = "CAPACITY"          # more is not worse; score saturates at 1.0
REGIME = "REGIME"              # different is different; two-sided similarity

UNKNOWN = "UNKNOWN"
UNCALIBRATED = "UNCALIBRATED"
NOT_APPLICABLE = "NOT_APPLICABLE"

PRICE_BANDS = ((0.10, "p<10c"), (0.25, "p10-25c"), (0.45, "p25-45c"),
               (0.55, "p45-55c"), (0.75, "p55-75c"), (0.90, "p75-90c"),
               (1.01, "p>90c"))

# Quantile grid the reference is stored on. Coarse on purpose: a finer grid
# would imply a precision 1,893 windows do not support.
GRID = (0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0)

# CALIBRATED features: L5's fills-only tape can produce a reference AND a
# candidate venue can supply the same quantity in kind. Each names the
# candidate-side quantity explicitly, because a mismatch here is how a
# comparison silently becomes meaningless.
CALIBRATED_FEATURES = (
    {"name": "price_changes_per_min", "kind": CAPACITY,
     "l5_source": "consecutive L5 fill prices within a window",
     "candidate_source": "consecutive observed prices (trade or BBO mid)",
     "sampling_bias": "DOWN",
     "caveat": "L5's is a trade-price series, a candidate's may be a quote "
               "mid; same quantity in kind, not identical in construction. "
               "A rate cannot exceed the observation cadence, so a coarsely "
               "sampled candidate is biased DOWNWARD here"},
    {"name": "share_move_ge_1c", "kind": CAPACITY,
     "l5_source": "|dprice| between consecutive L5 fills",
     "candidate_source": "|dprice| between consecutive observed prices",
     "sampling_bias": "UP",
     "caveat": "coarser sampling lets more price action accumulate between "
               "observations, so this is biased UPWARD for a coarsely "
               "sampled candidate"},
    {"name": "share_move_ge_2c", "kind": CAPACITY,
     "l5_source": "|dprice| between consecutive L5 fills",
     "candidate_source": "|dprice| between consecutive observed prices",
     "sampling_bias": "UP"},
    {"name": "share_move_ge_5c", "kind": CAPACITY,
     "l5_source": "|dprice| between consecutive L5 fills",
     "candidate_source": "|dprice| between consecutive observed prices",
     "sampling_bias": "UP"},
    {"name": "price_span", "kind": CAPACITY,
     "l5_source": "max - min L5 fill price in the window",
     "candidate_source": "max - min observed price in the window",
     "sampling_bias": "UP",
     "caveat": "an extremum can only be missed, never invented, by sparse "
               "sampling -- but a longer window catches more of it"},
    {"name": "bands_visited", "kind": CAPACITY,
     "l5_source": "distinct price bands L5's fills touched",
     "candidate_source": "distinct price bands the observed price touched",
     "sampling_bias": "DOWN"},
    {"name": "tte_p50_s", "kind": REGIME,
     "l5_source": "median seconds to settlement at L5's fills",
     "candidate_source": "median seconds to settlement at observation",
     "sampling_bias": "NONE"},
)

# A rate feature cannot exceed the observation cadence. Above this share of
# the ceiling the value is a SAMPLING ARTEFACT rather than a measurement.
SAMPLING_LIMITED_AT = 0.8

# What L5 CONSUMED. A candidate must supply at least this much in TRADES
# before it can be called adequate -- and a book-update rate is NOT a
# substitute, because quotes are not trades. Reported beside the score, never
# inside it.
L5_DEMAND_FEATURES = ("fills_per_min", "switches_per_min", "switch_rate")

# Invisible in a fills-only record. Reported for the candidate, never scored.
UNCALIBRATED_FEATURES = (
    "spread_ticks_p50", "depth_at_touch_p50", "book_updates_per_min",
    "trades_per_min", "contracts_per_min", "maker_touch_persistence_s",
    "trade_through_rate", "time_to_completable_opposite_s",
    "attainable_pair_cost_p50",
)

# Fields that would make this post-action. Refused, not ignored.
FORBIDDEN_INPUTS = (
    "settlement_outcome", "realized_usd", "realized_pnl", "settled",
    "our_fills", "filled_qty", "fill_price", "matched_qty", "residual",
    "pair_cost_realised", "order_id", "end_reason",
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "" or isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def band(px: Optional[float]) -> Optional[str]:
    if px is None:
        return None
    for hi, name in PRICE_BANDS:
        if px < hi:
            return name
    return PRICE_BANDS[-1][1]


def quantiles(values: Iterable[Optional[float]]) -> Optional[dict]:
    v = sorted(x for x in values if x is not None)
    if len(v) < 30:
        return None                       # too thin to be a reference
    return {"n": len(v),
            "q": {str(g): v[min(len(v) - 1, int(g * (len(v) - 1)))]
                  for g in GRID}}


def ecdf_position(value: Optional[float], ref: Optional[Mapping[str, Any]]
                  ) -> Optional[float]:
    """Where `value` sits in the reference, linearly interpolated on GRID."""
    if value is None or not ref:
        return None
    q = ref.get("q") or {}
    pts = sorted((float(k), _f(v)) for k, v in q.items() if _f(v) is not None)
    if len(pts) < 2:
        return None
    if value <= pts[0][1]:
        return 0.0
    if value >= pts[-1][1]:
        return 1.0
    for (g0, v0), (g1, v1) in zip(pts, pts[1:]):
        if v0 <= value <= v1:
            if v1 == v0:
                return g1
            return g0 + (g1 - g0) * (value - v0) / (v1 - v0)
    return None


def score_feature(value: Optional[float], ref: Optional[Mapping[str, Any]],
                  kind: str) -> dict:
    """One feature -> a 0..1 resemblance, or an explicit reason it has none."""
    if ref is None:
        return {"score": None, "status": UNCALIBRATED,
                "why": "L5's fills-only tape cannot produce a reference"}
    if value is None:
        return {"score": None, "status": UNKNOWN,
                "why": "the candidate did not supply a usable value"}
    pos = ecdf_position(value, ref)
    if pos is None:
        return {"score": None, "status": UNKNOWN, "why": "reference too thin"}
    if kind == CAPACITY:
        # reaching L5's median scores 0.5, his p90 scores 0.9, beyond
        # saturates: a richer market is not penalised and earns no bonus
        return {"score": round(min(1.0, pos), 4), "status": "SCORED",
                "ecdf_position": round(pos, 4), "kind": kind}
    # REGIME: distance from L5's median, in his own quantile units
    return {"score": round(max(0.0, 1.0 - 2.0 * abs(pos - 0.5)), 4),
            "status": "SCORED", "ecdf_position": round(pos, 4), "kind": kind}


def score(candidate: Mapping[str, Any], reference: Mapping[str, Any]) -> dict:
    """Score one environment. `candidate` carries pre-action features only.

    `observations_per_min`, when given, is the candidate's observation
    cadence and is used to flag rate features the sampling itself capped.
    """
    bad = sorted(set(candidate) & set(FORBIDDEN_INPUTS))
    if bad:
        raise ValueError(
            f"post-action fields may not reach the habitat score: {bad}")
    ceiling = _f(candidate.get("observations_per_min"))

    per, scored = {}, []
    for spec in CALIBRATED_FEATURES:
        n = spec["name"]
        val = _f(candidate.get(n))
        r = score_feature(val, (reference.get("calibrated")
                                or {}).get(n), spec["kind"])
        limited = None
        if ceiling and ceiling > 0 and n.endswith("_per_min") \
                and val is not None:
            limited = bool(val >= SAMPLING_LIMITED_AT * ceiling)
        per[n] = {**r, "value": candidate.get(n),
                  "candidate_source": spec["candidate_source"],
                  "sampling_bias": spec.get("sampling_bias"),
                  "sampling_limited": limited}
        if r["score"] is not None:
            scored.append(r["score"])

    unk = [n for n, v in per.items() if v["status"] == UNKNOWN]
    uncal = [n for n, v in per.items() if v["status"] == UNCALIBRATED]
    coverage = len(scored) / len(CALIBRATED_FEATURES)
    raw = (sum(scored) / len(scored)) if scored else None

    reported = {n: candidate.get(n, None) for n in UNCALIBRATED_FEATURES}
    demand = {n: {"l5": (reference.get("l5_demand") or {}).get(n),
                  "candidate": candidate.get(n, None),
                  "status": (UNKNOWN if candidate.get(n) is None else "GIVEN")}
              for n in L5_DEMAND_FEATURES}

    return {
        "habitat_score_on_observed": None if raw is None else round(raw, 4),
        "coverage": round(coverage, 4),
        "coverage_discounted_score": (None if raw is None
                                      else round(raw * coverage, 4)),
        "features_scored": len(scored),
        "features_total": len(CALIBRATED_FEATURES),
        "unknown_features": unk,
        "uncalibrated_features": uncal,
        "per_feature": per,
        "uncalibrated_reported_not_scored": reported,
        "l5_demand_side": demand,
        "interpretation": {
            "direction": ("BELOW the reference is conclusive habitat "
                          "deficit; ABOVE it is NOT conclusive sufficiency"),
            "ceiling": ("this is a CEILING on habitat adequacy, never an "
                        "estimate: L5's tape records his fills, which lower-"
                        "bound what his market supplied"),
            "not_profit": ("this score does not predict profit and is not "
                           "calibrated to it"),
            "missing_data": ("UNKNOWN and UNCALIBRATED features leave the "
                             "denominator; coverage_discounted_score is the "
                             "figure to quote when coverage is low"),
            "sampling_bias": ("rate features are biased DOWN by coarse "
                              "observation and move-magnitude features are "
                              "biased UP by it. A candidate that scores LOW "
                              "on the UP-biased features is failing despite "
                              "a bias in its own favour, which is the "
                              "stronger reading; a low score on a "
                              "sampling_limited rate feature is not."),
        },
        "sampling_limited_features": [
            n for n, v in per.items() if v.get("sampling_limited")],
        "observations_per_min": ceiling,
    }


def window_features(observations: Sequence[Mapping[str, Any]]) -> dict:
    """Pre-action features from a price/time series over ONE window.

    Each observation needs ``{t, price}``; ``tte_s``, ``two_sided``,
    ``spread_ticks`` and ``depth_at_touch`` are used when present. The series
    may be trades or quote mids -- the caller says which, and that label
    travels with the score.
    """
    pts = sorted(((_f(o.get("t")), _f(o.get("price")), o)
                  for o in observations
                  if _f(o.get("t")) is not None
                  and _f(o.get("price")) is not None), key=lambda x: x[0])
    if len(pts) < 2:
        return {"usable": False, "why": "fewer than two usable observations"}
    span = pts[-1][0] - pts[0][0]
    if span <= 0:
        return {"usable": False, "why": "zero time span"}
    mins = span / 60.0
    px = [p for _t, p, _o in pts]
    moves = [abs(b - a) for a, b in zip(px, px[1:])]
    changed = [m for m in moves if m > 1e-9]
    bands = {band(p) for p in px} - {None}
    tte = [_f(o.get("tte_s")) for _t, _p, o in pts]
    tte = sorted(x for x in tte if x is not None)
    two = [o.get("two_sided") for _t, _p, o in pts if o.get("two_sided")
           is not None]
    spr = sorted(x for x in (_f(o.get("spread_ticks"))
                             for _t, _p, o in pts) if x is not None)
    dep = sorted(x for x in (_f(o.get("depth_at_touch"))
                             for _t, _p, o in pts) if x is not None)
    return {
        "usable": True,
        "observations": len(pts),
        "span_s": round(span, 1),
        "price_changes_per_min": round(len(changed) / mins, 4),
        "share_move_ge_1c": (round(sum(1 for m in moves if m >= 0.01)
                                   / len(moves), 4) if moves else None),
        "share_move_ge_2c": (round(sum(1 for m in moves if m >= 0.02)
                                   / len(moves), 4) if moves else None),
        "share_move_ge_5c": (round(sum(1 for m in moves if m >= 0.05)
                                   / len(moves), 4) if moves else None),
        "price_span": round(max(px) - min(px), 4),
        "bands_visited": len(bands),
        "tte_p50_s": (tte[len(tte) // 2] if tte else None),
        "two_sided_share": (round(sum(1 for x in two if x) / len(two), 4)
                            if two else None),
        "spread_ticks_p50": (spr[len(spr) // 2] if spr else None),
        "depth_at_touch_p50": (dep[len(dep) // 2] if dep else None),
    }


def derive_reference(l5_windows: Iterable[Mapping[str, Any]]) -> dict:
    """Regenerate the whole reference from canonical L5 windows.

    Kept in the module so the reference is auditable and re-derivable rather
    than a table of numbers someone has to trust. A feature whose reference
    would rest on fewer than 30 windows is emitted as UNCALIBRATED instead.
    """
    ws = list(l5_windows)
    cal = {}
    for spec in CALIBRATED_FEATURES:
        cal[spec["name"]] = quantiles(w.get(spec["name"]) for w in ws)
    dem = {n: quantiles(w.get(n) for w in ws) for n in L5_DEMAND_FEATURES}
    return {"windows": len(ws), "grid": list(GRID),
            "calibrated": cal, "l5_demand": dem,
            "uncalibrated": {n: None for n in UNCALIBRATED_FEATURES},
            "labels": ("derived from L5's canonical PUBLIC FILLS. His fill "
                       "cadence lower-bounds what his markets supplied, so "
                       "every comparison against it is one-directional.")}
