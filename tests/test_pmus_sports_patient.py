"""PATIENT stranded-leg management (owner 2026-11-18, his manual style)."""
import os
from decimal import Decimal as D

import pytest

os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
from src import pmus_sports_recovery as R  # noqa: E402
from src import pmus_sports_two_sided as M  # noqa: E402
from tests.test_pmus_sports_two_sided import FakeVenue  # noqa: E402

P = {"patient": True}
# lead YES @0.40; YES book 0.40/0.42 -> NO book 0.58/0.60
BASE = dict(lead="YES", lead_px=D("0.40"), need=D("5"), bid=D("0.40"),
            ask=D("0.42"), tick=D("0.01"), late=False)


def test_pair_cap_steps_only_with_time_and_late_stage():
    assert R.patient_pair_cap(0, False) == D("0.97")
    assert R.patient_pair_cap(599, False) == D("0.97")
    assert R.patient_pair_cap(600, False) == D("0.99")
    assert R.patient_pair_cap(1800, False) == D("1.00")
    assert R.patient_pair_cap(0, True) == D("1.03")


def test_completion_rests_at_the_pair_cap_below_the_bid_and_waits():
    d = R.decide(**BASE, age_s=0, params=P)
    # 0.97 - 0.40 = 0.57: under the NO bid 0.58 -> rests, waits for a dip
    assert d["action"] == "COMPLETE_MAKER" and D(d["px"]) == D("0.57")
    assert d["mode"] == "patient" and d["pair_cap"] == "0.97"


def test_it_does_not_chase_the_tick():
    # the book moves against us but the cap has not stepped: KEEP 0.57
    moved = dict(BASE, bid=D("0.36"), ask=D("0.38"))     # NO 0.62/0.64
    d = R.decide(**moved, age_s=120, resting_px=D("0.57"), params=P)
    assert d["action"] == "KEEP" and D(d["px"]) == D("0.57")


def test_steps_up_after_ten_minutes_and_never_crosses_the_ask():
    d = R.decide(**BASE, age_s=700, resting_px=D("0.57"), params=P)
    assert d["action"] == "REPRICE_COMPLETION" and D(d["px"]) == D("0.59")
    # 1.00 cap would be 0.60 = the NO ask: stays one tick under (post-only)
    d = R.decide(**BASE, age_s=2000, params=P)
    assert D(d["px"]) == D("0.59")


def test_final_stage_accepts_a_small_locked_loss():
    far = dict(BASE, bid=D("0.30"), ask=D("0.32"), late=True)  # NO .68/.70
    d = R.decide(**far, age_s=60, params=P)
    assert D(d["px"]) == D("0.63")                      # 1.03 - 0.40


def test_default_is_unchanged():
    assert R.DEFAULTS["patient"] is False
    d0 = R.decide(**BASE, age_s=0)
    assert d0.get("mode") != "patient"
    assert M.RECOVERY_PATIENT is False and M.MAX_STRANDED == 0


# ------------------------------------------------------------ engine
@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RECOVERY_ON", True)
    monkeypatch.setattr(M, "RECOVERY_PATIENT", True)
    monkeypatch.setattr(M, "OPEN_POLICY", "two_sided")
    v = FakeVenue()                                      # YES 0.44/0.46
    return M.Engine(v, M.Book(tmp_path / "s.json")), v


def test_engine_rests_the_completion_at_the_pair_cap(eng):
    e, v = eng
    e.select(v.account())
    e.step_market("aec-x")
    yes = next(k for k, o in v.orders.items() if o["open"] and
               o["side"] == "YES")
    v.orders[yes].update(filled=D("1"), open=False)      # YES @0.42
    e.step_market("aec-x")
    e.step_market("aec-x")
    comp = [o for o in v.orders.values() if o["open"] and o["side"] == "NO"]
    assert comp and all(D(str(o["px"])) <= D("0.55") for o in comp)
    # 0.42 + 0.55 = 0.97: the pair cap, not the touch


def test_max_stranded_stops_new_first_legs(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "MAX_STRANDED", 1)
    e.b.m("aec-other")["lone_since"] = 1.0               # one stranded leg
    assert e.stranded_count() == 1
    assert e.halt_new_exposure() is True
    monkeypatch.setattr(M, "MAX_STRANDED", 0)
    assert e.halt_new_exposure() is False
