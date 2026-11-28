"""Recovering a resting first leg the book has left, before its age deadline.

THE GAP, measured live on 2026-10-23. `plan_executable` already detects a
stale plan, but `retire_blocked_by` refuses to retire a round while any order
is live -- correctly, since an order we may still own must be cancelled by its
own id and proved terminal first. So while a first leg RESTED with zero fills,
nothing but the 120-second age deadline could rescue it.

2200z is the case this file pins. A DOWN first leg was placed at own 0.2100
against an own-side touch of 0.2300, correctly two ticks behind. Then:

    22:01:45  wire 0.7600/0.7700   own touch 0.23    2 ticks behind
    22:02:17  wire 0.5600/0.5700   own touch 0.43   22 ticks behind
    22:02:50  wire 0.7400/0.7500   own touch 0.25    4 ticks behind
    22:03:23  wire 0.7000/0.7100   own touch 0.29    8 ticks behind

It rested unfillable for 56 seconds of a 540-second entry budget, was
cancelled at zero fill by the age deadline, re-quoted two ticks from the NEW
touch at own 0.2900, and filled in 15 seconds.

WHAT MUST NOT CHANGE, and each is a test here: the 2-tick placement rule; the
<= 0.96 pair cap; cancel by exact venue id with zero cumulative fill proved
first; a racing fill becoming inventory rather than being abandoned; never
cancelling a quote for becoming MORE competitive; and a bounded number of
recoveries so the OPEN budget and queue priority are not churned away.

Nothing here submits an order, cancels one, uses a credential or opens a
socket.
"""
from __future__ import annotations

import collections
import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS


# the 2200z first leg, in its own terms
LEG = dict(side="DOWN", own_px="0.21", qty="0.94", remaining="0.94",
           state="LIVE", submitted_t=1000.0, now=1060.0,
           tick="0.01", depth_ticks=2, book_age_s=0.03,
           recoveries_used=0, open_budget_remaining=27)


def at(bid, ask, **over):
    return TS.recover_stale_leg(**dict(LEG, bid=bid, ask=ask, **over))


class TestTheOwnSideConversionIsNotGuessed:
    """`record_submit` stores the intent price, and l5_order_body.build takes
    own (outcome) terms and converts with `venue_price`. So the stored price
    is OWN-side and the comparison needs no wire conversion."""

    def test_the_body_builder_converts_own_to_wire_not_the_caller(self):
        from src import l5_order_body as OB
        assert OB.venue_price("OUTCOME_SIDE_NO", D("0.21")) == D("0.79")
        assert OB.venue_price("OUTCOME_SIDE_YES", D("0.21")) == D("0.21")

    def test_down_own_touch_is_one_minus_the_ask(self):
        assert TS.touch_own_price("DOWN", bid="0.76", ask="0.77") == D("0.23")
        assert TS.touch_own_price("UP", bid="0.76", ask="0.77") == D("0.76")

    def test_two_ticks_behind_is_lower_in_own_terms_on_both_sides(self):
        assert TS.quote_own_price("DOWN", bid="0.76", ask="0.77",
                                  tick="0.01", depth_ticks=2) == D("0.21")
        assert TS.quote_own_price("UP", bid="0.76", ask="0.77",
                                  tick="0.01", depth_ticks=2) == D("0.74")


class TestTheLiveCase:

    def test_the_2200z_drift_is_recovered(self):
        """own touch 0.43, we rest at 0.21: twenty ticks behind the quote."""
        v = at("0.56", "0.57")
        assert v["recover"] is True
        assert v["reason"] is None
        assert D(v["drift_ticks"]) == D(20)
        assert v["would_quote_now"] == "0.41"

    @pytest.mark.parametrize("bid, ask, ticks", [
        ("0.74", "0.75", 2),      # own touch 0.25 -> quote 0.23 -> 2 ticks
        ("0.70", "0.71", 6),      # own touch 0.29 -> quote 0.27 -> 6 ticks
    ])
    def test_the_rest_of_the_2200z_walk(self, bid, ask, ticks):
        v = at(bid, ask)
        assert D(v["drift_ticks"]) == D(ticks)
        assert v["recover"] is (ticks > TS.recover_drift_ticks())

    def test_at_placement_there_is_no_drift_at_all(self):
        """Two ticks behind the touch IS the placement rule, not drift."""
        v = at("0.76", "0.77")
        assert v["recover"] is False
        assert v["reason"] == "NOT_DRIFTED_AWAY"
        assert D(v["drift_ticks"]) == D(0)


