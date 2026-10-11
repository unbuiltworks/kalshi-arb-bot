"""EXPLORATORY / IN-SAMPLE / NOT VALIDATION.

A COVERAGE CENSUS FIRST, because the answer turns out to be a coverage
answer. It asks what the PRE-EXISTING historical Gemini BTC5m corpus can
actually support for the chained-strike hypothesis, which needs FOUR things
joined per window:

  1. an authoritative current-window strike;
  2. the settlement outcome;
  3. CONTEMPORANEOUS observations of the published settlement reference;
  4. executable ladder state at early checkpoints.

It manufactures nothing. Where a requirement is absent it is reported
absent, and no proxy is substituted for it.

IT TOUCHES NEITHER FROZEN ANALYZER. It does not read, write or influence
gemini_early_window_analysis.py (f2800e3e...), the prospective study
gemini_btc5m_chained_study.py (b2f6d50a...), their candidates, thresholds,
selection rule or OOS boundary. It reads only pre-existing historical
artifacts, named explicitly below, and never the prospective recorder's
tapes.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

LABEL = "GEMINI_CHAINED_HISTORICAL_FEASIBILITY_EXPLORATORY"
LADDER = "analysis/gemini/info_edge/ie_tte_ladder.json"
#: the checkpoints the hypothesis is stated at, as T+ seconds since t0
WANTED_CHECKPOINTS = (15, 30, 45, 60, 90, 120, 150, 180)
#: BTC5m life, so tte <-> T+ conversion is explicit rather than implied
LIFE_S = 300
TAKER_RATE = D("0.07")


def _d(v: Any) -> Optional[D]:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def reference_corpus_census() -> Dict[str, Any]:
    """Does ANY pre-existing artifact carry a timestamped reference series?

    Requirement 3 is the one that decides this study, so it is searched for
    explicitly rather than assumed either way. The prospective recorder's
    own tapes are EXCLUDED by path: they belong to the frozen prospective
    lane and using them here would contaminate it.
    """
    # A NAME IS NOT A SERIES. The first draft put the index's NAME in with
    # the value keys, so four artifacts that merely quote
    # "GRR-KAIKO_RFR_BTCUSD_60S" as contract metadata made the verdict read
    # PRESENT. Those files carry no reference VALUES at all. The two kinds
    # are counted separately now and only the value keys decide.
    value_keys = ("index_price", "thirdPartyPrice", "parsed_value",
                  "indexPrice")
    name_keys = ("GRR-KAIKO_RFR_BTCUSD_60S", "LS-LRRR-XBTUSD", "LRRR_XBT")
    found: List[Dict[str, Any]] = []
    named: List[Dict[str, Any]] = []
    scanned = 0
    for f in sorted(glob.glob("analysis/**/*.json", recursive=True)
                    + glob.glob("analysis/**/*.jsonl", recursive=True)):
        if "btc5m_rec" in f:
            continue
        scanned += 1
        try:
            txt = open(f, encoding="utf-8").read()
        except Exception:                                      # noqa: BLE001
            continue
        vh = {k: txt.count('"%s"' % k) for k in value_keys
              if '"%s"' % k in txt}
        nh = {k: txt.count(k) for k in name_keys if k in txt}
        if vh:
            found.append({"file": f, "value_hits": vh})
        if nh:
            named.append({"file": f, "name_only_hits": nh})
    return {
        "files_scanned": scanned,
        "files_with_a_reference_VALUE_series": found,
        "files_merely_NAMING_the_index": named,
        "verdict": ("PRESENT" if found else
                    "ABSENT -- no pre-existing artifact carries a "
                    "timestamped settlement-reference value series"),
        "excluded_by_path": ("analysis/gemini/btc5m_rec/* -- the prospective "
                             "recorder's tapes, which belong to the frozen "
                             "prospective lane"),
        "note": ("the index stream was only established on 2026-10-05, in "
                 "recorder run 5; the settlement reference also CHANGED "
                 "mid-programme (GRR-KAIKO_RFR_BTCUSD_60S through the "
                 "2026-10-02T17:45Z expiry, LS-LRRR-XBTUSD from 17:50Z), so "
                 "even a recovered pre-10-02 series would be a different "
                 "index from the one the hypothesis is stated on"),
    }


def ladder_census() -> Dict[str, Any]:
    """Requirements 1, 2 and 4 in the pre-existing executable corpus."""
    if not os.path.exists(LADDER):
        return {"file": LADDER, "present": False}
    raw = [r for r in json.load(open(LADDER, encoding="utf-8"))
           if str(r.get("ticker", "")).startswith("BTC05M")]
    by: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in raw:
        by[r["ticker"]].append(r)
    ttes = sorted({r.get("tte") for r in raw if r.get("tte") is not None},
                  reverse=True)
    have_strike = {tk for tk, rs in by.items()
                   if any(_d(r.get("strike")) is not None for r in rs)}
    have_settle = {tk for tk, rs in by.items()
                   if any(r.get("up_won") is not None for r in rs)}
    have_ref = set()          # requirement 3, measured not assumed
    for tk, rs in by.items():
        for r in rs:
            for k in ("reference", "lrrr", "index_price", "reference_value"):
                if r.get(k) is not None:
                    have_ref.add(tk)
    # which WANTED checkpoints does the tte grid actually reach?
    reachable = {}
    for cp in WANTED_CHECKPOINTS:
        tte = LIFE_S - cp
        reachable["T+%ds" % cp] = {
            "equivalent_tte_s": tte,
            "present_in_grid": tte in ttes,
            "windows_observed": sum(
                1 for rs in by.values()
                if any(r.get("tte") == tte for r in rs)),
        }
    # executable depth at the single reachable checkpoint, by size
    depth: Dict[str, Any] = {}
    cp180 = [r for rs in by.values() for r in rs if r.get("tte") == 120]
    for q in (1, 5, 10, 25):
        up = [r for r in cp180
              if (r.get("up_ask_sz") or 0) >= q and r.get("up_ask")]
        dn = [r for r in cp180
              if (r.get("down_sz") or 0) >= q and r.get("down_price")]
        both = [r for r in cp180
                if (r.get("up_ask_sz") or 0) >= q
                and (r.get("down_sz") or 0) >= q
                and r.get("up_ask") and r.get("down_price")]
        depth["q%d" % q] = {
            "observations": len(cp180),
            "up_fillable": len(up), "down_fillable": len(dn),
            "both_fillable": len(both),
            "both_fillable_pct": (round(100.0 * len(both) / len(cp180), 2)
                                  if cp180 else None),
        }
    return {
        "file": LADDER, "present": True,
        "btc5m_rows": len(raw), "btc5m_windows": len(by),
        "tte_grid_s": ttes,
        "earliest_observation_is_T_plus_s": (LIFE_S - max(ttes)
                                             if ttes else None),
        "windows_with_authoritative_strike": len(have_strike),
        "windows_with_settlement": len(have_settle),
        "windows_with_a_contemporaneous_reference_observation": len(have_ref),
        "windows_with_all_four_requirements": len(
            have_strike & have_settle & have_ref),
        "wanted_checkpoint_reachability": reachable,
        "executable_depth_at_the_one_reachable_checkpoint": depth,
    }


def proxy_bound() -> Dict[str, Any]:
    """COULD a proxy have answered it? A bound, NOT a substitute analysis.

    The historical corpus carries a COINBASE SPOT field, not the settlement
    reference. Substituting it would be answering a different question, so
    this reports only whether such a study could have been informative at
    the magnitudes the hypothesis cares about -- measured BTC-only, because
    the earlier pooled figure mixed XRP/SOL/ETH/BTC and was meaningless.
    """
    return {
        "what_exists": ("a `coinbase` spot price and a `cb_dist` distance, "
                        "per row -- a PROXY for the reference, never the "
                        "published reference itself"),
        "measured_proxy_error_btc_only": {
            "median_usd": 8.39, "median_bps": 1.08, "windows": 16,
            "source": "ref_lag_study.json, recomputed BTC-only",
        },
        "thresholds_the_hypothesis_states": {
            "5_bps_at_85k_usd": 42.5, "25_bps_at_85k_usd": 212.5},
        "bound": ("a 1.08 bps median proxy error is ~5x smaller than the "
                  "5 bps threshold and ~23x smaller than 25 bps, so a "
                  "proxy study would NOT be swamped by proxy error at those "
                  "magnitudes -- but it would still be a different signal, "
                  "and the checkpoint gap below blocks it anyway"),
        "not_run_because": ("substituting another signal was explicitly "
                            "excluded, and the corpus reaches only one of "
                            "the eight stated checkpoints regardless"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=("analysis/gemini/updown/"
                                      "CHAINED_HISTORICAL_FEASIBILITY.json"))
    a = ap.parse_args(argv)
    ref = reference_corpus_census()
    lad = ladder_census()
    blockers = []
    if ref["verdict"].startswith("ABSENT"):
        blockers.append("NO_CONTEMPORANEOUS_REFERENCE_SERIES")
    if lad.get("present") and not lad[
            "windows_with_all_four_requirements"]:
        blockers.append("ZERO_WINDOWS_WITH_ALL_FOUR_REQUIREMENTS")
    reach = [k for k, v in (lad.get("wanted_checkpoint_reachability") or {}
                            ).items() if v["present_in_grid"]]
    if len(reach) < len(WANTED_CHECKPOINTS):
        blockers.append("CHECKPOINT_GRID_REACHES_ONLY_%d_OF_%d"
                        % (len(reach), len(WANTED_CHECKPOINTS)))
    rep = {
        "label": LABEL,
        "status": "EXPLORATORY / IN-SAMPLE / NOT VALIDATION",
        "touches_frozen_analyzers": False,
        "frozen_untouched": ["gemini_early_window_analysis.py "
                             "f2800e3e55672485708a7e0ebd167d92d69bb160f7091"
                             "ccd8b43f6a6c960e90b",
                             "gemini_btc5m_chained_study.py "
                             "b2f6d50a035b56c0edc4efea49d4ade8fc5eec292e2da"
                             "da2e58e405a142f9bc6"],
        "reference_corpus": ref,
        "executable_corpus": lad,
        "proxy_feasibility_bound": proxy_bound(),
        "blockers": blockers,
        "answerable": not blockers,
        "accounting": ("census of pre-existing recorded data; no order was "
                       "sent and nothing here is realized"),
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, sort_keys=True, default=str)
    print(json.dumps({"label": LABEL, "answerable": rep["answerable"],
                      "blockers": blockers,
                      "ref_verdict": ref["verdict"][:60],
                      "windows_all_four": lad.get(
                          "windows_with_all_four_requirements"),
                      "btc5m_windows": lad.get("btc5m_windows"),
                      "earliest_T_plus_s": lad.get(
                          "earliest_observation_is_T_plus_s")},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
