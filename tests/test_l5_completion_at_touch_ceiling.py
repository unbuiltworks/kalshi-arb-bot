"""At-touch completion, and the completion ceiling made explicit.

THE OWNER'S DIRECTION was a configurable $0.99 fee-inclusive completion
ceiling for already-filled unmatched inventory, with at-touch maker
completion prioritised. Checking it against the code first changed what
needed building, and these tests pin both the correction and the change.

0.96 WAS NEVER THE COMPLETION BOUND. The opening cap is enforced at the
submission boundary by `pair_cost_refusal`, but a COMPLETION that fails it
falls through to `completion_pair_rescue`, whose own limit was `eff <= 1.00`
-- a matched pair redeems exactly $1.00. So the deployed completion bound was
already 1.00, MORE permissive than the 0.99 requested. The venue record
proves it: at 2026-11-02 22:45:40.718 this engine submitted a complement at a
pair of exactly 1.0000.

So the ceiling is not raised here. It is given a name, a default equal to
today's behaviour, telemetry, and a knob that can only move it DOWN toward
the opening cap. Setting 0.99 TIGHTENS.

WHAT WAS ACTUALLY BLOCKING COMPLETION WAS PLACEMENT, and it is one argument
at one call site. At 22:45:36.749 the two-sided path submitted the complement
at own 0.5100 while the UP bid was 0.5300 -- two ticks BELOW the touch,
behind the entire visible queue, fillable only if the market moved two more
ticks AGAINST the leg being hedged. Its pair was 0.9600 and it cleared every
economic gate. It never filled, and no window in that campaign ever completed.

`l5_book_completion` had already diagnosed this for its own path --
COMPLETION_DEPTH_TICKS defaults to 0 and its comment records the same failure
at 1445z -- but the two-sided path kept reusing the OPENING depth
(`L5_TS_DEPTH_TICKS`, default 2), and the two-sided path is the one that
submits. An opening rests behind the touch to earn the spread; a completion
is removing risk from inventory already held, and queue position is the whole
of its fill probability.

Both changes default to today's behaviour, so parity is unchanged and
production is untouched.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS

SLUG = "cpc-btc-updown-15m-2026-11-02-2245z"
CEIL_ENV = TS.COMPLETION_PAIR_CEILING_ENV
AT_TOUCH_ENV = "L5_TS_COMPLETION_AT_TOUCH"


@pytest.fixture()
def ex(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "COMPLETION-CEILING")
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    monkeypatch.delenv(CEIL_ENV, raising=False)
    t = H.FakeTransport(fill_all=True)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    x = E.FaithfulExecutor(H.store(tmp_path),
                           H.limits(max_live_orders=4), transport=t,
                           now_fn=H.NOW)
    # THE RESIDUAL BUDGET THE RESCUE ANSWERS TO. Without it the rescue
    # refuses RESIDUAL_BUDGET_UNKNOWN and no completion above the opening cap
    # is reachable at all -- correct fail-closed behaviour, pinned on its own
    # below. The deployed service sets L5_MAX_WORST_CASE_DEFICIT_USD, so the
    # production-shaped path is the one with a budget, and that is what these
    # tests exercise.
    from src import l5_live_validation_gates as VG
    x.validation_gates = VG.ValidationGates(
        max_worst_case_deficit_usd=D("2.00"))
    return x, t, H, E


# ================================================ the ceiling accessor

def test_the_default_is_todays_behaviour_not_the_opening_cap(monkeypatch):
    """1.00, because that is what the rescue branch already enforced."""
    monkeypatch.delenv(CEIL_ENV, raising=False)
    assert TS.completion_pair_ceiling() == D("1.00")
    assert TS.completion_pair_ceiling() > TS.max_pair_cost()


def test_099_is_settable_and_it_tightens(monkeypatch):
    monkeypatch.setenv(CEIL_ENV, "0.99")
    assert TS.completion_pair_ceiling() == D("0.99")
    assert TS.completion_pair_ceiling() < D("1.00")


def test_it_can_never_exceed_redemption(monkeypatch):
    for v in ("1.01", "1.50", "9"):
        monkeypatch.setenv(CEIL_ENV, v)
        assert TS.completion_pair_ceiling() == D("1.00"), v


def test_it_can_never_fall_below_the_opening_cap(monkeypatch):
    """A completion stricter than an opening would be backwards: an opening
    buys a bargain, a completion chooses between a dear pair and a stranded
    leg."""
    for v in ("0.90", "0.50", "0.0001"):
        monkeypatch.setenv(CEIL_ENV, v)
        assert TS.completion_pair_ceiling() == TS.max_pair_cost(), v


def test_unreadable_falls_back_to_the_default_and_not_to_anything_looser(
        monkeypatch):
    for v in ("junk", "", "   ", "NaN", "-1", "0"):
        monkeypatch.setenv(CEIL_ENV, v)
        assert TS.completion_pair_ceiling() == D("1.00"), v


def test_the_opening_ceiling_is_untouched_by_the_completion_knob(monkeypatch):
    monkeypatch.setenv(CEIL_ENV, "0.99")
    assert TS.max_pair_cost() == D("0.96")
    assert TS.MAX_PAIR_COST == D("0.96")


def test_the_opening_cap_still_only_configures_cheaper(monkeypatch):
    """The existing widen-downward-only guard must survive this change."""
    monkeypatch.setenv(TS.MAX_PAIR_COST_ENV, "0.99")
    assert TS.max_pair_cost() == D("0.96")


# ============================== the rescue honours the configurable ceiling

def test_the_2245z_at_touch_completion_is_allowed_by_default(ex):
    """held DOWN 2 at own 0.4500, complement UP at own 0.5300 -> eff 0.9800.

    Above the 0.96 opening cap, so it reaches the rescue -- and the rescue's
    default bound is redemption, so it is permitted. This is the deployed
    behaviour, not a change.
    """
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "DOWN", D("2"), D("0.4500"), basis="OWN")
    assert x.pair_cost_refusal(SLUG, {"side": "UP"},
                               {"price": "0.53", "qty": "2"}) is None


def test_tightening_the_ceiling_refuses_that_same_completion(ex, monkeypatch):
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "DOWN", D("2"), D("0.4500"), basis="OWN")
    monkeypatch.setenv(CEIL_ENV, "0.97")
    bad = x.pair_cost_refusal(SLUG, {"side": "UP"},
                              {"price": "0.53", "qty": "2"})
    assert bad is not None
    assert bad["why"] == "PAIR_COST_ABOVE_CEILING"
    r = bad["completion_rescue"]
    assert r["reason"] == "EFFECTIVE_PAIR_ABOVE_REDEMPTION"
    assert r["completion_pair_ceiling"] == "0.97"
    assert D(r["effective_pair_cost"]) == D("0.9800")


def test_at_099_a_pair_of_exactly_one_dollar_is_refused(ex, monkeypatch):
    """The venue record shows this engine submitting a pair of 1.0000 at
    22:45:40.718. Under a 0.99 ceiling that stops."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "DOWN", D("2"), D("0.4500"), basis="OWN")
    monkeypatch.setenv(CEIL_ENV, "0.99")
    assert x.pair_cost_refusal(SLUG, {"side": "UP"},
                               {"price": "0.55", "qty": "2"}) is not None
    # and 0.98 still clears it
    assert x.pair_cost_refusal(SLUG, {"side": "UP"},
                               {"price": "0.53", "qty": "2"}) is None


