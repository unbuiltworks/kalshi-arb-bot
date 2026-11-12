"""ONE continuous inventory controller drives the BTC15m lifecycle -- through
the REAL runner and executor. Default OFF.

`l5_continuous_inventory.decide_pass` is the per-pass decision; with
`L5_TS_CONTINUOUS_INVENTORY=YES` the runner's timer slot hands it the market
(`Runner.maybe_continuous_inventory`), every PLACE goes through
`FaithfulExecutor.handle(None, book, controller=...)` -- the strategy
ownership gate, validation gates, safety overlay, residual cap, durable claim
and reconciliation -- and every CANCEL goes through `LX.deadman(only=[coid])`,
the exact-id, re-read-proved route. The other discretionary order paths stand
down for that market.

Harness: `tests/test_l5_ladder_runner.py` (a Runner built by its own
constructor, a FaithfulExecutor wired as `Runner.roll` wires it, a scripted
venue and book, `Runner.tick`'s own stage order). Production limits are the
harness's PRODUCTION set plus PMUS_RESIDUAL_CAP_K=1 and L5_MAX_LIVE_ORDERS=4:
$0.90 cash, $1.00 exposure, 5 unmatched contracts, 0.96 opening pair, taker
completion off. Nothing here is evidence of profitability.
"""
import json
from decimal import Decimal as D

import pytest

import tests.test_l5_ladder_runner as LAD
import tests.test_l5_runner_pair_and_cancel_state as T
from src import l5_continuous_inventory as CI
from src import l5_live_execution as E
from src import l5_pmus_live_runner as R

SLUG = T.SLUG
ON = {"L5_TS_CONTINUOUS_INVENTORY": "YES", "PMUS_RESIDUAL_CAP_K": "1",
      "L5_MAX_LIVE_ORDERS": "4"}
CENT = D("0.01")


def rig(tmp_path, monkeypatch, **env):
    h = LAD.harness(tmp_path, monkeypatch, **dict(ON, **env))
    return h


def passes(h):
    return [r for label, r in h.emitted if label == "L5_CONTINUOUS_INVENTORY"]


def last(h):
    p = passes(h)
    return p[-1] if p else {}


def oid_of(h, side):
    ids = LAD.resting_ids(h, side)
    assert len(ids) == 1, (side, ids, LAD.resting(h))
    return ids[0]


def opened(h, t=6.8):
    h.at(t - 0.3).l5_action()
    h.tick(t)
    return h


def fill_and_book(h, oid, start, qty=None):
    """The venue fills `oid`; tick until the runner's order poll books it.
    Returns the time of the tick that booked it -- the controller reacts
    on the NEXT tick, because the poll runs after the decision stages."""
    side = str(h.venue.by_oid[oid]["side"])
    before = h.pos(side)
    h.venue.fill(oid, qty)
    t = float(start)
    for _ in range(4):
        h.tick(t)
        if h.pos(side) != before:
            return t
        t += 3.5
    raise AssertionError("the fill was never booked")


def actions_since(h, n, intent=None, kind=None):
    return [a for p in passes(h)[n:] for a in p["actions"]
            if (intent is None or a.get("intent") == intent)
            and (kind is None or a.get("kind") == kind)]


def max_cash_reserved(h):
    vg = h.r.executor.validation_gates
    filled = vg.cash_committed(h.r.executor.cash_legs_for_gates())
    resting = vg.reserved_cash(
        outstanding=h.r.executor.outstanding_for_gates(), order_qty=D(0),
        order_price=D(0), order_intent="ORDER_INTENT_BUY_LONG",
        inventory=h.r.executor.inventory_for_gates(SLUG))["resting"]
    return filled + resting


# ======================================================================
# 0. SWITCH OFF => UNCHANGED
# ======================================================================
@pytest.mark.parametrize("val", [None, "", "NO", "Y", "ON", "1"])
def test_switch_off_is_byte_for_byte_the_deployed_path(tmp_path, monkeypatch,
                                                       val):
    env = {} if val is None else {"L5_TS_CONTINUOUS_INVENTORY": val}
    if val is None:
        monkeypatch.delenv("L5_TS_CONTINUOUS_INVENTORY", raising=False)
    h = LAD.build(LAD.harness(tmp_path, monkeypatch, **env))
    # exactly what tests/test_l5_ladder_runner pins for production
    assert LAD.resting(h) == [("DOWN", D("0.44")), ("UP", D("0.50"))]
    assert not passes(h)
    assert not [k for k in h.r.stats if k.startswith("continuous")]
    assert R.Runner.continuous_owns(h.r, SLUG) is False
    for s in h.venue.sent:
        assert "|l5ci|" not in str(s.get("action_key") or "")


