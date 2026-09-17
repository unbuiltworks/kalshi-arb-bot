"""OWNER 2026-09-17 (C) -- the realized-income scoreboard.

The scoreboard's only job is to be the number that cannot flatter us, so most
of this file is about what it must REFUSE to count. Every exclusion below
corresponds to something that has actually looked like money at some point in
this program.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from src import l5_income_scoreboard as sb

NOW = datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc)


def _write(d, rows):
    (d / "ledger.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _realized(slug, usd, when=NOW):
    return {"t": when.isoformat(), "kind": "realized", "slug": slug,
            "realized_usd": str(usd)}


def _breakdown(slug, matched, residual, status="REALIZED", when=NOW, **kw):
    row = {"t": when.isoformat(), "kind": "settlement_breakdown", "slug": slug,
           "settlement_status": status,
           "matched_contribution_usd": str(matched),
           "residual_contribution_usd": str(residual),
           "directional_residual": "0", "matched_pairs": "0",
           "cost_basis_usd": "0", "fees_usd": "venue_unreported",
           "incentives_usd": "0"}
    row.update(kw)
    return row


# --------------------------------------------------------------------------
# what counts

def test_only_venue_confirmed_realized_rows_count(tmp_path):
    _write(tmp_path, [_realized("m1", "-0.1836"), _realized("m2", "-0.3783")])
    s = sb.scoreboard(tmp_path, NOW)
    assert s["today"]["total_confirmed_realized_usd"] == "-0.5619"
    assert s["today"]["markets_completed"] == 2


def test_matched_and_residual_are_reported_separately_never_summed(tmp_path):
    _write(tmp_path, [_realized("m1", "-0.1836"),
                      _breakdown("m1", "0", "-0.1836")])
    s = sb.scoreboard(tmp_path, NOW)["today"]
    assert s["matched_realized_usd"] == "0"
    assert s["residual_realized_usd"] == "-0.1836"
    assert s["total_confirmed_realized_usd"] == "-0.1836"


def test_incentives_are_never_folded_into_trading_pnl(tmp_path):
    """A rebate that makes a losing trade look profitable is the whole reason
    this is a separate line."""
    _write(tmp_path, [_realized("m1", "-0.50"),
                      _breakdown("m1", "0", "-0.50", incentives_usd="5.00")])
    s = sb.scoreboard(tmp_path, NOW)["today"]
    assert s["incentives_usd"] == "5.00"
    assert s["total_confirmed_realized_usd"] == "-0.50"


# --------------------------------------------------------------------------
# what must NOT count

def test_settled_realized_unconfirmed_is_excluded_and_named(tmp_path):
    """The live case: the market resolved, the account went flat, the venue
    served no realizedPnl. That is UNMEASURED, not zero, and hiding it would
    be worse than either."""
    _write(tmp_path, [
        _breakdown("aec-itfme-artkuk-egople-2026-09-17", "999", "999",
                   status="SETTLED_REALIZED_UNCONFIRMED")])
    s = sb.scoreboard(tmp_path, NOW)
    assert s["today"]["total_confirmed_realized_usd"] == "0"
    assert s["today"]["matched_realized_usd"] == "0"
    assert s["excluded_settled_realized_unconfirmed"] == \
        ["aec-itfme-artkuk-egople-2026-09-17"]


def test_a_cost_basis_is_not_profit(tmp_path):
    """0.92 paid for a pair that should redeem at 1.00 contributes nothing
    until a realized row exists."""
    _write(tmp_path, [{"t": NOW.isoformat(), "kind": "filled", "slug": "m1",
                       "qty": "0.34", "side": "OUTCOME_SIDE_YES"},
                      {"t": NOW.isoformat(), "kind": "mandate_commit",
                       "slug": "m1", "cash_at_risk": "0.3128"}])
    s = sb.scoreboard(tmp_path, NOW)
    assert s["today"]["total_confirmed_realized_usd"] == "0"
    assert s["capital_committed_usd"] == "0.3128"


def test_a_settled_row_without_a_realized_row_counts_nothing(tmp_path):
    _write(tmp_path, [{"t": NOW.isoformat(), "kind": "settled", "slug": "m1"}])
    assert sb.scoreboard(tmp_path, NOW)["today"][
        "total_confirmed_realized_usd"] == "0"


def test_an_unreadable_ledger_is_never_reported_as_zero(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(
        json.dumps(_realized("m1", "-1.00")) + "\n{ not json\n", encoding="utf-8")
    s = sb.scoreboard(tmp_path, NOW)
    assert s["readable"] is False
    assert "PARTIALLY UNREADABLE" in sb.render(s)


# --------------------------------------------------------------------------
# windows and rates

def test_windows_separate_today_from_7d_and_30d(tmp_path):
    _write(tmp_path, [
        _realized("today", "1.00", NOW),
        _realized("d3", "2.00", NOW - timedelta(days=3)),
        _realized("d20", "4.00", NOW - timedelta(days=20)),
        _realized("d60", "8.00", NOW - timedelta(days=60))])
    s = sb.scoreboard(tmp_path, NOW)
    assert s["today"]["total_confirmed_realized_usd"] == "1.00"
    assert s["last_7d"]["total_confirmed_realized_usd"] == "3.00"
    assert s["last_30d"]["total_confirmed_realized_usd"] == "7.00"


def test_complete_fill_rate_is_absent_rather_than_invented(tmp_path):
    """With no `submitted` rows carrying a stage there is no denominator, and
    a rate with an invented denominator is worse than an absent one."""
    _write(tmp_path, [{"t": NOW.isoformat(), "kind": "filled",
                       "stage": "spring_complete_1", "slug": "m1"}])
    s = sb.scoreboard(tmp_path, NOW)
    assert s["complete_fill_rate"] is None
    assert s["complete_filled"] == 1
    assert s["complete_submitted"] == 0


def test_rates_are_computed_from_real_rows(tmp_path):
    _write(tmp_path, [
        {"t": NOW.isoformat(), "kind": "mandate_commit", "slug": "m1",
         "cash_at_risk": "0.10"},
        {"t": NOW.isoformat(), "kind": "mandate_commit", "slug": "m1",
         "cash_at_risk": "0.10"},
        {"t": NOW.isoformat(), "kind": "submitted", "stage": "spring_complete_1"},
        {"t": NOW.isoformat(), "kind": "submitted", "stage": "spring_complete_2"},
        {"t": NOW.isoformat(), "kind": "filled", "stage": "spring_complete_1"},
        {"t": NOW.isoformat(), "kind": "order_terminal", "filled_qty": "0.2"},
        {"t": NOW.isoformat(), "kind": "order_terminal", "filled_qty": "0",
         "state": "ORDER_STATE_REJECTED"}])
    s = sb.scoreboard(tmp_path, NOW)
    assert s["fill_rate"] == "0.5000"
    assert s["rejection_rate"] == "0.5000"
    assert s["complete_fill_rate"] == "0.5000"


def test_render_states_the_target_is_not_a_projection(tmp_path):
    _write(tmp_path, [])
    out = sb.render(sb.scoreboard(tmp_path, NOW))
    assert "TARGET" in out and "never a projection" in out
    assert "TOTAL CONFIRMED REALIZED" in out


def test_the_scoreboard_never_writes_to_the_ledger(tmp_path):
    rows = [_realized("m1", "-0.1836")]
    _write(tmp_path, rows)
    before = (tmp_path / "ledger.jsonl").read_bytes()
    sb.scoreboard(tmp_path, NOW)
    assert (tmp_path / "ledger.jsonl").read_bytes() == before
