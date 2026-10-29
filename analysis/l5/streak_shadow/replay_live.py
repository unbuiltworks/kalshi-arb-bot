"""P0/P1/P3: three decisions side by side at every recorded live decision.

SHADOW ONLY. This reads decisions the live engine ALREADY made and logged;
it submits nothing, changes nothing, and is not importable by the probe.

  FROZEN_MODEL   the `relation_ranked` the live engine actually computed
  STREAK_SHADOW  the same key conditioned on side streak (l5_streak_shadow)
  ACTED          the relation the safety/execution shell actually permitted

Both arms are drawn with THE SAME u the live engine drew with, so a
disagreement between them is a disagreement between probabilities.

    python3 analysis/l5/streak_shadow/replay_live.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_streak_shadow as ss            # noqa: E402

HERE = Path(__file__).resolve().parent
SESSIONS = HERE / "live_sessions.jsonl.gz"
ROWS_OUT = HERE / "live_rows.jsonl.gz"
JSON_OUT = HERE / "live_replay.json"

RELATIONS = ss.RELATIONS
ACTIONS = ("open", "extend", "complete", "hold")

# The owner's override-reason taxonomy, verbatim, plus the two names the
# live shell actually emits mapped onto it. Nothing is invented: a reason
# the shell does not emit reports 0 and is still listed, because "this gate
# never fired" is a finding and a missing row is not.
TAXONOMY = ("pair_ceiling", "fee_safe", "post_only", "no_legal_maker_price",
            "opening_budget", "residual_risk", "inventory_state",
            "stale_evidence", "settlement_horizon", "order_rest_limit",
            "other")


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def classify_reason(d: dict) -> tuple[str, str]:
    """(taxonomy bucket, the shell's own name for the gate).

    The shell's name is kept beside the bucket because `entry_band_left`
    is not any of the owner's eleven categories: it is an ENTRY-admission
    price band being applied to a decision that is often not an entry.
    Folding it silently into `inventory_state` or `residual_risk` would
    hide exactly the thing worth reporting.
    """
    caps = [str(c) for c in (d.get("cap_binding") or [])]
    refusal = str(d.get("refusal") or "")
    div = str(d.get("divergence") or "")
    name = caps[0] if caps else (refusal.split(":")[0] if refusal else
                                 (div.split(":")[-1] if div else "none"))
    bucket = {
        "entry_band_left": "other",
        "residual_cap": "residual_risk",
        "spend_cap": "opening_budget",
        "spend_cap_reached": "opening_budget",
        "no_legal_maker_price": "no_legal_maker_price",
        "complement_not_priceable": "no_legal_maker_price",
        "pair_ceiling": "pair_ceiling",
        "opening_budget_spent": "opening_budget",
        "stale_evidence": "stale_evidence",
    }.get(name, "other")
    return bucket, name


def executability(d: dict) -> tuple[str, str]:
    """P3 class A / B / C for one overridden decision.

    A  the action was genuinely economically impossible or unsafe
    B  the venue would have taken it; OUR implementation policy refused
    C  unknown -- the record does not carry enough to say

    Judged only from what the record states. `entry_band_left` fires on the
    BID leaving an admission band; it is not a statement that no legal
    maker price existed, that the pair ceiling was breached, or that the
    venue would have rejected the order -- so it is B, which says the
    constraint is ours, NOT that the constraint is wrong.
    """
    _, name = classify_reason(d)
    if name == "entry_band_left":
        band = d.get("entry_band_left") or {}
        bid = _f(band.get("bid"), None)
        if bid is None:
            return "C", "entry_band_left without a recorded bid"
        return ("B", f"admission band refused a bid of {bid:g}; the record "
                     "states no venue-side impossibility")
    if name == "residual_cap":
        return ("A", "residual cap is a declared risk bound; exceeding it is "
                     "unsafe by the owner's own envelope, not a venue limit")
    if name in ("no_legal_maker_price", "complement_not_priceable"):
        return "A", "no legal maker price existed at this book"
    if name in ("spend_cap", "spend_cap_reached", "opening_budget_spent"):
        return ("B", "budget exhaustion is our policy, not the venue's")
    return "C", f"unclassified gate {name!r}"


def main() -> int:
    model = ss.load()
    decisions = []
    orders_by_chunk = []
    import gzip
    raw = (gzip.decompress(SESSIONS.read_bytes()).decode()
           if SESSIONS.suffix == ".gz" else SESSIONS.read_text())
    for line in raw.splitlines():
        o = json.loads(line)
        ds = o.get("decisions") or []
        decisions.extend(ds)
        orders_by_chunk.append((tuple(d.get("decision_id") for d in ds),
                                o.get("orders") or []))
    # ORDERS JOIN BY THEIR OWN INDEX, NOT BY POSITION. Each order carries
    # `stage` = "spring_<relation>_<k>[_retryN]", where k is the 1-based
    # position of the decision inside that log chunk. A positional join over
    # acting decisions looked reasonable and was wrong: chunks routinely end
    # before their last decision's order is logged, and retries add extra
    # rows, so the two lists drift and every later order in the chunk is
    # attributed to the wrong decision. The index in `stage` is what the
    # engine itself recorded; the relation is then verified against it.
    by_id = {d.get("decision_id"): d for d in decisions}
    fill_by_decision: dict[str, dict] = {}
    join_ok = join_bad = 0
    for ids, orders in orders_by_chunk:
        for o in orders:
            parts = str(o.get("stage") or "").split("_")
            k = next((int(p) for p in parts if p.isdigit()), None)
            if k is None or not (1 <= k <= len(ids)):
                join_bad += 1
                continue
            did = ids[k - 1]
            if str(o.get("relation")) != str((by_id.get(did) or {}).get("acted")):
                join_bad += 1
                continue
            prev = fill_by_decision.get(did)
            # a retry supersedes its own first attempt only if it filled;
            # otherwise the first attempt's outcome is the decision's outcome
            if prev is None or (_f(o.get("filled_qty")) > _f(prev.get("filled_qty"))):
                fill_by_decision[did] = o
            join_ok += 1

    sessions = ss.group_campaigns(decisions)
    rows = []
    for (slug, sid), ds in sessions.items():
        streaks = ss.live_streaks(ds)
        for d, run in zip(ds, streaks):
            lk = d.get("lookup") or {}
            st = (d.get("shadow") or {}).get("stochastic") or {}
            u = st.get("u")
            frozen_ranked = d.get("relation_ranked") or []
            sk = ss.streak_bucket(run)
            sd = ss.streak_dist(model, lk.get("state") or d.get("state") or "",
                                lk.get("last_rel") or "open",
                                lk.get("last_size_behavioral") or "q<5", sk)
            streak_masses = ss.relation_masses(sd["dist"])
            streak_ranked = ss.ranked_from_masses(streak_masses)
            frozen_draw = ss.inverse_cdf(frozen_ranked, u)
            streak_draw = ss.inverse_cdf(streak_ranked, u)
            acted = d.get("acted")
            order = fill_by_decision.get(d.get("decision_id")) or {}
            bucket, gate = classify_reason(d)
            converted = bool(d.get("cap_binding")) or (
                st.get("sampled") is not None and acted != st.get("sampled"))
            cls, why = executability(d) if converted else ("", "")
            inv = d.get("inventory_before") or {}
            pre = d.get("complement_preflight") or {}
            rows.append({
                "decision_id": d.get("decision_id"),
                "slug": slug, "session_id": sid,
                "t_utc": d.get("t_utc"),
                "tte_bucket": (d.get("state") or "||").split("|")[1],
                "frozen_key": lk.get("full_key_v1"),
                "base_state": d.get("state"),
                "side_streak": run, "streak_bucket": sk,
                "streak_cell_n": sd["n"], "streak_backed_off": sd["backed_off"],
                "inventory_before": {k: inv.get(k) for k in
                                     ("up", "dn", "matched", "residual",
                                      "spent", "unmatched_usd")},
                "frozen_masses": {r["relation"]: r["p"] for r in frozen_ranked},
                "streak_masses": {k: round(v, 6)
                                  for k, v in streak_masses.items()},
                "u": u,
                "sampled_frozen": frozen_draw,
                "sampled_frozen_logged": st.get("sampled"),
                "sampled_streak": streak_draw,
                "acted": acted,
                "conversion": (f"{st.get('sampled')}->{acted}"
                               if converted else "none"),
                "conversion_reason_bucket": bucket if converted else None,
                "conversion_gate": gate if converted else None,
                "executability_class": cls or None,
                "executability_why": why or None,
                "px": order.get("px"),
                "qty": order.get("qty"),
                "filled_qty": order.get("filled_qty"),
                "fill_outcome": order.get("outcome"),
                "rest_s": order.get("rest_s"),
                "pair_economics": {
                    "chosen_px": pre.get("chosen_px"),
                    "opp_ask": pre.get("opp_ask"),
                    "pair_cost_max": pre.get("pair_cost_max"),
                    "spring_pair_target": pre.get("spring_pair_target"),
                    "l5_touch_px": order.get("l5_touch_px"),
                    "fresh_opp_ask": order.get("fresh_opp_ask"),
                } if (pre or order) else None,
                "safety_shell_version": d.get("safety_shell_version"),
            })

    # ---- faithfulness: our draw must reproduce the engine's own draw
    drawn = [r for r in rows if r["sampled_frozen_logged"] is not None]
    agree = sum(1 for r in drawn
                if r["sampled_frozen"] == r["sampled_frozen_logged"])

    def freq(key, universe=ACTIONS):
        c = Counter(r[key] for r in rows if r[key])
        n = sum(c.values()) or 1
        return {a: round(c.get(a, 0) / n, 4) for a in universe}, sum(c.values())

    def l1(a, b):
        return round(sum(abs(a.get(k, 0.0) - b.get(k, 0.0))
                         for k in set(a) | set(b)), 4)

    # L5's own expected distribution over these very states
    exp = {r: 0.0 for r in RELATIONS}
    for r in rows:
        for k, v in r["frozen_masses"].items():
            if k in exp:
                exp[k] += float(v)
    exp = {k: round(v / max(len(rows), 1), 4) for k, v in exp.items()}
    exp_streak = {r: 0.0 for r in RELATIONS}
    for r in rows:
        for k, v in r["streak_masses"].items():
            exp_streak[k] += float(v)
    exp_streak = {k: round(v / max(len(rows), 1), 4)
                  for k, v in exp_streak.items()}

    f_frozen, _ = freq("sampled_frozen", RELATIONS)
    f_streak, _ = freq("sampled_streak", RELATIONS)
    f_acted, _ = freq("acted")

    matrix = defaultdict(Counter)
    for r in rows:
        if r["sampled_frozen_logged"]:
            matrix[r["sampled_frozen_logged"]][r["acted"]] += 1
    conv_matrix = {s: {a: matrix[s].get(a, 0) for a in ACTIONS}
                   for s in RELATIONS}

    converted_rows = [r for r in rows if r["conversion"] != "none"]
    reasons = Counter(r["conversion_reason_bucket"] for r in converted_rows)
    gates = Counter(r["conversion_gate"] for r in converted_rows)
    classes = Counter(r["executability_class"] for r in converted_rows)
    nconv = len(converted_rows) or 1

    streak_dist_counts = Counter(r["streak_bucket"] for r in rows)
    arms_differ = [r for r in rows
                   if r["sampled_frozen"] != r["sampled_streak"]]

    # ---- did the acted order actually fill? (P0: "eventual fill/no-fill")
    def fill_stats(sel):
        sub = [r for r in rows if sel(r) and r["fill_outcome"]]
        filled = [r for r in sub if _f(r["filled_qty"]) > 0]
        return {"orders": len(sub), "filled": len(filled),
                "fill_rate": round(len(filled) / len(sub), 4) if sub else None,
                "outcomes": dict(Counter(r["fill_outcome"] for r in sub))}

    fills = {rel: fill_stats(lambda r, rel=rel: r["acted"] == rel)
             for rel in RELATIONS}
    # the completions the shell MANUFACTURED, against the ones the model
    # chose: if the forced ones fill far less often, the override is not
    # merely a behavioural substitution, it is a worse trade
    fills["complete_forced_by_override"] = fill_stats(
        lambda r: r["acted"] == "complete" and r["conversion"] != "none")
    fills["complete_chosen_by_model"] = fill_stats(
        lambda r: r["acted"] == "complete" and r["conversion"] == "none")

    doc = {
        "labels": "SHADOW ONLY; no live behaviour was changed by this replay",
        "fills": fills,
        "corpus": {
            "decisions": len(rows),
            "markets": len({r["slug"] for r in rows}),
            "campaigns": len(sessions),
            "first_t_utc": min(r["t_utc"] for r in rows) if rows else None,
            "last_t_utc": max(r["t_utc"] for r in rows) if rows else None,
            "safety_shell_versions": sorted(
                {r["safety_shell_version"] for r in rows}),
            "order_join": {"joined": join_ok, "unjoinable": join_bad},
        },
        "rng_control": {
            "draws_checked": len(drawn),
            "reproduced_the_engines_own_draw": agree,
            "note": ("both arms are drawn from the engine's own u; a "
                     "mismatch here would invalidate every comparison below"),
        },
        "distributions": {
            "l5_expected_frozen": exp,
            "l5_expected_streak": exp_streak,
            "sampled_frozen": f_frozen,
            "sampled_streak": f_streak,
            "acted_incl_hold": f_acted,
            "l1_sampled_frozen_vs_l5": l1(f_frozen, exp),
            "l1_sampled_streak_vs_l5": l1(f_streak, exp),
            "l1_acted_vs_l5": l1(f_acted, exp),
            "l1_acted_vs_sampled_frozen": l1(f_acted, f_frozen),
        },
        "conversion_matrix_sampled_to_acted": conv_matrix,
        "override_reasons": {
            "converted_decisions": len(converted_rows),
            "share_of_all_decisions": round(len(converted_rows) /
                                            max(len(rows), 1), 4),
            "by_taxonomy": {k: {"n": reasons.get(k, 0),
                                "pct_of_overrides": round(
                                    100 * reasons.get(k, 0) / nconv, 1)}
                            for k in TAXONOMY},
            "by_shell_gate": {k: {"n": v,
                                  "pct_of_overrides": round(100 * v / nconv, 1)}
                              for k, v in gates.most_common()},
        },
        "executability_classes": {
            k: {"n": v, "pct_of_overrides": round(100 * v / nconv, 1)}
            for k, v in classes.most_common()},
        "streak_coverage": {
            "by_bucket": dict(streak_dist_counts),
            "cells_with_evidence": sum(1 for r in rows
                                       if not r["streak_backed_off"]),
            "cells_backed_off_to_production": sum(1 for r in rows
                                                  if r["streak_backed_off"]),
        },
        "arms_disagree": {
            "n": len(arms_differ),
            "pct": round(100 * len(arms_differ) / max(len(rows), 1), 1),
            "transitions": dict(Counter(
                f"{r['sampled_frozen']}->{r['sampled_streak']}"
                for r in arms_differ)),
        },
    }
    blob = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode()
    ROWS_OUT.write_bytes(gzip.compress(blob, 9) if ROWS_OUT.suffix == ".gz"
                         else blob)
    JSON_OUT.write_text(json.dumps(doc, indent=1, sort_keys=True))
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
