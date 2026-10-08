"""THE EPOCH NAMESPACE: a second authorization binds its own window.

The window binding is immutable and never released, so a probe cannot
collect a fresh attempt allowance in each window it chooses. Across TWO
authorizations that same property refuses the second one outright: the
22:00z one-attempt probe bound window 3, and the three-attempt probe that
followed read that binding, found window 4 did not match, and refused with
PROBE_IS_BOUND_TO_WINDOW_3 having placed nothing.

The fix is a namespace, not a reset. These tests pin that:

  * the previous epoch's file is never read, written or deleted;
  * a new epoch starts empty and binds once, O_EXCL, immutably;
  * a restart inside an epoch re-reads the same binding and grants no
    further attempts;
  * a rebind within one epoch is still a ProbeBindingConflict;
  * the archive is a COPY: refused if it would overwrite, and the
    original stays on disk;
  * nothing about cumulative loss, the ledger or the latch slots moves.
"""
from __future__ import annotations

import json
import os

import pytest

from analysis.l5.e1_cohort import budget as BUD
from analysis.l5.e1_cohort import latch as L

SID = "E1-COHORT-20261008T005816Z-051888d8"
SLUG3 = "cpc-btc-updown-15m-2026-10-08-2200z"
SLUG4 = "cpc-btc-updown-15m-2026-10-08-2300z"


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    monkeypatch.setenv("PMUS_E1_SESSION_ID", SID)
    monkeypatch.delenv(BUD.EPOCH_ENV, raising=False)
    yield


def test_the_legacy_and_epoch_paths_are_different_files():
    legacy = BUD.binding_path(session_id=SID)
    epoch = BUD.binding_path(session_id=SID, epoch="3attempt")
    assert legacy != epoch
    assert "epoch-3attempt" in epoch
    assert "epoch-" not in os.path.basename(legacy)


def test_a_new_epoch_starts_empty_even_with_a_legacy_binding():
    BUD.bind_window(window=3, slug=SLUG3, session_id=SID)
    assert BUD.read_binding(session_id=SID)["window"] == 3
    # the new authorization sees NOTHING in its own namespace
    assert BUD.read_binding(session_id=SID, epoch="3attempt") is None


def test_the_new_epoch_binds_a_different_window_without_conflict():
    BUD.bind_window(window=3, slug=SLUG3, session_id=SID)
    rec = BUD.bind_window(window=4, slug=SLUG4, session_id=SID,
                          epoch="3attempt")
    assert rec["window"] == 4
    assert rec["epoch"] == "3attempt"
    # and the old one is untouched
    old = BUD.read_binding(session_id=SID)
    assert old["window"] == 3 and old["slug"] == SLUG3


def test_the_legacy_binding_is_never_read_inside_an_epoch(monkeypatch):
    BUD.bind_window(window=3, slug=SLUG3, session_id=SID)
    monkeypatch.setenv(BUD.EPOCH_ENV, "3attempt")
    # reading with the env set must not surface window 3
    assert BUD.read_binding(session_id=SID) is None


def test_a_rebind_inside_one_epoch_is_still_a_conflict():
    BUD.bind_window(window=4, slug=SLUG4, session_id=SID, epoch="e")
    with pytest.raises(BUD.ProbeBindingConflict):
        BUD.bind_window(window=5, slug="other", session_id=SID, epoch="e")


def test_a_restart_inside_an_epoch_grants_no_new_attempts():
    a = BUD.bind_window(window=4, slug=SLUG4, session_id=SID, epoch="e")
    b = BUD.bind_window(window=4, slug=SLUG4, session_id=SID, epoch="e")
    assert b["already_bound"] is True
    assert a["window"] == b["window"] == 4


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "  ", "-lead",
                                 "x" * 65, "has space"])
def test_a_malformed_epoch_is_refused_not_sanitised(monkeypatch, bad):
    monkeypatch.setenv(BUD.EPOCH_ENV, bad)
    if bad.strip() == "":
        assert BUD.probe_epoch() is None
        return
    with pytest.raises(BUD.BudgetNotAuthorized):
        BUD.probe_epoch()


def test_the_archive_is_a_copy_and_the_original_survives():
    BUD.bind_window(window=3, slug=SLUG3, session_id=SID)
    src = BUD.binding_path(session_id=SID)
    out = BUD.archive_binding(session_id=SID, label="w3-2200z",
                              reason="probe complete",
                              evidence={"n_outstanding": 0})
    assert out["archived"] is True
    assert os.path.exists(src), "the original must NOT be deleted"
    assert out["original_untouched"] is True
    rec = json.load(open(out["archive_path"], encoding="utf8"))
    assert rec["window"] == 3 and rec["slug"] == SLUG3
    assert rec["original_binding"]["window"] == 3
    assert rec["ORIGINAL_WAS_NOT_DELETED_OR_REWRITTEN"] is True
    assert rec["THIS_AUTHORIZES_NOTHING"] is True
    assert rec["evidence_at_retirement"]["n_outstanding"] == 0
    # and the live binding still reads exactly as before
    assert BUD.read_binding(session_id=SID)["window"] == 3


def test_archiving_twice_under_one_label_is_refused():
    BUD.bind_window(window=3, slug=SLUG3, session_id=SID)
    BUD.archive_binding(session_id=SID, label="w3", reason="r")
    again = BUD.archive_binding(session_id=SID, label="w3", reason="r")
    assert again["archived"] is False
    assert again["why"] == "ARCHIVE_ALREADY_EXISTS_NOT_OVERWRITTEN"


def test_archiving_nothing_is_not_an_error_and_writes_nothing():
    out = BUD.archive_binding(session_id=SID, label="none", reason="r")
    assert out["archived"] is False
    assert out["why"].startswith("NO_BINDING_AT_")


def test_an_invalid_archive_label_is_refused():
    BUD.bind_window(window=3, slug=SLUG3, session_id=SID)
    with pytest.raises(BUD.BudgetNotAuthorized):
        BUD.archive_binding(session_id=SID, label="../etc/passwd",
                            reason="r")


def test_attempts_in_the_new_window_start_at_zero_without_any_reset():
    """The three-attempt budget counts the NEW window's own latch slots.

    No counter is reset: window 4 simply has no slot files yet, while
    window 3's remain on disk exactly as the completed probe left them.
    """
    BUD.bind_window(window=3, slug=SLUG3, session_id=SID)
    L.consume_attempt(window=3, session_id=SID, slug=SLUG3,
                      side="OUTCOME_SIDE_NO", price="0.02", qty="0.13")
    assert L.window_state(window=3,
                          session_id=SID)["attempts_spent"] == 1
    BUD.bind_window(window=4, slug=SLUG4, session_id=SID, epoch="e")
    assert L.window_state(window=4,
                          session_id=SID)["attempts_spent"] == 0
    # and window 3's record is still there
    assert L.window_state(window=3,
                          session_id=SID)["attempts_spent"] == 1
