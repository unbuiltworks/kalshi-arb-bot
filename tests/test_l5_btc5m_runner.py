"""The BTC5m live session, end to end against a scripted venue, plus the
shared fill-cost correction in the account-wide ledger.

Deterministic and offline. The adapter, the authorization claim, the
approval gate, the window ledger and the account-wide store are the real
ones. The venue is a script, so nothing here is evidence about the real
venue or about economics; it proves the sequence: preflight, arm, place,
fill, cancel by exact id, settle, reconcile, read fees, report, stop.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_btc5m_runner as RUN
from src import l5_live_authorization as AUTH
from src import l5_live_execution as E
from src import l5_origin as OG
from tests import test_l5_btc5m_execution as T5

UTC = dt.timezone.utc
M = "cpc-btc-updown-5m-2026-11-11-1200z"
WS = dt.datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
M15 = "cpc-btc-updown-15m-2026-11-11-1200z"


class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(float(s), 0.25)


class LiveVenue(T5.Venue):
    """The adapter's scripted venue, plus what a session also calls."""

    def __init__(self, clock, *, preflight="PASSED", settle_px="1",
                 settle_after_s=360.0, fee="0"):
        super().__init__()
        self.clock = clock
        self.armed = False
        self.pf = preflight
        self.settle_px, self.settle_after_s = settle_px, settle_after_s
        self.fee = fee
        self.disarmed_with = None
        self.cancel_owner_oracle = None

    def preflight(self):
        return {"preflight": self.pf,
                "checks": [{"check": "slug_is_btc5m_series",
                            "passed": self.pf == "PASSED"}]}

    def arm(self):
        if self.pf != "PASSED":
            raise RuntimeError("preflight has not passed")
        self.armed = True
        return {"armed": True}

    def disarm(self, reason=""):
        self.armed, self.disarmed_with = False, reason
        return {"disarmed": True}

    def cancel(self, order_id, market_slug=None):
        # the transport's own ownership test, installed by the session
        assert self.cancel_owner_oracle is not None
        assert self.cancel_owner_oracle(order_id), order_id
        return super().cancel(order_id, market_slug)

    def order(self, oid):
        row = super().order(oid)
        row["commissionNotionalTotalCollected"] = {"value": self.fee}
        return row

    def settlement(self, slug):
        done = self.clock() >= WS.timestamp() + self.settle_after_s
        return {"terminal": done, "settlement_px":
                D(self.settle_px) if done else None,
                "not_terminal_reason": None if done else "STATE_NOT_TERMINAL"}


def ok_risk(now, own):
    return {"ok": True, "reason": None, "own": str(own),
            "loss_room_usd": "5.00"}


def ok_halt():
    return {"ok": True}


class Rig:
    def __init__(self, tmp_path, monkeypatch, *, venue_kw=None,
                 risk=ok_risk, halt=ok_halt, fill_at=None, book=None,
                 arm=True):
        self.clock = Clock(WS.timestamp() - 40)
        T5.authorize_5m(monkeypatch, tmp_path,
                        now_fn=lambda: dt.datetime.fromtimestamp(
                            self.clock(), UTC), starts=(WS,))
        for k, v in T5.ARMED.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv(X.MARKET_ENV, M)
        monkeypatch.setenv(X.NOT_AFTER_ENV, "2026-11-11T12:05:00Z")
        self.venue = LiveVenue(self.clock, **(venue_kw or {}))
        self.ex = X.Btc5mExecutor(
            self.venue, str(tmp_path / "btc5m" / "ledger.json"),
            now_fn=lambda: dt.datetime.fromtimestamp(self.clock(), UTC))
        self.fill_at = dict(fill_at or {})
        self.book = book or (lambda t: {"bid": D("0.60"), "ask": D("0.62"),
                                        "age_s": 0.1})
        self.session = RUN.Session(
            market=M, executor=self.ex, transport=self.venue,
            root=str(tmp_path / "root"), book_fn=self._book,
            p_fn=lambda m, ws, now: {"ok": True, "p_up": "0.75"},
            risk_fn=risk, halt_fn=halt, clock=self.clock,
            sleep=self.clock.sleep,
            # the scripted venue's own arm. Production has no arm_fn: the
            # session cannot arm until authorize_arming covers it
            arm_fn=(lambda t: t.arm()) if arm is True else None,
            bounds_fn=lambda m: {
                "start": WS.isoformat(),
                "end": (WS + dt.timedelta(minutes=5)).isoformat(),
                "source": "VENUE"} if m == M else None)

    def _book(self, market):
        t = self.clock() - WS.timestamp()
        for at, (oid, qty) in list(self.fill_at.items()):
            if t >= at and oid in self.venue.by_oid:
                self.venue.fill(oid, qty)
                self.fill_at.pop(at)
        return self.book(t)

    def kinds(self, kind):
        return [e for e in self.session.events if e["kind"] == kind]


