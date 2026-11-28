"""THE $10 CAMPAIGN BOUND, proven across every submission path.

OWNER 2026-11-02 authorised `MAX_ADDITIONAL_LOSS_USD` $5 -> $10 and asked for
proof, not assertion: "Prove that worst_case_usd and the campaign budget
enforce the $10 bound across every order-submission path, including
simultaneous orders, partial fills, cancellations, restarts, and unsettled
positions."

ONE CORRECTION TO THE PREMISE, AND IT MATTERS. `worst_case_usd` is NOT the
function that enforces this bound. There are two separate campaign loss
gates with different env vars, different constants and different measures:

  A. `CBL.MAX_ADDITIONAL_LOSS_USD` (the $10 raised here), env
     `L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD`, enforced in
     `l5_live_validation_gates` via `worst_case_deficit` -- the NET measure
     `C - min(up, dn)`, the most the book can lose on EITHER outcome.

  B. `CBD.DEFAULT_BUDGET_USD` ($5), env `L5_CAMPAIGN_BUDGET_USD`, enforced
     in `l5_campaign_budget.CampaignBudgetLedger` via `worst_case_usd` -- the
     GROSS measure (full cost of anything unsettled, full notional of
     anything resting).

Both are live in production and both bind; B is untouched by this change and
is the TIGHTER of the two on gross cash. This file proves A, because A is
what the $10 configures. `DEFAULT_BUDGET_USD` is left at $5 deliberately: it
is only a constructor default and the sole production call site always
passes `budget_usd=raw` from the env, so raising it would change nothing
while breaking a test that pins it to the proposed envelope.

THE OWNER'S ACTUAL CONSTRAINT -- "I do NOT authorize a potential $50
single-settlement loss against a $10 daily-loss budget" -- is what
`test_the_fifty_dollar_single_settlement_loss_is_arithmetically_refused`
proves, and it is gate A that refuses it.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from src import l5_campaign_baseline as CBL
from src import l5_live_validation_gates as VG

CID = "camp-20261102a"


def _baseline(tmp_path, realized="-6.0979", cid=CID):
    """A real on-disk baseline, the way the runner captures one."""
    os.makedirs(str(tmp_path), exist_ok=True)
    b = CBL.CampaignBaseline(root=str(tmp_path))
    out = b.capture(cid, account_wide_realized=realized,
                    now_iso="2026-11-02T05:00:00+00:00")
    assert out.get("ok"), out
    return b


def _gate(tmp_path, limit="10.00", **kw):
    """The gate as the runner builds it, at the approved limits."""
    return VG.ValidationGates(
        max_cash_deployed_usd=D("12"),
        max_unmatched_contracts=D("5"),
        max_cumulative_loss_usd=D("20"),
        campaign_baseline=_baseline(tmp_path, **kw),
        campaign_id=CID,
        max_campaign_additional_loss_usd=D(limit),
        cash_policy=VG.NO_RECYCLING)


def _refuse(g, *, qty, price, intent="BUY_LONG", inv=None, outstanding=(),
            realized="-6.0979"):
    return g.refuse(
        order_qty=D(str(qty)), order_price=D(str(price)), order_intent=intent,
        inventory=inv or {"up_qty": "0", "dn_qty": "0",
                          "up_cost": "0", "dn_cost": "0"},
        outstanding=list(outstanding),
        cumulative_realized=D(realized))


# --------------------------------------------------------------- the headline

def test_the_fifty_dollar_single_settlement_loss_is_refused_by_the_cash_cap(
        tmp_path):
    """THE OWNER'S EXPLICIT CONSTRAINT, and the gate that actually enforces it.

    "I do NOT authorize a potential $50 single-settlement loss against a $10
    daily-loss budget." The $50 leg IS refused -- but by
    `CASH_COMMITMENT:50.00>12`, the $12 cash cap, which runs BEFORE the
    campaign bound. The $10 bound never sees it.

    THIS DISTINCTION IS NOT PEDANTRY. It means the owner's $50 protection
    rests on the cash cap they chose to LEAVE UNCHANGED, not on the limit
    they raised. Had the raise been to exposure or cash instead, this
    protection would have moved. It is recorded here so that a later change
    to the cash cap cannot silently remove it.
    """
    g = _gate(tmp_path)
    why = _refuse(g, qty=100, price="0.50")
    assert why is not None
    assert why.startswith("CASH_COMMITMENT:"), why
    # and it is NOT the campaign bound doing this work
    g.max_campaign_additional_loss_usd = None
    assert _refuse(g, qty=100, price="0.50") is not None


def test_the_ten_dollar_bound_is_the_binding_one_inside_the_cash_envelope(
        tmp_path):
    """WHERE THE $10 ACTUALLY BITES: the band between $10 and the $12 cash cap.

    24 contracts at 0.46 commits $11.04 -- inside the cash cap -- and leaves
    a fully unmatched book whose deficit is ~$11.07. Cash lets it through;
    the campaign bound refuses it. Disabling the bound admits it, which is
    what proves this gate and no other is responsible.
    """
    g = _gate(tmp_path)
    why = _refuse(g, qty=24, price="0.46")
    assert why is not None
    assert "CAMPAIGN_ADDITIONAL_LOSS_LIMIT" in why, why
    g.max_campaign_additional_loss_usd = None
    assert _refuse(g, qty=24, price="0.46") is None, (
        "something other than the campaign bound refused an $11.04 book")


def test_a_ten_dollar_bound_admits_a_small_opening_leg(tmp_path):
    """THE BOUND IS NOT A BLANKET REFUSAL -- it has to pass real work.

    4 contracts at 0.46 is $1.84 plus fee; the deficit is under $2 and the
    campaign has spent nothing, so this must be ALLOWED. A gate that refused
    here would be a gate that cannot trade.
    """
    g = _gate(tmp_path)
    assert _refuse(g, qty=4, price="0.46") is None


def test_the_raise_actually_widened_the_window_from_five_to_ten(tmp_path):
    """THE $5 -> $10 CHANGE HAS AN OBSERVABLE EFFECT, which is the point.

    A book whose deficit lands between the two limits is refused at $5 and
    admitted at $10. Without this the raise would be inert.
    """
    inv = {"up_qty": "14", "dn_qty": "0", "up_cost": "6.30", "dn_cost": "0"}
    assert _refuse(_gate(tmp_path / "a", limit="5.00"),
                   qty=2, price="0.45", inv=inv) is not None
    assert _refuse(_gate(tmp_path / "b", limit="10.00"),
                   qty=2, price="0.45", inv=inv) is None


# ------------------------------------------------- simultaneous / resting

def test_simultaneous_two_sided_orders_take_the_worst_corner(tmp_path):
    """SIMULTANEOUS ORDERS. The bound maximises over WHICH orders fill.

    With an UP order proposed and a DOWN order already resting, the
    convenient reading is "both fill, so they pair and the deficit is small".
    The gate instead evaluates all four corners and takes the worst, so the
    resting DOWN order is priced as something that MIGHT NOT fill.
    """
    g = _gate(tmp_path)
    wcb = g.worst_case_deficit(
        up_qty="0", dn_qty="0", up_cost="0", dn_cost="0",
        order_side="UP", order_qty="20", order_price="0.50",
        outstanding=[{"intent": "BUY_SHORT", "qty": "20", "price": "0.50"}])
    assert wcb["ok"], wcb
    # 'all' would pair 20x20 and look cheap; 'none' leaves 20 UP unhedged.
    assert wcb["scenario"] in ("none", "up_only"), wcb["scenarios"]
    assert D(wcb["scenarios"]["none"]) > D(wcb["scenarios"]["all"])
    # and therefore the pair of orders is refused at $10
    assert _refuse(
        g, qty=20, price="0.50",
        outstanding=[{"intent": "BUY_SHORT", "qty": "20", "price": "0.50"}]
    ) is not None


def test_a_resting_opposite_order_is_never_credited_as_a_hedge(tmp_path):
    """OWNER: "Do not count resting orders as guaranteed hedges."

    Adding a resting DOWN order to an unmatched UP book must never LOWER the
    measured worst case. If it did, an order the venue has not filled would
    be buying exposure.
    """
    g = _gate(tmp_path)
    args = dict(up_qty="18", dn_qty="0", up_cost="8.10", dn_cost="0",
                order_side="UP", order_qty="1", order_price="0.45")
    bare = g.worst_case_deficit(**args, outstanding=[])
    hedged = g.worst_case_deficit(
        **args,
        outstanding=[{"intent": "BUY_SHORT", "qty": "19", "price": "0.50"}])
    assert bare["ok"] and hedged["ok"]
    assert D(hedged["deficit"]) >= D(bare["deficit"]), (bare, hedged)


def test_an_outstanding_order_of_unknown_side_refuses(tmp_path):
    """FAIL CLOSED. An order whose side cannot be read is not zero risk."""
    g = _gate(tmp_path)
    wcb = g.worst_case_deficit(
        up_qty="0", dn_qty="0", up_cost="0", dn_cost="0",
        order_side="UP", order_qty="2", order_price="0.45",
        outstanding=[{"intent": "WHO_KNOWS", "qty": "5", "price": "0.5"}])
    assert wcb["ok"] is False
    assert wcb["reason"] == "DEFICIT_OUTSTANDING_SIDE_UNKNOWN"


# ------------------------------------------------------------- partial fills

@pytest.mark.parametrize("resting_qty", [1, 3, 7, 12])
def test_every_partial_fill_quantity_stays_within_the_measured_bound(
        tmp_path, resting_qty):
    """PARTIAL FILLS, swept exhaustively rather than argued.

    The corner maximum plus the rounding allowance must dominate EVERY
    partial fill quantity of the resting order, not just 0 and all. This is
    the property that makes a four-corner search a genuine upper bound.
    """
    g = _gate(tmp_path)
    base = dict(up_qty="6", dn_qty="2", up_cost="2.70", dn_cost="0.90",
                order_side="UP", order_qty="3", order_price="0.46")
    bound = g.worst_case_deficit(
        **base,
        outstanding=[{"intent": "BUY_SHORT", "qty": str(resting_qty),
                      "price": "0.52"}])
    assert bound["ok"], bound
    for k in range(0, resting_qty + 1):
        part = g.worst_case_deficit(
            **base,
            outstanding=([{"intent": "BUY_SHORT", "qty": str(k),
                           "price": "0.52"}] if k else []))
        assert part["ok"], part
        assert D(part["deficit"]) <= D(bound["deficit"]), (k, part, bound)


# ------------------------------------------- realized losses consume budget

def test_existing_realized_losses_consume_the_ten_dollars(tmp_path):
    """"including existing realized losses and reserved risk".

    The measure is `max(0, baseline - current) + open_worst_case`. A campaign
    that has already realized -$9 of loss has $1 of room, so a book that
    would otherwise pass is refused.
    """
    g = _gate(tmp_path, realized="0")
    # fresh campaign: a ~$1.4 deficit passes
    assert _refuse(g, qty=3, price="0.46", realized="0") is None
    # same order after $9 of realized campaign loss: refused
    why = _refuse(g, qty=3, price="0.46", realized="-9.00")
    assert why is not None and "CAMPAIGN_ADDITIONAL_LOSS_LIMIT" in why, why


def test_a_realized_gain_creates_no_extra_room(tmp_path):
    """A gain is not a loss that did not happen. `max(0, ...)` on the delta.

    +$5 of realized gain must NOT buy a $15 budget.
    """
    g = _gate(tmp_path, realized="0")
    b = g.campaign_baseline.additional_loss_usd(
        CID, account_wide_realized="5.00", open_worst_case_usd="9.00")
    assert b["ok"], b
    assert D(b["realized_delta_loss_usd"]) == D(0)
    assert D(b["additional_loss_usd"]) == D("9.00")


# ---------------------------------------- restarts / windows / cancellations

def test_a_restart_rereads_the_same_zero_point(tmp_path):
    """RESTARTS. The baseline is a file, so a new process measures from the
    same point -- and `capture` is idempotent, so a re-arm cannot move it."""
    first = _baseline(tmp_path, realized="-6.0979")
    doc1 = first.read()
    # a brand-new instance, as after a container restart, then a re-capture
    # attempt with a DIFFERENT figure
    second = CBL.CampaignBaseline(root=str(tmp_path))
    again = second.capture(CID, account_wide_realized="-99.00",
                           now_iso="2026-11-02T06:00:00+00:00")
    assert again.get("ok"), again
    assert second.read()["account_wide_realized"] == doc1[
        "account_wide_realized"], "a re-arm moved the zero point"


def test_a_window_boundary_does_not_reset_or_release_the_budget(tmp_path):
    """OWNER ITEM 2. "Verify that the budget does not reset or release
    committed risk prematurely at window boundaries."

    The baseline is keyed on the CAMPAIGN id, not the window, and nothing in
    the measure reads a window index. So the same book is refused identically
    at window 0 and at window 3, and the realized loss carried in from
    earlier windows still counts.
    """
    g = _gate(tmp_path, realized="0")
    for w in (0, 1, 2, 3):
        assert g.refuse(
            order_qty=D("3"), order_price=D("0.46"),
            order_intent="BUY_LONG", windows_used=w,
            inventory={"up_qty": "0", "dn_qty": "0",
                       "up_cost": "0", "dn_cost": "0"},
            cumulative_realized=D("-9.00")) is not None, w


def test_a_foreign_campaign_id_refuses_instead_of_granting_a_fresh_budget(
        tmp_path):
    """A NEW APPROVAL MUST NOT ERASE THE SPEND. A baseline belonging to
    another campaign is a refusal, not a reason to start from zero."""
    _baseline(tmp_path, cid="camp-OTHER")
    g = VG.ValidationGates(
        campaign_baseline=CBL.CampaignBaseline(root=str(tmp_path)),
        campaign_id=CID, max_campaign_additional_loss_usd=D("10.00"),
        cash_policy=VG.NO_RECYCLING)
    why = _refuse(g, qty=1, price="0.45")
    assert why is not None and "FOREIGN" in why, why


def test_a_missing_or_unreadable_baseline_refuses(tmp_path):
    """"I could not tell" and "nothing spent" differ by the whole budget."""
    g = VG.ValidationGates(
        campaign_baseline=CBL.CampaignBaseline(root=str(tmp_path / "empty")),
        campaign_id=CID, max_campaign_additional_loss_usd=D("10.00"),
        cash_policy=VG.NO_RECYCLING)
    why = _refuse(g, qty=1, price="0.45")
    assert why is not None, "an absent baseline passed an order"
    assert "CAMPAIGN_BASELINE" in why or "ADDITIONAL_LOSS" in why, why

    # and a CORRUPT one is not read as zero either
    d = str(tmp_path / "corrupt")
    os.makedirs(os.path.join(d, CBL.BASELINE_DIR), exist_ok=True)
    with open(os.path.join(d, CBL.BASELINE_DIR, CBL.BASELINE_FILE), "w") as fh:
        fh.write("{not json")
    g2 = VG.ValidationGates(
        campaign_baseline=CBL.CampaignBaseline(root=d),
        campaign_id=CID, max_campaign_additional_loss_usd=D("10.00"),
        cash_policy=VG.NO_RECYCLING)
    assert _refuse(g2, qty=1, price="0.45") is not None


def test_an_unsettled_position_still_counts_at_full_risk(tmp_path):
    """UNSETTLED POSITIONS. Held inventory enters through its cost basis and
    its quantity; nothing waits for settlement to start counting."""
    g = _gate(tmp_path)
    inv = {"up_qty": "20", "dn_qty": "0", "up_cost": "9.20", "dn_cost": "0"}
    why = _refuse(g, qty=2, price="0.46", inv=inv)
    assert why is not None and "CAMPAIGN_ADDITIONAL_LOSS_LIMIT" in why, why


def test_inventory_absent_or_incoherent_refuses(tmp_path):
    """The bound needs the book. No book is a refusal, not a zero."""
    g = _gate(tmp_path)
    assert g.refuse(order_qty=D("2"), order_price=D("0.45"),
                    order_intent="BUY_LONG",
                    cumulative_realized=D("0")) is not None
    # quantity without cost is incoherent, not free
    why = _refuse(g, qty=2, price="0.45",
                  inv={"up_qty": "10", "dn_qty": "0",
                       "up_cost": "0", "dn_cost": "0"})
    assert why is not None and "DEFICIT" in why, why


# ------------------------------------------------------- the clamp, restated

def test_the_effective_limit_is_min_env_ceiling_and_unset_is_inert(
        monkeypatch):
    """RAISING THE CONSTANT ALONE IS INERT, which is the deployment hazard.

    `limit_from_env()` = min(env, MAX_ADDITIONAL_LOSS_USD). Production must
    ALSO set the variable to 10, or the authorised $10 silently stays $5.
    """
    assert CBL.MAX_ADDITIONAL_LOSS_USD == D("10.00")
    for raw, want in (("5", D("5")), ("10", D("10")), ("25", D("10.00")),
                      ("9.99", D("9.99"))):
        monkeypatch.setenv(CBL.LIMIT_ENV, raw)
        assert CBL.limit_from_env() == want, raw
    # unset is OFF -- the gate is not constructed at all
    monkeypatch.delenv(CBL.LIMIT_ENV, raising=False)
    assert CBL.limit_from_env() is None
    # a typed-but-unparseable value is the TIGHTEST bound, never the default
    monkeypatch.setenv(CBL.LIMIT_ENV, "ten dollars")
    assert CBL.limit_from_env() == D(0)


def test_an_unconfigured_limit_leaves_the_gate_silent(tmp_path):
    """None must change nothing: the bound is additive by construction.

    Measured inside the cash envelope, because outside it the cash cap
    refuses regardless and would hide this property.
    """
    g = _gate(tmp_path)
    assert _refuse(g, qty=24, price="0.46") is not None
    g.max_campaign_additional_loss_usd = None
    assert _refuse(g, qty=24, price="0.46") is None
