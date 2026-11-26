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


NOW = __import__("datetime").datetime(
    2026, 11, 26, 18, 0, tzinfo=__import__("datetime").timezone.utc
).timestamp()


def _apply_as_8cfb39d37(s):
    """Write exactly what release 8cfb39d37 wrote: the row restated, the
    attributed line and the legacy scalar moved by the delta."""
    row = s.state["retired_markets"][DUKE]
    keys = list(row.get("unverified_cost_basis_keys") or [])
    rt = VCA.round_trip_from_rows(duke_rows(), DUKE)
    s.state["accounting_rebase_log"] = []       # the old verb's view
    out = s.attest_retired_round_trip(DUKE, round_trip=rt,
                                      walk={"sufficient": True},
                                      correction_id="venue-round-trip|" + DUKE)
    s.state["accounting_rebase_log"] = [dict(REBASE)]
    assert out["attested"] is True
    rec = [r for r in s.state["cost_basis_corrections"]
           if r["market"] == DUKE][0]
    for k in ("inside_cash_anchor", "lifetime_delta"):
        rec.pop(k, None)
    s.save()
    return keys


def test_a_row_settled_before_the_reanchor_is_not_restated(anchored):
    before = json.dumps(anchored.state["retired_markets"][DUKE],
                        sort_keys=True)
    run_until_idle(anchored, Feed([duke_rows()]))
    assert json.dumps(anchored.state["retired_markets"][DUKE],
                      sort_keys=True) == before
    assert anchored.realized_today() == D("-50")
    assert anchored.lifetime_basis()["reconciled"] is True
    assert not [r for r in anchored.state.get("cost_basis_corrections") or []
                if r.get("market") == DUKE]
    rt = VCA.round_trip_from_rows(duke_rows(), DUKE)
    out = anchored.attest_retired_round_trip(
        DUKE, round_trip=rt, walk={"sufficient": True}, correction_id="x")
    assert out["reason"] == "SETTLED_BEFORE_THE_LAST_REBASE"


def test_the_8cfb39d37_correction_is_reversed_once(anchored):
    original = json.dumps(anchored.state["retired_markets"][DUKE],
                          sort_keys=True)
    _apply_as_8cfb39d37(anchored)
    assert anchored.realized_today() == D("-44.90")
    assert anchored.lifetime_basis()["reconciled"] is False
    out = anchored.repair_round_trips_inside_cash_anchor(
        reader=VCA.ROUND_TRIP_READER)
    assert len(out) == 1 and D(out[0]["reversed_delta"]) == D("5.10")
    assert anchored.realized_today() == D("-50")
    assert D(anchored.state["realized_unattributed"]) == D("-10")
    assert anchored.lifetime_basis()["reconciled"] is True
    assert json.dumps(anchored.state["retired_markets"][DUKE],
                      sort_keys=True) == original
    rec = [r for r in anchored.state["cost_basis_corrections"]
           if r["market"] == DUKE][0]
    assert rec["reversed_inside_cash_anchor"]
    assert anchored.repair_round_trips_inside_cash_anchor() == []
    again = LX.InventoryStore(anchored.path)
    assert again.repair_round_trips_inside_cash_anchor() == []
    assert again.realized_today() == D("-50")
    assert VCA.candidates(again) == []


def test_the_cdfa68f4d_residual_offset_is_undone_and_the_day_is_readable(
        tmp_path):
    """The production state after cdfa68f4d: residual -5.10, so the day
    figure was UNKNOWN. After the repair: residual 0, day readable."""
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    retire(s, DUKE, up="5", yes="0", t="2026-11-14T23:00:00+00:00")
    s.state["accounting_rebase_log"] = [dict(REBASE, unattributed="0")]
    s.state["cash_anchor"] = {"at_read_utc": "2026-11-16T00:11:52Z"}
    s.state["realized_attributed"] = "-50"
    s.state["realized_unattributed"] = "0"
    s.state["realized_today"] = "-50"
    original = json.dumps(s.state["retired_markets"][DUKE], sort_keys=True)
    _apply_as_8cfb39d37(s)
    rec = [r for r in s.state["cost_basis_corrections"]
           if r["market"] == DUKE][0]
    # cdfa68f4d's repair
    s.state["realized_unattributed"] = "-5.10"
    s.state["realized_today"] = str(D(s.state["realized_today"]) - D("5.10"))
    rec["inside_cash_anchor"] = True
    rec["lifetime_delta"] = "0"
    assert s.realized_current_utc_day(now=NOW) is None
    assert s.realized_today() == D("-50")
    s.repair_round_trips_inside_cash_anchor()
    assert D(s.state["realized_unattributed"]) == D("0")
    assert s.realized_today() == D("-50")
    assert D(s.state["realized_attributed"]) == D("-50")
    assert s.realized_current_utc_day(now=NOW) == D("0")
    assert s.lifetime_basis()["reconciled"] is True
    assert json.dumps(s.state["retired_markets"][DUKE],
                      sort_keys=True) == original


def test_the_attestor_step_runs_the_repair_without_a_transport(anchored):
    _apply_as_8cfb39d37(anchored)
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
    assert D(s.state["realized_unattributed"]) == D("-10")
