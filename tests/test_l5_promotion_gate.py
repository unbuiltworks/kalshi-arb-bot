"""The promotion gate can only BLOCK. It must never arm anything."""
from __future__ import annotations

import datetime as dt

import pytest

from src import l5_promotion_gate as P

NOW = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
FRESH = (NOW - dt.timedelta(days=1)).isoformat()


PROM = {"min_settled_windows": 300, "min_expectancy_usd": "0.05",
        "oos_evidence_id": "btc15m-oos-20261023",
        "requires_bounded_live_validation": True}
GOOD_SHADOW = {"windows_settled": 400, "net_per_settled_window": "0.11"}
GOOD_OOS = {"evidence_id": "btc15m-oos-20261023", "asof_utc": FRESH,
            "fill_basis": "CONSERVATIVE", "windows": 500,
            "net_per_window_usd": "0.09", "in_sample": False}
GOOD_LIVE = {"evidence_id": "btc15m-oos-20261023", "asof_utc": FRESH,
             "settlement_confirmed": True, "windows": 8,
             "realized_net_usd": "0.42"}


def ev(**kw):
    base = dict(promotion=PROM, shadow=GOOD_SHADOW, oos_evidence=GOOD_OOS,
                live_validation=GOOD_LIVE, now=NOW)
    base.update(kw)
    return P.evaluate(**base)


class TestItCannotArmAnything:

    def test_there_is_no_function_that_grants_permission(self):
        for name in ("permit", "enable", "arm", "allow", "promote",
                     "authorize"):
            assert not hasattr(P, name), name

    def test_the_only_answer_is_blocks_live(self):
        v = ev()
        assert "blocks_live" in v
        for k in v:
            assert k not in ("permitted", "armed", "ok", "enabled")

    def test_not_blocking_is_documented_as_not_permission(self):
        assert "not permission to trade" in ev()["labels"]

    def test_telemetry_states_it_can_arm_nothing(self):
        assert P.telemetry(ev())["can_this_gate_arm_anything"] is False


class TestShadowAloneIsNeverEnough:

    def test_a_great_shadow_with_no_oos_evidence_still_blocks(self):
        v = ev(oos_evidence=None)
        assert v["blocks_live"]
        assert "OOS_EVIDENCE_MISSING" in v["reasons"]

    def test_a_great_shadow_and_oos_with_no_live_validation_still_blocks(self):
        v = ev(live_validation=None)
        assert v["blocks_live"]
        assert "LIVE_VALIDATION_MISSING" in v["reasons"]

    def test_only_all_four_together_stop_the_block(self):
        assert ev()["blocks_live"] is False


class TestTodaysRealProductionShadow:
    """282 settled windows at -$0.2065 -- the figure measured on
    2026-10-23. The gate must refuse it."""

    def test_it_blocks_on_the_real_numbers(self):
        v = P.evaluate(promotion=PROM,
                       shadow={"windows_settled": 282,
                               "net_per_settled_window": "-0.2065"},
                       now=NOW)
        assert v["blocks_live"]
        assert "SHADOW_SAMPLE_TOO_SMALL" in v["reasons"]
        assert "SHADOW_EXPECTANCY_BELOW_THRESHOLD" in v["reasons"]
        assert "OOS_EVIDENCE_MISSING" in v["reasons"]
        assert "LIVE_VALIDATION_MISSING" in v["reasons"]


class TestShadowChecks:

    def test_too_small_a_sample_blocks(self):
        v = ev(shadow={"windows_settled": 10,
                       "net_per_settled_window": "0.50"})
        assert "SHADOW_SAMPLE_TOO_SMALL" in v["reasons"]

    def test_expectancy_below_threshold_blocks(self):
        v = ev(shadow={"windows_settled": 400,
                       "net_per_settled_window": "0.01"})
        assert "SHADOW_EXPECTANCY_BELOW_THRESHOLD" in v["reasons"]

    def test_missing_shadow_blocks(self):
        assert "SHADOW_STATS_MISSING" in ev(shadow=None)["reasons"]

    def test_unreadable_expectancy_blocks(self):
        v = ev(shadow={"windows_settled": 400,
                       "net_per_settled_window": "n/a"})
        assert "SHADOW_EXPECTANCY_UNREADABLE" in v["reasons"]


