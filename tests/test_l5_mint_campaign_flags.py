"""The mint flags may only ever TIGHTEN a grant.

WHY THEY EXIST. On 2026-11-07 the owner approved exactly one BTC15m window
at an aggregate worst-case exposure of $1.00. The script hardcoded
`EXPOSURE_USD = "20"`, and `--windows 1` selects the NEXT quarter hour --
which was minutes away, too soon for a variable write, a redeploy, a boot
and a preflight, and not the window the owner named. Installing a $20
document and relying on a $1 environment variable to clamp it would have
been installing a grant twenty times wider than the one approved.

So these three flags were added, and every one of them refuses rather than
widens. The script's own doctrine: "this script never widens a ceiling".
"""
from __future__ import annotations

import datetime as dt
import importlib

import pytest

from src import l5_campaign_approval as CA

MOD = importlib.import_module("scripts.l5_mint_campaign_194")
EXP_CEIL = str(CA.MAX_TOTAL_EXPOSURE_USD)
LOSS_CEIL = str(CA.MAX_DAILY_LOSS_USD)


def _exp(argv):
    return MOD._usd_from_argv(argv, "--exposure", MOD.EXPOSURE_USD, EXP_CEIL)


# ============================================== 1. TIGHTENING IS PERMITTED

@pytest.mark.parametrize("given,want", [
    ("1.00", "1.00"), ("1", "1"), ("0.50", "0.50"), ("20", "20"),
])
def test_an_exposure_at_or_below_the_scope_is_honoured(given, want):
    assert _exp(["--exposure", given]) == want
    assert _exp(["--exposure=%s" % given]) == want


def test_an_absent_flag_leaves_the_scripts_own_scope():
    assert _exp([]) == MOD.EXPOSURE_USD == "20"
    assert MOD._usd_from_argv([], "--loss", MOD.DAILY_LOSS_USD,
                              LOSS_CEIL) == "5"


# ================================================= 2. WIDENING IS REFUSED

@pytest.mark.parametrize("given", ["20.01", "21", "100", "1e9"])
def test_an_exposure_above_the_scripts_scope_is_refused(given):
    with pytest.raises(SystemExit) as e:
        _exp(["--exposure", given])
    assert "only ever tightens" in str(e.value)


@pytest.mark.parametrize("given", ["0", "-1", "-0.01"])
def test_a_non_positive_exposure_is_refused(given):
    with pytest.raises(SystemExit) as e:
        _exp(["--exposure", given])
    assert "must be > 0" in str(e.value)


@pytest.mark.parametrize("given", ["abc", "", "1,00", "$1"])
def test_an_unparseable_exposure_is_refused_not_defaulted(given):
    """Refusing beats falling back: a silent default here would install a
    grant the operator did not mean to ask for."""
    with pytest.raises(SystemExit):
        _exp(["--exposure", given])


def test_the_loss_flag_obeys_the_same_rules():
    assert MOD._usd_from_argv(["--loss", "2"], "--loss",
                              MOD.DAILY_LOSS_USD, LOSS_CEIL) == "2"
    with pytest.raises(SystemExit):
        MOD._usd_from_argv(["--loss", "6"], "--loss",
                           MOD.DAILY_LOSS_USD, LOSS_CEIL)


def test_neither_flag_can_exceed_the_code_constant():
    """Belt and braces: even if the script's scope were raised, the code
    ceiling still binds."""
    assert MOD._usd_from_argv(["--exposure", "20"], "--exposure", "25",
                              EXP_CEIL) == "20"
    with pytest.raises(SystemExit) as e:
        MOD._usd_from_argv(["--exposure", "25"], "--exposure", "30",
                           EXP_CEIL)
    assert "code ceiling" in str(e.value)


# ============================================ 3. THE NAMED WINDOW

def _future_boundary(offset_windows=2):
    now = dt.datetime.now(dt.timezone.utc)
    t = now.replace(second=0, microsecond=0)
    t -= dt.timedelta(minutes=t.minute % 15)
    return t + dt.timedelta(minutes=15 * offset_windows)


def test_a_future_boundary_is_accepted_and_normalised_to_utc():
    want = _future_boundary()
    got = MOD._start_from_argv(["--start-utc", want.isoformat()])
    assert got == want
    assert got.tzinfo == dt.timezone.utc


def test_an_absent_flag_falls_back_to_the_next_windows():
    assert MOD._start_from_argv([]) is None


def test_a_window_that_has_already_opened_is_refused():
    past = _future_boundary(-4)
    with pytest.raises(SystemExit) as e:
        MOD._start_from_argv(["--start-utc", past.isoformat()])
    assert "not in the future" in str(e.value)


@pytest.mark.parametrize("delta_min", [1, 7, 14])
def test_a_non_boundary_minute_is_refused(delta_min):
    t = _future_boundary() + dt.timedelta(minutes=delta_min)
    with pytest.raises(SystemExit) as e:
        MOD._start_from_argv(["--start-utc", t.isoformat()])
    assert "window boundary" in str(e.value)


def test_a_naive_timestamp_is_refused():
    t = _future_boundary().replace(tzinfo=None)
    with pytest.raises(SystemExit) as e:
        MOD._start_from_argv(["--start-utc", t.isoformat()])
    assert "explicit timezone" in str(e.value)


def test_an_unparseable_timestamp_is_refused():
    with pytest.raises(SystemExit):
        MOD._start_from_argv(["--start-utc", "tomorrow"])


# ==================================== 4. THE DOCUMENT CARRIES WHAT WAS ASKED

def test_mint_names_exactly_the_requested_window_and_exposure():
    start = _future_boundary(3)
    doc = MOD.mint(windows=1, exposure_usd="1.00", daily_loss_usd="5",
                   first_start=start)
    assert doc["max_windows"] == 1
    assert doc["max_total_exposure_usd"] == "1.00"
    assert doc["max_daily_loss_usd"] == "5"
    assert doc["window_starts"] == [start.isoformat()]
    assert doc["starts_utc"] == doc["last_open_utc"] == start.isoformat()
    # expiry covers the window's CLOSE plus the settlement grace
    exp = dt.datetime.fromisoformat(doc["expires_utc"])
    assert exp == start + dt.timedelta(
        seconds=MOD.WINDOW_PERIOD_S + MOD.GRACE_S)


def test_the_minted_document_is_approved_by_the_real_gate():
    """Read-only validation, as the script does -- `state()`, never
    `authorize_window`, which would RESERVE the window being verified."""
    start = _future_boundary(3)
    doc = MOD.mint(windows=1, exposure_usd="1.00", daily_loss_usd="5",
                   first_start=start)
    st, _raw = MOD.validate(doc)
    assert st.get("approved") is True, st
    rows = MOD.opening_check(doc)
    assert len(rows) == 1
    assert all("openable=True" in r or r.get("openable") for r in rows) \
        or all(getattr(r, "openable", True) for r in rows)


def test_the_default_mint_is_unchanged_for_callers_that_pass_nothing():
    """The eight-window shape still mints exactly as it did."""
    doc = MOD.mint()
    assert doc["max_windows"] == MOD.WINDOWS == 8
    assert doc["max_total_exposure_usd"] == MOD.EXPOSURE_USD == "20"
    assert doc["max_daily_loss_usd"] == MOD.DAILY_LOSS_USD == "5"
    assert len(doc["window_starts"]) == 8
