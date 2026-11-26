"""Manual orders identified by the venue's own indicator (owner 2026-11-26).

"prepare the existing bot-versus-total account risk split so future manual
trades do not unnecessarily interrupt L5. Keep the bot's $20 limit
unchanged. Do not activate the split or increase account-wide limits
without separate approval."

OFF by default; with L5_MANUAL_BY_VENUE_INDICATOR=1 (and its SINCE instant)
manual order ids are found from the activity feed instead of an attested
list, and each is verified and costed by the existing reader.
"""
from decimal import Decimal as D

import pytest

from src import l5_manual_commitment as MC
from src import l5_portfolio_drawdown as P

SPORT = "aec-nfl-bal-atl-2026-11-29"
OID, SELL_OID, BOT_OID = "D1SA9RRSTZ73", "S2QQ7PLMN041", "B0T0RDER0001"
NOW = 1_800_000_000.0                         # 2027-01-15T08:00:00Z
SINCE = "2027-01-15T00:00:00Z"
T_IN = "2027-01-15T07:00:00.123456789Z"       # after SINCE
T_OLD = "2027-01-14T07:00:00Z"                # before SINCE
BASE = {P.ANCHOR_ENV: "320.00", P.MAX_DRAWDOWN_ENV: "5"}
ENV = dict(BASE, **{P.TOTAL_MAX_DRAWDOWN_ENV: "40"})
AUTO = {P.AUTO_MANUAL_ENV: "1", P.AUTO_MANUAL_SINCE_ENV: SINCE}
ALL_ENVS = (P.ANCHOR_ENV, P.MAX_DRAWDOWN_ENV, P.TOTAL_MAX_DRAWDOWN_ENV,
            P.MANUAL_ORDERS_ENV, P.AUTO_MANUAL_ENV, P.AUTO_MANUAL_SINCE_ENV)
HELD = {SPORT: {"UP": D("66"), "DOWN": D("0")}}
MANUAL = MC.MANUAL_INDICATOR
AUTOMATIC = "MANUAL_ORDER_INDICATOR_AUTOMATIC"


def order_row(oid=OID, **over):
    """A filled manual BUY YES: 66 x 0.29 + 0.66 commission = 19.80."""
    r = {"id": oid, "marketSlug": SPORT, "state": "ORDER_STATE_FILLED",
         "quantity": 66, "cumQuantity": 66, "leavesQuantity": 0,
         "avgPx": {"value": "0.2900", "currency": "USD"},
         "commissionNotionalTotalCollected": {"value": "0.6600"},
         "manualOrderIndicator": MANUAL,
         "outcomeSide": "OUTCOME_SIDE_YES",
         "intent": "ORDER_INTENT_BUY_LONG", "action": "ORDER_ACTION_BUY"}
    r.update(over)
    return r


def sell_row(px):
    """A closing manual SELL of the 66 YES at `px`, 0.66 commission."""
    r = order_row(SELL_OID, avgPx={"value": px, "currency": "USD"},
                  intent="ORDER_INTENT_SELL_LONG", side="ORDER_SIDE_SELL")
    r.pop("action")
    return r


def trade(oid=OID, indicator=MANUAL, t=T_IN, tid=None, aggressor=True,
          qty="66"):
    """An activity-feed trade row: two parties, `isAggressor` says ours."""
    mine = {"order": {"id": oid, "manualOrderIndicator": indicator},
            "lastShares": qty, "outcomeSide": "OUTCOME_SIDE_YES"}
    other = {"order": {"id": "CPTY" + oid[-6:],
                       "manualOrderIndicator": MANUAL},
             "lastShares": qty, "outcomeSide": "OUTCOME_SIDE_NO"}
    body = {"marketSlug": SPORT, "id": tid or "T-" + oid,
            "aggressorExecution": mine, "passiveExecution": other}
    if t is not None:
        body["time"] = t
    if aggressor is not None:
        body["isAggressor"] = aggressor
    return {"type": "activity_type_trade", "trade": body}


class Venue:
    """Order rows by id plus a paged, newest-first activity feed."""

    def __init__(self, rows, pages, fail_feed=False):
        self.rows, self.pages, self.fail_feed = rows, pages, fail_feed
        self.calls, self.feed_calls = [], []

    def order(self, oid):
        self.calls.append(oid)
        return dict(self.rows.get(oid) or {})

    def _get(self, path):
        self.feed_calls.append(path)
        if self.fail_feed:
            raise RuntimeError("feed down")
        n = int(path.split("cursor=p")[1]) if "cursor=p" in path else 0
        last = n + 1 >= len(self.pages)
        return {"activities": list(self.pages[n]), "eof": last,
                "nextCursor": "" if last else "p%d" % (n + 1)}


