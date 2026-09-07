"""OWNER PROGRAM STATEMENT (chat 2026-09-07 17:55Z, recorded as #167
5574156328): "L5 is the strategy ... If he says COMPLETE at 1.02, our
replication target is 1.02 -- not an arbitrary 1.00 ... Any safety override
gets recorded explicitly as our deviation from L5 ... a hard
catastrophic-loss shell around the ~$111 account, but inside that shell I
want maximum L5 fidelity."

R5F = the fidelity rung: COMPLETE at L5's touch (pair reported, not gated),
no unmatched-dollar cap, model-driven rest, concurrency 2, continuous
campaign. Every other rung -- R4 included -- must read exactly as before."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont
from src import pmus_l5_ramp_ladder as ladder
from src import pmus_q1_executor as q1
from src import pmus_spring_l5_session as base


@pytest.fixture(autouse=True)
def _reset_active_rung():
    ladder.set_active(None)
    yield
    ladder.set_active(None)


def test_r5f_is_the_fidelity_rung_with_the_audit_removals_and_the_shell_kept():
    r = ladder.rung("R5F")
    assert r["id"] == "R5F" and r["lever"] == "fidelity" and r["promote"] is None
    # REMOVED by the restriction audit
    assert r["max_unmatched_usd"] is None                 # unmatched_usd_cap
    assert r["pair_cost_max"] is None                     # pair_cost_ceiling
    assert r["complement_pricing"] == "l5_touch"
    assert r["rest_policy"] == "model" and r["rest_floor_s"] == "5"   # maker_rest_30s
    # KEPT initially (OWNER 5574408755): one market at a time -- L5's own
    # one-lane distribution (max_markets_open_within_one_lane == 1)
    assert r["concurrency"] == 1
    assert r["markets_per_campaign"] is None              # cohort_3_markets
    # KEPT: the catastrophic-loss shell and the size
    assert r["per_market_cap_usd"] == "7.50" and r["residual_units"] == 6
    assert r["target_notional_usd"] == "2.50" and r["max_leg_notional_usd"] == "3.25"
    assert r["daily_loss_stop_usd"] == "-15.00" and r["max_actions_per_session"] == 6
    assert ladder.shell_version(r) == (
        "R5F/cap7.50/unmatched-none/complete-l5touch/conc1/maker-only/rest-model5s/action2.50")
    # the prebuilt "R5" (concurrency-2 at $0.10) is a different rung
    assert ladder.rung("R5")["per_market_cap_usd"] != "7.50"
    ids = [x["id"] for x in ladder.ALL_RUNGS]
    assert ids.count("R5F") == 1 and len(set(ids)) == len(ids)


def test_r5f_worst_case_is_the_shell_not_a_market_count():
    wc = ladder.worst_case(ladder.rung("R5F"))
    assert wc["cohort"] == "continuous"
    assert wc["per_market_usd"] == "7.50"
    assert wc["max_concurrent_open_exposure_usd"] == "7.50"       # 7.50 x conc 1
    assert wc["per_campaign_usd"] == "22.50"                        # x 3 turns (preflight)
    assert wc["per_day_bounded_by_daily_stop_usd"] == "22.50"      # stop + one in-flight cap
    assert wc["daily_loss_stop_usd"] == "-15.00"
    assert wc["max_at_risk_per_market_usd"] == "7.50"              # no unmatched cap: the spend cap
    assert wc["pair_cost_max"] is None and wc["complement_pricing"] == "l5_touch"
    assert wc["per_action_target_usd"] == "2.50" and wc["per_leg_max_usd"] == "3.25"
    assert "daily stop" in wc["per_day_uncapped_usd"]


def test_every_other_rung_reads_exactly_as_before():
    for rid in ("R1", "R2", "R3", "R4-AUTO", "R5", "R2C", "R2C2", "R3L", "R4"):
        r = ladder.rung(rid)
        assert ladder.complement_pricing(r) == "fee_safe"
        assert ladder.rest_policy(r) == "fixed30"
        assert ladder.markets_per_campaign(r) == r["markets_per_campaign"] >= 1
        assert ladder.max_sessions_per_campaign(r) == 48
        assert "l5touch" not in ladder.shell_version(r)
        assert "cohort" not in ladder.worst_case(r)
    r4 = ladder.rung("R4")
    assert ladder.pair_cost_max(r4) == D("1.00") and ladder.max_unmatched_usd(r4) == D("2.50")
    assert ladder.worst_case(r4)["per_campaign_usd"] == "22.50"
    assert ladder.worst_case(r4)["per_campaign_at_risk_usd"] == "7.50"
    assert ladder.shell_version(r4) == (
        "R4/cap7.50/unmatched2.50/complete1.00/conc1/maker-only/rest30s/action2.50")


def test_par_stays_a_ceiling_for_pair_cost_max_even_though_r5f_does_not_use_it():
    """pair_cost_max() is the R1..R4 guardrail and still never reads above
    1.00; R5F simply does not price a COMPLETE through it."""
    assert ladder.pair_cost_max(ladder.rung("R5F")) == D("0.98")
    ladder.set_active("R5F")
    assert base.pair_cost_max() == D("0.98") and base.complement_pricing() == "l5_touch"
    assert base.max_unmatched_usd() is None


def test_complete_is_priced_at_l5_touch_above_par_included_under_r5f():
    """The owner's example: L5 completes at 1.02 -> our target is 1.02."""
    tick = D("0.01")
    ladder.set_active("R5F")
    # surplus YES at 0.38 average; the deficit-side (NO) bid is 0.64, ask 0.66
    px, econ = base.spring_complement_price(D("0.38"), D("0.66"), tick, touch=D("0.64"))
    assert px == D("0.64") and econ["regime"] == "l5_touch"
    assert econ["pair_at_chosen"] == "1.02" and econ["above_par"] is True
    assert econ["fee_safe"] is False and econ["pair_cost_max"] is None
    assert econ["post_only_trim"] is False and econ["chosen_px"] == "0.64"
    # the identical call under R4 is capped at par (0.62) as before
    ladder.set_active("R4")
    px4, econ4 = base.spring_complement_price(D("0.38"), D("0.66"), tick, touch=D("0.64"))
    assert px4 == D("0.62") and econ4["pair_cost_max"] == "1.00"
    # and under R3L at the fee-safe 0.98 bound (0.60) as before
    ladder.set_active("R3L")
    px3, econ3 = base.spring_complement_price(D("0.38"), D("0.66"), tick, touch=D("0.64"))
    assert px3 == D("0.60") and econ3["regime"] == "spring_target"


