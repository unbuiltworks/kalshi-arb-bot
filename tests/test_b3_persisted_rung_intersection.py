"""OWNER AUTHORIZATION 2026-09-16 "B3": stored rung state may only TIGHTEN.

THE PRODUCTION DEFECT, reproduced here. `halted_envelope()` disclosed
`persisted_rung: "R3L"` on deployment 10c2d10d while `active_rung` read CP1.
`run_campaign` restores durable state on the ARMED path only --

    persisted = load_campaign_state() if armed and t is not None else {}
    if persisted.get("rung"):
        _apply_rung(str(persisted["rung"]))

-- and `_apply_rung` REPLACED the image-pinned rung. R3L is the 2026-09-16
breach's own end state, a $7.50 rung, so a process pinned to CP1 would have
armed as R3L. That is why the restart tests below are not decoration: the
halted process and the armed process disagreed, and only the armed one traded.

The invariant: the effective configuration is the tightest bound of the pinned
rung, any permissible stored restriction, and the immutable live mandate. A
stored rung may make the probe smaller. It may never make it larger, and it may
never turn CP1 into R3L.
"""
import itertools
import json

import pytest

from decimal import Decimal as D

from src import pmus_l5_continuous_session as cont
from src import pmus_l5_ramp_ladder as ld
from src import pmus_micro_executor as mx
from src import pmus_spring_l5_session as spring


# --------------------------------------------------------------------------
# the dimension table itself

def test_every_rung_key_is_classified():
    """THE GUARD THAT KEEPS THIS HONEST AS THE LADDER GROWS.

    A key nobody sorted into RISK_DIMENSIONS, UNORDERED_LEVERS or IDENTITY_KEYS
    is silently never taken from a stored rung. That is fail-closed, which is
    right -- but it also means a NEW risk lever could be added to a rung and
    quietly become un-tightenable, or (worse) be assumed handled. So adding an
    unclassified key fails the suite here instead.
    """
    classified = (set(ld.RISK_DIMENSIONS) | set(ld.UNORDERED_LEVERS)
                  | set(ld.IDENTITY_KEYS))
    seen = set()
    for r in ld.ALL_RUNGS:
        seen |= set(r)
    assert not (seen - classified), (
        "unclassified rung key(s) -- sort each into RISK_DIMENSIONS (with its "
        "tighter direction and its ABSENT default), UNORDERED_LEVERS, or "
        f"IDENTITY_KEYS: {sorted(seen - classified)}")


def test_the_ladder_is_not_totally_ordered_so_name_order_cannot_be_used():
    """Guards the design, not the code: the owner forbade choosing by
    name/order unless the ordering is PROVEN no-less-permissive. It is not --
    R2C2 allows concurrency 2 where R3L allows 1, while R3L allows the larger
    per-market cap. Neither dominates, so a per-dimension comparison is the
    only correct rule, and this fails if that stops being true."""
    r2c2, r3l = ld.rung("R2C2"), ld.rung("R3L")
    assert int(r2c2["concurrency"]) > int(r3l["concurrency"])
    assert D(r2c2["per_market_cap_usd"]) < D(r3l["per_market_cap_usd"])


# --------------------------------------------------------------------------
# tightest(): every dimension, both directions

def test_the_production_pair_cp1_x_r3l_never_loosens_any_dimension():
    pinned, stored = ld.rung("CP1"), ld.rung("R3L")
    eff = ld.tightest(pinned, stored)
    for k in ld.RISK_DIMENSIONS:
        p, e = ld._effective(pinned, k), ld._effective(eff, k)
        assert ld._tighter_of(k, p, e) == e, (
            f"{k} loosened: pinned={p} -> effective={e}")