# ======================================================================
# 1. ONE WINDOW, LIVE, START TO FINISH
# ======================================================================
def test_a_session_places_fills_cancels_settles_and_reports(tmp_path,
                                                            monkeypatch):
    rig = Rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", "0.05")},
              venue_kw={"fee": "0", "settle_px": "1"})
    rep = rig.session.run()
    v = rig.venue
    # armed through the transport's own arm, after preflight, which ran at
    # the window start and not at launch (a 5-minute market reads CLOSED
    # until its window opens)
    start = rig.kinds("L5_BTC5M_START")[0]
    assert start["transport_armed"] is True
    assert start["utc"] >= WS.isoformat()
    assert v.cancel_owner_oracle == rig.ex.owns_venue_order_id
    # nothing before the window opened
    first = min(e["utc"] for e in rig.kinds("L5_BTC5M_STEP"))
    assert first >= WS.isoformat()
    # every order sent was a post-only maker buy on THIS market, with a claim
    assert v.sent and all(s["market"] == M and s["post_only"] is True
                          for s in v.sent)
    for s in v.sent:
        assert AUTH.claim_held(AUTH.btc5m_sid(AUTH.experiment_id(),
                                              s["client_order_id"]))
    # cancels named only our own venue ids, and nothing is left resting
    owned = {r["venue_order_id"] for r in rig.ex.orders().values()}
    assert {oid for oid, _m in v.cancelled} <= owned
    assert rig.ex.live_orders() == {}
    assert rep["close"]["clean"] is True
    assert v.disarmed_with == "BTC5M_SESSION_CLOSED" and v.armed is False
    # nothing placed in the last CANCEL_LEAD_S seconds of the window
    for e in rig.kinds("L5_BTC5M_STEP"):
        assert e["t_in_window_s"] < RUN.WINDOW_S - RUN.CANCEL_LEAD_S
    # settled from the venue's own settlement, once
    assert rep["settlement"]["settled"] is True
    assert rep["settlement"]["settlement_px"] == "1"
    assert rep["filled_orders"] >= 1 and D(rep["fills_qty"]) >= D("0.05")
    # fees are the venue's own, read back per order
    assert rep["venue_fees"]["complete"] is True
    assert D(rep["venue_fees"]["total_usd"]) == 0
    assert rep["settled_net_usd"] == str(
        D(rep["settlement"]["settlement_pnl"]) - D(0))
    # the venue's position row and our inventory agree
    assert rep["reconcile"]["read"] is True and rep["reconcile"]["agrees"]
    assert rep["would_submit_not_sent"] == 0
    assert "REALIZED once the venue credits" in rep["labels"]
    # and it will not run this market again
    assert os.path.exists(rig.session.marker())
    again = rig.session.run()
    assert again == {"started": False, "ok": False,
                     "reason": "MARKET_ALREADY_RUN",
                     "marker": rig.session.marker()}


def test_venue_commission_is_subtracted_as_the_venue_reports_it(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", None)},
              venue_kw={"fee": "0.01", "settle_px": "0"})
    rep = rig.session.run()
    n = len(rep["venue_fees"]["per_order"])
    assert n >= 1 and D(rep["venue_fees"]["total_usd"]) == D("0.01") * n
    assert D(rep["settled_net_usd"]) == D(
        rep["settlement"]["settlement_pnl"]) - D("0.01") * n


def test_an_unreadable_commission_leaves_the_net_unstated(tmp_path,
                                                          monkeypatch):
    rig = Rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", None)})
    rig.venue.fee = None
    base = rig.venue.order

    def no_fee(oid):
        row = base(oid)
        row.pop("commissionNotionalTotalCollected", None)
        return row
    monkeypatch.setattr(rig.venue, "order", no_fee)
    rep = rig.session.run()
    assert rep["venue_fees"]["complete"] is False
    assert rep["settled_net_usd"] is None


# ======================================================================
# 2. WHAT STOPS IT
# ======================================================================
def test_a_failed_preflight_sends_nothing_and_says_would_submit(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, venue_kw={"preflight": "FAILED"})
    rep = rig.session.run()
    assert rig.venue.sent == [] and rig.venue.armed is False
    assert rep["transport_armed"] is False and rep["submitted"] == 0
    assert rep["would_submit_not_sent"] > 0
    assert rep["fills_qty"] == "0"


@pytest.mark.parametrize("which", ["halt", "risk"])
def test_a_refusing_gate_cancels_our_orders_and_places_nothing_new(
        tmp_path, monkeypatch, which):
    state = {"block_from": 20.0}

    def gate(*_a):
        t = rig.clock() - WS.timestamp()
        if t >= state["block_from"]:
            return {"ok": False, "reason": "TEST_REFUSAL"}
        return {"ok": True, "loss_room_usd": "5.00"}
    kw = {"halt": gate} if which == "halt" else {"risk": gate}
    rig = Rig(tmp_path, monkeypatch, **kw)
    rig.session.run()
    sent_before = [s for s in rig.venue.sent]
    blocked = rig.kinds("L5_BTC5M_BLOCKED")
    assert blocked and all("TEST_REFUSAL" in b["reason"] for b in blocked)
    # every order it had resting when the gate refused was cancelled at once
    first_block = min(b["utc"] for b in blocked)
    assert rig.ex.live_orders() == {}
    assert all(e["utc"] < first_block for e in rig.kinds("L5_BTC5M_STEP"))
    assert len(rig.venue.sent) == len(sent_before)


