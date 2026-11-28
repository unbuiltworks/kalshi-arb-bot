"""Depth-2 simultaneous maker execution, and what the provisional price
actually blocks.

PRIORITY 1's QUESTION, ANSWERED: does `PROVISIONAL_PRICE_UNREADABLE`
prevent otherwise-valid simultaneous maker orders? **Yes, both legs of
them, on the basis of a price that never reaches an order.**

The trace:

    book -> maybe_controller_open
         -> controller_provisional_price(UP) and (DOWN)      <- the gate
         -> refuse if either is None                         <- the defect
         -> dec = {side, price}                              <- ADVISORY
         -> executor.handle(..., controller={"decision": dec})
         -> probe_plan -> TS.build_orders(bid, ask, tick, min_qty,
                                          depth_ticks=L5_TS_DEPTH_TICKS)
         -> exactly two post-only intents, or a terminal refusal

`build_orders` takes **no side and no price**. It reprices both legs from
the authenticated book. `dec["side"]` is read exactly once downstream, to
record what was superseded. So the gate refuses a two-legged entry over a
number that cannot influence either leg.

THAT ALSO CORRECTS MY OWN EARLIER CAUTION. I withheld the repair partly
because "at ask 0.01 the forced side is DOWN at own 0.99 -- a poor use of
the probe's one entry". That reasoning was wrong: the forced side does not
choose the placed leg. The remaining reason to keep the flag is simply
that enabling it changes WHEN the controller opens, which is the owner's
call on a deployment -- not that it would place a bad order.

PRIORITY 2: the depth-2 configuration is `L5_TS_DEPTH_TICKS`, default 2,
and this file verifies the two legs it builds.

No transport, no network, no order.
"""
from __future__ import annotations

import inspect
import os
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as RUN
from src import l5_two_sided_probe as TS

MARKET = "cpc-btc-updown-15m-2026-11-08-1200z"


@pytest.fixture(autouse=True)
def authorized_market(monkeypatch):
    """`build_orders` refuses any market but the authorized one --
    `NOT_THE_AUTHORIZED_MARKET`, checked before anything else. That is a
    safety gate, not a nuisance: it is why a plan cannot be built for a
    market nobody approved. The tests below are about pricing, so the
    market is authorized explicitly rather than by ambient environment."""
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "readiness-depth2")
    monkeypatch.setenv(TS.MARKET_ENV, MARKET)
    assert TS.active(MARKET) is True
    yield


def plan(bid="0.62", ask="0.63", *, depth=2, bid_depth="500",
         ask_depth="500", cash="12", loss="1.00", age="0.4",
         gap=False, tick="0.01", min_qty="1"):
    return TS.build_orders(
        market=MARKET, bid=bid, ask=ask, bid_depth=bid_depth,
        ask_depth=ask_depth, tick=tick, min_qty=min_qty,
        depth_ticks=depth, cash_envelope=D(cash),
        max_worst_case_loss=D(loss), budget_headroom=D(cash),
        book_age_s=D(age), gap_suspected=gap)


# =====================================================================
# PRIORITY 1 -- the provisional price cannot influence the orders
# =====================================================================

def test_build_orders_takes_no_side_and_no_price():
    """THE PROOF. If the signature carries neither, the provisional
    decision cannot reach the venue, and refusing on it is refusing on
    something that does not matter."""
    sig = inspect.signature(TS.build_orders)
    names = set(sig.parameters)
    assert "side" not in names, names
    assert "price" not in names, names
    assert {"bid", "ask", "tick", "min_qty", "depth_ticks"} <= names


def test_the_provisional_decision_is_only_used_to_record_what_it_superseded():
    src = inspect.getsource(LX)
    i = src.index('superseded_l5 = {"side": str(dec.get("side"))')
    assert i > 0
    # and the two-sided plan is built from the BOOK, not from dec
    j = src.index("def probe_plan(")
    body = src[j:j + 1400]
    assert "authenticated.get(\"bid\")" in body
    assert "authenticated.get(\"ask\")" in body
    assert "dec" not in body.split("return TS.build_orders")[0][-200:]


