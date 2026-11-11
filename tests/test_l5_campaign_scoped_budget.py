"""The $5 campaign budget must measure THIS campaign, not the account's past.

WHAT HAPPENED, 2026-11-04T01:31:36Z. An authorized four-window campaign
submitted zero orders. Every window was refused at the arming boundary with

    CAMPAIGN_BUDGET_REFUSED
    CAMPAIGN_WORST_CASE_EXHAUSTED:11.33690000>=5

against campaign camp1.9c793662, which had spent nothing. Two faults:

1. THE BUDGET COUNTED LIFETIME HISTORY. `ValidationGates.refuse` computed
   `campaign_used` from `max(0, -cumulative_realized)` -- the account's
   LIFETIME realized loss, measured at -10.6211. A $5 per-campaign budget
   was therefore spent before any order existed. A campaign budget that
   charges losses the campaign did not cause is not a campaign budget.

2. A FINISHED CAMPAIGN'S HIGH-WATER MARK REFUSED A NEW ONE.
   `refuse_arming`'s lock is closed-aware; its peak check was not. The
   closed camp1.1c6b9f88's peak of 11.3369 refused the fresh campaign -- and
   because `refuse_arming` runs BEFORE `open_or_resume`, the new campaign
   never reached the call that resets its own peak to 0.

WHAT IS NOT CHANGED, and these tests pin all of it: no historical loss is
written down, reset or erased; `cumulative_realized` is still the full
lifetime figure; every independent daily, lifetime and account-wide control
still reads it; the $5 budget, $5 daily halt, $12 cash ceiling and $20
exposure limit are untouched; a realized GAIN creates no headroom; and the
zero point is owner-gated, because the baseline is keyed on the approval
nonce the executor cannot write and `capture` is idempotent.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_campaign_baseline as CBL
from src import l5_campaign_budget as CBD
from src import l5_live_validation_gates as VG

CID = "camp1.aaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "camp1.bbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def root(tmp_path):
    return str(tmp_path)


def baseline(root, cid=CID, realized="-10.6211"):
    b = CBL.CampaignBaseline(root=root)
    assert b.capture(cid, account_wide_realized=realized,
                     now_iso="2026-11-04T01:00:00+00:00")["ok"] is True
    return b


def gates(root, *, cid=CID, budget="5", base=None):
    return VG.ValidationGates(
        max_cash_deployed_usd=D("12"),
        campaign_budget=CBD.CampaignBudgetLedger(root=root, budget_usd=budget),
        campaign_id=cid,
        campaign_baseline=base if base is not None
        else CBL.CampaignBaseline(root=root),
        cash_policy=VG.NO_RECYCLING)


def refuse(g, *, realized="-10.6211", legs=(), outstanding=(),
           price="0.50", qty="1"):
    return g.refuse(order_price=D(price), order_qty=D(qty),
                    order_intent="ORDER_INTENT_BUY_LONG",
                    filled_legs=legs, outstanding=outstanding,
                    cumulative_realized=D(realized))


# ============== 1. prior historical losses do not consume the budget

def test_a_PRIOR_LIFETIME_LOSS_no_longer_spends_the_campaign_budget(root):
    """THE DEFECT. $10.6211 of standing account loss against a $5 budget
    refused everything. Measured from the campaign's own zero point it is
    zero, because the campaign has not lost anything yet."""
    b = baseline(root, realized="-10.6211")
    g = gates(root, base=b)
    assert refuse(g, realized="-10.6211") is None


def test_the_lifetime_figure_is_NOT_written_down(root):
    """The baseline records a zero point; it does not alter realized."""
    b = baseline(root, realized="-10.6211")
    stored = b.read()
    assert stored["account_wide_realized"] == "-10.6211"
    got = b.additional_loss_usd(CID, account_wide_realized=D("-10.6211"),
                                open_worst_case_usd=D(0))
    assert got["current_realized_usd"] == "-10.6211"
    assert got["baseline_realized_usd"] == "-10.6211"
    assert got["realized_delta_loss_usd"] == "0"


def test_a_loss_INCURRED_BY_THIS_CAMPAIGN_does_spend_it(root):
    """The budget still binds on the campaign's own losses."""
    b = baseline(root, realized="-10.6211")
    g = gates(root, base=b)
    # the campaign has since lost a further $6: 10.6211 + 6 = 16.6211
    why = refuse(g, realized="-16.6211")
    assert why is not None and "CAMPAIGN_WORST_CASE" in why


