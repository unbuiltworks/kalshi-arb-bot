"""Three economic outcomes, kept apart.

OWNER re-arm 2026-09-10: "For this run, I want every session to preserve
three separate economic outcomes: matched-pair P&L | directional residual
P&L | incentives/rebates. That will tell us whether the residual problem is
occasional noise or still structurally overwhelming the L5-like matched
trading."

WHY SEPARATION IS THE WHOLE POINT. On 2026-09-10 the settled cohort netted
+1.0986 across two one-sided residuals (+8.4456 and -7.3470) while every
matched pair in the same cohort contributed a derived few dollars on $57.48
of basis. A single blended number would have read as a modest win and hidden
both facts. Summing these three is the failure mode; these tests exist to
make the sum impossible to reach by accident.

The third stream is the venue's OWN credit -- commissionNotionalCollected on
whichever execution `isAggressor` says is ours -- never a modelled rebate,
and never the 0.0125 x qty x p(1-p) coefficient, which is a calibration and
not a receipt.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont
from src import pmus_settlement_auditor as auditor


def _ledger(tmp_path, monkeypatch, rows):
    import json
    # the executor the session actually uses is v4, whose `.base` is the
    # module that owns STATE_ENV; importing pmus_q1_executor directly gets a
    # different module with no `.base`
    from src import pmus_spring_l5_session as _b
    monkeypatch.setenv(_b.q1.base.STATE_ENV, str(tmp_path))
    (tmp_path / "ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))


def _fill(slug, side, px, qty, oid):
    return [{"kind": "submitted", "stage": "spring_open_1", "slug": slug,
             "order_id": oid, "side": side, "price": px, "qty": qty},
            {"kind": "order_terminal", "slug": slug, "order_id": oid,
             "filled_qty": qty, "price": px}]


def test_the_three_streams_are_reported_separately(tmp_path, monkeypatch):
    """A matched pair at 0.90, a venue realized number, and a credit."""
    rows = (_fill("m", "OUTCOME_SIDE_YES", "0.45", "10", "A")
            + _fill("m", "OUTCOME_SIDE_NO", "0.45", "10", "B"))
    _ledger(tmp_path, monkeypatch, rows)
    audit = {"realized_usd": "1.50", "trade_summary": {"trades": []},
             "resolution": {"side": "POSITION_RESOLUTION_SIDE_LONG"},
             "incentives": {"net_to_us_usd": "0.0351"}}
    out = cont.settlement_breakdown("m", audit)
    three = out["three_streams"]
    assert three["matched_pair_pnl_usd"] == "1.00"       # 10 x (1 - 0.90)
    assert three["directional_residual_pnl_usd"] == "0.50"   # 1.50 - 1.00
    assert three["incentives_usd"] == "0.0351"


def test_the_streams_are_never_pre_summed():
    """No total field, and no arithmetic combining the three."""
    import inspect
    src = inspect.getsource(cont.settlement_breakdown)
    i = src.index('"three_streams"')
    block = src[i:i + 900]
    for banned in ("total_usd", "combined_usd", "sum(", "net_economics"):
        assert banned not in block, banned
    assert "never summed" in block


def test_a_one_sided_residual_shows_the_whole_swing_in_ONE_stream(
        tmp_path, monkeypatch):
    """The 2026-09-10 shape: no matched pairs, so matched P&L is exactly 0
    and every cent of the outcome lands in the residual stream where it
    cannot be mistaken for trading skill."""
    _ledger(tmp_path, monkeypatch,
            _fill("w", "OUTCOME_SIDE_YES", "0.4358", "14.97", "A"))
    audit = {"realized_usd": "8.4456", "trade_summary": {"trades": []},
             "incentives": {"net_to_us_usd": "0"}}
    out = cont.settlement_breakdown("w", audit)
    assert out["three_streams"]["matched_pair_pnl_usd"] == "0"
    assert out["three_streams"]["directional_residual_pnl_usd"] == "8.4456"
    assert out["matched_pairs"] == "0"


def test_the_losing_twin_lands_in_the_same_stream(tmp_path, monkeypatch):
    """Same shape, opposite sign -- the point being that one stream carries
    both, so the distribution is visible instead of netted away."""
    _ledger(tmp_path, monkeypatch,
            _fill("l", "OUTCOME_SIDE_YES", "0.4399", "16.70", "A"))
    audit = {"realized_usd": "-7.3470", "trade_summary": {"trades": []},
             "incentives": {"net_to_us_usd": "0"}}
    out = cont.settlement_breakdown("l", audit)
    assert out["three_streams"]["directional_residual_pnl_usd"] == "-7.3470"


def test_an_unconfirmed_settlement_leaves_the_residual_stream_None(
        tmp_path, monkeypatch):
    """4 of 6 markets that day lacked the venue's realizedPnl. Unknown must
    stay None -- a 0 here would read as 'the residual cost us nothing'."""
    rows = (_fill("u", "OUTCOME_SIDE_YES", "0.45", "10", "A")
            + _fill("u", "OUTCOME_SIDE_NO", "0.45", "10", "B"))
    _ledger(tmp_path, monkeypatch, rows)
    out = cont.settlement_breakdown("u", {"realized_usd": None,
                                          "trade_summary": {"trades": []},
                                          "incentives": {"net_to_us_usd": "0"}})
    assert out["three_streams"]["directional_residual_pnl_usd"] is None
    assert out["three_streams"]["matched_pair_pnl_usd"] == "1.00"


def test_a_missing_incentive_read_is_None_not_zero(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch,
            _fill("x", "OUTCOME_SIDE_YES", "0.45", "10", "A"))
    out = cont.settlement_breakdown("x", {"realized_usd": "1.00",
                                          "trade_summary": {"trades": []}})
    assert out["three_streams"]["incentives_usd"] is None
    assert out["incentives_usd"] is None


# ------------------------------------------------- the credit is the venue's
def test_the_credit_comes_from_the_venues_own_commission_field():
    """Our side only, chosen by isAggressor: summing both sides conflates
    our maker rebate with the taker's fee and inverts the sign."""
    trades = [{"trade": {
        "id": "t1", "marketSlug": "m", "qtyDecimal": "10",
        "isAggressor": False,
        "passiveExecution": {"commissionNotionalCollected": {"value": "-0.05"}},
        "aggressorExecution": {"commissionNotionalCollected": {"value": "0.30"}},
    }}]
    out = auditor._credited_here(trades)
    assert out["credited_to_us_usd"] == "0.05"     # negative on the venue = ours
    assert out["charged_to_us_usd"] == "0"
    assert out["net_to_us_usd"] == "0.05"
    assert "our side only" in out["source"]


