import pytest
from src.pmus_sports_sharp import devig

@pytest.mark.parametrize("bad", ["NaN", "sNaN", "Infinity", "-Infinity", "0", "1", "-2", None])
def test_invalid_odds_fail_closed(bad):
    assert devig([bad, "2.0"]) is None
    assert devig(["2.0", bad]) is None

def test_valid_odds_still_normalize():
    p = devig(["2.0", "2.0"])
    assert p is not None
    assert p[0] == p[1]
    assert sum(p) == 1
