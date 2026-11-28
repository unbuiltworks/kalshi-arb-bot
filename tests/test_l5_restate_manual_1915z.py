"""The owner-approved restatement of the manual 19:15Z trade, and ONLY it.

Owner, 2026-11-12: "Execute the approved manual-trade cost-basis
restatement from $5.00 to the venue-verified $2.10."

The evidence is the real audit from l5-pmus-live deployment e9e9df5b
(tests/data/l5_production_audit_20261112.json: 61 legs, 2,753 activity rows,
walk complete). On production that walk plans FOUR corrections -- the
manual leg and three BTC15m legs -- and the owner approved one. The scoped
manifest applies the one and leaves the three exactly as they are.

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
    (HERE / "data" / "l5_production_audit_20261112.json").read_text())
MANIFEST = json.loads(
    (HERE.parent / "config"
     / "cost_basis_batch_manifest_20261112_manual1915z.json").read_text())
BATCH = "cb-20261112-manual1915z-2.90"
MANUAL = "cpc-btc-updown-15m-2026-11-11-1915z"
KEY = MANUAL + "|DOWN"
#: the four legs production plans, as its own dry run listed them
PLANNED = {
    "cpc-btc-updown-15m-2026-11-02-2300z": ("UP", "1.6800"),
    "cpc-btc-updown-15m-2026-11-05-2215z": ("DOWN", "1.0000"),
    "cpc-btc-updown-15m-2026-11-11-0415z": ("UP", "0.4400"),
    MANUAL: ("DOWN", "5.0000"),
}
BEFORE = D("-21.04060000")


def _row(side, cost):
    """A retired market held at the bound and lost, as `_retire` stores it."""
    return {"up_qty": cost if side == "UP" else "0",
            "dn_qty": cost if side == "DOWN" else "0",
            "cost_total": cost,
            "yes_value": "1.0000" if side == "DOWN" else "0.0000",
            "settlement_pnl": str(-D(cost)), "redemption_total": "0E-8"}


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        s.state["retired_markets"] = {m: _row(side, c)
                                      for m, (side, c) in PLANNED.items()}
        s.state["realized_attributed"] = str(BEFORE)
        s.state["realized_unattributed"] = "0"
        s.state["realized_today"] = str(BEFORE)
        yield s


def _apply(store, manifest=MANIFEST, batch=BATCH):
    return A.apply_cost_basis_corrections(
        store, copy.deepcopy(AUDIT), batch_id=batch,
        manifest=copy.deepcopy(manifest))


def test_the_manifest_names_one_leg_and_its_venue_evidence():
    assert MANIFEST["scope"] == "KEYS_ONLY"
    assert MANIFEST["keys"] == [KEY] and MANIFEST["legs"] == 1
    assert MANIFEST["trade_ids"] == ["DNKM4580F3YF"]
    leg = [v for v in AUDIT["legs"] if v["key"] == KEY][0]
    assert leg["basis"]["established"] is True
    assert D(leg["basis"]["actual_cost_usd"]) == D("2.10")
    assert D(leg["basis"]["fees_usd"]) == 0
    assert [f["trade_id"] for f in leg["fills"]] == ["DNKM4580F3YF"]
    per = MANIFEST["per_leg"][KEY]
    assert D(per["cost_before_usd"]) - D(per["cost_after_usd"]) == \
        D(MANIFEST["realized_delta_usd"]) == D("2.90")
    assert AUDIT["activity_walk"]["complete"] is True


def test_the_dry_run_plans_exactly_the_one_leg(store):
    out = A.apply_cost_basis_corrections(
        store, copy.deepcopy(AUDIT), batch_id=None,
        manifest=copy.deepcopy(MANIFEST))
    assert out["mode"] == A.APPLY_INSPECT
    assert [p["key"] for p in out["applied_detail"]] == [KEY]
    assert out["batch_check"]["ok"] is True
    out_of_scope = [p["key"] for p in out["preserved_detail"]
                    if p["reason"] == "OUT_OF_BATCH_SCOPE"]
    assert sorted(out_of_scope) == sorted(
        "%s|%s" % (m, sd) for m, (sd, _c) in PLANNED.items() if m != MANUAL)
    assert D(store.state["realized_attributed"]) == BEFORE   # nothing written


def test_applying_moves_realized_by_exactly_2_90_and_touches_nothing_else(
        store):
    others = {m: dict(r) for m, r in store.state["retired_markets"].items()
              if m != MANUAL}
    out = _apply(store)
    assert out["mode"] == "APPLIED", out
    assert out["applied"] == 1 and out["refused"] == 0
    assert D(store.state["realized_attributed"]) == BEFORE + D("2.90")
    assert D(store.state["realized_today"]) == BEFORE + D("2.90")
    assert D(store.state["realized_attributed"]) == D("-18.14060000")
    for m, r in others.items():
        assert store.state["retired_markets"][m] == r, m
    row = store.state["retired_markets"][MANUAL]
    assert D(row["cost_total"]) == D("2.10")
    assert D(row["settlement_pnl"]) == D("-2.10")


def test_the_original_figure_is_kept_beside_the_corrected_one(store):
    _apply(store)
    row = store.state["retired_markets"][MANUAL]
    assert D(row["cost_superseded"]["cost_total"]) == D("5.0000")
    rec = [r for r in store.state["cost_basis_corrections"]
           if r.get("key") == KEY]
    assert len(rec) == 1
    assert rec[0]["trade_ids"] == ["DNKM4580F3YF"]
    assert rec[0]["id"] == "%s|%s" % (BATCH, KEY)


def test_applying_twice_writes_nothing(store):
    _apply(store)
    after = json.dumps(store.state, sort_keys=True, default=str)
    again = _apply(store)
    assert again["applied"] == 0
    assert json.dumps(store.state, sort_keys=True, default=str) == after


def test_without_the_scope_the_same_one_leg_manifest_aborts(store):
    """The default rule is unchanged: a manifest must match EVERY planned
    leg, so a one-leg manifest without the scope writes nothing."""
    m = copy.deepcopy(MANIFEST)
    m.pop("scope")
    out = _apply(store, manifest=m)
    assert out["mode"] == "ABORTED" and out["reason"] == "BATCH_MISMATCH"
    assert D(store.state["realized_attributed"]) == BEFORE


@pytest.mark.parametrize("change", [
    lambda m: m["per_leg"][KEY].update(cost_after_usd="2.00"),
    lambda m: m.update(realized_delta_usd="3.00"),
    lambda m: m.update(trade_ids=["DNKM4580F3YF", "SOMETHINGELSE"]),
    lambda m: m["per_leg"][KEY].update(trade_ids=["SOMETHINGELSE"]),
    lambda m: m.update(keys=[KEY, "cpc-btc-updown-15m-2026-11-11-0415z|UP"]),
])
def test_a_scoped_manifest_that_does_not_match_exactly_writes_nothing(
        store, change):
    m = copy.deepcopy(MANIFEST)
    change(m)
    out = _apply(store, manifest=m)
    assert out["mode"] == "ABORTED", out
    assert D(store.state["realized_attributed"]) == BEFORE


def test_a_scope_cannot_add_a_leg_the_walk_did_not_plan(store):
    m = copy.deepcopy(MANIFEST)
    ghost = "cpc-btc-updown-15m-2026-11-11-1930z|UP"
    m["keys"] = [ghost]
    m["per_leg"] = {ghost: m["per_leg"][KEY]}
    out = _apply(store, manifest=m)
    assert out["mode"] == "ABORTED"
    assert D(store.state["realized_attributed"]) == BEFORE


def test_the_daily_line_is_unchanged(store):
    """The restated market settled on 2026-11-11; today's figure, which the
    daily halt reads, does not move."""
    import datetime as dt
    before = store.realized_current_utc_day(
        now=dt.datetime(2026, 11, 12, 2, 0, tzinfo=dt.timezone.utc).timestamp())
    _apply(store)
    after = store.realized_current_utc_day(
        now=dt.datetime(2026, 11, 12, 2, 0, tzinfo=dt.timezone.utc).timestamp())
    assert before == after