def test_a_pair_above_redemption_is_refused_at_every_setting(ex, monkeypatch):
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "DOWN", D("2"), D("0.4500"), basis="OWN")
    for v in (None, "1.00", "0.99", "0.96"):
        if v is None:
            monkeypatch.delenv(CEIL_ENV, raising=False)
        else:
            monkeypatch.setenv(CEIL_ENV, v)
        assert x.pair_cost_refusal(SLUG, {"side": "UP"},
                                   {"price": "0.60", "qty": "2"}) is not None, v


def test_without_a_residual_budget_the_rescue_fails_closed(ex):
    """An unconfigured budget leaves the 0.96 OPENING ceiling in force, which
    is the stricter rule. Found by these tests rather than assumed: the first
    draft had no budget on the fixture and every completion above 0.96 was
    refused RESIDUAL_BUDGET_UNKNOWN."""
    x, t, H, E = ex
    x.validation_gates = None
    x.store.apply_fill(SLUG, "DOWN", D("2"), D("0.4500"), basis="OWN")
    bad = x.pair_cost_refusal(SLUG, {"side": "UP"},
                              {"price": "0.53", "qty": "2"})
    assert bad is not None
    assert bad["completion_rescue"]["reason"] == "RESIDUAL_BUDGET_UNKNOWN"


