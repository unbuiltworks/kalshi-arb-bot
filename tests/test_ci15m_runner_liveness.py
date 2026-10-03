"""The runner must STAY ALIVE until it can trade, or until the deadline.

WHY THIS FILE EXISTS. CI15M-2 exited 0 in seven seconds and the only way to
learn why was the Actions log. Two things were wrong in the orchestration,
independent of the adapter bug that actually fired:

  C  a window nothing was submitted into still counted as a COMPLETED
     window, so the one-window budget was consumed by a window that never
     traded -- and a retry would have refused at "WINDOW BUDGET reached";
  H  falling out of the loop at the deadline recorded NO REASON AT ALL.

"No eligible market right now" was already handled correctly -- it sleeps
and polls -- and these tests pin that down so it stays that way.

The clock and sleep are injected, so a 1500-second deadline is exercised in
milliseconds and nothing here touches a venue.
"""
from __future__ import annotations

import asyncio
import pathlib
from decimal import Decimal as D

import pytest

from src.gemini_execution import ci15m_budget as BUD
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import ci15m_runner as R
from src.gemini_execution import ci15m_runner as R_mod

CID = "CI15M-LIVENESS"


class FakeClock:
    """A clock that only advances when the runner sleeps."""

    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += float(s)


@pytest.fixture(autouse=True)
def _no_writes_to_the_real_cohort_root():
    """These tests must never touch the real cohort directory.

    They did, before `root` was threaded through `run()`: every budget
    function binds cohort.COHORT_ROOT as a DEFAULT ARGUMENT at import time,
    so the un-rooted calls wrote a CI15M-LIVENESS directory under the live
    cohort root -- debris that represented nothing and sat next to real
    authorized cohorts. This fails the test rather than leaving it to a
    git-status check.
    """
    from src.gemini_execution import cohort as C
    real = pathlib.Path(C.COHORT_ROOT)
    before = {p.name for p in real.iterdir()} if real.exists() else set()
    yield
    after = {p.name for p in real.iterdir()} if real.exists() else set()
    assert after == before, (
        "these tests wrote into the REAL cohort root: %s"
        % sorted(after - before))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "cohorts")
    monkeypatch.setattr(BUD.cohort, "COHORT_ROOT", root)
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    BUD.create(CID, root=root, profile=BUD.MECHANICS)
    return root


def _mkt(ticker="BTC15M2610031900", exp_offset=400.0, now=1_000_000.0):
    return {"symbol": "GEMI-%s-UP" % ticker, "ticker": ticker,
            "strike": {"value": "84000", "availableAt": now - 10},
            "expiry_ts": now + exp_offset}


# =====================================================================
# 1. t=0 no eligible market -> the runner STAYS ALIVE -> a market appears
#    later -> an OPEN is submitted.
# =====================================================================
def test_no_market_then_a_market_appears_and_open_is_submitted(env,
                                                               monkeypatch):
    clock = FakeClock()
    appears_at = clock.t + 600.0          # ten minutes in

    def discover(now):
        return [] if now < appears_at else [_mkt(now=now)]
    monkeypatch.setattr(R, "discover", discover)

    submitted = {}

    async def fake_run_window(cohort_id, symbol, ticker, expiry, **kw):
        root = kw.get("root")
        # stand in for a real window that submits ONE order
        intent = {"action": "OPEN", "side": "UP", "price": "0.40",
                  "quantity": "1", "unmatched_shares_after": "1",
                  "unmatched_principal_after": "0.40",
                  BUD.RECONCILED_KEY: "0"}
        row = BUD.consume_slot(cohort_id, ticker, intent, remote=None,
                               root=root)
        submitted["slot"] = row["slot"]
        submitted["ticker"] = ticker
        BUD.close_window(cohort_id, ticker, "RESIDUAL", root=root)
        return {"ticker": ticker, "events": [], "inventory": {},
                "n_actions": 1, "halted": None}
    monkeypatch.setattr(P, "run_window", fake_run_window)

    out = asyncio.run(R.run(CID, live=True, durable_branch=None,
                            max_windows=1, runtime_s=1500.0,
                            now_fn=clock, sleeper=clock.sleep,
                            root=env))

    assert submitted.get("slot") == 0, "the OPEN must have been submitted"
    assert out["exit"]["reason"] == "SUBMITTED"
    # it waited rather than exiting: the clock advanced past the appearance
    assert clock.t >= appears_at
    assert BUD.consumed(CID, root=env) == 1


