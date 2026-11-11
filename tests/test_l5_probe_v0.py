"""ECONOMIC_MEASUREMENT_PROBE_V0: bounds, venue reality, and what it is not."""
import os

import pytest

from src.l5_live_probe import envelope as E
from src.l5_live_probe import preflight as P
from src.l5_live_probe import probe_v0 as V
from src.l5_live_probe import venue_access as VA
from src.l5_risk import shell as RISK


# ------------------------------------------------- it is not a strategy

def test_v0_is_labelled_as_measurement_and_not_a_qualified_strategy():
    assert "NOT_A_QUALIFIED_STRATEGY" in V.STATUS
    assert "FAIL" in V.LINEAGE


def test_v0_removes_extension_rather_than_capping_it():
    """cap5 capped same-side extension at five and FAILED. V0 permits one fill
    per side, so residual is bounded by construction, not by a threshold."""
    assert V.MAX_FILLS_PER_SIDE == 1
    assert V.MAX_RESIDUAL_QTY == V.ORDER_QTY
    assert V.MAX_INVENTORY_SHARES == V.ORDER_QTY * 2


def test_the_order_size_is_exactly_the_venue_minimum():
    assert V.ORDER_QTY == V.VENUE_ORDER_MIN


# ------------------------------------------------- the loss bound

def test_every_reachable_state_is_enumerated_not_reasoned_about():
    states = V.reachable_states()
    assert len(states) == (V.MAX_FILLS_PER_SIDE + 1) ** 2
    assert (V.ORDER_QTY, V.ORDER_QTY,
            V.ORDER_QTY * 2 * V.VENUE_MAX_PRICE) in states


def test_the_worst_state_is_one_unmatched_leg_not_the_pair():
    """A completed pair settles to exactly $1 a unit, so it is SAFER than a
    single leg. If this ever inverts, the envelope arithmetic is wrong."""
    m = V.max_loss_per_market()
    w = m["worst_state"]
    assert {w["up"], w["dn"]} == {"5", "0"}
    assert m["max_loss_usd"] == "4.95"


def test_max_loss_agrees_with_the_shared_risk_shell():
    """Computed here against l5_risk.shell directly, so the probe cannot hold a
    second opinion about the one number that must mean the same everywhere."""
    worst = min(RISK.guaranteed_floor(u, d, s) for u, d, s in V.reachable_states())
    assert str(-worst) == V.max_loss_per_market()["max_loss_usd"]


def test_total_loss_is_the_stop_plus_one_market():
    t = V.max_total_loss()
    assert t["max_total_loss_usd"] == "14.95"
    assert t["max_open_cash_at_risk_usd"] == "9.90"


def test_the_envelope_the_probe_proposes_builds_a_valid_shell(monkeypatch):
    for k, v in V.envelope_env().items():
        monkeypatch.setenv(k, v)
    sh = E.shell()
    assert sh.max_concurrent_markets == 1
    assert sh.daily_loss_stop == "-10.00"


# ------------------------------------------------- venue reality

def test_a_market_read_from_international_fails_against_us_credentials():
    """The defect this closes: MARKET_ELIGIBILITY and CREDENTIALS_PRESENT were
    both passing while true about DIFFERENT venues."""
    c = VA.consistency(
        {"terms_source": "https://gamma-api.polymarket.com/markets?slug=x"},
        None)
    assert c["ok"] is False
    assert c["market_venue"] == "POLYMARKET_INTL"
    assert c["credentialed_venue"] == "POLYMARKET_US"


def test_the_venue_label_on_the_row_is_not_trusted():
    """l5_shadow rows say venue='PMUS' for international markets, so
    attribution is by SOURCE URL."""
    assert VA.venue_of("https://clob.polymarket.com/markets/x") == "POLYMARKET_INTL"
    assert VA.venue_of("https://gateway.polymarket.us/v1/markets") == "POLYMARKET_US"
    assert VA.venue_of(None) is None


def test_an_empty_enumeration_is_undetermined_never_absence():
    """0 markets because the gateway was unreachable must not read as 'the
    market is not listed'."""
    r = VA.tradeable_on_credentialed_venue("updown", {"markets": [], "pages": 0})
    assert r["state"] == "UNDETERMINED"


def test_a_bounded_enumeration_reports_its_bound_beside_the_count():
    r = VA.tradeable_on_credentialed_venue(
        "updown", {"markets": [{"slug": "tec-mlb-x"}], "pages": 60,
                   "exhausted": False})
    assert r["state"] == "NOT_ON_CREDENTIALED_VENUE"
    assert r["exhausted"] is False and r["pages"] == 60


def test_venue_access_is_a_gate_in_the_verdict():
    v = P.verdict(market={"terms_source": "https://clob.polymarket.com/x"})
    assert any(c["check"] == "VENUE_ACCESS" and c["state"] == "FAIL"
               for c in v["checks"])


# ------------------------------------------------- credentials

def test_the_preflight_checks_the_names_the_client_actually_reads():
    """PMUS_API_KEY and friends were invented; nothing reads them, so the check
    would have been asking about variables that do not exist."""
    src = open("src/polymarket_probe.py").read()
    for name in P.CREDENTIAL_ENV:
        assert name in src, f"{name} is not read by the client"


# ------------------------------------------------- the risk-shell call

def test_the_intent_path_calls_the_method_the_shell_actually_has(monkeypatch):
    """`allows()` does not exist on RiskShell -- the method is `check()`. With
    intent=None this branch never ran, so the wrong name sat latent."""
    for k, v in V.envelope_env().items():
        monkeypatch.setenv(k, v)

    class _I:
        incremental_qty = V.ORDER_QTY
        max_acceptable_price = str(V.VENUE_MAX_PRICE)

    gates = P.risk_bounds(realized="0", floors=[], current_inventory="0",
                          market_spent="0", open_markets=0,
                          last_heartbeat_age_s=1.0, intent=_I())
    res = [g for g in gates if g["check"] == "RESIDUAL_EXPOSURE_LIMIT"][0]
    assert res["state"] == "PASS", res["blocker"]


def test_an_intent_over_the_inventory_cap_is_refused(monkeypatch):
    for k, v in V.envelope_env().items():
        monkeypatch.setenv(k, v)

    class _Big:
        incremental_qty = 500
        max_acceptable_price = "0.99"

    gates = P.risk_bounds(realized="0", floors=[], current_inventory="0",
                          market_spent="0", open_markets=0,
                          last_heartbeat_age_s=1.0, intent=_Big())
    res = [g for g in gates if g["check"] == "RESIDUAL_EXPOSURE_LIMIT"][0]
    assert res["state"] == "FAIL" and "INVENTORY CAP" in res["blocker"]


# ------------------------------------------------- rewards

def test_the_expected_rebate_is_the_venue_formula_not_a_forecast():
    # 0.2 * (0.07 * min(p,1-p) * qty); at p=0.50, qty=5 -> 0.2*0.175 = 0.035
    from decimal import Decimal as D
    assert V.expected_rebate_usd("0.50") == D("0.0350")
    # near the extremes a 5-lot rounds to well under a cent
    assert float(V.expected_rebate_usd("0.02")) < 0.01


def test_v0_does_not_claim_rewards_are_measurable():
    d = V.describe()
    assert d["rewards"]["measurable"] is False
    assert d["rewards"]["min_size_is_binding"] is False
