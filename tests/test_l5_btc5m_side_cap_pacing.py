"""The side cap can be released across the window. It only ever lowers the
cap in force; off by default."""
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from tests.test_l5_btc5m_one_sided_control import (     # noqa: F401
    _controller_on, decide, inv, legs)

ON = dict(straddle_pace_side_cap=True)
CUT = float(C.Params().no_new_entry_after_s)
#: 0.20 UP at 0.50 and the same DOWN: $0.10 committed a side, balanced
HELD = inv("0.20", "0.10", "0.20", "0.10")


def test_off_by_default_nothing_changes():
    for t in (5.0, 60.0, 200.0):
        a = decide(HELD, "0.50", "0.51", t=t)
        b = decide(HELD, "0.50", "0.51", t=t, straddle_pace_side_cap=False)
        assert a["orders"] == b["orders"]
        assert a["evaluation"]["pacing"]["on"] is False


def test_early_in_the_window_a_side_already_at_its_paced_cap_waits():
    r = decide(HELD, "0.50", "0.51", t=10.0, **ON)
    assert legs(r) == []
    assert set(r["evaluation"]["side_refusals"].values()) == {"SIDE_CAP_PACED"}
    assert D(r["evaluation"]["pacing"]["side_cap_now"]) < D("0.30")


def test_later_the_same_position_may_be_added_to():
    late = decide(HELD, "0.50", "0.51", t=CUT - 1.0, **ON)
    assert late["orders"] == decide(HELD, "0.50", "0.51", t=CUT - 1.0)["orders"]
    assert len(legs(late)) == 2


def test_one_clip_is_always_allowed_from_the_start():
    r = decide(inv(), "0.50", "0.51", t=1.0, **ON)
    assert sorted(o["side"] for o in legs(r)) == ["DOWN", "UP"]
    assert D(r["evaluation"]["pacing"]["side_cap_now"]) == D("0.05")


@pytest.mark.parametrize("t", [1.0, 30.0, 90.0, 150.0, 239.0])
def test_the_cap_in_force_never_exceeds_the_configured_cap(t):
    p = decide(HELD, "0.50", "0.51", t=t, **ON)["evaluation"]["pacing"]
    assert D(p["side_cap_now"]) <= D(p["side_cap_full"]) == D("0.30")


def test_it_rises_with_time():
    caps = [D(decide(HELD, "0.50", "0.51", t=t, **ON)["evaluation"]["pacing"]
              ["side_cap_now"]) for t in (10.0, 60.0, 120.0, 200.0)]
    assert caps == sorted(caps) and caps[0] < caps[-1]


def test_it_never_places_what_the_unpaced_controller_would_not():
    for t in (5.0, 40.0, 100.0, 180.0):
        for bid in ("0.30", "0.50", "0.70"):
            ask = str(D(bid) + D("0.01"))
            off = {(o["side"], o["price"], o["qty"])
                   for o in legs(decide(HELD, bid, ask, t=t))}
            on = {(o["side"], o["price"], o["qty"])
                  for o in legs(decide(HELD, bid, ask, t=t, **ON))}
            assert on <= off


def test_the_full_cap_is_still_named_as_itself():
    full = inv("0.60", "0.30", "0.60", "0.30")
    r = decide(full, "0.50", "0.51", t=CUT - 1.0, **ON)
    assert "SIDE_CAP_REACHED" in set(r["evaluation"]["side_refusals"].values())


@pytest.mark.parametrize("raw,expect", [
    (None, False), ("", False), ("1", False), ("LINEAR", False),
    ("LINEAR_TO_ENTRY_CUTOFF", True)])
def test_only_the_exact_token_enables_it(monkeypatch, raw, expect):
    if raw is None:
        monkeypatch.delenv(X.STRADDLE_PACE_SIDE_CAP_ENV, raising=False)
    else:
        monkeypatch.setenv(X.STRADDLE_PACE_SIDE_CAP_ENV, raw)
    assert X.straddle_pace_side_cap() is expect
