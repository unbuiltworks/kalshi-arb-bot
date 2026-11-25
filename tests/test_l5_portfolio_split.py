"""Bot-only vs total-account drawdown (owner 2026-11-25).

"Manual trading must not be misclassified as bot trading losses, but all
positions and commitments must remain subject to portfolio-level
protection. Do not simply reset the anchor or exclude manual exposure from
all risk controls."
"""
import collections
import inspect
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_live_validation_gates as G
from src import l5_manual_commitment as MC
from src import l5_pmus_live_runner as R
from src import l5_portfolio_drawdown as P

SPORT = "aec-nfl-bal-atl-2026-11-29"
OID = "D1SA9RRSTZ73"
NOW = 1_800_000_000.0
BASE = {P.ANCHOR_ENV: "320.00", P.MAX_DRAWDOWN_ENV: "5"}
ENV = dict(BASE, **{P.TOTAL_MAX_DRAWDOWN_ENV: "40"})
ALL_ENVS = (P.ANCHOR_ENV, P.MAX_DRAWDOWN_ENV, P.TOTAL_MAX_DRAWDOWN_ENV,
            P.MANUAL_ORDERS_ENV)


def reader_capacity(block):
    """What an unchanged reader in another service computes."""
    return D(block["max_drawdown_usd"]) - D(block["drawdown_usd"])


def cfgs(env=None):
    e = ENV if env is None else env
    c = P.config(e)
    return c, P.split_config(c, e)


def row(**over):
    """A filled manual BUY YES: 66 x 0.29 + 0.66 commission = 19.80."""
    r = {"id": OID, "marketSlug": SPORT, "state": "ORDER_STATE_FILLED",
         "quantity": 66, "cumQuantity": 66, "leavesQuantity": 0,
         "avgPx": {"value": "0.2900", "currency": "USD"},
         "commissionNotionalTotalCollected": {"value": "0.6600"},
         "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL",
         "outcomeSide": "OUTCOME_SIDE_YES",
         "intent": "ORDER_INTENT_BUY_LONG", "action": "ORDER_ACTION_BUY"}
    r.update(over)
    return r


class Venue:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def order(self, oid):
        self.calls.append(oid)
        got = self.rows.get(oid)
        if isinstance(got, BaseException):
            raise got
        return dict(got or {})


def measured(rows=None, ids=(OID,), now=NOW):
    v = Venue({OID: row()} if rows is None else rows)
    return MC.Reader().step(v, now, list(ids))


HELD = {SPORT: {"UP": D("66"), "DOWN": D("0")}}


def commit(m=None, pos=None, now=NOW + 5):
    return P.manual_commitment(measured() if m is None else m,
                               HELD if pos is None else pos, now)


# ------------------------------------------------------------------ off

@pytest.mark.parametrize("value", [None, D("330"), D("298.00"), D("100")])
@pytest.mark.parametrize("env", [{}, BASE, {P.ANCHOR_ENV: "320"},
                                 {P.ANCHOR_ENV: "x", P.MAX_DRAWDOWN_ENV: "5"}])
def test_split_off_report_and_refusal_are_exactly_what_they_were(
        value, env, monkeypatch):
    for k in ALL_ENVS:
        monkeypatch.delenv(k, raising=False)
    c = P.config(env)
    # the block as the module built it before the split existed
    old = {"state": c.get("state"),
           "worst_case_value_usd": None if value is None else str(value)}
    ref = None
    if c.get("state") == "ON":
        old.update(anchor_usd=str(c["anchor_usd"]),
                   max_drawdown_usd=str(c["max_drawdown_usd"]))
        if value is not None:
            dd = c["anchor_usd"] - value
            old.update(drawdown_usd=str(dd),
                       room_usd=str(c["max_drawdown_usd"] - dd))
            if dd >= c["max_drawdown_usd"]:
                ref = "PORTFOLIO_DRAWDOWN_LIMIT:%s>=%s" % (
                    dd, c["max_drawdown_usd"])
        else:
            ref = "PORTFOLIO_VALUE_UNAVAILABLE"
    elif c.get("why"):
        old["why"] = c["why"]
        ref = "PORTFOLIO_DRAWDOWN_CONFIG_" + c["why"]
    old["refusal"] = ref
    old["labels"] = ("value = buying power + min(UP,DOWN) per market; "
                     "unmatched contracts and resting-order holds count 0. "
                     "Anchor moves only when the owner updates "
                     "L5_PORTFOLIO_ANCHOR_USD for a deposit or withdrawal.")
    for got in (P.report(value, c),
                # a measurement handed in while OFF changes nothing either
                P.report(value, c, manual=commit())):
        assert got == old and list(got) == list(old)
    assert P.refusal(value, c, manual=D("19.80")) == ref


