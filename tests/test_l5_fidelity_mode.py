"""The fidelity mode, and the guarantee that makes it safe to land undeployed.

THE CENTRAL GUARANTEE. With `L5_FIDELITY_MODE` unset, every accessor returns
exactly the constant it returned before this module existed, EVEN WHEN the
environment asks for something wider. That is what makes the wiring landable
while the service stays on the pinned behaviour, and it is asserted knob by
knob below against literal expected values rather than against the code's own
constants, so a change to a constant cannot silently satisfy the test.

THE SECOND GUARANTEE. Turning the mode on raises CEILINGS, never DEFAULTS. An
unset knob is unchanged even in fidelity mode, so headroom must be asked for
explicitly and cannot be acquired by flipping one switch.
"""
import os

import pytest

from src import l5_fidelity_mode as FM
from src import l5_two_sided_probe as TS

# the values that were in force before the fidelity mode existed, written out
# literally on purpose. See the module docstring.
TODAY = {
    "entry_cutoff_s": 600.0,
    "complete_cutoff_s": 720.0,
    "opening_leg_max_contracts": "3",
    "order_ttl_s": 120.0,
    "concurrent_opening_orders": 1,
    "round_allocations_max": 12,        # 4 until 2026-11-11; see POLICY
}
WIDE_ENV = {
    "L5_TS_ENTRY_CUTOFF_S": "860",
    "L5_TS_COMPLETE_CUTOFF_S": "880",
    "L5_TS_OPENING_LEG_MAX_CONTRACTS": "9",
    "L5_TS_ORDER_TTL_S": "290",
    "L5_TS_ROUND_ALLOCATIONS": "11",
}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in list(WIDE_ENV) + [FM.FIDELITY_ENV]:
        monkeypatch.delenv(k, raising=False)


def _wide(monkeypatch):
    for k, v in WIDE_ENV.items():
        monkeypatch.setenv(k, v)


# --------------------------------------------- guarantee 1: off is unchanged

def test_mode_is_off_when_unset():
    assert FM.enabled() is False


@pytest.mark.parametrize("knob,expected", sorted(TODAY.items()))
def test_ceilings_off_match_the_pre_existing_constants(knob, expected):
    assert str(FM.ceiling(knob)) == str(expected)


def test_accessors_off_are_todays_values(monkeypatch):
    assert TS.entry_cutoff_s() == 600.0
    assert TS.complete_cutoff_s() == 720.0
    assert str(TS.opening_leg_max_contracts()) == "3"
    assert TS.order_ttl_s() == 120.0


def test_a_wide_environment_is_still_clamped_away_when_off(monkeypatch):
    """THE PARITY GUARANTEE. Wiring must not loosen anything by itself."""
    _wide(monkeypatch)
    assert TS.entry_cutoff_s() == 600.0
    assert TS.complete_cutoff_s() == 720.0
    assert str(TS.opening_leg_max_contracts()) == "3"
    assert TS.order_ttl_s() == 120.0
    # 11 was out of range until 2026-11-11, when the allocation ceiling
    # became 12 outside fidelity mode as well. A divisor only makes clips
    # SMALLER, so honouring it loosens nothing.
    assert TS.round_allocations() == 11


# ------------------------------------------------- fail closed on the token

@pytest.mark.parametrize("tok", ["on", "On", "1", "true", "TRUE", "yes", "Y",
                                 "", " ", "OFF", "ENABLED", "ON ", " ON"])
def test_only_the_exact_token_enables(monkeypatch, tok):
    monkeypatch.setenv(FM.FIDELITY_ENV, tok)
    if tok.strip() == "ON":
        assert FM.enabled() is True
    else:
        assert FM.enabled() is False, "a typo must not enable live widening"


def test_the_exact_token_enables(monkeypatch):
    monkeypatch.setenv(FM.FIDELITY_ENV, "ON")
    assert FM.enabled() is True


# ------------------------------------------- guarantee 2: ceilings not defaults

