"""The execution path, including the ways it must refuse to work.

The account-protecting properties are tested as hard as the happy path,
because each of these failures loses real money quietly: a duplicate order
after a crash, an inventory file trusted over the venue, a stale action
priced against a book that has moved, or a safety refusal misfiled as
evidence about L5.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from src import l5_faithful_translation as FT


def row(key="a", side="UP", l5px="0.65", size=10.0, ask="0.60", bid="0.40",
        ask_sh="500", bid_sh="500", receipt="2026-09-23T16:15:00+00:00"):
    return {
        "action_key": key, "kind": "l5_action", "backlog": False,
        "l5_event_utc": "2026-09-23T16:15:00+00:00", "receipt_utc": receipt,
        "window_start_utc": "2026-09-23T16:15:00+00:00",
        "pmus_snapshot_age_s": 0.5,
        "l5": {"pmus_side": side, "l5_price": l5px, "l5_size": size},
        "pmus": {"bbo": {"bid": bid, "ask": ask,
                         "bid_shares": bid_sh, "ask_shares": ask_sh}},
        "copyability": {"l5_price": l5px},
    }


class FakeTransport:
    """An armed transport, for tests only. Records what it was asked to do."""

    armed = True
    name = "FAKE"
    #: the venue's own quantity grid, as this market's preflight
    #: reported it on 2026-09-25. A double without it is not
    #: modelling the transport -- and an unfaithful double is how
    #: the unquantized quantity reached the wire in the first place.
    quantity_increment = D("0.01")

    def __init__(self, fill_qty="1", fill_price=None, reject_dupes=True,
                 fill_all=False):
        self.sent, self.cancelled = [], []
        #: ids this venue has retired, so a re-read can answer
        self.retired = set()
        #: what each order's ACK said it filled, so the re-read agrees with
        #: it. The venue is one witness, not two contradicting ones.
        self.acked_fill = {}
        self.fill_qty, self.fill_price = fill_qty, fill_price
        self.reject_dupes = reject_dupes
        #: fill the WHOLE intent, so the order reaches a terminal state and
        #: releases the one-live-order slot. The default fills 1 of a larger
        #: quantity, which leaves the order resting -- still exposure.
        self.fill_all = fill_all

    def submit(self, intent):
        if self.reject_dupes and any(
                s["client_order_id"] == intent["client_order_id"]
                for s in self.sent):
            raise RuntimeError("DUPLICATE_CLIENT_ORDER_ID")
        self.sent.append(dict(intent))
        oid = "v%d" % len(self.sent)
        self.acked_fill[oid] = (str(intent["qty"]) if self.fill_all
                                else self.fill_qty)
        return {"order_id": oid,
                "filled_qty": self.acked_fill[oid],
                "fill_price": self.fill_price or str(intent["price"])}

    def cancel(self, order_id):
        """The venue cancels by ITS id, so the double must demand one too."""
        self.cancelled.append(order_id)
        self.retired.add(order_id)
        return {"cancelled": order_id}

    def order(self, order_id):
        """What a re-read says. A cancel ack is not proof; this is.

        Added when `deadman` stopped trusting its own ack: a double with no
        /v1/order route cannot express the contract under test.

        It reports the SAME cumQuantity its own ACK reported. An earlier
        version answered 0 for every order and the store rightly refused it
        with CUM_QTY_WENT_BACKWARDS -- a venue cannot un-fill a fill, so a
        double that says it did is modelling nothing real.
        """
        if order_id not in self.retired:
            raise KeyError(order_id)
        cum = self.acked_fill.get(order_id, "0")
        return {"id": order_id, "state": "ORDER_STATE_CANCELED",
                "cumQuantity": str(cum), "leavesQuantity": "0",
                "avgPx": {"value": "0", "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"}}

    def open_orders(self):
        return []

    def positions(self):
        return []


#: rows carry a fixed receipt, so the clock must be pinned beside it --
#: otherwise every test trips the staleness guard, which is correct
#: behaviour and not what these tests are about.
NOW = lambda: dt.datetime(2026, 9, 23, 16, 15, 5, tzinfo=dt.timezone.utc)


def store(tmp_path):
    return E.InventoryStore(str(tmp_path / "inv.json"))


def limits(**kw):
    base = dict(max_usd_per_market=D("100"), max_abs_residual=D("100"),
                max_total_exposure_usd=D("100"), max_concurrent_markets=5,
                daily_realized_loss_halt_usd=D("100"))
    base.update(kw)
    return E.SafetyLimits(**base)


# ---------------------------------------------------------------- cannot fire

def test_the_default_engine_cannot_transmit(tmp_path):
    ex = E.FaithfulExecutor(store(tmp_path), limits(), now_fn=NOW)
    rec = ex.handle(row(), {}, market="m", window="16:15")
    assert rec["ORDER"]["submitted"] is False
    assert rec["ORDER"]["reason"] == "NOT_ARMED"
    # the intent is preserved so it can be reviewed without being sent
    assert rec["ORDER"]["intent"]["side"] == "UP"


def test_null_transport_raises_rather_than_silently_dropping():
    with pytest.raises(E.NotArmed):
        E.NullTransport().submit({"a": 1})


def test_not_armed_is_not_recorded_as_a_refusal_by_a_behavioural_layer(tmp_path):
    ex = E.FaithfulExecutor(store(tmp_path), limits(), now_fn=NOW)
    rec = ex.handle(row(), {}, market="m", window="16:15")
    assert rec["refused_by_layer"] is None      # nothing refused it; we are unarmed
    assert rec["PMUS_TRANSLATION"]["ok"] is True


# ------------------------------------------------------------- idempotency

def test_the_client_order_id_is_derived_not_random():
    assert E.client_order_id("k1") == E.client_order_id("k1")
    assert E.client_order_id("k1") != E.client_order_id("k2")
    assert E.client_order_id("k1", 0) != E.client_order_id("k1", 1)


def test_a_replayed_action_does_not_double_the_position(tmp_path):
    # fill_all so the first order is terminal and the one-live-order slot is
    # free: this test is about id-based replay suppression, not the envelope.
    st, t = store(tmp_path), FakeTransport(fill_all=True)
    ex = E.FaithfulExecutor(st, limits(), transport=t, now_fn=NOW)
    r = row()
    first = ex.handle(r, {}, market="m", window="16:15")
    second = ex.handle(r, {}, market="m", window="16:15")
    assert first["ORDER"]["submitted"] is True
    assert second["ORDER"]["submitted"] is False
    assert second["ORDER"]["reason"] == "DUPLICATE_SUPPRESSED"
    assert len(t.sent) == 1


@pytest.mark.multi_submission
def test_a_crash_between_submit_and_save_still_cannot_double(tmp_path):
    # the id is derived from the action, so even a store that lost the record
    # produces the same id and the VENUE rejects the duplicate. The rejection
    # no longer escapes as an exception: an unknown submit outcome fails
    # CLOSED, so exactly one order exists at the venue and the session stops
    # submitting until the venue is asked authoritatively.
    t = FakeTransport(fill_all=True)
    ex = E.FaithfulExecutor(store(tmp_path), limits(), transport=t, now_fn=NOW)
    ex.handle(row(), {}, market="m", window="16:15")
    lost = E.InventoryStore(str(tmp_path / "lost.json"))
    fresh = E.FaithfulExecutor(lost, limits(), transport=t, now_fn=NOW)
    rec = fresh.handle(row(), {}, market="m", window="16:15")
    assert len(t.sent) == 1                       # the venue got exactly one
    assert rec["ORDER"]["reason"] == "SUBMIT_OUTCOME_UNKNOWN"
    assert rec["refused_reason"] == "SUBMIT_OUTCOME_UNKNOWN"
    assert lost.orders_uncertain()
    # and nothing further is submitted while that uncertainty stands
    again = fresh.handle(row(key="other"), {}, market="m", window="16:15")
    assert again["refused_reason"] == "ORDER_STATE_UNCERTAIN"
    assert len(t.sent) == 1


# ------------------------------------------------------------------- staleness

def test_a_stale_action_is_refused(tmp_path):
    late = lambda: dt.datetime(2026, 9, 23, 16, 20, tzinfo=dt.timezone.utc)
    ex = E.FaithfulExecutor(store(tmp_path), limits(),
                            transport=FakeTransport(), now_fn=late)
    rec = ex.handle(row(), {}, market="m", window="16:15")
    assert rec["refused_by_layer"] == "PMUS_TRANSLATION"
    assert rec["refused_reason"].startswith("STALE_ACTION")


# ----------------------------------------------------------------- hard safety

@pytest.mark.parametrize("kw,expect", [
    (dict(max_usd_per_market=D("0.01")), "MAX_USD_PER_MARKET"),
    (dict(max_total_exposure_usd=D("0.01")), "MAX_TOTAL_EXPOSURE"),
    (dict(max_abs_residual=D("0")), "MAX_ABS_RESIDUAL"),
])
def test_each_hard_limit_refuses_and_names_itself(tmp_path, kw, expect):
    ex = E.FaithfulExecutor(store(tmp_path), limits(**kw),
                            transport=FakeTransport(), now_fn=NOW)
    rec = ex.handle(row(), {}, market="m", window="16:15")
    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"
    assert rec["refused_reason"] == expect


def test_concurrent_market_limit(tmp_path):
    ex = E.FaithfulExecutor(store(tmp_path), limits(max_concurrent_markets=1),
                            transport=FakeTransport(), now_fn=NOW)
    rec = ex.handle(row(), {}, market="m2", window="16:15",
                    open_markets=["m1"])
    assert rec["refused_reason"] == "MAX_CONCURRENT_MARKETS"


def test_daily_loss_halt_blocks_everything(tmp_path):
    # The halt measures the CURRENT UTC DAY, account-wide, so the fixture is
    # a settlement that lost $6 TODAY rather than a bare all-time scalar. It
    # is deliberately UNATTESTED: a manual or unknown loss today must trip
    # the halt exactly as an L5 one would.
    st = store(tmp_path)
    today = E.dt.datetime.now(E.dt.timezone.utc).strftime("%Y-%m-%d")
    st.state["realized_unattributed"] = "0"
    st.state["retired_markets"] = {
        "loss-today": {"t": today + "T00:30:00+00:00",
                       "settlement_pnl": "-6",
                       "l5_owner_attribution_verified": False}}
    ex = E.FaithfulExecutor(st, limits(daily_realized_loss_halt_usd=D("5")),
                            transport=FakeTransport(), now_fn=NOW)
    rec = ex.handle(row(), {}, market="m", window="16:15")
    assert rec["refused_reason"] == "DAILY_REALIZED_LOSS_HALT"


def test_a_safety_refusal_never_reads_as_l5_or_venue(tmp_path):
    ex = E.FaithfulExecutor(store(tmp_path), limits(max_abs_residual=D("0")),
                            transport=FakeTransport(), now_fn=NOW)
    rec = ex.handle(row(), {}, market="m", window="16:15")
    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"
    # the venue said yes, and L5's decision is intact on the record
    assert rec["PMUS_TRANSLATION"]["ok"] is True
    assert rec["L5_NATIVE"]["side"] == "UP"
    assert "never a statement about L5" in rec["SAFETY_OVERLAY"]["note"]


def test_no_price_band_is_imposed_by_default(tmp_path):
    # a band is a behavioural preference, not account protection. L5 traded
    # at 0.03 and the faithful engine must not silently refuse that.
    ex = E.FaithfulExecutor(store(tmp_path), limits(),
                            transport=FakeTransport(), now_fn=NOW)
    rec = ex.handle(row(l5px="0.05", ask="0.03"), {}, market="m",
                    window="16:15")
    assert rec["refused_by_layer"] is None
    assert rec["ORDER"]["submitted"] is True


@pytest.mark.multi_submission
def test_the_behaviour_our_old_heuristics_disliked_is_not_suppressed(tmp_path):
    # repeated same side, then the opposite, then a >$1 combined cost
    # each order fills completely, so the one-live-order slot is free for the
    # next: the point here is that no BEHAVIOURAL heuristic suppresses L5's
    # same-side repeat or its opposite leg. Account protection serialising
    # orders is a different thing and is tested separately.
    st, t = store(tmp_path), FakeTransport(fill_qty="1", fill_all=True)
    ex = E.FaithfulExecutor(st, limits(max_usd_per_market=D("1000"),
                                       max_total_exposure_usd=D("1000"),
                                       max_abs_residual=D("1000")),
                            transport=t, now_fn=NOW)
    for i, (side, px, ask, bid) in enumerate([
            ("UP", "0.70", "0.65", "0.60"), ("UP", "0.72", "0.66", "0.61"),
            ("DOWN", "0.60", "0.90", "0.45")]):
        ex.handle(row(key="k%d" % i, side=side, l5px=px, ask=ask, bid=bid),
                  {}, market="m", window="16:15")
    assert len(t.sent) == 3
    assert st.position("m", "UP") > 0
    assert st.position("m", "DOWN") > 0


# -------------------------------------------------------------- reconciliation

def test_the_venue_contradicting_our_file_is_reported_and_fails_closed(tmp_path):
    """REVERSED AGAIN, 2026-10-14, by owner authorization of Priority A.

    THE HISTORY MATTERS, so it is kept. On 2026-09-26 this contract was
    reversed to "nothing is silently replaced", because adopting the venue's
    net left `leg_cost` describing shares that were gone -- which is how
    1815z settled 1.81 shares against $4.3425 of cost, $2.40 a share for a
    contract that pays at most $1. That reasoning was correct about the harm
    and wrong about the remedy: keeping our book meant `positions` -- what
    `risk_exposure_usd` and the $5 gate read -- could sit BELOW what the
    venue said we held. Measured 2026-10-14: our 7.00 UP against a venue row
    of 10.34 UP understated exposure by 3.34 shares.

    So the venue is the position of record again, and the 1815z harm is
    prevented structurally rather than by declining to adopt:
    `adopt_venue_position` quarantines the displaced gross book AND its cost
    basis, then re-books the adopted shares at the $1 upper bound. No
    `leg_cost` entry survives describing shares that are gone -- which this
    test now asserts directly, because that is the property the 2026-09-26
    reversal actually wanted.

    Fail-closed is unchanged: the drift is reported and the book is uncertain.
    """
    st = store(tmp_path)
    st.state["positions"] = {"m|UP": "5"}
    st.state["leg_cost"] = {"m|UP": "4.3425"}      # the 1815z shape
    # the venue's real row: keyed by slug, no side field, direction in the
    # sign of the net with the unsigned halves beside it to prove it
    out = st.reconcile_from_venue(
        [{"marketSlug": "m", "qtyBoughtDecimal": "3", "qtySoldDecimal": "0",
          "netPositionDecimal": "3"}], [])
    assert out["authority"] == "VENUE"
    assert out["drift"]["m|UP"] == {"ours": "5", "venue": "3"}
    # the venue is the authority on what exists
    assert st.position("m", "UP") == D("3")
    # and the 1815z failure cannot recur: the stale basis is gone from the
    # live book, preserved as evidence, and the adopted shares are bounded
    assert st.state["gross_quarantine"]["m"]["leg_cost"] == {"m|UP": "4.3425"}
    assert st.leg_cost("m")["UP"] == D("3")        # 3 shares at the $1 bound
    assert "m|UP" in st.state["leg_cost_basis_unknown"]
    # never below what we already believed we could lose
    assert st.risk_exposure_usd("m") >= D("4.3425")
    assert st.orders_uncertain() is not None


def test_inventory_survives_restart(tmp_path):
    st = store(tmp_path)
    st.apply_fill("m", "UP", D("2"), D("0.5"), basis=E.OWN)
    again = E.InventoryStore(str(tmp_path / "inv.json"))
    assert again.position("m", "UP") == D("2")
    assert again.committed()["m"] == D("1.0")


# ------------------------------------------------------------------ settlement

def test_realized_requires_settlement_and_keeps_fees_separate():
    recs = [
        {"L5_NATIVE": {"side": "UP"},
         "FILL": {"filled": True, "filled_qty": "2", "fill_price": "0.40",
                  "fee_usd": "0.01", "maker_rebate_usd": "0"}},
        {"L5_NATIVE": {"side": "DOWN"},
         "FILL": {"filled": True, "filled_qty": "2", "fill_price": "0.50",
                  "fee_usd": "0", "maker_rebate_usd": "0.02"}},
    ]
    out = E.settle(recs, market="m", yes_value=D("1"))
    assert out["matched"] == "2" and out["residual"] == "0"
    # pair cost 0.90 on 2 matched -> +0.20
    assert D(out["matched_pair_pnl_usd"]) == D("0.20")
    assert out["taker_fees_usd"] == "0.01"
    assert out["maker_rebate_usd"] == "0.02"
    assert D(out["realized_net_usd"]) == D("0.21")


def test_unfilled_orders_never_reach_settlement():
    recs = [{"L5_NATIVE": {"side": "UP"}, "FILL": {"filled": False}}]
    out = E.settle(recs, market="m", yes_value=D("1"))
    assert out["matched"] == "0" and out["realized_net_usd"] == "0"


# --------------------------------------------------------------------- deadman

def test_deadman_cancels_everything_it_believes_live(tmp_path):
    """By the VENUE's id, and only once the venue's re-read agrees.

    Rewritten with the fix: the old version passed when the deadman cancelled
    by CLIENT order id, which the venue route ignores, and counted a
    non-raising ack as a cancellation. Both are the defects, so the old
    assertions could not be kept.
    """
    st, t = store(tmp_path), FakeTransport()
    ex = E.FaithfulExecutor(st, limits(), transport=t, now_fn=NOW)
    ex.handle(row(), {}, market="m", window="16:15")
    coid = next(iter(st.live_orders()))
    vid = st.live_orders()[coid]["venue_order_id"]
    out = E.deadman(st, t, reason="TEST")
    assert t.cancelled == [vid] and coid not in t.cancelled
    assert len(out["cancelled"]) == 1 and not out["errors"]
    assert out["cancelled"][0]["venue_order_id"] == vid
    assert not st.orders_uncertain()


def test_deadman_is_safe_when_nothing_is_armed(tmp_path):
    """And the submit LOG is not the cancel list.

    The old test asserted the opposite -- that an entry in `submitted` with
    no live order produced a cancel attempt and an error. That is the bug:
    `submitted` is permanent history, so cancelling from it re-cancels every
    order this ledger has ever sent, at every window end, forever.
    """
    st = store(tmp_path)
    st.state["submitted"] = {"x": {}}          # history, long since terminal
    out = E.deadman(st, E.NullTransport(), reason="TEST")
    assert out["cancelled"] == [] and out["errors"] == []
    assert out["still_open"] == [] and out["no_venue_id"] == []
    assert not st.orders_uncertain(), "history is not uncertainty"


# -------------------------------------------------------------------- exposure

def test_worst_case_loss_is_stated_not_guessed():
    lim = E.SafetyLimits(max_total_exposure_usd=D("10"),
                         daily_realized_loss_halt_usd=D("5"))
    assert lim.worst_case_loss_usd() == D("10")


def test_the_provenance_chain_covers_every_stage(tmp_path):
    ex = E.FaithfulExecutor(store(tmp_path), limits(),
                            transport=FakeTransport(), now_fn=NOW)
    rec = ex.handle(row(), {}, market="m", window="16:15")
    for stage in ("L5_NATIVE", "PMUS_TRANSLATION", "SAFETY_OVERLAY",
                  "ORDER", "FILL", "INVENTORY"):
        assert stage in rec
    assert rec["stage_reached"] == "INVENTORY"
    assert rec["action_key"] == "a"
