"""Log-only held-cycle ranking (OWNER GO 5588416241).

The approval was narrow — evidence instrumentation, not a concurrency, risk
or strategy change — so these tests are mostly about what the code CANNOT do.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import pmus_l5_continuous_session as camp


@pytest.fixture(autouse=True)
def _reset():
    camp._SHADOW_RANK.update({"next_at": 0.0, "slow": 0, "disabled": False})
    yield
    camp._SHADOW_RANK.update({"next_at": 0.0, "slow": 0, "disabled": False})


def _rows():
    out = []
    return out, (lambda kind=None, **f: out.append({"kind": kind, **f}))


def test_it_ranks_and_reports_but_never_acts(monkeypatch):
    monkeypatch.setattr(camp, "rank_candidates", lambda *a, **k: {
        "status_t": "2026-09-08T16:00:00+00:00",
        "admitted": [{"rank": 1, "slug": "s1", "pair_score": 0.8,
                      "state_n": 9, "components": {"flow": 1.0}}],
        "refused": [{"slug": "s2"}]})
    monkeypatch.setattr(camp, "pmus_shadow_yield", lambda now: {})
    import src.pmus_spring_l5_probe_entry as entry
    monkeypatch.setattr(entry, "load_status_document",
                        lambda p=None: ({"rows": []}, "status_url:test"))
    rows, log = _rows()
    out = camp.shadow_rank_held_cycle(1000.0, ["held1"], 1, None, log)
    assert out["kind"] == "shadow_selection"
    assert out["acted"] is False and out["l5_action"] is False
    assert out["n_admitted"] == 1 and out["admitted"][0]["slug"] == "s1"
    assert out["held"] == ["held1"]
    assert "NOT acted on" in out["labels"]
    # no "chosen" field exists at all -- there is nothing for a caller to act on
    assert "chosen" not in out


def test_the_row_carries_its_own_provenance_so_nothing_is_carried_forward(
        monkeypatch):
    """'Do not carry stale rankings forward' — a reader must be able to see
    how old the evidence under any ranking was."""
    monkeypatch.setattr(camp, "rank_candidates", lambda *a, **k: {
        "status_t": "2026-09-08T16:00:00+00:00", "admitted": [], "refused": []})
    monkeypatch.setattr(camp, "pmus_shadow_yield", lambda now: {})
    import src.pmus_spring_l5_probe_entry as entry
    monkeypatch.setattr(entry, "load_status_document",
                        lambda p=None: ({"rows": []}, "status_url:test"))
    rows, log = _rows()
    out = camp.shadow_rank_held_cycle(1000.0, [], 1, None, log)
    assert out["status_t"] == "2026-09-08T16:00:00+00:00"
    assert "evidence_age_s" in out and "elapsed_ms" in out
    assert out["source"] == "status_url:test"


def test_it_is_rate_limited(monkeypatch):
    calls = []
    monkeypatch.setattr(camp, "rank_candidates", lambda *a, **k: (
        calls.append(1) or {"status_t": None, "admitted": [], "refused": []}))
    monkeypatch.setattr(camp, "pmus_shadow_yield", lambda now: {})
    import src.pmus_spring_l5_probe_entry as entry
    monkeypatch.setattr(entry, "load_status_document",
                        lambda p=None: ({"rows": []}, "s"))
    rows, log = _rows()
    camp.shadow_rank_held_cycle(1000.0, [], 1, None, log)
    camp.shadow_rank_held_cycle(1005.0, [], 1, None, log)   # inside the gap
    assert len(calls) == 1
    camp.shadow_rank_held_cycle(1000.0 + camp.SHADOW_RANK_MIN_GAP_S + 1,
                                [], 1, None, log)
    assert len(calls) == 2


def test_a_slow_ranking_disables_the_MEASUREMENT_not_the_execution(monkeypatch):
    """The GO is explicit: if it materially delays the live loop, fail the
    measurement rather than execution. The recorder starved once this month;
    an evidence feature never gets to repeat that."""
    monkeypatch.setattr(camp, "rank_candidates", lambda *a, **k: {
        "status_t": None, "admitted": [], "refused": []})
    monkeypatch.setattr(camp, "pmus_shadow_yield", lambda now: {})
    import src.pmus_spring_l5_probe_entry as entry
    monkeypatch.setattr(entry, "load_status_document",
                        lambda p=None: ({"rows": []}, "s"))
    ticks = iter([0.0, 99.0] * 10)          # 99 s per ranking: far too slow
    rows, log = _rows()
    for i in range(camp.SHADOW_RANK_SLOW_STRIKES):
        camp.shadow_rank_held_cycle(1000.0 + i * 60, [], 1, None, log,
                                    clock=lambda: next(ticks))
    assert camp._SHADOW_RANK["disabled"] is True
    assert any(r["kind"] == "shadow_selection_disabled" for r in rows)
    # and it stays disabled, returning None rather than raising
    assert camp.shadow_rank_held_cycle(99999.0, [], 1, None, log) is None


def test_a_failure_is_logged_and_never_raised(monkeypatch):
    import src.pmus_spring_l5_probe_entry as entry
    monkeypatch.setattr(entry, "load_status_document",
                        lambda p=None: (_ for _ in ()).throw(RuntimeError("503")))
    rows, log = _rows()
    assert camp.shadow_rank_held_cycle(1000.0, [], 1, None, log) is None
    assert any(r["kind"] == "shadow_selection_error" for r in rows)


def test_the_instrumentation_cannot_reach_order_execution_or_inventory():
    """The narrow-approval guarantee, checked against the source rather than
    promised in a docstring."""
    src = Path(camp.__file__).read_text(encoding="utf-8")
    body = src.split("def shadow_rank_held_cycle")[1].split(
        "\ndef rank_candidates")[0]
    # strip the docstring: it legitimately NAMES select_candidate and the
    # order path while explaining why neither is reachable, and a substring
    # test that trips on its own prose is a test of the prose
    parts = body.split('"""')
    body = parts[0] + ("".join(parts[2:]) if len(parts) > 2 else "")
    for forbidden in ("session_runner", "place_limit_buy", "micro.",
                      "inv.", "_persist", "filled_markets", "cooldown",
                      "exclude", "concurrency =", "funnel["):
        assert forbidden not in body, forbidden
    # and it may not call the real selector, whose row would be
    # indistinguishable from a decision the campaign actually made
    assert "select_candidate" not in body


def test_it_runs_after_the_session_so_it_cannot_delay_an_order():
    src = Path(camp.__file__).read_text(encoding="utf-8")
    held = src.split("if held:")[1].split("continue")[0]
    assert held.index("session_runner") < held.index("shadow_rank_held_cycle")
    assert "AFTER the session returns" in held


def test_the_caller_cannot_let_it_raise_into_the_campaign_loop():
    src = Path(camp.__file__).read_text(encoding="utf-8")
    held = src.split("if held:")[1].split("continue")[0]
    call = held.split("shadow_rank_held_cycle")[0][-400:]
    assert "try:" in call