def test_switch_off_executor_takes_no_continuous_intent(monkeypatch):
    monkeypatch.delenv("L5_TS_CONTINUOUS_INVENTORY", raising=False)
    c = {"provenance": E.CONTROLLER_GENERATED, "decision": {},
         "continuous": {"kind": "PLACE"}}
    assert E.FaithfulExecutor.continuous_pass_intent(c) is None
    monkeypatch.setenv("L5_TS_CONTINUOUS_INVENTORY", "YES")
    assert E.FaithfulExecutor.continuous_pass_intent(c) is not None
    assert E.FaithfulExecutor.continuous_pass_intent(
        dict(c, continuous={"kind": "CANCEL"})) is None


# ======================================================================
# 1. SIMULTANEOUS QUOTES
# ======================================================================
def test_both_maker_quotes_rest_from_one_pass(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    assert LAD.resting(h) == [("DOWN", D("0.44")), ("UP", D("0.50"))]
    sent = h.sent()
    assert len(sent) == 2 and sent[0][2] == sent[1][2] > 0
    assert sum(p for _, p, _ in sent) <= D("0.96")
    for s in h.venue.sent:
        assert str(s["mode"]).upper() == "MAKER"
    a = last(h)["actions"]
    assert sorted(x["intent"] for x in a) == ["OPEN", "OPEN"]
    # the L5 action was observed, not followed: one controller
    assert h.r.win["CONTINUOUS_INVENTORY:CONTINUOUS_INVENTORY_OWNS_MARKET"] == 1
    # cash reserved exactly as the gate reserves it, within $0.90
    assert max_cash_reserved(h) <= D("0.90")


def test_a_repeated_tick_places_nothing_more(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    for t in (8.0, 9.5, 11.0):
        h.tick(t)
    assert len(h.venue.sent) == 2
    assert last(h)["holds"] == {"UP": "KEEP_RESTING", "DOWN": "KEEP_RESTING"}


def test_the_other_order_paths_stand_down(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    fill_and_book(h, oid_of(h, "UP"), 10.0)
    assert h.r.maybe_book_completion(h.t) is None
    assert h.r.stats["book_completion:CONTINUOUS_INVENTORY_OWNS"] > 0
    assert h.r.maybe_completion_requote(h.t) is None
    assert h.r.stats["requote_refused:CONTINUOUS_INVENTORY_OWNS"] > 0


# ======================================================================
# 2. FIRST-LEG FILL -> IMMEDIATE MISSING-LEG REPRICE BY EXACT ID
# ======================================================================
def test_first_leg_fill_reprices_the_missing_leg_at_once_by_exact_id(
        tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    down = oid_of(h, "DOWN")
    tb = fill_and_book(h, oid_of(h, "UP"), 10.0)
    assert h.pos("UP") == D("0.6") and h.pos("DOWN") == 0
    assert h.venue.cancelled == []
    h.tick(tb + 3.5)
    # cancelled exactly the resting DOWN, nothing else, BEFORE its 14s rest
    assert h.venue.cancelled == [down]
    assert any(a.get("reason") == "REPRICE_MISSING_LEG"
               for p in passes(h) for a in p["actions"])
    # the replacement is the completion: at the touch, inside the target
    assert LAD.resting(h) == [("DOWN", D("0.46"))]
    basis = h.r.store.leg_cost(SLUG)["UP"] / h.pos("UP")
    assert basis + D("0.46") <= D("0.96")
    assert h.sent()[-1] == ("DOWN", D("0.46"), D("0.6"))


def test_cancel_unconfirmed_holds_everything(tmp_path, monkeypatch):
    """The venue still reports the order live after the cancel: the store
    goes uncertain and the second decision of the pass places nothing."""
    h = opened(rig(tmp_path, monkeypatch))
    down = oid_of(h, "DOWN")
    tb = fill_and_book(h, oid_of(h, "UP"), 10.0)
    h.venue.lag[down] = 50
    n = len(h.venue.sent)
    h.tick(tb + 3.5, poll=False)
    assert h.venue.cancelled == [down]
    assert len(h.venue.sent) == n
    assert h.r.store.orders_uncertain()
    assert last(h)["holds"]["DOWN"] == "ORDERS_UNCERTAIN"


def test_a_fill_that_beats_the_cancel_is_booked_and_not_hedged_twice(
        tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    up = oid_of(h, "UP")
    # the book walks away from both quotes, past their minimum rest
    h.book.set("0.56", "0.58")
    h.venue.fill(up)                       # filled at the venue, unpolled
    h.r.reconciled, h.r.last_reconcile_t = True, h.t + 100
    h.tick(22.0, poll=False)
    # UP's fill was found by the cancel's re-read and booked once
    assert h.pos("UP") == D("0.6")
    ups = [s for s in h.sent() if s[0] == "UP"]
    assert len(ups) == 1, h.sent()          # no second UP opened
    # and what rests on DOWN is ONE completion, inside the target
    assert len(LAD.resting_ids(h, "DOWN")) <= 1
    for side, px, q in h.sent()[2:]:
        assert side == "DOWN" and q <= h.pos("UP")


# ======================================================================
# 3. OPPOSITE-SIDE COMPLETION, THEN RAPID REPLENISHMENT
# ======================================================================
def test_completion_then_replenishment_in_the_next_pass(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    tb = fill_and_book(h, oid_of(h, "UP"), 10.0)
    h.tick(tb + 3.5)
    tb = fill_and_book(h, oid_of(h, "DOWN"), tb + 4.0)   # matched 0.6 / 0.6
    assert h.pos("UP") == h.pos("DOWN") == D("0.6")
    lc = h.r.store.leg_cost(SLUG)
    assert (lc["UP"] + lc["DOWN"]) / D("0.6") <= D("0.96")
    n = len(passes(h))
    h.tick(tb + 3.5)
    intents = [a["intent"] for a in actions_since(h, n, kind="PLACE")]
    assert intents == ["REPLENISH", "REPLENISH"], passes(h)[n:]
    assert sorted(s for s, _ in LAD.resting(h)) == ["DOWN", "UP"]
    # the completed pair's cash stays committed until settlement, so the
    # replenishment is SMALLER -- the cash cap is not stretched to fit it
    assert h.sent()[-1][2] < D("0.6")
    assert max_cash_reserved(h) <= D("0.90")


def test_replenishment_ledger_gates_reopening_when_on(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch, L5_DUAL_REPLENISH="ON"))
    # nothing has filled or been cancelled: no credit, so after the first
    # pair is withdrawn nothing re-opens
    out = E.deadman(h.r.store, h.venue, reason="test", only=None)
    assert out
    n = len(h.venue.sent)
    h.tick(9.0)
    assert len(h.venue.sent) == n
    assert any(k.startswith("continuous:REPLENISH_UNPAID")
               for k in h.r.stats)


# ======================================================================
# 4. PARTIAL FILLS AND PER-CONTRACT BASIS
# ======================================================================
def test_a_partial_first_leg_completes_only_what_filled(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    tb = fill_and_book(h, oid_of(h, "UP"), 10.0, qty=D("0.2"))
    assert h.pos("UP") == D("0.2")
    n = len(passes(h))
    h.tick(tb + 3.5)
    comp = actions_since(h, n, intent="COMPLETE")
    assert comp and D(comp[0]["qty"]) == D("0.2"), passes(h)[n:]
    # UP's unfilled remainder still rests, counted once
    assert len(LAD.resting_ids(h, "UP")) == 1
    st = last(h)["state"]
    assert D(st["resting_qty"]["UP"]) == D("0.4")


def test_partial_completion_is_priced_per_contract_against_average_cost():
    """PR #218 P0: 1.18 held in three clips for $0.5508. A 0.20 completion
    compares 0.20 x (avg + price) to 0.20 x target -- never one clip's cost
    against all 1.18 contracts."""
    held, cost = D("1.18"), D("0.5508")
    avg = cost / held
    p = CI.decide_pass(market="m", bid="0.40", ask="0.50", tick="0.01",
                       up_q=held, up_cost=cost, dn_q=D("0.98"),
                       dn_cost=D("0.98") * D("0.45"),
                       complete_target=D("0.98"), pair_ceiling=D("0.96"),
                       cash_committed=D(0), cash_cap=D(5),
                       max_worst_loss=D(5), residual_k=None)
    comp = [a for a in p["actions"] if a.get("intent") == "COMPLETE"]
    assert comp and D(comp[0]["qty"]) == D("0.20")
    px = D(comp[0]["price"])
    assert px == D("0.50")                       # the DOWN touch, 1 - ask
    assert avg + px <= D("0.98")
    # average-cost attribution conserves basis exactly
    matched, remaining = D("0.20") * avg, (held - D("0.20")) * avg
    assert abs(matched + remaining - cost) < D("1e-20")


@pytest.mark.parametrize("touch_dn,want_px,placed", [
    ("0.507", D("0.507"), True),     # pair 0.977 <= 0.98 target: at touch
    ("0.535", D("0.510"), True),     # touch would be 1.005: rests at target
])
def test_target_versus_hard_ceiling(touch_dn, want_px, placed):
    """Matched cost $0.47/contract: a 0.507 missing leg is a 0.977 pair and
    is taken at the touch; at a 0.535 touch (1.005) the order is NOT sent at
    the touch -- it rests at the 0.98 target, never past the 1.00 ceiling."""
    ask = str(D(1) - D(touch_dn))
    p = CI.decide_pass(market="m", bid="0.40", ask=ask, tick="0.001",
                       up_q=D(1), up_cost=D("0.47"),
                       complete_target=D("0.98"), complete_ceiling=D("1.00"),
                       pair_ceiling=D("0.96"), cash_cap=D(5),
                       max_worst_loss=D(5), residual_k=None)
    comp = [a for a in p["actions"] if a.get("intent") == "COMPLETE"]
    assert bool(comp) is placed
    assert D(comp[0]["price"]) == want_px
    assert D("0.47") + D(comp[0]["price"]) <= D("1.00")


def test_executor_refuses_a_completion_above_the_hard_ceiling(
        tmp_path, monkeypatch):
    """Defence in depth: an intent the pass could never produce is still
    refused at the seam, before any claim is spent."""
    h = opened(rig(tmp_path, monkeypatch))
    fill_and_book(h, oid_of(h, "UP"), 10.0)
    E.deadman(h.r.store, h.venue, reason="test", only=None)
    h.book.set("0.40", "0.45")
    bad = {"kind": "PLACE", "intent": "COMPLETE", "side": "DOWN",
           "price": D("0.54"), "qty": D("0.6")}          # 0.50 + 0.54
    rec = h.r.executor.handle(
        None, h.book.snapshot(), market=SLUG, window=str(T.WS),
        controller={"provenance": E.CONTROLLER_GENERATED,
                    "decision": {"side": "DOWN", "price": D("0.54"),
                                 "l5_size": D(1), "relation": "continuous"},
                    "maker_class": None, "continuous": bad})
    assert rec["refused_reason"] == "COMPLETION_ABOVE_HARD_CEILING"
    over = dict(bad, price=D("0.40"), qty=D("0.7"))
    rec = h.r.executor.handle(
        None, h.book.snapshot(), market=SLUG, window=str(T.WS),
        controller={"provenance": E.CONTROLLER_GENERATED,
                    "decision": {"side": "DOWN", "price": D("0.40"),
                                 "l5_size": D(1), "relation": "continuous"},
                    "maker_class": None, "continuous": over})
    assert rec["refused_reason"] == "COMPLETE_WOULD_REVERSE"
    cross = dict(bad, price=D("0.60"), qty=D("0.6"), intent="OPEN")
    h.r.executor._probe_leg_pending = None
    rec = h.r.executor.handle(
        None, h.book.snapshot(), market=SLUG, window=str(T.WS),
        controller={"provenance": E.CONTROLLER_GENERATED,
                    "decision": {"side": "DOWN", "price": D("0.60"),
                                 "l5_size": D(1), "relation": "continuous"},
                    "maker_class": None, "continuous": cross})
    assert rec["refused_reason"] in ("OPEN_NOT_FLAT", "WOULD_CROSS:DOWN")


# ======================================================================
# 5. SAME-SIDE FILLS (ADD) WITHIN THE CAP, AND IMBALANCE SKEW
# ======================================================================
def test_add_is_off_by_default_and_refused_by_the_production_cap(
        tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    tb = fill_and_book(h, oid_of(h, "UP"), 10.0)
    h.tick(tb + 3.5)
    assert last(h)["holds"]["UP"] == "ADD_BAND_ZERO"
    assert [s for s, _ in LAD.resting(h)] == ["DOWN"]
    # a stated band does not beat k=1: the residual cap still refuses
    monkeypatch.setenv(R.Runner.CONTINUOUS_ADD_BAND_ENV, "2")
    h.tick(tb + 5.0)
    assert last(h)["holds"]["UP"] == "RESIDUAL_CAP", last(h)
    assert [s for s, _ in LAD.resting(h)] == ["DOWN"]


def test_repeated_same_side_fills_stay_inside_the_stated_cap(
        tmp_path, monkeypatch):
    """NOT a production configuration: k=3 and a 1.2-contract band, so ADD
    is reachable at all. Each ADD keeps (new avg + opposite bid) <= 0.96 and
    the imbalance never exceeds the band."""
    h = opened(rig(tmp_path, monkeypatch, PMUS_RESIDUAL_CAP_K="3",
                   L5_CI_ADD_BAND_CONTRACTS="2",
                   L5_MAX_CASH_DEPLOYED_USD="3.00",
                   L5_MAX_WORST_CASE_DEFICIT_USD="3.00"))
    h.r.limits.max_total_exposure_usd = D("3.00")
    h.book.set("0.52", "0.53")
    fill_and_book(h, oid_of(h, "UP"), 10.0)
    h.tick(24.0)
    adds = [a for p in passes(h) for a in p["actions"]
            if a.get("intent") == "ADD"]
    assert adds, last(h)
    for a in adds:
        assert D(a["projected_pair"]) <= D("0.96")
    for t in (30.0, 40.0, 50.0):
        for oid in LAD.resting_ids(h, "UP"):
            h.venue.fill(oid)
        LAD.book_fills(h, t)
        assert h.pos("UP") - h.pos("DOWN") <= D("2")
        assert h.pos("UP") - h.pos("DOWN") + \
            h.r.store.outstanding_on_side(SLUG, "UP") <= D("2")


def test_skew_moves_the_light_side_nearer_and_the_heavy_side_further():
    base = dict(market="m", bid="0.50", ask="0.52", tick="0.01",
                up_q=D(1), up_cost=D("0.44"), complete_depth_ticks=2,
                depth_ticks=2, imbalance_cap=D(3), residual_k=None,
                complete_target=D("0.98"), pair_ceiling=D("0.99"),
                cash_cap=D(5), max_worst_loss=D(5))
    p0 = CI.decide_pass(**base)
    p1 = CI.decide_pass(**dict(base, skew_ticks=1))
    px = lambda p, s: [D(a["price"]) for a in p["actions"]
                       if a["side"] == s][0]
    assert px(p1, "DOWN") == px(p0, "DOWN") + CENT       # light: nearer
    assert px(p1, "UP") == px(p0, "UP") - CENT           # heavy: further
    assert px(p0, "DOWN") == D("0.46")                   # 0.48 touch - 2


# ======================================================================
# 6. ADAPTIVE REPRICING WITH EXACT-ID CANCEL
# ======================================================================
def test_the_book_moving_reprices_both_quotes_by_exact_id(tmp_path,
                                                          monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    ids = set(LAD.resting_ids(h))
    h.book.set("0.55", "0.57")
    h.tick(10.0)                       # under the 14s minimum rest: kept
    assert h.venue.cancelled == []
    h.tick(22.0)
    assert set(h.venue.cancelled) == ids
    assert LAD.resting(h) == [("DOWN", D("0.41")), ("UP", D("0.53"))]
    # never two orders on one side, never more than the order limit
    for s in ("UP", "DOWN"):
        assert len(LAD.resting_ids(h, s)) == 1
    assert len(h.r.store.live_orders()) <= R.max_live_orders()


def test_a_completion_ahead_of_the_touch_is_not_moved_down():
    """A resting completion bid the book came TO is better placed, not
    stale: moving it down would only spend a claim to fill less."""
    p = CI.decide_pass(
        market="m", bid="0.40", ask="0.50", tick="0.01", up_q=D(1),
        up_cost=D("0.40"), now_s=100.0,
        resting=[{"coid": "c", "side": "DOWN", "price": "0.53",
                  "remaining": "1", "submitted_s": 50.0}],
        complete_target=D("0.98"), cash_cap=D(5), max_worst_loss=D(5),
        residual_k=None)
    assert not [a for a in p["actions"] if a["kind"] == "CANCEL"]
    assert p["holds"]["DOWN"] == "KEEP_RESTING"


def test_a_completion_breaching_the_ceiling_is_cancelled_regardless():
    p = CI.decide_pass(
        market="m", bid="0.40", ask="0.50", tick="0.01", up_q=D(1),
        up_cost=D("0.50"), now_s=1.0,
        resting=[{"coid": "c", "side": "DOWN", "price": "0.55",
                  "remaining": "1", "submitted_s": 0.5}],
        complete_target=D("0.98"), cash_cap=D(5), max_worst_loss=D(5),
        residual_k=None, reprice_min_rest_s=14.0)
    assert [(a["kind"], a["coid"], a["reason"]) for a in p["actions"]] == [
        ("CANCEL", "c", "REPRICE_CEILING_BREACH")]


# ======================================================================
# 7. CAPITAL RESERVATION REFUSALS
# ======================================================================
def test_cash_cap_shrinks_then_refuses(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch, L5_MAX_CASH_DEPLOYED_USD="0.30"))
    sent = h.sent()
    assert sent and all(q <= D("0.2") for _, _, q in sent)
    assert max_cash_reserved(h) <= D("0.30")
    h2 = opened(LAD.harness(tmp_path / "b", monkeypatch,
                            **dict(ON, L5_MAX_CASH_DEPLOYED_USD="0.005")))
    assert h2.venue.sent == []
    assert set(last(h2)["holds"].values()) <= {"CASH_CAP"}


def test_worst_case_settlement_bound_is_respected():
    p = CI.decide_pass(market="m", bid="0.52", ask="0.54", tick="0.01",
                       clip=D(10), cash_cap=D(50), max_worst_loss=D("1.00"),
                       residual_k=None, pair_ceiling=D("0.96"))
    rest_up = sum(D(a["price"]) * D(a["qty"]) for a in p["actions"]
                  if a["side"] == "UP")
    rest_dn = sum(D(a["price"]) * D(a["qty"]) for a in p["actions"]
                  if a["side"] == "DOWN")
    assert CI.worst_settlement_loss(up_q=D(0), dn_q=D(0), up_cost=D(0),
                                    dn_cost=D(0), rest_up_usd=rest_up,
                                    rest_dn_usd=rest_dn) <= D("1.00")


def test_claim_slots_are_the_existing_bound_and_the_last_is_kept(
        tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch, L5_ACC_MAX_ROUNDS="2"))
    ex = h.r.executor
    assert ex.continuous_slots_left(SLUG, "UP") == 1
    # the book moves: an opening leg may not spend the last slot
    h.book.set("0.56", "0.58")
    h.tick(22.0)
    assert h.venue.cancelled == []
    assert last(h)["holds"]["UP"] == "REPRICE_DEFERRED_CLAIM_SLOTS_LOW"
    # ...which a completion still may: UP fills, the book comes back, and
    # the DOWN leg now sits BEHIND its completion price
    h.book.set("0.52", "0.54")
    tb = fill_and_book(h, oid_of(h, "UP"), 23.0)
    n = len(passes(h))
    h.tick(tb + 3.5)
    assert actions_since(h, n, intent="COMPLETE"), passes(h)[n:]
    assert ex.continuous_slots_left(SLUG, "DOWN") == 0
    # and with no slot left, nothing more can be placed on DOWN
    h.book.set("0.50", "0.52")
    h.tick(tb + 30.0)
    assert len(LAD.resting_ids(h, "DOWN")) == 1


# ======================================================================
# 8. ENTRY / COMPLETE CUTOFFS
# ======================================================================
def test_past_entry_cutoff_nothing_opens_but_completion_continues(
        tmp_path, monkeypatch):
    h = rig(tmp_path, monkeypatch)
    h.at(479.5).l5_action()
    h.tick(481.0)
    assert h.venue.sent == []
    assert last(h)["phase"] == "COMPLETE_ONLY"
    assert set(last(h)["holds"].values()) == {"PAST_ENTRY_CUTOFF"}

    h = opened(rig(tmp_path / "b", monkeypatch))
    fill_and_book(h, oid_of(h, "UP"), 10.0)
    E.deadman(h.r.store, h.venue, reason="test", only=None)
    h.tick(500.0)
    comp = [a for a in last(h)["actions"] if a.get("intent") == "COMPLETE"]
    assert comp and last(h)["phase"] == "COMPLETE_ONLY"
    assert LAD.resting(h)[0][0] == "DOWN"


def test_past_the_complete_cutoff_nothing_is_placed(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    fill_and_book(h, oid_of(h, "UP"), 10.0)
    E.deadman(h.r.store, h.venue, reason="test", only=None)
    n = len(h.venue.sent)
    h.tick(601.0)
    assert len(h.venue.sent) == n
    assert last(h)["phase"] == "CLOSED"


# ======================================================================
# 9. RESTART / RECONCILIATION
# ======================================================================
def test_restart_rebuilds_from_the_store_and_places_no_duplicate(
        tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    fill_and_book(h, oid_of(h, "UP"), 10.0)
    h.tick(17.0)
    sent_before = list(h.venue.sent)
    resting_before = LAD.resting(h)
    # a new process: new Runner, new executor, the SAME durable store and
    # authorization ledger, the SAME venue
    h2 = T.Harness(tmp_path, monkeypatch, root="live")
    h2.venue, h2.book = h.venue, h.book
    h2.r.book = h.book
    h2.attach()
    LAD.apply_limits(h2)
    h2.at(18.0)
    assert h2.pos("UP") == D("0.6")
    h2.tick(18.0)
    h2.tick(20.0)
    assert h2.venue.sent == sent_before          # nothing re-sent
    assert LAD.resting(h2) == resting_before
    assert last(h2)["holds"]["DOWN"] == "KEEP_RESTING"
    # claims are durable: the next placement takes a NEW slot, never one
    # the first process spent
    assert h2.r.executor.continuous_slots_left(SLUG, "DOWN") == \
        h.r.executor.continuous_slots_left(SLUG, "DOWN")
    coids = [s["client_order_id"] for s in h.venue.sent]
    assert len(coids) == len(set(coids))


# ======================================================================
# 10. SETTLEMENT / RESIDUAL ACCOUNTING
# ======================================================================
def test_settlement_splits_matched_and_residual(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    tb = fill_and_book(h, oid_of(h, "UP"), 10.0)
    h.tick(tb + 3.5)
    tb = fill_and_book(h, oid_of(h, "DOWN"), tb + 4.0, qty=D("0.4"))
    assert h.pos("UP") == D("0.6") and h.pos("DOWN") == D("0.4")
    h.tick(tb + 1.0)
    # the pass reads the ledger's own costs, and its worst case is the
    # arithmetic over both settlements with the resting 0.2 DOWN counted
    lc = h.r.store.leg_cost(SLUG)
    st = last(h)["state"]
    rest_dn = sum(D(r["remaining"]) * D(r["price"]) for r in st["resting"]
                  if r["side"] == "DOWN")
    assert D(st["worst_settlement_loss"]) == CI.worst_settlement_loss(
        up_q=D("0.6"), dn_q=D("0.4"), up_cost=lc["UP"], dn_cost=lc["DOWN"],
        rest_dn_usd=rest_dn)
    E.deadman(h.r.store, h.venue, reason="test", only=None)
    cost = lc["UP"] + lc["DOWN"]
    assert D(st["up_cost"]) + D(st["dn_cost"]) == cost
    # DOWN settles: 0.4 redeems, 0.6 UP is worth nothing
    pnl_down = D("0.4") - cost
    pnl_up = D("0.6") - cost
    assert CI.worst_settlement_loss(up_q=D("0.6"), dn_q=D("0.4"),
                                    up_cost=lc["UP"], dn_cost=lc["DOWN"]
                                    ) == max(-pnl_down, -pnl_up, D(0))
    # the matched 0.4 is locked whatever the outcome; the 0.2 residual is
    # the directional part, priced at its own basis, never at $1
    basis_up = lc["UP"] / D("0.6")
    matched = D("0.4") * (D(1) - basis_up - lc["DOWN"] / D("0.4"))
    residual_if_down = -D("0.2") * basis_up
    assert pnl_down == matched + residual_if_down


# ======================================================================
# 11. PR #219'S TWO INVARIANTS, UNDER THE CONTINUOUS CONTROLLER
# ======================================================================
def _stranded_down(tmp_path, monkeypatch, away):
    """DOWN filled in three clips, UP's opening quote still resting; then
    the book walks to `away`. Each completion sent must hold
    avg(DOWN) + price <= 1.00 per contract and qty <= DOWN held."""
    h = opened(rig(tmp_path, monkeypatch, L5_TS_COMPLETION_MAX_PAIR="0.98",
                   L5_INVENTORY_RECOVERY="ON"))
    down = oid_of(h, "DOWN")
    before = len(h.venue.sent)                  # the opening pair
    fill_and_book(h, down, 10.0, qty=D("0.2"))
    fill_and_book(h, down, 24.0, qty=D("0.4"))
    fill_and_book(h, down, 38.0)
    assert h.pos("DOWN") == D("0.6")
    h.book.set(*away)
    for t in (60.0, 80.0, 100.0):
        h.tick(t)
    return h, before


@pytest.mark.parametrize("away", [("0.55", "0.56"), ("0.88", "0.89")])
def test_pr219_requotes_never_exceed_held_basis_plus_price_ceiling(
        tmp_path, monkeypatch, away):
    h, before = _stranded_down(tmp_path, monkeypatch, away)
    avg = h.r.store.leg_cost(SLUG)["DOWN"] / h.pos("DOWN")
    assert avg > 0
    # the missing leg WAS worked: at least one completion went out
    assert len(h.sent()) > before
    for side, price, qty in h.sent()[before:]:
        assert side == "UP"
        assert D(0) < qty <= h.pos("DOWN")
        assert avg + price <= D("1.00")
        assert avg + price <= D("0.98")
    assert h.r.store.orders_uncertain() is None
    assert h.pos("UP") <= h.pos("DOWN")


# ======================================================================
# 12. NO TAKER, NO SALE
# ======================================================================
def test_reduce_by_selling_is_evaluated_and_never_sent(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    tb = fill_and_book(h, oid_of(h, "UP"), 10.0)
    h.tick(tb + 3.5)
    ev = last(h)["evaluated"]["REDUCE_BY_SELLING"]
    assert ev["authorized"] is False and ev["side"] == "UP"
    for s in h.venue.sent:
        assert str(s["mode"]).upper() == "MAKER"
        assert "SELL" not in json.dumps(s, default=str).upper()


# ======================================================================
# 13. ISSUE #233 TRACK A: SCORED DISPOSITIONS, LIFECYCLE, HARD STOPS
# ======================================================================
def test_every_imbalance_decision_scores_all_dispositions(tmp_path,
                                                          monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    tb = fill_and_book(h, oid_of(h, "UP"), 10.0)
    n = len(passes(h))
    h.tick(tb + 3.5)
    sc = passes(h)[n]["evaluated"]["alternatives"]
    rows = sc["alternatives"]
    assert sc["basis"] == "INCREMENTAL_WORST_CASE_SETTLED_PNL"
    assert {"HOLD", "TAKER_COMPLETE", "SELL_REDUCE"} <= set(rows)
    assert rows["TAKER_COMPLETE"]["authorized"] is False
    assert rows["SELL_REDUCE"]["authorized"] is False
    assert sc["chosen"] in ("MAKER_REPRICE", "MAKER_COMPLETE",
                            "KEEP_RESTING", "HOLD")
    # UP is the residual, so every completion buys DOWN at an EXECUTABLE
    # price: the taker at 1 - YES bid, not at any mid
    assert rows["TAKER_COMPLETE"]["side"] == "DOWN"
    assert D(rows["TAKER_COMPLETE"]["price"]) == D(1) - D("0.52")
    assert rows["SELL_REDUCE"]["side"] == "UP"
    assert D(rows["SELL_REDUCE"]["price"]) == D("0.52")
    # and nothing that was evaluated-only reached the venue
    for s in h.venue.sent:
        assert str(s["mode"]).upper() == "MAKER"
    d = passes(h)[n]["decision"]
    for k in ("matched", "residual_qty", "residual_basis",
              "worst_outcome_pnl", "decision", "reason", "time_left_s",
              "order_states"):
        assert k in d, k


def test_scoring_uses_fill_cost_tier1_fee_and_no_rebate():
    sc = CI.score_alternatives(
        up_q=D(1), dn_q=D(0), up_cost=D("0.47"), dn_cost=D(0), bid="0.50",
        ask="0.52", tick=D("0.01"), bid_shares="0.4", ask_shares="10",
        maker=(D("0.48"), D(1)), haircut_ticks=1)
    r = sc["alternatives"]
    # held UP at 0.47: the losing-side worst case is -0.47
    assert D(sc["worst_settled_now"]) == D("-0.47")
    # maker completion of 1 DOWN at 0.48, one-tick haircut, no rebate:
    # worst after = 1 - 0.47 - 0.48 - 0.01 = 0.04
    assert D(r["MAKER_COMPLETE"]["worst_settled_after"]) == D("0.04")
    # the taker is depth-limited to the displayed 0.4 at 1 - bid = 0.50
    t = r["TAKER_COMPLETE"]
    assert D(t["qty"]) == D("0.4") and D(t["price"]) == D("0.50")
    assert D(t["taker_fee"]) == (D("0.095") * D("0.4") * D("0.5")
                                 * D("0.5")).quantize(D("0.0001"))


def test_operator_halt_places_nothing(tmp_path, monkeypatch):
    h = rig(tmp_path, monkeypatch)
    monkeypatch.setenv(R.LIVE_HALT_ENV, "1")
    h.at(6.5).l5_action()
    h.tick(6.8)
    assert h.venue.sent == []
    assert h.r.stats["continuous:OPERATOR_HALT"] >= 1


def test_watchdog_halt_gate_refuses_every_placement(tmp_path, monkeypatch):
    class Latched:
        def submissions_permitted(self):
            return False

        def refuse_reason(self):
            return "halt_asserted"

        def state(self):
            return {"record": {"reason": "watchdog"}}

    h = rig(tmp_path, monkeypatch)
    h.r.executor.halt_gate = Latched()
    h.at(6.5).l5_action()
    h.tick(6.8)
    assert h.venue.sent == []
    assert any(k.startswith("continuous:place:OPEN:") and "HALT" in k.upper()
               for k in h.r.stats), dict(h.r.stats)


def test_order_limit_is_a_hard_cap(tmp_path, monkeypatch):
    h = opened(rig(tmp_path, monkeypatch, L5_MAX_LIVE_ORDERS="1"))
    assert len(h.r.store.live_orders()) <= 1
    for t in (9.0, 12.0, 15.0):
        h.tick(t)
        assert len(h.r.store.live_orders()) <= 1


def test_window_rollover_places_nothing_and_summarises(tmp_path,
                                                       monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    n = len(h.venue.sent)
    # past the window's terminal: nothing may be placed in it
    h.tick(905.0)
    assert len(h.venue.sent) == n
    # the transport moves to the next window, which this experiment does
    # not name: the controller does not own it and places nothing
    nxt = "cpc-btc-updown-15m-2026-11-10-0215z"
    h.venue.market_slug = nxt
    assert R.Runner.continuous_owns(h.r, nxt) is False
    h.r.maybe_controller_open(h.t)
    assert len(h.venue.sent) == n
    # and on a window change the previous window gets exactly one summary
    h.r._ci_window_slug = SLUG
    h.r._ci_window_rollover(nxt, h.t)
    h.r._ci_window_rollover(nxt, h.t)
    s = [r for label, r in h.emitted if label == "L5_CI_WINDOW_SUMMARY"]
    assert len(s) == 1 and s[0]["market"] == SLUG
    assert s[0]["status"] in ("SETTLED", "UNSETTLED")


def test_order_lifecycle_is_recorded_with_measured_latencies(tmp_path,
                                                             monkeypatch):
    h = opened(rig(tmp_path, monkeypatch))
    tb = fill_and_book(h, oid_of(h, "UP"), 10.0)
    h.tick(tb + 3.5)
    h.tick(tb + 7.0)
    lc = [r for label, r in h.emitted if label == "L5_CI_ORDER_LIFECYCLE"]
    assert lc, "the filled UP and the cancelled DOWN both left the ledger"
    for r in lc:
        assert "submit_ack_latency_s" in r and "decision_t" in r
        assert r["terminal_observed_t"] >= r["resting_observed_t"]
    assert any("cancel_ack_latency_s" in r for r in lc)


def test_a_still_valid_opening_leg_is_never_cancelled():
    """The book comes TO a resting opening leg, but its pair with the other
    side's current quote is still inside 0.96: it is kept."""
    p = CI.decide_pass(
        market="m", bid="0.50", ask="0.52", tick="0.01", now_s=100.0,
        resting=[{"coid": "u", "side": "UP", "price": "0.50",
                  "remaining": "0.6", "submitted_s": 10.0},
                 {"coid": "d", "side": "DOWN", "price": "0.44",
                  "remaining": "0.6", "submitted_s": 10.0}],
        pair_ceiling=D("0.96"), cash_cap=D(5), max_worst_loss=D(5),
        residual_k=None, reprice_min_rest_s=14.0)
    # UP wanted 0.48, resting 0.50 (ahead); 0.50 + DOWN 0.46 = 0.96: valid
    assert not [a for a in p["actions"] if a["kind"] == "CANCEL"
                and a["side"] == "UP"]
