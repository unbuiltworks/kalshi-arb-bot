"""ONE immutable spec, consumed by BOTH readiness and live. No second copy.

THE DRIFT THIS ELIMINATES. The workflow reconstructed the experiment's
arguments BY HAND in two separate steps. Readiness got
`--entry-tte-band 840:880` and nothing else, so it resolved family BTC5m and
envelope SHADOW_Q001 and intersected the band to the empty [840, 300]; the
armed step meanwhile still carried literal `--envelope live_q1` and
`--entry-tte-band 240:280` with no family at all. Two hand-built command
lines for one experiment can always disagree, and these did.

So the arguments stop being written by hand. A spec is declared once, and

    readiness(spec) -> resolved invocation -> mutation boundary BLOCKED
    live(spec)      -> resolved invocation -> mutation boundary ENABLED

are the same invocation. The ONLY permitted difference is the mutation
authorization/transport state. Family, envelope, band, cohort and every
experiment parameter come from the same object.

THE SPEC HASH IS THE INVARIANT. `spec_sha` covers the INVOCATION -- family,
envelope, band, cohort, action size and the envelope's full bound set. If the
hash readiness proved differs from the hash live consumes by even one field,
live refuses before the latch.

WHY auth_id IS IN THE SPEC BUT NOT IN spec_sha. Readiness runs with the
sentinel and live runs with the grant; that difference is the one the design
intends, and folding it into the hash would make the invariant unprovable by
construction. The auth id is carried, reported and compared SEPARATELY by the
readiness gate. Stated here rather than left to be discovered.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from src.gemini_execution import contract_cohort as CC
from src.gemini_execution import live_envelope as LE

LABEL = "GEMINI_LIVE_PROBE_SPEC_V1"

SENTINEL_AUTH = "NO-AUTHORIZATION-IN-FORCE"

#: the envelope names the CLI accepts, mapped once, here
ENVELOPES: Dict[str, Any] = {
    "shadow_q001": LE.SHADOW_Q001,
    "live_q1": LE.LIVE_Q1,
    "live_q1_btc15m": LE.LIVE_Q1_BTC15M,
}

FAMILIES: Dict[str, Any] = {"btc5m": CC.BTC5M, "btc15m": CC.BTC15M}


class SpecUnknown(KeyError):
    """A spec id nobody declared. Refused, never defaulted."""


@dataclass(frozen=True)
class LiveProbeSpec:
    """Every experiment parameter, declared once."""
    spec_id: str
    family: str
    envelope: str
    entry_tte_band: str
    cohort: Optional[str]
    auth_id: str
    action_size: str
    ledger: str = "analysis/gemini/continuous_ledger.jsonl"

    # ---- resolution -----------------------------------------------------

    @property
    def family_obj(self) -> Any:
        return FAMILIES[self.family]

    @property
    def envelope_obj(self) -> Any:
        return ENVELOPES[self.envelope]

    @property
    def symbol_family(self) -> str:
        return "GEMI-" + str(self.family_obj.prefix)

    def bounds(self) -> Dict[str, str]:
        e = self.envelope_obj
        return {"action_size": str(e.action_size),
                "max_working_orders": str(e.max_working_orders),
                "max_committed_usd": str(e.max_committed_usd),
                "max_residual_usd": str(e.max_residual_usd),
                "max_same_side_run": str(e.max_same_side_run),
                "max_yes": str(e.max_yes), "max_no": str(e.max_no),
                "max_actions": str(e.max_actions),
                "max_concurrent_markets": str(e.max_concurrent_markets),
                "cohort_prefix": str(e.cohort_prefix)}

    # ---- the ONE place an invocation is built ---------------------------

    def canonical_argv(self, *, auth_id: Optional[str] = None,
                       out: str, wire: str,
                       readiness: bool = False) -> Tuple[str, ...]:
        """The invocation. Readiness and live differ ONLY by --readiness."""
        argv = ["--auth-id", str(auth_id if auth_id is not None
                                 else self.auth_id),
                "--family", self.family,
                "--envelope", self.envelope,
                "--entry-tte-band", self.entry_tte_band,
                "--ledger", self.ledger,
                "--out", out, "--wire", wire]
        if self.cohort:
            argv += ["--cohort", self.cohort]
        if readiness:
            argv += ["--readiness"]
        return tuple(argv)

    def invocation_fingerprint(self) -> Dict[str, Any]:
        """What spec_sha is computed over: the EXPERIMENT, not the run.

        Deliberately excludes auth id, output paths and the readiness flag --
        those differ between a readiness run and its live counterpart by
        design, and including them would make the equality check vacuous.
        """
        return {"label": LABEL, "spec_id": self.spec_id,
                "family": self.family, "symbol_family": self.symbol_family,
                "envelope": self.envelope,
                "envelope_id": self.envelope_obj.name,
                "entry_tte_band": self.entry_tte_band,
                "cohort": self.cohort, "action_size": self.action_size,
                "ledger": self.ledger, "bounds": self.bounds()}

    def spec_sha(self) -> str:
        return hashlib.sha256(json.dumps(
            self.invocation_fingerprint(), sort_keys=True,
            default=str).encode("utf-8")).hexdigest()

    def as_dict(self) -> Dict[str, Any]:
        d = dict(self.invocation_fingerprint())
        d["auth_id"] = self.auth_id
        d["auth_id_note"] = (
            "carried but NOT in spec_sha: readiness runs with the sentinel "
            "and live with the grant, so folding it in would make the "
            "readiness==live invariant unprovable by construction")
        d["spec_sha"] = self.spec_sha()
        return d


#: EVERY declared probe. A spec id that is not here is refused.
SPECS: Dict[str, LiveProbeSpec] = {}


def _declare(spec: LiveProbeSpec) -> LiveProbeSpec:
    SPECS[spec.spec_id] = spec
    return spec


#: THE BTC15m CANDIDATE. family, envelope, band and q all in one place.
BTC15M_Q1 = _declare(LiveProbeSpec(
    spec_id="btc15m_q1",
    family="btc15m",
    envelope="live_q1_btc15m",
    #: 840:880 is contract_cohort.BTC15M's OWN band. L5's measured TTE_policy
    #: is [0, 900]; the [0, 300] in the frozen surface is OURS_ADAPTED -- the
    #: length of a BTC5m contract, inherited by BTC15m. Entering at 240:280
    #: would leave the process unattached for the entire interval the
    #: correction is about. See contract_cohort.BTC15M and TTE_PROVENANCE_1.
    entry_tte_band="840:880",
    cohort=None,                       # set when a live cohort is created
    auth_id=SENTINEL_AUTH,             # replaced only by an owner grant
    action_size="1",
))

#: the BTC5m probe, declared so the shape is not BTC15m-only
BTC5M_Q1 = _declare(LiveProbeSpec(
    spec_id="btc5m_q1", family="btc5m", envelope="live_q1",
    entry_tte_band="240:280", cohort=None, auth_id=SENTINEL_AUTH,
    action_size="1"))


def get(spec_id: str) -> LiveProbeSpec:
    try:
        return SPECS[str(spec_id)]
    except KeyError:
        raise SpecUnknown(
            "%r is not a declared probe spec; known: %s"
            % (spec_id, ", ".join(sorted(SPECS)))) from None
