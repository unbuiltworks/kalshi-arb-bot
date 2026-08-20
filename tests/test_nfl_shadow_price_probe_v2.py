from decimal import Decimal

from src.gemini_kalshi_kalshi_fees import KalshiSeriesFeeMetadata
from src.nfl_identity_probe import NflIdentity
from src.nfl_shadow_price_probe import Quote
from src.nfl_shadow_price_probe_v2 import scan_v2

D=Decimal


def row(venue,market_id,start_ms=1_000_000):
    return NflIdentity(
        venue=venue,market_id=market_id,family="moneyline",teams=("NYJ","PIT"),
        subject="NYJ",comparator="YES",line=None,start_ms=start_ms,start_provenance="test",
    )


def test_unknown_kalshi_fee_can_report_gross_headroom_but_never_net_profit():
    rows=[row("gemini","g"),row("kalshi","k")]
    quotes={
        ("gemini","g"):Quote("gemini","g",D("0.40"),D("0.60")),
        ("kalshi","k"):Quote("kalshi","k",D("0.55"),D("0.45"),"KXNFLGAME"),
    }
    meta={"KXNFLGAME":KalshiSeriesFeeMetadata(
        series_ticker="KXNFLGAME",fee_type="quadratic_with_maker_fees",fee_multiplier="1",
        last_updated_ts="test",scheduled_changes=(),
    )}
    result=scan_v2(rows,quotes,meta)
    assert result["fee_unmapped_gross_positive_observations"]>=1
    candidate=result["top_fee_unmapped_gross"][0]
    assert candidate["gross_fee_headroom"]>"0"
    assert candidate["net_profit"] is None
    assert candidate["fee_status"]=="UNMAPPED_DO_NOT_CALCULATE_NET_PROFIT"
    assert candidate["rule_executable"] is False
    assert "GROSS_ONLY" in candidate["execution_status"]


def test_no_raw_gap_means_no_gross_candidate_even_with_unknown_fee():
    rows=[row("gemini","g"),row("kalshi","k")]
    quotes={
        ("gemini","g"):Quote("gemini","g",D("0.60"),D("0.40")),
        ("kalshi","k"):Quote("kalshi","k",D("0.65"),D("0.45"),"KXNFLGAME"),
    }
    meta={"KXNFLGAME":KalshiSeriesFeeMetadata("KXNFLGAME","quadratic_with_maker_fees","1","test",())}
    result=scan_v2(rows,quotes,meta)
    # At least one direction has raw cost >=1; neither may be falsely called a
    # positive if its complementary cost is not below $1.
    assert all(D(x["raw_cost"])<D("1") for x in result["top_fee_unmapped_gross"])


def test_fully_priced_gemini_sportsbook_candidate_stays_rule_blocked():
    rows=[row("gemini","g"),row("sportsbook:fanduel","fd")]
    quotes={
        ("gemini","g"):Quote("gemini","g",D("0.35"),D("0.65")),
        ("sportsbook:fanduel","fd"):Quote("sportsbook:fanduel","fd",D("0.70"),D("0.50")),
    }
    result=scan_v2(rows,quotes,{})
    assert result["fully_fee_priced_positive_observations"]>=1
    assert result["top_fully_priced"][0]["rule_executable"] is False
