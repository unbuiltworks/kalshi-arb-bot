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


def test_a_payout_carries_the_date_it_RAN_not_the_date_it_pays_for():
    """The first run returned NO_PAYOUT_ROW because it looked for a row dated
    on the fills' ET day. Payouts run at 5pm ET, so ET day D is settled by the
    run on D+1 -- the real row for our 2026-09-21 fills is dated 2026-09-22."""
    assert R.payout_date_for("2026-09-21") == "2026-09-22"
    rows = {"rows": [
        {"id": "ran-on-the-fill-day", "paid_at": "2026-09-21T21:00:00Z"},
        {"id": "the-one-that-pays-for-it", "paid_at": "2026-09-22T21:03:06Z"},
    ]}
    got = R.pick_payout(rows, "2026-09-21")
    assert got["id"] == "the-one-that-pays-for-it"
    assert got["covers_et_day"] == "2026-09-21"
    assert got["matched_date"] == "2026-09-22"


def test_the_mapping_can_be_overridden_without_touching_the_accrual():
    rows = {"rows": [{"id": "x", "paid_at": "2026-09-25T21:00:00Z"}]}
    assert R.pick_payout(rows, "2026-09-21") is None
    assert R.pick_payout(rows, "2026-09-21", "2026-09-25")["id"] == "x"


def test_created_at_is_accepted_when_a_row_is_not_paid_yet():
    rows = {"rows": [{"id": "pending", "created_at": "2026-09-22T21:00:00Z",
                      "paid_at": None}]}
    got = R.pick_payout(rows, "2026-09-21")
    assert got["id"] == "pending" and got["matched_on"] == "created_at"


def test_the_real_2026_09_21_row_disagrees_on_all_three_counts():
    """The venue's own row 21320, verbatim from the gate artifact. The dollars
    look close; the composition does not, which is why three deltas are
    reported and not one."""
    from src.gemini_execution import maker_rebate_api as API
    row = {"id": 21320, "status": "PAID",
           "paid_at": "2026-09-22T21:03:06.090688Z",
           "total_rebate_usd": "0.05", "total_fill_count": 11,
           "total_volume_usd": "3.95"}
    out = API.reconcile(row, R.our_accrual())
    assert out["verdict"] == "DISAGREES"
    assert out["credited"] is True
    assert out["deltas"] == {"rebate_usd": "-0.03", "fill_count": -6,
                             "volume_usd": "-2.72"}


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
