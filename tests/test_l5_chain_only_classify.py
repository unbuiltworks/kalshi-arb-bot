"""Naming the cause of a `chain_only` transaction — gate G-C3.

`chain_only` is benign as publication lag and DANGEROUS as anything else: a
faster feed that reports fills the venue never published would build inventory
out of events that did not happen to us. Counting the set proves nothing, so
every transaction needs an evidenced cause and `unresolved` must be zero.

These tests pin the two ways this goes wrong quietly: a category assigned
without the evidence that defines it, and an unreadable source treated as a
benign answer.
"""
import pytest

from src.l5_chain_observer import classify as CL
from src.l5_chain_observer import observer as O

PAD = O.PAD
EX = O.EXCHANGES[0]
W = (1000.0, 1900.0)


def _boom(_tx):
    raise O.RpcError("TimeoutError")


def _kw(**over):
    base = dict(feed={}, window=W, receipt_fn=lambda t: {"logs": []},
                l5_topic=PAD, exchanges=O.EXCHANGES)
    base.update(over)
    return base


def _fill_receipt(price_usdc=45_600_000, size=80_000_000, token=7208, pos=2):
    topics = ["0x" + "0" * 64] * 4
    topics[0] = "0xd543adfd" + "0" * 56
    topics[pos] = PAD
    words = [0, token, price_usdc, size, 0, 0, 0]
    return {"logs": [{"topics": topics,
                      "data": "0x" + "".join(f"{w:064x}" for w in words),
                      "transactionHash": "0x1", "blockNumber": "0x10",
                      "logIndex": "0x0", "address": EX}]}


# ----------------------------------------------- the feed settles most cases

def test_a_transaction_the_feed_has_inside_the_window_is_publication_lag():
    r = CL.classify_one("0x1", **_kw(
        feed={"0x1": {"tx": "0x1", "ts": 1500.0}}))
    assert r["category"] == "publication_lag"
    assert r["evidence"]["feed_ts"] == 1500.0


def test_a_transaction_published_after_the_window_closed_is_a_window_edge():
    """The block range is a SUPERSET of the time window, by construction."""
    r = CL.classify_one("0x1", **_kw(feed={"0x1": {"ts": 1901.4}}))
    assert r["category"] == "window_edge"
    assert r["evidence"]["past_window_end_s"] == pytest.approx(1.4)


def test_a_transaction_published_before_the_window_opened_is_a_window_edge():
    r = CL.classify_one("0x1", **_kw(feed={"0x1": {"ts": 998.5}}))
    assert r["category"] == "window_edge"
    assert r["evidence"]["before_window_start_s"] == pytest.approx(1.5)


def test_a_feed_row_with_no_timestamp_cannot_be_placed_and_is_unresolved():
    r = CL.classify_one("0x1", **_kw(feed={"0x1": {"ts": None}}))
    assert r["category"] == "unresolved"


def test_the_feed_is_consulted_before_any_receipt_is_fetched():
    """Cheap and certain first; a found transaction needs no RPC at all."""
    r = CL.classify_one("0x1", **_kw(feed={"0x1": {"ts": 1500.0}},
                                     receipt_fn=_boom))
    assert r["category"] == "publication_lag"


# --------------------------------------------------- then the chain decides

def test_an_l5_log_with_no_decodable_fill_is_a_non_trade_transfer():
    r = CL.classify_one("0x1", **_kw(receipt_fn=lambda t: {"logs": []}))
    assert r["category"] == "non_trade_transfer"
    assert r["evidence"]["decoded_fill_legs"] == 0


def test_a_real_fill_outside_the_cohort_is_other_market():
    r = CL.classify_one("0x1", **_kw(
        receipt_fn=lambda t: _fill_receipt(token=7208), cohort=["999"]))
    assert r["category"] == "other_market"
    assert r["evidence"]["token_ids"] == ["7208"]


def test_a_real_fill_inside_the_cohort_that_the_feed_lacks_is_unresolved():
    """The case the gate exists to catch, and it must never be softened."""
    r = CL.classify_one("0x1", **_kw(
        receipt_fn=lambda t: _fill_receipt(token=7208), cohort=["7208"]))
    assert r["category"] == "unresolved"
    assert r["evidence"]["groups"][0]["price"] == "0.570000"


