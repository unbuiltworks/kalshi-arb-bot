"""Can the late-favorite actually produce an order, and does EV still bite?

Everything here is about the gap between "configured" and "reachable".
Before this wiring the strategy had no environment readers at all, so
all four of its inputs sat at 0 and it refused on every step of every
window -- which in a log is indistinguishable from a market with no
opportunity in it.
"""
import os
from decimal import Decimal as D

import pytest

from src import l5_btc5m_execution as X
from src import l5_late_favorite as LF
from src import l5_live_safety as S

ENVS = (X.LATE_WINDOW_ENV, X.LATE_CLIP_ENV, X.LATE_EDGE_ENV, X.LATE_FEE_ENV)


@pytest.fixture(autouse=True)
def _clean_env():
    saved = {k: os.environ.get(k) for k in ENVS}
    for k in ENVS:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ------------------------------------------------------- the readers

def test_every_input_defaults_to_a_refusing_value():
    assert X.late_favorite_window_s() == D("0")
    assert X.late_favorite_clip_usd() == D("0")
    assert X.late_favorite_min_edge_per_share() == D("0")


def test_a_window_at_or_under_the_cancel_lead_is_refused():
    #: THE SHIPPED DEFECT. 30 is the cancel lead, so a 30-second window
    #: begins exactly where the session stops stepping.
    for v in ("30", "35", "0", "-5"):
        os.environ[X.LATE_WINDOW_ENV] = v
        assert X.late_favorite_window_s() == D("0"), v


def test_a_reachable_window_is_accepted():
    os.environ[X.LATE_WINDOW_ENV] = "45"
    assert X.late_favorite_window_s() == D("45")


def test_an_absurd_window_is_refused_rather_than_clamped():
    os.environ[X.LATE_WINDOW_ENV] = "280"
    assert X.late_favorite_window_s() == D("0")


def test_an_edge_margin_above_the_bound_is_refused():
    os.environ[X.LATE_EDGE_ENV] = "0.50"
    assert X.late_favorite_min_edge_per_share() == D("0")


def test_a_clip_over_the_per_market_ceiling_is_refused():
    os.environ[X.LATE_CLIP_ENV] = "6.00"
    assert X.late_favorite_clip_usd() == D("0")


def test_garbage_never_becomes_a_number():
    for env in ENVS:
        os.environ[env] = "soon"
    assert X.late_favorite_window_s() == D("0")
    assert X.late_favorite_clip_usd() == D("0")
    assert X.late_favorite_min_edge_per_share() == D("0")
    assert X.late_favorite_fee_per_share() == D("0")


def test_an_unreadable_fee_keeps_zero_rather_than_refusing():
    #: a maker order may genuinely pay no commission, and overstating
    #: the fee only makes the EV gate stricter
    os.environ[X.LATE_FEE_ENV] = "nonsense"
    assert X.late_favorite_fee_per_share() == D("0")


# ------------------------------------------- reachable, end to end

def _book(bid="0.9700", ask="0.9900", bid_shares="40", ask_shares="40"):
    return {"bid": D(bid), "ask": D(ask),
            "bid_shares": D(bid_shares), "ask_shares": D(ask_shares)}


def _eval(**over):
    kw = dict(p_up=D("0.995"), p_age_s=D("0.4"), t_in_window_s=D("265"),
              window_s=D("300"), book=_book(),
              inventory={"UP": {"qty": D(0), "cost": D(0)},
                         "DOWN": {"qty": D(0), "cost": D(0)}},
              resting=[], room_usd=D("5.00"), loss_room_usd=D("5.00"),
              clip_usd=D("2.00"), fee_per_share=D("0"),
              min_edge_per_share=D("0.004"), late_window_s=D("45"))
    kw.update(over)
    return LF.evaluate(**kw)


def test_an_unset_late_window_refuses_as_itself(monkeypatch):
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    assert _eval(late_window_s=D("0"))["reason"] == "NO_LATE_WINDOW_SET"
    assert _eval(late_window_s=None)["reason"] == "NO_LATE_WINDOW_SET"


