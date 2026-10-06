"""LIVE-L5-EXEC-1 — frozen spec. DISARMED.

A single-leg EXECUTION-MEASUREMENT experiment. Its only purpose is to get
ONE genuine minimum-size near-touch maker order into the book and record
what the venue does with it.

WHAT THIS IS NOT:
  * NOT a profitable strategy, and not represented as one;
  * NOT evidence that a pair cost of 0.99 is economically attractive;
  * NOT a test of L5's economics -- it is one leg, so matched-pair
    economics are N/A and are reported as N/A, not as zero;
  * NOT the economically reserved candidate. That is LIVE-L5-MICRO-1
    (0522b0da...9d1b31), preserved unchanged and still unarmed.

WHY IT CARRIES NO PAIR-COST RESERVATION. The 0.96 reservation is OUR
PAIR_ECONOMICS design choice, not an observed L5 rule, and the historical
work REFUTED a hard L5 pair-cost ceiling (running pair-cost control =
REFUTED; ~0.98 is ambient complement-sum arithmetic). Requiring an
unvalidated profitability condition before measuring execution would
prevent the measurement entirely: at a 0.01 spread the mechanically
available pair cost is ~0.99, so a 0.96 gate refuses to quote. This
experiment therefore asks a different question and accepts that its one
tiny order may lose its whole notional.

The two questions must not be contaminated:
  S3 / reserved candidate -> does the frozen 0.96 reservation find
                             qualifying economic opportunities?
  LIVE-L5-EXEC-1          -> what happens to our actual minimum-size
                             near-touch maker order?

ZERO ARMING BY DEFAULT. NOTHING HERE CAN SUBMIT OR SIGN.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal as D
from typing import Any, Dict

PROBE = "LIVE-L5-EXEC-1"

#: venue grid bounds, from the venue's own metadata -- not design choices
VENUE_MIN_QTY = D("0.01")          # observed minimumTradeQty on BTC15m
VENUE_MAX_PRICE = D("0.99")        # the venue's own top of the 0.01 grid

#: exactly one minimum-sized order, so the loss bound is closed arithmetic
MAX_POSSIBLE_LOSS_USD = (VENUE_MIN_QTY * VENUE_MAX_PRICE).quantize(D("0.0001"))

SPEC: Dict[str, Any] = {
    "probe": PROBE,
    "spec_version": 1,
    "experiment_class": "EXECUTION_MEASUREMENT",
    "armed_by_default": False,
    "is_profitable_strategy_claim": False,
    "is_evidence_pair_cost_099_is_attractive": False,

    "question": ("does a genuine minimum-size near-touch maker order "
                 "rest/fill, how long does it rest, what happens ahead of "
                 "it in the book, is the resulting execution "
                 "venue-confirmed passive, and what are its "
                 "+1/+5/+10/+30/+60/settlement markouts?"),

    "does_not_contaminate": {
        "s3_fingerprint":
            "8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df",
        "s3_question": ("does the frozen 0.96 reservation find qualifying "
                        "economic opportunities?"),
        "reserved_micro_fingerprint":
            "0522b0dac3fc5dec5ec0a01c6dbbb6369fde3f7062cede16f965fdfc459d1b31",
        "probe_1_fingerprint":
            "69904fd4e64bcc62093ce70c1b63b5d825548f5d495eda63744fc91169919118",
        "all_preserved_unchanged": True,
    },

    # ---- the only L5 behaviour used ----
    "behaviour": {
        "mechanism": "near-touch passive exposure",
        "provenance": ("the single most strongly evidenced L5 behaviour: "
                       "median offset 0 ticks from the touch at every "
                       "inferred level rank, rho(rank, offset) = 0.138"),
        "offset_from_touch_ticks": 0,
        "no_complement_chasing": True,
        "no_martingale": True,
        "no_averaging": True,
        "no_second_order": True,
        "no_automatic_repeat": True,
        "pair_cost_reservation": None,
        "pair_cost_reservation_rationale": (
            "deliberately absent. It is a PAIR_ECONOMICS design choice, not "
            "an observed L5 rule, and a hard L5 pair-cost ceiling was "
            "REFUTED. Requiring it here would refuse to quote at a 0.01 "
            "spread and the experiment could never run."),
    },

    # ---- size and the closed loss bound ----
    "size": {
        "quantity_shares": str(VENUE_MIN_QTY),
        "quantity_source": "venue `minimumTradeQty`, read live, fail-closed",
        "quantity_is_the_venue_minimum": True,
        "fail_closed_if_min_qty_absent_or_ge_1": True,
        "max_entry_price": str(VENUE_MAX_PRICE),
        "max_entry_price_source": "the venue's own 0.01-grid ceiling",
        "MAX_POSSIBLE_LOSS_USD": str(MAX_POSSIBLE_LOSS_USD),
        "loss_bound_derivation": ("exactly ONE order of the venue minimum: "
                                  "0.01 shares x 0.99 max price = $0.0099. "
                                  "A total loss is under one cent."),
        "may_lose_entire_notional": True,
        "may_place_a_second_order_to_repair": False,
    },

    # ---- venue surface ----
    "orders": {
        "markets_concurrent": 1,
        "series": "btc-updown-15m",
        "max_simultaneous_resting_orders": 1,
        "max_orders_this_experiment": 1,
        "maker_intended_post_only": True,
        "post_only_flag": "participateDontInitiate",
        "taker_permitted": False,
        "partial_fills_permitted": True,
        "partial_never_promoted_to_full": True,
    },

    # ---- book source, reused and validated ----
    "book": {
        "bbo_source": "l5_pmus_auth_book.AuthBook",
        "bbo_source_forbidden": "gateway /v1/markets/{slug}/bbo",
        "staleness_bound_s": 2.0,
        "staleness_bound_provenance": (
            "REUSED: l5_preview_gate.BOOK_MAX_AGE_S = 2.0, validated and "
            "accepted on 31/31 consecutive observations at <= 2.0 s"),
        "staleness_gate_function": "l5_preview_gate.fresh_two_sided",
        "fail_closed_if_stale": True,
        "fail_closed_if_not_two_sided": True,
        "tick_source": "venue `orderPriceMinTickSize`, validated as a grid",
        "tick_universal_constant": None,
    },

    # ---- what must be captured AT PLACEMENT ----
    "capture_at_placement": (
        "side", "price", "bbo_bid", "bbo_ask", "spread",
        "depth_ahead_at_our_price", "quantity", "tte_s",
        "book_timestamp", "book_age_s", "order_timestamp"),

    # ---- and afterwards ----
    "capture_lifecycle": (
        "every venue lifecycle event with its timestamp",
        "observable queue depletion at our price",
        "rest duration",
        "venue_confirmed_liquidity_role",
        "actual commission",
    ),

    "evidence": {
        "touch_equals_fill": False,
        "fill_requires": "venue-reported fill on our own order id",
        "submitted_post_only": "RECORDED SEPARATELY, never implies maker",
        "venue_confirmed_liquidity_role": (
            "venue trade object `passiveExecution` vs `aggressorExecution`, "
            "selected by `isAggressor`; else UNRESOLVED"),
        "liquidity_role_field_forbidden": "`aggressor` (a boolean)",
        "fee_source": "signed `commissionNotionalCollected` on our execution",
        "modeled_fee_may_be_reported_as_actual": False,
        "rewards_counted": "ONLY when actually credited",
    },

    # ---- accounting: one leg, so matched is N/A ----
    "accounting": {
        "matched_pair_economics": "N/A_SINGLE_LEG_EXPERIMENT",
        "matched_is_not_zero_it_is_not_applicable": True,
        "lines_kept_separate": ("directional_residual_pnl", "fees",
                                "credited_rewards", "final_net"),
        "realized_requires": "FILLED + SETTLED + CASH_CONFIRMED",
        "does_not_test_full_L5_economics": True,
    },

    # ---- safety ----
    "deadman": {
        "mechanism": "market-scoped cancel-all in a finally block",
        "on_every_exit_path": True,
        "post_mutations_never_retried": True,
        "no_auto_restart": True,
        "no_auto_scaling": True,
    },
    "withdrawal": {
        "tte_shutdown_s": 120,
        "cancel_at_window_end": True,
    },
    "daily_limit": {
        "daily_realized_loss_stop_usd": "-1.00",
        "note": ("inherited unchanged; this experiment cannot approach it, "
                 "since its total exposure is $0.0099"),
    },

    "arming": {
        "required_env_keys": ["PMUS_EXEC1_LIVE=YES",
                              "PMUS_EXEC1_SESSION_ID=<session id>",
                              "PMUS_EXEC1_ARM=<same session id>",
                              "PMUS_EXEC1_GATE_EVIDENCE=<exact market slug>"],
        "isolated_from": ["PMUS_Q1_*", "PMUS_MICRO_*", "PMUS_CANARY_*",
                          "PMUS_CASE1_*", "PMUS_SCHEMA_PROBE_*"],
        "claude_may_not_set_these": True,
        "default_state": "DISARMED",
    },

    "constraints": {
        "orders_submitted_by_this_session": 0,
        "wallet_signing_by_this_session": 0,
        "s3_modified": False,
        "reserved_micro_candidate_modified": False,
    },
}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


#: HARD-CODED LITERAL so assert_frozen() cannot be vacuous.
EXEC1_FINGERPRINT = \
    "b1dbe7f1e628e6f9fc8e6459a2a5b95098ada2f16ae872f10081d5dc9bb82e06"


def assert_frozen() -> None:
    got = fingerprint()
    if got != EXEC1_FINGERPRINT:
        raise SystemExit(
            "LIVE-L5-EXEC-1 spec moved: expected %s, got %s. Re-authorize "
            "rather than running." % (EXEC1_FINGERPRINT, got))


if __name__ == "__main__":
    print(fingerprint())
