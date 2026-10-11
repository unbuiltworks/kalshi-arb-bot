#!/usr/bin/env python3
"""L5 HABITAT SCORE -- derive the reference, score PMUS, set the baseline.

    python3 analysis/l5/habitat/run.py [<railway-xcheck-dump.json> ...]

READ-ONLY and PRE-ACTION. No production change, no admission change, no
deployment. The score is NOT used to gate anything here.

THE REFERENCE AND THE CANDIDATES GO THROUGH THE SAME FUNCTION. L5's windows
and every candidate window are both turned into features by
`l5_habitat_score.window_features`, so a difference in the score cannot come
from a difference in how the features were computed.
"""
from __future__ import annotations

import csv
import glob
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_habitat_score as hs                   # noqa: E402

L5SEQ = ROOT / "analysis/l5/l5_canonical_sequence.csv.gz"
OUT = Path(__file__).resolve().parent / "habitat.json"
REF_OUT = Path(__file__).resolve().parent / "l5_reference.json"

MIN_SPAN_S = 180.0          # a window shorter than this cannot carry a rate
PMUS_TICK = 0.01            # only used to express a spread in ticks


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def l5_windows() -> list[dict]:
    """Canonical L5 -> one feature row per window, via the SAME extractor a
    candidate uses, plus the demand-side fields a candidate cannot supply."""
    by = defaultdict(list)
    with gzip.open(L5SEQ, "rt") as fh:
        for r in csv.DictReader(fh):
            if r.get("side") != "BUY":
                continue
            by[r["window_id"]].append(r)
    out = []
    for w, rows in by.items():
        rows.sort(key=lambda r: int(r.get("trade_seq") or 0))
        obs = [{"t": _f(r["timestamp"]), "price": _f(r["price"]),
                "tte_s": _f(r["seconds_to_settlement"]), "two_sided": None}
               for r in rows]
        feat = hs.window_features(obs)
        if not feat.get("usable"):
            continue
        mins = feat["span_s"] / 60.0
        sides = [r["outcome"] for r in rows]
        sw = sum(1 for a, b in zip(sides, sides[1:]) if a != b)
        out.append({**feat, "window_id": w,
                    "fills_per_min": round(len(rows) / mins, 4),
                    "switches_per_min": round(sw / mins, 4),
                    "switch_rate": round(sw / max(1, len(rows) - 1), 4),
                    "duration_minutes": rows[0].get("duration_minutes"),
                    "asset": rows[0].get("asset")})
    return out


def pmus_windows(paths) -> tuple[list[dict], dict]:
    """PMUS market observations from PMUS_WS_XCHECK rows.

    These are QUOTE observations, not trades: the price series is the BBO
    mid. That is stated on every row it produces, because comparing a quote
    series to L5's trade series is a like-in-kind comparison and not an
    identical one.

    `ws_trades_total` is a deque length capped at 500. Once pinned it cannot
    measure trade rate at all, so a saturated counter yields UNKNOWN -- never
    a floor presented as a measurement.
    """
    obs = defaultdict(list)
    rows_seen = 0
    for path in paths:
        d = json.loads(Path(path).read_text())
        for r in d.get("deploy") or []:
            m = r.get("message") or ""
            i = m.find("{")
            if i < 0:
                continue
            try:
                j = json.loads(m[i:])
            except Exception:                            # noqa: BLE001
                continue
            if not j.get("rows") or not j.get("t"):
                continue
            try:
                ts = datetime.fromisoformat(
                    str(j["t"]).replace("Z", "+00:00")).timestamp()
            except Exception:                            # noqa: BLE001
                continue
            rows_seen += 1
            for row in j["rows"]:
                bid, ask = _f(row.get("ws_bid")), _f(row.get("ws_ask"))
                if bid is None or ask is None:
                    continue
                obs[row["slug"]].append({
                    "t": ts, "price": (bid + ask) / 2.0,
                    "two_sided": True,
                    "spread_ticks": round((ask - bid) / PMUS_TICK, 2),
                    "tte_s": None, "depth_at_touch": None,
                    "_book_updates": row.get("ws_book_updates"),
                    "_trades_total": row.get("ws_trades_total")})

    out, skipped = [], {"too_short": 0, "unusable": 0}
    sat = 0
    for slug, v in obs.items():
        v.sort(key=lambda o: o["t"])
        feat = hs.window_features(v)
        if not feat.get("usable"):
            skipped["unusable"] += 1
            continue
        if feat["span_s"] < MIN_SPAN_S:
            skipped["too_short"] += 1
            continue
        bu = [o["_book_updates"] for o in v if o["_book_updates"] is not None]
        tt = [o["_trades_total"] for o in v if o["_trades_total"] is not None]
        mins = feat["span_s"] / 60.0
        saturated = bool(tt and max(tt) >= 500)
        if saturated:
            sat += 1
        out.append({
            **feat, "market": slug,
            "price_series_is": "BBO mid (quotes), not trades",
            "book_updates_per_min": (round((bu[-1] - bu[0]) / mins, 2)
                                     if len(bu) >= 2 else None),
            # UNKNOWN, not a floor: the counter is a deque length capped at
            # 500 and is pinned in every market observed
            "trades_per_min": None,
            "trades_per_min_status": (
                "UNKNOWN: ws_trades_total is a deque length capped at 500 "
                "and is saturated here, so it cannot measure a rate"
                if saturated else "UNKNOWN: not derived"),
            "contracts_per_min": None,
            "depth_at_touch_p50": None,
            "maker_touch_persistence_s": None,
            "trade_through_rate": None,
            "time_to_completable_opposite_s": None,
            "attainable_pair_cost_p50": None})
    return out, {"xcheck_rows": rows_seen, "markets_seen": len(obs),
                 "markets_scored": len(out), "skipped": skipped,
                 "trades_counter_saturated": sat}


