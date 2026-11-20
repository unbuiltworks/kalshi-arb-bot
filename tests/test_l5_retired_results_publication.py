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
