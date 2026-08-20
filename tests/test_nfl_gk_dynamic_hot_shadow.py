from decimal import Decimal

from src.nfl_gk_dynamic_hot_shadow import discover_pairs
from src.nfl_identity_probe import NflIdentity

D=Decimal


def row(venue,market,start,key_subject="PHI",family="moneyline",line=None):
    if line is not None and not isinstance(line,Decimal):
        line=D(str(line))
    return NflIdentity(
        venue=venue,market_id=market,family=family,teams=("DAL","PHI"),
        subject=key_subject,comparator="YES" if family=="moneyline" else "GT",
        line=line,start_ms=start,start_provenance="test",
    )


def test_exact_future_gk_pair_is_selected_and_rule_blocked():
    now=1_000_000
    g=row("gemini","g1",now+100_000)
    k=row("kalshi","k1",now+100_000)
    pairs,diag=discover_pairs([g],[k],max_pairs=10,now_ms=now)
    assert len(pairs)==1
    assert pairs[0].gemini_symbol=="g1"
    assert pairs[0].kalshi_ticker=="k1"
    assert pairs[0].rule_status=="CONDITIONAL"
    assert pairs[0].rule_blockers
    assert diag["selected"]==1


def test_kickoff_outlier_is_rejected():
    now=1_000_000
    g=row("gemini","g1",now+100_000)
    k=row("kalshi","k1",now+500_001)
    pairs,diag=discover_pairs([g],[k],max_pairs=10,now_ms=now,max_start_skew_ms=300_000)
    assert pairs==[]
    assert diag["start_skew_blocked"]==1


def test_ambiguous_multiple_markets_for_same_venue_are_rejected():
    now=1_000_000
    g1=row("gemini","g1",now+100_000)
    g2=row("gemini","g2",now+100_000)
    k=row("kalshi","k1",now+100_000)
    pairs,diag=discover_pairs([g1,g2],[k],max_pairs=10,now_ms=now)
    assert pairs==[]
    assert diag["ambiguous"]==1


def test_past_game_is_rejected_and_nearest_future_pairs_win_limit():
    now=1_000_000
    past_g=row("gemini","gp",now-1)
    past_k=row("kalshi","kp",now-1)
    future=[]
    for i,start in enumerate((now+300_000,now+100_000,now+200_000)):
        # Different subject ensures distinct proposition keys.
        subject=("PHI","DAL","PHI")[i]
        family="moneyline" if i<2 else "spread"
        line=None if family=="moneyline" else D("3.5")
        future.append((row("gemini",f"g{i}",start,subject,family,line),row("kalshi",f"k{i}",start,subject,family,line)))
    gs=[past_g]+[x[0] for x in future]
    ks=[past_k]+[x[1] for x in future]
    pairs,diag=discover_pairs(gs,ks,max_pairs=2,now_ms=now)
    assert diag["past_blocked"]==1
    assert len(pairs)==2
    assert [p.kickoff_ms for p in pairs]==sorted(p.kickoff_ms for p in pairs)
