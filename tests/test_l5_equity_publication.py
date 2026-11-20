"""What l5-pmus-live PUBLISHES about venue equity, for a second strategy.

The published copy must refuse whenever the value behind it is not known to
be current: buying power never read, buying power stale, order state
uncertain, positions unreadable, transfers not established, baseline
malformed, or a limit breached. And publishing it must change no ledger
figure and none of this service's own gates.
"""
from __future__ import annotations

import collections
import copy
import datetime as dt
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_equity_baseline as EB
from src import l5_equity_feed as EF
from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_portfolio_drawdown as PDD

from tests.test_l5_venue_cost_attestation import Feed, fill

ROOT = Path(__file__).resolve().parents[1]
T0 = dt.datetime(2026, 11, 20, 0, 30, tzinfo=dt.timezone.utc)
NOW = T0.timestamp() + 600
M = "cpc-btc-updown-5m-2026-11-20-0100z"


class Rn:
    equity_risk_lines = R.Runner.equity_risk_lines
    maybe_watch_equity_transfers = R.Runner.maybe_watch_equity_transfers

    def __init__(self, store, feed=None):
        self.store = store
        self.transport = feed
        self.available_cash_usd = D("321.4782")
        self.available_cash_t = NOW - 30
        self.stats = collections.Counter()


def baseline(**over):
    d = {"id": "baseline-20261120", "at_utc": T0.isoformat(),
         "buying_power_usd": "321.4782", "matched_redemption_usd": "0",
         "equity_usd": "321.4782", "positions": {}, "open_orders": 0,
         "max_drawdown_usd": "5",
         "historical": {"ledger_realized_usd": "-136.7845",
                        "prior_anchor_usd": "336.68",
                        "prior_drawdown_usd": "15.2018"},
         "acknowledgement": EB.ACKNOWLEDGEMENT}
    d.update(over)
    return json.dumps(d)


@pytest.fixture
def store(tmp_path):
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    s.save()
    return s


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(PDD.ANCHOR_ENV, "336.68")
    monkeypatch.setenv(PDD.MAX_DRAWDOWN_ENV, "15")
    monkeypatch.setenv(EB.DOC_ENV, baseline())


def quiet_feed():
    return Feed([[fill(M, "UP", "1", "0.5", "T1", "V1",
                       t="2026-11-20T00:35:00Z")]])


def watched(store, feed=None):
    r = Rn(store, feed or quiet_feed())
    for i in range(5):
        r.maybe_watch_equity_transfers(NOW - 20 + i)
    return r


def refusals(out):
    return (out["portfolio_drawdown"]["refusal"],
            out["equity_baseline"]["refusal"])


def test_with_nothing_configured_both_blocks_are_off(store, monkeypatch):
    for k in (PDD.ANCHOR_ENV, PDD.MAX_DRAWDOWN_ENV, EB.DOC_ENV):
        monkeypatch.delenv(k, raising=False)
    feed = quiet_feed()
    r = Rn(store, feed)
    r.maybe_watch_equity_transfers(NOW)
    out = r.equity_risk_lines(NOW)
    assert out["portfolio_drawdown"]["state"] == "OFF"
    assert out["equity_baseline"]["state"] == "OFF"
    assert refusals(out) == (None, None)
    assert feed.reads == []            # no baseline, no feed read


def test_the_existing_limit_is_published_as_breached(store, env):
    out = watched(store).equity_risk_lines(NOW)
    assert out["portfolio_drawdown"]["refusal"] \
        == "PORTFOLIO_DRAWDOWN_LIMIT:15.2018>=15"
    eq = out["equity_baseline"]
    assert eq["refusal"] is None and eq["period_change_usd"] == "0.0000"
    assert eq["historical"]["ledger_realized_usd"] == "-136.7845"
    assert out["equity_value"]["usd"] == "321.4782"


def test_a_loss_past_the_baseline_allowance_is_published(store, env):
    r = watched(store)
    r.available_cash_usd = D("316.4782")
    assert r.equity_risk_lines(NOW)["equity_baseline"]["refusal"] \
        == "EQUITY_DRAWDOWN_LIMIT:5.0000>=5"


