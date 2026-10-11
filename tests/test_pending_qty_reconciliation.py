"""A halted probe must keep asking why its quantities disagree.

The alarm that carries the per-trade evidence fires at session_end. After the
02:02Z restart the probe refused to re-arm (`flat_held_state` failed, since it
holds the cricket market) and went to shutdown_idle -- which never reaches
session_end. So the one question blocking both the flat boundary and the
sizing of the residual could never be answered on the path the probe is
actually running.

It runs here instead, on the read-only settlement pass a halted probe does
still run, and it reports BOTH ledger measures: the settled-cohort check
compares the venue's gross traded quantity against the ledger's gross FILLED
quantity, while the live session path compares it against CURRENT HOLDINGS.
For a buy-only probe those coincide -- so if they disagree, that disagreement
is itself the finding.
"""
from __future__ import annotations

import pytest

from src import pmus_l5_continuous_session as C


class _Sig:
    """Stand-ins; nothing here reaches a venue."""


@pytest.fixture
def patched(monkeypatch):
    from src import pmus_settlement_auditor as sa
    from src import pmus_spring_l5_session as sp
    state = {"venue": "45.01", "filled": "45.01", "up": "16.67", "dn": "28.34",
             "evidence": {"n_trades": 2, "scaled_qty_rows": [],
                          "qty_vs_qtydecimal_disagreements": [],
                          "duplicate_trade_ids": []}}

    monkeypatch.setattr(sa, "traded_qty_with_evidence",
                        lambda slug, t=None, **kw: {
                            "qty": state["venue"], "evidence": state["evidence"]})
    monkeypatch.setattr(sa, "ledger_qty_total", lambda slug: state["filled"])

    class _Inv:
        def __init__(self):
            from decimal import Decimal as D
            self.up, self.dn = D(state["up"]), D(state["dn"])

    monkeypatch.setattr(sp, "held_inventory_from_ledger", lambda slug: _Inv())
    return state


def test_a_reconciling_market_agrees_on_both_measures(patched):
    [row] = C.pending_qty_reconciliation(["cricket"], t=_Sig())
    assert row["vs_ledger_filled"]["agrees"] is True
    assert row["vs_ledger_held"]["agrees"] is True
    assert row["residual"] == "-11.67"
    assert row["qty_evidence"]["available"] is True


def test_the_live_gap_is_reported_against_both_measures(patched):
    """The 01:44Z shape: venue 774.62, holdings 45.01."""
    patched["venue"] = "774.6200"
    [row] = C.pending_qty_reconciliation(["cricket"], t=_Sig())
    assert row["venue_qty_total"] == "774.6200"
    assert row["ledger_qty_held"] == "45.01"
    assert row["vs_ledger_held"]["agrees"] is False
    assert row["vs_ledger_held"]["delta"] == "729.6100"
    assert row["vs_ledger_filled"]["agrees"] is False


def test_the_two_measures_can_disagree_with_each_other(patched):
    """If gross-filled reconciles while holdings do not, the live path was
    comparing the wrong quantity all along -- and only reporting both can
    show that."""
    patched["venue"] = "774.6200"
    patched["filled"] = "774.6200"
    [row] = C.pending_qty_reconciliation(["cricket"], t=_Sig())
    assert row["vs_ledger_filled"]["agrees"] is True
    assert row["vs_ledger_held"]["agrees"] is False


def test_evidence_digest_distinguishes_the_three_explanations(patched):
    patched["venue"] = "774.6200"
    patched["evidence"] = {"n_trades": 9,
                           "scaled_qty_rows": [{"scale_keys": ["exponent"]}],
                           "qty_vs_qtydecimal_disagreements": [{}],
                           "duplicate_trade_ids": []}
    [row] = C.pending_qty_reconciliation(["cricket"], t=_Sig())
    d = row["qty_evidence"]
    assert (d["scaled_qty"], d["qty_vs_qtydecimal"], d["duplicate_ids"]) == (1, 1, 0)
    assert d["scale_keys"] == ["exponent"]


def test_an_unreachable_venue_yields_unknown_not_agreement(monkeypatch):
    from src import pmus_settlement_auditor as sa
    monkeypatch.setattr(sa, "traded_qty_with_evidence",
                        lambda slug, t=None, **kw: {"qty": None,
                                                    "evidence": None})
    monkeypatch.setattr(sa, "ledger_qty_total", lambda slug: "45.01")
    [row] = C.pending_qty_reconciliation(["cricket"], t=_Sig())
    assert row["vs_ledger_filled"]["agrees"] is None
    assert row["qty_evidence"]["available"] is False


def test_it_never_raises_and_is_bounded(monkeypatch):
    from src import pmus_settlement_auditor as sa

    def boom(*a, **kw):
        raise RuntimeError("venue down")

    monkeypatch.setattr(sa, "traded_qty_with_evidence", boom)
    rows = C.pending_qty_reconciliation([f"s{i}" for i in range(50)],
                                        t=_Sig(), limit=8)
    assert len(rows) == 8, "a halted probe must not walk 50 markets per cycle"
    assert all("venue down" in r["error"] for r in rows)


def test_no_slugs_is_no_work():
    assert C.pending_qty_reconciliation([], t=_Sig()) == []
    assert C.pending_qty_reconciliation(None, t=None) == []


def test_the_settle_pass_carries_it_and_survives_its_failure(monkeypatch):
    """The seam: shutdown_settle_pass must attach the rows, and a failure in
    them must not take down the halted probe's settlement reporting."""
    monkeypatch.setattr(C, "unsettled_slugs", lambda: ["cricket"])
    monkeypatch.setattr(C.base.q1, "has_credentials", lambda: True)
    monkeypatch.setattr(C, "settle_held",
                        lambda t, slug: {"settled": False, "status": "PENDING"})

    def boom(*a, **kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(C, "pending_qty_reconciliation", boom)
    out = C.shutdown_settle_pass(t=_Sig())
    # subset, not exact equality: the record gained per-market timing when the
    # pass was bounded, and the property under test is that the pass SURVIVES
    # the failure, not the exact shape of its diagnostic row.
    assert [{"slug": r["slug"], "status": r["status"]}
            for r in out["still_pending"]] == \
        [{"slug": "cricket", "status": "PENDING"}]
    assert any("qty_reconciliation" in e for e in out["errors"])

    monkeypatch.setattr(C, "pending_qty_reconciliation",
                        lambda slugs, t, **kw: [{"slug": s} for s in slugs])
    out2 = C.shutdown_settle_pass(t=_Sig())
    assert out2["qty_reconciliation"] == [{"slug": "cricket"}]


def test_the_pass_stays_read_only():
    """No order path may appear on a halted probe's cycle."""
    import inspect
    src = inspect.getsource(C.pending_qty_reconciliation)
    for banned in ("submit", "place_order", "ledger_append", "cancel",
                   "_order(", "post_only"):
        assert banned not in src, banned


def test_without_credentials_the_pass_asks_the_venue_nothing(monkeypatch):
    """A halted probe with no credentials must not pretend to reconcile:
    it returns no_credentials rather than an empty, agreeable-looking row."""
    calls = []
    monkeypatch.setattr(C, "unsettled_slugs", lambda: ["cricket"])
    monkeypatch.setattr(C.base.q1, "has_credentials", lambda: False)
    monkeypatch.setattr(C, "pending_qty_reconciliation",
                        lambda *a, **kw: calls.append(1) or [])
    out = C.shutdown_settle_pass(t=_Sig())
    assert out["errors"] == ["no_credentials"]
    assert "qty_reconciliation" not in out
    assert calls == []
