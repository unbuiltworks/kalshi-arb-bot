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
from typing import Any, Dict, List, Mapping, Optional

from src.gemini_execution import spec

EVENTS_PATH = "/v1/prediction-markets/events"
BOOK_PATH = "/v1/book"
TERMS_STATUS_PATH = "/v1/prediction-markets/terms/status"

#: fields the owner requires established before any order
REQUIRED_FIELDS = ("quantityMinimum", "quantityIncrement",
                   "priceMinimum", "priceIncrement")


def event_ticker(symbol: str) -> Optional[str]:
    """GEMI-BTC05M2609180045-UP -> BTC05M2609180045."""
    parts = symbol.split("-")
    return parts[1] if len(parts) >= 3 else None


BTC5M_PREFIX = "BTC05M"


def current_ticker(now: Optional[float] = None, *, prefix: str = BTC5M_PREFIX,
                   minutes: int = 5) -> str:
    """The ticker of the window currently running, from the clock alone.

    PARAMETERISED BY GRID, not copied per family: the 5- and 15-minute
    contracts share one ceil-to-the-grid rule and differ only in the prefix
    and the step.

    The transport diagnostic must not depend on a window being ELIGIBLE to
    trade: eligibility is a narrow band inside each cycle, so tying the
    diagnostic to it means the question goes unanswered on most runs -- which
    is exactly what happened on the first attempt.
    """
    import math
    import time as _t
    t = datetime.fromtimestamp(now if now is not None else _t.time(),
                               tz=timezone.utc)
    from datetime import timedelta
    step = int(minutes)
    minute = int(math.ceil((t.minute + t.second / 60.0) / step) * step)
    end = t.replace(minute=0, second=0, microsecond=0) + timedelta(
        minutes=minute)
    return prefix + end.strftime("%y%m%d%H%M")


def current_btc5m_ticker(now: Optional[float] = None) -> str:
    """The 5-minute family's current ticker. Unchanged behaviour."""
    return current_ticker(now, prefix=BTC5M_PREFIX, minutes=5)


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
        # THE STRIKE, published on the contract and previously reachable only
        # through _strike_check. Distance from it is the single most obvious
        # causal driver of which side of a prediction book goes toxic, and it
        # was not being surfaced. The settlement rule is the contract's own:
        # UP resolves yes when settlementValue >= strike.value.
        "strike": ((c.get("strike") or {}).get("value")
                   if isinstance(c.get("strike"), Mapping) else None),
        "missing_fields": missing,
        "source": "%s/{eventTicker} contract object (PUBLIC)" % EVENTS_PATH,
        "why": (None if not missing
                else "the contract omits %s; nothing is defaulted" % missing),
    }


#: a settlement is irreversible, so N reads are taken and any ONE settled
#: reading wins over any number of `active` ones.
RESOLUTION_READS = 3

SETTLED = "SETTLED"
PENDING = "PENDING"
UNCONFIRMED = "UNCONFIRMED"
RESOLUTION_UNREADABLE = "UNREADABLE"