class TestItNeverCancelsAnImprovingQuote:
    """Requirement five. Drift is SIGNED, and only away counts."""

    def test_a_quote_now_better_than_the_touch_is_left_alone(self):
        # the market came TO us: own touch 0.19, we rest dearer at 0.21
        v = at("0.80", "0.81")
        assert v["recover"] is False
        assert v["reason"] == "NOT_DRIFTED_AWAY"

    def test_that_holds_for_up_too(self):
        """UP rests on the BID, so a FALLING book comes to us: resting at
        0.74 with a 0.50 bid is dearer than today's 0.48 quote. More
        competitive -- in fact crossing, which post-only handles -- and never
        a reason to cancel."""
        v = TS.recover_stale_leg(**dict(LEG, side="UP", own_px="0.74",
                                        bid="0.50", ask="0.51"))
        assert v["reason"] == "NOT_DRIFTED_AWAY"

    def test_and_an_up_leg_left_behind_is_recovered(self):
        """A RISING book leaves an UP bid behind: touch 0.90, quote 0.88,
        and we rest 14 ticks below it."""
        v = TS.recover_stale_leg(**dict(LEG, side="UP", own_px="0.74",
                                        bid="0.90", ask="0.91"))
        assert v["recover"] is True
        assert D(v["drift_ticks"]) == D(14)


class TestZeroFillIsProvedNotAssumed:

    def test_a_partial_fill_is_never_abandoned(self):
        v = at("0.56", "0.57", remaining="0.50",
               state="PARTIALLY_FILLED")
        assert v["recover"] is False
        assert v["reason"] == "NOT_ZERO_FILL"

    def test_a_partial_that_still_says_live_is_refused_on_quantity(self):
        v = at("0.56", "0.57", remaining="0.50", state="LIVE")
        assert v["reason"] == "NOT_ZERO_FILL"

    def test_an_unreadable_quantity_is_not_a_zero(self):
        assert at("0.56", "0.57", remaining="?")["reason"] == \
            "FILL_STATE_UNREADABLE"
        assert at("0.56", "0.57", qty=None)["reason"] == \
            "FILL_STATE_UNREADABLE"

    def test_the_row_predicate_agrees_with_the_store_s_own_writes(self):
        """`apply_order_progress` sets PARTIALLY_FILLED and pops a full
        fill, so LIVE-with-full-quantity is the only abandonable row."""
        assert TS.row_is_zero_fill(
            {"state": "LIVE", "qty": "0.94", "remaining": "0.94"}) is True
        assert TS.row_is_zero_fill(
            {"state": "PARTIALLY_FILLED", "qty": "0.94",
             "remaining": "0.40"}) is False
        # a filled order's row is POPPED, so absent is terminal, not zero
        assert TS.row_is_zero_fill(None) is False
        assert TS.row_is_zero_fill({}) is False
        assert TS.row_is_zero_fill(
            {"state": "LIVE", "qty": "x", "remaining": "0.94"}) is False
        assert TS.row_is_zero_fill(
            {"state": "LIVE", "qty": "0", "remaining": "0"}) is False


class TestItFailsClosedOnEveryUnknown:

    @pytest.mark.parametrize("over, reason", [
        ({"tick": None}, "TICK_UNKNOWN"),
        ({"tick": "0"}, "TICK_UNKNOWN"),
        ({"book_age_s": 30.0}, "BOOK_STALE"),
        ({"book_age_s": None}, "BOOK_STALE"),
        ({"book_age_s": -1.0}, "BOOK_STALE"),
        ({"submitted_t": None}, "REST_TIME_UNREADABLE"),
        ({"own_px": None}, "LEG_PRICE_UNREADABLE"),
        ({"open_budget_remaining": "UNREADABLE"}, "OPEN_BUDGET_UNREADABLE"),
    ])
    def test_an_unknown_declines_the_recovery(self, over, reason):
        assert at("0.56", "0.57", **over)["reason"] == reason

    @pytest.mark.parametrize("bid, ask", [
        (None, "0.57"), ("0.56", None), ("0", "0.57"),
        ("0.57", "0.56"),            # crossed
        ("0.56", "0.56"),            # locked
    ])
    def test_a_book_it_cannot_judge_judges_nothing(self, bid, ask):
        assert at(bid, ask)["reason"] == "BOOK_UNUSABLE"

    def test_declining_leaves_the_pre_existing_age_deadline_in_charge(self):
        """Every refusal above is the behaviour that existed before this, so
        a fault in recovery can only restore it -- never widen anything."""
        assert TS.order_ttl_s() <= TS.ORDER_TTL_S


