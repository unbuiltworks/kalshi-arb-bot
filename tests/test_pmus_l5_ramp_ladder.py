"""OWNER 5554127702 #4: the prebuilt ramp ladder is inert (R1 by default),
states every rung's worst case, and turns venue-confirmed evidence into
PROMOTE / HOLD / ROLLBACK with reasons -- never promoting on missing data."""
from __future__ import annotations

from src import pmus_l5_ramp_ladder as ld


def test_default_rung_is_the_authorized_one_and_unknown_falls_back(monkeypatch):
    monkeypatch.delenv(ld.RUNG_ENV, raising=False)
    assert ld.rung()["id"] == "R1"
    monkeypatch.setenv(ld.RUNG_ENV, "R9")
    assert ld.rung()["id"] == "R1"
    monkeypatch.setenv(ld.RUNG_ENV, "r3")
    assert ld.rung()["id"] == "R3" and ld.rung()["max_actions_per_session"] == 12


def test_worst_cases_are_explicit_and_bounded_by_the_daily_stop():
    wc = {r["id"]: r["worst_case"] for r in ld.ladder()}
    assert wc["R1"]["per_campaign_usd"] == "0.15"
    assert wc["R2"]["per_campaign_usd"] == "0.30" and wc["R2"]["per_day_uncapped_usd"] == "0.60"
    assert wc["R3"]["per_campaign_usd"] == "0.30"
    assert wc["R4"]["per_campaign_usd"] == "0.60"
    assert wc["R4"]["per_day_uncapped_usd"] == "1.20"
    # the stop is checked before every session, so the bound is the stop
    # plus the one in-flight market's cap (OWNER 5561465636 honest bound)
    assert wc["R4"]["per_day_bounded_by_daily_stop_usd"] == "1.10"
    assert wc["R1"]["per_day_bounded_by_daily_stop_usd"] == "0.15"
    assert wc["R2C"]["per_day_bounded_by_daily_stop_usd"] == "0.90"
    assert wc["R5"]["max_concurrent_open_exposure_usd"] == "0.20"
    # the order of levers is L5's: windows/actions first, size, concurrency
    assert [r["lever"] for r in ld.RUNGS] == [
        "baseline", "windows_per_day", "actions_per_window", "size", "concurrency"]


def test_evaluate_fails_closed_promotes_on_evidence_and_rolls_back_on_breach():
    hold = ld.evaluate("R1", {})
    assert hold["verdict"] == "HOLD" and any("settled_clean 0" in r for r in hold["reasons"])
    good = {"settled_markets_clean": 3, "fill_rate": 0.3, "shadow_mix_l1": 0.1,
            "matched_pairs": 1, "pair_cost_max": "0.97",
            "adverse_selection_c": 1.5, "max_drawdown_usd": "-0.02",
            "realized_usd": "0.01", "mismatches": 0}
    ok = ld.evaluate("R1", good)
    assert ok["verdict"] == "PROMOTE" and ok["next_rung"] == "R2"
    assert ok["next_worst_case"]["per_campaign_usd"] == "0.30"
    # missing adverse-selection measurement never promotes
    assert ld.evaluate("R1", {**good, "adverse_selection_c": None})["verdict"] == "HOLD"
    assert ld.evaluate("R1", {**good, "mismatches": 1})["verdict"] == "ROLLBACK"
    assert ld.evaluate("R1", {**good, "realized_usd": "-0.08"})["verdict"] == "ROLLBACK"
    assert ld.evaluate("R5", good)["verdict"] == "HOLD"     # top of ladder


def test_evidence_is_read_from_the_campaign_report():
    rep = {"sessions": [
        {"orders": [{"outcome": "filled"}, {"outcome": "no_fill_cancelled"}],
         "decisions": [{"acted": "open", "shadow": {"stochastic": {"sampled": "open"}}},
                       {"acted": "extend", "shadow": {"stochastic": {"sampled": "complete"}}}]}],
        "settlements": [{"status": "REALIZED", "realized_usd": "0.01",
                         "breakdown": {"matched_pairs": "0.02",
                                       "matched_pair_cost": "0.95"}}],
        "advance_gate": {"inventory_mismatches": 0, "orphan_cancels": 0, "halts": 0},
        "realized_pnl": "0.01"}
    ev = ld.evidence_from_campaign(rep)
    assert ev["settled_markets_clean"] == 1 and ev["fill_rate"] == 0.5
    assert ev["matched_pairs"] == 0.02 and ev["pair_cost_max"] == "0.95"
    assert abs(ev["shadow_mix_l1"] - 1.0) < 1e-9        # open/extend vs open/complete
    assert ev["adverse_selection_c"] is None and ev["mismatches"] == 0
