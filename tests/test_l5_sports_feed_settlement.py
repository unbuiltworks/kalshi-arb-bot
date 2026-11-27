"""A bot-owned sports market settles from the sports fill feed, with the
venue's own order reads and net position as evidence, BEFORE it can become an
unresolved $1/share bound row (live 2026-11-27 01:47Z,
aec-atp-timleg-karpol-2026-11-25: the bot bought YES 4 avg 0.485 and NO 4 avg
0.47; this ledger held one stale adopted snapshot, UP 3 at the bound; the
venue's net was 0; it settled YES=1 and was booked at the bound, which made
the published account-risk record unreadable and paused the sports bot).

Every miss falls back EXACTLY to the bound, and the venue pass later either
restates it (sibling form) or, for a feed-settled row, confirms it trade by
trade without re-applying it.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_venue_cost_attestation as VCA

from tests.test_l5_venue_cost_attestation import Feed, fill, run_until_idle
from tests.test_l5_unresolved_bound import _Runner

M = "aec-atp-timleg-karpol-2026-11-25"
M2 = "aec-nba-lac-dal-2026-11-26"
URL = "http://sports.invalid/l5/sports_fills.json"
CANON = {"YES": "UP", "NO": "DOWN"}
#: the live market: (venue order id, feed side, qty, own-outcome price)
TIMLEG = [("VA1", "YES", "2", "0.48"), ("VA2", "YES", "2", "0.49"),
          ("VB1", "NO", "2", "0.47"), ("VB2", "NO", "2", "0.47")]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(VCA, "RETRY_S", 0.0)
    monkeypatch.setattr(VCA, "SETTLE_AGE_S", 0.0)
    for k in (VCA.SPORTS_SETTLE_ENV, VCA.SIBLING_ENV,
              "L5_SPORTS_FILLS_URL", "L5_SPORTS_ATTEST_FLOOR_UTC_DAY",
              LX.InventoryStore.ACCEPT_BOUND_ENV):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(VCA.SPORTS_SETTLE_ENV, "1")
    monkeypatch.setenv("L5_SPORTS_FILLS_URL", URL)
    monkeypatch.setenv("L5_SPORTS_ATTEST_FLOOR_UTC_DAY", "2026-11-22")


def now():
    return dt.datetime.now(dt.timezone.utc)


def feed_doc(rows=TIMLEG, market=M, *, age_s=1.0, state="CLOSED",
             strategy="SPORTS", readable=True):
    return {"readable": readable, "strategy": strategy,
            "lane": "pmus-sports-two-sided",
            "as_of_utc": (now() - dt.timedelta(seconds=age_s)).isoformat(),
            "orders": [{"venue_order_id": vid, "market": market,
                        "side": side, "price": px, "limit_price": px,
                        "filled_qty": q, "filled_cost": str(D(q) * D(px)),
                        "cost_from": "VENUE_REPORTED", "state": state}
                       for vid, side, q, px in rows]}


def venue_orders(rows=TIMLEG, market=M, *, fee="0", cum=None, leaves="0"):
    out = {}
    for vid, side, q, px in rows:
        wire = D(px) if side == "YES" else D(1) - D(px)
        out[vid] = {"id": vid, "marketSlug": market,
                    "state": "ORDER_STATE_FILLED", "quantity": q,
                    "cumQuantity": (cum or {}).get(vid, q),
                    "leavesQuantity": leaves, "avgPx": {"value": str(wire)},
                    "commissionNotionalTotalCollected": {
                        "value": (fee.get(vid, "0") if isinstance(fee, dict)
                                  else fee)}}
    return out


def walk_fills(rows=TIMLEG, market=M, extra=()):
    out = [fill(market, CANON[side], q, px, "T-" + vid, vid,
                t="2026-11-27T01:2%d:00Z" % i)
           for i, (vid, side, q, px) in enumerate(rows)]
    return out + list(extra)


def held(tmp_path, *, up="3", dn="0", market=M, store=None):
    """The ledger's adopted snapshot, at the $1 bound."""
    s = store or LX.InventoryStore(str(tmp_path / "inv.json"))
    for k in ("realized_unattributed", "realized_attributed",
              "realized_today"):
        s.state.setdefault(k, "0")
    rows = {}
    if D(up):
        rows["%s|UP" % market] = up
    if D(dn):
        rows["%s|DOWN" % market] = dn
    s.adopt_venue_position(market, rows, why="test")
    s.save()
    return s