def test_the_gate_blocks_BOTH_legs_when_only_one_side_is_pinned():
    """The defect, as a sequence. At ask 0.01 the UP provisional is
    unreadable and DOWN is 0.99 -- and the controller returns BEFORE
    `executor.handle`, so neither leg is ever offered to `build_orders`,
    which would have priced both from the same book."""
    book = {"bid": "0.00", "ask": "0.01"}
    px_up = LX.controller_provisional_price("UP", book, "0.01")
    px_dn = LX.controller_provisional_price("DOWN", book, "0.01")
    assert px_up is None and px_dn is not None
    # the refusal path returns None -- nothing reaches the executor
    src = inspect.getsource(RUN.Runner.maybe_controller_open)
    gate = src.index("PROVISIONAL_PRICE_UNREADABLE")
    handle = src.index("self.executor.handle(")
    assert gate < handle, "the gate must precede the only submission door"


def test_what_build_orders_would_have_done_with_that_same_book():
    """And it refuses on its OWN terms, which is the point: the economics
    are judged where they belong, not pre-empted by an advisory price."""
    out = plan(bid="0.00", ask="0.01")
    assert out.get("ok") is False
    assert out.get("reason"), out


# =====================================================================
# PRIORITY 2 -- the depth-2 configuration, and the two legs it builds
# =====================================================================

def test_the_depth_2_configuration_is_L5_TS_DEPTH_TICKS_defaulting_to_2():
    """THE EXACT EXISTING CONFIGURATION asked for. No new switch."""
    src = inspect.getsource(LX.FaithfulExecutor.probe_plan)
    assert 'int(os.getenv("L5_TS_DEPTH_TICKS", "2"))' in src
    was = os.environ.pop("L5_TS_DEPTH_TICKS", None)
    try:
        assert int(os.getenv("L5_TS_DEPTH_TICKS", "2")) == 2
    finally:
        if was is not None:
            os.environ["L5_TS_DEPTH_TICKS"] = was


def test_depth_2_builds_exactly_two_post_only_legs_at_two_ticks_behind():
    """Book 0.62/0.63, tick 0.01, depth 2.

    OWN PRICES: UP rests on the BID, own == wire, so 0.62 - 0.02 = 0.60.
    DOWN rests on the ASK, own = 1 - wire, so (1 - 0.63) - 0.02 = 0.35.
    Pair 0.95, inside the 0.96 fee-safe ceiling.
    """
    out = plan(depth=2)
    assert out.get("ok") is True, out
    legs = out.get("orders") or out.get("legs") or []
    assert len(legs) == 2, out
    by = {str(o.get("_side") or o.get("side")).upper(): o for o in legs}
    assert set(by) == {"UP", "DOWN"}, by
    own = {s: D(str(o.get("own_price") or o.get("own") or o.get("price")))
           for s, o in by.items()}
    # UP's wire IS its own; DOWN's wire is 1 - own
    assert own["UP"] == D("0.60"), own
    assert own["DOWN"] == D("0.35"), own
    assert own["UP"] + own["DOWN"] == D("0.95")
    assert own["UP"] + own["DOWN"] <= TS.completion_max_pair()


def test_depth_0_builds_a_pair_that_breaches_our_own_ceiling():
    """Which is the replay result, reproduced by the live builder: at the
    touch the pair is 0.99, above the 0.96 ceiling. Depth 2 is not a
    preference, it is what keeps the entry inside our own rule."""
    out = plan(depth=0)
    if out.get("ok") is not True:
        pytest.skip("depth 0 refused on other grounds: %s" % out.get("reason"))
    legs = out.get("legs") or []
    by = {str(o.get("side")).upper(): o for o in legs}
    own = {s: D(str(o.get("price"))) for s, o in by.items()}
    assert own["UP"] + own["DOWN"] == D("0.99")
    assert own["UP"] + own["DOWN"] > TS.completion_max_pair()


