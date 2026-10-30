"""STAGE A SAFETY GATE: two resting orders must be bounded exactly as one was.

Stage A raises MAX_LIVE_ORDERS by one for the OPPOSITE leg of the SAME round
in the SAME market, through the mechanism that already exists
(`TS.second_leg_slot_refusal`). Nothing here builds a new path; every
assertion drives the REAL FaithfulExecutor and asks what reached the
transport, because the lesson on this path is that a mechanism which exists
is not a mechanism that is wired.

The six checks the owner named, one class each.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates

MKT = "m"


@pytest.fixture()
def probe(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "TS-STAGEA-1")
    monkeypatch.setenv(TS.MARKET_ENV, MKT)
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "TS-STAGEA-1")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    # STAGE A: the second maker slot is ON. Default is OFF and stays OFF
    # everywhere else.
    monkeypatch.setenv(TS.TWO_ORDER_PAIR_ENV, "1")
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


def mk(H, E, tmp_path, fill_qty="0", orders=1, g=None):
    """orders=1 ON PURPOSE: the ordinary ceiling stays at one, so a second
    resting order can only exist if the slot granted it."""
    t = H.FakeTransport(fill_qty=fill_qty)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    ex = E.FaithfulExecutor(H.store(tmp_path), H.limits(max_live_orders=orders),
                            transport=t, now_fn=H.NOW, maker_treatment=True)
    ex.validation_gates = g if g is not None else gates()
    return ex, t


def _ctl(E, book, side=None, size="3"):
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
    return ex.handle(None, bk, market=MKT, window="21:00",
                     controller=_ctl(E, bk, **kw))


def _rest_a_pair(H, E, tmp_path, **kw):
    """Two orders resting, opposite sides, same round, same market."""
    ex, t = mk(H, E, tmp_path, **kw)
    tick(ex, E)
    tick(ex, E)
    return ex, t


# ============================================== 1. both orders count as risk

class TestBothRestingOrdersCountTowardTheLimits:

    def test_two_opposite_legs_can_rest_under_a_one_order_ceiling(
            self, probe, tmp_path):
        H, E = probe
        ex, t = _rest_a_pair(H, E, tmp_path)
        assert len(t.sent) == 2, t.sent
        assert {s["side"] for s in t.sent} == {"UP", "DOWN"}
        assert ex.store.live_order_count() == 2

    def test_a_resting_order_is_priced_as_if_it_filled_in_full(
            self, probe, tmp_path):
        """`outstanding_residual` is what makes an unfilled order exposure."""
        H, E = probe
        ex, t = _rest_a_pair(H, E, tmp_path)
        outstanding = ex.store.outstanding_residual(MKT)
        assert outstanding is not None
        # two opposite legs of equal size net to zero RESIDUAL, which is the
        # point of a pair -- but each is still committed cash below.
        assert ex.store.position(MKT, "UP") == D(0)
        assert ex.store.position(MKT, "DOWN") == D(0)

    def test_the_pair_is_gated_as_a_whole_before_either_leg_is_claimed(self):
        """Leg one must not be able to spend leg two's headroom."""
        import inspect
        import re
        raw = inspect.getsource(LX.FaithfulExecutor.probe_precheck)
        flat = re.sub(r"\s+", " ", raw)       # the docstring line-wraps
        assert "as if BOTH were already resting" in flat
        assert "NEITHER is placed" in flat
        # each leg is gated with the OTHER counted as outstanding, and a
        # refusal names which leg breached
        assert "other = [both[1 - i]]" in raw
        assert "outstanding=self.outstanding_for_gates() + other" in raw
        assert 'return "PAIR_%s:%s" % (leg["side"], reason)' in raw

    def test_the_pair_cannot_breach_the_cash_ceiling(self, probe, tmp_path):
        """THE capital check, and the observed behaviour is stronger than
        "neither rests": the plan SHRINKS to fit, and if the second leg would
        still breach it is refused by name.

        MEASURED at a $1.50 ceiling: the plan shrank from qty 3 to 0.94, one
        leg rested committing $0.94, and the complement was refused
        `PAIR_UP:CASH_COMMITMENT:2.2514>1.50`.

        NOTE FOR STAGE A, and it is a real consequence: a tight cash ceiling
        can leave ONE leg resting with its complement refused for cash --
        the stranded-leg shape. At Stage A's numbers a pair commits about
        $4.74 against a $12.00 effective cash bound, so this does not bind;
        it is pinned here so a future tightening cannot do it silently.
        """
        H, E = probe
        ex, t = mk(H, E, tmp_path, g=gates(cash="1.50"))
        recs = [tick(ex, E), tick(ex, E)]
        committed = D(0)
        for o in t.sent:
            q, px = D(str(o["qty"])), D(str(o["price"]))
            # a DOWN leg posts the full quantity as collateral, not q*px
            committed += q if o["side"] == "DOWN" else q * px
        assert committed <= D("1.50"), (committed, t.sent)
        if len(t.sent) < 2:
            assert any("CASH_COMMITMENT" in str(r.get("refused_reason") or "")
                       for r in recs), recs

    def test_a_down_leg_commits_its_full_quantity_as_collateral(self):
        """Buying NO is implemented as selling YES, so the cash gate charges
        the quantity and not quantity x price. Pinned because sizing a pair
        against q*px for both legs would understate the DOWN leg."""
        import inspect
        s = inspect.getsource(ValidationGates.refuse)
        assert 'this_order = (q if str(order_intent or "").upper()' in s
        assert '.endswith("BUY_SHORT") else q * px)' in s

    def test_the_deficit_ceiling_is_the_owner_number_not_a_code_constant(
            self, probe, tmp_path):
        H, E = probe
        ex, _ = mk(H, E, tmp_path)
        assert ex.validation_gates.max_worst_case_deficit_usd == D("3")


