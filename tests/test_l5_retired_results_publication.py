"""Today's retired markets are published WITH the result each was booked
at, so another strategy can add exactly what this ledger is missing."""
import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

NOW = dt.datetime(2026, 11, 20, 17, 8, tzinfo=dt.timezone.utc).timestamp()
M1 = "cpc-btc-updown-5m-2026-11-20-1420z"
M2 = "cpc-btc-updown-5m-2026-11-20-1705z"
OLD = "cpc-btc-updown-15m-2026-11-19-1200z"


@pytest.fixture
def store(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.state["retired_markets"] = {
        M1: {"t": "2026-11-20T14:25:49+00:00", "settlement_pnl": "-0.0114"},
        M2: {"t": "2026-11-20T17:10:47+00:00", "settlement_pnl": "-0.44"},
        OLD: {"t": "2026-11-19T12:16:00+00:00", "settlement_pnl": "-3"}}
    return s


def test_it_lists_exactly_todays_rows_with_their_amounts(store):
    got = store.retired_results_current_utc_day(now=NOW)
    assert got == {M1: "-0.0114", M2: "-0.44"}


def test_it_sums_to_the_figure_the_daily_halt_reads(store):
    got = store.retired_results_current_utc_day(now=NOW)
    assert sum(D(v) for v in got.values()) \
        == store.realized_current_utc_day(now=NOW)


@pytest.mark.parametrize("break_it", [
    lambda s: s.state["retired_markets"].__setitem__("x", "not a row"),
    lambda s: s.state["retired_markets"].__setitem__(
        "x", {"t": "", "settlement_pnl": "1"}),
    lambda s: s.state["retired_markets"].__setitem__(
        "x", {"t": "2026-11-20T01:00:00+00:00", "settlement_pnl": "?"}),
    lambda s: s.state.__setitem__("realized_unattributed", "-1"),
])
def test_it_is_unknown_exactly_when_the_day_figure_is(store, break_it):
    break_it(store)
    assert store.realized_current_utc_day(now=NOW) is None
    assert store.retired_results_current_utc_day(now=NOW) is None


def test_a_restated_row_is_published_at_its_restated_amount(store):
    store.state["retired_markets"][M2]["settlement_pnl"] = "-0.0151"
    assert store.retired_results_current_utc_day(now=NOW)[M2] == "-0.0151"


def test_reading_changes_nothing(store):
    before = repr(store.state)
    store.retired_results_current_utc_day(now=NOW)
    assert repr(store.state) == before


def test_the_risk_record_carries_it_as_an_added_key():
    src = open(R.__file__, encoding="utf-8").read()
    i = src.index("def account_risk_line(")
    body = src[i:src.index("\n    def ", i + 10)]
    assert '"retired_results_utc_day"' in body
    #: every earlier key is still there
    for k in ("utc_day_realized_usd", "daily_halt_usd",
              "lifetime_realized_usd", "cumulative_loss_ceiling_usd",
              "operator_halt", '"retired_markets"'):
        assert k in body, k


def test_the_line_never_raises_and_says_none_when_unknown():
    class S:
        def retired_results_current_utc_day(self):
            raise RuntimeError("disk")

    class Rn:
        _retired_results_line = R.Runner._retired_results_line
        store = S()
    assert Rn()._retired_results_line("2026-11-20") is None

    class S2:
        def retired_results_current_utc_day(self):
            return {M1: "-0.0114"}
    Rn.store = S2()
    assert Rn()._retired_results_line(None) is None
    assert Rn()._retired_results_line("2026-11-20") == {
        "utc_day": "2026-11-20", "results": {M1: "-0.0114"}}


# ================================================= the lifetime basis

REBASE = {"t": "2026-11-16T00:20:00+00:00", "attributed": "-30",
          "unattributed": "-9.468", "cash_delta": "-39.468", "reason": "r"}
ANCHOR = {"at_read_utc": "2026-11-16T00:11:52Z"}
PRE = "cpc-btc-updown-5m-2026-11-15-2300z"


@pytest.fixture
def anchored(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.state["accounting_rebase_log"] = [dict(REBASE)]
    s.state["cash_anchor"] = dict(ANCHOR)
    s.state["realized_attributed"] = "-30.0114"
    s.state["realized_unattributed"] = "-9.468"
    s.state["retired_markets"] = {
        PRE: {"t": "2026-11-15T23:05:00+00:00", "settlement_pnl": "-7"},
        M1: {"t": "2026-11-20T14:25:49+00:00", "settlement_pnl": "-0.0114"}}
    return s


def test_it_lists_only_rows_retired_after_the_reanchor(anchored):
    b = anchored.lifetime_basis()
    assert b["results_since_rebase"] == {M1: "-0.0114"}
    assert b["cash_read_utc"] == "2026-11-16T00:11:52+00:00"
    assert b["rebased_at_utc"] == "2026-11-16T00:20:00+00:00"
    assert b["complete"] is True and b["reconciled"] is True
    assert b["lifetime_realized_usd"] == str(anchored.realized_today())


def test_reconciled_means_anchor_plus_rows_equals_the_lifetime_figure(
        anchored):
    anchored.state["realized_attributed"] = "-30.5"     # something else moved
    assert anchored.lifetime_basis()["reconciled"] is False


def test_a_restated_row_stays_reconciled_when_the_total_moves_with_it(
        anchored):
    anchored.state["retired_markets"][M2] = {
        "t": "2026-11-20T17:10:47+00:00", "settlement_pnl": "-0.44"}
    anchored.state["realized_attributed"] = "-30.4514"
    assert anchored.lifetime_basis()["reconciled"] is True
    anchored.state["retired_markets"][M2]["settlement_pnl"] = "-0.0151"
    anchored.state["realized_attributed"] = "-30.0265"
    b = anchored.lifetime_basis()
    assert b["reconciled"] is True
    assert b["results_since_rebase"][M2] == "-0.0151"


def test_with_no_reanchor_every_row_counts(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.state["retired_markets"] = {
        M1: {"t": "2026-11-20T14:25:49+00:00", "settlement_pnl": "-0.0114"}}
    s.state["realized_attributed"] = "-0.0114"
    b = s.lifetime_basis()
    assert b["cash_read_utc"] is None and b["rebased_at_utc"] is None
    assert b["results_since_rebase"] == {M1: "-0.0114"}
    assert b["reconciled"] is True


@pytest.mark.parametrize("break_it", [
    lambda s: s.state["retired_markets"].__setitem__("x", "not a row"),
    lambda s: s.state["retired_markets"].__setitem__(
        "x", {"t": "", "settlement_pnl": "1"}),
    lambda s: s.state["retired_markets"].__setitem__(
        "x", {"t": "2026-11-20T01:00:00+00:00", "settlement_pnl": "?"}),
    lambda s: s.state["retired_markets"].__setitem__(
        "x", {"t": "2026-11-20T01:00:00+00:00", "settlement_pnl": "nan"}),
    lambda s: s.state.__setitem__("cash_anchor", {}),
    lambda s: s.state.__setitem__("cash_anchor",
                                  {"at_read_utc": "2026-11-17T00:00:00Z"}),
    lambda s: s.state["accounting_rebase_log"][-1].pop("attributed"),
    lambda s: s.state["accounting_rebase_log"][-1].__setitem__("t", "?"),
])
def test_anything_unreadable_makes_the_basis_unknown(anchored, break_it):
    break_it(anchored)
    assert anchored.lifetime_basis() is None


def test_past_the_row_limit_it_keeps_the_newest_and_says_incomplete(
        anchored, monkeypatch):
    monkeypatch.setattr(LX.InventoryStore, "LIFETIME_BASIS_MAX_ROWS", 2)
    for i in range(4):
        anchored.state["retired_markets"]["m%d" % i] = {
            "t": "2026-11-20T15:0%d:00+00:00" % i, "settlement_pnl": "0"}
    b = anchored.lifetime_basis()
    assert b["complete"] is False and b["rows"] == 2
    assert set(b["results_since_rebase"]) == {"m2", "m3"}


def test_reading_the_basis_changes_nothing(anchored):
    before = repr(anchored.state)
    anchored.lifetime_basis()
    assert repr(anchored.state) == before


def test_the_risk_record_carries_the_basis_as_an_added_key():
    src = open(R.__file__, encoding="utf-8").read()
    i = src.index("def account_risk_line(")
    body = src[i:src.index("\n    def ", i + 10)]
    assert '"lifetime_basis"' in body
    for k in ("utc_day_realized_usd", "lifetime_realized_usd",
              "cumulative_loss_ceiling_usd", '"retired_markets"',
              '"retired_results_utc_day"'):
        assert k in body, k


def test_the_basis_line_never_raises():
    class S:
        def lifetime_basis(self):
            raise RuntimeError("disk")

    class Rn:
        _lifetime_basis_line = R.Runner._lifetime_basis_line
        store = S()
    assert Rn()._lifetime_basis_line() is None
