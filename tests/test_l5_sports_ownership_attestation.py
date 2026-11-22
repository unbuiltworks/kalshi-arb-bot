"""Owner 2026-11-22: "The BTC service must not adopt or settle positions
belonging to the sports controller. Every fill and position needs durable
ownership attribution, with ambiguous positions handled conservatively
rather than silently assigned ... never simply subtract $10.95 from a
safety ledger to make the halt go away."

THE DAY. The sports controller traded four markets on the shared account.
This ledger adopted one net snapshot of each at $1 a contract and booked:

    aec-nba-lac-dal-2026-11-21        DOWN 5   0.00   (cash: +0.20)
    aec-cfb-utsa-uab-2026-11-21       DOWN 5  -5.00   (cash: +0.15)
    atc-sea-bol-udi-2026-11-22-bol    UP 4    -4.00   (cash: +0.28)
    atc-sea-sas-gen-2026-11-22-gen    DOWN 4  -4.00   (cash: -2.68)

-13.00 on a day the account's cash moved -2.05. The exact form already
restates the last one (the venue's fills ARE what this ledger held). The
other three were paired at the venue while this ledger kept a one-sided
snapshot, and refuse `VENUE_QTY_NE_LEDGER_QTY` for good.

The fix is evidence, not arithmetic: the sports controller publishes its
own filled orders by venue order id, and a market is restated only when the
venue's fills and that ledger agree order by order -- from a day the owner
names, never by being deployed.
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

NBA = "aec-nba-lac-dal-2026-11-21"
CFB = "aec-cfb-utsa-uab-2026-11-21"
BOL = "atc-sea-bol-udi-2026-11-22-bol"
GEN = "atc-sea-sas-gen-2026-11-22-gen"
URL = "http://sports.invalid/l5/sports_fills.json"
DAY = "2026-11-22"

#: (market, the controller's own side word, qty, own-outcome price). The
#: Serie A rows are the eight fills of the 14:05-14:50Z campaign. The two
#: overnight markets are stated at their logged totals (5 a side, 4.80 and
#: 4.85); the split between the two sides is illustrative.
FILLS = [
    (BOL, "NO", "2", "0.47"), (BOL, "YES", "2", "0.49"),
    (BOL, "YES", "2", "0.45"), (BOL, "YES", "2", "0.43"),
    (BOL, "NO", "2", "0.51"), (BOL, "NO", "2", "0.51"),
    (GEN, "NO", "2", "0.66"), (GEN, "NO", "2", "0.68"),
    (NBA, "YES", "5", "0.47"), (NBA, "NO", "5", "0.49"),
    (CFB, "YES", "5", "0.48"), (CFB, "NO", "5", "0.49"),
]
CANON = {"YES": "UP", "NO": "DOWN"}


def venue_fills(rows=FILLS, fee="0", only=None):
    return [fill(m, CANON[side], q, px, "T%02d" % i, "V%02d" % i,
                 t="2026-11-22T14:%02d:00Z" % (10 + i), fee=fee)
            for i, (m, side, q, px) in enumerate(rows)
            if only is None or m in only]


def sports_doc(rows=FILLS, strategy="SPORTS", readable=True):
    """The document `pmus_sports_two_sided.fills_doc` publishes."""
    orders = [{"venue_order_id": "V%02d" % i, "market": m, "side": side,
               "price": px, "limit_price": px, "filled_qty": q,
               "filled_cost": str(D(q) * D(px)), "cost_from": "LIMIT_PRICE",
               "state": "CLOSED"}
              for i, (m, side, q, px) in enumerate(rows)]
    return {"readable": readable, "strategy": strategy, "lane":
            "pmus-sports-two-sided", "as_of_utc": "2026-11-22T16:30:00+00:00",
            "orders": orders}


@pytest.fixture
def store(tmp_path):
    """The four markets as this ledger actually held and booked them."""
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    for k in ("realized_unattributed", "realized_attributed",
              "realized_today"):
        s.state[k] = "0"
    retire(s, NBA, dn="5", yes="0", t="2026-11-22T03:28:40+00:00")
    retire(s, CFB, dn="5", yes="1", t="2026-11-22T04:31:08+00:00")
    retire(s, BOL, up="4", yes="0", t="2026-11-22T15:54:57+00:00")
    retire(s, GEN, dn="4", yes="1", t="2026-11-22T15:56:02+00:00")
    assert s.realized_today() == D("-13")
    return s


@pytest.fixture
def sports(monkeypatch):
    box = {"doc": sports_doc(), "calls": 0, "raise": None}

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
    monkeypatch.setenv(VCA.SIBLING_ENV, VCA.SIBLING_TOKEN)
    monkeypatch.delenv("L5_SPORTS_FILLS_URL", raising=False)
    monkeypatch.delenv("L5_SPORTS_ATTEST_FLOOR_UTC_DAY", raising=False)
    return box


@pytest.fixture
def named(sports, monkeypatch):
    """The owner has configured the feed and named today as the floor."""
    monkeypatch.setenv("L5_SPORTS_FILLS_URL", URL)
    monkeypatch.setenv("L5_SPORTS_ATTEST_FLOOR_UTC_DAY", DAY)
    return sports


def book(store, m):
    return store.state[VCA.STATE_KEY]["markets"][m]


def pnl(store, m):
    return D(store.state["retired_markets"][m]["settlement_pnl"])


# ------------------------------------------- deploying it changes nothing

def test_deployed_with_nothing_set_it_is_exactly_today(store, sports):
    """What production does now: the one-sided market is restated by the
    exact form, the three paired ones keep their bound, and the sports
    ledger is never read."""
    run_until_idle(store, Feed([venue_fills()]))
    assert sports["calls"] == 0
    assert pnl(store, GEN) == D("-2.68")
    assert (pnl(store, NBA), pnl(store, CFB), pnl(store, BOL)) == (
        D("0"), D("-5"), D("-4"))
    assert store.realized_today() == D("-11.68")
    for m in (NBA, CFB, BOL):
        assert book(store, m)["status"] == "UPPER_BOUND_KEPT"
        assert book(store, m)["last_reason"] == "VENUE_QTY_NE_LEDGER_QTY"
        assert store.state["retired_markets"][m]["origin"] == "UNKNOWN"


@pytest.mark.parametrize("url,floor", [
    (URL, None), (None, DAY), (URL, ""), (URL, "today"), (URL, "2026-11-2"),
    (URL, "2026-11-23"), (URL, "2027-01-01"), ("  ", DAY)])
def test_off_without_both_the_feed_and_a_floor_day_already_reached(
        store, sports, monkeypatch, url, floor):
    if url is not None:
        monkeypatch.setenv("L5_SPORTS_FILLS_URL", url)
    if floor is not None:
        monkeypatch.setenv("L5_SPORTS_ATTEST_FLOOR_UTC_DAY", floor)
    run_until_idle(store, Feed([venue_fills()]))
    assert sports["calls"] == 0
    assert store.realized_today() == D("-11.68")
    assert pnl(store, BOL) == D("-4")


def test_the_switch_itself_must_be_on(store, named, monkeypatch):
    monkeypatch.setenv(VCA.SIBLING_ENV, "YES")
    run_until_idle(store, Feed([venue_fills()]))
    assert named["calls"] == 0 and store.realized_today() == D("-11.68")


# ------------------------------------------------------------- the day

def test_the_day_is_restated_to_what_the_cash_did(store, named):
    run_until_idle(store, Feed([venue_fills()]))
    assert pnl(store, NBA) == D("0.20")
    assert pnl(store, CFB) == D("0.15")
    assert pnl(store, BOL) == D("0.28")
    assert pnl(store, GEN) == D("-2.68")
    assert store.realized_today() == D("-2.05")
    for m in (NBA, CFB, BOL):
        row = store.state["retired_markets"][m]
        assert row["venue_qty_form"] == store.SIBLING_LEDGER_FORM
        assert row["origin"] == "SPORTS"
        assert row["origin_evidence"] == \
            LX.ORIGIN.EVIDENCE_SIBLING_LEDGER
        #: the row as it was booked is kept beside the restated one
        assert row["cost_superseded"]["origin"] == "UNKNOWN"
        assert book(store, m)["status"] == "CORRECTED"
    #: Bologna: the venue's own six fills a side, not this ledger's UP 4
    row = store.state["retired_markets"][BOL]
    assert (D(row["venue_up_qty"]), D(row["venue_dn_qty"])) == (6, 6)
    assert (D(row["up_cost"]), D(row["dn_cost"])) == (D("2.74"), D("2.98"))
    assert D(row["cost_superseded"]["settlement_pnl"]) == D("-4")
    recs = {r["market"]: r for r in store.state["cost_basis_corrections"]}
    assert set(recs) == {NBA, CFB, BOL, GEN}
    assert recs[BOL]["sibling_ledger"]["strategy"] == "SPORTS"
    assert recs[BOL]["sibling_ledger"]["orders_matched"] == 6
    assert D(recs[BOL]["delta"]) == D("4.28")
    #: the one-sided market needed no second ledger and is not relabelled
    assert recs[GEN]["sibling_ledger"] is None
    assert store.state["retired_markets"][GEN]["origin"] == "UNKNOWN"


def test_the_result_stays_in_the_account_wide_totals(store, named):
    """A label moves a result between columns of the report and nowhere
    else: the account-wide figure the loss limits read still contains every
    sports result, at what it really was."""
    before = D(str(store.state["realized_attributed"]))
    run_until_idle(store, Feed([venue_fills()]))
    after = D(str(store.state["realized_attributed"]))
    assert after - before == D("-2.05") - D("-13")
    rep = store.realized_by_origin()
    by = {k: D(v["settled_usd"]) for k, v in rep["by_origin"].items()}
    assert by["SPORTS"] == D("0.63") and by["UNKNOWN"] == D("-2.68")
    assert sum(by.values()) == D(rep["sum_of_rows_usd"]) == D("-2.05")
    assert rep["by_origin"]["SPORTS"]["markets"] == 3


def test_it_is_applied_once(store, named):
    run_until_idle(store, Feed([venue_fills()]))
    calls = named["calls"]
    run_until_idle(store, Feed([venue_fills()]))
    assert named["calls"] == calls
    assert store.realized_today() == D("-2.05")
    assert len(store.state["cost_basis_corrections"]) == 4


def test_rows_kept_at_the_bound_earlier_are_reopened_by_the_owners_floor(
        store, sports, monkeypatch):
    """Production today: three markets already exhausted (or about to be).
    Naming the floor day is what reopens them -- three more reads each,
    counted durably -- and nothing before that day is touched."""
    run_until_idle(store, Feed([venue_fills()]))
    assert store.realized_today() == D("-11.68")
    monkeypatch.setenv("L5_SPORTS_FILLS_URL", URL)
    monkeypatch.setenv("L5_SPORTS_ATTEST_FLOOR_UTC_DAY", "2026-11-23")
    run_until_idle(store, Feed([venue_fills()]))
    assert store.realized_today() == D("-11.68") and sports["calls"] == 0
    monkeypatch.setenv("L5_SPORTS_ATTEST_FLOOR_UTC_DAY", DAY)
    run_until_idle(store, Feed([venue_fills()]))
    assert store.realized_today() == D("-2.05")


def test_a_market_settled_before_the_floor_day_keeps_its_bound(
        tmp_path, named):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    for k in ("realized_unattributed", "realized_attributed",
              "realized_today"):
        s.state[k] = "0"
    retire(s, BOL, up="4", yes="0", t="2026-11-21T23:59:59+00:00")
    run_until_idle(s, Feed([venue_fills()]))
    assert named["calls"] == 0
    assert pnl(s, BOL) == D("-4")
    assert book(s, BOL)["status"] == "UPPER_BOUND_KEPT"


# ------------------------------------- ambiguous is kept at the bound

def kept(store, m=BOL):
    assert pnl(store, m) == D("-4")
    assert store.state["retired_markets"][m]["origin"] == "UNKNOWN"
    rec = book(store, m)
    assert rec["status"] == "UPPER_BOUND_KEPT"
    assert rec["sibling_attempts"] == VCA.MAX_SIBLING_ATTEMPTS
    return rec["last_reason"]


def test_a_fill_the_sports_ledger_does_not_have(store, named):
    """Somebody else traded this market too -- the owner by hand, say. The
    venue shows a fill the sports ledger cannot account for, so nothing in
    the market is assigned to anyone."""
    extra = fill(BOL, "UP", "3", "0.40", "T-HAND", "V-HAND",
                 t="2026-11-22T14:30:00Z")
    run_until_idle(store, Feed([venue_fills() + [extra]]))
    assert "ORDER_SETS_DIFFER:venue_only=1,sibling_only=0" in kept(store)
    #: the other markets are judged on their own evidence
    assert pnl(store, NBA) == D("0.20")


def test_a_fill_the_venue_does_not_have(store, named):
    named["doc"]["orders"].append({
        "venue_order_id": "V-GHOST", "market": BOL, "side": "YES",
        "price": "0.40", "filled_qty": "2", "filled_cost": "0.80"})
    run_until_idle(store, Feed([venue_fills()]))
    assert "ORDER_SETS_DIFFER:venue_only=0,sibling_only=1" in kept(store)


@pytest.mark.parametrize("field,value,why", [
    ("filled_qty", "1", "ORDER_QTY_DIFFERS"),
    ("filled_cost", "0.95", "ORDER_COST_DIFFERS"),
    ("side", "NO", "ORDER_SIDE_DIFFERS"),
    ("side", "MAYBE", "SIBLING_SIDE_UNREADABLE"),
    ("filled_cost", None, "SIBLING_ORDER_UNREADABLE"),
])
def test_one_order_disagrees(store, named, field, value, why):
    o = next(o for o in named["doc"]["orders"]
             if o["market"] == BOL and o["side"] == "YES")
    o[field] = value
    run_until_idle(store, Feed([venue_fills()]))
    assert why in kept(store)


def test_a_fee_was_charged(store, named):
    rows = venue_fills(fee="0.01", only={BOL})
    run_until_idle(store, Feed([rows]))
    assert "FEE_NOT_ZERO" in kept(store)


def test_the_feed_says_it_is_another_strategys(store, named):
    named["doc"]["strategy"] = "BTC5M"
    run_until_idle(store, Feed([venue_fills()]))
    assert "SIBLING_FEED_IS_ANOTHER_STRATEGYS" in kept(store)


def test_the_feed_is_not_readable(store, named):
    named["doc"] = {"readable": False, "strategy": "SPORTS", "orders": []}
    run_until_idle(store, Feed([venue_fills()]))
    assert "SIBLING_FEED_NOT_READABLE" in kept(store)


def test_the_feed_is_unreachable(store, named):
    named["raise"] = OSError("down")
    run_until_idle(store, Feed([venue_fills()]))
    assert "SIBLING_FEED_UNREACHABLE" in kept(store)
    assert named["calls"] == VCA.MAX_SIBLING_ATTEMPTS * 3   # three markets


def test_the_btc5m_feed_cannot_vouch_for_a_sports_market(
        store, named, monkeypatch):
    """Each controller's ledger is read from its own address only."""
    monkeypatch.setenv("L5_BTC5M_FILLS_URL", "http://lane.invalid/btc5m_fills")
    monkeypatch.delenv("L5_SPORTS_FILLS_URL")
    run_until_idle(store, Feed([venue_fills()]))
    assert named["calls"] == 0 and pnl(store, BOL) == D("-4")


