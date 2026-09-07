"""OWNER 5567269295: R4 -- the residual cap in DOLLARS ($2.50 unmatched per
market) and the completion ceiling at PAR (1.00, hard), size untouched.
Every other rung must read exactly as it did."""
from __future__ import annotations

from decimal import Decimal as D

from src import pmus_l5_continuous_session as cont
from src import pmus_l5_ramp_ladder as ladder
from src import pmus_q1_executor as q1
from src import pmus_spring_l5_session as base


def test_r4_is_the_owner_rung_with_exactly_two_levers_changed():
    r = ladder.rung("R4")
    # unchanged from R3L
    assert r["per_market_cap_usd"] == "7.50" and r["markets_per_campaign"] == 3
    assert r["concurrency"] == 1 and r["campaigns_per_day"] == 1
    assert r["max_actions_per_session"] == 6 and r["residual_units"] == 6
    assert r["target_notional_usd"] == "2.50" and r["max_leg_notional_usd"] == "3.25"
    assert r["daily_loss_stop_usd"] == "-15.00" and r["promote"] is None
    # the two levers
    assert r["max_unmatched_usd"] == "2.50" and r["pair_cost_max"] == "1.00"
    assert ladder.shell_version(r) == (
        "R4/cap7.50/unmatched2.50/complete1.00/conc1/maker-only/rest30s/action2.50")
    wc = ladder.worst_case(r)
    # capital DEPLOYED is still bounded by the spend cap ...
    assert wc["per_market_usd"] == "7.50" and wc["per_campaign_usd"] == "22.50"
    # ... and dollars AT RISK by the unmatched cap; both reported, never conflated
    assert wc["max_at_risk_per_market_usd"] == "2.50"
    assert wc["per_campaign_at_risk_usd"] == "7.50"
    assert wc["pair_cost_max"] == "1.00"
    assert wc["daily_loss_stop_usd"] == "-15.00"
    assert wc["max_concurrent_open_exposure_usd"] == "7.50"


def test_rung_ids_are_unique_so_no_rung_can_run_under_another_name():
    ids = [r["id"] for r in ladder.ALL_RUNGS]
    assert len(set(ids)) == len(ids)
    assert ids.count("R4") == 1 and "R4-AUTO" in ids
    assert ladder.rung("R4")["max_unmatched_usd"] == "2.50"       # the owner's
    assert ladder.rung("R4-AUTO")["per_market_cap_usd"] == "0.10"  # the prebuilt


def test_every_other_rung_reads_exactly_as_before():
    for rid in ("R1", "R2", "R3", "R4-AUTO", "R5", "R2C", "R2C2", "R3L"):
        r = ladder.rung(rid)
        assert ladder.max_unmatched_usd(r) is None
        assert ladder.pair_cost_max(r) == D("0.98")
        assert "/fee-safe0.98/" in ladder.shell_version(r)
        assert "unmatched" not in ladder.shell_version(r)
        assert "max_at_risk_per_market_usd" not in ladder.worst_case(r)
    assert ladder.shell_version(ladder.rung("R3L")) == (
        "R3L/cap7.50/residual6/conc1/maker-only/fee-safe0.98/rest30s/action2.50")


def test_par_is_a_hard_ceiling_whatever_a_rung_says():
    """OWNER guardrail: 'do not operationally spend rebate beyond par' --
    even the rebate breakeven of ~1.003 is not a legal ceiling."""
    assert ladder.pair_cost_max({"pair_cost_max": "1.003"}) == D("1.00")
    assert ladder.pair_cost_max({"pair_cost_max": "1.10"}) == D("1.00")
    assert ladder.pair_cost_max({}) == D("0.98")


def test_completion_price_goes_to_par_under_r4_and_stays_fee_safe_elsewhere():
    tick = D("0.01")
    # R3L (and every earlier rung): 0.98 target, >=1c hard bound, as always
    ladder.set_active("R3L")
    px, econ = base.spring_complement_price(D("0.46"), D("0.60"), tick)
    assert px == D("0.52") and econ["regime"] == "spring_target"
    assert econ["spring_pair_target"] == "0.98" and econ["pair_cost_max"] == "0.98"
    # R4: the same book, the same surplus cost -> the complement may go to par
    ladder.set_active("R4")
    px4, econ4 = base.spring_complement_price(D("0.46"), D("0.60"), tick)
    assert px4 == D("0.54") and D("0.46") + px4 == D("1.00")
    assert econ4["pair_cost_max"] == "1.00" and econ4["observation_cap"] == "0.98"
    # never ABOVE par: the ceiling is the whole point of the guardrail
    for avg in ("0.30", "0.46", "0.65"):
        p, _ = base.spring_complement_price(D(avg), D("0.99"), tick)
        assert p is not None and D(avg) + p <= D("1.00")
    # still strictly below the opposite ask (maker-only never crosses): the
    # ask binds at 0.49 even though par would have allowed 0.54
    p, e = base.spring_complement_price(D("0.46"), D("0.50"), tick)
    assert p == D("0.49") and D("0.46") + p < D("1.00")
    assert e["chosen_px"] == "0.49"
    ladder.set_active(None)


def _inv(up="0", cost_up="0", dn="0", cost_dn="0"):
    inv = base.Inventory()
    inv.up, inv.cost_up = D(up), D(cost_up)
    inv.dn, inv.cost_dn = D(dn), D(cost_dn)
    return inv


