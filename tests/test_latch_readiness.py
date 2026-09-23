"""Readiness must ask the question that actually decided Probe B.

Probe B passed a full credentialed static readiness TWICE against
OWNER-2026-09-23-GEMINI-Q1-AB-1 -- spec, bundle, hashes, family, chain,
terms, positions, all PASS, why_not empty -- and could never have placed an
order. Probe A had already spent that latch, and the latch means ONE
AUTHORIZATION, ONE COHORT: `latch.consumed()` keys on the authorization id
alone, not on the cohort, the spec or the family. B is a different cohort
under the same id, so it would have returned STOPPED_NO_LATCH before its
first mutation.

Readiness could not see it because readiness passes `readiness=True` and
`_consume_cohort_latch` runs only under `if not readiness:`. The check below
closes that hole: it evaluates the same decision READ-ONLY, so a readiness
PASS now means the armed run can actually reach the mutation boundary.
"""
import json
import os

from src.gemini_execution import cohort_runner as CR
from src.gemini_execution import latch as LATCH


def _latch(tmp_path, auth_id):
    d = tmp_path / "latch"
    os.makedirs(d, exist_ok=True)
    LATCH.consume(auth_id, {"intent_id": "test"}, str(d))
    return str(d)


def test_a_fresh_authorization_reaches_the_mutation_boundary(tmp_path):
    d = str(tmp_path / "latch")
    os.makedirs(d, exist_ok=True)
    got = CR.latch_counterfactual("OWNER-FRESH", state_dir=d,
                                  head="COHORT-X-W01", max_windows=4)
    assert got["latch_path_exists"] is False
    assert got["reaches_mutation_boundary"] is True
    assert got["would"] == "CONSUME_FRESH"
    assert got["ok"] is True


def test_a_spent_authorization_on_a_new_cohort_is_refused(tmp_path):
    """EXACTLY Probe B's situation: same id, different cohort, 0 observed."""
    d = _latch(tmp_path, "OWNER-SPENT")
    got = CR.latch_counterfactual("OWNER-SPENT", state_dir=d,
                                  head="COHORT-NEW-W01", max_windows=4)
    assert got["latch_path_exists"] is True
    assert got["reaches_mutation_boundary"] is False
    assert got["would"] == "REFUSE_SPENT"
    assert got["ok"] is False
    assert "never a re-run" in got["why"]


def test_the_counterfactual_creates_no_latch(tmp_path):
    """A readiness check must never spend the thing it is checking."""
    d = str(tmp_path / "latch")
    os.makedirs(d, exist_ok=True)
    for _ in range(3):
        CR.latch_counterfactual("OWNER-FRESH", state_dir=d,
                                head="COHORT-X-W01", max_windows=4)
    assert os.listdir(d) == [], "the counterfactual wrote a latch"
    assert LATCH.consumed("OWNER-FRESH", d) is False


def test_readiness_fails_closed_when_the_latch_would_refuse(tmp_path,
                                                            monkeypatch):
    """The whole point: a tree that is perfect but unarmable must not PASS."""
    d = _latch(tmp_path, "OWNER-SPENT")

    async def _run(auth):
        from src.gemini_execution import live_probe_spec as SPEC
        spec = SPEC.get("btc5m_q1_improve")
        return await CR.static_readiness(spec, auth_id=auth,
                                         root=str(tmp_path / "cohorts"),
                                         chain=(), state_dir=d,
                                         max_windows=4)

    import asyncio
    out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _run("OWNER-SPENT"))
    assert out["checks"]["latch"]["ok"] is False
    assert out["verdict"] == "FAIL"
    assert any(w.startswith("latch:") for w in out["why_not"]), out["why_not"]


def test_the_real_ab2_latch_is_fresh_and_ab1_is_spent():
    """The live tree, not a fixture: AB-1 spent by Probe A, AB-2 unused."""
    head = "COHORT-2026-09-23-BTC5M-AB-B-W01"
    ab2 = CR.latch_counterfactual("OWNER-2026-09-23-GEMINI-Q1-AB-2",
                                  state_dir=CR.DEFAULT_STATE_DIR,
                                  head=head, max_windows=4)
    assert ab2["latch_path_exists"] is False
    assert ab2["reaches_mutation_boundary"] is True

    ab1 = CR.latch_counterfactual("OWNER-2026-09-23-GEMINI-Q1-AB-1",
                                  state_dir=CR.DEFAULT_STATE_DIR,
                                  head=head, max_windows=4)
    assert ab1["latch_path_exists"] is True, (
        "Probe A's latch must remain on disk: it is the record that the "
        "authorization was spent, and it is never cleared")
    assert ab1["reaches_mutation_boundary"] is False
