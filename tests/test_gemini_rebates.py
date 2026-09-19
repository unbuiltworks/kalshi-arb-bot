"""Rebate credits: confirmed only when the venue's own record shows them.

The owner's Activity screenshots show real Maker Rebate Reward credits. A
screenshot is not an authoritative record and a UI total is not a rate, so
these tests are mostly about what this module REFUSES to produce.
"""
import pytest

from src.gemini_execution import rebates as RB


# the shape `/v1/transfers` actually returns, from the 2026-09-19 survey
def _row(amount, ms, typ="Rebate", purpose="Maker Rebate Reward", eid=1):
    return {"type": typ, "status": "Complete", "timestampms": ms,
            "eid": eid, "currency": "USD", "amount": amount,
            "purpose": purpose}


# a real trade date from the window in question
VOL = [[[{"symbol": "GEMI-BTC05M2608301700-UP", "data_date": "2026-08-30",
          "buy_maker_notional": "40.00", "sell_maker_notional": "10.00",
          "buy_maker_count": "3", "sell_maker_count": "1"},
         {"symbol": "BTCUSD", "data_date": "2026-08-30",
          "buy_maker_notional": "9999.00", "sell_maker_notional": "1.00",
          "buy_maker_count": "1", "sell_maker_count": "1"}]]]


# ======================================================= reading the credits

def test_it_reads_the_credits_the_venue_records():
    got = RB.parse_credits([_row("0.14", 1787000000000, eid=11),
                            _row("0.05", 1786900000000, eid=12)])
    assert got["readable"] is True and got["n"] == 2
    assert got["total"] == "0.19"
    assert got["currencies"] == ["USD"]
    # oldest first, so a window filter reads naturally
    assert [c["amount"] for c in got["credits"]] == ["0.05", "0.14"]
    c = got["credits"][0]
    assert c["utc"].startswith("2026-")
    assert "authenticated account record" in c["source"]


def test_a_non_rebate_transfer_is_not_a_rebate():
    """The account's transfer list carries deposits and withdrawals too."""
    rows = [_row("0.14", 1787000000000, eid=1),
            {"type": "Deposit", "status": "Complete", "amount": "500.00",
             "currency": "USD", "timestampms": 1787000000001, "eid": 2},
            {"type": "Withdrawal", "status": "Complete", "amount": "100.00",
             "currency": "USD", "timestampms": 1787000000002, "eid": 3}]
    got = RB.parse_credits(rows)
    assert got["n"] == 1 and got["total"] == "0.14"


def test_a_repeated_read_does_not_double_count_a_credit():
    """The venue's own event id is the credit's identity, exactly as `tid` is
    an execution's."""
    r = _row("0.17", 1787100000000, eid=99)
    got = RB.parse_credits([r, dict(r), dict(r)])
    assert got["n"] == 1 and got["total"] == "0.17"


def test_an_unreadable_transfer_list_is_not_an_empty_one():
    got = RB.parse_credits(None)
    assert got["readable"] is False and got["n"] == 0
    assert "not absence of credits" in got["why"]


def test_no_credit_claims_a_fill_it_cannot_name():
    """THE REFUSAL THAT MATTERS. This surface carries no order id, no symbol
    and no fill linkage. A precise, traceable-looking, untrue attribution
    would be the worst number in this module."""
    got = RB.parse_credits([_row("0.14", 1787000000000)])
    c = got["credits"][0]
    assert c["related_fills"] is None
    assert "NOT available" in c["related_fills_why"]
    assert c["attribution_confidence"] == RB.UNATTRIBUTED


# ==================================================== the denominator

def test_maker_volume_counts_only_prediction_instruments():
    """A spot BTCUSD maker fill is not part of this programme's denominator,
    and including it would shrink the rate by three orders of magnitude."""
    got = RB.parse_maker_volume(VOL)
    assert got["readable"] is True and got["rows_matched"] == 1
    assert got["maker_notional_total"] == "50.00"
    assert got["maker_notional_by_day"] == {"2026-08-30": "50.00"}
    assert got["maker_fill_count_by_day"] == {"2026-08-30": 4}


def test_an_unreadable_volume_read_is_unreadable():
    assert RB.parse_maker_volume(None)["readable"] is False


# ===================================================== the effective rate

def test_volume_to_rebate_to_rate_when_the_denominator_is_defensible():
    credits = RB.parse_credits([_row("0.05", 1787000000000, eid=1)])
    got = RB.effective_rate(credits, RB.parse_maker_volume(VOL))
    assert got["computable"] is True
    assert got["rebates_confirmed_usd"] == "0.05"
    assert got["maker_notional_usd"] == "50.00"
    assert got["effective_rate"] == "0.001000"
    assert got["effective_bps"] == "10.00"
    assert got["attribution_confidence"] == RB.WINDOW_LEVEL


