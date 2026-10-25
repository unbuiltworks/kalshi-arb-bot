"""The fill-horizon probe: arming, envelope, lifecycle, and every refusal.

The venue is injected throughout, so these exercise the real control flow of a
run that places orders without placing one.
"""
from decimal import Decimal as D

import pytest

from src import pmus_fill_horizon_probe as P

GOOD = P.Quote(yes_bid=D("0.45"), yes_bid_qty=D(900),
               yes_ask=D("0.46"), yes_ask_qty=D(100))


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(P.LIVE_ENV, "YES")
    monkeypatch.setenv(P.AUTH_ENV, P.EXPECTED_AUTH_ID)
    monkeypatch.setenv(P.EXPIRY_ENV, "2099-01-01T00:00:00Z")


class Venue:
    """A fake venue that fills on command, so the lifecycle is deterministic."""

    def __init__(self, *, fill_after_s=None, ack=True, order_id="V1",
                 terminal_state="CANCELLED"):
        self.fill_after_s = fill_after_s
        self.ack = ack
        self.order_id = order_id
        self.terminal_state = terminal_state
        self.placed, self.cancelled, self.polls = [], [], 0
        self.t = 1000.0

    # clock
    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.1, s)

    # transport
    def place(self, **kw):
        self.placed.append(kw)
        if not self.ack:
            return {"ok": False, "why": "rejected -1013"}
        return {"ok": True, "venue_order_id": self.order_id,
                "client_order_id": "C1"}

    def cancel(self, order):
        self.cancelled.append(order.venue_order_id)
        return {"ok": True}

    def _is_cancelled(self, order):
        return order.venue_order_id in self.cancelled

    def poll(self, order):
        self.polls += 1
        rested = self.t - order.placed_at
        if self.fill_after_s is not None and rested >= self.fill_after_s:
            return {"state": "FILLED", "filled_qty": str(order.qty)}
        if self._is_cancelled(order):
            return {"state": self.terminal_state, "filled_qty": "0"}
        return {"state": "OPEN", "filled_qty": "0"}

    def tte(self):
        return 600.0


def _run(venue, state=None, window="w1"):
    state = state or P.ProbeState()
    res = P.run_one_window(window=window, state=state, quote_fn=lambda: GOOD,
                           place_fn=venue.place, cancel_fn=venue.cancel,
                           poll_fn=venue.poll, tte_fn=venue.tte,
                           sleep_fn=venue.sleep, now_fn=venue.now)
    return state, res


# ------------------------------------------------------------------ arming
def test_nothing_is_reachable_unarmed(monkeypatch):
    monkeypatch.delenv(P.LIVE_ENV, raising=False)
    assert P.armed() is False
    with pytest.raises(P.NotArmed):
        _run(Venue())


def test_every_missing_key_is_named_precisely(monkeypatch):
    monkeypatch.delenv(P.LIVE_ENV, raising=False)
    assert P.why_not_armed() == f"{P.LIVE_ENV} is not YES"
    monkeypatch.setenv(P.LIVE_ENV, "YES")
    monkeypatch.delenv(P.AUTH_ENV, raising=False)
    assert P.why_not_armed() == f"{P.AUTH_ENV} is absent"
    monkeypatch.setenv(P.AUTH_ENV, "SOME-OTHER-PROBE")
    assert P.why_not_armed() == "authorization identifier does not match this build"
    monkeypatch.setenv(P.AUTH_ENV, P.EXPECTED_AUTH_ID)
    monkeypatch.delenv(P.EXPIRY_ENV, raising=False)
    assert "is absent" in P.why_not_armed()
    monkeypatch.setenv(P.EXPIRY_ENV, "2000-01-01T00:00:00Z")
    assert P.why_not_armed() == "the authorization has expired"
    monkeypatch.setenv(P.EXPIRY_ENV, "not-a-date")
    assert P.why_not_armed() == "the authorization has expired"


def test_an_authorization_without_an_end_is_refused(monkeypatch):
    monkeypatch.setenv(P.LIVE_ENV, "YES")
    monkeypatch.setenv(P.AUTH_ENV, P.EXPECTED_AUTH_ID)
    monkeypatch.delenv(P.EXPIRY_ENV, raising=False)
    assert P.expired() is None
    assert P.armed() is False