class TestChurnIsBounded:

    def test_a_young_order_is_still_working(self):
        """Every live first-leg fill so far landed in 9 to 15 seconds."""
        v = at("0.56", "0.57", now=LEG["submitted_t"] + 5.0)
        assert v["recover"] is False
        assert v["reason"] == "TOO_YOUNG"

    def test_the_rest_floor_is_at_its_boundary(self):
        t0 = LEG["submitted_t"]
        assert at("0.56", "0.57",
                  now=t0 + TS.recover_min_rest_s())["recover"] is True
        assert at("0.56", "0.57",
                  now=t0 + TS.recover_min_rest_s() - 0.001
                  )["reason"] == "TOO_YOUNG"

    def test_the_per_market_ceiling_binds(self):
        cap = TS.recover_max_per_market()
        assert at("0.56", "0.57", recoveries_used=cap - 1)["recover"] is True
        v = at("0.56", "0.57", recoveries_used=cap)
        assert v["recover"] is False
        assert v["reason"] == "RECOVERY_BUDGET_SPENT"

    def test_an_exhausted_open_budget_declines_rather_than_spends_the_cancel(
            self):
        """Cancelling with no capacity to re-place is strictly worse than
        leaving the order resting."""
        v = at("0.56", "0.57", open_budget_remaining=0)
        assert v["recover"] is False
        assert v["reason"] == "OPEN_BUDGET_EXHAUSTED"
        assert at("0.56", "0.57", open_budget_remaining=1)["recover"] is True

    def test_an_unset_open_budget_is_not_consulted(self):
        """UNSET-means-off is preserved: a budget nobody configured does not
        switch itself on."""
        assert at("0.56", "0.57",
                  open_budget_remaining=None)["recover"] is True


class TestTheThresholdsAreConfigurableOnlyTheSafeWay:

    def test_the_drift_threshold_is_the_owner_approved_four(self):
        assert TS.STALE_RECOVER_DRIFT_TICKS == 4

    def test_it_is_wider_than_the_placement_offset(self):
        """So an order is never recovered for sitting where it was put."""
        assert TS.STALE_RECOVER_DRIFT_TICKS > TS.depth_ticks()

    def test_it_is_narrower_than_the_plan_retirement_tolerance(self):
        assert TS.STALE_RECOVER_DRIFT_TICKS < TS.STALE_DRIFT_TICKS

    @pytest.mark.parametrize("raw, want", [
        ("8", 8), ("2", 4), ("0", 4), ("-5", 4), ("x", 4), ("", 4),
    ])
    def test_drift_is_configurable_lazier_only(self, monkeypatch, raw, want):
        monkeypatch.setenv(TS.STALE_RECOVER_DRIFT_ENV, raw)
        assert TS.recover_drift_ticks() == want

    @pytest.mark.parametrize("raw, want", [
        ("60", 60.0), ("1", 15.0), ("x", 15.0), ("", 15.0), ("nan", 15.0),
    ])
    def test_the_rest_floor_is_configurable_longer_only(self, monkeypatch,
                                                        raw, want):
        monkeypatch.setenv(TS.STALE_RECOVER_MIN_REST_ENV, raw)
        assert TS.recover_min_rest_s() == want

    @pytest.mark.parametrize("raw, want", [
        ("1", 1), ("0", 0), ("99", 1), ("x", 1), ("", 1), ("-1", 0),
    ])
    def test_the_churn_cap_is_configurable_smaller_only(self, monkeypatch,
                                                        raw, want):
        monkeypatch.setenv(TS.STALE_RECOVER_MAX_ENV, raw)
        assert TS.recover_max_per_market() == want


