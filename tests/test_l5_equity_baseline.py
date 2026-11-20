"""The equity baseline: a venue-confirmed starting point that erases nothing.

Pure functions. What matters: a baseline cannot be malformed into force, a
drawdown is measured from venue equity, money moving in or out can never be
mistaken for profit or loss, uncertain rows are never counted as confirmed,
and nothing here touches a ledger figure or an existing limit.
"""
from __future__ import annotations

import ast
import copy
import datetime as dt
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_equity_baseline as EB

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 11, 20, 1, 0, tzinfo=dt.timezone.utc)
AT = "2026-11-20T00:30:00+00:00"
SINCE = dt.datetime(2026, 11, 20, 0, 30, tzinfo=dt.timezone.utc)


def doc(**over):
    d = {"id": "baseline-20261120",
         "at_utc": AT,
         "buying_power_usd": "321.4782",
         "matched_redemption_usd": "2",
         "equity_usd": "323.4782",
         "positions": {"m1": {"UP": "2", "DOWN": "2"},
                       "m2": {"UP": "2.9", "DOWN": "0"}},
         "open_orders": 0,
         "max_drawdown_usd": "5",
         "historical": {"ledger_realized_usd": "-136.7845",
                        "prior_anchor_usd": "336.68",
                        "prior_drawdown_usd": "15.2018"},
         "acknowledgement": EB.ACKNOWLEDGEMENT}
    d.update(over)
    return d


def read(d):
    raw = d if isinstance(d, str) else json.dumps(d)
    return EB.read_document({EB.DOC_ENV: raw}, now=NOW)


def test_unset_is_off_and_refuses_nothing():
    off = EB.read_document({}, now=NOW)
    assert off == {"state": "OFF"}
    assert EB.refusal(off, None, None) is None
    assert EB.line(off, None, None)["refusal"] is None


def test_a_good_document_is_on_and_keeps_the_history():
    d = read(doc())
    assert d["state"] == "ON"
    assert d["equity_usd"] == D("323.4782")
    assert d["historical"] == {"ledger_realized_usd": "-136.7845",
                               "prior_anchor_usd": "336.68",
                               "prior_drawdown_usd": "15.2018"}
    out = EB.line(d, D("323.4782"), D(0))
    assert out["historical"]["ledger_realized_usd"] == "-136.7845"
    assert out["period_change_usd"] == "0.0000" and out["refusal"] is None


def _drop(key):
    d = doc()
    d.pop(key)
    return d


def _hist(**over):
    h = dict(doc()["historical"])
    h.update(over)
    return doc(historical=h)


@pytest.mark.parametrize("bad, why", [
    ("{not json", "NOT_JSON"),
    ("[]", "NOT_AN_OBJECT"),
    (_drop("historical"), "FIELDS"),
    (_drop("acknowledgement"), "FIELDS"),
    (doc(extra="x"), "FIELDS"),
    (doc(acknowledgement="ok"), "ACKNOWLEDGEMENT"),
    (doc(acknowledgement=EB.ACKNOWLEDGEMENT.lower()), "ACKNOWLEDGEMENT"),
    (doc(id="short"), "ID"),
    (doc(at_utc="yesterday"), "AT_UTC"),
    (doc(at_utc="2026-11-20T00:30:00"), "AT_UTC"),
    (doc(at_utc="2026-11-20T02:00:00+00:00"), "AT_UTC_IN_THE_FUTURE"),
    (doc(buying_power_usd="lots"), "FIGURES_UNREADABLE"),
    (doc(max_drawdown_usd="NaN"), "FIGURES_UNREADABLE"),
    (doc(max_drawdown_usd=True), "FIGURES_UNREADABLE"),
    (doc(max_drawdown_usd="0"), "FIGURES_OUT_OF_RANGE"),
    (doc(max_drawdown_usd="-5"), "FIGURES_OUT_OF_RANGE"),
    (doc(buying_power_usd="-1", equity_usd="1"), "FIGURES_OUT_OF_RANGE"),
    (doc(positions="none"), "POSITIONS_UNREADABLE"),
    (doc(positions={"m": {"UP": "-1", "DOWN": "0"}}), "POSITIONS_UNREADABLE"),
    (doc(matched_redemption_usd="3", equity_usd="324.4782"),
     "MATCHED_REDEMPTION_NE_POSITIONS"),
    (doc(equity_usd="336.68"), "EQUITY_NE_BUYING_POWER_PLUS_MATCHED"),
    (doc(open_orders=2), "BASELINE_READ_WITH_ORDERS_RESTING"),
    (doc(open_orders="0"), "OPEN_ORDERS"),
    (doc(open_orders=True), "OPEN_ORDERS"),
    (doc(historical={"ledger_realized_usd": "0"}), "HISTORICAL_FIELDS"),
    (_hist(ledger_realized_usd="unknown"), "HISTORICAL_UNREADABLE"),
])
def test_anything_not_exactly_right_is_invalid_and_refuses(bad, why):
    d = read(bad)
    assert d["state"] == "INVALID" and d["why"] == why
    r = EB.refusal(d, D("1000"), D(0))
    assert r == "EQUITY_BASELINE_%s" % why
    assert EB.line(d, D("1000"), D(0))["refusal"] == r