def test_the_production_pair_keeps_every_authorized_cp1_limit():
    """The exact numbers the owner authorized, read through the accessors the
    live path uses rather than off the dict."""
    eff = ld.tightest(ld.rung("CP1"), ld.rung("R3L"))
    assert eff["id"] == "CP1"                          # never renamed to R3L
    assert D(eff["per_market_cap_usd"]) == D("5.00")
    assert int(eff["residual_units"]) == 3             # <= $3.00 at any price
    assert ld.daily_loss_stop(eff) == D("-6.00")       # not R3L's -15.00
    assert int(eff["concurrency"]) == 1
    assert ld.markets_per_campaign(eff) == 3
    assert int(eff["max_actions_per_session"]) == 6
    assert D(eff["per_market_cap_usd"]) * 3 == D("15.00")   # total spend


def test_no_r3l_leg_behaviour_leaks_into_cp1():
    """The owner named these two explicitly.

    CP1 states NEITHER lever, and absence here is not "no lever" -- it is the
    $0.01/$0.02 micro default, which is 250x and 162x TIGHTER than R3L's
    $2.50/$3.25. Comparing raw dict values would have read CP1's absence as
    unknown and let R3L's sizing through; comparing EFFECTIVE values keeps it
    out. This is the concrete reason the table stores an `absent` value.
    """
    eff = ld.tightest(ld.rung("CP1"), ld.rung("R3L"))
    assert ld.target_notional(eff) == D("0.01")
    assert ld.max_leg_notional(eff) == D("0.02")
    assert ld.target_notional(eff) == ld.target_notional(ld.rung("CP1"))
    assert ld.max_leg_notional(eff) == ld.max_leg_notional(ld.rung("CP1"))
    # and it really is what the order-sizing function would use
    q_eff = mx.choose_micro_quantity(D("0.45"), D("0.01"),
                                     ld.target_notional(eff),
                                     ld.max_leg_notional(eff))
    q_r3l = mx.choose_micro_quantity(D("0.45"), D("0.01"),
                                     ld.target_notional(ld.rung("R3L")),
                                     ld.max_leg_notional(ld.rung("R3L")))
    assert q_eff < q_r3l
    assert q_eff * D("0.45") <= D("0.02")


@pytest.mark.parametrize("dim,pin,store,expect", [
    ("per_market_cap_usd",      "5.00",  "7.50",  "5.00"),
    ("per_market_cap_usd",      "5.00",  "0.05",  "0.05"),   # tighter wins
    ("residual_units",          3,       6,       3),
    ("residual_units",          6,       3,       3),        # tighter wins
    ("daily_loss_stop_usd",     "-6.00", "-15.00", "-6.00"),
    ("daily_loss_stop_usd",     "-6.00", "-1.00",  "-1.00"),  # tighter wins
    ("concurrency",             1,       2,       1),
    ("concurrency",             2,       1,       1),        # tighter wins
    ("markets_per_campaign",    3,       6,       3),
    ("markets_per_campaign",    3,       1,       1),        # tighter wins
    ("markets_per_campaign",    3,       None,    3),        # None = continuous
    ("max_actions_per_session", 6,       12,      6),
    ("max_actions_per_session", 6,       4,       4),        # tighter wins
    ("campaigns_per_day",       1,       2,       1),
    ("target_notional_usd",     None,    "2.50",  "0.01"),   # absent = micro
    ("max_leg_notional_usd",    None,    "3.25",  "0.02"),   # absent = micro
    ("max_unmatched_usd",       None,    "2.50",  "2.50"),   # absent = no cap
    ("pair_cost_max",           None,    "1.00",  "0.98"),   # absent = 0.98
    ("complement_pricing",      None,    "l5_touch", "fee_safe"),
    ("complement_pricing",      "l5_touch", "fee_safe", "fee_safe"),
])
def test_each_dimension_takes_the_tighter_value(dim, pin, store, expect):
    """Both directions on every money-moving dimension the owner enumerated.

    The `tighter wins` rows are the owner's explicit inverse requirement: a
    genuinely tighter stored configuration must still be able to tighten.
    """
    pinned = {"id": "PIN", "per_market_cap_usd": "5.00", "residual_units": 3,
              "markets_per_campaign": 3, "campaigns_per_day": 1,
              "max_actions_per_session": 6, "concurrency": 1}
    stored = dict(pinned, id="STORED")
    pinned[dim], stored[dim] = pin, store
    eff = ld.tightest(pinned, stored)
    got = ld._effective(eff, dim)
    want = (D(expect) if ld.RISK_DIMENSIONS[dim]["kind"] == "decimal"
            else (int(expect) if ld.RISK_DIMENSIONS[dim]["kind"] == "int"
                  and expect is not None else expect))
    assert got == want, f"{dim}: pinned={pin} stored={store} -> {got}, want {want}"
    assert eff["id"] == "PIN"


