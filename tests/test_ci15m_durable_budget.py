"""A -- cross-run budget durability, proved against REAL refs.

WHY THIS IS A SEPARATE FILE. The other suite stubs the git read so it stays
offline and fast. That stub is exactly what must NOT be trusted here: the
defect was that nothing ever read the durable refs back, and a test that
mocks the read cannot find that. These tests use a real git repository, real
refs, and a FRESH EMPTY cohort directory -- which is what a new Actions
runner actually is.

THE DEFECT. `consumed()` reads a ledger on the local disk. A fresh runner's
disk is empty, so an exhausted 12/12 cohort counted as 0 and a second
dispatch would have spent twelve more orders. The latch refs survived the
whole time; nothing looked at them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from src.gemini_execution import ci15m_budget as BUD


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=60)


@pytest.fixture()
def remote(tmp_path):
    """A real bare repo carrying real ci15m-latch refs, as the live cohort
    left behind on github."""
    bare = tmp_path / "remote.git"
    _git("init", "--bare", "-q", str(bare), cwd=str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", cwd=str(work))
    _git("config", "user.email", "t@t", cwd=str(work))
    _git("config", "user.name", "t", cwd=str(work))
    (work / "f").write_text("x")
    _git("add", "-A", cwd=str(work))
    _git("commit", "-qm", "base", cwd=str(work))
    _git("remote", "add", "origin", str(bare), cwd=str(work))
    # twelve consumed slots, exactly as CI15M-1 left them
    for n in range(12):
        _git("push", "-q", "origin",
             "HEAD:refs/heads/ci15m-latch/CI15M-1-slot%d" % n, cwd=str(work))
    return str(bare), str(work)


def test_the_latch_refs_are_read_back_at_all(remote):
    bare, work = remote
    got = BUD.latch_slots("CI15M-1", remote=bare)
    assert got["readable"] is True
    assert got["count"] == 12
    assert got["slots"] == list(range(12))


def test_a_fresh_empty_directory_still_sees_twelve_spent_slots(remote,
                                                               tmp_path):
    """THE REGRESSION. A brand-new cohort root -- no ledger, no history,
    nothing on disk, which is precisely a new Actions runner."""
    bare, work = remote
    fresh = str(tmp_path / "fresh-runner-root")
    assert not os.path.exists(fresh)
    BUD.create("CI15M-1", root=fresh)
    assert BUD.consumed("CI15M-1", root=fresh) == 0, (
        "the local disk is empty, which is the whole problem")
    dur = BUD.durable_consumed("CI15M-1", root=fresh, remote=bare)
    assert dur["readable"] is True
    assert dur["local"] == 0
    assert dur["consumed"] == 12, "git must supply what the disk forgot"


def test_a_second_dispatch_of_an_exhausted_cohort_refuses(remote, tmp_path):
    bare, work = remote
    fresh = str(tmp_path / "second-dispatch")
    BUD.create("CI15M-1", root=fresh)
    why = BUD.expired("CI15M-1", root=fresh, remote=bare)
    assert why and "TOTAL_ORDER_BUDGET_EXHAUSTED" in why
    g = BUD.may_submit("CI15M-1", "BTC15M2610031745", root=fresh,
                       remote=bare)
    assert g["ok"] is False and "EXHAUSTED" in g["why"]


def test_the_refusal_happens_before_any_venue_access(remote, tmp_path):
    """consume_slot must raise, so the caller never reaches transport."""
    bare, work = remote
    fresh = str(tmp_path / "before-venue")
    BUD.create("CI15M-1", root=fresh)
    BUD.open_window("CI15M-1", "BTC15M2610031745", root=fresh) \
        if False else None
    with pytest.raises(BUD.BudgetRefused) as e:
        BUD.consume_slot("CI15M-1", "BTC15M2610031745",
                         {"action": "OPEN", "side": "UP", "price": "0.40",
                          "quantity": "1"}, root=fresh, remote=bare)
    assert "EXHAUSTED" in str(e.value)


def test_an_unreadable_git_side_fails_closed(tmp_path):
    """An unreadable budget is never an empty one."""
    fresh = str(tmp_path / "unreadable")
    BUD.create("CI15M-1", root=fresh)
    nowhere = str(tmp_path / "does-not-exist.git")
    dur = BUD.durable_consumed("CI15M-1", root=fresh, remote=nowhere)
    assert dur["readable"] is False and dur["consumed"] is None
    why = BUD.expired("CI15M-1", root=fresh, remote=nowhere)
    assert why and "DURABLE_BUDGET_UNREADABLE" in why
    with pytest.raises(BUD.BudgetRefused):
        BUD.consume_slot("CI15M-1", "T",
                         {"action": "OPEN", "side": "UP", "price": "0.40",
                          "quantity": "1"}, root=fresh, remote=nowhere)


def test_a_partly_spent_cohort_resumes_at_the_right_slot(remote, tmp_path):
    """Not merely all-or-nothing: 12 refs for a 12 budget is exhausted, but
    a cohort with room must resume rather than restart."""
    bare, work = remote
    # drop down to 5 consumed
    for n in range(5, 12):
        _git("push", "-q", "--delete", "origin",
             "refs/heads/ci15m-latch/CI15M-1-slot%d" % n, cwd=work)
    fresh = str(tmp_path / "partly")
    BUD.create("CI15M-1", root=fresh)
    dur = BUD.durable_consumed("CI15M-1", root=fresh, remote=bare)
    assert dur["consumed"] == 5
    assert BUD.expired("CI15M-1", root=fresh, remote=bare) is None


def test_a_genuinely_separate_process_agrees(remote, tmp_path):
    """Not just a fresh directory -- a fresh INTERPRETER, so no module
    state, no cached count and no fixture can be carrying the answer."""
    bare, work = remote
    fresh = str(tmp_path / "separate-process")
    BUD.create("CI15M-1", root=fresh)
    code = (
        "import json,sys;"
        "from src.gemini_execution import ci15m_budget as B;"
        "print(json.dumps(B.durable_consumed('CI15M-1', root=%r,"
        " remote=%r), default=str))" % (fresh, bare))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=120,
                       env={**os.environ, "PYTHONPATH": "."})
    assert p.returncode == 0, p.stderr[-800:]
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got["consumed"] == 12 and got["local"] == 0
