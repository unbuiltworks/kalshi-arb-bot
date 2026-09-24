"""Bounded preflight/arming session: order PREVIEW only, never submission.

Why this is a separate module and not part of the recorder
---------------------------------------------------------
The 24/7 L5 observer's safety property is structural, not behavioural: its
image contains no path that can POST, so no flag, bug or prompt can talk it
into sending an order. `POST /v1/order/preview` is not an order -- the venue
validates the request and returns the order it WOULD have created -- but it
is still a POST to the orders service, and putting one in the observer would
trade a structural guarantee for a conditional one.

So the preview lives here, in a session that is run deliberately and
bounded, and this module is deliberately NOT in the recorder image's
watchPatterns/COPY set. The observer stays GET-only.

What a preview does and does not prove
--------------------------------------
The venue documents `/v1/order/preview` as "Preview an order before
submission to validate parameters and see expected fills", answering 200
with the previewed `Order` or 400 "Bad request - invalid order preview
request". The documentation does NOT enumerate which validations produce the
400. So this module records raw outcomes and refuses to assert meaning that
has not been observed: `ConstraintProbe.proven` stays False until a live run
actually sees the venue reject the off-tick and below-minimum shapes.

In particular, a 200 for `OUTCOME_SIDE_YES` proves only that YES is a valid
outcome side on this market. It does NOT prove YES means "Up". The Up/Down
semantics come from the venue's published contract definition, recorded in
`STATIC_CONTRACT` with its provenance, and any corroboration the preview
response happens to carry (`marketMetadata.outcome`) is recorded separately
and never substituted for it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import pmus_auth as Q1

#: the ONLY path this session may POST to.
PREVIEW_PATH = "/v1/order/preview"

#: paths that exist on this service and move money or working orders. Listed
#: explicitly so the refusal is legible in a traceback, though the allowlist
#: above already refuses everything that is not the preview path.
MUTATING_PATHS = (
    "/v1/orders", "/v1/orders/open/cancel", "/v1/orders/batched",
    "/v1/orders/batched/cancel", "/v1/orders/batched/modify",
    "/v1/order/close-position",
)


class MutationRefused(PermissionError):
    """Raised when anything other than a preview is attempted."""


class PreviewOnlyTransport:
    """Signed HTTP that can GET, and can POST to exactly one path.

    The refusal is structural and lives here rather than behind a flag: a
    caller cannot reach `/v1/orders` through this object at all.
    """

    def __init__(self, http_post=None, http_get=None) -> None:
        self._post = http_post
        self._get = http_get
        self.calls: list = []

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = Q1.API) -> Any:
        m = str(method).upper()
        self.calls.append((m, path))
        if m == "GET":
            return self._do_get(path, base)
        if m != "POST":
            raise MutationRefused(
                "%s %s refused: this session may only GET or preview"
                % (m, path))
        if path != PREVIEW_PATH:
            raise MutationRefused(
                "POST %s refused: the only permitted POST path is %s"
                % (path, PREVIEW_PATH))
        return self._do_post(path, body or {}, base)

    # -- the two primitives, injectable so tests never touch the network --

    def _do_get(self, path: str, base: str) -> Any:
        if self._get is not None:
            return self._get(path, base)
        return _signed(path, None, base, method="GET")

    def _do_post(self, path: str, body: Mapping[str, Any], base: str) -> Any:
        if self._post is not None:
            return self._post(path, body, base)
        return _signed(path, body, base, method="POST")


def _signed(path: str, body: Optional[Mapping[str, Any]], base: str,
            method: str) -> Any:
    """The existing PMUS Ed25519 signing stack; no new credential path."""
    import ssl
    import urllib.request
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base + path, data=data, headers=Q1.signed_headers(method, path),
        method=method)
    with urllib.request.urlopen(
            req, context=ssl.create_default_context(), timeout=20) as r:
        return json.loads(r.read() or b"{}")


# ------------------------------------------------------------- redaction

_SECRET = re.compile(
    r"(X-PM-Signature|X-PM-Access-Key|Authorization)\s*[:=]\s*\S+", re.I)


def redact(obj: Any) -> Any:
    """Credentials must never reach a provenance record or a log."""
    key = (Q1.os.getenv(Q1.KEY_ENV, "") or "").strip()
    sec = (Q1.os.getenv(Q1.SECRET_ENV, "") or "").strip()
    s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    s = _SECRET.sub(lambda m: m.group(0).split(":")[0] + ": [REDACTED]", s)
    for v in (key, sec):
        if v:
            s = s.replace(v, "[REDACTED]")
    if isinstance(obj, str):
        return s
    try:
        return json.loads(s)
    except Exception:                                          # noqa: BLE001
        return s


def key_fingerprint() -> Optional[str]:
    kid = (Q1.os.getenv(Q1.KEY_ENV, "") or "").strip()
    return hashlib.sha256(kid.encode()).hexdigest()[:12] if kid else None


# ------------------------------------------- the static contract definition

#: Up/Down semantics, taken from Polymarket US's published contract
#: definition rather than inferred from prices, history or L5's behaviour.
#: This is the SEMANTIC source for the YES<->Up mapping. A preview cannot
#: supply it: the venue accepting OUTCOME_SIDE_YES says YES is a valid side,
#: not that YES is "Up".
STATIC_CONTRACT: Dict[str, Any] = {
    "series": "btc-updown-15m",
    "crypto_market_type": "updown",
    "asset_price_market_type": "ASSET_PRICE_MARKET_TYPE_UP_DOWN",
    "yes_is": "UP",
    "no_is": "DOWN",
    "basis": (
        "One binary instrument per window. Long side pays when the close is "
        "at or above the open."),
    "provenance": [
        {"source": "Polymarket US Trader Guide -- Crypto Schema",
         "url": "https://docs.polymarket.us/trader-guide/crypto-schema",
         "quote": "updown | ASSET_PRICE_MARKET_TYPE_UP_DOWN | One binary "
                  "instrument per window. Long side pays when the close is "
                  "at or above the open."},
        {"source": "Polymarket US Crypto FAQs",
         "url": "https://docs.polymarket.us/faqs/crypto-faqs",
         "quote": "a flat Up/Down window settles Up"},
        {"source": "Polymarket US -- Market Structure",
         "url": "https://docs.polymarket.us/learn/trading/basics/"
                "buying-yes-vs-selling-no",
         "quote": "Polymarket US markets use a single instrument "
                  "(buying Yes vs selling No)."},
    ],
    "retrieved_utc": "2026-09-24",
    "LABEL": "STATIC_CONTRACT_DEFINITION -- documentation, not a live read",
}

#: Contract attributes the same source publishes for this family. Recorded
#: for comparison against what the venue actually enforces; NEVER used as
#: the value sent in an order.
STATIC_FAMILY_ATTRS: Dict[str, Any] = {
    "tick_size": "0.01",
    "valid_price_range": ["0.01", "0.99"],
    "minimum_order_contracts": "0.01",
    "payout_per_contract_usd": "1.00",
    "LABEL": "DOCUMENTED FAMILY DEFAULTS -- not a per-instrument read",
}


def outcome_side_for(pmus_side: str) -> str:
    """UP/DOWN -> the venue's outcome side, via the static contract only."""
    s = str(pmus_side).upper()
    if s == STATIC_CONTRACT["yes_is"]:
        return "OUTCOME_SIDE_YES"
    if s == STATIC_CONTRACT["no_is"]:
        return "OUTCOME_SIDE_NO"
    raise ValueError("no contract mapping for side %r" % (pmus_side,))


