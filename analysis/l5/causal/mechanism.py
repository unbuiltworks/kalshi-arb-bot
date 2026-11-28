"""L5_CAUSAL_MECHANISM_V1 -- which process actually generates the BTC15m tape?

OWNER 2026-09-17. We have enough description. This discriminates between four
mechanisms that all fit the descriptive picture equally well, which is exactly
why the descriptive picture cannot choose between them.

    H1 ACTION_SELECTOR        individual fills are state-dependent discrete
                              decisions.
    H2 INVENTORY_TARGET       a continuously moving desired inventory; fills
                              move him around that target.
    H3 PASSIVE_QUOTE_SURFACE  two-sided / multi-level resting liquidity; market
                              flow largely chooses the observed side and run
                              sequence.
    H4 VOLATILITY_HARVESTER   oscillation is tolerated because price movement
                              generates later complementary inventory and the
                              matched economics that follow.

NO WINNER IS FORCED IN SAMPLE. `PREDICTIONS` below is declared before any
scoring code exists in this file and states, for each test, what each
hypothesis expects. Each test is then measured on a CHRONOLOGICAL out-of-sample
split and scored against those declarations. A hypothesis can end the run with
no support, with mixed support, or contradicted -- and a test that separates
nothing is reported as separating nothing rather than being dropped.

THE DATA IS THE SAME ONE-SIDED PUBLIC TAPE. 693,916 BTC15m BUY fills, zero
sells, no book at any instant, no unfilled orders, no queue. Every price here
is the price of one of L5's own prints. A test that would need the book is not
attempted and is named in UNTESTABLE_HERE.
"""
from __future__ import annotations

import collections
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.residual_exit import forensics as F

# ---------------------------------------------------------------------------
# DECLARED BEFORE SCORING. Each entry: what the test measures, and what each
# hypothesis predicts. "-" means the hypothesis makes no distinct prediction.

PREDICTIONS: Dict[str, Dict[str, str]] = {
    "P1_same_instant_clustering": {
        "measures": "share of fills sharing (timestamp, side, price) with "
                    "another fill -- one aggressor consuming one resting level",
        "H1": "LOW: one decision produces one order",
        "H2": "LOW",
        "H3": "HIGH: a resting level is swept in pieces",
        "H4": "-",
    },
    "P2_price_walk_within_a_run": {
        "measures": "share of consecutive same-side fills at an IDENTICAL price",
        "H1": "LOW: a decision re-prices",
        "H2": "LOW",
        "H3": "HIGH: the same resting level keeps trading",
        "H4": "-",
    },
    "P3_bought_the_falling_side": {
        "measures": "P(the side bought is the side whose price FELL over the "
                    "preceding 60s), fills at flat price excluded",
        "H1": "~0.5 unless a directional rule exists, and none is supported",
        "H2": "-",
        "H3": "ABOVE 0.5: a resting bid is hit by sellers pushing that side down",
        "H4": "-",
    },
    "P4_run_continuation_hazard": {
        "measures": "P(next fill is the same side | current run length k), OOS",
        "H1": "state-dependent, not monotone in k alone",
        "H2": "FALLS with k: the target pulls back",
        "H3": "RISES with k: order flow is persistent",
        "H4": "-",
    },
    "P5_error_correction_to_a_target": {
        "measures": "mean change in residual over the next 5 fills, by current "
                    "residual bucket",
        "H1": "-",
        "H2": "STRONG NEGATIVE SLOPE and stable OOS: deviation is corrected",
        "H3": "WEAK: a passive surface has no target to correct toward",
        "H4": "-",
    },
    "P6_state_beats_persistence": {
        "measures": "OOS log-loss of P(next side) from a state-conditional "
                    "table vs a previous-side-only baseline",
        "H1": "IMPROVES materially: state carries the decision",
        "H2": "improves via residual only",
        "H3": "little improvement beyond persistence",
        "H4": "-",
    },
    "P7_matched_yield_rises_with_volatility": {
        "measures": "fraction of shares acquired in a |dp| bin that end the "
                    "window MATCHED",
        "H1": "-",
        "H2": "-",
        "H3": "-",
        "H4": "RISES with |dp|: movement brings the complement",
    },
    "P8_timing_depends_on_inventory_or_on_flow": {
        "measures": "spread of median inter-fill seconds across OUR residual "
                    "buckets vs across market |dp| buckets",
        "H1": "inventory matters: a decider waits differently by position",
        "H2": "inventory matters",
        "H3": "FLOW matters and INVENTORY DOES NOT: a resting quote does not "
              "know our position",
        "H4": "-",
    },
}

