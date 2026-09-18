"""What would have to be true before a Gemini position counts as REALIZED.

THE GATE THIS CLOSES. The Gemini surface stops at an EVIDENCED fill and
hard-codes `synthetic_pair_realized: False`, which is honest but terminal: the
lane has no path to realized at all, so no amount of shadow evidence can ever
authorize live money. This module states the path, and refuses in every place
the path is not actually walked.

WHAT THE VENUE PUBLISHES, MEASURED 2026-09-17 21:44Z. Four expired BTC5m
windows, 578s to 3878s past expiry:

  GEMI-BTC05M2609172135-UP  status=closed  book 0b/0a  19 trades  last 0.35
  GEMI-BTC05M2609172130-UP  status=closed  book 0b/0a   5 trades  last 0.97
  GEMI-BTC05M2609172120-UP  status=closed  book 0b/0a   4 trades  last 0.90
  GEMI-BTC05M2609172040-UP  status=closed  book 0b/0a   8 trades  last 0.17

An expired contract closes with an empty book and the public tape survives --
and NOWHERE does the venue publish what it settled at. The last print is the
last print; a contract whose final trade was 0.97 may still settle 0. So:

  THE PUBLIC SURFACE CANNOT ESTABLISH SETTLEMENT. Not "has not yet"; cannot.

That leaves exactly two candidate sources, and only one of them is evidence:

  the account            /v1/mytrades, /v1/balances, /v1/transfers. Authenticated,
                         authoritative, and the only thing that can close this.
  the underlying         BTC at expiry against the strike. This is a MODEL. It
                         may well be right every time and it is still not a
                         venue fact, so it is admissible as INFERRED_SETTLEMENT
                         for research and is never REALIZED.

Live BTC5m venue terms, same measurement, from /v1/symbols/details:

  tick_size 0.01, quote_increment 0.01
  min_order_size 0.01     one HUNDREDTH of a contract. The shadow's UNIT=1 is
                          100x the venue minimum, so a bounded canary can be
                          far smaller than the shadow's unit -- about half a
                          cent of notional at 0.50.
  status limit_only       the VENUE refuses anything but a limit order here.
                          Maker-first is not only our policy on this instrument.

NOTHING HERE PLACES AN ORDER, AND NOTHING HERE HOLDS A CREDENTIAL. The
authenticated reads are named, not performed: they belong where the
credentials live, and this module fails closed without them rather than
degrading to a guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_SETTLEMENT_GATE_V1"

# ---------------------------------------------------------------- the ladder

AUTHORITATIVE_FILLS = "AUTHORITATIVE_FILLS"
POSITION_AT_EXPIRY = "POSITION_AT_EXPIRY"
SETTLEMENT_VALUE = "SETTLEMENT_VALUE"
CASH_CONFIRMED = "CASH_CONFIRMED"
FEES_ACCOUNTED = "FEES_ACCOUNTED"

#: every one of them, in order. REALIZED is the conjunction, not the last one.
REQUIREMENTS = (AUTHORITATIVE_FILLS, POSITION_AT_EXPIRY, SETTLEMENT_VALUE,
                CASH_CONFIRMED, FEES_ACCOUNTED)

#: where each requirement can come from, and whether that source is public
SOURCES: Dict[str, Dict[str, Any]] = {
    AUTHORITATIVE_FILLS: {
        "endpoint": "/v1/mytrades", "authenticated": True,
        "why": ("the venue's own record of what we filled, with its own fee "
                "fields. VOLUME_AHEAD_CLEARED is our model of a queue we "
                "cannot see and is not admissible here."),
    },
    POSITION_AT_EXPIRY: {
        "endpoint": "derived from AUTHORITATIVE_FILLS", "authenticated": True,
        "why": "net of authoritative fills only; a shadow position is not one",
    },
    SETTLEMENT_VALUE: {
        "endpoint": "NOT_PUBLISHED", "authenticated": True,
        "why": ("measured 2026-09-17: an expired contract reports "
                "status=closed with an empty book and no settlement value "
                "anywhere public. Must come from the account."),
    },
    CASH_CONFIRMED: {
        "endpoint": "/v1/balances + /v1/transfers", "authenticated": True,
        "why": ("cash that actually moved, attributable to this instrument. "
                "A balance change without sufficient source identity is not "
                "attribution."),
    },
    FEES_ACCOUNTED: {
        "endpoint": "/v1/mytrades fee fields", "authenticated": True,
        "why": "fees are never netted into a price; they are their own line",
    },
}

#: kept OUT of realized trading P&L on purpose, per the standing rule that
#: nobody scales on modeled rebates
SEPARATE_LEDGERS = ("maker_rebate", "taker_rebate", "liquidity_reward")

PUBLIC_VERDICT = "PUBLIC_SURFACE_CANNOT_ESTABLISH_SETTLEMENT"


class NotSettled(Exception):
    """Raised when something asks for realized P&L the evidence cannot support."""


#: the contract field that is NOT a payout. Named so the mistake is hard to
#: make twice: the venue's `settlementValue` is the underlying index print
#: (measured 2026-09-18: "80727.9451111111" against strike "80694.84"), while
#: the payout is 1/0 decided by `resolutionSide`.
UNDERLYING_PRICE_FIELD = "settlementValue"
OUTCOME_FIELD = "resolutionSide"


def apply_resolution(st: "Settlement", res: Mapping[str, Any]) -> Dict[str, Any]:
    """Attach a venue resolution reading to a Settlement. READ-ONLY input.

    WHY THIS FUNCTION EXISTS AT ALL. Nothing ever assigned `settlement_value`
    or `settlement_source`, so the proof chain's `authoritative_outcome` link
    evaluated `bool(None and None)` -- FALSE for every symbol, always. A
    contract the venue had resolved, and whose resolution was readable on a
    PUBLIC endpoint, stayed labelled unsettled indefinitely. CONTINUOUS-4 was
    resolved at 17:30:07.369Z and still read "absent" at 17:55Z.

    Takes the shape `metadata.resolution_for` returns. A reading that is not
    SETTLED attaches nothing: absence of evidence is not evidence, and an
    endpoint that flaps between settled and active must never be recorded as
    having un-settled a contract.
    """
    if not isinstance(res, Mapping):
        return {"applied": False, "why": "no resolution reading supplied"}
    if res.get("verdict") != "SETTLED":
        return {"applied": False, "verdict": res.get("verdict"),
                "why": ("the venue has not been observed to resolve this "
                        "contract; UNCONFIRMED is the absence of evidence, "
                        "never a claim that it did not settle")}
    side = res.get(OUTCOME_FIELD)
    if side not in ("yes", "no"):
        return {"applied": False,
                "why": "resolutionSide is %r, which names no side" % (side,)}

    st.resolution_side = str(side)
    st.resolved_at = res.get("resolvedAt")
    st.underlying_settlement_price = res.get(UNDERLYING_PRICE_FIELD)
    # the YES side's payout, which is what `settlement_value` has always meant
    st.settlement_value = "1" if side == "yes" else "0"
    st.settlement_source = res.get("source") or "contract object"
    return {"applied": True, "resolution_side": st.resolution_side,
            "resolved_at": st.resolved_at,
            "settlement_value_yes_side": st.settlement_value,
            "underlying_settlement_price": st.underlying_settlement_price,
            "strike_check": res.get("strike_check"),
            "labels": ("settlement_value is the 1/0 PAYOUT for the YES side, "
                       "derived from resolutionSide. underlying_settlement_"
                       "price is the index print and is never a payout.")}


# ------------------------------------------------------------------ evidence

@dataclass
class Leg:
    """One authoritative fill. Every field comes from the venue, not from us."""

    side: str                  # BUY or SELL
    qty: str
    price: str
    fee: str
    source: str                # the endpoint that produced it
    trade_id: Optional[str] = None
    #: WHICH SIDE OF THE CONTRACT. "yes" or "no", lowercased as the venue
    #: writes it, or None when the row did not say. Without it a YES leg and a
    #: NO leg are indistinguishable, and a two-sided position reconstructs as
    #: though EVERY unit paid out -- see `payout_per_unit`.
    outcome: Optional[str] = None

    def signed_qty(self) -> D:
        return D(self.qty) if self.side == "BUY" else -D(self.qty)

    def payout_per_unit(self, resolution_side: str) -> Optional[D]:
        """What one unit of THIS leg redeems for, given the venue's outcome.

        A binary contract pays 1 to the side that won and 0 to the other. Our
        CONTINUOUS-4 position was YES 0.01 and NO 0.01 on the same contract:
        the UP contract resolved `resolutionSide: "yes"`, so the YES unit
        redeemed 1 and the NO unit redeemed 0, for a payout of 0.0100 against
        a cost of 0.0106.

        Summing the position first and multiplying by one settlement value
        instead would have paid out BOTH units -- +0.0094, a phantom profit
        with the sign inverted. None here means the row did not name its
        outcome, which is UNREADABLE and must stop a reconstruction rather
        than default to either side.
        """
        if not self.outcome or not resolution_side:
            return None
        return D("1") if str(self.outcome).lower() == str(
            resolution_side).lower() else D("0")

    def cash(self) -> D:
        """Cash out at a buy, in at a sell. Fees are NOT folded in here."""
        q, p = D(self.qty), D(self.price)
        return -(q * p) if self.side == "BUY" else (q * p)


@dataclass
class Settlement:
    """The evidence assembled for one instrument, and what is still missing."""

    symbol: str
    legs: List[Leg] = field(default_factory=list)
    settlement_value: Optional[str] = None       # "0" or "1", venue-sourced
    settlement_source: Optional[str] = None
    cash_delta_observed: Optional[str] = None    # from balances/transfers
    cash_delta_source: Optional[str] = None
    inferred_settlement_value: Optional[str] = None   # from the underlying
    notes: Optional[str] = None
    #: the venue's own `resolutionSide` -- "yes" or "no". THE PAYOUT COMES
    #: FROM THIS, never from the contract's `settlementValue` field, which is
    #: the UNDERLYING INDEX PRICE (measured 2026-09-18: "80727.9451111111"
    #: against a strike of "80694.84"). Multiplying a position by that number
    #: would misprice a $0.01 position by five orders of magnitude.
    resolution_side: Optional[str] = None
    resolved_at: Optional[str] = None
    #: the index print the venue resolved against, kept for audit and NEVER
    #: used as a payout
    underlying_settlement_price: Optional[str] = None

    # ------------------------------------------------------------ positions

    def position(self) -> D:
        return sum((l.signed_qty() for l in self.legs), D("0"))

    def fills_cash(self) -> D:
        return sum((l.cash() for l in self.legs), D("0"))

    def fees(self) -> D:
        return sum((D(l.fee) for l in self.legs), D("0"))

    # ------------------------------------------------------------ the gate

    def satisfied(self) -> Dict[str, bool]:
        have_fills = bool(self.legs) and all(
            l.source and l.source != "SHADOW" for l in self.legs)
        return {
            AUTHORITATIVE_FILLS: have_fills,
            POSITION_AT_EXPIRY: have_fills,
            SETTLEMENT_VALUE: bool(self.settlement_value
                                   and self.settlement_source),
            CASH_CONFIRMED: bool(self.cash_delta_observed
                                 and self.cash_delta_source),
            FEES_ACCOUNTED: have_fills and all(
                l.fee is not None for l in self.legs),
        }

    def missing(self) -> List[str]:
        s = self.satisfied()
        return [r for r in REQUIREMENTS if not s[r]]

    def realized(self) -> D:
        """Cash actually realized, or an exception. There is no third option.

        The number is the VENUE's cash movement, not our reconstruction of it:
        a reconstruction that agrees is a check, and one that disagrees means
        the venue is right. Reconstruction is reported separately by `report`
        so the two can be compared without either being mistaken for the other.
        """
        gaps = self.missing()
        if gaps:
            raise NotSettled(
                "%s is not settled: %s. Realized is a venue cash fact and "
                "this is not one." % (self.symbol, ", ".join(gaps)))
        return D(str(self.cash_delta_observed))

    def payout(self) -> Optional[D]:
        """What the legs redeem for, PER LEG, by the side each one is on.

        Returns None when any leg cannot say which side it is on, because a
        two-sided position whose sides are unknown cannot be valued at all and
        guessing one is worse than reporting nothing.
        """
        if not self.resolution_side:
            return None
        total = D("0")
        for l in self.legs:
            per = l.payout_per_unit(self.resolution_side)
            if per is None:
                return None
            total += l.signed_qty() * per
        return total

    def reconstructed(self) -> Optional[D]:
        """What the legs and the venue's OUTCOME say the cash should be.

        A cross-check on `realized`, never a substitute: it is arithmetic on
        our records, and the venue's cash is the fact.

        THIS USED TO MULTIPLY THE WHOLE POSITION BY ONE SETTLEMENT VALUE. For
        a one-sided position that happens to be right. For CONTINUOUS-4's
        YES 0.01 + NO 0.01 it pays out both units -- +0.0094 instead of
        -0.0006, a phantom profit with the sign inverted. The payout is now
        taken leg by leg from the side each leg is on.
        """
        pay = self.payout()
        if pay is not None:
            return self.fills_cash() + pay - self.fees()
        # A NET-ZERO POSITION REDEEMS NOTHING WHICHEVER SIDE WON, so the
        # settlement term vanishes and no per-leg outcome is needed. This is
        # the ONLY case where the old whole-position arithmetic was provably
        # right, and it stays supported so a closed round trip still
        # reconstructs without labelled legs.
        if self.settlement_value is not None and self.position() == D("0"):
            return self.fills_cash() - self.fees()
        # An OPEN position whose legs cannot say which side they are on is
        # exactly the CONTINUOUS-4 case. Returning a number here is what
        # produced +0.0094 instead of -0.0006.
        return None

    def report(self) -> Dict[str, Any]:
        gaps = self.missing()
        rec = self.reconstructed()
        out: Dict[str, Any] = {
            "label": LABEL, "symbol": self.symbol,
            "n_legs": len(self.legs),
            "position_at_expiry": str(self.position()),
            "fills_cash": str(self.fills_cash()),
            "fees": str(self.fees()),
            "settlement_value": self.settlement_value,
            "settlement_source": self.settlement_source,
            "inferred_settlement_value": self.inferred_settlement_value,
            # the venue's OUTCOME evidence, surfaced so a resolved contract
            # cannot silently read as unsettled in an artifact again
            "resolution_side": self.resolution_side,
            "resolved_at": self.resolved_at,
            "underlying_settlement_price": self.underlying_settlement_price,
            "payout": (str(self.payout()) if self.payout() is not None
                       else None),
            "satisfied": self.satisfied(),
            "missing": gaps,
            "gate": "BLOCKED" if gaps else "OPEN",
            "reconstructed_cash": (str(rec) if rec is not None else None),
            "separate_ledgers": list(SEPARATE_LEDGERS),
            "labels": [
                "REALIZED is the venue's cash movement. reconstructed_cash is "
                "arithmetic on our own records and is a cross-check only.",
                "inferred_settlement_value comes from the underlying and is a "
                "MODEL. It is never realized, whatever it agrees with.",
                "rebates and rewards are separate ledgers and never enter "
                "trading P&L until actually credited and source-accounted.",
            ],
        }
        if not gaps:
            realized = self.realized()
            out["realized"] = str(realized)
            if rec is not None:
                out["reconstruction_delta"] = str(realized - rec)
                out["reconstruction_agrees"] = (realized == rec)
        return out


# -------------------------------------------------------------- the verdict

def public_surface_verdict() -> Dict[str, Any]:
    """Why this gate cannot be closed from public data, with the measurement.

    Stated as a finding rather than a TODO, because the difference decides
    whether anyone keeps looking for a public endpoint that does not exist.
    """
    return {
        "label": LABEL, "verdict": PUBLIC_VERDICT,
        "measured_utc": "2026-09-17T21:44Z",
        "evidence": [
            {"symbol": "GEMI-BTC05M2609172135-UP", "expired_s": 578,
             "status": "closed", "book": "0 bids / 0 asks", "trades": 19,
             "last_price": "0.35"},
            {"symbol": "GEMI-BTC05M2609172130-UP", "expired_s": 878,
             "status": "closed", "book": "0 bids / 0 asks", "trades": 5,
             "last_price": "0.97"},
            {"symbol": "GEMI-BTC05M2609172120-UP", "expired_s": 1478,
             "status": "closed", "book": "0 bids / 0 asks", "trades": 4,
             "last_price": "0.90"},
            {"symbol": "GEMI-BTC05M2609172040-UP", "expired_s": 3878,
             "status": "closed", "book": "0 bids / 0 asks", "trades": 8,
             "last_price": "0.17"},
        ],
        "why": ("an expired contract closes with an empty book and the public "
                "tape survives, but the venue publishes no settlement value. "
                "A contract whose last print was 0.97 may still settle 0, so "
                "the last print is not a terminal value."),
        "closes_with": {r: SOURCES[r]["endpoint"] for r in REQUIREMENTS},
        "status": "NOT_EXERCISED",
        "not_exercised_why": (
            "every remaining source is authenticated. This module names them "
            "and holds no credential; the reads belong where the credentials "
            "already live. Proving the structure is not the same as proving "
            "the path, and is not reported as PASS."),
    }


def venue_terms() -> Dict[str, Any]:
    """BTC5m terms read from the live venue, not assumed."""
    return {
        "label": LABEL, "measured_utc": "2026-09-17T21:44Z",
        "source": "/v1/symbols/details/GEMI-BTC05M<expiry>-UP",
        "tick_size": "0.01", "quote_increment": "0.01",
        "min_order_size": "0.01",
        "status": "limit_only",
        "notes": [
            "min_order_size is one HUNDREDTH of a contract: the shadow's "
            "UNIT=1 is 100x the venue minimum, so a bounded canary can be far "
            "smaller than the shadow's unit.",
            "limit_only means the VENUE rejects anything but a limit order on "
            "this instrument; maker-first here is not only our policy.",
            "product_type reads 'Unsupported Instrument' -- the details "
            "endpoint does not model prediction contracts, which is also why "
            "it carries no settlement metadata.",
        ],
    }


def gate(settlements: Sequence[Settlement]) -> Dict[str, Any]:
    """OPEN only if every instrument is fully evidenced. Any gap blocks."""
    reports = [s.report() for s in settlements]
    blocked = [r for r in reports if r["gate"] == "BLOCKED"]
    return {
        "label": LABEL,
        "gate": "BLOCKED" if (blocked or not reports) else "OPEN",
        "n": len(reports), "n_blocked": len(blocked),
        "missing_union": sorted({m for r in blocked for m in r["missing"]}),
        "public_surface": public_surface_verdict()["verdict"],
        "instruments": reports,
        "authorizes": ("nothing on its own. An OPEN gate means settlement "
                       "accounting CAN close, not that a probe is approved. "
                       "Live authorization remains a separate human decision."),
    }
