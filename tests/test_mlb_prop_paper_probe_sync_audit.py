from src.mlb_prop_paper_probe_sync_audit import classify_probe_sync, summarize


def test_accepts_literal_true_and_skew_at_or_below_500ms():
    assert classify_probe_sync({"synchronized_book_pair": True, "quote_receive_skew_ms": 0}) == (True, "SYNC_EVIDENCE_VALID")
    assert classify_probe_sync({"synchronized_book_pair": True, "quote_receive_skew_ms": "500"}) == (True, "SYNC_EVIDENCE_VALID")


def test_rejects_truthy_string_missing_nonfinite_negative_and_large_skew():
    assert classify_probe_sync({"synchronized_book_pair": "true", "quote_receive_skew_ms": 10})[0] is False
    assert classify_probe_sync({"synchronized_book_pair": True})[1] == "MISSING_OR_NONFINITE_QUOTE_RECEIVE_SKEW"
    assert classify_probe_sync({"synchronized_book_pair": True, "quote_receive_skew_ms": "NaN"})[0] is False
    assert classify_probe_sync({"synchronized_book_pair": True, "quote_receive_skew_ms": -1})[1] == "NEGATIVE_QUOTE_RECEIVE_SKEW"
    assert classify_probe_sync({"synchronized_book_pair": True, "quote_receive_skew_ms": 500.1})[1] == "QUOTE_RECEIVE_SKEW_EXCEEDS_500MS"


def test_summary_separates_invalid_groups_from_shadow_economics():
    out = summarize([
        {"trade_group_id": "ok", "synchronized_book_pair": True, "quote_receive_skew_ms": 25},
        {"trade_group_id": "bad", "synchronized_book_pair": True, "quote_receive_skew_ms": 900},
    ])
    assert out["paper_probe_groups_seen"] == 2
    assert out["synchronization_valid_groups"] == 1
    assert out["synchronization_invalid_groups"] == 1
    assert out["valid_trade_group_ids"] == ["ok"]
    assert out["invalid_trade_group_ids"] == ["bad"]
    assert out["all_groups_synchronization_valid"] is False
    assert out["pnl_capacity_policy"] == "EXCLUDE_INVALID_SYNC_GROUPS_FROM_EXECUTABLE_SHADOW_ECONOMICS"
    assert out["read_only"] is True
    assert out["order_entry"] is False
