"""MODEL ACTION vs EXECUTED SHADOW ACTION.

A safety overlay must never be readable as L5 behaviour. `plan()` already
distinguishes three reasons a rung does not go out, and the distinction is the
whole point:

    OSCILLATOR      the L5 surface itself withheld the rung   -> BEHAVIOUR
    ENVELOPE        MAX_IMBALANCE or a per-leg cap            -> SAFETY
    END_OF_WINDOW   the TTE 60/30 overlay                     -> SAFETY

What was missing is WHICH ACTION was withheld: a hold carried `action: HOLD`
and nothing said whether an EXTEND or a COMPLETE had been suppressed. These
pin that the label is attached, that it is a pure function of inventory, and
that it changes no decision.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP


def inv_with(yes="0", no="0"):
    i = CP.Inventory()
    if D(yes) > 0:
        i.apply_fill("YES", yes, "0.50", source="test")
    if D(no) > 0:
        i.apply_fill("NO", no, "0.50", source="test")
    return i


def test_classify_is_the_label_and_reads_only_inventory():
    flat = CP.Inventory()
    assert CP.classify("Up", flat) == CP.OPEN
    long_yes = inv_with(yes="0.01")
    # buying more YES while long YES WIDENS -> EXTEND
    assert CP.classify("Up", long_yes) == CP.EXTEND
    # buying NO while long YES REDUCES -> COMPLETE
    assert CP.classify("Down", long_yes) == CP.COMPLETE
    # balanced is an OPEN again, which is the oscillator continuing
    bal = inv_with(yes="0.01", no="0.01")
    assert CP.classify("Up", bal) == CP.OPEN
    assert CP.classify("Down", bal) == CP.OPEN


def test_the_envelope_names_WHICH_bound_it_was():
    """MAX_IMBALANCE and the per-leg caps are different constraints and must
    not be reported as one number."""
    at_cap = CP.Inventory()
    for _ in range(4):
        at_cap.apply_fill("YES", "0.01", "0.50", source="test")
    assert at_cap.imbalance() == CP.MAX_IMBALANCE
    why = CP.would_breach("YES", "0.50", at_cap)
    assert why is not None and "imbalance" in why
    # and the complement is still permitted: the cap bounds widening only
    assert CP.would_breach("NO", "0.50", at_cap) is None


def test_a_hold_carries_the_action_it_would_have_been():
    """The regression this exists to prevent: a report that says 'a rung was
    held' without saying an EXTEND was held."""
    import inspect
    src = inspect.getsource(CP.plan)
    assert src.count("would_be_action") == 3          # all three hold paths
    assert '"bound": ("MAX_IMBALANCE" if "imbalance" in breach' in src


def test_labelling_a_hold_changes_no_decision():
    """`classify` is consulted for held rungs purely to name them. It reads
    inventory and nothing else, so it cannot alter what is held."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(CP.classify)))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    # it may read inventory; it must not read price, book, tape or momentum
    for forbidden in ("price", "book", "prints", "spread", "momentum", "spot"):
        assert forbidden not in names, forbidden


def test_the_census_separates_behaviour_from_safety():
    """The four buckets must stay distinct in the collector."""
    import inspect

    from src.gemini_execution import shadow_soak as SS
    src = inspect.getsource(SS.one_window)
    for bucket in ("requested", "rested", "blocked", "policy_vetoed"):
        assert '"%s"' % bucket in src, bucket
    # an ENVELOPE hold is re-keyed with WHICH bound, never pooled
    assert 'ENVELOPE_%s' in src


def test_policy_vetoed_is_zero_under_the_faithful_policy():
    """GEMINI_RECYCLE's veto is the adaptation. Under FROZEN_L5_NATIVE the
    filter is not reached at all, so the bucket stays empty -- that is the
    A/B against gemini-econ-shadow."""
    import inspect

    from src.gemini_execution import shadow_soak as SS
    src = inspect.getsource(SS.one_window)
    assert "if policy == TR.POLICY_B and inv.residual() != 0:" in src
    # `vetoed` is only populated inside that branch
    before = src.split("if policy == TR.POLICY_B")[0]
    assert "vetoed = []" in before
    assert "vetoed = [a for a in wanted" not in before


def test_MAX_ACTIONS_is_inert_because_the_collector_never_records_one():
    """A3, corrected. The cap is real on the guard, but shadow_soak never
    calls guard.record(), so actions_taken stays 0 and plan() never sees the
    budget exhausted. If a future change starts recording actions, this test
    fails and the assumption has to be restated."""
    import inspect

    from src.gemini_execution import shadow_soak as SS
    src = inspect.getsource(SS)
    assert "guard.record(" not in src
    g = CP.WindowGuard("GEMI-BTC05M2609201900-UP", auth_id="T")
    assert g.actions_taken == 0


def test_a_fresh_guard_per_window_so_a_STOP_is_not_terminal_for_the_run():
    """WindowGuard.stop() is terminal BY DESIGN -- it exists so a one-window
    probe cannot roll forward. The collector therefore builds a new one per
    window, or the first window ending would end the whole run."""
    import inspect

    from src.gemini_execution import shadow_soak as SS
    assert "guard = CP.WindowGuard(" in inspect.getsource(SS.one_window)
