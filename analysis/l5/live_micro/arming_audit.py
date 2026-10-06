"""Boolean ARMED / DISARMED / UNKNOWN per executor, WITHOUT reading secrets.

The safe method, established from the deployment itself: each venue-facing
module SELF-ANNOUNCES its arming state in its own log line -- `"armed":
false`, `"post_capable": false`, or an explicit `NOT_ARMED: ...` refusal.
Those lines contain no key material.

Railway's list-variables returns VALUES, and these services hold
PMUS_API_SECRET_KEY, so it is NOT used here. Variable NAMES come from
describe-service, which does not return values.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

#: a service's arming state is ARMED/DISARMED only on its own evidence.
ARMED_PATTERNS = (r'"armed"\s*:\s*true', r'"post_capable"\s*:\s*true')
DISARMED_PATTERNS = (r'"armed"\s*:\s*false', r'NOT_ARMED', r'"disarmed"\s*:\s*true',
                     r'"post_capable"\s*:\s*false', r'"outcome"\s*:\s*"REFUSED"')
#: strings that would indicate a secret leaked into a log line we quote
SECRET_SHAPED = re.compile(r"(SECRET|PRIVATE_KEY|-----BEGIN|[A-Za-z0-9+/]{40,}={0,2})")


def classify(log_messages: List[str]) -> Dict[str, Any]:
    """Newest-evidence-wins boolean, with the quoted evidence redacted."""
    verdict = "UNKNOWN"
    evidence: Optional[str] = None
    for m in log_messages:                       # caller passes newest first
        if any(re.search(p, m) for p in DISARMED_PATTERNS):
            verdict, evidence = "DISARMED", m
            break
        if any(re.search(p, m) for p in ARMED_PATTERNS):
            verdict, evidence = "ARMED", m
            break
    quote = None
    if evidence:
        # keep only the arming-relevant fragment, and never emit anything
        # secret-shaped
        frag = evidence[:400]
        quote = "REDACTED_SECRET_SHAPED" if SECRET_SHAPED.search(frag) else frag
    return {"verdict": verdict, "evidence_quote": quote,
            "method": ("the service's own self-announced arming boolean; "
                       "no variable VALUE was read"),
            "secrets_read": False}


def report(services: Dict[str, List[str]]) -> Dict[str, Any]:
    out = {name: classify(msgs) for name, msgs in services.items()}
    return {"per_service": out,
            "n_armed": sum(1 for v in out.values() if v["verdict"] == "ARMED"),
            "n_disarmed": sum(1 for v in out.values()
                              if v["verdict"] == "DISARMED"),
            "n_unknown": sum(1 for v in out.values()
                             if v["verdict"] == "UNKNOWN"),
            "RAILWAY_LIST_VARIABLES_USED": False,
            "WHY_NOT": ("it returns values, and these services hold "
                        "PMUS_API_SECRET_KEY; variable NAMES came from "
                        "describe-service, which does not return values")}
