"""OFFLINE P0.3 verification. Not importable by the live engine.

WHY IT LIVES HERE. This file is under analysis/, not src/. The probe image
COPYs named src/ modules one by one, so nothing in analysis/ can reach the
deployed container even by accident. That is deliberate: a verification
harness must never be able to influence the thing it verifies.

WHY THE DEFINITION NEEDED FIXING. My first pass flagged four live rows as
suspect -- atc-lg1-lmf-rcl decisions 0-3, executable residual 0 and
behavioural residual None -- and it was the CHECKER that was wrong. Before the
first fill there is no behavioural quantity to report, so None is the correct
answer and `flat` correctly agrees with an executable residual of zero. A
harness that cries wolf on correct behaviour is worse than no harness, because
the next real signal gets discounted.

THE FOUR RULES, as the owner set them:

  1. executable residual == 0 AND behavioural None, before the first fill
     -> VALID.
  2. behavioural None or zero WITH MATERIAL executable residual
     -> INVALID. This is the false-flat class: the book is long or short and
     the model is told it is flat.
  3. sign mismatch with material executable residual
     -> INVALID. The fill-size adaptation inverted the direction.
  4. a `flat` state bucket with material executable residual
     -> INVALID, for the same reason as 2 but caught at the bucket rather than
     the quantity.

MATERIAL is |executable residual| > BEHAVIORAL_FLAT_QTY_TOL (1e-4), the same
tolerance the engine's own adapter uses, so the harness and the thing it
checks agree on what "flat" means.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

TOL = D("0.0001")          # == src.pmus_spring_l5_session.BEHAVIORAL_FLAT_QTY_TOL


def _dec(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:                      # noqa: BLE001
        return None


def is_material(executable_residual: Any) -> bool:
    d = _dec(executable_residual)
    return d is not None and abs(d) > TOL


def check_decision(row: Mapping[str, Any]) -> dict:
    """One decision -> {ok, violations[], material}. Violations are NAMED, so a
    failure says which of the four rules broke rather than just 'mismatch'."""
    ex = _dec(row.get("executable_residual"))
    bh = row.get("behavioral_residual")
    state = str(row.get("state") or "")
    bucket_is_flat = state.endswith("|flat") or state == "flat"
    v: list[str] = []

    if ex is None:
        return {"ok": False, "material": None,
                "violations": ["executable_residual_unreadable"]}

    if not is_material(ex):
        # RULE 1. Non-material residual: None and 0.0 are both correct.
        if bh is not None and abs(float(bh)) > 0.0:
            # the inverse defect: the book is flat, the model is not
            v.append("behavioral_nonzero_with_zero_executable")
        return {"ok": not v, "material": False, "violations": v}

    # MATERIAL from here down.
    if bh is None:
        v.append("behavioral_none_with_material_executable")      # RULE 2
    elif float(bh) == 0.0:
        v.append("behavioral_zero_with_material_executable")      # RULE 2
    elif (float(bh) > 0) != (ex > 0):
        v.append("sign_mismatch_with_material_executable")        # RULE 3
    if bucket_is_flat:
        v.append("false_flat_state_with_material_executable")     # RULE 4
    return {"ok": not v, "material": True, "violations": v}


def check_all(rows) -> dict:
    results = [(r, check_decision(r)) for r in rows]
    bad = [(r, c) for r, c in results if not c["ok"]]
    mat = [c for _, c in results if c["material"] is True]
    counts: dict = {}
    for _, c in results:
        for name in c["violations"]:
            counts[name] = counts.get(name, 0) + 1
    return {
        "decisions": len(results),
        "material_decisions": len(mat),
        "violations_total": sum(counts.values()),
        "violations_by_rule": counts,
        "failing": [{"idx": r.get("idx"), "t": r.get("t"),
                     "executable_residual": r.get("executable_residual"),
                     "behavioral_residual": r.get("behavioral_residual"),
                     "state": r.get("state"), "violations": c["violations"]}
                    for r, c in bad],
        "verdict": "PASS" if not bad else "FAIL",
    }


def load(path: str | Path) -> list:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = list(d.get("per_decision") or [])
    rows += list(d.get("pre_fill_decisions") or [])
    return rows


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else str(
        Path(__file__).with_name("p03_live_verification.json"))
    out = check_all(load(path))
    print(json.dumps(out, indent=2))
    return 0 if out["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
