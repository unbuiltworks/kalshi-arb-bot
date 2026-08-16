from decimal import Decimal
from types import SimpleNamespace

from src import gemini_kalshi_hot_persistence_probe as probe


def _candidate():
    return SimpleNamespace(
        direction="KALSHI_YES_GEMINI_NO",
        locked_profit=Decimal("0.12"),
        net_cost=Decimal("4.88"),
        fees=Decimal("0.05"),
        roi_on_cost=Decimal("0.02459"),
        quote_skew_ms=3,
    )


def test_persistence_logs_each_crossed_milestone_once(monkeypatch):
    active = {}
    candidate = _candidate()
    pair = SimpleNamespace(gemini_symbol="g", kalshi_ticker="k")

    def fake_original_update(**kwargs):
        ep = kwargs["active"].get(candidate.direction)
        if ep is None:
            ep = SimpleNamespace(
                start_ms=1000,
                events=1,
                best_profit=candidate.locked_profit,
                best_capital=candidate.net_cost,
                best_q=5,
            )
            kwargs["active"][candidate.direction] = ep
        else:
            ep.events += 1

    logs = []
    monkeypatch.setattr(probe, "_original_update", fake_original_update)
    monkeypatch.setattr(probe.hot.logger, "info", lambda *args: logs.append(args))

    common = dict(
        active=active,
        qualifying={candidate.direction: (5, candidate)},
        asset="XRP",
        pair=pair,
        trigger_venue="gemini",
    )
    probe._update_episodes_with_persistence(now_ms=1026, **common)
    probe._update_episodes_with_persistence(now_ms=1030, **common)
    probe._update_episodes_with_persistence(now_ms=1051, **common)

    milestone_logs = [args for args in logs if args and args[0] == "CRYPTO_HOT_PERSISTENCE_MILESTONE %s"]
    assert len(milestone_logs) == 3
    assert getattr(active[candidate.direction], "persistence_milestones_logged") == {10, 25, 50}
