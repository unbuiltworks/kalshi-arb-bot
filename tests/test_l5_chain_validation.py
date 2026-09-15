"""The chain cross-validation gate, and the bug that faked a promotion blocker.

The dangerous failure here is not a slow answer, it is a CONFIDENT WRONG one:
a window-boundary mistake reported "the chain MISSED a fill the venue
published" when the chain had simply never been asked about that block. These
tests pin the containment property that makes `feed_only` mean what it says.
"""
import time

import pytest

import analysis.l5.chain_observer.validate as V
from src.l5_chain_observer import observer as O


class _Chain:
    """A fake chain: 1 block every 2 seconds from a known origin."""

    T0 = 1_000_000.0
    B0 = 100_000

    def ts(self, n):
        return self.T0 + (n - self.B0) * 2.0


def _patch(monkeypatch, chain=None):
    c = chain or _Chain()
    monkeypatch.setattr(V, "block_ts", c.ts)
    return c


def test_floor_never_lands_after_the_target(monkeypatch):
    """The exact bug: a floor bound above the target hides real fills below it."""
    c = _patch(monkeypatch)
    head = c.B0 + 5000
    for offset in range(0, 40):
        target = c.T0 + offset * 1.7          # deliberately off the block grid
        b = V.block_at(target, head, c.ts(head), side="floor")
        assert c.ts(b) <= target, (offset, b, c.ts(b), target)


def test_ceil_never_lands_before_the_target(monkeypatch):
    c = _patch(monkeypatch)
    head = c.B0 + 5000
    for offset in range(0, 40):
        target = c.T0 + offset * 1.7
        b = V.block_at(target, head, c.ts(head), side="ceil")
        assert c.ts(b) >= target, (offset, b, c.ts(b), target)


def test_the_block_range_contains_the_time_window(monkeypatch):
    """Containment is the property that makes feed_only trustworthy."""
    c = _patch(monkeypatch)
    head = c.B0 + 5000
    lo_ts, hi_ts = c.T0 + 1000.3, c.T0 + 1900.7
    lo = V.block_at(lo_ts, head, c.ts(head), side="floor")
    hi = V.block_at(hi_ts, head, c.ts(head), side="ceil")
    assert c.ts(lo) <= lo_ts and c.ts(hi) >= hi_ts
    assert lo < hi


def test_a_fill_one_block_below_the_window_is_not_reported_as_missed(monkeypatch):
    """The live case: tx at 13:51:42 with a window opening 13:51:41."""
    c = _patch(monkeypatch)
    head = c.B0 + 5000
    lo_ts = c.T0 + 1000.0
    lo = V.block_at(lo_ts, head, c.ts(head), side="floor")
    # a fill one block earlier than the first block at-or-after lo_ts must
    # still be inside the queried range
    naive_first_at_or_after = c.B0 + 501          # ts = T0 + 1002
    assert lo < naive_first_at_or_after


# ---------------------------------------------------- the asymmetry that matters

def test_chain_only_is_benign_and_does_not_block_promotion():
    chain = {"0xa": {"tx": "0xa", "block": 1}, "0xb": {"tx": "0xb", "block": 1}}
    feed = {"0xa": {"tx": "0xa", "ts": 50}}
    r = O.cross_validate(chain, feed, window=(0, 100))
    assert r["chain_only"] == ["0xb"] and r["feed_only"] == []
    assert r["promotion_blocker"] is None


def test_feed_only_blocks_promotion():
    chain = {"0xa": {"tx": "0xa", "block": 1}}
    feed = {"0xa": {"tx": "0xa", "ts": 50}, "0xz": {"tx": "0xz", "ts": 60}}
    r = O.cross_validate(chain, feed, window=(0, 100))
    assert r["feed_only"] == ["0xz"]
    assert "MISSED" in r["promotion_blocker"]


