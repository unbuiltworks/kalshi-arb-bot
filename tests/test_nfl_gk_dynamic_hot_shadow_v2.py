from decimal import Decimal

from src.gemini_kalshi_execution_shadow import TopOfBook
from src.nfl_gk_dynamic_hot_shadow import HotPair
from src.nfl_gk_dynamic_hot_shadow_v2 import any_q1_depth, diversify_pairs, game_bucket

D=Decimal


def hp(game, kickoff, family, idx):
    return HotPair(
        key=f"football|nfl|{game}|{family}|SUB{idx}|GT|{idx}.5",
        family=family,kickoff_ms=kickoff,
        gemini_symbol=f"g-{game}-{family}-{idx}",kalshi_ticker=f"k-{game}-{family}-{idx}",
        rule_status="CONDITIONAL",rule_blockers=("blocked",),
    )


def book(venue,yes_bid,yes_bid_size,yes_ask,yes_ask_size):
    return TopOfBook(venue,"m",1000,D(str(yes_bid)),D(str(yes_bid_size)),D(str(yes_ask)),D(str(yes_ask_size)))


def test_diversify_caps_one_game_and_reaches_other_games():
    rows=[]
    for i in range(20): rows.append(hp("HOU,LV",1000,"game_total",i))
    for i in range(6): rows.append(hp("BAL,IND",2000,("moneyline","spread","game_total")[i%3],i))
    chosen=diversify_pairs(rows,max_pairs=10,per_game_cap=4)
    counts={}
    for p in chosen: counts[game_bucket(p)]=counts.get(game_bucket(p),0)+1
    assert max(counts.values())<=4
    assert len(counts)==2


def test_diversify_interleaves_families_when_available():
    rows=[hp("A,B",1000,"game_total",0),hp("A,B",1000,"spread",1),hp("A,B",1000,"moneyline",2)]
    chosen=diversify_pairs(rows,max_pairs=3,per_game_cap=3)
    assert {p.family for p in chosen}=={"moneyline","spread","game_total"}


def test_q1_depth_is_independent_of_profitability():
    # Direction 1 has q1 displayed depth even if prices may not form an arb.
    g=book("gemini",.49,2,.51,2)
    k=book("kalshi",.49,2,.51,2)
    assert any_q1_depth(g,k)

    # Both possible complementary directions lose a needed top-of-book size.
    g2=book("gemini",.49,.5,.51,.5)
    k2=book("kalshi",.49,.5,.51,.5)
    assert not any_q1_depth(g2,k2)
