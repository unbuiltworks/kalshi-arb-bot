"""L5 RESTRICTION AUDIT (owner program statement, 2026-09-07 17:55Z).

"L5 is the strategy. Pairing, rebates, residual caps, sports selection are
implementation/economic considerations, not replacements for his behavior.
Any safety override gets recorded explicitly as our deviation from L5. We
still need a hard catastrophic-loss shell around the ~$111 account, but
inside that shell: maximum L5 fidelity. The R5 question: what restrictions
are preventing us from executing what L5 would actually do, and how far
can we safely remove those restrictions with this bankroll?"

This module answers that question from evidence only:
  * L5's own behaviour on the canonical history (pair cost at completion,
    size, taker share, residual dollars, concurrency, reprice cadence);
  * every restriction in the live path, where it lives, and how often it
    bound in our live decisions (the persisted session ledgers: refusal /
    would_act_without_safety_cap / counterfactual_l5_price);
  * the bankroll shell: the catastrophic-loss bound expressed in dollars
    of the actual account, and each removal's worst case under it.

Nothing here changes a gate, a cap or the live path. Output:
  analysis/l5/l5_restriction_audit.json
  analysis/l5/L5_RESTRICTION_AUDIT.md
Every number derived from a model or a counterfactual is labelled
DERIVED; realized dollars come only from venue rows elsewhere.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import json
import statistics as st
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / "analysis" / "l5"
OUT_JSON = A / "l5_restriction_audit.json"
OUT_MD = A / "L5_RESTRICTION_AUDIT.md"

PROGRAM = ("L5 is the strategy; pairing, rebates, residual caps and sports "
           "selection are implementation/economic considerations, not "
           "replacements for his behavior; any safety override is recorded "
           "as OUR deviation from L5; a hard catastrophic-loss shell around "
           "the account, maximum L5 fidelity inside it.")

# --- the live path's restrictions (where they live; class per
# analysis/l5/execution_constraints.json and the R4 approval) ---
RESTRICTIONS = [
    {"id": "unmatched_usd_cap", "name": "Per-market unmatched-dollar cap",
     "where": "src/pmus_spring_l5_session.py max_unmatched_usd (R4 approval 5567269295: $2.50)",
     "ours": "2.50 USD unmatched per market", "class": "safety cap (owner)",
     "l5_field": "residual_usd_per_window", "ledger_refusal": "unmatched_usd_cap",
     "blocks": "EXTEND (L5's most frequent post-fill action)"},
    {"id": "pair_cost_ceiling", "name": "Completion pair-cost ceiling",
     "where": "spring_complement_price(): pair_cost_max 1.00 hard (R4), observation cap 0.98",
     "ours": "COMPLETE only at pair <= 1.00 (fee-safe bound price)", "class": "safety cap (owner)",
     "l5_field": "pair_unit_cost_at_completion", "ledger_refusal": "complement_not_priceable",
     "blocks": "COMPLETE at L5's touch when the pair would exceed 1.00"},
    {"id": "action_size", "name": "Per-action size / size cap",
     "where": "R4 per_action_target_usd 2.50, per_market_cap 7.50, venue minimum",
     "ours": "~5 shares / $2.50 per action", "class": "bankroll sizing (owner)",
     "l5_field": "shares_per_fill", "ledger_refusal": "spend_cap_reached",
     "blocks": "L5's size (p50 12.5 shares); size_divergence=venue_minimum_vs_l5_target on every row"},
    {"id": "maker_rest_30s", "name": "Maker rest floor before cancel/replace",
     "where": "src/pmus_spring_l5_session.py SPORTS_MAKER_REST_MAX_S = 30.0",
     "ours": "30 s rest, then cancel; reprice cadence >= 30 s", "class": "(b) owner execution policy, not a venue requirement",
     "l5_field": "reprice_cadence_s", "ledger_refusal": None,
     "blocks": "L5's 2-6 s reprice; fills after 20 s are 22% of ours"},
    {"id": "maker_only", "name": "Maker-only (post-only), taker disabled",
     "where": "executor post_only=True everywhere; live_policy 'maker/post-only only; taker disabled'",
     "ours": "0% taker", "class": "fee/risk policy (owner)",
     "l5_field": "taker_share", "ledger_refusal": None,
     "blocks": "L5's taker actions (2-3% of actions by state, 15% of fills overall)"},
    {"id": "concurrency_1", "name": "Concurrency (markets worked at once)",
     "where": "R4 rung concurrency 1; CAMPAIGN_HOLD:POSITION_HELD while a residual is held",
     "ours": "1 market at a time; a held residual blocks every other candidate", "class": "safety cap (owner)",
     "l5_field": "simultaneous_windows", "ledger_refusal": "position_held",
     "blocks": "L5 works 2+ windows 51% of his active time (median 2)"},
    {"id": "cohort_3_markets", "name": "Campaign cohort stop",
     "where": "R4 rung markets_allowed 3; rung stop at 3 settled",
     "ours": "3 markets then stop and report", "class": "approval ladder (owner)",
     "l5_field": None, "ledger_refusal": "rung_reached",
     "blocks": "continuous operation between reports"},
    {"id": "observation_band", "name": "Entry price band",
     "where": "OWNER DECISION 5533476555: observation 0.35-0.65 (spring_watch prefilter + entry_band gate)",
     "ours": "enter only with YES bid in 0.35-0.65", "class": "observation scope (owner)",
     "l5_field": "price_band_share", "ledger_refusal": "entry_band_left",
     "blocks": "L5 states outside the band (p<25c, p>65c) which the model already covers"},
    {"id": "strict_evidence_gate", "name": "Strict evidence gate (prints in window, two-sided flow, q1 touch depth)",
     "where": "src/pmus_l5_continuous_session.py strict admission (thin_flow, market_closed, no_q1_touch_depth)",
     "ours": "admit only markets with recent prints and served two-sided books", "class": "evidence requirement (owner 5526683554)",
     "l5_field": None, "ledger_refusal": "strict_evidence",
     "blocks": "nothing L5 does (L5 trades where flow exists); it excludes quiet books we could not evaluate honestly"},
    {"id": "off_distribution_hold", "name": "Off-distribution hold",
     "where": "frozen model: state not in the canonical history -> hold",
     "ours": "hold when the (state, relation) key is unknown", "class": "fidelity rule (frozen spec)",
     "l5_field": None, "ledger_refusal": "off_distribution_hold",
     "blocks": "acting where we have no L5 evidence; a fidelity protection, not a deviation"},
]


def _q(xs: list, q: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 4)


def l5_numbers(canon_path: Path = A / "l5_canonical_sequence.csv.gz",
               rows: Optional[list] = None) -> dict:
    """L5's own behaviour per restriction, from the canonical sequence."""
    if rows is None:
        with gzip.open(canon_path, "rt") as fh:
            rows = list(csv.DictReader(fh))
    mk = collections.Counter(r.get("maker_taker") for r in rows)
    known = mk.get("maker", 0) + mk.get("taker", 0)
    qty = [float(r["quantity"]) for r in rows if r.get("quantity")]
    dol = [float(r["dollars"]) for r in rows if r.get("dollars")]
    pc = [float(r["matched_pair_unit_cost"]) for r in rows
          if r.get("matched_pair_unit_cost") not in ("", "None", None)
          and float(r.get("matched_qty") or 0) > 0]
    win: dict = collections.defaultdict(list)
    for r in rows:
        win[r["window_id"]].append(r)
    residual_usd, spend = [], []
    price_in_band = 0
    for rs in win.values():
        last = rs[-1]
        rq = abs(float(last.get("residual_qty_signed") or 0))
        side = last.get("residual_side")
        uq, dq = float(last.get("cum_up_qty") or 0), float(last.get("cum_down_qty") or 0)
        us, ds = float(last.get("cum_up_spend") or 0), float(last.get("cum_down_spend") or 0)
        avg = (us / uq if uq else 0.0) if side == "UP" else (ds / dq if dq else 0.0)
        residual_usd.append(rq * avg)
        spend.append(sum(float(x.get("dollars") or 0) for x in rs))
    for r in rows:
        p = float(r.get("price") or 0)
        if 0.35 <= p <= 0.65:
            price_in_band += 1
    return {
        "source": str(canon_path.relative_to(ROOT)) if canon_path.is_relative_to(ROOT) else str(canon_path),
        "fills": len(rows), "windows": len(win),
        "taker_share": {"of_known": round(mk.get("taker", 0) / known, 4) if known else None,
                        "counts": dict(mk),
                        "labels": "maker/taker known only inside takerOnly feed coverage; unknown never treated as maker"},
        "shares_per_fill": {"p25": _q(qty, .25), "p50": _q(qty, .5), "p75": _q(qty, .75)},
        "dollars_per_fill": {"p50": _q(dol, .5), "p90": _q(dol, .9)},
        "pair_unit_cost_at_completion": {
            "n": len(pc), "p10": _q(pc, .1), "p25": _q(pc, .25), "p50": _q(pc, .5),
            "p75": _q(pc, .75), "p90": _q(pc, .9),
            "share_above_1_00": round(sum(1 for x in pc if x > 1.0) / len(pc), 4) if pc else None,
            "share_above_1_02": round(sum(1 for x in pc if x > 1.02) / len(pc), 4) if pc else None,
            "share_above_1_05": round(sum(1 for x in pc if x > 1.05) / len(pc), 4) if pc else None},
        "residual_usd_per_window": {"p50": _q(residual_usd, .5), "p90": _q(residual_usd, .9),
                                    "p99": _q(residual_usd, .99), "max": round(max(residual_usd), 2) if residual_usd else None,
                                    "share_windows_with_residual": round(sum(1 for x in residual_usd if x > 0.01) / len(residual_usd), 4) if residual_usd else None},
        "spend_usd_per_window": {"p50": _q(spend, .5), "p90": _q(spend, .9), "max": round(max(spend), 2) if spend else None},
        "residual_over_spend_p50": (round(_q(residual_usd, .5) / _q(spend, .5), 4)
                                    if spend and _q(spend, .5) else None),
        "price_band_share": {"fills_with_price_in_0_35_0_65": round(price_in_band / len(rows), 4) if rows else None},
        "labels": "DERIVED from L5's executed fills on the public feed; a fill is not a quote",
    }