# --------------------------- nothing else about a sports market changes

def test_a_sports_market_is_still_in_no_strategys_series(tmp_path):
    assert LX.ORIGIN.strategy_of_market(BOL) is None
    assert LX.ORIGIN.ledger_owner_of_market(BOL) == "SPORTS"
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.adopt_venue_position(BOL, {"%s|UP" % BOL: "4"}, why="test")
    #: adopted exactly as before: at the bound, cost unknown
    assert D(s.state["positions"]["%s|UP" % BOL]) == 4
    assert "%s|UP" % BOL in s.state["leg_cost_basis_unknown"]


# ------------------------------------------- before settlement, one-sided

def fills_rows(market):
    return [o for o in sports_doc()["orders"] if o["market"] == market]


def venue_orders(market):
    out = {}
    for o in fills_rows(market):
        own = D(o["price"])
        wire = own if o["side"] == "YES" else D(1) - own
        out[o["venue_order_id"]] = {"marketSlug": market,
                                    "cumQuantity": o["filled_qty"],
                                    "avgPx": {"value": str(wire)}}
    return out


def test_a_one_sided_sports_position_is_restated_before_it_settles(tmp_path):
    """Genoa: this ledger held exactly what the venue's two orders filled.
    With the sports ledger's rows confirmed by OUR reads of those orders,
    the cost is known before settlement and no false loss is ever booked."""
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    for k in ("realized_unattributed", "realized_attributed",
              "realized_today"):
        s.state[k] = "0"
    s.adopt_venue_position(GEN, {"%s|DOWN" % GEN: "4"}, why="test")
    got = s.attest_other_strategy_cost(
        GEN, fills_rows(GEN), venue_orders(GEN), source="TEST")
    assert got["attested"] is True and got["origin"] == "SPORTS"
    assert D(s.state["leg_cost"]["%s|DOWN" % GEN]) == D("2.68")
    out = s.retire_settled_market(GEN, yes_value=D(1), resolution={
        "source": "VENUE_SETTLEMENT_PX", "market": GEN, "yes_value": "1"})
    assert out["retired"] is True
    assert s.realized_today() == D("-2.68")
    row = s.state["retired_markets"][GEN]
    assert row["origin"] == "SPORTS"
    assert row["origin_cost_basis"] == LX.ORIGIN.BASIS_AUTHENTICATED
    assert row["pnl_provenance"] != "UNKNOWN_COST_UPPER_BOUND"


