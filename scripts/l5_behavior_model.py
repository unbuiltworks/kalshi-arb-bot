"""L5's DECISION BOUNDARIES, and an OUT-OF-SAMPLE test of them.

TWO HALVES, AND THE SECOND IS THE ONE THAT MATTERS.

  1. MEASUREMENT: conditional distributions around SIDE, PRICE, SIZE,
     RISK, PAIR ECONOMICS and TIMING, over every reconstructed
     (state-before, action) pair.
  2. PREDICTION: rules induced on a TRAIN split of WINDOWS and scored on
     a HELD-OUT split, against baselines that cost nothing. A rule that
     cannot beat "do what he did last time" has explained nothing.

THE SPLIT IS BY WINDOW, NEVER BY TRADE. Consecutive fills inside a window
share inventory, time and price level; splitting by trade would let the
answer leak across the boundary and would score a memoriser as a model.

EVERY RULE HERE IS INTERPRETABLE ON PURPOSE. The deliverable is a
behavioral rule that explains L5, not a classifier with good numbers --
and an interpretable rule is the only kind that can later be argued about
and risk-bounded before it is adapted to Gemini.

NOTHING IS FITTED TO GEMINI PROFITABILITY. The frozen Gemini policy is
untouched and is not imported.

Accounting label: historical observation of a third party.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.gemini_execution.l5_behavior import (DOWN, SAME_SIDE, SWITCH_SIDE,
                                              UP, reconstruct,
                                              window_summary)
from scripts.l5_vs_frozen_entry_study import L5_TAPE, load_l5

LABEL = "L5_BEHAVIOR_MODEL_V1"
SIZE_BANDS = ((0.0, 1.0, "XS"), (1.0, 5.0, "S"), (5.0, 20.0, "M"),
              (20.0, 60.0, "L"), (60.0, float("inf"), "XL"))
PRICE_BANDS = ((0.0, 0.20, "VERY_CHEAP"), (0.20, 0.40, "CHEAP"),
               (0.40, 0.60, "MID"), (0.60, 0.80, "DEAR"),
               (0.80, 1.01, "VERY_DEAR"))
TIMING_BANDS = ((0.0, 2.0, "IMMEDIATE"), (2.0, 10.0, "FAST"),
                (10.0, 60.0, "SLOW"), (60.0, float("inf"), "PATIENT"))


def band(v: Optional[float], bands: Sequence[Tuple]) -> Optional[str]:
    if v is None:
        return None
    for lo, hi, name in bands:
        if lo <= v < hi:
            return name
    return bands[-1][2]


def _counts(xs) -> Dict[str, int]:
    """Counter with STRING keys. A band() of None is a real category --
    the first fill has no previous action, so its timing band is absent --
    and mixing None with str keys makes the report unserialisable."""
    c = collections.Counter(("NONE" if x is None else str(x)) for x in xs)
    return dict(sorted(c.items()))


def _st(xs: List[float]) -> Dict[str, Any]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(xs), "min": round(s[0], 4),
            "p10": round(s[len(s) // 10], 4),
            "p25": round(s[len(s) // 4], 4),
            "median": round(statistics.median(s), 4),
            "p75": round(s[3 * len(s) // 4], 4),
            "p90": round(s[9 * len(s) // 10], 4),
            "max": round(s[-1], 4), "mean": round(statistics.fmean(s), 4)}


def _pct(n: int, d: int) -> Optional[float]:
    return None if not d else round(100.0 * n / d, 1)


# ----------------------------------------------------------------- measure
def measure(steps: List[Dict[str, Any]],
            summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    nf = [s for s in steps if not s["state"]["is_first_fill"]]
    switch = [s for s in nf if s["action"]["side_relation"] == SWITCH_SIDE]
    same = [s for s in nf if s["action"]["side_relation"] == SAME_SIDE]

    # SIDE: what separates a switch from another helping of the same side?
    def split(key: str) -> Dict[str, Any]:
        return {"on_switch": _st([s["state"][key] for s in switch]),
                "on_same_side": _st([s["state"][key] for s in same])}

    # P(switch) by bucket of the unhedged fraction -- the scale-free
    # imbalance measure, so a 4-share and a 400-share book compare.
    buckets: Dict[str, List[int]] = collections.defaultdict(lambda: [0, 0])
    for s in nf:
        f = abs(s["state"]["imbalance_frac"] or 0.0)
        key = ("0.0-0.2" if f < 0.2 else "0.2-0.4" if f < 0.4 else
               "0.4-0.6" if f < 0.6 else "0.6-0.8" if f < 0.8 else "0.8-1.0")
        buckets[key][1] += 1
        if s["action"]["side_relation"] == SWITCH_SIDE:
            buckets[key][0] += 1
    p_switch_by_imbalance = {
        k: {"p_switch_pct": _pct(v[0], v[1]), "n": v[1]}
        for k, v in sorted(buckets.items())}

    # does he switch TOWARD the short side? (the hedging interpretation)
    toward = sum(1 for s in switch
                 if s["state"]["residual_side"] is not None
                 and s["action"]["side"] != s["state"]["residual_side"])
    pair_b: Dict[str, List[int]] = collections.defaultdict(lambda: [0, 0])
    for s in nf:
        bp = s["state"]["book_pair_cost"]
        if bp is None:
            continue
        key = ("<0.90" if bp < 0.90 else "0.90-0.96" if bp < 0.96 else
               "0.96-1.00" if bp < 1.00 else "1.00-1.05" if bp < 1.05
               else ">=1.05")
        pair_b[key][1] += 1
        if s["action"]["side_relation"] == SWITCH_SIDE:
            pair_b[key][0] += 1

    # SIZE: bands overall and conditioned on the price band bought
    size_by_price: Dict[str, Any] = {}
    for pb in [b[2] for b in PRICE_BANDS]:
        xs = [s["action"]["size"] for s in steps
              if band(s["action"]["price"], PRICE_BANDS) == pb]
        size_by_price[pb] = _st(xs)
    size_by_rel = {
        "ENTRY": _st([s["action"]["size"] for s in steps
                      if s["action"]["side_relation"] == "ENTRY"]),
        SAME_SIDE: _st([s["action"]["size"] for s in same]),
        SWITCH_SIDE: _st([s["action"]["size"] for s in switch]),
    }

    # PAIR ECONOMICS: excursions above 1.00 and whether they compressed
    exc = []
    for sm in summaries:
        mx = sm.get("max_book_pair_cost")
        fin = sm.get("final_book_pair_cost")
        if mx is None or fin is None or mx != mx:        # NaN guard
            continue
        exc.append({"max": mx, "final": fin,
                    "compressed": fin < mx,
                    "above_1": mx > 1.00,
                    "ended_below_0_96": fin <= 0.96})
    above = [e for e in exc if e["above_1"]]

    return {
        "n_steps": len(steps), "n_non_entry": len(nf),
        "SIDE": {
            "pct_switch": _pct(len(switch), len(nf)),
            "p_switch_by_abs_imbalance_frac": p_switch_by_imbalance,
            "p_switch_by_book_pair_cost": {
                k: {"p_switch_pct": _pct(v[0], v[1]), "n": v[1]}
                for k, v in sorted(pair_b.items())},
            "switch_is_toward_the_short_side_pct": _pct(toward, len(switch)),
            "imbalance_frac": split("imbalance_frac"),
            "book_pair_cost": split("book_pair_cost"),
            "trades_since_switch": split("trades_since_switch"),
        },
        "PRICE": {
            "price_band_counts": _counts(
                band(s["action"]["price"], PRICE_BANDS) for s in steps),
            "price_vs_own_avg": _st([s["action"]["price_vs_own_avg"]
                                     for s in steps]),
            "price_vs_other_side_complement": _st(
                [s["action"]["price_vs_other_side_complement"]
                 for s in steps]),
            "price_change_same_side": _st([s["state"]["price_change_same_side"]
                                           for s in steps]),
            "pct_same_side_buys_improving_own_avg": _pct(
                sum(1 for s in same
                    if (s["action"]["price_vs_own_avg"] or 0) < 0), len(same)),
        },
        "SIZE": {
            "overall": _st([s["action"]["size"] for s in steps]),
            "dollars": _st([s["action"]["dollars"] for s in steps]),
            "by_price_band": size_by_price,
            "by_side_relation": size_by_rel,
            "band_counts": _counts(
                band(s["action"]["size"], SIZE_BANDS) for s in steps),
        },
        "RISK": {
            "peak_imbalance_shares": _st(
                [sm["peak_imbalance_shares"] for sm in summaries]),
            "peak_residual_principal_usd": _st(
                [sm["peak_residual_principal"] for sm in summaries]),
            "dollars_deployed_per_window": _st(
                [sm["dollars_deployed"] for sm in summaries]),
            "final_residual_qty": _st([sm["residual_qty"]
                                       for sm in summaries]),
            "pct_windows_completed": _pct(
                sum(1 for sm in summaries if sm["completed"]),
                len(summaries)),
        },
        "PAIR_ECONOMICS": {
            "n_windows": len(exc),
            "pct_exceeding_1_00_in_flight": _pct(len(above), len(exc)),
            "of_those_pct_compressed": _pct(
                sum(1 for e in above if e["compressed"]), len(above)),
            "of_those_pct_ended_at_or_below_0_96": _pct(
                sum(1 for e in above if e["ended_below_0_96"]), len(above)),
            "max_book_pair_cost": _st([e["max"] for e in exc]),
            "final_book_pair_cost": _st([e["final"] for e in exc]),
            "note": ("book_pair_cost is EXACT (own fills). print_pair_cost "
                     "is an approximation of the instantaneous market pair "
                     "cost built from L5's own prints, because we do not "
                     "have their book."),
        },
        "TIMING": {
            "seconds_between_actions": _st(
                [s["state"]["seconds_since_prev"] for s in steps]),
            "seconds_before_a_switch": _st(
                [s["state"]["seconds_since_prev"] for s in switch]),
            "seconds_before_same_side": _st(
                [s["state"]["seconds_since_prev"] for s in same]),
            "band_counts": _counts(
                band(s["state"]["seconds_since_prev"], TIMING_BANDS)
                for s in steps),
            "entry_tte": _st([s["state"]["tte"] for s in steps
                              if s["state"]["is_first_fill"]]),
        },
    }


# ------------------------------------------------------------- the rule
def induce(train: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fit the SIMPLEST rule the measurement justifies, on TRAIN only.

    SIDE: switch toward the short side once the unhedged fraction exceeds
    a threshold; the threshold is chosen as the value maximising train
    accuracy over a coarse grid, not hand-picked.
    SIZE/PRICE/TIMING: the modal band conditioned on the side relation,
    which is the honest baseline for "what does he usually do here".
    """
    nf = [s for s in train if not s["state"]["is_first_fill"]]
    best = (None, -1.0)
    for thr in [x / 100.0 for x in range(0, 101, 5)]:
        ok = 0
        for s in nf:
            f = abs(s["state"]["imbalance_frac"] or 0.0)
            pred = SWITCH_SIDE if f >= thr else SAME_SIDE
            if pred == s["action"]["side_relation"]:
                ok += 1
        acc = ok / len(nf) if nf else 0.0
        if acc > best[1]:
            best = (thr, acc)
    thr = best[0]

    def modal(xs: List[Optional[str]]) -> Optional[str]:
        xs = [x for x in xs if x]
        return collections.Counter(xs).most_common(1)[0][0] if xs else None

    by_rel: Dict[str, Dict[str, Any]] = {}
    for rel in ("ENTRY", SAME_SIDE, SWITCH_SIDE):
        rows = [s for s in train if s["action"]["side_relation"] == rel]
        by_rel[rel] = {
            "size_band": modal([band(r["action"]["size"], SIZE_BANDS)
                                for r in rows]),
            "price_band": modal([band(r["action"]["price"], PRICE_BANDS)
                                 for r in rows]),
            "timing_band": modal([band(r["state"]["seconds_since_prev"],
                                       TIMING_BANDS) for r in rows]),
            "n": len(rows),
        }
    return {"switch_threshold_abs_imbalance_frac": thr,
            "train_side_accuracy_pct": round(100.0 * best[1], 1),
            "modal_bands_by_side_relation": by_rel,
            "rule_in_words": (
                "SIDE: if the unhedged fraction of the book is at or above "
                "%.2f, buy the SHORT side; otherwise add to the side just "
                "bought. SIZE/PRICE/TIMING: the modal band for that kind "
                "of action." % thr)}


