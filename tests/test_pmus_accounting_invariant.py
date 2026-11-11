"""Every fill-derived dollar stays in exactly ONE state until the venue closes it.

The invariant combined_loss documents for itself is: "a market that settles
leaves the HELD set and its result enters realized_today in the same
transition. Nothing is in both." The transition is implemented by two functions
in two modules, and they disagreed about what "settles" means -- the auditor
writes `settled` always and `realized` only when the venue confirmed a number,
while realized_today() sums only `realized`. A settlement the venue would not
put a number on therefore left BOTH terms and was in NEITHER.

These tests pin the corrected contract from both ends.
"""
import json

import pytest

from src import pmus_l5_continuous_session as cont


def _ledger(tmp_path, rows):
    p = tmp_path / "ledger.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cont.base.q1.base, "state_dir", lambda: tmp_path)
    return tmp_path


FILL = {"kind": "filled", "stage": "spring_open", "slug": "m1"}


def test_a_bare_settled_row_does_not_close_accounting(state_dir):
    """THE BUG. The auditor writes `settled` unconditionally; only `realized`
    carries a venue-confirmed number. Closing on `settled` dropped the market's
    floor from combined_loss while realized_today() gained nothing."""
    _ledger(state_dir, [FILL, {"kind": "settled", "slug": "m1"}])
    assert cont.unsettled_slugs() == ["m1"], (
        "a settlement with no venue number must NOT close the market")
    assert cont.unsettled_detail() == {"m1": "SETTLED_REALIZED_UNCONFIRMED"}


def test_a_realized_row_does_close_accounting(state_dir):
    """The venue put a number on it, so realized_today() now carries the
    dollars and holding the floor too would double-count."""
    _ledger(state_dir, [FILL, {"kind": "settled", "slug": "m1"},
                        {"kind": "realized", "slug": "m1",
                         "realized_usd": "-1.25"}])
    assert cont.unsettled_slugs() == []


def test_a_confirmed_zero_still_closes(state_dir):
    """Zero is a venue-confirmed number. Closing must key on the ROW, not on
    the value being truthy."""
    _ledger(state_dir, [FILL, {"kind": "realized", "slug": "m1",
                               "realized_usd": "0"}])
    assert cont.unsettled_slugs() == []


def test_never_settled_is_distinguished_from_settled_unconfirmed(state_dir):
    """Both stay charged, but an operator must be able to tell a market the
    venue never resolved from one it resolved without a number."""
    _ledger(state_dir, [
        dict(FILL, slug="never"), dict(FILL, slug="unconf"),
        {"kind": "settled", "slug": "unconf"},
    ])
    assert set(cont.unsettled_slugs()) == {"never", "unconf"}
    assert cont.unsettled_detail() == {
        "never": "NEVER_SETTLED", "unconf": "SETTLED_REALIZED_UNCONFIRMED"}


def test_flat_or_matched_does_not_close_accounting(state_dir):
    """A market being flat, matched, or operationally complete is NOT a
    terminal accounting state -- that conflation is what let nicpar-gabpen
    stop being polled while its economics were still open."""
    _ledger(state_dir, [FILL,
                        {"kind": "session_end", "slug": "m1",
                         "status": "SPRING_MATCHED", "flat_verified": True,
                         "inventory": {"up": "2.43", "dn": "2.43",
                                       "residual": "0.00"}}])
    assert cont.unsettled_slugs() == ["m1"]


def test_the_settlement_sweep_keeps_polling_an_unconfirmed_market(state_dir):
    """The sweep reads THIS definition, so restoring it restores polling --
    which is why nicpar-gabpen was audited once and never again."""
    from src import pmus_settlement_sweep as sweep
    _ledger(state_dir, [FILL, {"kind": "settled", "slug": "m1"}])
    assert sweep.pending_slugs() == ["m1"]


def test_order_terminal_fills_are_counted_too(state_dir):
    """A fill that only ever appeared as order_terminal still creates an
    accounting obligation."""
    _ledger(state_dir, [{"kind": "order_terminal", "slug": "m2",
                         "filled_qty": "3.5"}])
    assert cont.unsettled_slugs() == ["m2"]
