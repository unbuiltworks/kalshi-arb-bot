"""P1: a resolved market must reach ONE auditable terminal account state.

TWO LIVE EXAMPLES, one mechanism. The legacy cricket market and
aec-atp-jayfri-maxbas-2026-09-13 both RESOLVED at the venue, both left the
scheduler's held set, and the probe's own alarm named what was missing at
13:57:17Z:

    held_absent_while_risk_bearing ... terminal_settlement_recorded: false
    "we are over-stating loss and withholding realized cashflow until the
     settlement audit records it"

THE REASON IT WAS LATE. The sweep read `unsettled_slugs()[:5]` -- a FIXED
PREFIX of a list in ledger first-fill order. Markets 1..5 were re-audited
every minute while 6..n were never reached, so a market's reconciliation
latency depended on how early it first filled rather than on whether it had
resolved. jayfri filled at 13:01, resolved, left held at 13:57, and its full
downside stayed charged for hours.

THE RECORD ITSELF ALREADY EXISTED. `settlement_breakdown` (OWNER 5553037966
#1) carries matched pairs, pair cost, matched contribution, directional
residual, the venue's realized number and the three separate streams. Adding
a second terminal record beside it would have been two canonical mechanisms,
which is the opposite of what was asked. So it is EXTENDED with what it
lacked: what established terminal, when the venue said so, which side won,
the residual's cost basis, provenance and a schema version.

Terminal is established by the venue, never inferred. An empty book, elapsed
time, a count of empty quotes and outcomePrices alone are not evidence.
"""
from __future__ import annotations

import ast
import json
from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont
from src import pmus_settlement_auditor as sa
from src import pmus_spring_l5_session as base


def _code_of(mod, name):
    """A function's CODE with its docstring removed. Docstrings here name the
    very things these tests forbid, as the failures they exist to prevent."""
    fn = next(n for n in ast.walk(ast.parse(open(mod.__file__).read()))
              if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.body = [n for n in fn.body
               if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                       and isinstance(n.value.value, str))]
    return ast.unparse(fn)


def _audit(**over):
    a = {
        "market": "aec-atp-jayfri-maxbas-2026-09-13",
        "status": "REALIZED", "settled": True,
        "settled_by": "venue_market_resolved_account_flat",
        "settlement_key": "flat_at_resolution:2026-09-13T13:56:20Z",
        "realized_confirmed": True, "realized_usd": "-3.9506",
        "realized_source": "venue_per_trade_realized_pnl",
        "position_net": "0", "venue_qty_total": "8.58", "ledger_qty_total": "8.58",
        "resolution": {"side": "Up", "updateTime": "2026-09-13T13:56:20Z"},
        "public": {"public_resolved": True, "settlement_px": "0.0000",
                   "status": "MARKET_STATUS_RESOLVED",
                   "state": "MARKET_STATE_EXPIRED",
                   "settlement_set_time": "2026-09-13T13:56:20Z"},
        "trade_summary": {"trades": [{"isAggressor": False}] * 4},
    }
    a.update(over)
    return a


def _inv(fills):
    inv = base.Inventory()
    for side_up, px, qty in fills:
        inv.record(side_up, D(str(px)), D(str(qty)), "x")
    return inv


def _breakdown(monkeypatch, fills, **over):
    inv = _inv(fills)
    monkeypatch.setattr(cont.base, "held_inventory_from_ledger", lambda s: inv)
    return cont.settlement_breakdown("m", _audit(**over))


# ------------------------------------- the record is complete, and singular

def test_there_is_exactly_one_terminal_record_kind():
    """A second parallel record would be two canonical mechanisms."""
    assert not hasattr(sa, "terminal_reconciliation")
    assert sa.IDEMPOTENT_KINDS == ("settled", "realized")
    body = _code_of(sa, "append_authoritative_ledger")
    assert "terminal_reconciliation" not in body


def test_every_field_the_owner_asked_for_is_present(monkeypatch):
    b = _breakdown(monkeypatch, [(True, "0.460442", "8.58")])
    for k in ("slug", "settlement_status", "settled_by", "outcome_mapping",
              "bought_yes", "bought_no", "cost_basis_usd",
              "matched_pairs", "matched_pair_cost", "matched_contribution_usd",
              "directional_residual", "residual_cost_basis",
              "realized_usd_venue", "fees_usd", "incentives_usd",
              "three_streams", "provenance", "venue_timestamps",
              "reconciliation_version", "reconciled_at"):
        assert k in b, k
    assert b["reconciliation_version"] == "canonical-terminal/v1"


def test_matched_and_residual_stay_decomposed(monkeypatch):
    """cov-bha's real shape: 5.67 matched at pair 0.763597, no residual."""
    b = _breakdown(monkeypatch,
                   [(True, "0.517248", "5.67"), (False, "0.246349", "5.67")])
    assert D(b["matched_pairs"]) == D("5.67")
    assert D(b["matched_pair_cost"]) == D("0.763597")
    assert D(b["matched_contribution_usd"]) == D("5.67") * (D(1) - D("0.763597"))
    assert D(b["directional_residual"]) == 0
    assert b["residual_cost_basis"] is None