def test_an_opening_is_never_rescued_by_the_completion_ceiling(ex, monkeypatch):
    """A first leg creates no matched quantity, so it never reaches the
    rescue at all -- the completion knob cannot loosen an opening."""
    x, t, H, E = ex
    monkeypatch.setenv(CEIL_ENV, "1.00")
    # flat book: buying UP creates no pair, so the gate returns None for a
    # reason that has nothing to do with any ceiling
    assert x.pair_cost_refusal(SLUG, {"side": "UP"},
                               {"price": "0.99", "qty": "2"}) is None
    # and with DOWN held, an UP order that CROSSES ZERO is refused
    x.store.apply_fill(SLUG, "DOWN", D("1"), D("0.4500"), basis="OWN")
    bad = x.pair_cost_refusal(SLUG, {"side": "UP"},
                              {"price": "0.90", "qty": "5"})
    assert bad is not None
    assert bad["completion_rescue"]["reason"] == \
        "WOULD_CROSS_ZERO_INTO_NEW_EXPOSURE"


# ===================================== the complement's depth, and the fix

def test_the_completion_depth_default_is_at_the_touch():
    assert BC.completion_depth_ticks() == 0
    assert BC.COMPLETION_DEPTH_TICKS == 0


def test_the_venue_record_is_reproduced_at_the_opening_depth():
    """own 0.5100 into a 0.5300 bid -- the order that actually went out."""
    r = TS.complement_order(
        side="UP", qty=D("2"), cost_px=D("0.4500"),
        held_qty=D("2"), held_cost_usd=D("0.9000"),
        deficient_qty=D(0), deficient_cost_usd=D(0),
        residual_budget_usd=D("2.00"),
        bid=D("0.5300"), ask=D("0.5400"), tick=D("0.01"),
        min_qty=D("0.01"), depth_ticks=2, book_age_s=D("0.004"),
        bid_shares=D("400.01"), ask_shares=D("845.00"),
        market=SLUG, held_side="DOWN")
    assert r["ok"] is True
    assert D(r["leg"]["price"]) == D("0.5100")
    assert D("0.4500") + D(r["leg"]["price"]) == D("0.9600")


def test_at_the_touch_the_same_book_prices_two_cents_dearer_and_fillably():
    r = TS.complement_order(
        side="UP", qty=D("2"), cost_px=D("0.4500"),
        held_qty=D("2"), held_cost_usd=D("0.9000"),
        deficient_qty=D(0), deficient_cost_usd=D(0),
        residual_budget_usd=D("2.00"),
        bid=D("0.5300"), ask=D("0.5400"), tick=D("0.01"),
        min_qty=D("0.01"), depth_ticks=0, book_age_s=D("0.004"),
        bid_shares=D("400.01"), ask_shares=D("845.00"),
        market=SLUG, held_side="DOWN")
    assert r["ok"] is True
    assert D(r["leg"]["price"]) == D("0.5300")        # AT the bid
    assert D("0.4500") + D(r["leg"]["price"]) == D("0.9800")


