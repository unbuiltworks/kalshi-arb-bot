"""The numerical accuracy contract, as a type rather than a habit.

A `Metric` cannot be constructed without the things that make it auditable:
an exact numerator and denominator, the UNIT those count, the artifact it came
from, the period, the lane, whether it was observed or reconstructed, and
whether it depends on the unresolved tape decoding.

WHY A TYPE AND NOT A CONVENTION. Three numbers in this programme were reported
wrong in one day, and every one of them failed in a way a field would have
caught: a rate divided fills by a per-cycle counter (wrong UNIT), a zero was
reported as a finding when the constraint could not fire (no REACHABILITY), and
a 6.2x was quoted between arms with 4-7.5x different exposure (no EXPOSURE
normalization). A convention did not stop any of them.

Nothing here computes a metric. This module only refuses to let one be stated
without its provenance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Any, Dict, Optional

#: THE UNIT VOCABULARY IS CLOSED. A unit outside this set is refused, because
#: "requests" and "actions" are exactly the words that let a rung-cycle be
#: reported as an order.
UNITS = {
    "trade",          # one print on the venue tape
    "fill",           # one execution against one of our rungs
    "order",          # one order actually submitted to the venue
    "rung_cycle",     # one desired rung, counted once PER CYCLE it stood
    "window",         # one contract window
    "market",         # one contract
    "usd",
    "contract",       # one binary contract of quantity
    "second",
    "episode",        # one residual/imbalance episode
    "row",            # one record in a named artifact
}

#: what a number IS, epistemically. Never mixed in a report.
OBSERVED = "OBSERVED"            # read directly from venue or source data
RECONSTRUCTED = "RECONSTRUCTED"  # depends on a decoder, model or accounting rule
SIMULATED = "SIMULATED"          # produced by replaying a policy
INFERRED = "INFERRED"            # a conclusion drawn from the above
EVIDENCE = (OBSERVED, RECONSTRUCTED, SIMULATED, INFERRED)

#: how badly the unresolved YES/complement tape ambiguity could move this.
DEC_NONE = "NONE"                # does not read a trade's side at all
DEC_SIDE = "SIDE_DEPENDENT"      # reads the print's aggressor side or mirror
DEC_FILL = "FILL_DEPENDENT"      # derived from fills, which are side-dependent
DECODER = (DEC_NONE, DEC_SIDE, DEC_FILL)

AGREE, DISAGREEMENT, NOT_VERIFIED = "AGREE", "DISAGREEMENT", "NOT_VERIFIED"


class ContractViolation(ValueError):
    """A metric was stated without what makes it checkable. Always fatal."""


@dataclass
class Metric:
    name: str
    numerator: D
    denominator: Optional[D]
    unit_numerator: str
    unit_denominator: Optional[str]
    source: str                   # artifact path or the exact query
    period: str                   # ISO range, or the artifact's own coverage
    lane: str                     # BTC5m / BTC15m / BTC hourly / ALL
    evidence: str
    decoder: str
    #: filled in by the INDEPENDENT implementation, never by the primary one
    independent: Optional[D] = None
    #: a constraint metric must say whether the constraint could fire at all
    reachable: Optional[bool] = None
    #: a comparison metric must say how exposure was handled
    exposure_note: Optional[str] = None
    notes: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.numerator = D(str(self.numerator))
        if self.denominator is not None:
            self.denominator = D(str(self.denominator))
        if self.independent is not None:
            self.independent = D(str(self.independent))
        if self.unit_numerator not in UNITS:
            raise ContractViolation(
                "%s: unit_numerator %r is not in the closed vocabulary %s"
                % (self.name, self.unit_numerator, sorted(UNITS)))
        if self.denominator is not None and self.unit_denominator not in UNITS:
            raise ContractViolation(
                "%s: a ratio must name the unit of its denominator; %r is not "
                "in the vocabulary" % (self.name, self.unit_denominator))
        if self.denominator is None and self.unit_denominator is not None:
            raise ContractViolation(
                "%s: unit_denominator given without a denominator"
                % self.name)
        if self.evidence not in EVIDENCE:
            raise ContractViolation("%s: evidence %r not in %s"
                                    % (self.name, self.evidence, EVIDENCE))
        if self.decoder not in DECODER:
            raise ContractViolation("%s: decoder %r not in %s"
                                    % (self.name, self.decoder, DECODER))
        if not self.source or not self.period or not self.lane:
            raise ContractViolation(
                "%s: source, period and lane are all required" % self.name)

    @property
    def value(self) -> Optional[D]:
        if self.denominator is None:
            return self.numerator
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    @property
    def agreement(self) -> str:
        if self.independent is None:
            return NOT_VERIFIED
        a, b = self.value, self.independent
        if a is None:
            return DISAGREEMENT if b is not None else AGREE
        #: exact where both are exact; a ratio is compared to 1e-9 because two
        #: correct implementations can divide in a different order.
        if a == b:
            return AGREE
        try:
            if abs(a - b) <= D("1e-9") * max(abs(a), abs(b), D(1)):
                return AGREE
        except Exception:                                      # noqa: BLE001
            pass
        return DISAGREEMENT

    @property
    def provisional(self) -> bool:
        """Any side- or fill-dependent number is provisional until the decoder
        gate resolves. This is a property, not a choice a reporter makes."""
        return self.decoder in (DEC_SIDE, DEC_FILL)

    def unit_phrase(self) -> str:
        if self.denominator is None:
            return self.unit_numerator
        return "%s per %s" % (self.unit_numerator, self.unit_denominator)

    def line(self) -> str:
        v = self.value
        vs = "n/a" if v is None else ("%.6g" % float(v))
        flag = " [PROVISIONAL:%s]" % self.decoder if self.provisional else ""
        return ("%-38s %14s  %s = %s/%s  %s  %s  %s%s"
                % (self.name, vs, self.unit_phrase(), self.numerator,
                   "-" if self.denominator is None else self.denominator,
                   self.lane, self.evidence, self.agreement, flag))

    def row(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": None if self.value is None else str(self.value),
            "numerator": str(self.numerator),
            "denominator": (None if self.denominator is None
                            else str(self.denominator)),
            "unit": self.unit_phrase(),
            "source": self.source, "period": self.period, "lane": self.lane,
            "evidence": self.evidence, "decoder": self.decoder,
            "provisional": self.provisional,
            "independent": (None if self.independent is None
                            else str(self.independent)),
            "agreement": self.agreement,
            "reachable": self.reachable,
            "exposure_note": self.exposure_note,
            "notes": self.notes,
        }