# --------------------------------------------------- building the preview

def preview_request(intent: Mapping[str, Any], market_slug: str
                    ) -> Dict[str, Any]:
    """The venue's documented CreateOrderRequest for the order the executor
    actually proposes -- same market, side, price, quantity and maker/taker
    mode. Not a synthetic stand-in: previewing a different shape would
    validate something we are not going to send.

    `market_slug` must be the executable slug the AUTHENTICATED feed named.
    Contract semantics are never derived from its text.
    """
    maker = str(intent.get("mode", "")).upper() == "MAKER"
    return {
        "request": {
            "marketSlug": market_slug,
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(intent["price"]), "currency": "USD"},
            "quantity": float(D(str(intent["qty"]))),
            "outcomeSide": outcome_side_for(intent["side"]),
            "action": "ORDER_ACTION_BUY",
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": maker,
        }
    }


def preview(transport: PreviewOnlyTransport, intent: Mapping[str, Any],
            market_slug: str, now: Optional[dt.datetime] = None
            ) -> Dict[str, Any]:
    """One preview, with full request/response provenance, credentials out."""
    now = now or dt.datetime.now(dt.timezone.utc)
    body = preview_request(intent, market_slug)
    rec: Dict[str, Any] = {
        "utc": now.isoformat(),
        "path": PREVIEW_PATH,
        "api_key_fingerprint": key_fingerprint(),
        "request": redact(body),
        "request_sha256_16": hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()[:16],
        "LABEL": "PREVIEW ONLY -- no order was submitted",
    }
    try:
        resp = transport.request("POST", PREVIEW_PATH, body, Q1.API)
        rec["http"] = 200
        rec["response"] = redact(resp)
        rec["accepted"] = True
        order = (resp or {}).get("order") or {}
        rec["echo"] = {
            "marketSlug": order.get("marketSlug"),
            "outcomeSide": order.get("outcomeSide"),
            "price": order.get("price"),
            "quantity": order.get("quantity"),
            "state": order.get("state"),
            "market_outcome_text": (order.get("marketMetadata") or {}
                                    ).get("outcome"),
        }
    except MutationRefused:
        raise
    except BaseException as exc:                               # noqa: BLE001
        rec["accepted"] = False
        rec["error"] = redact("%s: %s" % (type(exc).__name__, exc))
    return rec