def test_an_unordered_lever_is_never_taken_from_stored_state():
    """rest_policy / rest_floor_s have no proven ordering. Inventing one to
    look complete is how a "tightening" silently loosens something, so the
    pinned value stands in BOTH directions and that is stated, not hidden."""
    pinned = {"id": "PIN", "per_market_cap_usd": "5.00", "residual_units": 3,
              "markets_per_campaign": 3, "campaigns_per_day": 1,
              "max_actions_per_session": 6, "concurrency": 1}
    for value in ("model", "fixed30"):
        eff = ld.tightest(pinned, dict(pinned, id="S", rest_policy=value,
                                       rest_floor_s=1))
        assert "rest_policy" not in eff and "rest_floor_s" not in eff
        assert ld.rest_policy(eff) == "fixed30"


def test_no_stored_rung_in_the_whole_ladder_can_loosen_any_pinned_rung():
    """The sweep, not one pair: every (pinned, stored) combination in the
    ladder, every dimension. One pair passing proves one pair."""
    for pinned in ld.ALL_RUNGS:
        for stored in ld.ALL_RUNGS:
            eff = ld.tightest(pinned, stored)
            assert eff["id"] == pinned["id"]
            for k in ld.RISK_DIMENSIONS:
                p, e = ld._effective(pinned, k), ld._effective(eff, k)
                assert ld._tighter_of(k, p, e) == e, (
                    f"{pinned['id']} x {stored['id']}: {k} loosened {p} -> {e}")


def test_absent_stored_state_changes_nothing():
    for empty in (None, {}):
        assert ld.tightest(ld.rung("CP1"), empty) == dict(ld.rung("CP1"))


def test_an_unknown_stored_id_is_not_silently_treated_as_r1():
    """rung() falls back to R1 for an unknown id -- right for an env typo,
    wrong for stored state: intersecting CP1 against R1 would shrink the
    owner's $5.00 envelope to $0.05 without saying so. known_rung() returns
    None instead, and the caller logs it."""
    assert ld.known_rung("NOT-A-RUNG") is None
    assert ld.rung("NOT-A-RUNG")["id"] == "R1"        # the old behaviour
    eff = ld.tightest(ld.rung("CP1"), ld.known_rung("NOT-A-RUNG"))
    assert D(eff["per_market_cap_usd"]) == D("5.00")


# --------------------------------------------------------------------------
# the armed restoration path -- where the defect actually appears

def _harness(monkeypatch, tmp_path):
    from tests.test_pmus_l5_continuous_session import _two_run_harness
    fake = _two_run_harness(monkeypatch, tmp_path, {"v": 100.0}, {"trades": 5})
    monkeypatch.setattr(cont, "status_has_trades", lambda: True)
    monkeypatch.setattr(cont, "CAMPAIGN_APPROVALS_FILE", tmp_path / "none.json")
    monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: {
        "seen": 0, "cancelled": [], "foreign": [], "errors": []})
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])
    return fake