def test_the_boundary_is_exactly_the_budget(root):
    b = baseline(root, realized="-10.0")
    g = gates(root, base=b)
    assert refuse(g, realized="-14.0", price="0.01", qty="1") is None
    why = refuse(g, realized="-15.0", price="0.01", qty="1")
    assert why is not None and "CAMPAIGN_WORST_CASE_BUDGET" in why


# ===================================== 2. gains create no headroom

def test_a_REALIZED_GAIN_CREATES_NO_EXTRA_ROOM(root):
    """max(0, base - now): a campaign that made money does not thereby earn
    a larger loss budget, and cannot recycle profit into risk."""
    b = baseline(root, realized="-10.6211")
    got = b.additional_loss_usd(CID, account_wide_realized=D("-5.0000"),
                                open_worst_case_usd=D(0))
    assert got["realized_delta_loss_usd"] == "0"      # not -5.6211
    assert got["additional_loss_usd"] == "0"


def test_a_gain_does_not_let_a_bigger_commitment_through(root):
    b = baseline(root, realized="-10.6211")
    g = gates(root, base=b)
    # $5.62 of realized gain must NOT buy commitment room beyond the budget
    legs = [{"market": "m", "cash_out": D("6.00"), "settled": False}]
    why = refuse(g, realized="-5.0000", legs=legs)
    assert why is not None and "CAMPAIGN_WORST_CASE" in why


# ============================ 3. outstanding commitments still count

def test_outstanding_commitments_count_against_the_campaign(root):
    b = baseline(root, realized="-10.6211")
    g = gates(root, base=b)
    out = [{"coid": "c1", "qty": D("5"), "price": D("0.90"),
            "intent": "ORDER_INTENT_BUY_SHORT"}]
    why = refuse(g, realized="-10.6211", outstanding=out)
    assert why is not None, "a resting short's worst case must be charged"


def test_an_unsettled_leg_counts_and_a_settled_one_does_not(root):
    b = baseline(root, realized="-10.6211")
    g = gates(root, base=b)
    unsettled = [{"market": "m", "cash_out": D("6.00"), "settled": False}]
    assert refuse(g, legs=unsettled) is not None
    settled = [{"market": "m", "cash_out": D("6.00"), "settled": True}]
    assert refuse(g, legs=settled) is None


# ======================== 4. rollover cannot reset the same campaign

def test_the_SAME_campaign_cannot_reset_its_baseline(root):
    """Idempotent capture is the anti-reset property: a restart, a re-arm or
    a later window cannot move the zero point."""
    b = baseline(root, realized="-10.6211")
    again = b.capture(CID, account_wide_realized=D("-16.0"))
    assert again["ok"] is True and again["captured"] is False
    assert b.read()["account_wide_realized"] == "-10.6211"
    # so a loss since the baseline is still visible
    got = b.additional_loss_usd(CID, account_wide_realized=D("-16.0"),
                                open_worst_case_usd=D(0))
    assert got["realized_delta_loss_usd"] == "5.3789"


def test_a_FOREIGN_baseline_refuses_rather_than_measuring_from_it(root):
    b = baseline(root, cid=OTHER, realized="-10.6211")
    g = gates(root, cid=CID, base=b)
    why = refuse(g)
    assert why is not None and "FOREIGN" in why


def test_an_ABSENT_baseline_refuses_and_never_falls_back_to_lifetime(root):
    g = gates(root)                     # no capture at all
    why = refuse(g)
    assert why is not None
    assert "BASELINE" in why


