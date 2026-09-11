"""The OWNER'S ACCOUNT-WIDE daily loss stop.

OWNER 2026-09-11 21:52Z. The arming deploy booted, the re-arm gate read
combined_loss -15.1516 against R5F's -15.00 and refused ("rearm_refused ...
holding halted"). Told the day had ALREADY breached the original stop, and
offered stand down for today / apply -$30 account-wide now / stop and deal
with the 45.01-unit residual first, he chose "Apply -$30 as the account-wide
stop now".

WHY IT CANNOT BE A RUNG LEVER: the re-arm gate runs BEFORE any owner
approval is consumed, so it always reads the PREVIOUS campaign's rung --
R5F, -15.00. R5FT's own -30.00 never gets a chance to apply, because the
gate that would let the campaign reach its advance gate is the gate
refusing. A rung-level number cannot break a rung-level deadlock.
"""
from decimal import Decimal as D

from src import pmus_l5_ramp_ladder as L


def test_the_account_stop_is_the_number_the_owner_named():
    assert L.ACCOUNT_DAILY_LOSS_STOP == D("-30.00")


def test_it_overrides_every_rung_including_the_micro_ones():
    """THE COST OF "ACCOUNT-WIDE", asserted rather than discovered later.
    A rung's daily_loss_stop_usd is now its DECLARED figure and no longer
    its binding one -- the R1/R2C micro rungs carried -$1.00 and no longer
    halt there."""
    for rid in ("R1", "R4", "R5F", "R5FT"):
        assert L.daily_loss_stop(L.rung(rid)) == D("-30.00"), rid
    # and the declared rows are untouched, so the override is legible
    assert L.rung("R5F")["daily_loss_stop_usd"] == "-15.00"
    assert L.rung("R5FT")["daily_loss_stop_usd"] == "-30.00"


def test_it_cannot_be_widened_past_what_he_authorised(monkeypatch):
    """A later edit must not be able to enlarge the account's worst day on
    its own. -100 clamps back to the authorised -30."""
    monkeypatch.setattr(L, "ACCOUNT_DAILY_LOSS_STOP", D("-100.00"))
    assert L.daily_loss_stop(L.rung("R5FT")) == D("-30.00")


def test_a_tighter_account_stop_is_honoured(monkeypatch):
    """The clamp bounds LOOSENING only. Tightening the account stop must
    still work, or the owner could never pull risk back in."""
    monkeypatch.setattr(L, "ACCOUNT_DAILY_LOSS_STOP", D("-5.00"))
    assert L.daily_loss_stop(L.rung("R5FT")) == D("-5.00")


def test_clearing_it_returns_every_rung_to_its_own_stop(monkeypatch):
    """The account override is removable: set it to None and the rungs
    govern again, so this is not a one-way door."""
    monkeypatch.setattr(L, "ACCOUNT_DAILY_LOSS_STOP", None)
    assert L.daily_loss_stop(L.rung("R5F")) == D("-15.00")
    assert L.daily_loss_stop(L.rung("R5FT")) == D("-30.00")
    assert L.daily_loss_stop(L.rung("R1")) == L.DAILY_LOSS_STOP


def test_worst_case_reports_the_stop_in_force_not_the_declared_one():
    """A worst case that quoted a stop which would not actually halt the
    account would be the wrong number in the one place it matters."""
    assert L.worst_case(L.rung("R5F"))["daily_loss_stop_usd"] == "-30.00"
    assert L.worst_case(L.rung("R5FT"))["daily_loss_stop_usd"] == "-30.00"


def test_the_worst_day_is_the_stop_plus_one_in_flight_market():
    """What the owner was told when he chose it: roughly $60 against a
    ~$109 balance."""
    wc = L.worst_case(L.rung("R5FT"))
    worst_day = D(wc["daily_loss_stop_usd"]).copy_abs() + D(wc["per_market_usd"])
    assert worst_day == D("60.00")


def test_the_source_records_why_it_is_not_a_rung_lever():
    """If this reasoning leaves the source, the next person will "tidy" it
    into the rung table and reintroduce the deadlock."""
    import inspect
    src = inspect.getsource(L.daily_loss_stop)
    assert "ACCOUNT-WIDE" in src
    assert "before any rung decision" in src