def _run(fake, seen=None, ticks_n=5):
    def session(t, clock, sleep, wall, slug, gate_ok, deadline_s, **kw):
        if seen is not None:
            seen.append({
                "cap": str(spring.session_notional_cap()),
                "residual": spring.max_residual_units(),
                "stop": str(spring.daily_loss_stop()),
                "target": str(ld.target_notional()),
                "leg": str(ld.max_leg_notional()),
                "rung": ld.active()["id"],
            })
        return {"status": "SPRING_NO_FILL", "slug": slug, "orders": [],
                "decisions": [], "inventory": {"up": "0", "dn": "0"}}
    ticks = itertools.chain([0.0] + [1.0] * ticks_n, itertools.repeat(1000.0))
    return cont.run_campaign(fake, clock=lambda: next(ticks),
                             sleep=lambda s: None, wall=lambda: 1005.0,
                             campaign_s=500.0, session_runner=session)


def _save_state(rung_id):
    cont.save_campaign_state({
        "saved_utc": "x", "rung": rung_id, "campaign_id": "CP1-PROBE",
        "stop_rule": "rung_markets", "previous_campaigns": [],
        "filled_markets": [], "settled_here": [], "outage": None,
        "liveness_baseline": {}, "recovery_gate": None, "burst": None})


def test_pinned_cp1_with_persisted_r3l_arms_as_cp1(monkeypatch, tmp_path):
    """THE PRODUCTION FIXTURE. Exactly the state on the volume at 19:00Z.

    Driven through run_campaign's ARMED restore, because that is the only path
    that reads durable state -- asserting on tightest() alone would have
    passed while production still armed as R3L.
    """
    monkeypatch.setenv(ld.RUNG_ENV, "CP1")
    monkeypatch.setenv(cont.RUNG_MARKETS_ENV, "3")
    ld.set_active(None)
    fake = _harness(monkeypatch, tmp_path)
    _save_state("R3L")

    seen = []
    rep = _run(fake, seen)

    assert rep["rung"]["ladder_rung"] == "CP1", "CP1 was replaced by stored state"
    assert rep["rung"]["per_market_cap_usd"] == "5.00"
    assert rep["rung"]["residual_cap_units"] == 3
    assert rep["rung"]["daily_loss_stop_usd"] == "-6.00"
    assert rep["rung"]["concurrency"] == 1
    assert rep["rung"]["markets_allowed"] == 3
    assert rep["rung"]["worst_case_usd"] == "15.00"
    # no R3L leg behaviour
    assert rep["rung"]["per_action_target_usd"] == "0.01"
    assert rep["rung"]["per_leg_max_usd"] == "0.02"
    # and the SESSION layer -- the thing that actually sizes an order -- saw
    # the same numbers, not just the report.
    # NON-EMPTY IS PART OF THE ASSERTION: a `for s in seen` over an empty list
    # passes vacuously, and a campaign that never ran a session would look
    # identical to one that ran under CP1.
    assert seen, "no session ran -- the armed restore path was never exercised"
    for s in seen:
        assert s == {"cap": "5.00", "residual": 3, "stop": "-6.00",
                     "target": "0.01", "leg": "0.02", "rung": "CP1"}, s


def test_the_defect_would_have_been_caught_here(monkeypatch, tmp_path):
    """Guards the fixture above: if _apply_rung went back to replacing, these
    are the values production would have run. Asserting the OLD behaviour is
    wrong keeps the test above from passing vacuously."""
    r3l = ld.rung("R3L")
    assert D(r3l["per_market_cap_usd"]) == D("7.50")
    assert int(r3l["residual_units"]) == 6
    assert ld.daily_loss_stop(r3l) == D("-15.00")
    assert ld.target_notional(r3l) == D("2.50")
    assert ld.max_leg_notional(r3l) == D("3.25")