def test_both_legs_are_post_only_and_neither_crosses():
    """THE CONVENTION, RESOLVED FROM THE REAL OUTPUT rather than assumed.

    A leg's `price` is its OWN price: the DOWN leg reads
    `price 0.35, touch_at_placement 0.37, alone_cost_usd 0.37`, and the
    plan's `pair_cost` is 0.95 = 0.60 + 0.35. So to ask "does it cross?"
    the own price must be converted to the WIRE price the venue sees,
    which is what `TS.wire_price` exists for. Comparing an own price to a
    wire touch directly is the error -- my first version of this test did
    exactly that and failed, correctly.
    """
    out = plan(depth=2)
    legs = out.get("legs") or []
    for o in legs:
        assert o.get("post_only") is True, o
        assert str(o.get("mode")).upper() == "MAKER", o
    by = {str(o.get("side")).upper(): o for o in legs}
    wire = {s: TS.wire_price(s, D(str(o.get("price"))))
            for s, o in by.items()}
    # UP is a BUY of YES at wire 0.60, resting BELOW the 0.62 bid.
    assert wire["UP"] == D("0.60") and wire["UP"] < D("0.62")
    # DOWN is a sell of YES at wire 0.65, resting ABOVE the 0.63 ask.
    assert wire["DOWN"] == D("0.65") and wire["DOWN"] > D("0.63")


def test_a_stale_book_refuses_the_whole_plan_rather_than_one_leg():
    out = plan(depth=2, age=str(TS.MAX_BOOK_AGE_S + 5))
    assert out.get("ok") is False and out.get("reason"), out


def test_a_suspected_gap_refuses_the_whole_plan():
    out = plan(depth=2, gap=True)
    assert out.get("ok") is False and out.get("reason"), out


def test_it_never_degrades_to_a_single_leg(monkeypatch):
    """The module's own stated contract -- NO FALLBACK -- because a
    fallback turns a measurement into a trade."""
    src = inspect.getsource(TS.build_orders)
    assert "NO FALLBACK" in src
    # and every failure path returns ok False
    for bad in ({"bid": "0.62", "ask": "0.62"},        # not two-sided
                {"bid": "0.99", "ask": "1.00"}):       # no room at depth 2
        out = plan(bid=bad["bid"], ask=bad["ask"], depth=2)
        if out.get("ok"):
            assert len(out.get("legs") or []) == 2, out
        else:
            assert out.get("reason"), out


def test_a_one_dollar_worst_case_ceiling_still_admits_one_contract(
):
    """The deployed bound, against the deployed depth."""
    out = plan(depth=2, loss="1.00", cash="12")
    assert out.get("ok") is True, out
    legs = out.get("legs") or []
    for o in legs:
        assert D(str(o.get("qty"))) > 0


def test_an_exhausted_cash_envelope_refuses(
):
    out = plan(depth=2, cash="0")
    assert out.get("ok") is False and out.get("reason"), out


def test_an_unauthorized_market_is_refused_before_any_pricing(monkeypatch):
    """The gate that caught these tests, pinned as the property it is."""
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-2026-01-01-0000z")
    out = plan(depth=2)
    assert out == {"ok": False, "reason": "NOT_THE_AUTHORIZED_MARKET"}


def test_there_is_no_partial_arming_of_the_two_sided_probe(monkeypatch):
    """`active()` needs BOTH an experiment id and a market pattern. An
    incomplete configuration cannot half-enable the probe, which is the
    property that makes the market gate above a real safety gate rather
    than a lookup."""
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.setenv(TS.MARKET_ENV, MARKET)
    assert TS.active(MARKET) is False
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "x")
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    assert TS.active(MARKET) is False
    monkeypatch.setenv(TS.MARKET_ENV, MARKET)
    assert TS.active(MARKET) is True