@pytest.mark.parametrize("cash, age, why", [
    (None, None, "BUYING_POWER_NOT_READ"),
    (D("321.4782"), 181.0, "BUYING_POWER_STALE"),
    (D("321.4782"), 3600.0, "BUYING_POWER_STALE"),
    (D("321.4782"), -60.0, "BUYING_POWER_STALE"),
])
def test_a_missing_or_stale_balance_blocks_both(store, env, cash, age, why):
    r = watched(store)
    r.available_cash_usd = cash
    r.available_cash_t = None if age is None else NOW - age
    out = r.equity_risk_lines(NOW)
    assert out["equity_value"] == {
        "usd": None, "unavailable_why": why,
        "buying_power_age_s": age, "max_age_s": R.EQUITY_CASH_MAX_AGE_S}
    assert refusals(out) == ("PORTFOLIO_VALUE_UNAVAILABLE",
                             "EQUITY_UNAVAILABLE")


def test_a_balance_just_inside_the_age_limit_is_used(store, env):
    r = watched(store)
    r.available_cash_t = NOW - 180.0
    assert r.equity_risk_lines(NOW)["equity_value"]["usd"] == "321.4782"


def test_uncertain_order_state_blocks_both(store, env):
    store.mark_uncertain("cancel outcome unknown")
    out = watched(store).equity_risk_lines(NOW)
    assert out["equity_value"]["unavailable_why"] == "ORDER_STATE_UNCERTAIN"
    assert refusals(out) == ("PORTFOLIO_VALUE_UNAVAILABLE",
                             "EQUITY_UNAVAILABLE")


def test_unreadable_positions_block_both(store, env):
    store.state["positions"] = {M + "|UP": "not a number"}
    out = watched(store).equity_risk_lines(NOW)
    assert refusals(out)[0] is not None and refusals(out)[1] is not None
    assert out["equity_value"].get("usd") is None


def test_an_unmatched_position_is_worth_nothing(store, env):
    store.state["positions"] = {M + "|UP": "50"}
    assert watched(store).equity_risk_lines(NOW)["equity_value"]["usd"] \
        == "321.4782"


def test_a_matched_pair_is_worth_its_redemption(store, env):
    store.state["positions"] = {M + "|UP": "3", M + "|DOWN": "2"}
    assert watched(store).equity_risk_lines(NOW)["equity_value"]["usd"] \
        == "323.4782"


def test_transfers_never_read_block_the_baseline_not_the_old_limit(
        store, env):
    out = Rn(store, quiet_feed()).equity_risk_lines(NOW)      # never stepped
    assert out["equity_baseline"]["refusal"] \
        == "EQUITY_TRANSFERS_NOT_ESTABLISHED"
    assert out["equity_baseline"]["transfers"]["why"] == "FEED_NOT_READ"
    assert out["portfolio_drawdown"]["refusal"].startswith(
        "PORTFOLIO_DRAWDOWN_LIMIT")


def test_a_deposit_since_the_baseline_blocks(store, env):
    feed = Feed([[{"type": "activity_type_account_deposit",
                   "accountBalanceChange": {"time": "2026-11-20T00:36:00Z",
                                            "amount": "100"}}]])
    r = watched(store, feed)
    r.available_cash_usd = D("421.4782")          # "up $100"
    eq = r.equity_risk_lines(NOW)["equity_baseline"]
    assert eq["refusal"] == "EQUITY_TRANSFERS_NOT_ESTABLISHED"
    assert eq["transfers"]["why"] == "MONEY_MOVED_SINCE_BASELINE"
    assert "period_change_usd" not in eq


def test_a_transfer_read_that_went_stale_blocks(store, env):
    r = watched(store)
    later = NOW + EF.STALE_S + 60
    r.available_cash_t = later - 10
    eq = r.equity_risk_lines(later)["equity_baseline"]
    assert eq["refusal"] == "EQUITY_TRANSFERS_NOT_ESTABLISHED"


def test_a_feed_error_blocks_and_never_raises(store, env):
    feed = quiet_feed()
    feed.fail = RuntimeError("venue down")
    eq = watched(store, feed).equity_risk_lines(NOW)["equity_baseline"]
    assert eq["refusal"] == "EQUITY_TRANSFERS_NOT_ESTABLISHED"


