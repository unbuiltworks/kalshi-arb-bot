"""OWNER GO 5589583477: COMPLETE may be worked past the opening budget.

L5 completes 72.7% of windows, median 53.5 s, and only 34.8% of his
completions land inside our old horizon; his FAST completions use a median
of 7 distinct prices -- more than our whole budget of 6, which COMPLETE was
sharing with OPEN and EXTEND.

THE CORRECTION THIS ENCODES. The working loop already existed: every
iteration re-reads the book, recomputes the state key, re-asks the model and
reprices. What did not exist was enough iterations. Live: 78 of 145
order-placing sessions used exactly 6 actions and exited with NO stop reason
-- the budget, not a cap and not the clock.

The extension is OFF in conftest for the rest of the suite; these tests turn
it on explicitly."""
from __future__ import annotations

import inspect
import re

import pytest

from src import pmus_spring_l5_session as s
from tests.test_pmus_spring_l5_session import Fake, _script_proposals, _ticking

_SRC = inspect.getsource(s)


@pytest.fixture
def _extension_on(monkeypatch):
    monkeypatch.setenv(s.MAX_COMPLETE_ACTIONS_ENV, "12")


def _held_session(monkeypatch, tmp_path, rejecting: bool):
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                       _ledger_with_fill)
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    _script_proposals(monkeypatch, ["complete"])
    monkeypatch.setenv(s.MAX_ACTIONS_ENV, "1")

    class Rejecting(Fake):
        def request(self, method, path, body=None, base=None):
            out = super().request(method, path, body, base)
            if path == "/v1/orders":
                self.orders[out["id"]]["state"] = "ORDER_STATE_REJECTED"
                self.open_order = None
            return out
    cls = Rejecting if rejecting else Fake
    fake = cls(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    return s.run_session(fake, clock=_ticking(), sleep=lambda x: None,
                         wall=lambda: 1000.0, slug="held", gate_ok=True,
                         resume=True)


def test_a_residual_is_worked_past_the_opening_budget(
        monkeypatch, tmp_path, _extension_on):
    """THE POINT OF THE GO. MAX_ACTIONS is 1, yet a held residual keeps
    getting completion attempts -- which is what L5's 53.5 s median and 7
    prices require and our budget of 6 shared three ways denied."""
    rep = _held_session(monkeypatch, tmp_path, rejecting=False)
    completes = [o for o in rep["orders"] if o["relation"] == "complete"]
    assert len(completes) > 1, rep.get("stop_reason")
    assert all(o["stage"].startswith("spring_complete_") for o in completes)


def test_rejections_do_not_buy_patience(monkeypatch, tmp_path,
                                        _extension_on):
    """A REJECTED complete is not what the extended budget bought. #93's
    unexplained non-crossing rejections concentrate on COMPLETE, and a
    rejected complete at a non-crossing price REVOKES the complement proof
    -- the deadlock that took the lane down at 13:09. Retrying into refusals
    would make that more likely, not less."""
    rep = _held_session(monkeypatch, tmp_path, rejecting=True)
    completes = [o for o in rep["orders"] if o["relation"] == "complete"]
    assert len(completes) == s.COMPLETE_REJECT_STREAK_MAX, [
        o["stage"] for o in completes]
    assert rep["stop_reason"].startswith("complete_rejections_consecutive")


def test_the_extension_is_off_without_the_env(monkeypatch, tmp_path):
    """conftest pins it to 0 for the rest of the suite; with it off the
    session behaves exactly as it did before this commit."""
    rep = _held_session(monkeypatch, tmp_path, rejecting=False)
    completes = [o for o in rep["orders"] if o["relation"] == "complete"]
    assert len(completes) == 1


def test_the_loop_is_bounded_by_construction():
    """One monotonic counter against a moving limit. The first attempt used
    a second counter and a disjunctive condition and produced an UNBOUNDED
    loop -- a fixture emitted 100+ orders and the suite runtime tripled."""
    assert len(re.findall(r"(?<![a-z_])actions \+= 1", _SRC)) == 4
    assert "opening_actions" not in _SRC
    i = _SRC.index("def action_budget()")
    body = _SRC[i:i + 700]
    assert "max_actions + max_complete_actions if inv.residual != 0" in body
    assert "while actions < action_budget() and not out_of_time():" in _SRC


def test_the_extension_is_completion_only():
    """It buys working a residual into a pair, never more opening."""
    i = _SRC.index("if actions >= max_actions and relation != \"complete\":")
    assert "extension_is_completion_only_not_" in _SRC[i:i + 400]
    assert "break" in _SRC[i:i + 400]


def test_the_price_is_recomputed_never_incremented():
    """The GO drew this line: ask L5 again and recompute, NOT pay another
    cent until filled. A price derived from the previous one is a
    fill-chaser, not a clone."""
    for chaser in ("px + tick", "px + spec", "last_px + ", "px += ",
                   "px = px +", "previous_px"):
        assert chaser not in _SRC, chaser
    i = _SRC.index('f"spring_complete_{actions + 1}", post_only=True)')
    assert "l5_touch" in _SRC[max(0, i - 5000):i]


def test_the_model_is_re_asked_every_iteration():
    """cancel/confirm -> fresh state -> ask L5 again -> repost."""
    i = _SRC.index("while actions < action_budget()")
    # A fixed window over the loop head, and it must clear the END of the
    # model call, not its start. The owner's probe stop conditions added
    # eleven lines to the loop head and pushed this past a 700-char slice,
    # failing a test whose subject had not changed; a 1600 window then cut
    # mid-token. Sized with headroom, and the guard below fails loudly with
    # "widen it" rather than letting a future insertion silently weaken what
    # this asserts.
    body = _SRC[i:i + 2400]
    assert "spring_proposal(" in body, (
        "the window no longer reaches the model call; widen it rather than "
        "weakening what it asserts")
    assert 'q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")' in body
    assert "spring_state_key(inv" in body
    assert "spring_proposal(model, inv, skey, skey_v1)" in body


def test_no_dollar_limit_moved():
    """Execution fidelity, not bankroll risk: per-action size, the
    per-market spend cap, concurrency 1, maker-only and the daily stop are
    untouched by every line this commit added."""
    added = [l for l in _SRC.splitlines()
             if "complete_actions" in l or "action_budget" in l
             or "complete_reject_streak" in l]
    assert added
    for money in ("target_notional", "max_leg_notional", "DAILY",
                  "SPRING_BAND", "cap_usd", "spend"):
        assert not any(money in l for l in added), money


def test_the_session_row_reports_the_budget():
    assert '"max_actions_open_extend": max_actions,' in _SRC
    assert '"max_actions_complete_extension": max_complete_actions,' in _SRC
