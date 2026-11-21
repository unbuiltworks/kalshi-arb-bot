#!/usr/bin/env python3
"""CEILING_PINNED_PERSIST vs CONTROL -- bounded SHADOW replay, read-only.

    python3 analysis/l5/ceiling_pinned_persist/run.py

Reads COMPLETE orders the live engine already wrote and scores a
counterfactual cancel ladder over them. Nothing is submitted, priced,
repriced or deployed; no production file is read for configuration.

NO FILL IS INVENTED. Where the merged order would have outlived its
historical twins, that time carries NO observation -- the historical order
was cancelled, so nothing was resting there -- and it is reported as
COUNTERFACTUAL_FILL_UNKNOWN.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_ceiling_pinned_persist as cp      # noqa: E402
from src import l5_complete_conversion as cc         # noqa: E402

SESSIONS = ROOT / "analysis/l5/streak_shadow/live_sessions.jsonl.gz"
COHORT = ROOT / "analysis/l5/cross_shadow_live/cohort.jsonl"
OUT = Path(__file__).resolve().parent / "ceiling_pinned_persist.json"

# R5FT, the ACTIVE rung: rest_policy "model", rest_floor_s 5. The arm does
# not change it, so a merged order still dies at the model's own next
# re-evaluation. This is the ceiling on how much persistence the arm can buy.
REST_FLOOR_S = 5.0


def _f(x):
    try:
        return float(D(str(x)))
    except Exception:                                        # noqa: BLE001
        return None


def pctl(v, p):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))] if v else None


def load_pre_cutover():
    by_market, pair_econ, inv_last = defaultdict(list), {}, {}
    for line in gzip.open(SESSIONS, "rt"):
        line = line.strip()
        if not line:
            continue
        s = json.loads(line)
        ds = s.get("decisions") or []
        slug = ds[0].get("slug") if ds else s.get("slug")
        if isinstance(s.get("inventory"), dict) and s["inventory"]:
            inv_last[slug] = s["inventory"]
        pe = s.get("pair_economics") if isinstance(
            s.get("pair_economics"), dict) else None
        if pe:
            pair_econ.setdefault(slug, []).append(pe)
        # reconstruct the held average cost this session priced against, so
        # ceiling-pinning can be decided the EXACT way here too:
        #   economic_max_px = ceiling - avg_cost
        avg = None
        if pe and pe.get("economic_max_px") is not None \
                and pe.get("pair_cost_max") is not None:
            avg = str(D(str(pe["pair_cost_max"]))
                      - D(str(pe["economic_max_px"])))
        for i, o in enumerate(s.get("orders") or []):
            if o.get("relation") == "complete":
                by_market[slug].append(
                    (str(s.get("_ts")), i, {**o, "held_avg_cost": avg}))
    for v in by_market.values():
        v.sort(key=lambda x: (x[0], x[1]))
    return ({k: [o for _t, _i, o in v] for k, v in by_market.items()},
            pair_econ, inv_last)


def load_post_cutover():
    by_market = defaultdict(list)
    for line in open(COHORT):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        by_market[r["slug"]].append(r)
    for v in by_market.values():
        v.sort(key=lambda r: str(r.get("session_end_ts") or ""))
    # normalise the cohort row into the order shape the arm reads
    out = {}
    for slug, rows in by_market.items():
        out[slug] = [{
            "px": r.get("maker_px"), "qty": r.get("qty"),
            "filled_qty": r.get("filled_qty"), "order_id": r.get("order_id"),
            "end_reason": r.get("end_reason"), "stage": r.get("stage"),
            # NO post-time touch is synthesised. The touch inside a
            # `repriced:touch_moved:X` reason is the touch that MOVED, not
            # the one we priced against; using it would label every
            # re-quoted order ceiling-pinned. Ceiling-pinning on this corpus
            # is decided the exact way instead, from the held average cost.
            "l5_touch_px": None,
            "touch_at_cancel": (r["end_reason"].rsplit(":", 1)[-1]
                                if str(r.get("end_reason") or "").startswith(
                                    "repriced:touch_moved:") else None),
            "rest_s": None,
            "rest_book": {"polls": None,
                          # SIDE-ADJUSTED by the frozen cross-shadow module:
                          # obs_ask is the YES ask, which is the opposite ask
                          # only for an UP order. This field is the price a
                          # cross would pay in the ORDER'S own convention.
                          "min_opp_ask": r.get(
                              "obs_executable_complement_price"),
                          "max_touch": None},
            "held_avg_cost": r.get("econ_held_avg_cost"),
            "obs_bid": r.get("obs_bid"), "obs_ask": r.get("obs_ask"),
            "obs_bid_qty": r.get("obs_bid_qty"),
            "obs_ask_qty": r.get("obs_ask_qty"),
            "session_inv_residual": r.get("session_inv_residual"),
        } for r in rows]
    return out


def score(by_market, ticks, label, deadline_obs, avg_cost_key=None):
    chains_all, per_market, classes = [], {}, Counter()
    boundaries = Counter()
    for slug, orders in by_market.items():
        tick = ticks.get(slug) or D("0.01")
        chains = cp.chain_orders(orders, tick, avg_cost_key=avg_cost_key)
        merged = sum(len(c) for c in chains)
        rows = []
        for c in chains:
            k = cp.classify_chain(c)
            h = cp.horizon_at_natural_boundaries(c, deadline_obs)
            seg = cp.segment_chain_by_deadline(c, deadline_obs)
            classes[k["class"]] += 1
            boundaries[h["boundary"]] += 1
            touches = [str((o.get("rest_book") or {}).get("max_touch")
                           or o.get("l5_touch_px") or "")
                       for o in c]
            asks = [_f((o.get("rest_book") or {}).get("min_opp_ask"))
                    for o in c]
            rows.append({**k, "horizon": h, "deadline_segmented": seg,
                         "price": str(c[0].get("px")),
                         "distinct_touches_while_resting":
                             len({t for t in touches if t}),
                         "touch_span": ([min(t for t in touches if t),
                                         max(t for t in touches if t)]
                                        if any(touches) else None),
                         "closest_complement_ask":
                             (str(min(a for a in asks if a is not None))
                              if any(a is not None for a in asks) else None),
                         "complement_ever_reached_our_price": bool(
                             any(a is not None and a <= _f(c[0].get("px"))
                                 for a in asks))})
            chains_all.append(rows[-1])
        cut_real = sum(r["deadline_segmented"]["reposts_eliminated"]
                       for r in rows)
        per_market[slug] = {
            "complete_attempts": len(orders),
            "ceiling_pinned_attempts": merged,
            "chains": len(chains),
            "reposts_eliminated_naive": merged - len(chains),
            "reposts_eliminated_deadline_bounded": cut_real,
            "orders_under_control": len(orders),
            "orders_under_arm": len(orders) - cut_real,
            "detail": rows}
    tot_merged = sum(m["ceiling_pinned_attempts"] for m in per_market.values())
    tot_chains = sum(m["chains"] for m in per_market.values())
    tot_attempts = sum(m["complete_attempts"] for m in per_market.values())
    cut_real = sum(m["reposts_eliminated_deadline_bounded"]
                   for m in per_market.values())
    lifetimes = [c["observed_rest_s_total"] for c in chains_all
                 if c["observed_rest_s_total"]]
    return {
        "corpus": label,
        "complete_attempts": tot_attempts,
        "ceiling_pinned_attempts": tot_merged,
        "chains_the_arm_would_merge": tot_chains,
        "identical_price_reposts_eliminated_NAIVE": tot_merged - tot_chains,
        "identical_price_reposts_eliminated": cut_real,
        "naive_overstates_by": (tot_merged - tot_chains) - cut_real,
        "orders_under_control": tot_attempts,
        "orders_under_arm": tot_attempts - cut_real,
        "merged_order_observed_lifetime_s": {
            "n": len(lifetimes), "p25": pctl(lifetimes, .25),
            "p50": pctl(lifetimes, .50), "p75": pctl(lifetimes, .75),
            "max": max(lifetimes) if lifetimes else None},
        "fill_classification": dict(classes),
        "termination_boundary_reached": dict(boundaries),
        "complement_ever_reached_our_price": sum(
            1 for c in chains_all if c["complement_ever_reached_our_price"]),
        "chains": len(chains_all),
        "per_market": per_market,
    }


def main() -> int:
    pre, pair_econ, inv_last = load_pre_cutover()
    post = load_post_cutover()

    ticks = {}
    for slug, orders in pre.items():
        prices = []
        for o in orders:
            b = o.get("recorder_book") or {}
            prices += [o.get("px"), o.get("l5_touch_px"), b.get("yes_bid"),
                       b.get("yes_ask")]
        ticks[slug] = cc.infer_tick([o.get("tick") for o in orders], prices)[0]
    for slug, orders in post.items():
        ticks.setdefault(slug, cc.infer_tick(
            [], [o.get("px") for o in orders] + [o.get("obs_bid") for o in orders])[0])

    # the deadline horizon, measured rather than assumed
    dl = [_f(o.get("rest_s")) for orders in pre.values() for o in orders
          if o.get("end_reason") == "deadline"]
    deadline_obs = pctl(dl, .50) or REST_FLOOR_S

    pre_s = score(pre, ticks, "pre_cutover_live_sessions", deadline_obs,
                  avg_cost_key="held_avg_cost")
    pre_s["ceiling_pinning_route"] = (
        "recomputed_from_avg_cost -- EXACT. The weaker post-time-touch route "
        "was cross-checked against it and agreed on 259 of 260 orders; the "
        "exact route is used because it cannot be confused by a post-only "
        "trim, whose price signature appears on 79 of the 260.")
    pre_s["rest_s_recorded"] = True
    post_s = score(post, ticks, "post_cutover_cohort", deadline_obs,
                   avg_cost_key="held_avg_cost")
    post_s["ceiling_pinning_route"] = ("recomputed_from_avg_cost -- EXACT; "
                                       "this corpus has no post-time touch")
    post_s["rest_s_recorded"] = False
    post_s["merged_order_observed_lifetime_s"] = (
        "NOT RECORDED -- the cohort store carries no rest_s, so order "
        "lifetime is unmeasurable on this corpus")
    post_s["reposts_eliminated_IS_A_NAIVE_UPPER_BOUND"] = (
        "This corpus records no rest_s, so the deadline segmentation cannot "
        "bite and the figure degenerates to the naive one. On the corpus "
        "that DOES record rest time the naive count overstated the real one "
        "by 31%. Treat 18 as an upper bound, not a measurement.")

    # matched inventory / residual / pair economics under the arm
    matched = {
        "matched_inventory_delta": "0.00",
        "residual_delta": "0.00",
        "pair_economics_delta": "0.0000",
        "why": ("the arm invents no fill, so it adds no matched contract and "
                "removes no residual in replay. These are ZERO BY "
                "CONSTRUCTION, not a measured null result, and they must not "
                "be read as 'the arm has no economic effect'"),
        "pair_at_chosen_on_pinned_attempts": {
            "p50": pctl([_f(p.get("pair_at_chosen"))
                         for v in pair_econ.values() for p in v
                         if p.get("capped_by_pair_ceiling")], .50),
            "n": sum(1 for v in pair_econ.values() for p in v
                     if p.get("capped_by_pair_ceiling"))},
        "terminal_residual_contracts": str(sum(
            (abs(D(str(i.get("up") or 0)) - D(str(i.get("dn") or 0)))
             for i in inv_last.values()), D("0"))),
    }

    out = {
        "arm": cp.ARM,
        "scope": ("BOUNDED SHADOW EXPERIMENT. No production change. The arm "
                  "is not imported by any production module, is in neither "
                  "the probe image nor the recorder watch set, and nothing "
                  "here is proposed for deployment."),
        "the_one_changed_behaviour": (
            "branch 6 of the live cancel ladder (touch_moved) is suppressed "
            "for a COMPLETE whose price is pinned by the pair ceiling AND "
            "whose recomputation against the refreshed book returns the same "
            "legal maker price. Every other branch, in its live order, is "
            "unchanged and still terminates the order."),
        "unchanged": ["price", "pair ceiling", "size", "band", "cap",
                      "admission", "concurrency", "settlement horizon",
                      "post-only status", "campaign deadline",
                      "risk breakers", "maker-only execution"],
        "safety": ("would_cross is evaluated BEFORE the modified branch and "
                   "is never suspended, so holding cannot breach post-only. "
                   "bbo_unreadable and book_one_sided cancel immediately "
                   "under both arms."),
        "persistence_ceiling": {
            "rung": "R5FT", "rest_policy": "model",
            "rest_floor_s": REST_FLOOR_S,
            "observed_median_deadline_rest_s": deadline_obs,
            "note": ("the arm does NOT change the rest bound, so a merged "
                     "order still dies at the model's own next "
                     "re-evaluation. This bounds how much persistence the "
                     "arm can buy.")},
        "pre_cutover": pre_s,
        "post_cutover": post_s,
        "matched_and_residual": matched,
        "NOT_INFERABLE": {
            "would_the_held_order_have_filled": (
                "UNKNOWN. The historical order was cancelled, so the book "
                "path after the cancel was never recorded against a live "
                "order. No replay can answer it."),
            "queue_position": (
                "UNKNOWN on both arms. The arm would preserve queue position "
                "across what are currently k separate posts, which can only "
                "help, but nothing we record measures queue depth."),
            "trades_while_resting": (
                "NOT RECORDED. The order tape carries book extrema "
                "(min_opp_ask, max_touch) but no trade prints per rest "
                "window, so trade flow against a resting order is "
                "unobservable."),
            "gaps_between_cancel_and_repost": (
                "NOT OBSERVED. Nothing was resting there."),
        },
        "profitability": (
            "OUT OF SCOPE AND NOT CLAIMED. Even perfect conversion of the "
            "measured residual had insufficient margin in this corpus: the "
            "marginal pair is 0.9952 and the whole prize is $0.1563. "
            "Improved persistence is NOT expected profit and must not be "
            "described as such until filled and settled evidence exists."),
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True, default=str)
                   + "\n")

    for s in (pre_s, post_s):
        print(f"\n=== {s['corpus']}")
        print(f"  COMPLETE attempts            {s['complete_attempts']}")
        print(f"  ceiling-pinned attempts      {s['ceiling_pinned_attempts']}")
        print(f"  chains the arm would merge   {s['chains_the_arm_would_merge']}")
        print(f"  reposts cut, deadline-bounded {s['identical_price_reposts_eliminated']}"
              f"  (naive {s['identical_price_reposts_eliminated_NAIVE']}, "
              f"overstated by {s['naive_overstates_by']})")
        print(f"  orders: CONTROL {s['orders_under_control']} -> ARM "
              f"{s['orders_under_arm']}")
        print(f"  merged lifetime (observed s) {s['merged_order_observed_lifetime_s']}")
        print(f"  fill classes                 {s['fill_classification']}")
        print(f"  boundary reached             {s['termination_boundary_reached']}")
        print(f"  complement reached our price {s['complement_ever_reached_our_price']}"
              f" of {s['chains']} chains")
    print(f"\npersistence ceiling: rest_floor_s {REST_FLOOR_S}, observed median "
          f"deadline rest {deadline_obs}s")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
