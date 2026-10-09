"""Which balance field does the venue admit orders against? PREVIEW ONLY.

THE QUESTION. The account reads buyingPower 250.07425 and displayedCash
50.07425, split by a depositReservation of 200. Our entire executor admits
against `buyingPower` (l5_pmus_transport.buying_power, and the same in
pmus_q1_executor, _v4, the continuous session and the preflight); the string
"displayedCash" appears in NO line of our code. But we have never observed a
window where those two fields differ, so which one the VENUE enforces has
never been tested.

THE DESIGN, two previews and nothing else:
  A  notional just ABOVE displayedCash, below buyingPower
  B  notional just ABOVE buyingPower                         (the control)

  A passes, B fails  -> the venue validates funds AND recognises buyingPower
  A fails            -> displayedCash governs (but see the caveat below)
  both pass          -> preview does not check funds at all; A proved nothing
  both fail          -> body invalid, or funds checked against something else

B IS WHAT MAKES A INTERPRETABLE. Without it, a pass on A could simply mean
the endpoint ignores balances.

NO ORDER IS CREATED. The order-creation endpoint is never called and no
string constant naming it exists here; a structural test asserts that by
walking the AST rather than grepping prose. The transport is never armed -- irrelevant anyway, since
the arm gate sits on submit()/cancel() and not on _request -- and neither
submit nor cancel is called. Maximum possible loss: $0.00.

CAVEAT, carried from analysis/l5/ISSUE_93_REJECTION_FINDINGS.md: across 48
real rejections the venue's reason was uninformative every time
(ORDER_REJECTED_ZERO_FILL, no cause field). A rejection here is therefore
recorded VERBATIM and interpreted cautiously; an acceptance is the decisive
outcome, a rejection is a hint.

Balances are read immediately before and after, because a venue that
reserved against a preview would show it there and that would itself be a
finding.
"""
from __future__ import annotations

import json
import math
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, Optional

from src import l5_order_body as OB
from src import l5_pmus_transport as TP
from src import l5_window_actions as WA

#: how far above each balance the two probes aim.
OVER_BY = D("1.00")
PREVIEW_PATH = "/v1/order/preview"


def _bal(tp) -> Dict[str, Any]:
    try:
        raw = tp.account()
    except BaseException as exc:                               # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    rows = (raw or {}).get("balances") or []
    r = rows[0] if rows else {}
    return {k: r.get(k) for k in
            ("currentBalance", "buyingPower", "displayedCash",
             "availableToWithdraw", "balanceReservation",
             "depositReservation", "openOrders", "unsettledFunds")}


def _touch(tp, slug: str) -> Dict[str, Any]:
    try:
        b = tp.book(slug) if hasattr(tp, "book") else {}
    except BaseException as exc:                               # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    return b or {}


def preview(tp, *, slug: str, side: str, price: D, qty: D) -> Dict[str, Any]:
    """One preview call. Returns the venue's answer or its refusal, verbatim."""
    body = OB.build(slug=slug, side=side, price=price, qty=qty,
                    post_only=True)
    env = OB.preview_envelope(body)
    out: Dict[str, Any] = {"body": body, "notional_usd": str(price * qty)}
    try:
        resp = tp._request("POST", PREVIEW_PATH, env)
        out["accepted"] = True
        out["response"] = resp
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        detail = ""
        rd = getattr(exc, "read", None)
        if callable(rd):
            try:
                detail = rd().decode("utf-8", "replace")[:600]
            except BaseException:                              # noqa: BLE001
                detail = ""
        out["accepted"] = False
        out["error_type"] = type(exc).__name__
        out["error"] = str(exc)[:300]
        out["http_status"] = getattr(exc, "code", None)
        out["body_returned"] = detail
    return out


