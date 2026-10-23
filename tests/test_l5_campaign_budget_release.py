"""Releasing a finished campaign's budget cannot erase losses or free funds.

THE DEFECT, observed in production 2026-10-23T02:08:08Z. The 0153 campaign
could not arm:

    L5_LIVE_DISARMED why=CAMPAIGN_BUDGET_REFUSED
      CAMPAIGN_BUDGET_LOCKED:camp1.l5live-20261022-2122
      campaign_id camp1.l5live-20261023-0153

`CampaignBudgetLedger.close` was the right gate and nothing ever called it,
so a campaign's budget stayed OPEN for ever once its windows were spent and
every later campaign was locked out. The venue meanwhile reported
positions {}, openOrders 0, assetNotional 0, unsettledFunds 0 -- the old
campaign was provably finished. The anti-reset property was working; the
lifecycle around it was missing.

WHAT MUST REMAIN TRUE AFTER A RELEASE, and is pinned below:

  * realized losses are NOT erased. The ledger stores only
    {campaign_id, closed, opened_utc, peak_usd}; losses live in the
    executor's store and re-enter the gate through
    `worst_case_usd(realized_usd=...)` on EVERY evaluation.
  * headroom is NOT minted. After a release it is still
    budget - (realized losses + unsettled cost + outstanding notional).
  * a release is refused while ANY order is outstanding.
  * a release is refused while ANY leg is unsettled.
  * a campaign may not release ITSELF -- that would reset `peak_usd`
    mid-run, which is the exact reset the lock exists to prevent.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_campaign_budget as CB

OLD = "camp1.l5live-20261022-2122"
NEW = "camp1.l5live-20261023-0153"

#: the production figures this release is being performed against
REALIZED = D("-8.8988")
BUDGET = D("11.10")
HEADROOM = D("2.2012")


@pytest.fixture
def led(tmp_path):
    L = CB.CampaignBudgetLedger(str(tmp_path), budget_usd=BUDGET)
    L.open_or_resume(OLD, now_iso="2026-10-22T21:22:00Z")
    return L


class TestTheReleaseItself:

    def test_a_finished_campaign_is_released_to_the_claimant(self, led):
        out = led.release_stale(NEW, legs=(), outstanding=())
        assert out["ok"] is True and out["released"] is True
        assert out["closed_campaign_id"] == OLD
        assert out["claimant_id"] == NEW

    def test_the_claimant_can_then_open_its_own(self, led):
        led.release_stale(NEW, legs=(), outstanding=())
        got = led.open_or_resume(NEW, now_iso="2026-10-23T02:10:00Z")
        assert got["ok"] is True
        assert got["campaign_id"] == NEW

    def test_before_release_the_claimant_is_locked_out(self, led):
        assert led.refuse_arming(campaign_id=NEW) \
            == "CAMPAIGN_BUDGET_LOCKED:%s" % OLD

    def test_releasing_an_already_closed_ledger_is_a_no_op(self, led):
        led.release_stale(NEW)
        again = led.release_stale(NEW)
        assert again["ok"] is True and again["released"] is False
        assert again["reason"] == "ALREADY_CLOSED"


class TestItRefusesWhileObligationsRemain:

    def test_outstanding_orders_block_the_release(self, led):
        out = led.release_stale(NEW, outstanding=[{"qty": "1", "price": ".5"}])
        assert out["ok"] is False and out["released"] is False
        assert out["reason"] == "CAMPAIGN_OUTSTANDING_ORDERS"
        # and the lock still stands
        assert led.refuse_arming(campaign_id=NEW).startswith(
            "CAMPAIGN_BUDGET_LOCKED")

    def test_unsettled_legs_block_the_release(self, led):
        out = led.release_stale(NEW, legs=[{"cost_usd": "1.00",
                                            "settled": False}])
        assert out["ok"] is False
        assert out["reason"] == "CAMPAIGN_UNSETTLED_EXPOSURE:1"

    def test_settled_legs_do_not_block_it(self, led):
        out = led.release_stale(NEW, legs=[{"realized_usd": "-0.40",
                                            "settled": True}])
        assert out["ok"] is True and out["released"] is True

    def test_a_campaign_cannot_release_itself(self, led):
        """Self-release would reset peak_usd mid-run. That is the reset the
        lock exists to prevent, so it is refused by name."""
        out = led.release_stale(OLD)
        assert out["ok"] is False and out["released"] is False
        assert out["reason"] == "CANNOT_RELEASE_OWN_OPEN_CAMPAIGN"

    def test_an_empty_claimant_id_is_refused(self, led):
        for bad in ("", "   ", None):
            assert led.release_stale(bad)["ok"] is False


class TestNoLossIsErasedAndNoHeadroomIsMinted:

    def test_the_realized_loss_still_binds_after_release(self, led):
        """The whole point. Losses are not in the ledger and survive it."""
        led.release_stale(NEW)
        led.open_or_resume(NEW, now_iso="2026-10-23T02:10:00Z")
        used = CB.worst_case_usd(realized_usd=REALIZED)
        assert used == D("8.8988")
        # an order that would exceed the remaining headroom is still refused
        why = led.refuse_amount(campaign_id=NEW, used_usd=used,
                                new_order_cost_usd=D("2.50"))
        assert why is not None and why.startswith("CAMPAIGN_WORST_CASE_BUDGET")

    def test_headroom_after_release_is_unchanged(self, led):
        led.release_stale(NEW)
        led.open_or_resume(NEW)
        used = CB.worst_case_usd(realized_usd=REALIZED)
        assert BUDGET - used == HEADROOM
        # exactly at the boundary passes, a cent more does not
        assert led.refuse_amount(campaign_id=NEW, used_usd=used,
                                 new_order_cost_usd=HEADROOM) is None
        assert led.refuse_amount(campaign_id=NEW, used_usd=used,
                                 new_order_cost_usd=HEADROOM + D("0.01"))

    def test_release_does_not_write_any_realized_figure(self, led):
        """The ledger must not gain a loss field it could later reset."""
        led.release_stale(NEW)
        doc = led.read()
        for k in doc:
            assert "realiz" not in k.lower(), k
            assert "loss" not in k.lower(), k

    def test_a_settled_loss_still_counts_after_release(self, led):
        """Settled losses persist through the whole cycle."""
        legs = [{"realized_usd": "-0.40", "settled": True}]
        led.release_stale(NEW, legs=legs)
        led.open_or_resume(NEW)
        assert CB.worst_case_usd(realized_usd=REALIZED, legs=legs) \
            == D("9.2988")

    def test_the_budget_ceiling_is_unchanged_by_release(self, led):
        led.release_stale(NEW)
        led.open_or_resume(NEW)
        assert led.budget_usd == BUDGET
        assert led.telemetry(campaign_id=NEW)["budget_usd"] == str(BUDGET)
