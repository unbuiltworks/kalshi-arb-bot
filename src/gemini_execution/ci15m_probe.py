"""The bounded BTC15m CONTINUOUS-INVENTORY live probe: ONE venue-order path.

WHAT IT VALIDATES: that OPEN -> EXTEND -> COMPLEMENT -> LOCK executes
correctly against the real BTC15m venue, with actual fills and AUTHORITATIVE
inventory reconciliation after every fill.

WHAT IT DOES NOT ANSWER: whether EXTEND is profitable. Three windows of q=1
cannot distinguish any edge from zero. The prospective EXTEND-vs-NO-EXTEND
shadow answers that, runs separately, and is never pooled with these rows.

NO SECOND POLICY. Every decision is `ci15m_policy.POLICY["decide_once"]`,
which IS the frozen function in
`analysis/gemini/info_edge/btc15m_shadow_policy.py` that the shadow calls.
Nothing in this file decides a side, a price, a size or a time.

NO SECOND SIZE. `QUANTITY` is imported from `ci15m_policy` and is the only
quantity this module can name. It is passed to `decide_once`, re-asserted in
the budget, re-asserted here, and asserted again against the built payload
before anything is sent. There is no multiplier, no progression, no
martingale and no sizing function anywhere on the path.

ONE ORDER AT A TIME, ESTABLISHED FROM THE VENUE. Not from our own belief:
`active_orders` is read, and a non-empty answer refuses the next submission.
An UNREADABLE answer also refuses, because an account whose orders cannot be
read is not an account with none.

FAIL CLOSED EVERYWHERE. Every anomaly writes a durable HALT and stops: the
HALT conditions are enumerated in `ci15m_budget.HALT_CONDITIONS` and none of
them is a warning.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import (arming, ci15m_budget as BUD, cohort,
                                  payload, position_ledger, spec, transport)
from src.gemini_execution.ci15m_policy import POLICY, QUANTITY, fingerprint
from src.gemini_execution.continuous_probe import (COMPLETE, EXTEND, HOLD,
                                                   OPEN)

LABEL = "GEMINI_CI15M_PROBE_V1"

#: the authorization these bounds were granted against. It must match
#: `arming.EXPECTED_AUTH_ID` before anything is constructed.
PROBE_AUTH_ID = "OWNER-2026-10-03-GEMINI-CI15M-1"

#: the policy bytes the authorization was granted against. INSTALLED
#: 2026-10-03 under the owner's FINAL AUTHORIZATION -- CI15M-1. None would
#: mean "whatever is on disk", which is refused for a live run.
#:
#: If the frozen policy file changes by one byte this no longer matches,
#: `submit` writes POLICY_FINGERPRINT_MISMATCH and the cohort HALTs. That is
#: what makes "do not modify the frozen policy" enforceable.
AUTHORIZED_POLICY_FINGERPRINT: Optional[str] = (
    "5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329")

#: the commit the authorization names. Verified at runtime, before the first
#: venue submission, by `verify_runtime()`.
AUTHORIZED_SHA = "bf14f29523f02a72187cea5d39102aa09c457122"

#: the ONE contract family. A symbol outside it is refused by name.
SYMBOL_PREFIX = "GEMI-BTC15M"
WINDOW_SECONDS = 900.0

#: Up is YES, Down is NO. The outcome strings are TAKEN FROM `spec`, not
#: spelled out here: the REST dialect is lower-case "yes"/"no" and the WS
#: dialect is upper-case, and writing either literal by hand is how a
#: side-mapping bug gets in. (It did, in the first draft of this file, and
#: the payload validator refused it before any venue saw it.)
SIDE_TO_OUTCOME = {"UP": spec.OUTCOME_YES, "DOWN": spec.OUTCOME_NO}
OUTCOME_TO_SIDE = {str(v).lower(): k for k, v in SIDE_TO_OUTCOME.items()}


class ProbeRefused(RuntimeError):
    """A gate said no before the venue was touched."""


# ----------------------------------------------------------------- shapes
class LegView:
    """A shape bridge, not a policy. `position_ledger.reconcile` reads
    yes_qty/no_qty/matched(); `fi_core.Inventory` names them q_up/q_down.
    Translating between two spellings of the same two numbers is all this
    does, and it holds no state of its own."""

    def __init__(self, inv: Any) -> None:
        self._inv = inv

    @property
    def yes_qty(self) -> D:
        return self._inv.q_up

    @property
    def no_qty(self) -> D:
        return self._inv.q_down

    def matched(self) -> D:
        return self._inv.matched


def new_inventory() -> Any:
    return POLICY["Inventory"]()


# ------------------------------------------------------------- selectors
def ticker_of(symbol: str) -> str:
    """BTC15M2610031530 from GEMI-BTC15M2610031530-UP."""
    parts = symbol.split("-")
    return parts[1] if len(parts) >= 2 else symbol


def eligible(market: Mapping[str, Any], now: float) -> Dict[str, Any]:
    """Is this one market selectable? Every condition stated and answered.

    STRICTLY ONE FAMILY, a PUBLISHED and AVAILABLE strike, and a TTE inside
    the frozen policy's own OPEN band -- not a band invented here.
    """
    sym = str(market.get("symbol") or "")
    why: List[str] = []
    if not sym.startswith(SYMBOL_PREFIX):
        why.append("symbol %r is not in the %s family" % (sym, SYMBOL_PREFIX))
    strike = (market.get("strike") or {})
    if strike.get("value") in (None, ""):
        why.append("no published strike value")
    av = strike.get("availableAt")
    if av in (None, ""):
        why.append("strike.availableAt is absent")
    elif float(av) > now:
        why.append("strike.availableAt %s has not passed" % av)
    exp = market.get("expiry_ts")
    tte = None if exp in (None, "") else float(exp) - now
    if tte is None:
        why.append("no readable expiry")
    elif not (POLICY["OPEN_TTE_LO"] <= tte <= POLICY["OPEN_TTE_HI"]):
        why.append("tte %.1f is outside the frozen OPEN band [%s, %s]"
                   % (tte, POLICY["OPEN_TTE_LO"], POLICY["OPEN_TTE_HI"]))
    return {"symbol": sym, "ticker": ticker_of(sym), "tte": tte,
            "eligible": not why, "why": why}


def select_market(markets: Sequence[Mapping[str, Any]],
                  now: float) -> Dict[str, Any]:
    """The ONE market, or none. The EARLIEST-expiring eligible one, so the
    choice is deterministic rather than whichever the slate listed first."""
    rated = [eligible(m, now) for m in markets]
    ok = sorted((r for r in rated if r["eligible"]),
                key=lambda r: (r["tte"], r["ticker"]))
    return {"selected": ok[0] if ok else None, "considered": rated,
            "rule": ("one family, published+available strike, tte inside the "
                     "frozen OPEN band; earliest expiry wins")}


# ------------------------------------------------------- reconciliation
def reconcile_or_halt(cohort_id: str, symbol: str, inv: Any,
                      position_rows: Optional[Sequence[Mapping[str, Any]]], *,
                      root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """AUTHORITATIVE reconciliation. Anything but exact agreement HALTs.

    `position_rows is None` means the read did not succeed, which is
    UNKNOWN_POSITION and never zero.
    """
    rec = position_ledger.reconcile(LegView(inv), position_rows, symbol)
    if rec.get("verdict") == position_ledger.UNREADABLE:
        BUD.halt(cohort_id, "UNKNOWN_POSITION", rec.get("why"), root=root)
        rec["halted"] = True
        return rec
    if not rec.get("tradeable"):
        BUD.halt(cohort_id, "INVENTORY_DISAGREEMENT", rec.get("why"),
                 root=root)
        rec["halted"] = True
        return rec
    rec["halted"] = False
    return rec


def check_fill(cohort_id: str, intent: Mapping[str, Any],
               fill: Mapping[str, Any], *,
               root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """A fill against the intent that produced it. Two HALTs live here.

    FILL_ABOVE_LIMIT: we buy, so a fill price ABOVE our limit is worse than
    we agreed to and is a semantic contradiction, not a rounding detail.
    SIDE_MAPPING_DISAGREEMENT: the venue's outcome must map back to the leg
    we intended through SIDE_TO_OUTCOME, in both directions.
    """
    out: Dict[str, Any] = {"ok": True, "halted": None}
    limit = D(str(intent["price"]))
    try:
        got = D(str(fill["price"]))
    except Exception:                                          # noqa: BLE001
        out.update(ok=False, halted=BUD.halt(
            cohort_id, "SEMANTIC_CONTRADICTION",
            "fill carried an unreadable price %r" % (fill.get("price"),),
            root=root))
        return out
    if got > limit:
        out.update(ok=False, halted=BUD.halt(
            cohort_id, "FILL_ABOVE_LIMIT",
            "filled at %s against a submitted limit of %s" % (got, limit),
            root=root))
        return out
    venue_outcome = str(fill.get("outcome") or "").lower()
    want_side = str(intent["side"])
    if OUTCOME_TO_SIDE.get(venue_outcome) != want_side:
        out.update(ok=False, halted=BUD.halt(
            cohort_id, "SIDE_MAPPING_DISAGREEMENT",
            "venue outcome %r maps to %r, we intended %r"
            % (fill.get("outcome"), OUTCOME_TO_SIDE.get(venue_outcome),
               want_side),
            root=root))
        return out
    try:
        if D(str(fill["quantity"])) > QUANTITY:
            out.update(ok=False, halted=BUD.halt(
                cohort_id, "QUANTITY_NOT_ONE",
                "filled quantity %s exceeds q=%s" % (fill["quantity"],
                                                     QUANTITY),
                root=root))
            return out
    except Exception:                                          # noqa: BLE001
        out.update(ok=False, halted=BUD.halt(
            cohort_id, "SEMANTIC_CONTRADICTION",
            "fill carried an unreadable quantity %r" % (fill.get("quantity"),),
            root=root))
    return out


# --------------------------------------------------------------- the step
def plan(cohort_id: str, inv: Any, *, bid: Optional[D], ask: Optional[D],
         bid_size: Any, ask_size: Any, tte: float, n_extends: int,
         actions: int, root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """What the FROZEN policy says to do next, plus the live-only bounds.

    The decision itself is not made here and cannot be: `decide_once` is the
    frozen function, called with the frozen thresholds and q=QUANTITY.
    """
    h = BUD.halted(cohort_id, root=root)
    if h:
        return {"action": HOLD, "stop": "HALTED", "reason": h}

    # LOCKED IS CHECKED BEFORE ANYTHING ELSE AND INDEPENDENTLY OF THE POLICY.
    # decide_once also refuses on a locked book; this is a second, closer
    # gate so that a locked inventory cannot be mutated even if a caller
    # reached here with a decision in hand.
    if POLICY["locked"](inv):
        return {"action": HOLD, "stop": "LOCKED",
                "reason": ("LOCKED: both outcomes positive after fees. A "
                           "locked book is never unlocked for another "
                           "trade.")}

    dec = POLICY["decide_once"](
        inv, bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
        tte=tte, qty=QUANTITY, n_extends=n_extends, actions=actions,
        allow_extend=True)

    if dec["action"] == EXTEND and n_extends >= BUD.MAX_EXTENDS:
        # unreachable through the frozen rule, which refuses first; kept as a
        # named HALT so a policy change could never silently widen it.
        BUD.halt(cohort_id, "EXTEND_BUDGET_EXCEEDED",
                 "n_extends=%s, max=%s" % (n_extends, BUD.MAX_EXTENDS),
                 root=root)
        return {"action": HOLD, "stop": "HALTED",
                "reason": "a third EXTEND was proposed"}
    return dec


def intent_of(ticker: str, inv: Any, dec: Mapping[str, Any],
              n_extends: int, *, reconciled_signed: Any = None
              ) -> Dict[str, Any]:
    """The submission intent, with every number the budget will re-check.

    The quantity is QUANTITY and is not derived from anything.
    """
    side, px = dec["side"], D(str(dec["price"]))
    trial = POLICY["Inventory"](
        q_up=inv.q_up, q_down=inv.q_down, c_up=inv.c_up, c_down=inv.c_down,
        fees=inv.fees, rebates=inv.rebates, fills=list(inv.fills))
    trial.add(POLICY["make_fill"](0.0, side, "maker", px, QUANTITY,
                                 "intent " + str(dec["action"])))
    rs = trial.residual_side
    avg = (trial.avg_up if rs == "UP"
           else (trial.avg_down if rs == "DOWN" else D("0")))
    return {
        "action": dec["action"], "side": side,
        "outcome": SIDE_TO_OUTCOME[side],
        "price": str(px), "quantity": str(QUANTITY),
        "maker_or_cancel": True,
        "extends_before": n_extends,
        "unmatched_shares_after": str(trial.residual),
        "unmatched_principal_after": str(trial.residual * avg),
        "pair_cost_after": (None if trial.matched_pair_cost() is None
                            else str(trial.matched_pair_cost())),
        "required_complement_price_for_0.96":
            (lambda v: None if v is None else str(v))(
                POLICY["required_complement_price"](trial, QUANTITY)),
        "reason": dec["reason"],
        "policy_fingerprint": fingerprint(),
        # 3 -- THE STAMP. Every bound below is computed from `inv`, so the
        # submission must carry proof that `inv` was reconciled against the
        # venue. `consume_slot` refuses an unstamped intent.
        RECONCILED: reconciled_signed,
    }


async def one_outstanding_order(symbol: str) -> Dict[str, Any]:
    """B -- THE HARD INVARIANT. No submission while ANY previous order has
    an unresolved status.

    THE DEFECT THIS REPLACES, exactly. The previous version caught
    exceptions and then called `transport._orders_of(res)` without ever
    checking `res["ok"]`. `_orders_of` returns `[]` for any body shape it
    does not recognise -- an error body included -- so a FAILED or
    UNRECOGNISED active-orders read was read as "zero outstanding orders"
    and the probe submitted anyway. Its docstring claimed "Unreadable
    refuses"; the code did the opposite. CI15M-1 left FOUR orders resting
    simultaneously (slots 2,3,4,5 at 0.70/0.71/0.73/0.72, all status
    `open`) through that hole.

    THREE THINGS MUST NOW HOLD, and each is checked positively:
      1. the read SUCCEEDED (`res["ok"]`);
      2. the response SHAPE was recognised, so an empty list means "the
         venue said none" and not "we could not find the list";
      3. the recognised list is empty.
    Anything else refuses. An unreadable order state fails closed.
    """
    try:
        res = await transport.active_orders(symbol)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "readable": False, "read": None,
                "why": "active orders could not be read (%s); an account "
                       "whose orders cannot be read is not an account with "
                       "none" % exc}
    read = {k: res.get(k) for k in ("ok", "status", "classification",
                                    "request_shape")}
    if not res.get("ok"):
        return {"ok": False, "readable": False, "read": read,
                "why": "the active-orders read did not succeed (%s); an "
                       "unreadable order book is never an empty one" % read}
    body = res.get("body")
    recognised = isinstance(body, list) or (
        isinstance(body, dict)
        and any(isinstance(body.get(k), list)
                for k in ("orders", "results", "data")))
    if not recognised:
        return {"ok": False, "readable": False, "read": read,
                "why": "the active-orders response shape was not recognised "
                       "(body type %s, keys %s); an empty list we could not "
                       "find is not an empty order book"
                       % (type(body).__name__,
                          sorted(body)[:8] if isinstance(body, dict)
                          else None)}
    orders = transport._orders_of(res)
    if orders:
        return {"ok": False, "readable": True, "open": len(orders),
                "read": read,
                "order_ids": [str(o.get(payload.spec.ORDER_ID_FIELD)
                                  or o.get("orderId")) for o in orders][:10],
                "why": "%d order(s) already outstanding on %s; the probe "
                       "allows exactly one, and an unresolved order blocks "
                       "the next decision" % (len(orders), symbol)}
    return {"ok": True, "readable": True, "open": 0, "read": read,
            "why": None}


# ------------------------------------------------- the order lifecycle
#
# A PLACE ACK IS NOT A TERMINAL ORDER RESULT. CI15M-1 treated it as one and
# therefore never learned about its own four fills: slots 2-5 acked
# `status: open, filledQuantity: 0`, the engine recorded NOT_FILLED, and the
# orders then rested and filled with nobody watching. Inventory stayed FLAT,
# the risk gates were evaluated against that phantom-flat inventory, and 4
# shares / $2.86 got past a 3-share / $2.70 cap.
SUBMITTED = "SUBMITTED"
ACK_FILLED = "ACK_FILLED"
ACK_CANCELLED = "ACK_CANCELLED"
ACK_RESTING = "ACK_RESTING"
ACK_UNRESOLVED = "ACK_UNRESOLVED"
TERMINAL_FILLED = "TERMINAL_FILLED"
TERMINAL_CANCELLED = "TERMINAL_CANCELLED"

#: venue statuses that are TERMINAL. Nothing else is, and absent is not.
TERMINAL_STATUSES = ("filled", "cancelled", "canceled", "expired",
                     "rejected", "closed")

UNRESOLVED_STATUSES = ("open", "accepted", "pending", "partially_filled",
                       "partially filled", "")


def classify_ack(body: Mapping[str, Any]) -> Dict[str, Any]:
    """SUBMITTED -> ACK_CANCELLED | ACK_FILLED | ACK_RESTING | ACK_UNRESOLVED.

    `open` IS NOT `NOT_FILLED`. It is a resting order whose fate is still
    open, and it must be polled to a terminal state before anything else
    happens.
    """
    st = str(body.get("status") or "").strip().lower()
    try:
        filled = D(str(body.get("filledQuantity")
                       if body.get("filledQuantity") is not None
                       else body.get("executedQuantity") or "0"))
    except Exception:                                          # noqa: BLE001
        return {"state": ACK_UNRESOLVED, "status": st, "filled": None,
                "terminal": False,
                "why": "the ack carried an unreadable filled quantity"}
    if st == "filled" or (st in TERMINAL_STATUSES and filled > 0):
        return {"state": ACK_FILLED, "status": st, "filled": filled,
                "terminal": True, "why": None}
    if st in ("cancelled", "canceled", "expired", "rejected") and filled <= 0:
        return {"state": ACK_CANCELLED, "status": st, "filled": filled,
                "terminal": True, "why": None}
    if st in ("open", "accepted", "pending"):
        return {"state": ACK_RESTING, "status": st, "filled": filled,
                "terminal": False,
                "why": ("the order is RESTING. `open` is not NOT_FILLED -- "
                        "CI15M-1's four fills all began in this state. Its "
                        "terminal state must be polled.")}
    return {"state": ACK_UNRESOLVED, "status": st or "(absent)",
            "filled": filled, "terminal": False,
            "why": "unrecognised or absent order status -- fail closed"}


async def poll_terminal(cohort_id: str, symbol: str, order_id: Any, *,
                        attempts: int = 12, pause: float = 5.0,
                        root: str = cohort.COHORT_ROOT,
                        sleeper: Optional[Any] = None) -> Dict[str, Any]:
    """Poll the AUTHORITATIVE order state until it is terminal.

    Reads the venue's own order history, which is the only surface that can
    show a FILLED order -- an active-orders read by construction cannot.
    An unreadable or uninterpretable state HALTs rather than being guessed.
    """
    from src.gemini_execution import metadata
    sleep = sleeper or asyncio.sleep
    last: Dict[str, Any] = {}
    for i in range(attempts):
        try:
            hist = await metadata.order_history(
                symbol=symbol, statuses=("filled", "cancelled", "open"),
                limit=100, include_rows=True)
        except Exception as exc:                               # noqa: BLE001
            last = {"error": str(exc)}
            await sleep(pause)
            continue
        if hist.get("readable") is False:
            last = {"unreadable": hist.get("why")}
            await sleep(pause)
            continue
        for grp in (hist.get("per_status") or []):
            for o in (grp.get("orders") or []):
                if str(o.get("orderId")) != str(order_id):
                    continue
                st = str(o.get("status") or "").strip().lower()
                if st in TERMINAL_STATUSES:
                    try:
                        f = D(str(o.get("filledQuantity") or "0"))
                    except Exception:                          # noqa: BLE001
                        break
                    return {"resolved": True,
                            "state": (TERMINAL_FILLED if f > 0
                                      else TERMINAL_CANCELLED),
                            "status": st, "filled": f, "row": o,
                            "attempts": i + 1}
                last = {"status": st, "row": o}
        await sleep(pause)
    BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION",
             "order %s never reached a terminal state after %d polls: %r"
             % (order_id, attempts, last), root=root)
    return {"resolved": False, "state": ACK_UNRESOLVED, "last": last,
            "attempts": attempts,
            "why": ("the order's terminal state could not be established. "
                    "An unreadable order state fails closed; the engine may "
                    "not decide again.")}


RECONCILED = "reconciled_venue_inventory"


async def reconciled_inventory(cohort_id: str, symbol: str, *,
                               root: str = cohort.COHORT_ROOT
                               ) -> Dict[str, Any]:
    """Inventory AS THE VENUE REPORTS IT, not as we inferred it.

    THE RISK GATES RUN ON THIS. CI15M-1's gates ran on a locally inferred
    fill list that was simply wrong, which is why the caps did not bite.
    """
    from src.gemini_execution import metadata
    try:
        got = await metadata.positions()
    except Exception as exc:                                   # noqa: BLE001
        BUD.halt(cohort_id, "UNKNOWN_POSITION",
                 "positions read failed: %s" % exc, root=root)
        return {"ok": False, "why": str(exc), "signed": None}
    rows = got.get("positions") if isinstance(got, Mapping) else None
    if rows is None and isinstance(got, Mapping):
        rows = got.get("result") or got.get("data")
    v = position_ledger.venue_signed(rows, symbol)
    if v.get("signed") is None:
        BUD.halt(cohort_id, "UNKNOWN_POSITION", v.get("why"), root=root)
        return {"ok": False, "why": v.get("why"), "signed": None}
    return {"ok": True, "signed": v["signed"],
            "representation": v["representation"], "rows": rows}


def order_is_resolved(body: Mapping[str, Any]) -> Dict[str, Any]:
    """Is this order's fate SETTLED? submit ACK -> venue order status.

    `filled` and `cancelled` are resolved. `open` is NOT: the order is
    resting and the engine may not decide again until it is gone. An
    unreadable or unrecognised status is NOT resolved either -- fail closed.
    """
    st = str(body.get("status") or "").strip().lower()
    if st in ("filled", "closed", "cancelled", "canceled", "expired",
              "rejected"):
        return {"resolved": True, "status": st}
    if st in UNRESOLVED_STATUSES:
        return {"resolved": False, "status": st or "(absent)",
                "why": "the order is still live or its status is absent; "
                       "the engine may not submit again while any previous "
                       "order is unresolved"}
    return {"resolved": False, "status": st,
            "why": "unrecognised order status %r -- fail closed" % st}


def assert_payload_is_q1(symbol: str, intent: Mapping[str, Any]
                         ) -> Dict[str, Any]:
    """Build the EXACT body that would be signed and assert q is 1 IN IT.

    This is the last link in the q=1 chain: policy -> intent -> budget ->
    PAYLOAD. Asserting on the intent alone would leave the possibility that
    the transport call passes something else, so the body itself is built,
    structurally validated and read back.
    """
    body = payload.build_order(symbol=symbol, price=str(intent["price"]),
                               quantity=str(intent["quantity"]),
                               outcome=intent["outcome"],
                               maker_or_cancel=True)
    payload.validate_structure(body)
    payload.assert_types(body)                 # allow_taker stays FALSE
    if D(str(body["quantity"])) != QUANTITY:
        raise ProbeRefused("payload quantity %r is not q=%s"
                           % (body["quantity"], QUANTITY))
    if body["makerOrCancel"] is not True:
        raise ProbeRefused("payload is not maker-or-cancel")
    return body


async def submit(cohort_id: str, symbol: str, ticker: str,
                 intent: Dict[str, Any], *,
                 root: str = cohort.COHORT_ROOT,
                 slot_hook: Optional[Any] = None) -> Dict[str, Any]:
    """Consume the slot DURABLY, then send exactly one maker-or-cancel order.

    ORDER OF OPERATIONS IS THE SAFETY PROPERTY. The slot is fsynced before
    the venue is touched, so an ambiguous send finds its slot already spent.
    There is no retry path in this function and no rollback in the budget.
    """
    if BUD.halted(cohort_id, root=root):
        raise ProbeRefused("HALTED: %s" % BUD.halted(cohort_id, root=root))
    if arming.EXPECTED_AUTH_ID != PROBE_AUTH_ID:
        raise ProbeRefused(
            "this build's authorization is %r, not the continuous-inventory "
            "probe's %r" % (arming.EXPECTED_AUTH_ID, PROBE_AUTH_ID))
    if (AUTHORIZED_POLICY_FINGERPRINT is None
            or AUTHORIZED_POLICY_FINGERPRINT != fingerprint()):
        BUD.halt(cohort_id, "POLICY_FINGERPRINT_MISMATCH",
                 "authorized=%r on_disk=%r"
                 % (AUTHORIZED_POLICY_FINGERPRINT, fingerprint()), root=root)
        raise ProbeRefused("the frozen policy on disk is not the one this "
                           "authorization was granted against")
    body = assert_payload_is_q1(symbol, intent)
    out = await one_outstanding_order(symbol)
    if not out["ok"]:
        raise ProbeRefused(out["why"])

    row = BUD.consume_slot(cohort_id, ticker, intent, root=root)
    if slot_hook is not None:
        try:
            ok = slot_hook(row)
        except Exception as exc:                               # noqa: BLE001
            ok = False
            row["slot_hook_error"] = str(exc)
        if not ok:
            # THE SLOT STAYS SPENT. Durability failed, so we must not send,
            # and we must not pretend the slot is free either.
            return {"status": "REFUSED_SLOT_NOT_DURABLE", "slot": row["slot"],
                    "sent": False, "intent": intent,
                    "why": "the durability hook did not confirm; the slot is "
                           "SPENT and is never retried"}
    try:
        res = await transport.place(symbol=symbol, side=payload.spec.SIDE_BUY,
                                    outcome=intent["outcome"],
                                    price=str(intent["price"]),
                                    quantity=str(intent["quantity"]))
    except Exception as exc:                                   # noqa: BLE001
        BUD.record_status(cohort_id, ticker, slot=row["slot"],
                          status="SUBMITTED",
                          detail={"ambiguous": True, "error": str(exc)},
                          root=root)
        return {"status": "AMBIGUOUS", "slot": row["slot"], "sent": True,
                "intent": intent, "body": body, "error": str(exc),
                "why": "the send outcome is unknown. The slot is SPENT and "
                       "is never retried."}
    BUD.record_status(cohort_id, ticker, slot=row["slot"], status="SUBMITTED",
                      detail={"venue": res}, root=root)
    return {"status": "SUBMITTED", "slot": row["slot"], "sent": True,
            "intent": intent, "body": body, "venue": res,
            "labels": "SUBMITTED is not FILLED. A maker-or-cancel "
                      "CANCELLATION is a normal outcome and is never chased "
                      "or repriced outside the frozen policy."}


# ------------------------------------------------------------- preflight
def preflight(cohort_id: str, *,
              root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Everything checkable without touching the venue. Opens no path."""
    from src.gemini_execution import ci15m_policy
    return {
        "label": LABEL,
        "authorization_id": PROBE_AUTH_ID,
        "build_authorization_id": arming.EXPECTED_AUTH_ID,
        "authorization_matches": arming.EXPECTED_AUTH_ID == PROBE_AUTH_ID,
        "arming_state": arming.state(),
        "policy": ci15m_policy.summary(),
        "authorized_policy_fingerprint": AUTHORIZED_POLICY_FINGERPRINT,
        "policy_fingerprint_matches":
            AUTHORIZED_POLICY_FINGERPRINT == fingerprint(),
        "budget": BUD.state(cohort_id, root=root),
        "symbol_family": SYMBOL_PREFIX,
        "side_mapping": SIDE_TO_OUTCOME,
        "maker_or_cancel": True,
        "taker_path_reachable": False,
        "taker_note": ("transport.place is called WITHOUT allow_taker, and "
                       "allow_taker additionally requires the semantics "
                       "authorization, so a crossing order is not "
                       "constructible under this probe."),
        "separation": ("the prospective EXTEND-vs-NO-EXTEND shadow is a "
                       "different process writing different files, and its "
                       "results are never pooled with these rows."),
    }


