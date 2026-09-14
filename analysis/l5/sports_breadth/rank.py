"""Rank observed markets with the FROZEN L5 Habitat Score, at four levels.

    market  ->  sport  ->  venue x sport  ->  venue

NO SPORT-SPECIFIC TUNING. Every cohort goes through `src.l5_habitat_score`
unchanged, against the same L5 reference PMUS and Gemini 5m were scored on.
Nothing here adjusts a threshold, a weight or a feature for sports.

TWO PENALTIES, BOTH EXPLICIT AND BOTH REPORTED BESIDE THE RAW NUMBER:

  MISSING DATA   is already the frozen score's own `coverage_discounted_score`
                 -- a feature that could not be computed leaves the numerator
                 AND the denominator, so a market measured on three of seven
                 features cannot outrank one measured on seven by being
                 lucky on three.
  SAMPLE SIZE    is a shrinkage toward the global median with a fixed
                 pseudo-count. A cohort of one market does not get to top the
                 table on one lucky window. The raw median and `n` are ALWAYS
                 reported next to the shrunk value, so the adjustment can be
                 undone by any reader.

ATTAINABLE PAIR COST is computed here rather than assumed: a maker joining
both touches pays bid + (1 - ask) = 1 - spread. This inverts the naive
reading of spread -- a WIDE spread offers a CHEAPER attainable pair and a
TIGHT one offers a dearer pair -- which is exactly the tension between
Gemini's wide crypto books and PMUS's one-cent sports books.
"""
from __future__ import annotations

import collections
import json
import statistics
import sys
from typing import Any, Dict, List, Mapping, Optional

sys.path.insert(0, __file__.rsplit("/analysis/", 1)[0])
from src import l5_habitat_score as hs                       # noqa: E402

# Pseudo-markets of prior weight applied to every cohort. Fixed in advance,
# not tuned to the result.
SHRINK_K = 5.0

# Below this a cohort is reported but NOT ranked.
MIN_MARKETS_RANKABLE = 3
MIN_OBS_PER_MARKET = 20


def _p50(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 4) if xs else None


def market_row(key: str, entry: Mapping[str, Any],
               reference: Mapping[str, Any]) -> dict:
    """Score ONE observed market. Returns the frozen score plus the L5
    characteristics the owner named, which are reported, never scored."""
    obs = entry.get("obs") or []
    s = entry.get("summary") or {}
    feats = hs.window_features(obs)
    row = {"key": key, "market_id": s.get("market_id"),
           "venue": s.get("venue"), "sport": s.get("sport"),
           "league": s.get("league"), "category": s.get("category"),
           "market_type": s.get("market_type"), "pass": entry.get("pass"),
           "observations": len(obs), "span_s": s.get("span_s"),
           "cadence_s": s.get("cadence_s"),
           "two_sided_pct": s.get("two_sided_pct"),
           "one_sided_observations": s.get("one_sided_observations")}
    if not feats.get("usable"):
        return {**row, "usable": False, "why": feats.get("why"),
                "habitat_score_on_observed": None,
                "coverage_discounted_score": None}

    span = feats["span_s"] or 1.0
    obs_per_min = round(len(obs) / (span / 60.0), 4)
    # attainable maker pair = bid + (1 - ask) = 1 - spread
    pair = [1.0 - o["spread"] for o in obs if o.get("spread") is not None]
    cand = {k: v for k, v in feats.items()
            if not k.startswith("_") and k != "usable"}
    cand["observations_per_min"] = obs_per_min
    cand["spread_ticks_p50"] = feats.get("spread_ticks_p50")
    cand["depth_at_touch_p50"] = feats.get("depth_at_touch_p50")
    cand["attainable_pair_cost_p50"] = _p50(pair)
    cand["trades_per_min"] = (round(len(entry.get("prints") or [])
                                    / (span / 60.0), 4)
                              if entry.get("prints") is not None else None)
    # PMUS TTE is a day boundary, not a settlement instant. Refuse to score it
    # rather than feed a known-wrong value to the one REGIME feature.
    if s.get("tte_scoreable") is False:
        cand["tte_p50_s"] = None
    sc = hs.score(cand, reference)
    ut = s.get("useful_transitions") or {}
    return {
        **row, "usable": True,
        "habitat_score_on_observed": sc["habitat_score_on_observed"],
        "coverage": sc["coverage"],
        "coverage_discounted_score": sc["coverage_discounted_score"],
        "features_scored": sc["features_scored"],
        "unknown_features": sc["unknown_features"],
        "sampling_limited_features": sc["sampling_limited_features"],
        "observations_per_min": obs_per_min,
        # ---- L5 characteristics: REPORTED, NEVER SCORED ----
        "spread_ticks_p50": cand["spread_ticks_p50"],
        "depth_at_touch_p50": cand["depth_at_touch_p50"],
        "attainable_pair_cost_p50": cand["attainable_pair_cost_p50"],
        "trades_per_min": cand["trades_per_min"],
        "prints": len(entry.get("prints") or []),
        "traded_shares_delta": s.get("traded_shares_delta"),
        "side_oscillations": s.get("side_oscillations"),
        "share_gaps_in_1_4s": ut.get("share_gaps_in_1_4s"),
        "gap_p50_s": ut.get("gap_p50_s"),
        "price_changes": ut.get("price_changes"),
        "band_coverage_020_080": s.get("band_coverage_020_080"),
        "price_changes_per_min": feats.get("price_changes_per_min"),
        "price_span": feats.get("price_span"),
        "bands_visited": feats.get("bands_visited"),
    }