def test_unmatched_dollars_are_unmatched_shares_at_their_own_cost():
    # R3L wisc-nd: 19.27 YES at $6.2855 total, no NO -> all of it unmatched
    inv = _inv(up="19.27", cost_up="6.2855")
    assert base.unmatched_usd(inv) == D("6.2855")
    assert inv.snapshot()["unmatched_usd"] == "6.2855"
    # a matched pair carries none
    flat = _inv(up="5", cost_up="2.25", dn="5", cost_dn="2.75")
    assert base.unmatched_usd(flat) == D("0.0000")
    # NO-long book: unmatched on the NO side at the NO average
    no = _inv(up="5", cost_up="2.25", dn="8", cost_dn="4.00")
    assert base.unmatched_usd(no) == (D("3") * D("0.50")).quantize(D("0.0001"))


def test_unmatched_after_previews_an_order_without_recording_it():
    inv = _inv(up="5.55", cost_up="2.4975")                    # $2.4975 unmatched
    # a second same-side action would double it ...
    after = base.unmatched_usd_after(inv, True, D("0.45"), D("5.55"))
    assert after == D("4.9950")
    # ... a complement of the same size flattens it ...
    assert base.unmatched_usd_after(inv, False, D("0.55"), D("5.55")) == D("0.0000")
    # ... and nothing was recorded either way
    assert inv.up == D("5.55") and inv.dn == D("0")


def test_r4_cap_refuses_a_second_unpaired_action_but_never_a_complement():
    """The exact R3L failure: four same-side fills to $6.29 with no
    complement. Under R4 the second same-side action is refused, before
    submission, under its own name; a first leg and every complement pass."""
    ladder.set_active("R4")
    cap = base.max_unmatched_usd()
    assert cap == D("2.50")
    empty = _inv()
    first = base.unmatched_usd_after(empty, True, D("0.37"), D("5.15"))
    assert first <= cap                                   # $1.9055: allowed
    one = _inv(up="5.15", cost_up="1.9055")
    second = base.unmatched_usd_after(one, True, D("0.37"), D("5.15"))
    assert second == D("3.8110") and second > cap        # refused
    # a complement that REDUCES exposure is allowed however large the residual
    big = _inv(up="19.27", cost_up="6.2855")
    assert base.unmatched_usd_after(big, False, D("0.70"), D("5.00")) < base.unmatched_usd(big)
    ladder.set_active(None)
    # and on a units-only rung the dollar cap simply does not exist
    assert base.max_unmatched_usd() is None


def test_the_dollar_cap_is_classified_on_its_own_never_as_residual_or_spend_cap():
    """OWNER 5567269295: 'If the $2.50 residual cap itself blocks
    L5-consistent actions, classify that separately.'"""
    dv = base.divergence_reason("unmatched_usd_cap:4.9950>2.50", "extend", "hold", "extend")
    assert dv == "safety_adaptation:unmatched_usd_cap"
    assert base.divergence_class(dv) == "safety-cap override (R4 dollar residual cap)"
    assert base.divergence_reason("residual_cap:6>=6", "extend", "hold", "extend") \
        == "safety_adaptation:residual_cap"


def test_r4_approval_is_queued_behind_r3l_and_carries_the_guardrail():
    approvals = cont.load_campaign_approvals()
    r4 = approvals[-1]
    assert r4["campaign_id"] == "R4-RUN-2026-09-07" and r4["rung"] == "R4"
    assert r4["shell"] == ladder.shell_version(ladder.rung("R4"))
    assert "5567269295" in r4["source"]
    assert "hard live pair-cost ceiling at 1.00" in r4["source"]
    assert r4["stop_rule"] == "rung_markets"
    assert "$7.50" in r4["stop_rule_reading"] and "$22.50" in r4["stop_rule_reading"]
    assert any("22.50" in p for p in r4["preconditions"])
    assert "unmatched_usd_cap" in r4["changed"]
    assert "no automatic R5" in r4["not"]


def test_sized_preflight_for_r4_still_requires_the_deployed_worst_case(monkeypatch):
    """Dollars at risk fell to $7.50, but $22.50 can still be DEPLOYED, and
    the fail-closed buying-power check keeps asking for the larger number."""
    from tests.test_l5_rung_r3l import _t
    monkeypatch.setattr(cont, "settle_held", lambda t, s: {
        "status": "REALIZED", "settled": True, "settled_by": "POSITION_RESOLUTION",
        "realized_confirmed": True, "realized_usd": "0.01",
        "venue_qty_total": "0.04", "ledger_qty_total": "0.04"})
    monkeypatch.setattr(cont, "sweep_orphan_orders",
                        lambda t: {"seen": 0, "cancelled": [], "foreign": [], "errors": []})
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-1")
    r4 = ladder.rung("R4")
    pre = cont.continuation_preflight(_t(), {"m"}, {"m"}, rung=r4)
    assert pre["ok"]
    assert pre["checks"]["buying_power"]["required_per_campaign_worst_case_usd"] == "22.50"
    assert pre["checks"]["rung"]["worst_case"]["per_campaign_at_risk_usd"] == "7.50"
    assert cont.continuation_preflight(_t(power="20"), {"m"}, {"m"}, rung=r4)["failed"] \
        == ["buying_power"]


def test_r4_rebate_arithmetic_supports_par_and_no_further():
    """The measured rebate (0.0125 x p(1-p) per share) pays for a pair up to
    ~1.002-1.003 at p in [0.2, 0.5]; the owner keeps the live ceiling at par."""
    for p in ("0.20", "0.30", "0.45", "0.50"):
        per_share = q1.maker_rebate(D(p))
        assert D("0.0020") <= per_share <= D("0.0032")
    assert ladder.pair_cost_max(ladder.rung("R4")) == D("1.00")