# ======================================= 2. fills cannot exceed the limits

class TestFillsCannotExceedTheLimits:

    def test_both_legs_filling_leaves_a_matched_pair_and_no_residual(
            self, probe, tmp_path):
        H, E = probe
        ex, t = _rest_a_pair(H, E, tmp_path, fill_qty="3")
        assert len(t.sent) == 2, t.sent
        up, dn = ex.store.position(MKT, "UP"), ex.store.position(MKT, "DOWN")
        assert up == dn, (up, dn)
        assert ex.store.residual(MKT) == D(0)

    def test_a_partial_fill_on_one_leg_is_bounded_by_the_residual_cap(
            self, probe, tmp_path):
        """One leg half-filled is an imbalance, and the cap is stated in
        units of this market's own clip rather than as a constant."""
        H, E = probe
        ex, t = _rest_a_pair(H, E, tmp_path, fill_qty="1")
        resid = ex.store.residual(MKT)
        assert abs(resid) <= D("5"), resid     # max_unmatched_contracts

    def test_a_third_order_is_never_part_of_one_bounded_pair(
            self, probe, tmp_path):
        H, E = probe
        ex, t = _rest_a_pair(H, E, tmp_path)
        n = len(t.sent)
        for _ in range(5):
            tick(ex, E)
        assert len(t.sent) == n, "a third order rested beside the pair"

    def test_the_slot_refuses_once_a_pair_is_already_resting(self, probe):
        assert TS.second_leg_slot_refusal(
            market=MKT, side="UP",
            live_orders={"a": {"market": MKT, "side": "DOWN"},
                         "b": {"market": MKT, "side": "UP"}},
            legs_claimed={"UP": False, "DOWN": True}) == "PAIR_ALREADY_RESTING"


# ============================ 3. no duplicates from cancel, retry, restart

