"""Deterministic READ-ONLY first-probe readiness report (issue #167
comments 5532064571, 5533476555, 5533650823).

The report separates the question "does the pattern replicate?" from the
question "is it executable at live economics?" so neither can be
mistaken for the other:

REPLICATION_EVIDENCE — has the strict spring pattern been observed
  repeating, under the OWNER-DECIDED category bar?
  - observation regime is 35-65c open / pair <= 0.98 (evidence
    collection only, OWNER DECISION 5533476555);
  - the category bar needs >=2 strict completions within a rolling 60
    minutes from >=2 DISTINCT same-category markets — an isolated
    one-off market can never satisfy it alone;
  - the advisory review floor (``PMUS_SPRING_PROBE_MIN_COMPLETIONS``,
    default 3) applies to CATEGORY completions; the entry guard clamps
    its requirement to ``max(2, floor)`` so no environment setting can
    push the effective bar below the category bar;
  - evidence must be fresh (<=120s default) and for the exact slug.

EXECUTABLE_ECONOMICS — would the observed completions have cleared the
  LIVE execution bound, which is unchanged by the widened observation
  regime? Live complements always price through
  ``min(SPRING_PAIR_TARGET - cost, q1.complement_bound)`` — the hard
  >=1c-after-known-fees bound participates in every live pricing
  decision, so a <=0.98 observation can never admit a <1c-net pair.
  This layer checks the hard bound is present and that at least one
  observed completion was consistent with it (not only with the wider
  observation cap).

OPERATIONAL — durable state mount, env assertions, and the isolated
  dual key (operator-held, supplied only at arm time).

This module consumes a saved recorder status JSON plus the local
environment. It cannot arm, set credentials, submit or cancel orders,
or mutate Railway — it only names each gate, its layer, and its exact
blocker.

    python3 -m src.pmus_spring_probe_readiness \
        --status-json status.json --slug <market-slug>
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_spring_l5_probe_entry as entry
from src import pmus_spring_l5_session as session

LAYER_REPLICATION = "REPLICATION_EVIDENCE"
LAYER_ECONOMICS = "EXECUTABLE_ECONOMICS"
LAYER_OPERATIONAL = "OPERATIONAL"
# Provenance was reported by the transfer tool but NOT by this readiness
# report, so a payload with inferred or absent provenance could read
# ready:true here. It gets its own layer, like every other bar.
LAYER_PROVENANCE = "EVIDENCE_PROVENANCE"
# Advisory only since OWNER 5551236878: completion history ranks a
# candidate, it does not gate an OPEN.
LAYER_COMPLETION = "COMPLETION_EVIDENCE"


def _gate(layer: str, name: str, blocker: Optional[str],
          advisory: bool = False) -> dict:
    """`advisory` gates are MEASURED and shown but never withhold ready.

    Completion evidence is the only advisory gate: OWNER 5551236878 took
    it out of OPEN admission, so it must not silently return here as a
    readiness blocker -- but dropping the measurement entirely would be
    the opposite error, since it still ranks one candidate against
    another.
    """
    return {"layer": layer, "gate": name, "ok": blocker is None,
            "blocker": blocker, "advisory": advisory}


def _executable_economics_gates(evidence: Optional[Mapping[str, Any]]) \
        -> list[dict]:
    gates: list[dict] = []
    # structural: the live hard bound function must exist and the
    # session's observation cap must not exceed the owner-decided 0.98
    hard_ok = callable(getattr(session.q1, "complement_bound", None))
    gates.append(_gate(
        LAYER_ECONOMICS, "live_hard_bound_present",
        None if hard_ok else
        "q1.complement_bound missing; live >=1c-after-fees bound absent"))
    from decimal import Decimal as D
    gates.append(_gate(
        LAYER_ECONOMICS, "observation_cap_not_widened_past_owner_decision",
        None if session.SPRING_PAIR_TARGET <= D("0.98") else
        f"SPRING_PAIR_TARGET {session.SPRING_PAIR_TARGET} > 0.98"))
    if evidence is not None:
        # at least one observed completion consistent with the LIVE
        # fee-safe bound, not merely the wider observation cap
        try:
            fee_safe = int(evidence.get("spring_fee_safe_completions"))
        except (TypeError, ValueError):
            fee_safe = None
        gates.append(_gate(
            LAYER_ECONOMICS, "fee_safe_completion_observed",
            None if (fee_safe or 0) >= 1 else
            (f"spring_fee_safe_completions:{fee_safe} — pattern replicates "
             "only at observation economics; no observed completion "
             "cleared the live >=1c-after-known-fees bound"
             if fee_safe is not None else
             "spring_fee_safe_completions missing from evidence")))
    return gates


def readiness_report(status: Mapping[str, Any], slug: str,
                     now: Optional[float] = None) -> dict:
    """Every gate between here and a first bounded live probe, with the
    layer and exact blocker per gate. Purely observational."""
    now = time.time() if now is None else now
    gates: list[dict] = []

    evidence = entry.evidence_from_status(status, slug)
    gates.append(_gate(LAYER_REPLICATION, "evidence_row_in_status",
                       None if evidence else
                       f"slug_not_in_status_rows:{slug}"))
    if evidence:
        # the composed evidence bar (entry-guard semantics: category bar,
        # max(2, floor) clamp on category completions, freshness)
        # provenance is this report's own layer below; asking the
        # replication gate to judge it too would show one problem twice
        gates.append(_gate(LAYER_REPLICATION, "strict_evidence_composed",
                           entry.strict_evidence_reason(
                               slug, evidence, now=now,
                               require_provenance=False)))
        # Completion evidence: measured and shown, ADVISORY. It ranks a
        # candidate; since OWNER 5551236878 it no longer gates an OPEN.
        gates.append(_gate(LAYER_COMPLETION, "strict_completion_evidence",
                           entry.completion_evidence_reason(evidence),
                           advisory=True))

    gates.extend(_executable_economics_gates(evidence))

    gates.append(_gate(LAYER_OPERATIONAL, "durable_state",
                       entry.durable_state_reason()))
    gates.append(_gate(
        LAYER_REPLICATION, "gate_evidence_env_asserted",
        None if session.gate_asserted(slug) else
        f"{session.SPRING_GATE_ENV} not set to slug"))
    # Advisory since OWNER 5551236878: this env var asserts REPEATED
    # COMPLETION, which is completion evidence and no longer an OPEN
    # prerequisite. Still surfaced so an operator can see whether they
    # asserted it.
    gates.append(_gate(
        LAYER_COMPLETION, "strict_completion_env_asserted",
        None if session.strict_completion_asserted(slug) else
        f"{session.SPRING_STRICT_ENV} not set to slug", advisory=True))
    # Resolve evidence exactly as the guard will, so the report cannot
    # say "ready" about a path the guard would not actually take.
    ev_used, ev_source = entry.resolve_strict_evidence(slug, now=now)
    gates.append(_gate(
        LAYER_REPLICATION, "strict_evidence_available",
        None if ev_used is not None else
        f"no strict evidence resolved (source: {ev_source})"))
    if ev_used is not None:
        gates.append(_gate(LAYER_REPLICATION, "strict_evidence_valid",
                           entry.strict_evidence_reason(
                               slug, ev_used, now=now,
                               require_provenance=False)))
        prov = str(ev_used.get("evidence_provenance") or "").strip().lower()
        gates.append(_gate(
            LAYER_PROVENANCE, "evidence_authenticated",
            None if prov == entry.PROVENANCE_AUTHENTICATED else
            f"evidence_provenance={prov or 'missing'}; only trade "
            "direction OBSERVED on the authenticated stream admits"))
    gates.append(_gate(
        LAYER_OPERATIONAL, "isolated_dual_key_armed",
        None if session.armed() else
        "PMUS_SPRING_LIVE/SESSION_ID/ARM dual key absent (expected while "
        "DISARMED; the operator supplies it only at arm time)"))

    def layer_view(layer: str) -> dict:
        rows = [g for g in gates if g["layer"] == layer]
        blockers = [f"{g['gate']}: {g['blocker']}" for g in rows
                    if not g["ok"]]
        return {"ok": not blockers, "blockers": blockers}

    layers = {
        LAYER_REPLICATION: layer_view(LAYER_REPLICATION),
        LAYER_ECONOMICS: layer_view(LAYER_ECONOMICS),
        LAYER_OPERATIONAL: layer_view(LAYER_OPERATIONAL),
        LAYER_PROVENANCE: layer_view(LAYER_PROVENANCE),
        LAYER_COMPLETION: layer_view(LAYER_COMPLETION),
    }
    blockers = [g for g in gates if not g["ok"] and not g.get("advisory")]
    non_code = [g["blocker"] for g in blockers
                if g["gate"] == "durable_state"]
    return {
        "slug": slug,
        "evidence_source": ev_source,
        "evidence_age_s": entry.evidence_age_s(ev_used, now),
        "ready": not blockers,
        "layers": layers,
        "gates": gates,
        "non_code_blockers": non_code,
        "binding_bar": "category bar (>=2 strict completions / >=2 "
                       "distinct same-category markets / 60m) AND "
                       "category completions >= max(2, review floor; "
                       "default 3) AND READY_FOR_REVIEW AND fresh "
                       "evidence — replication alone never arms: "
                       "executable economics and operational layers "
                       "must independently clear",
        "labels": "READ-ONLY readiness report; cannot arm, set "
                  "credentials, place/cancel orders, or mutate Railway; "
                  "all evidence is SHADOW/THEORETICAL",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status-json", required=True,
                    help="file holding one recorder status JSON (a raw "
                         "PMUS_WS_STATUS line is accepted)")
    ap.add_argument("--slug", required=True)
    args = ap.parse_args()
    raw = Path(args.status_json).read_text().strip()
    if raw.startswith("PMUS_WS_STATUS "):
        raw = raw.split(" ", 1)[1]
    status = json.loads(raw)
    report = readiness_report(status, args.slug)
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
