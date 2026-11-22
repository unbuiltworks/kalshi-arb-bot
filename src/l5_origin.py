"""Whose is it? One place that answers, for orders, positions and settled
results on a venue account more than one thing trades on.

Owner decision, 2026-11-11:

    "Label every position and fill as BTC15m, BTC5m, manual, or unknown.
     Only authenticated, reconciled fills should establish cost basis.
     Unknown positions must remain conservatively included in account-wide
     risk."

    "Neither strategy should adopt, cancel, or settle another strategy's
     orders."

    "Introduce separate strategy-level realized P&L reporting, but do not
     use that separation to circumvent the account's lifetime limit."

PURE. No I/O beyond reading one environment variable, no network, no
credentials, no ledger writes. It classifies; it never moves a number. The
account-wide totals are computed where they always were and are returned
here untouched beside the per-origin breakdown, so a label can never make a
loss leave the account-wide figure.

THE LABELS, AND WHAT EACH REQUIRES
  BTC15M   created by the 15-minute executor's own orders (`l5f-` ids)
  BTC5M    created by the 5-minute adapter's own orders (`b5m-` ids)
  SPORTS   the sports controller's (owner 2026-11-22: "The BTC service must
           not adopt or settle positions belonging to the sports
           controller. Every fill and position needs durable ownership
           attribution, with ambiguous positions handled conservatively
           rather than silently assigned."). Its orders carry NO client-id
           prefix, so nothing is ever labelled SPORTS from an id or from a
           market's name: the label is written only on a settled market
           whose every venue fill is an order in the sports controller's
           own durable ledger, order by order (the sibling-ledger form).
           Until then a sports position is UNKNOWN, at the $1 bound
  MANUAL   the OWNER has attested this market was traded by hand. An
           attestation is a label; it is not a cost basis
  UNKNOWN  everything else. Never guessed into one of the other three

AN ORDER'S ORIGIN IS ITS CLIENT ORDER ID PREFIX, and the prefixes are a
code registry, not a variable: a strategy cannot be made to adopt another's
order by leaving a setting unset.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

BTC15M, BTC5M, MANUAL, UNKNOWN = "BTC15M", "BTC5M", "MANUAL", "UNKNOWN"
SPORTS = "SPORTS"
ORIGINS = (BTC15M, BTC5M, SPORTS, MANUAL, UNKNOWN)

#: the client-order-id prefix each automated strategy writes. THE registry.
STRATEGY_COID_PREFIX = {BTC15M: "l5f-", BTC5M: "b5m-"}
#: the market series each automated strategy trades
STRATEGY_SERIES = {BTC15M: "cpc-btc-updown-15m-", BTC5M: "cpc-btc-updown-5m-"}
#: A SEPARATE, NARROWER REGISTRY: other controllers on this account whose
#: settled markets may be restated from THEIR OWN order ledger. It is not
#: `STRATEGY_SERIES` on purpose. That registry is the approval layer's, and
#: it also decides which markets' uncertainty may be treated as "only
#: another strategy's" and which orders are foreign by name -- none of which
#: this changes for a sports market. The families are the venue's two
#: sports ones: `aec-` two-outcome events, `atc-` one outcome of a
#: three-outcome event. A family is where that controller trades; it is not
#: proof of who holds a position there, and nothing is restated on it alone.
LEDGER_OWNER_SERIES = {SPORTS: ("aec-", "atc-")}
#: evidence written on a settled row whose fills another strategy's own
#: ledger accounts for, order by order
EVIDENCE_SIBLING_LEDGER = "EVERY_VENUE_FILL_IS_AN_ORDER_IN_THAT_STRATEGYS_LEDGER"

#: cost-basis kinds, strongest first. Only the first is a measured cost.
BASIS_AUTHENTICATED = "AUTHENTICATED_FILLS"
BASIS_OWN_UNVERIFIED = "OWN_FILLS_ATTRIBUTION_UNVERIFIED"
BASIS_UPPER_BOUND = "UPPER_BOUND_1USD_PER_CONTRACT"

#: markets the OWNER attests were traded by hand: comma-separated slugs in a
#: variable only the owner sets. A label only -- it changes no number.
MANUAL_ATTESTED_ENV = "L5_MANUAL_ATTESTED_MARKETS"
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{4,118}[a-z0-9]")


def origin_of_coid(coid: Any) -> str:
    """BTC15M or BTC5M for an id carrying that strategy's prefix, else
    UNKNOWN. An id is never MANUAL: a hand-placed order has no prefix of
    ours, and what it lacks proves nothing about who placed it."""
    c = str(coid or "")
    for origin, prefix in STRATEGY_COID_PREFIX.items():
        if c.startswith(prefix):
            return origin
    return UNKNOWN


def other_strategy_prefixes(own: str) -> Tuple[str, ...]:
    """The prefixes of every registered strategy except `own`."""
    return tuple(p for o, p in STRATEGY_COID_PREFIX.items() if o != own)


def strategy_of_market(market: Any) -> Optional[str]:
    """Which strategy's SERIES this slug is in, or None. A series is where a
    strategy trades; it is not proof of who holds a position there."""
    m = str(market or "")
    for origin, series in STRATEGY_SERIES.items():
        if m.startswith(series):
            return origin
    return None


def ledger_owner_of_market(market: Any) -> Optional[str]:
    """Whose ORDER LEDGER could account for fills in this market: the
    registered strategy whose series it is in, else the controller whose
    family it is in, else None. Used only by the cost attestations."""
    got = strategy_of_market(market)
    if got is not None:
        return got
    m = str(market or "")
    for origin, families in LEDGER_OWNER_SERIES.items():
        if m.startswith(tuple(families)):
            return origin
    return None


def manual_attested_markets() -> Tuple[str, ...]:
    out: List[str] = []
    for tok in (os.getenv(MANUAL_ATTESTED_ENV) or "").split(","):
        tok = tok.strip()
        if _SLUG_RE.fullmatch(tok) and tok not in out:
            out.append(tok)
    return tuple(out)


def origin_of_position(market: Any, *, own: str,
                       booked_by_own_fills: bool,
                       other_strategy_order_seen: Optional[str] = None,
                       attested_manual: Optional[Iterable[str]] = None
                       ) -> Dict[str, Any]:
    """The origin label and the cost-basis kind for quantity held in
    `market`, as seen from strategy `own`'s ledger.

    `booked_by_own_fills`        the quantity was booked from this
                                 ledger's own order fills, at their prices
    `other_strategy_order_seen`  the origin of another registered
                                 strategy's order the VENUE listed in this
                                 market (its client id carried that
                                 strategy's prefix), or None

    Anything not booked by this ledger's own fills is held at the upper
    bound: this ledger did not see its price, and a label does not supply
    one.
    """
    m = str(market or "")
    if booked_by_own_fills:
        return {"origin": own, "cost_basis": BASIS_AUTHENTICATED,
                "evidence": "BOOKED_FROM_OWN_ORDER_FILLS"}
    manual = tuple(attested_manual) if attested_manual is not None \
        else manual_attested_markets()
    if m and m in manual:
        return {"origin": MANUAL, "cost_basis": BASIS_UPPER_BOUND,
                "evidence": "OWNER_ATTESTED_MARKET"}
    if other_strategy_order_seen in STRATEGY_COID_PREFIX \
            and other_strategy_order_seen != own \
            and strategy_of_market(m) == other_strategy_order_seen:
        return {"origin": other_strategy_order_seen,
                "cost_basis": BASIS_UPPER_BOUND,
                "evidence": "THAT_STRATEGYS_ORDER_LISTED_BY_VENUE_IN_MARKET"}
    return {"origin": UNKNOWN, "cost_basis": BASIS_UPPER_BOUND,
            "evidence": "NOT_BOOKED_BY_THIS_LEDGER"}


def origin_of_settlement(market: Any, *, own: str, verified_own: bool,
                         unknown_basis: bool,
                         other_strategy_order_seen: Optional[str] = None,
                         attested_manual: Optional[Iterable[str]] = None
                         ) -> Dict[str, Any]:
    """The label for one settled market's result in `own`'s ledger.

    `verified_own`    every share was booked from this ledger's own fills
                      and their attribution was verified
    `unknown_basis`   at least one leg was held at the upper bound
    """
    if verified_own and not unknown_basis:
        return {"origin": own, "cost_basis": BASIS_AUTHENTICATED,
                "evidence": "VERIFIED_OWN_FILLS"}
    if not unknown_basis:
        # booked from this ledger's own orders, attribution not verified
        return {"origin": own, "cost_basis": BASIS_OWN_UNVERIFIED,
                "evidence": "OWN_ORDER_FILLS_ATTRIBUTION_NOT_VERIFIED"}
    return origin_of_position(
        market, own=own, booked_by_own_fills=False,
        other_strategy_order_seen=other_strategy_order_seen,
        attested_manual=attested_manual)


def _dec(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = v if isinstance(v, D) else D(str(v).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def realized_by_origin(retired: Any, *, own: str,
                       seen: Optional[Mapping[str, Any]] = None,
                       attested_manual: Optional[Iterable[str]] = None
                       ) -> Dict[str, Any]:
    """Settled results split by origin, beside their own untouched sum.

    `retired` is the ledger's `retired_markets` mapping. A row that carries
    an `origin` written at settlement keeps it; an older row is classified
    now from what it recorded (`l5_owner_attribution_verified`,
    `unverified_cost_basis_keys`). A row whose result cannot be read is
    COUNTED under `unreadable` and makes `complete` False -- it is never
    read as zero.

    `sum_of_rows_usd` is the plain sum of every readable row, whatever its
    label. The per-origin figures always add up to it: a label moves a
    result between columns of this report and nowhere else.
    """
    by: Dict[str, Dict[str, Any]] = {
        o: {"settled_usd": D(0), "markets": 0, "cost_basis": {}}
        for o in ORIGINS}
    unreadable: List[str] = []
    total = D(0)
    rows = retired.items() if isinstance(retired, Mapping) else ()
    for market, row in rows:
        if not isinstance(row, Mapping):
            unreadable.append(str(market))
            continue
        pnl = _dec(row.get("settlement_pnl"))
        if pnl is None:
            unreadable.append(str(market))
            continue
        label = row.get("origin")
        basis = row.get("origin_cost_basis")
        if label not in ORIGINS or not basis:
            got = origin_of_settlement(
                market, own=own,
                verified_own=bool(row.get("l5_owner_attribution_verified")),
                unknown_basis=bool(row.get("unverified_cost_basis_keys")),
                other_strategy_order_seen=(
                    (seen or {}).get(str(market)) if isinstance(
                        seen, Mapping) else None),
                attested_manual=attested_manual)
            label, basis = got["origin"], got["cost_basis"]
        slot = by[label]
        slot["settled_usd"] += pnl
        slot["markets"] += 1
        slot["cost_basis"][basis] = slot["cost_basis"].get(basis, 0) + 1
        total += pnl
    out = {o: {"settled_usd": str(v["settled_usd"]), "markets": v["markets"],
               "cost_basis": dict(v["cost_basis"])} for o, v in by.items()}
    return {
        "by_origin": out,
        "sum_of_rows_usd": str(total),
        "rows": sum(v["markets"] for v in by.values()),
        "unreadable": sorted(unreadable),
        "complete": not unreadable,
        "labels": (
            "settled results of the markets this ledger still holds rows "
            "for, split by who created the position. Only AUTHENTICATED_"
            "FILLS is a measured cost; UPPER_BOUND rows were booked at $1 "
            "a contract because this ledger never saw their price, so "
            "their figure is a bound and not a result. The split is a "
            "report: the account-wide loss limits read the account-wide "
            "totals, which include every row here whatever its label."),
    }