class TestThePricingRulesAreUntouched:

    def test_the_pair_cap_is_still_point_nine_six(self):
        assert TS.max_pair_cost() == D("0.96")

    def test_the_placement_offset_is_still_two_ticks(self):
        assert TS.depth_ticks() == 2

    def test_a_replacement_is_priced_behind_the_touch_never_at_it(self):
        """The recovered leg is re-quoted by the ordinary rule, so it cannot
        be at or through the touch however far the book moved."""
        for bid, ask in (("0.56", "0.57"), ("0.74", "0.75"),
                         ("0.10", "0.11"), ("0.90", "0.91")):
            for side in ("UP", "DOWN"):
                q = TS.quote_own_price(side, bid=bid, ask=ask, tick="0.01",
                                       depth_ticks=TS.depth_ticks())
                t = TS.touch_own_price(side, bid=bid, ask=ask)
                assert q < t
                assert not TS.leg_crosses(side, q, bid=bid, ask=ask)

    def test_recovery_decides_nothing_about_price(self):
        """It returns ids and a measurement; it names no replacement price."""
        v = at("0.56", "0.57")
        assert "price" not in v
        assert "replacement" not in v


# ============================================== the runner's own wiring

class _Store:
    def __init__(self, rows):
        self._rows = rows

    def live_orders(self):
        return {k: dict(v) for k, v in self._rows.items()}


class _Book:
    def __init__(self, snap):
        self._snap = snap

    def snapshot(self, slug):
        return dict(self._snap) if self._snap else None


class _Runner:
    """A duck-typed stand-in. The detector is a module-level FUNCTION that
    reads the runner through getattr, so a stub carrying only what it needs
    is exactly the shape the suite's other runner stubs have."""

    def acc_stale_by_distance(self, *a, **kw):
        return R.acc_stale_by_distance(self, *a, **kw)

    def __init__(self, rows, snap, tick="0.01"):
        self.transport = type("T", (), {"tick_size": tick})()
        self.store = _Store(rows)
        self.book = _Book(snap)
        self.stats = collections.Counter()
        self.win = collections.Counter()
        self.acc_recoveries = {}


SLUG = "cpc-btc-updown-15m-2026-10-23-2200z"
ROW = {"market": SLUG, "side": "DOWN", "price": "0.21", "qty": "0.94",
       "remaining": "0.94", "state": "LIVE", "venue_order_id": "D9EFMDAG51R3",
       "t": dt.datetime(2026, 10, 23, 22, 2, 50,
                        tzinfo=dt.timezone.utc).isoformat()}
NOW = dt.datetime(2026, 10, 23, 22, 3, 46,
                  tzinfo=dt.timezone.utc).timestamp()
SNAP = {"bid": "0.56", "ask": "0.57", "stream_age_s": 0.03}


@pytest.fixture(autouse=True)
def _budget(monkeypatch):
    """The OPEN budget read is a durable-ledger call; these tests are about
    the detector, and the budget's own refusal has its own test above."""
    from src import l5_live_authorization as AUTH
    monkeypatch.setattr(AUTH, "class_budget_report",
                        lambda path, exp: {"OPEN": {"remaining": 27}})


def test_the_runner_finds_the_stale_leg():
    r = _Runner({"c1": ROW}, SNAP)
    assert r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders()) == ["c1"]
    assert r.stats["acc_recover:RECOVER"] == 1


def test_it_skips_orders_already_due_on_time():
    """The time path owns those; recovering them twice would double-count."""
    r = _Runner({"c1": ROW}, SNAP)
    out = r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders(),
                                 skip={"c1"})
    assert out == []


def test_it_ignores_another_market_s_orders():
    other = dict(ROW, market="cpc-btc-updown-15m-2026-10-23-2215z")
    r = _Runner({"c1": other}, SNAP)
    assert r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders()) == []


@pytest.mark.parametrize("snap", [None, {}, {"bid": "0.56", "ask": "0.57"}])
def test_no_usable_book_recovers_nothing(snap):
    r = _Runner({"c1": ROW}, snap)
    assert r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders()) == []


def test_an_unreadable_tick_recovers_nothing():
    r = _Runner({"c1": ROW}, SNAP, tick=None)
    assert r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders()) == []


def test_an_unparseable_submit_time_is_left_to_the_time_path():
    """That path HALTS on it; recovery must not pre-empt the halt."""
    r = _Runner({"c1": dict(ROW, t="not-a-time")}, SNAP)
    assert r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders()) == []


def test_a_naive_submit_time_is_refused_too():
    naive = dt.datetime(2026, 10, 23, 22, 2, 50).isoformat()
    r = _Runner({"c1": dict(ROW, t=naive)}, SNAP)
    assert r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders()) == []