def study_numbers() -> dict:
    """Numbers already established by the frozen studies (read-only)."""
    out: dict = {}
    def load(name):
        try:
            return json.loads((A / name).read_text())
        except Exception:                                  # noqa: BLE001
            return {}
    pat = load("l5_patience_study.json")
    l5 = (pat.get("l5") or {}).get("by_lane") or {}
    lane = l5.get("BTC-15m") or next(iter(l5.values()), {}) if l5 else {}
    out["reprice_cadence_s"] = {
        "l5_multi_print_cluster_duration_s": (lane.get("multi_print_cluster_duration_s") or {}),
        "l5_clusters_longer_than_our_deadline_share": lane.get("multi_print_clusters_longer_than_our_deadline_share"),
        "ours": pat.get("ours"), "source": "analysis/l5/l5_patience_study.json"}
    conc = (load("l5_concurrency_switching.json").get("overall") or {}).get("concurrency") or {}
    cap = conc.get("capital") or {}
    out["simultaneous_windows"] = {
        "median_time_weighted": cap.get("median_simultaneous_time_weighted"),
        "mean_time_weighted": cap.get("mean_simultaneous_time_weighted"),
        "share_at_2_or_more": cap.get("share_at_2_or_more"),
        "share_at_3_or_more": cap.get("share_at_3_or_more"),
        "max_simultaneous": cap.get("max_simultaneous"),
        "source": "analysis/l5/l5_concurrency_switching.json"}
    co = load("l5_cap_override_study.json").get("overall") or {}
    out["extend_vs_cap"] = {
        "extend_unmatched_after_in_action_units": co.get("extend_unmatched_after_in_action_units"),
        "extends_refused_by_cap_in_action_units": co.get("extends_refused_by_cap_in_action_units"),
        "action_unit_usd": co.get("action_unit_usd"),
        "source": "analysis/l5/l5_cap_override_study.json"}
    ext = (load("l5_extension_study.json").get("part_a_extend_episodes") or {}).get("overall") or {}
    out["extend_episodes"] = {k: ext.get(k) for k in (
        "episodes", "p_complement_before_end", "latency_to_first_complement_s",
        "pair_cost_when_complemented_median")}
    out["extend_episodes"]["source"] = "analysis/l5/l5_extension_study.json"
    ec = load("execution_constraints.json")
    out["execution_constraints_classification"] = {
        k: {"class": v.get("class"), "where": v.get("where")}
        for k, v in (ec.get("constraints") or {}).items()}
    return out