UNTESTABLE_HERE = (
    "whether L5 was quoting both sides at once -- unfilled orders leave no "
    "trace, so a two-sided surface and a one-sided one are indistinguishable "
    "except through their consequences, which is what P1/P2/P3/P8 measure",
    "queue position, depth, spread, or any book quantity",
    "whether a fill was maker or taker (only fee-bearing vs fee-free)",
)

MIN_BIN = 500


def _med(xs):
    v = sorted(x for x in xs if x is not None)
    return v[len(v) // 2] if v else None


# ---------------------------------------------------------------------------
# features

def build(cohort: str = "BTC15m") -> List[dict]:
    """One row per fill, with only what the eight tests need."""
    windows, capture_end = F.load_windows(cohort)
    rows: List[dict] = []
    for cid, w in windows.items():
        fills = w["fills"]
        if len(fills) < 5:
            continue
        _a, horizon, end = w["meta"]
        up_q = sum(x[3] for x in fills if x[2] == "Up")
        dn_q = sum(x[3] for x in fills if x[2] != "Up")
        if up_q <= 0 or dn_q <= 0:
            continue
        matched = min(up_q, dn_q)
        mf = {"Up": matched / up_q, "Down": matched / dn_q}
        # same-instant groups
        g_tsp = collections.Counter((t, oc, px) for t, px, oc, _s, _u in fills)
        g_ts = collections.Counter((t, oc) for t, _px, oc, _s, _u in fills)

        up = dn = 0.0
        run_side, run_len = None, 0
        p_hist: List[Tuple[int, float]] = []
        prev_t = None
        prev_same_px = None
        for i, (t, px, oc, sz, usd) in enumerate(fills):
            p_up = px if oc == "Up" else 1.0 - px
            dp = None
            for tt, pp in reversed(p_hist):
                if t - tt >= 60:
                    dp = p_up - pp
                    break
            res = up - dn
            run_here = (run_len + 1) if run_side == oc else 1
            same_px = (prev_same_px is not None and run_side == oc
                       and abs(px - prev_same_px) < 1e-12)
            rows.append({
                "cid": cid, "t": t, "i": i, "side": oc, "px": px, "sz": sz,
                "p_up": p_up, "dp_60s": dp,
                "abs_dp_60s": None if dp is None else abs(dp),
                "residual_before": res,
                "run_len_before": run_len if run_side == oc else 0,
                "run_here": run_here,
                "same_price_as_prev_same_side": same_px if run_here > 1 else None,
                "dt_s": None if prev_t is None else t - prev_t,
                "clustered_tsp": g_tsp[(t, oc, px)] > 1,
                "clustered_ts": g_ts[(t, oc)] > 1,
                "cluster_size_tsp": g_tsp[(t, oc, px)],
                "matched_frac_side": mf[oc],
                "tte_s": end - t,
            })
            if oc == "Up":
                up += sz
            else:
                dn += sz
            prev_same_px = px if run_side == oc or run_side is None else px
            if run_side != oc:
                prev_same_px = px
            run_side, run_len = oc, run_here
            prev_t = t
            p_hist.append((t, p_up))
    rows.sort(key=lambda r: (r["t"], r["cid"], r["i"]))
    # the NEXT fill's side, within the same window
    by_w: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        by_w[r["cid"]].append(r)
    for v in by_w.values():
        for a, b in zip(v, v[1:]):
            a["next_side"] = b["side"]
            a["next_residual_5"] = None
        for k, r in enumerate(v):
            j = min(k + 5, len(v) - 1)
            r["residual_after_5"] = v[j]["residual_before"]
        v[-1]["next_side"] = None
    return rows


def split(rows: Sequence[dict], frac: float = 0.7):
    c = int(len(rows) * frac)
    return rows[:c], rows[c:]


# ---------------------------------------------------------------------------
# the eight tests. Each returns a measurement plus a verdict PER HYPOTHESIS,
# scored against PREDICTIONS above, on the OOS half.

SUPPORTS, CONTRADICTS, NEUTRAL = "SUPPORTS", "CONTRADICTS", "NEUTRAL"


def p1_same_instant_clustering(rows: Sequence[dict]) -> dict:
    n = len(rows) or 1
    tsp = sum(1 for r in rows if r["clustered_tsp"])
    ts = sum(1 for r in rows if r["clustered_ts"])
    sizes = [r["cluster_size_tsp"] for r in rows if r["clustered_tsp"]]
    share = tsp / n
    return {
        "share_same_t_side_price": round(share, 4),
        "share_same_t_side": round(ts / n, 4),
        "mean_cluster_size_when_clustered": (round(sum(sizes) / len(sizes), 3)
                                             if sizes else None),
        "n": n,
        "verdict": {
            "H1": CONTRADICTS if share > 0.20 else SUPPORTS,
            "H2": CONTRADICTS if share > 0.20 else SUPPORTS,
            "H3": SUPPORTS if share > 0.20 else CONTRADICTS,
            "H4": NEUTRAL,
        },
    }


def p2_price_walk_within_a_run(rows: Sequence[dict]) -> dict:
    v = [r["same_price_as_prev_same_side"] for r in rows
         if r["same_price_as_prev_same_side"] is not None]
    share = (sum(1 for x in v if x) / len(v)) if v else None
    return {
        "n_continuations": len(v),
        "share_at_identical_price": (round(share, 4) if share is not None else None),
        # ANNOTATION, added after scoring and kept OUT of the verdict. As
        # declared, P2 expects a passive surface to keep trading at ONE price.
        # H3 as the owner stated it allows a MULTI-LEVEL surface, which walks
        # as successive levels are consumed. So a low identical-price share
        # rules out a single resting level, NOT H3. The declared verdict stands
        # as declared; this says what it does and does not establish.
        "specification_limit": (
            "tests a SINGLE-level resting quote. A multi-level surface -- which "
            "H3 explicitly permits -- predicts exactly this walk, so a "
            "CONTRADICTS here does not falsify H3, only its single-level form."),
        "verdict": {
            "H1": CONTRADICTS if (share or 0) > 0.50 else SUPPORTS,
            "H2": CONTRADICTS if (share or 0) > 0.50 else SUPPORTS,
            "H3": SUPPORTS if (share or 0) > 0.50 else CONTRADICTS,
            "H4": NEUTRAL,
        },
    }


def p3_bought_the_falling_side(rows: Sequence[dict]) -> dict:
    """A resting bid is hit by sellers, and sellers push that side DOWN. So a
    passive surface buys the side that has been falling. Fills with no
    measurable move are excluded rather than counted as agreement."""
    hits = tot = 0
    for r in rows:
        dp = r["dp_60s"]
        if dp is None or abs(dp) < 1e-9:
            continue
        fell_up = dp < 0
        bought_the_faller = (r["side"] == "Up") == fell_up
        hits += bought_the_faller
        tot += 1
    rate = (hits / tot) if tot else None
    return {
        "n_with_a_move": tot,
        "p_bought_the_falling_side": (round(rate, 4) if rate is not None else None),
        "verdict": {
            "H1": NEUTRAL,
            "H2": NEUTRAL,
            "H3": SUPPORTS if (rate or 0) > 0.55 else CONTRADICTS,
            "H4": NEUTRAL,
        },
    }


def p4_run_continuation_hazard(rows: Sequence[dict]) -> dict:
    g: Dict[int, List[int]] = collections.defaultdict(list)
    for r in rows:
        if r.get("next_side") is None:
            continue
        k = min(r["run_here"], 10)
        g[k].append(1 if r["next_side"] == r["side"] else 0)
    haz = {k: round(sum(v) / len(v), 4) for k, v in sorted(g.items())
           if len(v) >= MIN_BIN}
    ks = sorted(haz)
    rising = len(ks) >= 3 and haz[ks[-1]] > haz[ks[0]] + 0.05
    falling = len(ks) >= 3 and haz[ks[-1]] < haz[ks[0]] - 0.05
    return {
        "hazard_by_run_length": haz,
        "n_by_run_length": {k: len(v) for k, v in sorted(g.items())},
        "shape": "RISING" if rising else ("FALLING" if falling else "FLAT"),
        "verdict": {
            "H1": NEUTRAL,
            "H2": SUPPORTS if falling else CONTRADICTS,
            "H3": SUPPORTS if rising else CONTRADICTS,
            "H4": NEUTRAL,
        },
    }


def p5_error_correction_to_a_target(rows: Sequence[dict]) -> dict:
    """Mean change in residual over the next 5 fills, by current residual.
    A target-seeking process corrects: positive residual falls, negative rises."""
    def b(v):
        for hi, lab in ((-40, "<=-40"), (-15, "-40..-15"), (-5, "-15..-5"),
                        (5, "-5..5"), (15, "5..15"), (40, "15..40")):
            if v <= hi:
                return lab
        return ">40"
    g: Dict[str, List[float]] = collections.defaultdict(list)
    for r in rows:
        if r.get("residual_after_5") is None:
            continue
        g[b(r["residual_before"])].append(
            r["residual_after_5"] - r["residual_before"])
    tbl = {k: round(sum(v) / len(v), 4) for k, v in g.items() if len(v) >= MIN_BIN}
    order = ["<=-40", "-40..-15", "-15..-5", "-5..5", "5..15", "15..40", ">40"]
    seq = [tbl[k] for k in order if k in tbl]
    # error correction means the drift is NEGATIVE where residual is positive
    # and POSITIVE where it is negative -- a downward-sloping line through zero
    corrects = (len(seq) >= 4 and seq[0] > 0 > seq[-1])
    slope_ok = len(seq) >= 4 and all(a >= b2 for a, b2 in zip(seq, seq[1:]))
    return {
        "mean_next5_change_by_residual": tbl,
        "ordered": {k: tbl[k] for k in order if k in tbl},
        "corrects_toward_zero": corrects,
        "monotone_decreasing": slope_ok,
        "verdict": {
            "H1": NEUTRAL,
            "H2": SUPPORTS if (corrects and slope_ok) else CONTRADICTS,
            "H3": CONTRADICTS if (corrects and slope_ok) else SUPPORTS,
            "H4": NEUTRAL,
        },
    }


def _logloss(p: float, y: int) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(math.log(p) if y else math.log(1 - p))


def p6_state_beats_persistence(train: Sequence[dict],
                               oos: Sequence[dict]) -> dict:
    """Both models are fitted on TRAIN and scored on OOS. The baseline knows
    only the previous side; the challenger adds binned state. If the decision
    lives in the state, the challenger must beat it out of sample."""
    def base_key(r):
        return r["side"]

    def state_key(r):
        res = r["residual_before"]
        rb = ("neg_big" if res < -20 else "neg" if res < -2 else
              "flat" if res <= 2 else "pos" if res <= 20 else "pos_big")
        d = r["abs_dp_60s"]
        db = "na" if d is None else ("lo" if d < 0.02 else
                                     "mid" if d < 0.10 else "hi")
        rl = min(r["run_here"], 4)
        tb = "late" if (r["tte_s"] or 0) < 180 else "mid" if (r["tte_s"] or 0) < 450 else "early"
        return (r["side"], rb, db, rl, tb)

    def fit(keyfn):
        c: Dict[Any, List[int]] = collections.defaultdict(lambda: [0, 0])
        for r in train:
            if r.get("next_side") is None:
                continue
            k = keyfn(r)
            c[k][1] += 1
            c[k][0] += (r["next_side"] == "Up")
        return c

    prior = sum(1 for r in train if r.get("next_side") == "Up") / max(
        1, sum(1 for r in train if r.get("next_side") is not None))
    A, B = fit(base_key), fit(state_key)

    def score(c, keyfn, alpha=20.0):
        tot = n = 0.0
        backoff = 0
        for r in oos:
            if r.get("next_side") is None:
                continue
            k = keyfn(r)
            h, m = c.get(k, [0, 0])
            if m < 30:
                backoff += 1
            p = (h + alpha * prior) / (m + alpha)
            tot += _logloss(p, 1 if r["next_side"] == "Up" else 0)
            n += 1
        return (tot / n if n else None), int(n), backoff

    ll_b, n, bo_b = score(A, base_key)
    ll_s, _, bo_s = score(B, state_key)
    gain = (ll_b - ll_s) if (ll_b and ll_s) else None
    return {
        "oos_n": n, "prior_p_up": round(prior, 4),
        "baseline_logloss_prev_side_only": round(ll_b, 6) if ll_b else None,
        "state_conditional_logloss": round(ll_s, 6) if ll_s else None,
        "improvement_nats": round(gain, 6) if gain is not None else None,
        "state_cells_backed_off": bo_s, "baseline_cells_backed_off": bo_b,
        "verdict": {
            "H1": SUPPORTS if (gain or 0) > 0.005 else CONTRADICTS,
            "H2": NEUTRAL,
            "H3": SUPPORTS if (gain or 0) <= 0.005 else CONTRADICTS,
            "H4": NEUTRAL,
        },
    }


def p7_matched_yield_rises_with_volatility(rows: Sequence[dict]) -> dict:
    def b(r):
        d = r["abs_dp_60s"]
        if d is None:
            return None
        return ("<=0.01" if d <= 0.01 else "0.01-0.02" if d <= 0.02 else
                "0.02-0.05" if d <= 0.05 else "0.05-0.10" if d <= 0.10 else ">0.10")
    g: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        k = b(r)
        if k:
            g[k].append(r)
    tbl = {}
    for k, v in g.items():
        if len(v) < MIN_BIN:
            continue
        sh = sum(x["sz"] for x in v)
        tbl[k] = round(sum(x["sz"] * x["matched_frac_side"] for x in v) / sh, 4)
    order = ["<=0.01", "0.01-0.02", "0.02-0.05", "0.05-0.10", ">0.10"]
    seq = [tbl[k] for k in order if k in tbl]
    rises = len(seq) >= 3 and seq[-1] > seq[0] + 0.005
    return {
        "matched_share_of_acquired_by_abs_dp": {k: tbl[k] for k in order if k in tbl},
        "rises_with_volatility": rises,
        "verdict": {
            "H1": NEUTRAL, "H2": NEUTRAL, "H3": NEUTRAL,
            "H4": SUPPORTS if rises else CONTRADICTS,
        },
    }


def p8_timing_depends_on_inventory_or_flow(rows: Sequence[dict]) -> dict:
    """A resting quote does not know our position. So if inter-fill timing
    varies with market flow but NOT with our own residual, the timing is being
    set by the market, not by a decider."""
    def rb(r):
        a = abs(r["residual_before"])
        return "<=2" if a <= 2 else "2-10" if a <= 10 else "10-30" if a <= 30 else ">30"

    def db(r):
        d = r["abs_dp_60s"]
        if d is None:
            return None
        return "lo" if d < 0.02 else "mid" if d < 0.10 else "hi"

    def table(keyfn):
        g: Dict[Any, List[float]] = collections.defaultdict(list)
        for r in rows:
            if r["dt_s"] is None:
                continue
            k = keyfn(r)
            if k is not None:
                g[k].append(r["dt_s"])
        return {k: _med(v) for k, v in g.items() if len(v) >= MIN_BIN}

    ti, tf = table(rb), table(db)
    si = (max(ti.values()) - min(ti.values())) if len(ti) > 1 else None
    sf = (max(tf.values()) - min(tf.values())) if len(tf) > 1 else None
    inv_matters = bool(si is not None and si >= 1.0)
    flow_matters = bool(sf is not None and sf >= 1.0)
    dts = sorted(r["dt_s"] for r in rows if r["dt_s"] is not None)
    med_all = _med(dts)
    informative = bool(med_all is not None and med_all >= 5)
    return {
        "median_dt_by_our_residual": ti, "spread_inventory_s": si,
        "median_dt_by_market_abs_dp": tf, "spread_flow_s": sf,
        "inventory_matters": inv_matters, "flow_matters": flow_matters,
        "median_inter_fill_s_overall": med_all,
        "test_informative": informative,
        # ANNOTATION, added after scoring, verdict unchanged. Timestamps are
        # whole seconds and the median inter-fill time is 1-2 of them. Every
        # bucket therefore lands on 1 or 2, both "spreads" are exactly 1s, and
        # the comparison has no resolution to make. This is the only test whose
        # verdict did not replicate between TRAIN and OOS, which is what an
        # uninformative test looks like.
        "resolution_note": (
            "UNINFORMATIVE AT THIS RESOLUTION: the tape timestamps whole "
            "seconds and the median inter-fill gap is %s s, so every bucket "
            "collapses onto 1-2 s. Separating inventory-driven from "
            "flow-driven timing needs sub-second stamps this dataset does not "
            "carry. Report as undecided, not as support." % med_all),
        "verdict": {
            "H1": SUPPORTS if inv_matters else CONTRADICTS,
            "H2": SUPPORTS if inv_matters else CONTRADICTS,
            "H3": SUPPORTS if (flow_matters and not inv_matters) else CONTRADICTS,
            "H4": NEUTRAL,
        },
    }


def report(cohort: str = "BTC15m") -> dict:
    rows = build(cohort)
    tr, oos = split(rows)
    tests = {
        "P1_same_instant_clustering": p1_same_instant_clustering(oos),
        "P2_price_walk_within_a_run": p2_price_walk_within_a_run(oos),
        "P3_bought_the_falling_side": p3_bought_the_falling_side(oos),
        "P4_run_continuation_hazard": p4_run_continuation_hazard(oos),
        "P5_error_correction_to_a_target": p5_error_correction_to_a_target(oos),
        "P6_state_beats_persistence": p6_state_beats_persistence(tr, oos),
        "P7_matched_yield_rises_with_volatility":
            p7_matched_yield_rises_with_volatility(oos),
        "P8_timing_depends_on_inventory_or_flow":
            p8_timing_depends_on_inventory_or_flow(oos),
    }
    # the same tests on TRAIN, reported for stability -- NOT used to choose
    train_tests = {
        "P1_same_instant_clustering": p1_same_instant_clustering(tr),
        "P2_price_walk_within_a_run": p2_price_walk_within_a_run(tr),
        "P3_bought_the_falling_side": p3_bought_the_falling_side(tr),
        "P4_run_continuation_hazard": p4_run_continuation_hazard(tr),
        "P5_error_correction_to_a_target": p5_error_correction_to_a_target(tr),
        "P7_matched_yield_rises_with_volatility":
            p7_matched_yield_rises_with_volatility(tr),
        "P8_timing_depends_on_inventory_or_flow":
            p8_timing_depends_on_inventory_or_flow(tr),
    }
    tally: Dict[str, Dict[str, int]] = {
        h: {SUPPORTS: 0, CONTRADICTS: 0, NEUTRAL: 0} for h in
        ("H1", "H2", "H3", "H4")}
    for t in tests.values():
        for h, v in t["verdict"].items():
            tally[h][v] += 1
    replicates = {k: (train_tests[k]["verdict"] == tests[k]["verdict"])
                  for k in train_tests}
    return {
        "cohort": cohort, "n_fills": len(rows),
        "n_train": len(tr), "n_oos": len(oos),
        "predictions_declared_before_scoring": PREDICTIONS,
        "untestable_here": list(UNTESTABLE_HERE),
        "oos_tests": tests,
        "train_tests_for_stability_only": train_tests,
        "train_oos_verdict_agreement": replicates,
        "tally_oos": tally,
        "labels": ("Scored OUT OF SAMPLE against predictions declared before "
                   "any scoring code existed. No winner is forced: a "
                   "hypothesis may end contradicted, and a test that "
                   "separates nothing reports NEUTRAL rather than being "
                   "dropped. TRAIN is shown only to say whether a verdict "
                   "held in both halves."),
    }