def test_NO_rate_is_stated_when_there_is_no_maker_volume():
    """A credit with no measurable volume behind it is an aggregate, not a
    rate. Dividing by nothing and calling the result an effective rate is the
    number this refuses to produce."""
    credits = RB.parse_credits([_row("0.14", 1787000000000)])
    empty = RB.parse_maker_volume([[[]]])
    got = RB.effective_rate(credits, empty)
    assert got["computable"] is False
    assert got["rebates_confirmed_usd"] == "0.14"     # numerator still stands
    assert "no defensible denominator" in got["why"]
    assert "effective_rate" not in got


def test_NO_rate_is_stated_when_the_denominator_is_unreadable():
    credits = RB.parse_credits([_row("0.14", 1787000000000)])
    got = RB.effective_rate(credits, RB.parse_maker_volume(None))
    assert got["computable"] is False
    assert "UNREADABLE" in got["why"]
    assert got["rebates_confirmed_usd"] == "0.14"


def test_the_window_filter_selects_only_credits_inside_it():
    credits = RB.parse_credits([_row("0.05", 1787000000000, eid=1),
                                _row("9.99", 1400000000000, eid=2)])
    got = RB.effective_rate(credits, RB.parse_maker_volume(VOL),
                            since_utc="2026-01-01T00:00:00+00:00")
    assert got["credits_in_window"] == 1
    assert got["rebates_confirmed_usd"] == "0.05"


# ======================================================== the seven lines

def test_the_ledger_keeps_all_seven_lines_apart():
    got = RB.realized_ledger(
        matched_pair_pnl="0.10", directional_residual_pnl="-0.02",
        spread_capture="0.01", confirmed_maker_rebates="0.43",
        confirmed_other_rewards="0", fees="-0.05",
        settlement_cashflow="0.20")
    assert sorted(got["lines"]) == sorted(RB.LEDGER_LINES)
    assert got["unmeasured"] == []
    assert got["realized_net_pnl"] == "0.67"


def test_an_UNMEASURED_line_makes_the_TOTAL_unmeasured():
    """Treating an unmeasured component as zero reports a net number the
    evidence does not support -- and every one of these lines has been
    unmeasured at some point in this programme."""
    got = RB.realized_ledger(matched_pair_pnl="0.10",
                             confirmed_maker_rebates="0.43")
    assert got["realized_net_pnl"] is None
    assert "fees" in got["unmeasured"]
    assert "settlement_cashflow" in got["unmeasured"]


def test_estimated_rebates_are_carried_but_NEVER_added():
    got = RB.realized_ledger(
        matched_pair_pnl="0", directional_residual_pnl="0",
        spread_capture="0", confirmed_maker_rebates="0",
        confirmed_other_rewards="0", fees="0", settlement_cashflow="0",
        estimated_rebates="99.00", estimated_rewards="1.00")
    assert got["realized_net_pnl"] == "0"
    assert got["estimated_not_counted"]["estimated_rebates"] == "99.00"
    assert "NEVER added" in got["estimated_not_counted"]["why"]


def test_the_C8B_maker_fill_earns_no_assumed_rebate():
    """C8B filled YES 0.01 @ 0.31 with aggressor false and fee 0. That is
    maker EVIDENCE. It is not a rebate until a credit can be attributed or
    the venue's own rules establish qualification, so the confirmed line
    stays at what the account record shows and nothing is accrued for it."""
    got = RB.realized_ledger(confirmed_maker_rebates="0",
                             estimated_rebates=None)
    assert got["lines"]["confirmed_maker_rebates"] == "0"
    assert got["estimated_not_counted"]["estimated_rebates"] is None


@pytest.mark.parametrize("bad", [None, "", "not a list", 5])
def test_garbage_in_is_refused_not_guessed(bad):
    got = RB.parse_credits(bad)
    assert got["readable"] is False and got["n"] == 0


def test_the_symbol_match_is_CASE_INSENSITIVE():
    """/v1/tradevolume returns `gemi-btc05m...-up` in LOWER case while every
    other Gemini surface returns it upper. A case-sensitive prefix matched
    ZERO of 62 real prediction rows on 2026-09-19 and produced a confident
    verdict that the endpoint does not cover prediction instruments. It
    covers them completely; the filter was the defect."""
    lower = [[[{"symbol": "gemi-btc05m2608271730-up", "data_date": "2026-08-27",
                "buy_maker_notional": "5.64", "sell_maker_notional": "0",
                "buy_maker_count": "2", "sell_maker_count": "0"}]]]
    got = RB.parse_maker_volume(lower)
    assert got["rows_matched"] == 1
    assert got["maker_notional_total"] == "5.64"


def test_the_coverage_diagnostic_also_matches_case_insensitively():
    lower = [[[{"symbol": "gemi-btc05m2608271730-up", "data_date": "2026-08-27",
                "buy_maker_notional": "1", "sell_maker_notional": "0"}]]]
    cov = RB._coverage(lower)
    assert cov["verdict"] == "COVERS_PREDICTION_INSTRUMENTS"
    assert cov["prediction_symbols"] == ["gemi-btc05m2608271730-up"]
