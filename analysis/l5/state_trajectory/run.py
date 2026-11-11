#!/usr/bin/env python3
"""P0 ECONOMIC-STATE TRAJECTORY DECOMPOSITION -- read-only.

    python3 analysis/l5/state_trajectory/run.py

L5 canonical | PMUS sports | Gemini 5m | Gemini 15m, on one walker.
Nothing here submits, prices, cancels, reprices or deploys.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapters as A                                      # noqa: E402
from src import l5_state_trajectory as st                 # noqa: E402

L5SEQ = ROOT / "analysis/l5/l5_canonical_sequence.csv.gz"
SESSIONS = ROOT / "analysis/l5/streak_shadow/live_sessions.jsonl.gz"
GEM5 = ROOT / "analysis/l5/gemini_l5_shadow_v2.json"
GEM15 = ROOT / "analysis/l5/gemini_counterfactual_15m_rows.jsonl"
OUT = Path(__file__).resolve().parent / "state_trajectory.json"


def _f(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def median(v):
    v = sorted(x for x in v if x is not None)
    return v[len(v) // 2] if v else None


def profile(episodes: dict, name: str, clip_norm: bool = True) -> dict:
    """Every state distribution the comparison needs, from one walker."""
    states, terms = [], []
    for ep, fills in episodes.items():
        s = st.walk(fills)
        if not s:
            continue
        states += s
        terms.append(st.terminal(s, fills))
    clip = median(s["action_qty"] for s in states) or 1.0

    def norm(v):
        return (v / clip) if (clip_norm and v is not None) else v

    switches = [s for s in states if s["is_switch"]]
    dec = [d for d in (st.decompose_excess_pair(s) for s in switches) if d]
    return {
        "venue": name,
        "episodes": len(terms),
        "fills": len(states),
        "switches": len(switches),
        "median_clip_contracts": round(clip, 4),
        "clip_normalised": clip_norm,
        "gain_per_unit": st.distribution(
            (s["gain_per_unit"] for s in states), "gain_per_unit"),
        "realised_pair_cost": st.distribution(
            (s["realised_pair_cost"] for s in switches), "pair at a switch"),
        "attainable_pair_cost": st.distribution(
            (s["attainable_pair_cost"] for s in states), "attainable pair"),
        "excess_pair": st.distribution(
            (d["excess_pair"] for d in dec), "pair - 1.00"),
        "C_own_fills": st.distribution(
            (d["C_own_fills"] for d in dec), "held_avg - entry"),
        "B_market_move": st.distribution(
            (d["B_market_move"] for d in dec), "paid - (1 - entry)"),
        "fills_per_episode": st.distribution(
            (t["fills"] for t in terms), "fills/episode"),
        "switches_per_episode": st.distribution(
            (t["switches"] for t in terms), "switches/episode"),
        "matched_fraction_terminal": st.distribution(
            (t["matched_fraction"] for t in terms), "terminal matched frac"),
        "terminal_residual_clips": st.distribution(
            (norm(abs(t["residual_signed"])) for t in terms),
            "terminal |residual| in clips"),
        "residual_trajectory_clips": st.distribution(
            (norm(abs(s["residual_signed"])) for s in states),
            "|residual| at every state, in clips"),
        "seconds_between_fills": st.distribution(
            (s["dt_since_prev_fill"] for s in states), "fill cadence"),
        "seconds_between_switches": st.distribution(
            (s["dt_since_prev_switch"] for s in switches), "switch cadence"),
        "tte_s": st.distribution((s["tte_s"] for s in states), "TTE"),
        "entry_price": st.distribution(
            (s["entry_price"] for s in states), "entry price"),
        "degeneracy": st.degeneracy(states),
        "gain_bucket_mix": {k: round(v, 4) for k, v in
                            st.weights(states, "gain_bucket").items()},
        "price_band_mix": {k: round(v, 4) for k, v in
                           st.weights(states, "price_band").items()},
        "tte_bucket_mix": {k: round(v, 4) for k, v in
                           st.weights(states, "tte_bucket").items()},
        "_states": states,
        "_switch_decomp": dec,
    }


def perfect_conversion_states(orders: dict) -> list:
    """COUNTERFACTUAL: every COMPLETE fills at the price it was posted at.

    AUTHORISED AND LABELLED. The owner asked exactly this question, so fills
    ARE imputed here -- and only here, only for COMPLETE, only at the price
    actually posted, and only to answer a STATE-DISTRIBUTION question. No
    P&L, no realized figure and no fill-rate claim may be taken from it.
    Timestamps are absent from the order stream, so no cadence field is
    derived.

    AND THE IMPUTATION IS CAPPED BY THE RESIDUAL ACTUALLY HELD. A COMPLETE
    exists to close inventory we own; once the position is flat the engine
    posts no further COMPLETE, so imputing every attempt in full would run
    the position through flat and out the other side -- which is not
    "perfect conversion", it is a different and much worse strategy. Each
    imputed fill is therefore trimmed to the residual standing in front of
    it, and an attempt facing no residual is dropped. The trimmed and
    dropped counts are reported.
    """
    eps, stats = {}, {"imputed": 0, "trimmed": 0, "dropped_flat": 0,
                      "imputed_contracts": 0.0}
    for slug, os_ in orders.items():
        fills = []
        qty = {"up": 0.0, "dn": 0.0}
        for o in os_:
            px, q = _f(o.get("px")), _f(o.get("qty"))
            got = _f(o.get("filled_qty")) or 0.0
            if px is None or q is None or q <= 0:
                continue
            if str(o.get("end_reason") or "").startswith("rejected"):
                continue                      # the venue refused it; not a fill
            side = "up" if o.get("side") == "YES" else "dn"
            if got > 0:
                fills.append({"t": None, "side": side, "price": px,
                              "qty": got})
                qty[side] += got
            elif o.get("relation") == "complete":
                opp = "dn" if side == "up" else "up"
                room = qty[opp] - qty[side]           # residual this closes
                if room <= 1e-9:
                    stats["dropped_flat"] += 1
                    continue
                use = min(q, room)
                if use < q:
                    stats["trimmed"] += 1
                stats["imputed"] += 1
                stats["imputed_contracts"] += use
                fills.append({"t": None, "side": side, "price": px,
                              "qty": use, "imputed": True})
                qty[side] += use
        if fills:
            eps[slug] = fills
    stats["imputed_contracts"] = round(stats["imputed_contracts"], 2)
    return eps, stats


def gemini_audit() -> dict:
    """What Gemini can and cannot supply, counted rather than assumed."""
    g5 = json.loads(GEM5.read_text())
    arms = {k: {"campaigns": v.get("campaigns"),
                "first_leg_fills": v.get("first_leg_fills"),
                "complement_orders": v.get("complement_orders"),
                "complement_fills": v.get("complement_fills"),
                "fully_matched_campaigns": v.get("fully_matched_campaigns"),
                "median_pair_cost_after_fees":
                    v.get("median_pair_cost_after_fees")}
            for k, v in g5["results"].items()}
    rows = [json.loads(l) for l in open(GEM15) if l.strip()]
    g15_fills = sum(len(r.get("fills") or []) for r in rows)
    g15_matched = sum(1 for r in rows
                      if _f((r.get("inventory") or {}).get("matched")))
    best = max(arms.values(), key=lambda a: a.get("complement_fills") or 0)
    return {
        "gemini_5m": {
            "source": str(GEM5.relative_to(ROOT)),
            "underlying": "BTC 5m (gemini_bbo_btc05m / gemini_prints_btc05m)",
            "arms": arms,
            "best_arm_complement_fills": best.get("complement_fills"),
            "best_arm_matched_campaigns": best.get("fully_matched_campaigns"),
            "state_distributions": "UNAVAILABLE",
            "why": ("gain_per_unit needs a running average over repeated "
                    "same-side fills and a pair needs a completion. The best "
                    "arm has 2 complement fills and 2 matched campaigns out "
                    "of 118; three of the four arms have ZERO. n=2 cannot "
                    "carry a distribution."),
            "labels": g5.get("labels")},
        "gemini_15m": {
            "source": str(GEM15.relative_to(ROOT)),
            "rows": len(rows),
            "status_mix": dict(Counter(r["status"] for r in rows)),
            "total_fills": g15_fills,
            "rows_with_a_fill": sum(1 for r in rows if r.get("fills")),
            "matched_campaigns": g15_matched,
            "state_distributions": "UNAVAILABLE",
            "why": (f"{g15_fills} fills across {len(rows)} rows and zero "
                    "matched inventory. No pair was ever formed.")},
        "verdict": ("Gemini is NOT more L5-like on these distributions -- it "
                    "has essentially no pair formation at all. This is an "
                    "availability finding, not a performance comparison, and "
                    "it must not be read as either."),
    }


def gemini_schema() -> dict:
    """The exact per-fill record the Gemini workstream must return."""
    return {
        "purpose": ("one row PER FILL, chronological within an episode, so "
                    "src/l5_state_trajectory.walk can build the identical "
                    "states for Gemini that it builds for L5 and PMUS. "
                    "Return fills, not summaries: every distribution in this "
                    "study is rebuilt from the fill stream and cannot be "
                    "recovered from aggregates."),
        "required_fields": {
            "episode_id": "str -- one market window / campaign",
            "venue_duration": "str -- '5m' | '15m'",
            "seq": "int -- 1-based order within the episode",
            "t": "float epoch seconds of the FILL (not the order)",
            "side": "'up' | 'dn' -- the OUTCOME bought, not BUY/SELL",
            "price": "float in (0,1)",
            "qty": "float contracts filled",
            "tte_s": "float seconds to settlement AT THE FILL",
            "maker_taker": "'maker' | 'taker' | 'unknown'",
        },
        "required_if_a_shell_selected_the_action": {
            "sampled": "the action the model drew, before any shell gate",
            "acted": "the action actually taken",
            "refusal": "the gate that changed it, or null",
        },
        "required_per_episode": {
            "settlement_outcome": "'up' | 'dn' | null if unsettled",
            "terminal_up": "float", "terminal_dn": "float",
            "realized_usd": "venue-confirmed, or null -- never a proxy",
        },
        "must_not_be_sent": [
            "assumed or imputed fills of any kind",
            "mid-proxy settlements presented as realized",
            "aggregates in place of the fill stream",
        ],
        "minimum_to_be_usable": {
            "matched_campaigns": ">= 30 with a formed pair",
            "complement_fills": ">= 30",
            "reason": ("below that the gain_per_unit and pair-cost "
                       "distributions cannot be compared to L5's 21,739 "
                       "switches at any useful resolution"),
        },
        "query_shape": (
            "SELECT episode_id, venue_duration, seq, t, side, price, qty, "
            "tte_s, maker_taker, sampled, acted, refusal FROM fills "
            "WHERE filled = true ORDER BY episode_id, seq"),
    }


def main() -> int:
    l5_eps, l5_dropped = A.l5_canonical(L5SEQ)
    pmus_eps, pmus_unrec = A.pmus_from_inventory_deltas(SESSIONS)
    orders = A.pmus_orders_in_sequence(SESSIONS)

    l5 = profile(l5_eps, "L5_canonical")
    pm = profile(pmus_eps, "PMUS_sports")
    pc_eps, pc_stats = perfect_conversion_states(orders)
    pc = profile(pc_eps, "PMUS_perfect_COMPLETE_conversion_COUNTERFACTUAL")
    pc["imputation"] = {**pc_stats,
                        "labels": ("COUNTERFACTUAL state distribution only. "
                                   "Every imputed fill is capped by the "
                                   "residual standing in front of it. No "
                                   "P&L, realized figure or fill-rate claim "
                                   "may be taken from this column.")}

    # ---- OBSERVED decomposition of the pair-cost gap (exact identity)
    gap = {
        "l5_mean_excess_pair": l5["excess_pair"].get("mean"),
        "pmus_mean_excess_pair": pm["excess_pair"].get("mean"),
        "gap": (None if pm["excess_pair"].get("mean") is None
                or l5["excess_pair"].get("mean") is None
                else round(pm["excess_pair"]["mean"]
                           - l5["excess_pair"]["mean"], 4)),
        "C_own_fills": {"l5": l5["C_own_fills"].get("mean"),
                        "pmus": pm["C_own_fills"].get("mean")},
        "B_market_move": {"l5": l5["B_market_move"].get("mean"),
                          "pmus": pm["B_market_move"].get("mean")},
        "identity": ("excess_pair = C + B exactly; the gap in excess_pair is "
                     "therefore exactly the sum of the gaps in C and B"),
        "max_identity_residual": max(
            [abs(d["identity_residual"])
             for d in (l5["_switch_decomp"] + pm["_switch_decomp"])] or [0]),
        "n": {"l5_switches_with_a_pair": l5["excess_pair"].get("n"),
              "pmus_switches_with_a_pair": pm["excess_pair"].get("n")},
    }
    if gap["gap"] is not None:
        gap["dC"] = round((pm["C_own_fills"]["mean"]
                           - l5["C_own_fills"]["mean"]), 4)
        gap["dB"] = round((pm["B_market_move"]["mean"]
                           - l5["B_market_move"]["mean"]), 4)
        gap["dC_plus_dB"] = round(gap["dC"] + gap["dB"], 4)

    # ---- COUNTERFACTUAL reweightings, each alone, never summed
    cf = {}
    for comp, key in (("A_entry_state_price", "price_band"),
                      ("F_tte_regime", "tte_bucket"),
                      ("F_gain_state", "gain_bucket")):
        cf[comp] = st.reweight(pm["_states"], key,
                               st.weights(l5["_states"], key), "excess_pair") \
            or {"unavailable": f"no shared {key} strata carry an excess_pair"}
    # excess_pair only exists on switch states, so reweight those directly
    for comp, key in (("A_entry_state_price", "price_band"),
                      ("F_tte_regime", "tte_bucket")):
        src = [{**d, "price_band": s["price_band"],
                "tte_bucket": s["tte_bucket"]}
               for s, d in zip([x for x in pm["_states"] if x["is_switch"]],
                               pm["_switch_decomp"])]
        tgt = [{**d, "price_band": s["price_band"],
                "tte_bucket": s["tte_bucket"]}
               for s, d in zip([x for x in l5["_states"] if x["is_switch"]],
                               l5["_switch_decomp"])]
        cf[comp] = st.reweight(src, key, st.weights(tgt, key), "excess_pair") \
            or {"unavailable": "no shared strata"}

    # E: cadence / cycle count
    cf["E_inventory_cycles"] = {
        "l5_fills_per_episode_p50": l5["fills_per_episode"].get("p50"),
        "pmus_fills_per_episode_p50": pm["fills_per_episode"].get("p50"),
        "l5_switches_per_episode_p50": l5["switches_per_episode"].get("p50"),
        "pmus_switches_per_episode_p50": pm["switches_per_episode"].get("p50"),
        "labels": ("reported as a composition difference; PMUS has too few "
                   "episodes with >1 switch to reweight excess_pair by cycle "
                   "count without the estimate being driven by single "
                   "episodes")}

    # D: sampled -> acted shell divergence (PMUS only; L5 has no shell)
    conv = [s for s in pm["_states"]
            if s.get("sampled") and s.get("acted")
            and s["sampled"] != s["acted"]]
    same = [s for s in pm["_states"]
            if s.get("sampled") and s.get("acted")
            and s["sampled"] == s["acted"]]
    cf["D_shell_divergence"] = {
        "states_with_both_labels": len(conv) + len(same),
        "sampled_equals_acted": len(same),
        "sampled_differs_from_acted": len(conv),
        "gain_per_unit_when_equal": st.distribution(
            (s["gain_per_unit"] for s in same)),
        "gain_per_unit_when_differs": st.distribution(
            (s["gain_per_unit"] for s in conv)),
        "structural_note": ("L5 HAS NO SHELL, so this component exists on one "
                            "side only and can never be a like-for-like "
                            "contribution to the gap. It is reported as a "
                            "within-PMUS association."),
    }

    out = {
        "scope": ("READ-ONLY. No production change, no persistence "
                  "deployment, no taker, no cap/band/admission/sizing/"
                  "concurrency change; 392b5c0e remains deferred."),
        "method": {
            "one_walker": ("both venues are reconstructed by "
                           "src/l5_state_trajectory.walk from a common fill "
                           "record, so the comparison cannot measure two "
                           "different reconstructions"),
            "no_lookahead": ("every state is built from fills strictly "
                             "before it plus the action being taken"),
            "alignment": "episodes aligned at the FIRST FILL",
            "l5_non_buy_prints_dropped": l5_dropped,
            "pmus_fill_recovery": (
                "from the engine's own inventory ledger between TIMESTAMPED "
                "decisions; 44 fills recovered against 45 order rows with "
                "filled_qty>0 -- the one difference is a fill with no "
                "subsequent decision to observe it"),
            "pmus_unrecovered_fills": pmus_unrec,
            "pmus_cadence_is_an_upper_bound": (
                "a fill is stamped with the decision that OBSERVED it"),
        },
        "venues": {k: {kk: vv for kk, vv in v.items()
                       if not kk.startswith("_")}
                   for k, v in (("L5_canonical", l5), ("PMUS_sports", pm),
                                ("PMUS_perfect_conversion_CF", pc))},
        "observed_decomposition": gap,
        "counterfactual_decomposition": cf,
        "gemini": gemini_audit(),
        "gemini_required_schema": gemini_schema(),
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True, default=str)
                   + "\n")

    def row(lbl, a, b, c, key, sub="p50"):
        def g(x):
            v = x.get(key)
            return (v.get(sub) if isinstance(v, dict) else v)
        print(f"  {lbl:34s} {str(g(a)):>12} {str(g(b)):>12} {str(g(c)):>12}")

    print(f"\n{'':36s} {'L5':>12} {'PMUS':>12} {'PMUS perfect':>12}")
    print(f"  {'episodes':34s} {l5['episodes']:>12} {pm['episodes']:>12} "
          f"{pc['episodes']:>12}")
    print(f"  {'fills':34s} {l5['fills']:>12} {pm['fills']:>12} "
          f"{pc['fills']:>12}")
    print(f"  {'switches':34s} {l5['switches']:>12} {pm['switches']:>12} "
          f"{pc['switches']:>12}")
    for lbl, key in (("gain_per_unit p50", "gain_per_unit"),
                     ("realised pair p50", "realised_pair_cost"),
                     ("excess pair p50", "excess_pair"),
                     ("C own fills p50", "C_own_fills"),
                     ("B market move p50", "B_market_move"),
                     ("attainable pair p50", "attainable_pair_cost"),
                     ("fills/episode p50", "fills_per_episode"),
                     ("switches/episode p50", "switches_per_episode"),
                     ("terminal matched frac p50",
                      "matched_fraction_terminal"),
                     ("terminal |residual| clips p50",
                      "terminal_residual_clips"),
                     ("sec between fills p50", "seconds_between_fills"),
                     ("sec between switches p50", "seconds_between_switches")):
        row(lbl, l5, pm, pc, key)
    for lbl, key, sub in (
            ("gain exactly zero (share)", "degeneracy",
             "gain_exactly_zero_share"),
            ("states with a gain", "degeneracy", "states_with_a_gain")):
        print(f"  {lbl:34s} "
              f"{str(l5[key][sub]):>12} {str(pm[key][sub]):>12} "
              f"{str(pc[key][sub]):>12}")
    print(f"  {'median clip (contracts)':34s} "
          f"{l5['median_clip_contracts']:>12} "
          f"{pm['median_clip_contracts']:>12} "
          f"{pc['median_clip_contracts']:>12}")
    print(f"\nOBSERVED pair-cost gap: {gap.get('gap')} "
          f"(dC {gap.get('dC')}, dB {gap.get('dB')}, "
          f"sum {gap.get('dC_plus_dB')}); max identity residual "
          f"{gap['max_identity_residual']:.2e}")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