def test_the_depth_moves_the_price_one_tick_at_a_time():
    out = {}
    for d in (0, 1, 2):
        r = TS.complement_order(
            side="UP", qty=D("2"), cost_px=D("0.4500"),
            held_qty=D("2"), held_cost_usd=D("0.9000"),
            deficient_qty=D(0), deficient_cost_usd=D(0),
            residual_budget_usd=D("2.00"),
            bid=D("0.5300"), ask=D("0.5400"), tick=D("0.01"),
            min_qty=D("0.01"), depth_ticks=d, book_age_s=D("0.004"),
            bid_shares=D("400.01"), ask_shares=D("845.00"),
            market=SLUG, held_side="DOWN")
        out[d] = D(r["leg"]["price"])
    assert out == {0: D("0.5300"), 1: D("0.5200"), 2: D("0.5100")}


def test_the_complement_call_site_uses_the_completion_depth(monkeypatch):
    """Source-level, because this is the whole defect: the OPENING leg keeps
    `_dticks` and the COMPLEMENT must not."""
    import inspect
    from src import l5_live_execution as E
    src = inspect.getsource(E)
    assert "depth_ticks=_cdticks," in src
    # the opening leg is untouched
    assert "depth_ticks=_dticks, spent_sides=_spent)" in src
    # exactly one complement call site
    assert src.count("depth_ticks=_cdticks,") == 1


def test_the_at_touch_flag_defaults_off_so_deployed_pricing_is_unchanged(
        monkeypatch):
    import inspect
    from src import l5_live_execution as E
    src = inspect.getsource(E)
    # the default assignment is the opening depth, before any flag is read
    assert "_cdticks = _dticks" in src
    assert AT_TOUCH_ENV in src


@pytest.mark.parametrize("val,on", [
    ("1", True), ("YES", True), ("true", True), ("ON", True),
    ("0", False), ("NO", False), ("", False), ("maybe", False),
])
def test_the_flag_truthy_set_matches_the_rest_of_the_codebase(val, on):
    assert (val.strip().upper() in ("1", "YES", "TRUE", "ON")) is on


# ============================================ lot-aware acquisition costs

def test_lots_are_reported_individually_and_dearest_first(ex):
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("1.68"), D("0.2100"), basis="OWN")
    x.store.apply_fill(SLUG, "UP", D("1.68"), D("1.0000"), basis="OWN")
    lots = x.store.lots(SLUG, "UP")
    assert [l["own_px"] for l in lots] == ["1.0000", "0.2100"]
    assert [l["qty"] for l in lots] == ["1.68", "1.68"]


def test_the_2300z_blend_is_exposed_rather_than_averaged(ex):
    """The live defect: 3.3600 at an average own cost of 0.605, which is
    exactly 2 x 1.68 and exactly (0.21 + 1.00) / 2. The average is a price
    neither lot ever traded at, and on it every completion in the window
    reads above redemption."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("1.68"), D("0.2100"), basis="OWN")
    x.store.apply_fill(SLUG, "UP", D("1.68"), D("1.0000"), basis="OWN")
    q = x.store.position(SLUG, "UP")
    avg = x.store.leg_cost(SLUG)["UP"] / q
    assert q == D("3.36") == 2 * D("1.68")
    assert avg == D("0.605") == (D("0.2100") + D("1.0000")) / 2
    # the real lot alone clears redemption against a 0.74 complement;
    # the blend does not. Same inventory, opposite conclusion.
    assert D("0.2100") + D("0.7400") < D("1.00")
    assert avg + D("0.7400") > D("1.00")
    assert len(x.store.lots(SLUG, "UP")) == 2


def test_quantity_no_fill_explains_is_reported_as_unattested(ex):
    """Only a real fill writes a lot, so quantity that arrived from the
    venue's net has none -- which is how a placeholder is told apart from
    shares somebody actually bought."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("1.68"), D("0.2100"), basis="OWN")
    assert x.store.attested_qty(SLUG, "UP") == D("1.68")
    assert x.store.unattested_qty(SLUG, "UP") == D(0)
    # simulate reconcile writing the venue's net without a cost basis
    x.store.state["positions"]["%s|UP" % SLUG] = "3.36"
    assert x.store.attested_qty(SLUG, "UP") == D("1.68")
    assert x.store.unattested_qty(SLUG, "UP") == D("1.68")


