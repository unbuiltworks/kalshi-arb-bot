# Import applies the deep-review category aliases to the base discovery module.
from src import category_overlap_review  # noqa: F401
from src import universe_overlap_audit as base


def test_non_sports_category_aliases():
    assert base._compatible("climate", "climate and weather")
    assert base._compatible("technology", "science and technology")
    assert base._compatible("science", "science and technology")
    assert base._compatible("finance", "financials")
    assert base._compatible("politics", "elections")