def test_a_malformed_baseline_blocks(store, monkeypatch, env):
    monkeypatch.setenv(EB.DOC_ENV, baseline(equity_usd="400"))
    eq = watched(store).equity_risk_lines(NOW)["equity_baseline"]
    assert eq["refusal"] \
        == "EQUITY_BASELINE_EQUITY_NE_BUYING_POWER_PLUS_MATCHED"


def test_the_period_separates_measured_from_uncertain(store, env):
    store.state["retired_markets"] = {
        "before": {"t": "2026-11-19T00:00:00+00:00", "settlement_pnl": "-90",
                   "origin_cost_basis": "AUTHENTICATED_FILLS"},
        "m": {"t": "2026-11-20T00:40:00+00:00", "settlement_pnl": "-0.07",
              "origin_cost_basis": "AUTHENTICATED_FILLS"},
        "u": {"t": "2026-11-20T00:41:00+00:00", "settlement_pnl": "-5",
              "origin_cost_basis": "UPPER_BOUND_1USD_PER_CONTRACT"}}
    p = watched(store).equity_risk_lines(NOW)["equity_baseline"]["period"]
    assert D(p["measured_realized_usd"]) == D("-0.07")
    assert p["uncertain_rows"] == 1
    assert D(p["uncertain_booked_bound_usd"]) == D("-5")


def test_a_failure_inside_is_published_as_a_refusal(store, env):
    r = watched(store)
    r.store = None
    out = r.equity_risk_lines(NOW)
    assert all(x and x.startswith("EQUITY_LINES_FAILED") for x in
               refusals(out))


def test_publishing_changes_nothing_in_the_ledger(store, env, tmp_path):
    store.state["retired_markets"] = {
        "m": {"t": "2026-11-20T00:40:00+00:00", "settlement_pnl": "-0.07",
              "origin_cost_basis": "AUTHENTICATED_FILLS"}}
    store.state["realized_attributed"] = "-136.7845"
    store.save()
    disk = (tmp_path / "inv.json").read_bytes()
    before = copy.deepcopy(store.state)
    r = watched(store)
    for _ in range(3):
        r.equity_risk_lines(NOW)
    assert store.state == before
    assert (tmp_path / "inv.json").read_bytes() == disk
    assert store.realized_today() == D("-136.7845")


def _src():
    return (ROOT / "src" / "l5_pmus_live_runner.py").read_text()


def test_the_risk_record_carries_the_blocks_and_keeps_every_old_figure():
    src = _src()
    i = src.index("    def account_risk_line(self)")
    body = src[i:src.index("    def day_realized_line(self)")]
    assert "**self.equity_risk_lines()," in body
    for old in ('"utc_day_realized_usd": dr', '"daily_halt_usd"',
                '"lifetime_realized_usd": str(lifetime)',
                '"cumulative_loss_ceiling_usd"', '"cumulative_would_halt"'):
        assert old in body, old


def test_this_services_own_gates_do_not_read_the_new_module():
    for name in ("l5_live_validation_gates.py", "l5_live_execution.py",
                 "l5_portfolio_drawdown.py"):
        text = (ROOT / "src" / name).read_text()
        assert "l5_equity_baseline" not in text, name
        assert "l5_equity_feed" not in text, name
    # and the gate's own value is still computed where it was
    lx = (ROOT / "src" / "l5_live_execution.py").read_text()
    assert "return PDD.worst_case_value(self.available_cash_usd, pos)" in lx


def test_the_watch_runs_on_the_tick_and_is_swallowed():
    src = _src()
    assert src.count("self.maybe_watch_equity_transfers(now)") == 1
    i = src.index("    def maybe_watch_equity_transfers")
    body = src[i:src.index("    def equity_risk_lines")]
    assert 'if doc.get("state") != "ON":' in body
    assert "except BaseException" in body


def test_the_live_image_ships_both_modules():
    d = (ROOT / "deploy" / "l5_pmus_live" / "Dockerfile").read_text()
    for m in ("l5_equity_baseline.py", "l5_equity_feed.py"):
        assert "COPY src/%s src/%s" % (m, m) in d