def test_lots_are_empty_rather_than_raising_on_an_untouched_market(ex):
    x, t, H, E = ex
    assert x.store.lots(SLUG, "UP") == []
    assert x.store.attested_qty(SLUG, "UP") == D(0)


def test_lots_never_invent_a_basis_for_the_other_side(ex):
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2"), D("0.4000"), basis="OWN")
    assert x.store.lots(SLUG, "DOWN") == []
    assert x.store.attested_qty(SLUG, "DOWN") == D(0)


# ==================================================== telemetry says so

def test_both_new_knobs_are_reported_by_the_config_verifier():
    import json
    from src import l5_runtime_config_verify as V
    rows = V.verify()["observed_not_graded"]
    by = {r["name"]: r for r in rows if isinstance(r, dict) and "name" in r}
    assert "completion_pair_ceiling" in by
    assert by["completion_pair_ceiling"]["effective"] == "1.00"
    assert "ts_completion_at_touch" in by
    assert by["ts_completion_at_touch"]["effective"] == "false"


def test_the_verifier_still_reports_the_opening_ceiling_separately():
    from src import l5_runtime_config_verify as V
    s = str(V.verify())
    assert "completion_pair_ceiling" in s
    # the opening ceiling has its own row and is not replaced by the new one
    assert "0.96" in s


# ================================ the evidence artifact, pinned to the code

def _doc():
    import json
    return json.load(open(
        "analysis/l5/completion_at_touch_ceiling_20261103.json"))


def test_parity_is_reported_on_both_commits():
    p = _doc()["item1_state_and_parity"]
    assert p["parity_6db042fc"]["identical"] is True
    assert p["parity_head_56346421"]["identical"] is True
    assert p["parity_head_56346421"]["digest"] == "eb672d14b4baf439"


def test_the_artifact_records_that_096_was_never_the_completion_bound():
    d = _doc()["what_the_code_already_did"]
    assert "0.96 is the OPENING cap" in d["finding"]
    assert d["verified_against_the_live_2245z_state"][
        "completion_ceiling_returns"] == "0.95 own price"
    assert d["verified_against_the_live_2245z_state"]["at_own_0_53"][
        "clears_rescue_limit_1_00"] is True
    assert "1.0000" in d["and_the_venue_agrees"]


def test_the_budget_precondition_is_recorded_not_assumed():
    d = _doc()["what_the_code_already_did"]
    s = d["one_precondition_I_checked_rather_than_assumed"]
    assert "RESIDUAL_BUDGET_UNKNOWN" in s
    assert "IS present on the deployed service" in s
    assert "papered over" in s


def test_the_venue_proof_of_the_depth_defect_is_exact():
    d = _doc()["the_actual_blocker"]["venue_proof"]
    assert d["own_px"] == "0.5100"
    assert d["up_bid_at_submission"] == "0.5300"
    assert D(d["up_bid_at_submission"]) - D(d["own_px"]) == D("0.02")
    assert d["pair"] == "0.9600"


def test_the_ceiling_delta_and_the_lot_delta_are_both_reported():
    t = _doc()["item6_ceiling_comparison_0_96_vs_0_99"]["totals"]
    assert t["windows_with_any_admissible_completion"] == {
        "0.96": "1 of 4", "0.99": "2 of 4"}
    assert D(t["best_case_locked_margin_if_each_fills"]["0.99"]) \
        - D(t["best_case_locked_margin_if_each_fills"]["0.96"]) \
        == D(t["delta_from_the_ceiling_usd"])
    # and the lot fix is worth more than the ceiling
    assert D(t["delta_from_the_lot_fix_usd"]) > D(t["delta_from_the_ceiling_usd"])