def resolution_for(symbol: str, *, reads: int = RESOLUTION_READS,
                   now: Optional[float] = None) -> Dict[str, Any]:
    """Terminal resolution, from the authoritative CONTRACT object. PUBLIC.

    CORRECTING AN EARLIER FINDING. The settlement gate recorded that this
    venue "publishes NO terminal value anywhere public", measured across four
    expired BTC5m windows. That was measured on the BOOK and the print tape,
    which do go empty at expiry -- and the conclusion was drawn about the
    venue rather than about where I had looked. The contract object carries
    status, resolutionSide, resolvedAt and settlementValue, and it carried
    them for all four of those same windows.

    THIS ENDPOINT FLAPS. Measured 2026-09-18: GEMI-BTC05M2609180100-UP,
    polled ten times over twenty seconds, answered settled/yes/76525.56 three
    times and then active/null/null seven times -- for a contract whose
    expiry was fifteen minutes in the past. So a single `active` reading is
    NOT evidence that a contract has not settled; it is a stale read, and
    reporting it as unsettled would be the unreadable-versus-empty mistake in
    a new place.

    The rule that follows: a settlement is irreversible, so N reads are taken
    and ONE settled reading wins over any number of `active` ones. An expired
    contract that never reads settled is UNCONFIRMED, which is the absence of
    the evidence, never a claim that it did not settle.

    A RESOLUTION IS NOT A REALIZED P&L. This is the OUTCOME leg. The cash leg
    still requires the authenticated account surface, and nothing here may be
    labelled realized.
    """
    tk = event_ticker(symbol)
    if not tk:
        return {"readable": False, "verdict": RESOLUTION_UNREADABLE,
                "why": "symbol carries no event ticker"}

    observations: List[Dict[str, Any]] = []
    for _ in range(max(1, reads)):
        got = fetch_event(tk)
        if not got.get("readable"):
            observations.append({"readable": False, "why": got.get("err")})
            continue
        ev = got["event"]
        c = contract_of(ev, symbol)
        if c is None:
            observations.append({"readable": False,
                                 "why": "no contract with that symbol"})
            continue
        observations.append({
            "readable": True,
            "status": c.get("status"),
            "resolutionSide": c.get("resolutionSide"),
            "resolvedAt": c.get("resolvedAt"),
            "settlementValue": c.get("settlementValue"),
            "marketState": c.get("marketState"),
            "expiryDate": c.get("expiryDate"),
            "event_status": ev.get("status"),
            "_contract": c,
        })

    readable = [o for o in observations if o.get("readable")]
    if not readable:
        return {"readable": False, "verdict": RESOLUTION_UNREADABLE,
                "instrumentSymbol": symbol, "ticker": tk, "reads": len(
                    observations),
                "why": "no read of the contract object succeeded"}

    settled = [o for o in readable if o.get("status") == spec.STATUS_SETTLED
               and o.get("resolutionSide") is not None
               and o.get("resolvedAt") is not None]
    chosen = settled[0] if settled else readable[0]
    contract = chosen.pop("_contract", {})
    for o in readable:
        o.pop("_contract", None)

    expiry = chosen.get("expiryDate")
    expired = _is_past(expiry, now)
    if settled:
        verdict = SETTLED
    elif expired is True:
        verdict = UNCONFIRMED
    else:
        verdict = PENDING

    distinct = sorted({"%s/%s" % (o.get("status"), o.get("resolutionSide"))
                       for o in readable})
    out: Dict[str, Any] = {
        "readable": True,
        "verdict": verdict,
        "instrumentSymbol": symbol,
        "ticker": tk,
        "status": chosen.get("status"),
        "resolutionSide": chosen.get("resolutionSide"),
        "resolvedAt": chosen.get("resolvedAt"),
        "settlementValue": chosen.get("settlementValue"),
        "marketState": chosen.get("marketState"),
        "expiryDate": expiry,
        "event_status": chosen.get("event_status"),
        "is_terminal": verdict == SETTLED,
        "expired": expired,
        "reads": len(observations),
        "reads_readable": len(readable),
        "reads_settled": len(settled),
        "distinct_readings": distinct,
        "flapped": len(distinct) > 1,
        "observations": readable,
        "missing_fields": [f for f in spec.RESOLUTION_FIELDS
                           if chosen.get(f) in (None, "")],
        "strike_check": _strike_check(contract),
        "source": "%s/{eventTicker} contract object (PUBLIC)" % EVENTS_PATH,
        "labels": (
            "the OUTCOME leg only. A settled contract says what it resolved "
            "to, never what our account received; realized P&L still "
            "requires authenticated cash reconciliation. UNCONFIRMED means "
            "no read showed a settlement, NOT that none occurred."),
    }
    return out


