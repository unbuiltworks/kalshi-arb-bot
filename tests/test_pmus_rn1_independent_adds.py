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


# -------------------------------------------------- integration (Claude)
def test_default_is_off_and_matches_before(monkeypatch):
    """No setting, no config key: the average-cost ceiling applies, exactly
    as before the change."""
    monkeypatch.delenv("PMUS_RN1_INDEPENDENT_ADDS", raising=False)
    q = rn1.quotes(yes_bid="0.55", yes_ask="0.57", tick="0.01",
                   pos={"YES": 2, "NO": 0}, cost={"YES": "0.80", "NO": 0},
                   now=100, c=dict(qty=2, residual_cap_usd="3",
                                   per_market_usd="8"))
    assert q["YES"]["px"] == D("0.42")


def test_the_environment_setting_reaches_the_quote(monkeypatch):
    """The engine passes no `independent_adds` key: the setting is read from
    the environment at quote time."""
    monkeypatch.setenv("PMUS_RN1_INDEPENDENT_ADDS", "1")
    cfg = dict(qty=2, residual_cap_usd="3", per_market_usd="8",
               edge_gate=True, fair_yes="0.65", adverse_ticks=1,
               risk_step_ticks=1)
    q = rn1.quotes(yes_bid="0.55", yes_ask="0.57", tick="0.01",
                   pos={"YES": 2, "NO": 0}, cost={"YES": "0.80", "NO": 0},
                   now=100, c=cfg)
    assert q["YES"]["px"] == D("0.54")


def test_inert_under_the_venue_native_profile(monkeypatch):
    """The live campaign runs with the edge gate OFF (no reference price
    required). There the setting changes nothing: the ceiling stays."""
    monkeypatch.setenv("PMUS_RN1_INDEPENDENT_ADDS", "1")
    q = rn1.quotes(yes_bid="0.55", yes_ask="0.57", tick="0.01",
                   pos={"YES": 2, "NO": 0}, cost={"YES": "0.80", "NO": 0},
                   now=100, c=dict(qty=2, residual_cap_usd="3",
                                   per_market_usd="8", edge_gate=False))
    assert q["YES"]["px"] == D("0.42")


def test_independent_add_with_the_ladder_stays_inside_the_caps(monkeypatch):
    """Ladder levels behind an independently priced addition are planned
    from its (higher) price; the per-market unpaired cap still decides how
    many fit: 2 held at 0.40 ($0.80) + 2 at 0.54 + 2 at 0.53 = $2.94; a
    further level (0.52) would pass $3."""
    import time as _t
    from tests.test_pmus_rn1_ladder import resting  # noqa: F401
    from src import pmus_sports_two_sided as M
    from tests.test_pmus_sports_two_sided import FakeVenue, T  # noqa: F401
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 1)
    mk = {"pos": {"YES": D("2"), "NO": D("0")},
          "cost": {"YES": D("0.80"), "NO": D("0")}}
    want = {"YES": {"px": D("0.54"), "qty": D("2"), "role": "extend"},
            "NO": None}

    class E:
        rn1_ladder = M.Engine.rn1_ladder

        def rn1_campaign_exposure(self, exclude=None):
            return {"spend": D(0), "unmatched": D(0)}
    lv = E().rn1_ladder("aec-x", mk, want, D("0.01"), clip=D("2"))
    assert [str(l_["px"]) for l_ in lv] == ["0.53"]
    assert all(l_["side"] == "YES" for l_ in lv)
