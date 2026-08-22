from decimal import Decimal

from src.gemini_combo_cross_venue_router import HedgeOption, choose_cheapest_certified
from src.gemini_combo_mm_core import (
    ComboLeg,
    HedgeDepth,
    combo_cover_value,
    make_plan,
    partial_fill_safe_price,
    reserve_candidates,
)

D=Decimal


def depth(q:int, price:str)->HedgeDepth:
    p=D(price); raw=p*D(q)
    # Test fixtures supply already-rounded aggregate fee.
    fee=D("0.01")
    return HedgeDepth(qty=q,raw_total=raw,fee_total=fee,all_in_total=raw+fee,worst_price=p,units_available=D("100"))


def leg(cid:str,event:str,required:str="No",comp:str="YES",price:str="0.02")->ComboLeg:
    return ComboLeg(
        event_ticker=event,physical_event=event.lower(),title=event,symbol=f"SYM-{cid}",contract_id=cid,
        label=cid,required_outcome=required,complement_outcome=comp,selected_mid=D("0.985"),expiry_ts=2_000_000_000.0,
        terms_url="terms",hedge_depth={1:depth(1,price),2:depth(2,price),3:depth(3,price)},
    )


def test_combo_cover_identity_representative_two_to_six_legs():
    vectors=[D("0"),D("0.1"),D("0.5"),D("0.9"),D("1")]
    for n in range(2,7):
        cases=[vectors[:n] if n<=len(vectors) else [D("0.5")]*n,[D("1")]*n,[D("0")]*n,[D("0.9")]*n]
        for values in cases:
            assert combo_cover_value(values) >= D("1")


def test_partial_fill_safe_price_uses_worst_fill_quantity():
    # q1 fees make q1 less favorable than q3. A q3 live quote cannot use only q3 economics.
    price,by_q=partial_fill_safe_price(max_qty=3,hedge_total_by_fill_qty={1:D("0.06"),2:D("0.10"),3:D("0.14")})
    assert price is not None
    assert price == min(x for x in by_q.values() if x is not None)
    assert price <= by_q[3]


def test_same_physical_event_is_rejected_before_combo_create():
    a=leg("1","same-event")
    b=ComboLeg(**{**a.__dict__,"contract_id":"2","symbol":"SYM-2"})
    assert make_plan(legs=[a,b],max_qty=1,now_ts=1_900_000_000.0) is None


def test_plan_supports_two_to_six_unique_events():
    for n in range(2,7):
        rows=[leg(str(i),f"event-{i}") for i in range(n)]
        plan=make_plan(legs=rows,max_qty=1,now_ts=1_900_000_000.0)
        assert plan is not None
        assert plan.leg_count==n
        assert plan.partial_fill_safe_price is not None
        assert plan.locked_per_contract is not None
        assert plan.locked_per_contract >= D("0.03")


def test_capital_reservation_never_overcommits_and_caps_five():
    plans=[]
    for i in range(8):
        p=make_plan(legs=[leg(f"{i}a",f"e-{i}-a"),leg(f"{i}b",f"e-{i}-b")],max_qty=1,now_ts=1_900_000_000.0)
        assert p is not None
        plans.append(p)
    chosen,remaining=reserve_candidates(plans,available_capital=D("5"),max_resting=5)
    assert len(chosen)<=5
    assert remaining>=0
    assert sum((p.capital_reserve for p in chosen),D("0"))<=D("5")


def test_cross_venue_router_ignores_cheaper_uncertified_option():
    cheap=HedgeOption("kalshi","K","YES",D("0.01"),D("100"),D("1"),True,False)
    safe=HedgeOption("gemini","G","YES",D("0.03"),D("100"),D("1"),True,True)
    selected,rows=choose_cheapest_certified([cheap,safe])
    assert selected is safe
    assert any(r["venue"]=="kalshi" and not r["execution_eligible"] for r in rows)
