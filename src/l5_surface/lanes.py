"""Two venues, one hypothesis, and a spec that cannot pretend it was measured here.

WHY THIS FILE EXISTS. FIDELITY_OBSERVABLE_V1 is a statement about Polymarket
International BTC15m. Carrying any of it onto Polymarket US sports or Gemini
BTC5m is a TRANSFER HYPOTHESIS, not an observation about those venues, and the
entire value of running both lanes is that a transfer can FAIL. That is only
learnable if transferred fields stay distinguishable from measured ones -- so
every field a lane inherits is marked TRANSFERRED and names its source spec
and hash.

WHAT DOES NOT TRANSFER AT ALL. Some observations are about the INSTRUMENT, not
the behaviour, and carrying them would be a category error rather than a
hypothesis:

  TTE_policy          measured as [0, 900] because a BTC15m window is 900s.
                      A Gemini BTC5m window is 300s and a sports market runs
                      for hours or days. The NUMBER is about the instrument.
  secondary_rung_qty  33.121208 is a share count on a Polymarket outcome
                      token. It is not a quantity on a different venue's
                      contract.

Those are re-declared per lane as OURS_ADAPTED, because we are choosing them.

A LANE CAN BE WRONG, AND THE RULE ABOVE IS HOW. `TTE_policy` is declared
instrument-specific because the NUMBER is a window length -- so the number a
lane picks has to be ITS OWN instrument's length. GEMINI_CRYPTO picked 300,
which is the length of a Gemini BTC5m contract, and then the same lane was
bound to BTC15m, whose window is 900s. That is not a different judgement about
the instrument rule; it is the rule not being applied a second time. See
CORRECTIONS.

FIDELITY_OBSERVABLE_V1 IS NEVER MUTATED. Deriving a lane reads it and builds a
new set; a test asserts its hash is identical afterwards.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from src.l5_surface import params as PR

PMUS_SPORTS = "L5_ADAPTED_PMUS_SPORTS"
GEMINI_CRYPTO = "L5_ADAPTED_GEMINI_CRYPTO"
LANES = (PMUS_SPORTS, GEMINI_CRYPTO)

#: A corrected VARIANT of a lane, and deliberately NOT a member of LANES.
#: `lane_gates.SOURCES` is keyed by LANES, so adding one there would hand a
#: shadow research arm a live-progression gate it has earned nothing towards.
#: A correction is shadowed against the lane it corrects and is promoted, if
#: ever, by evidence and by an explicit edit -- never by existing.
GEMINI_BTC15M_V2 = "L5_ADAPTED_GEMINI_BTC15M_V2"
CORRECTION_LABELS = (GEMINI_BTC15M_V2,)

#: fields whose observed value is about the INSTRUMENT rather than the
#: behaviour, so they are never transferred -- each lane declares its own.
INSTRUMENT_SPECIFIC = ("TTE_policy", "secondary_rung_qty")


#: Each lane's OWN choices, declared here rather than in a freezing script so
#: the artifact on disk is reproducible from code and a test can prove the two
#: have not drifted. Venue-native, and never a claim about L5:
#:
#:   base/secondary qty  PMUS: the executor's fixed QTY=1. Gemini: UNIT=1.
#:                       Neither is 33.121208 shares of a Polymarket token.
#:   TTE_policy          PMUS sports run for hours or days, so the band is
#:                       effectively open; a Gemini BTC5m window is 300s.
#:   inventory_price_skew  NONE -- no skew is supported by anything observed,
#:                       and this is an experimental axis, not a finding.
#:   withdrawal_policy   TTE_FLOOR -- ours, for the same reason.
_OURS_COMMON: Dict[str, Any] = {
    "base_parent_qty": "1",
    "secondary_rung_qty": "1",
    "inventory_price_skew": {"widening_ticks": 0, "reducing_ticks": 0},
    "inventory_size_skew": {"widening_multiplier": 1,
                            "reducing_multiplier": 1},
    "replenishment_policy": {"mode": "REPLACE_AFTER_FILL"},
    "minimum_rest_time": 30.0,
    "withdrawal_policy": {"mode": "WITHDRAW_ON_TTE_FLOOR", "tte_floor_s": 0},
    "max_residual": "3.00",
}

OURS: Dict[str, Dict[str, Any]] = {
    PMUS_SPORTS: dict(_OURS_COMMON,
                      TTE_policy={"quote_between_tte_s": [0, 86400]}),
    GEMINI_CRYPTO: dict(_OURS_COMMON,
                        TTE_policy={"quote_between_tte_s": [0, 300]}),
}


#: WHAT EACH CORRECTION CHANGES, WHY, AND WHAT IT LEAVES ALONE. A correction
#: names exactly one field. Everything else -- side selection, the price
#: surface, sequential inventory state, EXTEND/COMPLETE mechanics, the size and
#: residual bounds, NO_NEW_OPEN_EXTEND_TTE_S and WITHDRAW_ALL_TTE_S -- is the
#: base lane's, unchanged, because only the corrected field was shown wrong.
CORRECTIONS: Dict[str, Dict[str, Any]] = {
    GEMINI_BTC15M_V2: {
        "corrects": GEMINI_CRYPTO,
        "field": "TTE_policy",
        "value": {"quote_between_tte_s": [0, 900]},
        "why": (
            "GEMINI_CRYPTO quotes only tte <= 300. That 300 is the length of "
            "a Gemini BTC5m contract: OURS_ADAPTED, never measured from L5, "
            "chosen for the 5-minute cohort and then inherited unchanged when "
            "the same lane was bound to BTC15m (TTE_PROVENANCE_1.md). "
            "TTE_policy is instrument-specific precisely because the number is "
            "a window length, and a Gemini BTC15m window is 900s -- so the "
            "instrument rule, applied to THIS instrument, gives 900. L5's own "
            "measured TTE_policy on a 900s window is [0, 900], which is the "
            "same number arrived at independently. "
            "L5 BTC15m fills, 63,569 deduped across 527 windows: 560 of 626 "
            "OPEN fills (89.5%) land at tte 600-900, 46 at 300-600, and NONE "
            "below tte 60 -- under [0, 300] the clone is present only for the "
            "3.2% tail in which L5 has stopped opening. "
            "Paired observed-price replay over 96 identical Gemini BTC15m "
            "contracts: a fee-positive complement is price-observable in 28 "
            "contracts under [300, 900] against 12 under [0, 300]; the "
            "discordant pairs split 22 to 6 (two-sided sign test p=0.0037); "
            "and 38 of the 96 contracts have NO prints whatsoever inside "
            "[0, 300]. See L5_TTE_SEGMENTS_1.md and "
            "analysis/gemini/early_15m_observed*.json."),
        "not_changed": (
            "side selection, tick_offsets, rung_count, both_sides_policy, "
            "sequential inventory state, EXTEND/COMPLETE classification, "
            "base_parent_qty, secondary_rung_qty, max_residual, "
            "NO_NEW_OPEN_EXTEND_TTE_S (60s), WITHDRAW_ALL_TTE_S (30s), "
            "MAX_ACTIONS (12)"),
        "status": (
            "SHADOW ONLY. Not authorized for any live path, and it carries no "
            "live-progression gate. It is run ALONGSIDE the lane it corrects "
            "so the two decision records can be compared on one tape."),
    },
}


def derive_correction(fidelity: PR.SurfaceParams, label: str
                      ) -> PR.SurfaceParams:
    """The base lane with ONE field replaced, and the replacement explained.

    The corrected field stays OURS_ADAPTED. It coincides with a value measured
    about L5, but we did not measure it here and arrived at it from our own
    instrument's length, so calling it observed would be claiming a finding
    this venue has not produced. The provenance goes in the evidence text
    where it can be read, not in the status where it would be a claim.
    """
    if label not in CORRECTIONS:
        raise ValueError("unknown correction %r; known: %s"
                         % (label, tuple(CORRECTIONS)))
    c = CORRECTIONS[label]
    base = derive(fidelity, c["corrects"])
    field = c["field"]
    out = dict(base.params)
    out[field] = PR.Param(
        field, PR.OURS_ADAPTED, value=c["value"],
        prior_evidence=("CORRECTION to %s.%s, which was %r. %s NOT CHANGED: "
                        "%s. %s"
                        % (c["corrects"], field,
                           base.params[field].value, c["why"],
                           c["not_changed"], c["status"])))
    return PR.SurfaceParams(
        label=label, kind="ADAPTED", params=out,
        notes=("%s corrects exactly one field of %s@%s: %s. Every other field "
               "is that lane's, byte for byte, and %s is preserved unchanged "
               "as the control."
               % (label, c["corrects"], base.spec_sha256()[:16], field,
                  c["corrects"])))


def derive(fidelity: PR.SurfaceParams, lane: str,
           ours: Optional[Mapping[str, Any]] = None, *,
           notes: Optional[str] = None) -> PR.SurfaceParams:
    """Build a lane spec from the observable set, marking every inheritance.

    `ours` supplies this lane's own choices; it defaults to the lane's
    declared OURS, which is what the frozen artifact was built from. Anything observed that is not
    instrument-specific is carried as TRANSFERRED, attributed to the source
    spec and its hash. Nothing that was UNRESOLVED becomes resolved by being
    copied.
    """
    if lane not in LANES:
        raise ValueError("unknown lane %r; known: %s" % (lane, LANES))
    ours = OURS[lane] if ours is None else ours
    src = "%s@%s" % (fidelity.label, fidelity.spec_sha256()[:16])
    out: Dict[str, PR.Param] = {}
    for f in PR.FIELDS:
        if f in ours:
            v = ours[f]
            out[f] = v if isinstance(v, PR.Param) else PR.Param(
                f, PR.OURS_ADAPTED, value=v,
                prior_evidence="OURS for %s. Not a finding about this venue "
                               "and never citable as one." % lane)
            continue
        p = fidelity.params[f]
        if f in INSTRUMENT_SPECIFIC:
            out[f] = PR.Param(
                f, PR.UNRESOLVED,
                prior_evidence=(
                    "NOT TRANSFERRED: %s's observed value is about the "
                    "Polymarket BTC15m instrument, not about behaviour, so "
                    "carrying it to %s would be a category error. This lane "
                    "must declare its own." % (f, lane)),
                pending_source=lane)
            continue
        if p.status == PR.OBSERVED_PROSPECTIVE:
            out[f] = PR.Param(f, PR.TRANSFERRED, value=p.value,
                              transferred_from=src,
                              prior_evidence=("observed on Polymarket BTC15m: "
                                              + str(p.evidence)))
        else:
            out[f] = PR.Param(f, PR.UNRESOLVED,
                              prior_evidence=p.prior_evidence,
                              pending_source=lane)
    return PR.SurfaceParams(
        label=lane, kind="ADAPTED", params=out,
        notes=(notes or ("derived from %s; every inherited field is "
                         "TRANSFERRED, never observed here" % src)))


def report_transfer(sp: PR.SurfaceParams) -> Dict[str, Any]:
    """What this lane ASSUMED versus what it CHOSE versus what it still lacks.

    The transfer list is the experiment: if this lane behaves differently from
    the reference, these are the assumptions that carried the difference."""
    return {
        "lane": sp.label, "spec_sha256": sp.spec_sha256(),
        "transferred": sp.transferred(),
        "ours": [f for f in PR.FIELDS
                 if sp.params[f].status == PR.OURS_ADAPTED],
        "unresolved": sp.unresolved(),
        "observed_here": sp.l5_evidence_fields(),
        "labels": [
            "observed_here is EMPTY until this lane measures something "
            "itself. Nothing inherited counts.",
            "transferred fields are the hypothesis under test; a lane that "
            "diverges from the reference diverges somewhere in this list.",
        ],
    }


def freeze(directory: str = PR.FROZEN_DIR) -> Dict[str, str]:
    """Regenerate both lane artifacts from the declarations above.

    Deterministic by construction, so re-running it on an unchanged repo
    rewrites byte-identical files. It reads FIDELITY_OBSERVABLE_V1 and never
    writes it.
    """
    import json
    import os

    sets = PR.load_frozen(directory)
    fid = sets[PR.FIDELITY_LABEL] if hasattr(PR, "FIDELITY_LABEL") \
        else sets["FIDELITY_OBSERVABLE_V1"]
    before = fid.spec_sha256()
    out: Dict[str, str] = {}
    specs = [(lane, derive(fid, lane)) for lane in LANES]
    specs += [(lbl, derive_correction(fid, lbl))
              for lbl in CORRECTION_LABELS]
    for lane, sp in specs:
        path = os.path.join(directory, "%s.json" % lane)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(PR.to_json(sp), fh, indent=2, sort_keys=True)
            fh.write("\n")
        out[lane] = sp.spec_sha256()
    assert fid.spec_sha256() == before, "deriving mutated the observable set"
    out[PR.FIDELITY_LABEL if hasattr(PR, "FIDELITY_LABEL")
        else "FIDELITY_OBSERVABLE_V1"] = before
    return out


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    for k, v in freeze().items():
        print("%-28s %s" % (k, v[:16]))