@pytest.fixture
def env(monkeypatch):
    for k in ALL_ENVS:
        monkeypatch.delenv(k, raising=False)

    def set_(d):
        for k, v in d.items():
            monkeypatch.setenv(k, v)
    return set_


def cfgs():
    c = P.config(ENV)
    return c, P.split_config(c, ENV)


def measure(venue, now=NOW, reader=None):
    return (reader or MC.Reader()).step(venue, now)


def block(m, value, pos=None, now=NOW + 5):
    c, s = cfgs()
    man = P.manual_commitment(m, HELD if pos is None else pos, now)
    return man, P.report(value, c, split=s, manual=man), \
        P.refusal(value, c, split=s, manual=man)


# ------------------------------------------------------------ off by default

def test_off_by_default_reads_no_feed_and_changes_nothing(env):
    env(ENV)
    assert P.auto_manual_config() == {"state": P.OFF}
    v = Venue({OID: order_row()}, [[trade()]])
    m = measure(v)
    assert v.feed_calls == [] and v.calls == []
    assert m == {"t": NOW, "orders": [], "rejected": [], "unclassified": [],
                 "reads": 0}
    man, rep, ref = block(m, D("298.00"))
    sp = rep["split"]
    # the manual trade is NOT taken off: bot == total, exactly as before
    assert D(sp["bot_drawdown_usd"]) == D(sp["total_drawdown_usd"]) \
        == D("22.00")
    assert ref == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"
    assert sp["manual_identification"]["by_venue_indicator"] == P.OFF


@pytest.mark.parametrize("value", [D("330"), D("298.00")])
def test_split_off_the_mode_changes_nothing_even_half_configured(
        env, value):
    for auto in (AUTO, {P.AUTO_MANUAL_ENV: "1"}, {P.AUTO_MANUAL_ENV: "yes"}):
        env(dict(BASE, **auto))
        c = P.config(BASE)
        before = P.report(value, c)
        assert "split" not in before
        assert P.refusal(value, c) == (
            None if value == D("330") else "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5")


def test_no_default_limit_moved():
    assert P.MANUAL_MAX_AGE_S == 180.0 and P.MANUAL_MAX_ORDERS == 25
    assert P.split_config({"state": "ON", "max_drawdown_usd": D("20")},
                          {}) == {"state": P.OFF}
    assert P.config({}) == {"state": P.OFF}


@pytest.mark.parametrize("auto,why", [
    ({P.AUTO_MANUAL_ENV: "1"}, "AUTO_MANUAL_HALF_CONFIGURED"),
    ({P.AUTO_MANUAL_SINCE_ENV: SINCE}, "AUTO_MANUAL_HALF_CONFIGURED"),
    ({P.AUTO_MANUAL_ENV: "true", P.AUTO_MANUAL_SINCE_ENV: SINCE},
     "AUTO_MANUAL_SWITCH_UNREADABLE"),
    ({P.AUTO_MANUAL_ENV: "1", P.AUTO_MANUAL_SINCE_ENV: "2027-01-15"},
     "AUTO_MANUAL_SINCE_UNREADABLE"),
    ({P.AUTO_MANUAL_ENV: "1", P.AUTO_MANUAL_SINCE_ENV: "2027-01-15T00:00"},
     "AUTO_MANUAL_SINCE_UNREADABLE")])
def test_half_configured_refuses_while_the_split_is_on(env, auto, why):
    env(dict(ENV, **auto))
    assert P.auto_manual_config()["why"] == why
    c, s = cfgs()
    assert P.refusal(D("330"), c, split=s, manual=None) \
        == "PORTFOLIO_MANUAL_" + why


def test_switch_zero_is_off(env):
    env(dict(ENV, **{P.AUTO_MANUAL_ENV: "0", P.AUTO_MANUAL_SINCE_ENV: SINCE}))
    assert P.auto_manual_config() == {"state": P.OFF}


# ------------------------------------------------------------------ mode on

