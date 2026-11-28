from decimal import Decimal
import pytest
from src.pmus_sports_soccer_fair import soccer_fair

def book():
    return {"markets":[{"key":"h2h","last_update":1000,"outcomes":[
        {"name":"Away FC","price":4},{"name":"Draw","price":4},
        {"name":"Home FC","price":2}]}]}

def fair(b, selection="home", now=1000):
    return soccer_fair(b,home="Home FC",away="Away FC",selection=selection,now=now)

@pytest.mark.parametrize("side,expected",[("home","0.5"),("away","0.25"),("draw","0.25")])
def test_three_way(side,expected):
    assert fair(book(),side)==Decimal(expected)

@pytest.mark.parametrize("now",[999,100000])
def test_invalid_age(now):
    assert fair(book(),now=now) is None

def test_missing_draw():
    b=book()
    b["markets"][0]["outcomes"].pop(1)
    assert fair(b) is None

def test_wrong_team():
    b=book()
    b["markets"][0]["outcomes"][0]["name"]="Other FC"
    assert fair(b) is None

def test_duplicate_market():
    b=book()
    b["markets"].append(b["markets"][0])
    assert fair(b) is None

def test_nonfinite():
    b=book()
    b["markets"][0]["outcomes"][0]["price"]="NaN"
    assert fair(b) is None
