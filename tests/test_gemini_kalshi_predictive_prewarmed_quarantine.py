import io
from types import SimpleNamespace

from src import gemini_kalshi_predictive_prewarmed_quarantine as quarantine


def test_startup_quarantine_updates_book_state_without_running_economics(monkeypatch):
    feed = SimpleNamespace(
        evaluation_eligible_ms=2000,
        stats=SimpleNamespace(),
        asset="BTC",
        pair=SimpleNamespace(),
    )
    called = []
    monkeypatch.setattr(quarantine, "_original_evaluate", lambda **kwargs: called.append(kwargs))

    g = SimpleNamespace(ts_ms=1000)
    quarantine._evaluate_event_quarantined(
        feed=feed,
        event=("gemini", 1000, None, g, 1),
        kalshi_fee=None,
        max_quote_skew_ms=100,
        capital_limit=5,
        min_profit=0.03,
        active_episodes={},
        fh=io.StringIO(),
    )

    assert feed.eval_gemini is g
    assert called == []


def test_event_after_quarantine_delegates_to_sequential_evaluator(monkeypatch):
    feed = SimpleNamespace(evaluation_eligible_ms=2000)
    called = []
    monkeypatch.setattr(quarantine, "_original_evaluate", lambda **kwargs: called.append(kwargs))
    kwargs = {
        "feed": feed,
        "event": ("gemini", 2001, None, SimpleNamespace(ts_ms=2001), 1),
        "kalshi_fee": None,
        "max_quote_skew_ms": 100,
        "capital_limit": 5,
        "min_profit": 0.03,
        "active_episodes": {},
        "fh": io.StringIO(),
    }
    quarantine._evaluate_event_quarantined(**kwargs)
    assert len(called) == 1
