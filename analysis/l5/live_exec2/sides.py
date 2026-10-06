"""The frozen, deterministic side rule.

WHY IT IS A HASH. This experiment measures whether a passive 0.01-share order
is reachable. It has no directional edge and claims none. If the runner chose
its side by looking at the book, every result would be contaminated by an
implicit forecast -- and worse, a runner that decides late can decide
opportunistically, which is exactly the degree of freedom that makes a single
observation unfalsifiable.

So the side is a pure function of two strings fixed BEFORE the window is
observed: the market slug and the owner's session id. Neither depends on
price. The rule is in the spec and therefore in the fingerprint, so it cannot
be changed after a result without changing the authorization.

It is NOT a forecast and must never be reported as one.
"""
from __future__ import annotations

import hashlib
from typing import Dict

#: the venue's own outcome-side tokens
UP = "OUTCOME_SIDE_YES"
DOWN = "OUTCOME_SIDE_NO"

RULE = ("sha256(slug + '|' + session_id); the low bit of the first byte "
        "selects UP (0) or DOWN (1)")


def side_for(slug: str, session_id: str) -> Dict[str, str]:
    if not slug or not session_id:
        raise ValueError("the side rule needs both a slug and a session id")
    digest = hashlib.sha256(("%s|%s" % (slug, session_id)).encode()).hexdigest()
    bit = int(digest[:2], 16) & 1
    return {"side": DOWN if bit else UP,
            "label": "DOWN" if bit else "UP",
            "digest": digest, "bit": str(bit), "rule": RULE,
            "inputs": "%s|%s" % (slug, session_id),
            "NOT_A_FORECAST": "the side carries no directional view"}
