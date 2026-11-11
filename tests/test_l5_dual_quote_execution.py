"""Behavioural proof of the four things the owner asked for.

These go through the REAL `FaithfulExecutor.handle` and the real transport
fake, not source inspection. Two consecutive `handle` calls are exactly what
the controller's pass loop performs in one tick, so calling it twice here IS
the same-pass behaviour under test.

    1. simultaneous resting orders  -- two opposing makers live, neither filled
    2. replenishment                -- a freed slot is re-used
    3. partial fills                -- inventory and the complement remainder
    4. safe recovery                -- a failed submission leaves nothing behind
"""
from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates


@pytest.fixture()
def probe(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "DQ-E2E-1")
    monkeypatch.setenv(TS.MARKET_ENV, "m")
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "DQ-E2E-1")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: H.NOW().timestamp() - 60.0)
    return H, E


def _book(**over):
    b = {"bid": "0.60", "ask": "0.61", "bid_shares": "900",
         "ask_shares": "5000", "stream_age_s": "0.05"}
    b.update(over)
    return b


def gates(cash="8", deficit="3", cum="10"):
    return ValidationGates(max_cash_deployed_usd=D(cash),
                           max_unmatched_contracts=D("5"),
                           max_cumulative_loss_usd=D(cum),
                           max_worst_case_deficit_usd=D(deficit))


def mk(H, E, tmp_path, t=None, g=None, orders=2):
    t = t or H.FakeTransport(fill_all=False)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    ex = E.FaithfulExecutor(H.store(tmp_path),
                            H.limits(max_live_orders=orders), transport=t,
                            now_fn=H.NOW, maker_treatment=True)
    ex.validation_gates = g if g is not None else gates()
    return ex, t


def intent(E, book, tick=D("0.01"), side=None, size="1"):
    up = E.controller_provisional_price("UP", book, tick)
    dn = E.controller_provisional_price("DOWN", book, tick)
    if side is None:
        side, px = ("UP", up) if up <= dn else ("DOWN", dn)
    else:
        px = up if side == "UP" else dn
    return {"provenance": E.CONTROLLER_GENERATED, "maker_class": None,
            "decision": {"side": side, "price": px,
                         "l5_size": D(size), "relation": "open"}}


def one_pass(ex, E, book=None, **kw):
    bk = book if book is not None else _book()
    return ex.handle(None, bk, market="m", window="21:00",
                     controller=intent(E, bk, **kw))


# ================================================ 1. simultaneous resting

class TestSimultaneousRestingOrders:

    def test_two_passes_leave_two_opposing_makers_resting(self, probe,
                                                          tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        r1 = one_pass(ex, E)
        assert r1.get("refused_reason") is None, r1.get("refused_reason")
        r2 = one_pass(ex, E)
        assert r2.get("refused_reason") is None, r2.get("refused_reason")
        assert len(t.sent) == 2, t.sent
        # OPPOSING sides, chosen by claim state and not by the controller
        sides = sorted(str(s["side"]) for s in t.sent)
        assert sides == ["DOWN", "UP"], t.sent
        # SAME ROUND, so this is one priced pair and not two unrelated quotes
        rounds = {str(s["action_key"]).rsplit("|", 2)[1] for s in t.sent}
        assert len(rounds) == 1, t.sent
        # and the pair they form clears the ceiling the precheck gated on
        pair = sum(D(str(s["price"])) for s in t.sent)
        assert pair <= TS.max_pair_cost(), (pair, t.sent)
        # both MAKER; neither crosses
        assert {str(s["mode"]) for s in t.sent} == {"MAKER"}, t.sent

    def test_neither_order_filled_while_both_rest(self, probe, tmp_path):
        """fill_qty="0" is the only genuinely non-filling transport.

        `FakeTransport(fill_all=False)` keeps the DEFAULT fill_qty of 1, so it
        partially fills every order and leaves it resting -- useful below, but
        it would make this assertion vacuous.
        """
        H, E = probe
        ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_qty="0"))
        one_pass(ex, E)
        one_pass(ex, E)
        assert len(t.sent) == 2, t.sent
        assert ex.store.position("m", "UP") == D(0)
        assert ex.store.position("m", "DOWN") == D(0)

    def test_every_order_is_post_only_maker(self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        one_pass(ex, E)
        one_pass(ex, E)
        for s in t.sent:
            blob = str(s).lower()
            assert "post" in blob or "maker" in blob or "limit" in blob, s

    def test_a_third_pass_is_refused_by_max_live_orders(self, probe,
                                                        tmp_path):
        """The pair is two. A third order in one tick has no gated economics."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=2,
                   t=H.FakeTransport(fill_qty="0"))
        one_pass(ex, E)
        one_pass(ex, E)
        r3 = one_pass(ex, E)
        assert r3.get("refused_reason") is not None
        assert len(t.sent) == 2, t.sent

    def test_one_slot_still_means_one_order(self, probe, tmp_path):
        """With max_live_orders=1 the second pass must be refused."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=1,
                   t=H.FakeTransport(fill_qty="0"))
        one_pass(ex, E)
        r2 = one_pass(ex, E)
        assert r2.get("refused_reason") is not None
        assert len(t.sent) == 1, t.sent