def test_the_operator_halt_blocks(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    monkeypatch.setenv(AUTH.LIVE_HALT_ENV, "1")
    rep = rig.session.run()
    assert rig.venue.sent == [] and rep["submitted"] == 0
    assert all(b["reason"] == "OPERATOR_HALT"
               for b in rig.kinds("L5_BTC5M_BLOCKED"))


def test_no_owner_approval_means_no_orders_even_fully_armed(tmp_path,
                                                            monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    from src import l5_campaign_approval as CA
    monkeypatch.delenv(CA.APPROVAL_ENV)
    rep = rig.session.run()
    assert rig.venue.sent == [] and rep["submitted"] == 0
    refused = [r for e in rig.kinds("L5_BTC5M_STEP") for r in e["refused"]]
    assert refused and all(r == "AUTHORIZATION:BTC5M_APPROVAL_ABSENT"
                           for r in refused)


def test_a_market_outside_the_series_never_starts(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.session.market = M15
    rep = rig.session.run()
    assert rep["started"] is False and rep["reason"] == "NOT_A_BTC5M_MARKET"
    assert rig.venue.sent == []


# ======================================================================
# 3. THE ACCOUNT-WIDE LOSS STATE, READ FROM THE BTC15m SERVICE
# ======================================================================
NOW = dt.datetime(2026, 11, 12, 15, 0, 30, tzinfo=UTC)


def risk_doc(**over):
    r = {"readable": True, "reason": None, "utc_day": "2026-11-12",
         "utc_day_realized_usd": "-1.00", "daily_halt_usd": "5",
         "lifetime_realized_usd": "-10.00",
         "cumulative_loss_ceiling_usd": "20", "operator_halt": False,
         # the venue-equity blocks are REQUIRED in the record since
         # 2026-11-20; OFF is "the owner configured no such limit"
         "portfolio_drawdown": {"state": "OFF", "refusal": None},
         "equity_baseline": {"state": "OFF", "refusal": None}}
    r.update(over)
    return {"as_of_utc": (NOW - dt.timedelta(seconds=3)).isoformat(),
            "account_risk": r}


def _on(room="3.00", **over):
    b = {"state": "ON", "refusal": None, "room_usd": room}
    b.update(over)
    return b


def test_the_record_must_carry_both_venue_equity_blocks():
    d = risk_doc()
    d["account_risk"].pop("portfolio_drawdown")
    assert risk(doc=d)["reason"] == "ACCOUNT_DRAWDOWN_NOT_PUBLISHED"
    d = risk_doc()
    d["account_risk"].pop("equity_baseline")
    assert risk(doc=d)["reason"] == "ACCOUNT_EQUITY_BASELINE_NOT_PUBLISHED"
    assert risk(portfolio_drawdown="OFF")["reason"] == \
        "ACCOUNT_DRAWDOWN_NOT_PUBLISHED"


@pytest.mark.parametrize("block,reason", [
    (_on(refusal="PORTFOLIO_DRAWDOWN_LIMIT:15.2018>=15", room="-0.2018"),
     "ACCOUNT_DRAWDOWN:PORTFOLIO_DRAWDOWN_LIMIT:15.2018>=15"),
    (_on(refusal="PORTFOLIO_VALUE_UNAVAILABLE"),
     "ACCOUNT_DRAWDOWN:PORTFOLIO_VALUE_UNAVAILABLE"),
    ({"state": "OFF", "refusal": "PORTFOLIO_DRAWDOWN_CONFIG_UNREADABLE"},
     "ACCOUNT_DRAWDOWN:PORTFOLIO_DRAWDOWN_CONFIG_UNREADABLE"),
    ({"state": "INVALID", "refusal": None}, "ACCOUNT_DRAWDOWN_STATE_INVALID"),
    ({"state": "UNREADABLE", "refusal": "EQUITY_LINES_FAILED:KeyError"},
     "ACCOUNT_DRAWDOWN:EQUITY_LINES_FAILED:KeyError"),
    ({"state": None, "refusal": None}, "ACCOUNT_DRAWDOWN_STATE_None"),
    ({"state": "ON", "refusal": None}, "ACCOUNT_DRAWDOWN_ROOM_UNREADABLE"),
    (_on(room="x"), "ACCOUNT_DRAWDOWN_ROOM_UNREADABLE"),
    (_on(room="0"), "ACCOUNT_DRAWDOWN_NO_ROOM"),
    (_on(room="-1"), "ACCOUNT_DRAWDOWN_NO_ROOM"),
])
def test_the_published_drawdown_limit_binds_this_lane(block, reason):
    assert risk(portfolio_drawdown=block)["reason"] == reason


@pytest.mark.parametrize("block,reason", [
    (_on(refusal="EQUITY_DRAWDOWN_LIMIT:5.0000>=5", room="0"),
     "ACCOUNT_EQUITY_BASELINE:EQUITY_DRAWDOWN_LIMIT:5.0000>=5"),
    (_on(refusal="EQUITY_TRANSFERS_NOT_ESTABLISHED"),
     "ACCOUNT_EQUITY_BASELINE:EQUITY_TRANSFERS_NOT_ESTABLISHED"),
    (_on(refusal="EQUITY_UNAVAILABLE"),
     "ACCOUNT_EQUITY_BASELINE:EQUITY_UNAVAILABLE"),
    ({"state": "INVALID", "refusal": "EQUITY_BASELINE_FIELDS"},
     "ACCOUNT_EQUITY_BASELINE:EQUITY_BASELINE_FIELDS"),
    (_on(room=None), "ACCOUNT_EQUITY_BASELINE_ROOM_UNREADABLE"),
])
def test_the_published_equity_baseline_binds_this_lane(block, reason):
    assert risk(equity_baseline=block)["reason"] == reason


def test_the_older_limits_are_judged_first_and_are_not_replaced():
    """A clean equity record buys nothing against the cumulative ceiling."""
    got = risk(lifetime_realized_usd="-136.7845",
               cumulative_loss_ceiling_usd="75",
               portfolio_drawdown=_on(), equity_baseline=_on())
    assert got["reason"] == "ACCOUNT_CUMULATIVE_LOSS_CEILING"
    assert risk(utc_day_realized_usd="-5.00", portfolio_drawdown=_on(),
                equity_baseline=_on())["reason"] == "ACCOUNT_DAILY_LOSS_HALT"


def test_loss_room_is_the_tightest_of_all_four():
    # daily room 4.00, cumulative room 10.00
    assert risk()["loss_room_usd"] == "4.00"
    got = risk(portfolio_drawdown=_on("2.50"), equity_baseline=_on("0.75"))
    assert got["ok"] is True and got["loss_room_usd"] == "0.75"
    assert got["portfolio_drawdown_room_usd"] == "2.50"
    assert risk(portfolio_drawdown=_on("9"),
                equity_baseline=_on("9"))["loss_room_usd"] == "4.00"


def risk(doc=None, own="0", **over):
    d = doc if doc is not None else risk_doc(**over)
    return RUN.account_risk(url="http://15m/registry", now=NOW,
                            own_unaccounted=D(own), fetch=lambda u: d)


def test_a_fresh_account_state_inside_both_limits_permits():
    got = risk()
    assert got["ok"] is True
    assert got["utc_day_realized_usd"] == "-1.00"


@pytest.mark.parametrize("over,reason", [
    ({"utc_day_realized_usd": "-5.00"}, "ACCOUNT_DAILY_LOSS_HALT"),
    ({"utc_day_realized_usd": "-5.0138"}, "ACCOUNT_DAILY_LOSS_HALT"),
    ({"lifetime_realized_usd": "-21.0406"}, "ACCOUNT_CUMULATIVE_LOSS_CEILING"),
    ({"lifetime_realized_usd": "-20.00"}, "ACCOUNT_CUMULATIVE_LOSS_CEILING"),
    ({"readable": False, "reason": "DAY_READ_FAILED"},
     "ACCOUNT_RISK_SAYS_UNREADABLE"),
    ({"utc_day_realized_usd": None}, "ACCOUNT_RISK_FIGURES_UNREADABLE"),
    ({"cumulative_loss_ceiling_usd": "x"}, "ACCOUNT_RISK_FIGURES_UNREADABLE"),
    ({"utc_day": "2026-11-11"}, "ACCOUNT_RISK_FOR_ANOTHER_DAY"),
])
def test_each_account_condition_refuses(over, reason):
    assert risk(**over)["reason"] == reason


def test_this_strategys_own_losses_are_added_and_its_gains_are_not():
    assert risk(utc_day_realized_usd="-4.50", own="-0.60")["reason"] == \
        "ACCOUNT_DAILY_LOSS_HALT"
    assert risk(utc_day_realized_usd="-4.50", own="+3.00")["ok"] is True
    got = risk(utc_day_realized_usd="-4.50", own="+3.00")
    assert got["utc_day_realized_usd"] == "-4.50"   # a gain buys no room


@pytest.mark.parametrize("doc,reason", [
    ({}, "ACCOUNT_RISK_NOT_PUBLISHED"),
    ({"account_risk": {"readable": True}}, "ACCOUNT_RISK_UNDATED"),
    ({"as_of_utc": (NOW - dt.timedelta(seconds=31)).isoformat(),
      "account_risk": {"readable": True}}, "ACCOUNT_RISK_STALE"),
])
def test_a_missing_or_stale_document_refuses(doc, reason):
    assert risk(doc=doc)["reason"] == reason


def test_an_unreachable_or_unset_source_refuses():
    def boom(u):
        raise ConnectionError("down")
    assert RUN.account_risk(url="http://x", now=NOW, own_unaccounted=D(0),
                            fetch=boom)["reason"] == \
        "ACCOUNT_RISK_UNREADABLE:ConnectionError"
    assert RUN.account_risk(url="", now=NOW, own_unaccounted=D(0))[
        "reason"] == "ACCOUNT_RISK_URL_NOT_SET"


def test_the_shared_halt_must_be_configured_and_permitting(monkeypatch):
    assert RUN.shared_halt("")["reason"] == "HALT_URL_NOT_SET"
    from src import l5_halt_gate as HG

    class Gate:
        def __init__(self, store):
            pass

        def state(self):
            return {"permitted": False, "why": "halt_asserted"}
    monkeypatch.setattr(HG, "HaltGate", Gate)
    assert RUN.shared_halt("http://h")["reason"] == "HALT:halt_asserted"
    monkeypatch.setattr(Gate, "state", lambda self: {"permitted": True})
    assert RUN.shared_halt("http://h") == {"ok": True, "reason": None}


def test_the_15m_service_publishes_its_account_risk(tmp_path):
    from src import l5_order_registry_server as REG
    store = E.InventoryStore(str(tmp_path / "inv.json"))
    doc = REG.registry(store, risk=lambda: {"readable": True, "x": 1})
    assert doc["account_risk"] == {"readable": True, "x": 1}
    assert "account_risk" not in REG.registry(store)

    def boom():
        raise RuntimeError("no")
    bad = REG.registry(store, risk=boom)["account_risk"]
    assert bad == {"readable": False, "reason": "RISK_READ_FAILED:RuntimeError"}


# ======================================================================
# 4. THE SHARED FILL-COST CORRECTION IN THE ACCOUNT-WIDE LEDGER
# ======================================================================
M5 = M


def adopted(tmp_path, up="0.17", dn="0.17"):
    s = E.InventoryStore(str(tmp_path / "inv.json"))
    rows = {}
    if D(up):
        rows["%s|UP" % M5] = up
    if D(dn):
        rows["%s|DOWN" % M5] = dn
    s.adopt_venue_position(M5, rows, why="test")
    return s


def fills(**over):
    out = [{"venue_order_id": "v1", "side": "UP", "filled_qty": "0.17",
            "price": "0.58"},
           {"venue_order_id": "v2", "side": "DOWN", "filled_qty": "0.17",
            "price": "0.38"}]
    for f in out:
        f.update(over.get(f["venue_order_id"], {}))
    return out


def venue(**over):
    rows = {"v1": {"marketSlug": M5, "cumQuantity": "0.17",
                   "avgPx": {"value": "0.58"}},
            "v2": {"marketSlug": M5, "cumQuantity": "0.17",
                   "avgPx": {"value": "0.62"}}}        # DOWN 0.38 in own terms
    for k, v in over.items():
        if v is None:
            rows.pop(k)
        else:
            rows[k].update(v)
    return rows


def test_a_matched_5m_pair_held_at_the_bound_is_restated_from_evidence(
        tmp_path):
    s = adopted(tmp_path)
    assert s.leg_cost(M5)["UP"] == D("0.17")           # the $1 bound
    got = s.attest_other_strategy_cost(M5, fills(), venue(), source="TEST")
    assert got["attested"] is True and got["origin"] == "BTC5M"
    assert s.leg_cost(M5) == {"UP": D("0.0986"), "DOWN": D("0.0646")}
    assert not any(str(k).startswith(M5) for k in
                   s.state.get("leg_cost_basis_unknown") or [])
    assert M5 not in (s.state.get("risk_floor_usd") or {})
    out = s.retire_settled_market(M5, yes_value=D("1"))
    # 0.17 redeems $0.17 against $0.1632: +0.0068, not -0.1700 at the bound
    assert D(out["settlement_pnl"]) == D("0.17") - D("0.1632")
    row = s.state["retired_markets"][M5]
    assert row["origin"] == "BTC5M"
    assert row["origin_cost_basis"] == OG.BASIS_AUTHENTICATED


@pytest.mark.parametrize("f_over,v_over,reason", [
    ({}, {"v2": None}, "VENUE_ORDER_NOT_READ"),
    ({}, {"v1": {"marketSlug": M15}}, "VENUE_ORDER_IN_ANOTHER_MARKET"),
    ({}, {"v1": {"cumQuantity": "0.16"}}, "VENUE_QUANTITY_DISAGREES"),
    ({}, {"v1": {"avgPx": {"value": "0.59"}}}, "VENUE_PRICE_DISAGREES"),
    ({"v1": {"filled_qty": "0.10"}}, {"v1": {"cumQuantity": "0.10"}},
     "FILLS_DO_NOT_ACCOUNT_FOR_THE_POSITION"),
    ({"v1": {"side": "SIDEWAYS"}}, {}, "FILL_SIDE_UNREADABLE"),
    ({"v1": {"price": "1.20"}}, {}, "FILL_NUMBERS_UNREADABLE"),
    ({"v2": {"venue_order_id": "v1"}}, {}, "FILL_ID_MISSING_OR_REPEATED"),
])
def test_any_miss_leaves_the_bound(tmp_path, f_over, v_over, reason):
    s = adopted(tmp_path)
    before = (dict(s.state["leg_cost"]), list(s.state["leg_cost_basis_unknown"]))
    got = s.attest_other_strategy_cost(M5, fills(**f_over), venue(**v_over),
                                       source="TEST")
    assert got["attested"] is False and got["reason"] == reason, got
    assert (s.state["leg_cost"], s.state["leg_cost_basis_unknown"]) == before
    out = s.retire_settled_market(M5, yes_value=D("1"))
    assert D(out["settlement_pnl"]) == D("0.17") - D("0.34")   # at the bound


def test_only_another_strategys_market_held_at_the_bound(tmp_path):
    s = E.InventoryStore(str(tmp_path / "inv.json"))
    assert s.attest_other_strategy_cost(M15, [], {}, source="T")["reason"] \
        == "NOT_ANOTHER_STRATEGYS_MARKET"
    assert s.attest_other_strategy_cost(M5, [], {}, source="T")["reason"] \
        == "NOT_HELD_AT_THE_BOUND"
    s2 = adopted(tmp_path)
    assert s2.attest_other_strategy_cost(M5, fills(), venue(), source=" ")[
        "reason"] == "NO_SOURCE"


def test_the_15m_settlement_sweep_restates_only_with_the_feed_configured(
        tmp_path, monkeypatch):
    from src import l5_pmus_live_runner as R
    s = adopted(tmp_path)
    emitted = []
    monkeypatch.setattr(R, "emit", lambda k, d: emitted.append((k, d)))

    class Shell:
        store = s
    monkeypatch.delenv(R.BTC5M_FILLS_URL_ENV, raising=False)
    assert R.Runner.attest_other_strategy_cost(Shell(), M5, None) is None
    monkeypatch.setenv(R.BTC5M_FILLS_URL_ENV, "http://btc5m/btc5m_fills")
    doc = {"readable": True,
           "orders": [dict(f, market=M5) for f in fills()]}

    class Resp:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    import urllib.request as UR
    monkeypatch.setattr(UR, "urlopen", lambda url, timeout=0: Resp(
        json.dumps(doc).encode()))
    v = venue()

    class Venue:
        def order(self, vid):
            return v[vid]
    got = R.Runner.attest_other_strategy_cost(Shell(), M5, Venue())
    assert got["attested"] is True
    assert emitted[-1][0] == "L5_OTHER_STRATEGY_COST"


def test_the_fills_feed_publishes_only_ledger_rows(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", "0.05")})
    rig.session.run()
    doc = RUN.fills_document(rig.ex)
    assert doc["readable"] is True and doc["strategy"] == "BTC5M"
    assert {o["venue_order_id"] for o in doc["orders"]} == {
        r["venue_order_id"] for r in rig.ex.orders().values()}
    assert "nonce" not in json.dumps(doc) and "secret" not in json.dumps(doc)


# ======================================================================
# 5. THE TRANSPORT'S SERIES OPTION LEAVES BTC15m UNCHANGED
# ======================================================================
def test_the_transport_defaults_to_btc15m_and_accepts_btc5m_by_name():
    from src import l5_pmus_transport as T
    t15 = T.PmusTransport(market_slug=M15, http=object())
    t5 = T.PmusTransport(market_slug=M, http=object(), series="BTC5M")
    assert t15.series == "BTC15M" and t5.series == "BTC5M"
    assert T.SERIES["BTC15M"][1].match(M15) and not T.SERIES["BTC15M"][1].match(M)
    assert T.SERIES["BTC5M"][1].match(M) and not T.SERIES["BTC5M"][1].match(M15)
    assert T.SERIES["BTC15M"][2:] == ("slug_is_btc15m_series",
                                      "market_series_is_btc15m")
    # an UNREGISTERED series name is refused. "ETH5M" used to serve as the
    # example here and became real on 2026-11-15, so this names an asset
    # the registry does not carry.
    assert "ETH5M" in T.SERIES and "DOGE5M" not in T.SERIES
    with pytest.raises(ValueError):
        T.PmusTransport(market_slug=M, http=object(), series="DOGE5M")


def test_a_5m_transport_refuses_a_15m_slug_at_preflight(monkeypatch):
    from src import l5_pmus_transport as T
    monkeypatch.setattr(T.Q1, "has_credentials", lambda: False)
    with pytest.raises(T.PreflightFailed) as e:
        T.PmusTransport(market_slug=M15, http=object(),
                        series="BTC5M").preflight()
    pf = json.loads(str(e.value))
    gates = {c["check"]: c["passed"] for c in pf["checks"]}
    assert gates["slug_is_btc5m_series"] is False
    assert pf["preflight"] == "FAILED"


def test_the_reference_prices_a_5_minute_strike_only_when_asked(monkeypatch):
    from src import l5_btc_reference as REF
    rec = {"market": {"slug": M, "assetPriceTerms": {
        "indexSymbol": "BRTI", "marketType": "ASSET_PRICE_MARKET_TYPE_UP_DOWN",
        "asset": {"symbol": "btc"},
        "windowStart": WS.isoformat(),
        "windowEnd": (WS + dt.timedelta(minutes=5)).isoformat(),
        "priceToBeat": {"value": "73000.00", "currency": "USD"}}}}
    assert REF.parse_strike(rec, slug=M, window_start=WS.timestamp())[
        "reason"] == "WINDOW_IS_NOT_FIFTEEN_MINUTES"
    got = REF.parse_strike(rec, slug=M, window_start=WS.timestamp(),
                           window_s=300)
    assert got["ok"] is True and got["strike"] == 73000.0
    assert REF.Reference(window_s=300).window_s == 300.0
    assert REF.Reference().window_s == float(REF.WINDOW_S) == 900.0


# ======================================================================
# 6. THE IMAGE
# ======================================================================
def test_the_image_ships_the_whole_closure_and_nothing_from_the_15m_runner():
    import pathlib
    import re
    import subprocess
    import sys
    root = pathlib.Path(__file__).resolve().parents[1]
    df = (root / "deploy" / "l5_btc5m_live" / "Dockerfile").read_text()
    shipped = set(re.findall(r"^COPY (src/[\w/]+\.py) ", df, re.M))
    code = (
        "import sys, importlib\n"
        "sys.path.insert(0, %r)\n"
        "for m in ('src.l5_btc5m_runner','src.l5_btc_reference',"
        "'src.l5_pmus_transport','src.l5_halt_gate','src.l5_order_body',"
        "'src.l5_preview_gate','src.l5_campaign_approval',"
        "'src.l5_pmus_auth_book','src.pmus_auth'):\n"
        "    importlib.import_module(m)\n"
        "print('\\n'.join(sorted(n for n in sys.modules "
        "if n == 'src' or n.startswith('src.'))))\n") % str(root)
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(root),
                         capture_output=True, text=True,
                         check=True).stdout.split()
    need = {("src/__init__.py" if m == "src" else m.replace(".", "/") + ".py")
            for m in out}
    assert need <= shipped, sorted(need - shipped)
    # THE ISOLATION BOUNDARY: the other runner, the watchdog, and the
    # cancel mechanism. None of them belongs in this image -- the first
    # is a different executor, and the last two hold the cancel power
    # over a shared account, which stays with the watchdog's own
    # credential-free service.
    for absent in ("src/l5_pmus_live_runner.py", "src/l5_watchdog.py",
                   "src/l5_cancel_mechanism.py"):
        assert absent not in shipped
    # `l5_order_registry_server` USED to be denied alongside them, as
    # "the 15-minute runner's registry server". That grouping was too
    # broad: the module imports nothing local, holds no credential and
    # knows no venue schema -- it is a pure document builder over
    # `store.live_orders()`. This lane now publishes the same
    # `l5/live_orders.json` through it, because the watchdog cancels
    # nothing it cannot claim and a second shape maintained in parallel
    # would drift from the one the other executor serves.
    assert "src/l5_order_registry_server.py" in shipped
    import src.l5_order_registry_server as _REG
    assert not [m for m in vars(_REG).values()
                if getattr(m, "__name__", "").startswith("src.")], \
        "the registry builder must stay free of local imports"
    manifest = json.loads((root / "deploy" / "l5_btc5m_live"
                           / "railway.json").read_text())
    assert manifest["deploy"]["restartPolicyType"] == "NEVER"
    assert manifest["deploy"]["numReplicas"] == 1
    assert 'CMD ["python3", "-u", "-m", "src.l5_btc5m_runner"]' in df


# ======================================================================
# 7. TIMING AND THE ACCOUNT'S REMAINING LOSS ROOM
# ======================================================================
def test_a_window_already_past_is_refused_not_chased(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.clock.t = WS.timestamp() + RUN.WINDOW_S - RUN.CANCEL_LEAD_S
    rep = rig.session.run()
    assert rep["started"] is False and rep["reason"] == "WINDOW_ALREADY_PAST"
    assert rig.venue.sent == [] and not os.path.exists(rig.session.marker())


def test_preflight_waits_for_the_window_to_open(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    seen = []
    base = rig.venue.preflight
    rig.venue.preflight = lambda: (seen.append(rig.clock()), base())[1]
    rig.session.run()
    assert seen and seen[0] >= WS.timestamp()


@pytest.mark.parametrize("room,ok", [("1.00", True), ("0.99", False),
                                     ("0", False), (None, False)])
def test_the_approved_exposure_must_fit_inside_the_loss_room(
        tmp_path, monkeypatch, room, ok):
    """Approved $1.00: it may trade only while the account can still lose
    $1.00 before either account-wide limit binds."""
    def risk_fn(now, own):
        return {"ok": True, "loss_room_usd": room}
    rig = Rig(tmp_path, monkeypatch, risk=risk_fn)
    T5.authorize_5m(monkeypatch, tmp_path,
                    now_fn=lambda: dt.datetime.fromtimestamp(rig.clock(), UTC),
                    starts=(WS,), max_total_exposure_usd="1.00")
    rep = rig.session.run()
    if ok:
        assert rep["submitted"] >= 1
        assert not rig.kinds("L5_BTC5M_BLOCKED")
    else:
        assert rig.venue.sent == [] and rep["submitted"] == 0
        reasons = {b["reason"] for b in rig.kinds("L5_BTC5M_BLOCKED")}
        assert reasons == {"EXPOSURE_CEILING_EXCEEDS_LOSS_ROOM"
                           if room is not None else "LOSS_ROOM_UNREADABLE"}


def test_todays_account_state_would_refuse_on_the_lifetime_ceiling():
    """The account as read from production on 2026-11-12 00:10Z: today's
    realized 0 against $5.00, all-time -$21.0406 against $18.6325. The
    session refuses, as it must, until the OWNER changes that state."""
    got = risk(utc_day_realized_usd="0", daily_halt_usd="5.00",
               lifetime_realized_usd="-21.0406",
               cumulative_loss_ceiling_usd="18.6325")
    assert got["ok"] is False
    assert got["reason"] == "ACCOUNT_CUMULATIVE_LOSS_CEILING"


def test_the_btc15m_services_own_kill_switch_does_not_stop_btc5m():
    """Production today: BTC15m is disarmed with L5_LIVE_HALT=1 on its own
    service. That is its kill switch, not an account limit; it is reported
    and BTC5m is judged on the loss state alone."""
    got = risk(operator_halt=True)
    assert got["ok"] is True and got["btc15m_operator_halt"] is True


def test_btc5ms_own_operator_halt_and_the_watchdogs_halt_still_stop_it(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, halt=lambda: {"ok": False,
                                                   "reason": "HALT:asserted"})
    rep = rig.session.run()
    assert rig.venue.sent == [] and rep["submitted"] == 0
    assert {b["reason"] for b in rig.kinds("L5_BTC5M_BLOCKED")} == {
        "SHARED_HALT:HALT:asserted"}


def test_the_runner_arms_only_inside_the_shared_boundary():
    """One `.arm()` in the module, inside `arm_through_boundary`, AFTER its
    call to `authorize_arming`."""
    import ast
    import inspect
    import pathlib
    src = pathlib.Path(RUN.__file__).read_text()
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "arm"]
    assert len(calls) == 1
    body = inspect.getsource(RUN.arm_through_boundary)
    assert body.index("AUTH.authorize_arming(") < body.index(".arm()")


# ======================================================================
# 8. THE PRODUCTION ARMING PATH: authorize_arming, with every condition
# ======================================================================
OWNER_TOKEN = {"PMUS_Q1_LIVE": "YES", "PMUS_Q1_SESSION_ID": "b5m-test-sid",
               "PMUS_Q1_ARM": "b5m-test-sid"}


def owner_armed(monkeypatch):
    for k, v in OWNER_TOKEN.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv(RUN.HALT_URL_ENV, "http://watchdog.internal:8080")
    for k in ("L5_LIVE_PROBE_MARKET", "L5_LIVE_AUTH_NOT_AFTER",
              "L5_LIVE_AUTH_MARKET_SERIES", "L5_WINDOW_STRATEGY",
              "L5_LIVE_AUTH_MAX_WINDOWS", "L5_CAMPAIGN_BUDGET_USD"):
        monkeypatch.delenv(k, raising=False)


def test_with_every_condition_the_boundary_arms_and_the_session_trades(
        tmp_path, monkeypatch):
    owner_armed(monkeypatch)
    rig = Rig(tmp_path, monkeypatch, arm="boundary",
              fill_at={12.0: ("v1", "0.05")})
    rep = rig.session.run()
    start = rig.kinds("L5_BTC5M_START")[0]
    assert start["arm_error"] is None, start
    assert start["transport_armed"] is True
    assert rep["submitted"] >= 1 and rep["close"]["clean"] is True
    assert rep["settlement"]["settled"] is True


@pytest.mark.parametrize("change,reason", [
    (lambda mp: [mp.delenv(k) for k in OWNER_TOKEN], "TRANSPORT_CANNOT_ARM"),
    (lambda mp: mp.setenv("PMUS_Q1_ARM", "a-different-token"),
     "TRANSPORT_CANNOT_ARM"),
    (lambda mp: mp.delenv(RUN.HALT_URL_ENV), "HALT_GATE_ABSENT"),
    (lambda mp: mp.setenv(AUTH.LIVE_HALT_ENV, "1"), "HALTED"),
    (lambda mp: mp.setenv(AUTH.SHADOW_ONLY_ENV, "1"), "SHADOW_ONLY_LOCK"),
    (lambda mp: mp.setenv(AUTH.SUBMIT_PAUSE_ENV, "1"),
     "SUBMISSION_PAUSED_FOR_DEPLOY"),
    (lambda mp: mp.setenv("L5_LIVE_PROBE_MARKET", M15),
     "AUTHORIZATION_BOUND_TO_MARKET"),
])
def test_each_missing_condition_keeps_the_transport_disarmed(
        tmp_path, monkeypatch, change, reason):
    owner_armed(monkeypatch)
    rig = Rig(tmp_path, monkeypatch, arm="boundary")
    change(monkeypatch)
    rep = rig.session.run()
    start = rig.kinds("L5_BTC5M_START")[0]
    assert start["transport_armed"] is False
    assert reason in (start["arm_error"] or ""), start["arm_error"]
    assert rig.venue.sent == [] and rep["submitted"] == 0


def test_unconfirmed_window_bounds_keep_it_disarmed(tmp_path, monkeypatch):
    owner_armed(monkeypatch)
    rig = Rig(tmp_path, monkeypatch, arm="boundary")
    rig.session.bounds_fn = lambda m: None
    rig.session.run()
    start = rig.kinds("L5_BTC5M_START")[0]
    assert start["transport_armed"] is False
    assert "WINDOW_BOUNDS_UNKNOWN" in start["arm_error"]
    assert rig.venue.sent == []


def test_an_unreconciled_book_keeps_it_disarmed(tmp_path, monkeypatch):
    owner_armed(monkeypatch)
    rig = Rig(tmp_path, monkeypatch, arm="boundary")
    rig.venue.extra_positions = [{"marketSlug": M, "netPositionDecimal": "0.5",
                                  "qtyBought": "0.5", "qtySold": "0"}]
    rig.session.run()
    start = rig.kinds("L5_BTC5M_START")[0]
    assert start["transport_armed"] is False
    assert "NOT_RECONCILED" in start["arm_error"]


def test_venue_window_bounds_confirm_the_slug_or_say_nothing():
    rec = {"market": {"slug": M, "assetPriceTerms": {
        "windowStart": "2026-11-11T12:00:00Z",
        "windowEnd": "2026-11-11T12:05:00Z"}}}
    got = RUN.venue_window_bounds(M, fetch=lambda u: rec)
    assert got == {"start": WS.isoformat(),
                   "end": (WS + dt.timedelta(minutes=5)).isoformat(),
                   "source": "VENUE"}
    for bad in ({"market": {"slug": M15}},
                {"market": {"slug": M, "assetPriceTerms": {
                    "windowStart": "2026-11-11T12:05:00Z",
                    "windowEnd": "2026-11-11T12:10:00Z"}}},
                {"market": {"slug": M, "assetPriceTerms": {
                    "windowStart": "2026-11-11T12:00:00Z",
                    "windowEnd": "2026-11-11T12:15:00Z"}}}):
        assert RUN.venue_window_bounds(M, fetch=lambda u, b=bad: b) is None

    def boom(u):
        raise ConnectionError("no")
    assert RUN.venue_window_bounds(M, fetch=boom) is None
