"""LIVE-L5-MICRO-1 — frozen spec. DISARMED.

A minimal-capital REAL-EXECUTION probe of the evidence-supported L5
mechanics. NOT maximum profit. NOT a new strategy. Separate from frozen S3
(8b20a259...87df) and from the residual-efficiency study.

NOTHING HERE CAN SUBMIT AN ORDER. Arming requires four owner-held env keys
that this session does not set and must not set.

Language discipline, enforced in the ledger:
  * SUBMITTED is not a fill.
  * a fill is not profit.
  * mark-to-market is not realized.
  * only FILLED + SETTLED + CASH_CONFIRMED is REALIZED.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal as D
from typing import Any, Dict

PROBE = "LIVE-L5-MICRO-1"

#: The seven ledger states. A state may only advance along this order.
LIFECYCLE = ("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED",
             "CANCELLED", "SETTLED", "CASH_CONFIRMED")
TERMINAL = ("CASH_CONFIRMED", "CANCELLED")

MARKOUT_HORIZONS = (1, 5, 10, 30, 60)      # seconds, plus settlement

SPEC: Dict[str, Any] = {
    "probe": PROBE,
    "spec_version": 2,
    "supersedes": {
        "probe": "PROBE_1_INFRASTRUCTURE_SKIP",
        "fingerprint":
            "69904fd4e64bcc62093ce70c1b63b5d825548f5d495eda63744fc91169919118",
        "outcome": "INFRASTRUCTURE SKIP / NO SUBMISSION",
        "preserved_at": "analysis/l5/live_micro/spec_probe1_frozen.py",
        "note": ("trading and risk behaviour are UNCHANGED from v1. Only "
                 "instrumentation was fixed: tick source, BBO source and "
                 "venue role/fee fields."),
    },
    "armed_by_default": False,
    "is_new_strategy": False,
    "objective": ("measure real execution of the evidence-supported L5 "
                  "mechanics at minimal capital; not profit maximisation"),

    # ---------------- (1) market / category ----------------
    "market": {
        "venue": "Polymarket US (PMUS)",
        "category": "crypto — BTC up/down",
        "series": "btc-updown-15m",
        "markets_concurrent": 1,
        "slug_selection": ("ONE market at a time, named at arm time via the "
                           "owner's gate-evidence key; never auto-selected "
                           "and never auto-advanced"),
    },

    # ---------------- (2) minimum permitted quantity ----------------
    "quantity": {
        "source": "venue metadata `minimumTradeQty`, read live per market",
        "requirement": "metadata must explicitly advertise minimumTradeQty < 1",
        "fail_closed_if_absent": True,
        "target_notional_usd": "0.01",
        "max_leg_notional_usd": "0.02",
        "note": ("the quantity is whatever the venue minimum and the ~$0.01 "
                 "target imply; it is NOT a chosen size and is never "
                 "scaled to recover a loss"),
        # FIX 1. v1 required tick == 0.005, which the venue does not serve
        # on BTC15m (actual 0.01) and which the production executor had
        # ALREADY abandoned: its own comment records that a hardcoded
        # equality check "REFUSED EVERY SPORTS MARKET ... the live path
        # could not have placed a sports order at all". The authoritative
        # source is the venue's per-market field, validated as a GRID.
        "tick_source": "venue field `orderPriceMinTickSize`, per market",
        "tick_authority": ("pmus_micro_executor: accept the venue's grid "
                           "and validate the price against THAT rather "
                           "than against a constant"),
        "tick_universal_constant": None,
        "fail_closed_if_tick_absent": True,
        "fail_closed_if_tick_implausible": "tick <= 0 or tick >= 1",
        "fail_closed_if_price_off_grid": "(price / tick) % 1 != 0",
        "observed_tick_btc15m_2026_10_06": "0.01",
    },

    # ---------------- (3) max loss in one market ----------------
    "per_market_risk": {
        "cumulative_first_leg_notional_cap_usd": "0.05",
        "cap_is_durable_across_invocations": True,
        "max_gross_pair_capital_usd": "0.04",
        "MAX_DOLLARS_LOSABLE_IN_ONE_MARKET_USD": "0.05",
        "derivation": ("worst case is total loss of deployed capital; "
                       "deployment is hard-capped at the durable $0.05 "
                       "cumulative first-leg notional, read from the "
                       "campaign ledger across invocations"),
    },

    # ---------------- (4) max directional exposure ----------------
    "directional_bound": {
        "max_residual_shares": "equal to the actual first-fill quantity",
        "max_residual_dollars_usd": "0.02",
        "breach_handling": ("any residual above the actual first-fill "
                            "quantity is reported as a CAP BREACH, never "
                            "rounded or truncated"),
        "no_martingale": True,
        "no_averaging_up_to_recover": True,
    },

    # ---------------- (5) max simultaneous resting orders ----------------
    "orders": {
        "max_simultaneous_resting_orders": 1,
        "maker_only": True,
        "post_only_flag": "participateDontInitiate",
        "taker_permitted": False,
        "partial_fills_permitted": True,
        "partial_never_promoted_to_full": True,
        "partial_complement_never_treated_as_matched": True,
    },

    # ---------------- (6) cancellation / deadman ----------------
    "deadman": {
        "mechanism": "market-scoped cancel-all in a finally block",
        "on_every_exit_path": True,
        "includes_crash_and_exception_paths": True,
        "post_mutations_never_retried": True,
        "no_auto_restart": True,
        "no_auto_scaling": True,
    },

    # ---------------- (7) daily realized-loss limit ----------------
    "daily_limit": {
        "daily_realized_loss_stop_usd": "-1.00",
        "checked_before_every_mutation": True,
        "realized_means": "FILLED + SETTLED + CASH_CONFIRMED only",
    },

    # ---------------- placement / repricing, frozen ----------------
    # Contemporaneous information ONLY. These are the evidence-supported
    # mechanics, not a tuned rule set, and no value here was chosen to
    # improve P&L.
    "placement": {
        "offset_from_touch_ticks": 0,
        "provenance_offset": ("measured median offset 0 ticks at every "
                              "inferred level rank (SPACING_FINDINGS)"),
        "reprice_on_displacement_price_distance": "0.06",
        "reprice_on_staleness_s": 69,
        "provenance_reprice": ("L5 fill-offset p99 = 6 ticks on the 0.01 "
                               "grid = 0.06 in PRICE, and max absence 69 s"),
        "threshold_held_in_price_not_ticks": True,
        "fixed_order_lifetime_s": None,
        "a_reprice_is_never_a_fill": True,
        "no_leave_behind_persistence_assumed": True,
        "provenance_no_persistence": ("persistence was "
                                      "CONSISTENT_WITH_REPOSTING"),

        # FIX 2, highest priority. v1 named no BBO source, so the default
        # path was pmus_q1_executor.fresh_bbo -> GATEWAY /v1/markets/{slug}
        # /bbo, which this repo's own live probe measured at ~30 s stale
        # with a 19-cent disagreement against the authenticated book, and
        # which made that probe rest ~20 ticks off the real touch and
        # self-classify PROBE_FAILURE.
        "bbo_source": "l5_pmus_auth_book.AuthBook",
        "bbo_mechanism": ("authenticated markets WebSocket, the same stream "
                          "and parser the evidence recorder uses; push "
                          "stream with per-frame receipt timestamps"),
        "bbo_source_forbidden": ("gateway /v1/markets/{slug}/bbo -- "
                                 "measured ~30 s stale"),
        "staleness_bound_s": 2.0,
        "staleness_bound_provenance": (
            "REUSED, not invented: l5_preview_gate.BOOK_MAX_AGE_S = 2.0, "
            "whose own comment reads 'the frozen rule's own snapshot bound "
            "is 2.0s; this is the same discipline applied to the order path "
            "and is deliberately not looser'"),
        "staleness_gate_function": "l5_preview_gate.fresh_two_sided",
        "staleness_measured_at": "the decision point, not a periodic preflight",
        "staleness_age_basis": (
            "time since the socket last delivered ANY frame (stream_age_s), "
            "not since the last book CHANGE -- a push stream sends nothing "
            "when nothing changes"),
        "fail_closed_if_stale": True,
        "fail_closed_if_not_two_sided": True,
    },
    "withdrawal": {
        "tte_shutdown_s": 120,
        "provenance": "withdrawal-associated TTE median 102 s vs 485 s",
        "withdraw_on_inventory_bound": True,
    },

    # ---------------- evidence standard ----------------
    "evidence": {
        "touch_equals_fill": False,
        "fill_requires": "venue-reported fill on our own order id",

        # FIX 3. The prior probe concluded maker/taker was unobservable
        # because `aggressor` is a BOOLEAN. It had read the wrong field.
        # The venue nests the two executions of a trade and names which is
        # ours, and the incentive audit already recovered 25 credited
        # prints this way.
        "liquidity_role_source": (
            "venue trade object: `passiveExecution` vs "
            "`aggressorExecution`, selected by the trade's `isAggressor`"),
        "liquidity_role_field_forbidden": (
            "`aggressor` -- a boolean that cannot resolve a role"),
        "submitted_post_only": "RECORDED SEPARATELY, never used to infer role",
        "venue_confirmed_liquidity_role": "from the venue only, else UNRESOLVED",
        "post_only_never_implies_maker": True,

        "fee_source": (
            "venue `commissionNotionalCollected` on OUR execution object, "
            "signed: negative = credited to us"),
        "fee_order_ack_fields": ["commissionNotionalTotalCollected",
                                 "commissionsBasisPoints",
                                 "makerCommissionsBasisPoints"],
        "fee_market_field": "feeCoefficient (observed 0.0695 on BTC15m)",
        "modeled_fee_may_be_reported_as_actual": False,
        "modeled_fee_label_if_used": "MODELLED_NOT_ACTUAL",
        "maker_taker_classification": "recorded from the venue, never assumed",
        "queue_information_recorded_where_observable": True,
        "rewards_counted": "ONLY when actually credited, on their own line",
        "estimated_rewards_counted": False,
    },

    # ---------------- what this probe MEASURES ----------------
    # The economics finding enters the MEASUREMENT, not the trading rules.
    "measurement": {
        "accounting_lines_kept_separate": (
            "matched_pair_economics", "directional_residual_pnl",
            "fees", "credited_rewards", "final_net"),
        "markout_horizons_s": list(MARKOUT_HORIZONS),
        "markout_at_settlement": True,
        "PRIMARY_QUESTION": (
            "can our residual controls preserve matched acquisition WITHOUT "
            "reproducing L5's residual losses?"),
        "L5_REFERENCE_RATIO": {
            "matched_pair_economics_usd": 22568.25,
            "directional_residual_usd": -25313.78,
            "residual_drag_ratio": 1.1217,
            "meaning": ("L5's residual destroyed 112 % of his matched "
                        "gains. The probe measures OUR ratio against this "
                        "reference. It is a MEASUREMENT target, not a "
                        "trading rule, and no rule here was changed to "
                        "chase it."),
        },
    },

    # ---------------- (9) arming ----------------
    "arming": {
        "required_env_keys": ["PMUS_MICRO_LIVE=YES",
                              "PMUS_MICRO_SESSION_ID=<session id>",
                              "PMUS_MICRO_ARM=<same session id>",
                              "PMUS_MICRO_GATE_EVIDENCE=<exact market slug>"],
        "dual_key_isolated_from_q1_and_v4": True,
        "owner_must_authorize_fingerprint_and_envelope": True,
        "claude_may_not_set_these": True,
        "default_state": "DISARMED",
    },

    "constraints": {
        "s3_modified": False,
        "s3_fingerprint":
            "8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df",
        "orders_submitted_by_this_session": 0,
        "wallet_signing_by_this_session": 0,
    },
}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


# HARD-CODED LITERAL: deriving it from SPEC at import would make the guard
# vacuous, recomputing the value it compares against.
MICRO_FINGERPRINT = \
    "0522b0dac3fc5dec5ec0a01c6dbbb6369fde3f7062cede16f965fdfc459d1b31"


def assert_frozen() -> None:
    got = fingerprint()
    if got != MICRO_FINGERPRINT:
        raise SystemExit(
            "LIVE-L5-MICRO-1 spec moved: expected %s, got %s. The owner "
            "authorized a specific fingerprint; re-authorize rather than "
            "running." % (MICRO_FINGERPRINT, got))


if __name__ == "__main__":
    print(fingerprint())