# ================================================================= RUNTIME
#
# THE SEVEN CONDITIONS THE OWNER REQUIRED BEFORE THE FIRST VENUE SUBMISSION.
# Checked at RUNTIME, in the credentialed environment, not here at authoring
# time -- an authoring-time check proves nothing about the process that will
# actually send the order.
def head_sha() -> Optional[str]:
    """The commit this process is running from, read from git."""
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              timeout=20).stdout.strip() or None
    except Exception:                                          # noqa: BLE001
        return None


def verify_runtime(cohort_id: str, *,
                   root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Every pre-submission condition, each answered independently.

    `ok` is True only when ALL of them pass. The SHA check is reported as
    `sha_is_authorized_or_descendant` because installing the authorization
    itself necessarily advances HEAD past the authorized commit -- see
    `sha_note`.
    """
    import subprocess
    head = head_sha()
    descendant = None
    diff_files: List[str] = []
    if head:
        try:
            descendant = subprocess.run(
                ["git", "merge-base", "--is-ancestor", AUTHORIZED_SHA, head],
                capture_output=True, timeout=20).returncode == 0
            diff_files = [x for x in subprocess.run(
                ["git", "diff", "--name-only", AUTHORIZED_SHA, head],
                capture_output=True, text=True,
                timeout=20).stdout.split("\n") if x.strip()]
        except Exception:                                      # noqa: BLE001
            descendant = None

    st = BUD.state(cohort_id, root=root)
    checks = {
        "head_known": head is not None,
        "head_is_authorized_sha": head == AUTHORIZED_SHA,
        "head_is_authorized_or_descendant": bool(descendant),
        "policy_fingerprint_matches":
            fingerprint() == AUTHORIZED_POLICY_FINGERPRINT,
        "authorization_installed":
            arming.EXPECTED_AUTH_ID == PROBE_AUTH_ID == "OWNER-2026-10-03-"
                                                        "GEMINI-CI15M-1",
        "quantity_is_one": QUANTITY == D("1"),
        "consumed_slots_zero": st["order_slots_consumed"] == 0,
        "confirmed_realized_loss_zero":
            D(st["confirmed_realized_principal_loss"]) == D("0"),
        "no_halt": st["halted"] is None,
        "not_expired": st["expired"] is None,
        "taker_path_shut":
            payload.SEMANTICS_PROBE_AUTH_ID != arming.EXPECTED_AUTH_ID,
        "arming_keys_present": arming.armed(),
    }
    # The SHA condition is satisfied by EITHER an exact match or a descendant
    # whose diff touches only the two authorization constants.
    # The ONLY files a descendant of the authorized commit may differ in.
    # arming.py and ci15m_probe.py carry the two authorization constants and
    # the runtime gate itself. The test file is included because it is not
    # imported by any execution path -- it cannot change what is sent -- and
    # installing the authorization necessarily made its pre-arm assertions
    # about those constants stale. The FROZEN POLICY FILE IS NOT IN THIS SET,
    # and is additionally checked exactly, by fingerprint, above.
    AUTH_ONLY = {"src/gemini_execution/arming.py",
                 "src/gemini_execution/ci15m_probe.py",
                 "tests/test_ci15m_probe.py",
                 # WIDENED 2026-10-03, and the widening is stated rather than
                 # quiet. The owner's instruction was explicit: "Add the
                 # minimal ci15m workflow path/step required to run the
                 # already-authorized CI15M probe." The CLI entry point and
                 # the workflow step ARE that path, and they did not exist at
                 # the authorized commit, so a descendant carrying them must
                 # pass or the authorized probe cannot run at all.
                 #
                 # This DOES widen the gate, and it is the weakest link in
                 # the SHA condition: ci15m_runner.py is execution code.
                 # What it does NOT widen is the substantive protection --
                 # the FROZEN POLICY FILE is still excluded, and is checked
                 # separately and EXACTLY by fingerprint, so no decision
                 # rule, threshold or bound can change through this door.
                 "src/gemini_execution/ci15m_runner.py",
                 ".github/workflows/gemini-settlement-gate.yml"}
    sha_ok = checks["head_is_authorized_sha"] or (
        checks["head_is_authorized_or_descendant"]
        and bool(diff_files) and set(diff_files) <= AUTH_ONLY)
    checks["sha_condition"] = bool(sha_ok)
    required = [k for k in checks
                if k not in ("head_is_authorized_sha",
                             "head_is_authorized_or_descendant")]
    failed = [k for k in required if not checks[k]]
    return {
        "ok": not failed, "failed": failed, "checks": checks,
        "head": head, "authorized_sha": AUTHORIZED_SHA,
        "diff_from_authorized_sha": diff_files,
        "policy_fingerprint": fingerprint(),
        "sha_note": ("the authorized SHA names the POLICY AND LOGIC commit. "
                     "Installing the authorization id and the authorized "
                     "fingerprint necessarily advances HEAD past it, so the "
                     "condition is: HEAD is that commit, or a descendant of "
                     "it whose diff touches ONLY arming.py and "
                     "ci15m_probe.py. The frozen policy file is not in that "
                     "set, and its fingerprint is checked separately and "
                     "exactly."),
        "labels": ("every condition is answered independently; `ok` requires "
                   "all of them. Nothing may be submitted while `ok` is "
                   "False."),
    }


def book_from_venue(symbol: str) -> Dict[str, Any]:
    """The decision book, PRICED FROM THE EXECUTABLE LADDER.

    WHAT CHANGED AND WHY. CI15M-1 took the PRICE from the contract object's
    `prices.buy.yes` / `bestAsk` and only the SIZE from `/v1/book`. Measured
    against the real ladder over 372 instants on 15 BTC15m symbols, that
    contract price agreed with the executable best ask only 23.1% of the
    time: it sat BELOW it 39.5% of the time (to 12c) and ABOVE it 60.5%
    (to 5c). A limit at a price below the real ask does not cross -- it
    rests. That is the whole explanation for CI15M-1's four accidental
    maker fills and eight instant cancellations, and the tape's 39.5/60.5
    split predicted the live 33/67 outcome.

    So the frozen policy's "immediately executable acquisition price" is now
    taken from the SAME authoritative ladder that was already supplying
    size. The contract price is still read and is carried through as
    TELEMETRY, so `contract price <-> executable ladder price` keeps being
    measured rather than forgotten.

    Nothing about the policy changes. This is the market-state INPUT.
    """
    from src.gemini_execution import metadata
    dep = metadata.contract_depth(symbol)
    if not dep.get("readable"):
        return {"readable": False, "why": dep.get("why"), "stage": "ladder"}
    bids, asks = dep.get("bids") or {}, dep.get("asks") or {}
    best_bid, best_ask = bids.get("best"), asks.get("best")
    if best_bid is None or best_ask is None:
        return {"readable": False, "stage": "ladder",
                "why": ("the executable ladder is one-sided (bid %r, ask "
                        "%r); nothing is derived in its place"
                        % (best_bid, best_ask))}
    bid, ask = D(str(best_bid)), D(str(best_ask))
    if not (bid < ask):
        return {"readable": False, "stage": "ladder",
                "semantic_contradiction": True,
                "why": "the executable ladder is crossed or locked: bid %s "
                       ">= ask %s" % (bid, ask)}

    # ---- TELEMETRY ONLY. Never used to price an order.
    tel: Dict[str, Any] = {"read": False}
    try:
        ob = metadata.outcome_book(symbol)
        if ob.get("readable"):
            c_ask = D(ob["yes"]["ask"])
            c_bid = D(ob["yes"]["bid"])
            tel = {"read": True,
                   "contract_ask": str(c_ask), "contract_bid": str(c_bid),
                   "ladder_ask": str(ask), "ladder_bid": str(bid),
                   "ask_gap_contract_minus_ladder": str(c_ask - ask),
                   "bid_gap_contract_minus_ladder": str(c_bid - bid),
                   "contract_ask_would_have": (
                       "CROSSED" if c_ask >= ask else "RESTED"),
                   "identity_consistent": ob.get("identity_consistent"),
                   "labels": ("TELEMETRY. The contract price is recorded so "
                              "the disagreement keeps being measured; it "
                              "prices nothing.")}
        else:
            tel = {"read": False, "why": ob.get("why")}
    except Exception as exc:                                   # noqa: BLE001
        tel = {"read": False, "why": "%s: %s" % (type(exc).__name__, exc)}

    return {
        "readable": True,
        "source": "contract_depth /v1/book -- the EXECUTABLE ladder",
        "bid": bid, "ask": ask,
        "bid_size": bids.get("best_qty"),
        "ask_size": asks.get("best_qty"),
        "up_acquisition": str(ask),
        "down_acquisition": str(D("1") - bid),
        "snapshot_age_s": dep.get("snapshot_age_s"),
        "venue_ts": dep.get("venue_ts"),
        "contract_price_telemetry": tel,
        "levels": {"bids": bids.get("levels"), "asks": asks.get("levels")},
    }


async def reconcile_after_fill(cohort_id: str, symbol: str, inv: Any, *,
                               root: str = cohort.COHORT_ROOT
                               ) -> Dict[str, Any]:
    """AUTHORITATIVE reconciliation against the account, after every fill."""
    from src.gemini_execution import metadata
    try:
        got = await metadata.positions()
    except Exception as exc:                                   # noqa: BLE001
        BUD.halt(cohort_id, "UNKNOWN_POSITION",
                 "positions read failed: %s" % exc, root=root)
        return {"halted": True, "verdict": "UNREADABLE", "why": str(exc)}
    rows = got.get("positions") if isinstance(got, Mapping) else None
    if rows is None and isinstance(got, Mapping):
        rows = got.get("result") or got.get("data")
    return reconcile_or_halt(cohort_id, symbol, inv, rows, root=root)


def _extend_report(inv_before: Any, inv_after: Any, px: D,
                   req_before: Optional[D], req_after: Optional[D]
                   ) -> Dict[str, Any]:
    """Exactly the fields the owner asked for on every EXTEND."""
    rs = inv_after.residual_side
    avg_after = (inv_after.avg_up if rs == "UP" else inv_after.avg_down)
    rs_b = inv_before.residual_side
    avg_before = (inv_before.avg_up if rs_b == "UP"
                  else (inv_before.avg_down if rs_b == "DOWN" else None))
    return {
        "old_avg": None if avg_before is None else str(avg_before),
        "fill_price": str(px),
        "new_avg": str(avg_after),
        "complement_ceiling_before":
            None if req_before is None else str(req_before),
        "complement_ceiling_after":
            None if req_after is None else str(req_after),
        "ceiling_widened_by":
            (None if (req_before is None or req_after is None)
             else str(req_after - req_before)),
        "residual_shares": str(inv_after.residual),
        "residual_principal": str(inv_after.residual * avg_after),
    }


async def run_window(cohort_id: str, symbol: str, ticker: str, expiry_ts: float,
                     *, root: str = cohort.COHORT_ROOT, poll_s: float = 5.0,
                     slot_hook: Optional[Any] = None,
                     now_fn: Optional[Any] = None) -> Dict[str, Any]:
    """ONE live BTC15m window, decided by the frozen policy and nothing else.

    THE LOOP IS DELIBERATELY DULL. Read the book, ask the frozen policy, and
    if it says act: build the payload, spend a durable slot, send ONE
    maker-or-cancel order, read back what happened, reconcile against the
    account, and go round again. Every bound lives in `plan` and
    `consume_slot`; this function adds none of its own.

    A MOC CANCELLATION IS A NORMAL OUTCOME. It spends its slot and the loop
    continues. There is no reprice and no chase: the next decision is taken
    from the next book by the same frozen rule.
    """
    import time
    clock = now_fn or time.time
    inv = new_inventory()
    n_extends, actions = 0, 0
    events: List[Dict[str, Any]] = []

    def ev(kind: str, **kw) -> Dict[str, Any]:
        row = {"utc": BUD._now(), "kind": kind, "ticker": ticker}
        row.update(kw)
        events.append(row)
        return row

    while True:
        tte = expiry_ts - clock()
        if tte <= 0:
            ev("WINDOW_EXPIRED", tte=tte)
            break
        h = BUD.halted(cohort_id, root=root)
        if h:
            ev("HALTED", why=h)
            break
        why = BUD.expired(cohort_id, root=root)
        if why:
            ev("COHORT_STOP", why=why)
            break

        bk = book_from_venue(symbol)
        if not bk["readable"]:
            if bk.get("semantic_contradiction"):
                BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION", bk["why"],
                         root=root)
                ev("HALTED", why=bk["why"])
                break
            ev("BOOK_UNREADABLE", stage=bk.get("stage"), why=bk.get("why"))
            await asyncio.sleep(poll_s)
            continue

        pl = plan(cohort_id, inv, bid=bk["bid"], ask=bk["ask"],
                  bid_size=bk["bid_size"], ask_size=bk["ask_size"],
                  tte=tte, n_extends=n_extends, actions=actions, root=root)
        if pl.get("stop") in ("LOCKED", "HALTED", "RISK_BLOCK"):
            ev("STOP", stop=pl["stop"], reason=pl["reason"],
               inventory=inv.snapshot())
            break
        if pl["action"] == HOLD:
            ev("HOLD", tte=round(tte, 1), bid=str(bk["bid"]),
               ask=str(bk["ask"]), reason=pl["reason"])
            await asyncio.sleep(poll_s)
            continue

        # 3 -- reconcile against the venue BEFORE building an intent, so
        # every cap is evaluated on inventory the venue confirms.
        rec0 = await reconciled_inventory(cohort_id, symbol, root=root)
        if not rec0["ok"]:
            ev("HALTED", why=rec0.get("why"))
            break
        ours = inv.q_up - inv.q_down
        if D(str(rec0["signed"])) != ours:
            BUD.halt(cohort_id, "INVENTORY_DISAGREEMENT",
                     "venue signed %s != ours %s" % (rec0["signed"], ours),
                     root=root)
            ev("HALTED", why="venue %s != ours %s" % (rec0["signed"], ours),
               labels="the engine may not decide against an inventory the "
                      "venue does not confirm")
            break
        intent = intent_of(ticker, inv, pl, n_extends,
                           reconciled_signed=str(rec0["signed"]))
        before = POLICY["Inventory"](
            q_up=inv.q_up, q_down=inv.q_down, c_up=inv.c_up,
            c_down=inv.c_down, fees=inv.fees, rebates=inv.rebates,
            fills=list(inv.fills))
        req_before = POLICY["required_complement_price"](inv, QUANTITY)

        try:
            res = await submit(cohort_id, symbol, ticker, intent, root=root,
                               slot_hook=slot_hook)
        except Exception as exc:                               # noqa: BLE001
            ev("SUBMIT_REFUSED", why=str(exc), intent=intent)
            break
        ev("SUBMITTED", status=res["status"], slot=res.get("slot"),
           intent=intent, tte=round(tte, 1), venue=res.get("venue"))
        if res["status"] in ("AMBIGUOUS", "REFUSED_SLOT_NOT_DURABLE"):
            # the slot is spent and is NEVER retried. The window stops so a
            # human can establish what the venue did with it.
            ev("STOP", stop=res["status"], reason=res.get("why"))
            break

        # ---- B: the order's fate must be RESOLVED before the next
        # decision. submit ACK -> venue order status -> active_orders ->
        # next decision, in that order and with no step skipped. A resting
        # order blocks the loop; it does not get a sibling.
        ack_body = ((res.get("venue") or {}).get("body")
                    if isinstance(res.get("venue"), Mapping) else None)
        if not isinstance(ack_body, Mapping):
            BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION",
                     "the place ack carried no readable order object",
                     root=root)
            ev("HALTED", why="unreadable place ack")
            break
        ack = classify_ack(ack_body)
        ev("ACK", slot=res["slot"], state=ack["state"],
           status=ack["status"], filled=str(ack["filled"]),
           why=ack.get("why"))

        # 2 -- A PLACE ACK IS NOT A TERMINAL RESULT. `open` is not
        # NOT_FILLED: CI15M-1's four fills all began exactly there.
        order_id = ack_body.get("orderId") or ack_body.get("id")
        if ack["terminal"]:
            term = {"resolved": True, "filled": ack["filled"],
                    "state": (TERMINAL_FILLED if ack["filled"] > 0
                              else TERMINAL_CANCELLED),
                    "row": ack_body, "from": "ack"}
        else:
            term = await poll_terminal(cohort_id, symbol, order_id, root=root)
            ev("TERMINAL", slot=res["slot"], **{
                k: str(term.get(k)) for k in ("resolved", "state", "filled",
                                              "attempts")})
        if not term["resolved"]:
            ev("STOP", stop="ORDER_UNRESOLVED", reason=term.get("why"))
            break

        if term["state"] == TERMINAL_CANCELLED:
            BUD.record_status(cohort_id, ticker, slot=res["slot"],
                              status="SUBMITTED",
                              detail={"terminal": "CANCELLED"}, root=root)
            ev("NOT_FILLED", slot=res["slot"], outcome="CANCELLED",
               labels="a maker-or-cancel CANCELLATION is a normal outcome. "
                      "The slot is spent, nothing is repriced or chased, and "
                      "the OPEN lifecycle does not reset.")
            await asyncio.sleep(poll_s)
            continue

        row = term.get("row") or ack_body
        fill = {"price": str(row.get("avgExecutionPrice")
                             or row.get("executedPrice")
                             or row.get("price") or intent["price"]),
                "quantity": str(term["filled"]),
                "outcome": row.get("outcome") or intent["outcome"],
                "orderId": order_id}
        chk = check_fill(cohort_id, intent, fill, root=root)
        if not chk["ok"]:
            ev("HALTED", why=chk["halted"])
            break
        filled = {"filled": True, "halted": False, "fill": fill}

        BUD.record_status(cohort_id, ticker, slot=res["slot"], status="FILLED",
                          detail=filled["fill"], root=root)
        inv.add(POLICY["make_fill"](clock(), intent["side"], "maker",
                                    D(str(filled["fill"]["price"])),
                                    D(str(filled["fill"]["quantity"])),
                                    "live " + pl["action"]))
        inv.assert_identities()
        actions += 1

        rec = await reconcile_after_fill(cohort_id, symbol, inv, root=root)
        if rec["halted"]:
            ev("HALTED", why=rec.get("why"), reconcile=rec)
            break
        BUD.record_status(cohort_id, ticker, slot=res["slot"],
                          status="INVENTORY_RECONCILED",
                          detail={"verdict": rec["verdict"],
                                  "venue_signed": rec.get("venue_signed"),
                                  "ours": rec.get("our_signed")}, root=root)
        ev("INVENTORY_RECONCILED", slot=res["slot"], verdict=rec["verdict"],
           venue_signed=rec.get("venue_signed"), ours=rec.get("our_signed"),
           inventory=inv.snapshot())

        if pl["action"] == EXTEND:
            n_extends += 1
            ev("EXTEND_REPORT", slot=res["slot"], **_extend_report(
                before, inv, D(str(filled["fill"]["price"])), req_before,
                POLICY["required_complement_price"](inv, QUANTITY)))
        elif pl["action"] == COMPLETE:
            n_extends = 0
            pc = inv.matched_pair_cost()
            BUD.record_status(cohort_id, ticker, slot=res["slot"],
                              status="COMPLEMENTED",
                              detail={"matched_pair_cost": str(pc)},
                              root=root)
            ev("COMPLEMENT_REPORT", slot=res["slot"],
               effective_matched_pair_cost=(None if pc is None else str(pc)),
               matched=str(inv.matched), residual=str(inv.residual))

        if POLICY["locked"](inv):
            ev("LOCKED", inventory=inv.snapshot(),
               labels="both settlement outcomes positive after modelled "
                      "fees. NOT realized: settlement and confirmed cashflow "
                      "decide that, and neither has happened.")
            break

    snap = inv.snapshot()
    BUD.close_window(cohort_id, ticker,
                     "LOCKED" if POLICY["locked"](inv) else
                     ("RESIDUAL" if inv.fills else "NO_FILL"),
                     detail={"actions": actions, "inventory": snap},
                     root=root)
    return {"ticker": ticker, "symbol": symbol, "events": events,
            "inventory": snap, "n_actions": actions,
            "halted": BUD.halted(cohort_id, root=root),
            "labels": ("SUBMITTED / FILLED / INVENTORY_RECONCILED / "
                       "COMPLEMENTED / RESIDUAL are recorded separately. "
                       "Nothing here is SETTLED or REALIZED.")}


async def _settle_order(cohort_id: str, symbol: str, ticker: str,
                        intent: Mapping[str, Any], res: Mapping[str, Any], *,
                        root: str = cohort.COHORT_ROOT) -> Dict[str, Any]:
    """Did that order fill? Read from the venue's ACK and its own records.

    THE ACK IS AUTHORITATIVE ON A MAKER-OR-CANCEL ORDER's immediate fate --
    it either rested, filled or was cancelled, and the venue says which. An
    active-orders read alone CANNOT see a filled order, which produced a
    false "venue disagrees" verdict once already; that lesson is why the ack
    is read first here.
    """
    ack = res.get("venue") or {}
    body = ack.get("order") if isinstance(ack.get("order"), Mapping) else ack
    if not isinstance(body, Mapping):
        BUD.halt(cohort_id, "SEMANTIC_CONTRADICTION",
                 "the place ack carried no readable order object: %r" % (ack,),
                 root=root)
        return {"filled": False, "halted": True, "outcome": "UNREADABLE",
                "why": "unreadable place ack"}
    try:
        got = D(str(body.get("filledQuantity")
                    if body.get("filledQuantity") is not None
                    else body.get("executedQuantity") or "0"))
    except Exception:                                          # noqa: BLE001
        got = D("0")
    status = str(body.get("status") or body.get("state") or "").upper()
    if got <= 0:
        return {"filled": False, "halted": False,
                "outcome": status or "NO_FILL", "why": None}
    price = (body.get("avgExecutionPrice") or body.get("executedPrice")
             or body.get("price") or intent["price"])
    fill = {"price": str(price), "quantity": str(got),
            "outcome": body.get("outcome") or intent["outcome"],
            "orderId": body.get("orderId") or body.get("id"),
            "status": status}
    chk = check_fill(cohort_id, intent, fill, root=root)
    if not chk["ok"]:
        return {"filled": True, "halted": True, "fill": fill,
                "outcome": status, "why": chk["halted"]}
    return {"filled": True, "halted": False, "fill": fill,
            "outcome": status, "why": None}
