"""What an adapter must report about ONE frozen intent, on ONE venue.

THE CONTRACT. An adapter is handed an intent it did not create and must answer,
for its own venue: can this inventory actually be acquired, at what price, with
what fee, and -- separately -- did it actually fill. The intent is never
modified. The adapter's answer is a new record keyed by `intent_id`.

THE DISTINCTION THIS TYPE ENFORCES, because it is the one that has cost money
in this program before:

    would_submit   we would have sent an order              MODELLED
    would_fill     we believe it would have filled          MODELLED
    filled         the VENUE told us it filled              EVIDENCE

`filled` may only be set from venue evidence. `would_fill` is a belief and is
never promoted to `filled` by anything this module does. A theoretical fill is
never realized P&L -- the accounting lifecycle is
shadow -> executable shadow -> submitted -> filled -> settled -> realized, and
an adapter may only ever advance a record as far as `filled`.

MAKER/TAKER IS ONLY KNOWN WHEN THE VENUE SAYS SO. `liquidity_role` stays None
unless the venue reported it. Inferring "we rested, therefore maker" is exactly
the assumption that produced unsupported rebate accounting earlier.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal as D
from typing import Any, Mapping, Optional

SCHEMA = "l5_adapter_evaluation/v1"

# how far an evaluation is allowed to advance. `settled`/`realized` belong to
# the settlement auditor, never to an adapter.
STAGES = ("no_market", "not_executable", "would_not_submit", "submitted",
          "filled")


@dataclass
class AdapterEvaluation:
    """One venue's answer about one frozen intent."""

    venue: str
    intent_id: str
    market_key: str
    evaluated_at: float

    # --- market mapping: allowed to fail, and failure is a RESULT
    venue_market_id: Optional[str] = None
    mapping_confidence: Optional[str] = None      # exact | derived | none
    mapping_reason: Optional[str] = None

    # --- what the venue was showing
    available_price: Optional[str] = None         # best price on the wanted side
    available_depth: Optional[str] = None
    spread: Optional[str] = None
    book_age_s: Optional[float] = None

    # --- economics, before any fill is assumed
    expected_fee: Optional[str] = None
    expected_fee_basis: Optional[str] = None      # how the fee was derived
    submission_price: Optional[str] = None
    effective_cost: Optional[str] = None          # price + fee, per share
    within_ceiling: Optional[bool] = None

    # --- MODELLED
    would_submit: Optional[bool] = None
    would_submit_reason: Optional[str] = None
    would_fill: Optional[bool] = None
    would_fill_basis: Optional[str] = None

    # --- EVIDENCE (venue-confirmed only)
    filled: Optional[bool] = None
    fill_evidence: Optional[dict] = None
    filled_qty: Optional[str] = None
    liquidity_role: Optional[str] = None          # maker/taker ONLY if venue said
    latency_s: Optional[float] = None

    # --- resulting position, if it did fill
    resulting_matched: Optional[str] = None
    resulting_residual: Optional[str] = None
    pair_cost: Optional[str] = None

    stage: str = "not_executable"
    notes: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}: {self.stage}")
        if self.filled and not self.fill_evidence:
            raise ValueError(
                "filled=True requires fill_evidence from the venue. A modelled "
                "fill belongs in would_fill; promoting it to `filled` is how a "
                "theoretical number becomes a reported one.")
        if self.liquidity_role is not None and not self.fill_evidence:
            raise ValueError(
                "liquidity_role may only be set from venue evidence. Inferring "
                "maker from 'we rested' is an assumption, not an observation.")

    @property
    def id(self) -> str:
        blob = json.dumps({"v": self.venue, "i": self.intent_id,
                           "t": self.evaluated_at},
                          sort_keys=True, separators=(",", ":"))
        return "ev-" + hashlib.sha256(blob.encode()).hexdigest()[:16]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = SCHEMA
        d["evaluation_id"] = self.id
        return d


def no_market(venue: str, intent, reason: str) -> AdapterEvaluation:
    """The honest answer when a venue has no equivalent market.

    This is a RESULT, not an error. 'Neither venue can do this' is a decision
    the router is required to be able to reach, and it can only reach it if
    adapters are allowed to say no.
    """
    return AdapterEvaluation(
        venue=venue, intent_id=intent.id, market_key=intent.market_key,
        evaluated_at=time.time(), mapping_confidence="none",
        mapping_reason=reason, stage="no_market", would_submit=False,
        would_submit_reason=reason)


def within_ceiling(effective_cost: Any, ceiling: Any) -> bool:
    """Is the all-in per-share cost at or under the intent's economic ceiling?

    Compared on EFFECTIVE cost (price + fee), not on the quoted price. A quote
    under the ceiling whose fee pushes it over is not executable, and comparing
    quotes is how that gets missed.
    """
    return D(str(effective_cost)) <= D(str(ceiling))