def test_mode_on_does_not_move_defaults(monkeypatch):
    """Headroom must be asked for, never acquired by one switch."""
    monkeypatch.setenv(FM.FIDELITY_ENV, "ON")
    assert TS.entry_cutoff_s() == 600.0
    assert TS.complete_cutoff_s() == 720.0
    assert str(TS.opening_leg_max_contracts()) == "3"
    assert TS.order_ttl_s() == 120.0


def test_mode_on_honours_a_configured_value_up_to_the_ceiling(monkeypatch):
    monkeypatch.setenv(FM.FIDELITY_ENV, "ON")
    _wide(monkeypatch)
    assert TS.entry_cutoff_s() == 840.0      # 860 asked, ceiling 840
    assert TS.complete_cutoff_s() == 870.0   # 880 asked, ceiling 870
    assert str(TS.opening_leg_max_contracts()) == "9"
    assert TS.order_ttl_s() == 290.0
    assert TS.round_allocations() == 11


def test_mode_on_still_clamps_beyond_the_fidelity_ceiling(monkeypatch):
    monkeypatch.setenv(FM.FIDELITY_ENV, "ON")
    monkeypatch.setenv("L5_TS_ENTRY_CUTOFF_S", "100000")
    monkeypatch.setenv("L5_TS_OPENING_LEG_MAX_CONTRACTS", "10000")
    assert TS.entry_cutoff_s() == 840.0
    assert str(TS.opening_leg_max_contracts()) == "10"


def test_unreadable_is_never_permission(monkeypatch):
    monkeypatch.setenv(FM.FIDELITY_ENV, "ON")
    monkeypatch.setenv("L5_TS_ENTRY_CUTOFF_S", "not-a-number")
    monkeypatch.setenv("L5_TS_OPENING_LEG_MAX_CONTRACTS", "banana")
    assert TS.entry_cutoff_s() == 600.0
    assert str(TS.opening_leg_max_contracts()) == "3"


def test_tightening_still_works_in_both_modes(monkeypatch):
    monkeypatch.setenv("L5_TS_ENTRY_CUTOFF_S", "300")
    assert TS.entry_cutoff_s() == 300.0
    monkeypatch.setenv(FM.FIDELITY_ENV, "ON")
    assert TS.entry_cutoff_s() == 300.0


# ------------------------------------------------- policy / safety separation

def test_policy_and_safety_are_disjoint():
    assert set(FM.POLICY) & set(FM.SAFETY) == set()


@pytest.mark.parametrize("ctrl", FM.SAFETY)
def test_no_safety_control_has_a_fidelity_ceiling(ctrl):
    with pytest.raises(KeyError):
        FM.ceiling(ctrl)


def test_an_unknown_knob_raises_rather_than_returning_none():
    with pytest.raises(KeyError):
        FM.ceiling("whatever")


@pytest.mark.parametrize("must", [
    "max_total_exposure_usd", "max_cash_deployed_usd", "max_abs_residual",
    "ABS_RESIDUAL_CAP_CONTRACTS", "daily_realized_loss_halt_usd",
    "max_worst_case_deficit_usd", "campaign_additional_loss_usd",
    "max_live_orders", "reconciliation", "exact_order_cancellation",
    "watchdog_deadman", "L5_LIVE_HALT", "halt_latch", "shadow_lock",
    "owner_arm_token", "completion_pair_ceiling",
])
def test_the_named_non_negotiables_are_listed_as_safety(must):
    """The owner named these explicitly; they must be in the safety list."""
    assert must in FM.SAFETY


def test_paying_above_redemption_is_safety_not_policy():
    """18% of August pairs cost over $1.00, and August lost $1,507 in 4 days.

    Paying more than redemption for a matched pair is a guaranteed loss by
    no-arbitrage, so it is not a behavioural choice the mode may widen.
    """
    assert "completion_pair_ceiling" in FM.SAFETY
    assert "completion_pair_ceiling" not in FM.POLICY
    from decimal import Decimal as D
    monkey = os.environ.get("L5_COMPLETION_PAIR_CEILING")
    assert TS.completion_pair_ceiling() <= D("1.00"), monkey