def test_no_commission_field_is_a_measured_zero_not_an_error():
    trades = [{"trade": {"id": "t", "marketSlug": "m", "qtyDecimal": "10",
                         "isAggressor": False}}]
    out = auditor._credited_here(trades)
    assert out["net_to_us_usd"] == "0"
    assert out["our_fills_with_a_credit"] == 0
    assert not out["source"].startswith("UNAVAILABLE")


def test_an_unreachable_helper_is_UNAVAILABLE_not_zero(monkeypatch):
    """'No credit' and 'could not read the credit' are different facts and
    only one of them is a measurement."""
    import src.pmus_incentive_audit as inc
    monkeypatch.setattr(inc, "credited_on_fills",
                        lambda rows: (_ for _ in ()).throw(OSError("net")))
    out = auditor._credited_here([])
    assert out["net_to_us_usd"] is None
    assert out["source"].startswith("UNAVAILABLE:OSError")


def test_the_auditor_attaches_incentives_to_every_market_result():
    """Wiring: a helper that exists but is never called changes nothing."""
    import ast
    import inspect
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(auditor)))
              if isinstance(n, ast.FunctionDef) and n.name == "audit_market")
    body = ast.unparse(fn)
    assert "'incentives': _credited_here(trades)" in body


def test_the_modelled_coefficient_is_not_the_source():
    """0.0125 x qty x p(1-p) is a CALIBRATION of past credits, not a
    receipt. It must never become the number a settlement reports."""
    import inspect
    for fn in (auditor._credited_here, cont.settlement_breakdown):
        src = inspect.getsource(fn)
        assert "0.0125" not in src
        assert "MAKER_REBATE_RATE" not in src
