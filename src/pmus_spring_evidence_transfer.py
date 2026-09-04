"""Fail-closed transfer: sports shadow candidate -> spring preflight.

Issue #167 comment 5540937360. The sports decision layer emits a
`preflight_candidate`; the spring probe entry guard consumes a strict
evidence payload. Those are NOT the same thing, and this module exists
so nobody has to take a string like `execution_path_if_approved` on
faith. It answers one binary question with named blockers:

    SPRING_READY_FOR_REVIEW = true | false

THE THREE STATE DIRECTORIES ARE DISTINCT — no hand-waving (item 1):

* ``/state/pmus-sports`` — the KEYLESS forward collector's tape and
  status report. Public gateway GETs only. Trade direction here is
  INFERRED (volume delta + lastTradePx vs BBO), never observed.
* ``/state/pmus-ws`` — the AUTHENTICATED WS recorder's evidence. Real
  book/trade stream; this is the source the spring entry guard was
  designed around (``entry.evidence_from_status`` reads recorder status
  rows).
* ``/state/pmus-spring`` — the probe's OWN durable ledger. Neither of
  the above. It must be a real writable mount before anything arms, so
  the ledger and cumulative-notional state survive container
  replacement.

What this module does: recompute the canonical strict spring replay
(``pmus_spring_shadow_replay``) and the canonical category bar over
SPORTS collector rows, compose them into the exact schema the spring
entry guard validates, then run the guard's own predicate. Every field
carries provenance. Because the sports source infers trade direction
rather than observing it, provenance is `inferred` and that alone is a
blocker for arming — a fresh sports candidate can reach
`SHADOW_PREFLIGHT_CANDIDATE`, never `SPRING_READY_FOR_REVIEW`, until
the SAME market is corroborated by the authenticated recorder.

Structurally incapable of arming: no credentials, no transport, no
order path, and it never calls the spring session or executor.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_status_report as st
from src import pmus_spring_shadow_replay as replay
from src import pmus_sports_l5_decision as l5d
from src import pmus_spring_l5_probe_entry as entry
from src import pmus_spring_l5_session as session

SOURCE_SPORTS = ("pmus-sports-forward-collector:/state/pmus-sports "
                 "(keyless public GET; trade direction INFERRED from "
                 "lastTradePx vs BBO, never observed)")
SOURCE_RECORDER = ("pmus-ws-evidence-recorder:/state/pmus-ws "
                   "(authenticated stream; observed trades)")
SPRING_STATE_DIR = "/state/pmus-spring"
PROVENANCE_INFERRED = "inferred"
PROVENANCE_AUTHENTICATED = "authenticated"

LAYER_REPLICATION = "REPLICATION_EVIDENCE"
LAYER_ECONOMICS = "EXECUTABLE_ECONOMICS"
LAYER_PROVENANCE = "EVIDENCE_PROVENANCE"
LAYER_OPERATIONAL = "OPERATIONAL"

LABELS = ("READ-ONLY preflight reconciliation; DISARMED; no credentials, "
          "transport, or order path; SHADOW_PREFLIGHT_CANDIDATE never "
          "implies SPRING_READY_FOR_REVIEW")


def replay_state_from_sports(obs: list) -> dict:
    """Sports collector rows -> the {trades, bbo_history} shape the
    canonical strict spring replay consumes. Direction stays whatever
    the collector could infer; unknown stays unknown."""
    rows = sorted((o for o in obs if st._ts(o) is not None),
                  key=lambda o: st._ts(o))
    trades = [{"t": t["t"], "px": str(t["px"]), "dir": t["dir_inferred"]}
              for t in st.infer_trades(rows) if t.get("t")]
    history = [{"t": st._ts(o), "bid": o.get("bid"), "ask": o.get("ask"),
                "ask_book_missing": bool(o.get("ask_book_missing", True))}
               for o in rows]
    return {"trades": trades, "bbo_history": history}


def strict_rows_from_sports(by_slug: Mapping[str, list],
                            now: float) -> dict:
    """Canonical strict spring replay per market, over the sports tape."""
    out: dict[str, dict] = {}
    for slug, obs in by_slug.items():
        out[slug] = replay.summarize_state(
            replay_state_from_sports(obs), now)
    return out


def off_distribution_of(row: Optional[Mapping[str, Any]]) -> Optional[bool]:
    """Read the persistence-v2 distribution verdict out of a decision row.
    Unknown stays None so the entry guard fails closed on it."""
    if not row:
        return None
    na = row.get("next_action") or {}
    if na.get("reason") == "v2_off_distribution":
        return True
    if na.get("decision") == "quote":
        return False
    return None


def compose_evidence(slug: str, by_slug: Mapping[str, list], now: float,
                     model_sha: Optional[str] = None,
                     off_distribution: Optional[bool] = None) -> Optional[dict]:
    """Build the spring entry guard's evidence payload for one sports
    market, preserving provenance, age, category semantics and the
    action-model SHA. Missing inputs stay missing so the guard fails
    closed rather than inventing an admission."""
    obs = by_slug.get(slug)
    if not obs:
        return None
    strict_rows = strict_rows_from_sports(by_slug, now)
    cats = replay.category_spring_evidence(strict_rows, now)
    mine = strict_rows.get(slug) or {}
    cat_key = replay.slug_category(slug)
    cat_ev = cats.get(cat_key) or {"completions_60m": 0,
                                   "distinct_markets_60m": 0,
                                   "markets": [], "bar_met": False}
    fresh = st.analyze_market(obs, now, 900.0)
    last = sorted(obs, key=lambda o: st._ts(o) or 0)[-1]
    row = {
        "slug": slug,
        "evidence_t": st._ts(last),
        "bid": last.get("bid"),
        "trades_1h": fresh.get("inferred_trades"),
        "two_sided_trades": fresh.get("two_sided_inferred_flow"),
        # the sports path has no persistence-v2 distribution check of its
        # own; the decision layer supplies it and absence stays unknown
        "off_distribution": off_distribution,
        "spring_pair_target": mine.get("spring_pair_target"),
        "spring_matched_slots": mine.get("spring_matched_slots"),
        "spring_repeated_completion": mine.get("spring_repeated_completion"),
        "spring_slot_counts_theoretical":
            mine.get("spring_slot_counts_theoretical"),
        "spring_evaluable_slots": mine.get("spring_evaluable_slots"),
        "spring_matched_fraction": mine.get("spring_matched_fraction"),
        "spring_pair_costs": mine.get("spring_pair_costs") or [],
        "spring_fee_safe_completions":
            mine.get("spring_fee_safe_completions"),
        "spring_evidence_missing": mine.get("spring_evidence_missing") or [],
        "spring_category": cat_key,
        "spring_category_evidence": cat_ev,
        "spring_category_bar_met": bool(cat_ev.get("bar_met")),
        "evidence_provenance": PROVENANCE_INFERRED,
        "evidence_source": SOURCE_SPORTS,
        "action_model_sha": model_sha,
        "labels": "SHADOW/THEORETICAL strict replay recomputed over the "
                  "keyless sports tape; NOT authenticated recorder evidence",
    }
    row["probe_evidence"] = _probe_evidence(row)
    return row


def _probe_evidence(row: Mapping[str, Any]) -> dict:
    """Advisory READY_FOR_REVIEW verdict in the recorder's own shape, so
    the entry guard can validate a sports-sourced payload unchanged."""
    import os
    floor = max(replay.CATEGORY_BAR_COMPLETIONS,
                int(os.getenv("PMUS_SPRING_PROBE_MIN_COMPLETIONS", "3")))
    reasons: list[str] = []
    trades = row.get("trades_1h")
    if trades is None or int(trades) < session.SPRING_MIN_TRADES_1H:
        reasons.append(f"thin_flow:{trades}")
    if row.get("two_sided_trades") is not True:
        reasons.append("one_directional_flow")
    reasons.extend(row.get("spring_evidence_missing") or [])
    cat = row.get("spring_category_evidence") or {}
    if not cat.get("bar_met"):
        reasons.append(
            f"category_bar_not_met:{cat.get('completions_60m', 0)}"
            f"completions/{cat.get('distinct_markets_60m', 0)}markets"
            f"<{replay.CATEGORY_BAR_COMPLETIONS}/"
            f"{replay.CATEGORY_BAR_DISTINCT_MARKETS}")
    n_cat = int(cat.get("completions_60m") or 0)
    if n_cat < floor:
        reasons.append(f"insufficient_category_completions:{n_cat}<{floor}")
    return {"status": ("READY_FOR_REVIEW" if not reasons
                       else "INSUFFICIENT_EVIDENCE"),
            "reasons": reasons, "min_completions_required": floor,
            "labels": "ADVISORY; sports-sourced, inferred provenance"}


def _gate(layer: str, name: str, blocker: Optional[str]) -> dict:
    return {"layer": layer, "gate": name, "ok": blocker is None,
            "blocker": blocker}


def spring_readiness(by_slug: Mapping[str, list], slug: str,
                     now: Optional[float] = None,
                     model_sha: Optional[str] = None,
                     durable_reason=None,
                     off_distribution: Optional[bool] = None) -> dict:
    """The binary answer, with every blocker named."""
    now = time.time() if now is None else now
    gates: list[dict] = []
    evidence = compose_evidence(slug, by_slug, now, model_sha=model_sha,
                                off_distribution=off_distribution)

    gates.append(_gate(LAYER_REPLICATION, "sports_rows_present",
                       None if evidence else f"no_rows_for_slug:{slug}"))
    if evidence:
        gates.append(_gate(
            LAYER_REPLICATION, "spring_entry_guard_predicate",
            entry.strict_evidence_reason(slug, evidence, now=now)))
        fee_safe = evidence.get("spring_fee_safe_completions")
        gates.append(_gate(
            LAYER_ECONOMICS, "fee_safe_completion_observed",
            None if (fee_safe or 0) >= 1 else
            f"no_fee_safe_completion_in_window:{fee_safe}"))
        try:
            pt_ok = evidence.get("spring_pair_target") is not None and \
                replay.D(str(evidence["spring_pair_target"])) <= \
                session.SPRING_PAIR_TARGET
        except Exception:
            pt_ok = False
        gates.append(_gate(
            LAYER_ECONOMICS, "observation_cap_within_owner_decision",
            None if pt_ok else
            f"pair_target_invalid:{evidence.get('spring_pair_target')}"))
        # PROVENANCE: inferred direction is not an authenticated fill
        # record. This gate is why a sports candidate cannot arm alone.
        gates.append(_gate(
            LAYER_PROVENANCE, "authenticated_source_required",
            None if evidence.get("evidence_provenance") ==
            PROVENANCE_AUTHENTICATED else
            (f"evidence_provenance_{PROVENANCE_INFERRED}: the spring "
             f"entry guard admits {SOURCE_RECORDER}; this payload came "
             f"from {SOURCE_SPORTS}. Corroborate the SAME slug in the "
             "authenticated recorder's spring_watch before arming.")))

    reason = (durable_reason() if durable_reason
              else entry.durable_state_reason())
    gates.append(_gate(LAYER_OPERATIONAL, "durable_spring_state", reason))

    # ARM-TIME control, deliberately NOT a readiness blocker. Readiness
    # means "an owner may now review and decide"; the dual key is what
    # the owner supplies AFTER deciding. Counting it here would make
    # SPRING_READY_FOR_REVIEW unreachable while DISARMED, which is the
    # exact state the review is supposed to happen in. The key still
    # gates actual arming inside the session and entry guard, untouched.
    arming = [] if session.armed() else [
        "isolated_dual_key: PMUS_SPRING_LIVE/SESSION_ID/ARM absent "
        "(expected while DISARMED; operator-held, supplied only at arm "
        "time after approval)"]

    blockers = [f"{g['gate']}: {g['blocker']}" for g in gates
                if not g["ok"]]
    layers = {}
    for layer in (LAYER_REPLICATION, LAYER_ECONOMICS, LAYER_PROVENANCE,
                  LAYER_OPERATIONAL):
        rows = [g for g in gates if g["layer"] == layer]
        layers[layer] = {
            "ok": bool(rows) and all(g["ok"] for g in rows),
            "blockers": [f"{g['gate']}: {g['blocker']}" for g in rows
                         if not g["ok"]],
        }
    return {
        "slug": slug,
        "SPRING_READY_FOR_REVIEW": not blockers,
        "blockers": blockers,
        "arming_prerequisites": arming,
        "layers": layers,
        "gates": gates,
        "evidence": evidence,
        "required_mount": SPRING_STATE_DIR,
        "order_entry": False, "realized_pnl": None, "labels": LABELS,
    }


def reconcile(by_slug: Mapping[str, list],
              now: Optional[float] = None) -> dict:
    """Full picture: the shadow's own preflight candidate alongside the
    spring service's binary verdict on that same market. The two are
    reported separately BY CONSTRUCTION (item 3)."""
    now = time.time() if now is None else now
    decisions = l5d.slate_decisions(by_slug, now=now)
    cand = decisions.get("preflight_candidate")
    slug = cand["slug"] if cand else None
    row = next((r for r in decisions.get("rows") or []
                if r.get("slug") == slug), None)
    readiness = (spring_readiness(by_slug, slug, now=now,
                                  model_sha=decisions.get("model_sha"),
                                  off_distribution=off_distribution_of(row))
                 if slug else None)
    return {
        "kind": "PMUS_SPRING_PREFLIGHT_RECONCILE",
        "t": now,
        "SHADOW_PREFLIGHT_CANDIDATE": cand,
        "SPRING_READY_FOR_REVIEW": bool(
            readiness and readiness["SPRING_READY_FOR_REVIEW"]),
        "spring_readiness": readiness,
        "candidates_evaluated": decisions.get("candidates_evaluated"),
        "note": ("SHADOW_PREFLIGHT_CANDIDATE means the sports shadow "
                 "found a fresh actionable market. It does NOT mean the "
                 "spring probe would accept it; only "
                 "SPRING_READY_FOR_REVIEW=true means that, and it "
                 "additionally requires authenticated provenance, a "
                 f"durable {SPRING_STATE_DIR} mount, and the "
                 "operator-held dual key."),
        "order_entry": False, "realized_pnl": None, "labels": LABELS,
    }


def load_collector_rows(root: Path, hours: float = 2.0,
                        now: Optional[float] = None) -> dict:
    """Read bbo_book rows per slug from the collector's durable tape."""
    from datetime import datetime, timezone
    now = time.time() if now is None else now
    since = datetime.fromtimestamp(now - hours * 3600, timezone.utc)
    by_slug: dict[str, list] = {}
    for r in st.load_tape(root, since):
        if r.get("kind") == "bbo_book" and r.get("slug"):
            by_slug.setdefault(r["slug"], []).append(r)
    return by_slug


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collector-root", default="/state/pmus-sports",
                    help="durable root of the keyless sports collector")
    ap.add_argument("--slug", default="",
                    help="market to test; default = freshest shadow "
                         "preflight candidate")
    ap.add_argument("--hours", type=float, default=2.0)
    args = ap.parse_args()
    by_slug = load_collector_rows(Path(args.collector_root), args.hours)
    if args.slug:
        out = spring_readiness(by_slug, args.slug)
        out = {"kind": "PMUS_SPRING_PREFLIGHT_RECONCILE",
               "SHADOW_PREFLIGHT_CANDIDATE": None,
               "SPRING_READY_FOR_REVIEW": out["SPRING_READY_FOR_REVIEW"],
               "spring_readiness": out, "labels": LABELS}
    else:
        out = reconcile(by_slug)
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
