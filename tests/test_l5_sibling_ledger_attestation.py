"""Another strategy's two-sided market, restated only when the venue's
fills and that strategy's own order ledger agree order by order.

THE CASE: cpc-btc-updown-5m-2026-11-21-0620z. Seventeen maker fills, UP
0.49 for $0.2948 and DOWN 0.61 for $0.2957, DOWN won: +$0.0195, which is
what the account's cash moved. This ledger adopted DOWN 0.10 at the $1
bound and retired the market at $0.00.
"""
from __future__ import annotations

import copy
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_venue_cost_attestation as VCA

from tests.test_l5_venue_cost_attestation import (
    Feed, _no_retry_spacing, fill, retire, run_until_idle,  # noqa: F401
)

M = "cpc-btc-updown-5m-2026-11-21-0620z"
OTHER = "aec-nba-det-sa-2026-11-21"
URL = "http://lane.invalid/btc5m_fills"

#: the seventeen fills of the window, as the lane logged them
WINDOW = [
    ("DOWN", "0.08", "0.40"), ("UP", "0.07", "0.66"), ("DOWN", "0.07", "0.33"),
    ("DOWN", "0.06", "0.20"), ("UP", "0.06", "0.79"), ("UP", "0.06", "0.81"),
    ("UP", "0.06", "0.79"), ("DOWN", "0.06", "0.20"), ("UP", "0.06", "0.82"),
    ("DOWN", "0.08", "0.39"), ("UP", "0.08", "0.60"), ("DOWN", "0.08", "0.61"),
    ("DOWN", "0.06", "0.72"), ("DOWN", "0.07", "0.67"), ("UP", "0.05", "0.10"),
    ("DOWN", "0.05", "0.93"), ("UP", "0.05", "0.06"),
]


def venue_fills(rows=WINDOW, market=M, fee="0"):
    return [fill(market, side, q, px, "T%02d" % i, "V%02d" % i,
                 t="2026-11-21T06:2%d:00Z" % (i % 4), fee=fee)
            for i, (side, q, px) in enumerate(rows)]


def lane_doc(rows=WINDOW, market=M, strategy="BTC5M", readable=True):
    orders = [{"client_order_id": "b5m-%02d" % i, "venue_order_id": "V%02d" % i,
               "market": market, "side": side, "price": px,
               "filled_qty": q, "filled_cost": str(D(q) * D(px)),
               "state": "FILLED"}
              for i, (side, q, px) in enumerate(rows)]
    #: orders that never filled are in the feed too
    orders.append({"client_order_id": "b5m-zz", "venue_order_id": "V99",
                   "market": market, "side": "UP", "price": "0.58",
                   "filled_qty": "0", "filled_cost": "0",
                   "state": "CANCELLED"})
    orders.append({"client_order_id": "b5m-old", "venue_order_id": "VOLD",
                   "market": "cpc-btc-updown-5m-2026-11-20-0100z",
                   "side": "UP", "price": "0.5", "filled_qty": "1",
                   "filled_cost": "0.5", "state": "FILLED"})
    return {"readable": readable, "strategy": strategy,
            "as_of_utc": "2026-11-21T06:30:00+00:00", "orders": orders}


