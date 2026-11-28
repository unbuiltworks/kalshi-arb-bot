from src import pmus_sports_sharp as SH

def book(key, ts):
    return {"key": key, "last_update": ts, "markets": [
        {"key": "h2h", "outcomes": [
            {"name": "A", "price": 2}, {"name": "B", "price": 2}]}]}

def test_future_odds_refused():
    for key in ("pinnacle", "other"):
        assert SH.fair_from_event({"bookmakers": [book(key, 1001)]}, now=1000) is None

def test_consensus_age_is_oldest():
    result = SH.fair_from_event({"bookmakers": [
        book("one", 950), book("two", 990)]}, now=1000)
    assert result["age_s"] == 50

def test_cache_refresh_follows_poll_until_quota_is_reported(monkeypatch):
    """Polling is unchanged (issue #251: report coverage and quota first).
    A cache entry younger than poll_s is reused; stale ODDS inside it are
    still refused by fair_from_event (fail closed)."""
    monkeypatch.setattr(SH, "MAX_ODDS_AGE_S", 900)
    calls = []

    def opener(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("test")
    client = SH.OddsClient(key="test", poll_s=3600, opener=opener,
                           clock=lambda: 1000)
    client.cache["sport"] = (500, [])
    client.fetch("sport")
    assert len(calls) == 0
    client.clock = lambda: 500 + 3601
    client.fetch("sport")
    assert len(calls) == 1
