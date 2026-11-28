from decimal import Decimal as D
import pytest
from src.pmus_sports_line_fair import line_fair

def book(kind, names, points, stamp=1000):
    return {"markets": [{"key": kind, "last_update": stamp,
        "outcomes": [{"name": n, "point": p, "price": 2}
                     for n,p in zip(names, points)]}]}

def test_spread_exact():
    b = book("spreads", ["A","B"], [-5.5,5.5])
    assert line_fair(b, kind="spreads", selection="A", opponent="B", point=-5.5, now=1000) == D(".5")
    assert line_fair(b, kind="spreads", selection="A", opponent="B", point=-4.5, now=1000) is None

@pytest.mark.parametrize("point,now", [(220,1000),(220.5,100000),(220.5,999),(221.5,1000)])
def test_total_refusals(point,now):
    b = book("totals", ["Over","Under"], [220.5,220.5])
    assert line_fair(b, kind="totals", selection="Over", point=point, now=now) is None

def test_total_exact_and_duplicate():
    b = book("totals", ["Over","Under"], [220.5,220.5])
    args = dict(kind="totals", selection="Under", point=220.5, now=1000)
    assert line_fair(b, **args) == D(".5")
    b["markets"] *= 2
    assert line_fair(b, **args) is None
