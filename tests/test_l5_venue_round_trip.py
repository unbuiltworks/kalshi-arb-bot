"""Round-trip form of the venue cost attestation (owner 2026-11-26: "Fix
the underlying accounting mechanism").

LIVE 2026-11-26: the owner bought 469 NO in aec-cbb-duke-uconn-2026-11-25 at
02:07:50 (venue order DYSS79F015P6) and sold all 469 at 02:14:01
(DYSW1R1G15P6). The ledger adopted the 469 at the $1/share bound at 02:12,
never saw the sale, and settlement at 02:26 booked -469.00. The venue's own
result was -20.63 (cash -25.00 then +4.37). The exact forms refused
(COST_CROSSCHECK_FAILED) because they read the SALE as a purchase."""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_venue_cost_attestation as VCA
from tests.test_l5_venue_cost_attestation import (  # noqa: F401
    Feed, retire, run_until_idle, fill)

UCONN = "aec-cbb-duke-uconn-2026-11-25"
SETTLED = "2026-11-26T02:26:26+00:00"


@pytest.fixture(autouse=True)
def _no_retry_spacing(monkeypatch):
    monkeypatch.setattr(VCA, "RETRY_S", 0.0)
    monkeypatch.setattr(VCA, "RETRO_FLOOR_UTC_DAY", "2026-11-13")


def trade(market, intent, qty, realized, tid, vid, *, t, manual=True,
          aggressor=True, cost="0"):
    """A trade row as the production activity feed sends it: OUR
    execution (selected by isAggressor) carries the order with its intent
    and manual indicator; the trade carries the venue's realizedPnl."""
    order = {"id": vid, "intent": intent,
             "outcomeSide": ("OUTCOME_SIDE_NO" if intent.endswith("SHORT")
                             else "OUTCOME_SIDE_YES"),
             "manualOrderIndicator": ("MANUAL_ORDER_INDICATOR_MANUAL"
                                      if manual else
                                      "MANUAL_ORDER_INDICATOR_AUTOMATIC")}
    mine = {"order": order, "lastShares": str(qty),
            "lastPx": {"value": "0.95", "currency": "USD"},
            "commissionNotionalCollected": {"value": "0.01",
                                            "currency": "USD"}}
    other = {"order": {"id": "CPTY-" + tid}, "lastShares": str(qty),
             "lastPx": {"value": "0.95", "currency": "USD"}}
    key_mine = "aggressorExecution" if aggressor else "passiveExecution"
    key_other = "passiveExecution" if aggressor else "aggressorExecution"
    return {"type": "activity_type_trade", "trade": {
        "id": tid, "marketSlug": market, "qty": str(qty),
        "price": {"value": "0.95", "currency": "USD"},
        "cost": {"value": cost, "currency": "USD"},
        "realizedPnl": {"value": str(realized), "currency": "USD"},
        "isAggressor": aggressor, key_mine: mine, key_other: other,
        "time": t}}


def uconn_rows(*, sell_qty="469", sell_realized="-20.63", buy_realized="0"):
    # newest first, as the feed returns them
    return [
        trade(UCONN, "ORDER_INTENT_SELL_SHORT", sell_qty, sell_realized,
              "T-SELL", "DYSW1R1G15P6", t="2026-11-26T02:14:01Z"),
        trade(UCONN, "ORDER_INTENT_BUY_SHORT", "469", buy_realized,
              "T-BUY", "DYSS79F015P6", t="2026-11-26T02:07:50Z"),
    ]


@pytest.fixture()
def uconn(tmp_path):
    """The production state: 469 DOWN adopted at the bound, settled YES=1,
    the exact forms already exhausted on COST_CROSSCHECK_FAILED."""
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.state["realized_attributed"] = "-45.9314"
    s.state["realized_unattributed"] = "0"
    s.state["realized_today"] = "-45.9314"
    s.state.setdefault("risk_floor_usd", {})[UCONN] = "469.0000"
    row = retire(s, UCONN, dn="469", yes="1", t=SETTLED)
    assert D(row["settlement_pnl"]) == D("-469")
    for _ in range(VCA.MAX_ATTEMPTS):
        s.note_venue_cost_attempt(UCONN, reason="DOWN:COST_CROSSCHECK_FAILED",
                                  final=True)
    return s


def day_of(store):
    import datetime as dt
    now = dt.datetime(2026, 11, 26, 3, 30, tzinfo=dt.timezone.utc).timestamp()
    return store.realized_current_utc_day(now=now)


