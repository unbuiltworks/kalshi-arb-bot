"""ENABLED-MODE engine tests of PMUS_RN1_DIRECTIONAL on the release
activation config (tests/pmus_enabled_mode.py fixture `en`), with a settled
international fair value supplied through Engine.intl_fair_band."""
from decimal import Decimal as D

import pytest

from src import pmus_directional_alloc as DA
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_enabled_mode_safety import (  # noqa: F401
    en, full_ladder, quotes, worst)
from tests.test_pmus_rn1_ladder import _cool_down, _fill
from tests.test_pmus_sports_continuous_controller import SLUG, resting


def _fair(e, monkeypatch, mid, settled=True):
    band = {"mid": D(mid), "lo": D(mid), "hi": D(mid), "settled": settled,
            "calm": True, "range": D(0), "span_s": 30.0}
    monkeypatch.setattr(e, "intl_fair_band", lambda slug: dict(band))


@pytest.fixture
def dr(en, monkeypatch):
    monkeypatch.setattr(M, "RN1_DIRECTIONAL", True)
    return en


def test_flag_off_by_default():
    assert M.RN1_DIRECTIONAL is False or \
        __import__("os").getenv("PMUS_RN1_DIRECTIONAL") == "1"
    assert M.RN1_DIR_CFG["judge_openings"] is False


def test_without_a_settled_fair_value_nothing_changes(dr, monkeypatch):
    e, v, _ = dr
    _fair(e, monkeypatch, "0.60", settled=False)
    full_ladder(e, v)


def test_openings_pass_and_additions_on_the_overpriced_side_are_refused(
        dr, monkeypatch, capsys):
    """Fair YES 0.60, book 0.48/0.50: both sides still open (level 0), the
    YES ladder rests, the NO levels behind its opening are refused (NO fair
    0.40 is below every NO bid)."""
    e, v, _ = dr
    _fair(e, monkeypatch, "0.60")
    e.step_market(SLUG)
    rs = resting(v)
    assert ("NO", "0.49", "2") in rs
    assert [r for r in rs if r[0] == "NO"] == [("NO", "0.49", "2")]
    assert [r for r in rs if r[0] == "YES"] == [
        ("YES", "0.45", "2"), ("YES", "0.46", "2"), ("YES", "0.47", "2")]
    q = quotes(capsys)[-1]
    assert q["directional"]["favoured"] == "YES"
    assert q["directional"]["ladder_refused"]


def test_favoured_outcome_flips_additions_stop_completion_kept(
        dr, monkeypatch):
    """YES filled while favoured; fair moves to 0.40: no YES addition
    rests, the NO completion still rests at or under the pair cap."""
    e, v, _ = dr
    _fair(e, monkeypatch, "0.60")
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    _cool_down(e)
    _fair(e, monkeypatch, "0.40")
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    assert D(str(mk["pos"]["YES"])) == D(2)
    assert [r for r in resting(v) if r[0] == "YES"] == []
    avg = D(str(mk["cost"]["YES"])) / D(str(mk["pos"]["YES"]))
    nos = [o for o in mk["orders"].values() if o.get("open")
           and o["side"] == "NO"]
    assert nos and all(D(str(o["px"])) + avg <= R.PAIR_CAP for o in nos)


def test_caps_hold_through_fills_with_directional_on(dr, monkeypatch):
    e, v, _ = dr
    _fair(e, monkeypatch, "0.55")
    e.step_market(SLUG)
    for _ in range(4):
        for o in v.orders.values():
            if o["open"]:
                o["filled"], o["open"] = o["qty"], False
        e.step_market(SLUG)
        _cool_down(e)
        e.step_market(SLUG)
        mk = e.b.m(SLUG)
        assert R.unmatched_usd(mk["pos"], mk["cost"]) <= R.RESIDUAL_CAP_USD
        c = worst(e)
        assert c["unmatched"] <= R.RESIDUAL_CAP_USD
        assert c["spend"] <= R.TOTAL_SPEND_USD


def test_an_allocation_error_changes_nothing(dr, monkeypatch, capsys):
    e, v, _ = dr
    _fair(e, monkeypatch, "0.60")

    def boom(**kw):
        raise RuntimeError("x")
    monkeypatch.setattr(DA, "allocate", boom)
    full_ladder(e, v)
    q = quotes(capsys)[-1]
    assert q["directional"]["why"] == "error:RuntimeError"


