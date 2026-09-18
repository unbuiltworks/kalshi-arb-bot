"""Authoritative BTC5m contract metadata, from the prediction-markets API.

WHY NOT /v1/symbols/details. That endpoint reports product_type "Unsupported
Instrument" for these contracts -- it does not model prediction markets at all
-- and its min_order_size describes the SPOT schema. Canary #1 was built on
that number. It happened to be right, which is worse than being wrong: a value
read from an endpoint that disclaims the instrument is not evidence, whatever
it says.

THE AUTHORITATIVE SOURCE is the contract object on

    GET /v1/prediction-markets/events/{eventTicker}

which carries quantityMinimum, quantityIncrement, priceMinimum,
priceIncrement, status and marketState -- all as STRINGS. It is a PUBLIC read;
no credential is needed and none is used here.

TERMS ARE A PRECONDITION, NOT A DETAIL. Gemini requires the latest Prediction
Markets terms to be accepted before an order is placed, and returns
TERMS_NOT_ACCEPTED otherwise. GET /v1/prediction-markets/terms/status reports
hasAcceptedLatest and is AUTHENTICATED, so it belongs in the credentialed part
of the preflight.

THIS MODULE NEVER ACCEPTS TERMS. POST /v1/prediction-markets/terms/accept is a
mutation and a legal acceptance on the account's behalf; it is not something
an automated preflight may do, and nothing here can reach it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

EVENTS_PATH = "/v1/prediction-markets/events"
TERMS_STATUS_PATH = "/v1/prediction-markets/terms/status"

#: fields the owner requires established before any order
REQUIRED_FIELDS = ("quantityMinimum", "quantityIncrement",
                   "priceMinimum", "priceIncrement")


def event_ticker(symbol: str) -> Optional[str]:
    """GEMI-BTC05M2609180045-UP -> BTC05M2609180045."""
    parts = symbol.split("-")
    return parts[1] if len(parts) >= 3 else None


def sibling_ticker(ticker: str, minutes: int = 5) -> Optional[str]:
    """The ticker of an ADJACENT window, e.g. BTC05M2609180100 -> ...0055.

    Used to give a read-only probe two different values for the same field,
    which is the only way to tell "the venue honoured the field" from "the
    endpoint ignores it".
    """
    if not ticker or len(ticker) < 10:
        return None
    prefix, stamp = ticker[:-10], ticker[-10:]
    try:
        t = datetime.strptime(stamp, "%y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    from datetime import timedelta
    return prefix + (t - timedelta(minutes=minutes)).strftime("%y%m%d%H%M")


def fetch_event(ticker: str, *, timeout: float = 15.0) -> Dict[str, Any]:
    """PUBLIC read of one event and its contracts."""
    from src.l5_shadow import venues as V
    r = V.get("%s%s/%s" % (V.GEMINI, EVENTS_PATH, ticker), timeout=timeout)
    if not r.get("ok"):
        return {"readable": False, "ticker": ticker, "err": r.get("err")}
    try:
        return {"readable": True, "ticker": ticker, "event": json.loads(r["body"])}
    except ValueError:
        return {"readable": False, "ticker": ticker, "err": "unparseable"}


def contract_of(event: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    for c in (event.get("contracts") or []):
        if c.get("instrumentSymbol") == symbol:
            return c
    return None


def terms_for(symbol: str) -> Dict[str, Any]:
    """The instrument's own trading terms. Never inferred, never defaulted."""
    tk = event_ticker(symbol)
    if not tk:
        return {"readable": False, "why": "symbol carries no event ticker"}
    got = fetch_event(tk)
    if not got.get("readable"):
        return {"readable": False, "why": got.get("err"), "ticker": tk}
    ev = got["event"]
    c = contract_of(ev, symbol)
    if c is None:
        return {"readable": False, "ticker": tk,
                "why": "the event lists no contract with that instrumentSymbol",
                "contracts_present": [x.get("instrumentSymbol")
                                      for x in (ev.get("contracts") or [])]}
    missing = [f for f in REQUIRED_FIELDS if c.get(f) in (None, "")]
    return {
        "readable": not missing,
        "ticker": tk,
        "instrumentSymbol": c.get("instrumentSymbol"),
        "quantityMinimum": c.get("quantityMinimum"),
        "quantityIncrement": c.get("quantityIncrement"),
        "priceMinimum": c.get("priceMinimum"),
        "priceIncrement": c.get("priceIncrement"),
        "priceMaximum": c.get("priceMaximum"),
        "quantityMaximum": c.get("quantityMaximum"),
        "contract_status": c.get("status"),
        "market_state": c.get("marketState"),
        "event_status": ev.get("status"),
        "expiry": c.get("expiryDate"),
        "missing_fields": missing,
        "source": "%s/{eventTicker} contract object (PUBLIC)" % EVENTS_PATH,
        "why": (None if not missing
                else "the contract omits %s; nothing is defaulted" % missing),
    }


def smallest_valid_quantity(terms: Dict[str, Any]) -> Optional[str]:
    """The minimum the VENUE states, snapped up to its own increment.

    Not an assumption, not 1, not 100. If the minimum is not a whole multiple
    of the increment, the first valid size is the next multiple up.
    """
    if not terms.get("readable"):
        return None
    q_min = D(str(terms["quantityMinimum"]))
    q_inc = D(str(terms["quantityIncrement"]))
    if q_inc <= 0:
        return str(q_min)
    steps = (q_min / q_inc).to_integral_value(rounding="ROUND_CEILING")
    return str((steps * q_inc).normalize() if steps * q_inc != q_min else q_min)