def test_a_missing_baseline_OBJECT_refuses(root):
    g = gates(root, base=None)
    g.campaign_baseline = None
    assert refuse(g) == "CAMPAIGN_BASELINE_UNAVAILABLE"


# ================= 5. both gates share the campaign-scoped basis

def test_refuse_arming_does_not_inherit_a_CLOSED_campaigns_peak(root):
    """THE SECOND DEFECT. camp1.1c6b9f88 closed with peak 11.3369 and
    refused the fresh camp1.9c793662 at the arming boundary."""
    led = CBD.CampaignBudgetLedger(root=root, budget_usd="5")
    led.open_or_resume(OTHER, now_iso="2026-11-04T00:00:00+00:00")
    # THE PEAK ONLY EVER RECORDS *ALLOWED* USAGE: refuse_amount returns
    # before recording when projected exceeds the budget. That is how the
    # real 11.3369 came to be stored -- it was allowed under the $29.30
    # budget in force at the time, and only became exhausting when the
    # budget was later tightened to $5.
    wide = CBD.CampaignBudgetLedger(root=root, budget_usd="29.30")
    assert wide.refuse_amount(campaign_id=OTHER,
                              used_usd=D("11.3369")) is None
    led.close(OTHER, now_iso="2026-11-04T00:20:00+00:00")
    assert _d(led.read().get("peak_usd")) >= D("11.3369")
    assert led.refuse_arming(campaign_id=CID) is None, \
        "a finished campaign's history must not refuse a new campaign"


def test_refuse_arming_STILL_refuses_this_campaigns_own_exhaustion(root):
    """The anti-reset property is preserved: our own spent budget refuses."""
    led = CBD.CampaignBudgetLedger(root=root, budget_usd="5")
    led.open_or_resume(CID, now_iso="2026-11-04T00:00:00+00:00")
    # exactly AT the budget is allowed and recorded; the next arming is not
    assert led.refuse_amount(campaign_id=CID, used_usd=D("5")) is None
    why = led.refuse_arming(campaign_id=CID)
    assert why is not None and "CAMPAIGN_WORST_CASE_EXHAUSTED" in why


def test_refuse_arming_still_refuses_an_OPEN_foreign_campaign(root):
    led = CBD.CampaignBudgetLedger(root=root, budget_usd="5")
    led.open_or_resume(OTHER, now_iso="2026-11-04T00:00:00+00:00")
    why = led.refuse_arming(campaign_id=CID)
    assert why is not None and "CAMPAIGN_BUDGET_LOCKED" in why


def test_a_nonce_rotation_cannot_escape_an_open_budget(root):
    """Requirement 4 at the budget layer: rotating the nonce mid-campaign
    does not mint a new budget."""
    led = CBD.CampaignBudgetLedger(root=root, budget_usd="5")
    led.open_or_resume(CID, now_iso="2026-11-04T00:00:00+00:00")
    assert led.refuse_amount(campaign_id=CID, used_usd=D("5")) is None
    assert led.open_or_resume(OTHER)["ok"] is False
    assert "LOCKED" in led.open_or_resume(OTHER)["reason"]


# ============================= 6. settlement and restart

def test_SETTLEMENT_releases_commitment_but_never_the_loss(root):
    b = baseline(root, realized="-10.6211")
    g = gates(root, base=b)
    unsettled = [{"market": "m", "cash_out": D("5.00"), "settled": False}]
    assert refuse(g, legs=unsettled, price="0.90", qty="1") is not None
    # settled FLAT: the commitment is handed back, realized unchanged
    assert refuse(g, legs=[{"market": "m", "cash_out": D("5.00"),
                            "settled": True}]) is None
    # settled at a LOSS: realized moves and the loss persists
    why = refuse(g, realized="-16.6211",
                 legs=[{"market": "m", "cash_out": D("4.00"),
                        "settled": True}])
    assert why is not None and "CAMPAIGN_WORST_CASE" in why


