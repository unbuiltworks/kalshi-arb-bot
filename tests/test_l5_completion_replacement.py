"""A cancelled-unfilled leg must be re-placed, so inventory is never left
unattended for the rest of the window.

THE DEFECT, 1445z 2026-10-30. `acc_leg_attempt` already granted a later
placement over a leg holding nothing with nothing live, and
`probe_legs_claimed` already reported such a leg unspent so the chooser
re-offered it. But `handle` built the client order id at attempt ZERO
regardless, so the permitted re-placement landed on the FIRST placement's id
and was suppressed as a duplicate BEFORE the claim was reached. The
`L5_ACC_MAX_ATTEMPTS` budget was therefore unreachable in practice.

What it cost: the 1.91 DOWN leg sat with `order_resting: false` for the final
12 minutes of the window and settled unmatched for -$0.706700.

Drives the REAL FaithfulExecutor, because the lesson on this path is that a
mechanism which exists is not a mechanism that is wired.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates


@pytest.fixture()
def probe(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "TS-REPLACE-1")
    monkeypatch.setenv(TS.MARKET_ENV, "m")
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "TS-REPLACE-1")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: H.NOW().timestamp() - 60.0)
    return H, E


_SEQ = [0]


def _row(H, **over):
    _SEQ[0] += 1
    kw = dict(bid="0.60", ask="0.61", l5px="0.60", side="DOWN",
              key="rep-%d" % _SEQ[0])
    kw.update(over)
    return H.row(**kw)


def _book(**over):
    b = {"bid": "0.60", "ask": "0.61", "bid_shares": "900",
         "ask_shares": "5000", "stream_age_s": "0.05"}
    b.update(over)
    return b


def mk(H, E, tmp_path, fill_qty="0", orders=2):
    # fill_qty="0" is an order that RESTS AND DOES NOT FILL, which is the
    # state under test: the default double acks 1 filled, and a leg holding
    # filled quantity may never be re-placed.
    t = H.FakeTransport(fill_qty=fill_qty)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    ex = E.FaithfulExecutor(H.store(tmp_path),
                            H.limits(max_live_orders=orders), transport=t,
                            now_fn=H.NOW, maker_treatment=True)
    ex.validation_gates = ValidationGates(
        max_cash_deployed_usd=D("8"), max_unmatched_contracts=D("5"),
        max_cumulative_loss_usd=D("10"),
        max_worst_case_deficit_usd=D("3"))
    return ex, t


def _ctl(E, book, side=None, size="3"):
    """The controller intent the timer builds, as `maybe_controller_open`
    does. The action key it leads to is keyed on (market, round, leg) and
    NOT on the clock -- which is exactly why a re-placement needs the
    attempt axis to reach the wire at all."""
    up = E.controller_provisional_price("UP", book, D("0.01"))
    dn = E.controller_provisional_price("DOWN", book, D("0.01"))
    if side is None:
        side, px = ("UP", up) if up <= dn else ("DOWN", dn)
    else:
        px = up if side == "UP" else dn
    return {"provenance": E.CONTROLLER_GENERATED, "maker_class": None,
            "decision": {"side": side, "price": px,
                         "l5_size": D(size), "relation": "open"}}


def tick(ex, E, book=None, **kw):
    bk = book if book is not None else _book()
    return ex.handle(None, bk, market="m", window="21:00",
                     controller=_ctl(E, bk, **kw))


def _cancel_everything_unfilled(ex):
    """What a venue-confirmed cancel at cum 0 leaves behind: no live order
    and no filled quantity. Nothing is booked, because nothing filled."""
    ex.store.state["live_orders"].clear()
    ex.store.save()
    assert ex.store.live_order_count() == 0


# ----------------------------------------------------------- the derived id

def test_attempt_zero_keeps_its_id_byte_for_byte():
    """Claims already on the durable volume must not be handed back as
    unspent, which is why `accumulation_sid` keeps attempt zero's key too."""
    k = LX.controller_action_key("m", 0, "UP")
    assert LX.client_order_id(k, 0) == LX.client_order_id(k)