def _is_past(iso: Any, now: Optional[float] = None) -> Optional[bool]:
    """None means UNKNOWN -- never False by default."""
    if not iso:
        return None
    import time as _t
    txt = str(iso).replace("Z", "+00:00")
    try:
        t = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp() < (now if now is not None else _t.time())


def most_recent_settled(symbol: str, *, look_back: int = 4,
                        reads: int = RESOLUTION_READS,
                        now: Optional[float] = None) -> Dict[str, Any]:
    """Walk back from this instrument's window until one reads SETTLED.

    A CAPABILITY check: can the four terminal fields be read at all, right
    now, for a contract that has certainly settled. One window is not enough
    to answer that, because this endpoint flaps and because the window that
    just expired may genuinely not be settled yet -- both would make a
    single-window check fail for reasons that say nothing about the
    capability.
    """
    # FROM THE CLOCK, not from the quoted instrument's window. That window
    # can be well in the future -- stepping back from it lands on windows
    # that have not expired yet, and four PENDING readings say nothing about
    # whether the terminal fields are readable.
    tk = current_btc5m_ticker(now)
    own = event_ticker(symbol) or ""
    suffix = symbol[symbol.index(own) + len(own):] if own and own in symbol \
        else "-UP"
    tried = []
    for step in range(1, max(1, look_back) + 1):
        prev = sibling_ticker(tk, minutes=5 * step)
        if not prev:
            break
        cand = "GEMI-%s%s" % (prev, suffix)
        r = resolution_for(cand, reads=reads, now=now)
        tried.append({"symbol": cand, "verdict": r.get("verdict"),
                      "flapped": r.get("flapped")})
        if r.get("verdict") == SETTLED:
            r["windows_tried"] = tried
            return r
    return {"readable": False, "verdict": UNCONFIRMED, "windows_tried": tried,
            "why": ("no window in the last %d read as settled; that is the "
                    "absence of the evidence, not a claim that none settled"
                    % look_back)}


def _strike_check(contract: Mapping[str, Any]) -> Dict[str, Any]:
    """Does the published resolutionSide agree with settlementValue vs strike?

    A CONSISTENCY CHECK, not a second source of truth: resolutionSide is
    authoritative and this only says whether the two published numbers tell
    the same story. Verified to AGREE on four settled BTC5m contracts
    (2026-09-18). A disagreement is a flag to stop on, not a value to prefer.
    """
    side = contract.get("resolutionSide")
    sv = contract.get("settlementValue")
    strike = contract.get("strike") or {}
    k = strike.get("value") if isinstance(strike, Mapping) else None
    sym = str(contract.get("instrumentSymbol") or "")
    if side is None or sv in (None, "") or k in (None, ""):
        return {"available": False,
                "why": "not settled yet, or no strike published"}
    if not sym.endswith("-UP"):
        return {"available": False,
                "why": "the >= rule is stated for the UP contract; not "
                       "assumed for %s" % sym}
    try:
        expected = "yes" if D(str(sv)) >= D(str(k)) else "no"
    except Exception:                                          # noqa: BLE001
        return {"available": False, "why": "unparseable settlement or strike"}
    return {"available": True, "agrees": expected == side,
            "published_resolutionSide": side, "implied_by_strike": expected,
            "settlementValue": str(sv), "strike": str(k),
            "rule": "UP resolves yes when settlementValue >= strike.value"}


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


POSITIONS_PATH = "/v1/prediction-markets/positions"


