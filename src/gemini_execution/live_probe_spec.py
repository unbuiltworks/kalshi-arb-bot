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
    "live_q1_btc15m_cap4": LE.LIVE_Q1_BTC15M_CAP4,
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
    #: THE BUDGET LEDGER THIS PROBE SPENDS FROM. Carried, reported and
    #: compared, but deliberately NOT in `invocation_fingerprint` -- for the
    #: same reason `auth_id` is not, and it is worth stating rather than
    #: leaving to be discovered:
    #:
    #:   `cohort` above is part of the EXPERIMENT's identity. A readiness run
    #:   and its live counterpart are the same experiment, and folding a
    #:   ledger id into that identity would mean the spec hash moved the
    #:   moment an owner granted a budget -- so the hash reviewed before the
    #:   grant could never equal the hash the armed run resolves, and the
    #:   readiness==live invariant would be unprovable by construction.
    #:
    #: Both steps take it from THIS object, so they still cannot disagree.
    live_cohort: Optional[str] = None
    #: THE TTE INTERVAL THE FROZEN SURFACE MAY QUOTE IN, and it IS hashed.
    #:
    #: None means the lane's own `quote_between_tte_s` is used untouched --
    #: [0, 300] for L5_ADAPTED_GEMINI_CRYPTO, which is where every window
    #: this programme has run got its quoting interval. A spec that names a
    #: band is a BEHAVIOURAL change, so unlike `live_cohort` and `auth_id`
    #: this field is inside `invocation_fingerprint`: the interval cannot
    #: move without spec_sha moving, and readiness compares spec_sha.
    quote_band: Optional[Tuple[float, float]] = None

    # ---- resolution -----------------------------------------------------

    @property
    def family_obj(self) -> Any:
        return FAMILIES[self.family]

    @property
    def envelope_obj(self) -> Any:
        return ENVELOPES[self.envelope]

    @property
    def budget_cohort(self) -> Optional[str]:
        """The ledger a run actually binds: the granted one, else the hashed
        one. One accessor, so no caller has to know which field is set."""
        return self.live_cohort or self.cohort

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
        if self.budget_cohort:
            argv += ["--cohort", self.budget_cohort]
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
                "ledger": self.ledger, "bounds": self.bounds(),
                **self._quote_band_fingerprint()}

    def _quote_band_fingerprint(self) -> Dict[str, Any]:
        """The quoting interval's contribution to spec_sha, and its absence.

        PRESENT ONLY WHEN A BAND IS NAMED, deliberately. The property that
        matters is "the quoting interval cannot move without spec_sha moving",
        and that holds either way: None -> a band moves the hash, and one band
        -> another moves it. Including the key as an explicit null instead
        would ALSO move the hash of every spec that never had a band, which
        would retire reviewed identities -- among them the
        7fda21b1... the owner approved for the CAP-4 windows -- for a
        refactor that changed none of their behaviour. A hash should move when
        behaviour moves and not otherwise.
        """
        if self.quote_band is None:
            return {}
        return {"quote_band": [float(self.quote_band[0]),
                               float(self.quote_band[1])]}

    def spec_sha(self) -> str:
        return hashlib.sha256(json.dumps(
            self.invocation_fingerprint(), sort_keys=True,
            default=str).encode("utf-8")).hexdigest()

    def as_dict(self) -> Dict[str, Any]:
        d = dict(self.invocation_fingerprint())
        d["auth_id"] = self.auth_id
        d["live_cohort"] = self.live_cohort
        d["budget_cohort"] = self.budget_cohort
        d["live_cohort_note"] = (
            "carried but NOT in spec_sha, for the same reason as auth_id: a "
            "readiness run and its live counterpart are one experiment, and "
            "folding the granted budget ledger into the experiment's identity "
            "would move the hash at the moment of the grant")
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
    cohort=None,                       # HASHED field: stays None, see below
    auth_id=SENTINEL_AUTH,             # replaced only by an owner grant
    action_size="1",
    #: GRANTED 2026-09-21 under OWNER-2026-09-21-GEMINI-BTC15M-Q1-LIVE-1.
    #: Exactly 12 action attempts, chained to COHORT-2026-09-21-Q1LIVE-1
    #: (12/12 SPENT). ONE window: when this is spent its unused slots stay
    #: unused. It is carried HERE rather than in `cohort` so that granting a
    #: budget does not move spec_sha away from the reviewed
    #: d2f1f05d789387f52fc2e72cb4516342684c560c1b2484f54fe2a282c5e05a3c.
    live_cohort="COHORT-2026-09-21-BTC15M-Q1LIVE-1",
))

