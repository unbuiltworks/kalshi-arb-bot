"""The both-sides detector, and the error it made once.

WHY THIS FILE EXISTS. The forensic capture answers exactly one question --
did BOTH outcomes of a contract actually trade while the first exposure was
open -- and the first version answered it wrong, in the direction that
manufactures evidence. It counted `status == "closed"` as a fill. On this
venue `closed` means CLOSED AT EXPIRY, and such a row routinely carries
filledQuantity 0: an order that never traded at all.

That inflated the count from the true 10 to 12 and put an ETH contract on the
list that had a YES fill and an untouched NO order. The rows below are the
REAL ones from analysis/gemini/fill_forensics_20260921T105113Z.json.
"""
from scripts import gemini_fill_forensics as F

# --- the exact rows that produced the false positive, verbatim ------------

ETH_0140_YES = {
    "symbol": "GEMI-ETH05M2608290140-UP", "outcome": "yes", "side": "buy",
    "price": "0.45", "quantity": "1", "filledQuantity": "1",
    "remainingQuantity": "0", "status": "filled",
    "createdAt": "2026-08-29T01:36:41.618Z",
    "updatedAt": "2026-08-29T01:37:06.554Z",
    "clientOrderId": "b5-2e7d166da943ab92403ec120-yes",
}
#: CLOSED AT EXPIRY, NEVER TRADED. This is the row that was miscounted.
ETH_0140_NO_CLOSED = {
    "symbol": "GEMI-ETH05M2608290140-UP", "outcome": "no", "side": "buy",
    "price": "0.43", "quantity": "1", "filledQuantity": "0",
    "remainingQuantity": "1", "status": "closed",
    "createdAt": "2026-08-29T01:36:41.690Z",
    "updatedAt": "2026-08-29T01:40:00.101Z",
    "clientOrderId": "b5-2e7d166da943ab92403ec120-no",
}
#: the genuine both-sides case, one order from each outcome
ETH_0215_YES = {
    "symbol": "GEMI-ETH05M2609010215-UP", "outcome": "yes", "side": "buy",
    "price": "0.48", "quantity": "1", "filledQuantity": "1",
    "remainingQuantity": "0", "status": "filled",
    "createdAt": "2026-09-01T02:08:16.453Z",
    "updatedAt": "2026-09-01T02:10:03.805Z",
    "clientOrderId": "l5lad-b5dd34d71ed9c9a18fc95a-y0",
}
ETH_0215_NO = {
    "symbol": "GEMI-ETH05M2609010215-UP", "outcome": "no", "side": "buy",
    "price": "0.5", "quantity": "1", "filledQuantity": "1",
    "remainingQuantity": "0", "status": "filled",
    "createdAt": "2026-09-01T02:08:16.776Z",
    "updatedAt": "2026-09-01T02:10:58.081Z",
    "clientOrderId": "l5lad-6258d0cc2bd1dafa7b03e6-n0",
}


# --- the regression -------------------------------------------------------

def test_closed_with_no_filled_quantity_is_NOT_a_fill():
    """THE BUG, pinned on the row that caused it."""
    assert F._is_filled(ETH_0140_NO_CLOSED) is False
    assert str(ETH_0140_NO_CLOSED["status"]) == "closed", (
        "the fixture must keep the status that fooled the first version")


def test_a_real_fill_is_a_fill():
    for row in (ETH_0140_YES, ETH_0215_YES, ETH_0215_NO):
        assert F._is_filled(row) is True, row["clientOrderId"]


def test_quantity_decides_regardless_of_status_string():
    """A status label is not the fact. Both directions."""
    assert F._is_filled({"status": "closed", "filledQuantity": "1"}) is True
    assert F._is_filled({"status": "filled", "filledQuantity": "0"}) is False
    assert F._is_filled({"status": "cancelled",
                         "filledQuantity": "0.5"}) is True   # a partial
    assert F._is_filled({"status": "cancelled",
                         "filledQuantity": "0"}) is False


def test_an_unreadable_quantity_is_not_a_fill():
    """Fail closed: an unparseable field must never manufacture evidence."""
    for bad in (None, "", "abc", {}, []):
        assert F._is_filled({"status": "filled", "filledQuantity": bad}) \
            is False, bad
    assert F._is_filled({"status": "filled"}) is False


def test_the_eth_0140_contract_is_NOT_both_sides():
    """End to end on the two real rows: one outcome traded, one did not."""
    rows = [ETH_0140_YES, ETH_0140_NO_CLOSED]
    filled = {str(r["outcome"]).upper() for r in rows if F._is_filled(r)}
    assert filled == {"YES"}
    assert not {"YES", "NO"} <= filled


def test_the_eth_0215_contract_IS_both_sides():
    rows = [ETH_0215_YES, ETH_0215_NO]
    filled = {str(r["outcome"]).upper() for r in rows if F._is_filled(r)}
    assert {"YES", "NO"} <= filled
    # and the opposite fill came LATER, with no sell between -- which is the
    # property the whole finding rests on
    assert ETH_0215_NO["updatedAt"] > ETH_0215_YES["updatedAt"]
    assert all(r["side"] == "buy" for r in rows)


def test_the_canonical_artifact_still_says_ten():
    """The stored evidence and the corrected detector must agree.

    The artifact's own `verdict` block was written by the BROKEN detector and
    says 12. It is left as captured -- raw provenance is not edited after the
    fact -- so this recomputes from the RAW ROWS, which are what the venue
    actually returned.
    """
    import json
    import pathlib
    from collections import defaultdict

    p = pathlib.Path("analysis/gemini/fill_forensics_20260921T105113Z.json")
    if not p.exists():                     # pragma: no cover
        import pytest
        pytest.skip("canonical artifact not present in this checkout")
    doc = json.loads(p.read_text())

    by = defaultdict(list)
    for r in doc["rows"]:
        by[r.get("symbol")].append(r)
    both = sorted(s for s, rs in by.items()
                  if {"YES", "NO"} <= {str(r.get("outcome")).upper()
                                       for r in rs if F._is_filled(r)})
    assert len(both) == 10, both
    assert "GEMI-ETH05M2609010215-UP" in both
    assert "GEMI-ETH05M2609011145-UP" in both
    # the two the broken predicate added
    assert "GEMI-ETH05M2608290140-UP" not in both
    assert "GEMI-BTC05M2608301245-UP" not in both
    # and the artifact's own stale verdict is still 12, unedited
    assert doc["verdict"]["n_both"] == 12
