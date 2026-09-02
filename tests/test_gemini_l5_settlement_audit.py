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
    # window over, no settled rows, no open position: for a MATCHED pair
    # this is venue-netting-consistent (docs: settled = held-to-resolution)
    # -> zero-residual-liability state, still NOT settled/realized
    r2 = aud.classify_event(fills, settled=[], open_positions=[],
                            window_over=True)
    assert r2["status"] == "MATCHED_PAIR_NO_SETTLED_ROWS"
    assert r2["residual_liability"] is False


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


# ---------- matched-pair netting reconciliation (venue-semantics fix) ----------

def _pair_fills():
    return [{"clientOrderId": "cid-y0", "eventOutcome": "yes",
             "filledQuantity": "1", "avgExecutionPrice": "0.47"},
            {"clientOrderId": "cid-n1", "eventOutcome": "no",
             "filledQuantity": "1", "avgExecutionPrice": "0.50"}]


def test_matched_pair_omitted_from_settled_is_not_unresolved():
    # probe #4 shape: matched q1 pair, window over, no settled rows, no
    # open positions -> venue-netting-consistent, zero residual liability
    r = aud.classify_event(_pair_fills(), settled=[], open_positions=[],
                           window_over=True)
    assert r["status"] == "MATCHED_PAIR_NO_SETTLED_ROWS"
    assert r["residual_liability"] is False
    # unmatched single fill in the same shape still halts loudly
    r2 = aud.classify_event([_pair_fills()[0]], [], [], True)
    assert r2["status"] == "UNRESOLVED" and r2["residual_liability"] is True


def test_confirmed_netting_requires_ws_fees_and_balance_delta():
    base = aud.classify_event(_pair_fills(), [], [], True)
    evidence = {
        "ws_fills": [
            {"clientOrderId": "cid-y0", "n": "0.01", "m": True,
             "price": "0.47", "quantity": "1"},
            {"clientOrderId": "cid-n1", "n": "0.01", "m": True,
             "price": "0.50", "quantity": "1"},
        ],
        # credit = 1*1 - 0.97 - 0.02 = 0.01 exactly
        "balance_updates": [{"c": "100.00"}, {"c": "100.01"}],
    }
    r = aud.apply_netting_evidence(base, _pair_fills(), evidence)
    assert r["status"] == "MATCHED_NETTED_CONFIRMED"
    assert r["netting_final"] is True
    assert r["netting_evidence"]["consistent"] is True
    assert r["netting_evidence"]["ws_fee_total"] == "0.02"


def test_balance_movement_alone_never_confirms():
    base = aud.classify_event(_pair_fills(), [], [], True)
    # only balance updates, no WS fill rows -> stays unconfirmed
    r = aud.apply_netting_evidence(base, _pair_fills(), {
        "ws_fills": [], "balance_updates": [{"c": "100"}, {"c": "100.03"}]})
    assert r["status"] == "MATCHED_NETTING_UNCONFIRMED"
    # and unconfirmed is never realized: no realized_cash key
    assert "realized_cash" not in r and "netted_cash_source_accounted" not in r


def test_inconsistent_cashflow_is_a_halt_condition():
    base = aud.classify_event(_pair_fills(), [], [], True)
    evidence = {
        "ws_fills": [
            {"clientOrderId": "cid-y0", "n": "0.01", "price": "0.47",
             "quantity": "1"},
            {"clientOrderId": "cid-n1", "n": "0.01", "price": "0.50",
             "quantity": "1"}],
        "balance_updates": [{"c": "100.00"}, {"c": "100.50"}],  # way off
    }
    r = aud.apply_netting_evidence(base, _pair_fills(), evidence)
    assert r["status"] == "NETTING_CASHFLOW_INCONSISTENT"


def test_netting_never_bypasses_identity_filter_or_ambiguity():
    # ambiguity still classifies fail-closed before any netting logic runs
    rows = [{"outcome": "yes", "positionQuantity": "1", "costBasis": "0.47",
             "payout": "1"}]
    filt = aud.filter_settled_rows(rows, "BTC05M2609021210")
    assert filt["ambiguous"] == 1
    # apply_netting_evidence refuses to touch non-matched-pair statuses
    realized = {"status": "REALIZED"}
    assert aud.apply_netting_evidence(realized, _pair_fills(), {}) == realized