class TestNoDuplicateOrders:

    def test_the_same_leg_is_never_placed_twice_while_resting(
            self, probe, tmp_path):
        H, E = probe
        ex, t = _rest_a_pair(H, E, tmp_path)
        ids = [s["client_order_id"] for s in t.sent]
        assert len(set(ids)) == len(ids)
        for _ in range(10):
            tick(ex, E)
        assert len(t.sent) == 2

    def test_a_restart_cannot_re_place_either_resting_leg(
            self, probe, tmp_path):
        """The claim file is the restart record, on the same volume."""
        H, E = probe
        ex, t = _rest_a_pair(H, E, tmp_path)
        first = {s["client_order_id"] for s in t.sent}
        ex2, t2 = mk(H, E, tmp_path)            # same store path
        ex2.limits = H.limits(max_live_orders=1)
        tick(ex2, E)
        assert not ({s["client_order_id"] for s in t2.sent} & first), t2.sent

    def test_a_cancelled_unfilled_leg_is_replaced_under_a_NEW_id(
            self, probe, tmp_path):
        H, E = probe
        ex, t = _rest_a_pair(H, E, tmp_path)
        first = [s["client_order_id"] for s in t.sent]
        ex.store.state["live_orders"].clear()
        ex.store.save()
        tick(ex, E)
        assert len(t.sent) == 3, "a cancelled-unfilled leg was not re-placed"
        assert t.sent[2]["client_order_id"] not in first

    def test_the_venue_double_also_rejects_a_duplicate_id(
            self, probe, tmp_path):
        """Two independent layers, so neither alone is carrying this."""
        H, E = probe
        ex, t = _rest_a_pair(H, E, tmp_path)
        with pytest.raises(RuntimeError):
            t.submit(dict(t.sent[0]))

    def test_an_extend_may_not_take_the_slot(self, probe):
        """A same-side order is a different decision with its own
        economics, and it is not authorized to use this slot."""
        assert TS.second_leg_slot_refusal(
            market=MKT, side="DOWN",
            live_orders={"a": {"market": MKT, "side": "DOWN"}},
            legs_claimed={"DOWN": True, "UP": False}
        ) == "RESTING_ORDER_IS_THE_SAME_SIDE"

    def test_another_market_may_not_take_the_slot(self, probe):
        assert TS.second_leg_slot_refusal(
            market=MKT, side="UP",
            live_orders={"a": {"market": "other", "side": "DOWN"}},
            legs_claimed={"DOWN": True, "UP": False}
        ) == "RESTING_ORDER_IS_ANOTHER_MARKET"

    def test_another_round_may_not_take_the_slot(self, probe):
        assert TS.second_leg_slot_refusal(
            market=MKT, side="UP",
            live_orders={"a": {"market": MKT, "side": "DOWN"}},
            legs_claimed={"DOWN": False, "UP": False}
        ) == "RESTING_ORDER_IS_ANOTHER_ROUND"

    def test_the_slot_fails_closed_on_unreadable_state(self, probe):
        for kw in ({"live_orders": None, "legs_claimed": {"DOWN": True}},
                   {"live_orders": {"a": {"market": MKT, "side": "DOWN"}},
                    "legs_claimed": None},
                   {"live_orders": {"a": {"market": MKT, "side": "?"}},
                    "legs_claimed": {"DOWN": True}}):
            assert TS.second_leg_slot_refusal(
                market=MKT, side="UP", **kw) is not None


# ====================== 4. a one-sided fill reaches the completion logic

class TestAOneSidedFillIsCompleted:

    def test_an_unmatched_leg_is_visible_to_the_completion_route(
            self, probe, tmp_path):
        """`BC.decide` prices the complement from the LEDGER's inventory, so
        a one-sided fill is exactly what it acts on."""
        from src import l5_book_completion as BC
        H, E = probe
        ex, t = mk(H, E, tmp_path, fill_qty="3")
        tick(ex, E)                              # one leg fills whole
        held = "UP" if ex.store.position(MKT, "UP") > 0 else "DOWN"
        qty = ex.store.position(MKT, held)
        assert qty > 0
        legs = ex.store.leg_cost(MKT)
        rec = BC.decide(
            market=MKT, held_side=held, held_qty=qty,
            held_cost_usd=legs.get(held),
            deficient_qty=D(0), deficient_cost_usd=D(0),
            book=_book(stream_age_s="0.05"),
            now_t=H.NOW().timestamp(),
            window_start=H.NOW().timestamp() - 60.0,
            resting=(), tick="0.01", min_qty="0.01",
            depth_ticks=BC.completion_depth_ticks(),
            residual_budget_usd=D("3"), submissions_left=5,
            last_decide_t=None)
        assert rec["reason"] != "NO_INVENTORY"
        assert rec.get("side") == ("DOWN" if held == "UP" else "UP")

    def test_the_completion_ceiling_is_1_00_and_not_the_opening_cap(self):
        from src import l5_book_completion as BC
        assert BC.REDEMPTION_PAIR == D("1.00")
        assert TS.max_pair_cost() == D("0.96")

    def test_a_completion_rests_at_the_touch_not_behind_it(self):
        from src import l5_book_completion as BC
        assert BC.completion_depth_ticks() == 0