async def positions(*, timeout: float = 20.0) -> Dict[str, Any]:
    """The account's OPEN positions. READ-ONLY, and fail-closed.

    THE ONLY THING THAT MAY RELEASE OR CONFIRM A POSITION. `rows` is None
    whenever the read did not succeed -- an exception, a non-200, an
    unparseable body, or a payload whose `positions` key is not a list. None is
    UNREADABLE and is never an empty account: a caller that treated it as
    "flat" would be assuming exactly the state that costs money to be wrong
    about.

    Gemini authenticates a read with POST. The verb is not a mutation, and
    this module has no order transport of its own.
    """
    from src.gemini_prediction_auth import private_rest_request
    out: Dict[str, Any] = {"path": POSITIONS_PATH, "rows": None,
                           "status": None, "ok": False}
    try:
        r = await private_rest_request("POST", POSITIONS_PATH, timeout=timeout)
    except Exception as exc:                                   # noqa: BLE001
        out["why"] = "positions read raised %s" % type(exc).__name__
        return out
    out["status"] = r.status_code
    if r.status_code != 200:
        out["why"] = "positions read returned %s" % r.status_code
        return out
    try:
        body = r.json()
    except ValueError:
        out["why"] = "positions body was not JSON"
        return out
    rows = body.get("positions") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        out["why"] = ("positions payload carried no list under `positions`; "
                      "the shape is unrecognised and is not read as empty")
        return out
    out["rows"] = rows
    out["total"] = (body.get("total") if isinstance(body, dict) else None)
    out["ok"] = True
    return out


ORDER_HISTORY_PATH = "/v1/prediction-markets/orders/history"


