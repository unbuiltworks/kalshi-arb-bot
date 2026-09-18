"""One hash over every file that carries a safety property.

WHY `executable_sha` IS NO LONGER ENOUGH. It covers ws_continuous.py, where
the BOUNDS live. It does not cover run_continuous.py, where the ORDERING lives
-- and ordering is a safety property with a price already paid twice:
CONTINUOUS-1 was spent by a preflight that ran after the latch, and
CONTINUOUS-3 by a recovery that ran after the latch. Both fixes are invoker
changes that `executable_sha` cannot see. An authorization that names only the
driver would not notice either of them being undone.

So an authorization names BOTH: `executable_sha` for the driver, and
`bundle_sha` for the whole set.

THE SAME DETACHED-SIGNATURE SHAPE AS THE DRIVER. Every authorization constant
in every member is normalized away before hashing, including this file's own.
Otherwise installing a bundle authorization would change the bundle, which
would invalidate the authorization being installed -- the self-referential
preimage that made the first driver UNARMABLE rather than unarmed.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional

#: The sha256 of the bundle as reviewed. None means NO BUNDLE AUTHORIZATION
#: HAS BEEN ISSUED. Nothing reads an environment variable to fill it in.
AUTHORIZED_BUNDLE: Optional[str] = "3bd2872fdb462ea48aa28dc04b299232c9df6da45f88030af96faca5f993497d"  # noqa: E501

_HERE = os.path.dirname(os.path.abspath(__file__))

#: ORDER IS PART OF THE HASH. Appending a member changes it, which is correct:
#: a new file carrying a safety property is a change to what was reviewed.
MEMBERS = (
    "ws_continuous.py",        # bounds, ledger, budget, ambiguity handling
    "run_continuous.py",       # ordering: preflight/recovery before the latch
    "continuous_probe.py",     # the frozen controller and its envelope
    "cohort.py",               # the bounded budget a window is granted
    "continuous_preflight.py",  # the authoritative preflight
    "metadata.py",             # the authoritative venue reads recovery uses
    "arming.py",               # the transport arm and the identifier
    "bundle.py",               # this file, so the member list cannot be edited
)

#: every authorization-bearing assignment, in any member, normalized away
_AUTH_LINES = re.compile(
    r"^(REQUIRED_AUTH_ID|AUTHORIZED_SHA|AUTHORIZED_ENVELOPE|"
    r"AUTHORIZED_BUNDLE|EXPECTED_AUTH_ID)"
    r"(: Optional\[str\])? = .*$", re.MULTILINE)
#: deliberately NOT ws_continuous's own "<AUTHORIZATION>": that token
#: appears verbatim in its source as the driver's placeholder, so reusing
#: it would make "did this file already contain the token?" unanswerable.
_PLACEHOLDER = r"\1\2 = <BUNDLE-AUTHORIZATION>"

#: how many authorization lines each member is expected to carry. Checked
#: exactly, so BOTH a constant appearing and a constant vanishing are
#: refusals rather than a silently different hash.
EXPECTED_AUTH_LINES = {
    "ws_continuous.py": 3,
    "run_continuous.py": 0,
    "continuous_probe.py": 0,
    "cohort.py": 0,
    "continuous_preflight.py": 0,
    "metadata.py": 0,
    "arming.py": 1,
    "bundle.py": 1,
}


class BundleNormalizationFailed(RuntimeError):
    """Refused rather than hashed over a shape nobody reviewed."""


def member_source(name: str) -> str:
    with open(os.path.join(_HERE, name), "r", encoding="utf-8") as fh:
        return fh.read()


def normalized(name: str) -> str:
    """A member's source with its authorization constants blanked."""
    src = member_source(name)
    # NO "does this file already contain the token?" CHECK. This file defines
    # the token, so such a check can never pass on itself -- and it guards
    # nothing real: substitution only rewrites lines the regex MATCHES, so a
    # token sitting anywhere else is hashed verbatim like any other text and
    # hides nothing. The exact count below is the guard that does the work.
    body, n = _AUTH_LINES.subn(_PLACEHOLDER, src)
    want = EXPECTED_AUTH_LINES.get(name)
    if want is None:
        raise BundleNormalizationFailed(
            "%s has no expected authorization-line count" % name)
    if n != want:
        raise BundleNormalizationFailed(
            "%s normalized %d authorization line(s), expected %d; the hash is "
            "refused rather than computed over an unknown shape"
            % (name, n, want))
    return body


def bundle_body() -> str:
    """Every member, in order, each labelled so two files cannot swap."""
    parts: List[str] = []
    for name in MEMBERS:
        parts.append("\n===== BUNDLE MEMBER: %s =====\n" % name)
        parts.append(normalized(name))
    return "".join(parts)


def bundle_sha() -> str:
    return hashlib.sha256(bundle_body().encode("utf-8")).hexdigest()


def member_shas() -> Dict[str, str]:
    """Per-member hashes, so a mismatch says WHICH file moved."""
    return {n: hashlib.sha256(normalized(n).encode("utf-8")).hexdigest()
            for n in MEMBERS}


def authorized() -> bool:
    return (AUTHORIZED_BUNDLE is not None
            and AUTHORIZED_BUNDLE == bundle_sha())


def state() -> Dict[str, Any]:
    cur = bundle_sha()
    why: List[str] = []
    if AUTHORIZED_BUNDLE is None:
        why.append("AUTHORIZED_BUNDLE is None: no bundle authorization has "
                   "been issued for this set of files")
    elif AUTHORIZED_BUNDLE != cur:
        why.append("AUTHORIZED_BUNDLE names a different bundle than the one "
                   "running; a file carrying a safety property changed after "
                   "it was reviewed")
    return {"authorized": authorized(),
            "bundle_sha": cur,
            "authorized_bundle": AUTHORIZED_BUNDLE,
            "members": list(MEMBERS),
            "member_shas": member_shas(),
            "why_not": why,
            "labels": ("executable_sha covers the driver's BOUNDS; bundle_sha "
                       "covers the ORDERING too. Both are named by an "
                       "authorization, because undoing either has cost a "
                       "live authorization before.")}