def test_a_RESTART_preserves_the_baseline_and_the_peak(root):
    """Both live on disk, so a restart changes neither."""
    b = baseline(root, realized="-10.6211")
    led = CBD.CampaignBudgetLedger(root=root, budget_usd="5")
    led.open_or_resume(CID, now_iso="2026-11-04T00:00:00+00:00")
    led.refuse_amount(campaign_id=CID, used_usd=D("3.00"))
    # fresh objects == a new process
    b2 = CBL.CampaignBaseline(root=root)
    led2 = CBD.CampaignBudgetLedger(root=root, budget_usd="5")
    assert b2.read()["account_wide_realized"] == "-10.6211"
    assert b2.read()["campaign_id"] == CID
    assert _d(led2.read().get("peak_usd")) == D("3.00")
    assert led2.refuse_arming(campaign_id=CID) is None


# ================= 7. independent controls are untouched

def test_the_CASH_ceiling_still_binds_independently(root):
    b = baseline(root, realized="0")
    g = gates(root, base=b)
    legs = [{"market": "m", "cash_out": D("11.50"), "settled": False}]
    why = refuse(g, realized="0", legs=legs, price="0.90", qty="1")
    assert why is not None and "CASH_COMMITMENT" in why


def test_an_exhausted_budget_refuses_even_with_a_clean_baseline(root):
    """Requirement 6: a campaign-scoped basis must not become a way past
    the budget itself."""
    b = baseline(root, realized="0")
    g = gates(root, base=b)
    legs = [{"market": "m", "cash_out": D("5.50"), "settled": False}]
    why = refuse(g, realized="0", legs=legs, price="0.01", qty="1")
    assert why is not None and "CAMPAIGN_WORST_CASE" in why


def test_the_budget_and_the_ceilings_are_unchanged_constants():
    assert CBD.DEFAULT_BUDGET_USD == D("5.00")
    assert CBL.MAX_ADDITIONAL_LOSS_USD == D("10.00")


def _d(v):
    return D(str(v or 0))


# ========== 8. a FRESH campaign is allowed only when the independent
#               account-wide gates also pass

def _gates_with_l5_ceilings(root, base, *, daily="5", cumulative="20"):
    """The same gate, with the two independent L5 loss ceilings configured.

    These read `l5_scoped_realized` / `l5_scoped_realized_today` -- the
    account-wide post-baseline figures -- and they are checked BEFORE the
    campaign term. Scoping the campaign budget must not give a fresh
    campaign a way past them.
    """
    return VG.ValidationGates(
        max_cash_deployed_usd=D("12"),
        max_l5_daily_loss_usd=D(daily),
        max_l5_cumulative_loss_usd=D(cumulative),
        campaign_budget=CBD.CampaignBudgetLedger(root=root, budget_usd="5"),
        campaign_id=CID,
        campaign_baseline=base,
        cash_policy=VG.NO_RECYCLING)


def test_a_fresh_campaign_still_refuses_on_the_DAILY_halt(root):
    """REQUIREMENT 6. The campaign's own baseline is clean -- it has lost
    nothing -- but today's account-wide realized loss has reached the $5
    daily halt. The fresh campaign is refused anyway, on the daily control,
    not on the campaign budget."""
    b = baseline(root, realized="-10.6211")
    g = _gates_with_l5_ceilings(root, b)
    why = g.refuse(order_price=D("0.50"), order_qty=D("1"),
                   order_intent="ORDER_INTENT_BUY_LONG",
                   cumulative_realized=D("-10.6211"),
                   l5_scoped_realized=D("-5.00"),
                   l5_scoped_realized_today=D("-5.00"))
    assert why is not None, "the daily halt must still refuse"
    assert why.startswith("L5_DAILY_LOSS_LIMIT:"), why


def test_a_fresh_campaign_still_refuses_on_the_CUMULATIVE_ceiling(root):
    """REQUIREMENT 6, lifetime arm. Nothing lost by THIS campaign, no loss
    today, but the post-baseline cumulative figure has reached its own $20
    ceiling. Still refused, on the lifetime control."""
    b = baseline(root, realized="-10.6211")
    g = _gates_with_l5_ceilings(root, b)
    why = g.refuse(order_price=D("0.50"), order_qty=D("1"),
                   order_intent="ORDER_INTENT_BUY_LONG",
                   cumulative_realized=D("-10.6211"),
                   l5_scoped_realized=D("-20.00"),
                   l5_scoped_realized_today=D("0"))
    assert why is not None, "the cumulative ceiling must still refuse"
    assert why.startswith("L5_CUMULATIVE_LOSS_LIMIT:"), why


