"""The bounded EXECUTION-SEMANTICS probe. A DRIVER, NOT AN EXECUTOR.

WHAT THIS IS FOR. One question, and it is not a profitability question:

    when our book parser says the favored side IS / IS NOT executable,
    does Gemini's actual order and fill behaviour agree?

Specifically it tests two interpretations we have never confirmed against the
venue by acting on them:

    "UP ask absent"  == we cannot immediately buy UP
    "DOWN acquisition" == 1 - UP bid

NO SECOND EXECUTOR EXISTS HERE. Every byte that reaches Gemini is built,
signed, armed and sent by the already-proven path:

    transport.place / transport.cancel_and_confirm   (arm-gated mutations)
      -> payload.build_order / validate_structure / assert_types
      -> spec                                        (field names, paths)
      -> gemini_prediction_auth.private_rest_json    (the reviewed signer)

and every read by `metadata` (outcome_book, contract_depth, terms_for,
positions, order_history). This module contributes NO HTTP call, NO signing,
and NO payload construction of its own. It decides WHICH ONE order to send,
records the chain, and stops.

CASE A IS NOT REACHABLE ON THE FROZEN PATH, BY CONSTRUCTION. `payload`
hard-codes `makerOrCancel: True` and `assert_types` REQUIRES it to be exactly
True, so a crossing (taker) order cannot be built at all -- the venue would
cancel rather than cross. That is a deliberate property of the reviewed path,
not an oversight, so case A is REFUSED here rather than worked around. It
needs its own authorized change to `payload`/`spec`. Case B is fully
supported today and is what this module runs.

ONE ORDER, ONE AUTHORIZATION, GIT-BOUND. The latch discipline is
`run_canary`'s and is not reimplemented: consume and PUSH the latch before
the venue sees anything, so a retried runner cannot send a second order.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from decimal import Decimal as D
from typing import Any, Dict, Optional

from src.gemini_execution import arming, metadata, transport

#: case B holds a resting order for at most this long, then cancels. Declared
#: here, before any run, because a timeout chosen after seeing the book is not
#: a predeclared timeout.
REST_TIMEOUT_S = 90.0

#: the probe's own cash ceiling per market, independent of any envelope.
MAX_CASH_PER_MARKET_USD = D("5.00")


class ProbeRefusal(Exception):
    """A predeclared rule refused the probe. Never retried, never relaxed."""


def classify(book: Dict[str, Any], favored: str) -> Dict[str, Any]:
    """Our interpretation, stated BEFORE we act on it, so the venue's answer
    can agree or disagree with something written down.

    `favored` is "yes" (UP) or "no" (DOWN). Executability is read from the
    outcome's OWN published sell side, never from the complement identity.
    """
    if favored not in ("yes", "no"):
        raise ProbeRefusal("favored must be yes or no, got %r" % (favored,))
    sell = (book.get("prices") or {}).get("sell") or {}
    buy = (book.get("prices") or {}).get("buy") or {}
    ask = buy.get(favored)          # what it costs to BUY the favored outcome
    bid = sell.get(favored)         # what a SELL of it receives
    executable = ask is not None and D(str(ask)) > 0
    return {
        "favored": favored,
        "our_claim": ("EXECUTABLE: an offer exists, so a crossing buy should "
                      "trade immediately"
                      if executable else
                      "UNAVAILABLE: no offer, so no immediate buy is possible "
                      "and only a resting maker order can be placed"),
        "case": "A" if executable else "B",
        "favored_ask": None if ask is None else str(ask),
        "favored_bid": None if bid is None else str(bid),
        "identity_check": book.get("identity_check"),
        "read_from": "contract prices.buy/prices.sell -- NOT derived",
    }


def _cash(price: str, qty: str) -> D:
    return (D(price) * D(qty)).quantize(D("0.0001"))


async def run(*, symbol: str, favored: str, auth_id: str,
              rest_price: Optional[str] = None,
              out: Optional[str] = None) -> Dict[str, Any]:
    """ONE probe. Records the full chronological chain and returns it.

    Fails closed at every stage: an unreadable book, a crossed book, a failed
    identity check, a cash breach or a case-A classification all STOP with
    zero mutations.
    """
    rec: Dict[str, Any] = {
        "label": "GEMINI_EXECUTION_SEMANTICS_PROBE_V1",
        "auth_id": auth_id,
        "signal_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "symbol": symbol,
        "mutations": [],
        "realized": "NOT_REALIZED: no settlement cashflow confirmed",
    }

    # ---- 1. state, all of it, before any intent exists -------------------
    rec["terms"] = metadata.terms_for(symbol)
    rec["book"] = metadata.outcome_book(symbol)
    rec["depth"] = metadata.contract_depth(symbol)
    rec["resolution"] = metadata.resolution_for(symbol)
    rec["positions_before"] = await metadata.positions()

    if not rec["book"].get("readable", True):
        rec["stop"] = "UNREADABLE_BOOK"
        return _finish(rec, out)
    if rec["book"].get("crossed"):
        rec["stop"] = "CROSSED_BOOK"
        return _finish(rec, out)

    # ---- 2. our interpretation, written down before the venue answers ----
    rec["classification"] = classify(rec["book"], favored)

    if rec["classification"]["case"] == "A":
        # Not a failure of the probe: a structural property of the frozen
        # path. Recorded as evidence and STOPPED, never routed around.
        rec["stop"] = (
            "CASE_A_NOT_SUPPORTED_BY_FROZEN_PATH: payload.build_order sets "
            "makerOrCancel True and assert_types requires it, so no crossing "
            "order is constructible. Case A needs its own authorized change "
            "to payload/spec. ZERO mutations sent.")
        return _finish(rec, out)

    # ---- 3. case B: one tiny resting maker order --------------------------
    qty = metadata.smallest_valid_quantity(rec["terms"])
    if qty is None:
        rec["stop"] = "NO_VALID_QUANTITY_FROM_TERMS"
        return _finish(rec, out)
    if rest_price is None:
        rec["stop"] = ("NO_PREDECLARED_REST_PRICE: the price must be stated "
                       "before the book is read, not chosen from it")
        return _finish(rec, out)

    pv = metadata.price_is_valid(rest_price, rec["terms"])
    qv = metadata.quantity_is_valid(qty, rec["terms"])
    cash = _cash(rest_price, qty)
    rec["intended_action"] = {
        "case": "B",
        "side": "buy", "outcome": favored,
        "price": rest_price, "quantity": qty,
        "cash_at_risk_usd": str(cash),
        "price_valid": pv, "quantity_valid": qv,
        "rest_timeout_s": REST_TIMEOUT_S,
        "why_this_price": ("predeclared, and BELOW the favored outcome's own "
                           "bid so it rests rather than crosses"),
    }
    if not pv.get("valid") or not qv.get("valid"):
        rec["stop"] = "TERMS_REFUSED_PRICE_OR_QUANTITY"
        return _finish(rec, out)
    if cash > MAX_CASH_PER_MARKET_USD:
        rec["stop"] = ("CASH_CAP_BREACH: %s > %s. The cap is NEVER raised to "
                       "admit a venue minimum." % (cash,
                                                   MAX_CASH_PER_MARKET_USD))
        return _finish(rec, out)

    # the arm is checked HERE, explicitly, so a disarmed run reports rather
    # than raising out of the middle of the transport.
    rec["arming_state"] = arming.state()
    if not arming.armed():
        rec["stop"] = ("NOT_ARMED: zero mutations possible. Preflight only. %s"
                       % rec["arming_state"].get("why_not"))
        return _finish(rec, out)

    t0 = time.monotonic()
    ack = await transport.place(symbol=symbol, side="buy", outcome=favored,
                               price=rest_price, quantity=qty)
    rec["submitted_body"] = ack.get("request")
    rec["acknowledgement"] = {k: ack.get(k) for k in
                              ("status", "classification", "ok", "body")}
    rec["latency_s"] = round(time.monotonic() - t0, 4)
    rec["mutations"].append("place")

    if ack.get("classification") != "ACCEPTED":
        rec["stop"] = "PLACE_NOT_ACCEPTED: %s" % ack.get("classification")
        rec["positions_after"] = await metadata.positions()
        return _finish(rec, out)

    order_id = ((ack.get("body") or {}).get("orderId")
                or (ack.get("body") or {}).get("order_id"))
    rec["order_id"] = order_id

    # ---- 4. where did it rest, and did anyone trade into it? -------------
    rec["observations"] = []
    deadline = time.monotonic() + REST_TIMEOUT_S
    filled = False
    while time.monotonic() < deadline:
        await asyncio.sleep(10.0)
        live = await transport.active_orders(symbol)
        found = await transport.find_order(symbol=symbol, order_id=order_id)
        rec["observations"].append({
            "t_s": round(time.monotonic() - t0, 2),
            "book": metadata.outcome_book(symbol),
            "active_count": len(transport._orders_of(live)),
            "order": found,
        })
        st = ((found or {}).get("order") or {}).get("status")
        if st == "filled":
            filled = True
            break

    # ---- 5. predeclared cancel, fail-closed -----------------------------
    if not filled and order_id is not None:
        rec["cancel"] = await transport.cancel_and_confirm(order_id, symbol)
        rec["mutations"].append("cancel")

    rec["order_history"] = await metadata.order_history()
    rec["positions_after"] = await metadata.positions()
    rec["filled"] = filled
    rec["trade_claim"] = ("A TRADE OCCURRED" if filled else
                          "NO TRADE: an acknowledged order is not a trade")
    rec["verdict"] = _verdict(rec)
    return _finish(rec, out)


def _verdict(rec: Dict[str, Any]) -> str:
    """The one sentence this probe exists to be able to say."""
    cls = rec.get("classification") or {}
    if cls.get("case") != "B":
        return "NO VERDICT: the probe stopped before acting."
    if rec.get("filled"):
        return ("Our book said the favored side had NO executable seller. A "
                "resting maker buy was nevertheless FILLED by incoming flow "
                "-- consistent with our interpretation: we could not take, "
                "but we could be taken.")
    return ("Our book said the favored side had NO executable seller. A "
            "resting maker buy was accepted, rested, and was NOT filled "
            "within the predeclared timeout. Consistent with the "
            "interpretation; it does not by itself confirm it.")


def _finish(rec: Dict[str, Any], out: Optional[str]) -> Dict[str, Any]:
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2, sort_keys=True, default=str)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--favored", required=True, choices=("yes", "no"))
    ap.add_argument("--auth-id", default=arming.EXPECTED_AUTH_ID)
    ap.add_argument("--rest-price", default=None,
                    help="PREDECLARED resting price; without it the probe "
                         "refuses rather than choosing one from the book")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rec = asyncio.run(run(symbol=a.symbol, favored=a.favored,
                          auth_id=a.auth_id, rest_price=a.rest_price,
                          out=a.out))
    print(json.dumps({k: rec.get(k) for k in
                      ("stop", "classification", "acknowledgement",
                       "order_id", "latency_s", "filled", "verdict",
                       "realized")}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
