"""The modelled fill instrumentation must be inert with respect to money.

It is wired into the live executor's own decision stream, so the thing to
prove is not that it computes well -- test_l5_maker_fill_model.py does that
-- but that it CANNOT submit, cannot alter a gate, and cannot break the
executor or the feed when it fails.
"""
from __future__ import annotations

import ast
import inspect
from decimal import Decimal as D

import pytest

from src import l5_maker_fill_model as FM
from src import l5_pmus_auth_book as AB
from src import l5_pmus_live_runner as RUN


# --------------------------------------------------- it cannot move money

def test_the_shadow_lock_is_untouched_by_this_change():
    assert RUN.ShadowOnlyTransport.BLOCKED == ("submit",)
    assert RUN.shadow_only() is True


def test_the_gates_are_untouched_by_this_change():
    from src import l5_live_validation_gates as VG
    g = VG.ValidationGates()
    assert g.max_cash_deployed_usd == D("10")
    assert g.max_unmatched_contracts == D("5")
    assert g.max_cumulative_loss_usd == D("20")
    assert g.cash_policy == VG.NO_RECYCLING


def test_the_model_is_not_consulted_by_any_refusal():
    """A modelled number must never be able to admit a real order."""
    from src import l5_live_execution as LX
    src = inspect.getsource(LX)
    for name in ("l5_maker_fill_model", "ModelSession", "HypoInventory",
                 "HypoOrder"):
        assert name not in src, (
            "the execution path references %r; modelled state must never "
            "reach a real decision" % name)


def test_model_methods_never_call_submit_or_arm():
    for fn in (RUN.Runner.model_place, RUN.Runner.model_advance,
               RUN.Runner.model_close):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)}
        for bad in ("submit", "arm", "cancel", "place_order", "preview"):
            assert bad not in called, "%s calls .%s()" % (fn.__name__, bad)


# --------------------------------------------------- it cannot break things

def test_a_raising_observer_cannot_take_down_the_feed():
    """Reconciliation depends on this book. A modelling bug must not."""
    b = AB.AuthBook()
    b.frame_observer = lambda m, t: 1 / 0
    b.ingest({"marketSlug": "s", "bids": [], "offers": []}, 1.0)
    assert b.observer_errors == 1


def test_the_observer_is_off_unless_attached():
    assert AB.AuthBook().frame_observer is None


def test_trades_returns_a_copy_not_the_live_list():
    """The feed thread trims this list to 500 while a reader iterates."""
    b = AB.AuthBook()
    b.state["s"] = {"trades": [{"t": 1.0}]}
    got = b.trades("s")
    got.append({"t": 2.0})
    assert len(b.state["s"]["trades"]) == 1


class _R:
    """Only what the model hooks touch."""
    model_place = RUN.Runner.model_place
    model_advance = RUN.Runner.model_advance

    def __init__(self, model=None, book=None):
        import collections
        self.model, self.book = model, book
        self.stats = collections.Counter()


def test_a_malformed_intent_is_counted_not_raised(monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    r = _R(model=FM.ModelSession("s"))
    r.model_place({"mode": "MAKER", "side": "UP", "price": "oops",
                   "qty": "5"}, {"pmus": {"bbo": {}}}, 1.0)
    assert r.stats["model_place_errors"] == 1


def test_no_model_session_is_a_no_op(monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    r = _R(model=None)
    r.model_place({"mode": "MAKER", "side": "UP", "price": "0.5",
                   "qty": "5"}, {"pmus": {"bbo": {}}}, 1.0)
    r.model_advance(1.0)
    assert not r.stats


def test_a_taker_intent_is_never_modelled_as_resting(monkeypatch):
    monkeypatch.setattr(RUN, "emit", lambda *a, **k: None)
    m = FM.ModelSession("s")
    r = _R(model=m)
    r.model_place({"mode": "TAKER", "side": "UP", "price": "0.5",
                   "qty": "5"}, {"pmus": {"bbo": {}}}, 1.0)
    assert m.intents == 0


def test_a_maker_intent_is_modelled_with_the_book_at_the_decision(monkeypatch):
    seen = []
    monkeypatch.setattr(RUN, "emit", lambda k, p: seen.append((k, p)))
    m = FM.ModelSession("s")
    m.ingest_frame({"marketSlug": "s",
                    "bids": [{"px": {"value": "0.53"}, "qty": "300"},
                             {"px": {"value": "0.52"}, "qty": "100"}],
                    "offers": [{"px": {"value": "0.54"}, "qty": "400"}]}, 1.0)
    r = _R(model=m)
    r.model_place({"mode": "MAKER", "side": "UP", "price": "0.52",
                   "qty": "5", "client_order_id": "c1"},
                  {"pmus": {"bbo": {"bid": "0.53", "ask": "0.54"}}}, 9.0)
    assert m.intents == 1
    rec = [p for k, p in seen if k == "L5_MODEL_INTENT"][0]
    assert rec["displayed_depth_at_price"] == "100"
    assert rec["queue_ahead_at_placement"] == "400"
    assert rec["ticks_behind_touch"] == 1
    assert "MODELLED" in rec["labels"]


# --------------------------------------------------- labelling discipline

def test_every_model_marker_is_namespaced_and_says_modelled():
    src = inspect.getsource(RUN)
    for marker in ("L5_MODEL_INTENT", "L5_MODEL_FILL", "L5_MODEL_WINDOW",
                   "L5_MODEL_ERROR"):
        assert marker in src
    # and never under a realized/settled name
    assert "L5_LIVE_SETTLED" not in inspect.getsource(RUN.Runner.model_close)


def test_an_unestablished_outcome_settles_nothing():
    m = FM.ModelSession("s")
    m.place(FM.UP, D("0.5"), D("5"), 1.0, D("0.51"), D("0.52"))
    rep = m.close(up_won=None)
    for sc in FM.SCENARIOS:
        assert rep["scenarios"][sc]["settlement"]["modelled_net"] is None


def test_the_env_switch_defaults_on_and_can_be_silenced(monkeypatch):
    monkeypatch.delenv(RUN.MODEL_FILLS_ENV, raising=False)
    assert RUN.model_fills() is True
    monkeypatch.setenv(RUN.MODEL_FILLS_ENV, "0")
    assert RUN.model_fills() is False


def test_the_model_advances_even_during_a_venue_cooloff():
    """Below the cool-off early-return the model would stall, and the
    recorder's trade list is a rolling 500 -- a long stall drops prints.
    The model makes no venue requests, so it must not be gated on one."""
    src = inspect.getsource(RUN.Runner.tick)
    i_model = src.index("self.model_advance(")
    i_cool = src.index("cooling = self.venue_cooling()")
    assert i_model < i_cool, (
        "model_advance sits below the cool-off check and will stall")
    i_roll = src.index("self.roll(")
    assert i_roll < i_model, "the window's model session is not open yet"
