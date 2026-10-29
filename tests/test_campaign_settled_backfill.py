"""The advance gate must ask the LEDGER, not a process-local accumulator.

A campaign is held open until every market it filled is settled. That was
read from `settled_here`, a set built by the RUNNING process, so a market
settled by an earlier process -- a redeploy, or the read-only settlement
sweep -- was invisible to it and the gate stalled forever on a market that
had in fact settled. With the gate stalled no owner approval can ever be
consumed, which is how a deliberate rung change becomes unreachable.

The live shape, from the R5F state saved 2026-09-11T14:42:40Z: 92 filled
markets, 80 in settled_here, and unsettled_slugs() EMPTY.
"""
from src.pmus_l5_continuous_session import ledger_settled_backfill


def test_backfills_markets_the_ledger_says_are_settled():
    filled = {"a", "b", "c"}
    settled_here = {"a"}
    assert ledger_settled_backfill(filled, settled_here, []) == {"b", "c"}


def test_never_backfills_a_market_the_ledger_still_calls_unsettled():
    """The whole safety point: a filled market with no settlement row is
    open risk and must keep the gate shut."""
    filled = {"a", "b"}
    assert ledger_settled_backfill(filled, set(), ["b"]) == {"a"}
    assert "b" not in ledger_settled_backfill(filled, set(), ["b"])


def test_returns_only_the_addition_never_the_existing_set():
    """The caller unions the result in; returning the whole set would hide
    how much the previous process failed to record."""
    assert ledger_settled_backfill({"a"}, {"a"}, []) == set()


def test_the_live_r5f_shape_unblocks_the_gate():
    """92 filled, 80 recorded, nothing unsettled -> the 12 missing markets
    are added and `filled - settled` becomes empty, which is exactly the
    condition _rung_gate tests before it may fire."""
    filled = {f"m{i}" for i in range(92)}
    settled_here = {f"m{i}" for i in range(80)}
    added = ledger_settled_backfill(filled, settled_here, [])
    assert len(added) == 12
    assert not (filled - (settled_here | added))


def test_a_slug_outside_the_campaign_is_never_invented():
    """Only markets THIS campaign filled may enter its settled set."""
    added = ledger_settled_backfill({"a"}, set(), [])
    assert added == {"a"}
    assert ledger_settled_backfill(set(), set(), []) == set()


def test_accepts_any_iterable_for_the_unsettled_answer():
    """unsettled_slugs() returns a list; the helper must not depend on it
    already being a set."""
    assert ledger_settled_backfill(["a", "b"], [], ("b",)) == {"a"}


def test_docstring_states_the_venue_reaudit_is_unchanged():
    """This helper is not permitted to read as a relaxation. The blocking
    check remains continuation_preflight's venue audit of every filled
    market; if that sentence ever leaves the source, the reviewer loses
    the only statement of what this does NOT decide."""
    doc = ledger_settled_backfill.__doc__ or ""
    assert "continuation_preflight" in doc
    assert "blocks" in doc
