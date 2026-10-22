"""The campaign worst-case budget: anti-reset, settlement-only release, and
the failure modes that have actually bitten this system before.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_campaign_budget as CB


def ledger(tmp_path, budget="20"):  # tests pin their own budget explicitly
    return CB.CampaignBudgetLedger(root=str(tmp_path), budget_usd=budget)


# --------------------------------------------------------------- anti-reset

def test_a_different_nonce_cannot_open_a_second_budget(tmp_path):
    """THE REQUIREMENT. Rotating the nonce mid-campaign must not mint a
    fresh budget -- it must be refused."""
    L = ledger(tmp_path)
    assert L.open_or_resume("camp1.alpha")["ok"] is True
    out = L.open_or_resume("camp1.beta")
    assert out["ok"] is False
    assert out["reason"].startswith("CAMPAIGN_BUDGET_LOCKED")
    assert out["campaign_id"] == "camp1.alpha"


def test_rotating_nonce_does_not_clear_spent_budget(tmp_path):
    L = ledger(tmp_path)
    L.open_or_resume("camp1.alpha")
    legs = [{"cost_usd": "19", "settled": False}]
    assert L.refuse(campaign_id="camp1.alpha", legs=legs,
                    new_order_cost_usd="0") is None
    # the new nonce is refused outright, and the peak is still there
    assert L.refuse(campaign_id="camp1.beta", legs=[],
                    new_order_cost_usd="1").startswith(
                        "CAMPAIGN_BUDGET_LOCKED")
    assert D(L.telemetry()["peak_usd"]) >= D("19")


def test_experiment_id_is_never_consulted(tmp_path, monkeypatch):
    """Rotating L5_EXPERIMENT_ID cannot affect this ledger.

    Proven two ways: the module reads no environment variable other than the
    volume root, and the verdict is identical across a rotation.
    """
    import re
    src = (CB.__file__).replace(".pyc", ".py")
    with open(src) as fh:
        body = fh.read()
    read_envs = set(re.findall(r"getenv\(\s*([A-Z_]+)", body))
    assert read_envs <= {"ROOT_ENV"}, read_envs

    L = ledger(tmp_path)
    L.open_or_resume("c1")
    book = [{"cost_usd": "19", "settled": False}]
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-one")
    first = L.refuse(campaign_id="c1", legs=book, new_order_cost_usd="4")
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-two-rotated")
    second = L.refuse(campaign_id="c1", legs=book, new_order_cost_usd="4")
    assert first == second and first is not None


def test_settled_losses_persist_but_flat_cycling_is_allowed(tmp_path):
    """The two halves of a LOSS budget, asserted together.

    A window that settles FLAT returns its exposure -- otherwise eight
    sequential windows could never run. A window that settles at a LOSS
    keeps consuming the budget forever.
    """
    L = ledger(tmp_path, budget="20")
    L.open_or_resume("c1")
    # $18 at risk, then settles flat: exposure is released
    assert L.refuse(campaign_id="c1",
                    legs=[{"cost_usd": "18", "settled": False}]) is None
    flat = [{"cost_usd": "18", "settled": True, "realized_usd": "0"}]
    assert L.refuse(campaign_id="c1", legs=flat,
                    new_order_cost_usd="5") is None
    # but the same $18 settling as a LOSS is permanent and binds
    lost = [{"cost_usd": "18", "settled": True, "realized_usd": "-18"}]
    out = L.refuse(campaign_id="c1", legs=lost, new_order_cost_usd="5")
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


# ------------------------------------------------- settlement, not pairing

def test_matched_but_unsettled_pair_still_consumes_full_exposure(tmp_path):
    """A 'matched' pair is not riskless until the venue settles it."""
    L = ledger(tmp_path)
    L.open_or_resume("c1")
    legs = [{"cost_usd": "11", "settled": False, "matched": True},
            {"cost_usd": "11", "settled": False, "matched": True}]
    out = L.refuse(campaign_id="c1", legs=legs)
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


def test_settlement_releases_exposure_using_realized_economics(tmp_path):
    L = ledger(tmp_path, budget="20")
    L.open_or_resume("c1")
    settled = [{"cost_usd": "11", "settled": True, "realized_usd": "-1"},
               {"cost_usd": "11", "settled": True, "realized_usd": "0.5"}]
    # only the $1 realized loss counts now, not the $22 of cost
    assert CB.worst_case_usd(legs=settled) == D("1")
    assert L.refuse(campaign_id="c1", legs=settled) is None


def test_realized_gain_does_not_create_headroom(tmp_path):
    assert CB.worst_case_usd(realized_usd="5") == D(0)
    assert CB.worst_case_usd(realized_usd="-5") == D("5")


# ------------------------------------------------------ the failure modes

def test_partial_fill_counts_filled_cost_and_remaining_notional(tmp_path):
    """A partially filled order is BOTH a position and a live order."""
    used = CB.worst_case_usd(
        legs=[{"cost_usd": "4", "settled": False}],          # the filled part
        outstanding=[{"leaves_qty": "10", "price": "0.6"}])  # the rest
    assert used == D("4") + D("6")


def test_failed_cancellation_still_counts_the_resting_order(tmp_path):
    """A cancel that did not take leaves the order live; it keeps consuming
    budget because it can still fill."""
    L = ledger(tmp_path)
    L.open_or_resume("c1")
    out = L.refuse(campaign_id="c1",
                   outstanding=[{"leaves_qty": "40", "price": "0.55"}])
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


def test_unknown_order_state_is_not_read_as_zero(tmp_path):
    """An unreadable ledger refuses rather than reporting an empty budget."""
    L = ledger(tmp_path)
    L.open_or_resume("c1")
    with open(L._path(), "w") as fh:
        fh.write("{ not json")
    assert L.refuse(campaign_id="c1") == "CAMPAIGN_BUDGET_UNREADABLE"
    with pytest.raises(CB.CampaignBudgetUnavailable):
        L.read()


def test_residual_inventory_counts_in_full_until_settled(tmp_path):
    """The previous campaign's actual failure: unmatched residual."""
    legs = [{"cost_usd": "3", "settled": False, "residual_units": 3}]
    assert CB.worst_case_usd(legs=legs) == D("3")