def test_an_unwindowed_comparison_is_never_a_promotion_pass():
    chain = {"0xa": {"tx": "0xa", "block": 1}}
    feed = {"0xa": {"tx": "0xa", "ts": 50}}
    r = O.cross_validate(chain, feed)
    assert r["feed_only"] == []
    assert "INDICATIVE only" in r["promotion_blocker"]


def test_a_feed_row_outside_the_window_is_not_counted_against_the_chain():
    chain = {"0xa": {"tx": "0xa", "block": 1}}
    feed = {"0xa": {"tx": "0xa", "ts": 50}, "0xlate": {"tx": "0xlate", "ts": 900}}
    r = O.cross_validate(chain, feed, window=(0, 100))
    assert r["feed_only"] == [] and r["promotion_blocker"] is None


# ------------------------------------------------- the metric that was bogus

def test_the_validator_does_not_claim_to_measure_lead():
    """Feed timestamp IS the block timestamp; differencing them is zero by
    construction and is not a lead."""
    out = V._leads({"0xa": {"block": 1}}, {"0xa": {"ts": 50}})
    assert out["measured"] is False
    assert "same" in out["why"].lower() or "block timestamp" in out["why"]


def test_the_observer_is_not_promoted():
    assert O.PROMOTED is False


# --------------------------------- a failed pass is not a dead process

def test_an_rpc_failure_anywhere_in_the_pass_is_a_failed_pass_not_a_crash(
        monkeypatch):
    """Observed in production 15:28:42Z: a TimeoutError inside block_at escaped
    one_pass and killed the process (returncode 1). The supervisor restarted it
    and recorded the gap, but a collector that dies on every network blip churns
    coverage holes into the record for nothing."""
    from src.l5_chain_observer import observer as O

    def boom(*_a, **_k):
        raise O.RpcError("TimeoutError: The read operation timed out")

    monkeypatch.setattr(V, "block_ts", boom)
    monkeypatch.setattr(O, "rpc", boom)
    r = V.one_pass()
    assert r["ok"] is False
    assert "TimeoutError" in r["err"]
    assert "NOT evidence" in r["labels"]


def test_a_failed_pass_is_never_mistaken_for_a_pass_that_found_nothing(
        monkeypatch):
    from src.l5_chain_observer import observer as O

    def boom(*_a, **_k):
        raise O.RpcError("boom")

    monkeypatch.setattr(V, "block_ts", boom)
    monkeypatch.setattr(O, "rpc", boom)
    r = V.one_pass()
    # no agreement figures at all -- an unrun pass reports nothing about them
    assert "agreement_rate" not in r and "feed_only" not in r


# ------------------------------------------ attribution: agreeing about WHAT

def _fill_log(*, price_usdc, size, token, pos=2, event="0xd543adfd", li=0):
    topics = ["0x" + "0" * 64] * 4
    topics[0] = event + "0" * 56
    topics[pos] = O.PAD
    words = [0, token, price_usdc, size, 0, 0, 0]
    return {"topics": topics,
            "data": "0x" + "".join(f"{w:064x}" for w in words),
            "transactionHash": "0x1", "blockNumber": "0x10",
            "logIndex": hex(li), "address": O.EXCHANGES[0]}


def _attr_env(monkeypatch, *, receipt, confirmations=100):
    head = 1000
    chain = {"0x1": {"tx": "0x1", "block": head - confirmations,
                     "contract": O.EXCHANGES[0], "topic_pos": 2}}
    monkeypatch.setattr(O, "rpc", lambda m, p, *a, **k: receipt)
    return chain, head


def test_attribution_matches_the_feed_aggregate_against_the_right_group(
        monkeypatch, tmp_path):
    """The two sources must agree about price and size, not merely about the
    transaction. Measured live: 30/30 exact, all on (0xd543adfd, L5@2)."""
    receipt = {"logs": [_fill_log(price_usdc=45_600_000, size=80_000_000,
                                  token=7208)]}
    chain, head = _attr_env(monkeypatch, receipt=receipt)
    feed = {"0x1": {"agg_price": "0.57", "agg_size": "80", "n_fills": 1,
                    "slug": "m"}}
    a = V._attribute(chain, feed, head=head, out_dir=str(tmp_path))
    assert a["n_attributed"] == 1
    assert a["price_agree_rate"] == 1.0 and a["size_agree_rate"] == 1.0
    assert a["mismatches"] == []