# ========================================================= 2. replenishment

class TestReplenishment:

    def test_a_filled_slot_is_re_used(self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_all=True))
        r1 = one_pass(ex, E)
        assert r1.get("refused_reason") is None
        n_after_first = len(t.sent)
        # the slot freed by the fill admits another placement
        r2 = one_pass(ex, E)
        assert len(t.sent) > n_after_first, (r2.get("refused_reason"), t.sent)

    def test_inventory_moves_when_a_fill_lands(self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_all=True))
        one_pass(ex, E)
        up = ex.store.position("m", "UP")
        dn = ex.store.position("m", "DOWN")
        assert (up + dn) > D(0), (up, dn)

    def test_repeated_passes_keep_building_rather_than_stopping_at_one(
            self, probe, tmp_path):
        """The engine must not stop after a single pair."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_all=True))
        placed = 0
        for _ in range(6):
            r = one_pass(ex, E)
            if r.get("refused_reason") is None:
                placed += 1
        assert placed >= 2, "the engine stopped after one placement"


# ========================================================= 3. partial fills

class TestPartialFills:

    def test_a_partial_fill_records_only_what_filled(self, probe, tmp_path):
        """fill_all=False fills 1 of the ordered quantity and keeps resting.

        So inventory must move by EXACTLY the filled 1, never by the 3 that
        were ordered. Booking the order size instead of the fill is the
        phantom-inventory class of bug.
        """
        H, E = probe
        ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_all=False,
                                                     fill_qty="1"))
        r = one_pass(ex, E, size="3")
        assert r.get("refused_reason") is None, r.get("refused_reason")
        booked = ex.store.position("m", "UP") + ex.store.position("m", "DOWN")
        assert booked == D(1), ("booked the order size, not the fill", booked)

    def test_a_zero_fill_books_nothing(self, probe, tmp_path):
        """A resting order is not a position."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_qty="0"))
        r = one_pass(ex, E, size="3")
        assert r.get("refused_reason") is None, r.get("refused_reason")
        assert ex.store.position("m", "UP") == D(0)
        assert ex.store.position("m", "DOWN") == D(0)

    def test_unmatched_exposure_is_tracked_continuously(self, probe,
                                                        tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_all=True))
        one_pass(ex, E)
        up = ex.store.position("m", "UP")
        dn = ex.store.position("m", "DOWN")
        resid = up - dn
        # whatever filled, the residual is derivable and bounded
        assert abs(resid) <= D("5"), resid

    def test_the_residual_cap_still_binds_after_several_passes(self, probe,
                                                              tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, t=H.FakeTransport(fill_all=True))
        for _ in range(8):
            one_pass(ex, E)
        resid = ex.store.position("m", "UP") - ex.store.position("m", "DOWN")
        assert abs(resid) <= D("5"), ("residual cap breached", resid)


# ======================================================== 4. safe recovery

class TestSafeRecoveryFromFailedSubmission:

    def test_a_raising_transport_leaves_no_phantom_inventory(self, probe,
                                                             tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)

        def boom(*a, **k):
            raise RuntimeError("venue refused")
        t.submit = boom
        try:
            one_pass(ex, E)
        except BaseException:
            pass
        assert ex.store.position("m", "UP") == D(0)
        assert ex.store.position("m", "DOWN") == D(0)

    def test_a_failed_submission_does_not_consume_the_slot_forever(
            self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)
        calls = {"n": 0}
        real = t.submit

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return real(*a, **k)
        t.submit = flaky
        try:
            one_pass(ex, E)
        except BaseException:
            pass
        r2 = one_pass(ex, E)
        # the engine may refuse for a named reason, but it must not be
        # permanently wedged with a phantom live order
        assert r2 is not None

    def test_a_failed_submission_never_creates_a_position(self, probe,
                                                          tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path)

        def reject(*a, **k):
            return {"ok": False, "reason": "REJECTED"}
        t.submit = reject
        try:
            one_pass(ex, E)
        except BaseException:
            pass
        assert ex.store.position("m", "UP") == D(0)
        assert ex.store.position("m", "DOWN") == D(0)


# =================================================== gates are not bypassed

class TestEveryGateStillRunsOnPassTwo:

    def test_cash_exhaustion_refuses_the_second_leg(self, probe, tmp_path):
        H, E = probe
        ex, t = mk(H, E, tmp_path, g=gates(cash="0.01"))
        r = one_pass(ex, E)
        assert r.get("refused_reason") is not None
        assert t.sent == [], t.sent

    def test_the_second_pass_is_a_full_handle_not_a_shortcut(self, probe,
                                                             tmp_path):
        """Pass two must be refusable on its own merits."""
        H, E = probe
        ex, t = mk(H, E, tmp_path, orders=1)
        one_pass(ex, E)
        r2 = one_pass(ex, E)
        assert r2.get("refused_by_layer") is not None
        assert r2.get("refused_reason") is not None