@pytest.fixture
def store(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.state["realized_unattributed"] = "0"
    s.state["realized_attributed"] = "0"
    s.state["realized_today"] = "0"
    retire(s, M, dn="0.10", yes="0", t="2026-11-21T06:26:56+00:00")
    assert s.realized_today() == D("0")
    return s


@pytest.fixture
def lane(monkeypatch):
    box = {"doc": lane_doc(), "calls": 0, "raise": None}

    def fake(market, *, fetch=None):
        def f(url):
            box["calls"] += 1
            assert url == URL
            if box["raise"] is not None:
                raise box["raise"]
            return copy.deepcopy(box["doc"])
        return REAL(market, fetch=f)
    REAL = VCA.sibling_evidence
    monkeypatch.setattr(VCA, "sibling_evidence", fake)
    monkeypatch.setenv("L5_BTC5M_FILLS_URL", URL)
    return box


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(VCA.SIBLING_ENV, VCA.SIBLING_TOKEN)


def book(store, m=M):
    return store.state[VCA.STATE_KEY]["markets"][m]


# ------------------------------------------------------------- off

@pytest.mark.parametrize("raw", ["", "1", "YES", "ON",
                                 "venue_fills_and_sibling_ledger"])
def test_off_unless_the_exact_token(store, lane, monkeypatch, raw):
    monkeypatch.setenv(VCA.SIBLING_ENV, raw)
    run_until_idle(store, Feed([venue_fills()]))
    assert lane["calls"] == 0
    assert book(store)["status"] == "UPPER_BOUND_KEPT"
    assert book(store)["last_reason"] == "VENUE_QTY_NE_LEDGER_QTY"
    assert store.realized_today() == D("0")
    assert not store.state.get("cost_basis_corrections")


# ------------------------------------------------------------- the case

def test_the_window_is_restated_to_what_the_cash_did(store, lane, on):
    _att, results = run_until_idle(store, Feed([venue_fills()]))
    done = [r for o in results for r in (o.get("evaluated") or [])
            if r.get("attested")]
    assert len(done) == 1 and done[0]["form"] == store.SIBLING_LEDGER_FORM
    assert D(done[0]["delta"]) == D("0.0195")
    assert store.realized_today() == D("0.0195")
    row = store.state["retired_markets"][M]
    assert D(row["settlement_pnl"]) == D("0.0195")
    assert D(row["up_cost"]) == D("0.2948")
    assert D(row["dn_cost"]) == D("0.2957")
    assert D(row["venue_up_qty"]) == D("0.49")
    assert D(row["venue_dn_qty"]) == D("0.61")
    assert row["venue_qty_form"] == store.SIBLING_LEDGER_FORM
    #: the original retirement is kept beside the restated one
    assert D(row["cost_superseded"]["settlement_pnl"]) == D("0")
    rec, = store.state["cost_basis_corrections"]
    assert rec["qty_form"] == store.SIBLING_LEDGER_FORM
    assert rec["sibling_ledger"]["strategy"] == "BTC5M"
    assert rec["sibling_ledger"]["orders_matched"] == 17
    assert len(rec["trade_ids"]) == 17
    assert book(store)["status"] == "CORRECTED"
    assert "%s|DOWN" % M not in store.state.get("leg_cost_basis_unknown", [])


def test_it_is_applied_once(store, lane, on):
    run_until_idle(store, Feed([venue_fills()]))
    calls = lane["calls"]
    run_until_idle(store, Feed([venue_fills()]))
    assert lane["calls"] == calls
    assert store.realized_today() == D("0.0195")
    assert len(store.state["cost_basis_corrections"]) == 1


def test_a_losing_window_is_restated_downward_too(tmp_path, lane, on):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    for k in ("realized_unattributed", "realized_attributed",
              "realized_today"):
        s.state[k] = "0"
    retire(s, M, dn="0.10", yes="1", t="2026-11-21T06:26:56+00:00")
    assert s.realized_today() == D("-0.10")
    run_until_idle(s, Feed([venue_fills()]))
    #: UP won: 0.49 redeemed against 0.5905 spent
    assert s.realized_today() == D("0.49") - D("0.5905")


def test_an_already_exhausted_market_is_reopened_when_switched_on(
        store, lane, monkeypatch):
    run_until_idle(store, Feed([venue_fills()]))           # switch off
    assert book(store)["status"] == "UPPER_BOUND_KEPT"
    assert book(store)["attempts"] == VCA.MAX_ATTEMPTS
    monkeypatch.setenv(VCA.SIBLING_ENV, VCA.SIBLING_TOKEN)
    run_until_idle(store, Feed([venue_fills()]))
    assert book(store)["status"] == "CORRECTED"
    assert store.realized_today() == D("0.0195")


# ------------------------------------------------------------- refusals

def refused(store, lane, feed_rows=None, n=VCA.MAX_SIBLING_ATTEMPTS):
    before = copy.deepcopy(store.state["retired_markets"][M])
    run_until_idle(store, Feed([feed_rows if feed_rows is not None
                                else venue_fills()]))
    assert store.state["retired_markets"][M] == before
    assert store.realized_today() == D("0")
    assert not store.state.get("cost_basis_corrections")
    assert book(store)["status"] == "UPPER_BOUND_KEPT"
    assert book(store)["sibling_attempts"] == n
    return book(store)["last_reason"]


def test_lane_unreachable_keeps_the_bound_and_is_bounded(store, lane, on):
    lane["raise"] = OSError("down")
    why = refused(store, lane)
    assert why.endswith("SIBLING:SIBLING_FEED_UNREACHABLE:OSError")
    assert lane["calls"] == VCA.MAX_SIBLING_ATTEMPTS


def test_lane_feed_not_readable(store, lane, on):
    lane["doc"] = lane_doc(readable=False)
    assert refused(store, lane).endswith("SIBLING_FEED_NOT_READABLE")


def test_feed_of_another_strategy(store, lane, on):
    lane["doc"] = lane_doc(strategy="BTC15M")
    assert refused(store, lane).endswith("SIBLING_FEED_IS_ANOTHER_STRATEGYS")


def test_feed_url_not_configured(store, lane, on, monkeypatch):
    monkeypatch.delenv("L5_BTC5M_FILLS_URL")
    assert refused(store, lane).endswith("SIBLING_FEED_NOT_CONFIGURED")


def test_the_lane_is_missing_a_fill_the_venue_has(store, lane, on):
    lane["doc"] = lane_doc(WINDOW[:-1])
    assert "ORDER_SETS_DIFFER:venue_only=1,sibling_only=0" in refused(
        store, lane)


def test_the_venue_is_missing_a_fill_the_lane_has(store, lane, on):
    assert "ORDER_SETS_DIFFER:venue_only=0,sibling_only=1" in refused(
        store, lane, venue_fills(WINDOW[:-1]))


def test_a_quantity_differs(store, lane, on):
    rows = list(WINDOW)
    rows[3] = ("DOWN", "0.05", "0.20")
    lane["doc"] = lane_doc(rows)
    assert "ORDER_QTY_DIFFERS:V03" in refused(store, lane)


def test_a_price_differs(store, lane, on):
    rows = list(WINDOW)
    rows[3] = ("DOWN", "0.06", "0.21")
    lane["doc"] = lane_doc(rows)
    assert "ORDER_COST_DIFFERS:V03" in refused(store, lane)


def test_a_side_differs(store, lane, on):
    rows = list(WINDOW)
    rows[3] = ("UP", "0.06", "0.20")
    lane["doc"] = lane_doc(rows)
    assert "ORDER_SIDE_DIFFERS:V03" in refused(store, lane)


def test_a_fee_was_charged(store, lane, on):
    assert "FEE_NOT_ZERO" in refused(store, lane, venue_fills(fee="0.01"))


def test_a_repeated_order_id_in_the_lane_feed(store, lane, on):
    d = lane_doc()
    d["orders"][1]["venue_order_id"] = d["orders"][0]["venue_order_id"]
    lane["doc"] = d
    assert refused(store, lane).endswith(
        "SIBLING_ORDER_ID_MISSING_OR_REPEATED")


def test_settled_before_the_last_rebase(store, lane, on):
    store.state["accounting_rebase_log"] = [
        {"t": "2026-11-21T07:00:00+00:00", "id": "r"}]
    assert "SETTLED_BEFORE_THE_LAST_REBASE" in refused(store, lane)


# ------------------------------------------- scope: nothing wider

def test_this_services_own_markets_never_use_the_form(tmp_path, lane, on):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    for k in ("realized_unattributed", "realized_attributed",
              "realized_today"):
        s.state[k] = "0"
    retire(s, OTHER, up="5", yes="0", t="2026-11-21T06:26:56+00:00")
    run_until_idle(s, Feed([[fill(OTHER, "UP", "10", "0.46", "TA", "VA"),
                             fill(OTHER, "DOWN", "10", "0.48", "TB", "VB")]]))
    assert lane["calls"] == 0
    b = s.state[VCA.STATE_KEY]["markets"][OTHER]
    assert b["status"] == "UPPER_BOUND_KEPT"
    assert b["last_reason"] == "VENUE_QTY_NE_LEDGER_QTY"
    assert "sibling_attempts" not in b
    assert s.realized_today() == D("-5")


def test_a_side_the_ledger_already_knew_is_never_repriced(tmp_path, lane, on):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    for k in ("realized_unattributed", "realized_attributed",
              "realized_today"):
        s.state[k] = "0"
    retire(s, M, up="0.05", dn="0.10", yes="0",
           unknown=["%s|DOWN" % M], cost={"UP": "0.03", "DOWN": "0.10"},
           t="2026-11-21T06:26:56+00:00")
    run_until_idle(s, Feed([venue_fills()]))
    assert "KNOWN_SIDE:UP" in s.state[VCA.STATE_KEY]["markets"][M][
        "last_reason"]
    assert not s.state.get("cost_basis_corrections")


def test_the_store_verb_alone_needs_the_evidence(store):
    """Called without `sibling`, the store behaves exactly as before."""
    legs = VCA.legs_from_rows(venue_fills(), M)["legs"]
    out = store.attest_retired_market_cost(
        M, legs=legs, walk={"sufficient": True, "by": "END_OF_FEED"},
        correction_id="x")
    assert out["attested"] is False
    assert out["reason"] == "VENUE_QTY_NE_LEDGER_QTY"
    assert out["sibling"] == "NO_SIBLING_EVIDENCE"