#: THE CAP-4 CANDIDATE. Identical to BTC15M_Q1 in family, band, q, cohort
#: prefix and controller; the ONLY difference is the envelope, and the only
#: difference in the envelope is how much inventory the oscillator may carry.
#: A DIFFERENT spec_sha is correct here: this is a different experiment, and
#: the hash is what says so.
BTC15M_Q1_CAP4 = _declare(LiveProbeSpec(
    spec_id="btc15m_q1_cap4",
    family="btc15m",
    envelope="live_q1_btc15m_cap4",
    entry_tte_band="840:880",
    cohort=None,
    auth_id=SENTINEL_AUTH,
    action_size="1",
    live_cohort="COHORT-2026-09-21-BTC15M-CAP4-3",
))

#: PROPOSED, NOT AUTHORIZED. Identical to BTC15M_Q1_CAP4 in family, envelope,
#: entry band, q, cohort prefix, rung count, pricing and every risk bound. The
#: ONLY difference is `quote_band`, and the different spec_sha is the point:
#: this is a different experiment and the hash says so before anyone runs it.
#:
#: WHY [60, 880]. The standing [0, 300] is OURS_ADAPTED and was never measured
#: from L5: it is the LENGTH OF A BTC5m CONTRACT, chosen for the 5-minute
#: cohort and inherited unchanged when the same lane was bound to BTC15m
#: (TTE_PROVENANCE_1.md), where it means the clone is absent for the first two
#: thirds of every market. The L5 BTC15m source evidence -- 63,569 deduped
#: fills across 527 windows -- puts 560 of 626 OPEN fills (89.5%) at tte
#: 600-900 and NONE below tte 60. The frozen shadow lane
#: L5_ADAPTED_GEMINI_BTC15M_V2 already corrects this to [0, 900] and has been
#: running alongside as evidence; 880 is the top of the entry band (a contract
#: does not exist above 900) and 60 is NO_NEW_OPEN_EXTEND_TTE_S, so the
#: proposal quotes exactly where the evidence says L5 opens and stops where
#: the existing terminal safety already stops it.
#:
#: NOT CHANGED: pricing, tick_offsets, rung_count, both_sides_policy, q, the
#: CAP-4 envelope, aggressiveness, the 30s rest floor, and the two settlement
#: guards (NO_NEW_OPEN_EXTEND_TTE_S 60s, WITHDRAW_ALL_TTE_S 30s), which still
#: bind inside this band exactly as they do outside it.
BTC15M_Q1_CAP4_W60_880 = _declare(LiveProbeSpec(
    spec_id="btc15m_q1_cap4_w60_880",
    family="btc15m",
    envelope="live_q1_btc15m_cap4",
    entry_tte_band="840:880",
    cohort=None,
    auth_id=SENTINEL_AUTH,
    action_size="1",
    #: GRANTED 2026-09-21 under
    #: OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-COHORT-1: the HEAD of a ten
    #: ledger chain, W01..W10, 12 action attempts each and 120 in total.
    #: Carried here rather than in `cohort` so that granting a budget does
    #: not move spec_sha away from the reviewed 223b81ed...
    live_cohort="COHORT-2026-09-21-BTC15M-COHORT1-W01",
    quote_band=(60.0, 880.0),
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