def test_the_shipped_thirty_second_window_refuses_at_every_stepped_time(
        monkeypatch):
    #: the whole stepped range, at the real 5s cadence, under the value
    #: that shipped. Not one of them produces an order.
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    reasons = set()
    t = D("0")
    while t < D("270"):
        reasons.add(_eval(t_in_window_s=t, late_window_s=D("30"))["reason"])
        t += D("5")
    assert reasons == {"TOO_EARLY"}


def test_with_a_reachable_window_it_produces_an_order(monkeypatch):
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    out = _eval()
    assert out["ok"] is True, out["reason"]
    o = out["order"]
    assert o["side"] == "UP"
    #: cheapest level that clears the margin without crossing the ask
    assert D(o["price"]) < D("0.9900")
    assert D(o["qty"]) > 0
    assert o["post_only"] is True


# ------------------------------------------- the EV gate still bites

def test_a_probability_at_break_even_is_refused(monkeypatch):
    #: at 0.97 the break-even win rate IS about 97%. A model that says
    #: exactly that leaves nothing for the margin.
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    out = _eval(p_up=D("0.970"))
    assert out["ok"] is False
    assert out["reason"] == "NO_LEVEL_QUALIFIED"
    #: and every level says WHY, so a refusal is auditable rather than
    #: just a no
    rejects = {r["price"]: r.get("reject")
               for r in (out.get("considered") or ())}
    assert rejects, out
    assert set(rejects.values()) <= {"WOULD_CROSS", "EDGE_BELOW_MARGIN"}


def test_a_thin_edge_is_refused_rather_than_rounded_up(monkeypatch):
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    thin = _eval(p_up=D("0.9735"), min_edge_per_share=D("0.01"))
    assert thin["ok"] is False


def test_raising_the_margin_refuses_what_a_lower_one_allowed(monkeypatch):
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    assert _eval(min_edge_per_share=D("0.004"))["ok"] is True
    assert _eval(min_edge_per_share=D("0.19"))["ok"] is False


def test_fees_are_charged_against_the_edge(monkeypatch):
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    free = _eval(p_up=D("0.9790"), min_edge_per_share=D("0.008"))
    paid = _eval(p_up=D("0.9790"), min_edge_per_share=D("0.008"),
                 fee_per_share=D("0.01"))
    assert free["ok"] is True
    assert paid["ok"] is False


def test_it_never_crosses_the_ask(monkeypatch):
    #: a maker strategy that crosses is a taker strategy
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    out = _eval(book=_book(ask="0.9700"))
    if out["ok"]:
        assert D(out["order"]["price"]) < D("0.9700")
    for row in out.get("considered") or ():
        if row.get("reject") != "WOULD_CROSS":
            assert D(row["price"]) < D("0.9700")


def test_a_stale_probability_refuses_however_good_it_looks(monkeypatch):
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    out = _eval(p_up=D("1.0"), p_age_s=D("9"))
    assert out["reason"] == "PROBABILITY_STALE"


def test_off_by_default_even_with_every_input_valid(monkeypatch):
    monkeypatch.delenv(LF.ENABLE_ENV, raising=False)
    assert _eval()["reason"] == "LATE_FAVORITE_OFF"


# ------------------------------- the safety gate agrees with the wiring

def test_the_safety_gate_passes_exactly_the_reachable_configuration():
    bad = S.strategy_can_fire(late_window_s=X.late_favorite_window_s(),
                              window_s=300, cancel_lead_s=30, step_s=5)
    assert bad["ok"] is False          # unset default of 0

    os.environ[X.LATE_WINDOW_ENV] = "45"
    good = S.strategy_can_fire(late_window_s=X.late_favorite_window_s(),
                               window_s=300, cancel_lead_s=30, step_s=5)
    assert good["ok"] is True
    assert good["rest_s"] == "15"


def test_the_readers_lower_bound_matches_what_the_gate_will_accept():
    #: a value the reader accepts must be one the gate can fire, or the
    #: two would disagree and the operator would get a lane that passes
    #: configuration and still places nothing
    os.environ[X.LATE_WINDOW_ENV] = str(X.LATE_WINDOW_MIN_S)
    v = X.late_favorite_window_s()
    assert v == X.LATE_WINDOW_MIN_S
    assert S.strategy_can_fire(late_window_s=v, window_s=300,
                               cancel_lead_s=30, step_s=5)["ok"] is True
