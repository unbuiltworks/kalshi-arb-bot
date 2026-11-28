import pytest
from src.pmus_sports_sharp import _h2h

@pytest.mark.parametrize("names", [("Team A", "team-a"), ("", "Team B"), (None, "Team B")])
def test_ambiguous_outcomes_refused(names):
    book = {"markets": [{"key": "h2h", "last_update": 1000,
                         "outcomes": [{"name": n, "price": 2} for n in names]}]}
    assert _h2h(book) is None

def test_distinct_outcomes_accepted():
    book = {"markets": [{"key": "h2h", "last_update": 1000,
                         "outcomes": [{"name": n, "price": 2} for n in ("Team A", "Team B")]}]}
    probs, timestamp = _h2h(book)
    assert set(probs) == {"team a", "team b"}
    assert sum(probs.values()) == 1
    assert timestamp == 1000
