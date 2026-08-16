from types import SimpleNamespace

from src import gemini_kalshi_basis_execution_revalidation_safe as safe


def _plan(opened_ms=1000):
    return SimpleNamespace(direction="x", opened_ms=opened_ms)


def test_same_original_episode_requires_both_states_to_match_start():
    plan = _plan(1000)
    active = {"x": SimpleNamespace(start_ms=1000)}
    state = SimpleNamespace(start_ms=1000)
    assert safe.same_original_episode(plan, active, state) is True


def test_reopened_direction_cannot_satisfy_old_plan():
    plan = _plan(1000)
    active = {"x": SimpleNamespace(start_ms=1040)}
    new_state = SimpleNamespace(start_ms=1040)
    assert safe.same_original_episode(plan, active, new_state) is False


def test_mismatched_basis_state_fails_closed():
    plan = _plan(1000)
    active = {"x": SimpleNamespace(start_ms=1000)}
    wrong_state = SimpleNamespace(start_ms=1001)
    assert safe.same_original_episode(plan, active, wrong_state) is False


def test_missing_episode_fails_closed():
    plan = _plan(1000)
    assert safe.same_original_episode(plan, {}, None) is False