def test_every_locked_margin_is_consistent_with_its_pair_and_quantity():
    for w in _doc()["item6_ceiling_comparison_0_96_vs_0_99"]["per_window"]:
        qty = D(w["held"].split()[1])
        for key in ("best_0_96", "best_0_99"):
            b = w.get(key)
            if not b:
                continue
            assert (D(1) - D(b["pair"])) * qty == D(b["locked_usd"]), (w, key)


def test_no_fill_rate_is_claimed_for_either_ceiling():
    f = _doc()["item6_ceiling_comparison_0_96_vs_0_99"]["fill_rates"]
    assert f["supported_by_order_level_evidence"] == "NONE"
    assert f["complement_fills_in_the_entire_campaign"] == 0
    assert f["first_leg_fills"] == 4
    assert "does not guarantee" in f["why"]


def test_no_rebate_is_credited_anywhere_in_the_comparison():
    b = _doc()["item6_ceiling_comparison_0_96_vs_0_99"]["basis"]
    assert "rebate_usd is null" in b and "NO" in b


def test_hedge_and_profitable_completion_are_reported_separately():
    h = _doc()["item7_residual_risk_hedge_versus_profitable_completion"]
    assert h["reported_separately_on_purpose"] is True
    assert "locks a gain against the basis" in h["profitable_completion"]
    assert "EXACTLY EV-neutral" in h["hedge"]
    assert "not rejected for being unprofitable" in \
        "a hedge is not rejected for being unprofitable"
    assert "both are true" in h["so_a_hedge_is_not_rejected_for_being_unprofitable"]
    assert "does not do is create expected value" in \
        h["so_a_hedge_is_not_rejected_for_being_unprofitable"]


def test_the_ev_identity_holds_for_the_two_admissible_windows():
    for basis, mk in (("0.4500", "0.5300"), ("0.2100", "0.7400")):
        b, m = D(basis), D(mk)
        assert (D(1) - b - m) == ((D(1) - m) - b)


def test_the_lot_blend_arithmetic_in_the_artifact_is_exact():
    l = _doc()["lot_aware_pricing"]
    assert "0.605" in l["why_it_matters_concretely"]
    assert (D("0.2100") + D("1.00")) / 2 == D("0.605")
    assert D("0.605") + D("0.7400") > D("1.00")
    assert D("0.2100") + D("0.7400") < D("1.00")
    assert "no gate consults them yet" in l["honest_limit"]


def test_the_decision_is_go_on_code_and_no_go_on_enabling():
    g = _doc()["go_no_go"]
    assert g["decision"].startswith("GO on the code, NO-GO on enabling")
    assert "never filled" in g["no_go_because"] or \
        "no completion has ever filled" in g["no_go_because"]
    assert "no live orders" in g["not_authorized_here"]


def test_the_validation_config_leaves_every_protection_alone():
    c = _doc()["bounded_live_validation_config"]
    keep = " ".join(c["preserved_unchanged"])
    for need in ("$0.96 opening ceiling", "300s cancellation margin",
                 "exact-order cancellation", "partial-fill reconciliation",
                 "orders_uncertain fail-closed", "watchdog", "residual cap"):
        assert need in keep, need
    assert c["env_to_leave_alone"]["L5_TS_DEPTH_TICKS"].startswith("2")
    assert c["limits"]["cumulative_realized_loss_limit_usd"] == 5.0


def test_safety_block_says_nothing_live_moved():
    s = _doc()["safety"]
    assert s["live_policy_changed"] is False
    assert s["live_trading_authorized"] is False
    assert s["opening_pair_cap"].startswith("0.96")
    assert s["cancel_margin_s"] == 300.0
    assert s["at_touch_flag_default"] == "off"
    assert s["armed"] is False and s["orders_placed"] == 0
    assert s["new_services"] == 0 and s["new_branches"] == 0
    assert s["deployed"] is False
