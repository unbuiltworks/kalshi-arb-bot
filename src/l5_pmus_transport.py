"""Authenticated PMUS transport for the faithful L5 executor.

REUSES the existing authenticated PMUS infrastructure -- `pmus_q1_executor`'s
Ed25519 request signing, its API/GATEWAY hosts, its credential environment
names and its owner-arm contract -- rather than standing up a second execution
stack. The ORDER WIRE FORMAT is the one `pmus_micro_executor` verified live on
2026-09-05/06 (price.value is the LONG side's price, so a NO order at X is sent
as 1 - X; quantity is a number; preview is wrapped as {"request": body}).

=== IT STARTS DISARMED, AND FAILS CLOSED ===

`PmusTransport.armed` is False on construction and stays False until BOTH:

  1. a full read-only preflight has PASSED in this process, and
  2. an explicit owner arm token is present in the environment AND matches
     the session id, exactly as the existing executor requires.

Any submit or cancel attempted before that raises `NotArmed`. Every preflight
check is a hard gate. A check that cannot be evaluated FAILS -- an unknown
answer is never treated as a pass.

=== WHY THE READ PATHS CHANGED (2026-09-23 22:56Z, on the recorder) ===

The first authenticated preflight run where the credentials live FAILED on
paths, not on access: `/v1/account` and `/v1/positions` answered 404 and
`/v1/orders` 501. The executor's live-proven reads are `/v1/account/balances`,
`/v1/portfolio/positions` and `/v1/orders/open`, and those are used here.

Market metadata and the book are no longer read from the PUBLIC gateway. The
gateway exposes a BTC15m market only partway into its own window and does not
list series 375's events at all, so a preflight that depended on it could never
pass at the moment L5 opens. Metadata is read SIGNED from the API host; the book
comes from the AUTHENTICATED WebSocket (an injected `book_source`), falling
back to the signed REST book. The gateway is never accepted as authenticated.

=== CREDENTIALS NEVER APPEAR ===

Nothing in this module returns, logs or stores a key or a secret. The
preflight reports only whether credentials are PRESENT; the account identity it
prints is the venue's own answer, or a one-way fingerprint of the key id when
the venue serves none. `redact()` is applied to every error string.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from decimal import Decimal as D, ROUND_DOWN
from typing import Any, Callable, Dict, List, Mapping, Optional

from src import pmus_btc15m_preflight as G
from src import pmus_q1_executor as Q1
from src.l5_live_execution import NotArmed

BTC15M_SERIES = G.BTC15M_SERIES
BTC15M_SLUG_RE = G.BTC15M_SLUG_RE

#: the two outcomes, and the ONLY mapping this engine accepts: UP is the long
#: (YES) side, DOWN the short (NO) side
OUTCOMES = ("UP", "DOWN")
WIRE_SIDE = {"UP": "OUTCOME_SIDE_YES", "DOWN": "OUTCOME_SIDE_NO"}

#: owner arm token, reusing the existing executor's environment contract
ARM_ENV, SID_ENV, LIVE_ENV = Q1.ARM_ENV, Q1.SID_ENV, Q1.LIVE_ENV

BALANCES_PATH = G.BALANCES_PATH
POSITIONS_PATH = G.POSITIONS_PATH
OPEN_ORDERS_PATH = G.OPEN_ORDERS_PATH
MAX_COMMITTED_USD = G.MAX_COMMITTED_USD
BOOK_MAX_STREAM_AGE_S = G.BOOK_MAX_STREAM_AGE_S

#: IOC orders are terminal almost immediately; how long to read their state
IOC_SETTLE_S = 5.0

redact = G.redact
key_fingerprint = G.key_fingerprint
_num = G.num


class PreflightFailed(RuntimeError):
    pass


class PmusTransport:
    """GET-only until armed. Mutations exist but are gated behind preflight."""

    name = "PMUS"

    def __init__(self, *, market_slug: str, http: Optional[Any] = None,
                 now_fn=None,
                 book_source: Optional[Callable[[str], Optional[dict]]] = None,
                 sleep=time.sleep) -> None:
        self.market_slug = market_slug
        self.http = http or Q1.Transport()
        self._now = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
        self.book_source = book_source
        self._sleep = sleep
        self.armed = False
        self.preflight_report: Optional[Dict[str, Any]] = None
        self.market_meta: Dict[str, Any] = {}
        #: client order id -> venue order id, for exact cancels
        self.venue_ids: Dict[str, str] = {}

    # ------------------------------------------------------------ read only

    def _get(self, path: str, base: str = Q1.API) -> Any:
        return self.http.request("GET", path, None, base)

    def market(self, slug: str) -> Dict[str, Any]:
        r = self._get(G.MARKET_PATH % slug) or {}
        mk = r.get("market") if isinstance(r, Mapping) else None
        return dict(mk) if isinstance(mk, Mapping) else dict(r or {})

    def positions(self) -> Dict[str, D]:
        return G.positions_by_slug(self._get(POSITIONS_PATH) or {})

    def open_orders(self) -> List[Any]:
        return G.open_orders_list(self._get(OPEN_ORDERS_PATH) or {})

    # ------------------------------------------------------------ preflight

    def preflight(self, *, expect_account: Optional[str] = None,
                  known_positions: Optional[Mapping[str, Any]] = None
                  ) -> Dict[str, Any]:
        """Every gate (pmus_btc15m_preflight.run), read-only, plus this
        transport's own state. Any failure leaves the transport DISARMED."""
        rep = G.run(self._get, self.market_slug,
                    book_source=self.book_source,
                    expect_account=expect_account,
                    known_positions=known_positions, now=self._now())
        self.market_meta = rep.pop("_market", {}) or {}
        disarmed = self.armed is False
        rep["checks"].append({"check": "executor_starts_disarmed",
                              "passed": disarmed, "detail": None})
        rep["checks"].append({"check": "owner_arm_token_present",
                              "passed": Q1.armed(), "blocking": False,
                              "detail": "arming is a separate owner act; "
                                        "preflight passing does NOT arm"})
        if not disarmed:
            rep["preflight"] = "FAILED"
            rep["blocking_failures"].append("executor_starts_disarmed")
        rep["labels"] = ("read-only; no credential value appears anywhere in "
                         "this report")
        self.preflight_report = rep
        if rep["preflight"] != "PASSED":
            raise PreflightFailed(json.dumps(rep, default=str))
        return rep

    def worst_case_from_venue(self, mk: Mapping[str, Any]) -> Dict[str, Any]:
        return G.worst_case(mk)

    # ------------------------------------------------------------ mutations

    def _require_armed(self) -> None:
        if not self.armed:
            raise NotArmed(
                "transport is DISARMED. Preflight status: %s; owner arm: %s"
                % ((self.preflight_report or {}).get("preflight", "NOT_RUN"),
                   "present" if Q1.armed() else "absent"))

    def disarm(self, reason: str = "") -> None:
        self.armed = False

    def arm(self) -> Dict[str, Any]:
        """Arm ONLY if preflight passed in this process AND the owner armed."""
        pf = (self.preflight_report or {}).get("preflight")
        if pf != "PASSED":
            raise NotArmed("preflight has not passed in this process (%s)" % pf)
        if (self.preflight_report or {}).get("market_slug") != self.market_slug:
            raise NotArmed("preflight was for a different market")
        if not Q1.armed():
            raise NotArmed("owner arm token absent or does not match the "
                           "session id (%s / %s / %s)"
                           % (LIVE_ENV, SID_ENV, ARM_ENV))
        self.armed = True
        return {"armed": True, "market": self.market_slug,
                "preflight": pf,
                "note": "arming is revocable by unsetting the owner token"}

    def order_body(self, intent: Mapping[str, Any]) -> Dict[str, Any]:
        """The live-verified wire body (pmus_micro_executor._body).

        Prices stay in the OUTCOME's own terms everywhere else; only here is a
        DOWN (NO) price mapped to the long-side value the venue reads.
        Quantity is floored to the market's minimum-quantity grid -- a venue
        constraint, never a behavioural resize -- and a result below the
        minimum is refused rather than rounded up.
        """
        side = str(intent["side"]).upper()
        if side not in WIRE_SIDE:
            raise ValueError("side must be UP or DOWN, got %r" % side)
        px = D(str(intent["price"]))
        wire_px = (D(1) - px) if side == "DOWN" else px
        tick = _num(self.market_meta.get(Q1.TICK_FIELD))
        if tick is None or tick <= 0:
            raise NotArmed("market tick unknown; preflight metadata missing")
        if (wire_px / tick) != (wire_px / tick).to_integral_value():
            raise ValueError("price %s is off the venue grid %s" % (wire_px, tick))
        minq = _num(self.market_meta.get("minimumTradeQty")) or D("1")
        qty = (D(str(intent["qty"])) / minq).to_integral_value(
            rounding=ROUND_DOWN) * minq
        if qty < minq:
            raise ValueError("qty %s below venue minimum %s"
                             % (intent["qty"], minq))
        taker = intent.get("mode") != "MAKER"
        return {
            "marketSlug": intent.get("market") or self.market_slug,
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(wire_px), "currency": "USD"},
            "quantity": float(qty),
            "tif": ("TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" if taker
                    else "TIME_IN_FORCE_GOOD_TILL_CANCEL"),
            "participateDontInitiate": not taker,
            "outcomeSide": WIRE_SIDE[side],
            "action": "ORDER_ACTION_BUY",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        }

    def submit(self, intent: Mapping[str, Any]) -> Dict[str, Any]:
        """Preview, then EXACTLY ONE order mutation, then read its state.

        The mutation is never retried: an ambiguous response is reported, not
        replayed. A marketable (IOC) order's fill is read back from the venue's
        own order record; a fill is never assumed.
        """
        self._require_armed()
        if (intent.get("market") or self.market_slug) != self.market_slug:
            raise NotArmed("intent market differs from the preflighted market")
        body = self.order_body(intent)
        preview = self.http.request("POST", "/v1/order/preview",
                                    {"request": body}, Q1.API) or {}
        po = preview.get("order") if isinstance(preview, Mapping) else None
        if not isinstance(po, Mapping) or preview.get("error"):
            raise RuntimeError("PREVIEW_REJECTED: %s" % redact(preview))
        resp = self.http.request("POST", "/v1/orders", body, Q1.API) or {}
        oid = str(resp.get("id") or resp.get("orderId")
                  or (resp.get("order") or {}).get("id") or "")
        out: Dict[str, Any] = {"order_id": oid or None, "filled_qty": "0",
                               "fill_price": None, "wire": body}
        if not oid:
            out["error"] = "NO_ORDER_ID"
            return out
        self.venue_ids[str(intent.get("client_order_id"))] = oid
        if body["tif"] != "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL":
            return out
        deadline = time.monotonic() + IOC_SETTLE_S
        order: Mapping[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                order = Q1._order_status(self.http, oid, sleep=self._sleep)
            except Exception as exc:                           # noqa: BLE001
                out["status_error"] = redact(exc)
                break
            st = Q1.order_state_str(order)
            if any(k in st for k in ("FILLED", "CANCEL", "REJECT", "EXPIRED")):
                break
            self._sleep(0.25)
        filled = Q1._filled_qty(order)
        out["state"] = Q1.order_state_str(order)
        out["filled_qty"] = str(filled)
        if filled > 0:
            px = None
            for k in ("avgPx", "avgPrice", "averagePrice", "avgExecutionPrice"):
                px = _num(order.get(k))
                if px is not None and px > 0:
                    break
            if px is not None:
                # the venue serves the long-side price; a NO fill is 1 - avgPx
                out["fill_price"] = str((D(1) - px)
                                        if str(intent["side"]).upper() == "DOWN"
                                        else px)
            else:
                out["error"] = "AMBIGUOUS_FILL_PRICE"
        return out

    def cancel(self, client_order_id: str) -> Dict[str, Any]:
        self._require_armed()
        oid = self.venue_ids.get(str(client_order_id))
        if not oid:
            return {"cancelled": False, "reason": "NO_VENUE_ID"}
        return self.http.request("POST", "/v1/order/%s/cancel" % oid,
                                 {"marketSlug": self.market_slug}, Q1.API) or {}

    def cancel_all(self) -> Dict[str, Any]:
        """Deadman: cancel every open order in the probe's market."""
        self._require_armed()
        return self.http.request("POST", "/v1/orders/open/cancel",
                                 {"slugs": [self.market_slug]}, Q1.API) or {}
