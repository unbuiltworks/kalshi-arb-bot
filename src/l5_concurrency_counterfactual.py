"""Concurrency 1 vs 2 vs 3 on the same forward tape, scored against the
PRE-REGISTERED gate (OWNER #167 comment 5586921649).

READ THE GATE FIRST, AND ONLY FROM THE GATE. This module imports
src.l5_concurrency_gate and calls assert_unmodified() before it computes
anything. It never restates a threshold inline, because a threshold copied
into the scoring code is a threshold that can drift away from the one that
was frozen.

WHAT THE FORWARD TAPE CAN AND CANNOT ANSWER, established from the code
rather than assumed:

The c=1 arm is OBSERVED -- it is what actually ran, with venue-confirmed
fills and settlement behind it.

The c=2 and c=3 arms are NOT observed, and the honest question is whether
they are RECONSTRUCTIBLE. They are not, and the reason is structural rather
than a matter of sample size. In pmus_l5_continuous_session.run_campaign,
when a market is held the loop logs `resume_start` and calls the session
runner directly; `select_candidate` is never reached. So on exactly the
cycles where a second slot would have been used, the system does not record
what was admissible -- there is no ranked universe, no admitted list, no
opportunity score. A second slot cannot be filled from evidence that was
never captured.

Inventing those candidates -- carrying forward the last ranking, or sampling
from the flat-cycle distribution -- would produce numbers that look like a
counterfactual and are actually an assumption about the very thing the gate
is meant to test. The frozen gate anticipates this: an arm that cannot be
tied to venue evidence is UNVERIFIABLE, which is a distinct verdict from
DO_NOT_RECOMMEND, because unknown is not the same as failing.

So this module measures what is real, states plainly what is missing, and
quantifies exactly how much of it is missing.

    python3 -m src.l5_concurrency_counterfactual --logs <dir> --out analysis/l5
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_allocation_metrics as am
from src import l5_concurrency_gate as gate

OWNER_REF = "#167 comment 5586921649"


# --------------------------------------------------------------------------

def arm_c1(rep: Mapping[str, Any], sel: Sequence[Mapping[str, Any]],
           skipped: Sequence[Mapping[str, Any]],
           funnels: Sequence[Mapping[str, Any]]) -> dict:
    """The observed arm. Every number here happened."""
    lock = am.capital_lockup(rep)
    otc = am.open_to_complete(rep)
    econ = am.economics(rep)
    idle = am.flat_time_with_eligible_candidate(sel, skipped)
    locked_s = sum(p["locked_s"] for p in (lock.get("per_market") or []))
    completed = otc.get("sequences_completed") or 0
    # bankroll-dollar-hours actually committed, from the spend the ledger
    # recorded against each market rather than from the nominal bankroll
    spent_hours = 0.0
    for p in (lock.get("per_market") or []):
        usd = am._f(p.get("spent_usd"))
        if usd:
            spent_hours += abs(usd) * (p["locked_s"] / 3600.0)
    return {
        "arm": "c1",
        "observed": True,
        "completed_sequences": completed,
        "sequences_started": otc.get("sequences_started"),
        "completion_probability": otc.get("completion_probability"),
        "median_open_to_complete_s": otc.get("median_open_to_complete_s"),
        "capital_locked_market_seconds": round(locked_s, 1),
        "bankroll_dollar_hours": round(spent_hours, 6),
        "completed_per_bankroll_dollar_hour": (
            round(completed / spent_hours, 4) if spent_hours > 0 else None),
        "settled_markets_venue_confirmed":
            econ["settled_markets_venue_confirmed"],
        "realized_usd_venue_total": econ["realized_usd_venue_total"],
        "idle_cycles_with_candidate_and_no_choice":
            idle.get("cycles_flat_with_candidate_and_no_choice"),
        "cycles_skipped_position_held": idle.get("cycles_skipped_position_held"),
        "venue_ledger_reconcilable": True,
        "basis": ("observed live arm; settled dollars are the settlement "
                  "auditor's venue rows only"),
    }


def missing_evidence_for_higher_arms(sel: Sequence[Mapping[str, Any]],
                                     skipped: Sequence[Mapping[str, Any]],
                                     shadow: Sequence[Mapping[str, Any]] = ()
                                     ) -> dict:
    """How much of the c>=2 counterfactual is simply not on the tape.

    A second slot would be used exactly on the cycles the first slot was
    busy. Those are the cycles with no ranked universe, so the size of this
    number IS the size of the hole."""
    hold_cycles = len(skipped)
    ranked_cycles = sum(1 for r in sel if r.get("n_admitted"))
    # OWNER GO 5588416241: a held cycle that carries a shadow ranking is no
    # longer a hole. An acted shadow row would be a contradiction in terms and
    # is refused rather than counted.
    shadow_ranked = sum(1 for r in shadow
                        if r.get("n_admitted") and not r.get("acted"))
    shadow_acted = sum(1 for r in shadow if r.get("acted"))
    hold_cycles = max(0, hold_cycles - shadow_ranked)
    total = hold_cycles + ranked_cycles + shadow_ranked
    return {
        "cycles_with_a_ranked_universe": ranked_cycles,
        "held_cycles_with_a_shadow_ranking": shadow_ranked,
        "shadow_rows_marked_acted": shadow_acted,
        "shadow_rows_marked_acted_note": (
            "must be 0; a shadow ranking that claims it acted is a "
            "contradiction and is never counted as evidence"),
        "cycles_held_with_no_ranked_universe": hold_cycles,
        "share_of_cycles_unreconstructible": (round(hold_cycles / total, 4)
                                              if total else None),
        "why": ("run_campaign logs resume_start and calls the session runner "
                "directly when a market is held; select_candidate is never "
                "reached, so no admitted list, ranking or opportunity score "
                "exists for those cycles"),
        "what_would_fix_it": (
            "rank the universe during holds too and log it WITHOUT acting on "
            "it -- a shadow selection row. That is a log-only change: no "
            "order path, no cap, no gate, no concurrency change. It makes "
            "the c>=2 arms reconstructible from real observed candidates "
            "instead of assumed ones. It needs an owner go because it "
            "touches the live campaign loop, even though it cannot trade."),
    }


def arm_unverifiable(n: int, missing: Mapping[str, Any]) -> dict:
    return {
        "arm": f"c{n}",
        "observed": False,
        "verdict": "UNVERIFIABLE",
        "why": ("the cycles a second slot would have used are precisely the "
                "cycles with no recorded candidate universe"),
        "cycles_that_cannot_be_reconstructed":
            missing["cycles_held_with_no_ranked_universe"],
        "refused_to_estimate": (
            "carrying forward a stale ranking or sampling the flat-cycle "
            "distribution would produce a number that looks like a "
            "counterfactual and is actually an assumption about the thing "
            "under test"),
    }


# --------------------------------------------------------------------------

def score_against_gate(c1: Mapping[str, Any],
                       arms: Mapping[str, Mapping[str, Any]],
                       rep: Mapping[str, Any],
                       sel: Sequence[Mapping[str, Any]]) -> dict:
    """Sample minimums come from the frozen gate; nothing is restated here."""
    mins = gate.CONCURRENCY_GATE["sample_minimums"]
    markets = {r["chosen"] for r in sel if r.get("chosen")}
    leagues = {am.category_of(s) for s in markets}
    have = {
        "completed_sequences_at_c1": c1.get("completed_sequences") or 0,
        "distinct_markets": len(markets),
        "distinct_leagues": len(leagues),
        "settled_markets_with_venue_confirmed_realized":
            c1.get("settled_markets_venue_confirmed") or 0,
        "forward_hours_of_tape": round(
            ((sel[-1]["t"] - sel[0]["t"]) / 3600.0) if len(sel) > 1 else 0.0, 2),
    }
    met = {k: (have.get(k, 0) >= v) for k, v in mins.items()
           if isinstance(v, (int, float))}
    if not all(met.values()):
        verdict = "NOT_YET_ENOUGH_FORWARD_EVIDENCE"
    elif any(a.get("verdict") == "UNVERIFIABLE" for a in arms.values()):
        verdict = "UNVERIFIABLE"
    else:                                          # pragma: no cover
        verdict = "DO_NOT_RECOMMEND"
    return {
        "gate_id": gate.CONCURRENCY_GATE["id"],
        "gate_sha256_16": gate.gate_sha16(),
        "gate_frozen_utc": gate.GATE_FROZEN_UTC,
        "sample_minimums_required": {k: v for k, v in mins.items()
                                     if isinstance(v, (int, float))},
        "sample_minimums_have": have,
        "sample_minimums_met": met,
        "verdict": verdict,
        "enacts": gate.CONCURRENCY_GATE["enacts"],
    }


def build_report(log_paths: Sequence[str]) -> dict:
    gate.assert_unmodified()          # before anything is computed
    from src.l5_sports_clock_study import build_ledger, expand_logs
    files = expand_logs(list(log_paths))
    rep = build_ledger(list(log_paths))
    sel = am.selection_timeline(files)
    skipped = am.skipped_timeline(files)
    funnels = am.funnel_timeline(files)

    c1 = arm_c1(rep, sel, skipped, funnels)
    shadow = am.shadow_selection_timeline(files)
    missing = missing_evidence_for_higher_arms(sel, skipped, shadow)
    arms = {"c1": c1,
            "c2": arm_unverifiable(2, missing),
            "c3": arm_unverifiable(3, missing)}
    return {
        "owner_ref": OWNER_REF,
        "read_only": ("scores a frozen gate; enacts nothing. Concurrency, "
                      "per-market capital and every cap are owner decisions"),
        "arms": arms,
        "missing_evidence": missing,
        "gate": score_against_gate(c1, arms, rep, sel),
        "opportunity_context": am.opportunity_scores(sel),
    }


def render_markdown(r: Mapping[str, Any]) -> str:
    g, c1, m = r["gate"], r["arms"]["c1"], r["missing_evidence"]
    L = [f"# Concurrency counterfactual — {g['verdict']}", "",
         f"_OWNER {r['owner_ref']}. {r['read_only']}._", "",
         f"Gate `{g['gate_id']}` sha `{g['gate_sha256_16']}`, frozen "
         f"{g['gate_frozen_utc']}.", "",
         "## Sample minimums", "",
         "| minimum | required | have | met |", "|---|---:|---:|---|"]
    for k, v in sorted(g["sample_minimums_required"].items()):
        L.append(f"| {k} | {v} | {g['sample_minimums_have'].get(k)} | "
                 f"{g['sample_minimums_met'].get(k)} |")
    L += ["", "## Arms", "",
          "| arm | observed | verdict |", "|---|---|---|",
          f"| c1 | yes | scored below |",
          f"| c2 | no | {r['arms']['c2']['verdict']} |",
          f"| c3 | no | {r['arms']['c3']['verdict']} |", "",
          "### c1 (observed)", "",
          f"- completed sequences: {c1['completed_sequences']} of "
          f"{c1['sequences_started']} started "
          f"(p={c1['completion_probability']})",
          f"- completed per bankroll-dollar-hour: "
          f"{c1['completed_per_bankroll_dollar_hour']}",
          f"- settled venue-confirmed: {c1['settled_markets_venue_confirmed']} "
          f"markets, {c1['realized_usd_venue_total']} USD",
          f"- capital locked: {c1['capital_locked_market_seconds']} "
          f"market-seconds", "",
          "### Why c2 and c3 are UNVERIFIABLE rather than failing", "",
          f"- cycles with a ranked universe: "
          f"{m['cycles_with_a_ranked_universe']}",
          f"- held cycles WITH a shadow ranking: "
          f"{m.get('held_cycles_with_a_shadow_ranking')}",
          f"- cycles held with NO ranked universe: "
          f"**{m['cycles_held_with_no_ranked_universe']}**",
          f"- share of cycles unreconstructible: "
          f"{m['share_of_cycles_unreconstructible']}", "",
          f"_{m['why']}_", "", f"**What would fix it:** {m['what_would_fix_it']}",
          ""]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--out", default="analysis/l5")
    a = ap.parse_args(list(argv) if argv is not None else None)
    rep = build_report(a.logs)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "l5_concurrency_counterfactual.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True, default=str)
    md = render_markdown(rep)
    with open(os.path.join(a.out, "L5_CONCURRENCY_COUNTERFACTUAL.md"), "w",
              encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    return 0


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main())