def test_a_real_fill_with_no_cohort_supplied_is_unresolved_not_assumed_benign():
    r = CL.classify_one("0x1", **_kw(receipt_fn=lambda t: _fill_receipt()))
    assert r["category"] == "unresolved"


# ------------------------------------------- unreadable is never benign

def test_an_rpc_failure_leaves_the_case_unresolved_rather_than_guessed():
    r = CL.classify_one("0x1", **_kw(receipt_fn=_boom))
    assert r["category"] == "unresolved"
    assert "RpcError" in r["evidence"]["error"]
    assert "UNKNOWN" in r["why"]


def test_a_transaction_older_than_the_feeds_reach_is_unresolved():
    """The endpoint returns a bounded number of recent fills. Absence beyond
    its horizon is not evidence of absence, and must not read as one."""
    r = CL.classify_one("0x1", **_kw(receipt_fn=_boom,
                                     feed_horizon_ts=2000.0, block_ts=1500.0))
    assert r["category"] == "unresolved"
    assert "could not be asked" in r["why"]


def test_a_transaction_inside_the_feeds_reach_is_still_examined():
    r = CL.classify_one("0x1", **_kw(feed_horizon_ts=1000.0, block_ts=1500.0))
    assert r["category"] == "non_trade_transfer"


# ------------------------------------------------------- the summary counts

def test_unresolved_is_a_category_but_not_an_explanation():
    s = CL.summarise([{"category": "publication_lag"},
                      {"category": "window_edge"},
                      {"category": "unresolved"}])
    assert s["n"] == 3 and s["classified"] == 2 and s["unresolved"] == 1
    assert s["all_classified"] is False


def test_a_category_outside_the_permitted_list_is_counted_separately():
    s = CL.summarise([{"category": "probably_fine"}])
    assert s["outside_permitted_categories"] == 1
    assert s["all_classified"] is False


def test_a_clean_summary_is_all_classified():
    s = CL.summarise([{"category": "publication_lag"},
                      {"category": "non_trade_transfer"}])
    assert s["all_classified"] is True and s["unresolved"] == 0


def test_summarising_nothing_is_not_a_pass():
    """Zero rows means the classifier did not run, not that it succeeded."""
    assert CL.summarise([])["all_classified"] is False


def test_every_category_produced_is_in_the_permitted_tuple():
    """A category invented in a branch would otherwise reach the gate."""
    import inspect
    src = inspect.getsource(CL.classify_one)
    for token in src.split('_c(tx, "')[1:]:
        assert token.split('"')[0] in CL.CATEGORIES, token[:40]


# ---------------------------------- a declined log is named, never just absent

def _transfer_batch_receipt():
    """The live shape: L5 at topic 2 of an ERC1155 TransferBatch. Topic 2 is
    `from`, so L5 is moving tokens out -- a redemption or merge, not a trade.
    Both live cases carried >200 logs."""
    topics = ["0x4a39dc06" + "0" * 56, "0x" + "0" * 64, PAD, "0x" + "0" * 64]
    return {"logs": [{"topics": topics, "data": "0x" + f"{0x80:064x}" * 8,
                      "transactionHash": "0x1", "blockNumber": "0x10",
                      "logIndex": "0x0",
                      "address": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"}]}


def test_the_live_case_that_a_garbage_decode_called_a_missing_fill():
    """Before the allowlist this decoded to a size of 2.5e70 and classified as
    `unresolved` -- the gate's most serious verdict, about a token transfer."""
    r = CL.classify_one("0x1", **_kw(
        receipt_fn=lambda t: _transfer_batch_receipt()))
    assert r["category"] == "non_trade_transfer"
    d = r["evidence"]["declined_logs"]
    assert len(d) == 1 and d[0]["event"] == "0x4a39dc06"
    assert "allowlist" in d[0]["reason"]


def test_an_unresolved_case_also_reports_what_was_declined():
    """So a real fill hiding behind an unlisted signature is visible."""
    r = CL.classify_one("0x1", **_kw(receipt_fn=lambda t: {
        "logs": _fill_receipt()["logs"] + _transfer_batch_receipt()["logs"]}))
    assert r["category"] == "unresolved"
    assert r["evidence"]["declined_logs"][0]["event"] == "0x4a39dc06"