def test_a_later_placement_gets_a_different_id():
    k = LX.controller_action_key("m", 0, "UP")
    ids = [LX.client_order_id(k, a) for a in range(4)]
    assert len(set(ids)) == 4


def test_the_id_is_still_derived_so_a_replay_is_still_a_duplicate():
    k = LX.controller_action_key("m", 0, "UP")
    assert LX.client_order_id(k, 2) == LX.client_order_id(k, 2)


# ------------------------------------------------- the re-placement happens

def test_a_cancelled_unfilled_controller_leg_is_replaced_in_the_SAME_round(
        probe, tmp_path):
    """THE 1445z SHAPE, on the path the defect actually lives on.

    The controller's action key is keyed on (market, round, leg) and not on
    the clock, so every tick after the first produces the SAME key. That is
    deliberate -- it is what stops the timer stacking orders -- but it also
    means a cancelled-unfilled leg can only be re-placed if the coid carries
    the attempt the axis granted. Without that it lands on the first
    placement's id and is suppressed, which is how 1.91 DOWN came to sit for
    12 minutes with nothing resting.
    """
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    tick(ex, E)
    tick(ex, E)
    first = [x["client_order_id"] for x in t.sent]
    assert len(first) == 2, first
    # both legs of round 0 are now claimed at attempt 0 and both rest
    assert ex.probe_legs_claimed("m", 0) == {"UP": True, "DOWN": True}

    # ten more ticks change nothing while they rest: the deterministic key
    # is doing its job and this fix must not weaken it
    for _ in range(10):
        tick(ex, E)
    assert len(t.sent) == 2, t.sent

    # a venue-confirmed cancel at cum 0 on both
    _cancel_everything_unfilled(ex)
    assert ex.store.position("m", "UP") == D(0)
    assert ex.store.position("m", "DOWN") == D(0)
    assert ex.acc_leg_attempt("m", 0, "UP") == 1
    assert ex.acc_leg_attempt("m", 0, "DOWN") == 1
    assert ex.probe_round("m") == 0        # the round is live again

    # the very next tick must quote AGAIN rather than leave it unattended
    tick(ex, E)
    assert len(t.sent) == 3, (
        "a cancelled-unfilled controller leg was not re-placed: this is the "
        "1445z defect, where nothing rested for the last 12 minutes")
    third = t.sent[2]
    assert third["client_order_id"] not in first
    # SAME round, SAME leg namespace, attempt ONE -- not a new pair
    assert third["client_order_id"] == LX.client_order_id(
        E.controller_action_key("m", 0, str(third["side"])), 1)


def test_the_l5_row_path_never_had_this_defect(probe, tmp_path):
    """Recorded so the two paths are not conflated. An L5 action key carries
    the action's own identity, so each admitted action yields a different
    coid and a re-quote was never a duplicate. The defect is specific to the
    clock-independent controller key."""
    H, E = probe
    a = LX.client_order_id("txhash-A|BTC|UP|0.60|3|1793371500")
    b = LX.client_order_id("txhash-B|BTC|UP|0.60|3|1793371560")
    assert a != b
    k = E.controller_action_key("m", 0, "UP")
    assert LX.client_order_id(k) == LX.client_order_id(k)


def test_the_replacement_is_bounded_by_the_attempt_budget(probe, tmp_path,
                                                          monkeypatch):
    H, E = probe
    monkeypatch.setenv(AUTH.ACC_MAX_ATTEMPTS_ENV, "2")
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    side = t.sent[0]["side"]
    _cancel_everything_unfilled(ex)
    assert ex.acc_leg_attempt("m", 0, side) == 1
    ex.handle(_row(H), _book(), market="m", window="21:00")
    _cancel_everything_unfilled(ex)
    # budget of 2 is now spent for this leg: no third placement
    assert ex.acc_leg_attempt("m", 0, side) is None


