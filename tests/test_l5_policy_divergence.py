"""L5_CANONICAL vs L5_ADAPTED.

The single property worth testing: an adapted action can never differ from
the canonical one without a recorded reason. Everything else is bookkeeping.
"""
import pytest

from src import l5_policy_divergence as D


def test_no_constraints_means_no_divergence():
    d = D.adapt("open")
    assert d.adapted_action == "open" and not d.diverged
    assert d.divergences == []
    assert "INVARIANT_VIOLATION" not in d.as_dict()


def test_pair_economics_divergence_is_labelled_with_both_numbers():
    d = D.adapt("complete", pair_cost=0.99, pair_ceiling=0.96)
    assert d.diverged and d.adapted_action == "hold"
    x = d.divergences[0]
    assert x.reason == D.PAIR_ECONOMICS
    assert x.evidence == {"pair_cost": 0.99, "pair_ceiling": 0.96}


def test_pair_at_the_ceiling_is_allowed_not_refused():
    """The ceiling is inclusive; an off-by-one here would silently drop a
    whole band of L5's behaviour."""
    assert not D.adapt("complete", pair_cost=0.96, pair_ceiling=0.96).diverged


def test_risk_cap_and_venue_and_unknown_each_have_their_own_reason():
    assert D.adapt("extend", residual_units=6, residual_cap=6
                   ).divergences[0].reason == D.RISK_CAP
    assert D.adapt("open", venue_admissible=False
                   ).divergences[0].reason == D.VENUE_RULE
    assert D.adapt("open", data_known=False
                   ).divergences[0].reason == D.DATA_UNKNOWN


def test_unknown_data_refuses_rather_than_imputing():
    d = D.adapt("open", data_known=False, data_detail="no depth on this venue")
    assert d.adapted_action == "hold"
    assert "UNKNOWN" in d.divergences[0].detail or "no depth" in d.divergences[0].detail


def test_a_silent_divergence_is_flagged_as_an_invariant_violation():
    """THE WHOLE POINT. An adapted action reported as L5 behaviour with no
    reason attached is the failure this module exists to prevent."""
    d = D.Decision(canonical_action="open", adapted_action="hold")
    out = d.as_dict()
    assert d.diverged and not d.divergences
    assert "INVARIANT_VIOLATION" in out
    assert "never be reported as L5 behaviour" in out["INVARIANT_VIOLATION"]


def test_unknown_reason_is_refused():
    with pytest.raises(ValueError):
        D.Divergence("BECAUSE_I_SAID_SO", "open", "hold")


def test_multiple_constraints_all_record_their_reason():
    d = D.adapt("complete", pair_cost=0.99, pair_ceiling=0.96,
                residual_units=9, residual_cap=6, venue_admissible=False)
    # the first binding constraint holds the action, and it is recorded
    assert d.adapted_action == "hold"
    assert {x.reason for x in d.divergences} <= set(D.REASONS)
    assert len(d.divergences) >= 1


def test_summary_counts_reasons_and_silent_divergences():
    ds = [D.adapt("open"),
          D.adapt("complete", pair_cost=0.99, pair_ceiling=0.96),
          D.adapt("extend", residual_units=6, residual_cap=6),
          D.Decision("open", "hold")]
    s = D.summarize(ds)
    assert s["decisions"] == 4 and s["diverged"] == 3
    assert s["by_reason"][D.PAIR_ECONOMICS] == 1
    assert s["by_reason"][D.RISK_CAP] == 1
    assert s["silent_divergences"] == 1
    assert "NEVER" in s["labels"]


def test_module_defines_no_limit_of_its_own():
    """Every threshold is passed in by the caller, so this can never become a
    second source of truth for a live limit."""
    import inspect
    src = inspect.getsource(D)
    for forbidden in ("0.96", "0.97", "= 6", "MAX_RESIDUAL", "PAIR_TARGET"):
        assert forbidden not in src, forbidden