def test_the_equity_cannot_be_stated_above_what_the_venue_read_supports():
    """The obvious way to buy headroom: claim a higher starting equity."""
    assert read(doc(equity_usd="400"))["state"] == "INVALID"


@pytest.mark.parametrize("now_eq, moved, verdict", [
    ("323.4782", "0", None),
    ("318.4783", "0", None),                       # 4.9999 down
    ("318.4782", "0", "EQUITY_DRAWDOWN_LIMIT:5.0000>=5"),
    ("300", "0", "EQUITY_DRAWDOWN_LIMIT:23.4782>=5"),
    ("400", "0", None),
])
def test_the_drawdown_is_measured_from_the_baseline(now_eq, moved, verdict):
    assert EB.refusal(read(doc()), D(now_eq), D(moved)) == verdict


def test_missing_figures_refuse_and_are_never_read_as_zero():
    d = read(doc())
    assert EB.refusal(d, None, D(0)) == "EQUITY_UNAVAILABLE"
    assert EB.refusal(d, D("323.4782"), None) \
        == "EQUITY_TRANSFERS_NOT_ESTABLISHED"
    out = EB.line(d, D("323.4782"), None)
    assert "period_change_usd" not in out and "room_usd" not in out


# --- money moving in or out --------------------------------------------------

def seen(rows, back="2026-08-14T03:26:06+00:00"):
    return {"complete_back_to": back, "rows": rows}


def test_no_movement_since_a_baseline_the_feed_reaches_is_zero():
    out = EB.net_transfers(seen([
        {"type": "activity_type_account_deposit",
         "time": "2026-10-01T00:00:00+00:00"}]), SINCE)
    assert out["net_usd"] == D(0) and out["rows_before_baseline"] == 1


@pytest.mark.parametrize("s, why", [
    (None, "FEED_NOT_READ"),
    ({"rows": []}, "FEED_NOT_READ_BACK_TO_BASELINE"),
    (seen([], back="2026-11-20T00:45:00+00:00"),
     "FEED_NOT_READ_BACK_TO_BASELINE"),
    (seen("x"), "FEED_ROWS_UNREADABLE"),
    (seen(["x"]), "FEED_ROWS_UNREADABLE"),
    (seen([{"type": "activity_type_transfer", "time": None}]),
     "UNDATED_TRANSFER_ROW"),
    (seen([{"type": "activity_type_account_deposit",
            "time": "2026-11-20T00:40:00+00:00"}]),
     "MONEY_MOVED_SINCE_BASELINE"),
    (seen([{"type": "activity_type_transfer",
            "time": "2026-11-20T00:30:00+00:00"}]),
     "MONEY_MOVED_SINCE_BASELINE"),
])
def test_anything_short_of_proof_of_no_movement_refuses(s, why):
    out = EB.net_transfers(s, SINCE)
    assert out["net_usd"] is None and out["why"] == why
    assert EB.refusal(read(doc()), D("999"), out["net_usd"]) \
        == "EQUITY_TRANSFERS_NOT_ESTABLISHED"


def test_a_deposit_can_never_be_read_as_a_gain():
    """Equity up by exactly a deposit: without the row that is a 'profit'
    that hands back drawdown room. With the row it is a refusal."""
    moved = EB.net_transfers(seen([
        {"type": "activity_type_account_deposit",
         "time": "2026-11-20T00:50:00+00:00"}]), SINCE)
    assert EB.refusal(read(doc()), D("423.4782"), moved["net_usd"]) \
        is not None


# --- the period result -------------------------------------------------------