def predict(rule: Dict[str, Any], step: Dict[str, Any]) -> Dict[str, Any]:
    s = step["state"]
    if s["is_first_fill"]:
        rel = "ENTRY"
        side = None
    else:
        f = abs(s["imbalance_frac"] or 0.0)
        rel = (SWITCH_SIDE
               if f >= rule["switch_threshold_abs_imbalance_frac"]
               else SAME_SIDE)
        if rel == SWITCH_SIDE:
            side = (DOWN if s["residual_side"] == UP else UP)
        else:
            side = s["prev_side"]
    b = rule["modal_bands_by_side_relation"].get(rel) or {}
    return {"side_relation": rel, "side": side,
            "size_band": b.get("size_band"),
            "price_band": b.get("price_band"),
            "timing_band": b.get("timing_band")}


def score(rule: Dict[str, Any],
          test: List[Dict[str, Any]]) -> Dict[str, Any]:
    nf = [s for s in test if not s["state"]["is_first_fill"]]
    rel_ok = side_ok = size_ok = price_ok = time_ok = 0
    rel_n = side_n = size_n = price_n = time_n = 0
    # BASELINES that cost nothing and must be beaten to mean anything.
    persist_ok = 0                      # "same side as last time"
    majority_rel = collections.Counter(
        s["action"]["side_relation"] for s in nf).most_common(1)
    majority_rel = majority_rel[0][0] if majority_rel else SAME_SIDE
    maj_ok = 0

    for s in nf:
        p = predict(rule, s)
        a = s["action"]
        rel_n += 1
        if p["side_relation"] == a["side_relation"]:
            rel_ok += 1
        if majority_rel == a["side_relation"]:
            maj_ok += 1
        if s["state"]["prev_side"] == a["side"]:
            persist_ok += 1
        if p["side"] is not None:
            side_n += 1
            if p["side"] == a["side"]:
                side_ok += 1
        ab = band(a["size"], SIZE_BANDS)
        if p["size_band"] and ab:
            size_n += 1
            if p["size_band"] == ab:
                size_ok += 1
        pb = band(a["price"], PRICE_BANDS)
        if p["price_band"] and pb:
            price_n += 1
            if p["price_band"] == pb:
                price_ok += 1
        tb = band(s["state"]["seconds_since_prev"], TIMING_BANDS)
        if p["timing_band"] and tb:
            time_n += 1
            if p["timing_band"] == tb:
                time_ok += 1
    return {
        "n_test_decisions": rel_n,
        "side_relation_accuracy_pct": _pct(rel_ok, rel_n),
        "side_accuracy_pct": _pct(side_ok, side_n),
        "size_band_accuracy_pct": _pct(size_ok, size_n),
        "price_band_accuracy_pct": _pct(price_ok, price_n),
        "timing_band_accuracy_pct": _pct(time_ok, time_n),
        "baselines": {
            "majority_side_relation_pct": _pct(maj_ok, rel_n),
            "persistence_same_side_as_previous_pct": _pct(persist_ok, rel_n),
            "majority_class": majority_rel,
        },
        "verdict_note": ("a rule that does not BEAT these baselines has "
                         "explained nothing, however high its accuracy "
                         "looks."),
    }


