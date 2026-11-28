from decimal import Decimal as D
import pytest
from src.pmus_sports_line_fair import prop_fair

def fixture():
    return {"markets":[{"key":"player_points","last_update":1000,
        "outcomes":[{"name":s,"description":p,"point":20.5,"price":2}
                    for p in ("Player A","Player B") for s in ("Over","Under")]}]}

def test_exact_prop():
    assert prop_fair(fixture(),market_key="player_points",player="Player A",
                     selection="Over",point=20.5,now=1000)==D(".5")

@pytest.mark.parametrize("change", [
    {"player":"Player C"}, {"market_key":"player_rebounds"},
    {"point":21.5}, {"point":20}, {"now":999}, {"now":100000}])
def test_refuse_mismatch(change):
    args=dict(market_key="player_points",player="Player A",
              selection="Over",point=20.5,now=1000)
    args.update(change)
    assert prop_fair(fixture(),**args) is None

def test_duplicate_refused():
    b=fixture()
    b["markets"]*=2
    assert prop_fair(b,market_key="player_points",player="Player A",
                     selection="Over",point=20.5,now=1000) is None
