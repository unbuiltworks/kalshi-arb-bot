"""One BTC 5-minute window from first order to settlement, against the
scripted venue, with another strategy's orders resting the whole time.

The adapter's separate behaviours are each tested in
tests/test_l5_btc5m_execution.py. This file is the single ordered story the
integration review asked for: submit -> partial fill -> cancel -> reprice
-> hedge -> fills -> end of window -> settlement, asserting the EXACT calls
that reach the venue at each step, and that an order belonging to the
15-minute strategy (or placed by hand) is never cancelled, whatever asks.

Deterministic and offline, under a real owner approval for the window (the
real approval gate, window ledger and single-use claims; see
`authorize_5m`). Nothing here is evidence about a real venue or about
economics, and the settlement figures are arithmetic on scripted fills, not
realized profit.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from tests import test_l5_btc5m_execution as T

X = T.X
MARKET, M15 = T.MARKET, T.M15
L5F_COID = "l5f-" + "1" * 24


@pytest.fixture(autouse=True)
def armed_env(monkeypatch, tmp_path):
    for k in T.ENV:
        monkeypatch.delenv(k, raising=False)
    T.authorize_5m(monkeypatch, tmp_path, now_fn=lambda: T.T0,
                   starts=(T.W_1200, T.W_1205))
    for k, v in T.ARMED.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def rig(tmp_path):
    r = T.Rig(tmp_path)
    # v1: a resting order of the 15-minute strategy. v2: a hand-placed
    # order in OUR OWN 5-minute market. Neither is in the adapter's ledger.
    r.venue.submit({"client_order_id": L5F_COID, "market": M15,
                    "side": "UP", "price": "0.50", "qty": "0.6"})
    r.venue.submit({"client_order_id": "manual-1", "market": MARKET,
                    "side": "DOWN", "price": "0.30", "qty": "1"})
    return r


def ours(rig):
    """What the adapter itself sent: (side, price, qty), in order."""
    return rig.sent()[2:]


def strangers_untouched(rig):
    open_ids = [o["id"] for o in rig.venue.open_orders()]
    assert "v1" in open_ids and "v2" in open_ids
    assert all(oid not in ("v1", "v2") for oid, _m in rig.venue.cancelled)


def test_one_window_submit_partial_cancel_reprice_hedge_settle(rig, monkeypatch):
    v = rig.venue

    # 1. SUBMIT. One post-only maker buy on the favorite, two ticks under
    #    the bid, on the 5-minute market, under the adapter's own id prefix.
    rep = rig.step(t=30.0)
    assert rep["action"] == C.OPEN_FAVORITE
    assert ours(rig) == [("UP", D("0.58"), D("0.17"))]
    first = v.sent[2]
    assert first["market"] == MARKET and first["post_only"] is True
    assert first["client_order_id"].startswith(X.COID_PREFIX)
    assert v.cancelled == []

    # 2. PARTIAL FILL. 0.05 of 0.17 fills at 0.58: booked once at exact
    #    cost, the rest stays resting, and the unmatched 0.05 is hedged.
    v.fill("v3", "0.05")
    rep = rig.tick(5).step(t=35.0)
    assert [(f["side"], f["qty"], f["price"], f["cost"]) for f in rep["fills"]] == [
        ("UP", D("0.05"), D("0.58"), D("0.0290"))]
    assert rep["action"] == C.HEDGE_UNDERDOG
    assert ours(rig)[1:] == [("DOWN", D("0.38"), D("0.05"))]
    assert v.cancelled == []                       # the entry was kept
    assert rep["inventory"]["UP"] == {"qty": D("0.05"), "cost": D("0.029")}

    # 3. CANCEL + REPRICE. The book moves up two ticks. Both of OUR resting
    #    orders are cancelled by their exact venue ids on OUR market, each
    #    is read back as terminal, and only then is the entry re-placed.
    rep = rig.tick(5).step(bid="0.62", ask="0.64", p_up="0.78", t=40.0)
    assert v.cancelled == [("v3", MARKET), ("v4", MARKET)]
    assert [(c["venue_order_id"], c["terminal"]) for c in rep["cancelled"]] == [
        ("v3", True), ("v4", True)]
    assert rep["action"] == C.ADD_FAVORITE
    assert ours(rig)[2:] == [("UP", D("0.60"), D("0.16"))]
    # the cancelled entry keeps the 0.05 it filled; nothing is booked twice
    assert rep["fills"] == []
    assert rep["inventory"]["UP"] == {"qty": D("0.05"), "cost": D("0.029")}

    # 4. The hedge is restored at the new price on the next decision, not
    #    in the step that cancelled the old one (a cancel can lose its race).
    rep = rig.tick(5).step(bid="0.62", ask="0.64", p_up="0.78", t=45.0)
    assert rep["action"] == C.HEDGE_UNDERDOG
    assert ours(rig)[3:] == [("DOWN", D("0.36"), D("0.05"))]
    assert len(v.cancelled) == 2

    # 5. ANOTHER STRATEGY'S ORDER IS NEVER CANCELLED, whatever asks: by its
    #    venue id, by its client id, or by an id shaped like ours that this
    #    ledger never wrote.
    with monkeypatch.context() as m:
        T.scripted(m, {"op": "CANCEL", "id": "v1"},
                   {"op": "CANCEL", "id": L5F_COID},
                   {"op": "CANCEL", "id": "v2"},
                   {"op": "CANCEL", "id": X.COID_PREFIX + "a" * 24})
        rep = rig.tick(1).step(bid="0.62", ask="0.64", p_up="0.78", t=46.0)
    assert T.reasons(rep) == ["NOT_OWNED"] * 4
    assert rep["cancelled"] == [] and len(v.cancelled) == 2
    strangers_untouched(rig)

    # 6. FILLS. The hedge fills in full (a matched 0.05 pair at 0.58 + 0.36)
    #    and the re-priced entry fills 0.10 of 0.16.
    v.fill("v6")
    v.fill("v5", "0.10")
    got = rig.ex.poll()
    assert sorted((f["side"], f["qty"], f["cost"]) for f in got["fills"]) == [
        ("DOWN", D("0.05"), D("0.0180")), ("UP", D("0.10"), D("0.0600"))]
    inv = rig.ex.inventory(MARKET)
    assert inv["UP"] == {"qty": D("0.15"), "cost": D("0.089")}
    assert inv["DOWN"] == {"qty": D("0.05"), "cost": D("0.018")}

    # 7. END OF WINDOW. Only what this ledger still has resting is
    #    cancelled: the remainder of v5. The strangers are still resting.
    rep = rig.ex.end_of_window(MARKET)
    assert [c["venue_order_id"] for c in rep["cancelled"]] == ["v5"]
    assert v.cancelled == [("v3", MARKET), ("v4", MARKET), ("v5", MARKET)]
    assert rep["still_open"] == rep["unresolved"] == []
    assert rig.ex.live_orders() == {}
    strangers_untouched(rig)
    assert [o["id"] for o in v.open_orders()] == ["v1", "v2"]

    # 8. SETTLEMENT, at exact cost. UP 0.15 for 0.089, DOWN 0.05 for 0.018:
    #    0.05 matched pairs and 0.10 unmatched UP, total cost 0.107. The
    #    pair cost is stated at AVERAGE cost per side (0.089 / 0.15 + 0.36),
    #    not by pairing particular fills; the P&L does not depend on it.
    lost = rig.ex.settlement_arithmetic(MARKET, False)   # the other outcome
    assert lost["redemption_total"] == D("0.05")
    assert lost["settlement_pnl"] == D("-0.057")
    won = rig.ex.settle(MARKET, True)
    assert won["provisional"] is False
    assert won["matched_qty"] == D("0.05")
    assert won["pair_cost"] == D("0.089") / D("0.15") + D("0.36")
    assert (won["residual_side"], won["residual_qty"]) == ("UP", D("0.10"))
    assert won["cost_total"] == D("0.107")
    assert won["redemption_total"] == D("0.15")
    assert won["settlement_pnl"] == D("0.043")
    assert won["origin"] == "BTC5M"
    with pytest.raises(ValueError):                      # recorded once
        rig.ex.settle(MARKET, False)
    # the figure is labelled as not yet realized
    assert "realized only when the venue credits" in won["label"]

    # THE WHOLE WINDOW, as the venue saw it: five orders of ours, every one
    # a post-only buy on the 5-minute market under our prefix; three
    # cancels, all of our own ids; nothing else.
    assert ours(rig) == [("UP", D("0.58"), D("0.17")),
                         ("DOWN", D("0.38"), D("0.05")),
                         ("UP", D("0.60"), D("0.16")),
                         ("DOWN", D("0.36"), D("0.05"))]
    for s in v.sent[2:]:
        assert s["market"] == MARKET and s["post_only"] is True
        assert s["client_order_id"].startswith(X.COID_PREFIX)
        assert not s["client_order_id"].startswith("l5f-")
    assert len({s["client_order_id"] for s in v.sent}) == len(v.sent)
    # and each of the four spent exactly one durable claim, of the class of
    # what it did: open, hedge, re-price, hedge
    assert [r["klass"] for r in sorted(rig.ex.orders().values(),
                                       key=lambda r: r["created_at"])] == [
        "OPEN", "COMPLETE", "REPLACE", "COMPLETE"]
    for s in v.sent[2:]:
        assert T.AUTH.claim_held(T.AUTH.btc5m_sid(
            T.AUTH.experiment_id(), s["client_order_id"]))


def test_a_restart_in_the_middle_of_the_window_changes_nothing(rig):
    """Same window, with the process replaced after the partial fill and
    the hedge: the ledger on disk carries the inventory and the resting
    orders, and nothing is sent twice."""
    v = rig.venue
    rig.step(t=30.0)
    v.fill("v3", "0.05")
    rig.tick(5).step(t=35.0)
    before = (list(v.sent), list(v.cancelled))
    rig.restart()
    assert rig.ex.inventory(MARKET)["UP"] == {"qty": D("0.05"), "cost": D("0.029")}
    assert sorted(r["venue_order_id"] for r in rig.ex.live_orders().values()) == [
        "v3", "v4"]
    rep = rig.tick(5).step(t=40.0)                 # same book: nothing to do
    assert rep["placed"] == [] and rep["cancelled"] == [] and rep["fills"] == []
    assert (list(v.sent), list(v.cancelled)) == before
    strangers_untouched(rig)