# --------------------------------------------- what the venue enforces

def constraint_probe(transport: PreviewOnlyTransport,
                     intent: Mapping[str, Any], market_slug: str,
                     tick: str, min_qty: str) -> Dict[str, Any]:
    """Ask the venue to reject shapes that should be invalid.

    Three previews: the real shape, the same shape off-tick, and the same
    shape below the minimum quantity. Meaning is only established when the
    venue actually answers: `proven` is True only if the valid shape was
    accepted AND both invalid shapes were refused. Anything else leaves the
    constraint UNPROVEN, which is not the same as satisfied.
    """
    half = (D(tick) / 2).normalize()
    off_tick = dict(intent, price=str(D(str(intent["price"])) + half))
    below_min = dict(intent, qty=str(D(min_qty) / 2))

    out = {
        "valid": preview(transport, intent, market_slug),
        "off_tick": preview(transport, off_tick, market_slug),
        "below_min_qty": preview(transport, below_min, market_slug),
        "tick_tested": tick,
        "min_qty_tested": min_qty,
    }
    out["tick_enforced"] = (out["valid"]["accepted"] is True
                            and out["off_tick"]["accepted"] is False)
    out["min_qty_enforced"] = (out["valid"]["accepted"] is True
                               and out["below_min_qty"]["accepted"] is False)
    out["proven"] = bool(out["tick_enforced"] and out["min_qty_enforced"])
    out["LABEL"] = (
        "venue-enforced constraint probe; `proven` False means UNPROVEN, "
        "never 'satisfied'")
    return out


def contract_evidence(*, probe: Mapping[str, Any],
                      market_slug: str,
                      feed_slug: str,
                      book_state_open: Optional[bool],
                      book_two_sided: Optional[bool]) -> Dict[str, Any]:
    """Can the three legs together stand in for `market_readable`?

    The old gate was meant to guarantee four things before an order is
    constructed: we are talking about the exact executable market; we know
    its outcome mapping; we know the price grid and quantity floor it will
    accept; and it is currently tradable. This reports each leg with its
    source, and passes only when every one is satisfied by evidence rather
    than by assumption.

    Tradability is deliberately NOT taken from the preview: the `state` on a
    preview response is the ORDER's state, not the market's, so the
    authenticated book remains the source for that invariant.
    """
    legs = {
        "exact_market_identity": {
            "ok": bool(market_slug) and market_slug == feed_slug,
            "source": "authenticated venue feed marketSlug",
            "detail": {"previewed": market_slug, "feed": feed_slug},
        },
        "outcome_mapping_up_down": {
            "ok": STATIC_CONTRACT["yes_is"] == "UP",
            "source": "published contract definition (STATIC_CONTRACT)",
            "detail": {"yes_is": STATIC_CONTRACT["yes_is"],
                       "no_is": STATIC_CONTRACT["no_is"],
                       "preview_corroboration": (
                           probe.get("valid", {}).get("echo", {})
                           .get("market_outcome_text")),
                       "note": "preview acceptance of OUTCOME_SIDE_YES does "
                               "not prove YES is Up; the contract definition "
                               "does, and any echo is corroboration only"},
        },
        "tick_enforced_by_venue": {
            "ok": bool(probe.get("tick_enforced")),
            "source": "POST /v1/order/preview rejection of an off-tick price",
        },
        "min_quantity_enforced_by_venue": {
            "ok": bool(probe.get("min_qty_enforced")),
            "source": "POST /v1/order/preview rejection of a sub-minimum qty",
        },
        "market_open_tradable": {
            "ok": bool(book_state_open) and bool(book_two_sided),
            "source": "authenticated book/state, NOT the preview",
            "detail": {"state_open": book_state_open,
                       "two_sided": book_two_sided},
        },
    }
    failed = sorted(k for k, v in legs.items() if not v["ok"])
    return {"legs": legs, "blocking_failures": failed,
            "contract_proven": not failed,
            "LABEL": "replacement evidence for market_readable"}
