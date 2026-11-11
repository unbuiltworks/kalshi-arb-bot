"""The mint script's window count is a parameter, and the printed block must agree.

No network, no install, no arming: `mint` builds a dict and `validate` runs
the real gate read-only in a throwaway environment.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os

import pytest

from src import l5_campaign_approval as CA

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "l5_mint_campaign_194.py")
_spec = importlib.util.spec_from_file_location("l5_mint_campaign_194", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

NOW = dt.datetime(2026, 11, 6, 2, 0, 7, tzinfo=dt.timezone.utc)


# ------------------------------------------------------ the default is intact
def test_the_default_is_still_eight_windows():
    assert M.WINDOWS == 8
    doc = M.mint(now=NOW, nonce="a" * 32)
    assert doc["max_windows"] == 8
    assert len(doc["window_starts"]) == 8


# ------------------------------------------------------- the three-window ask
def test_three_windows_mints_three_consecutive_quarter_hours():
    doc = M.mint(now=NOW, nonce="b" * 32, windows=3)
    assert doc["max_windows"] == 3
    starts = [dt.datetime.fromisoformat(s) for s in doc["window_starts"]]
    assert len(starts) == 3
    assert [s.isoformat() for s in starts] == [
        "2026-11-06T02:15:00+00:00",
        "2026-11-06T02:30:00+00:00",
        "2026-11-06T02:45:00+00:00"]
    # consecutive, 900 s apart, all strictly after `now`
    for a, b in zip(starts, starts[1:]):
        assert (b - a).total_seconds() == M.WINDOW_PERIOD_S
    assert all(s > NOW for s in starts)


def test_max_windows_in_the_document_matches_the_window_start_count():
    for n in (1, 2, 3, 5, 8):
        doc = M.mint(now=NOW, nonce="c" * 32, windows=n)
        assert doc["max_windows"] == n == len(doc["window_starts"])


def test_the_opening_bound_is_stated_three_ways_so_none_is_guessed():
    doc = M.mint(now=NOW, nonce="d" * 32, windows=3)
    assert doc["starts_utc"] == doc["window_starts"][0]
    assert doc["last_open_utc"] == doc["window_starts"][-1]


def test_expiry_covers_the_last_window_close_plus_grace():
    doc = M.mint(now=NOW, nonce="e" * 32, windows=3)
    last = dt.datetime.fromisoformat(doc["last_open_utc"])
    exp = dt.datetime.fromisoformat(doc["expires_utc"])
    assert (exp - last).total_seconds() == M.WINDOW_PERIOD_S + M.GRACE_S


def test_every_window_start_is_before_expiry():
    doc = M.mint(now=NOW, nonce="f" * 32, windows=3)
    exp = dt.datetime.fromisoformat(doc["expires_utc"])
    assert all(dt.datetime.fromisoformat(s) < exp
               for s in doc["window_starts"])


# ------------------------------------------------- the real gate accepts it
def test_a_three_window_document_passes_the_real_approval_gate():
    doc = M.mint(windows=3)                 # issued now, so not stale
    st, _raw = M.validate(doc)
    assert st.get("approved") is True, st
    assert st["scope"]["max_windows"] == 3


def test_every_authorized_window_is_openable():
    doc = M.mint(windows=3)
    rows = M.opening_check(doc)
    assert len(rows) == 3
    assert all(r["openable"] for r in rows), rows


def test_the_paired_only_scope_is_stated_in_the_note():
    doc = M.mint(now=NOW, nonce="g" * 32, windows=3)
    note = doc["note"].lower()
    assert "paired-only" in note
    assert "probability model is disabled" in note


# -------------------------------------------------------- the ceiling guard
def test_the_script_refuses_to_widen_the_code_ceiling():
    with pytest.raises(SystemExit):
        M._windows_from_argv(["--windows", str(CA.MAX_WINDOWS + 1)])


def test_a_nonpositive_window_count_is_refused():
    for bad in ("0", "-1"):
        with pytest.raises(SystemExit):
            M._windows_from_argv(["--windows", bad])


@pytest.mark.parametrize("argv,want", [
    (["--windows", "3"], 3),
    (["--windows=3"], 3),
    ([], 8),
    (["--windows", "8"], 8),
])
def test_the_window_argument_parses_both_spellings(argv, want):
    assert M._windows_from_argv(argv) == want


# ------------------------------------- the limits the owner approved, unchanged
def test_the_approved_limits_are_at_or_inside_every_code_ceiling():
    doc = M.mint(now=NOW, nonce="h" * 32, windows=3)
    assert doc["max_total_exposure_usd"] == str(CA.MAX_TOTAL_EXPOSURE_USD)
    assert doc["max_daily_loss_usd"] == str(CA.MAX_DAILY_LOSS_USD)
    assert doc["series"] == CA.ALLOWED_SERIES[0]
    assert doc["max_windows"] <= CA.MAX_WINDOWS
    span = (dt.datetime.fromisoformat(doc["expires_utc"])
            - dt.datetime.fromisoformat(doc["issued_utc"])).total_seconds()
    assert span <= CA.MAX_CAMPAIGN_S


def test_the_completion_reserve_still_holds_for_every_permitted_open():
    worst, ok, total_ok, per = M.reserve_holds()
    assert ok and total_ok
    assert per == 2
    assert worst >= 0


def test_minting_installs_nothing():
    """The script must not leave the approval variable set behind."""
    before = os.environ.get(CA.APPROVAL_ENV)
    M.validate(M.mint(now=NOW, nonce="i" * 32, windows=3))
    assert os.environ.get(CA.APPROVAL_ENV) == before


# ------------------------------------- the campaign id must be the engine's
def test_the_printed_campaign_id_is_the_one_the_engine_keys_on():
    """`nonce_sid` is what the ledgers, refuse_arming and telemetry use.

    The script printed "camp1." + sha256(nonce)[:24] instead, a string no
    gate ever computes, so an owner authorizing that id named something that
    appears nowhere in the running system.
    """
    import hashlib
    doc = M.mint(now=NOW, nonce="j" * 32, windows=3)
    assert CA.nonce_sid(doc["nonce"]) == "camp1." + doc["nonce"]
    # and the discarded derivation is genuinely different
    assert CA.nonce_sid(doc["nonce"]) != "camp1." + hashlib.sha256(
        doc["nonce"].encode()).hexdigest()[:24]


def test_the_mint_scripts_main_uses_nonce_sid():
    import inspect
    src = inspect.getsource(M)
    assert "CA.nonce_sid(doc[\"nonce\"])" in src
    assert 'hashlib.sha256(\n        doc["nonce"]' not in src
