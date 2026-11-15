"""Account-wide inventory reconciliation (Priority 0, 2026-11-15).

The audit said "$26.72 one-sided exposure open" while the heartbeat said $0.
The $26.72 was the lifetime lone-leg cost of settled markets. These tests pin
the corrected meaning against the venue's own position rows, on a shared
account that also carries BTC5m/BTC15m inventory, across a restart."""
from decimal import Decimal as D

from src import pmus_sports_inventory as INV
from src import pmus_sports_two_sided as M


def mk(y, n, cy, cn, done=False, settle_px=None):
    m = {"orders": {}, "pos": {"YES": str(y), "NO": str(n)},
         "cost": {"YES": str(cy), "NO": str(cn)}, "done": done}
    if settle_px is not None:
        m["settle_px"] = str(settle_px)
    return m


def row(slug, bought, sold, net=None, **kw):
    r = {"marketSlug": slug, "qtyBoughtDecimal": str(bought),
         "qtySoldDecimal": str(sold)}
    r["netPositionDecimal"] = str(net if net is not None else
                                  D(str(bought)) - D(str(sold)))
    r.update(kw)
    return r


def test_settled_lone_legs_are_lifetime_cost_not_open_exposure():
    # the live shape: lone legs that already settled at zero, venue flat
    book = {"aec-cs2-a-b": mk(6, 0, "6.00", "0", done=True, settle_px="0"),
            "aec-cfb-c-d": mk(3, 2, "1.80", "1.10", done=True,
                              settle_px="1"),
            "aec-atp-e-f": mk(1, 1, "0.48", "0.49", done=True,
                              settle_px="0")}
    r = INV.reconcile([], [], book)
    assert r["sports_open_unmatched_cost_usd"] == "0.0000"
    assert D(r["sports_lifetime_unmatched_cost_usd"]) == D("6.60")
    assert r["reconciled"] and r["sports_status"]["settled_flat"] == 3


def test_netting_venue_net_equals_book_yes_minus_no():
    # bought 3 YES, then bought 2 NO = sold 2 YES on a netting venue
    book = {"aec-nba-x-y": mk(3, 2, "1.50", "0.96")}
    r = INV.reconcile([row("aec-nba-x-y", 3, 2)], [], book)
    assert r["sports_status"]["match"] == 1 and not r["mismatches"]
    assert r["sports_open_unmatched_cost_usd"] == "0.5000"   # 1 lone YES


def test_lone_no_is_a_negative_venue_net():
    book = {"aec-nba-x-y": mk(0, 2, "0", "0.90")}
    r = INV.reconcile([row("aec-nba-x-y", 0, 2)], [], book)
    assert r["sports_status"]["match"] == 1
    assert r["sports_open_unmatched_cost_usd"] == "0.9000"


def test_venue_holding_what_the_book_thinks_is_flat_is_a_mismatch():
    book = {"aec-nba-x-y": mk(1, 1, "0.45", "0.50")}
    r = INV.reconcile([row("aec-nba-x-y", 2, 1)], [], book)
    assert not r["reconciled"]
    assert r["mismatches"][0]["venue_net"] == "1"
    assert r["mismatches"][0]["book_net"] == "0"


def test_book_lone_leg_the_venue_does_not_hold_is_a_mismatch_while_open():
    book = {"aec-nba-x-y": mk(1, 0, "0.45", "0")}
    r = INV.reconcile([], [], book)
    assert r["sports_status"]["mismatch"] == 1
    assert r["sports_open_unmatched_cost_usd"] == "0.0000"


def test_closed_unpaid_market_is_pending_resolution_not_open():
    book = {"aec-nfl-a-b": mk(1, 0, "0.40", "0", done=True)}
    r = INV.reconcile([row("aec-nfl-a-b", 1, 0)], [], book)
    assert r["sports_status"]["pending_resolution"] == 1
    assert r["sports_open_unmatched_cost_usd"] == "0.0000"
    assert r["sports_pending_resolution_cost_usd"] == "0.4000"


def test_closed_market_venue_flat_without_price_awaits_accounting():
    book = {"aec-nfl-a-b": mk(1, 0, "0.40", "0", done=True)}
    r = INV.reconcile([], [], book)
    assert r["sports_status"]["settled_awaiting_px"] == 1 and r["reconciled"]


def test_settled_market_venue_row_still_present_is_an_accounting_lag():
    book = {"aec-nfl-a-b": mk(1, 0, "0.40", "0", done=True, settle_px="1")}
    r = INV.reconcile([row("aec-nfl-a-b", 1, 0)], [], book)
    assert r["settled_awaiting_venue_update"][0]["slug"] == "aec-nfl-a-b"
    assert not r["mismatches"] and r["reconciled"]


def test_shared_account_attributes_other_strategies_and_never_counts_them():
    book = {"aec-nba-x-y": mk(1, 1, "0.45", "0.50")}
    rows = [row("aec-nba-x-y", 1, 0, net=None) | {"qtySoldDecimal": "1",
                                                  "netPositionDecimal": "0"},
            row("cpc-btc-updown-5m-2026-11-15-2100z", 4, 0),
            row("cpc-btc-updown-15m-2026-11-15-2100z", 0, 3),
            row("aec-nhl-manual-trade", 2, 0),
            row("tsc-something-else", 1, 0)]
    orders = [{"id": "s1", "marketSlug": "aec-nba-x-y"},
              {"id": "b5", "marketSlug": "cpc-btc-updown-5m-2026-11-15-2100z"},
              {"id": "b15", "marketSlug": "cpc-btc-updown-15m-x"},
              {"id": "man", "marketSlug": "aec-nba-x-y"}]
    r = INV.reconcile(rows, orders, book, own_order_ids={"s1"})
    owners = r["by_owner"]
    assert owners["btc5m"]["positions"] == 1 and owners["btc5m"]["open_orders"] == 1
    assert owners["btc15m"]["positions"] == 1 and owners["btc15m"]["open_orders"] == 1
    assert owners["sports_venue_untracked"]["positions"] == 1
    assert owners["unknown"]["positions"] == 1
    assert r["open_orders_by_owner"]["sports"] == 1
    # a foreign order resting in one of OUR markets is surfaced, not adopted
    assert r["foreign_orders_in_sports_markets"] == [
        {"slug": "aec-nba-x-y", "order_id": "man"}]
    # BTC inventory never enters sports exposure
    assert r["sports_open_unmatched_cost_usd"] == "0.0000"