def test_r5f_touch_pricing_keeps_only_the_venue_bounds():
    tick = D("0.01")
    ladder.set_active("R5F")
    # a touch AT the opposite ask would cross: post-only trims it one tick
    # and says so (an execution adaptation, not a safety override)
    px, econ = base.spring_complement_price(D("0.38"), D("0.64"), tick, touch=D("0.64"))
    assert px == D("0.63") and econ["post_only_trim"] is True
    assert econ["l5_touch_px"] == "0.64" and econ["pair_at_touch"] == "1.02"
    # a touch below the fee-safe bound is simply fee-safe and still the touch
    px, econ = base.spring_complement_price(D("0.38"), D("0.60"), tick, touch=D("0.50"))
    assert px == D("0.50") and econ["fee_safe"] is True and econ["above_par"] is False
    # the venue's legal range is the only remaining refusal
    px, econ = base.spring_complement_price(D("0.05"), D("0.995"), tick, touch=D("0.99"))
    assert px is None and econ["regime"] == "no_legal_maker_price"
    # without a touch (an OPEN/EXTEND caller) the fee-safe pricer still runs
    px, econ = base.spring_complement_price(D("0.38"), D("0.66"), tick)
    assert px == D("0.60") and econ["regime"] == "spring_target"


def test_maker_rest_follows_the_model_under_r5f_and_stays_30s_elsewhere():
    ladder.set_active("R5F")
    assert base.maker_rest_max_s() == 5.0 and base.rest_policy() == "model"
    for rid in ("R1", "R3L", "R4"):
        ladder.set_active(rid)
        assert base.maker_rest_max_s() == base.SPORTS_MAKER_REST_MAX_S == 30.0
        assert base.rest_policy() == "fixed30"


def test_continuous_campaign_never_spends_a_market_allowance(monkeypatch):
    """rung_markets None: the campaign is bounded by the daily stop and
    the horizon, never by a market count; the env override can also say
    'continuous' explicitly, and a number still means a number."""
    monkeypatch.delenv(cont.RUNG_MARKETS_ENV, raising=False)
    assert ladder.markets_per_campaign(ladder.rung("R5F")) is None
    assert ladder.markets_per_campaign(ladder.rung("R4")) == 3
    assert ladder.max_sessions_per_campaign(ladder.rung("R5F")) == 10080
    assert ladder.max_sessions_per_campaign(ladder.rung("R4"), default=48) == 48


def test_sized_preflight_for_r5f_requires_the_continuous_shell(monkeypatch):
    """The fail-closed buying-power check asks for the R5F deployed worst
    case ($22.50 = $7.50 x conc 1 x 3 turns); $111 passes, $20 fails."""
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
    r5 = ladder.rung("R5F")
    pre = cont.continuation_preflight(_t(power="111.14"), {"m"}, {"m"}, rung=r5)
    assert pre["ok"]
    assert pre["checks"]["buying_power"]["required_per_campaign_worst_case_usd"] == "22.50"
    assert pre["checks"]["rung"]["worst_case"]["cohort"] == "continuous"
    assert cont.continuation_preflight(_t(power="20"), {"m"}, {"m"}, rung=r5)["failed"] \
        == ["buying_power"]


def test_r5f_is_not_approved_until_the_owner_names_the_run():
    """No campaign approval row for R5F exists yet: the rung is built, the
    approval is the owner's (program statement 'Then run it' at
    settlement / rung-id confirmation), never written by the code."""
    rows = cont.load_campaign_approvals()
    assert all(a.get("rung") != "R5F" for a in rows)
    assert cont.approved_continuation("R4-RUN-2026-09-07",
                                      [a["campaign_id"] for a in rows]) is None
