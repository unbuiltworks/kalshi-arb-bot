"""A round trip settled BEFORE the last cash re-anchor is already inside that
flat-account cash read. LIVE 2026-11-26 (release 8cfb39d37):
aec-cfb-duke-mia-2026-11-14 was restated -5.00 -> +0.10 and the lifetime
figure moved +5.10, counting the result twice (lifetime_basis.reconciled
False). The row is restated; the lifetime figure must not move."""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_venue_cost_attestation as VCA
from tests.test_l5_venue_cost_attestation import retire, run_until_idle, Feed
from tests.test_l5_venue_round_trip import trade, _no_retry_spacing  # noqa

DUKE = "aec-cfb-duke-mia-2026-11-14"
REBASE = {"t": "2026-11-16T00:58:25+00:00", "attributed": "-40",
          "unattributed": "-10", "cash_delta": "-50", "reason": "r"}


def duke_rows():
    return [
        trade(DUKE, "ORDER_INTENT_SELL_LONG", "5", "0.10", "T-S", "V-S",
              t="2026-11-14T20:10:00Z"),
        trade(DUKE, "ORDER_INTENT_BUY_LONG", "5", None, "T-B", "V-B",
              t="2026-11-14T19:00:00Z"),
    ]


@pytest.fixture
def anchored(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    row = retire(s, DUKE, up="5", yes="0", t="2026-11-14T23:00:00+00:00")
    assert D(row["settlement_pnl"]) == D("-5")
    # the re-anchor after it: its cash read already holds duke-mia's result
    s.state["accounting_rebase_log"] = [dict(REBASE)]
    s.state["cash_anchor"] = {"at_read_utc": "2026-11-16T00:11:52Z"}
    s.state["realized_attributed"] = "-40"
    s.state["realized_unattributed"] = "-10"
    s.state["realized_today"] = "-50"
    for _ in range(VCA.MAX_ATTEMPTS):
        s.note_venue_cost_attempt(DUKE, reason="UP:COST_CROSSCHECK_FAILED",
                                  final=True)
    s.save()
    assert s.lifetime_basis()["reconciled"] is True
    return s


def test_the_row_is_restated_and_the_lifetime_figure_does_not_move(anchored):
    run_until_idle(anchored, Feed([duke_rows()]))
    row = anchored.state["retired_markets"][DUKE]
    assert D(row["settlement_pnl"]) == D("0.10")
    assert anchored.realized_today() == D("-50")
    assert anchored.lifetime_basis()["reconciled"] is True
    rec = [r for r in anchored.state["cost_basis_corrections"]
           if r["market"] == DUKE][0]
    assert rec["inside_cash_anchor"] is True
    assert rec["lifetime_delta"] == "0"
    assert D(anchored.state["realized_attributed"]) == D("-40") + D("5.10")
    assert D(anchored.state["realized_unattributed"]) == D("-10") - D("5.10")


def test_a_correction_applied_by_the_old_verb_is_repaired_once(anchored,
                                                               tmp_path):
    # what release 8cfb39d37 wrote: lifetime moved by the delta, no marker
    run_until_idle(anchored, Feed([duke_rows()]))
    rec = [r for r in anchored.state["cost_basis_corrections"]
           if r["market"] == DUKE][0]
    for k in ("inside_cash_anchor", "lifetime_delta"):
        rec.pop(k)
    anchored.state["realized_unattributed"] = "-10"
    anchored.state["realized_today"] = str(D("-50") + D("5.10"))
    anchored.save()
    assert anchored.realized_today() == D("-44.90")
    assert anchored.lifetime_basis()["reconciled"] is False

    out = anchored.repair_round_trips_inside_cash_anchor()
    assert out == [{"market": DUKE, "lifetime_moved_usd": "-5.10000000"}] \
        or (len(out) == 1 and D(out[0]["lifetime_moved_usd"]) == D("-5.10"))
    assert anchored.realized_today() == D("-50")
    assert anchored.lifetime_basis()["reconciled"] is True
    assert D(anchored.state["retired_markets"][DUKE]["settlement_pnl"]) \
        == D("0.10")
    # once, and durably across a restart
    assert anchored.repair_round_trips_inside_cash_anchor() == []
    again = LX.InventoryStore(anchored.path)
    assert again.repair_round_trips_inside_cash_anchor() == []
    assert again.realized_today() == D("-50")


def test_the_attestor_step_runs_the_repair_without_a_transport(anchored):
    run_until_idle(anchored, Feed([duke_rows()]))
    rec = [r for r in anchored.state["cost_basis_corrections"]
           if r["market"] == DUKE][0]
    for k in ("inside_cash_anchor", "lifetime_delta"):
        rec.pop(k)
    anchored.state["realized_unattributed"] = "-10"
    anchored.state["realized_today"] = str(D("-44.90"))
    VCA.Attestor().step(anchored, None, 0.0)
    assert anchored.realized_today() == D("-50")


def test_a_round_trip_after_the_reanchor_still_moves_the_lifetime(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    m = "aec-cfb-duke-mia-2026-11-20"
    retire(s, m, up="5", yes="0", t="2026-11-20T23:00:00+00:00")
    s.state["accounting_rebase_log"] = [dict(REBASE)]
    s.state["cash_anchor"] = {"at_read_utc": "2026-11-16T00:11:52Z"}
    s.state["realized_attributed"] = "-45"
    s.state["realized_unattributed"] = "-10"
    s.state["realized_today"] = "-55"
    for _ in range(VCA.MAX_ATTEMPTS):
        s.note_venue_cost_attempt(m, reason="UP:COST_CROSSCHECK_FAILED",
                                  final=True)
    rows = [trade(m, "ORDER_INTENT_SELL_LONG", "5", "0.10", "T-S2", "V-S2",
                  t="2026-11-20T20:10:00Z"),
            trade(m, "ORDER_INTENT_BUY_LONG", "5", None, "T-B2", "V-B2",
                  t="2026-11-20T19:00:00Z")]
    run_until_idle(s, Feed([rows]))
    assert s.realized_today() == D("-55") + D("5.10")
    rec = [r for r in s.state["cost_basis_corrections"]
           if r["market"] == m][0]
    assert rec["inside_cash_anchor"] is False
    assert s.repair_round_trips_inside_cash_anchor() == []
    assert s.realized_today() == D("-55") + D("5.10")