async def order_history(*, since_ms: Optional[int] = None,
                        until_ms: Optional[int] = None,
                        symbol: Optional[str] = None,
                        statuses: tuple = ("filled", "cancelled"),
                        limit: int = 100,
                        timeout: float = 45.0,
                        include_rows: bool = False) -> Dict[str, Any]:
    """FORENSIC, READ-ONLY: has this account ever had a prediction-market order?

    The question this answers is whether any previous Gemini live work left a
    venue-side record on the credentials we hold. /v1/mytrades returning zero
    rows does not answer it: mytrades is the classic SPOT trades endpoint, and
    zero rows there is equally consistent with "no trades" and with "wrong
    endpoint for this product". This is the endpoint the prior q1 code itself
    used to reconcile its own orders, so it is the one that would carry them.

    Asked once per status, because `status` is a scalar in the documented
    request body rather than a list. A non-200 is reported as unreadable and
    never as an empty history.

    "open" IS NOT A VALID STATUS HERE. The venue answers 400 InvalidStatus,
    "Use /orders/active for open orders" -- measured 2026-09-18. Asking for it
    cost a third of the read and taught nothing, so it is gone; active orders
    have their own path and their own caller.

    The rows come paginated, so a full page is a FLOOR on the count and never
    the total; `pagination` is carried through rather than discarded.
    """
    from src.gemini_prediction_auth import has_credentials, private_rest_json
    if not has_credentials():
        return {"readable": False, "verdict": "NO_CREDENTIALS"}
    import time as _t
    now_ms = int(_t.time() * 1000)
    lo = since_ms if since_ms is not None else now_ms - 45 * 86400 * 1000
    hi = until_ms if until_ms is not None else now_ms + 60000
    per: List[Dict[str, Any]] = []
    total = 0
    unreadable = []
    for st in statuses:
        # A WIDE `filled` window came back 503 "upstream connect error" and,
        # before that, a read timeout. Neither is an empty history, and both
        # are cost signals rather than answers -- so the same question is
        # asked again more cheaply before it is called unreadable. The WINDOW
        # is preserved as long as possible, because narrowing it is what
        # would actually hide an old fill.
        attempts_for_status = [(limit, lo), (5, lo),
                               (5, max(lo, now_ms - 14 * 86400 * 1000))]
        r = payload = None
        tried: List[Dict[str, Any]] = []
        for lim, since in attempts_for_status:
            body: Dict[str, Any] = {"status": st, "limit": lim, "offset": 0,
                                    "from": since, "to": hi}
            if symbol:
                body["symbol"] = symbol
            try:
                r = await private_rest_json("POST", ORDER_HISTORY_PATH,
                                            params=body, timeout=timeout)
            except Exception as exc:                           # noqa: BLE001
                tried.append({"limit": lim, "http": None,
                              "err": type(exc).__name__})
                r = None
                continue
            tried.append({"limit": lim, "http": r.status_code})
            if r.status_code == 200:
                break
        if r is None:
            per.append({"status_filter": st, "http": None,
                        "attempts": tried, "unreadable": True})
            unreadable.append(st)
            continue
        try:
            payload = r.json()
        except ValueError:
            payload = None
        rows = None
        if isinstance(payload, dict):
            for k in ("orders", "results", "data"):
                if isinstance(payload.get(k), list):
                    rows = payload[k]
                    break
        elif isinstance(payload, list):
            rows = payload
        entry = {"status_filter": st, "http": r.status_code,
                 "attempts": tried,
                 "rows": (len(rows) if rows is not None else None),
                 "page_is_full": (rows is not None and len(rows) >= limit),
                 "pagination": (payload.get("pagination")
                                if isinstance(payload, dict) else None),
                 "body_keys": (sorted(payload) if isinstance(payload, dict)
                               else None)}
        if r.status_code != 200 or rows is None:
            unreadable.append(st)
            entry["unreadable"] = True
            entry["detail"] = (payload if isinstance(payload, dict)
                               else str(r.text)[:200])
        else:
            total += len(rows)
            # AGGREGATES over the whole page, not just the sample: what
            # timeInForce this venue has actually accepted from us, what
            # quantities, and whether any BTC5m contract appears at all.
            # "historical orders were quantity 1" is a fact about MLB rows
            # unless the rows say otherwise.
            tifs: Dict[str, int] = {}
            qtys: Dict[str, int] = {}
            btc = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                t = str(row.get("timeInForce"))
                tifs[t] = tifs.get(t, 0) + 1
                q = str(row.get("quantity"))
                qtys[q] = qtys.get(q, 0) + 1
                if str(row.get("symbol") or "").startswith("GEMI-BTC"):
                    btc += 1
            entry["timeInForce_counts"] = tifs
            entry["quantity_counts"] = dict(sorted(
                qtys.items(), key=lambda kv: -kv[1])[:12])
            entry["btc_rows"] = btc
            entry["btc_samples"] = [
                {k: r.get(k) for k in ("symbol", "side", "outcome",
                                       "orderType", "timeInForce", "price",
                                       "quantity", "filledQuantity",
                                       "status", "createdAt")}
                for r in rows if isinstance(r, dict)
                and str(r.get("symbol") or "").startswith("GEMI-BTC")][:3]
            # first row's SHAPE and identifiers only -- enough to prove an
            # order existed and to compare its fields, without dumping an
            # account's whole history into an artifact.
            if rows:
                keep = ("orderId", "globalOrderId", "clientOrderId",
                        "symbol", "side", "outcome", "orderType",
                        "timeInForce", "price", "quantity", "filledQuantity",
                        "remainingQuantity", "status", "createdAt",
                        "updatedAt", "cancelledAt")
                entry["sample"] = {k: rows[0].get(k) for k in keep
                                   if isinstance(rows[0], dict)
                                   and k in rows[0]}
                entry["row_keys"] = sorted(rows[0]) if isinstance(
                    rows[0], dict) else None
            # OPT-IN, DEFAULT OFF. This function summarises by design so an
            # account's whole history does not land in an artifact, and every
            # existing caller keeps that behaviour. But a CALLER THAT MUST
            # RECONSTRUCT INVENTORY needs the rows themselves: reading a key
            # that is never produced returned [] on every call, which is
            # "readable and empty" -- exactly the substitution that makes a
            # restart assume flat when it is not.
            if include_rows:
                entry["orders"] = [r for r in rows if isinstance(r, dict)]
        per.append(entry)
    return {
        "readable": not unreadable,
        "verdict": ("UNREADABLE" if unreadable
                    else ("ORDERS_FOUND" if total else "NO_ORDERS_ON_RECORD")),
        "path": ORDER_HISTORY_PATH,
        "window_ms": [lo, hi],
        "window_utc": [datetime.fromtimestamp(lo / 1000, timezone.utc
                                              ).isoformat(),
                       datetime.fromtimestamp(hi / 1000, timezone.utc
                                              ).isoformat()],
        "symbol": symbol,
        "total_rows": total,
        "per_status": per,
        "unreadable_statuses": unreadable,
        "labels": ("NO_ORDERS_ON_RECORD is a statement about THESE "
                   "credentials in THIS window, never about other accounts "
                   "or hosts. UNREADABLE is not an empty history."),
    }