def test_a_stale_snapshot_is_not_restated_before_settlement(tmp_path):
    """Bologna: this ledger held UP 4 while the venue had filled six a
    side. The fills do not account for the position, so it stays at the
    bound here and is restated only after settlement, by the form above."""
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.adopt_venue_position(BOL, {"%s|UP" % BOL: "4"}, why="test")
    got = s.attest_other_strategy_cost(
        BOL, fills_rows(BOL), venue_orders(BOL), source="TEST")
    assert got["attested"] is False
    assert got["reason"] == "FILLS_DO_NOT_ACCOUNT_FOR_THE_POSITION"
    assert "%s|UP" % BOL in s.state["leg_cost_basis_unknown"]


def test_the_settlement_sweep_reads_the_sports_feed_only_in_scope(
        tmp_path, monkeypatch):
    import json
    import urllib.request as UR
    from src import l5_pmus_live_runner as R
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.adopt_venue_position(GEN, {"%s|DOWN" % GEN: "4"}, why="test")
    emitted, asked = [], []
    monkeypatch.setattr(R, "emit", lambda k, d: emitted.append((k, d)))

    class Shell:
        store = s

    class Resp:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    box = {"doc": sports_doc()}

    def urlopen(url, timeout=0):
        asked.append(url)
        return Resp(json.dumps(box["doc"]).encode())
    monkeypatch.setattr(UR, "urlopen", urlopen)
    rows = venue_orders(GEN)

    class Venue:
        def order(self, vid):
            return rows[vid]
    go = lambda: R.Runner.attest_other_strategy_cost(Shell(), GEN, Venue())
    for k in ("L5_SPORTS_FILLS_URL", "L5_SPORTS_ATTEST_FLOOR_UTC_DAY"):
        monkeypatch.delenv(k, raising=False)
    #: the 5-minute lane's feed is never asked about a sports market
    monkeypatch.setenv(R.BTC5M_FILLS_URL_ENV, "http://btc5m/btc5m_fills")
    assert go() is None
    monkeypatch.setenv("L5_SPORTS_FILLS_URL", URL)
    assert go() is None                       # no floor day named
    monkeypatch.setenv("L5_SPORTS_ATTEST_FLOOR_UTC_DAY", "2999-01-01")
    assert go() is None                       # floor not reached
    assert asked == []
    monkeypatch.setenv("L5_SPORTS_ATTEST_FLOOR_UTC_DAY", "2026-11-22")
    box["doc"] = sports_doc(strategy="BTC5M")
    got = go()
    assert got["attested"] is False and asked == [URL]
    assert "%s|DOWN" % GEN in s.state["leg_cost_basis_unknown"]
    box["doc"] = sports_doc()
    got = go()
    assert got["attested"] is True and got["origin"] == "SPORTS"
    assert got["source"] == \
        "SPORTS_LEDGER_FILLS_CONFIRMED_BY_VENUE_ORDER_READS"
    assert emitted[-1][0] == "L5_OTHER_STRATEGY_COST"
