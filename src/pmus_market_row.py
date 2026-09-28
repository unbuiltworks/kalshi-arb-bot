"""The canonical PMUS market row: ONE read, one host, one shape.

There were two implementations of "resolve this market's metadata" and they
disagreed about which host and which resource answers.

  `pmus_micro_executor.market_micro_spec` -- the one that has placed and
  settled real PMUS orders, via pmus_spring_l5_session -- does exactly one
  call: GET {GATEWAY}/v1/market/slug/{slug}, and reads
  `minimumTradeQty` and `orderPriceMinTickSize` off `resp["market"]`.

  `l5_pmus_transport.resolve_market` tried the ACCOUNT-SCOPED api host
  first, then /v1/events/slug/{event}, then the gateway. The api host does
  not serve these public routes at all. The fallback chain hid that, and on
  2026-09-28 it turned a transient gateway 404 into a dead 15-minute
  window whose only evidence was the word FAILED.

Measured on 2026-09-28T22:34:59Z, gateway, unauthenticated: this route
answered 200 with a matching slug for every BTC15m window tested (1915z,
2000z, 2145z, 2215z, 2230z and every future window through
2026-09-29-0800z), and carried `orderPriceMinTickSize` 0.01 and
`minimumTradeQty` 0.01 on each.

/v1/markets/{slug}/bbo is NOT an existence oracle and is not used here: it
404s for every pre-open window (state MARKET_STATUS_CLOSED) and 404ed for
2215z even after that window had opened and closed. Existence is this row.

This module holds no state, imports nothing but the host constant, and
never decides tradability -- `status`/`active`/`closed` are returned as the
venue wrote them and judged by the caller, contemporaneously.
"""
from __future__ import annotations

import json
import ssl
import urllib.request
from typing import Any, Callable, Dict, Mapping, Optional

from src import pmus_auth as Q1

#: the one route. Gateway, never the account-scoped api host.
MARKET_ROW_PATH = "/v1/market/slug/%s"

#: the per-market execution grid, as the venue names it. A hardcoded
#: equality against a module constant once refused every sports market
#: outright (see pmus_micro_executor), so the venue's own field is the only
#: source and a missing field is an error rather than a default.
TICK_FIELD = "orderPriceMinTickSize"
MIN_QTY_FIELD = "minimumTradeQty"

UA = "pmus-market-row (read-only metadata)"


class MarketRowUnavailable(LookupError):
    """The venue did not serve this exact slug. Never a default row."""


def _plain_get(path: str, *, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(Q1.GATEWAY + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=ssl.create_default_context(),
                                timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def market_row(slug: str, *, get: Optional[Callable[..., Any]] = None,
               require_slug: bool = True) -> Dict[str, Any]:
    """The venue's row for EXACTLY this slug, or MarketRowUnavailable.

    `get` is any callable taking a path and returning the decoded body, so
    an authenticated transport, the tape collector's reader or a test double
    all work. It is called with the gateway path; callers that sign only for
    the api host must pass a getter that targets the gateway.

    The slug is matched HERE, on the row the venue returned. The gateway
    ignores query filters it does not know and answers 200 with an
    unrelated default page, so a status code is not a match.
    """
    fetch = _plain_get if get is None else get
    try:
        resp = fetch(MARKET_ROW_PATH % slug)
    except Exception as exc:                                   # noqa: BLE001
        raise MarketRowUnavailable(
            "market row not served for %s on %s: %s: %s"
            % (slug, Q1.GATEWAY, type(exc).__name__, str(exc)[:200])) from exc
    row = resp.get("market") if isinstance(resp, Mapping) else None
    if not isinstance(row, Mapping):
        row = resp if isinstance(resp, Mapping) else None
    if not isinstance(row, Mapping):
        raise MarketRowUnavailable(
            "market row for %s was not an object" % slug)
    # `require_slug=False` exists for ONE caller and is documented there:
    # pmus_micro_executor, which has traded live for months without this
    # check. Adding it there changed behaviour in pmus_spring_l5_session and
    # pmus_l5_continuous_session -- 75 tests -- and those services were not
    # in scope. The LOCK5 path keeps the strict match, and the difference is
    # explicit here rather than an accident of two implementations.
    if require_slug and row.get("slug") != slug:
        raise MarketRowUnavailable(
            "market row for %s did not identify itself: got slug %r"
            % (slug, row.get("slug")))
    return dict(row)


def grid(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Tick and minimum quantity as the venue wrote them, or KeyError.

    Returned as the raw values, not parsed to Decimal: the two callers
    parse differently (`_as_decimal` here, `_d` there) and neither should
    inherit the other's coercion.
    """
    out = {}
    for field in (TICK_FIELD, MIN_QTY_FIELD):
        if row.get(field) in (None, ""):
            raise KeyError("market row is missing %s" % field)
        out[field] = row[field]
    return out