class TestTheLiveCase:

    def test_the_bound_is_replaced_by_the_venue_result(self, uconn):
        before_day, before_life = day_of(uconn), uconn.realized_today()
        run_until_idle(uconn, Feed([uconn_rows()]))
        row = uconn.state["retired_markets"][UCONN]
        assert D(row["settlement_pnl"]) == D("-20.63")
        assert row["pnl_provenance"] == "VENUE_ATTESTED_COST"
        assert row["venue_qty_form"] == LX.InventoryStore.ROUND_TRIP_FORM
        assert row["cost_superseded"]["settlement_pnl"] == "-469.00000000" \
            or D(row["cost_superseded"]["settlement_pnl"]) == D("-469")
        delta = D("-20.63") - D("-469")
        assert day_of(uconn) == before_day + delta
        assert uconn.realized_today() == before_life + delta
        assert uconn.realized_today() == D("-45.9314") + D("-20.63")

    def test_manual_attribution_is_a_label_only(self, uconn):
        run_until_idle(uconn, Feed([uconn_rows()]))
        row = uconn.state["retired_markets"][UCONN]
        assert row["origin"] == "MANUAL"
        # the manual loss stays in the account-wide totals
        assert D(uconn.state["realized_attributed"]) == \
            D("-45.9314") + D("-20.63")
        assert row["l5_owner_attribution_verified"] is False

    def test_bound_keys_and_risk_floor_are_cleared(self, uconn):
        run_until_idle(uconn, Feed([uconn_rows()]))
        assert not [k for k in uconn.state.get("leg_cost_basis_unknown", [])
                    if k.startswith(UCONN)]
        assert UCONN not in (uconn.state.get("risk_floor_usd") or {})

    def test_the_correction_record_carries_the_evidence(self, uconn):
        run_until_idle(uconn, Feed([uconn_rows()]))
        rec = [r for r in uconn.state["cost_basis_corrections"]
               if r["market"] == UCONN]
        assert len(rec) == 1
        rec = rec[0]
        assert rec["kind"] == "VENUE_ATTESTED_ROUND_TRIP"
        assert sorted(rec["venue_order_ids"]) == ["DYSS79F015P6",
                                                  "DYSW1R1G15P6"]
        assert sorted(rec["trade_ids"]) == ["T-BUY", "T-SELL"]
        assert D(rec["old_pnl"]) == D("-469")
        assert D(rec["new_pnl"]) == D("-20.63")
        assert rec["settlement_utc_day"] == "2026-11-26"


class TestFailClosed:

    def test_a_position_still_open_at_settlement_keeps_the_bound(self, uconn):
        run_until_idle(uconn, Feed([uconn_rows(sell_qty="200",
                                               sell_realized="-8")]))
        row = uconn.state["retired_markets"][UCONN]
        assert D(row["settlement_pnl"]) == D("-469")
        assert row["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"
        rec = uconn.state["venue_cost_attestation"]["markets"][UCONN]
        assert "POSITION_OPEN_AT_SETTLEMENT" in rec["last_reason"]

    def test_an_unreadable_intent_keeps_the_bound(self, uconn):
        rows = uconn_rows()
        rows[1]["trade"]["aggressorExecution"]["order"]["intent"] = ""
        run_until_idle(uconn, Feed([rows]))
        assert D(uconn.state["retired_markets"][UCONN]["settlement_pnl"]) \
            == D("-469")

    def test_a_missing_realized_pnl_keeps_the_bound(self, uconn):
        rows = uconn_rows()
        del rows[0]["trade"]["realizedPnl"]
        run_until_idle(uconn, Feed([rows]))
        assert D(uconn.state["retired_markets"][UCONN]["settlement_pnl"]) \
            == D("-469")

    def test_a_market_the_ledger_traded_is_not_replaced(self, uconn):
        uconn.state.setdefault("fill_provenance", {})[UCONN] = {
            "entries": [{"l5_owner_verified": True}]}
        run_until_idle(uconn, Feed([uconn_rows()]))
        assert D(uconn.state["retired_markets"][UCONN]["settlement_pnl"]) \
            == D("-469")

    def test_attempts_are_bounded(self, uconn):
        rows = uconn_rows(sell_qty="200")
        for _ in range(6):
            run_until_idle(uconn, Feed([rows]))
        rec = uconn.state["venue_cost_attestation"]["markets"][UCONN]
        assert rec["round_trip_attempts"] == VCA.MAX_ROUND_TRIP_ATTEMPTS
        assert rec["round_trip_final"] is True
        assert VCA.candidates(uconn) == []


class TestDurableAndIdempotent:

    def test_a_restart_neither_repeats_nor_undoes_it(self, uconn, tmp_path):
        run_until_idle(uconn, Feed([uconn_rows()]))
        snap = json.dumps(uconn.state["retired_markets"][UCONN],
                          sort_keys=True)
        life = uconn.realized_today()
        reloaded = LX.InventoryStore(uconn.path)
        assert reloaded.realized_today() == life
        run_until_idle(reloaded, Feed([uconn_rows()]))
        assert json.dumps(reloaded.state["retired_markets"][UCONN],
                          sort_keys=True) == snap
        assert len([r for r in reloaded.state["cost_basis_corrections"]
                    if r["market"] == UCONN]) == 1
        assert reloaded.realized_today() == life

    def test_the_verb_refuses_a_second_application(self, uconn):
        rt = VCA.round_trip_from_rows(uconn_rows(), UCONN)
        walk = {"sufficient": True}
        a = uconn.attest_retired_round_trip(UCONN, round_trip=rt, walk=walk,
                                            correction_id="x")
        b = uconn.attest_retired_round_trip(UCONN, round_trip=rt, walk=walk,
                                            correction_id="x")
        assert a["attested"] is True
        assert b["attested"] is False


class TestOtherFormsUnchanged:

    def test_an_exhausted_market_with_no_sale_is_ruled_out_once(
            self, tmp_path):
        s = LX.InventoryStore(str(tmp_path / "inv.json"))
        m = "cpc-btc-updown-5m-2026-11-13-1610z"
        retire(s, m, up="5", yes="0", t="2026-11-13T16:16:00+00:00")
        for _ in range(VCA.MAX_ATTEMPTS):
            s.note_venue_cost_attempt(m, reason="NO_VENUE_FILLS_FOUND",
                                      final=True)
        feed = Feed([[fill(m, "UP", "4", "0.08", "T-1", "V-1",
                           t="2026-11-13T16:11:00Z")]])
        run_until_idle(s, feed)
        rec = s.state["venue_cost_attestation"]["markets"][m]
        assert rec["round_trip_final"] is True
        assert D(s.state["retired_markets"][m]["settlement_pnl"]) == D("-5")
        assert VCA.candidates(s) == []
