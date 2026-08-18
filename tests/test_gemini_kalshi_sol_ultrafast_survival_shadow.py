import asyncio

from src import gemini_kalshi_sol_ultrafast_survival_shadow as fast


def test_fast_delay_schedule_extends_existing_matrix():
    assert fast.FAST_DELAYS_MS == (10, 15, 25, 50, 100)


def test_probe_passes_explicit_fast_delays(monkeypatch):
    seen = {}

    async def fake_probe_matrix(*, feed, plan, active_episodes, delays_ms):
        seen["feed"] = feed
        seen["plan"] = plan
        seen["active_episodes"] = active_episodes
        seen["delays_ms"] = delays_ms

    monkeypatch.setattr(fast.matrix, "_probe_matrix", fake_probe_matrix)
    asyncio.run(fast._probe_ultrafast(feed="f", plan="p", active_episodes={"x": 1}))
    assert seen == {
        "feed": "f",
        "plan": "p",
        "active_episodes": {"x": 1},
        "delays_ms": (10, 15, 25, 50, 100),
    }