def test_attested_orders_alone_do_not_turn_the_split_on():
    e = dict(BASE, **{P.MANUAL_ORDERS_ENV: OID})
    assert P.split_config(P.config(e), e) == {"state": P.OFF}


# ------------------------------------------------------- the worked example

def test_worked_example():
    c, s = cfgs()
    m = commit()
    assert m["manual_commitment_usd"] == D("19.80")
    r = P.report(D("298.00"), c, split=s, manual=m)
    sp = r["split"]
    assert sp["state"] == "ON"
    # the venue states prices to four places, so the strings carry them
    num = {k: D(v) for k, v in sp.items() if k.endswith("_usd")}
    assert num == {"total_drawdown_usd": D("22.00"),
                   "total_max_drawdown_usd": D("40"),
                   "total_room_usd": D("18.00"),
                   "bot_drawdown_usd": D("2.20"),
                   "bot_max_drawdown_usd": D("5"),
                   "bot_room_usd": D("2.80"),
                   "manual_commitment_usd": D("19.80")}
    assert sp["bot_max_drawdown_usd"] == "5"
    assert sp["total_max_drawdown_usd"] == "40"
    assert sp["binding"] == "BOT"
    assert D(r["drawdown_usd"]) == D("2.20") and r["max_drawdown_usd"] == "5"
    assert D(r["room_usd"]) == D("2.80") and r["refusal"] is None
    assert reader_capacity(r) == D("2.80")
    assert r["anchor_usd"] == "320.00"
    assert sp["manual_orders"] == [{
        "order_id_tail": "TZ73", "market": SPORT, "side": "UP",
        "filled": "66", "cost_usd": "19.8000", "counted_usd": "19.8000",
        "state": "ORDER_STATE_FILLED"}]
    assert sp["rejected"] == [] and "settles" in sp["labels"].lower()
    assert OID not in repr(r)                    # the tail only, ever
    # without the split the same account refuses every bot
    assert P.refusal(D("298.00"), P.config(BASE), split={"state": P.OFF}) \
        == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"


@pytest.mark.parametrize("value,manual,binding", [
    ("298.00", "19.80", "BOT"),        # bot room 2.80, total room 18
    ("283.00", "36.50", "TOTAL"),      # bot room 4.50, total room 3
    ("320.00", "0", "BOT"),            # nothing down
    ("330.00", "5", "BOT"),            # above the anchor
])
def test_effective_figures_are_the_tighter_room(value, manual, binding):
    c, s = cfgs()
    r = P.report(D(value), c, split=s, manual=D(manual))
    sp = r["split"]
    tight = min(D(sp["bot_room_usd"]), D(sp["total_room_usd"]))
    assert sp["binding"] == binding
    assert D(r["room_usd"]) == tight
    assert reader_capacity(r) == tight
    assert r["max_drawdown_usd"] == "5"
    assert r["refusal"] is None


def test_total_limit_trips_while_bot_is_fine():
    c, s = cfgs()
    # 41 down in total, 40 of it manual: bot figure 1.00
    r = P.report(D("279.00"), c, split=s, manual=D("40.00"))
    assert r["split"]["bot_drawdown_usd"] == "1.00"
    assert r["split"]["binding"] == "TOTAL"
    assert r["refusal"] == "PORTFOLIO_DRAWDOWN_LIMIT:TOTAL:41.00>=40"
    assert r["refusal"].startswith(P.LIMIT_PREFIX)
    assert reader_capacity(r) <= 0
    assert P.refusal(D("279.00"), c, split=s, manual=D("40.00")) \
        == r["refusal"]


