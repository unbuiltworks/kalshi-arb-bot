"""The contract family is a PARAMETER, not a second copy of the pipeline."""
import pytest

from src.gemini_execution import contract_cohort as CC
from src.gemini_execution import metadata as MD


def test_the_families_we_run():
    assert CC.BTC5M.prefix == "BTC05M" and CC.BTC5M.minutes == 5
    assert CC.BTC15M.prefix == "BTC15M" and CC.BTC15M.minutes == 15
    assert CC.DEFAULT is CC.BTC5M


def test_an_unknown_cohort_RAISES_rather_than_falling_back():
    """Binding a loop to the wrong contract family because of a typo is the
    kind of quiet substitution this codebase refuses everywhere else."""
    with pytest.raises(ValueError):
        CC.resolve("btc7m")
    assert CC.resolve(None) is CC.BTC5M
    assert CC.resolve("btc15m") is CC.BTC15M
    assert CC.resolve("BTC15M") is CC.BTC15M


def test_a_symbol_says_which_family_it_belongs_to():
    assert CC.of_symbol("GEMI-BTC15M2609191445-UP").name == "BTC15m"
    assert CC.of_symbol("GEMI-BTC05M2609191435-UP").name == "BTC5m"
    assert CC.of_symbol("nonsense") is None


def test_the_window_length_is_the_entry_ceiling():
    assert CC.BTC5M.max_entry_tte_s == 300.0
    assert CC.BTC15M.max_entry_tte_s == 900.0


def test_THE_FROZEN_TTE_BOUNDS_ARE_NOT_SCALED_PER_FAMILY():
    """They are ABSOLUTE safety margins from expiry, not fractions of a
    window. Scaling them would be changing Model A, and Model A is frozen."""
    from src.gemini_execution import continuous_probe as CP
    src = open("src/gemini_execution/contract_cohort.py").read()
    assert "NO_NEW_OPEN_EXTEND_TTE_S" not in src.split('"""')[2]
    assert CP.NO_NEW_OPEN_EXTEND_TTE_S == 60.0
    assert CP.WITHDRAW_ALL_TTE_S == 30.0
    for c in (CC.BTC5M, CC.BTC15M):
        assert not hasattr(c, "withdraw_tte_s")
        assert not hasattr(c, "complete_only_tte_s")


def _widest_band(cohort):
    """The quoting window of the widest arm this cohort actually runs."""
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import shadow_soak as SS
    specs = [CP.probe_params()]
    corr = SS.correction_spec(cohort)
    if corr is not None:
        specs.append(corr)
    bands = [s.get("TTE_policy")["quote_between_tte_s"] for s in specs]
    return min(float(b[0]) for b in bands), max(float(b[1]) for b in bands)


def test_the_band_is_set_by_the_QUOTING_WINDOW_not_by_symmetry():
    """This test previously asserted the band sat within 60s of the window's
    OPEN -- symmetry with the 5-minute family. That was the wrong property.

    It is still the TTE the policy will speak at that matters, but "the
    policy" is now plural: BTC15m runs a corrected arm quoting [0, 900]
    beside the frozen [0, 300]. The band has to sit inside the WIDEST arm,
    because the widest arm is the one that has to be live from the first
    cycle -- a narrower arm withholding early is its decision, recorded, and
    not a lane that cannot trade."""
    for c in (CC.BTC5M, CC.BTC15M):
        lo, hi = _widest_band(c)
        assert lo <= c.entry_band[0] <= c.entry_band[1] <= hi, c.name


def test_the_ticker_grid_is_shared_and_only_the_step_differs():
    at = 1789827300.0
    five = MD.current_ticker(at, prefix="BTC05M", minutes=5)
    fifteen = MD.current_ticker(at, prefix="BTC15M", minutes=15)
    assert five.startswith("BTC05M") and fifteen.startswith("BTC15M")
    assert len(five) == len(fifteen)
    # the 15-minute window ends at or after the 5-minute one containing it
    assert fifteen[-10:] >= five[-10:]


def test_the_five_minute_path_is_UNCHANGED_by_the_refactor():
    """current_btc5m_ticker must still answer exactly what it always did."""
    for at in (1789827300.0, 1789827301.0, 1789827599.0, 1789827600.0):
        assert MD.current_btc5m_ticker(at) == MD.current_ticker(
            at, prefix="BTC05M", minutes=5)


def test_siblings_step_by_the_familys_own_grid():
    assert MD.sibling_ticker("BTC15M2609191445", 15) == "BTC15M2609191430"
    assert MD.sibling_ticker("BTC05M2609191445", 5) == "BTC05M2609191440"


def test_THE_ENTRY_WAIT_BUDGET_SCALES_WITH_THE_WINDOW():
    """Hard-coding the 5-minute family's 420s meant a 15-minute run timed out
    before its band could ever open -- caught on the first live attempt."""
    from src.gemini_execution import run_continuous as RC
    assert CC.BTC5M.max_entry_wait_s == RC.MAX_ENTRY_WAIT_S
    assert CC.BTC15M.max_entry_wait_s > CC.BTC15M.window_s
    for c in (CC.BTC5M, CC.BTC15M):
        worst = (c.entry_band[0] - 0.01) + (c.window_s - c.entry_band[1])
        assert worst < c.max_entry_wait_s, (c.name, worst)


