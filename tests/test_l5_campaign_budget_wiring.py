"""The campaign budget as the EXECUTOR sees it: through ValidationGates.

The unit tests prove the ledger. These prove the wiring -- that the budget
actually binds on the per-order path, that it is fed the same reconciled
figures the cash gate uses, and that the three evasion routes are closed.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_campaign_budget as CB
from src import l5_live_validation_gates as VG


def gates(tmp_path, budget="11.10", cid="camp1.alpha", cash="100"):
    led = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd=budget)
    led.open_or_resume(cid)
    return VG.ValidationGates(
        max_cash_deployed_usd=D(cash),
        max_unmatched_contracts=D("1000"),
        max_cumulative_loss_usd=D("1000"),
        campaign_budget=led, campaign_id=cid), led


def ask(g, *, qty="1", price="0.50", legs=(), outstanding=(), realized="0"):
    return g.refuse(order_qty=D(qty), order_price=D(price),
                    order_intent="ORDER_INTENT_BUY_LONG",
                    filled_legs=list(legs), outstanding=list(outstanding),
                    cumulative_realized=D(realized),
                    cumulative_unrealized_loss=D(0))


# ------------------------------------------------------- the gate binds

def test_off_by_default_changes_nothing(tmp_path):
    g = VG.ValidationGates(max_cash_deployed_usd=D("100"),
                           max_unmatched_contracts=D("1000"),
                           max_cumulative_loss_usd=D("1000"))
    assert g.campaign_budget is None
    assert ask(g, qty="20", price="0.50") is None


def test_a_single_order_inside_the_budget_is_allowed(tmp_path):
    g, _ = gates(tmp_path)
    assert ask(g, qty="2", price="0.50") is None


def test_an_order_over_the_budget_is_refused_by_name(tmp_path):
    g, _ = gates(tmp_path)
    out = ask(g, qty="30", price="0.50")          # $15 > $11.10
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


# ------------------------------------------------ the three evasion routes

def test_concurrent_resting_orders_cannot_evade_the_budget(tmp_path):
    """Route 1: split the size across a resting order and a new one."""
    g, _ = gates(tmp_path)
    resting = [{"qty": D("16"), "price": D("0.50"),
                "intent": "ORDER_INTENT_BUY_LONG"}]       # $8 still fillable
    out = ask(g, qty="14", price="0.50", outstanding=resting)   # +$7
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


def test_pending_unsettled_commitment_cannot_evade_the_budget(tmp_path):
    """Route 2: already-filled but unsettled cash."""
    g, _ = gates(tmp_path)
    filled = [{"cash_out": D("9"), "settled": False}]
    out = ask(g, qty="8", price="0.50", legs=filled)            # +$4
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


def test_residual_inventory_counts_until_it_settles(tmp_path):
    """Route 3: unmatched residual is simply an unsettled leg."""
    g, _ = gates(tmp_path)
    residual = [{"cash_out": D("10.50"), "settled": False,
                 "residual_units": 3}]
    out = ask(g, qty="2", price="0.50", legs=residual)
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")
    # once it SETTLES flat the same book is affordable again
    settled = [{"cash_out": D("10.50"), "settled": True}]
    assert ask(g, qty="2", price="0.50", legs=settled) is None


def test_a_short_leg_is_charged_its_full_dollar_worst_case(tmp_path):
    """The budget inherits pending_obligation's BUY_SHORT rule rather than
    recomputing qty*price, which would understate it."""
    g, _ = gates(tmp_path)
    short = [{"qty": D("12"), "price": D("0.10"),
              "intent": "ORDER_INTENT_BUY_SHORT"}]   # $12, not $1.20
    out = ask(g, qty="1", price="0.50", outstanding=short)
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


# ------------------------------------------------------- the named cases

def test_partial_fill_counts_both_halves(tmp_path):
    g, _ = gates(tmp_path)
    filled = [{"cash_out": D("6"), "settled": False}]          # filled part
    rest = [{"qty": D("8"), "price": D("0.75"),
             "intent": "ORDER_INTENT_BUY_LONG"}]               # $6 remaining
    out = ask(g, qty="1", price="0.50", legs=filled, outstanding=rest)
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


def test_a_rejected_complement_leaves_the_opened_leg_consuming_budget(tmp_path):
    """The complement never fills, so the opened leg is naked and unsettled;
    it must keep consuming the budget rather than being netted away."""
    g, _ = gates(tmp_path)
    opened = [{"cash_out": D("11"), "settled": False}]
    assert ask(g, qty="1", price="0.50", legs=opened) is not None


def test_cancellation_releases_only_once_the_order_is_gone(tmp_path):
    g, _ = gates(tmp_path)
    resting = [{"qty": D("20"), "price": D("0.55"),
                "intent": "ORDER_INTENT_BUY_LONG"}]
    assert ask(g, qty="1", price="0.50", outstanding=resting) is not None
    assert ask(g, qty="1", price="0.50", outstanding=[]) is None


def test_settlement_loss_persists_across_a_restart(tmp_path):
    led = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10")
    led.open_or_resume("camp1.alpha")
    g1 = VG.ValidationGates(max_cash_deployed_usd=D("100"),
                            max_unmatched_contracts=D("1000"),
                            max_cumulative_loss_usd=D("1000"),
                            campaign_budget=led, campaign_id="camp1.alpha")
    assert ask(g1, qty="1", price="0.50", realized="-10") is None
    # a brand new process reading the same volume
    led2 = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10")
    g2 = VG.ValidationGates(max_cash_deployed_usd=D("100"),
                            max_unmatched_contracts=D("1000"),
                            max_cumulative_loss_usd=D("1000"),
                            campaign_budget=led2, campaign_id="camp1.alpha")
    assert led2.is_active() is True
    out = ask(g2, qty="4", price="0.50", realized="-10")   # 10 + 2 > 11.10
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


def test_a_rotated_nonce_is_refused_on_the_order_path_too(tmp_path):
    g, led = gates(tmp_path)
    g2 = VG.ValidationGates(max_cash_deployed_usd=D("100"),
                            max_unmatched_contracts=D("1000"),
                            max_cumulative_loss_usd=D("1000"),
                            campaign_budget=led, campaign_id="camp1.ROTATED")
    out = ask(g2, qty="1", price="0.50")
    assert out is not None and out.startswith("CAMPAIGN_BUDGET_LOCKED")


def test_an_unreadable_budget_refuses_rather_than_passes(tmp_path):
    g, led = gates(tmp_path)
    with open(led._path(), "w") as fh:
        fh.write("{ not json")
    assert ask(g, qty="1", price="0.50") == "CAMPAIGN_BUDGET_UNREADABLE"


def test_telemetry_reports_the_budget_without_secrets(tmp_path):
    g, _ = gates(tmp_path)
    d = g.as_dict()
    assert d["campaign_id"] == "camp1.alpha"
    assert d["campaign_budget"]["budget_usd"] == "11.10"
    assert d["campaign_budget"]["readable"] is True


# --------------------------------------------------------- arming gate

def test_arming_is_refused_once_the_budget_is_exhausted(tmp_path):
    led = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10")
    led.open_or_resume("camp1.alpha")
    assert led.refuse_arming(campaign_id="camp1.alpha") is None
    # spend it
    led.refuse_amount(campaign_id="camp1.alpha", used_usd=D("11.10"))
    out = led.refuse_arming(campaign_id="camp1.alpha")
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_EXHAUSTED")


def test_arming_is_refused_for_a_campaign_that_does_not_own_the_budget(
        tmp_path):
    led = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10")
    led.open_or_resume("camp1.alpha")
    out = led.refuse_arming(campaign_id="camp1.beta")
    assert out is not None and out.startswith("CAMPAIGN_BUDGET_LOCKED")


def test_the_arming_factory_is_off_unless_configured(tmp_path, monkeypatch):
    from src import l5_live_authorization as AUTH
    monkeypatch.delenv(AUTH.CAMPAIGN_BUDGET_ENV, raising=False)
    assert AUTH.campaign_budget_ledger() is None
    monkeypatch.setenv(AUTH.CAMPAIGN_BUDGET_ENV, "11.10")
    led = AUTH.campaign_budget_ledger(root=str(tmp_path))
    assert led is not None and led.budget_usd == D("11.10")


# ------------------------------- durable ownership (the inert-lock defect)

def test_without_a_claim_the_amount_binds_but_the_lock_does_not(tmp_path):
    """THE DEFECT THIS PINS. An unopened ledger still enforces the AMOUNT,
    which is why the gap was invisible in telemetry -- the number looked
    enforced while the ownership lock was not."""
    led = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10")
    # never opened
    assert led.is_active() is False
    assert led.refuse_amount(campaign_id="c1", used_usd=D("12")) is not None
    # ...but a foreign campaign is NOT refused, and no peak is recorded
    assert led.refuse_amount(campaign_id="OTHER", used_usd=D("1")) is None
    assert D(led.telemetry()["peak_usd"]) == D(0)


def test_claiming_makes_the_lock_and_the_peak_real(tmp_path):
    led = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10")
    assert led.open_or_resume("camp1.alpha", now_iso="t0")["ok"] is True
    assert led.is_active() is True
    assert led.refuse_amount(campaign_id="camp1.alpha",
                             used_usd=D("9")) is None
    # the peak is now durable
    assert D(led.telemetry(campaign_id="camp1.alpha")["peak_usd"]) == D("9")
    assert led.telemetry(campaign_id="camp1.alpha")["owns_budget"] is True
    # and a rotated nonce is refused on the ORDER path
    assert led.refuse_amount(campaign_id="camp1.ROTATED",
                             used_usd=D("1")).startswith(
                                 "CAMPAIGN_BUDGET_LOCKED")


def test_the_claimed_peak_survives_a_new_process(tmp_path):
    CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10"
                            ).open_or_resume("camp1.alpha", now_iso="t0")
    a = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10")
    a.refuse_amount(campaign_id="camp1.alpha", used_usd=D("10.50"))
    b = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10")
    assert D(b.telemetry()["peak_usd"]) == D("10.50")
    assert b.refuse_arming(campaign_id="camp1.alpha") is None
    b.refuse_amount(campaign_id="camp1.alpha", used_usd=D("11.10"))
    c = CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd="11.10")
    out = c.refuse_arming(campaign_id="camp1.alpha")
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_EXHAUSTED")


def test_authorize_arming_claims_the_budget_after_checking_it():
    """Structural: the claim must follow refuse_arming, never precede it."""
    from src import l5_live_authorization as AUTH
    src = AUTH.__file__.replace(".pyc", ".py")
    body = open(src).read()
    i_check = body.index("refuse_arming(campaign_id=_cid)")
    i_claim = body.index("open_or_resume(")
    assert i_check < i_claim, "the claim must come after the lock check"