def test_a_fresh_campaign_is_ALLOWED_when_every_gate_passes(root):
    """REQUIREMENT 8, the positive case. $10.6211 of standing historical
    loss, a clean campaign baseline, both independent L5 ceilings unspent,
    cash within the $12 ceiling: the order is allowed. This is the whole
    point of the correction -- and it holds only with all of the above."""
    b = baseline(root, realized="-10.6211")
    g = _gates_with_l5_ceilings(root, b)
    assert g.refuse(order_price=D("0.50"), order_qty=D("1"),
                    order_intent="ORDER_INTENT_BUY_LONG",
                    cumulative_realized=D("-10.6211"),
                    l5_scoped_realized=D("-1.00"),
                    l5_scoped_realized_today=D("-1.00")) is None


def test_a_MISSING_scoped_figure_still_refuses_a_fresh_campaign(root):
    """MISSING IS NOT ZERO survives the change: a configured ceiling with
    no scoped figure refuses rather than reading absence as no loss."""
    b = baseline(root, realized="-10.6211")
    g = _gates_with_l5_ceilings(root, b)
    why = g.refuse(order_price=D("0.50"), order_qty=D("1"),
                   order_intent="ORDER_INTENT_BUY_LONG",
                   cumulative_realized=D("-10.6211"),
                   l5_scoped_realized=None)
    assert why == "L5_SCOPED_REALIZED_UNAVAILABLE", why


# ===== 9. the zero point cannot be re-measured, and outstanding
#          commitments are never forgiven by a rollover

def test_the_baseline_cannot_be_RECAPTURED_AFTER_LOSSES(root):
    """OWNER 2026-11-04. A campaign that has lost money must not be able to
    re-measure its own zero point and win back headroom.

    The campaign starts at -10.6211, loses $4, and `capture` is called again
    with the WORSE figure -- which is what a re-arm inside the same campaign
    does. The stored zero point is returned unchanged, `captured` is False,
    and the $4 is still charged."""
    b = baseline(root, realized="-10.6211")
    again = b.capture(CID, account_wide_realized="-14.6211",
                      now_iso="2026-11-04T03:00:00+00:00")
    assert again["ok"] is True
    assert again["captured"] is False, "a second capture must not re-measure"
    assert again["baseline"]["account_wide_realized"] == "-10.6211"

    # and the loss is still charged against the campaign
    got = b.additional_loss_usd(CID, account_wide_realized=D("-14.6211"),
                                open_worst_case_usd=D(0))
    assert got["realized_delta_loss_usd"] == "4.0000"
    g = gates(root, base=b)
    why = refuse(g, realized="-14.6211", price="0.99", qty="2")
    assert why is not None and "CAMPAIGN_WORST_CASE" in why, why


def test_the_baseline_cannot_be_RECAPTURED_AFTER_A_RESTART(root):
    """A restart constructs a NEW CampaignBaseline over the same durable
    root. It must read the stored zero point back, not write a new one from
    whatever realized figure the restarted process happens to see first."""
    b = baseline(root, realized="-10.6211")
    del b
    fresh = CBL.CampaignBaseline(root=root)          # as a restart would
    res = fresh.capture(CID, account_wide_realized="-13.0000",
                        now_iso="2026-11-04T04:00:00+00:00")
    assert res["captured"] is False
    assert res["baseline"]["account_wide_realized"] == "-10.6211"
    assert fresh.read()["captured_utc"] == "2026-11-04T01:00:00+00:00"