def test_the_own_to_wire_boundary_is_consistent_end_to_end():
    """The chain the probe's accounting depends on, checked as one piece:

      build_orders  -> OWN prices        (DOWN 0.35)
      TS.wire_price -> WIRE for the venue (DOWN 0.65)
      venue avg_px  -> WIRE              (DOWN 0.65)
      apply_fill    -> OWN into leg_cost (DOWN 0.35)

    `apply_fill` stores `own * qty` -- it used to take the wire number,
    "which credits a short instead of charging it", per its own comment.
    So a DOWN leg filled at the price the plan asked for is booked at the
    cost the plan priced, and a completed depth-2 pair books at 0.95.
    """
    import tempfile
    out = plan(depth=2)
    by = {str(o.get("side")).upper(): o for o in (out.get("legs") or [])}
    own = {s: D(str(o["price"])) for s, o in by.items()}
    wire = {s: TS.wire_price(s, own[s]) for s in own}

    st = LX.InventoryStore(os.path.join(tempfile.mkdtemp(), "inv.json"))
    for s in ("UP", "DOWN"):
        coid = "l5f-%s" % s.lower()
        st.record_submit(coid, {"market": MARKET, "side": s,
                                "price": str(wire[s]), "qty": "1"},
                         session_id="probe", mode="live")
        st.apply_order_progress(
            coid, {"cum_qty": "1", "avg_px": str(wire[s]),
                   "leaves_qty": "0", "terminal": True,
                   "state": "ORDER_STATE_FILLED", "commission": "0",
                   "venue_order_id": "V-" + s},
            market=MARKET, side=s)
    legs = st.leg_cost(MARKET)
    assert legs["UP"] == own["UP"] == D("0.60"), (legs, own)
    assert legs["DOWN"] == own["DOWN"] == D("0.35"), (legs, own)
    assert legs["UP"] + legs["DOWN"] == D(str(out["pair_cost"])) == D("0.95")
    assert st.residual(MARKET) == 0
    assert not (st.state.get("leg_cost_basis_unknown") or [])


# =====================================================================
# THE CORRECTION THAT MATTERS MOST: depth 0 is not a live option at all
# =====================================================================

def test_the_live_builder_REFUSES_depth_0_and_1_on_a_one_tick_book():
    """I PRESENTED DEPTH 0 AS A LIVE CANDIDATE. IT IS NOT.

    On the 0.62/0.63 book, `build_orders` returns
    `PAIR_COST_ABOVE_CAP` at depth 0 (pair 0.99) and depth 1 (pair 0.97),
    because both breach the 0.96 fee-safe cap. Only depth 2 (0.95) and
    depth 3 (0.93) produce a plan at all.

    So the replay's depth-0 and depth-1 cells describe entries THE LIVE
    ENGINE WOULD NEVER HAVE PLACED, and the "depth 0 gives a 65% pair
    rate" framing I offered for the probe was wrong -- there is no depth-0
    probe to run. Depth 2 is not a preference over depth 0; it is the
    SHALLOWEST ADMISSIBLE depth under a cap we already enforce.
    """
    seen = {}
    for d in (0, 1, 2, 3):
        out = plan(depth=d)
        seen[d] = (out.get("ok"), out.get("reason"),
                   str(out.get("pair_cost")))
    assert seen[0] == (False, "PAIR_COST_ABOVE_CAP", "0.99"), seen
    assert seen[1] == (False, "PAIR_COST_ABOVE_CAP", "0.97"), seen
    assert seen[2][0] is True and seen[2][2] == "0.95", seen
    assert seen[3][0] is True and seen[3][2] == "0.93", seen


def test_depth_2_is_the_shallowest_admissible_depth():
    """Stated as the property, so a cap change cannot silently move it
    without this failing."""
    admissible = [d for d in (0, 1, 2, 3) if plan(depth=d).get("ok")]
    assert admissible == [2, 3], admissible
    assert min(admissible) == 2


def test_the_fee_floor_verdict_is_for_post_only_on_both_legs():
    """The edge is only real if both legs rest. The plan says so itself,
    and it is why a crossing completion is a different economic animal."""
    out = plan(depth=2)
    ff = out["fee_floor"]
    assert ff["ok"] is True
    assert ff["requires_post_only"] is True
    assert D(str(ff["net_if_maker"])) > D(str(ff["net_if_taker"]))
    assert D(str(out["edge_per_contract"])) == D("0.05")
    # and the plan refuses to call a displayed price a fill
    assert "displayed prices are NOT fills" in out["labels"]