def observation_spec() -> dict:
    """What a venue must stream for a FULL habitat score."""
    return {
        "purpose": ("continuous READ-ONLY market observation, before any "
                    "strategy acts, over COMPLETE windows"),
        "one_row_per_observation": {
            "window_id": "str -- the market/window being observed",
            "venue": "str", "category": "str", "duration_minutes": "number",
            "t": "float epoch seconds",
            "bid": "float", "ask": "float",
            "bid_qty": "float contracts at the bid",
            "ask_qty": "float contracts at the ask",
            "last_trade_price": "float or null",
            "trades_since_last_observation": "int -- a COUNT, not a capped "
                                             "deque length",
            "contracts_since_last_observation": "float",
            "tte_s": "float seconds to settlement AT the observation",
            "book_updates_since_last_observation": "int",
        },
        "why_a_delta_not_a_total": (
            "PMUS's ws_trades_total is a deque length capped at 500 and is "
            "saturated in every market observed, so it cannot measure a rate "
            "at all. Send a DELTA since the previous observation, or a "
            "monotonic uncapped counter."),
        "cadence": "<= 10 s between observations; finer is better",
        "coverage": "COMPLETE windows, open to settlement, not samples",
        "must_not_be_sent": [
            "anything downstream of a decision: our fills, order state, "
            "settlement outcome, realized P&L",
            "a capped counter presented as a rate",
            "imputed values for fields the venue did not supply",
        ],
        "fills_schema_restated_from_ef5ef135": (
            "SEPARATE deliverable, still required, and NOT substitutable by "
            "campaign summaries: one row PER FILL with episode_id, "
            "venue_duration, seq, t, side ('up'|'dn'), price, qty, tte_s, "
            "maker_taker, sampled, acted, refusal; per episode "
            "settlement_outcome, terminal_up, terminal_dn and "
            "venue-confirmed realized_usd (never a proxy). Minimum to be "
            "usable: >=30 matched campaigns and >=30 complement fills."),
    }