def test_preflight_is_safe_unarmed_and_says_what_is_missing(monkeypatch):
    monkeypatch.delenv(P.LIVE_ENV, raising=False)
    pf = P.preflight()
    assert pf["armed"] is False and pf["envelope_ok"] is True
    assert pf["expected_auth_id"] == P.EXPECTED_AUTH_ID


# ---------------------------------------------------------------- envelope
def test_the_envelope_bounds_its_own_worst_case_under_the_approved_ceiling():
    e = P.check_envelope()
    assert D(e["computed_worst_case_usd"]) <= D(e["approved_worst_case_usd"])
    assert e["maker_only"] is True and e["crosses_ever"] is False
    assert e["max_concurrent_orders"] == 1
    assert D(e["clip_contracts"]) == D(5)
    assert e["max_windows"] == 20


def test_a_widened_envelope_refuses_to_run(monkeypatch):
    monkeypatch.setattr(P, "MAX_WINDOWS", 1000)
    with pytest.raises(P.EnvelopeViolation):
        P.check_envelope()
    monkeypatch.setattr(P, "MAX_WINDOWS", 20)
    monkeypatch.setattr(P, "CLIP_CONTRACTS", D(500))
    with pytest.raises(P.EnvelopeViolation):
        P.check_envelope()


def test_more_than_one_resting_order_is_refused(monkeypatch):
    monkeypatch.setattr(P, "MAX_CONCURRENT_ORDERS", 2)
    with pytest.raises(P.EnvelopeViolation):
        P.check_envelope()


def test_a_run_that_could_not_answer_the_question_refuses(monkeypatch):
    """Every rest step inside the already-measured horizon would spend money to
    re-measure what we already know."""
    monkeypatch.setattr(P, "REST_STEPS_S", (10.0, 20.0, 30.0))
    with pytest.raises(P.EnvelopeViolation):
        P.check_envelope()


def test_the_rest_steps_actually_reach_past_the_known_horizon():
    assert max(P.REST_STEPS_S) > P.KNOWN_HORIZON_S
    assert P.L5_MEDIAN_COMPLEMENT_GAP_S in P.REST_STEPS_S or \
        max(P.REST_STEPS_S) > P.L5_MEDIAN_COMPLEMENT_GAP_S


def test_rest_steps_rotate_so_an_early_stop_still_samples_the_curve():
    got = [P.rest_target_for(i) for i in range(len(P.REST_STEPS_S) * 2)]
    assert set(got) == set(P.REST_STEPS_S)
    assert got[:4] == list(P.REST_STEPS_S)


# ----------------------------------------------------------- where to post
def test_it_posts_at_the_touch_on_the_deeper_side_and_never_crosses():
    plan = P.choose_post(GOOD, 60.0, 600.0)
    assert plan["post"] is True
    assert plan["side"] == "UP"                  # yes_bid_qty 900 > ask 100
    assert plan["price"] == D("0.45")            # the touch, not through it
    assert plan["qty"] == D(5)
    # the deeper side flips with the book
    other = P.Quote(yes_bid=D("0.45"), yes_bid_qty=D(10),
                    yes_ask=D("0.46"), yes_ask_qty=D(900))
    p2 = P.choose_post(other, 60.0, 600.0)
    assert p2["side"] == "DOWN" and p2["price"] == D("0.54")


def test_an_unreadable_or_one_sided_book_is_a_refusal_not_a_default():
    assert P.choose_post(P.Quote(None, None, None, None, readable=False),
                         60.0, 600.0)["reason"] == "BOOK_UNREADABLE"
    assert P.choose_post(P.Quote(yes_bid=None, yes_bid_qty=D(1),
                                 yes_ask=D("0.5"), yes_ask_qty=D(1)),
                         60.0, 600.0)["reason"] == "BOOK_UNREADABLE"
    crossed = P.Quote(yes_bid=D("0.60"), yes_bid_qty=D(1),
                      yes_ask=D("0.50"), yes_ask_qty=D(1))
    assert P.choose_post(crossed, 60.0, 600.0)["reason"] == "BOOK_NOT_TWO_SIDED"


