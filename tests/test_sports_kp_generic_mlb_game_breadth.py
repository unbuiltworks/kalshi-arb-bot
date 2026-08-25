from decimal import Decimal
from types import SimpleNamespace

from src.mlb_game_side_normalization import NormalizedSide
from src.sports_kp_generic_mlb_game_breadth import Pair, _orient, _pair_normal_event_sides, _q1_economics
from src.universal_sports_identity import RuleAudit, SportsContractIdentity, SportsEventIdentity

D = Decimal


def _side(venue: str, market: str, side: str, *, subject: str = "bos", start_ms: int = 1_700_000_000_000) -> NormalizedSide:
    return NormalizedSide(
        identity=SportsContractIdentity(
            venue=venue,
            market_id=f"{market}#{side}",
            event=SportsEventIdentity(
                sport="baseball",
                league="mlb",
                participants=("bos", "mia"),
                scheduled_start_ms=start_ms,
            ),
            market_family="moneyline",
            scope="full_contest",
            subject_type="team",
            subject=subject,
            stat="",
            comparator="YES",
            line=None,
            rule_audit=RuleAudit(
                official_source=f"{venue}_rules_unverified",
                overtime="unverified",
                postponement="unverified",
                stat_corrections="unverified",
                push_tie="unverified",
                complete=False,
            ),
        ),
        venue_market_id=market,
        side=side,
        source_label=market,
    )


def test_orient_no_swaps_binary_contract_sides() -> None:
    quote = SimpleNamespace(yes_ask=D("0.44"), yes_depth=D("10"), no_ask=D("0.58"), no_depth=D("20"))
    book = _orient(quote, "NO")
    assert book is not None
    assert book.prop_ask == D("0.58")
    assert book.prop_depth == D("20")
    assert book.opp_ask == D("0.44")
    assert book.opp_depth == D("10")


def test_pairing_collapses_one_yes_plus_one_no_mirror() -> None:
    p_yes = _side("polymarket", "p_yes", "YES")
    p_no = _side("polymarket", "p_complement", "NO")
    k_yes = _side("kalshi", "k_yes", "YES")
    k_no = _side("kalshi", "k_complement", "NO")
    pairs, ambiguous = _pair_normal_event_sides([p_yes, p_no], [k_yes, k_no])
    assert ambiguous == 0
    assert len(pairs) == 1
    assert pairs[0].poly.venue_market_id == "p_yes"
    assert pairs[0].kalshi.venue_market_id == "k_yes"


def test_pairing_rejects_multiple_yes_normal_event_mapping() -> None:
    p = _side("polymarket", "p1", "YES")
    k1 = _side("kalshi", "k1", "YES")
    k2 = _side("kalshi", "k2", "YES")
    pairs, ambiguous = _pair_normal_event_sides([p], [k1, k2])
    assert pairs == []
    assert ambiguous == 1


def test_q1_economics_uses_real_fee_rounding_and_depth() -> None:
    pair = Pair(
        poly=_side("polymarket", "p1", "YES"),
        kalshi=_side("kalshi", "k1", "YES"),
        start_skew_ms=0,
    )
    poly = SimpleNamespace(yes_ask=D("0.45"), yes_depth=D("8"), no_ask=D("0.57"), no_depth=D("9"))
    kalshi = SimpleNamespace(yes_ask=D("0.46"), yes_depth=D("6"), no_ask=D("0.56"), no_depth=D("7"))
    row = _q1_economics(pair, poly, kalshi, poly_rate=D("0.05"), kalshi_rate=D("0.07"))
    assert row is not None
    assert row["direction"] == "POLY_PROP_KALSHI_OPP"
    assert row["profit"] == D("-0.04")
    assert row["q1_depth"] == D("7")
    assert row["missing_to_3c"] == D("0.07")