RETIRED = {
    "old": {"t": "2026-11-19T10:00:00+00:00", "settlement_pnl": "-50",
            "origin_cost_basis": "AUTHENTICATED_FILLS"},
    "a": {"t": "2026-11-20T00:40:00+00:00", "settlement_pnl": "0.12",
          "origin_cost_basis": "AUTHENTICATED_FILLS"},
    "b": {"t": "2026-11-20T00:45:00+00:00", "settlement_pnl": "-0.30",
          "origin_cost_basis": "UPPER_BOUND_1USD_PER_CONTRACT",
          "pnl_provenance": "VENUE_ATTESTED_COST"},
    "c": {"t": "2026-11-20T00:50:00+00:00", "settlement_pnl": "-5",
          "origin_cost_basis": "UPPER_BOUND_1USD_PER_CONTRACT",
          "pnl_provenance": "UNKNOWN_COST_UPPER_BOUND"},
    "d": {"t": "2026-11-20T00:55:00+00:00", "settlement_pnl": "0",
          "origin_cost_basis": "OWN_FILLS_ATTRIBUTION_UNVERIFIED"},
}


def test_only_measured_rows_count_and_the_period_starts_at_the_baseline():
    out = EB.period_realized(RETIRED, SINCE)
    assert D(out["measured_realized_usd"]) == D("-0.18")
    assert out["measured_rows"] == 2
    assert out["uncertain_rows"] == 2
    assert D(out["uncertain_booked_bound_usd"]) == D("-5")
    assert out["uncertain_markets"] == ["c", "d"]
    assert out["complete"] is True


def test_an_empty_period_reads_zero():
    out = EB.period_realized({"old": RETIRED["old"]}, SINCE)
    assert D(out["measured_realized_usd"]) == 0
    assert out["measured_rows"] == 0 and out["uncertain_rows"] == 0


def test_an_unreadable_row_makes_the_period_incomplete_not_zero():
    rows = dict(RETIRED, bad={"t": "2026-11-20T00:58:00+00:00",
                              "settlement_pnl": "?"},
                undated={"settlement_pnl": "-9"})
    out = EB.period_realized(rows, SINCE)
    assert out["unreadable_rows"] == 2 and out["complete"] is False


# --- it reads; it changes nothing --------------------------------------------

def test_nothing_is_altered_by_any_function():
    rows = copy.deepcopy(RETIRED)
    d = doc()
    env = {EB.DOC_ENV: json.dumps(d)}
    before = (copy.deepcopy(rows), dict(env))
    got = EB.read_document(env, now=NOW)
    EB.line(got, D("320"), D(0))
    EB.period_realized(rows, SINCE)
    EB.net_transfers(seen([]), SINCE)
    assert (rows, env) == before


def test_the_module_touches_no_ledger_limit_or_transport():
    src = (ROOT / "src" / "l5_equity_baseline.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)) \
                and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(getattr(node.body[0], "value", None),
                               ast.Constant):
            node.body = node.body[1:] or [ast.Pass()]
    code = ast.unparse(tree)
    imports = sorted({n.names[0].name for n in ast.walk(tree)
                      if isinstance(n, ast.Import)}
                     | {n.module for n in ast.walk(tree)
                        if isinstance(n, ast.ImportFrom)})
    assert imports == ["__future__", "datetime", "decimal", "json", "os",
                       "typing"]
    for word in ("realized_attributed", "L5_MAX_CUMULATIVE", "save(",
                 "state[", "open(", "os.environ[", "_get", "_post",
                 "submit", "cancel"):
        assert word not in code, word


def test_the_acknowledgement_says_what_it_does_not_do():
    a = EB.ACKNOWLEDGEMENT
    assert "NOT ERASED" in a and "DOES NOT RESET, RAISE OR REPLACE" in a


def test_the_sign_of_a_transfer_figure_is_pinned():
    """`net_transfers` only ever yields 0 today, but the arithmetic takes a
    figure: money IN raises the bar equity must clear, money OUT lowers it."""
    d = read(doc())
    assert EB.refusal(d, D("333.4782"), D("10")) is None
    assert EB.line(d, D("333.4782"), D("10"))["period_change_usd"] == "0.0000"
    assert EB.refusal(d, D("323.4782"), D("10")) \
        == "EQUITY_DRAWDOWN_LIMIT:10.0000>=5"
    assert EB.refusal(d, D("313.4782"), D("-10")) is None


def test_a_gain_never_adds_room():
    """Equity above the baseline -- which is also what an unseen deposit
    looks like -- leaves room at the allowance, not above it."""
    d = read(doc())
    up = EB.line(d, D("373.4782"), D(0))
    assert up["period_change_usd"] == "50.0000"
    assert up["room_usd"] == "5" and up["refusal"] is None
    down = EB.line(d, D("321.4782"), D(0))
    assert down["room_usd"] == "3.0000"
