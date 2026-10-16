"""The consolidated restatement batch, proved against the PRODUCTION state.

The evidence is the real audit from Railway deployment 119637c4
(tests/data/l5_production_audit_20261016.json: 49 legs, 2,129 activity rows,
walk ended eof), with each leg's basis re-derived by the FIXED side-aware
`establish_basis`.

WHAT THIS PROVES
  1. applying the batch ONCE moves account-wide realized from
     -76.12160000 to -7.88480000, exactly the manifest's 68.23680000;
  2. applying it TWICE writes nothing at all;
  3. the two-sided market keeps its other leg's real cost;
  4. every leg that needs no correction stays out, and both
     quantity-mismatched legs stay UNKNOWN.

Nothing here touches production: the store is a temporary file.
"""
import copy
import json
import os
import pathlib
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_cost_basis_audit as A
from src import l5_live_execution as LX

HERE = pathlib.Path(__file__).resolve().parent
AUDIT = json.loads(
    (HERE / "data" / "l5_production_audit_20261016.json").read_text())
MANIFEST = json.loads(
    (HERE.parent / "config"
     / "cost_basis_batch_manifest_20261016c.json").read_text())
BATCH = "cb-20261016c-24leg-68.2368"

REALIZED_BEFORE = D("-76.12160000")
REALIZED_AFTER = D("-7.88480000")

XRP = "cpcxrp-xrp-updown-15m-2026-10-16-0330z"
TWO_SIDED = "cpc-btc-updown-15m-2026-10-15-1945z"
STAGE_A = "cpc-btc-updown-15m-2026-10-14-2230z"


def _rows():
    """The production ledger as the four settlement records describe it."""
    rows = {}
    for leg in AUDIT["legs"]:
        m, side, qty = leg["market"], leg["side"], leg["ledger_qty"]
        own = "up_cost" if side == "UP" else "dn_cost"
        if m == XRP:
            rows[m] = {"up_qty": "50.0000", "dn_qty": "0",
                       "up_cost": "37.975400", "cost_total": "37.975400",
                       "yes_value": "0.0000",
                       "settlement_pnl": "-37.97540000",
                       "redemption_total": "0E-8"}
        elif m == TWO_SIDED:
            rows[m] = {"up_qty": "1", "dn_qty": "1.0000",
                       "up_cost": "0.4600", "dn_cost": "1.0000",
                       "cost_total": "1.4600", "yes_value": "0.0000",
                       "settlement_pnl": "-0.46000000",
                       "redemption_total": "1.00000000"}
        elif m == STAGE_A:
            rows[m] = {"up_qty": "2.90", "dn_qty": "0",
                       "cost_total": "0.590000", "yes_value": "0.0000",
                       "settlement_pnl": "-0.59000000",
                       "redemption_total": "0E-8",
                       "cost_superseded": {"cost_total": "2.90"}}
        elif leg["_applied_after"] is not None:
            now = leg["_applied_after"]
            # its own side lost, so settlement_pnl is minus its cost
            rows[m] = {"up_qty": qty if side == "UP" else "0",
                       "dn_qty": qty if side == "DOWN" else "0",
                       own: now, "cost_total": now,
                       "yes_value": "1.0000" if side == "DOWN" else "0.0000",
                       "settlement_pnl": str(-D(now)),
                       "redemption_total": "0E-8",
                       "cost_superseded": {
                           "cost_total": leg["_applied_before"]}}
        else:
            rows[m] = {"up_qty": qty if side == "UP" else "0",
                       "dn_qty": qty if side == "DOWN" else "0",
                       "cost_total": qty, "yes_value": "0.0000"}
    return rows


def _prior_corrections():
    return [{"id": "cb-20261016-47leg-209.6638|%s" % leg["key"],
             "key": leg["key"],
             "trade_ids": [f["trade_id"] for f in leg["fills"]]}
            for leg in AUDIT["legs"] if leg["_applied_after"] is not None]


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        s.state["retired_markets"] = _rows()
        s.state["cost_basis_corrections"] = _prior_corrections()
        s.state["realized_attributed"] = str(REALIZED_BEFORE)
        s.state["realized_unattributed"] = "0"
        s.state["realized_today"] = str(REALIZED_BEFORE)
        yield s


def _apply(store, batch=BATCH):
    return A.apply_cost_basis_corrections(
        store, copy.deepcopy(AUDIT), batch_id=batch, manifest=MANIFEST)


# -- 1. the manifest itself ------------------------------------------------

def test_the_manifest_is_internally_consistent():
    assert MANIFEST["legs"] == 24 == len(MANIFEST["keys"])
    assert len(MANIFEST["trade_ids"]) == len(set(MANIFEST["trade_ids"])) == 35
    assert MANIFEST["realized_delta_usd"] == "68.23680000"
    total = sum(D(v["cost_before_usd"]) - D(v["cost_after_usd"])
                for v in MANIFEST["per_leg"].values())
    assert total == D(MANIFEST["realized_delta_usd"])
    assert set(MANIFEST["per_leg"]) == set(MANIFEST["keys"])


# -- 2. applying it ONCE ---------------------------------------------------

def test_applying_once_produces_the_correct_accounting(store):
    out = _apply(store)
    assert out["mode"] == "APPLIED", out.get("reason")
    assert out["batch_check"]["ok"] is True
    assert (out["applied"], out["refused"]) == (24, 0), out["refused_detail"]
    assert out["preserved_unknown"] == 25
    assert out["realized_delta_usd"] == "68.23680000"
    # BOTH realized keys move together, and unattributed is untouched
    assert store.state["realized_today"] == str(REALIZED_AFTER)
    assert store.state["realized_attributed"] == str(REALIZED_AFTER)
    assert store.state["realized_unattributed"] == "0"
    assert D(store.state["realized_today"]) - REALIZED_BEFORE == D("68.2368")