def live_bindings(log_dirs: list) -> dict:
    """How often each restriction bound in our live decisions, per shell,
    from the persisted session_end rows (deduplicated by decision_id)."""
    seen: set = set()
    refusals: collections.Counter = collections.Counter()
    l5_vs_acted: collections.Counter = collections.Counter()
    would_vs_acted: collections.Counter = collections.Counter()
    complete_cf: list = []
    orders: collections.Counter = collections.Counter()
    decisions = 0
    for d in log_dirs:
        for f in sorted(glob.glob(str(Path(d) / "*"))):
            try:
                doc = json.loads(Path(f).read_text())
            except Exception:                          # noqa: BLE001
                continue
            rows = doc.get("deploy", []) if isinstance(doc, dict) else (doc if isinstance(doc, list) else [])
            for r in rows:
                m = r.get("message", "") if isinstance(r, dict) else ""
                if "session_end" not in m:
                    continue
                try:
                    j = json.loads(m[m.find("{"):])
                except Exception:                      # noqa: BLE001
                    continue
                if j.get("kind") != "session_end":
                    continue
                shell = str((j.get("spring_gates") or {}).get("safety_shell_version") or "pre-R2").split("/")[0]
                for dd in j.get("decisions") or []:
                    key = dd.get("decision_id")
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    decisions += 1
                    rec, act = dd.get("v2_recommended"), dd.get("acted")
                    l5_vs_acted[(shell, str(rec), str(act))] += 1
                    if dd.get("refusal"):
                        refusals[(shell, str(dd["refusal"]).split(":")[0])] += 1
                    w = dd.get("would_act_without_safety_cap")
                    if w is not None and w != act:
                        would_vs_acted[(shell, str(w), str(act))] += 1
                    c = dd.get("counterfactual_l5_price")
                    if c:
                        try:
                            complete_cf.append({
                                "shell": shell, "pair_at_touch": float(c.get("pair_at_touch")),
                                "within_rung_bound": c.get("within_rung_bound"),
                                "class": c.get("class")})
                        except (TypeError, ValueError):
                            pass
                for o in j.get("orders") or []:
                    stage = str(o.get("stage") or "")
                    rel = stage.split("_")[1] if "_" in stage else stage
                    orders[(shell, rel, str(o.get("outcome")))] += 1
    def fmt(c):
        return [{"key": list(k), "n": n} for k, n in sorted(c.items(), key=lambda kv: -kv[1])]
    above = [c for c in complete_cf if c["pair_at_touch"] > 1.0]
    return {
        "decisions": decisions, "log_dirs": [str(d) for d in log_dirs],
        "refusals": fmt(refusals),
        "l5_recommended_vs_acted": fmt(l5_vs_acted),
        "would_act_without_safety_cap_vs_acted": fmt(would_vs_acted),
        "complete_counterfactuals": {
            "n": len(complete_cf),
            "l5_touch_pair_above_1_00": len(above),
            "l5_touch_pair_above_1_00_share": round(len(above) / len(complete_cf), 4) if complete_cf else None,
            "pair_at_touch_p50": _q([c["pair_at_touch"] for c in complete_cf], .5),
            "pair_at_touch_p90": _q([c["pair_at_touch"] for c in complete_cf], .9)},
        "orders": fmt(orders),
        "labels": "deduplicated by decision_id across every saved session_end row; shells as persisted",
    }