def _agg(rows: List[Mapping[str, Any]], global_median: Optional[float]) -> dict:
    """One cohort. `n` and the raw median always travel with the shrunk
    value so the adjustment is reversible by the reader."""
    usable = [r for r in rows if r.get("usable")
              and r.get("observations", 0) >= MIN_OBS_PER_MARKET]
    scores = [r["coverage_discounted_score"] for r in usable
              if r.get("coverage_discounted_score") is not None]
    n = len(scores)
    med = _p50(scores)
    shrunk = None
    if med is not None and global_median is not None:
        shrunk = round((n * med + SHRINK_K * global_median) / (n + SHRINK_K), 4)
    return {
        "markets_observed": len(rows),
        "markets_usable": len(usable),
        "n_scored": n,
        "rankable": n >= MIN_MARKETS_RANKABLE,
        "rank_status": ("RANKABLE" if n >= MIN_MARKETS_RANKABLE
                        else f"INSUFFICIENT_SAMPLE:{n}<{MIN_MARKETS_RANKABLE}"),
        "habitat_p50_raw": med,
        "habitat_p50_shrunk": shrunk,
        "coverage_p50": _p50([r.get("coverage") for r in usable]),
        "observations_total": sum(r.get("observations", 0) for r in rows),
        "obs_per_min_p50": _p50([r.get("observations_per_min") for r in usable]),
        "cadence_s_p50": _p50([r.get("cadence_s") for r in rows]),
        "two_sided_pct_p50": _p50([r.get("two_sided_pct") for r in rows]),
        # L5 characteristics, reported not scored
        "spread_ticks_p50": _p50([r.get("spread_ticks_p50") for r in usable]),
        "depth_at_touch_p50": _p50([r.get("depth_at_touch_p50") for r in usable]),
        "attainable_pair_cost_p50": _p50(
            [r.get("attainable_pair_cost_p50") for r in usable]),
        "trades_per_min_p50": _p50([r.get("trades_per_min") for r in usable]),
        "prints_total": sum(r.get("prints", 0) or 0 for r in rows),
        "side_oscillations_p50": _p50([r.get("side_oscillations") for r in usable]),
        "share_gaps_in_1_4s_p50": _p50([r.get("share_gaps_in_1_4s") for r in usable]),
        "gap_p50_s_p50": _p50([r.get("gap_p50_s") for r in usable]),
        "band_coverage_p50": _p50([r.get("band_coverage_020_080") for r in usable]),
        "price_changes_per_min_p50": _p50(
            [r.get("price_changes_per_min") for r in usable]),
    }


def rank(observed: Mapping[str, Any], reference: Mapping[str, Any]) -> dict:
    rows = [market_row(k, v, reference)
            for k, v in (observed.get("series") or {}).items()]
    scored = [r["coverage_discounted_score"] for r in rows
              if r.get("coverage_discounted_score") is not None]
    gmed = _p50(scored)

    def group(fn):
        g = collections.defaultdict(list)
        for r in rows:
            g[fn(r)].append(r)
        out = {k: _agg(v, gmed) for k, v in g.items()}
        return dict(sorted(out.items(),
                           key=lambda kv: (kv[1]["rankable"],
                                           kv[1]["habitat_p50_shrunk"] or -1),
                           reverse=True))

    return {
        "global_habitat_p50": gmed,
        "shrink_pseudo_markets": SHRINK_K,
        "min_markets_rankable": MIN_MARKETS_RANKABLE,
        "min_obs_per_market": MIN_OBS_PER_MARKET,
        "penalties": {
            "missing_data": "coverage_discounted_score (frozen scorer)",
            "sample_size": f"shrinkage toward global median, k={SHRINK_K}",
        },
        "by_market": sorted(
            [r for r in rows if r.get("usable")],
            key=lambda r: -(r.get("coverage_discounted_score") or -1)),
        "unusable_markets": [r for r in rows if not r.get("usable")],
        "by_sport": group(lambda r: str(r.get("sport"))),
        "by_venue_sport": group(
            lambda r: "%s/%s" % (r.get("venue"), r.get("sport"))),
        "by_venue": group(lambda r: str(r.get("venue"))),
    }
