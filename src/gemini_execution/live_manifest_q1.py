"""The q=1 bounded live manifest, GENERATED FROM THE CODE THAT ENFORCES IT.

A manifest that restates its bounds in prose is a second copy of every number
and a second place for each to drift. Every figure below is read from
`live_envelope`, `continuous_probe` and `ws_continuous` at call time, so a
bound that moves moves here too, and the envelope hash changes with it --
which is what invalidates any authorization granted against the old one.

WHAT THE FIRST RUN CAN AND CANNOT ANSWER, said before anything else so it is
not discovered afterwards:

  IT CAN ANSWER       executability at q=1 (a 100x larger order sits in the
                      same queue but is far likelier to fill PARTIALLY);
                      whether the whole cash chain reconciles to the cent;
                      the REALIZED sign of one complete cycle; and -- as a
                      by-product of the position reads -- which position
                      representation the venue actually uses.
  IT CANNOT ANSWER    expectancy. One window is n=1. A single settled cycle
                      is a fact about that cycle, and calling it an edge
                      would be the same error as calling a shadow fill money.

NOTHING HERE ARMS ANYTHING. `ws_continuous.REQUIRED_AUTH_ID`,
`AUTHORIZED_SHA` and `AUTHORIZED_ENVELOPE` are all None in the shipped tree,
and this module has no order path, no session and no credential.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import outstanding_ledger as OL
from src.gemini_execution import position_ledger as PL
from src.gemini_execution import ws_continuous as WC

LABEL = "GEMINI_L5_LIVE_MANIFEST_Q1_V1"

#: the read paths the run depends on. Every one of them RETURNS data and
#: changes nothing; Gemini authenticates a read with POST.
READS = {
    "positions": "POST /v1/prediction-markets/positions",
    "settled": "POST /v1/prediction-markets/positions/settled?withCashOuts=true",
    "trades": "POST /v1/mytrades?symbol=",
    "balances": "POST /v1/balances",
    "transfers": "POST /v1/transfers",
}


def sequence() -> List[Dict[str, Any]]:
    """The whole run, step by step, with the refusal at each step."""
    return [
        {"step": 0, "name": "PREFLIGHT", "mutates": False,
         "does": ["terms accepted", "balances readable -- record STARTING CASH",
                  "positions readable AND FLAT for the target symbol",
                  "active orders empty", "mutation ledger clean",
                  "authorization valid for THIS executable and THIS envelope"],
         "refuses_if": "any read is unreadable, any position or order exists, "
                       "or the authorization does not name the running code. "
                       "An unread venue is never an empty one."},
        {"step": 1, "name": "BIND ONE CONTRACT", "mutates": False,
         "does": ["one BTC5m symbol, TTE in [%s, %s]"
                  % (CP.WITHDRAW_ALL_TTE_S, 300),
                  "WindowGuard binds the symbol and re-checks it every turn",
                  "the process market lease refuses a second symbol"],
         "refuses_if": "a market is already bound in this process"},
        {"step": 2, "name": "RUN FROZEN_L5_NATIVE", "mutates": True,
         "does": ["the frozen policy decides side, price and timing",
                  "maker-or-cancel only; every frame is a BUY",
                  "q=%s per rung" % LE.LIVE_Q1.action_size,
                  "a slot is spent BEFORE the frame goes out",
                  "a rung already resting at the desired price is KEPT free"],
         "refuses_if": "the live envelope refuses the rung, naming every "
                       "bound it breaks"},
        {"step": 3, "name": "RECONCILE AFTER EVERY FILL", "mutates": False,
         "does": ["read positions", "position_ledger.reconcile against ours",
                  "record which representation the venue sent"],
         "refuses_if": "verdict is DISAGREE or UNREADABLE -- cancel "
                       "everything and stop. A position we cannot confirm is "
                       "not a position we may trade on."},
        {"step": 4, "name": "END OF WINDOW", "mutates": True,
         "does": ["TTE < %s: no new OPEN or EXTEND"
                  % CP.NO_NEW_OPEN_EXTEND_TTE_S,
                  "TTE < %s: cancel everything resting, quote nothing"
                  % CP.WITHDRAW_ALL_TTE_S,
                  "no market state for %ss: withdraw and stop (deadman)"
                  % CP.DEADMAN_S],
         "refuses_if": "n/a -- this step only ever removes exposure"},
        {"step": 5, "name": "SETTLEMENT", "mutates": False,
         "does": ["read settled positions with withCashOuts=true",
                  "take payout, costBasis, realizedPnl, netProfit from the "
                  "VENUE's own fields"],
         "refuses_if": "the contract is not observed SETTLED; absence of "
                       "evidence is not evidence"},
        {"step": 6, "name": "CASH", "mutates": False,
         "does": ["read balances -- record ENDING CASH",
                  "read transfers -- rebate credits, reported SEPARATELY",
                  "read /v1/mytrades for the venue's own fee_amount"],
         "refuses_if": "a balance change with no attributable source is not "
                       "attribution and is never called realized"},
        {"step": 7, "name": "THE ONLY NUMBER THAT COUNTS", "mutates": False,
         "does": ["ENDING CASH - STARTING CASH, against the chain below"],
         "refuses_if": "the chain does not close; the gap is REPORTED, never "
                       "absorbed into a P&L line"},
    ]


def reconciliation_chain() -> List[Dict[str, str]]:
    """orderId -> fill -> position -> settlement -> cash, link by link."""
    return [
        {"link": "order", "key": "clientOrderId (ours) + orderId (venue's)",
         "source": "the place ack's result.orderId, recorded in the durable "
                   "mutation ledger BEFORE the frame went out",
         "note": "the two identifiers are not interchangeable: "
                 "reconciliation MATCHES on clientOrderId, cancel TAKES "
                 "orderId"},
        {"link": "fill", "key": "orderId",
         "source": "/v1/mytrades for price, quantity, fee_amount, aggressor",
         "note": "/v1/mytrades carries NO outcome, so the leg is joined from "
                 "our own placement record, never parsed from our "
                 "clientOrderId string"},
        {"link": "position", "key": "instrumentSymbol",
         "source": READS["positions"],
         "note": "reduced to a SIGNED quantity, which is well defined under "
                 "every candidate representation, and compared to ours"},
        {"link": "settlement", "key": "instrumentSymbol",
         "source": READS["settled"],
         "note": "payout, costBasis, realizedPnl, netProfit are the VENUE's "
                 "fields; an omitted field is null, never zero"},
        {"link": "cash", "key": "balance delta + transfers",
         "source": "%s and %s" % (READS["balances"], READS["transfers"]),
         "note": "REALIZED is a cash movement with a source. Rebates are a "
                 "separate ledger and enter nothing until credited"},
    ]


def position_reads() -> List[Dict[str, str]]:
    """The four reads that answer the open representation question."""
    return [
        {"when": "before any order",
         "expect": "flat",
         "answers": "nothing on its own -- a flat ledger agrees with a flat "
                    "venue under every model. It is the control."},
        {"when": "after the FIRST fill (single-sided)",
         "expect": "one row",
         "answers": "the venue's row shape for a position we actually hold"},
        {"when": "after the COMPLETE fill",
         "expect": "THE DECIDING READ",
         "answers": "two rows for one symbol -> model A; one signed row -> "
                    "model B; no row at all -> B or C. "
                    "position_ledger records which."},
        {"when": "after settlement",
         "expect": "a settled row",
         "answers": "whether both legs redeemed separately, and the venue's "
                    "own netProfit for the cycle"},
    ]


def restart_state_machine() -> Dict[str, Any]:
    """Exactly what happens when a process comes back up.

    Every transition is a refusal except the last one, and the ONLY thing that
    releases committed cash is an authoritative active-order read.
    """
    return {
        "order_states": {
            "potentially_live": [
                OL.INTENT, OL.ACCEPTED, OL.PARTIAL, OL.FILLED,
                OL.SUBMISSION_UNKNOWN, OL.CANCEL_REQUESTED, OL.CANCEL_ACKED,
                OL.CANCEL_UNKNOWN],
            "terminal": list(OL.TERMINAL),
            "note": ("FILLED commits no further cash but is still not PROVEN "
                     "gone, so it blocks a new window until a read clears "
                     "it. REJECTED is terminal because the venue itself said "
                     "it did not create the order."),
        },
        "steps": [
            {"n": 1, "at": "process start",
             "does": "OutstandingLedger replays its append-only file. A torn "
                     "final row becomes an order of UNKNOWN fate, never a "
                     "skipped one.",
             "on_failure": "an unreadable row keeps its order potentially "
                           "live"},
            {"n": 2, "at": "recover(), BEFORE anything else",
             "does": "reconcile the durable ledger against the venue's "
                     "authoritative active-order read",
             "on_failure": "read is None or malformed -> UNREADABLE -> "
                           "DO_NOT_TRADE. Nothing is released."},
            {"n": 3, "at": "reconcile",
             "does": "ours still listed -> BLOCKED, commitment refreshed "
                     "from the venue's own filledQuantity. Ours not listed "
                     "-> VENUE_ABSENT, released. An active order we do NOT "
                     "recognise -> BLOCKED, and its notional counts.",
             "on_failure": "BLOCKED -> DO_NOT_TRADE, and nothing further is "
                           "settled"},
            {"n": 4, "at": "after a CLEAN read only",
             "does": "each released order's dangling mutation attempt is "
                     "settled by client order id",
             "on_failure": "the slot is NOT freed; consumed budget never "
                           "re-opens"},
            {"n": 5, "at": "the rest of recover()",
             "does": "inventory, working set and consumed budget, each from "
                     "the venue plus the durable ledger",
             "on_failure": "any one unreadable -> DO_NOT_TRADE"},
            {"n": 6, "at": "bind()",
             "does": "re-asserts that nothing is potentially live, then takes "
                     "the single-market lease",
             "on_failure": "anything outstanding -> DO_NOT_TRADE"},
            {"n": 7, "at": "place()",
             "does": "filled spend + in-process resting + DURABLE "
                     "OUTSTANDING (de-duplicated against the working set) + "
                     "this rung, against max_committed_usd",
             "on_failure": "unreadable durable total -> DO_NOT_TRADE; over "
                           "the cap -> the rung is refused"},
        ],
    }


def safety() -> List[Dict[str, str]]:
    """Each requirement, what enforces it, and the test that drives it."""
    t = "tests/test_gemini_live_envelope_q1.py"
    return [
        {"requirement": "the action budget actually binds",
         "enforced_by": "MutationLedger.attempt before the frame + "
                        "Driver.budget_consumed + WindowGuard.actions_taken",
         "proved_by": "%s::test_the_action_budget_binds_on_the_live_path"
                      " and ::test_a_spent_slot_survives_a_restart" % t},
        {"requirement": "total dollar exposure on the live order path",
         "enforced_by": "live_envelope.would_breach: FILLED spend + RESTING "
                        "commitments + the rung being sent, against "
                        "max_committed_usd",
         "proved_by": "%s::test_resting_orders_are_counted_as_committed_cash"
                      " and ::test_the_old_check_would_have_let_that_through"
                      % t},
        {"requirement": "the gate measures the rung that is actually sent",
         "enforced_by": "qty is a REQUIRED keyword with no default; a rung "
                        "larger than the envelope size is refused",
         "proved_by": "%s::test_the_gate_uses_the_rungs_own_qty_not_a_constant"
                      % t},
        {"requirement": "maximum residual DOLLAR exposure",
         "enforced_by": "max_residual_usd, priced at the leg's average COST",
         "proved_by": "%s::test_residual_is_bounded_in_dollars_not_only_"
                      "contracts and ::test_residual_is_priced_at_cost_not_"
                      "at_a_mark" % t},
        {"requirement": "one concurrent BTC5m market maximum",
         "enforced_by": "ws_continuous._BOUND_MARKETS process lease + "
                        "WindowGuard symbol binding + the cohort prefix check "
                        "in the constructor",
         "proved_by": "%s::test_a_second_market_cannot_be_bound_in_this_"
                      "process and ::test_the_lease_is_released_when_the_"
                      "driver_stops" % t},
        {"requirement": "deadman and cancellation",
         "enforced_by": "WITHDRAW_ALL_TTE_S floor, DEADMAN_S stale-state "
                        "stop, cleanup budget separate from the strategy "
                        "budget, cancel refuses to send without a venue "
                        "orderId",
         "proved_by": "%s::test_below_the_withdraw_floor_everything_resting_"
                      "is_cancelled and ::test_a_cancel_without_a_venue_"
                      "order_id_is_refused_not_sent" % t},
        {"requirement": "outstanding orders survive a restart",
         "enforced_by": "outstanding_ledger: fsynced INTENT before the frame; "
                        "only an authoritative active-order read releases "
                        "committed cash; a cancel ack is a receipt",
         "proved_by": ("tests/test_gemini_outstanding_ledger_restart.py::"
                       "test_the_reported_sequence_refuses_order_C_after_a_"
                       "restart and ::test_restart_with_an_unreadable_active_"
                       "order_read_sends_nothing")},
        {"requirement": "the $2.50 and $2.00 bounds hold exhaustively",
         "enforced_by": "live_envelope.would_breach, with durable "
                        "outstanding commitment as a term",
         "proved_by": ("tests/test_gemini_outstanding_ledger_restart.py::"
                       "test_no_permitted_rung_can_push_committed_cash_past_"
                       "2_50 and ::test_no_permitted_rung_can_push_"
                       "directional_residual_past_2_00")},
        {"requirement": "the ledger matches the venue's representation",
         "enforced_by": "position_ledger.reconcile on the SIGNED quantity; "
                        "UNREADABLE and DISAGREE are both refusals",
         "proved_by": "%s::test_both_venue_shapes_reduce_to_the_same_signed_"
                      "quantity and ::test_a_disagreeing_venue_stops_us" % t},
        {"requirement": "nothing is armed",
         "enforced_by": "REQUIRED_AUTH_ID / AUTHORIZED_SHA / "
                        "AUTHORIZED_ENVELOPE are all None, and an "
                        "authorization names an executable AND an envelope",
         "proved_by": "%s::test_the_shipped_driver_cannot_mutate_anything"
                      % t},
    ]


def decision_rule() -> Dict[str, Any]:
    return {
        "after": "one settled q=1 cycle with a closed cash chain",
        "continue_if": [
            "the cash chain closes to the cent",
            "the position representation is resolved",
            "realized cash is positive, OR negative by less than the "
            "rebate/fee structure explains and repeatable",
        ],
        "terminate_if": [
            "no fill is obtainable at q=1",
            "the cash chain does not close",
            "the venue representation is incompatible with the process",
            "realized cash is negative in a way the mechanism explains",
        ],
        "not_a_reason_to_continue": [
            "a favourable markout", "a pair basis below 1.00",
            "a shadow fill", "an estimated rebate", "a mark-to-market gain",
        ],
    }


def manifest() -> Dict[str, Any]:
    return {
        "label": LABEL,
        "policy": "FROZEN_L5_NATIVE -- unchanged, and no filter added",
        "cohort": "BTC5m only",
        "envelope": LE.LIVE_Q1.as_dict(),
        "envelope_sha": LE.LIVE_Q1.sha(),
        "driver_envelope_sha": WC.envelope_sha(),
        "worst_case_cash_at_risk_usd": str(LE.LIVE_Q1.worst_case_loss_usd),
        "armed": WC.authorized(),
        "why_not_armed": WC.authorization_state()["why_not"],
        "reads": READS,
        "sequence": sequence(),
        "position_reads": position_reads(),
        "reconciliation_chain": reconciliation_chain(),
        "restart_state_machine": restart_state_machine(),
        "safety": safety(),
        "decision_rule": decision_rule(),
        "models": PL.MODEL_OF,
        "labels": [
            "The only outcome is CASH: starting cash -> fills and fees -> "
            "settlement -> ending cash. Rebates are reported separately and "
            "are never netted into it.",
            "No mark-to-market. No theoretical pair profit. No shadow P&L "
            "substituted for cash.",
            "One window is n=1 and cannot establish expectancy.",
        ],
    }


def main() -> int:
    print(json.dumps(manifest(), indent=1, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
