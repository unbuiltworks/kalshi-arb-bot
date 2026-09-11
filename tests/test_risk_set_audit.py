"""The risk set must be visible, and leaving the scheduler must be noticed.

THE INCIDENT. aec-county-not-ham-2026-09-08 was held and quarantined at
2026-09-11T12:03:20Z and absent from `held` by 13:18:11Z, with no settlement
row. Its state could not be established from the logs at all -- not because
the evidence was ambiguous but because **the running probe never emitted its
risk set**. `unsettled` appears only in `rung_markets_reached`, which a
continuous campaign never reaches, and in the shutdown_idle budget re-price,
which needs a halt first.

THE CORRECTION THIS MODULE ENCODES. My first reading was that combined_loss
prices `held`, so the position had dropped out of the daily budget. That was
wrong: every risk path keys off unsettled_slugs(), and a market leaves that
set only when the settlement auditor records a venue POSITION_RESOLUTION.
Scheduler state cannot erase exposure. So this is an ALARM, not a stop, and
the failure it watches for is conservative -- over-charging a resolved
market, not under-charging a live one.
"""
from __future__ import annotations

from src import pmus_risk_set_audit as rsa

CRICKET = "aec-county-not-ham-2026-09-08"
NICKJA = "aec-atp-nickja-moeech-2026-09-11"


# ------------------------------------------------------------- the alarm
def test_the_cricket_transition_alarms():
    """Exactly the 12:03 -> 13:18 shape: was scheduled, now absent, still
    in the risk-bearing set."""
    got = rsa.detect(prev_held=[CRICKET], held=[NICKJA],
                     unsettled=[CRICKET, NICKJA])
    assert len(got) == 1
    a, = got
    assert a["slug"] == CRICKET
    assert a["alarm"] == "held_absent_while_risk_bearing"
    assert a["terminal_settlement_recorded"] is False
    assert a["action"].startswith("none taken")


def test_a_market_that_settled_does_not_alarm():
    """It left `held` AND left the risk set, which is the ordinary,
    correct lifecycle: the settlement row is what removes it from
    unsettled_slugs()."""
    assert rsa.detect([CRICKET], [NICKJA], [NICKJA]) == []


def test_a_market_still_scheduled_does_not_alarm():
    assert rsa.detect([CRICKET], [CRICKET, NICKJA], [CRICKET, NICKJA]) == []


def test_the_first_cycle_cannot_alarm():
    """Seeded empty on purpose: "we have not looked yet" is not a
    transition, and a false alarm on every restart would train us to ignore
    the row that matters."""
    assert rsa.detect([], [NICKJA], [CRICKET, NICKJA]) == []


def test_several_departures_are_each_named():
    got = rsa.detect([CRICKET, NICKJA], [], [CRICKET, NICKJA])
    assert [a["slug"] for a in got] == sorted([CRICKET, NICKJA])


def test_the_alarm_says_it_is_an_accounting_signal_not_uncovered_risk():
    """The wording is load-bearing. An operator reading this at 3am must
    not page themselves over an uncovered position that is in fact still
    fully charged."""
    a, = rsa.detect([CRICKET], [], [CRICKET])
    assert "STILL charged" in a["reading"]
    assert "not an" in a["reading"] and "uncovered-risk" in a["reading"]


# ---------------------------------------------------------- the snapshot
def test_the_snapshot_separates_carrying_from_scheduling():
    s = rsa.snapshot(unsettled=[CRICKET, NICKJA], held=[NICKJA],
                     combined_loss_usd="-3.21",
                     daily_loss_stop_usd="-15.00")
    assert s["risk_bearing"] == sorted([CRICKET, NICKJA])
    assert s["scheduled"] == [NICKJA]
    assert s["charged_not_scheduled"] == [CRICKET]
    assert s["scheduled_not_charged"] == []
    assert s["n_risk_bearing"] == 2 and s["n_scheduled"] == 1
    assert s["combined_loss_usd"] == "-3.21"


def test_the_other_direction_is_reported_too():
    """The venue shows a position the ledger has already settled. Rarer and
    more surprising, so it gets its own field rather than being folded in."""
    s = rsa.snapshot(unsettled=[], held=[NICKJA])
    assert s["scheduled_not_charged"] == [NICKJA]
    assert s["charged_not_scheduled"] == []


def test_the_label_states_which_set_prices_risk():
    s = rsa.snapshot([], [])
    assert "unsettled_slugs" in s["labels"]
    assert "never over" in s["labels"]


# ------------------------------------------------------------- emission
def test_the_snapshot_is_emitted_on_change_only():
    a = rsa.snapshot([CRICKET], [CRICKET])
    b = rsa.snapshot([CRICKET], [CRICKET])
    c = rsa.snapshot([CRICKET], [])
    assert rsa.changed(None, a) is True, "the first row always emits"
    assert rsa.changed(a, b) is False, "an unchanged row every 15s buries it"
    assert rsa.changed(b, c) is True


# ------------------------------------------------- read-only, structurally
def test_the_module_cannot_act():
    """No venue call, no order, no cap, no gate. An audit that can change
    behaviour is not an audit."""
    import inspect
    src = inspect.getsource(rsa)
    for banned in ("request(", "place_limit", "submit", "cancel", "Transport",
                   "set_concurrent_owned", "daily_loss_stop(", "os.environ"):
        assert banned not in src, banned


def test_it_is_pure_and_does_not_mutate_its_inputs():
    unsettled, held, prev = [CRICKET, NICKJA], [NICKJA], [CRICKET]
    rsa.snapshot(unsettled, held)
    rsa.detect(prev, held, unsettled)
    assert unsettled == [CRICKET, NICKJA]
    assert held == [NICKJA] and prev == [CRICKET]


# --------------------------------------------------------- wired into the loop
def test_the_campaign_loop_emits_it_and_cannot_be_broken_by_it():
    """Additive logging only: the audit is wrapped so that an audit failure
    logs and continues rather than stopping a live money campaign."""
    import inspect
    from src import pmus_l5_continuous_session as C
    loop = inspect.getsource(C.run_campaign)
    assert "rsa.snapshot(" in loop and "rsa.detect(" in loop
    assert '_log("risk_set"' in loop and '_log("risk_set_alarm"' in loop
    assert '_log("risk_set_error"' in loop, "an audit must never halt the loop"


def test_the_audit_is_packaged_in_the_probe_image():
    """Module-scope import in the entry module: absent from the image, the
    probe does not degrade, it fails to import. The 565b4cde shape."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    docker = (root / "deploy/pmus_spring_l5/Dockerfile").read_text()
    rail = (root / "deploy/pmus_spring_l5/railway.json").read_text()
    assert "COPY src/pmus_risk_set_audit.py" in docker
    assert "src/pmus_risk_set_audit.py" in rail
