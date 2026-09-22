"""The reconciliation must not be able to move to meet the venue.

The accrual was stated in advance. These tests pin the things that would let it
drift: the fill list, the code path that prices it, the day-matching rule, and
the separation of accrued from credited.
"""
from src.gemini_execution import maker_rebate_reconcile as R


def test_the_accrual_reproduces_what_was_committed_in_advance():
    a = R.our_accrual()
    assert a["n_fills"] == 18
    assert a["n_eligible"] == 17            # the 0.19 fill is below the band
    assert a["qualifying_volume_usd"] == "6.67"
    assert a["accrued_usd"] == "0.08332590"
    assert a["would_pay_usd"] == "0.08"
    assert a["matches_committed"] is True
    assert a["drift_from_committed"] is None


def test_the_fill_list_is_the_evidence_packet_and_says_so():
    assert len(R.EP016_FILLS) == 18
    assert sum(1 for f in R.EP016_FILLS if float(f["price"]) < 0.20) == 1
    assert "EVIDENCE_PACKET_016" in R.our_accrual()["provenance"]


def test_a_payout_row_is_matched_on_its_own_date_not_its_position():
    rows = {"rows": [
        {"id": "wrong-day", "paid_at": "2026-09-20T21:00:00Z"},
        {"id": "right-day", "paid_at": "2026-09-21T21:00:00Z"},
    ]}
    assert R.pick_payout(rows, "2026-09-21")["id"] == "right-day"
    assert R.pick_payout(rows, "2026-09-19") is None


def test_created_at_is_accepted_when_a_row_is_not_paid_yet():
    rows = {"rows": [{"id": "pending", "created_at": "2026-09-21T21:00:00Z",
                      "paid_at": None}]}
    got = R.pick_payout(rows, "2026-09-21")
    assert got["id"] == "pending" and got["matched_on"] == "created_at"


def test_no_payout_row_is_not_the_same_as_a_zero_payout():
    from src.gemini_execution import maker_rebate_api as API
    out = API.reconcile(None, R.our_accrual())
    assert out["verdict"] == "NO_PAYOUT_ROW"
    assert out["matched"] is None
    assert "deltas" not in out


def test_a_pending_row_is_reconciled_but_never_credited():
    from src.gemini_execution import maker_rebate_api as API
    row = {"id": "p1", "status": "PENDING", "total_rebate_usd": "0.08",
           "total_fill_count": 17, "total_volume_usd": "6.67"}
    out = API.reconcile(row, R.our_accrual())
    assert out["verdict"] == "RECONCILED"
    assert out["credited"] is False


def test_all_three_deltas_are_reported_not_just_the_dollars():
    from src.gemini_execution import maker_rebate_api as API
    row = {"id": "p1", "status": "PAID", "total_rebate_usd": "0.08",
           "total_fill_count": 12, "total_volume_usd": "5.00"}
    out = API.reconcile(row, R.our_accrual())
    assert out["deltas"]["rebate_usd"] == "0"
    assert out["deltas"]["fill_count"] == -5
    assert out["deltas"]["volume_usd"] == "-1.67"
    assert out["matched"] is False          # dollars agreeing is not a match


def test_the_module_reaches_no_order_path():
    """CODE ONLY -- prose may mention orders, code may not call one. Comments
    and strings are stripped before the check, because the prose trap has
    caught this programme several times in the other direction."""
    import io
    import inspect
    import tokenize
    code = "".join(
        tok.string.lower() for tok in
        tokenize.generate_tokens(io.StringIO(inspect.getsource(R)).readline)
        if tok.type not in (tokenize.COMMENT, tokenize.STRING))
    for bad in ("place", "cancel", "neworder", "withdraw", "transfer"):
        assert bad not in code, bad