def outcome_book(symbol: str) -> Dict[str, Any]:
    """Both outcomes' bids and asks, READ from the contract rather than
    derived from the complement identity.

    The contract object publishes them directly:

        prices: {buy:  {yes, no},      <- what it costs to BUY each outcome
                 sell: {yes, no},      <- what a SELL of each outcome receives
                 bestBid, bestAsk, lastTradePrice}

    So a resting BUY on an outcome joins that outcome's SELL side, and the
    passive pair cost is sell.yes + sell.no -- measured 0.19 + 0.50 = 0.69 on
    GEMI-BTC05M2609181115-UP at 11:15Z, against 1.31 to cross both asks.

    THE IDENTITY IS CHECKED, NOT ASSUMED. no_bid should equal 1 - yes_ask and
    no_ask should equal 1 - yes_bid; when it does not, that is reported rather
    than smoothed over, because a book that violates it is not a book we
    understand well enough to quote into.
    """
    tk = event_ticker(symbol)
    if not tk:
        return {"readable": False, "why": "symbol carries no event ticker"}
    got = fetch_event(tk)
    if not got.get("readable"):
        return {"readable": False, "ticker": tk, "why": got.get("err")}
    c = contract_of(got["event"], symbol)
    if c is None:
        return {"readable": False, "ticker": tk,
                "why": "the event lists no contract with that symbol"}
    px = c.get("prices") or {}
    buy, sell = px.get("buy") or {}, px.get("sell") or {}
    need = (buy.get("yes"), buy.get("no"), sell.get("yes"), sell.get("no"))
    if any(v in (None, "") for v in need):
        return {"readable": False, "ticker": tk, "prices": px,
                "why": "the contract does not publish both outcomes' prices; "
                       "nothing is derived in their place"}
    yes_bid, yes_ask = D(str(sell["yes"])), D(str(buy["yes"]))
    no_bid, no_ask = D(str(sell["no"])), D(str(buy["no"]))
    ident = {"no_bid_vs_1_minus_yes_ask": str(no_bid - (D("1") - yes_ask)),
             "no_ask_vs_1_minus_yes_bid": str(no_ask - (D("1") - yes_bid))}
    consistent = all(D(v) == 0 for v in ident.values())
    return {
        "readable": True, "ticker": tk, "instrumentSymbol": symbol,
        "yes": {"bid": str(yes_bid), "ask": str(yes_ask)},
        "no": {"bid": str(no_bid), "ask": str(no_ask)},
        "passive_pair_cost": str(yes_bid + no_bid),
        "crossing_pair_cost": str(yes_ask + no_ask),
        "last_trade_price": px.get("lastTradePrice"),
        "complement_identity": ident,
        "identity_consistent": consistent,
        "source": "%s/{eventTicker} contract prices (PUBLIC)" % EVENTS_PATH,
        "labels": ("a resting BUY joins the outcome's SELL side, so the "
                   "passive pair cost is what a maker surface can reach. It "
                   "is a cost basis, never a realized profit."),
    }