def test_a_transaction_below_the_confirmation_depth_is_not_attributed(
        monkeypatch, tmp_path):
    """Gate G-C5: an unconfirmed fill can still be reorged away, and counting
    it is the 'faster but wrong' failure in miniature."""
    receipt = {"logs": [_fill_log(price_usdc=45_600_000, size=80_000_000,
                                  token=7208)]}
    chain, head = _attr_env(monkeypatch, receipt=receipt,
                            confirmations=V.CONFIRMATIONS - 1)
    feed = {"0x1": {"agg_price": "0.57", "agg_size": "80"}}
    a = V._attribute(chain, feed, head=head, out_dir=str(tmp_path))
    assert a["n_shared"] == 1 and a["n_final"] == 0 and a["n_attributed"] == 0
    assert a["price_agree_rate"] is None


def test_a_genuine_disagreement_is_recorded_as_a_mismatch(monkeypatch,
                                                          tmp_path):
    receipt = {"logs": [_fill_log(price_usdc=45_600_000, size=80_000_000,
                                  token=7208)]}
    chain, head = _attr_env(monkeypatch, receipt=receipt)
    feed = {"0x1": {"agg_price": "0.57", "agg_size": "40"}}   # wrong size
    a = V._attribute(chain, feed, head=head, out_dir=str(tmp_path))
    assert a["price_agree"] == 0 and a["size_agree"] == 0
    assert len(a["mismatches"]) == 1
    assert a["mismatches"][0]["matched_group"] is None


def test_an_unreadable_receipt_is_not_counted_as_a_mismatch(monkeypatch,
                                                            tmp_path):
    def boom(*a, **k):
        raise O.RpcError("timeout")

    chain = {"0x1": {"tx": "0x1", "block": 900}}
    monkeypatch.setattr(O, "rpc", boom)
    feed = {"0x1": {"agg_price": "0.57", "agg_size": "80"}}
    a = V._attribute(chain, feed, head=1000, out_dir=str(tmp_path))
    assert a["n_final"] == 1 and a["n_attributed"] == 0
    assert a["mismatches"] == []


def test_attribution_rows_retain_their_provenance(monkeypatch, tmp_path):
    """Gate G-C8: the report must be reconstructible from the stored rows."""
    import json
    receipt = {"logs": [_fill_log(price_usdc=45_600_000, size=80_000_000,
                                  token=7208, li=3)]}
    chain, head = _attr_env(monkeypatch, receipt=receipt)
    feed = {"0x1": {"agg_price": "0.57", "agg_size": "80"}}
    V._attribute(chain, feed, head=head, out_dir=str(tmp_path))
    rows = [json.loads(l) for l in
            open(tmp_path / V.ATTR).read().splitlines() if l.strip()]
    assert len(rows) == 1
    r = rows[0]
    assert r["tx"] == "0x1" and r["blocks"] == [16] and r["log_indexes"] == [3]
    assert r["contract"] == O.EXCHANGES[0] and r["l5_topic_pos"] == 2
    assert r["confirmations"] == 100