def test_it_will_not_post_an_order_it_cannot_rest_to_target():
    assert P.choose_post(GOOD, 90.0, 100.0)["reason"] == "NOT_ENOUGH_TIME_TO_REST"
    assert P.choose_post(GOOD, 90.0, 600.0)["post"] is True


def test_it_does_not_measure_in_the_degenerate_price_band():
    longshot = P.Quote(yes_bid=D("0.02"), yes_bid_qty=D(900),
                       yes_ask=D("0.03"), yes_ask_qty=D(100))
    r = P.choose_post(longshot, 60.0, 600.0)
    assert r["reason"] == "PRICE_OUTSIDE_MEASUREMENT_BAND"


def test_choose_post_consults_no_probability_and_takes_no_view():
    """Checks the function's real references rather than its docstring, which
    legitimately explains that it takes no view."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(P.choose_post).lstrip())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"p_up", "prob", "Prob", "sigma", "oracle", "estimate",
                        "evaluate", "risk_adj_ev"}
    # and its signature cannot even accept one
    assert set(inspect.signature(P.choose_post).parameters) == {
        "q", "rest_target_s", "tte_s"}


# ------------------------------------------------------------- lifecycle
def test_a_fill_is_recorded_as_an_event_at_the_time_it_happened(armed):
    v = Venue(fill_after_s=20.0)
    state, res = _run(v)
    assert res["terminal"] == P.TERMINAL_FILLED
    obs = state.survival_rows()
    assert len(obs) == 1
    assert obs[0]["event"] is True and obs[0]["censored"] is False
    assert 20.0 <= obs[0]["rest_s"] < 30.0
    assert len(v.placed) == 1 and v.placed[0]["qty"] == D(5)


def test_an_unfilled_order_is_cancelled_and_CENSORED_not_a_no_fill(armed):
    v = Venue(fill_after_s=None)
    state, res = _run(v)
    assert res["terminal"] == P.TERMINAL_CANCELLED
    assert v.cancelled == ["V1"]
    obs = state.survival_rows()[0]
    assert obs["censored"] is True and obs["event"] is False
    assert obs["rest_s"] >= 30.0


def test_a_partial_fill_is_its_own_terminal(armed):
    v = Venue()
    v.poll = lambda o: {"state": "OPEN", "filled_qty": "2"}
    state, res = _run(v)
    assert res["terminal"] == P.TERMINAL_PARTIAL
    assert state.orders[0].filled_qty == D(2)
    assert state.survival_rows()[0]["event"] is True


def test_observations_past_the_known_horizon_are_flagged(armed):
    v = Venue(fill_after_s=None)
    state = P.ProbeState()
    # window index 3 -> the 90s step, which is past both 32.2s and L5's 47s
    state.windows_used = 3
    _run(v, state, window="w4")
    obs = state.survival_rows()[0]
    assert obs["rest_target_s"] == 90.0
    assert obs["past_known_horizon"] is True
    assert state.report()["observations_past_known_horizon"] == 1


def test_a_rejected_order_is_a_hard_stop(armed):
    v = Venue(ack=False)
    state = P.ProbeState()
    with pytest.raises(P.HardStop):
        _run(v, state)
    assert state.stopped == "PLACE_REJECTED"
    assert state.submissions == 0


def test_an_ack_without_an_order_id_is_a_hard_stop(armed):
    """An order we cannot name is an order we cannot cancel."""
    v = Venue(order_id=None)
    state = P.ProbeState()
    with pytest.raises(P.HardStop):
        _run(v, state)
    assert state.stopped == "ACK_WITHOUT_ORDER_ID"


def test_an_unexplained_terminal_is_a_hard_stop(armed):
    v = Venue(fill_after_s=None, terminal_state="SOMETHING_WEIRD")
    state = P.ProbeState()
    with pytest.raises(P.HardStop):
        _run(v, state)
    assert state.stopped == "UNKNOWN_TERMINAL"
    assert "unexplained" in state.why


def test_a_skipped_window_still_counts_against_the_budget(armed):
    """Otherwise an unreadable market could be retried forever."""
    v = Venue()
    state = P.ProbeState()
    res = P.run_one_window(window="w", state=state,
                           quote_fn=lambda: P.Quote(None, None, None, None,
                                                    readable=False),
                           place_fn=v.place, cancel_fn=v.cancel, poll_fn=v.poll,
                           tte_fn=v.tte, sleep_fn=v.sleep, now_fn=v.now)
    assert res["placed"] is False and res["reason"] == "BOOK_UNREADABLE"
    assert state.windows_used == 1 and state.submissions == 0
    assert not v.placed


# ----------------------------------------------------------------- budget
def test_the_run_stops_at_the_window_cap(armed):
    state = P.ProbeState(windows_used=P.MAX_WINDOWS)
    assert state.admits_another_window() == "MAX_WINDOWS"
    v = Venue()
    _, res = _run(v, state)
    assert res["placed"] is False and res["reason"] == "MAX_WINDOWS"
    assert not v.placed


def test_the_run_stops_at_the_submission_cap(armed):
    state = P.ProbeState(submissions=P.MAX_SUBMISSIONS)
    assert state.admits_another_window() == "MAX_SUBMISSIONS"


def test_a_hard_stop_prevents_any_further_window(armed):
    state = P.ProbeState()
    state.stopped = "UNKNOWN_TERMINAL"
    v = Venue()
    _, res = _run(v, state)
    assert res["placed"] is False
    assert not v.placed


def test_committed_worst_case_never_exceeds_the_approved_ceiling(armed):
    """Walk the full 20-window budget and check the bound empirically."""
    state = P.ProbeState()
    for i in range(P.MAX_WINDOWS + 5):
        v = Venue(fill_after_s=1.0)          # fill everything: the worst case
        v.t = 1000.0 + i * 1000
        if state.admits_another_window():
            break
        _run(v, state, window=f"w{i}")
    assert state.submissions <= P.MAX_SUBMISSIONS
    assert state.worst_case_committed_usd() <= P.APPROVED_WORST_CASE_USD


# ----------------------------------------------------------------- report
def test_the_report_is_kaplan_meier_ready_and_pools_with_the_existing_965(armed):
    state = P.ProbeState()
    for i in range(4):
        v = Venue(fill_after_s=(5.0 if i % 2 == 0 else None))
        v.t = 1000.0 + i * 1000
        _run(v, state, window=f"w{i}")
    rep = state.report()
    assert rep["observations"] == 4
    assert set(rep["by_rest_step"]) == {"30.0", "45.0", "60.0", "90.0"}
    for row in rep["survival_rows"]:
        # the same convention the existing calibration uses, so points pool
        assert row["event"] is not row["censored"]
        assert "rest_s" in row and "rest_target_s" in row
    assert "not_a_pnl_run" in rep["labels"]
    assert D(rep["worst_case_committed_usd"]) <= P.APPROVED_WORST_CASE_USD


def test_terminal_classification_never_guesses():
    assert P.classify_terminal("OPEN", 0, D(5)) == P.TERMINAL_UNKNOWN
    assert P.classify_terminal("LIVE", None, D(5)) == P.TERMINAL_UNKNOWN
    assert P.classify_terminal("", None, D(5)) == P.TERMINAL_UNKNOWN
    assert P.classify_terminal("WAT", 0, D(5)) == P.TERMINAL_UNKNOWN
    assert P.classify_terminal("CANCELLED", 0, D(5)) == P.TERMINAL_CANCELLED
    assert P.classify_terminal("EXPIRED", 0, D(5)) == P.TERMINAL_CANCELLED
    assert P.classify_terminal("FILLED", 5, D(5)) == P.TERMINAL_FILLED
    assert P.classify_terminal("CANCELLED", 3, D(5)) == P.TERMINAL_PARTIAL
    assert P.classify_terminal("OPEN", "bogus", D(5)) == P.TERMINAL_UNKNOWN


def test_the_module_entry_point_places_nothing():
    """The CLI is preflight only; wiring to the live transport is a separate
    reviewable change on the service that holds the credentials."""
    import inspect
    src = inspect.getsource(P.main)
    assert "run_one_window" not in src
    assert "preflight" in src