def res(market=M, yes="1"):
    return {"source": "VENUE_SETTLEMENT_PX", "market": market,
            "yes_value": yes}


def settle(s, *, market=M, yes="1", doc=None, orders=None, net="0"):
    return s.settle_from_sibling_feed(
        market, yes_value=D(yes), resolution=res(market, yes),
        feed=feed_doc() if doc is None else doc,
        venue_orders=venue_orders() if orders is None else orders,
        venue_net=net)


def bound(s, market=M, yes="1"):
    out = s.retire_settled_market(market, yes_value=D(yes),
                                  resolution=res(market, yes))
    assert out["retired"], out
    return out


def row(s, market=M):
    return s.state["retired_markets"][market]


def publication(s, monkeypatch):
    monkeypatch.setattr(R, "venue_basis_lines", lambda r: None)
    return _Runner(s).account_risk_line()


def snap(s, tmp_path):
    return copy.deepcopy(s.state), (tmp_path / "inv.json").read_bytes()


# ------------------------------------------------------------ the live case

def test_timleg_settles_from_the_feed_and_the_account_stays_readable(
        tmp_path, monkeypatch):
    s = held(tmp_path)
    out = settle(s)
    assert out["retired"] is True and out["fallback"] is False
    r = row(s)
    # four pairs at 0.485 + 0.47: redeem 4.00 against 3.82
    assert D(r["settlement_pnl"]) == D("0.18")
    assert r["pnl_provenance"] == VCA.FEED_SETTLEMENT
    assert r["origin"] == "SPORTS" and r["origin_cost_basis"] == \
        LX.ORIGIN.BASIS_AUTHENTICATED
    assert (D(r["up_qty"]), D(r["dn_qty"])) == (4, 4)
    ev = r["sibling_feed_settlement"]
    assert ev["venue_order_ids"] == ["VA1", "VA2", "VB1", "VB2"]
    assert ev["ledger_held_before"] == {"%s|UP" % M: "3"}
    assert ev["venue_confirmation"] == "PENDING_ACTIVITY_WALK"
    assert s.realized_today() == D("0.18")
    assert not [k for k in s.state["leg_cost_basis_unknown"]
                if k.startswith(M)]
    assert M not in (s.state.get("risk_floor_usd") or {})
    # capital release: the published record is readable at once
    assert s.unresolved_bound()["count"] == 0
    line = publication(s, monkeypatch)
    assert line["readable"] is True and line["reason"] is None
    # durable: a fresh process reads the same booked row
    again = LX.InventoryStore(str(tmp_path / "inv.json"))
    assert again.state["retired_markets"][M]["pnl_provenance"] == \
        VCA.FEED_SETTLEMENT
    assert again.unresolved_bound()["count"] == 0