def test_the_multi_fill_transaction_that_faked_a_disagreement(monkeypatch,
                                                              tmp_path):
    """L5 maker on one token, taker on the other, plus a restating event. The
    feed reports the taker leg; summing everything said 240 @ 0.35."""
    receipt = {"logs": [
        _fill_log(price_usdc=25_650_000, size=27_000_000, token=7208, pos=3,
                  li=1),
        _fill_log(price_usdc=50_350_000, size=53_000_000, token=7208, pos=3,
                  li=2),
        _fill_log(price_usdc=4_000_000, size=80_000_000, token=2007, li=3),
        _fill_log(price_usdc=4_000_000, size=80_000_000, token=2007, li=4,
                  event="0x174b3811")]}
    chain, head = _attr_env(monkeypatch, receipt=receipt)
    feed = {"0x1": {"agg_price": "0.05", "agg_size": "80", "n_fills": 1}}
    a = V._attribute(chain, feed, head=head, out_dir=str(tmp_path))
    assert a["price_agree"] == 1 and a["size_agree"] == 1


# ------------------------------------------ chain_only classification wiring

def test_chain_only_cases_are_classified_and_persisted(monkeypatch, tmp_path):
    import json
    chain = {"0xa": {"tx": "0xa", "block": 10},
             "0xb": {"tx": "0xb", "block": 11}}
    feed = {"0xa": {"ts": 1500.0}}                 # published, inside window
    monkeypatch.setattr(V, "block_ts", lambda b: 1600.0)
    monkeypatch.setattr(O, "rpc", lambda *a, **k: {"logs": []})
    out = V._classify_chain_only(["0xa", "0xb"], chain, feed,
                                 window=(1000.0, 1900.0),
                                 out_dir=str(tmp_path))
    assert out["n"] == 2 and out["unresolved"] == 0
    assert out["by_category"] == {"non_trade_transfer": 1,
                                  "publication_lag": 1}
    rows = [json.loads(l) for l in
            open(tmp_path / V.CLASSIFIED).read().splitlines() if l.strip()]
    assert {r["tx"] for r in rows} == {"0xa", "0xb"}
    assert all(r["why"] for r in rows)


def test_no_chain_only_cases_is_reported_as_such_not_as_a_silent_zero(tmp_path):
    out = V._classify_chain_only([], {}, {}, window=(0.0, 1.0),
                                 out_dir=str(tmp_path))
    assert out["n"] == 0 and out["all_classified"] is True
    assert "no chain_only" in out["note"]


def test_classification_sees_the_unwindowed_feed(monkeypatch, tmp_path):
    """A transaction the venue published OUTSIDE the compared window is a
    boundary artifact, and only the unwindowed feed can show that."""
    monkeypatch.setattr(V, "block_ts", lambda b: 1960.0)
    out = V._classify_chain_only(["0xa"], {"0xa": {"block": 10}},
                                 {"0xa": {"ts": 1950.0}},
                                 window=(1000.0, 1900.0),
                                 out_dir=str(tmp_path))
    assert out["by_category"] == {"window_edge": 1}


# ------------------------------------ the feed's reach bounds what can be asked

def _stub_pass(monkeypatch, feed, *, head=1_000_000, ts=lambda b: 0.0):
    monkeypatch.setattr(O, "activity_fills", lambda *a, **k: feed)
    monkeypatch.setattr(O, "rpc", lambda m, p, *a, **k: hex(head))
    monkeypatch.setattr(V, "block_ts", ts)
    monkeypatch.setattr(O, "chain_fills_checked", lambda a, b: (True, {}))


def test_a_window_beyond_the_feeds_reach_is_clamped_not_compared(monkeypatch):
    """The endpoint returns 500 rows whatever `limit` asks: in a burst its
    reach shrinks below the window, and every chain fill in the uncovered
    stretch would otherwise be counted as chain_only for no reason."""
    now = time.time()
    reach = now - V.SETTLE_MARGIN_S - 200.0        # covers only 200s of window
    _stub_pass(monkeypatch, {"0x1": {"ts": reach}},
               ts=lambda b: now - V.SETTLE_MARGIN_S)
    r = V.one_pass()
    assert r["ok"] is True
    assert r["window_clamped_to_feed_reach"] is True
    lo, hi = r["window_utc"]
    assert lo >= reach                            # never asks past the reach
    assert lo - reach == pytest.approx(V.FEED_EDGE_MARGIN_S, abs=0.01)


