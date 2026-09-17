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

    def signed_qty(self) -> D:
        return D(self.qty) if self.side == "BUY" else -D(self.qty)

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

    def reconstructed(self) -> Optional[D]:
        """What the legs and the settlement value SAY the cash should be.

        A cross-check on `realized`, never a substitute: it is arithmetic on
        our records, and the venue's cash is the fact."""
        if not self.settlement_value:
            return None
        return (self.fills_cash()
                + self.position() * D(str(self.settlement_value))
                - self.fees())

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
