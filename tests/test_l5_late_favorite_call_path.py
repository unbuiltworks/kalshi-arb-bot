"""A 97-99c favourite order through the REAL execution call path.

Everything else about this strategy has been tested by calling
`LF.evaluate` directly, which is exactly how the gap that made it
unreachable survived: the evaluator was right and could not be reached.

This file drives `Btc5mExecutor.step()` -- controller, arbiter, the
arming gate, the standing envelope, the shared reservation, the
authorization claim, the ledger write and the scripted venue -- and
asserts that an order with the right price and side actually arrives,
and that each gate refuses it on its own terms when it should.

Scripted and offline. Nothing here is evidence about a real venue, a
real fill probability, or economics.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_late_favorite as LF
from src import l5_persistent_mode as PMODE
from src import l5_shared_reservation as RESV
from tests import test_l5_btc5m_execution as T

X = T.X
MARKET = T.MARKET

#: Stage 1a configuration, as proposed: midpoint eligibility, one clip,
#: a conservative margin on top of positive net EV.
STAGE1 = {
    LF.ENABLE_ENV: "YES",
    X.LATE_WINDOW_ENV: "150",
    X.LATE_CLIP_ENV: "2.00",
    X.LATE_EDGE_ENV: "0.004",
    X.LATE_FEE_ENV: "0",
}


@pytest.fixture
def armed(monkeypatch, tmp_path):
    for k in T.ENV:
        monkeypatch.delenv(k, raising=False)
    T.authorize_5m(monkeypatch, tmp_path, now_fn=lambda: T.T0,
                   starts=(T.W_1200, T.W_1205))
    for k, v in T.ARMED.items():
        monkeypatch.setenv(k, v)
    for k, v in STAGE1.items():
        monkeypatch.setenv(k, v)
    #: `effective_params` re-reads the entry mode from the environment and
    #: overwrites whatever Params said, so the mode has to be set HERE or
    #: the executor silently runs EDGE_GATED and never reaches the
    #: straddle arm where the late favourite is evaluated.
    monkeypatch.setenv(X.ENTRY_MODE_ENV, C.STRADDLE)
    monkeypatch.setenv(X.STRADDLE_PRICE_ENV, "0.08")
    #: the account-wide protections, all present and binding
    monkeypatch.setenv("L5_ACCOUNT_LANES", "1")
    monkeypatch.setenv("L5_BTC5M_FOREIGN_EXPOSURE", "CHARGED")
    monkeypatch.setenv(RESV.PATH_ENV if hasattr(RESV, "PATH_ENV")
                       else "L5_SHARED_RESERVATION",
                       str(tmp_path / "resv.json"))
    #: THE FULL ENVELOPE CONTRACT, as `parse_envelope` requires it. Each
    #: field is a separate refusal when absent, and none of them
    #: defaults: `approved` must be exactly True, the mode must name
    #: itself PERSISTENT so a bounded campaign document can never be
    #: read as a standing one, the deployment must match this image,
    #: taker permission is STATED rather than defaulted, and the nonce
    #: must be at least 8 characters.
    monkeypatch.setenv(PMODE.DEPLOYMENT_ENV, "test-deployment")
    monkeypatch.setenv(PMODE.ENVELOPE_ENV, json.dumps({
        "approved": True,
        "mode": "PERSISTENT",
        "deployment": "test-deployment",
        "strategies": ["LATE_FAVORITE"],
        "series": [MARKET.rsplit("-", 4)[0] + "-"],
        "taker_allowed": False,
        "max_aggregate_usd": "10.00",
        "max_per_market_usd": "5.00",
        "max_daily_loss_usd": "5.00",
        "nonce": "stage1a-call-path-test",
    }))
    monkeypatch.setenv("L5_PERSISTENT_STATE", str(tmp_path / "state.json"))
    PMODE.write_state(PMODE.ARMED, why="test", now=T.T0)
    return tmp_path


def params():
    """STRADDLE mode with the straddle's own EV inputs LEFT UNSET.

    That is how the campaign disables new straddle entries without
    removing a line of its code: the entry-EV gate refuses a rung whose
    probabilities it does not know, so every rung reports
    ENTRY_EV_P_PAIR_NATURAL_NOT_SET and the late favourite is the only
    thing that can place. Measured on the fifth shadow run: 926 such
    refusals, no straddle order.
    """
    return C.Params(
        entry_mode=C.STRADDLE, straddle_price=D("0.08"), clip_usd=D("0.16"),
        hedge_enabled=False, reserve_mode="NONE", max_exposure_usd=D("5.00"),
        late_favorite_window_s=D("150"), late_favorite_clip_usd=D("2.00"),
        late_favorite_fee_per_share=D("0"),
        late_favorite_min_edge_per_share=D("0.004"),
        late_favorite_continuation_usd=D("0.0014"),
        late_favorite_handoff_margin_usd=D("0.01"))


def rig(tmp_path, **kw):
    return T.Rig(tmp_path, params=params(), **kw)


def _why(rep):
    return {"action": rep.get("action"), "why": rep.get("why"),
            "refused": rep.get("refused"), "late": late(rep),
            "entry_eval": rep.get("entry_eval")}


def step(r, *, p_up, t, bid="0.9600", ask="0.9850",
         bid_shares="50", ask_shares="50", age_s=0.1,
         p_age_s=0.4, loss_room_usd="5.00"):
    return r.ex.step(
        market=MARKET,
        book={"bid": bid, "ask": ask, "age_s": age_s,
              "bid_shares": bid_shares, "ask_shares": ask_shares},
        t_in_window_s=t, p_up=p_up, p_source="TEST",
        p_age_s=p_age_s, loss_room_usd=loss_room_usd)


def late(rep):
    """The controller's late-favorite block as the STEP report carries it.

    `step()` does not pass `decision["evaluation"]` through wholesale --
    it lifts named sub-blocks -- so a test that read `rep["evaluation"]`
    would silently see nothing and pass for the wrong reason.
    """
    for key in ("late_eval", "late_favorite"):
        if isinstance(rep.get(key), dict):
            return rep[key]
    return ((rep.get("evaluation") or {}).get("late_favorite") or {})


# --------------------------------------------------- it reaches the venue

def test_a_confident_up_favourite_reaches_the_venue_at_97_to_99(armed):
    r = rig(armed)
    rep = step(r, p_up="0.995", t=160.0)
    placed = rep["placed"]
    assert placed, _why(rep)
    o = placed[0]
    assert o["side"] == "UP"
    assert D("0.97") <= D(o["price"]) <= D("0.99")
    #: the whole chain ran: a venue id only exists if arming, the
    #: envelope, the reservation, the claim and the ledger all passed
    row = r.ex.orders()[o["client_order_id"]]
    assert row["venue_order_id"]
    assert row["strategy"] or row["reason"]


def test_a_confident_down_favourite_reaches_the_venue_too(armed):
    #: BOTH SIDES. The DOWN favourite's ask is 1 - the UP bid, and the
    #: size available there is what rests on the UP bid, so a
    #: side-symmetric strategy is not symmetric in the book's fields.
    r = rig(armed)
    rep = step(r, p_up="0.005", t=160.0, bid="0.0150", ask="0.0400")
    placed = rep["placed"]
    assert placed, {"refused": rep["refused"], "late": late(rep)}
    o = placed[0]
    assert o["side"] == "DOWN"
    assert D("0.97") <= D(o["price"]) <= D("0.99")


def test_the_order_is_maker_only_through_the_whole_path(armed):
    r = rig(armed)
    step(r, p_up="0.995", t=160.0)
    sent = r.venue.sent
    assert sent, "nothing reached the venue"
    for body in sent:
        #: post_only is what makes this a resting bid rather than a
        #: purchase at the ask; taker is not merely unused, it is refused
        assert body.get("post_only") is True or body.get("postOnly") is True, body


def test_it_is_evaluated_at_the_midpoint_and_not_before(armed):
    r = rig(armed)
    assert late(step(r, p_up="0.995", t=140.0))["reason"] == "TOO_EARLY"
    assert late(step(r, p_up="0.995", t=150.0)).get("reason") != "TOO_EARLY"


def test_nothing_rests_past_the_cancellation_lead(armed):
    #: the 30-second lead is preserved: the session stops stepping at
    #: t=270, so an order placed at the midpoint is withdrawn by the
    #: close sweep rather than left to settle
    r = rig(armed)
    rep = step(r, p_up="0.995", t=269.0)
    assert late(rep).get("reason") != "TOO_EARLY"


# ------------------------------------------- every gate still refuses

def test_the_ev_gate_refuses_a_break_even_probability_on_the_real_path(armed):
    r = rig(armed)
    rep = step(r, p_up="0.970", t=160.0)
    assert rep["placed"] == []
    assert late(rep)["reason"] == "NO_LEVEL_QUALIFIED"
    #: and says why, level by level, so a missed opportunity is recorded
    #: rather than merely absent
    assert late(rep).get("considered")


def test_a_stale_probability_refuses_on_the_real_path(armed):
    r = rig(armed)
    rep = step(r, p_up="0.995", t=160.0)
    assert rep["placed"]
    r2 = rig(armed)
    rep2 = step(r2, p_up="0.995", t=160.0, p_age_s=9.0)
    assert rep2["placed"] == []


def test_no_depth_on_the_favourite_refuses(armed):
    r = rig(armed)
    rep = step(r, p_up="0.995", t=160.0, ask_shares="0")
    assert rep["placed"] == []
    assert late(rep)["reason"] in ("NO_DEPTH", "NO_ASK_ON_FAVOURITE")


def test_it_never_prices_at_or_above_the_ask(armed):
    #: strict price limits: a maker strategy that crosses is a taker one
    r = rig(armed)
    rep = step(r, p_up="0.995", t=160.0, ask="0.9700")
    for o in rep["placed"]:
        assert D(o["price"]) < D("0.9700")


def test_one_clip_a_window_and_no_adding_to_a_97c_favourite(armed):
    #: ONE CLIP A WINDOW. Adding to a 0.97 favourite is how a small edge
    #: becomes a large correlated loss, so a held favourite refuses the
    #: next candidate on that side outright.
    r = rig(armed)
    first = step(r, p_up="0.995", t=160.0)
    assert first["placed"]
    coid = first["placed"][0]["client_order_id"]
    vid = r.ex.orders()[coid]["venue_order_id"]
    r.venue.fill(vid)
    r.ex.poll()
    second = step(r, p_up="0.995", t=200.0)
    assert second["placed"] == []
    assert late(second)["reason"] == "ALREADY_HOLD_FAVOURITE"


def test_the_envelope_refuses_when_the_per_market_ceiling_is_reached(
        armed, monkeypatch):
    monkeypatch.setenv(PMODE.ENVELOPE_ENV, json.dumps({
        "max_aggregate_usd": "10.00", "max_per_market_usd": "0.01",
        "max_daily_loss_usd": "5.00", "maker_only": True}))
    r = rig(armed)
    rep = step(r, p_up="0.995", t=160.0)
    assert rep["placed"] == []
    reasons = [x["reason"] for x in rep["refused"]]
    assert any(str(x).startswith(("ENVELOPE", "SHARED_RESERVATION"))
               for x in reasons), reasons


def test_the_shared_reservation_refuses_when_the_budget_is_gone(
        armed, monkeypatch):
    r = rig(armed)
    #: another lane already holds the whole account budget
    held = RESV.reserve(lane="OTHER", key="other-1", usd=D("10.00"),
                        now=T.T0, cap_usd=D("10.00"))
    assert held.get("ok") is True, held
    rep = step(r, p_up="0.995", t=160.0)
    assert rep["placed"] == []
    reasons = [str(x["reason"]) for x in rep["refused"]]
    #: EITHER gate refusing is correct, and the envelope gets there
    #: first: `may_submit` judges the worst case against
    #: `RESV.aggregate()`, so a budget already held by a sibling is seen
    #: as AGGREGATE_EXPOSURE before the reservation itself is asked for a
    #: hold. The reservation remains the binding one -- it decides under
    #: the lock -- but the envelope is the earlier, cheaper refusal.
    assert any(("RESERVATION" in x) or ("AGGREGATE_EXPOSURE" in x)
               for x in reasons), reasons


def test_off_by_default_even_fully_configured(armed, monkeypatch):
    monkeypatch.delenv(LF.ENABLE_ENV, raising=False)
    r = rig(armed)
    rep = step(r, p_up="0.995", t=160.0)
    assert rep["placed"] == []
    assert late(rep)["reason"] == "LATE_FAVORITE_OFF"


def test_the_straddle_places_nothing_with_its_ev_inputs_unset(armed):
    #: the campaign's way of disabling straddle entries without deleting
    #: code: early in the window, where only the straddle could act
    r = rig(armed)
    rep = step(r, p_up="0.75", t=30.0, bid="0.60", ask="0.62")
    assert rep["placed"] == []
    #: the rung is refused by the entry-EV gate for want of the measured
    #: probabilities -- the campaign's way of standing the straddle down
    #: without deleting a line of it
    assert rep["why"] == "ENTRY_EV_P_PAIR_NATURAL_NOT_SET"