# ------------------------------------------------ what must NEVER be reused

def test_a_filled_leg_is_never_replaced(probe, tmp_path):
    """The safety rule the attempt axis exists to enforce. A leg holding
    filled quantity can never be re-placed by any path."""
    H, E = probe
    ex, t = mk(H, E, tmp_path, fill_qty="1")
    ex.handle(_row(H), _book(), market="m", window="21:00")
    side = t.sent[0]["side"]
    _cancel_everything_unfilled(ex)
    assert ex.store.position("m", side) != D(0)
    assert ex.acc_leg_attempt("m", 0, side) is None


def test_a_live_order_blocks_a_replacement(probe, tmp_path):
    """An unverified cancellation leaves the order live here, and a leg whose
    cancel was never venue-confirmed must not buy a retry."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    side = t.sent[0]["side"]
    assert ex.store.live_order_count() >= 1
    assert ex.acc_leg_attempt("m", 0, side) is None


def test_the_replacement_never_exceeds_the_one_order_limit(probe, tmp_path):
    """Concurrency is still bounded by max_live_orders, not by the attempt
    axis: a re-placement adds no slot."""
    H, E = probe
    ex, t = mk(H, E, tmp_path)
    ex.limits = H.limits(max_live_orders=1)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    n = len(t.sent)
    ex.handle(_row(H), _book(), market="m", window="21:00")
    assert len(t.sent) == n, "a second order rested under a one-order limit"


def test_the_coid_and_the_claim_use_the_same_attempt():
    """If they disagreed, either a permitted placement is suppressed as a
    duplicate (the 1445z defect) or a claim is spent under an id that never
    reached the wire."""
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    i_coid = src.index("coid = client_order_id(")
    assert "acc_leg_attempt" in src[:i_coid]
    assert "client_order_id(key, _att)" in src
    # the claim site takes the same index from the same function
    assert "attempt=att" in src
    assert "self.acc_leg_attempt(market, rnd_pending" in src


# ======================= the retry identity cannot buy anything but an id
#
# The attempt axis changes ONE thing: the client order id. It must not become
# a way around the per-leg budget, an outstanding order, a claim, an exposure
# ceiling or the daily-loss halt. Each is checked here rather than argued.

class TestTheRetryIdentityBypassesNothing:

    @property
    def src(self):
        import inspect
        return inspect.getsource(LX.FaithfulExecutor.handle)

    def test_every_gate_runs_before_the_submission_boundary(self):
        """The structural reason no gate can be bypassed. The id is only a
        NAME: it is chosen mid-path, and every gate -- those before it and
        those after -- still runs before `record_submit`, which is the
        boundary the wire sits behind. So a different id changes which
        order this is, never whether it is allowed."""
        s = self.src
        i_rec = s.index("self.store.record_submit(")
        for gate in ("validation_gates", "limits.check", "probe_precheck",
                     "realized_current_utc_day", "DAY_REALIZED_UNAVAILABLE",
                     "balancing_refusal", "pair_cost_refusal"):
            pos = s.find(gate)
            assert 0 <= pos < i_rec, (gate, pos, i_rec)

    def test_the_gates_after_the_id_still_precede_the_wire(self):
        """`balancing_refusal` and `pair_cost_refusal` are chosen after the
        id exists. Pinned so a future reader does not mistake that for them
        being skippable."""
        s = self.src
        i_coid = s.index("coid = client_order_id(")
        i_rec = s.index("self.store.record_submit(")
        for gate in ("balancing_refusal", "pair_cost_refusal"):
            pos = s.find(gate)
            assert i_coid < pos < i_rec, gate

    def test_the_claim_is_taken_after_the_id_and_under_the_same_attempt(self):
        s = self.src
        i = s.index("coid = client_order_id(")
        after = s[i:]
        assert "claim_accumulation_leg" in after
        assert "attempt=att" in after

    def test_an_outstanding_order_cannot_be_duplicated(self, probe, tmp_path):
        """A live order's own id is still refused as a duplicate, which is
        what stops a retry becoming a second copy of a resting order."""
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        tick(ex, E)
        coid = t.sent[0]["client_order_id"]
        assert ex.store.already_submitted(coid) is True
        # and the venue double would reject it too, so neither layer alone
        # is carrying this
        import pytest as _p
        with _p.raises(RuntimeError):
            t.submit({"client_order_id": coid, "market": "m", "side": "UP",
                      "price": D("0.40"), "qty": D("1")})

    def test_a_retry_cannot_exceed_the_per_leg_attempt_budget(self, probe,
                                                              tmp_path,
                                                              monkeypatch):
        """With a budget of ONE placement per leg, BOTH legs spent and both
        cancelled unfilled, no retry may go out -- and the round is then
        genuinely exhausted rather than silently re-placed."""
        H, E = probe
        monkeypatch.setenv(AUTH.ACC_MAX_ATTEMPTS_ENV, "1")
        ex, t = mk(H, E, tmp_path)
        tick(ex, E)
        tick(ex, E)
        assert len(t.sent) == 2, t.sent
        _cancel_everything_unfilled(ex)
        for leg in ("UP", "DOWN"):
            assert ex.acc_leg_attempt("m", 0, leg) is None, leg
        # anything placed now must be a NEW ROUND at attempt 0, never a
        # second placement of a spent round-0 leg. Accumulation opening its
        # next round is the engine working, not a retry past the budget.
        tick(ex, E)
        forbidden = {LX.client_order_id(
            E.controller_action_key("m", 0, leg), 1)
            for leg in ("UP", "DOWN")}
        placed = {x["client_order_id"] for x in t.sent}
        assert not (placed & forbidden), (
            "a round-0 leg was re-placed past its attempt budget")
        if len(t.sent) > 2:
            third = t.sent[2]
            assert third["client_order_id"] == LX.client_order_id(
                E.controller_action_key("m", 1, str(third["side"])), 0)

    def test_a_retry_is_still_refused_by_the_cash_gate(self, probe, tmp_path):
        """The exposure ceiling is not a function of the id."""
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        tick(ex, E)
        _cancel_everything_unfilled(ex)
        assert ex.acc_leg_attempt("m", 0, t.sent[0]["side"]) == 1
        # now make the cash gate unsatisfiable and retry
        from src.l5_live_validation_gates import ValidationGates
        ex.validation_gates = ValidationGates(
            max_cash_deployed_usd=D("0.01"),
            max_unmatched_contracts=D("5"),
            max_cumulative_loss_usd=D("10"),
            max_worst_case_deficit_usd=D("3"))
        n = len(t.sent)
        rec = tick(ex, E)
        assert len(t.sent) == n, rec.get("refused_reason")
        assert rec.get("refused_reason") is not None

    def test_a_retry_is_still_refused_when_the_day_figure_is_unreadable(self):
        """DAY_REALIZED_UNAVAILABLE refuses rather than reading as zero, and
        it sits before the id is built."""
        s = self.src
        i = s.index("coid = client_order_id(")
        assert "DAY_REALIZED_UNAVAILABLE" in s[:i]

    def test_the_attempt_comes_from_the_axis_and_is_never_computed_here(self):
        """The safety rule lives in one place. If this counted attempts
        itself it could disagree with the claim that bounds them."""
        s = self.src
        i = s.index("coid = client_order_id(")
        window = s[max(0, i - 1200):i]
        assert "self.acc_leg_attempt(market, _rp, _lp)" in window
        for forbidden in ("_att += ", "_att = _att", "range("):
            assert forbidden not in window, forbidden