def test_bot_limit_trips_while_total_is_fine():
    c, s = cfgs()
    r = P.report(D("295.00"), c, split=s, manual=D("19.80"))   # bot 5.20
    assert r["split"]["bot_drawdown_usd"] == "5.20"
    assert D(r["split"]["total_room_usd"]) == D("15")
    assert r["refusal"] == "PORTFOLIO_DRAWDOWN_LIMIT:5.20>=5"
    assert reader_capacity(r) == D("-0.20")
    # exactly at the limit refuses, a cent inside does not
    assert P.refusal(D("295.20"), c, split=s, manual=D("19.80")) \
        == "PORTFOLIO_DRAWDOWN_LIMIT:5.00>=5"
    assert P.refusal(D("295.21"), c, split=s, manual=D("19.80")) is None


def test_unknown_value_still_refuses_with_the_split_on():
    c, s = cfgs()
    r = P.report(None, c, split=s, manual=commit())
    assert r["refusal"] == "PORTFOLIO_VALUE_UNAVAILABLE"
    assert "drawdown_usd" not in r and r["split"]["state"] == "ON"


# ---------------------------------------------- the cap, and never negative

@pytest.mark.parametrize("dd,manual,bot", [
    ("22", "30", "0"),         # more manual than drawdown: capped
    ("22", "-10", "22"),       # negative is nothing
    ("-3", "19.80", "-3"),     # above the anchor: nothing to take off
    ("0", "19.80", "0"),
    ("22", "junk", "22"),
])
def test_manual_is_capped_at_the_drawdown_and_never_negative(dd, manual, bot):
    f = P.split_figures(D(dd), manual, D("5"), D("40"))
    assert f["bot_drawdown_usd"] == D(bot)
    assert D(0) <= f["manual_commitment_usd"] <= max(D(0), D(dd))
    assert f["bot_drawdown_usd"] <= f["total_drawdown_usd"] or D(dd) < 0
    assert f["effective_drawdown_usd"] == D("5") - min(
        f["bot_room_usd"], f["total_room_usd"])


# ------------------------------------------------------ the total limit env

@pytest.mark.parametrize("raw", ["x", "nan", "0", "-5", "4.99"])
def test_invalid_total_limit_is_a_refusal_never_off(raw):
    e = dict(BASE, **{P.TOTAL_MAX_DRAWDOWN_ENV: raw})
    c, s = cfgs(e)
    assert s == {"state": "INVALID", "why": "TOTAL_LIMIT_INVALID"}
    want = "PORTFOLIO_DRAWDOWN_CONFIG_TOTAL_LIMIT_INVALID"
    assert P.refusal(D("320"), c, split=s) == want
    r = P.report(D("320"), c, split=s)
    assert r["refusal"] == want and r["state"] == "INVALID"
    assert "split" not in r


def test_total_limit_without_the_base_limit_refuses():
    e = {P.TOTAL_MAX_DRAWDOWN_ENV: "40"}
    c, s = cfgs(e)
    assert P.refusal(D("320"), c, split=s) \
        == "PORTFOLIO_DRAWDOWN_CONFIG_TOTAL_LIMIT_INVALID"


def test_total_equal_to_bot_limit_is_valid_and_changes_nothing_material():
    e = dict(BASE, **{P.TOTAL_MAX_DRAWDOWN_ENV: "5"})
    c, s = cfgs(e)
    assert s["state"] == "ON"
    r = P.report(D("298"), c, split=s, manual=D("19.80"))
    assert r["refusal"] == "PORTFOLIO_DRAWDOWN_LIMIT:TOTAL:22.00>=5"
    assert reader_capacity(r) == D("-17")


# -------------------------------------------------- what the venue row says

def test_manual_buy_is_measured_from_the_venue_row():
    got = MC.parse_row(OID, row())
    assert got["terminal"] is True
    assert got["order"] == {"order_id_tail": "TZ73", "market": SPORT,
                            "side": "UP", "filled": "66",
                            "cost_usd": "19.8000",
                            "state": "ORDER_STATE_FILLED"}


