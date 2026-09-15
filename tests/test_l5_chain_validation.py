"""The chain cross-validation gate, and the bug that faked a promotion blocker.

The dangerous failure here is not a slow answer, it is a CONFIDENT WRONG one:
a window-boundary mistake reported "the chain MISSED a fill the venue
published" when the chain had simply never been asked about that block. These
tests pin the containment property that makes `feed_only` mean what it says.
"""
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