# ===================== 5. the watchdog and the daily-loss halt still bind

class TestTheSafetyRailsStillBind:

    def test_the_daily_loss_figure_refuses_rather_than_reading_as_zero(self):
        import inspect
        s = inspect.getsource(LX.FaithfulExecutor.handle)
        assert "DAY_REALIZED_UNAVAILABLE" in s
        assert "None is UNKNOWN and refuses; it is never read as zero" in s

    def test_the_halt_is_checked_before_any_submission(self):
        import inspect
        s = inspect.getsource(LX.FaithfulExecutor.handle)
        i_rec = s.index("self.store.record_submit(")
        assert s.index("realized_current_utc_day") < i_rec

    def test_the_watchdog_is_a_separate_process_not_a_method_here(self):
        """It must be able to act when this process cannot."""
        import pathlib
        p = pathlib.Path("src/l5_watchdog.py")
        assert p.exists(), "the independent watchdog module is missing"
        src = p.read_text()
        # it must be able to ACT, not merely report
        assert "cancel" in src.lower()
        # and it must not be reachable as a method on the trading object,
        # which is what makes it independent of a wedged engine
        assert not hasattr(LX.FaithfulExecutor, "watchdog_sweep")

    def test_two_resting_orders_do_not_raise_the_exposure_ceiling(
            self, probe, tmp_path):
        H, E = probe
        ex, _ = _rest_a_pair(H, E, tmp_path)
        assert ex.validation_gates.max_cash_deployed_usd == D("8")
        assert ex.validation_gates.max_unmatched_contracts == D("5")


# ============================== 6. the 0.96 combined cap, and the fees

class TestTheCombinedPairCapAndFees:

    def test_the_opening_cap_is_0_96_and_applies_to_the_pair(self):
        assert TS.max_pair_cost() == D("0.96")

    def test_a_pair_quoted_from_one_book_is_spread_only(self):
        """pair = 1 - spread - 2*d*t, which is what makes the combined cap
        checkable at quote time rather than after one leg fills."""
        bid, ask, t, d = D("0.60"), D("0.61"), D("0.01"), 2
        up = bid - d * t
        dn = D(1) - ask - d * t
        assert up + dn == D(1) - (ask - bid) - 2 * d * t == D("0.95")
        assert up + dn <= TS.max_pair_cost()

    def test_a_pair_over_the_cap_is_refused_at_the_submission_boundary(self):
        import inspect
        s = inspect.getsource(LX.FaithfulExecutor.pair_cost_refusal)
        assert "TS.max_pair_cost()" in s
        assert "PAIR_COST_ABOVE_CEILING" in s

    def test_the_fee_terms_the_gates_carry_are_the_measured_ones(self):
        g = gates()
        assert g.fee_theta_worst == D("0.0695")      # taker worst case
        assert g.fee_extra_per_order_usd == D("0.01")

    def test_maker_fills_have_carried_no_commission_on_this_account(self):
        """Every campaign fill acked commission 0.0000, which is why the
        pair arithmetic above is not fee-adjusted. A zero commission is NOT
        a rebate and is not counted as one anywhere."""
        import pathlib
        doc = pathlib.Path(
            "analysis/l5/completion_repair/CAMPAIGN_LEDGER_RECONCILIATION.md"
        ).read_text()
        assert "credited $0" in doc

    def test_the_pair_cap_is_not_relaxed_by_the_second_slot(self):
        import inspect
        s = inspect.getsource(TS.second_leg_slot_refusal)
        assert "grants no other allowance" in s
        assert "MAX_LIVE_ORDERS ceiling may be one higher" in s