def test_a_buy_no_order_costs_one_minus_the_wire_price():
    # the venue's avgPx is the YES price; 10 NO at wire 0.71 cost 0.29 each
    got = MC.parse_row(OID, row(
        outcomeSide="OUTCOME_SIDE_NO", intent="ORDER_INTENT_BUY_SHORT",
        cumQuantity=10, quantity=10, avgPx={"value": "0.7100"},
        commissionNotionalTotalCollected={"value": "0.1000"}))
    assert got["order"]["side"] == "DOWN"
    assert D(got["order"]["cost_usd"]) == D("3.00")


@pytest.mark.parametrize("over,why", [
    ({"manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"},
     "NOT_MANUAL_PER_VENUE"),
    ({"manualOrderIndicator": None}, "NOT_MANUAL_PER_VENUE"),
    ({"intent": "ORDER_INTENT_SELL_LONG"}, "MANUAL_SELL_NOT_SUPPORTED"),
    ({"action": "ORDER_ACTION_SELL"}, "MANUAL_SELL_NOT_SUPPORTED"),
    ({"intent": None}, "INTENT_UNREADABLE"),
    ({"avgPx": None}, "AVG_PRICE_UNREADABLE"),
    ({"avgPx": {"value": "1.5"}}, "AVG_PRICE_UNREADABLE"),
    ({"id": "SOMEOTHERID1"}, "ORDER_ID_MISMATCH"),
    ({"marketSlug": None}, "ROW_UNREADABLE:UnknownMarket"),
    ({"outcomeSide": "OUTCOME_SIDE_NO"}, "ROW_UNREADABLE:UnknownSide"),
])
def test_anything_the_venue_does_not_confirm_is_rejected_and_counts_zero(
        over, why):
    m = measured({OID: row(**over)})
    assert m["orders"] == []
    assert m["rejected"] == [{"order_id_tail": "TZ73", "why": why}]
    c, s = cfgs()
    got = commit(m)
    assert got["manual_commitment_usd"] == 0
    r = P.report(D("298.00"), c, split=s, manual=got)
    assert r["split"]["bot_drawdown_usd"] == "22.00"      # the whole of it
    assert r["split"]["rejected"] == m["rejected"]
    assert r["refusal"] == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"


@pytest.mark.parametrize("rows,why", [
    ({OID: RuntimeError("GET /v1/order/%s 500" % OID)},
     "ORDER_UNREADABLE:RuntimeError"),
    ({}, "ORDER_NOT_FOUND"),
])
def test_unreadable_or_missing_order_counts_zero(rows, why):
    m = measured(rows)
    assert m["rejected"] == [{"order_id_tail": "TZ73", "why": why}]
    assert OID not in repr(m)
    assert commit(m)["manual_commitment_usd"] == 0


def test_malformed_id_is_never_sent_to_the_venue():
    v = Venue({})
    m = MC.Reader().step(v, NOW, ["../account", "ab"])
    assert v.calls == []
    assert [r["why"] for r in m["rejected"]] == ["ORDER_ID_MALFORMED"] * 2


def test_unfilled_manual_order_commits_nothing():
    m = measured({OID: row(cumQuantity=0, leavesQuantity=66,
                           state="ORDER_STATE_NEW", avgPx=None)})
    assert m["orders"][0]["cost_usd"] == "0"
    assert commit(m)["manual_commitment_usd"] == 0


# ------------------------------------------------- still a commitment, or not

def test_position_smaller_than_filled_counts_pro_rata():
    got = commit(pos={SPORT: {"UP": D("33"), "DOWN": D("0")}})
    assert got["manual_commitment_usd"] == D("9.90")
    assert got["manual_orders"][0]["counted_usd"] == "9.9000"


@pytest.mark.parametrize("pos", [
    {},                                              # settled and gone
    {SPORT: {"UP": D("0"), "DOWN": D("0")}},
    {SPORT: {"UP": D("0"), "DOWN": D("66")}},        # the other side only
    {"aec-other": {"UP": D("66"), "DOWN": D("0")}},
])
def test_position_gone_counts_zero(pos):
    got = commit(pos=pos)
    assert got["manual_commitment_usd"] == 0
    assert D(got["manual_orders"][0]["counted_usd"]) == 0


def test_unreadable_positions_count_zero():
    got = P.manual_commitment(measured(), None, NOW)
    assert got["manual_commitment_usd"] == 0
    assert got["why"] == "POSITIONS_UNREADABLE"