def test_settlement_loss_accumulates_against_the_budget(tmp_path):
    L = ledger(tmp_path, budget="20")
    L.open_or_resume("c1")
    lost = [{"cost_usd": "6", "settled": True, "realized_usd": "-6"}
            for _ in range(3)]
    out = L.refuse(campaign_id="c1", legs=lost, new_order_cost_usd="3")
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


def test_restart_recovery_keeps_ownership_and_still_binds(tmp_path):
    """A redeploy must not reopen the budget or forget who owns it."""
    L1 = ledger(tmp_path)
    L1.open_or_resume("c1")
    book = [{"cost_usd": "17", "settled": False}]
    L1.refuse(campaign_id="c1", legs=book)

    # a brand-new process, same volume
    L2 = ledger(tmp_path)
    assert L2.is_active() is True
    assert D(L2.telemetry()["peak_usd"]) >= D("17")
    # the campaign is still owned: a rotated nonce is still refused
    assert L2.open_or_resume("c2")["ok"] is False
    # and the still-unsettled book still binds against a further order
    out = L2.refuse(campaign_id="c1", legs=book, new_order_cost_usd="5")
    assert out is not None and out.startswith("CAMPAIGN_WORST_CASE_BUDGET")


# ------------------------------------------------------------- lifecycle

def test_close_is_refused_while_outstanding_or_unsettled(tmp_path):
    L = ledger(tmp_path)
    L.open_or_resume("c1")
    assert L.close("c1", outstanding=[{"leaves_qty": "1", "price": "0.5"}]
                   )["reason"] == "CAMPAIGN_OUTSTANDING_ORDERS"
    r = L.close("c1", legs=[{"cost_usd": "1", "settled": False}])
    assert r["ok"] is False and r["reason"].startswith(
        "CAMPAIGN_UNSETTLED_EXPOSURE")


def test_close_then_a_new_campaign_may_open(tmp_path):
    L = ledger(tmp_path)
    L.open_or_resume("c1")
    assert L.close("c1", legs=[{"cost_usd": "1", "settled": True,
                                "realized_usd": "0"}])["ok"] is True
    assert L.open_or_resume("c2")["ok"] is True


def test_a_foreign_campaign_cannot_close_the_budget(tmp_path):
    L = ledger(tmp_path)
    L.open_or_resume("c1")
    assert L.close("c2")["ok"] is False


# ------------------------------------- eight windows, sequential pairing

def test_eight_sequential_windows_fit_inside_the_budget_when_settled(tmp_path):
    """Eight windows of OPEN->COMPLETE->settle, one market at a time.

    Each window commits $2 and settles flat. Settlement releases it, so the
    campaign runs all eight inside $20 -- which is the behaviour the budget
    must permit, not just the behaviour it must refuse.
    """
    L = ledger(tmp_path, budget="20")
    L.open_or_resume("camp1.eight")
    settled = []
    for i in range(8):
        live = settled + [{"cost_usd": "2", "settled": False}]
        assert L.refuse(campaign_id="camp1.eight", legs=live) is None, i
        settled.append({"cost_usd": "2", "settled": True,
                        "realized_usd": "0"})
    assert D(L.telemetry()["peak_usd"]) <= D("20")


def test_eight_windows_of_total_losses_are_stopped_before_the_ninth(tmp_path):
    """The same eight windows, each losing its full $2 plus a $6 opener --
    the budget must bind partway rather than letting all eight run."""
    L = ledger(tmp_path, budget="20")
    L.open_or_resume("camp1.eight")
    book = []
    refused_at = None
    for i in range(8):
        out = L.refuse(campaign_id="camp1.eight", legs=book,
                       new_order_cost_usd="3")
        if out is not None:
            refused_at = i
            break
        book.append({"cost_usd": "3", "settled": True, "realized_usd": "-3"})
    assert refused_at is not None and refused_at < 8


def test_one_live_order_and_one_market_still_bounded_by_instant_cap(tmp_path):
    """The campaign budget is ADDITIVE. A single window may not exceed the
    per-instant envelope either; that is ValidationGates' job, and this
    asserts the campaign gate does not silently widen it."""
    L = ledger(tmp_path, budget="20")
    L.open_or_resume("c1")
    # $20 in one shot is inside the CAMPAIGN budget...
    assert L.refuse(campaign_id="c1", new_order_cost_usd="20") is None
    # ...and $21 is not.
    L2 = ledger(tmp_path, budget="20")
    assert L2.refuse(campaign_id="c1", new_order_cost_usd="21") is not None
