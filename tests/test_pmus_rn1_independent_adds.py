from decimal import Decimal as D

from src import pmus_rn1_replica as rn1


def quote(**overrides):
    cfg = dict(qty=2, depth_ticks=1, px_min="0.20", px_max="0.92",
               pair_cap="0.97", residual_cap_usd="3", per_market_usd="8",
               extend_max_up="0.02", refill_cooldown_s=10,
               edge_gate=True, fair_yes="0.65", adverse_ticks=1,
               risk_step_ticks=1, independent_adds=True)
    cfg.update(overrides)
    return rn1.quotes(yes_bid="0.55", yes_ask="0.57", tick="0.01",
                      pos={"YES": 2, "NO": 0},
                      cost={"YES": "0.80", "NO": 0}, now=100, c=cfg)


def test_independent_add_prices_at_touch_depth():
    assert quote()["YES"]["px"] == D("0.54")
    assert quote(independent_adds=False)["YES"]["px"] == D("0.42")


def test_independent_add_requires_edge_gate():
    assert quote(edge_gate=False, extend_ev=False)["YES"]["px"] == D("0.42")


def test_independent_add_rejects_bad_edge():
    assert quote(fair_yes="0.54")["YES"] is None


def test_independent_add_preserves_residual_and_spend_caps():
    assert quote(residual_cap_usd="1")["YES"] is None
    assert quote(per_market_usd="2")["YES"] is None
