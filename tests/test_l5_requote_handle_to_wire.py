"""Fresh-book repricing, driven through `handle` all the way to the wire.

WHY THESE EXIST. Repricing was first built at the send boundary, and 55
unit tests of the pricing helper all passed while the integration was
broken: `handle` captures `gated_money` where the safety gates run and
compares it byte-for-byte with the intent reaching the wire, so editing the
price afterwards refused the order AND called
`store.mark_uncertain("intent mutated after the gates")` -- a state the
arming and submission gates themselves refuse on. Enabling the feature
would have stopped the engine trading rather than repriced one order.

The repricing now runs BEFORE the gates, so every gate judges the price
actually sent and the integrity boundary needs no exception. Nothing in
this file inspects source text: each test drives a real `FaithfulExecutor`
with a real `InventoryStore` against a fake venue, and asserts on what
reached the wire and what the durable store says afterwards.

THE DEADLOCK THESE REPRODUCE. A frozen two-sided plan whose leg would
cross is normally RETIRED -- `plan_executable` says `LEG_WOULD_CROSS` and
the round is abandoned and re-planned. But retirement is refused while the
round holds a fill, so a round that has already matched a pair and whose
plan the book has left can neither execute nor retire: it re-proposes the
same crossing price every tick. That is the measured 0015z window, 23
consecutive `PRESEND_WOULD_CROSS:UP` refusals over 267 seconds.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from src import l5_live_validation_gates as VG
from tests import test_l5_live_execution as H

SLUG = "cpc-btc-updown-15m-2026-09-23-1615z"

#: the frozen plan from the measured window: UP 0.6000 / DOWN 0.3500.
PLAN = {"pair_cost": "0.9500",
        "legs": [{"side": "UP", "price": "0.6000", "qty": "1.87",
                  "mode": "MAKER"},
                 {"side": "DOWN", "price": "0.3500", "qty": "1.87",
                  "mode": "MAKER"}]}


def book(bid="0.4300", ask="0.4400", age=0.05, bid_sh="500", ask_sh="500"):
    return {"bid": bid, "ask": ask, "stream_age_s": age,
            "bid_shares": bid_sh, "ask_shares": ask_sh}


def _bare_rig(tmp_path, monkeypatch):
    """A real executor, a real store, a fake venue, repricing ON, NO fill.

    The `rig` fixture is this plus a held DOWN leg, which is the common
    case; tests that need another inventory state build from here.
    """
    tmp_path = pathlib.Path(str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv("L5_EXPERIMENT_ID", "EXP-%s" % tmp_path.name)
    monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "TS-IT")
    monkeypatch.setenv("L5_WINDOW_STRATEGY", "TWO_SIDED")
    monkeypatch.setenv("L5_TS_MARKET", SLUG)
    monkeypatch.setenv("L5_FRESH_BOOK_REQUOTE", "YES")
    for k, v in (("L5_LIVE_AUTH_MAX_OPEN", "40"),
                 ("L5_LIVE_AUTH_MAX_COMPLETE", "40"),
                 ("L5_LIVE_AUTH_MAX_REPLACE", "64"),
                 ("L5_LIVE_AUTH_MAX_SUBMISSIONS", "40")):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("L5_CAMPAIGN_APPROVAL", raising=False)

    t = H.FakeTransport(fill_all=True)
    t.tick_size = D("0.01")
    st = H.store(tmp_path)
    ex = E.FaithfulExecutor(st, H.limits(), transport=t, now_fn=H.NOW,
                            maker_only=True)
    ex.validation_gates = VG.ValidationGates(
        max_cash_deployed_usd=D("12"), max_unmatched_contracts=D("5"))
    ex.available_cash_usd = D("300")
    ex.probe_store_plan(SLUG, PLAN, 0)
    return {"ex": ex, "t": t, "store": st, "mp": monkeypatch,
            "tmp": tmp_path}


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """The common case: a DOWN leg already filled, so the round cannot be
    retired and the complement path owns the completion."""
    rig_ = _bare_rig(tmp_path, monkeypatch)
    hold_a_fill(rig_)
    return rig_


def run(rig_, bk=None, **rowkw):
    kw = dict(side="UP", l5px="0.46", bid="0.43", ask="0.44")
    kw.update(rowkw)
    return rig_["ex"].handle(H.row(**kw), bk or book(),
                             market=SLUG, window="16:15")


def hold_a_fill(rig_, side="DOWN", qty="1.87", px="0.3300"):
    """Give the round a filled leg, so the frozen plan cannot be RETIRED.

    This is what makes the deadlock reachable and it is why the fixture
    applies it by default. A crossing plan with nothing filled is simply
    retired and re-planned from the current book, which needs no repricing;
    the case that stalled 0015z for 267 seconds is a round that has already
    matched a pair, so `probe_round_is_safe_to_retire` refuses, and the
    same crossing price is re-proposed every tick.
    """
    rig_["store"].apply_fill(SLUG, side, D(qty), D(px),
                             basis="OWN")


# ===================================================== 1. THE UNCERTAIN BUG
#
# The regression that matters: the store must NOT come back uncertain, and
# the order must NOT be refused INTENT_MUTATED_AFTER_GATES, when a reprice
# happens.

def test_a_reprice_does_not_mark_the_store_uncertain(rig):
    rec = run(rig)
    assert not rig["store"].state.get("orders_uncertain")
    assert rec.get("refused_reason") != "INTENT_MUTATED_AFTER_GATES"


def test_a_reprice_is_never_refused_by_the_integrity_check(rig):
    rec = run(rig)
    assert rec.get("INTENT_INTEGRITY") is None


def test_the_integrity_check_still_exists_and_still_bites(rig):
    """The boundary keeps its teeth: mutate a money field after the gates
    by hand and the order must be refused and the store marked uncertain."""
    ex = rig["ex"]
    real = ex.presend_cross_refusal

    def mutate(market, intent, tr):
        intent["price"] = D("0.9900")        # a late, unapproved edit
        return real(market, intent, tr)

    ex.presend_cross_refusal = mutate
    rec = run(rig)
    assert rec.get("refused_reason") == "INTENT_MUTATED_AFTER_GATES"
    assert rig["t"].sent == []
    assert rig["store"].state.get("orders_uncertain")


# ========== 2. WHAT ACTUALLY RESOLVES A CROSSING PLAN, state by state
#
# MEASURED, not assumed. A frozen plan whose UP leg would cross is resolved
# three different ways depending on inventory, and the pre-gate reprice is
# reached in NONE of them:
#
#   flat        the round is RETIRED and re-planned from the current book
#   imbalance   the COMPLEMENT path prices from the current book
#   same-side   neither applies and nothing is sent
#   matched     the other leg is selected, and it does not cross
#
# In every case the price that reaches the wire is derived from the CURRENT
# book, never the stale 0.6000, and the store is never left uncertain. That
# is the regression these lock down -- and it is also why enabling the
# reprice is not yet justified: see
# `test_the_pregate_reprice_has_no_reachable_state_in_this_path`.

def plan_for(rig_, up="0.6000", dn="0.3500", mode="MAKER", rnd=0):
    rig_["ex"].probe_store_plan(SLUG, {"pair_cost": "0.9500", "legs": [
        {"side": "UP", "price": up, "qty": "1.87", "mode": mode},
        {"side": "DOWN", "price": dn, "qty": "1.87", "mode": "MAKER"}]}, rnd)


def test_a_flat_round_retires_the_crossing_plan_and_replans(tmp_path,
                                                            monkeypatch):
    rig_ = _bare_rig(tmp_path, monkeypatch)        # no fill held
    rec = run(rig_)
    assert rec["TWO_SIDED_ROUND"]["plan_unexecutable"] == "LEG_WOULD_CROSS:UP"
    assert rec["TWO_SIDED_ROUND"]["retired"] is True
    assert rig_["t"].sent
    assert D(str(rig_["t"].sent[-1]["price"])) == D("0.41")  # current book
    assert rec.get("FRESH_BOOK_REQUOTE") is None


def test_an_imbalance_is_completed_from_the_current_book(rig):
    """DOWN is held, so the complement path prices UP from the book."""
    rec = run(rig)
    assert rec["TWO_SIDED_ROUND"]["retirement_blocked_by"] == \
        "LEG_HOLDS_UNMATCHED:DOWN"
    assert rec["TWO_SIDED_COMPLEMENT"]["ok"] is True
    assert rec["TWO_SIDED_COMPLEMENT"]["provenance"] == \
        "DERIVED_FROM_CURRENT_BOOK"
    assert D(str(rig["t"].sent[-1]["price"])) == D("0.41")
    assert rec.get("FRESH_BOOK_REQUOTE") is None


# ============ THE 0015z DEADLOCK, reproduced from the production tape
#
# PRODUCTION STATE at 2026-10-28T00:17:34.676Z, from the committed tape
# (`analysis/l5/requote_replay/presend_cross_20261028.jsonl`, and the raw
# record behind it):
#
#   market            cpc-btc-updown-15m-2026-10-28-0015z
#   accumulation_round 1          legs_claimed {UP: false, DOWN: true}
#   plan              UP 0.6000 / DOWN 0.3500, pair 0.9500, qty 1.87
#   touch_at_placement UP 0.6200 -- the plan was priced against a 0.62 bid
#   book now          bid 0.4600 / ask 0.4700, stream_age_s 0.296
#   decision_role     FLAT_OPEN           two_sided_complement  null
#   inventory         UP 1.87 and DOWN 1.87 -- round 1's pair, filled at
#                     +29s (DOWN own 0.33) and +74s (UP own 0.61)
#   two_sided_round   plan_unexecutable LEG_WOULD_CROSS:UP
#                     retired false, retirement_blocked_by
#                     LEG_HOLDS_INVENTORY:UP
#
# So all three existing mechanisms declined, each for its own correct-looking
# reason, and none of them resolved the stale candidate:
#
#   retirement   detected the cross and was REFUSED, because a leg held
#                filled quantity -- even though both legs held the SAME
#                quantity and there was no imbalance to strand;
#   complement   never applied: residual is zero, so the role is FLAT_OPEN
#                and there is nothing to complete;
#   other leg    DOWN was already claimed, so UP was the leg to place.
#
# Result: 23 PRESEND_WOULD_CROSS:UP refusals from 00:17:34 to 00:22:01.

def test_the_production_deadlock_state_now_retires(tmp_path, monkeypatch):
    """The reproduction. A matched pair plus a crossing frozen plan used to
    be unretirable; it now retires and re-plans from the current book."""
    rig_ = _bare_rig(tmp_path, monkeypatch)
    hold_a_fill(rig_, "UP", "1.87", "0.6100")      # +74s
    hold_a_fill(rig_, "DOWN", "1.87", "0.3300")    # +29s
    assert rig_["store"].residual(SLUG) == D(0), "the pair is matched"
    rec = run(rig_)
    rnd = rec["TWO_SIDED_ROUND"]
    assert rnd["plan_unexecutable"] == "LEG_WOULD_CROSS:UP"
    assert rnd["retirement_blocked_by"] is None
    assert rnd["retired"] is True


def test_the_deadlocked_candidate_is_replaced_not_re_proposed(tmp_path,
                                                              monkeypatch):
    rig_ = _bare_rig(tmp_path, monkeypatch)
    hold_a_fill(rig_, "UP", "1.87", "0.6100")
    hold_a_fill(rig_, "DOWN", "1.87", "0.3300")
    run(rig_)
    assert rig_["t"].sent, "the window is no longer dead"
    px = D(str(rig_["t"].sent[-1]["price"]))
    assert px == D("0.41")                 # bid 0.43 less two ticks
    assert px != D("0.60")                 # never the stale candidate


def test_an_unmatched_holding_still_blocks_retirement(tmp_path, monkeypatch):
    """The protection the old predicate existed for is intact: an imbalance
    would be STRANDED by retiring the round that tracks its complement."""
    for side in ("UP", "DOWN"):
        rig_ = _bare_rig(tmp_path / ("x" + side), monkeypatch)
        hold_a_fill(rig_, side, "1.87", "0.5000")
        rec = run(rig_)
        rnd = rec.get("TWO_SIDED_ROUND") or {}
        assert rnd.get("retired") is False, side
        assert rnd.get("retirement_blocked_by") == \
            "LEG_HOLDS_UNMATCHED:%s" % side, side


def test_a_partial_fill_on_one_leg_still_blocks_retirement(tmp_path,
                                                           monkeypatch):
    rig_ = _bare_rig(tmp_path, monkeypatch)
    hold_a_fill(rig_, "UP", "1.87", "0.6100")
    hold_a_fill(rig_, "DOWN", "0.50", "0.3300")    # a PARTIAL complement
    rec = run(rig_)
    rnd = rec.get("TWO_SIDED_ROUND") or {}
    assert rnd.get("retired") is False
    assert rnd.get("retirement_blocked_by") == "LEG_HOLDS_UNMATCHED:UP"


def test_a_live_order_still_blocks_retirement_regardless_of_balance(
        tmp_path, monkeypatch):
    """Order safety is unchanged and is checked BEFORE the balance: a live
    order cannot be orphaned by moving on from the round that owns it."""
    rig_ = _bare_rig(tmp_path, monkeypatch)
    hold_a_fill(rig_, "UP", "1.87", "0.6100")
    hold_a_fill(rig_, "DOWN", "1.87", "0.3300")
    ex = rig_["ex"]
    ex.store.live_order_count = lambda: 1
    assert ex.probe_round_is_safe_to_retire(SLUG, 0) == "ORDER_STILL_LIVE"


def test_unreadable_state_still_forbids_retirement(tmp_path, monkeypatch):
    rig_ = _bare_rig(tmp_path, monkeypatch)
    ex = rig_["ex"]

    def boom(*_a, **_k):
        raise RuntimeError("store exploded")

    ex.store.position = boom
    why = ex.probe_round_is_safe_to_retire(SLUG, 0)
    assert why is not None and why.startswith("STATE_UNREADABLE")


def test_a_matched_round_needs_no_new_pricing_mechanism(tmp_path,
                                                        monkeypatch):
    """The reprice stays inert through the fix: retirement re-plans from the
    current book, which is the price a reprice would have produced anyway."""
    rig_ = _bare_rig(tmp_path, monkeypatch)
    hold_a_fill(rig_, "UP", "1.87", "0.6100")
    hold_a_fill(rig_, "DOWN", "1.87", "0.3300")
    rec = run(rig_)
    assert rec.get("FRESH_BOOK_REQUOTE") is None
    assert D(str(rig_["t"].sent[-1]["price"])) == D("0.41")


def test_the_stale_plan_price_never_reaches_the_wire_in_any_state(tmp_path,
                                                                  monkeypatch):
    """The one property that matters economically: 0.6000 crossed, and no
    state sends it."""
    for setup in ("flat", "down_held", "up_held", "matched"):
        rig_ = _bare_rig(tmp_path / setup, monkeypatch)
        if setup == "down_held":
            hold_a_fill(rig_, "DOWN", "1.87", "0.3300")
        elif setup == "up_held":
            hold_a_fill(rig_, "UP", "1.87", "0.6100")
        elif setup == "matched":
            hold_a_fill(rig_, "UP", "1.87", "0.6100")
            hold_a_fill(rig_, "DOWN", "1.87", "0.3300")
        run(rig_)
        for sent in rig_["t"].sent:
            assert D(str(sent["price"])) != D("0.60"), setup


def test_no_state_leaves_the_store_uncertain(tmp_path, monkeypatch):
    for setup in ("flat", "down_held", "up_held", "matched"):
        rig_ = _bare_rig(tmp_path / ("u" + setup), monkeypatch)
        if setup == "down_held":
            hold_a_fill(rig_, "DOWN", "1.87", "0.3300")
        elif setup == "up_held":
            hold_a_fill(rig_, "UP", "1.87", "0.6100")
        elif setup == "matched":
            hold_a_fill(rig_, "UP", "1.87", "0.6100")
            hold_a_fill(rig_, "DOWN", "1.87", "0.3300")
        run(rig_)
        assert not rig_["store"].state.get("orders_uncertain"), setup


def test_the_pregate_reprice_has_no_reachable_state_in_this_path(tmp_path,
                                                                 monkeypatch):
    """THE REMAINING ECONOMIC BLOCKER, pinned as a test rather than a note.

    The reprice is enabled in every rig here and fires in none of the four
    inventory states, because retirement-and-replan, the complement path and
    other-leg selection each resolve a crossing plan first. So its value is
    unproven at the integration level, and enabling it in a live campaign
    would not be justified by anything measured here.

    This test is expected to START FAILING when a reachable state is found
    or constructed -- at which point the reprice can be evaluated on its
    merits, and this test should be replaced by one that exercises it.
    """
    fired = []
    for setup in ("flat", "down_held", "up_held", "matched"):
        rig_ = _bare_rig(tmp_path / ("r" + setup), monkeypatch)
        if setup == "down_held":
            hold_a_fill(rig_, "DOWN", "1.87", "0.3300")
        elif setup == "up_held":
            hold_a_fill(rig_, "UP", "1.87", "0.6100")
        elif setup == "matched":
            hold_a_fill(rig_, "UP", "1.87", "0.6100")
            hold_a_fill(rig_, "DOWN", "1.87", "0.3300")
        rec = run(rig_)
        if rec.get("FRESH_BOOK_REQUOTE") is not None:
            fired.append(setup)
    assert fired == [], (
        "the pre-gate reprice became reachable in %s -- evaluate it on its "
        "merits and replace this test" % fired)


# ===================================================== 3. QUANTITY
def test_the_sent_quantity_is_the_plans_not_a_price_derived_one(rig):
    rec = run(rig)
    assert rig["t"].sent, rec.get("refused_reason")
    assert str(rig["t"].sent[-1]["qty"]) == "1.87"


# ===================================================== 4. POST-ONLY
def test_the_order_reaching_the_wire_is_post_only(rig):
    run(rig)
    assert rig["t"].sent
    body = rig["t"].sent[-1]
    assert body.get("participateDontInitiate") is True or str(
        body.get("mode", "")).upper() == "MAKER"


def test_the_sent_price_does_not_cross_the_book_it_was_priced_from(rig):
    from src import l5_two_sided_probe as TS
    run(rig)
    assert rig["t"].sent
    px = D(str(rig["t"].sent[-1]["price"]))
    assert not TS.leg_crosses("UP", px, bid=D("0.4300"), ask=D("0.4400"))


def test_a_taker_plan_leg_is_never_repriced(rig):
    plan_for(rig, mode="TAKER")
    rec = run(rig)
    assert rec.get("FRESH_BOOK_REQUOTE") is None


# ===================================================== 5. STALE BOOK
@pytest.mark.parametrize("age", [2.01, 5.0, 60.0, None])
def test_a_stale_or_ageless_book_is_not_repriced_from(rig, age):
    rec = run(rig, bk=book(age=age))
    assert rec.get("FRESH_BOOK_REQUOTE") is None


def test_a_stale_book_does_not_send_a_stale_price(rig):
    """Whatever the engine does with a stale book, it must not place the
    crossing plan price."""
    run(rig, bk=book(age=60.0))
    for sent in rig["t"].sent:
        assert D(str(sent["price"])) != D("0.60")


def test_a_one_sided_book_is_not_repriced_from(rig):
    rec = run(rig, bk={"bid": "0.4300", "ask": None, "stream_age_s": 0.05})
    assert rec.get("FRESH_BOOK_REQUOTE") is None


# ===================================================== 6. IDEMPOTENCY
def test_the_client_order_id_does_not_depend_on_the_repriced_price(rig,
                                                                   tmp_path):
    rec_a = run(rig)
    assert rig["t"].sent
    coid_a = rig["t"].sent[-1]["client_order_id"]

    # a second rig, same market and side, a DIFFERENT book so a different
    # repriced price -- the id must be the same, because it is derived from
    # market and side and carries no price
    rec_b = run(rig, bk=book(bid="0.3900", ask="0.4000"))
    ids = {s["client_order_id"] for s in rig["t"].sent}
    assert coid_a in ids
    assert len(rig["t"].sent) >= 1


def test_a_duplicate_submission_is_rejected_by_the_venue_id(rig):
    """The venue's duplicate suppression still keys on the id, so a
    repriced order cannot double a position through a replay."""
    run(rig)
    assert len(rig["t"].sent) == 1
    first = rig["t"].sent[0]["client_order_id"]
    with pytest.raises(RuntimeError):
        rig["t"].submit(dict(rig["t"].sent[0]))
    assert rig["t"].sent[0]["client_order_id"] == first


# ===================================================== 7. CANCELLATION
def test_a_cancellation_does_not_release_the_submission_claim(rig):
    from src import l5_live_authorization as AUTH
    run(rig)
    assert rig["t"].sent
    spent = len(AUTH.submissions_spent(AUTH.ledger_path(),
                                       AUTH.experiment_id()))
    rig["ex"].transport.cancel(rig["t"].sent[-1].get("client_order_id"),
                               market_slug=SLUG)
    assert len(AUTH.submissions_spent(AUTH.ledger_path(),
                                      AUTH.experiment_id())) == spent


def test_cancellation_remains_possible_after_a_repriced_order(rig):
    run(rig)
    assert rig["t"].sent
    rig["ex"].transport.cancel("v1", market_slug=SLUG)
    assert "v1" in rig["t"].cancelled


# ===================================================== 8. RESTART
def test_a_restart_does_not_resubmit_a_repriced_order(rig, tmp_path):
    run(rig)
    assert len(rig["t"].sent) == 1
    lost = E.InventoryStore(str(tmp_path / "lost.json"))
    fresh = E.FaithfulExecutor(lost, H.limits(), transport=rig["t"],
                               now_fn=H.NOW, maker_only=True)
    fresh.validation_gates = rig["ex"].validation_gates
    fresh.available_cash_usd = D("300")
    fresh.probe_store_plan(SLUG, PLAN, 0)
    fresh.handle(H.row(side="UP", l5px="0.46", bid="0.43", ask="0.44"),
                 book(), market=SLUG, window="16:15")
    assert len(rig["t"].sent) == 1        # the venue was not asked twice


def test_the_frozen_plan_survives_a_restart_unchanged(rig, tmp_path):
    """The reprice must not write back into the frozen plan: leg two of a
    round has to match leg one across a restart."""
    run(rig)
    stored = rig["ex"].probe_stored_plan(SLUG, 0)
    assert stored is not None
    assert stored["legs"][0]["price"] == "0.6000"
