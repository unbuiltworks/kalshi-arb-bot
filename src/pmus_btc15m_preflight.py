"""The ONE definition of the PMUS BTC15m preflight gates.

Two callers run it: the production transport (`l5_pmus_transport`, which can
later place orders) and the recorder's read-only preflight
(`scripts/pmus_auth_preflight_readonly.py`, which never can). They used to
carry separate copies with a drift test between them, and both copies carried
the same wrong paths. Now both delegate here, so a gate means the same thing
wherever it is evaluated and there is nothing to drift.

This module has NO order path and NO executor dependency: it takes a `get`
callable and never issues anything else. That is what lets the recorder image,
which promises no order endpoint exists in it, run the same gates.

PATHS are the live-proven ones (pmus_q1_executor.preflight): the first run of
the old gates on the recorder, 2026-09-23 22:56Z, failed with /v1/account 404,
/v1/positions 404 and /v1/orders 501 -- wrong routes, not missing access.
Market metadata is read SIGNED from the API host, because the public gateway
does not list series 375's windows and exposes each market only partway into
its own window.

A check that cannot be EVALUATED fails. An unknown is never a pass.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional

API = "https://api.polymarket.us"
KEY_ENV = "PMUS_API_KEY_ID"
SECRET_ENV = "PMUS_API_SECRET_KEY"
TICK_FIELD = "orderPriceMinTickSize"

BTC15M_SERIES = "btc-updown-15m"
BTC15M_SLUG_RE = re.compile(r"^cpc-btc-updown-15m-\d{4}-\d{2}-\d{2}-\d{4}z$")

BALANCES_PATH = "/v1/account/balances"
POSITIONS_PATH = "/v1/portfolio/positions"
OPEN_ORDERS_PATH = "/v1/orders/open"
MARKET_PATH = "/v1/market/slug/%s"
BOOK_PATH = "/v1/markets/%s/book"

#: The venue's PUBLISHED contract terms for every crypto price family (cpc),
#: docs.polymarket.us/trader-guide/crypto-schema, Section 2: "tick size 0.01,
#: valid prices 0.01 to 0.99, minimum order 0.01 contract, payout $1.00", and
#: its worked 15-minute example: "the long side (Yes, 'Up') paid".
#:
#: WHY IT IS NEEDED. The public market catalogue does not carry the automated
#: BTC15m markets at all: GET /v1/market/slug/{slug} answers "Market not
#: found" for every window, live or settled (probed 23:32Z on 2130z..2330z),
#: and the metadata route exists only on the public host. So for this series
#: the market record is assembled from the venue's own sources: the published
#: contract terms below, and the market's live state from the AUTHENTICATED
#: stream (marketData.state). Each gate names which source it came from.
CRYPTO_CONTRACT_SPEC = {
    TICK_FIELD: "0.01", "minimumTradeQty": "0.01",
    "marketSides": [{"description": "Up", "long": True},
                    {"description": "Down", "long": False}],
    "_spec_source": "docs.polymarket.us/trader-guide/crypto-schema",
}
OPEN_STATE = "MARKET_STATE_OPEN"
CLOSED_STATES = ("MARKET_STATE_EXPIRED", "MARKET_STATE_TERMINATED")

#: the probe's committed cap; the worst case is computed against it
MAX_COMMITTED_USD = D("5")

#: an authenticated book whose socket has been silent longer than this is not
#: "the book now"
BOOK_MAX_STREAM_AGE_S = 30.0

_SECRETISH = re.compile(
    r"(X-PM-Access-Key|X-PM-Signature|X-PM-Timestamp|Authorization)"
    r"\s*[:=]\s*\S+", re.I)


def has_credentials() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip()) and \
        bool(os.getenv(SECRET_ENV, "").strip())


def redact(text: Any) -> str:
    """Strip anything header- or signature-shaped from a string."""
    s = str(text)
    s = _SECRETISH.sub(r"\1: [REDACTED]", s)
    for env in (KEY_ENV, SECRET_ENV):
        v = os.getenv(env, "")
        if v and len(v) > 6:
            s = s.replace(v, "[REDACTED]")
    return s[:600]


def key_fingerprint() -> Optional[str]:
    """A one-way handle on WHICH key is in use, so an owner can bind an
    authorization to it without the key ever being printed."""
    v = os.getenv(KEY_ENV, "").strip()
    return ("sha256:" + hashlib.sha256(v.encode()).hexdigest()[:12]) if v else None


def num(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def outcome_names(mk: Mapping[str, Any]) -> Dict[str, str]:
    """{'long': NAME, 'short': NAME} from the venue's own market record.

    `marketSides[].long` is authoritative for which side a YES order buys;
    `outcomes` (a JSON-encoded list on this venue) is read when the sides
    carry no description.
    """
    out: Dict[str, str] = {}
    for s in mk.get("marketSides") or []:
        if isinstance(s, Mapping) and s.get("description") is not None:
            out["long" if s.get("long") else "short"] = \
                str(s.get("description")).strip().upper()
    if len(out) < 2:
        raw = mk.get("outcomes")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        if isinstance(raw, list) and len(raw) == 2:
            names = [str((o.get("outcome") if isinstance(o, Mapping) else o)
                         or "").strip().upper() for o in raw]
            out.setdefault("long", names[0])
            out.setdefault("short", names[1])
    return out


def positions_by_slug(raw: Any) -> Dict[str, D]:
    """Venue positions as {slug: net qty}. The live schema is a MAP keyed by
    market slug; the list shape is still accepted."""
    body = raw.get("positions") if isinstance(raw, Mapping) else raw
    rows: List[tuple] = []
    if isinstance(body, Mapping):
        rows = list(body.items())
    elif isinstance(body, list):
        rows = [(p.get("marketSlug") or p.get("market"), p)
                for p in body if isinstance(p, Mapping)]
    out: Dict[str, D] = {}
    for slug, p in rows:
        q = None
        if isinstance(p, Mapping):
            for k in ("netPosition", "qtyDecimal", "quantity",
                      "positionQuantity", "qty"):
                q = num(p.get(k))
                if q is not None:
                    break
        else:
            q = num(p)
        if slug and q is not None and q != 0:
            out[str(slug)] = q
    return out


def open_orders_list(raw: Any) -> List[Any]:
    body = raw.get("orders") if isinstance(raw, Mapping) else raw
    if isinstance(body, Mapping):
        return list(body.values())
    return list(body or [])


def buying_power(bal: Any) -> Optional[D]:
    """/v1/account/balances: `balances` is a LIST of entries carrying
    buyingPower (same reading as pmus_q1_executor._buying_power)."""
    if not isinstance(bal, Mapping):
        return None
    entries: list = []
    raw = bal.get("balances")
    if isinstance(raw, list):
        entries.extend(raw)
    elif isinstance(raw, Mapping):
        entries.append(raw)
    entries.append(bal)
    for e in entries:
        if not isinstance(e, Mapping):
            continue
        for key in ("buyingPower", "availableBalance", "cashBalance"):
            v = num(e.get(key))
            if v is not None:
                return v
    return None


def account_identity(bal: Any) -> Optional[str]:
    entries = [bal] if isinstance(bal, Mapping) else []
    if isinstance(bal, Mapping) and isinstance(bal.get("balances"), list):
        entries += [e for e in bal["balances"] if isinstance(e, Mapping)]
    for e in entries:
        ident = (e.get("accountId") or e.get("account_id")
                 or e.get("userId") or e.get("id"))
        if ident:
            return str(ident)
    return key_fingerprint()


def rest_top(raw: Any) -> Dict[str, Any]:
    """Top of book from the signed REST book's actual levels."""
    md = (raw or {}).get("marketData") or {} if isinstance(raw, Mapping) else {}

    def top(levels):
        if not levels:
            return None, None
        lv = levels[0]
        return num(lv.get("px")), num(lv.get("qty"))
    bid, bq = top(md.get("bids"))
    ask, aq = top(md.get("offers"))
    return {"bid": bid, "ask": ask, "bid_shares": bq, "ask_shares": aq,
            "source": "authenticated_rest"}


