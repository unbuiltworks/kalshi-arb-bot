"""A fresh bounded mutation budget, with provenance, that rewrites nothing.

WHY THIS EXISTS. CONTINUOUS-2 through CONTINUOUS-4 spent all 12 slots of the
original ledger, and that ledger is committed to the repository. The obvious
way to run again -- reset the counter -- destroys the only durable record that
those mutations happened. This is the alternative: a NEW cohort with its OWN
empty ledger, which records the predecessor's identity and content hash and
leaves the predecessor byte-for-byte untouched.

THE THREE PROPERTIES THAT MAKE IT SAFE:

  NO REWRITE. The historical ledger is opened read-only, hashed, and recorded.
  If its bytes ever differ from the recorded hash, `verify_predecessor` says
  so -- so a later reset cannot be passed off as a fresh cohort.

  NO ROLLOVER. The budget is fixed at creation. Unused slots do not carry into
  any later cohort, and spent slots from an earlier one do not follow the new
  one either. A cohort is a bounded allowance, not a running balance.

  NO AUTOMATIC CREATION. `create` refuses when the manifest already exists,
  and nothing in the package calls it. A cohort comes into being because a
  human ran a command, exactly like an authorization.

THE ENVELOPE IS STILL THE CEILING. A cohort may ask for FEWER slots than
`CP.MAX_ACTIONS` and never more; a request above the envelope is refused
rather than clamped, because silently granting less than was asked for is how
a caller comes to believe it has budget it does not have.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.gemini_execution import continuous_probe as CP

LABEL = "GEMINI_MUTATION_COHORT_V1"

#: where cohorts live. One directory per cohort, holding its manifest and its
#: own ledger, so no two cohorts can ever share a budget file.
COHORT_ROOT = os.path.join("analysis", "gemini", "cohorts")

#: the ledger every historical mutation was written to. Referenced, hashed,
#: and never opened for writing.
HISTORICAL_LEDGER = os.path.join("analysis", "gemini",
                                 "continuous_ledger.jsonl")

MANIFEST = "cohort.json"
LEDGER = "ledger.jsonl"


class CohortRefused(RuntimeError):
    """Refused rather than created. Always safe to see."""


@dataclass
class Cohort:
    cohort_id: str
    created_utc: str
    budget: int
    ledger_path: str
    predecessor_ledger: str
    predecessor_sha256: str
    predecessor_consumed: int
    envelope_max_actions: int
    note: str

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def _sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _consumed_in(path: str) -> int:
    """Slots spent in a ledger, counted WITHOUT importing the driver."""
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    n += 1          # a torn row is a consumed slot, not a gap
                    continue
                if isinstance(row, dict) and row.get("kind") in ("PLACE",
                                                                 "CANCEL"):
                    n += 1
    except OSError:
        return 0
    return n


def directory(cohort_id: str, *, root: str = COHORT_ROOT) -> str:
    safe = str(cohort_id)
    if not safe or "/" in safe or "\\" in safe or safe.startswith("."):
        raise CohortRefused("%r is not a usable cohort id" % (cohort_id,))
    return os.path.join(root, safe)


def manifest_path(cohort_id: str, *, root: str = COHORT_ROOT) -> str:
    return os.path.join(directory(cohort_id, root=root), MANIFEST)


def exists(cohort_id: str, *, root: str = COHORT_ROOT) -> bool:
    return os.path.exists(manifest_path(cohort_id, root=root))


def create(cohort_id: str, budget: int, *, root: str = COHORT_ROOT,
           predecessor_ledger: str = HISTORICAL_LEDGER,
           note: str = "") -> Cohort:
    """Bring ONE cohort into being. Refuses everything else.

    Deliberately not idempotent: a second call for the same id is a refusal,
    not a no-op, because "create the cohort again" and "I did not realise one
    already existed" look identical from here and only one of them is safe.
    """
    if not isinstance(budget, int) or isinstance(budget, bool):
        raise CohortRefused("budget must be a whole number of slots")
    if budget < 1:
        raise CohortRefused("a cohort with no slots cannot do anything")
    if budget > CP.MAX_ACTIONS:
        raise CohortRefused(
            "budget %d exceeds the envelope's %d; the envelope is the ceiling "
            "and a cohort may only ask for fewer"
            % (budget, CP.MAX_ACTIONS))
    if exists(cohort_id, root=root):
        raise CohortRefused(
            "cohort %s already exists; a cohort is created once, by a human, "
            "and never re-created automatically" % cohort_id)
    if not os.path.exists(predecessor_ledger):
        raise CohortRefused(
            "predecessor ledger %s is missing; a cohort records what came "
            "before it and cannot be created without it" % predecessor_ledger)

    d = directory(cohort_id, root=root)
    os.makedirs(d, exist_ok=True)
    led = os.path.join(d, LEDGER)
    if os.path.exists(led):
        raise CohortRefused("cohort ledger %s already exists" % led)

    c = Cohort(
        cohort_id=str(cohort_id),
        created_utc=datetime.now(timezone.utc).isoformat(),
        budget=int(budget),
        ledger_path=led,
        predecessor_ledger=predecessor_ledger,
        predecessor_sha256=_sha256_file(predecessor_ledger),
        predecessor_consumed=_consumed_in(predecessor_ledger),
        envelope_max_actions=int(CP.MAX_ACTIONS),
        note=str(note),
    )

    # the ledger is created EMPTY and the manifest written after it, so a
    # crash between the two leaves a cohort that `load` refuses rather than
    # one that looks usable with no record of where it came from
    with open(led, "w", encoding="utf-8") as fh:
        fh.flush()
        os.fsync(fh.fileno())
    with open(manifest_path(cohort_id, root=root), "w",
              encoding="utf-8") as fh:
        json.dump({"label": LABEL, **c.to_json(),
                   "labels": ("NO ROLLOVER: this budget is fixed and unused "
                              "slots never carry. NO REWRITE: the predecessor "
                              "ledger is hashed, never modified. NO AUTOMATIC "
                              "CREATION: nothing in the package calls "
                              "create().")},
                  fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    return c


def load(cohort_id: str, *, root: str = COHORT_ROOT) -> Cohort:
    p = manifest_path(cohort_id, root=root)
    if not os.path.exists(p):
        raise CohortRefused("no cohort %s; it must be created explicitly"
                            % cohort_id)
    with open(p, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    fields = {k: raw.get(k) for k in Cohort.__dataclass_fields__}
    missing = [k for k, v in fields.items() if v is None]
    if missing:
        raise CohortRefused("cohort %s manifest is incomplete: %s"
                            % (cohort_id, ", ".join(missing)))
    c = Cohort(**fields)
    if not os.path.exists(c.ledger_path):
        raise CohortRefused("cohort %s names a ledger that does not exist"
                            % cohort_id)
    return c


def verify_predecessor(c: Cohort) -> Dict[str, Any]:
    """Is the history this cohort was created against still intact?

    A cohort whose predecessor has CHANGED is the exact shape of a reset
    dressed up as a fresh start, so it is reported rather than assumed.
    """
    try:
        now = _sha256_file(c.predecessor_ledger)
    except OSError as exc:
        return {"intact": False, "why": "predecessor unreadable: %s" % exc}
    intact = now == c.predecessor_sha256
    return {"intact": intact,
            "predecessor_ledger": c.predecessor_ledger,
            "recorded_sha256": c.predecessor_sha256,
            "current_sha256": now,
            "recorded_consumed": c.predecessor_consumed,
            "current_consumed": _consumed_in(c.predecessor_ledger),
            "why": (None if intact else
                    "the historical ledger changed after this cohort was "
                    "created; prior mutations must stay byte-for-byte")}


def state(cohort_id: str, *, root: str = COHORT_ROOT) -> Dict[str, Any]:
    """Everything a report needs, without opening a mutation path."""
    c = load(cohort_id, root=root)
    used = _consumed_in(c.ledger_path)
    return {"label": LABEL, "cohort_id": c.cohort_id,
            "created_utc": c.created_utc,
            "budget": c.budget, "consumed": used,
            "remaining": max(0, c.budget - used),
            "ledger_path": c.ledger_path,
            "envelope_max_actions": c.envelope_max_actions,
            "predecessor": verify_predecessor(c),
            "note": c.note,
            "labels": ("the cohort budget is a TIGHTENING of the envelope, "
                       "never an extension of it. No rollover, no automatic "
                       "successor.")}
