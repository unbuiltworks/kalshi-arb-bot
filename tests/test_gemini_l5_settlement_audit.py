"""Tests for the read-only settlement/cashflow audit classification."""
from __future__ import annotations

from decimal import Decimal as D

from src import gemini_l5_settlement_audit as aud


def _fill(outcome, qty, px, cid="c1"):
    return {"clientOrderId": cid, "eventOutcome": outcome,
            "filledQuantity": qty, "avgExecutionPrice": px}


def _settled(outcome, qty, cost, payout, net):
    return {"outcome": outcome, "positionQuantity": qty, "costBasis": cost,
            "payout": payout, "netProfit": net}


def test_matched_pair_is_not_settled_until_source_rows_exist():
    fills = [_fill("yes", "1", "0.48"), _fill("no", "1", "0.49", "c2")]
    # window still running, positions emptied (netting view): NOT settled
    r = aud.classify_event(fills, settled=[], open_positions=[],
                           window_over=False)
    assert r["status"] == "MATCHED_UNSETTLED" and r["matched"] is True
    # window over, no settled rows yet, no open position -> flagged
    r2 = aud.classify_event(fills, settled=[], open_positions=[],
                            window_over=True)
    assert r2["status"] == "UNRESOLVED"


def test_realized_only_with_consistent_settled_economics():
    fills = [_fill("yes", "1", "0.48"), _fill("no", "1", "0.49", "c2")]
    settled = [_settled("yes", "1", "0.48", "1", "0.52"),
               _settled("no", "1", "0.49", "0", "-0.49")]
    r = aud.classify_event(fills, settled, open_positions=[], window_over=True)
    assert r["status"] == "REALIZED"
    assert r["realized_cash"] == "0.03"          # payout 1 - cost 0.97
    assert r["binary_payout_consistent"] is True
    # non-binary payout -> SETTLED but not REALIZED
    bad = [_settled("yes", "1", "0.48", "0.5", "0.02")]
    r2 = aud.classify_event([_fill("yes", "1", "0.48")], bad, [], True)
    assert r2["status"] == "SETTLED" and r2["realized_cash"] is None


def test_positions_empty_never_implies_settlement():
    # one-sided fill, window over, open positions empty, no settled rows:
    # must be UNRESOLVED, never SETTLED/REALIZED
    r = aud.classify_event([_fill("no", "1", "0.50")], [], [], True)
    assert r["status"] == "UNRESOLVED"
    # with an open position still visible: pending, not settled
    r2 = aud.classify_event([_fill("no", "1", "0.50")], [],
                            [{"positionQuantity": "1"}], True)
    assert r2["status"] == "PENDING_SETTLEMENT"


def test_event_window_end_parsing():
    end = aud.event_window_end_epoch("BTC05M2609021210")
    from datetime import datetime, timezone
    assert end == datetime(2026, 9, 2, 12, 10,
                           tzinfo=timezone.utc).timestamp() + 300
    assert aud.event_window_end_epoch("NOTAWINDOW") is None