# ============ the continuous rollover, and that it cannot reach a LIVE run

def test_THE_SHADOW_ROLLOVER_RELAXATION_IS_NARROWLY_SHADOW_ONLY():
    """The gate stops a new window opening while the last may still have live
    orders. In SHADOW no session is ever constructed, so that hazard cannot
    exist and the refusal is recorded rather than ending the collector. In
    LIVE the stop must be untouched."""
    import ast
    src = open("src/gemini_execution/shadow_soak.py").read()
    tree = ast.parse(src)
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        cond = ast.unparse(node.test)
        if "runner.mode" in cond and "SHADOW" in cond:
            body = ast.unparse(node)
            if "rolled_on_shadow = True" in body and "runner.stop" in body:
                guarded = True
    assert guarded, "the relaxation must sit inside an explicit SHADOW guard"


def test_the_soak_still_constructs_no_session_and_no_mutation_verb():
    """What makes the relaxation safe is what this module CAN reach."""
    import ast
    tree = ast.parse(open("src/gemini_execution/shadow_soak.py").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("place", "cancel", "submit"), node.attr


def test_windows_zero_means_roll_continuously():
    src = open("src/gemini_execution/shadow_soak.py").read()
    assert "forever = (a.windows == 0)" in src
    assert "if not forever and not adm[\"admits\"]:" in src


def test_THE_ONE_FAMILY_BINDING_IS_PARAMETERISED_NOT_WEAKENED():
    """A guard still accepts exactly one contract family and refuses every
    other symbol. What changed is that the family is named rather than
    assumed, so the 15-minute lane binds as tightly as the 5-minute one."""
    from src.gemini_execution import continuous_probe as CP
    g = CP.WindowGuard("GEMI-BTC15M2609191500-UP", auth_id="SHADOW",
                       cohort_prefix="GEMI-BTC15M")
    assert g.cohort_prefix == "GEMI-BTC15M"
    # the standing default is byte-for-byte the old behaviour
    assert CP.WindowGuard("GEMI-BTC05M2609191500-UP",
                          auth_id="SHADOW").cohort_prefix == CP.COHORT_PREFIX

    # a symbol outside the bound family is refused
    with pytest.raises(CP.ProbeStop):
        CP.WindowGuard("GEMI-BTC05M2609191500-UP", auth_id="SHADOW",
                       cohort_prefix="GEMI-BTC15M")
    # a family not on the known list is refused, so this cannot bind to an
    # arbitrary symbol
    with pytest.raises(CP.ProbeStop):
        CP.WindowGuard("GEMI-SOL05M2609191500-UP", auth_id="SHADOW",
                       cohort_prefix="GEMI-SOL05M")
    with pytest.raises(CP.ProbeStop):
        CP.WindowGuard("GEMI-ETH05M2609191500-UP", auth_id="SHADOW")


def test_every_cohort_we_run_is_a_family_the_guard_will_bind():
    from src.gemini_execution import continuous_probe as CP
    for c in (CC.BTC5M, CC.BTC15M):
        assert "GEMI-%s" % c.prefix in CP.COHORT_PREFIXES


def test_EVERY_ENTRY_BAND_SITS_INSIDE_A_QUOTING_WINDOW_THAT_IS_RUN():
    """A band outside every arm's quoting window is a band that cannot trade.

    The check is against the arms the cohort RUNS, so adding an arm can widen
    what is admissible and removing one narrows it back -- a band left behind
    by a retired arm fails here rather than quietly going silent.
    """
    for c in (CC.BTC5M, CC.BTC15M):
        lo, hi = _widest_band(c)
        assert lo <= c.entry_band[0] <= hi, c.name
        assert lo <= c.entry_band[1] <= hi, c.name


def test_THE_FAMILIES_NO_LONGER_ENTER_AT_THE_SAME_TTE_AND_THAT_IS_THE_POINT():
    """They entered at the same TTE because both were bound to [0, 300].

    BTC15m now enters at the top of its own window so the corrected arm has
    an interval to be measured over at all. Entering both at 240-280 would
    leave the correction unobservable by construction, and its silence would
    read as a finding about the correction rather than about the schedule.
    """
    assert CC.BTC5M.entry_band == (240.0, 280.0)
    assert CC.BTC15M.entry_band == (840.0, 880.0)
    # each still enters inside its own instrument, with room to work
    for c in (CC.BTC5M, CC.BTC15M):
        assert c.entry_band[1] <= c.max_entry_tte_s, c.name
        assert c.entry_band[0] > 0, c.name


def test_BTC5M_HAS_NO_CORRECTION_ARM_AND_ITS_BAND_IS_UNMOVED():
    """Its window is 300s: [0, 300] and [0, 900] are the same band there, so
    a second arm would be the first one wearing a different label."""
    from src.gemini_execution import shadow_soak as SS
    assert SS.correction_spec(CC.BTC5M) is None
    assert CC.BTC5M.entry_band == (240.0, 280.0)
    assert _widest_band(CC.BTC5M) == (0.0, 300.0)