def main() -> int:
    slug = os.getenv("L5_PROBE_MARKET", "").strip() or WA.current_slug()
    tp = TP.PmusTransport(market_slug=slug)

    # THE SANCTIONED PATH, and nothing else. PmusTransport defaults its http
    # to ReadOnlyTransport, which raises on any non-GET -- the first run of
    # this probe was refused there and never reached the wire, which is the
    # guard working. `_SignedHTTP` is constructed ONLY inside arm(), and this
    # module does not name it: preflight must pass and the owner arm trio
    # must be present, exactly as for a real order.
    #
    # The arm identity here is the PROBE's own, never R7's, and arming does
    # not touch the authorization ledger: that is l5_live_authorization.claim,
    # which is not imported here and never called. R7's single-use token is
    # untouched.
    pf = {}
    armed = {}
    try:
        pf = tp.preflight(expect_account=os.getenv(
            "PMUS_PREFLIGHT_ACCOUNT", "").strip() or None)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        pf = {"preflight": "RAISED",
              "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    # NOT ARMED, AND NO LONGER ASKED TO BE. This probe calls only the preview
    # endpoint through `_request`; submit() and cancel() are never reached and
    # the arm gate sits on those, so arming bought nothing while breaking the
    # single-arming-surface invariant that tests/test_l5_live_oneshot.py
    # asserts by walking the AST: only l5_pmus_live_oneshot may arm. The
    # module docstring above always said "the transport is never armed"; the
    # code disagreed with it, and the code was wrong.
    armed = {"armed": False, "attempted": False,
             "why": "preview-only probe; the arm gate guards submit/cancel, neither of which is called here"}

    before = _bal(tp)
    bp = D(str(before.get("buyingPower") or 0))
    dc = D(str(before.get("displayedCash") or 0))

    # A passive BUY at a price the book supports. Price is in the outcome's
    # own terms; the body builder does any inversion.
    px = D(os.getenv("L5_PROBE_PX", "0.50"))
    def qty_for(target: D) -> D:
        return D(str(math.ceil(float(target / px))))

    target_a = dc + OVER_BY          # above displayedCash, below buyingPower
    target_b = bp + OVER_BY          # above buyingPower: the control

    a = preview(tp, slug=slug, side="up", price=px, qty=qty_for(target_a))
    b = preview(tp, slug=slug, side="up", price=px, qty=qty_for(target_b))
    after = _bal(tp)
    # Nothing to disarm: this process never armed. Reported explicitly so a
    # reader cannot mistake it for a disarm that silently failed.
    disarmed = {"disarmed": True, "attempted": False, "why": "never armed"}

    reached_wire = (a.get("error_type") != "PermissionError"
                    and b.get("error_type") != "PermissionError")
    if not reached_wire:
        verdict = "NEVER_REACHED_THE_VENUE__OUR_OWN_GUARD_REFUSED"
    elif a.get("accepted") and not b.get("accepted"):
        verdict = "BUYING_POWER_GOVERNS"
    elif a.get("accepted") and b.get("accepted"):
        verdict = "PREVIEW_DOES_NOT_CHECK_FUNDS__INCONCLUSIVE"
    elif not a.get("accepted") and not b.get("accepted"):
        verdict = "BOTH_REFUSED__INCONCLUSIVE"
    else:
        verdict = "A_REFUSED__DISPLAYED_CASH_MAY_GOVERN"

    out = {
        "LABEL": "BALANCE_ADMISSION_PREVIEW_PROBE",
        "orders_created": 0, "armed": False, "max_possible_loss_usd": "0.00",
        "market": slug, "probe_price": str(px),
        "preflight": pf.get("preflight"), "arm": armed,
        "disarmed": disarmed, "reached_venue": reached_wire,
        "r7_authorization_touched": False,
        "balances_before": before, "balances_after": after,
        "balances_unchanged": before == after,
        "A_above_displayed_cash": {"target_usd": str(target_a), **a},
        "B_above_buying_power": {"target_usd": str(target_b), **b},
        "verdict": verdict,
        "labels": ("PREVIEW ONLY -- the order-creation endpoint is never "
                   "called. A rejection reason from this "
                   "venue is known to be uninformative (48/48 in "
                   "ISSUE_93_REJECTION_FINDINGS), so acceptance is decisive "
                   "and refusal is only a hint."),
    }
    print("BALANCE_ADMISSION " + json.dumps(out, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