def test_a_venue_manual_open_buy_is_off_the_bot_figure_but_in_total(env):
    env(dict(ENV, **AUTO))
    v = Venue({OID: order_row()}, [[trade()]])
    m = measure(v)
    assert v.calls == [OID]                       # verified by id
    assert [o["order_id_tail"] for o in m["orders"]] == [OID[-4:]]
    assert m["rejected"] == [] and m["discovery"]["status"] is None
    man, rep, ref = block(m, D("298.00"))
    assert man["manual_commitment_usd"] == D("19.80")
    sp = rep["split"]
    assert D(sp["total_drawdown_usd"]) == D("22.00")       # never adjusted
    assert D(sp["bot_drawdown_usd"]) == D("2.20")
    assert ref is None
    assert sp["manual_identification"]["by_venue_indicator"] == "ON"
    # the full id is never published
    assert OID not in repr(rep)


def test_a_closed_manual_round_trip_loss_stays_off_the_bot_figure(env):
    env(dict(ENV, **AUTO))
    # newest first: the sale, then the buy. 66 x 0.20 - 0.66 = 12.54 back
    v = Venue({OID: order_row(), SELL_OID: sell_row("0.2000")},
              [[trade(SELL_OID), trade(OID)]])
    m = measure(v)
    assert {o["action"] for o in m["orders"]} == {"BUY", "SELL"}
    man, rep, ref = block(m, D("312.74"), pos={})
    assert man["manual_settled_net_usd"] == D("7.26")
    assert man["manual_markets"][0]["status"] == "CLOSED"
    sp = rep["split"]
    assert D(sp["total_drawdown_usd"]) == D("7.26")
    assert D(sp["bot_drawdown_usd"]) == D("0")
    assert ref is None


def test_a_manual_gain_is_added_back_to_the_bot_figure(env):
    env(dict(ENV, **AUTO))
    # 66 x 0.50 - 0.66 = 32.34 back on 19.80 out: a 12.54 manual gain
    v = Venue({OID: order_row(), SELL_OID: sell_row("0.5000")},
              [[trade(SELL_OID), trade(OID)]])
    m = measure(v)
    # the account is up 12.54 by hand and down 3.00 by the bot
    man, rep, ref = block(m, D("329.54"), pos={})
    assert man["manual_settled_net_usd"] == D("-12.54")
    sp = rep["split"]
    assert D(sp["total_drawdown_usd"]) == D("-9.54")
    assert D(sp["bot_drawdown_usd"]) == D("3.00")
    # 2.01 more of bot loss trips the bot limit although the total is up
    _m, _r, ref = block(m, D("327.53"), pos={})
    assert ref == "PORTFOLIO_DRAWDOWN_LIMIT:5.0100>=5"


def test_an_automatic_order_is_never_manual(env):
    env(dict(ENV, **AUTO))
    # the feed says AUTOMATIC (our side; the counterparty is MANUAL)
    v = Venue({BOT_OID: order_row(BOT_OID)},
              [[trade(BOT_OID, indicator=AUTOMATIC)]])
    m = measure(v)
    assert v.calls == [] and m["orders"] == [] and m["rejected"] == []
    _man, rep, ref = block(m, D("298.00"))
    assert D(rep["split"]["bot_drawdown_usd"]) == D("22.00")
    assert ref == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"
    # the feed says MANUAL but the venue's order row does not: not manual,
    # and harmless (no refusal of its own)
    v = Venue({OID: order_row(manualOrderIndicator=AUTOMATIC)}, [[trade()]])
    m = measure(v)
    assert m["orders"] == [] and m["rejected"][0]["why"] \
        == "NOT_MANUAL_PER_VENUE"
    _man, rep, ref = block(m, D("298.00"))
    assert D(rep["split"]["bot_drawdown_usd"]) == D("22.00")
    assert ref == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"


@pytest.mark.parametrize("row", [
    trade(indicator=None), trade(indicator=""),
    trade(indicator="MANUAL"),                    # not the venue's value
    trade(aggressor=None)])                       # which party is ours: ?
def test_an_unreadable_indicator_is_not_manual_bot_figure_larger(env, row):
    env(dict(ENV, **AUTO))
    v = Venue({OID: order_row()}, [[row]])
    m = measure(v)
    assert v.calls == [] and m["orders"] == [] and m["unclassified"] == []
    _man, rep, ref = block(m, D("298.00"))
    sp = rep["split"]
    assert D(sp["bot_drawdown_usd"]) == D(sp["total_drawdown_usd"])
    assert ref == "PORTFOLIO_DRAWDOWN_LIMIT:22.00>=5"


