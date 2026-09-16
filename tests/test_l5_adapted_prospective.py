"""The prospective gate must be able to FAIL, and A-3 must bite."""
import json
import pytest

from analysis.l5.adapted import collect as C
from analysis.l5.adapted import prospective as P


def _w(arm, **kw):
    d = {"arm": arm, "venue": "PMUS", "two_sided_evidenced": True,
         "settled": True, "matched_pnl": 10.0, "residual_pnl": -1.0,
         "total_pnl": 9.0, "fees": 0.0, "matched_qty": 100.0,
         "hindsight_fills": 0}
    d.update(kw)
    return d


def test_below_twenty_windows_is_insufficient_not_a_fail():
    s = P.score_arm([_w("cap8") for _ in range(19)], arm="cap8",
                    control_matched=1900.0)
    assert s["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert "A-1_windows_two_sided" in s["failed"]


def test_a_clean_run_passes():
    s = P.score_arm([_w("cap8") for _ in range(20)], arm="cap8",
                    control_matched=2000.0)
    assert s["verdict"] == "PASS", s["failed"]


def test_a3_fails_when_the_residual_eats_more_than_half_the_matched_edge():
    """The proposition the whole experiment exists to test: profitable is not
    enough if the residual still consumes the matched leg."""
    rows = [_w("cap8", matched_pnl=10.0, residual_pnl=-6.0, total_pnl=4.0)
            for _ in range(20)]
    s = P.score_arm(rows, arm="cap8", control_matched=2000.0)
    assert s["verdict"] == "FAIL"
    assert "A-3_residual_within_half_of_matched" in s["failed"]
    assert s["total_trading_pnl"] > 0        # profitable, and still fails


def test_a2_fails_on_negative_trading_pnl():
    rows = [_w("cap8", matched_pnl=1.0, residual_pnl=-5.0, total_pnl=-4.0)
            for _ in range(20)]
    s = P.score_arm(rows, arm="cap8", control_matched=2000.0)
    assert "A-2_settled_trading_pnl" in s["failed"]


def test_a4_fails_when_too_little_matched_capacity_survives():
    rows = [_w("cap5") for _ in range(20)]
    s = P.score_arm(rows, arm="cap5", control_matched=5000.0)  # 2000/5000
    assert "A-4_matched_retained_vs_control" in s["failed"]


def test_a5_fails_on_any_hindsight_fill():
    rows = [_w("cap8") for _ in range(20)]
    rows[0]["hindsight_fills"] = 1
    s = P.score_arm(rows, arm="cap8", control_matched=2000.0)
    assert "A-5_no_hindsight_fills" in s["failed"]


def test_incentives_are_never_in_qualification():
    s = P.score_arm([_w("cap8") for _ in range(20)], arm="cap8",
                    control_matched=2000.0)
    assert s["incentives"]["included_in_qualification"] is False
    assert "never netted" in s["incentives"]["note"]


def test_the_tightest_passing_cap_is_selected(tmp_path):
    rows = ([_w("cap5") for _ in range(20)] + [_w("cap8") for _ in range(20)]
            + [_w("control", matched_qty=100.0) for _ in range(20)])
    p = tmp_path / "adapted_windows.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    rep = P.report(str(tmp_path))
    assert rep["overall"] == "PASS"
    assert rep["selected"][1] == "cap5"      # bounded residual, not max P&L
    assert "preflight" in rep["next_step"]


def test_only_the_strict_criterion_counts_as_a_fill():
    assert C._strict({"evidenced_fill": True,
                      "fill_criteria": {"strongest_satisfied":
                                        "VOLUME_AHEAD_CLEARED"}})
    assert not C._strict({"evidenced_fill": True,
                          "fill_criteria": {"strongest_satisfied": "TOUCH"}})
    assert not C._strict({"evidenced_fill": True,
                          "fill_criteria": {"strongest_satisfied":
                                            "TRADE_THROUGH"}})
    assert not C._strict({"evidenced_fill": None})


def test_a_withheld_side_takes_no_fills(tmp_path):
    """cap5 must stop accumulating Up after five, while control keeps going."""
    rows = []
    for i in range(8):
        rows.append({"schema": "x/maker_outcome", "venue": "PMUS",
                     "market_key": "btc-updown-15m-1", "desired_outcome": "Up",
                     "evidenced_fill": True, "resolved_at": i,
                     "intent_id": f"i{i}", "quote_price": "0.5",
                     "filled_qty": "10",
                     "fill_criteria": {"strongest_satisfied":
                                       "VOLUME_AHEAD_CLEARED"}})
    (tmp_path / "maker_outcomes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows))
    got = {r["arm"]: r for r in C.build(str(tmp_path))}
    assert got["control"]["fills_taken"] == 8
    assert got["cap5"]["fills_taken"] == 5
    assert got["cap8"]["fills_taken"] == 8
    assert got["cap5"]["two_sided_evidenced"] is False   # one-sided window


# ------------------------------------------------- settlement must actually happen

def _fill_row(side, i, qty="10", px="0.45"):
    return {"schema": "x/maker_outcome", "venue": "PMUS",
            "market_key": "btc-updown-15m-1", "desired_outcome": side,
            "evidenced_fill": True, "resolved_at": i, "intent_id": f"i{i}",
            "quote_price": px, "filled_qty": qty,
            "fill_criteria": {"strongest_satisfied": "VOLUME_AHEAD_CLEARED"}}


def test_a_resolved_window_books_matched_and_residual(tmp_path, monkeypatch):
    """Without this the arms could never clear A-2/A-3/A-6 and the experiment
    would sit at INSUFFICIENT_EVIDENCE for ever."""
    rows = [_fill_row("Up", 0), _fill_row("Up", 1), _fill_row("Down", 2)]
    (tmp_path / "maker_outcomes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows))
    import src.l5_shadow.venues as V
    monkeypatch.setattr(V, "pm_settlement", lambda k: {
        "resolved": True, "winning_outcome": "Up",
        "resolution_source": "gamma_outcome_prices"})
    got = {r["arm"]: r for r in C.build(str(tmp_path))}
    c = got["control"]
    assert c["settled"] is True
    assert c["winning_outcome"] == "Up"
    # 10 matched pairs at 0.45+0.45, plus a 10-share Up residual that won
    assert c["matched_pnl"] == pytest.approx(10 * (1 - 0.90))
    assert c["residual_pnl"] == pytest.approx(10 * (1 - 0.45))
    assert c["total_pnl"] == pytest.approx(c["matched_pnl"] + c["residual_pnl"])


def test_an_unresolved_window_books_nothing(tmp_path, monkeypatch):
    (tmp_path / "maker_outcomes.jsonl").write_text(json.dumps(_fill_row("Up", 0)))
    import src.l5_shadow.venues as V
    monkeypatch.setattr(V, "pm_settlement", lambda k: {"resolved": False})
    got = {r["arm"]: r for r in C.build(str(tmp_path))}
    assert got["control"]["settled"] is False
    assert got["control"]["total_pnl"] is None


def test_a_losing_residual_is_booked_as_a_loss(tmp_path, monkeypatch):
    rows = [_fill_row("Up", 0), _fill_row("Up", 1), _fill_row("Down", 2)]
    (tmp_path / "maker_outcomes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows))
    import src.l5_shadow.venues as V
    monkeypatch.setattr(V, "pm_settlement", lambda k: {
        "resolved": True, "winning_outcome": "Down",
        "resolution_source": "gamma_outcome_prices"})
    c = {r["arm"]: r for r in C.build(str(tmp_path))}["control"]
    assert c["residual_pnl"] == pytest.approx(10 * (0 - 0.45))   # it lost
    assert c["matched_pnl"] == pytest.approx(10 * (1 - 0.90))    # unaffected