def price_is_valid(price: Any, terms: Dict[str, Any]) -> Dict[str, Any]:
    """Does this price satisfy priceMinimum and priceIncrement?"""
    if not terms.get("readable"):
        return {"valid": None, "why": "terms unreadable"}
    p = D(str(price))
    p_min = D(str(terms["priceMinimum"]))
    p_inc = D(str(terms["priceIncrement"]))
    p_max = terms.get("priceMaximum")
    checks = {
        "at_or_above_minimum": p >= p_min,
        "on_increment": (p - p_min) % p_inc == 0 if p_inc > 0 else True,
        "at_or_below_maximum": (True if p_max in (None, "")
                                else p <= D(str(p_max))),
    }
    return {"valid": all(checks.values()), "checks": checks,
            "priceMinimum": str(p_min), "priceIncrement": str(p_inc)}


def quantity_is_valid(qty: Any, terms: Dict[str, Any]) -> Dict[str, Any]:
    if not terms.get("readable"):
        return {"valid": None, "why": "terms unreadable"}
    q = D(str(qty))
    q_min = D(str(terms["quantityMinimum"]))
    q_inc = D(str(terms["quantityIncrement"]))
    q_max = terms.get("quantityMaximum")
    checks = {
        "at_or_above_minimum": q >= q_min,
        "on_increment": (q - q_min) % q_inc == 0 if q_inc > 0 else True,
        "at_or_below_maximum": (True if q_max in (None, "")
                                else q <= D(str(q_max))),
    }
    return {"valid": all(checks.values()), "checks": checks,
            "quantityMinimum": str(q_min), "quantityIncrement": str(q_inc)}


def max_cash_exposure(qty: Any, price: Any) -> str:
    """Worst case for a BUY: the full limit price times the size."""
    return str((D(str(qty)) * D(str(price))).quantize(D("0.00000001")))


ACCEPTED = "ACCEPTED"
NOT_ACCEPTED = "NOT_ACCEPTED"
UNREADABLE = "UNREADABLE"

TERMS_PATH = "/v1/prediction-markets/terms"


def latest_terms_version(*, timeout: float = 15.0) -> Dict[str, Any]:
    """PUBLIC read of the current terms document. Reading is not accepting."""
    from src.l5_shadow import venues as V
    r = V.get("%s%s" % (V.GEMINI, TERMS_PATH), timeout=timeout)
    if not r.get("ok"):
        return {"readable": False, "err": r.get("err")}
    try:
        body = json.loads(r["body"])
    except ValueError:
        return {"readable": False, "err": "unparseable"}
    # `content` is the terms text itself and is not evidence of anything;
    # only its version matters here, so it is deliberately not carried.
    return {"readable": isinstance(body, dict) and "version" in body,
            "version": (body or {}).get("version"),
            "termsType": (body or {}).get("termsType"),
            "updatedAt": (body or {}).get("updatedAt")}


async def terms_status() -> Dict[str, Any]:
    """AUTHENTICATED read of terms acceptance. Never accepts anything.

    THE METHOD IS GET, per the spec, which also states the OrderStatus
    permission requirement and the response schema: hasAcceptedLatest
    (boolean, required), acceptedVersion and latestVersion (integer or null).
    The classic Gemini private convention is POST-only, and this API's own
    reads are POST, so if GET does not authenticate we ask again with POST
    rather than reporting a method mismatch as a refusal to trade. Both are
    reads; neither can accept anything.

    UNREADABLE IS NOT "NOT ACCEPTED". Both block an order, but only one of
    them is a fact about the account, and reporting a transport failure as a
    terms refusal would send the next investigation somewhere false.
    """
    from src.gemini_prediction_auth import has_credentials, private_rest_request
    if not has_credentials():
        return {"readable": False, "verdict": "NO_CREDENTIALS",
                "blocks_orders": True,
                "why": "run where the Gemini credentials live"}

    attempts = []
    for method in ("GET", "POST"):
        try:
            r = await private_rest_request(method, TERMS_STATUS_PATH,
                                           timeout=15.0)
        except Exception as exc:                               # noqa: BLE001
            attempts.append({"method": method, "status": None,
                             "err": type(exc).__name__})
            continue
        try:
            body = r.json()
        except ValueError:
            attempts.append({"method": method, "status": r.status_code,
                             "unparseable": r.text[:200]})
            continue
        accepted = None
        if isinstance(body, dict):
            for k in ("hasAcceptedLatest", "hasAccepted", "accepted"):
                if isinstance(body.get(k), bool):
                    accepted = body[k]
                    break
        attempts.append({"method": method, "status": r.status_code,
                         "body_keys": sorted(body) if isinstance(body, dict)
                                      else None})
        if accepted is None:
            continue
        return {
            "readable": True,
            "verdict": ACCEPTED if accepted else NOT_ACCEPTED,
            "method": method,
            "status": r.status_code,
            "hasAcceptedLatest": accepted,
            "acceptedVersion": body.get("acceptedVersion"),
            "latestVersion": body.get("latestVersion"),
            "blocks_orders": not accepted,
            "attempts": attempts,
            "why": (None if accepted else
                    "the account has not accepted the latest Prediction "
                    "Markets terms. The documented rejection is "
                    "TERMS_NOT_ACCEPTED, an HTTP 422. Acceptance is a legal "
                    "act on the account and is NOT performed here."),
        }

    return {"readable": False, "verdict": UNREADABLE, "blocks_orders": True,
            "hasAcceptedLatest": None, "attempts": attempts,
            "why": ("acceptance could not be read by either method. This is "
                    "NOT evidence that the terms are unaccepted -- it is the "
                    "absence of evidence either way, and it blocks an order "
                    "for that reason alone.")}
