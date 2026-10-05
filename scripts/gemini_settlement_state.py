"""DOES THE EXECUTABLE GEMINI PRICE REFLECT THE ACTUAL SETTLEMENT STATE?

Descriptive and calibration surfaces only. NO threshold is optimized here and
no rule is selected; the unconditional matrix was produced and frozen first
(`gemini_underdog_matrix.py`), and this asks the separate question of whether
the price is explained by the causal state.

THE CAUSAL FEED, AND THE HONEST GAP. Gemini's own contract objects name the
settlement reference in `sourceDetails.index`. Our recorded venue data carries
TWO values, and they changed mid-day:

    GRR-KAIKO_RFR_BTCUSD_60S   BTC5m/BTC15m through the 2026-10-02T17:45Z
                               expiry   (agency: Kaiko)
    LS-LRRR-XBTUSD             from the 2026-10-02T17:50Z expiry onward

The BTC5m ladder tape used here is entirely the LS-LRRR era. We do NOT have a
historical series of either index. What the tape carries is a COINBASE SPOT
price sampled at each checkpoint, which is a PROXY and is labelled as one
everywhere below. Distance-from-strike, velocity and realized volatility are
computed from that proxy, so a disagreement between price and state could be
the proxy rather than the market. This is a stated limitation, not a result.

STRIKE CONSTRUCTION is not a proxy: Gemini's `StrikeType.reference` is
documented as "Crypto Up/Down reference strike price captured at availableAt
time", and the tape carries the resolved strike per window.

NO LEAKAGE. Every feature at checkpoint t uses only observations at tte >= t
within that window. `settlement_value`, `resolution_side` and `up_won` are
OUTCOME LABELS and never features.

Accounting label: historical replay on Gemini's own recorded books and
settlements. No order was sent.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
from typing import Any, Dict, List, Optional, Sequence

LABEL = "GEMINI_SETTLEMENT_STATE_V1"
TAKER_RATE = 0.07


def _features(win_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-checkpoint causal state from the PROXY reference series."""
    rows = sorted(win_rows, key=lambda r: -r["tte"])      # earliest first
    out: List[Dict[str, Any]] = []
    hist: List[tuple] = []                                # (tte, px)
    for r in rows:
        px = r.get("coinbase")
        tte = float(r["tte"])
        if px is not None:
            hist.append((tte, float(px)))

        def back(sec: float) -> Optional[float]:
            """the proxy price ~sec seconds BEFORE now, from history only"""
            want = tte + sec
            cand = [h for h in hist if h[0] >= want - 15 and h[0] <= want + 15]
            return cand[0][1] if cand else None

        v30 = v60 = None
        if px is not None:
            b30, b60 = back(30.0), back(60.0)
            if b30 is not None:
                v30 = (float(px) - b30) / 30.0
            if b60 is not None:
                v60 = (float(px) - b60) / 60.0
        rv = None
        if len(hist) >= 3:
            d = [hist[i][1] - hist[i - 1][1] for i in range(1, len(hist))]
            if len(d) >= 2:
                rv = statistics.pstdev(d)
        strike = r.get("strike")
        dist = bps = None
        if px is not None and strike:
            dist = float(px) - float(strike)
            bps = 10000.0 * dist / float(strike)
        req_move = None
        if dist is not None:
            # the move the proxy would need to cross the strike from here
            req_move = -dist
        out.append({
            "window": r["ticker"], "checkpoint_s": tte,
            "reference_proxy": px, "strike": strike,
            "proxy_distance_from_strike": (None if dist is None
                                           else round(dist, 4)),
            "proxy_distance_bps": None if bps is None else round(bps, 3),
            "proxy_velocity_30s": None if v30 is None else round(v30, 5),
            "proxy_velocity_60s": None if v60 is None else round(v60, 5),
            "proxy_realized_vol_per_tick": (None if rv is None
                                            else round(rv, 4)),
            "time_remaining_s": tte,
            "required_proxy_move_to_flip": (None if req_move is None
                                            else round(req_move, 4)),
            "vol_normalized_required_move": (
                None if (req_move is None or not rv) else
                round(req_move / rv, 4)),
            "up_ask": r.get("up_ask"), "up_bid": r.get("bid"),
            "down_ask": r.get("down_price"),
            "up_won": r.get("up_won"),
            "settlement_value": r.get("settlement_value"),
            "reference_feed_note": ("features are from a COINBASE SPOT PROXY, "
                                    "not Gemini's settlement index "
                                    "(LS-LRRR-XBTUSD in this era)"),
        })
    return out