def main(argv=None) -> int:
    paths = list(argv or sys.argv[1:])
    l5w = l5_windows()
    reference = hs.derive_reference(l5w)
    REF_OUT.write_text(json.dumps(reference, indent=1, sort_keys=True) + "\n")

    pm, audit = (pmus_windows(paths) if paths else ([], {"note": "no dump"}))
    scored = []
    for w in pm:
        cand = {k: v for k, v in w.items() if not k.startswith("_")}
        cand["observations_per_min"] = (round(w["observations"]
                                              / (w["span_s"] / 60.0), 4)
                                        if w["span_s"] else None)
        s = hs.score(cand, reference)
        scored.append({"market": w["market"], "span_s": w["span_s"],
                       "observations_per_min": cand["observations_per_min"],
                       "observations": w["observations"],
                       "price_series_is": w["price_series_is"], **s})

    # the PMUS baseline: the distribution of the score across its markets
    def q(vals, p):
        v = sorted(x for x in vals if x is not None)
        return round(v[min(len(v) - 1, int(p * (len(v) - 1)))], 4) if v else None
    raws = [s["habitat_score_on_observed"] for s in scored]
    disc = [s["coverage_discounted_score"] for s in scored]
    baseline = {
        "markets": len(scored),
        "coverage": (scored[0]["coverage"] if scored else None),
        "habitat_score_on_observed": {
            "p10": q(raws, .10), "p25": q(raws, .25), "p50": q(raws, .50),
            "p75": q(raws, .75), "p90": q(raws, .90)},
        "coverage_discounted_score": {
            "p10": q(disc, .10), "p25": q(disc, .25), "p50": q(disc, .50),
            "p75": q(disc, .75), "p90": q(disc, .90)},
        "sampling_limited_anywhere": sorted(
            {n for s in scored for n in s.get("sampling_limited_features")
             or []}),
        "p50_score_on_up_biased_features": {
            sp["name"]: q([s["per_feature"][sp["name"]]["score"]
                           for s in scored], .5)
            for sp in hs.CALIBRATED_FEATURES
            if sp.get("sampling_bias") == "UP"},
        "unknown_features": (scored[0]["unknown_features"] if scored else []),
        "uncalibrated_features": (scored[0]["uncalibrated_features"]
                                  if scored else []),
    }

    out = {
        "scope": ("READ-ONLY, PRE-ACTION. No production change, no admission "
                  "change, no deployment. The score gates nothing."),
        "reference": {k: v for k, v in reference.items() if k != "calibrated"},
        "reference_calibrated": reference["calibrated"],
        "l5_windows_used": len(l5w),
        "pmus_observation_audit": audit,
        "pmus_markets": scored,
        "pmus_baseline": baseline,
        "observation_spec_for_any_venue": observation_spec(),
        "not_yet": ("no admission, sizing, concurrency or production change "
                    "is made on the strength of this score"),
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True, default=str)
                   + "\n")

    print(f"L5 reference derived from {len(l5w)} windows")
    for s in hs.CALIBRATED_FEATURES:
        r = reference["calibrated"].get(s["name"])
        if r:
            qq = r["q"]
            print(f"  {s['name']:26s} p10={qq['0.1']:>9.4f} "
                  f"p50={qq['0.5']:>9.4f} p90={qq['0.9']:>9.4f}")
        else:
            print(f"  {s['name']:26s} UNCALIBRATED")
    print(f"\nPMUS observation audit: {audit}")
    if scored:
        print(f"\n{'market':44s} {'obs':>5} {'span_s':>8} {'score':>7} "
              f"{'cov':>5} {'disc':>6}")
        for s in sorted(scored,
                        key=lambda x: -(x["coverage_discounted_score"] or 0)):
            print(f"{s['market'][:44]:44s} {s['observations']:5d} "
                  f"{s['span_s']:8.0f} "
                  f"{str(s['habitat_score_on_observed']):>7} "
                  f"{s['coverage']:5.2f} "
                  f"{str(s['coverage_discounted_score']):>6}")
        print(f"\nPMUS BASELINE  score p50 "
              f"{baseline['habitat_score_on_observed']['p50']}  "
              f"coverage {baseline['coverage']}  discounted p50 "
              f"{baseline['coverage_discounted_score']['p50']}")
        lim = sorted({n for s in scored
                      for n in s.get("sampling_limited_features") or []})
        print(f"  sampling-limited anywhere: {lim or 'none'}")
        up = [sp["name"] for sp in hs.CALIBRATED_FEATURES
              if sp.get("sampling_bias") == "UP"]
        vals = {n: q([s["per_feature"][n]["score"] for s in scored], .5)
                for n in up}
        print(f"  p50 score on UP-BIASED features (bias favours the "
              f"candidate): {vals}")
        print(f"  UNKNOWN:      {baseline['unknown_features']}")
        print(f"  UNCALIBRATED: {baseline['uncalibrated_features']}")
    print(f"\nwrote {OUT.relative_to(ROOT)} and {REF_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
