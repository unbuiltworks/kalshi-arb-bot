"""The 1130z tape, as a regression fixture.

WHAT THIS ANCHORS. On 2026-09-27 the realized-only cap let four DOWN orders
through while realized residual sat inside a 1.80 cap; they filled within
seven seconds and took |residual| to 3.24. The tape of that window -- every
decision and every observed fill -- is checked in, and these tests assert
that the arithmetic still reproduces it.

WHAT IT CANNOT ANCHOR, stated because the temptation is obvious. Fills exist
only for the 38 orders the live run actually placed. For the 18 it refused
there is no fill data, and none is invented here. A rule that would have
TAKEN one of those 18 is unscorable on this tape -- that is not the same as
scoring well, and it is never counted as an improvement.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "l5_1130z_tape.json"


@pytest.fixture(scope="module")
def tape():
    return json.loads(FIXTURE.read_text())


def test_the_tape_is_intact(tape):
    assert tape["live"] == {"eligible": 56, "submitted": 38, "bound": 18,
                            "peak_realized_residual": "3.24",
                            "cap_in_force": "1.80"}
    assert len(tape["decisions"]) == 56
    assert sum(1 for d in tape["decisions"] if d["key"] == "SUBMIT") == 38
    assert sum(1 for d in tape["decisions"] if d["key"] == "BIND") == 18


def test_the_observed_fills_reconcile_to_the_settlement_record(tape):
    up = sum(D(f["qty"]) for f in tape["fills"] if f["side"] == "UP")
    dn = sum(D(f["qty"]) for f in tape["fills"] if f["side"] == "DOWN")
    assert up == D(tape["settled"]["up_qty"]) == D("6.59")
    assert dn == D(tape["settled"]["dn_qty"]) == D("7.74")
    assert up - dn == D(tape["settled"]["final_residual"]) == D("-1.15")


def walk(tape, basis):
    """Replay the tape. basis='realized' is the rule that traded 1130z;
    basis='prospective' is the rule that replaced it."""
    fills = sorted(tape["fills"], key=lambda f: f["t"])
    decs = sorted(tape["decisions"], key=lambda d: d["t"])
    k = D(tape["k"])
    fi = 0
    resid = D(0)
    max_clip = D(0)
    outstanding = {}
    peak = D(0)
    verdicts = []
    # An order this walk REFUSES was never placed, so its observed fills do
    # not happen. That direction is exact -- we are deleting data we have,
    # not inventing data we lack.
    dropped = set()
    for d in decs:
        while fi < len(fills) and fills[fi]["t"] <= d["t"]:
            f = fills[fi]; fi += 1
            if f["coid"] in dropped:
                continue
            q = D(f["qty"])
            resid += q if f["side"] == "UP" else -q
            if q > max_clip:
                max_clip = q
            if f["coid"] in outstanding:
                outstanding[f["coid"]][1] -= q
                if outstanding[f["coid"]][1] <= 0:
                    outstanding.pop(f["coid"])
            peak = max(peak, abs(resid))
        q = D(d["qty"])
        signed = q if d["side"] == "UP" else -q
        if basis == "realized":
            before = resid
        else:
            net = sum((v[1] if v[0] == "UP" else -v[1])
                      for v in outstanding.values())
            before = resid + net
        after = before + signed
        clip = max(max_clip, q)
        cap = k * clip
        exempt = abs(before) == 0
        increases = abs(after) > abs(before)
        binds = (not exempt) and increases and abs(after) > cap
        verdicts.append("BIND" if binds else "SUBMIT")
        if binds:
            if d["coid"]:
                dropped.add(d["coid"])
        elif d["coid"]:
            outstanding[d["coid"]] = [d["side"], q]
    while fi < len(fills):
        f = fills[fi]; fi += 1
        if f["coid"] in dropped:
            continue
        resid += D(f["qty"]) if f["side"] == "UP" else -D(f["qty"])
        peak = max(peak, abs(resid))
    return verdicts, resid, peak


def test_the_realized_only_rule_reproduces_1130z_exactly(tape):
    """The arithmetic anchor. If this drifts, the fixture or the gate moved."""
    verdicts, resid, peak = walk(tape, "realized")
    observed = [d["key"] for d in sorted(tape["decisions"], key=lambda x: x["t"])]
    assert verdicts == observed, "the realized-only rule no longer reproduces 1130z"
    assert resid == D("-1.15")


def test_1130z_is_the_overshoot_this_change_exists_to_stop(tape):
    _, _, peak = walk(tape, "realized")
    assert peak == D(tape["live"]["peak_realized_residual"]) == D("3.24")
    assert peak > D(tape["live"]["cap_in_force"]) * D("1.5")


def test_prospective_refuses_strictly_more_of_what_was_actually_placed(tape):
    """Scored ONLY over the 38 orders the live run placed, where fills are
    observed. The 18 it refused are excluded: we do not know what they would
    have filled, and counting them either way would be inventing data."""
    prospective, _, _ = walk(tape, "prospective")
    decs = sorted(tape["decisions"], key=lambda d: d["t"])
    placed = [(i, d) for i, d in enumerate(decs) if d["key"] == "SUBMIT"]
    now_refused = sum(1 for i, _ in placed if prospective[i] == "BIND")
    assert len(placed) == 38
    # regression anchor: the count is fixed, and it is not zero -- prospective
    # accounting must actually catch some of what realized-only let through.
    # MEASURED, not chosen: this is what the rule does on the recorded tape.
    assert now_refused == 4, (
        "prospective verdicts on the 38 live submissions changed: %d" % now_refused)


def test_prospective_would_have_held_1130z_inside_the_cap(tape):
    """The determinate counterfactual, and the only one this tape supports:
    drop the fills of the orders prospective refuses -- all of which the live
    run DID place, so their fills are observed -- and see where inventory
    peaks. Nothing is invented; 18 live-refused actions stay refused."""
    _, _, realized_peak = walk(tape, "realized")
    _, resid, prospective_peak = walk(tape, "prospective")
    assert realized_peak == D("3.24")
    assert prospective_peak < realized_peak, (
        "prospective accounting did not reduce the overshoot")
    assert prospective_peak == D("1.79"), (
        "peak under prospective changed: %s" % prospective_peak)
    assert prospective_peak <= D(tape["live"]["cap_in_force"]), (
        "prospective still breached the cap on the window it was built for")


def test_no_scoring_is_attempted_for_the_actions_refused_live(tape):
    """Guard against a future edit quietly scoring the unscorable."""
    refused = [d for d in tape["decisions"] if d["key"] == "BIND"]
    assert len(refused) == 18
    coids = {f["coid"] for f in tape["fills"]}
    for d in refused:
        assert d["coid"] is None or d["coid"] not in coids, (
            "a live-refused action has fill data; the tape is not what it claims")
