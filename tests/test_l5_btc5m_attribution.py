"""Matched-pair contribution and unmatched-inventory loss, kept apart.

Both live windows of 2026-11-20 are pinned here from their recorded fills.
The 17:05Z figures are the venue's own: its position row read
`realized 0.0296` and `cost 0.0447`.
"""
from decimal import Decimal as D

import pytest

from src import l5_btc5m_attribution as A

W1705 = [  # in the order the legs were opened
    ("UP", "0.09", "0.45"), ("DOWN", "0.07", "0.33"), ("UP", "0.07", "0.66"),
    ("DOWN", "0.07", "0.32"), ("UP", "0.07", "0.67"), ("DOWN", "0.07", "0.36"),
    ("UP", "0.07", "0.69"), ("DOWN", "0.06", "0.25"), ("UP", "0.06", "0.73"),
    ("DOWN", "0.06", "0.25"), ("DOWN", "0.05", "0.15"), ("UP", "0.05", "0.84"),
    ("DOWN", "0.05", "0.15"), ("DOWN", "0.06", "0.20"), ("DOWN", "0.06", "0.17"),
    ("DOWN", "0.05", "0.13"), ("DOWN", "0.05", "0.08"), ("DOWN", "0.05", "0.11"),
    ("DOWN", "0.05", "0.04"), ("DOWN", "0.05", "0.02"), ("DOWN", "0.05", "0.01"),
]
W1420 = [("UP", "0.08", "0.58"), ("DOWN", "0.08", "0.41"),
         ("UP", "0.08", "0.60"), ("DOWN", "0.08", "0.41"),
         ("DOWN", "0.06", "0.19")]


def rows(w, fee="0"):
    return [{"side": s, "qty": q, "cost": D(q) * D(p), "fee": fee}
            for s, q, p in w]


def test_the_1705z_window_matches_the_venues_own_position_row():
    a = A.attribute(rows(W1705), True)
    assert a["ok"] and a["matched"]["qty"] == D("0.41")
    assert a["matched"]["pnl"] == D("0.0296")          # venue: realized
    assert a["unmatched"]["cost"] == D("0.0447")       # venue: cost
    assert a["unmatched"]["side"] == "DOWN"
    assert a["unmatched"]["qty"] == D("0.44")
    assert a["unmatched"]["pnl"] == D("-0.0447")
    assert a["settlement_pnl"] == D("-0.0151") == a["net_usd"]
    assert a["fees_usd"] == 0


def test_the_1420z_window():
    a = A.attribute(rows(W1420), True)
    assert a["matched"]["qty"] == D("0.16")
    assert a["matched"]["pnl"] == D("0")               # pairs at 0.99, 1.01
    assert a["unmatched"]["pnl"] == D("-0.0114")
    assert a["net_usd"] == D("-0.0114")


@pytest.mark.parametrize("w", [W1705, W1420])
@pytest.mark.parametrize("up_won", [True, False])
def test_the_two_lines_always_sum_to_the_settlement(w, up_won):
    a = A.attribute(rows(w), up_won)
    q = {s: sum(D(x[1]) for x in w if x[0] == s) for s in ("UP", "DOWN")}
    cost = sum(D(x[1]) * D(x[2]) for x in w)
    assert a["matched"]["pnl"] + a["unmatched"]["pnl"] \
        == (q["UP"] if up_won else q["DOWN"]) - cost


def test_the_matched_line_does_not_depend_on_the_outcome():
    assert A.attribute(rows(W1705), True)["matched"] \
        == A.attribute(rows(W1705), False)["matched"]


def test_pairing_is_first_in_first_out_not_average_cost():
    #: a cheap late DOWN must not lower the cost of the pair locked earlier
    w = [("UP", "1", "0.60"), ("DOWN", "1", "0.39"), ("DOWN", "1", "0.01")]
    a = A.attribute(rows(w), True)
    assert a["matched"]["pair_cost"] == D("0.99")
    assert a["unmatched"]["cost"] == D("0.01")


