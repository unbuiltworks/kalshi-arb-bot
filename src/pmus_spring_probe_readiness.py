"""Deterministic READ-ONLY first-probe readiness report (issue #167
comment 5532064571).

TWO EVIDENCE LAYERS, reconciled so the first live probe can never be
armed from an ambiguous interpretation:

- canonical ``spring_repeated_completion`` (>=2 strict <=0.96
  completions inside the rolling replay window) is the CANDIDATE label:
  necessary, never sufficient.
- the advisory ``probe_evidence`` floor
  (``PMUS_SPRING_PROBE_MIN_COMPLETIONS``, default 3) is the REVIEW bar;
  the deployment entry guard enforces it via ``READY_FOR_REVIEW`` and
  ADDITIONALLY clamps its own completion requirement to
  ``max(2, floor)`` — so no environment setting can push the effective
  arm bar below 2 strict completions, and the default bar is 3.
- The BINDING pre-arm bar is therefore the STRICTER of the layers:
  ``READY_FOR_REVIEW`` at the configured floor AND
  ``spring_matched_slots >= max(2, floor)`` AND
  ``spring_repeated_completion is True`` — plus evidence freshness,
  band/flow/two-sided/on-distribution, durable state, and the full
  isolated dual-key + assertion env set. "Two strict completions" alone
  NEVER arms anything.

This module consumes a saved recorder status JSON plus the local
environment. It cannot arm, set credentials, submit or cancel orders,
or mutate Railway — it only names each gate and its exact blocker.

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


def _gate(name: str, blocker: Optional[str]) -> dict:
    return {"gate": name, "ok": blocker is None, "blocker": blocker}


def readiness_report(status: Mapping[str, Any], slug: str,
                     now: Optional[float] = None) -> dict:
    """Every gate between here and a first bounded live probe, with the
    exact blocker per gate. Purely observational."""
    now = time.time() if now is None else now
    gates: list[dict] = []

    evidence = entry.evidence_from_status(status, slug)
    gates.append(_gate("evidence_row_in_status",
                       None if evidence else
                       f"slug_not_in_status_rows:{slug}"))
    if evidence:
        # the composed two-layer evidence bar (entry-guard semantics,
        # including the max(2, floor) clamp and freshness)
        gates.append(_gate("strict_evidence_composed",
                           entry.strict_evidence_reason(slug, evidence,
                                                        now=now)))

    gates.append(_gate("durable_state", entry.durable_state_reason()))

    gates.append(_gate(
        "gate_evidence_env_asserted",
        None if session.gate_asserted(slug) else
        f"{session.SPRING_GATE_ENV} not set to slug"))
    gates.append(_gate(
        "strict_completion_env_asserted",
        None if session.strict_completion_asserted(slug) else
        f"{session.SPRING_STRICT_ENV} not set to slug"))
    ev_json = entry.parse_strict_evidence()
    gates.append(_gate(
        "strict_evidence_json_env",
        None if ev_json is not None else
        f"{entry.STRICT_EVIDENCE_ENV} absent or invalid JSON"))
    if ev_json is not None:
        gates.append(_gate("strict_evidence_json_valid",
                           entry.strict_evidence_reason(slug, ev_json,
                                                        now=now)))
    gates.append(_gate(
        "isolated_dual_key_armed",
        None if session.armed() else
        "PMUS_SPRING_LIVE/SESSION_ID/ARM dual key absent (expected while "
        "DISARMED; the operator supplies it only at arm time)"))

    blockers = [g for g in gates if not g["ok"]]
    non_code = [g["blocker"] for g in blockers
                if g["gate"] == "durable_state"]
    return {
        "slug": slug,
        "ready": not blockers,
        "gates": gates,
        "non_code_blockers": non_code,
        "binding_bar": "READY_FOR_REVIEW at the configured floor AND "
                       ">=max(2, floor) strict <=0.96 completions AND "
                       "spring_repeated_completion true; floor default 3, "
                       "never effectively below 2",
        "labels": "READ-ONLY readiness report; cannot arm, set "
                  "credentials, place/cancel orders, or mutate Railway",
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
