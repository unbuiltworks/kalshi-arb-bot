"""SETTLEMENT REFERENCE IDENTIFICATION, and the Z it would take to use it.

WHAT THIS DOES NOT DO: it does not compute Z. The state variable the research
question needs is the remaining move required in the ACTUAL settlement
statistic, normalized by the causal uncertainty of that remaining statistic --
NOT spot distance over sigma*sqrt(T), and not any formula borrowed from
another venue's reference rate. That estimator cannot be parameterized without
the exact construction of the index, so this module records what is
authoritative, what was verified, and what remains unknown, and returns a
status of NOT_IDENTIFIED until the gaps are closed.

WHAT IS AUTHORITATIVE (held in-repo, re-readable):

  * Gemini OpenAPI `SourceDetails`: "Structured data source information for
    price observation ... Present for crypto Up/Down events", with
    `agency` exampled "Kaiko" and `index` exampled "GRR-KAIKO_BTCUSD_60S".
    Preserved at analysis/gemini/sell/prediction-markets.openapi.20261004.yaml
    (sha256 53caea563447398132afe9f9c45bfe45398526d03b8308c2603ebeb63069d165).
  * Gemini OpenAPI `StrikeType.reference`: "Crypto Up/Down reference strike
    price captured at `availableAt` time", null until then.
  * OUR OWN recorded contract objects, which name TWO indices, not one:
        GRR-KAIKO_RFR_BTCUSD_60S   through the 2026-10-02T17:45Z expiry
        LS-LRRR-XBTUSD             from the 2026-10-02T17:50Z expiry onward
    The switch is visible inside a single day in ie_usable_windows.json.
  * 1,322 resolved `reference`-type contracts with strike and settlement.

THE ERA PROBLEM, which has to be said before any Kaiko parameter matters: the
only Gemini tape that carries a reference-price series alongside executable
books (ie_tte_ladder.json, 133 BTC5m windows) is ENTIRELY the LS-LRRR era.
Identifying GRR-KAIKO_RFR_BTCUSD_60S exactly would therefore not license a Z
on that tape. There are two unknown constructions, and the richer tape sits
under the one that is not even nominally Kaiko.

WHAT IS NOT AUTHORITATIVE AND IS NOT USED AS IF IT WERE: a search-engine
summary of Kaiko's general aggregation approach (equal-size time partitions,
a volume-weighted median per partition at the 50th percentile of cumulative
volume, then a recency-favouring time weighting, with real-time windows
selectable from 15/20/30/60/120/300s). That is secondary description of the
FAMILY, not the parameterization of this ticker, exactly as the brief warns.
kaiko.com, docs.kaiko.com and marketing.kaiko.com are all denied by this
environment's egress policy, so the rulebook could not be read here.

WHAT THE REPO ALREADY HAS, and is reused rather than rebuilt:
`src/gemini_maker/fair_value.py` implements the CORRECT FORM of the estimator
-- the distribution of the settlement AVERAGE, not of spot:

    tau >= w :  E[settle] = spot ;            Var = sigma^2 * ((tau-w) + w/3)
    tau <  w :  E[settle] = (realized*(w-tau) + spot*tau)/w
                                              Var = sigma^2 * tau^3/(3 w^2)

and Z would be (E[settle] - strike)/sqrt(Var). Both variance expressions
assume the settlement statistic is an EQUAL-WEIGHT time average over a fixed
w. Under partitioned volume-weighted medians with recency weighting, the
weight function changes, so BOTH the realized share and the variance of the
remaining part change, and the Z this module would emit would be a different
number. That one assumption is the whole blocker.

VERIFICATION THAT WAS ACTUALLY RUN (analysis/gemini/ref_lag_study.json,
`proxy_errors`): an equal-weight 60s mean of single-venue Gemini spot trades,
scored against Gemini's own published settlement values.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
from typing import Any, Dict, List

LABEL = "GEMINI_SETTLEMENT_REFERENCE_V1"

#: every parameter the brief requires, with its status. None is UNKNOWN.
REQUIRED_PARAMETERS = {
    "calculation_window_length": {
        "status": "PARTIAL",
        "evidence": ("the ticker name ends _60S and Gemini's market pages "
                     "describe a one-minute average; our own fit to 133 "
                     "resolved settlements prefers a 60s mean over an "
                     "instantaneous print ($0.99 vs $3.09 median error)"),
        "unknown": ("whether the window is the 60s ENDING at expiry or offset "
                    "by a publication lag")},
    "partition_size_and_count": {
        "status": "UNKNOWN",
        "evidence": "none obtainable from this environment",
        "unknown": "everything"},
    "intra_partition_statistic": {
        "status": "UNKNOWN",
        "evidence": ("secondary description of the Kaiko FAMILY mentions a "
                     "volume-weighted median; not confirmed for this ticker"),
        "unknown": "whether it applies, and at what percentile definition"},
    "weighting_across_partitions": {
        "status": "UNKNOWN",
        "evidence": ("secondary description mentions recency-favouring time "
                     "weighting; the functional form is not stated"),
        "unknown": ("the weight function -- which is exactly what the Z "
                    "variance depends on")},
    "source_venues_and_trade_filters": {
        "status": "UNKNOWN",
        "evidence": "none; 'vetted centralized exchanges' is not a list",
        "unknown": ("the venue set, so a single-venue reconstruction cannot "
                    "be made faithful even in principle")},
    "publication_timing": {
        "status": "UNKNOWN",
        "evidence": "none obtainable here",
        "unknown": "emit cadence and the lag between window close and print"},
    "missing_data_behaviour": {
        "status": "UNKNOWN",
        "evidence": "none obtainable here",
        "unknown": "fallback when a partition has no qualifying trades"},
    "current_era_index_LS_LRRR_XBTUSD": {
        "status": "UNKNOWN",
        "evidence": ("named in our own recorded contract objects from the "
                     "2026-10-02T17:50Z expiry onward"),
        "unknown": ("the provider, the statistic, and the window -- nothing "
                    "about it is established, and it governs the only tape "
                    "that carries a reference series")},
}


def verification(path: str) -> Dict[str, Any]:
    """Score the equal-weight 60s proxy against published settlements."""
    if not os.path.exists(path):
        return {"ran": False, "why": "ref_lag_study.json not present"}
    d = json.load(open(path, encoding="utf-8"))
    pe = [r for r in (d.get("proxy_errors") or [])
          if r.get("abs_err") is not None and r.get("proxy_value")]
    btc = [r for r in pe if "BTC" in str(r.get("market"))]
    if not btc:
        return {"ran": False, "why": "no BTC rows"}
    ae = sorted(r["abs_err"] for r in btc)
    bp = sorted(10000.0 * r["abs_err"] / r["proxy_value"] for r in btc)
    return {
        "ran": True,
        "proxy_definition": ("equal-weight mean of SINGLE-VENUE Gemini spot "
                             "trades over the final 60s"),
        "scored_against": "Gemini's own published settlement value",
        "btc_windows": len(btc),
        "by_duration": dict(collections.Counter(
            "BTC05M" if "BTC05M" in r["market"] else "BTC15M" for r in btc)),
        "abs_err_usd": {"median": round(statistics.median(ae), 2),
                        "p75": round(ae[3 * len(ae) // 4], 2),
                        "p90": round(ae[9 * len(ae) // 10], 2),
                        "max": round(ae[-1], 2)},
        "abs_err_bps": {"median": round(statistics.median(bp), 3),
                        "p90": round(bp[9 * len(bp) // 10], 3),
                        "max": round(bp[-1], 3)},
        "sign_of_settlement_minus_strike_agreed": (
            "%d of %d" % (sum(1 for r in btc if r.get("side_agrees")),
                          len(btc))),
        "reading": ("the SIGN is reproduced in every window, so a crude "
                    "proxy answers 'which side settles'. The LEVEL is not: a "
                    "median $8.39 error is the same order as the quantity Z "
                    "must measure."),
        "all_assets_note": ("the artifact pools XRP/SOL/ETH/BTC, so its "
                            "dollar statistics are meaningless pooled; only "
                            "BTC rows are used here"),
    }


def resolvability(ladder: str, median_err_usd: float,
                  p90_err_usd: float) -> Dict[str, Any]:
    """How often is the settlement margin SMALLER than the proxy's error?"""
    rows = json.load(open(ladder, encoding="utf-8"))
    per_window = {}
    for r in rows:
        if r.get("settlement_value") and r.get("strike"):
            per_window[r["ticker"]] = abs(float(r["settlement_value"])
                                          - float(r["strike"]))
    m = sorted(per_window.values())
    if not m:
        return {"windows": 0}
    return {
        "windows": len(m),
        "abs_settlement_margin_usd": {
            "median": round(statistics.median(m), 2),
            "p25": round(m[len(m) // 4], 2),
            "p10": round(m[len(m) // 10], 2),
            "min": round(m[0], 2)},
        "windows_margin_below_median_proxy_error": {
            "threshold_usd": round(median_err_usd, 2),
            "count": sum(1 for x in m if x < median_err_usd),
            "pct": round(100.0 * sum(1 for x in m if x < median_err_usd)
                         / len(m), 1)},
        "windows_margin_below_p90_proxy_error": {
            "threshold_usd": round(p90_err_usd, 2),
            "count": sum(1 for x in m if x < p90_err_usd),
            "pct": round(100.0 * sum(1 for x in m if x < p90_err_usd)
                         / len(m), 1)},
        "reading": ("in this share of windows the reconstruction error alone "
                    "is as large as the entire distance to the strike, so a "
                    "Z built on such a proxy cannot resolve the outcome at "
                    "the precision the question requires"),
    }


def power_check(ladder: str) -> Dict[str, Any]:
    """Can the same-price test be run AT ALL on this corpus, whatever Z is?

    The test is: within one executable-price x TTE bucket, do Z strata
    separate winners from losers after fees? Its unit is the WINDOW, so the
    answer depends only on windows per cell -- which is knowable now.
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    from scripts.gemini_favorite_persistence import BUCKETS, build
    recs = build([r for r in json.load(open(ladder, encoding="utf-8"))
                  if str(r.get("ticker", "")).startswith("BTC05M")])
    cells: List[Dict[str, Any]] = []
    for cp in sorted({r["checkpoint_s"] for r in recs}, reverse=True):
        for name, lo, hi in BUCKETS:
            sub = [r for r in recs if r["checkpoint_s"] == cp
                   and r["favorite_bucket"] == name]
            if not sub:
                continue
            w = len({r["window"] for r in sub})
            cells.append({"checkpoint_s": cp, "bucket": name, "windows": w,
                          "observations": len(sub),
                          "windows_per_Z_tercile": round(w / 3.0, 1)})
    buckets = collections.Counter(
        "usable (>=10 windows/tercile)" if c["windows_per_Z_tercile"] >= 10
        else ("marginal (5-10)" if c["windows_per_Z_tercile"] >= 5
              else "underpowered (<5)") for c in cells)
    return {
        "total_windows": len({r["window"] for r in recs}),
        "cells": len(cells),
        "feasibility": dict(buckets),
        "largest_cell_windows": max((c["windows"] for c in cells),
                                    default=0),
        "largest_cells": sorted(cells, key=lambda c: -c["windows"])[:8],
        "requirement": ("detecting a 10pp win-rate separation between extreme "
                        "Z strata at a ~70% base rate needs on the order of "
                        "300 windows per cell at 80% power; the largest cell "
                        "here holds 15"),
        "verdict": ("the same-price Z test is UNRUNNABLE on this corpus "
                    "regardless of how Z is defined. Identifying the Kaiko "
                    "construction is necessary but not sufficient: the "
                    "window count is the binding constraint."),
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder",
                    default="analysis/gemini/info_edge/ie_tte_ladder.json")
    ap.add_argument("--reflag",
                    default="analysis/gemini/ref_lag_study.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    ver = verification(a.reflag)
    res = (resolvability(a.ladder,
                         ver["abs_err_usd"]["median"],
                         ver["abs_err_usd"]["p90"])
           if ver.get("ran") else {"skipped": True})
    rep = {
        "label": LABEL,
        "lane": "GEMINI -- no PMUS formula, data or assumption is reused",
        "Z_STATUS": "NOT_IDENTIFIED",
        "why_not_identified": [
            "the partition size/count, intra-partition statistic, "
            "cross-partition weighting, venue set, publication timing and "
            "missing-data rules of GRR-KAIKO_RFR_BTCUSD_60S are all unknown "
            "here, and Kaiko's domains are denied by this environment's "
            "egress policy",
            "the weighting function is precisely what the variance of the "
            "REMAINING settlement statistic depends on, so no normalization "
            "can be written down without it",
            "the only Gemini tape with a reference series is the LS-LRRR era, "
            "whose index construction is entirely undocumented here -- so "
            "even a fully identified Kaiko spec would not license Z on it",
            "verified reconstruction error is of the same order as the "
            "settlement margin it would have to resolve",
        ],
        "what_is_authoritative": {
            "openapi_sha256": ("53caea563447398132afe9f9c45bfe45398526d03b"
                               "8308c2603ebeb63069d165"),
            "source_details": ("agency/index per contract; index exampled "
                               "GRR-KAIKO_BTCUSD_60S"),
            "strike": ("StrikeType.reference, captured at availableAt, null "
                       "until then"),
            "indices_observed_in_our_own_records": {
                "GRR-KAIKO_RFR_BTCUSD_60S": "through 2026-10-02T17:45Z expiry",
                "LS-LRRR-XBTUSD": "from 2026-10-02T17:50Z expiry onward"},
            "resolved_reference_contracts": 1322,
        },
        "secondary_not_authoritative": (
            "a search-engine summary of Kaiko's general approach (equal-size "
            "time partitions, volume-weighted median per partition, "
            "recency-favouring time weighting, real-time windows from "
            "15/20/30/60/120/300s). Family description, not this ticker's "
            "parameterization. Not used as if authoritative."),
        "required_parameters": REQUIRED_PARAMETERS,
        "estimator_form_already_in_repo": {
            "module": "src/gemini_maker/fair_value.py",
            "pre_window_variance": "sigma^2 * ((tau - w) + w/3)",
            "in_window_variance": "sigma^2 * tau^3 / (3 w^2)",
            "expected_settle_in_window": "(realized*(w-tau) + spot*tau)/w",
            "is_not": "spot distance over sigma*sqrt(T)",
            "blocking_assumption": ("both variances assume an EQUAL-WEIGHT "
                                    "time average over a fixed w; a "
                                    "partitioned volume-weighted median with "
                                    "recency weighting changes the weight "
                                    "function, hence the realized share AND "
                                    "the remaining variance"),
            "third_distinct_statistic_in_repo": (
                "src/gemini_maker/ref_lag_recorder.py averages TRADES "
                "(sum(p)/len(dq)) rather than time, which is neither the "
                "equal-weight time average nor Kaiko's statistic"),
        },
        "verification_attempt": ver,
        "resolvability": res,
        "same_price_test_power": power_check(a.ladder),
        "unconditional_calibration": (
            "unaffected and already delivered: see "
            "analysis/gemini/updown/underdog_matrix.json and "
            "favorite_persistence.json. Those results stand without Z."),
        "accounting_label": ("documentation and historical replay on Gemini's "
                             "own records; no order was sent"),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s  Z_STATUS=%s  unknown_params=%d/%d  power=%s"
          % (LABEL, rep["Z_STATUS"],
             sum(1 for v in REQUIRED_PARAMETERS.values()
                 if v["status"] == "UNKNOWN"), len(REQUIRED_PARAMETERS),
             rep["same_price_test_power"]["feasibility"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