def test_the_per_market_count_is_honoured_across_calls():
    r = _Runner({"c1": ROW}, SNAP)
    r.acc_recoveries[SLUG] = TS.recover_max_per_market()
    assert r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders()) == []
    assert r.stats["acc_recover:RECOVERY_BUDGET_SPENT"] == 1


def test_the_churn_cap_is_one_per_market_not_three():
    """Lowered on review: 8 windows x (1 opening + 3 recoveries) is exactly
    the 32-submission OPEN budget, which would leave a later window unable
    to open at all. At 1 the worst case is 16 of 32."""
    assert TS.STALE_RECOVER_MAX_PER_MARKET == 1
    assert 8 * (1 + TS.STALE_RECOVER_MAX_PER_MARKET) * 2 <= 32


def test_two_stale_legs_do_not_both_spend_the_last_slot():
    """`recoveries_used` advances within the sweep, so the cap binds even
    when several rows qualify in one pass."""
    r = _Runner({"c1": ROW, "c2": dict(ROW, venue_order_id="X")}, SNAP)
    r.acc_recoveries[SLUG] = TS.recover_max_per_market() - 1
    assert len(r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders())) == 1


def test_a_failure_in_the_layer_cannot_break_the_deadline():
    """Recovery is an optimisation; the deadline bounds real exposure."""
    class Boom:
        def snapshot(self, slug):
            raise RuntimeError("socket thread died")

    r = _Runner({"c1": ROW}, SNAP)
    r.book = Boom()
    assert r.acc_stale_by_distance(NOW, SLUG, r.store.live_orders()) == []
    assert any(k.startswith("acc_recover:LAYER_FAILED")
               for k in r.stats)


def test_the_detector_never_cancels_anything():
    """It returns ids. The one cancel route is `deadman(only=...)`, which
    cancels by exact venue order id and proves the cancel terminal.

    By AST, not by grepping prose: the function must make no call to
    `deadman` and no call to anything whose name contains "cancel".
    """
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(R.acc_stale_by_distance)))
    called = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        called.append(f.attr if isinstance(f, ast.Attribute)
                      else getattr(f, "id", ""))
    assert "deadman" not in called
    assert not [c for c in called if "cancel" in str(c).lower()], called


def test_a_runner_stub_without_the_new_attributes_recovers_nothing():
    """Every duck-typed RunnerStub in the suite grafts `maybe_acc_deadline`
    onto an object carrying only what that method touched. A missing book,
    transport or counter must therefore yield no recovery rather than raise
    out of the deadline that bounds real exposure."""
    bare = type("Bare", (), {})()
    assert R.acc_stale_by_distance(bare, NOW, SLUG, {"c1": ROW}) == []


def test_the_deadline_is_still_a_method_of_the_runner():
    """A SCAR. The first version of this change put the detector at module
    level between two class methods, which silently ENDED the class body:
    `maybe_acc_deadline` and `maybe_qm_deadline` became module functions, so
    `self.maybe_acc_deadline(now)` would have raised AttributeError and the
    per-order deadline that bounds real exposure would simply not have run.
    py_compile passed and the unit tests passed; only the 25 broken runner
    tests showed it.

    These assertions are cheap and they pin the shape, not the behaviour.
    """
    for name in ("maybe_acc_deadline", "maybe_qm_deadline", "try_arm",
                 "close_window", "poll_orders", "roll", "maybe_preflight"):
        assert hasattr(R.Runner, name), name
    # and the detector is deliberately NOT a method
    assert hasattr(R, "acc_stale_by_distance")
    assert not hasattr(R.Runner, "acc_stale_by_distance")


def test_the_deadline_calls_the_detector_before_it_cancels():
    """Ordering, by AST: the stale sweep must be reached before `deadman`,
    or a drifted leg could never join the overdue list it feeds."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(R.Runner.maybe_acc_deadline)))
    detect, kill = [], []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if name == "acc_stale_by_distance":
            detect.append(n.lineno)
        if name == "deadman":
            kill.append(n.lineno)
    assert detect and kill
    assert min(detect) < min(kill)


def test_a_racing_fill_is_dropped_from_the_cancel_list():
    """Requirement three, at the one place it is decided: the re-check after
    the authoritative poll uses `row_is_zero_fill`, so a leg that filled in
    the race is never abandoned and becomes inventory instead."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(R.Runner.maybe_acc_deadline)))
    names = [n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert "row_is_zero_fill" in names
    assert "poll_orders" in names
    assert names.index("poll_orders") < names.index("row_is_zero_fill")