def test_a_window_inside_the_feeds_reach_is_not_clamped(monkeypatch):
    now = time.time()
    _stub_pass(monkeypatch, {"0x1": {"ts": now - 100_000.0}},
               ts=lambda b: now - V.SETTLE_MARGIN_S)
    r = V.one_pass()
    assert r["window_clamped_to_feed_reach"] is False
    lo, hi = r["window_utc"]
    assert hi - lo == pytest.approx(V.WINDOW_S, abs=0.01)


def test_a_feed_too_short_to_cover_any_settled_window_is_a_failed_pass(
        monkeypatch):
    """Not a pass that found nothing -- a pass that could not be made."""
    now = time.time()
    _stub_pass(monkeypatch, {"0x1": {"ts": now - 10.0}})
    r = V.one_pass()
    assert r["ok"] is False
    assert "reach" in r["err"]
    assert "agreement_rate" not in r


def test_a_feed_with_no_timestamps_is_a_failed_pass(monkeypatch):
    _stub_pass(monkeypatch, {"0x1": {"ts": None}})
    r = V.one_pass()
    assert r["ok"] is False and "timestamp" in r["err"]


def test_the_reach_is_reported_on_every_successful_pass(monkeypatch):
    """So a shrinking feed is visible before it starts distorting the counts."""
    now = time.time()
    _stub_pass(monkeypatch, {"0x1": {"ts": now - 3433.0}},
               ts=lambda b: now - V.SETTLE_MARGIN_S)
    r = V.one_pass()
    assert r["feed_reach_s"] == pytest.approx(3433.0, abs=1.0)


# ------------------------------- every row says which code measured it

def test_every_written_row_carries_the_measurement_epoch(monkeypatch,
                                                         tmp_path):
    """Without this the >=500 sample mixes in superseded measurements, and it
    did: rows reading 240 @ 0.35 for a fill the venue reported as 80 @ 0.05."""
    import json
    receipt = {"logs": [_fill_log(price_usdc=45_600_000, size=80_000_000,
                                  token=7208)]}
    chain, head = _attr_env(monkeypatch, receipt=receipt)
    V._attribute(chain, {"0x1": {"agg_price": "0.57", "agg_size": "80"}},
                 head=head, out_dir=str(tmp_path))
    row = json.loads(open(tmp_path / V.ATTR).read().splitlines()[0])
    assert row["epoch"] == V.EPOCH

    monkeypatch.setattr(V, "block_ts", lambda b: 1600.0)
    monkeypatch.setattr(O, "rpc", lambda *a, **k: {"logs": []})
    V._classify_chain_only(["0xb"], {"0xb": {"block": 1}}, {"0xa": {"ts": 1}},
                           window=(1000.0, 1900.0), out_dir=str(tmp_path))
    crow = json.loads(open(tmp_path / V.CLASSIFIED).read().splitlines()[0])
    assert crow["epoch"] == V.EPOCH


def test_a_failed_pass_is_stamped_too_so_it_can_be_excluded(monkeypatch):
    def boom(*a, **k):
        raise O.RpcError("boom")

    monkeypatch.setattr(O, "activity_fills", boom)
    r = V.one_pass()
    assert r["ok"] is False and r["epoch"] == V.EPOCH


def test_a_successful_pass_records_the_commit_that_measured_it(monkeypatch):
    now = time.time()
    _stub_pass(monkeypatch, {"0x1": {"ts": now - 100_000.0}},
               ts=lambda b: now - V.SETTLE_MARGIN_S)
    r = V.one_pass()
    assert r["epoch"] == V.EPOCH
    assert "code" in r          # None on an image without git metadata


def test_the_epoch_is_an_integer_that_only_moves_deliberately():
    """A bump invalidates the accumulated sample, so it is never incidental."""
    assert isinstance(V.EPOCH, int) and V.EPOCH >= 3