def shell(bankroll_usd: float, per_market_cap: float = 7.50, daily_stop: float = 15.0,
          concurrency: int = 2, preflight_multiple: float = 3.0) -> dict:
    """The catastrophic-loss shell in dollars of THIS account. Worst case =
    every simultaneously worked market losing its whole per-market cap;
    the daily stop bounds the day; the preflight multiple is the deployed
    cash the boot check demands (R4 used 3x worst case)."""
    worst = per_market_cap * concurrency
    return {"bankroll_usd": bankroll_usd,
            "per_market_cap_usd": per_market_cap,
            "concurrency": concurrency,
            "worst_case_simultaneous_usd": round(worst, 2),
            "worst_case_share_of_bankroll": round(worst / bankroll_usd, 4),
            "daily_loss_stop_usd": daily_stop,
            "daily_stop_share_of_bankroll": round(daily_stop / bankroll_usd, 4),
            "preflight_deployed_required_usd": round(worst * preflight_multiple, 2),
            "preflight_ok": worst * preflight_multiple <= bankroll_usd,
            "labels": "hard shell; nothing inside it is a strategy rule"}


def removals(l5: Mapping[str, Any], studies: Mapping[str, Any], live: Mapping[str, Any],
             sh: Mapping[str, Any]) -> list:
    """Per restriction: what L5 does, how often it bound, the removal and its
    worst case under the shell. Recommendations are ranked by how often the
    restriction blocked an L5-consistent action in live decisions."""
    ref = {tuple(x["key"]): x["n"] for x in live["refusals"]}
    would = {tuple(x["key"]): x["n"] for x in live["would_act_without_safety_cap_vs_acted"]}
    r4_ext_held = would.get(("R4", "extend", "hold"), 0)
    r4_comp_held = would.get(("R4", "complete", "hold"), 0)
    cap = sh["per_market_cap_usd"]
    pc = l5["pair_unit_cost_at_completion"]
    out = []
    for r in RESTRICTIONS:
        item = dict(r)
        rid = r["id"]
        if rid == "unmatched_usd_cap":
            item.update({
                "l5_does": f"residual per window p50 ${l5['residual_usd_per_window']['p50']}, p90 ${l5['residual_usd_per_window']['p90']} on spend p50 ${l5['spend_usd_per_window']['p50']} (residual/spend p50 {l5['residual_over_spend_p50']}); {studies['extend_vs_cap']['extends_refused_by_cap_in_action_units'].get('1.0', {}).get('share_of_extends')} of his EXTENDs would be refused by a 1-action-unit cap",
                "bound_live": {"R4_extend_held": r4_ext_held, "R4_refusals": ref.get(("R4", "unmatched_usd_cap"), 0)},
                "removal": f"remove the separate unmatched cap; the per-market cap (${cap}) is the only inventory bound, exactly as L5's residual is bounded only by what he committed",
                "worst_case_usd": cap, "worst_case_note": "already the per-market cap; no new dollars at risk",
                "recommend": "REMOVE in R5"})
        elif rid == "pair_cost_ceiling":
            item.update({
                "l5_does": f"pair unit cost at completion p50 {pc['p50']}, p75 {pc['p75']}, p90 {pc['p90']}; {pc['share_above_1_00']} of his completions above 1.00, {pc['share_above_1_02']} above 1.02, {pc['share_above_1_05']} above 1.05",
                "bound_live": {"R4_complete_held": r4_comp_held,
                               "R4_complement_not_priceable": ref.get(("R4", "complement_not_priceable"), 0),
                               "complete_counterfactuals_touch_above_1_00": live["complete_counterfactuals"]["l5_touch_pair_above_1_00"]},
                "removal": "price the complement at L5's touch (deficit-side touch join), whatever the pair; the loss on an above-par pair is (pair - 1) x matched shares and is bounded by the per-market cap",
                "worst_case_usd": round((pc["p90"] - 1.0) * (cap / 0.5), 2) if pc.get("p90") else None,
                "worst_case_note": f"DERIVED: at L5's p90 pair {pc['p90']} on the whole per-market cap the above-par cost is this many dollars; the shell already bounds it",
                "recommend": "REMOVE in R5 (record pair > 1.00 as L5-consistent, not as a deviation)"})
        elif rid == "action_size":
            item.update({
                "l5_does": f"shares per fill p25/p50/p75 {l5['shares_per_fill']['p25']}/{l5['shares_per_fill']['p50']}/{l5['shares_per_fill']['p75']}; dollars per fill p50 ${l5['dollars_per_fill']['p50']}",
                "bound_live": {"every_row": "size_divergence=venue_minimum_vs_l5_target", "R4_spend_cap_reached": ref.get(("R4", "spend_cap_reached"), 0)},
                "removal": "keep the action size where it is; size scales with the bankroll after profitability is measured (owner: profitability tells how far to scale, not how to rewrite)",
                "worst_case_usd": cap, "worst_case_note": "unchanged",
                "recommend": "KEEP (scale later)"})
        elif rid == "maker_rest_30s":
            item.update({
                "l5_does": f"multi-print cluster duration p50 {studies['reprice_cadence_s']['l5_multi_print_cluster_duration_s'].get('p50')} s, p90 {studies['reprice_cadence_s']['l5_multi_print_cluster_duration_s'].get('p90')} s; clusters longer than our 30 s deadline {studies['reprice_cadence_s']['l5_clusters_longer_than_our_deadline_share']}",
                "bound_live": {"ours": (studies["reprice_cadence_s"].get("ours") or {}).get("deadline_cancels"), "fill_share": (studies["reprice_cadence_s"].get("ours") or {}).get("fill_share")},
                "removal": "reprice at the model's own reevaluation cadence (2 s / 30 s per state) with cancel/replace; class (b): an owner execution policy, not a venue requirement",
                "worst_case_usd": 0.0, "worst_case_note": "no new dollars at risk; more cancel/replace messages",
                "recommend": "REMOVE in R5"})
        elif rid == "maker_only":
            item.update({
                "l5_does": f"taker share of known-side fills {l5['taker_share']['of_known']}; model p_taker 2-3% of actions by tte bucket",
                "bound_live": {"live_policy": "taker disabled on every row"},
                "removal": "allow taker only when the model's p_taker draw says so for that state (2-3% of actions); fee 0.06 x p(1-p) per share, bounded by the per-market cap",
                "worst_case_usd": round(0.06 * 0.25 * (cap / 0.5), 2),
                "worst_case_note": "DERIVED: max taker fee if every share of the per-market cap were taken at p=0.5",
                "recommend": "STAGE (R5b) after the fee is measured on the first taker fills"})
        elif rid == "concurrency_1":
            item.update({
                "l5_does": f"median {studies['simultaneous_windows']['median_time_weighted']} simultaneous windows, {studies['simultaneous_windows']['share_at_2_or_more']} of active time at 2+, max {studies['simultaneous_windows']['max_simultaneous']}",
                "bound_live": {"R4_open_held": would.get(("R4", "open", "hold"), 0) + ref.get(("R4", "position_held"), 0),
                               "note": "every candidate cycle while a residual was held was skipped (CAMPAIGN_HOLD:POSITION_HELD)"},
                "removal": f"concurrency {sh['concurrency']} (L5's median), worst case {sh['concurrency']} x ${cap} = ${sh['worst_case_simultaneous_usd']}",
                "worst_case_usd": sh["worst_case_simultaneous_usd"], "worst_case_note": "the shell's worst case",
                "recommend": "REMOVE to 2 in R5"})
        elif rid == "cohort_3_markets":
            item.update({
                "l5_does": "continuous; 2,216 windows over the history",
                "bound_live": {"R4": "campaign stops at 3 settled; the 6 h horizon stopped it once at 14:48Z"},
                "removal": "continuous operation under the shell: daily loss stop and the 7-day horizon are the stops; report per settlement instead of per cohort",
                "worst_case_usd": sh["daily_loss_stop_usd"], "worst_case_note": "the daily stop",
                "recommend": "REMOVE in R5 (report cadence unchanged)"})
        elif rid == "observation_band":
            item.update({
                "l5_does": f"share of L5 fills priced inside 0.35-0.65: {l5['price_band_share']['fills_with_price_in_0_35_0_65']}",
                "bound_live": {"R4_entry_band_left": ref.get(("R4", "entry_band_left"), 0)},
                "removal": "widen entry to every state the frozen model covers with evidence (the off-distribution hold still protects unknown states); size unchanged so the worst case is unchanged. This is the largest fidelity gap by share of his activity (71% of his fills are outside the band)",
                "worst_case_usd": cap, "worst_case_note": "unchanged per market",
                "recommend": "STAGE (R5b) after the band-inside sample is compared out of sample"})
        elif rid == "strict_evidence_gate":
            item.update({
                "l5_does": "trades where flow exists",
                "bound_live": {"R4_refusals": "thin_flow dominated while the feed was dead; honest refusals"},
                "removal": "none; this is the evidence requirement, not a deviation",
                "worst_case_usd": 0.0, "worst_case_note": "n/a", "recommend": "KEEP"})
        elif rid == "off_distribution_hold":
            item.update({
                "l5_does": "n/a (states without L5 evidence)",
                "bound_live": {"R4_refusals": ref.get(("R4", "off_distribution_hold"), 0)},
                "removal": "none; fidelity protection. Shrinks as the versioned lookup refreshes from his newest activity",
                "worst_case_usd": 0.0, "worst_case_note": "n/a", "recommend": "KEEP (refresh the lookup)"})
        out.append(item)
    # rank: removals first, ordered by how often they blocked an
    # L5-consistent action in live decisions; then staged; then kept
    def bound_n(x: Mapping[str, Any]) -> int:
        return sum(v for v in x["bound_live"].values() if isinstance(v, int))
    def tier(x: Mapping[str, Any]) -> int:
        rec = x["recommend"]
        return 0 if rec.startswith("REMOVE") else (1 if rec.startswith("STAGE") else 2)
    return sorted(out, key=lambda x: (tier(x), -bound_n(x), x["id"]))


