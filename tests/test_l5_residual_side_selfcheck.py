"""The boot-time self-check must PASS on the fix and FAIL without it.

A self-check that cannot fail is decoration. The negative controls here replace
the deployed methods with the superseded cheapest-first rule and with a
no-op quantity cap, and assert the self-check reports PASS False -- which is the
only thing that makes a PASS in the container mean anything.
"""
import json

from src import l5_live_execution as LE


def test_selfcheck_passes_on_the_deployed_logic():
    r = LE.residual_side_selfcheck()
    assert r["PASS"] is True, r["failed"]
    assert r["checked"] == 7
    assert r["failed"] == []
    assert r["label"] == "L5_RESIDUAL_SIDE_SELFCHECK"


def test_selfcheck_is_json_serialisable_for_the_boot_emit():
    json.dumps(LE.residual_side_selfcheck(), default=str)


def test_every_case_reports_both_legs_exactly_once():
    for c in LE.residual_side_selfcheck()["cases"]:
        if "both_legs_once" in c:
            assert c["both_legs_once"] is True, c


def test_negative_control_old_cheapest_first_rule_FAILS(monkeypatch):
    """The superseded rule: side comes from the frozen plan, cheapest first."""
    monkeypatch.setattr(LE.FaithfulExecutor, "probe_leg_order",
                        lambda self, market, legs, cheapest_first:
                        list(cheapest_first))
    r = LE.residual_side_selfcheck()
    assert r["PASS"] is False
    bad = [c["case"] for c in r["failed"]]
    # the two windows the defect actually cost must be among the failures
    assert any("1245z" in b for b in bad), bad
    assert any("0130z" in b for b in bad), bad


def test_negative_control_missing_quantity_cap_FAILS(monkeypatch):
    """Without the cap, a reducing leg can overshoot into a reversal."""
    monkeypatch.setattr(LE.FaithfulExecutor, "probe_leg_qty_cap",
                        lambda self, market, side, qty: qty)
    r = LE.residual_side_selfcheck()
    assert r["PASS"] is False
    assert any("overshoot" in c["case"] for c in r["failed"]), r["failed"]


def test_selfcheck_touches_no_real_store_and_cannot_submit():
    """The stub exposes exactly two readers and no transport of any kind."""
    st = LE._SelfCheckStore("-1.4", "0")
    shim = LE._SelfCheckShim(st)
    assert set(LE._SelfCheckShim.__slots__) == {"store"}
    for forbidden in ("submit", "place", "cancel", "amend", "transport",
                      "arm", "post", "write", "persist"):
        assert not hasattr(st, forbidden), forbidden
        assert not hasattr(shim, forbidden), forbidden


def test_unreadable_residual_case_keeps_todays_rule_rather_than_halting():
    r = LE.residual_side_selfcheck()
    row = [c for c in r["cases"] if "unreadable" in c["case"]][0]
    assert row["ok"] is True
    assert row["got_leg"] == LE.DOWN