def test_unsettled_leaves_the_outcome_lines_unstated():
    a = A.attribute(rows(W1705), None)
    assert a["matched"]["pnl"] == D("0.0296")
    assert a["unmatched"]["pnl"] is None and a["net_usd"] is None


def test_fees_are_subtracted_as_reported():
    a = A.attribute(rows(W1420, fee="0.001"), True)
    assert a["fees_usd"] == D("0.005")
    assert a["net_usd"] == D("-0.0114") - D("0.005")


def test_an_unread_fee_leaves_the_net_unstated():
    r = rows(W1420)
    r[2]["fee"] = None
    a = A.attribute(r, True)
    assert a["fees_usd"] is None and a["net_usd"] is None
    assert a["settlement_pnl"] == D("-0.0114")


def test_a_rebate_is_only_what_was_credited():
    assert A.attribute(rows(W1420), True)["rebates_credited_usd"] is None
    a = A.attribute(rows(W1420), True, rebates_credited_usd="0.002")
    assert a["net_usd"] == D("-0.0114") + D("0.002")


def test_a_flat_book_has_no_unmatched_line():
    a = A.attribute(rows([("UP", "1", "0.5"), ("DOWN", "1", "0.48")]), False)
    assert a["unmatched"]["side"] is None and a["unmatched"]["pnl"] == 0
    assert a["net_usd"] == D("0.02")


@pytest.mark.parametrize("bad", [
    [{"side": "SIDEWAYS", "qty": "1", "cost": "0.5"}],
    [{"side": "UP", "qty": "x", "cost": "0.5"}],
    [{"side": "UP", "qty": "1", "cost": "-0.5"}],
    [{"side": "UP", "qty": "1"}]])
def test_an_unreadable_fill_is_refused_not_guessed(bad):
    assert A.attribute(bad, True)["ok"] is False


def test_an_unreadable_outcome_is_refused():
    assert A.attribute(rows(W1420), "UP")["ok"] is False


def test_the_settlement_record_carries_it(tmp_path, monkeypatch):
    from src import l5_btc5m_execution as X
    from tests.test_l5_settlement_decoupled import oid, row
    ex = X.Btc5mExecutor(transport=None,
                         ledger_path=str(tmp_path / "ledger.json"))
    m = "cpc-btc-updown-5m-2026-11-16-1800z"
    ex._ledger["orders"] = dict([
        row(oid("a"), m, X.C.UP, "0.60", "1", "1", "0.60"),
        row(oid("b"), m, X.C.DOWN, "0.39", "1", "1", "0.39"),
        row(oid("c"), m, X.C.DOWN, "0.05", "1", "1", "0.05")])
    for i, c in enumerate(sorted(ex._ledger["orders"])):
        ex._ledger["orders"][c]["created_at"] = "2026-11-16T18:00:0%dZ" % i
    ex._ledger["inventory"] = {m: {X.C.UP: {"qty": "1", "cost": "0.60"},
                                   X.C.DOWN: {"qty": "2", "cost": "0.44"}}}
    s = ex.settlement_arithmetic(m, True)
    a = s["attribution"]
    assert D(a["matched"]["pnl"]) == D("0.01")
    assert D(a["unmatched"]["pnl"]) == D("-0.05")
    assert D(a["matched"]["pnl"]) + D(a["unmatched"]["pnl"]) \
        == s["settlement_pnl"]
    #: it is written to the ledger and logged, so it must be JSON
    import json
    json.dumps(a)


def test_the_module_is_arithmetic_and_nothing_else():
    import re
    src = open(A.__file__, encoding="utf-8").read()
    assert re.findall(r"^(?:from|import) (\S+)", src, re.M) == [
        "__future__", "decimal", "typing"]
    for word in ("os.", "open(", "urllib", "socket", "transport", "environ"):
        assert word not in src, word


def test_the_live_image_ships_it():
    df = open("deploy/l5_btc5m_live/Dockerfile", encoding="utf-8").read()
    assert "COPY src/l5_btc5m_attribution.py src/l5_btc5m_attribution.py" in df