def test_the_residual_cap_is_not_a_policy_knob():
    """The owner said not to simply raise k; it is capital, not behaviour."""
    assert "max_abs_residual" in FM.SAFETY
    assert not any("residual" in k for k in FM.POLICY)


# ------------------------------------------------------------- the slot fix

def test_the_controller_slot_ceiling_is_one_when_off():
    """`resting >= 1` must be equivalent to the old `if resting:`."""
    assert int(FM.ceiling("concurrent_opening_orders")) == 1


def test_the_slot_ceiling_never_exceeds_authorized_live_orders(monkeypatch):
    """The fix uses an ALREADY AUTHORIZED slot; it raises no risk limit."""
    monkeypatch.setenv(FM.FIDELITY_ENV, "ON")
    from src import l5_pmus_live_runner as R
    monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "2")
    assert int(FM.ceiling("concurrent_opening_orders")) <= R.max_live_orders()


def test_the_runner_compares_against_the_ceiling_not_truthiness():
    import inspect
    from src import l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.maybe_controller_open)
    assert 'resting >= int(FID.ceiling("concurrent_opening_orders"))' in src
    assert "\n        if resting:\n" not in src


# ------------------------------------------------------------------ reporting

def test_describe_states_what_is_in_force(monkeypatch):
    d = FM.describe()
    assert d["enabled"] is False
    assert d["policy"]["entry_cutoff_s"]["in_force"] == "600.0"
    monkeypatch.setenv(FM.FIDELITY_ENV, "ON")
    d = FM.describe()
    assert d["enabled"] is True
    assert d["policy"]["entry_cutoff_s"]["in_force"] == "840.0"
    assert "max_total_exposure_usd" in d["safety_never_widened_here"]


def test_describe_arms_nothing():
    d = FM.describe()
    assert "token" not in str(d).lower() or "owner_arm_token" in str(d)
    assert d["label"] == "L5_FIDELITY_MODE"


# ------------------------------------------- the alias collision, regressed

def test_no_module_alias_is_bound_twice_in_the_runner():
    """`from src import l5_fidelity_mode as FM` SHADOWED l5_maker_fill_model.

    The runner already aliased `l5_maker_fill_model` to FM and used FM.UP,
    FM.BID, FM.ASK, FM.Ladder and FM.ModelSession. Re-binding the name broke
    58 tests, and it surfaced as `KeyError: ts_queue_view_error:AttributeError`
    from inside a swallow -- a second-order symptom naming neither the module
    nor the alias.

    MODULE LEVEL ONLY. A function-local import that deliberately shadows a
    module-level alias is an existing, intentional pattern here: TS is
    l5_two_sided_shadow at module scope and l5_two_sided_probe locally inside
    one method. That is its own hazard -- it nearly cost a wire/own price
    conversion earlier -- but failing on it would make this guard unusable.
    """
    import ast as _ast
    import collections as _c
    for path in ("src/l5_pmus_live_runner.py", "src/l5_two_sided_probe.py"):
        tree = _ast.parse(open(path).read())
        seen = _c.defaultdict(set)
        for node in tree.body:                      # top level, not walk()
            if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                for al in node.names:
                    if al.asname:
                        seen[al.asname].add(al.name)
        dupes = {a: sorted(m) for a, m in seen.items() if len(m) > 1}
        assert dupes == {}, "%s binds one alias to several modules: %r" % (
            path, dupes)


def test_the_fidelity_alias_is_not_FM():
    """FM is l5_maker_fill_model in both files. The fidelity module is FID."""
    for path in ("src/l5_pmus_live_runner.py", "src/l5_two_sided_probe.py"):
        src = open(path).read()
        assert "l5_fidelity_mode as FID" in src, path
        assert "l5_fidelity_mode as FM" not in src, path


def test_the_image_carries_the_fidelity_module():
    """The import is unconditional, so a missing COPY breaks the entrypoint.

    Omitting it failed every image-closure suite with ModuleNotFoundError.
    """
    for dockerfile in ("deploy/l5_pmus_live/Dockerfile",
                       "deploy/l5_live_probe/Dockerfile"):
        assert "COPY src/l5_fidelity_mode.py" in open(dockerfile).read(), (
            dockerfile)