def run(family: str = "btc-updown-15m",
        train_frac: float = 0.7) -> Dict[str, Any]:
    by = load_l5(L5_TAPE, family)
    wins = sorted(by)
    steps_by_win = {w: reconstruct(by[w]) for w in wins}
    steps_by_win = {w: s for w, s in steps_by_win.items() if s}
    wins = sorted(steps_by_win)
    cut = int(len(wins) * train_frac)
    tr_w, te_w = wins[:cut], wins[cut:]
    tr = [s for w in tr_w for s in steps_by_win[w]]
    te = [s for w in te_w for s in steps_by_win[w]]
    all_steps = [s for w in wins for s in steps_by_win[w]]
    summaries = [window_summary(steps_by_win[w]) for w in wins]

    rule = induce(tr)
    return {
        "label": LABEL, "family": family,
        "n_windows": len(wins), "n_decisions": len(all_steps),
        "split": {"train_windows": len(tr_w), "test_windows": len(te_w),
                  "train_decisions": len(tr), "test_decisions": len(te),
                  "split_by": "WINDOW, never by trade -- fills inside a "
                              "window share inventory, time and price "
                              "level, so a trade split would leak"},
        "measurement": measure(all_steps, summaries),
        "candidate_rule": rule,
        "out_of_sample": score(rule, te),
        "accounting_label": "historical observation of a third party",
        "control": ("the frozen Gemini policy is untouched and is not "
                    "imported by this study"),
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="btc-updown-15m")
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    r = run(a.family, a.train_frac)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(r, indent=2, sort_keys=True,
                                default=str) + "\n")
    m = r["measurement"]
    print("%s  %s  windows=%d decisions=%d  (train %d / test %d windows)"
          % (r["label"], r["family"], r["n_windows"], r["n_decisions"],
             r["split"]["train_windows"], r["split"]["test_windows"]))
    print("\nSIDE  switch=%s%%  switch-toward-short=%s%%"
          % (m["SIDE"]["pct_switch"],
             m["SIDE"]["switch_is_toward_the_short_side_pct"]))
    print("  P(switch) by |unhedged fraction|:")
    for k, v in m["SIDE"]["p_switch_by_abs_imbalance_frac"].items():
        print("     %-9s %5s%%  (n=%d)" % (k, v["p_switch_pct"], v["n"]))
    print("  P(switch) by book pair cost:")
    for k, v in m["SIDE"]["p_switch_by_book_pair_cost"].items():
        print("     %-11s %5s%%  (n=%d)" % (k, v["p_switch_pct"], v["n"]))
    pe = m["PAIR_ECONOMICS"]
    print("\nPAIR  above 1.00 in flight: %s%% of windows | of those "
          "compressed %s%% | ended <=0.96 %s%%"
          % (pe["pct_exceeding_1_00_in_flight"],
             pe["of_those_pct_compressed"],
             pe["of_those_pct_ended_at_or_below_0_96"]))
    print("      max book pair cost %s" % pe["max_book_pair_cost"])
    print("\nRISK  peak imbalance shares %s" % m["RISK"]
          ["peak_imbalance_shares"])
    print("      peak residual $      %s" % m["RISK"]
          ["peak_residual_principal_usd"])
    print("      $ deployed/window    %s" % m["RISK"]
          ["dollars_deployed_per_window"])
    print("\nTIMING seconds between actions %s" % m["TIMING"]
          ["seconds_between_actions"])
    print("\nPRICE same-side buys improving own avg: %s%%"
          % m["PRICE"]["pct_same_side_buys_improving_own_avg"])
    print("      price vs own avg %s" % m["PRICE"]["price_vs_own_avg"])
    print("\nCANDIDATE RULE: %s" % r["candidate_rule"]["rule_in_words"])
    o = r["out_of_sample"]
    print("\nOUT OF SAMPLE (%d held-out decisions)" % o["n_test_decisions"])
    print("   side_relation %s%%   side %s%%   size_band %s%%   "
          "price_band %s%%   timing_band %s%%"
          % (o["side_relation_accuracy_pct"], o["side_accuracy_pct"],
             o["size_band_accuracy_pct"], o["price_band_accuracy_pct"],
             o["timing_band_accuracy_pct"]))
    print("   BASELINES: majority(%s) %s%%  |  persistence %s%%"
          % (o["baselines"]["majority_class"],
             o["baselines"]["majority_side_relation_pct"],
             o["baselines"]["persistence_same_side_as_previous_pct"]))
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