def test_a_manual_row_that_cannot_be_costed_is_a_possible_full_win(env):
    env(dict(ENV, **AUTO))
    v = Venue({}, [[trade(t=None)]])
    m = measure(v)
    assert m["unclassified"][0]["why"] == "MANUAL_ROW_UNDATED"
    man, rep, _ref = block(m, D("320"), pos={})
    # 66 contracts counted as a possible $66 win: bot figure only larger
    assert man["manual_settled_net_usd"] == D("-66")
    assert D(rep["split"]["bot_drawdown_usd"]) == D("66")


def test_a_manual_trade_before_since_is_not_counted(env):
    env(dict(ENV, **AUTO))
    v = Venue({OID: order_row()}, [[trade(t=T_OLD)]])
    m = measure(v)
    assert v.calls == [] and m["orders"] == []


def test_the_total_limit_still_binds(env):
    env(dict(ENV, **AUTO))
    rows = {OID: order_row(quantity=150, cumQuantity=150,
                           avgPx={"value": "0.2600", "currency": "USD"},
                           commissionNotionalTotalCollected={"value": "1"})}
    v = Venue(rows, [[trade(qty="150")]])
    m = measure(v)
    pos = {SPORT: {"UP": D("150"), "DOWN": D("0")}}
    man, rep, ref = block(m, D("279.00"), pos=pos)
    assert man["manual_commitment_usd"] == D("40.00")
    assert D(rep["split"]["bot_drawdown_usd"]) == D("1.00")
    assert ref == "PORTFOLIO_DRAWDOWN_LIMIT:TOTAL:41.00>=40"


# ------------------------------------------------------ fail closed, bounded

def test_until_discovery_completes_new_exposure_is_refused(env):
    env(dict(ENV, **AUTO))
    v = Venue({}, [[trade()]], fail_feed=True)
    m = measure(v)
    assert m["rejected"] == [{"order_id_tail": "",
                              "why": "AUTO_MANUAL_DISCOVERY_INCOMPLETE"}]
    _man, _rep, ref = block(m, D("330"))
    assert ref == ("PORTFOLIO_MANUAL_ORDER_UNREADABLE:"
                   "AUTO_MANUAL_DISCOVERY_INCOMPLETE")


def test_a_discovery_gone_stale_refuses(env):
    env(dict(ENV, **AUTO))
    r = MC.Reader()
    v = Venue({OID: order_row()}, [[trade()]])
    measure(v, reader=r)
    v.fail_feed = True
    m = measure(v, NOW + MC.DISCOVERY_MAX_AGE_S + 1, reader=r)
    assert {"order_id_tail": "", "why": "AUTO_MANUAL_DISCOVERY_STALE"} \
        in m["rejected"]
    assert [o["order_id_tail"] for o in m["orders"]] == [OID[-4:]]


def test_read_cost_is_bounded_per_pass_and_walks_are_spaced(env):
    env(dict(ENV, **AUTO))
    pages = [[trade(tid="T%d" % n, t="2027-01-15T07:%02d:00Z" % (59 - n),
                    indicator=AUTOMATIC)] for n in range(6)]
    v = Venue({}, pages)
    r = MC.Reader()
    m = measure(v, reader=r)
    assert m["pages"] == MC.PAGES_PER_STEP == len(v.feed_calls)
    assert m["rejected"][0]["why"] == "AUTO_MANUAL_DISCOVERY_INCOMPLETE"
    # within the step interval: no page at all
    assert measure(v, NOW + 1, reader=r)["pages"] == 0
    t = NOW
    while r.discovery.walk is not None or r.discovery.completed_t is None:
        t += MC.STEP_INTERVAL_S
        assert measure(v, t, reader=r)["pages"] <= MC.PAGES_PER_STEP
    assert len(v.feed_calls) == 6
    assert measure(v, t + MC.STEP_INTERVAL_S, reader=r)["rejected"] == []
    # no new walk before REWALK_S
    assert len(v.feed_calls) == 6


def test_a_walk_stops_at_since(env):
    env(dict(ENV, **AUTO))
    pages = [[trade()], [trade("OLD0RDER0001", t=T_OLD)],
             [trade("OLDER0RDER02", t="2027-01-13T00:00:00Z")]]
    v = Venue({OID: order_row()}, pages)
    m = measure(v)
    assert len(v.feed_calls) == 2 and m["discovery"]["status"] is None
    assert v.calls == [OID]


def test_explicit_ids_never_walk_the_feed(env):
    env(dict(ENV, **AUTO))
    v = Venue({OID: order_row()}, [[trade()]])
    m = MC.Reader().step(v, NOW, [OID])
    assert v.feed_calls == [] and "discovery" not in m
