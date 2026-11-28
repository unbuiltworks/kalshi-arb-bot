"""A and B are separate behaviours, and only B is behind a flag.

  A  another pair may begin after a previous pair COMPLETED
  B  additional opening exposure while an unmatched leg REMAINS

These are distinct, and the owner wants A without B. The mechanism that
decides each is `probe_round_is_safe_to_retire`, and it already separates
them on the residual:

  residual == 0  -> None, the round retires, a new pair may begin    (A)
  residual != 0  -> LEG_HOLDS_UNMATCHED, the round is pinned         (B blocked)

So A needs NO flag: it is available in the deployed engine today.
`L5_TS_ROUND_RETIRE_WITH_RESIDUAL` changes ONLY the second line, which is
exactly B. These tests pin that separation so it cannot drift, and pin the
defect that stops the OTHER flag from being promotable.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_continuous_inventory as CI
from src import l5_live_execution as E
from tests import test_l5_live_execution as H

PLAN = {"pair_cost": "0.9500",
        "legs": [{"side": "UP", "price": "0.4700", "qty": "2.00",
                  "mode": "MAKER"},
                 {"side": "DOWN", "price": "0.4800", "qty": "2.00",
                  "mode": "MAKER"}]}


def ex_with(tmp_path, monkeypatch, retire):
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("L5_EXPERIMENT_ID", "AB-SEP")
    if retire:
        monkeypatch.setenv(E.ROUND_RETIRE_RESIDUAL_ENV, "YES")
    else:
        monkeypatch.delenv(E.ROUND_RETIRE_RESIDUAL_ENV, raising=False)
    st = H.store(tmp_path)
    ex = E.FaithfulExecutor(st, H.limits(), now_fn=H.NOW, maker_only=True)
    ex.probe_store_plan("m", PLAN, 0)
    return ex, st


# ===================================== A needs no flag
@pytest.mark.parametrize("retire", [False, True])
def test_A_a_completed_pair_always_frees_the_round(tmp_path, monkeypatch,
                                                   retire):
    """A is available in the DEPLOYED engine. The flag does not affect it."""
    ex, st = ex_with(tmp_path, monkeypatch, retire)
    st.apply_fill("m", "UP", D("2.00"), D("0.4700"), basis="OWN")
    st.apply_fill("m", "DOWN", D("2.00"), D("0.4800"), basis="OWN")
    assert st.residual("m") == D(0)
    assert ex.probe_round_is_safe_to_retire("m", 0) is None


def test_A_works_with_the_flag_UNSET_which_is_the_deployed_state(
        tmp_path, monkeypatch):
    ex, st = ex_with(tmp_path, monkeypatch, retire=False)
    assert E.round_retire_with_residual() is False
    st.apply_fill("m", "UP", D("2.00"), D("0.4700"), basis="OWN")
    st.apply_fill("m", "DOWN", D("2.00"), D("0.4800"), basis="OWN")
    ex.probe_retire_round("m", 0, "pair complete")
    assert ex.probe_round("m") == 1          # a new pair may begin


# ===================================== B is the flag, and only B
def test_B_is_blocked_with_the_flag_unset(tmp_path, monkeypatch):
    """The behaviour the owner does NOT want enabled automatically."""
    ex, st = ex_with(tmp_path, monkeypatch, retire=False)
    st.apply_fill("m", "DOWN", D("1.92"), D("0.3900"), basis="OWN")
    assert st.residual("m") != 0
    assert ex.probe_round_is_safe_to_retire("m", 0) == \
        "LEG_HOLDS_UNMATCHED:DOWN"
    assert ex.probe_round("m") == 0           # pinned, no added exposure


def test_B_is_what_the_flag_enables_and_nothing_else(tmp_path, monkeypatch):
    ex, st = ex_with(tmp_path, monkeypatch, retire=True)
    st.apply_fill("m", "DOWN", D("1.92"), D("0.3900"), basis="OWN")
    assert ex.probe_round_is_safe_to_retire("m", 0) is None
    ex.probe_retire_round("m", 0, "LEG_WOULD_CROSS")
    assert ex.probe_round("m") == 1           # B: exposure may be added


def flag(monkeypatch, on):
    """The accessor reads the environment at CALL time, so it must be set
    immediately before the call, not at construction."""
    if on:
        monkeypatch.setenv(E.ROUND_RETIRE_RESIDUAL_ENV, "YES")
    else:
        monkeypatch.delenv(E.ROUND_RETIRE_RESIDUAL_ENV, raising=False)


def test_the_flag_changes_only_the_imbalanced_branch(tmp_path, monkeypatch):
    """Proof of separation: identical on residual 0, different otherwise."""
    # ---- matched: the flag must make NO difference (behaviour A)
    a, sa = ex_with(tmp_path / "a", monkeypatch, retire=False)
    sa.apply_fill("m", "UP", D(1), D("0.47"), basis="OWN")
    sa.apply_fill("m", "DOWN", D(1), D("0.48"), basis="OWN")
    flag(monkeypatch, False)
    off_matched = a.probe_round_is_safe_to_retire("m", 0)
    flag(monkeypatch, True)
    on_matched = a.probe_round_is_safe_to_retire("m", 0)
    assert off_matched is None and on_matched is None

    # ---- imbalanced: the flag is the ONLY thing that changes (behaviour B)
    c, sc = ex_with(tmp_path / "c", monkeypatch, retire=False)
    sc.apply_fill("m", "UP", D(1), D("0.47"), basis="OWN")
    flag(monkeypatch, False)
    assert c.probe_round_is_safe_to_retire("m", 0) == \
        "LEG_HOLDS_UNMATCHED:UP"
    flag(monkeypatch, True)
    assert c.probe_round_is_safe_to_retire("m", 0) is None


# =============== the defect that stops the OTHER flag being promotable
def test_the_continuous_key_collides_on_a_second_pair_at_an_unchanged_book():
    """`L5_TS_CONTINUOUS_INVENTORY` keys identity on (market, side, price).
    A second pair wanted at the SAME book therefore produces the SAME key as
    the first and is refused DUPLICATE_SUPPRESSED -- it regresses A. So that
    flag is NOT promotable as written, and must stay unset."""
    first = CI.action_key("m", "UP", D("0.47"))
    second = CI.action_key("m", "UP", D("0.47"))
    assert first == second            # same identity => suppressed
    # it only separates them when the PRICE differs
    assert CI.action_key("m", "UP", D("0.48")) != first