def test_a_tighter_persisted_rung_still_tightens_cp1(monkeypatch, tmp_path):
    """The owner's inverse requirement, through the same armed path. R1 is
    genuinely tighter on spend ($0.05), so it must still bind."""
    monkeypatch.setenv(ld.RUNG_ENV, "CP1")
    monkeypatch.setenv(cont.RUNG_MARKETS_ENV, "3")
    ld.set_active(None)
    fake = _harness(monkeypatch, tmp_path)
    _save_state("R1")

    seen = []
    rep = _run(fake, seen)
    assert rep["rung"]["ladder_rung"] == "CP1"          # identity still pinned
    assert rep["rung"]["per_market_cap_usd"] == "0.05"  # but genuinely tighter
    assert rep["rung"]["residual_cap_units"] == 3
    # R1 states no stop, so its EFFECTIVE stop is the absolute -$1.00, which
    # is tighter than CP1's -$6.00 and must bind
    assert rep["rung"]["daily_loss_stop_usd"] == "-1.00"
    assert seen, "no session ran -- the armed restore path was never exercised"
    for s in seen:
        assert s["cap"] == "0.05" and s["rung"] == "CP1"


def test_a_tighter_market_allowance_survives_the_env_pin(monkeypatch, tmp_path):
    """The env pin outranks a stored rung wanting MORE markets, but must not
    discard one wanting FEWER. R5FT allows 1 market; the env says 3."""
    monkeypatch.setenv(ld.RUNG_ENV, "CP1")
    monkeypatch.setenv(cont.RUNG_MARKETS_ENV, "3")
    ld.set_active(None)
    fake = _harness(monkeypatch, tmp_path)
    _save_state("R5FT")
    rep = _run(fake)
    assert rep["rung"]["markets_allowed"] == 1, "a tighter allowance was discarded"
    assert rep["rung"]["ladder_rung"] == "CP1"


def test_the_intersection_survives_a_restart(monkeypatch, tmp_path):
    """RESTART/RESUME EXPLICITLY -- the owner called this out because it is
    where the defect appears. Run 1 saves state, the process 'dies' (the
    ladder's process-local ACTIVE is cleared, as a fresh boot would), run 2
    restores from the same durable directory."""
    monkeypatch.setenv(ld.RUNG_ENV, "CP1")
    monkeypatch.setenv(cont.RUNG_MARKETS_ENV, "3")
    ld.set_active(None)
    fake = _harness(monkeypatch, tmp_path)
    _save_state("R3L")

    rep1 = _run(fake)
    assert rep1["rung"]["ladder_rung"] == "CP1"
    saved = json.loads((tmp_path / "l5_campaign_state.json").read_text())
    assert saved["rung"] == "CP1", (
        "the campaign must persist what it RAN, or the next boot re-reads R3L "
        "forever and the intersection has to keep saving us")

    ld.set_active(None)                       # a fresh process
    seen2 = []
    rep2 = _run(fake, seen2)
    assert rep2["rung"]["ladder_rung"] == "CP1"
    assert rep2["rung"]["per_market_cap_usd"] == "5.00"
    assert rep2["rung"]["residual_cap_units"] == 3
    assert rep2["rung"]["daily_loss_stop_usd"] == "-6.00"
    assert seen2, "no session ran after the restart"
    for s in seen2:
        assert s["target"] == "0.01" and s["leg"] == "0.02"


def test_a_stale_r3l_survives_many_restarts_without_ever_winning(
        monkeypatch, tmp_path):
    """Re-seed the breach value on EVERY boot, as a volume nobody cleared
    would. The pin must win every time, not just the first."""
    monkeypatch.setenv(ld.RUNG_ENV, "CP1")
    monkeypatch.setenv(cont.RUNG_MARKETS_ENV, "3")
    fake = _harness(monkeypatch, tmp_path)
    for _ in range(3):
        ld.set_active(None)
        _save_state("R3L")
        rep = _run(fake)
        assert rep["rung"]["ladder_rung"] == "CP1"
        assert rep["rung"]["per_market_cap_usd"] == "5.00"
        assert rep["rung"]["daily_loss_stop_usd"] == "-6.00"