def r5_rung(sh: Mapping[str, Any], rem: list) -> dict:
    return {
        "candidate_id": "R5-FIDELITY (owner writes the approval identity)",
        "shell": {"per_market_cap_usd": sh["per_market_cap_usd"], "concurrency": sh["concurrency"],
                  "worst_case_simultaneous_usd": sh["worst_case_simultaneous_usd"],
                  "daily_loss_stop_usd": sh["daily_loss_stop_usd"],
                  "preflight_deployed_required_usd": sh["preflight_deployed_required_usd"],
                  "horizon_s": 604800, "maker_only": True, "per_action_target_usd": 2.50},
        "removed": [r["id"] for r in rem if r["recommend"].startswith("REMOVE")],
        "kept": [r["id"] for r in rem if r["recommend"].startswith("KEEP")],
        "staged_r5b": [r["id"] for r in rem if r["recommend"].startswith("STAGE")],
        "deviation_recording": ("every live decision keeps would_act_without_safety_cap, divergence_class and "
                                "counterfactual_l5_price; under R5 a pair above 1.00 at L5's touch is "
                                "L5-consistent (no deviation); the per-market cap and the daily stop are the "
                                "only deviations and are labelled shell_cap"),
        "stop": "daily loss stop, the 7-day horizon, or the owner; no cohort stop; report per settlement",
        "labels": "PROPOSAL derived from the audit; not an approval; nothing mutates until the owner writes the rung id",
    }


