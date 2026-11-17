"""Cheap-leg-first as a SELECTABLE, economically gated opening policy."""
import os
from decimal import Decimal as D

import pytest

os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
from src import pmus_sports_open_policy as OP  # noqa: E402
from src import pmus_sports_recovery as R  # noqa: E402
from src import pmus_sports_two_sided as M  # noqa: E402
from tests.test_pmus_sports_two_sided import FakeVenue  # noqa: E402

WANT = {"YES": D("0.42"), "NO": D("0.52")}       # pair plan, 0.94


# ----------------------------------------------------------- pure policy
def test_cheap_leg_comes_from_executable_asks_not_midpoints():
    assert OP.executable_prices(D("0.44"), D("0.46")) == {
        "YES": D("0.46"), "NO": D("0.56")}
    assert OP.cheap_side(D("0.44"), D("0.46")) == "YES"
    assert OP.cheap_side(D("0.60"), D("0.62")) == "NO"     # NO ask 0.40
    assert OP.cheap_side(D("0.49"), D("0.51")) is None     # tie: no cheap leg


def test_max_completion_recomputed_from_actual_cost_basis():
    assert OP.max_completion_px(D("0.42")) == D("0.56")
    assert OP.max_completion_px(D("0.40"), buffer=D("0.03"),
                                fee_per_contract=D("0.02")) == D("0.55")


def test_two_sided_is_the_default_and_unchanged():
    d = OP.choose("two_sided", WANT, D("0.44"), D("0.46"))
    assert d["sides"] == ("YES", "NO") and d["policy"] == "two_sided"
    assert OP.choose("nonsense", WANT, D("0.44"), D("0.46"))["sides"] == \
        ("YES", "NO")


def test_cheap_first_quotes_only_the_cheap_leg_when_the_gate_passes():
    d = OP.choose("cheap_first", WANT, D("0.44"), D("0.46"))
    assert d["sides"] == ("YES",) and d["policy"] == "cheap_first"
    assert D(d["max_completion_px"]) == D("0.56")


def test_a_long_shot_is_not_attractive_just_because_it_is_cheap():
    d = OP.choose("cheap_first", {"YES": D("0.10"), "NO": D("0.85")},
                  D("0.12"), D("0.14"))
    assert d["sides"] == ("YES", "NO") and \
        d["why"] == "cheap_leg_outside_band"


def test_no_cheap_first_when_the_completion_could_not_settle_a_profit():
    # plan 0.45 + 0.54 = 0.99: completion needs <= 0.53 at a 2c buffer
    d = OP.choose("cheap_first", {"YES": D("0.45"), "NO": D("0.54")},
                  D("0.46"), D("0.47"))
    assert d["why"] == "completion_not_profitable"
    assert d["sides"] == ("YES", "NO")                    # falls back


def test_auto_follows_the_bots_own_evidence():
    # prior alone (live episodes): cheap-first ahead -> selected
    assert OP.choose("auto", WANT, D("0.44"), D("0.46"))["policy"] == \
        "cheap_first"
    # the bot's own records say cheap-first episodes lose -> not selected
    bad = [{"outcome": "paired", "lead_px": "0.40", "pair_cost": "1.06"}
           ] * 60 + [{"outcome": "paired", "lead_px": "0.60",
                      "pair_cost": "0.97"}] * 60
    d = OP.choose("auto", WANT, D("0.44"), D("0.46"), records=bad)
    assert d["policy"] == "two_sided" and d["why"] == "evidence_not_ahead"


def test_expensive_first_comparison_arm():
    d = OP.choose("expensive_first", WANT, D("0.44"), D("0.46"))
    assert d["sides"] == ("NO",)


def test_no_plan_means_nothing_to_place():
    assert OP.choose("cheap_first", None, D("0.44"), D("0.46"))["sides"] == ()


# ------------------------------------------------- taker completion gate
def test_taker_completion_only_at_a_positive_net_margin():
    base = dict(lead="YES", lead_px=D("0.44"), need=D("1"), tick=D("0.01"),
                age_s=3600, late=False, taker_allowed=True)
    # NO ask 0.53 (bid 0.47): 0.44 + 0.53 + fee 0.03 = 1.00 > 0.98
    d = R.decide(bid=D("0.47"), ask=D("0.48"), **base,
                 params={"taker_max_pair": D("0.98")})
    assert d["scores"]["COMPLETE_TAKER"]["why"] == "pair_not_profitable"
    assert d["action"] != "COMPLETE_TAKER"
    # NO ask 0.50: 0.44 + 0.50 + 0.03 = 0.97 <= 0.98 -> allowed
    d = R.decide(bid=D("0.50"), ask=D("0.51"), **base,
                 params={"taker_max_pair": D("0.98")})
    assert d["scores"]["COMPLETE_TAKER"].get("value") is not None
    # unset: the old behaviour (lock-loss cap only)
    d = R.decide(bid=D("0.47"), ask=D("0.48"), **base)
    assert d["scores"]["COMPLETE_TAKER"].get("value") is not None


# ------------------------------------------------------- engine wiring
@pytest.fixture
def cf(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "OPEN_POLICY", "cheap_first")
    monkeypatch.setattr(M, "RECOVERY_ON", True)
    v = FakeVenue()                                    # 0.44 / 0.46
    return M.Engine(v, M.Book(tmp_path / "s.json")), v


def resting(v):
    return sorted((o["side"], str(o["px"])) for o in v.orders.values()
                  if o["open"])


def test_engine_rests_only_the_cheap_leg_and_does_not_churn(cf):
    e, v = cf
    e.select(v.account())
    e.step_market("aec-x")
    assert resting(v) == [("YES", "0.42")]
    stage = e.b.m("aec-x")["orders"][next(iter(v.orders))]["stage"]
    assert stage.endswith("_cheap_first")
    n = v.n
    e.step_market("aec-x")
    assert v.n == n                                   # kept, not re-placed


def test_cheap_leg_fill_is_completed_and_attributed(cf):
    e, v = cf
    e.select(v.account())
    e.step_market("aec-x")
    oid = next(k for k, o in v.orders.items() if o["open"])
    v.orders[oid].update(filled=D("1"), open=False)
    e.step_market("aec-x")                            # lone YES @0.42
    assert any(o["open"] and o["side"] == "NO" for o in v.orders.values())
    comp = next(k for k, o in v.orders.items()
                if o["open"] and o["side"] == "NO")
    v.orders[comp].update(filled=D("1"), open=False)
    e.step_market("aec-x")
    rec = e.b.d["recoveries"][-1]
    assert rec["outcome"] == "paired" and rec["open_policy"] == "cheap_first"
    assert D(rec["lead_px"]) == D("0.42")
    assert D(rec["pair_cost"]) == D("0.42") + D(rec["completion_px"])


def test_default_policy_still_quotes_both_legs(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "OPEN_POLICY", "two_sided")
    v = FakeVenue()
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.select(v.account())
    e.step_market("aec-x")
    assert resting(v) == [("NO", "0.52"), ("YES", "0.42")]


def test_cheap_first_never_bypasses_existing_stops(cf, monkeypatch):
    e, v = cf
    monkeypatch.setattr(e, "daily_stopped", lambda: True)
    e.select(v.account())
    e.step_market("aec-x")
    assert resting(v) == []
