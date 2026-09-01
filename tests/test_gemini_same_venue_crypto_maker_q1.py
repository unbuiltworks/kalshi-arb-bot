from decimal import Decimal

from src import gemini_same_venue_crypto_maker_q1 as m

D = Decimal


def contract(symbol, strike, *, qmin="1", qinc="1", tick="0.01"):
    return {
        "instrumentSymbol": symbol,
        "strike": {"value": strike},
        "quantityMinimum": qmin,
        "quantityIncrement": qinc,
        "priceIncrement": tick,
    }


def row(*, existing="0", maker_ask=None):
    return {
        "event_ticker": "XRP2608312100",
        "event_title": "XRP price on August 31",
        "expiry": "2026-08-31T21:00:00.000Z",
        "seconds_to_expiry": 600000.0,
        "source": "GRR-KAIKO_RFR_XRPUSD_60S",
        "low_symbol": "GEMI-XRP2608312100-HI0D7",
        "low_strike": "0.7",
        "high_symbol": "GEMI-XRP2608312100-HI0D8",
        "high_strike": "0.8",
        "maker_symbol": "GEMI-XRP2608312100-HI0D7",
        "maker_outcome": "YES",
        "maker_ask": maker_ask,
        "existing_maker_bid": existing,
        "hedge_symbol": "GEMI-XRP2608312100-HI0D8",
        "hedge_outcome": "NO",
        "hedge_ask": "0.03",
        "hedge_depth": "515",
    }


def whole_contracts(*, maker_inc="1", hedge_inc="1"):
    return {
        "GEMI-XRP2608312100-HI0D7": contract("GEMI-XRP2608312100-HI0D7", "0.7", qinc=maker_inc, qmin=maker_inc),
        "GEMI-XRP2608312100-HI0D8": contract("GEMI-XRP2608312100-HI0D8", "0.8", qinc=hedge_inc, qmin=hedge_inc),
    }


def test_robust_frontier_reserves_one_hedge_tick_and_three_cents():
    quote, hedge_limit, profit = m.robust_frontier(
        maker_ask=None,
        observed_hedge_ask=D("0.03"),
        maker_tick=D("0.01"),
        hedge_tick=D("0.01"),
    )
    assert hedge_limit == D("0.04")
    assert quote == D("0.91")
    assert profit == D("0.03")
    assert m.locked_profit(D("0.92"), D("0.04")) == D("0.02")


def test_whole_contract_only_rejects_fractional_increment():
    assert m.whole_contract_only(contract("A", "1", qmin="1", qinc="1"))
    assert not m.whole_contract_only(contract("A", "1", qmin="0.01", qinc="0.01"))


def test_candidate_promotes_whole_contract_seed_at_robust_quote():
    c = m.candidate_from_row(row(), whole_contracts())
    assert c is not None
    assert c.maker_quote == D("0.91")
    assert c.hedge_limit == D("0.04")
    assert c.locked_profit == D("0.03")
    assert c.action == "SEED_FIRST_BID"


def test_fractional_maker_or_hedge_is_not_live_eligible():
    assert m.candidate_from_row(row(), whole_contracts(maker_inc="0.01")) is None
    assert m.candidate_from_row(row(), whole_contracts(hedge_inc="0.01")) is None


def test_robust_quote_cannot_sit_behind_unsafe_existing_bid():
    c = m.candidate_from_row(row(existing="0.92"), whole_contracts())
    assert c is None


def test_robust_quote_may_join_equal_safe_bid():
    c = m.candidate_from_row(row(existing="0.91"), whole_contracts())
    assert c is not None
    assert c.action == "JOIN_SAFE_BID"