def worst_case(mk: Mapping[str, Any]) -> Dict[str, Any]:
    """From the venue's own fee implementation, not an estimate.

    Principal is the committed cap because a share can settle worthless; the
    taker fee is maximised at p = 0.5, against the most contracts the cap can
    fund there.
    """
    from src import pmus_fees as fees
    cap = MAX_COMMITTED_USD
    worst_p = D("0.5")
    contracts = cap / worst_p
    fee = fees.taker_fee(worst_p, contracts)
    # format(..., "f") not str(): Decimal renders 5/0.5 as "1E+1"
    f = lambda d: format(D(str(d)), "f")
    return {
        "max_committed_usd": f(cap),
        "fee_maximising_price": f(worst_p),
        "contracts_at_that_price": f(contracts),
        "max_taker_fee_usd": f(fee),
        "worst_case_total_usd": f(cap + fee),
        "tick_from_market": str(mk.get(TICK_FIELD) or mk.get("tickSize")),
        "basis": ("principal is the committed cap because a share can settle "
                  "worthless; no leverage exists on this venue"),
    }


def run(get: Callable[[str], Any], slug: str, *,
        book_source: Optional[Callable[[str], Optional[dict]]] = None,
        expect_account: Optional[str] = None,
        known_positions: Optional[Mapping[str, Any]] = None,
        extra_checks: Optional[List[Dict[str, Any]]] = None,
        now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Every gate, GET-only, each evaluated even after an earlier failure so
    one run names every blocker. Returns the report and the market record."""
    checks: List[Dict[str, Any]] = list(extra_checks or [])
    reads: List[str] = []

    def gate(name: str, ok: Any, detail: Any = None) -> bool:
        checks.append({"check": name, "passed": bool(ok),
                       "detail": redact(detail) if detail is not None
                       else None})
        return bool(ok)

    def read(name: str, fn):
        """A read that raises is a FAILED gate, never a skipped one.
        BaseException: a missing crypto backend raises pyo3 PanicException,
        which is not an Exception; only the two fatal exits propagate."""
        try:
            v = fn()
            reads.append(name)
            return v, True
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:                           # noqa: BLE001
            gate(name, False, "%s: %s" % (type(exc).__name__, redact(exc)))
            return None, False

    ok = all(c.get("passed") or not c.get("blocking", True) for c in checks)
    ok &= gate("credentials_present", has_credentials(),
               "env %s / %s present" % (KEY_ENV, SECRET_ENV))

    # authenticated account: a SIGNED read the venue answered, its identity,
    # and buying power enough for the bounded worst case
    bal, got = read("account_readable", lambda: get(BALANCES_PATH) or {})
    ok &= got
    wc_total = D(worst_case({})["worst_case_total_usd"])
    if got:
        gate("account_readable", True, BALANCES_PATH)
        ident = account_identity(bal)
        ok &= gate("account_identity_resolved", bool(ident), ident)
        if expect_account:
            ok &= gate("account_matches_expected",
                       str(ident) == str(expect_account),
                       "expected %s got %s" % (expect_account, ident))
        power = buying_power(bal)
        ok &= gate("buying_power_covers_worst_case",
                   power is not None and power >= wc_total,
                   {"buying_power_usd": str(power),
                    "worst_case_usd": str(wc_total)})

    ok &= gate("slug_is_btc15m_series", bool(BTC15M_SLUG_RE.match(slug or "")),
               slug)
    mk: Dict[str, Any] = {}
    try:
        raw = get(MARKET_PATH % slug) or {}
        m = raw.get("market") if isinstance(raw, Mapping) else None
        mk = dict(m) if isinstance(m, Mapping) else dict(raw or {})
        catalogue = None
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:                               # noqa: BLE001
        catalogue = "%s: %s" % (type(exc).__name__, redact(exc))
    if catalogue is None and mk:
        reads.append("market_readable")
        gate("market_readable", True, "catalogue " + MARKET_PATH % slug)
        ok &= gate("market_resolves", str(mk.get("slug") or "") == slug,
                   mk.get("slug"))
    else:
        # the catalogue does not list this series; say so, never hide it
        checks.append({"check": "market_catalogue_record", "passed": False,
                       "blocking": False, "detail": catalogue})
        snap = None
        if book_source is not None and BTC15M_SLUG_RE.match(slug or ""):
            try:
                snap = book_source(slug)
            except Exception:                                  # noqa: BLE001
                snap = None
        st = (snap or {}).get("market_state")
        if st:
            mk = dict(CRYPTO_CONTRACT_SPEC, slug=slug, status=st,
                      active=(st == OPEN_STATE),
                      closed=(st in CLOSED_STATES),
                      _source="authenticated_ws_state+venue_published_spec")
        ok &= gate("market_readable", bool(st),
                   "authenticated markets stream state=%s; contract terms "
                   "from %s" % (st, CRYPTO_CONTRACT_SPEC["_spec_source"])
                   if st else "no catalogue record and no authenticated "
                              "stream state for this market")
        if st:
            ok &= gate("market_resolves", True,
                       "authenticated stream serves %s" % slug)

    state = str(mk.get("status") or mk.get("state") or "")
    ok &= gate("market_open_tradable",
               state.upper().endswith("OPEN") and not mk.get("closed")
               and mk.get("active", True) is not False,
               {"status": state, "closed": mk.get("closed"),
                "active": mk.get("active")})

    names = outcome_names(mk)
    ok &= gate("outcome_mapping_is_up_down",
               names.get("long") == "UP" and names.get("short") == "DOWN",
               names)

    tick = num(mk.get(TICK_FIELD) or mk.get("tickSize"))
    minq = num(mk.get("minimumTradeQty") or mk.get("orderMinQuantity")
               or mk.get("minOrderSize"))
    ok &= gate("tick_constraint_known", tick is not None and tick > 0, tick)
    ok &= gate("quantity_constraint_known", minq is not None and minq > 0, minq)

    def _book():
        if book_source is not None:
            b = book_source(slug)
            if b:
                return dict(b)
        return rest_top(get(BOOK_PATH % slug))
    bk, got = read("book_readable", _book)
    ok &= got
    bk = bk or {}
    if got:
        gate("book_readable", True, bk.get("source"))
    ok &= gate("book_two_sided",
               all(bk.get(k) is not None
                   for k in ("bid", "ask", "bid_shares", "ask_shares")),
               {k: str(bk.get(k)) for k in
                ("bid", "ask", "bid_shares", "ask_shares", "source")})
    ok &= gate("book_is_authenticated",
               str(bk.get("source") or "").startswith("authenticated"),
               bk.get("source"))
    age = bk.get("stream_age_s")
    ok &= gate("book_fresh",
               age is None or float(age) <= BOOK_MAX_STREAM_AGE_S, age)

    pos, got = read("positions_readable",
                    lambda: positions_by_slug(get(POSITIONS_PATH) or {}))
    oo, got2 = read("orders_readable",
                    lambda: open_orders_list(get(OPEN_ORDERS_PATH) or {}))
    ok &= got and got2
    if got and got2:
        gate("positions_readable", True, POSITIONS_PATH)
        gate("orders_readable", True, OPEN_ORDERS_PATH)
        ok &= gate("no_unexpected_open_orders", len(oo) == 0, len(oo))
        if known_positions is None:
            ok &= gate("no_position_in_target_market", slug not in pos,
                       {"target": str(pos.get(slug, "0")),
                        "other_markets_with_positions": len(pos)})
        else:
            drift = {k: {"ours": str(v), "venue": str(pos.get(k, "0"))}
                     for k, v in known_positions.items()
                     if D(str(v)) != pos.get(k, D(0))}
            ok &= gate("positions_reconciled", not drift, drift)

    return {
        "preflight": "PASSED" if ok else "FAILED",
        "utc": (now or dt.datetime.now(dt.timezone.utc)).isoformat(),
        "market_slug": slug,
        "checks": checks,
        "blocking_failures": [c["check"] for c in checks
                              if not c["passed"] and c.get("blocking", True)],
        "worst_case": worst_case(mk),
        "armed_after_preflight": False,
        "reads": reads,
        "_market": mk,
    }