def test_two_orders_share_one_holding():
    o2 = "D1SA9RRSTZ99"
    m = measured({OID: row(), o2: row(id=o2)}, ids=(OID, o2))
    assert len(m["orders"]) == 2
    got = commit(m, pos={SPORT: {"UP": D("99"), "DOWN": D("0")}})
    assert [o["counted_usd"] for o in got["manual_orders"]] \
        == ["19.8000", "9.9000"]
    assert got["manual_commitment_usd"] == D("29.70")


@pytest.mark.parametrize("m,now,why", [
    (None, NOW, "MANUAL_NOT_MEASURED"),
    ("measured", NOW + 181, "MANUAL_MEASUREMENT_STALE"),
    ("measured", NOW - 60, "MANUAL_MEASUREMENT_STALE"),      # clock went back
    ("no_t", NOW, "MANUAL_MEASUREMENT_STALE"),
])
def test_stale_or_absent_measurement_counts_zero_and_says_so(m, now, why):
    if m == "measured":
        m = measured()
    elif m == "no_t":
        m = dict(measured(), t=None)
    got = P.manual_commitment(m, HELD, now)
    assert got["manual_commitment_usd"] == 0 and got["why"] == why
    c, s = cfgs()
    r = P.report(D("298.00"), c, split=s, manual=got)
    assert r["split"]["manual_commitment_usd"] == "0"
    assert r["split"]["manual_measurement"]["counted_zero_because"] == why
    assert r["refusal"] == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"


def test_fresh_at_180s():
    assert P.manual_commitment(measured(), HELD, NOW + 180)[
        "manual_commitment_usd"] == D("19.80")


# ------------------------------------------------------------ the read pace

def test_terminal_order_is_read_once_and_a_working_one_every_minute():
    v = Venue({OID: row()})
    rd = MC.Reader()
    for i in range(0, 600, 5):
        m = rd.step(v, NOW + i, [OID])
        assert m["t"] == NOW + i and len(m["orders"]) == 1
    assert v.calls == [OID]
    w = Venue({OID: row(cumQuantity=10, leavesQuantity=56,
                        state="ORDER_STATE_PARTIALLY_FILLED")})
    rd = MC.Reader()
    for i in range(0, 181, 5):
        rd.step(w, NOW + i, [OID])
    assert len(w.calls) == 4                      # t = 0, 60, 120, 180


def test_reads_per_tick_are_bounded_and_the_rest_wait():
    ids = ["ORDERID%04d" % i for i in range(P.MANUAL_MAX_ORDERS + 2)]
    v = Venue({i: row(id=i) for i in ids})
    rd = MC.Reader()
    m = rd.step(v, NOW, ids)
    assert len(v.calls) == MC.READS_PER_STEP == len(m["orders"])
    whys = collections.Counter(r["why"] for r in m["rejected"])
    assert whys["TOO_MANY_ATTESTED_ORDERS"] == 2
    assert whys["NOT_READ_YET"] == P.MANUAL_MAX_ORDERS - MC.READS_PER_STEP
    for k in range(1, 20):
        m = rd.step(v, NOW + k, ids)
    assert len(m["orders"]) == P.MANUAL_MAX_ORDERS
    assert len(v.calls) == P.MANUAL_MAX_ORDERS


def test_an_id_removed_from_the_attestation_stops_counting():
    rd = MC.Reader()
    v = Venue({OID: row()})
    assert rd.step(v, NOW, [OID])["orders"]
    assert rd.step(v, NOW + 1, []) ["orders"] == [] and rd.cache == {}


def test_attested_ids_parse(monkeypatch):
    assert P.attested_order_ids({P.MANUAL_ORDERS_ENV: " a1 ,b2,,a1 "}) \
        == ["a1", "b2"]
    assert P.attested_order_ids({}) == []


# ------------------------------------------------------------------ wiring

