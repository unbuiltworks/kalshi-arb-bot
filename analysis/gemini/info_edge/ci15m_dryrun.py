"""DRY RUN of the bounded BTC15m continuous-inventory probe. NO VENUE.

It exercises the REAL probe module -- the real selector, the real frozen
policy, the real durable budget, the real reconciliation and the real payload
builder -- against a STUB venue that records what it was asked for and never
opens a socket. `transport.place` is not called and cannot be: the probe is
unarmed, and this script substitutes the submit step with the payload build
plus the budget consume, which is everything up to the wire.

WHAT IT PROVES: the state machine walks WS -> OPEN -> reconcile -> EXTEND ->
reconcile -> COMPLEMENT -> LOCK, that each status is recorded separately, and
that a LOCKED book then refuses to act.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from decimal import Decimal as D

sys.path.insert(0, ".")

from src.gemini_execution import ci15m_budget as BUD          # noqa: E402
from src.gemini_execution import ci15m_probe as P             # noqa: E402
from src.gemini_execution.ci15m_policy import (POLICY,        # noqa: E402
                                               QUANTITY, fingerprint)
from src.gemini_execution.continuous_probe import (COMPLETE,  # noqa: E402
                                                   EXTEND, HOLD, OPEN)
from src.gemini_execution.ci15m_sim import simulate  # noqa: E402
from src.gemini_execution.ci15m_sim import CANCELLED, FILLED, RESTING  # noqa

CID = "CI15M-DRYRUN"
SYMBOL = "GEMI-BTC15M2610031530-UP"
TICKER = "BTC15M2610031530"

#: a WS book timeline chosen to exercise EVERY transition once: an OPEN, a
#: 5c-cheaper EXTEND, a refusal in between, and a complement that lands the
#: pair under 0.96. These are prices, not predictions.
#: NOTE ON ORDERING, which this tape made visible: `decide_once` evaluates
#: the UP candidate before the DOWN one, so at a tick where BOTH an EXTEND
#: and a COMPLEMENT qualify, the EXTEND is taken. That is a property of the
#: frozen policy, not of this harness, and it is reported rather than
#: worked around here.
TAPE = [
    # tte,   bid,    ask,   bid_sz, ask_sz, note
    (820.0, "0.30", "0.40", 500, 500, "OPEN UP at the ask (0.40)"),
    (800.0, "0.30", "0.40", 500, 500, "same price -- EXTEND refused, 0c"),
    (700.0, "0.32", "0.38", 500, 500, "2c better than 0.40 -- refused"),
    (600.0, "0.26", "0.34", 500, 500, "6c better -- EXTEND #1, avg -> 0.37"),
    (500.0, "0.28", "0.36", 500, 500, "1c better than 0.37 -- refused"),
    (450.0, "0.10", "0.20", 500, 500, "17c better -- EXTEND #2, avg 0.3133"),
    (400.0, "0.38", "0.40", 500, 500, "DOWN 0.62, pair 0.9333 -- COMPLEMENT"),
    (350.0, "0.38", "0.40", 500, 500, "COMPLEMENT again (partials allowed)"),
    (300.0, "0.38", "0.40", 500, 500, "COMPLEMENT the last share -> LOCK"),
    (250.0, "0.10", "0.20", 500, 500, "LOCKED: nothing may act, even at 0.20"),
    (200.0, "0.38", "0.40", 500, 500, "still LOCKED, never unlocked"),
]


def venue_position_rows(inv):
    """The STUB venue's authoritative position: exactly what we believe.

    A dry run must not fake agreement into existence, so this is derived from
    the inventory and the disagreement case is exercised separately at the
    end rather than being impossible here.
    """
    rows = []
    if inv.q_up:
        rows.append({"symbol": SYMBOL, "outcome": "yes",
                     "totalQuantity": str(inv.q_up)})
    if inv.q_down:
        rows.append({"symbol": SYMBOL, "outcome": "no",
                     "totalQuantity": str(inv.q_down)})
    return rows


def main():
    root = tempfile.mkdtemp(prefix="ci15m_dryrun_")
    try:
        BUD.create(CID, root=root, note="DRY RUN -- no venue")
        print("=" * 100)
        print("BTC15m CONTINUOUS-INVENTORY PROBE -- DRY RUN. NO VENUE IS "
              "CONTACTED.")
        print("=" * 100)
        print("policy fingerprint : %s" % fingerprint())
        print("authorization id   : %s" % P.PROBE_AUTH_ID)
        print("q                  : %s (fixed)" % QUANTITY)
        print("order budget       : %d/window, %d total (envelope ceiling)"
              % (BUD.ORDERS_PER_WINDOW, BUD.TOTAL_ORDER_BUDGET))
        print()

        # --- the selector, on a real-shaped slate
        sel = P.select_market([
            {"symbol": SYMBOL,
             "strike": {"value": "121000", "availableAt": 0.0},
             "expiry_ts": 800.0},
            {"symbol": "GEMI-BTC05M2610031530-UP",
             "strike": {"value": "121000", "availableAt": 0.0},
             "expiry_ts": 200.0},
        ], now=0.0)
        print("SELECTOR: %s" % sel["rule"])
        for c in sel["considered"]:
            print("  %-32s eligible=%-5s %s"
                  % (c["symbol"], c["eligible"],
                     "" if c["eligible"] else "; ".join(c["why"])))
        print("  --> selected %s\n" % sel["selected"]["ticker"])

        BUD.open_window(CID, TICKER, root=root)
        inv = P.new_inventory()
        n_extends, actions = 0, 0

        print("%-6s %-11s %-9s %-5s %-6s %-3s %-5s %-5s %-8s %-9s %s"
              % ("tte", "bid/ask", "action", "side", "price", "q", "UPq",
                 "DNq", "resid", "pair", "status / reason"))
        print("-" * 118)

        for (tte, bid, ask, bsz, asz, note) in TAPE:
            pl = P.plan(CID, inv, bid=D(bid), ask=D(ask), bid_size=bsz,
                        ask_size=asz, tte=tte, n_extends=n_extends,
                        actions=actions, root=root)
            act = pl["action"]
            pair = inv.matched_pair_cost()

            def line(a, side="", px="", status=""):
                print("%-6.0f %-11s %-9s %-5s %-6s %-3s %-5s %-5s %-8s %-9s %s"
                      % (tte, "%s/%s" % (bid, ask), a, side, px,
                         "" if not side else str(QUANTITY),
                         inv.q_up, inv.q_down, inv.residual,
                         "-" if pair is None else str(pair), status))

            if act == HOLD:
                line(HOLD, status="%s | %s" % (pl.get("stop") or "WAIT",
                                               pl["reason"][:140]))
                continue

            intent = P.intent_of(TICKER, inv, pl, n_extends)

            # --- the last link before the wire: the payload itself
            body = P.assert_payload_is_q1(SYMBOL, intent)
            assert body["quantity"] == "1" and body["makerOrCancel"] is True

            # --- the slot is spent DURABLY before the venue.
            # remote=None keeps this dry run offline; the DURABLE git path
            # is proved separately in tests/test_ci15m_durable_budget.py
            # against real refs, because a mocked read is exactly what
            # failed to catch the original defect.
            try:
                row = BUD.consume_slot(CID, TICKER, intent, root=root,
                                       remote=None)
            except BUD.BudgetRefused as exc:
                line(act, intent["side"], intent["price"],
                     "REFUSED (no slot spent) -- %s" % str(exc)[:150])
                continue
            BUD.record_status(CID, TICKER, slot=row["slot"],
                              status="SUBMITTED",
                              detail={"payload": body}, root=root)
            line(act, intent["side"], intent["price"],
                 "SUBMITTED slot %d | %s" % (row["slot"], pl["reason"][:110]))

            # --- THE SIMULATOR, not a stub that fills anything.
            #
            # The old stub filled at the limit unconditionally, which is why
            # this dry run reported a clean LOCK while the live probe filled
            # NOTHING: the policy picks a crossing price and the transport
            # sent post-only, so the venue cancelled every order. The
            # simulator now refuses exactly as the venue does.
            sim = simulate(side=intent["side"], price=D(intent["price"]),
                           qty=QUANTITY,
                           maker_or_cancel=intent["maker_or_cancel"],
                           bid=D(bid), ask=D(ask), bid_size=D(str(bsz)),
                           ask_size=D(str(asz)))
            if sim["outcome"] != FILLED:
                BUD.record_status(CID, TICKER, slot=row["slot"],
                                  status="SUBMITTED",
                                  detail={"sim": sim}, root=root)
                line("", status="%s -- %s" % (sim["outcome"],
                                              sim["why"][:120]))
                continue
            fill = {"price": str(sim["fill_price"]),
                    "outcome": intent["outcome"],
                    "quantity": str(sim["filled_qty"])}
            chk = P.check_fill(CID, intent, fill, root=root)
            assert chk["ok"], chk
            BUD.record_status(CID, TICKER, slot=row["slot"], status="FILLED",
                              detail=fill, root=root)

            inv.add(POLICY["make_fill"](0.0, intent["side"],
                                        ("maker" if intent["maker_or_cancel"]
                                         else "taker"),
                                        D(fill["price"]),
                                        D(fill["quantity"]),
                                        "dryrun " + act))
            inv.assert_identities()
            actions += 1
            if act == EXTEND:
                n_extends += 1
            elif act == COMPLETE:
                n_extends = 0

            # --- AUTHORITATIVE reconciliation after EVERY fill
            rec = P.reconcile_or_halt(CID, SYMBOL, inv,
                                      venue_position_rows(inv), root=root)
            assert not rec["halted"], rec
            BUD.record_status(CID, TICKER, slot=row["slot"],
                              status="INVENTORY_RECONCILED",
                              detail={"verdict": rec["verdict"],
                                      "venue_signed": rec["venue_signed"],
                                      "ours": rec["our_signed"]}, root=root)
            line("", status="FILLED @ %s -> INVENTORY_RECONCILED (%s: venue "
                            "%s == ours %s)"
                            % (fill["price"], rec["verdict"],
                               rec["venue_signed"], rec["our_signed"]))

            if act == EXTEND:
                req = POLICY["required_complement_price"](inv, QUANTITY)
                line("", status="EXTEND measurement: residual avg now %s, "
                                "required complement for 0.96 now %s"
                                % (inv.avg_up or inv.avg_down, req))
            if POLICY["locked"](inv):
                BUD.record_status(CID, TICKER, slot=row["slot"],
                                  status="COMPLEMENTED",
                                  detail={"locked": True}, root=root)
                line("", status="LOCKED: ifUP %s ifDOWN %s -- both positive "
                                "after fees" % (inv.up_settlement_pnl,
                                                inv.down_settlement_pnl))

        print()
        print("FINAL INVENTORY")
        snap = inv.snapshot()
        for k in ("UP_qty", "DOWN_qty", "total_cost", "matched",
                  "residual_qty", "matched_pair_cost", "UP_settlement_PnL",
                  "DOWN_settlement_PnL", "worst_case", "state"):
            if k in snap:
                print("  %-22s %s" % (k, snap[k]))
        print()

        # --- a LOCKED book must refuse, and the negative cases must HALT
        # the CI15M-1 reality check: under maker-or-cancel the frozen
        # policy's crossing price can never fill, so this walk must show
        # CANCELLED throughout rather than a LOCK.
        locked_now = POLICY["locked"](inv)
        pl = P.plan(CID, inv, bid=D("0.10"), ask=D("0.20"), bid_size=9999,
                    ask_size=9999, tte=240.0, n_extends=0, actions=actions,
                    root=root)
        print("LOCKED              : %s (ifUP %s / ifDOWN %s)"
              % (locked_now, inv.up_settlement_pnl, inv.down_settlement_pnl))
        print("LOCKED CHECK        : offered UP at 0.20 against a %s average "
              "-- action=%s stop=%s"
              % (inv.avg_up, pl["action"], pl["stop"]))
        if locked_now:
            assert pl["action"] == HOLD and pl["stop"] == "LOCKED", (
                "a LOCKED book must refuse even a cheap EXTEND")
        else:
            print("NOT LOCKED           : nothing filled. Under "
                  "maker-or-cancel the frozen policy's crossing price "
                  "cannot fill -- which is exactly what CI15M-1 measured "
                  "live, twelve orders for zero fills.")
        bad = P.check_fill(CID, {"price": "0.40", "side": "UP"},
                           {"price": "0.99", "outcome": "yes",
                            "quantity": "1"}, root=root)
        print("FILL-ABOVE-LIMIT    : ok=%s halt written=%s"
              % (bad["ok"], bool(BUD.halted(CID, root=root))))

        print()
        print("DURABLE LEDGER, in order (what a restart would read back):")
        for r in BUD._rows(BUD._paths(CID, root)["ledger"]):
            if r.get("kind") == "PLACE":
                print("  slot %d PLACE   %-8s %-5s @ %-5s q=%s"
                      % (r["slot"], r["intent"]["action"],
                         r["intent"]["side"], r["intent"]["price"],
                         r["intent"]["quantity"]))
            else:
                print("  slot %d STATUS  %s" % (r["slot"], r["status"]))
        st = BUD.state(CID, root=root)
        print()
        print("BUDGET AFTER THE DRY RUN")
        for k in ("window_budget", "windows_completed", "orders_per_window",
                  "total_order_budget", "order_slots_consumed",
                  "max_unmatched_shares", "max_unmatched_principal",
                  "structural_max_loss", "realized_loss_stop",
                  "confirmed_realized_principal_loss", "expired"):
            print("  %-36s %s" % (k, st[k]))
        print()
        print("NO VENUE WAS CONTACTED. transport.place was never called and "
              "the probe is unarmed.")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
