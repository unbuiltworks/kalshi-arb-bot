"""The preflight's worst case must track the limit actually in force.

THE DISCREPANCY THE OWNER FLAGGED. `L5_LIVE_PREFLIGHT` reported
`max_committed_usd: 5` and `worst_case_total_usd: 5.1738` against a stated
$5 ceiling. Both numbers are correct and they measure different things:

  * the cash gate counts PRINCIPAL ONLY. `committed` is own_price * qty
    (l5_live_execution: `committed[market] += own * qty`),
    `pending_obligation` is qty * price, or qty for a resting short, and the
    proposed order is q * px. No term carries a fee.
  * the taker fee is therefore charged ON TOP of the ceiling.

So $5 bounds principal and the all-in worst case is larger. Nothing was
silently raised; the label was incomplete.

AND THE $5.1738 WAS ITSELF OPTIMISTIC. It assumed the fee peaks at p = 0.50,
which is true PER CONTRACT but not under a fixed principal: contracts = cap/p,
so fee = Theta*cap*(1-p), monotone DECREASING in p and maximised at the
CHEAPEST admissible price. On $5 of principal that is $0.3301 at the 0.05 band
floor, for an all-in worst case of $5.3301 -- the reported figure understated
it by 90%.

To hold the ALL-IN total at X the principal ceiling must be
X / (1 + Theta*(1 - floor_p)), which for X = $5 is $4.69.

Separately, the cap was HARDCODED, so a tightened ceiling would not have
shown up in the evidence a campaign is activated on. These tests pin all of it.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_fees as fees
from src.l5_pmus_transport import PmusTransport


class _Bare(PmusTransport):
    """Only `worst_case_from_venue` is under test; it touches no socket."""

    def __init__(self):            # noqa: D107 - deliberately no super()
        pass


def _wc(monkeypatch, env=None):
    if env is None:
        monkeypatch.delenv("L5_MAX_CASH_DEPLOYED_USD", raising=False)
    else:
        monkeypatch.setenv("L5_MAX_CASH_DEPLOYED_USD", env)
    return _Bare().worst_case_from_venue({"tickSize": "0.01"})


def test_the_corrected_default_worst_case(monkeypatch):
    out = _wc(monkeypatch)
    assert out["max_committed_usd"] == "5"
    assert out["max_committed_source"] == "default"
    # the cheapest admissible price, not 0.50
    assert out["fee_maximising_price"] == "0.05"
    assert out["max_taker_fee_usd"] == "0.3301"
    assert out["worst_case_total_usd"] == "5.3301"


def test_the_old_p_equals_half_figure_understated_the_fee(monkeypatch):
    """Pins the magnitude of the correction so it cannot silently regress."""
    out = _wc(monkeypatch)
    old_fee = fees.taker_fee(D("0.5"), D("5") / D("0.5"))
    assert old_fee == D("0.1738")
    assert D(out["max_taker_fee_usd"]) > old_fee
    assert D(out["max_taker_fee_usd"]) / old_fee > D("1.8")


def test_the_fee_is_declared_to_be_outside_the_cash_ceiling(monkeypatch):
    """The label, not the limit, was the problem."""
    out = _wc(monkeypatch)
    assert out["fee_is_outside_the_cash_ceiling"] is True
    assert "charged" in out["basis_fees"]


def test_a_tightened_ceiling_now_shows_up_in_the_evidence(monkeypatch):
    """Previously hardcoded: the preflight would have kept reporting $5."""
    out = _wc(monkeypatch, "4.69")
    assert out["max_committed_usd"] == "4.69"
    assert out["max_committed_source"] == "L5_MAX_CASH_DEPLOYED_USD"
    assert D(out["worst_case_total_usd"]) <= D("5")


@pytest.mark.parametrize("bad", ["", "abc", "0", "-1"])
def test_an_unusable_setting_falls_back_to_the_figure_it_always_reported(
        monkeypatch, bad):
    out = _wc(monkeypatch, bad)
    assert out["max_committed_usd"] == "5"
    assert out["worst_case_total_usd"] == "5.3301"


def test_the_all_in_principal_for_a_five_dollar_ceiling_is_4_69(monkeypatch):
    """X / (1 + Theta*(1-floor_p)) with X=5, Theta=0.0695, floor_p=0.05."""
    principal = (D(5) / (D(1) + D("0.0695") * (D(1) - D("0.05")))
                 ).quantize(D("0.01"))
    assert principal == D("4.69")
    out = _wc(monkeypatch, str(principal))
    assert D(out["worst_case_total_usd"]) <= D("5")
    # and the figure derived from the OLD, wrong maximum does not hold
    out_wrong = _wc(monkeypatch, "4.83")
    assert D(out_wrong["worst_case_total_usd"]) > D("5")


def test_the_fee_bound_is_taken_at_the_cheapest_price_under_fixed_principal(
        monkeypatch):
    """fee = Theta*cap*(1-p) is monotone decreasing in p, so no price above
    the floor can beat it."""
    out = _wc(monkeypatch)
    cap = D(out["max_committed_usd"])
    at_floor = D(out["max_taker_fee_usd"])
    for p in ("0.06", "0.1", "0.25", "0.5", "0.75", "0.95"):
        assert fees.taker_fee(D(p), cap / D(p)) <= at_floor, p