def test_an_empty_first_poll_is_not_a_termination(env, monkeypatch):
    """The narrow claim, stated on its own."""
    clock = FakeClock()
    monkeypatch.setattr(R, "discover", lambda now: [])
    out = asyncio.run(R.run(CID, live=True, durable_branch=None,
                            max_windows=1, runtime_s=1500.0,
                            now_fn=clock, sleeper=clock.sleep,
                            root=env))
    assert clock.t >= 1_000_000.0 + 1500.0, (
        "the runner exited before the deadline on an empty market list")
    assert out["exit"]["reason"] == "NO_ELIGIBLE_WINDOW_TIMEOUT"


# =====================================================================
# 2. no eligible market for the WHOLE runtime -> exits only at the
#    deadline, with an explicit NO_ELIGIBLE_WINDOW_TIMEOUT.
# =====================================================================
def test_no_market_for_the_entire_runtime_exits_only_at_the_deadline(
        env, monkeypatch):
    clock = FakeClock()
    start = clock.t
    monkeypatch.setattr(R, "discover", lambda now: [])
    out = asyncio.run(R.run(CID, live=True, durable_branch=None,
                            max_windows=1, runtime_s=1500.0,
                            now_fn=clock, sleeper=clock.sleep,
                            root=env))
    assert clock.t - start >= 1500.0
    ex = out["exit"]
    assert ex["reason"] == "NO_ELIGIBLE_WINDOW_TIMEOUT"
    assert ex["known_reason"] is True
    assert ex["detail"]["polls"] > 1, "it must have polled repeatedly"
    assert "not a success" in ex["detail"]["why"]
    # and it is DURABLE: a later reader can see why
    assert any(r["reason"] == "NO_ELIGIBLE_WINDOW_TIMEOUT"
               for r in BUD.exits(CID, root=env))
    assert BUD.consumed(CID, root=env) == 0


