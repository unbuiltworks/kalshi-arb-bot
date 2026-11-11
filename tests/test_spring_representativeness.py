"""Strict-spring representativeness study regressions (issue #167
comment 5532993594): band denominators, pair-cost CDF, no-hindsight gate
replay, relation classification, and read-only purity."""
from __future__ import annotations

from pathlib import Path

from src.polymarket_l5 import spring_representativeness as rep


def _row(wid, seq, t, outcome, side, px, qty, cum_up, cum_dn,
         matched="0", pair="", tte="200"):
    return {"window_id": wid, "trade_seq": str(seq), "timestamp": str(t),
            "market_slug": f"m-{wid}", "asset": "BTC",
            "duration_minutes": "5", "outcome": outcome, "side": side,
            "price": str(px), "quantity": str(qty),
            "dollars": str(round(float(px) * float(qty), 6)),
            "seconds_to_settlement": tte,
            "cum_up_qty": str(cum_up), "cum_down_qty": str(cum_dn),
            "matched_qty": matched, "matched_pair_unit_cost": pair}


def _fixture():
    rows = []
    # W1: opens IN band at 0.50, completes at pair 0.95 60s later
    rows += [
        _row("w1", 1, 1000, "Up", "BUY", "0.50", "1", "1", "0"),
        _row("w1", 2, 1060, "Down", "BUY", "0.45", "1", "1", "1",
             matched="1", pair="0.95"),
    ]
    # W2: opens OUT of the strict band at 0.70 (inside 25-75), completes
    # cheaply at 0.92 — no-hindsight: must NOT count for the 45-55 gate
    # despite the attractive pair
    rows += [
        _row("w2", 1, 2000, "Up", "BUY", "0.70", "1", "1", "0"),
        _row("w2", 2, 2030, "Down", "BUY", "0.22", "1", "1", "1",
             matched="1", pair="0.92"),
    ]
    # W3: opens IN band, EXTENDs (same side), never completes
    rows += [
        _row("w3", 1, 3000, "Up", "BUY", "0.52", "1", "1", "0"),
        _row("w3", 2, 3040, "Up", "BUY", "0.53", "1", "2", "0"),
    ]
    return rows


def test_band_denominators_and_gate_replay_no_hindsight():
    r = rep.compute(_fixture())
    d = r["denominators"]
    assert d["windows_with_any_buy"] == 3
    assert d["buy_actions"] == 6
    b = r["entry_price_bands"]["45-55"]
    assert b["window_opens_in_band"] == 2          # w1 + w3, never w2
    assert b["window_opens_frac"] == round(2 / 3, 4)
    cur = r["current_strict_gate_replay_45_55_le096"]
    assert cur["eligible_windows"] == 2
    assert cur["completed_windows"] == 1           # only w1; w2 excluded
    assert cur["completion_rate_of_eligible"] == 0.5


def test_pair_cost_cdf_and_latency():
    r = rep.compute(_fixture())
    allb = r["pair_cost_and_latency_by_stream"]["ALL"]
    assert allb["matched_windows"] == 2
    assert allb["pair_cost_cdf"]["le_0.94"] == 0.5   # 0.92 only
    assert allb["pair_cost_cdf"]["le_0.96"] == 1.0   # 0.92 + 0.95
    lat = allb["completion_latency"]
    assert lat["n"] == 2 and lat["gt_30s"] == 0.5    # 60s and 30s


def test_relation_classification_extend_vs_complete():
    r = rep.compute(_fixture())
    post = r["post_first_side_behavior"]["by_price_band"]["25-75"]
    # w1's second buy = complete (0.45 in band); w3's = extend (0.53)
    assert post["n_actions"] == 2
    assert post["extend_frac"] == 0.5
    assert post["complete_frac"] == 0.5


def test_grid_contains_current_definition_and_wider_cells():
    r = rep.compute(_fixture())
    g = r["evidence_grid"]
    assert g["45-55|le_0.96"]["completed_windows"] == 1
    # widening the band admits w2's cheap completion
    assert g["25-75|le_0.96"]["completed_windows"] == 2
    assert "35-65|le_0.98" in g


def test_study_is_read_only_research():
    src = Path(rep.__file__).read_text()
    for banned in ("urllib", "websockets", "requests", "Transport(",
                   "/v1/orders", "POST", "setenv"):
        assert banned not in src, banned
    assert "READ-ONLY research" in src
