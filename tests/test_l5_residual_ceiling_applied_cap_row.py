"""The preflight must report the cap that will BIND, not only the worst one.

THE MISREADING, and it was mine. The `residual_ceiling` row reported

    k 1, largest_recorded_clip 37.73, relative_cap_at_that_clip 37.73,
    effective_cap_contracts 5, which_binds ABSOLUTE

on production at 2026-11-03T16:20:53Z, and I reported to the owner that
their approved 1-contract residual cap was therefore NOT in force and that
worst-case per-window risk was 5 contracts rather than 1.

That was wrong, and the module said so in its own docstring:
`largest_recorded_clip` is ACCOUNT-WIDE and explicitly "for TELEMETRY only:
the applied residual ceiling is always computed per market inside the gate".
The gate reads `store.max_clip(market)`, which is 0 for a BTC15m window that
has never filled, so `use_clip = max(qty, 0) = qty` and the applied cap at a
1-contract clip is min(1 * 1, 5) = 1 contract. Exactly the approved figure.

The 37.73 clip belongs to one XRP market from 2026-10-16. It can never be
the clip of the next BTC15m window.

So the row keeps the historical worst case -- it answers a real question --
and now reports the applied cap beside it, because the two differ by 5x at
pilot size and an authorization decision was about to be made off the wrong
one.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_live_authorization as A
from src import l5_live_execution as LE


def _row(monkeypatch, *, k="1", clip="1"):
    monkeypatch.setenv("PMUS_RESIDUAL_CAP_K", k)
    monkeypatch.setenv("L5_TS_OPENING_LEG_MAX_CONTRACTS", clip)
    out = A.authorization_preflight()
    for r in out["rows"]:
        if r.get("check") == "residual_ceiling":
            return r
    raise AssertionError("no residual_ceiling row")


def test_the_applied_cap_at_a_one_contract_clip_is_one_contract(monkeypatch):
    r = _row(monkeypatch, k="1", clip="1")
    assert r["applied_cap_at_opening_clip"] == "1"


def test_the_applied_cap_tracks_the_clip(monkeypatch):
    assert _row(monkeypatch, k="1",
                clip="3")["applied_cap_at_opening_clip"] == "3"


def test_the_absolute_cap_still_binds_a_large_clip(monkeypatch):
    """min(k * clip, 5): the absolute half is the backstop, and it is a
    module constant with no env override."""
    assert _row(monkeypatch, k="2",
                clip="3")["applied_cap_at_opening_clip"] == "5"
    assert LE.ABS_RESIDUAL_CAP_CONTRACTS == D("5")


def test_the_historical_worst_case_fields_are_still_reported(monkeypatch):
    """They answer a different question and are not removed."""
    r = _row(monkeypatch)
    for k in ("largest_recorded_clip", "relative_cap_at_that_clip",
              "effective_cap_contracts", "which_binds",
              "absolute_cap_contracts", "outer_bound_contracts"):
        assert k in r, k


def test_the_row_says_which_question_each_figure_answers(monkeypatch):
    r = _row(monkeypatch)
    assert "THIS market's recorded clip" in r["applied_cap_basis"]
    assert "PER MARKET" in r["note"]


def test_the_row_is_never_an_arming_blocker(monkeypatch):
    """Telemetry. A fault reading it must not turn the preflight red and
    send an operator hunting an authorization problem that does not exist."""
    r = _row(monkeypatch)
    assert r["verdict"] in ("PASS", "NOT_EVALUATED")


def test_the_gate_itself_agrees_with_the_reported_applied_cap(monkeypatch):
    """The row is only worth having if it matches the gate. A same-side
    EXTEND past one contract is refused; a COMPLETE never is."""
    row = _row(monkeypatch, k="1", clip="1")
    cap = D(row["applied_cap_at_opening_clip"])
    common = dict(qty=D(1), k=D(1), clip=D(1), other_side=D(0),
                  same_side=D(0), same_side_includes_this_order=False)
    opened = LE.residual_cap_decision(resid=D(0), side="UP", **common)
    assert opened["cap_contracts"] == str(cap)
    assert opened["binds"] is False          # a first leg is never capped
    extend = LE.residual_cap_decision(resid=D(1), side="UP", **common)
    assert extend["cap_contracts"] == str(cap)
    assert extend["binds"] is True           # +1 on top of 1 held is refused
    complete = LE.residual_cap_decision(resid=D(1), side="DOWN", **common)
    assert complete["binds"] is False        # risk-reducing, never refused


def test_an_unset_k_reads_POLICY_OFF_not_a_default(monkeypatch):
    """`residual_cap_k` returns None for unset, blank, unparseable and
    non-positive: that selects NO policy rather than some default k, and a
    row that printed a number here would claim a cap that does not apply.

    This is also what broke the row the first time: k is None in the test
    environment, so `k * clip` raised and the whole row fell to
    NOT_EVALUATED -- which moved a preflight list and cost a parity run.
    """
    monkeypatch.delenv("PMUS_RESIDUAL_CAP_K", raising=False)
    monkeypatch.setenv("L5_TS_OPENING_LEG_MAX_CONTRACTS", "1")
    out = A.authorization_preflight()
    r = [x for x in out["rows"] if x.get("check") == "residual_ceiling"][0]
    assert r["verdict"] == "PASS"
    assert r["applied_cap_at_opening_clip"] == "POLICY_OFF"
    assert "POLICY_OFF means" in r["applied_cap_basis"]


def test_the_row_stays_out_of_the_not_evaluated_list(monkeypatch):
    """The three declared-not-evaluated steps are the SEQUENTIAL claim
    steps. This telemetry row must not join them in either state."""
    expected = {"window_bounds", "order_state_and_reconciliation",
                "claim_held_and_scope_digest"}
    monkeypatch.delenv("PMUS_RESIDUAL_CAP_K", raising=False)
    assert set(A.authorization_preflight()["not_evaluated"]) == expected
    monkeypatch.setenv("PMUS_RESIDUAL_CAP_K", "1")
    assert set(A.authorization_preflight()["not_evaluated"]) == expected
