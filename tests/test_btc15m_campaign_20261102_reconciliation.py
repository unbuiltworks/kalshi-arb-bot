"""The 2300z cost basis: reconciled against the venue, not rewritten.

THE DEFECT. `cpc-btc-updown-15m-2026-11-02-2300z` settled with
`up_qty 1.6800` and `up_cost 1.6800` -- exactly $1.00 a share -- for a maker
buy whose ONLY venue fill was 1.68 @ own 0.2100, i.e. $0.3528. The same
settlement row carries `committed_released 0.352800`, so the record disagrees
with itself, and the $1.3272 difference was posted to `realized_today` and
therefore consumed the $5 UTC-day loss halt.

WHY NOTHING CAUGHT IT. `book_violations` raises COST_PER_SHARE_IMPOSSIBLE on
`per > MAX_OWN_COST_PER_SHARE`, strictly greater. 1.6800/1.6800 is exactly
1.00, so the book sat ON the exclusive bound and `require_coherent_book`
passed it. That boundary behaviour is pinned below so the gap is a decision
rather than an accident.

HISTORY IS PRESERVED. This reconciles; it does not repair. The original
L5_LIVE_SETTLED rows stay the primary evidence and no executor code is
changed. `l5_pmus_live_runner.LEDGER_REPAIRS` is the mechanism that would
actually rewrite the book, and this defect is deliberately NOT registered
there.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

from src import l5_live_execution as EX

DOC = json.load(open("analysis/l5/btc15m_campaign_20261102_reconciliation.json"))
MKT = "cpc-btc-updown-15m-2026-11-02-2300z"
W = {w["slug"]: w for w in DOC["windows"]}


# --------------------------------------------- the venue arithmetic

def test_every_window_cost_is_qty_times_own_fill_price():
    for slug, w in W.items():
        got = D(w["fill_qty"]) * D(w["fill_price_own"])
        assert got == D(w["venue_derived_cost"]), slug


def test_three_of_four_windows_already_agree_with_the_venue():
    agree = [s for s, w in W.items() if w["agrees"]]
    assert len(agree) == 3
    for s in agree:
        assert D(W[s]["as_booked_cost"]) == D(W[s]["venue_derived_cost"]), s


def test_only_2300z_disagrees_and_by_exactly_1_3272():
    w = W[MKT]
    assert w["agrees"] is False
    assert D(w["as_booked_cost"]) - D(w["venue_derived_cost"]) == D("1.3272")
    assert D(w["discrepancy_usd"]) == D("1.3272")


def test_the_settlement_row_disagrees_with_its_own_committed_released():
    """`committed_released` carried the correct outlay all along."""
    w = W[MKT]
    assert D(w["committed_released"]) == D(w["venue_derived_cost"]) == D("0.3528")
    assert D(w["as_booked_cost"]) == D("1.6800")


def test_as_booked_cost_per_share_is_exactly_one_dollar():
    w = W[MKT]
    assert D(w["as_booked_cost"]) / D(w["fill_qty"]) == D(1)


# --------------------------------------------- the campaign totals

def test_campaign_totals_reconcile():
    t = DOC["totals"]
    ab = sum(D(w["residual_pnl_as_booked"]) for w in DOC["windows"])
    vd = sum(D(w["residual_pnl_venue_derived"]) for w in DOC["windows"])
    assert ab == D(t["residual_pnl_as_booked"])
    assert vd == D(t["residual_pnl_venue_derived"])
    assert ab - vd == -D(t["overstatement_usd"])
    assert t["pairs_completed"] == 0


def test_the_overstatement_is_what_the_loss_halt_consumed():
    assert D(DOC["totals"]["overstatement_usd"]) == D("1.3272")
    assert "loss halt" in DOC["impact"]["effect"]


# --------------------------------------------- the detection gap, pinned

def _book(tmp_path, qty: str, cost: str):
    st = EX.InventoryStore(str(tmp_path / "inv.json"))
    st.state["positions"]["%s|UP" % MKT] = qty
    st.state.setdefault("leg_cost", {})["%s|UP" % MKT] = cost
    st.save()
    return st


def test_exactly_one_dollar_a_share_does_NOT_trip_the_coherence_gate(tmp_path):
    """The reason the defect reached settlement. Pinned, not endorsed."""
    st = _book(tmp_path, "1.6800", "1.6800")
    bad = [v["violation"] for v in st.book_violations(MKT)]
    assert "COST_PER_SHARE_IMPOSSIBLE" not in bad


def test_a_fraction_of_a_cent_above_the_bound_does_trip_it(tmp_path):
    """So the bound works; it is the `>` that let 1.00 through."""
    st = _book(tmp_path, "1.6800", "1.6801")
    bad = [v["violation"] for v in st.book_violations(MKT)]
    assert "COST_PER_SHARE_IMPOSSIBLE" in bad


def test_the_true_basis_is_coherent(tmp_path):
    st = _book(tmp_path, "1.6800", "0.3528")
    assert st.book_violations(MKT) == []


# --------------------------------------------- history is preserved

def test_this_is_a_reconciliation_and_not_a_repair():
    from src import l5_pmus_live_runner as R
    ids = {r.get("id") for r in getattr(R, "LEDGER_REPAIRS", [])}
    assert not any("2300z" in str(i) for i in ids), (
        "the 2300z basis is reconciled, not rewritten; registering it as a "
        "LEDGER_REPAIR would overwrite venue history")
    assert DOC["disposition"].startswith("RECORDED, NOT REWRITTEN")


def test_every_figure_names_its_source():
    p = DOC["provenance"]
    assert "L5_LIVE_SETTLED" in p["settlements"]
    assert "l5-pmus-live" in p["orders_and_fills"]
    assert DOC["evidence_class"].startswith("VENUE_CONFIRMED")
