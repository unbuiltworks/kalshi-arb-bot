from decimal import Decimal
from types import SimpleNamespace

from src import gemini_kalshi_preprobe_attribution as attribution

D = Decimal


def _snapshot(*, margin="12", target="100", fresh=True, same_side=True, basis2=True):
    return {
        "kalshi_target": target,
        "proxy_fresh_1000ms": fresh,
        "same_side_of_both_boundaries": same_side,
        "min_abs_double_boundary_margin_bps": margin,
        "research_proxy_gate_2bp_fresh_1000ms": basis2,
    }


def _reset_counters():
    for key in attribution.COUNTERS:
        attribution.COUNTERS[key] = 0


def test_classify_clean_q1_entry_passes_10bp():
    out = attribution.classify_entry(
        selected_q=1,
        selected_profit=D("0.04"),
        q1_profit=D("0.04"),
        min_profit=D("0.03"),
        snapshot=_snapshot(margin="12"),
        min_boundary_bps=D("10"),
    )
    assert out["selected_q1"] is True
    assert out["q1_qualifies"] is True
    assert out["entry_boundary_pass"] is True
    assert out["revalidation_probe_scheduled"] is True
    assert out["q1_and_entry_boundary_pass"] is True
    assert "ENTRY_BOUNDARY_PASS" in out["reasons"]


def test_classify_5_to_10bp_is_boundary_below_core_not_missing_context():
    out = attribution.classify_entry(
        selected_q=1,
        selected_profit=D("0.05"),
        q1_profit=D("0.05"),
        min_profit=D("0.03"),
        snapshot=_snapshot(margin="7.5"),
        min_boundary_bps=D("10"),
    )
    assert out["entry_boundary_pass"] is False
    assert out["revalidation_probe_scheduled"] is True
    assert "BOUNDARY_BELOW_MIN" in out["reasons"]
    assert "NO_BASIS_CONTEXT" not in out["reasons"]


def test_classify_q1_can_qualify_even_when_best_total_profit_q_is_larger():
    out = attribution.classify_entry(
        selected_q=5,
        selected_profit=D("0.20"),
        q1_profit=D("0.04"),
        min_profit=D("0.03"),
        snapshot=_snapshot(margin="15"),
        min_boundary_bps=D("10"),
    )
    assert out["selected_q1"] is False
    assert out["q1_qualifies"] is True
    assert out["q1_and_entry_boundary_pass"] is False
    assert "BEST_TOTAL_PROFIT_Q_GT1" in out["reasons"]
    assert "Q1_QUALIFIES_BUT_BEST_Q_GT1" in out["reasons"]


def test_classify_missing_target_and_stale_or_wrong_side_fail_closed():
    out = attribution.classify_entry(
        selected_q=1,
        selected_profit=D("0.03"),
        q1_profit=D("0.03"),
        min_profit=D("0.03"),
        snapshot=_snapshot(margin=None, target=None, fresh=False, same_side=False, basis2=False),
        min_boundary_bps=D("10"),
    )
    assert out["entry_boundary_pass"] is False
    assert out["revalidation_probe_scheduled"] is False
    assert set([
        "NO_KALSHI_TARGET",
        "STALE_BASIS_PROXY",
        "BOUNDARY_SIDE_MISMATCH",
        "BOUNDARY_MARGIN_UNAVAILABLE",
    ]).issubset(out["reasons"])


def test_episode_wrapper_counts_one_new_episode_then_repeats(monkeypatch):
    _reset_counters()
    direction = "GEMINI_YES_KALSHI_NO"
    candidate = SimpleNamespace(
        direction=direction,
        locked_profit=D("0.04"),
        net_cost=D("0.96"),
        fees=D("0.01"),
        quote_skew_ms=20,
    )
    pair = SimpleNamespace(gemini_symbol="GEMI-BTC", kalshi_ticker="KXBTC")
    feed = SimpleNamespace(
        asset="BTC",
        boundary_ms=1000,
        pair=pair,
        eval_gemini=None,
        eval_kalshi=None,
    )
    active = {}

    def fake_real_evaluator(**kwargs):
        kwargs["feed"].eval_gemini = object()
        kwargs["feed"].eval_kalshi = object()
        kwargs["active_episodes"].setdefault(direction, SimpleNamespace(start_ms=1100))

    monkeypatch.setattr(attribution, "_ORIGINAL_EVALUATE", fake_real_evaluator)
    monkeypatch.setattr(
        attribution.hot,
        "_small_q_candidates",
        lambda *args, **kwargs: [(1, candidate)],
    )
    monkeypatch.setattr(attribution.basis, "basis_snapshot", lambda *args: _snapshot(margin="12"))
    monkeypatch.setattr(attribution.readiness, "MIN_DOUBLE_BOUNDARY_BPS", D("10"))

    kwargs = dict(
        feed=feed,
        event=("gemini", 1100, None, object(), 0),
        kalshi_fee=object(),
        max_quote_skew_ms=50,
        capital_limit=D("1"),
        min_profit=D("0.03"),
        active_episodes=active,
        fh=None,
    )
    attribution._evaluate_with_attribution(**kwargs)
    attribution._evaluate_with_attribution(**kwargs)

    assert attribution.COUNTERS["threshold_direction_events"] == 2
    assert attribution.COUNTERS["new_episodes"] == 1
    assert attribution.COUNTERS["existing_episode_repeats"] == 1
    assert attribution.COUNTERS["selected_q1"] == 1
    assert attribution.COUNTERS["entry_boundary_pass"] == 1
    assert attribution.COUNTERS["q1_and_entry_boundary_pass"] == 1


def test_install_changes_only_exact_evaluator_reference(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(attribution, "_INSTALLED", False)
    monkeypatch.setattr(attribution.exact, "_evaluate_with_revalidation", sentinel)
    before_threshold = attribution.readiness.MIN_DOUBLE_BOUNDARY_BPS

    attribution.install()

    assert attribution.exact._evaluate_with_revalidation is attribution._evaluate_with_attribution
    assert attribution.readiness.MIN_DOUBLE_BOUNDARY_BPS == before_threshold