class TestOosEvidence:

    def test_a_mismatched_evidence_id_blocks(self):
        v = ev(oos_evidence=dict(GOOD_OOS, evidence_id="something-else"))
        assert "OOS_EVIDENCE_ID_MISMATCH" in v["reasons"]

    def test_stale_evidence_blocks(self):
        old = (NOW - dt.timedelta(days=400)).isoformat()
        v = ev(oos_evidence=dict(GOOD_OOS, asof_utc=old))
        assert "OOS_EVIDENCE_STALE" in v["reasons"]

    def test_an_optimistic_fill_basis_blocks(self):
        """Evidence about a simulator, not about our fills."""
        v = ev(oos_evidence=dict(GOOD_OOS, fill_basis="OPTIMISTIC"))
        assert "OOS_FILL_BASIS_NOT_ACCEPTED" in v["reasons"]

    def test_in_sample_evidence_blocks(self):
        v = ev(oos_evidence=dict(GOOD_OOS, in_sample=True))
        assert "OOS_EVIDENCE_IS_IN_SAMPLE" in v["reasons"]

    def test_a_negative_oos_result_blocks(self):
        v = ev(oos_evidence=dict(GOOD_OOS, net_per_window_usd="-0.01"))
        assert "OOS_EVIDENCE_BELOW_THRESHOLD" in v["reasons"]

    def test_a_naive_timestamp_blocks(self):
        v = ev(oos_evidence=dict(GOOD_OOS, asof_utc="2026-10-22T00:00:00"))
        assert "OOS_EVIDENCE_TIMESTAMP_INVALID" in v["reasons"]


class TestBoundedLiveValidation:

    def test_unsettled_validation_blocks(self):
        v = ev(live_validation=dict(GOOD_LIVE, settlement_confirmed=False))
        assert "LIVE_VALIDATION_NOT_SETTLEMENT_CONFIRMED" in v["reasons"]

    def test_merely_absent_confirmation_blocks(self):
        lv = {k: x for k, x in GOOD_LIVE.items()
              if k != "settlement_confirmed"}
        v = ev(live_validation=lv)
        assert "LIVE_VALIDATION_NOT_SETTLEMENT_CONFIRMED" in v["reasons"]

    def test_a_negative_live_result_blocks(self):
        v = ev(live_validation=dict(GOOD_LIVE, realized_net_usd="-0.10"))
        assert "LIVE_VALIDATION_NEGATIVE" in v["reasons"]

    def test_validation_for_other_evidence_blocks(self):
        v = ev(live_validation=dict(GOOD_LIVE, evidence_id="other"))
        assert "LIVE_VALIDATION_EVIDENCE_MISMATCH" in v["reasons"]

    def test_stale_validation_blocks(self):
        old = (NOW - dt.timedelta(days=400)).isoformat()
        v = ev(live_validation=dict(GOOD_LIVE, asof_utc=old))
        assert "LIVE_VALIDATION_STALE" in v["reasons"]


class TestMandateTerms:

    def test_a_mandate_not_requiring_live_validation_blocks(self):
        """It should never have verified; if it reaches here it blocks."""
        v = ev(promotion=dict(PROM, requires_bounded_live_validation=False))
        assert "PROMOTION_LIVE_VALIDATION_NOT_REQUIRED" in v["reasons"]

    def test_missing_terms_block(self):
        assert "PROMOTION_TERMS_MISSING" in ev(promotion=None)["reasons"]

    def test_an_invalid_sample_floor_blocks(self):
        v = ev(promotion=dict(PROM, min_settled_windows=0))
        assert "PROMOTION_MIN_SAMPLE_INVALID" in v["reasons"]
