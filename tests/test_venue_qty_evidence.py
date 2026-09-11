"""A quantity gap has three explanations. The totals cannot tell them apart.

LIVE 01:44Z 2026-09-11, aec-county-not-ham-2026-09-08:

    venue_qty 774.6200  ledger_qty 45.01  delta 729.6100  agrees false

45.01 is exactly the session's up 16.67 + dn 28.34, and the same venue
measure reconciled EXACTLY on 15 of 16 settled R5F markets. A 729.61-share
fill is not reachable under a 7.50 session cap, so the gap is far more
likely a reading error than a position -- but "more likely" is not a
finding, and the alarm as it stood could not tell the difference.

So: evidence, not a verdict. These tests pin that the three explanations
stay distinguishable, and that nothing here quietly resolves the gap.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_settlement_auditor as S


def _tr(id_, qty=None, qty_decimal=None, **kw):
    tr = {"marketSlug": "m", "id": id_, "state": "TRADE_STATE_NEW"}
    if qty is not None:
        tr["qty"] = qty
    if qty_decimal is not None:
        tr["qtyDecimal"] = qty_decimal
    tr.update(kw)
    return {"type": "ACTIVITY_TYPE_TRADE", "trade": tr}


def _info(*rows):
    return S._trade_summary(list(rows), "m")


# ------------------------------------------------------- the clean case
def test_ordinary_trades_produce_no_diagnosis_at_all():
    """15 of 16 markets look like this. Nothing must be flagged on them."""
    ev = S.qty_evidence(_info(_tr("a", qty_decimal="16.67"),
                              _tr("b", qty_decimal="28.34")))
    assert ev["n_trades"] == 2
    assert ev["resolved_sum"] == "45.01"
    assert ev["scaled_qty_rows"] == []
    assert ev["qty_vs_qtydecimal_disagreements"] == []
    assert ev["duplicate_trade_ids"] == []


def test_the_total_is_unchanged_by_carrying_evidence():
    """Instrumentation must not move the number it is explaining."""
    info = _info(_tr("a", qty_decimal="16.67"), _tr("b", qty_decimal="28.34"))
    assert S.venue_qty_total(info) == "45.01"
    S.qty_evidence(info)
    assert S.venue_qty_total(info) == "45.01"


# ------------------------------------- explanation 1: units we misread
@pytest.mark.parametrize("key", list(S.SCALE_KEYS))
def test_a_quantity_carrying_a_scale_key_is_flagged_not_silently_summed(key):
    """_amount() takes .value and drops everything else, so a scaled
    quantity reads as a number far larger than the position. That is the
    shape that turned notionalTraded into a 100x error."""
    ev = S.qty_evidence(_info(_tr("a", qty={"value": "729.61", key: -2})))
    assert [r["id"] for r in ev["scaled_qty_rows"]] == ["a"]
    assert ev["scaled_qty_rows"][0]["scale_keys"] == [key]
    assert ev["scaled_qty_rows"][0]["resolved"] == "729.61"


def test_qty_disagreeing_with_qtydecimal_is_reported_with_both_numbers():
    ev = S.qty_evidence(_info(
        _tr("a", qty={"value": "301", "exponent": -2}, qty_decimal="3.01")))
    [row] = ev["qty_vs_qtydecimal_disagreements"]
    assert (row["qty"], row["qtyDecimal"]) == ("301", "3.01")


def test_the_live_shape_is_reproduced_end_to_end():
    """One trade with qtyDecimal and one scaled trade without it reproduce
    the live signature: a small true position reading as a large one."""
    info = _info(_tr("a", qty={"value": "4501", "exponent": -2},
                     qty_decimal="45.01"),
                 _tr("b", qty={"value": "729.61", "exponent": -2}))
    assert S.venue_qty_total(info) == "774.62"      # the number we saw
    ev = S.qty_evidence(info)
    assert len(ev["scaled_qty_rows"]) == 2
    assert ev["qty_vs_qtydecimal_disagreements"], "the a-row proves the scale"
    assert ev["duplicate_trade_ids"] == []


# ---------------------------------- explanation 2: a row counted twice
def test_a_repeated_trade_id_is_reported_as_a_duplicate():
    ev = S.qty_evidence(_info(_tr("a", qty_decimal="3.01"),
                              _tr("a", qty_decimal="3.01"),
                              _tr("b", qty_decimal="1.00")))
    assert ev["duplicate_trade_ids"] == [{"id": "a", "times": 2}]
    assert ev["resolved_sum"] == "7.02"


# ------------------------- explanation 3: a fill we never recorded
def test_a_real_unrecorded_fill_leaves_every_diagnosis_empty():
    """The dangerous case must be distinguishable BY its silence: ordinary
    trades, a real total, and nothing flagged. vervas-kohboh was this."""
    ev = S.qty_evidence(_info(_tr("a", qty_decimal="9.48"),
                              _tr("b", qty_decimal="4.34")))
    assert ev["resolved_sum"] == "13.82"
    assert not ev["scaled_qty_rows"]
    assert not ev["qty_vs_qtydecimal_disagreements"]
    assert not ev["duplicate_trade_ids"]


# ------------------------------------------------------ the alarm digest
def test_the_digest_separates_the_three_explanations():
    from src import pmus_spring_l5_session as SP
    scaled = S.qty_evidence(_info(_tr("a", qty={"value": "729.61",
                                                "exponent": -2})))
    real = S.qty_evidence(_info(_tr("a", qty_decimal="9.48"),
                                _tr("b", qty_decimal="4.34")))
    d1 = SP._qty_evidence_digest({"qty_evidence": scaled})
    d2 = SP._qty_evidence_digest({"qty_evidence": real})
    assert d1["scaled_qty"] == 1 and d1["scale_keys"] == ["exponent"]
    assert (d2["scaled_qty"], d2["qty_vs_qtydecimal"],
            d2["duplicate_ids"]) == (0, 0, 0)
    assert d1["available"] is True and d2["available"] is True


def test_an_unaskable_venue_is_reported_as_unavailable_not_as_clean():
    """Missing evidence must never read as 'nothing wrong'."""
    from src import pmus_spring_l5_session as SP
    for recon in ({}, {"qty_evidence": None}, {"qty_evidence": "boom"}):
        d = SP._qty_evidence_digest(recon)
        assert d["available"] is False
        assert "could not be asked" in d["why"]
        assert "scaled_qty" not in d


def test_the_digest_fits_on_a_log_line():
    from src import pmus_spring_l5_session as SP
    import json
    ev = S.qty_evidence(_info(*[_tr(f"t{i}", qty_decimal="1.00")
                                for i in range(200)]))
    assert len(json.dumps(SP._qty_evidence_digest({"qty_evidence": ev}))) < 700


def test_evidence_rows_are_bounded_but_say_they_were_truncated():
    ev = S.qty_evidence(_info(*[_tr(f"t{i}", qty_decimal="1.00")
                                for i in range(50)]), limit=5)
    assert len(ev["trades"]) == 5
    assert ev["truncated"] is True
    assert ev["n_trades"] == 50            # the COUNT is never truncated
    assert ev["resolved_sum"] == "50.00"   # nor the total


# ---------------------------------------------------------- the seam
def test_one_walk_serves_both_the_total_and_the_evidence(monkeypatch):
    """Two calls would paginate the whole account activity feed twice."""
    walks = []

    def fake_pages(t, since=None, **kw):
        walks.append(since)
        return {"rows": [_tr("a", qty_decimal="45.01")]}

    monkeypatch.setattr(S, "activity_pages", fake_pages)
    monkeypatch.setattr(S, "first_fill_time", lambda slug: None)
    got = S.traded_qty_with_evidence("m", t=object())
    assert len(walks) == 1
    assert got["qty"] == "45.01"
    assert got["evidence"]["n_trades"] == 1


def test_an_unreachable_venue_yields_unknown_never_zero(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("venue down")

    monkeypatch.setattr(S, "activity_pages", boom)
    monkeypatch.setattr(S, "first_fill_time", lambda slug: None)
    got = S.traded_qty_with_evidence("m", t=object())
    assert got["qty"] is None, "unknown must never be reported as 0"
    assert got["evidence"] is None
    assert "venue down" in got["error"]


def test_a_mismatch_still_refuses_to_assert_flat(monkeypatch):
    """Evidence explains the gap; it never excuses it. may_assert_flat stays
    False whatever the diagnosis says."""
    from src import pmus_fill_ledger as fl
    row = fl.venue_vs_ledger("774.6200", "45.01")
    assert row["agrees"] is False
    assert fl.may_assert_flat(row) is False