def test_without_the_feed_the_same_market_fails_closed_as_today(
        tmp_path, monkeypatch):
    s = held(tmp_path)
    bound(s)
    assert row(s)["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"
    assert D(row(s)["settlement_pnl"]) == 0
    line = publication(s, monkeypatch)
    assert line["readable"] is False
    assert line["reason"] == "UNRESOLVED_COST_BASIS:1"


# ----------------------------------------------------------- exact shapes

@pytest.mark.parametrize("yes,expect", [("1", "1.12"), ("0", "-0.88")])
def test_asymmetric_residual_on_the_winning_and_losing_side(
        tmp_path, yes, expect):
    rows = [("VA1", "YES", "5", "0.50"), ("VB1", "NO", "3", "0.46")]
    s = held(tmp_path, up="2")
    out = settle(s, yes=yes, doc=feed_doc(rows), orders=venue_orders(rows),
                 net="2")
    assert out["retired"], out
    # yes=1: 5 - (2.50 + 1.38); yes=0: 3 - 3.88
    assert D(row(s)["settlement_pnl"]) == D(expect)
    assert D(out["matched_qty"]) == 3 and out["residual_side"] == "UP"


def test_partial_fill_of_a_cancelled_order(tmp_path):
    rows = [("VA1", "YES", "1.5", "0.40"), ("VB1", "NO", "1.5", "0.55")]
    orders = venue_orders(rows)
    orders["VA1"].update(state="ORDER_STATE_CANCELED", quantity="3")
    s = held(tmp_path, up="1.5")
    out = settle(s, doc=feed_doc(rows), orders=orders, net="0")
    assert out["retired"], out
    assert D(row(s)["settlement_pnl"]) == D("1.5") - D("0.60") - D("0.825")


def test_fees_are_in_the_result_and_a_credit_is_not(tmp_path):
    s = held(tmp_path)
    out = settle(s, orders=venue_orders(fee={"VA1": "0.02",
                                             "VB1": "-0.01"}))
    assert out["retired"], out
    r = row(s)
    assert D(r["settlement_pnl"]) == D("0.16")
    assert D(r["fees_usd"]) == D("0.02")
    ev = r["sibling_feed_settlement"]
    assert D(ev["venue_credit_usd_not_in_pnl"]) == D("0.01")


def test_two_markets_settled_in_one_sweep_each_on_its_own_evidence(
        tmp_path, monkeypatch, on):
    rows2 = [("VC1", "YES", "3", "0.30"), ("VD1", "NO", "3", "0.66")]
    s = held(tmp_path)
    held(tmp_path, dn="1", market=M2, store=s)
    docs = feed_doc()
    docs["orders"] += feed_doc(rows2, M2)["orders"]
    orders = dict(venue_orders(), **venue_orders(rows2, M2))
    sh, emitted = runner_shell(s, monkeypatch, docs, orders,
                               positions=[], settled=[M, M2])
    R.Runner.sweep_settlements(sh)
    assert D(row(s, M)["settlement_pnl"]) == D("0.18")
    assert D(row(s, M2)["settlement_pnl"]) == D("3") - D("0.90") - D("1.98")
    assert {r["pnl_provenance"] for r in
            s.state["retired_markets"].values()} == {VCA.FEED_SETTLEMENT}
    assert s.unresolved_bound()["count"] == 0
    kinds = [k for k, _d in emitted]
    assert kinds.count("L5_SPORTS_FEED_SETTLEMENT") == 2
    assert kinds.count("L5_LIVE_SETTLED") == 2


# ------------------------------------------------------- every fallback

def _falls_back(s, tmp_path, out, reason):
    assert out["retired"] is False and out["fallback"] is True, out
    assert str(out["reason"]).startswith(reason), out
    assert M not in (s.state.get("retired_markets") or {})


@pytest.mark.parametrize("doc,reason", [
    (lambda: feed_doc(age_s=120), "FEED_STALE"),
    (lambda: feed_doc(age_s=-60), "FEED_STALE"),
    (lambda: feed_doc(readable=False), "FEED_NOT_READABLE"),
    (lambda: feed_doc(strategy="BTC5M"), "FEED_IS_ANOTHER_STRATEGYS"),
    (lambda: feed_doc(state="OPEN"), "FEED_ORDER_STILL_OPEN"),
    (lambda: feed_doc(TIMLEG[:3]), "VENUE_NET_NE_FEED_NET"),
    (lambda: feed_doc([]), "FEED_HAS_NO_FILLS_IN_MARKET"),
])
def test_feed_problems_write_nothing(tmp_path, doc, reason):
    s = held(tmp_path)
    before = snap(s, tmp_path)
    out = settle(s, doc=doc(), orders=venue_orders())
    _falls_back(s, tmp_path, out, reason)
    assert snap(s, tmp_path) == before


def test_a_delayed_fill_not_yet_in_the_feed_falls_back_then_restates_once(
        tmp_path, monkeypatch, on):
    # the venue already has the last NO fill of VB2; the feed shows 1 of 2
    late = [r if r[0] != "VB2" else ("VB2", "NO", "1", "0.47")
            for r in TIMLEG]
    s = held(tmp_path)
    out = settle(s, doc=feed_doc(late),
                 orders=venue_orders(cum={"VB2": "2"}), net="0")
    _falls_back(s, tmp_path, out, "VENUE_QTY_NE_FEED_QTY")
    bound(s)                                    # exactly today's settlement
    assert s.unresolved_bound()["count"] == 1
    # later: the feed has caught up and the venue pass restates the row
    monkeypatch.setenv(VCA.SIBLING_ENV, VCA.SIBLING_TOKEN)
    monkeypatch.setattr(VCA, "sibling_evidence",
                        lambda m, _r=VCA.sibling_evidence: _r(
                            m, fetch=lambda url: feed_doc()))
    run_until_idle(s, Feed([walk_fills()]))
    assert D(row(s)["settlement_pnl"]) == D("0.18")
    assert row(s)["venue_qty_form"] == s.SIBLING_LEDGER_FORM
    assert s.unresolved_bound()["count"] == 0
    run_until_idle(s, Feed([walk_fills()]))
    assert len(s.state["cost_basis_corrections"]) == 1
    assert s.realized_today() == D("0.18")


def test_a_manual_trade_in_the_market_falls_back(tmp_path):
    # the owner bought one YES by hand: the venue's net is +1, the feed's 0
    s = held(tmp_path)
    out = settle(s, net="1")
    _falls_back(s, tmp_path, out, "VENUE_NET_NE_FEED_NET")


@pytest.mark.parametrize("mut,reason", [
    (lambda o: o.pop("VB2"), "VENUE_ORDER_NOT_READ"),
    (lambda o: o["VB2"].update(marketSlug=M2), "VENUE_ORDER_NOT_IN_MARKET"),
    (lambda o: o["VB2"].update(leavesQuantity="1",
                               state="ORDER_STATE_PARTIALLY_FILLED"),
     "VENUE_ORDER_NOT_TERMINAL"),
    (lambda o: o["VB2"].update(cumQuantity="1"), "VENUE_QTY_NE_FEED_QTY"),
    (lambda o: o["VB2"].update(avgPx={"value": "0.60"}),
     "VENUE_PRICE_NE_FEED_PRICE"),
    (lambda o: o["VB2"].pop("avgPx"), "VENUE_PRICE_UNSTATED"),
    (lambda o: o["VB2"].pop("commissionNotionalTotalCollected"),
     "VENUE_FEE_UNREADABLE"),
])
def test_venue_order_problems_write_nothing(tmp_path, mut, reason):
    s = held(tmp_path)
    before = snap(s, tmp_path)
    orders = venue_orders()
    mut(orders)
    out = settle(s, orders=orders)
    _falls_back(s, tmp_path, out, reason)
    assert snap(s, tmp_path) == before


def test_a_foreign_market_or_a_btc_market_is_never_settled_here(tmp_path):
    s = held(tmp_path, market="cpc-btc-updown-5m-2026-11-27-0100z")
    out = s.settle_from_sibling_feed(
        "cpc-btc-updown-5m-2026-11-27-0100z", yes_value=D(1),
        resolution=res("cpc-btc-updown-5m-2026-11-27-0100z"),
        feed=feed_doc(), venue_orders=venue_orders(), venue_net="0")
    assert out["reason"] == "NOT_A_SPORTS_SERIES_MARKET"


def test_a_resolution_for_another_market_refuses(tmp_path):
    s = held(tmp_path)
    out = s.settle_from_sibling_feed(
        M, yes_value=D(1), resolution=res(M2), feed=feed_doc(),
        venue_orders=venue_orders(), venue_net="0")
    _falls_back(s, tmp_path, out, "RESOLUTION:RESOLUTION_MARKET_MISMATCH")


def test_a_side_whose_basis_the_ledger_knew_refuses(tmp_path):
    s = held(tmp_path)
    s.state["positions"]["%s|DOWN" % M] = "1"
    s.state["leg_cost"]["%s|DOWN" % M] = "0.47"
    out = settle(s)
    _falls_back(s, tmp_path, out, "KNOWN_SIDE_HELD")


def test_a_refused_retirement_restores_the_book_exactly(tmp_path,
                                                         monkeypatch):
    s = held(tmp_path)
    before = copy.deepcopy(s.state)
    monkeypatch.setattr(s, "retire_settled_market",
                        lambda *a, **k: {"retired": False, "reason": "X"})
    out = settle(s)
    assert out["fallback"] is True and out["reason"] == "RETIRE_REFUSED:X"
    assert s.state == before


# ------------------------------------- the venue pass: confirm, never twice

def test_the_venue_pass_confirms_once_and_never_reapplies(tmp_path):
    s = held(tmp_path)
    settle(s)
    pnl = row(s)["settlement_pnl"]
    # a restart between settlement and the walk
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    run_until_idle(s, Feed([walk_fills()]))
    r = row(s)
    assert r["settlement_pnl"] == pnl and s.realized_today() == D("0.18")
    assert r["sibling_feed_settlement"]["venue_confirmation"] == "CONFIRMED"
    assert sorted(r["sibling_feed_settlement"]["trade_ids"]) == [
        "T-VA1", "T-VA2", "T-VB1", "T-VB2"]
    recs = s.state["cost_basis_corrections"]
    assert [x["kind"] for x in recs] == [
        "SIBLING_FEED_SETTLEMENT_VENUE_CONFIRMED"]
    assert D(recs[0]["delta"]) == 0
    # the bound-correcting verb refuses it outright
    assert s.attest_retired_market_cost(
        M, legs={}, walk={"sufficient": True},
        correction_id="venue-attest|%s" % M)["reason"] == \
        "NOT_AT_THE_UPPER_BOUND"
    # and a second pass reads nothing new and writes nothing
    before = copy.deepcopy(s.state)
    att, results = run_until_idle(s, Feed([walk_fills()]))
    assert results[0].get("idle") == "NO_CANDIDATES"
    assert s.state == before
    assert s.unresolved_bound()["count"] == 0


def test_a_manual_pair_the_net_could_not_see_is_caught_by_the_walk(
        tmp_path, monkeypatch):
    # a hand-placed YES 1 + NO 1 leaves the venue net at 0, so settlement
    # cannot see it; the walk does, and the row fails closed again
    s = held(tmp_path)
    settle(s)
    extra = [fill(M, "UP", "1", "0.50", "T-MAN1", "MANUAL1"),
             fill(M, "DOWN", "1", "0.49", "T-MAN2", "MANUAL2")]
    run_until_idle(s, Feed([walk_fills(extra=extra)]))
    r = row(s)
    assert r["sibling_feed_settlement"]["venue_confirmation"] == \
        "CONTRADICTED"
    assert r["venue_contradicted"]["reason"] == \
        "VENUE_FILL_OF_AN_ORDER_NOT_IN_THE_FEED"
    # manual rows are never attributed to the bot: no trade id consumed
    assert not s.state.get("cost_basis_corrections")
    assert s.unresolved_bound()["count"] == 1
    line = publication(s, monkeypatch)
    assert line["readable"] is False
    assert line["reason"] == "UNRESOLVED_COST_BASIS:1"


def test_a_short_walk_spends_a_bounded_attempt_and_keeps_the_row(
        tmp_path, monkeypatch):
    s = held(tmp_path)
    settle(s)
    monkeypatch.setattr(VCA, "MAX_WALK_PAGES", 1)     # stops short
    for _ in range(VCA.MAX_CONFIRM_ATTEMPTS + 2):
        run_until_idle(s, Feed([walk_fills(), [], []]))
    r = row(s)
    assert r["pnl_provenance"] == VCA.FEED_SETTLEMENT
    assert r["sibling_feed_settlement"]["venue_confirmation"] == \
        "PENDING_ACTIVITY_WALK"
    rec = s.state[VCA.STATE_KEY]["markets"][M]
    assert rec["confirm_attempts"] == VCA.MAX_CONFIRM_ATTEMPTS
    assert s.unresolved_bound()["count"] == 0


# --------------------------------------------------------- the runner path

def runner_shell(store, monkeypatch, doc, orders, *, positions=(),
                 settled=(M,)):
    emitted = []
    monkeypatch.setattr(R, "emit", lambda k, d: emitted.append((k, d)))

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

    class T:
        market_slug = "cpc-btc-updown-15m-2026-11-27-0145z"

        def settlement(self, m):
            assert m in settled
            return {"terminal": True, "settlement_px": D(1),
                    "state": "MARKET_STATE_EXPIRED",
                    "resolution": res(m)}

        def order(self, vid):
            return copy.deepcopy(orders[vid])

        def positions(self):
            return copy.deepcopy(list(positions))

    class Stats(dict):
        def __missing__(self, k):
            return 0

    class Sh:
        pass
    sh = Sh()
    sh.store, sh.transport = store, T()
    sh.now_t, sh.last_settlement_poll = 1e9, {}
    sh.stats, sh.win = Stats(), Stats()
    sh.attest_other_strategy_cost = lambda m, t: None
    sh.settle_sports_from_feed = lambda m, t, rep: \
        R.Runner.settle_sports_from_feed(sh, m, t, rep)
    sh.emit_window_accounting = lambda m, settlement=None: None
    return sh, emitted


def test_the_switch_off_reads_nothing_and_settles_at_the_bound(
        tmp_path, monkeypatch):
    s = held(tmp_path)
    monkeypatch.setenv("L5_SPORTS_FILLS_URL", URL)
    monkeypatch.setenv("L5_SPORTS_ATTEST_FLOOR_UTC_DAY", "2026-11-22")
    sh, emitted = runner_shell(s, monkeypatch, feed_doc(), venue_orders())
    sh.transport.order = lambda vid: pytest.fail("read an order while off")
    R.Runner.sweep_settlements(sh)
    assert row(s)["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"
    assert "L5_SPORTS_FEED_SETTLEMENT" not in [k for k, _ in emitted]


@pytest.mark.parametrize("unset", ["L5_SPORTS_FILLS_URL",
                                   "L5_SPORTS_ATTEST_FLOOR_UTC_DAY"])
def test_the_switch_needs_the_sports_scope_too(tmp_path, monkeypatch, on,
                                               unset):
    monkeypatch.delenv(unset)
    s = held(tmp_path)
    sh, _ = runner_shell(s, monkeypatch, feed_doc(), venue_orders())
    R.Runner.sweep_settlements(sh)
    assert row(s)["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"


def test_the_runner_settles_timleg_from_the_feed(tmp_path, monkeypatch, on):
    s = held(tmp_path)
    sh, emitted = runner_shell(s, monkeypatch, feed_doc(), venue_orders())
    R.Runner.sweep_settlements(sh)
    assert row(s)["pnl_provenance"] == VCA.FEED_SETTLEMENT
    assert D(row(s)["settlement_pnl"]) == D("0.18")
    line = publication(s, monkeypatch)
    assert line["readable"] is True


def test_the_runner_reads_the_venue_net_from_positions(tmp_path,
                                                       monkeypatch, on):
    # a residual: the venue still lists the position with its halves
    rows = [("VA1", "YES", "5", "0.50"), ("VB1", "NO", "3", "0.46")]
    s = held(tmp_path, up="2")
    pos = [{"marketSlug": M, "qtyBought": "5", "qtySold": "3",
            "netPosition": "2", "expired": True}]
    sh, _ = runner_shell(s, monkeypatch, feed_doc(rows), venue_orders(rows),
                         positions=pos)
    R.Runner.sweep_settlements(sh)
    assert row(s)["pnl_provenance"] == VCA.FEED_SETTLEMENT
    # ... and a positions row that disagrees (a manual YES) falls back
    (tmp_path / "b").mkdir()
    s2 = held(tmp_path / "b", up="2")
    pos2 = [dict(pos[0], qtyBought="6", netPosition="3")]
    sh2, _ = runner_shell(s2, monkeypatch, feed_doc(rows),
                          venue_orders(rows), positions=pos2)
    R.Runner.sweep_settlements(sh2)
    assert row(s2)["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"
    assert s2.unresolved_bound()["count"] == 1


def test_an_unreachable_feed_falls_back_to_the_bound(tmp_path, monkeypatch,
                                                     on):
    s = held(tmp_path)
    sh, emitted = runner_shell(s, monkeypatch, feed_doc(), venue_orders())
    import urllib.request as UR

    def boom(url, timeout=0):
        raise OSError("down")
    monkeypatch.setattr(UR, "urlopen", boom)
    R.Runner.sweep_settlements(sh)
    assert row(s)["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"
    got = [d for k, d in emitted if k == "L5_SPORTS_FEED_SETTLEMENT"]
    assert got and got[0]["reason"] == "FEED_SETTLE_FAILED:OSError"