def test_the_protection_requires_an_explicit_pin_and_the_image_supplies_one():
    """THE ONE PLACE THIS GUARD CAN LAPSE, pinned down.

    With PMUS_SPRING_RUNG unset there is no pinned configuration, so the
    restore keeps its historical behaviour -- intersecting against the R1
    DEFAULT would clamp every campaign to R1 and break the owner's
    approval-advance-then-restart path, which B3 never named. That makes the
    env pin load-bearing, so this asserts the probe image really carries it:
    if someone drops the ENV line, the protection would quietly stop applying
    in production and THIS fails, not just the image test.
    """
    import os as _os
    df = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                       "deploy", "l5_r1_probe", "Dockerfile")
    env = [l.strip() for l in open(df, encoding="utf-8").read().splitlines()
           if l.strip().startswith("PMUS_SPRING_RUNG=")]
    assert env == ["PMUS_SPRING_RUNG=CP1 \\"], env


def test_without_a_pin_the_restore_keeps_its_historical_behaviour(
        monkeypatch, tmp_path):
    """The other branch, exercised rather than asserted in prose."""
    monkeypatch.delenv(ld.RUNG_ENV, raising=False)
    monkeypatch.delenv(cont.RUNG_MARKETS_ENV, raising=False)
    ld.set_active(None)
    fake = _harness(monkeypatch, tmp_path)
    _save_state("R2C")
    rep = _run(fake)
    assert rep["rung"]["ladder_rung"] == "R2C"
    assert rep["rung"]["per_market_cap_usd"] == "0.15"


def test_the_approval_gate_is_not_intersected(monkeypatch, tmp_path):
    """SCOPE, asserted rather than trusted. _apply_rung takes stored_state,
    and only the durable-state restore passes it. Intersecting the gate too
    was tried and reverted: it disabled the owner's advance mechanism, which
    B3 did not authorise. The gate keeps its own guard -- an approval must
    POSTDATE the active mandate."""
    src = open(cont.__file__, encoding="utf-8").read()
    assert src.count("stored_state=True") == 1, (
        "exactly one caller -- the durable-state restore -- may opt in")
    body = src.split("if persisted.get(\"rung\"):")[1][:400]
    assert "stored_state=True" in body
    gate = src.split("_apply_rung(str(appr[\"rung\"]))")
    assert len(gate) == 2, "the approval gate must still call it unintersected"


# --------------------------------------------------------------------------
# the two layers the owner said to preserve

def test_min_rung_mandate_is_preserved(monkeypatch):
    """B3 tightens the RUNG layer. It must not disturb the MANDATE layer --
    min(rung, mandate) is the 2026-09-16 safety property and still decides."""
    src = open(spring.__file__, encoding="utf-8").read()
    body = src.split("def session_notional_cap(")[1].split("\ndef ")[0]
    assert "min(rung" in body


@pytest.mark.real_mandate_env
def test_a_tighter_mandate_still_clamps_the_intersected_rung(monkeypatch):
    """End to end over both layers: effective rung $5.00, mandate $1.00."""
    monkeypatch.setenv("PMUS_SERVICE_ROLE", "")
    monkeypatch.setenv("PMUS_LIVE_MANDATE", json.dumps({
        "authorization_id": "B3-TEST", "authorized_at": "2026-01-01T00:00:00Z",
        "service": "test-suite", "strategy": "t", "strategy_version": "t",
        "max_markets": 1, "max_total_markets": 3,
        "max_spend_per_market": "1.00", "max_total_spend": "3.00",
        "max_residual": "3.00", "max_daily_realized_loss": "6.00",
        "expiry": "2999-01-01T00:00:00Z"}))
    ld.set_active_effective(ld.tightest(ld.rung("CP1"), ld.rung("R3L")))
    try:
        assert spring.session_notional_cap() == D("1.00")
    finally:
        ld.set_active(None)


def test_guarded_transport_is_untouched():
    """B3 authorised a rung-selection change and nothing else."""
    from src import pmus_live_mandate as m
    src = open(m.__file__, encoding="utf-8").read()
    assert "tightest" not in src and "set_active_effective" not in src
    assert "RISK_DIMENSIONS" not in src