# =====================================================================
# C: a window nothing was submitted into must not consume the budget.
#    This is the CI15M-2 shape exactly.
# =====================================================================
def test_a_window_with_zero_submissions_does_not_consume_the_budget(
        env, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(R, "discover", lambda now: [_mkt(now=now)])

    async def halting_window(cohort_id, symbol, ticker, expiry, **kw):
        """Exactly CI15M-2: opened, halted before any submission, closed
        NO_FILL."""
        BUD.close_window(cohort_id, ticker, "NO_FILL",
                         detail={"actions": 0}, root=kw.get("root"))
        return {"ticker": ticker, "events": [], "inventory": {},
                "n_actions": 0, "halted": None}
    monkeypatch.setattr(P, "run_window", halting_window)

    asyncio.run(R.run(CID, live=True, durable_branch=None, max_windows=1,
                      runtime_s=100.0, now_fn=clock, sleeper=clock.sleep,
                            root=env))
    assert BUD.consumed(CID, root=env) == 0
    assert BUD.completed_windows(CID, root=env) == 0, (
        "a window with zero submissions is not a completed experiment")
    assert BUD.windows(CID, root=env)["BTC15M2610031900"] == BUD.NOT_ATTEMPTED
    # and the one-window budget is therefore still available
    assert BUD.expired(CID, root=env, remote=None) is None


def test_a_window_that_submitted_and_was_cancelled_DOES_consume_it(env,
                                                                   monkeypatch):
    """The owner's rule: a cancelled or unfilled OPEN ends that market's
    experiment. That window IS consumed -- only zero-submission is not."""
    clock = FakeClock()
    monkeypatch.setattr(R, "discover", lambda now: [_mkt(now=now)])

    async def cancelled_window(cohort_id, symbol, ticker, expiry, **kw):
        rt = kw.get("root")
        BUD.consume_slot(cohort_id, ticker,
                         {"action": "OPEN", "side": "UP", "price": "0.40",
                          "quantity": "1", "unmatched_shares_after": "1",
                          "unmatched_principal_after": "0.40",
                          BUD.RECONCILED_KEY: "0"}, remote=None, root=rt)
        BUD.close_window(cohort_id, ticker, "NO_FILL", detail={"actions": 0},
                         root=rt)
        return {"ticker": ticker, "events": [], "inventory": {},
                "n_actions": 0, "halted": None}
    monkeypatch.setattr(P, "run_window", cancelled_window)

    asyncio.run(R.run(CID, live=True, durable_branch=None, max_windows=1,
                      runtime_s=100.0, now_fn=clock, sleeper=clock.sleep,
                            root=env))
    assert BUD.consumed(CID, root=env) == 1
    assert BUD.completed_windows(CID, root=env) == 1
    assert "WINDOW_BUDGET_EXHAUSTED" in (BUD.expired(CID, root=env, remote=None) or "")


# =====================================================================
# every pre-submission exit is durable and visible
# =====================================================================
def test_every_termination_records_a_durable_reason(env, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(R, "discover", lambda now: [_mkt(now=now)])

    async def halted_window(cohort_id, symbol, ticker, expiry, **kw):
        rt = kw.get("root")
        BUD.halt(cohort_id, "UNKNOWN_POSITION", "positions not readable",
                 root=rt)
        BUD.close_window(cohort_id, ticker, "NO_FILL", detail={"actions": 0},
                         root=rt)
        return {"ticker": ticker, "events": [], "inventory": {},
                "n_actions": 0, "halted": BUD.halted(cohort_id, root=rt)}
    monkeypatch.setattr(P, "run_window", halted_window)

    out = asyncio.run(R.run(CID, live=True, durable_branch=None,
                            max_windows=1, runtime_s=100.0,
                            now_fn=clock, sleeper=clock.sleep,
                            root=env))
    assert out["exit"]["reason"] == "HALTED"
    rows = BUD.exits(CID, root=env)
    assert rows and rows[-1]["reason"] == "HALTED"
    assert "UNKNOWN_POSITION" in str(rows[-1]["detail"])


def test_the_exit_reason_appears_in_the_cohort_state(env, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(R, "discover", lambda now: [])
    asyncio.run(R.run(CID, live=True, durable_branch=None, max_windows=1,
                      runtime_s=100.0, now_fn=clock, sleeper=clock.sleep,
                            root=env))
    st = BUD.state(CID, root=env)
    assert st["exits"], "state() must surface why the probe stopped"
    assert st["exits"][-1]["reason"] == "NO_ELIGIBLE_WINDOW_TIMEOUT"


def test_a_green_run_that_sent_nothing_is_never_silent(env, monkeypatch):
    """The meta-requirement: no termination path may leave the record
    empty."""
    clock = FakeClock()
    monkeypatch.setattr(R, "discover", lambda now: [])
    out = asyncio.run(R.run(CID, live=True, durable_branch=None,
                            max_windows=1, runtime_s=100.0,
                            now_fn=clock, sleeper=clock.sleep,
                            root=env))
    assert out["exit"] is not None
    assert out["exit"]["reason"]
    assert BUD.exits(CID, root=env)


# =====================================================================
# THE LATCH REF: an orphan commit of the cohort files only.
#
# CI15M-3 assembled the whole chain, consumed slot 0, and was refused at
# the push because the latch commit was built on the pinned tree, which
# contains .github/workflows/ -- and the job's GITHUB_TOKEN carries
# Contents: write only:
#
#   ! [remote rejected] HEAD -> ci15m-latch/CI15M-3-slot0
#     (refusing to allow a GitHub App to create or update workflow
#      `.github/workflows/gemini-settlement-gate.yml` without `workflows`
#      permission)
# =====================================================================
def test_the_latch_commit_carries_no_workflow_file(tmp_path, monkeypatch):
    """Built from the cohort files alone, so the pushed tree cannot contain
    a workflow file and cannot need `workflows` permission."""
    import subprocess
    root = str(tmp_path / "cohorts")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    BUD.create(CID, root=root, profile=BUD.MECHANICS)
    BUD.open_window(CID, "BTC15M2610031945", root=root)
    BUD.consume_slot(CID, "BTC15M2610031945",
                     {"action": "OPEN", "side": "UP", "price": "0.59",
                      "quantity": "1", "unmatched_shares_after": "1",
                      "unmatched_principal_after": "0.59",
                      BUD.RECONCILED_KEY: "0"}, root=root, remote=None)

    monkeypatch.setattr(BUD.cohort, "directory",
                        lambda cid, **k: str(pathlib.Path(root) / cid))
    pushed = {}
    real = subprocess.run

    def fake(cmd, *a, **k):
        if cmd[:2] == ["git", "push"]:
            pushed["spec"] = cmd[-1]

            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", fake)

    hook = R_mod.git_slot_hook("some-branch")
    assert hook({"cohort_id": CID, "slot": 0, "ticker": "BTC15M2610031945"})
    sha, ref = pushed["spec"].split(":")
    assert ref == "refs/heads/ci15m-latch/%s-slot0" % CID

    # the pushed commit must be PARENTLESS and contain ONLY cohort files
    out = real(["git", "cat-file", "-p", sha], capture_output=True,
               text=True, timeout=30).stdout
    assert "parent " not in out, "the latch commit must be an orphan"
    tree = real(["git", "ls-tree", "-r", "--name-only", sha],
                capture_output=True, text=True, timeout=30).stdout.split()
    assert tree, "the latch tree is empty"
    assert not any(t.startswith(".github/") for t in tree), tree
    assert all(t in ("ledger.jsonl", "windows.jsonl", "cohort.json",
                     "exits.jsonl", "settled.jsonl", "HALT") for t in tree), \
        tree
    # and the ledger really is in there, with the consumed slot
    blob = real(["git", "show", "%s:ledger.jsonl" % sha],
                capture_output=True, text=True, timeout=30).stdout
    assert '"kind": "PLACE"' in blob and '"slot": 0' in blob


def test_the_latch_hook_fails_closed_when_the_push_is_rejected(tmp_path,
                                                               monkeypatch):
    """A rejected push must return False so the probe stops with the slot
    spent -- it must never be read as success."""
    import subprocess
    root = str(tmp_path / "cohorts")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    BUD.create(CID, root=root, profile=BUD.MECHANICS)
    monkeypatch.setattr(BUD.cohort, "directory",
                        lambda cid, **k: str(pathlib.Path(root) / cid))
    real = subprocess.run

    def fake(cmd, *a, **k):
        if cmd[:2] == ["git", "push"]:
            class R:
                returncode = 1
                stdout = ""
                stderr = "! [remote rejected] ... without `workflows` permission"
            return R()
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", fake)
    hook = R_mod.git_slot_hook("b")
    assert hook({"cohort_id": CID, "slot": 0, "ticker": "T"}) is False


def test_an_empty_cohort_directory_fails_closed(tmp_path, monkeypatch):
    import subprocess
    d = tmp_path / "empty"
    d.mkdir()
    monkeypatch.setattr(BUD.cohort, "directory", lambda cid, **k: str(d))
    hook = R_mod.git_slot_hook("b")
    assert hook({"cohort_id": "X", "slot": 0, "ticker": "T"}) is False


# ---------------------------------------------------------------------------
# CI15M-4: the preflight's budget condition must follow the AUTHORIZATION,
# not a magic number. It read `orders_total == 3` -- CI15M-3's MECHANICS
# shape -- so the CI15M-4 preflight failed on a cohort that was correctly
# configured at the authorized 6. No order was submitted; the whole run
# died at the checklist. The replacement compares against
# P.AUTHORIZED_BOUNDS, so it fails in BOTH directions.
# ---------------------------------------------------------------------------

def test_preflight_budget_condition_names_no_magic_number():
    import inspect
    from src.gemini_execution import ci15m_runner as R
    src = inspect.getsource(R.preflight)
    assert "durable_slots_zero_of_three" not in src
    assert "durable_slots_zero_of_authorized_total" in src
    cond = src.split("durable_slots_zero_of_authorized_total", 1)[1]
    cond = cond.split("conditions[", 1)[0]
    assert "AUTHORIZED_BOUNDS" in cond
    # no bare integer budget comparison survives in the condition
    assert "== 3" not in cond and "== 6" not in cond


def test_authorized_bounds_profile_equals_mechanics2():
    from src.gemini_execution import ci15m_probe as P
    from src.gemini_execution import ci15m_budget as B
    ab = P.AUTHORIZED_BOUNDS
    prof = B.PROFILES[ab["profile"]]
    assert ab["profile"] == "MECHANICS2"
    assert prof["windows"] == ab["windows"] == 2
    assert prof["orders_total"] == ab["orders_total"] == 6
    assert prof["orders_per_window"] == ab["orders_per_window"] == 3
