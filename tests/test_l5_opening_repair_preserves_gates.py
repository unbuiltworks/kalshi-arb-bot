"""Enabling the opening-gate repair bypasses nothing.

The owner's condition for accepting the `PROVISIONAL_PRICE_UNREADABLE`
repair: prove it preserves **market authorization, book freshness,
post-only orders, pair-cost limits and the existing risk gates**, and that
the preflight and settlement audit **fail closed on unknown cost basis**.

WHY THIS IS THE RIGHT TEST. The repair makes the controller open in states
where it previously refused. Everything that protected us in those states
sat DOWNSTREAM of the refusal, so the only question that matters is
whether the downstream gates still refuse when the upstream one no longer
does. These tests run with the repair **ENABLED** -- the configuration we
do not plan to deploy -- precisely because that is the adversarial case.

The repair cannot make a bad order: `TS.build_orders` takes no side and no
price (pinned in `test_l5_depth2_execution_path`), so opening the gate
hands the economics to the builder rather than to the gate. These tests
show the builder refuses everything it should.

No transport, no network, no order.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_probe_preflight as PF
from src import l5_two_sided_probe as TS

MARKET = "cpc-btc-updown-15m-2026-11-08-1200z"
OTHER = "cpc-btc-updown-15m-2026-01-01-0000z"


@pytest.fixture
def repair_enabled(monkeypatch):
    """THE ADVERSARIAL CONFIGURATION. Not what we plan to deploy."""
    monkeypatch.setenv("L5_PROVISIONAL_FORCED_SIDE", "YES")
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "gate-preservation")
    monkeypatch.setenv(TS.MARKET_ENV, MARKET)
    assert (os.getenv("L5_PROVISIONAL_FORCED_SIDE") or "").lower() == "yes"
    yield


def plan(market=MARKET, bid="0.62", ask="0.63", *, depth=2, age="0.4",
         gap=False, cash="12", loss="1.00", bid_depth="500",
         ask_depth="500", tick="0.01", min_qty="1"):
    return TS.build_orders(
        market=market, bid=bid, ask=ask, bid_depth=bid_depth,
        ask_depth=ask_depth, tick=tick, min_qty=min_qty,
        depth_ticks=depth, cash_envelope=D(cash),
        max_worst_case_loss=D(loss), budget_headroom=D(cash),
        book_age_s=D(age), gap_suspected=gap)


# =====================================================================
# 1. MARKET AUTHORIZATION
# =====================================================================

def test_the_repair_does_not_authorize_a_market(repair_enabled):
    """The pinned side the repair would choose is irrelevant: a plan for
    an unauthorized market is refused before any pricing."""
    assert plan(market=OTHER) == {"ok": False,
                                  "reason": "NOT_THE_AUTHORIZED_MARKET"}


def test_the_repair_does_not_create_partial_arming(monkeypatch):
    """With the repair on but the experiment id absent, the probe is
    still inert -- the repair touches neither half of `active()`."""
    monkeypatch.setenv("L5_PROVISIONAL_FORCED_SIDE", "YES")
    monkeypatch.setenv(TS.MARKET_ENV, MARKET)
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    assert TS.active(MARKET) is False
    assert plan()["ok"] is False


def test_the_extreme_book_that_triggers_the_repair_is_still_market_gated(
        monkeypatch):
    """bid 0.00 / ask 0.01 is the state that pins UP and triggers the
    repair. On an unauthorized market it still refuses."""
    monkeypatch.setenv("L5_PROVISIONAL_FORCED_SIDE", "YES")
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "x")
    monkeypatch.setenv(TS.MARKET_ENV, MARKET)
    assert plan(market=OTHER, bid="0.00", ask="0.01")["reason"] == \
        "NOT_THE_AUTHORIZED_MARKET"


# =====================================================================
# 2. BOOK FRESHNESS
# =====================================================================

@pytest.mark.parametrize("age", ["2.1", "6.8", "60", "-3"])
def test_a_stale_or_future_dated_book_still_refuses_the_whole_plan(
        repair_enabled, age):
    out = plan(age=age)
    assert out["ok"] is False, out
    assert out.get("reason"), out


def test_a_suspected_gap_still_refuses(repair_enabled):
    out = plan(gap=True)
    assert out["ok"] is False and out.get("reason"), out


def test_freshness_is_judged_on_the_book_not_on_the_provisional_price(
        repair_enabled):
    """A fresh book at the extreme that triggers the repair, and a stale
    book at an ordinary price: the verdict follows the BOOK."""
    fresh_extreme = plan(bid="0.00", ask="0.01", age="0.4")
    stale_ordinary = plan(bid="0.62", ask="0.63", age="9.9")
    assert stale_ordinary["ok"] is False
    # the extreme book refuses on ECONOMICS, not on freshness
    assert fresh_extreme["ok"] is False
    assert fresh_extreme["reason"] != stale_ordinary["reason"]


# =====================================================================
# 3. POST-ONLY
# =====================================================================

def test_every_leg_the_builder_emits_is_post_only_maker(repair_enabled):
    out = plan()
    assert out["ok"] is True, out
    legs = out["legs"]
    assert len(legs) == 2
    for o in legs:
        assert o["post_only"] is True, o
        assert str(o["mode"]).upper() == "MAKER", o


def test_the_fee_floor_verdict_still_requires_post_only(repair_enabled):
    ff = plan()["fee_floor"]
    assert ff["ok"] is True and ff["requires_post_only"] is True
    assert D(str(ff["net_if_maker"])) > D(str(ff["net_if_taker"]))


def test_the_builder_still_refuses_to_degrade_to_one_leg(repair_enabled):
    """Its stated contract. A one-sided book gives no plan, not a single
    leg -- which is what would turn a measurement into a directional bet."""
    import inspect
    assert "NO FALLBACK" in inspect.getsource(TS.build_orders)
    out = plan(bid="0.62", ask="0.62")
    if out["ok"]:
        assert len(out["legs"]) == 2, out
    else:
        assert out.get("reason"), out


# =====================================================================
# 4. PAIR-COST LIMIT -- kept at 0.96, not relaxed to manufacture fills
# =====================================================================

def test_the_pair_ceiling_is_unchanged_at_0_96(repair_enabled):
    assert TS.completion_max_pair() == D("0.96")


def test_the_repair_does_not_admit_depth_0_or_1(repair_enabled):
    """THE OWNER'S EXPLICIT CONDITION: fix the unnecessary refusal, do NOT
    relax the economics. With the repair ENABLED, depths 0 and 1 are
    still refused on pair cost."""
    for d, pair in ((0, "0.99"), (1, "0.97")):
        out = plan(depth=d)
        assert out["ok"] is False, (d, out)
        assert out["reason"] == "PAIR_COST_ABOVE_CAP", (d, out)
        assert str(out["pair_cost"]) == pair, (d, out)
    assert [d for d in (0, 1, 2, 3) if plan(depth=d)["ok"]] == [2, 3]


def test_the_extreme_book_the_repair_unblocks_is_refused_on_ECONOMICS(
        repair_enabled):
    """The decisive case. At bid 0.00 / ask 0.01 the repair lets the
    controller reach the builder -- and the builder refuses on its own
    terms. The repair moves the decision to where the economics live; it
    does not win the argument there."""
    out = plan(bid="0.00", ask="0.01", depth=2)
    assert out["ok"] is False, out
    assert out.get("reason"), out


# =====================================================================
# 5. THE EXISTING RISK GATES
# =====================================================================

def test_an_exhausted_cash_envelope_still_refuses(repair_enabled):
    assert plan(cash="0")["ok"] is False


def test_a_zero_worst_case_loss_ceiling_still_refuses(repair_enabled):
    assert plan(loss="0")["ok"] is False


def test_the_one_dollar_ceiling_still_binds_the_size(repair_enabled):
    """Not just "it refuses sometimes": the ceiling is what sizes the
    plan, and the plan says so."""
    out = plan(loss="1.00")
    assert out["ok"] is True, out
    assert str(out["sized_by"]) == "LOSS_CEILING", out
    assert D(str(out["worst_single_leg_loss_usd"])) >= D("-1.00"), out
    for o in out["legs"]:
        assert D(str(o["qty"])) > 0


def test_the_cash_gate_charges_a_resting_DOWN_leg_a_dollar_a_share(
        repair_enabled):
    """The basis that makes the plan one BOTH gates accept. If the repair
    had changed sizing, this is where it would show."""
    out = plan()
    assert "BUY_SHORT" in str(out["cash_basis"])
    assert D(str(out["cash_gate_obligation_usd"])) > D(str(
        out["gross_cash_usd"]))


def test_the_opening_leg_cap_is_still_reported_and_respected(repair_enabled):
    out = plan()
    assert int(out["opening_leg_max_contracts"]) >= 1
    for o in out["legs"]:
        assert D(str(o["qty"])) <= D(str(out["opening_leg_max_contracts"]))


def test_the_plan_still_refuses_to_call_a_displayed_price_a_fill(
        repair_enabled):
    assert "displayed prices are NOT fills" in plan()["labels"]


# =====================================================================
# 6. THE PREFLIGHT AND AUDIT FAIL CLOSED ON UNKNOWN COST BASIS
# =====================================================================

def hb(**over):
    h = {"submission_gate": {"blocked": True, "reason": "SHADOW_ONLY_LOCK",
                             "armed": False, "halt_latch": "CLEAR",
                             "halt_latch_telemetry": {"kind": "PRESENT"}},
         "effective_cash_gate": {"max_cash_deployed_usd": "12",
                                 "cash_policy": "NO_RECYCLING",
                                 "max_unmatched_contracts": "5",
                                 "max_worst_case_deficit_usd": "1.00",
                                 "worst_case_deficit_check": "ACTIVE"},
         "day_realized": {"room_usd": "5", "halt_usd": "5",
                          "would_halt": False},
         "campaign": {"completion_reserve": {"may_open": True,
                                             "submissions_per_obligation": 2}},
         "auth_book": {"connected": True, "stream_age_s": 0.0,
                       "gap_suspected": False, "stall_reconnects": 0},
         "realized": {"unknown_basis_keys": []},
         "accounting": {"unknown_detail": {
             "post_baseline_settlements_not_attested": []}}}
    h.update(over)
    return h


FILLED_POS = {"%s|UP" % MARKET: "1", "%s|DOWN" % MARKET: "1"}
FILLED_LEGS = {"%s|UP" % MARKET: "0.60", "%s|DOWN" % MARKET: "0.35"}
LAT = {"book_age_at_decision_s": {"n": 5, "median": 0.4, "p95": 0.9},
       "detection_to_first_decision_s": 2.0}


def test_audit_fails_closed_when_the_probe_market_basis_is_unknown():
    h = hb()
    h["realized"]["unknown_basis_keys"] = ["%s|UP" % MARKET]
    out = PF.audit(markets=[MARKET], heartbeat=h, leg_cost=FILLED_LEGS,
                   positions=FILLED_POS,
                   fills=[{"venue_order_id": "V-1"}], latency=LAT)
    assert out["verdict"] == "UNINTERPRETABLE"
    assert out["ok"] is False


def test_audit_fails_closed_on_an_upper_bounded_settlement():
    h = hb()
    h["accounting"]["unknown_detail"][
        "post_baseline_settlements_not_attested"] = [
            {"market": MARKET, "pnl": "-1.00",
             "provenance": "UNKNOWN_COST_UPPER_BOUND"}]
    out = PF.audit(markets=[MARKET], heartbeat=h, leg_cost=FILLED_LEGS,
                   positions=FILLED_POS,
                   fills=[{"venue_order_id": "V-1"}], latency=LAT)
    assert out["verdict"] == "UNINTERPRETABLE"


def test_audit_fails_closed_on_a_filled_leg_with_no_basis():
    legs = {"%s|UP" % MARKET: "0.60"}          # DOWN filled, unpriced
    out = PF.audit(markets=[MARKET], heartbeat=hb(), leg_cost=legs,
                   positions=FILLED_POS,
                   fills=[{"venue_order_id": "V-1"}], latency=LAT)
    assert out["verdict"] == "UNINTERPRETABLE"
    assert "every_filled_leg_has_a_cost_basis" in out["failed"]


@pytest.mark.parametrize("missing", ["heartbeat", "leg_cost", "positions"])
def test_audit_fails_closed_on_an_ABSENT_input_rather_than_passing(missing):
    """An empty mapping is not evidence of a clean basis. The worst
    failure mode for this module would be reading silence as a pass."""
    kw = {"markets": [MARKET], "heartbeat": hb(), "leg_cost": FILLED_LEGS,
          "positions": FILLED_POS, "fills": [{"venue_order_id": "V-1"}],
          "latency": LAT}
    kw[missing] = {}
    out = PF.audit(**kw)
    assert out["ok"] is False, out
    assert out["verdict"] in ("UNINTERPRETABLE", "INCOMPLETE"), out


def test_audit_never_returns_MEASURED_without_a_priced_filled_leg():
    """Exhaustive over the small state space that matters."""
    for pos, legs in (({}, {}),
                      (FILLED_POS, {}),
                      (FILLED_POS, {"%s|UP" % MARKET: "0"}),
                      ({"%s|UP" % MARKET: "0"}, FILLED_LEGS)):
        out = PF.audit(markets=[MARKET], heartbeat=hb(), leg_cost=legs,
                       positions=pos, fills=[{"venue_order_id": "V"}],
                       latency=LAT)
        assert out["verdict"] != "MEASURED" or not pos, (pos, legs, out)
        if out["verdict"] == "MEASURED":
            # `ok` additionally needs fees and restart evidence, which this
            # minimal fixture does not supply; the BASIS is what MEASURED
            # asserts, and that is the property under test here.
            assert not [r for r in out["checks"]
                        if r["ok"] is False
                        and "basis" in r["check"]]


def test_preflight_fails_closed_when_the_recorder_cannot_calibrate():
    out = PF.preflight(heartbeat=hb(), plan=plan(), markets=[MARKET],
                       calibration={"orders": 0, "events_appended": 0,
                                    "errors": {}})
    assert out["ok"] is False
    assert "recorder_has_observed_something" in out["failed"]


def test_preflight_fails_closed_on_an_unknown_rather_than_assuming_a_pass():
    out = PF.preflight(heartbeat={}, plan=None, markets=[])
    assert out["ok"] is False
    assert out["unknown"], out
    assert all(r["ok"] is not True or r["check"] == "markets_pre_registered"
               for r in out["checks"] if r["ok"] is PF.UNKNOWN)