def contract_depth(symbol: str, *, timeout: float = 2.5,
                   levels: int = 5) -> Dict[str, Any]:
    """Resting SIZE at and behind the touch, from the venue's own ladder.

    WHY THIS IS A SECOND READ AND STAYS A SECOND READ. `outcome_book` is the
    state the controller decides on, and the contract object publishes PRICES
    ONLY -- it carries no depth at all. So "was there anything to trade
    against?" cannot be answered from the presented book, and CONTINUOUS-7's
    zero fills had to be explained by inferring the book from our own quoted
    prices and `ticks_behind_bid`. That inference is exactly the substitution
    this program refuses to keep making.

    GET /v1/book/{symbol} is a PUBLIC, unauthenticated read of the SAME
    contract, proven against these symbols over 8,186 samples
    (analysis/l5/gemini_depth/INVESTIGATION.md). Its `bids` and `asks` are the
    contract's own ladder and, at the touch, equal `outcome_book`'s yes
    bid/ask -- measured 0.83/0.99 against 0.83/0.99 on
    GEMI-BTC05M2609190015-UP. The caller CHECKS that agreement per cycle
    rather than assuming it.

    NOTHING IS DERIVED FOR THE NO SIDE. The venue publishes ONE contract
    ladder. A NO buy rests against it through the complement identity that
    `outcome_book` already verifies, but relabelling an ask size as a "NO bid
    size" would put an inferred number where no observed one exists. The sizes
    are reported under the ladder's own names and the absence is stated.

    NEVER RAISES, and never blocks a decision: an unreadable book is a RESULT
    carrying its reason. It is EVIDENCE ONLY -- no caller may price, size or
    gate anything on it.
    """
    import time as _t

    from src.l5_shadow import venues as V
    t0 = _t.time()
    out: Dict[str, Any] = {
        "source": "GET %s%s/{symbol} (PUBLIC, unauthenticated)" % (V.GEMINI,
                                                                   BOOK_PATH),
        "instrumentSymbol": symbol,
        "read_utc": datetime.fromtimestamp(t0, timezone.utc).isoformat(),
        "labels": ("EVIDENCE ONLY. A separate read from the book the "
                   "controller decided on, with its own clock; the two are "
                   "reported side by side and never merged."),
    }
    r = V.get("%s%s/%s" % (V.GEMINI, BOOK_PATH, symbol), timeout=timeout)
    out["rtt_s"] = r.get("rtt_s")
    if not r.get("ok"):
        out.update({"readable": False, "why": r.get("err")})
        return out
    try:
        j = json.loads(r["body"])
    except ValueError:
        out.update({"readable": False, "why": "unparseable book body"})
        return out

    def side(rows: Any) -> Dict[str, Any]:
        lv: List[Dict[str, Any]] = []
        for row in (rows or [])[:levels]:
            if not isinstance(row, Mapping):
                continue
            px, qty = row.get("price"), row.get("amount")
            if px in (None, "") or qty in (None, ""):
                continue
            lv.append({"price": str(px), "qty": str(qty),
                       "venue_ts": row.get("timestamp")})
        total = sum((D(x["qty"]) for x in lv), D("0"))
        return {"levels": lv, "n_levels": len(lv),
                "best": (lv[0]["price"] if lv else None),
                "best_qty": (lv[0]["qty"] if lv else None),
                "qty_shown": str(total)}

    bids, asks = side(j.get("bids")), side(j.get("asks"))
    stamps = [D(str(x["venue_ts"])) for s_ in (bids, asks)
              for x in s_["levels"] if x.get("venue_ts") not in (None, "")]
    venue_ts = max(stamps) if stamps else None
    out.update({
        "readable": True,
        # the ladder under the venue's OWN names. For a -UP contract these are
        # the YES bid and YES ask sides; that equality is asserted against the
        # presented book per cycle, not assumed here.
        "bids": bids, "asks": asks,
        "levels_requested": levels,
        "venue_ts": (str(venue_ts) if venue_ts is not None else None),
        "snapshot_age_s": (round(t0 - float(venue_ts), 3)
                           if venue_ts is not None else None),
        "no_side_depth": None,
        "no_side_depth_why": ("the venue publishes one contract ladder and no "
                              "separate NO ladder; a NO size is NOT derived "
                              "from an ask size here"),
    })
    return out
