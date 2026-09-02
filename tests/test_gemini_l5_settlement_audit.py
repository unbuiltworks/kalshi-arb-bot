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


# ---------- mixed-event regression (AWS-observed probe #4 pattern) ----------

def _settled_with_meta(event, outcome, qty, cost, payout):
    return {"contractMetadata": {"eventTicker": event}, "outcome": outcome,
            "positionQuantity": qty, "costBasis": cost, "payout": payout,
            "netProfit": "0"}


def test_mixed_event_settled_rows_are_filtered_to_exact_target():
    target = "BTC05M2609021210"
    rows = [
        _settled_with_meta(target, "yes", "1", "0.48", "1"),
        _settled_with_meta(target, "no", "1", "0.49", "0"),
        # the AWS failure pattern: unrelated events in the same response
        _settled_with_meta("BTC05M2609020045", "yes", "3", "1.20", "0"),
        {"symbol": "GEMI-ETH05M2609020100-UP", "outcome": "yes",
         "positionQuantity": "2", "costBasis": "0.9", "payout": "2"},
    ]
    filt = aud.filter_settled_rows(rows, target)
    assert len(filt["exact"]) == 2
    assert filt["unrelated_dropped"] == 2
    assert filt["ambiguous"] == 0
    # classification over exact rows only: probe-4 economics realize cleanly
    fills = [_fill("yes", "1", "0.48"), _fill("no", "1", "0.49", "c2")]
    r = aud.classify_event(fills, filt["exact"], [], window_over=True)
    assert r["status"] == "REALIZED" and r["realized_cash"] == "0.03"


def test_ambiguous_settled_identity_fails_closed():
    target = "BTC05M2609021210"
    rows = [
        _settled_with_meta(target, "yes", "1", "0.48", "1"),
        {"outcome": "no", "positionQuantity": "1", "costBasis": "0.49",
         "payout": "0"},               # no identity anywhere -> ambiguous
    ]
    filt = aud.filter_settled_rows(rows, target)
    assert filt["ambiguous"] == 1 and len(filt["exact"]) == 1
    # audit() maps any ambiguity to AMBIGUOUS_SETTLEMENT_IDENTITY (fail
    # closed); at the pure level: never feed ambiguous rows to classify


def test_identity_resolution_precedence():
    # contractMetadata wins over top-level and symbol
    row = {"contractMetadata": {"eventTicker": "A"}, "eventTicker": "B",
           "symbol": "GEMI-C-UP"}
    assert aud._row_event_identity(row) == "A"
    assert aud._row_event_identity({"eventTicker": "B"}) == "B"
    assert aud._row_event_identity({"symbol": "GEMI-BTC05M2609021210-UP"}) \
        == "BTC05M2609021210"
    assert aud._row_event_identity({"outcome": "yes"}) == ""