def _cal(rows: Sequence[Dict[str, Any]], key: str,
         edges: Sequence[float]) -> Dict[str, Any]:
    """Win rate and executable price of the UP side, binned by one feature."""
    out: Dict[str, Any] = {}
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        sub = [r for r in rows if r.get(key) is not None
               and lo <= r[key] < hi and r.get("up_won") is not None]
        if not sub:
            out["%g..%g" % (lo, hi)] = {"observations": 0}
            continue
        asks = [r["up_ask"] for r in sub if r["up_ask"] is not None]
        bids = [r["up_bid"] for r in sub if r["up_bid"] is not None]
        out["%g..%g" % (lo, hi)] = {
            "observations": len(sub),
            "distinct_windows": len({r["window"] for r in sub}),
            "up_settled_pct": round(100.0 * sum(1 for r in sub
                                                if r["up_won"]) / len(sub), 2),
            "median_up_executable_ask": (None if not asks
                                         else round(statistics.median(asks),
                                                    4)),
            "median_up_bid": (None if not bids
                              else round(statistics.median(bids), 4)),
            "implied_mid_if_both": (
                None if not (asks and bids)
                else round((statistics.median(asks)
                            + statistics.median(bids)) / 2.0, 4)),
        }
    return out


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder",
                    default="analysis/gemini/info_edge/ie_tte_ladder.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    raw = json.load(open(a.ladder, encoding="utf-8"))
    by_w: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in raw:
        if r.get("tte") is None:
            continue
        by_w[r["ticker"]].append(r)
    feats: List[Dict[str, Any]] = []
    for w, rows in by_w.items():
        feats.extend(_features(rows))

    rep: Dict[str, Any] = {
        "label": LABEL,
        "lane": "GEMINI -- no PMUS data or assumptions",
        "duration": "BTC5M",
        "windows": len(by_w),
        "observations": len(feats),
        "causal_feed": {
            "gemini_settlement_index_in_this_era": "LS-LRRR-XBTUSD",
            "previous_index": "GRR-KAIKO_RFR_BTCUSD_60S",
            "switch_between_expiries": ["2026-10-02T17:45Z",
                                        "2026-10-02T17:50Z"],
            "features_computed_from": "COINBASE SPOT PROXY",
            "limitation": ("no historical series of either Gemini index is "
                           "held, so a price/state disagreement may be the "
                           "proxy rather than the market"),
        },
        "no_leakage": ("every feature at checkpoint t uses only observations "
                       "at tte >= t in the same window; settlement_value, "
                       "resolution_side and up_won are labels only"),
        "calibration": {
            "by_proxy_distance_bps": _cal(
                feats, "proxy_distance_bps",
                (-1e9, -40, -20, -10, -5, 0, 5, 10, 20, 40, 1e9)),
            "by_vol_normalized_required_move": _cal(
                feats, "vol_normalized_required_move",
                (-1e9, -4, -2, -1, -0.5, 0, 0.5, 1, 2, 4, 1e9)),
            "by_time_remaining": _cal(
                feats, "time_remaining_s", (0, 10, 30, 60, 120, 1e9)),
            "by_proxy_velocity_60s": _cal(
                feats, "proxy_velocity_60s",
                (-1e9, -2, -0.5, 0, 0.5, 2, 1e9)),
        },
        "observations_sample": feats[:3],
        "accounting_label": ("historical replay on Gemini's own recorded "
                             "books and settlements; no order was sent"),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s windows=%d obs=%d" % (LABEL, len(by_w), len(feats)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
