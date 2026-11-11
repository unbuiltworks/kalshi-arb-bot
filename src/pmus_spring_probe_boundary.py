"""The submission boundary for spring probe #1, in one command.

Answers exactly what an owner needs before arming, and nothing else:
which candidate, what the canonical L5 action model would do FIRST at
that state, what the worst case costs, how old the evidence is, and what
is still blocking.

    python3 -m src.pmus_spring_probe_boundary \
        --collector-root /state/pmus-sports \
        [--recorder-status /state/pmus-ws/status/pmus_ws_status.jsonl]

READ ONLY and DISARMED. No venue call, no order path, no arming, no
mutation. `realized_pnl` is null because nothing was submitted.

Two sources, two different jobs, deliberately not interchangeable:

* the COLLECTOR nominates the candidate -- it watches the whole slate,
  but it is keyless, so its trade direction is INFERRED from lastTradePx
  against the BBO and it can never carry authenticated provenance;
* the RECORDER authenticates it -- it observes direction on the
  authenticated stream, which is the only provenance admission accepts.

So a collector candidate alone can never admit a probe, however strong
it looks. That is not a defect to route around; it is the provenance
gate doing its job, and this report states which half is missing.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_coverage_report as cov
from src import pmus_l5_persistence_v2 as pv2
from src import pmus_micro_executor as mx
from src import pmus_sports_l5_decision as l5d
from src import pmus_spring_evidence_transfer as xfer
from src import pmus_spring_l5_probe_entry as entry
from src import pmus_spring_l5_session as session
from src import pmus_status_report as st


def _by_slug(rows: list) -> dict:
    out: dict[str, list] = {}
    for r in rows:
        if r.get("kind") == "bbo_book" and r.get("slug"):
            out.setdefault(r["slug"], []).append(r)
    for v in out.values():
        v.sort(key=lambda o: st._ts(o) or 0.0)
    return out


def _event_of(rows: list, slug: str) -> Optional[str]:
    for d in rows:
        if d.get("kind") != "discovery":
            continue
        for t in d.get("targets") or []:
            if str(t.get("slug") or "") == slug and t.get("event_slug"):
                return str(t["event_slug"])
    return None


def nominate(rows: list, now: float) -> dict:
    """The collector's current candidate, through the canonical decision
    layer -- not a fresh ranking invented here."""
    dec = l5d.slate_decisions(_by_slug(rows), now=now)
    ranked = list(dec.get("rows") or [])
    live = [r for r in ranked if r.get("executable_now")]
    chosen = (live or ranked or [None])[0]
    return {
        "candidates_evaluated": dec.get("candidates_evaluated"),
        "executable_now": len(live),
        "candidate": chosen,
        "model_sha": dec.get("model_sha"),
        "runners_up": [
            {"slug": r.get("slug"), "status": r.get("fresh_15m_status"),
             "executable_now": r.get("executable_now"),
             "stale_reason": r.get("stale_reason")}
            for r in ranked[1:4]],
    }


def first_action_risk(candidate: Mapping[str, Any],
                      min_trade_qty: D) -> dict:
    """What the FIRST action costs and what it can lose.

    The probe's first action is `probe_entry_action` -- the action from a
    FLAT book. `next_action` continues the replayed inventory and is not
    what a first probe would place.
    """
    act = (candidate.get("probe_entry_action")
           or candidate.get("next_action") or {})
    px = act.get("target_price")
    if px is None:
        return {"action": act, "sizing": None,
                "reason": "no_priced_first_action"}
    p = D(str(px))
    try:
        qty = mx.choose_micro_quantity(p, min_trade_qty,
                                       mx.DEFAULT_TARGET_NOTIONAL,
                                       mx.DEFAULT_MAX_LEG_NOTIONAL)
    except mx.Halt as exc:
        return {"action": act, "sizing": None, "reason": f"sizing_halt:{exc}"}
    cost = (p * qty).quantize(D("0.000001"))
    # worst case on ONE action is the action itself settling to zero; the
    # session cap bounds the event, and it is the number that matters
    return {
        "action": act,
        "sizing": {"price": str(p), "quantity": str(qty),
                   "notional": str(cost),
                   "minimum_trade_qty": str(min_trade_qty)},
        "max_loss_this_action": str(cost),
        "max_loss_this_event": str(session.SESSION_NOTIONAL_CAP),
        "caps": {
            "session_notional_cap": str(session.SESSION_NOTIONAL_CAP),
            "max_actions": session.MAX_ACTIONS_ENV + "=6 (image default)",
            "maker_only": True,
            "one_outstanding_order": True,
            "auto_restart": False,
            "pair_observation_cap": str(session.SPRING_PAIR_TARGET),
            "hard_bound_after_known_fees": ">=1c, always in the min()",
            "rebates_counted_toward_bound": False,
        },
    }


def evidence_state(slug: str, rows: list, now: float,
                   recorder_status: Optional[Path]) -> dict:
    """Both halves of the evidence, each labelled with its provenance and
    judged by the SAME admission guard."""
    out: dict[str, Any] = {}
    ev_c = xfer.compose_evidence(slug, _by_slug(rows), now, None)
    out["collector"] = {
        "provenance": (ev_c or {}).get("evidence_provenance"),
        "age_s": entry.evidence_age_s(ev_c, now),
        "admission_reason": entry.strict_evidence_reason(slug, ev_c, now=now),
        "admission_reason_ignoring_provenance":
            entry.strict_evidence_reason(slug, ev_c, now=now,
                                         require_provenance=False),
    }
    if recorder_status is None:
        out["recorder"] = {"present": False,
                           "blocker": "no recorder status document supplied"}
        return out
    ev_r, source = entry.evidence_from_status_path(slug, str(recorder_status),
                                                   now=now)
    out["recorder"] = {
        "present": ev_r is not None,
        "source": source,
        "provenance": (ev_r or {}).get("evidence_provenance"),
        "age_s": entry.evidence_age_s(ev_r, now),
        "admission_reason": entry.strict_evidence_reason(slug, ev_r, now=now),
    }
    return out


def blockers(nom: Mapping[str, Any], ev: Mapping[str, Any]) -> list:
    out = []
    cand = nom.get("candidate")
    if not cand:
        out.append("no_candidate_on_tape")
        return out
    if not cand.get("executable_now"):
        # `stale_reason` covers only staleness. When the model itself
        # declined -- an off-distribution state, an unpriceable side --
        # stale_reason is null and the blocker read "unstated", which is
        # the one thing a blocker must never be.
        act = (cand.get("probe_entry_action")
               or cand.get("next_action") or {})
        why = (cand.get("stale_reason")
               or act.get("reason")
               or (f"model_{act['decision']}" if act.get("decision")
                   else None)
               or "no reason recorded by the decision layer")
        out.append(f"candidate_not_executable_now:{why}")
    rec = ev.get("recorder") or {}
    if not rec.get("present"):
        out.append("authenticated_recorder_evidence_absent: the collector "
                   "nominates, only the recorder can authenticate")
    elif rec.get("admission_reason"):
        out.append(f"recorder_evidence_refused:{rec['admission_reason']}")
    dur = entry.durable_state_reason()
    if dur:
        out.append(f"durable_state:{dur}")
    if not session.armed():
        out.append("spring_dual_key_absent (expected: arming is "
                   "operator-held and never happens from here)")
    return out


def run(collector_root: Path, recorder_status: Optional[Path] = None,
        now: Optional[float] = None,
        min_trade_qty: D = D("0.01"),
        min_qty_source: str = "declared_assumption",
        hours: float = 2.0) -> dict:
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    rows = cov.load_rows(collector_root, hours, now)
    nom = nominate(rows, now)
    cand = nom.get("candidate") or {}
    slug = str(cand.get("slug") or "")
    ev = evidence_state(slug, rows, now, recorder_status) if slug else {}
    risk = first_action_risk(cand, min_trade_qty) if slug else {}
    return {
        "kind": "PMUS_SPRING_PROBE_BOUNDARY",
        "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "armed": session.armed(),
        "candidate": {
            "slug": slug or None,
            "event_slug": _event_of(rows, slug) if slug else None,
            "fresh_15m_status": cand.get("fresh_15m_status"),
            "executable_now": cand.get("executable_now"),
            "last_observation_age_s": cand.get("last_observation_age_s"),
            "stale_reason": cand.get("stale_reason"),
        },
        "selection": {k: v for k, v in nom.items() if k != "candidate"},
        "first_action": risk,
        "evidence": ev,
        "sizing_source": min_qty_source,
        "blockers": blockers(nom, ev),
        "realized_pnl": None,
        "labels": ("SHADOW/THEORETICAL; DISARMED; no venue call, no order "
                   "path; realized stays null until authoritative "
                   "settlement; rebates never counted toward the >=1c bound"),
    }


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collector-root", required=True, type=Path)
    ap.add_argument("--recorder-status", type=Path, default=None)
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--min-trade-qty", default=None)
    args = ap.parse_args(argv)
    minq = (D(str(args.min_trade_qty)) if args.min_trade_qty else D("0.01"))
    src = "operator_supplied" if args.min_trade_qty else "declared_assumption"
    print(json.dumps(run(args.collector_root, args.recorder_status,
                         None, minq, src, args.hours),
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