def test_a_one_sided_book_is_all_residual(monkeypatch):
    b = _breakdown(monkeypatch, [(True, "0.460442", "8.58")])
    assert D(b["matched_pairs"]) == 0
    assert D(b["directional_residual"]) == D("8.58")
    assert D(b["residual_cost_basis"]) == D("0.460442")


def test_a_short_book_reports_the_NO_cost_basis(monkeypatch):
    b = _breakdown(monkeypatch, [(False, "0.45", "1.88")])
    assert D(b["directional_residual"]) == D("-1.88")
    assert D(b["residual_cost_basis"]) == D("0.45")


def test_the_three_streams_are_never_summed(monkeypatch):
    b = _breakdown(monkeypatch, [(True, "0.460442", "8.58")])
    t = b["three_streams"]
    assert set(t) >= {"matched_pair_pnl_usd", "directional_residual_pnl_usd",
                      "incentives_usd"}
    assert "never summed" in t["labels"]
    assert b["fees_usd"] == "venue_unreported"
    assert b["incentives_usd"] is None      # nothing credited, nothing claimed


def test_realized_stays_the_venues_number_only(monkeypatch):
    b = _breakdown(monkeypatch, [(True, "0.46", "8.58")],
                   status="SETTLED_REALIZED_UNCONFIRMED",
                   realized_confirmed=False, realized_usd=None)
    assert b["realized_usd_venue"] is None
    assert b["residual_contribution_usd"] is None
    assert b["provenance"]["realized_confirmed"] is False
    assert b["settlement_status"] == "SETTLED_REALIZED_UNCONFIRMED"


def test_the_outcome_mapping_and_timestamps_come_from_the_venue(monkeypatch):
    b = _breakdown(monkeypatch, [(True, "0.46", "8.58")])
    assert b["outcome_mapping"]["public_settlement_px"] == "0.0000"
    assert b["outcome_mapping"]["public_status"] == "MARKET_STATUS_RESOLVED"
    assert b["outcome_mapping"]["resolution_side"] == "Up"
    assert b["venue_timestamps"]["resolution_update_time"] == \
        "2026-09-13T13:56:20Z"
    assert b["settled_by"] == "venue_market_resolved_account_flat"


def test_nothing_is_inferred_from_a_book_or_a_clock():
    body = _code_of(cont, "settlement_breakdown")
    for forbidden in ("outcomePrices", "elapsed", "empty_book",
                      "bid", "ask", "depth"):
        assert forbidden not in body, forbidden


def test_it_records_terminal_and_never_decides_it(monkeypatch):
    """It copies `status` from the audit; audit_market alone establishes it."""
    b = _breakdown(monkeypatch, [(True, "0.46", "8.58")],
                   status="PENDING_SETTLEMENT")
    assert b["settlement_status"] == "PENDING_SETTLEMENT"


def test_no_market_is_special_cased():
    body = _code_of(cont, "settlement_breakdown")
    for slug in ("jayfri", "county-not-ham", "cricket"):
        assert slug not in body, slug


def test_it_is_written_once_beside_the_venue_confirmed_rows():
    """`if written:` -- the authoritative rows are idempotent, so the
    breakdown rides their key and a repeat pass writes nothing."""
    body = _code_of(cont, "settle_held")
    i_w = body.index("written = auditor.append_authoritative_ledger(result)")
    i_b = body.index("if written:")
    assert i_w < i_b


# --------------------------------------------- the sweep that ran too rarely

def _sweep_body():
    src = open(cont.__file__).read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "run_campaign")
    return ast.unparse(fn)


def test_the_fixed_prefix_is_gone():
    assert "unsettled_slugs()[:5]" not in _sweep_body(), "the prefix is the bug"


def test_the_sweep_rotates():
    body = _sweep_body()
    assert "_settle_rr" in body and "SETTLE_AUDIT_BATCH" in body


def test_markets_the_risk_set_charges_but_the_scheduler_drops_go_first():
    """jayfri's exact shape: still risk-bearing, no longer held."""
    body = _sweep_body()
    assert body.index("_absent = [s for s in _uns if s not in set(held_all)]") \
        < body.index("_order = _absent + _rest")


def test_the_rotation_reaches_every_market():
    order = [f"m{i}" for i in range(13)]
    rr, seen = {"i": 0}, set()
    for _ in range(10):
        n = len(order)
        i = rr["i"] % n
        rr["i"] += cont.SETTLE_AUDIT_BATCH
        seen.update(order[(i + k) % n]
                    for k in range(min(cont.SETTLE_AUDIT_BATCH, n)))
    assert seen == set(order), sorted(set(order) - seen)


def test_one_pass_never_audits_the_same_market_twice():
    """Concatenating and slicing doubled the list when fewer markets were
    unsettled than the batch -- the live tests caught it auditing `gone`
    twice in a single pass."""
    for n in range(1, 8):
        order = [f"m{i}" for i in range(n)]
        i = 3 % n
        got = [order[(i + k) % n]
               for k in range(min(cont.SETTLE_AUDIT_BATCH, n))]
        assert len(got) == len(set(got)) == min(cont.SETTLE_AUDIT_BATCH, n)


def test_the_batch_and_interval_are_named():
    assert cont.SETTLE_AUDIT_BATCH == 5
    assert cont.SETTLE_AUDIT_EVERY_S == 60.0
