"""CHAINED-D V2 -- a NEW preregistered study. RESEARCH ONLY. No live trading.

V1 IS NOT MODIFIED OR OVERWRITTEN. `gemini_btc5m_chained_study.py`
(0b746c20...5ef553bd, superseding b2f6d50a...142f9bc6 for the loader defect
alone) stands as a valid, documented NULL-SELECTION result: its candidate
thresholds of +/-5 and +/-25 bps sat largely outside the observed |D|
distribution, so its candidates were untested at the data's scale rather than
refuted. V2 exists to test the same economic question at the right scale.

THE ECONOMIC QUESTION IS UNCHANGED:

    does causal D_t = published LRRR_XBT(t) - the AUTHORITATIVE strike
    contain information NOT adequately reflected in the executable
    contract price?

WHAT V2 CHANGES, and nothing else:

  1. SCALE. The +/-5 / +/-25 bps grid is replaced by thresholds DERIVED FROM
     THE DISCOVERY FEATURE DISTRIBUTION at predeclared quantile levels --
     0.50, 0.70, 0.85, 0.95 of |D_bps|. The LEVELS are the preregistered
     constants; the bps values are whatever the discovery features say they
     are. No cutoff is chosen by looking at an outcome or a P&L.
  2. D IS EVALUATED AS INFORMATION, not merely as a threshold crossing:
     settlement probability and executable breakeven conditional on D's SIGN
     and on its MAGNITUDE bucket, plus an explicit MONOTONICITY test of
     whether larger positive/negative D moves subsequent UP settlement
     probability in order.
  3. INCORPORATION is reported directly: after observing D at checkpoint t,
     does the executable UP price subsequently move in the direction D
     implies, and at what lag.

EVERYTHING ELSE IS INHERITED FROM V1 BY IMPORT, not reimplemented, so it
cannot drift: the checkpoints, the cumulative executable VWAP construction,
the actual fee treatment and breakeven, the integrity and freshness
requirements, NO_TRADE accounting in every per-window denominator, capacity
reporting, the causal constraints and leakage assertion, the excluded
probe-contaminated tickers, and the discovery/OOS split rule and seal.

THRESHOLD DERIVATION TOUCHES NO OUTCOME. `derive_thresholds()` receives a
list of FEATURE dicts only. It has no access to a settlement, and
`assert_features_only()` refuses any key naming one. The sealed OOS
partition is neither read nor used in the derivation.

OOS STAYS SEALED. V2 reads the discovery partition only, under V1's own
split rule and OOS_FRACTION, so the sealed window set is identical to V1's.
It is unsealed only after V2's candidate-selection procedure is frozen --
which this file does NOT do: it reports the conditional structure and leaves
selection to a later, separately frozen step.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import statistics
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

# V1, IMPORTED AND NOT TOUCHED. Every inherited rule comes from here.
from scripts import gemini_btc5m_chained_study as V1

LABEL = "GEMINI_BTC5M_CHAINED_D_STUDY_V2_PREREGISTERED"
V1_FINGERPRINT_EXPECTED = (
    "0b746c20fba27dd1891c9dab7a8f922dbfc2ed1bb6721363db19c1545ef553bd")

#: PREREGISTERED QUANTILE LEVELS of |D_bps|, computed on DISCOVERY FEATURES
#: ONLY. These levels are the fixed constants; the bps values they resolve to
#: are whatever the feature distribution says. This is the whole point: a
#: distribution-derived scale rather than an outcome-selected cutoff.
QUANTILE_LEVELS: Tuple[float, ...] = (0.50, 0.70, 0.85, 0.95)
#: magnitude buckets for the information tables, as quantile levels of |D|
MAGNITUDE_BUCKET_LEVELS: Tuple[float, ...] = (0.0, 0.25, 0.50, 0.75, 1.0)
#: lags, in checkpoint steps, for the incorporation study
INCORPORATION_LAG_STEPS: Tuple[int, ...] = (1, 2, 3)

FEATURE_FORBIDDEN = ("won", "resolution", "settle", "eventual", "winner",
                     "payout", "future", "outcome", "pnl", "ev_")


def assert_features_only(feat: Dict[str, Any]) -> None:
    """A threshold may not be derived from anything naming an outcome."""
    for k in feat:
        low = k.lower()
        for w in FEATURE_FORBIDDEN:
            if w in low:
                raise AssertionError(
                    "threshold derivation saw %r, which names an outcome "
                    "(%r)" % (k, w))


def self_fingerprint() -> str:
    return hashlib.sha256(open(__file__, "rb").read()).hexdigest()


def quantile(vals: List[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    if q <= 0:
        return s[0]
    if q >= 1:
        return s[-1]
    i = q * (len(s) - 1)
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def derive_thresholds(features: List[Dict[str, Any]]) -> Dict[str, Any]:
    """|D_bps| quantiles at the preregistered levels. FEATURES ONLY.

    This function receives feature dicts and nothing else -- no settlement,
    no price outcome, no P&L -- and every one is checked by
    assert_features_only before a number is taken from it.
    """
    for f in features:
        assert_features_only(f)
    ad = [abs(float(f["D_bps"])) for f in features
          if f.get("D_bps") is not None]
    out = {
        "n_discovery_observations": len(ad),
        "levels": list(QUANTILE_LEVELS),
        "thresholds_bps": {("q%02d" % int(q * 100)): quantile(ad, q)
                           for q in QUANTILE_LEVELS},
        "abs_D_bps_distribution": ({
            "min": min(ad), "median": statistics.median(ad),
            "mean": round(statistics.fmean(ad), 4), "max": max(ad),
            "p25": quantile(ad, 0.25), "p75": quantile(ad, 0.75),
            "p90": quantile(ad, 0.90)} if ad else None),
        "derivation": ("quantiles of |D_bps| over DISCOVERY observations "
                       "only. No outcome, price result or P&L is visible to "
                       "this computation, and the sealed OOS partition is "
                       "not read."),
        "why_not_5_and_25_bps": (
            "V1's grid was +/-5 and +/-25 bps = $42.50 and $212.50 at an "
            "85,000 strike. The observed |D| had median 1.48 bps and never "
            "reached 25, so 31 of 32 candidates could not fire. V1 stands as "
            "a null-selection result; it is not evidence about D."),
    }
    return out


# ------------------------------------------------------ D AS INFORMATION
def _wilson(k: int, n: int) -> Optional[List[float]]:
    return V1.wilson(k, n)


def information_tables(obs: List[Dict[str, Any]], outcomes: Dict[str, Dict],
                       thr: Dict[str, Any]) -> Dict[str, Any]:
    """Settlement probability and executable breakeven by D sign/magnitude.

    DISTINCT WINDOWS ARE REPORTED BESIDE EVERY CELL, because the lesson from
    the early-window analyzer is that six checkpoints of one window are one
    window, not six observations. A win rate identical across checkpoints is
    a base rate, not a signal.
    """
    def cell(rows: List[Dict[str, Any]], side: str) -> Optional[Dict]:
        use = []
        for o in rows:
            f = o["features"]
            px = V1._dec(f["UP_vwap"] if side == "UP" else f["DOWN_vwap"])
            res = (outcomes.get(o["ticker"]) or {}).get("up_resolution_side")
            if px is None or res not in ("yes", "no"):
                continue
            won = ((side == "UP" and res == "yes")
                   or (side == "DOWN" and res == "no"))
            use.append((o["ticker"], px, won))
        if not use:
            return None
        n = len(use)
        wins = sum(1 for _, _, w in use if w)
        avg = sum((p for _, p, _ in use), D("0")) / n
        be = V1.breakeven(avg)
        # V1.fee_for takes (price, size); size is 1 contract throughout V2,
        # matching the q=1 observations it is built on.
        ev = sum((((D("1") if w else D("0")) - p - V1.fee_for(p, D("1")))
                  for _, p, w in use), D("0")) / n
        return {
            "observations": n,
            "distinct_windows": len({t for t, _, _ in use}),
            "settlement_win_pct": round(100.0 * wins / n, 2),
            "wilson95": _wilson(wins, n),
            "avg_executable_vwap": str(avg.quantize(D("0.0001"))),
            "breakeven_pct_with_fees": round(float(be) * 100, 2),
            "win_minus_breakeven_pp": round(
                100.0 * wins / n - float(be) * 100, 2),
            "ev_per_trade_usd": str(ev.quantize(D("0.0001"))),
            "caution": ("observations within a window are NOT independent; "
                        "read distinct_windows, not observations"),
        }

    out: Dict[str, Any] = {"by_D_sign": {}, "by_D_magnitude_bucket": {},
                           "by_signed_magnitude": {}}
    for name, pred in (("D_POSITIVE", lambda f: float(f["D_bps"]) > 0),
                       ("D_NEGATIVE", lambda f: float(f["D_bps"]) < 0),
                       ("D_ZERO", lambda f: float(f["D_bps"]) == 0)):
        sub = [o for o in obs if pred(o["features"])]
        out["by_D_sign"][name] = {s: cell(sub, s) for s in ("UP", "DOWN")}

    ad = sorted(abs(float(o["features"]["D_bps"])) for o in obs)
    edges = [quantile(ad, q) for q in MAGNITUDE_BUCKET_LEVELS]
    out["magnitude_bucket_edges_bps"] = edges
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        last = i == len(edges) - 2
        sub = []
        for o in obs:
            v = abs(float(o["features"]["D_bps"]))
            if (lo <= v <= hi) if last else (lo <= v < hi):
                sub.append(o)
        key = "[%.2f,%.2f%s" % (lo, hi, "]" if last else ")")
        out["by_D_magnitude_bucket"][key] = {
            s: cell(sub, s) for s in ("UP", "DOWN")}
    # signed magnitude, for the monotonicity question as asked
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        last = i == len(edges) - 2
        for sign, sgn in (("POS", 1), ("NEG", -1)):
            sub = []
            for o in obs:
                v = float(o["features"]["D_bps"]) * sgn
                if v < 0:
                    continue
                if (lo <= v <= hi) if last else (lo <= v < hi):
                    sub.append(o)
            out["by_signed_magnitude"]["%s_[%.2f,%.2f%s" % (
                sign, lo, hi, "]" if last else ")")] = {
                    "UP": cell(sub, "UP")}
    # MONOTONICITY: does UP settlement probability move in order with D?
    mono = []
    for key, v in out["by_signed_magnitude"].items():
        c = v.get("UP")
        if c:
            mono.append({"bucket": key, "up_win_pct": c[
                "settlement_win_pct"], "distinct_windows": c[
                "distinct_windows"], "observations": c["observations"]})
    pos = [m for m in mono if m["bucket"].startswith("POS")]
    neg = [m for m in mono if m["bucket"].startswith("NEG")]

    def ordered(seq) -> Optional[bool]:
        vals = [m["up_win_pct"] for m in seq]
        if len(vals) < 2:
            return None
        return all(b >= a for a, b in zip(vals, vals[1:]))
    out["monotonicity"] = {
        "positive_D_buckets": pos,
        "negative_D_buckets": neg,
        "up_win_rises_with_positive_D": ordered(pos),
        "up_win_rises_with_negative_D_magnitude": ordered(neg),
        "reading": ("the question is whether LARGER positive D raises "
                    "subsequent UP settlement probability and larger "
                    "negative D lowers it. With few distinct windows per "
                    "bucket an ordering is a description, not a test."),
    }
    return out


# ------------------------------------------------------------ INCORPORATION
def incorporation(obs_by_window: Dict[str, Dict[int, Dict[str, Any]]]
                  ) -> Dict[str, Any]:
    """After observing D at t, does the executable UP price move as D implies?

    The predictor is causal D at t. The outcome is a LATER PRICE, never a
    settlement. Sign agreement is reported per lag: if the price already
    embeds D, agreement sits at chance and there is no lead to trade.
    """
    rows = []
    for tk, cps in obs_by_window.items():
        ks = sorted(cps)
        for i, c in enumerate(ks):
            f0 = cps[c]
            p0 = V1._dec(f0["UP_vwap"])
            d0 = float(f0["D_bps"])
            if p0 is None or d0 == 0:
                continue
            for lag in INCORPORATION_LAG_STEPS:
                j = i + lag
                if j >= len(ks):
                    continue
                f1 = cps[ks[j]]
                p1 = V1._dec(f1["UP_vwap"])
                if p1 is None:
                    continue
                dp = float(p1 - p0)
                rows.append({"ticker": tk, "from_s": c, "to_s": ks[j],
                             "lag_steps": lag, "D_bps_at_t": d0,
                             "up_price_change": dp,
                             "agrees_with_D_sign": (dp > 0) == (d0 > 0)
                             if dp != 0 else None})
    out: Dict[str, Any] = {"pairs": len(rows), "by_lag": {}}
    for lag in INCORPORATION_LAG_STEPS:
        sub = [r for r in rows if r["lag_steps"] == lag]
        dec = [r for r in sub if r["agrees_with_D_sign"] is not None]
        xs = [r["D_bps_at_t"] for r in sub]
        ys = [r["up_price_change"] for r in sub]
        corr = None
        if len(xs) >= 3:
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            sxx = sum((x - mx) ** 2 for x in xs)
            syy = sum((y - my) ** 2 for y in ys)
            if sxx > 0 and syy > 0:
                corr = round(sxy / (sxx ** 0.5 * syy ** 0.5), 4)
        agree = sum(1 for r in dec if r["agrees_with_D_sign"])
        out["by_lag"]["lag_%d_checkpoints" % lag] = {
            "pairs": len(sub),
            "distinct_windows": len({r["ticker"] for r in sub}),
            "decidable_pairs": len(dec),
            "sign_agreement_pct": (round(100.0 * agree / len(dec), 2)
                                   if dec else None),
            "sign_agreement_wilson95": _wilson(agree, len(dec)) if dec
            else None,
            "corr_D_vs_later_price_change": corr,
            "zero_change_pairs": len(sub) - len(dec),
        }
    out["reading"] = (
        "sign agreement at ~50% means the executable price already embeds D "
        "and there is NO informational lead. Above 50% means the price moves "
        "AFTER the reference does, which is the only version that could pay. "
        "The outcome here is a PRICE, never a settlement.")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes",
                    default="analysis/gemini/btc5m_snap/btc5m_rec_*.jsonl.gz")
    ap.add_argument("--tranche", default="V2_T1")
    ap.add_argument("--out", default=("analysis/gemini/updown/"
                                      "chained_v2.json"))
    a = ap.parse_args(argv)

    live_v1 = V1.self_fingerprint()
    rep: Dict[str, Any] = {
        "label": LABEL,
        "fingerprint_sha256": self_fingerprint(),
        "v1_fingerprint_expected": V1_FINGERPRINT_EXPECTED,
        "v1_fingerprint_live": live_v1,
        "v1_unmodified": live_v1 == V1_FINGERPRINT_EXPECTED,
        "v1_status": ("V1 is a VALID documented NULL-SELECTION result. Its "
                      "+/-5 and +/-25 bps candidate thresholds sat largely "
                      "outside the observed |D| distribution, so its "
                      "candidates were untested at the data's scale rather "
                      "than refuted. V2 does not modify or overwrite it."),
        "economic_question": ("does causal D_t = published LRRR_XBT(t) minus "
                             "the AUTHORITATIVE strike contain information "
                             "NOT adequately reflected in the executable "
                             "contract price"),
        "inherited_from_v1_by_import": [
            "CHECKPOINTS", "SIZE_LEVELS", "cumulative executable VWAP",
            "fee_for / breakeven (actual fee treatment)",
            "integrity and freshness requirements", "observations()",
            "assert_no_leakage", "capacity()", "EXCLUDED_TICKERS",
            "OOS_FRACTION and the discovery/OOS split rule"],
        "what_v2_changes": [
            "threshold SCALE: distribution-derived |D| quantiles at the "
            "preregistered levels %s, replacing +/-5 / +/-25 bps"
            % (list(QUANTILE_LEVELS),),
            "D evaluated as INFORMATION: settlement probability and "
            "executable breakeven by D sign and magnitude, plus an explicit "
            "monotonicity report",
            "INCORPORATION: sign agreement and correlation between causal D "
            "at t and the LATER executable UP price, by lag"],
        "no_live_authorization": True,
        "oos": ("SEALED. V2 reads the DISCOVERY partition only, under V1's "
                "own split rule and OOS_FRACTION, so the sealed set is "
                "identical to V1's. V2 does NOT freeze a candidate-selection "
                "procedure and does not unseal anything."),
        "tranche": a.tranche,
    }
    if not rep["v1_unmodified"]:
        rep["refused"] = "V1 has changed; V2 will not run against a moved V1"
        print(json.dumps({"label": LABEL, "refused": rep["refused"]}))
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2,
                  sort_keys=True, default=str)
        return 1

    rows = V1.tape_rows(a.tapes)
    wins = V1.windows(rows)
    outcomes = V1.settlements(rows)
    settled = sorted([tk for tk in wins
                      if (outcomes.get(tk) or {}).get("up_resolution_side")
                      in ("yes", "no")])
    n_seal = int(len(settled) * V1.OOS_FRACTION)
    oos = set(settled[len(settled) - n_seal:]) if n_seal else set()
    use = [tk for tk in settled if tk not in oos]

    obs: List[Dict[str, Any]] = []
    for tk in use:
        obs.extend(V1.observations(wins[tk], V1.reference_series(rows),
                                   D("1")))
    feats = [o["features"] for o in obs]

    # SCALE FIRST, FROM FEATURES ONLY, BEFORE ANY OUTCOME IS READ.
    rep["scale"] = derive_thresholds(feats)
    rep["windows_settled_total"] = len(settled)
    rep["windows_sealed_oos"] = n_seal
    rep["windows_used_discovery"] = len(use)
    rep["discovery_observations"] = len(obs)
    rep["capacity"] = V1.capacity({1: obs})

    # only now are outcomes consulted, for the conditional structure
    rep["information"] = information_tables(obs, outcomes, rep["scale"])
    by_w: Dict[str, Dict[int, Dict[str, Any]]] = collections.defaultdict(dict)
    for o in obs:
        by_w[o["ticker"]][o["features"]["checkpoint_s"]] = o["features"]
    rep["incorporation"] = incorporation(by_w)

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2,
              sort_keys=True, default=str)
    print(json.dumps({"label": LABEL,
                      "fingerprint": rep["fingerprint_sha256"],
                      "v1_unmodified": True,
                      "windows_discovery": len(use),
                      "windows_sealed": n_seal,
                      "observations": len(obs),
                      "thresholds_bps": rep["scale"]["thresholds_bps"]},
                     sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
