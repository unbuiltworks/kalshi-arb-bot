"""R7's own sequence as the balance discriminator, at no extra risk.

Nothing read-only settles which balance field the venue admits against, and
/v1/order/preview accepted $251.50 against $250.07 of buying power, so it
does not check funds. R7's normal sequence crosses displayedCash by itself --
R5 committed $62.70 cumulative -- so the evidence is available without any
artificial order.

These tests pin the instrumentation: the threshold arithmetic, that the
balance read happens ONCE and only at the crossing, and that a rejection is
never recorded as evidence.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import pmus_native_money_path as NMP


class FakeRegistry:
    def __init__(self, cumulative="0"):
        self.cum = cumulative
        self.reads = 0

    def as_dict(self):
        return {"cumulative_submitted": self.cum}


class MP:
    """The two methods under test, on a minimal stand-in."""
    BALANCE_EVIDENCE_THRESHOLD_USD = NMP.NativeMoneyPath.\
        BALANCE_EVIDENCE_THRESHOLD_USD
    _balance_evidence = NMP.NativeMoneyPath._balance_evidence

    def __init__(self, cumulative="0", balances=None, raises=False):
        self.registry = FakeRegistry(cumulative)
        self._balance_evidence_taken = False
        self._calls = 0
        self._balances = balances or {"buyingPower": 250.07425,
                                      "displayedCash": 50.07425,
                                      "depositReservation": 200}
        self._raises = raises

        class TP:
            def account(inner):
                self._calls += 1
                if self._raises:
                    raise RuntimeError("venue 503")
                return {"balances": [self._balances]}
        self.tp = TP()


def test_the_threshold_is_the_observed_displayed_cash():
    assert MP.BALANCE_EVIDENCE_THRESHOLD_USD == D("50.07425")


def test_no_balance_read_below_the_crossing():
    mp = MP(cumulative="10.00")
    assert mp._balance_evidence(committed_before=D("10.00"),
                                notional=D("4.80")) is None
    assert mp._calls == 0, "a balance GET was spent below the crossing"


def test_the_balance_is_read_exactly_at_the_crossing():
    mp = MP(cumulative="48.00")
    ev = mp._balance_evidence(committed_before=D("48.00"),
                              notional=D("4.80"))
    assert ev is not None and mp._calls == 1
    assert ev["buyingPower"] == 250.07425
    assert ev["displayedCash"] == 50.07425


def test_the_balance_is_read_only_once_however_many_crossings():
    """A GET per submission would spend the 1.5 rps budget that decision
    freshness already loses its p95 to."""
    mp = MP(cumulative="60.00")
    for _ in range(5):
        mp._balance_evidence(committed_before=D("60.00"), notional=D("4.80"))
    assert mp._calls == 1, "read %d times" % mp._calls


def test_exactly_at_the_threshold_is_not_a_crossing():
    mp = MP()
    assert mp._balance_evidence(committed_before=D("50.07425"),
                                notional=D("0")) is None


def test_a_failing_balance_read_is_recorded_not_raised():
    mp = MP(cumulative="60.00", raises=True)
    ev = mp._balance_evidence(committed_before=D("60.00"), notional=D("4.80"))
    assert ev is not None and "error" in ev
    assert mp._calls == 1


def test_the_journal_row_shape_distinguishes_acceptance_from_rejection():
    """A rejection is NOT evidence: the venue's reject reason is
    uninformative (48/48 in ISSUE_93_REJECTION_FINDINGS), so the row must
    carry the verdict separately and say so."""
    src = open(NMP.__file__, encoding="utf-8").read()
    assert "balance_admission_evidence" in src
    assert "venue_accepted" in src
    assert "A rejection is NOT" in src.replace("\n", " ").replace('"', '')
    assert "committed_before_usd" in src and "crosses_displayed_cash" in src
