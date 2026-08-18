from decimal import Decimal

from src.mlb_game_side_normalization import NormalizedSide
from src.strict_mlb_prop_snapshot import TopBook
from src.three_venue_pricing_core import (
    gemini_contract_book,
    gemini_one_contract_fee,
    make_priced_quote,
    orient_book,
)
from src.universal_sports_identity import RuleAudit, SportsContractIdentity, SportsEventIdentity

D = Decimal


def _side(venue="gemini", source_side="CONTRACT"):
    event = SportsEventIdentity(
        sport="baseball", league="mlb", participants=("MIA", "PHI"),
        scheduled_start_ms=1786992000000,
    )
    identity = SportsContractIdentity(
        venue=venue,
        market_id=f"{venue}-phi",
        event=event,
        market_family="moneyline",
        scope="full_contest",
        subject_type="team",
        subject="PHI",
        stat="",
        comparator="YES",
        line=None,
        rule_audit=RuleAudit("source", "ot", "postpone", "corrections", "tie", complete=False),
    )
    return NormalizedSide(identity, f"{venue}-phi", source_side, "Philadelphia")


def test_gemini_q1_taker_fee_rounds_up_to_cent():
    assert gemini_one_contract_fee(D("0.40")) == D("0.02")
    assert gemini_one_contract_fee(D("0.50")) == D("0.02")
    assert gemini_one_contract_fee(D("0.01")) == D("0.01")


def test_gemini_contract_requires_executable_best_quotes():
    # Reference buy prices can exist even when Gemini has no live order book.
    assert gemini_contract_book({"prices": {"buy": {"yes": "0.40", "no": "0.60"}}}) is None


def test_gemini_best_bid_ask_become_q1_book():
    book = gemini_contract_book({
        "prices": {
            "bestAsk": "0.42",
            "bestBid": "0.35",
            "buy": {"yes": "0.42", "no": "0.65"},
        }
    })
    assert book == TopBook(D("0.42"), D("1"), D("0.65"), D("1"))


def test_gemini_crossed_or_one_sided_best_quotes_fail_closed():
    assert gemini_contract_book({"prices": {"bestAsk": "0.40"}}) is None
    assert gemini_contract_book({"prices": {"bestAsk": "0.40", "bestBid": "0.41"}}) is None


def test_no_source_side_inverts_book_orientation():
    book = TopBook(D("0.42"), D("7"), D("0.61"), D("9"))
    inverted = orient_book(book, "NO")
    assert inverted == TopBook(D("0.61"), D("9"), D("0.42"), D("7"))


def test_gemini_priced_quote_is_fee_inclusive_and_q1_only():
    side = _side()
    book = TopBook(D("0.40"), D("50"), D("0.60"), D("50"))
    priced = make_priced_quote(side, book, received_ms=1234, depth_source="test")
    assert priced is not None
    assert priced.quote.yes_cost == D("0.42")
    assert priced.quote.no_cost == D("0.62")
    assert priced.quote.yes_depth == D("1")
    assert priced.quote.no_depth == D("1")
    assert priced.quote.settlement_confidence == "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"