def test_the_restated_legs_move_realized_the_UNFAVOURABLE_way(store):
    _apply(store)
    # the worst of the 13: 0215z|DOWN was recorded at 0.31680000 and the
    # venue says 4.9632, so its restatement RAISES the loss.
    row = store.state["retired_markets"][
        "cpcxrp-xrp-updown-15m-2026-10-16-0215z"]
    assert D(row["cost_total"]) == D("4.9632")
    assert D(row["dn_cost"]) == D("4.9632")
    assert row["cost_provenance"] == "MEASURED_FROM_VENUE_FILLS"


def test_the_two_sided_market_keeps_the_other_legs_real_cost(store):
    _apply(store)
    row = store.state["retired_markets"][TWO_SIDED]
    assert D(row["dn_cost"]) == D("0.49")     # corrected
    assert row["up_cost"] == "0.4600"         # UNTOUCHED
    assert D(row["cost_total"]) == D("0.95")  # recomputed, never 0.49


def test_the_original_superseded_rows_are_never_overwritten(store):
    _apply(store)
    for leg in AUDIT["legs"]:
        if leg["_applied_before"] is None:
            continue
        row = store.state["retired_markets"][leg["market"]]
        # the pre-FIRST-correction figure survives untouched...
        assert row["cost_superseded"]["cost_total"] == leg["_applied_before"]
        if leg["key"] in MANIFEST["per_leg"]:
            # ...and the superseded intermediate is kept beside it
            assert row["cost_restatements"], leg["key"]
            assert (row["cost_restatements"][0]["cost_total"]
                    == leg["_applied_after"])


def test_nothing_that_needs_no_correction_was_touched(store):
    out = _apply(store)
    reasons = {}
    for d in out["preserved_detail"]:
        reasons.setdefault(d["reason"], []).append(d["key"])
    assert len(reasons["NOTHING_TO_CORRECT"]) == 23
    assert len(reasons["QUANTITY_MISMATCH"]) == 2
    # both UNKNOWN legs stay UNKNOWN, and the Stage A leg is left alone
    assert all(d["cost"] == "UNKNOWN" for d in out["preserved_detail"]
               if d["reason"] == "QUANTITY_MISMATCH")
    assert STAGE_A + "|UP" in reasons["NOTHING_TO_CORRECT"]
    assert "cost_restatements" not in store.state["retired_markets"][STAGE_A]


def test_ownership_and_the_halt_are_untouched(store):
    before_owner = {m: {k: v for k, v in r.items()
                        if "owner" in k or "provenance" in k}
                    for m, r in store.state["retired_markets"].items()}
    _apply(store)
    for m, r in store.state["retired_markets"].items():
        for k, v in before_owner[m].items():
            if k == "cost_provenance":
                continue
            assert r.get(k) == v, (m, k)
    assert "halt" not in store.state
    assert "L5_SHADOW_ONLY" not in str(store.state)


# -- 3. applying it TWICE --------------------------------------------------

def test_applying_twice_writes_nothing_at_all(store):
    first = _apply(store)
    assert first["applied"] == 24
    after = copy.deepcopy(store.state)

    second = _apply(store)
    assert second["mode"] == "ABORTED"
    assert second["reason"] == "BATCH_MISMATCH"
    assert second["applied"] == 0
    assert store.state == after, "the second apply changed the ledger"
    assert store.state["realized_today"] == str(REALIZED_AFTER)


def test_a_second_apply_under_a_FRESH_batch_id_also_writes_nothing(store):
    _apply(store)
    after = copy.deepcopy(store.state)
    again = _apply(store, batch="cb-20261016c-SECOND-ATTEMPT")
    assert again["mode"] == "ABORTED"
    assert again["reason"] == "BATCH_MISMATCH"
    assert store.state == after


def test_the_batch_refuses_outright_if_one_leg_has_moved(store):
    """A changed world is not a smaller correction."""
    store.state["retired_markets"][XRP]["up_cost"] = "37.975399"
    store.state["retired_markets"][XRP]["cost_total"] = "37.975399"
    out = _apply(store)
    assert out["mode"] == "ABORTED"
    assert out["reason"] == "BATCH_MISMATCH"
    assert out["applied"] == 0
    assert store.state["realized_today"] == str(REALIZED_BEFORE)


def test_INSPECT_with_no_batch_id_writes_nothing(store):
    out = A.apply_cost_basis_corrections(store, copy.deepcopy(AUDIT))
    assert out["mode"] == "INSPECT"
    assert len(out["applied_detail"]) == 24
    assert out["realized_delta_usd"] == "68.23680000"
    assert store.state["realized_today"] == str(REALIZED_BEFORE)
    assert not any("cost_restatements" in r
                   for r in store.state["retired_markets"].values())


# -- 4. the side rule lives in exactly two places and they agree ----------

def test_the_two_implementations_of_the_side_rule_agree():
    """`own_price` in the audit and the inline complement in
    correct_cost_basis are the same convention. If one drifts, this fails."""
    import inspect
    src = inspect.getsource(LX.InventoryStore.correct_cost_basis)
    assert 'q * (px if side == "UP" else (D(1) - px))' in src
    for px in ("0", "0.0100", "0.0600", "0.5100", "0.9900", "1"):
        for side, want in (("UP", D(px)), ("DOWN", D(1) - D(px))):
            assert A.own_price(D(px), side) == want