@pytest.fixture
def on(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv(P.MANUAL_ORDERS_ENV, OID)


@pytest.fixture
def store(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.save()
    return s


def venue_holds(store, qty="66", sold="0"):
    """The sports position arrives the way every venue position does: the
    reconciliation adopts a market this ledger never booked. The first pass
    marks the book uncertain; the next one, agreeing, clears it."""
    rows = [{"marketSlug": SPORT, "qtyBoughtDecimal": qty,
             "qtySoldDecimal": sold,
             "netPositionDecimal": str(D(qty) - D(sold))}]
    store.reconcile_from_venue(rows, [])
    assert store.orders_uncertain()
    store.reconcile_from_venue(rows, [])
    assert store.orders_uncertain() is None


def test_a_sports_position_is_in_the_positions_source(store):
    """YES held is `<slug>|UP`, NO held is `<slug>|DOWN`, as the venue's
    NET position -- the mapping the value and the manual check both read."""
    venue_holds(store)
    assert store.positions_by_market()[SPORT] == {"UP": D("66"),
                                                  "DOWN": D("0")}


def test_a_sports_no_position_is_the_down_side(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "no.json"))
    s.save()
    venue_holds(s, qty="0", sold="10")
    assert s.positions_by_market()[SPORT]["DOWN"] == D("10")
    m = measured({OID: row(
        outcomeSide="OUTCOME_SIDE_NO", intent="ORDER_INTENT_BUY_SHORT",
        cumQuantity=10, quantity=10, avgPx={"value": "0.7100"},
        commissionNotionalTotalCollected={"value": "0.1000"})})
    assert P.manual_commitment(m, s.positions_by_market(), NOW)[
        "manual_commitment_usd"] == D("3.00")


class Rn:
    """The runner's own methods on a minimal object, as the other
    publication tests do it."""
    equity_risk_lines = R.Runner.equity_risk_lines
    portfolio_drawdown_report = R.Runner.portfolio_drawdown_report
    maybe_measure_manual_orders = R.Runner.maybe_measure_manual_orders

    def __init__(self, store, venue, cash="298.00", now=None):
        self.store, self.transport = store, venue
        self.available_cash_usd = D(cash)
        self.available_cash_t = NOW if now is None else now
        self.stats = collections.Counter()
        self.executor = None


def test_runner_measures_on_the_tick_and_publishes_from_the_stored_copy(
        on, store):
    venue_holds(store)
    v = Venue({OID: row()})
    r = Rn(store, v)
    # before any tick: nothing measured, so the bot figure is everything
    blk = r.equity_risk_lines(NOW)["portfolio_drawdown"]
    assert blk["split"]["manual_commitment_usd"] == "0"
    assert blk["refusal"] == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"
    assert v.calls == []                    # publishing never asks the venue
    r.maybe_measure_manual_orders(NOW)
    assert v.calls == [OID]
    blk = r.equity_risk_lines(NOW + 10)["portfolio_drawdown"]
    assert v.calls == [OID]
    assert D(blk["drawdown_usd"]) == D("2.20")
    assert blk["max_drawdown_usd"] == "5"
    assert blk["split"]["binding"] == "BOT" and blk["refusal"] is None
    assert D(blk["split"]["total_drawdown_usd"]) == D("22")
    assert reader_capacity(blk) == D("2.80")
    # the heartbeat's copy is the same block (it stamps with the wall clock)
    import time as _t
    r.maybe_measure_manual_orders(_t.time())
    hb = r.portfolio_drawdown_report()
    assert D(hb["split"]["bot_drawdown_usd"]) == D("2.20")
    assert hb["refusal"] is None
    # the tick stops (a cool-off, a hang): the stored copy goes stale
    r._manual_measurement = dict(r._manual_measurement, t=NOW)
    r.available_cash_t = NOW + 200
    blk = r.equity_risk_lines(NOW + 200)["portfolio_drawdown"]
    assert blk["split"]["manual_measurement"]["counted_zero_because"] \
        == "MANUAL_MEASUREMENT_STALE"
    assert blk["split"]["manual_commitment_usd"] == "0"
    assert blk["refusal"] == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"


def test_runner_after_the_manual_position_settles(on, store):
    v = Venue({OID: row()})
    r = Rn(store, v)                          # the venue holds nothing now
    r.maybe_measure_manual_orders(NOW)
    blk = r.equity_risk_lines(NOW)["portfolio_drawdown"]
    assert blk["split"]["manual_commitment_usd"] == "0"
    assert D(blk["split"]["manual_orders"][0]["counted_usd"]) == 0
    assert blk["refusal"] == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"


def test_runner_with_the_split_off_asks_the_venue_nothing(
        store, monkeypatch):
    for k in ALL_ENVS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(P.ANCHOR_ENV, "320.00")
    monkeypatch.setenv(P.MAX_DRAWDOWN_ENV, "5")
    monkeypatch.setenv(P.MANUAL_ORDERS_ENV, OID)
    venue_holds(store)
    v = Venue({OID: row()})
    r = Rn(store, v)
    r.maybe_measure_manual_orders(NOW)
    assert v.calls == [] and not hasattr(r, "_manual_measurement")
    blk = r.equity_risk_lines(NOW)["portfolio_drawdown"]
    assert blk == P.report(D("298.00"), P.config(BASE))
    assert "split" not in blk
    assert r.portfolio_drawdown_report() == blk


def test_a_failing_venue_never_stops_the_tick(on, store):
    class Boom:
        def order(self, oid):
            raise RuntimeError("down")
    r = Rn(store, Boom())
    r.maybe_measure_manual_orders(NOW)
    assert r._manual_measurement["rejected"][0]["why"] \
        == "ORDER_UNREADABLE:RuntimeError"
    r.transport = None                          # not even a transport
    r.maybe_measure_manual_orders(NOW + 61)
    assert r._manual_measurement["rejected"][0]["why"] \
        == "ORDER_UNREADABLE:AttributeError"


def test_the_measurement_runs_on_the_tick_not_in_the_publication():
    tick = inspect.getsource(R.Runner.tick)
    assert "self.maybe_measure_manual_orders(now)" in tick
    pub = inspect.getsource(R.Runner.equity_risk_lines) \
        + inspect.getsource(R.Runner.portfolio_manual_lines) \
        + inspect.getsource(R.Runner.portfolio_drawdown_report)
    assert "transport" not in pub and ".order(" not in pub


# ------------------------------------------------------- this service's gate

def gate(value, manual=None):
    return G.ValidationGates().refuse(
        order_qty=D("1"), order_price=D("0.40"),
        order_intent="ORDER_INTENT_BUY_LONG", portfolio_value_usd=value,
        portfolio_manual=manual)


def test_own_gate_is_bound_by_both_limits(on):
    m = commit()
    clear = gate(D("320"))
    assert not (clear or "").startswith("PORTFOLIO_")
    assert gate(D("298.00"), m) == clear               # manual, not a bot loss
    assert gate(D("298.00")) == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"
    assert gate(D("295.00"), m) == "PORTFOLIO_DRAWDOWN_LIMIT:5.2000>=5"
    big = dict(m, manual_commitment_usd=D("40"))
    assert gate(D("279.00"), big) \
        == "PORTFOLIO_DRAWDOWN_LIMIT:TOTAL:41.00>=40"


class Ex:
    portfolio_manual_for_gates = LX.FaithfulExecutor.portfolio_manual_for_gates

    def __init__(self, store):
        self.store = store


def test_executor_hands_the_gate_the_stored_measurement(on, store):
    import time as _t
    venue_holds(store)
    e = Ex(store)
    assert e.portfolio_manual_for_gates()["manual_commitment_usd"] == 0
    e.portfolio_manual_measurement = measured(now=_t.time())
    got = e.portfolio_manual_for_gates()
    assert got["manual_commitment_usd"] == D("19.80")
    assert gate(D("298.00"), got) == gate(D("320"))
    e.portfolio_manual_measurement = measured(now=_t.time() - 181)
    assert e.portfolio_manual_for_gates()["manual_commitment_usd"] == 0


def test_executor_computes_nothing_with_the_split_off(store, monkeypatch):
    for k in ALL_ENVS:
        monkeypatch.delenv(k, raising=False)
    e = Ex(store)
    e.portfolio_manual_measurement = measured()
    assert e.portfolio_manual_for_gates() is None


def test_runner_hands_the_executor_the_measurement(on, store):
    r = Rn(store, Venue({OID: row()}))
    r.executor = Ex(store)
    r.maybe_measure_manual_orders(NOW)
    assert r.executor.portfolio_manual_measurement is r._manual_measurement