def render_md(doc: Mapping[str, Any]) -> str:
    l5, sh, live = doc["l5"], doc["shell"], doc["live_bindings"]
    lines = ["# L5 RESTRICTION AUDIT", "",
             f"_Generated {doc['generated_utc']}. Program: {PROGRAM}_", "",
             "## The question", "",
             "What restrictions are preventing us from executing what L5 would actually do, and how far can we safely remove them with this bankroll?", "",
             "## The shell (hard catastrophic-loss bound, dollars of this account)", "",
             f"- Bankroll (venue, last read): **${sh['bankroll_usd']}**",
             f"- Per-market cap ${sh['per_market_cap_usd']} x concurrency {sh['concurrency']} = worst case **${sh['worst_case_simultaneous_usd']}** ({sh['worst_case_share_of_bankroll']:.1%} of bankroll)",
             f"- Daily loss stop **-${sh['daily_loss_stop_usd']}** ({sh['daily_stop_share_of_bankroll']:.1%})",
             f"- Preflight deployed cash required (3x worst case): ${sh['preflight_deployed_required_usd']} -> {'OK' if sh['preflight_ok'] else 'NOT OK'}", "",
             "## L5's own numbers (canonical history, DERIVED from executed fills)", "",
             f"- Fills {l5['fills']}, windows {l5['windows']}; taker share of known-side fills {l5['taker_share']['of_known']}",
             f"- Shares per fill p25/p50/p75: {l5['shares_per_fill']['p25']}/{l5['shares_per_fill']['p50']}/{l5['shares_per_fill']['p75']}; dollars per fill p50 ${l5['dollars_per_fill']['p50']}",
             f"- Pair unit cost at completion p10/p25/p50/p75/p90: {l5['pair_unit_cost_at_completion']['p10']}/{l5['pair_unit_cost_at_completion']['p25']}/{l5['pair_unit_cost_at_completion']['p50']}/{l5['pair_unit_cost_at_completion']['p75']}/{l5['pair_unit_cost_at_completion']['p90']}; above 1.00: {l5['pair_unit_cost_at_completion']['share_above_1_00']}, above 1.02: {l5['pair_unit_cost_at_completion']['share_above_1_02']}",
             f"- Residual dollars per window p50/p90/p99: ${l5['residual_usd_per_window']['p50']}/${l5['residual_usd_per_window']['p90']}/${l5['residual_usd_per_window']['p99']}; spend per window p50 ${l5['spend_usd_per_window']['p50']}",
             f"- Simultaneous windows: median {doc['studies']['simultaneous_windows']['median_time_weighted']}, share of active time at 2+: {doc['studies']['simultaneous_windows']['share_at_2_or_more']}",
             f"- Reprice cadence: multi-print cluster p50 {doc['studies']['reprice_cadence_s']['l5_multi_print_cluster_duration_s'].get('p50')} s, p90 {doc['studies']['reprice_cadence_s']['l5_multi_print_cluster_duration_s'].get('p90')} s",
             f"- Share of his fills priced inside our 0.35-0.65 band: {l5['price_band_share']['fills_with_price_in_0_35_0_65']}", "",
             "## What bound in our live decisions", "",
             f"- Decisions audited: {live['decisions']} (deduplicated by decision_id)",
             "- L5 said / we did (top rows): " + "; ".join(f"{k[0]} {k[1]}->{k[2]} x{n}" for k, n in ((tuple(x['key']), x['n']) for x in live['l5_recommended_vs_acted'][:8])),
             "- Would act without the cap / acted: " + "; ".join(f"{k[0]} {k[1]}->{k[2]} x{n}" for k, n in ((tuple(x['key']), x['n']) for x in live['would_act_without_safety_cap_vs_acted'][:6])),
             f"- COMPLETE counterfactuals: {live['complete_counterfactuals']['n']}; L5's touch pair above 1.00 in {live['complete_counterfactuals']['l5_touch_pair_above_1_00']} ({live['complete_counterfactuals']['l5_touch_pair_above_1_00_share']}); pair at touch p50 {live['complete_counterfactuals']['pair_at_touch_p50']}, p90 {live['complete_counterfactuals']['pair_at_touch_p90']}", "",
             "## Restrictions, ranked", "",
             "| # | Restriction | Class | Ours | L5 does | Bound live | Removal | Worst case under shell | R5 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(doc["removals"], 1):
        lines.append(f"| {i} | {r['name']} | {r['class']} | {r['ours']} | {r['l5_does']} | {json.dumps(r['bound_live'], default=str)} | {r['removal']} | ${r['worst_case_usd']} ({r['worst_case_note']}) | **{r['recommend']}** |")
    rr = doc["r5_rung"]
    lines += ["", "## R5 rung that follows (PROPOSAL, owner writes the id)", "",
              f"- Shell: {json.dumps(rr['shell'])}",
              f"- Removed: {', '.join(rr['removed'])}",
              f"- Kept: {', '.join(rr['kept'])}",
              f"- Staged for R5b: {', '.join(rr['staged_r5b'])}",
              f"- Deviation recording: {rr['deviation_recording']}",
              f"- Stop: {rr['stop']}", "",
              "## Labels", "",
              "- Realized dollars are never in this document; every dollar above is a cap, a bound or a DERIVED counterfactual.",
              "- Nothing here mutates the live path; the R4 shell runs unchanged until the owner writes the R5 identity.", ""]
    return "\n".join(lines)


def build(log_dirs: list, bankroll: float, concurrency: int = 2, rows: Optional[list] = None) -> dict:
    from datetime import datetime, timezone
    l5 = l5_numbers(rows=rows)
    studies = study_numbers()
    live = live_bindings(log_dirs)
    sh = shell(bankroll, concurrency=concurrency)
    rem = removals(l5, studies, live, sh)
    return {"generated_utc": datetime.now(timezone.utc).isoformat(), "program": PROGRAM,
            "l5": l5, "studies": studies, "live_bindings": live, "shell": sh,
            "removals": rem, "r5_rung": r5_rung(sh, rem)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", action="append", default=[], help="directory of saved log dumps (repeatable)")
    ap.add_argument("--bankroll", type=float, required=True, help="venue balance (USD), from the last authenticated read")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--out", default=str(A))
    args = ap.parse_args()
    doc = build(args.logs, args.bankroll, args.concurrency)
    out = Path(args.out)
    (out / "l5_restriction_audit.json").write_text(json.dumps(doc, indent=1, default=str))
    (out / "L5_RESTRICTION_AUDIT.md").write_text(render_md(doc))
    print(json.dumps({"decisions": doc["live_bindings"]["decisions"],
                      "removed": doc["r5_rung"]["removed"], "staged": doc["r5_rung"]["staged_r5b"],
                      "worst_case": doc["shell"]["worst_case_simultaneous_usd"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