def test_net_disagreeing_with_bought_minus_sold_is_flagged_not_guessed():
    book = {"aec-nba-x-y": mk(1, 0, "0.45", "0")}
    r = INV.reconcile([row("aec-nba-x-y", 1, 0, net=-1)], [], book)
    assert r["mismatches"][0]["sign_conflict"] is True
    assert not r["reconciled"]


def test_incomplete_position_walk_is_never_reported_reconciled():
    r = INV.reconcile([], [], {}, walk_complete=False)
    assert not r["reconciled"]


def test_object_keyed_by_slug_shape_is_read():
    resp = {"positions": {"aec-a": {"qtyBoughtDecimal": "1",
                                    "qtySoldDecimal": "0"}},
            "availablePositions": [{"marketSlug": "aec-b",
                                    "qtyBoughtDecimal": "9"}]}
    rows = INV.rows_of(resp)
    assert [r["marketSlug"] for r in rows] == ["aec-a"]   # available != held


# ------------------------------------------------------------ engine wiring
class SnapVenue:
    def __init__(self, rows, orders, complete=True):
        self.snap = {"rows": rows, "open_orders": orders, "pages": 1,
                     "complete": complete}
        self.t = object()
        self.cancels = []

    def positions_snapshot(self):
        return self.snap

    def cancel(self, oid):                    # must never be called
        self.cancels.append(oid)
        return True

    cancel_fast = cancel


def _engine(tmp_path, venue):
    return M.Engine(venue, M.Book(tmp_path / "s.json"))


def test_restart_reloads_book_and_reconciles_identically(tmp_path):
    v = SnapVenue([row("aec-nba-x-y", 3, 2),
                   row("cpc-btc-updown-5m-2026-11-15-2100z", 4, 0)],
                  [{"id": "b5", "marketSlug": "cpc-btc-updown-5m-x"}])
    e = _engine(tmp_path, v)
    m = e.b.m("aec-nba-x-y")
    m["pos"] = {"YES": "3", "NO": "2"}
    m["cost"] = {"YES": "1.50", "NO": "0.96"}
    s = e.b.m("aec-cs2-old")                     # settled lone leg
    s.update(pos={"YES": "6", "NO": "0"}, cost={"YES": "6", "NO": "0"},
             done=True, settle_px="0")
    e.b.save()
    before = e.inventory_reconcile()
    e2 = _engine(tmp_path, v)                    # process restart
    after = e2.inventory_reconcile()
    for k in ("sports_open_unmatched_cost_usd",
              "sports_lifetime_unmatched_cost_usd", "sports_status",
              "by_owner", "reconciled"):
        assert before[k] == after[k], k
    assert after["sports_open_unmatched_cost_usd"] == "0.5000"
    assert after["sports_lifetime_unmatched_cost_usd"] == "6.5000"
    assert after["reconciled"]
    assert v.cancels == []                       # read-only, BTC untouched


def test_audit_open_figure_is_venue_confirmed_not_lifetime(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    from src import pmus_settlement_auditor as SA
    v = SnapVenue([], [])
    e = _engine(tmp_path, v)
    s = e.b.m("aec-cs2-old")
    s.update(pos={"YES": "6", "NO": "0"}, cost={"YES": "6", "NO": "0"},
             done=True, settle_px="0")
    monkeypatch.setattr(SA, "activity_pages",
                        lambda t, since=None, max_pages=0:
                        {"rows": [], "ended": "eof"})
    monkeypatch.setattr(M, "SETTLE_LOOKUPS_PER_AUDIT", 0)
    e.account_audit()
    out = capsys.readouterr().out
    body = out.split("ACCOUNT_AUDIT")[1]
    assert '"6_unmatched_cost_open_usd":"0.0000"' in body
    assert '"6_unmatched_cost_open_source":"venue_reconciled"' in body
    assert '"6_unmatched_cost_lifetime_usd":"6.0000"' in body
    assert "INVENTORY_RECONCILE" in out


def test_audit_falls_back_to_open_book_figure_when_venue_unreadable(
        tmp_path, monkeypatch, capsys):
    from src import pmus_settlement_auditor as SA

    class Broken(SnapVenue):
        def positions_snapshot(self):
            raise RuntimeError("429")
    e = _engine(tmp_path, Broken([], []))
    s = e.b.m("aec-cs2-old")
    s.update(pos={"YES": "6", "NO": "0"}, cost={"YES": "6", "NO": "0"},
             done=True)
    monkeypatch.setattr(SA, "activity_pages",
                        lambda t, since=None, max_pages=0:
                        {"rows": [], "ended": "eof"})
    monkeypatch.setattr(M, "SETTLE_LOOKUPS_PER_AUDIT", 0)
    e.account_audit()
    body = capsys.readouterr().out.split("ACCOUNT_AUDIT")[-1]
    assert '"6_unmatched_cost_open_usd":"0"' in body        # done market
    assert '"6_unmatched_cost_open_source":"book_only"' in body
