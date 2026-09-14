"""The candidate 5m completion policy, replayed OFFLINE against recorded PMUS
states. It submits nothing and never reads back into the live session."""
import importlib.util
import pathlib

_p = (pathlib.Path(__file__).resolve().parents[1]
      / "analysis" / "l5" / "l5_5m_shadow" / "shadow_replay.py")
_spec = importlib.util.spec_from_file_location("shadow_replay", _p)
SR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SR)


def _row(pair=None, filled="0"):
    return {"slug": "m", "stage": "spring_complete_1", "side": "YES",
            "maker_px": "0.50", "qty": "1", "filled_qty": filled,
            "econ_pair_cost": (None if pair is None else str(pair))}


def test_candidate_completes_only_at_or_below_the_target():
    assert SR.new_l5_5m(_row(0.8900))["action"] == "COMPLETE"
    assert SR.new_l5_5m(_row(SR.COMPLETE_TARGET))["action"] == "COMPLETE"
    assert SR.new_l5_5m(_row(0.9001))["action"] == "WAIT"


def test_unknown_pair_is_refused_because_unknown_is_not_permission():
    """The live record carries no pair on most fills. Treating UNKNOWN as
    acceptable would let the candidate 'complete' on states whose economics
    were never observed -- the exact error the ledger discipline forbids."""
    got = SR.new_l5_5m(_row(None))
    assert got["action"] == "WAIT"
    assert "UNKNOWN" in got["reason"]


def test_candidate_is_strictly_tighter_than_production_never_looser():
    """It must refuse a superset of what production refuses: no state exists
    where the candidate completes and production would not."""
    for pair in (0.50, 0.80, 0.90, 0.95, 0.99, 1.00, 1.20, None):
        cand = SR.new_l5_5m(_row(pair))
        if cand["action"] == "COMPLETE":
            assert pair is not None and pair <= SR.LIVE_HARD_CEILING, pair


def test_production_action_is_read_from_the_record_not_resimulated():
    assert SR.current_r5ft(_row(0.5, filled="2.5"))["action"] == "COMPLETE_FILLED"
    assert SR.current_r5ft(_row(0.5, filled="0"))["action"] == "COMPLETE_RESTED_NO_FILL"


def test_replay_flags_the_disagreement_that_matters():
    rep = SR.replay([_row(0.99, filled="1")])
    assert rep[0]["disagreement"] == "PRODUCTION_FILLED_CANDIDATE_WOULD_REFUSE"
    assert rep[0]["CURRENT_R5FT"] == "COMPLETE_FILLED"
    assert rep[0]["NEW_L5_5M"] == "WAIT"