def test_campaign_loss_stop_still_pulls_openings_with_directional_on(
        dr, monkeypatch):
    """The real campaign stop (PILOT_LOSS_USD, latched in the book) still
    leaves only the completion; directional on brings no opening back."""
    import time
    from tests.test_pmus_enabled_mode_safety import only_completion
    from tests.test_pmus_rn1_ladder import _pilot_ok
    e, v, _ = dr
    _fair(e, monkeypatch, "0.60")
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("4.00"))
    monkeypatch.setattr(M, "RN1_LOSS_STOP_COUNTS_RESTING", False)
    _pilot_ok(e)
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    e._pilot = {"realized_net_usd": "-4.10", "open_residual_worst_usd": "0",
                "unconfirmed": []}
    e._pilot_at = time.monotonic()
    _cool_down(e)
    e.step_market(SLUG)
    assert e.b.d["pilot_loss_stop"]["net"] == "-4.10"
    assert only_completion(e)


def _sharp(e, fair, age_s=0.0, odds_age_s=0.0):
    import time as _t
    e.b.d.setdefault("sharp_shadow", {})[SLUG] = {
        "t": _t.time() - age_s, "fair_yes": fair, "odds_age_s": odds_age_s,
        "source": "pinnacle"}


def test_sportsbook_probability_used_when_no_international_fair(
        dr, monkeypatch, capsys):
    e, v, _ = dr
    _fair(e, monkeypatch, "0.60", settled=False)
    monkeypatch.setattr(M, "RN1_DIR_SHARP", True)
    _sharp(e, "0.60")
    e.step_market(SLUG)
    q = quotes(capsys)[-1]
    assert q["directional"]["source"] == "sportsbook:pinnacle"
    assert [r for r in resting(v) if r[0] == "NO"] == [("NO", "0.49", "2")]


def test_stale_sportsbook_probability_changes_nothing(dr, monkeypatch):
    e, v, _ = dr
    _fair(e, monkeypatch, "0.60", settled=False)
    monkeypatch.setattr(M, "RN1_DIR_SHARP", True)
    _sharp(e, "0.60", age_s=M.RN1_DIR_SHARP_MAX_AGE_S + 5)
    full_ladder(e, v)


def test_sportsbook_off_by_default_changes_nothing(dr, monkeypatch):
    e, v, _ = dr
    _fair(e, monkeypatch, "0.60", settled=False)
    _sharp(e, "0.60")
    full_ladder(e, v)


def test_upsizing_never_exceeds_the_per_order_maximum(dr, monkeypatch,
                                                    capsys):
    """up_mult 2 asks for more, but the final gate's per-order maximum is a
    limit: the hint is capped at it, so nothing is refused at the gate."""
    e, v, _ = dr
    _fair(e, monkeypatch, "0.70")
    monkeypatch.setitem(M.RN1_DIR_CFG, "up_mult", "2")
    e.step_market(SLUG)
    out = capsys.readouterr().out
    assert "size_above_maximum" not in out
    assert all(D(r[2]) <= M.rn1_max_clip() for r in resting(v))


def test_favoured_side_gets_extra_levels_inside_the_caps(dr, monkeypatch):
    """extra_levels 2: the favoured YES side may ladder deeper; every level
    still has to fit the exact worst-case caps."""
    e, v, _ = dr
    _fair(e, monkeypatch, "0.70")
    monkeypatch.setitem(M.RN1_DIR_CFG, "extra_levels", 2)
    e.step_market(SLUG)
    ys = [r for r in resting(v) if r[0] == "YES"]
    assert len(ys) >= 3
    c = worst(e)
    assert c["unmatched"] <= R.RESIDUAL_CAP_USD
    assert c["spend"] <= R.PER_MARKET_USD


def test_priced_additions_withdrawn_if_allocation_fails(dr, monkeypatch):
    e, v, _ = dr
    _fair(e, monkeypatch, "0.60")
    monkeypatch.setattr(M, "RN1_DIR_PRICE_ADDS", True)
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    _cool_down(e)

    def boom(**kw):
        raise RuntimeError("x")
    monkeypatch.setattr(DA, "allocate", boom)
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    yes_open = [o for o in mk["orders"].values() if o.get("open")
                and o["side"] == "YES" and o.get("level", 0) == 0]
    assert all(o.get("stage") != "rn1_extend" for o in yes_open)