def test_a_ROLLOVER_does_not_forgive_an_OUTSTANDING_COMMITMENT(root):
    """OWNER 2026-11-04: outstanding commitments stay charged.

    A new campaign id does move the realized zero point -- that costs an
    owner action and is the point of the correction. It does NOT discharge
    live commitments: `open_worst_case_usd` is read from the durable ledger
    at evaluation time, so inventory and resting orders that survive the
    rollover are charged in full to whoever is trading now. That is the
    conservative direction and it can never under-charge."""
    b = CBL.CampaignBaseline(root=root)
    assert b.capture(OTHER, account_wide_realized="-10.6211",
                     now_iso="2026-11-04T05:00:00+00:00")["ok"] is True
    g = gates(root, cid=OTHER, base=b)

    # a $4.50 resting BUY_SHORT survives from before: charged at full
    # dollars, as pending_obligation does, so $4.50 of the $5 is spent
    # `pending_obligation` keys on `intent`, and charges a resting SHORT at
    # FULL DOLLARS -- $4.50, not 4.5 * 0.20 -- because a short owes up to $1
    # a share if YES settles 1.
    outstanding = ({"intent": "ORDER_INTENT_BUY_SHORT",
                    "qty": "4.5", "price": "0.20",
                    "market": "cpc-btc-updown-15m-2026-11-04-0500z"},)
    why = g.refuse(order_price=D("0.60"), order_qty=D("1"),
                   order_intent="ORDER_INTENT_BUY_LONG",
                   outstanding=outstanding,
                   cumulative_realized=D("-10.6211"))
    assert why is not None, "the surviving commitment must still be charged"
    assert ("CAMPAIGN_WORST_CASE" in why or "CASH" in why), why


# ===== 10. the PLANNER must size against the budget the GATE enforces

class _Store:
    def __init__(self, realized):
        self._r = D(realized)

    def realized_today(self):
        return self._r


class _Exec:
    """Only what probe_budget_headroom reads. Called unbound, as the
    runner's own self-checks do, so no executor is constructed."""

    def __init__(self, vg, realized):
        self.validation_gates = vg
        self.store = _Store(realized)

    def cash_legs_for_gates(self):
        return ()

    def outstanding_for_gates(self):
        return ()


def _headroom(vg, realized):
    from src import l5_live_execution as LX
    return LX.FaithfulExecutor.probe_budget_headroom(_Exec(vg, realized))


def test_the_PLANNER_headroom_matches_the_GATE_not_lifetime_loss(root):
    """THE DRIFT. Scoping the gate and not the planner made the planner
    refuse every window with CAMPAIGN_BUDGET_EXHAUSTED while the gate it
    exists to anticipate held the full $5. Measured live 2026-11-04T03:34Z
    with $10.6211 of standing account-wide realized loss."""
    b = baseline(root, realized="-10.6211")
    g = gates(root, base=b)
    assert _headroom(g, "-10.6211") == D("5")
    # and the gate agrees: the same state allows an order
    assert refuse(g, realized="-10.6211") is None


def test_the_planner_charges_THIS_campaigns_own_loss(root):
    """Not a relaxation: a loss the campaign itself caused still reduces
    the room the planner sizes against."""
    b = baseline(root, realized="-10.6211")
    g = gates(root, base=b)
    assert _headroom(g, "-13.6211") == D("2")      # $3 of the $5 spent
    assert _headroom(g, "-16.6211") == D("0")      # exhausted


def test_the_planner_reports_NO_ROOM_when_the_baseline_is_unestablished(root):
    """Unreadable is never headroom, and an absent baseline is unreadable.
    Same fail-closed direction the method already had."""
    g = gates(root, base=CBL.CampaignBaseline(root=root))   # nothing captured
    assert _headroom(g, "-10.6211") == D("0")


def test_the_planner_is_UNCONSTRAINED_with_no_campaign_budget(root):
    """None means no campaign is running; build_orders treats that as
    unconstrained. The pre-campaign behaviour, unchanged."""
    vg = VG.ValidationGates(max_cash_deployed_usd=D("12"),
                            cash_policy=VG.NO_RECYCLING)
    assert _headroom(vg, "-10.6211") is None
