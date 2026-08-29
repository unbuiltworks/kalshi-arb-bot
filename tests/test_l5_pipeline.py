"""End-to-end: raw JSONL cache -> CSVs -> analysis -> findings document.

Runs entirely offline against the synthetic cache built here, which is also a
check that `build` and `analyze` never reach for the network.
"""

import csv
import json
import random

import pytest

from src.l5 import config, pipeline
from src.l5.http import RawCache
from tests import l5_fixtures as fx


def _build_cache(directory) -> dict:
    """A synthetic history: 24 BTC/ETH windows across both window lengths."""
    cache = RawCache(str(directory))
    rng = random.Random(11)

    markets: list[dict] = []
    activity: list[dict] = []
    positions: list[dict] = []

    base = fx.BTC5M_START
    for i in range(24):
        asset = "bitcoin" if i % 2 == 0 else "ethereum"
        minutes = 5 if i % 3 else 15
        start = base + i * 3600
        end = start + minutes * 60
        condition_id = f"0xw{i:03d}"
        markets.append(
            fx.market(
                condition_id,
                f"{asset}-up-or-down-{minutes}m-{i}",
                f"{asset.title()} Up or Down - {minutes} minute - slot {i}",
                start,
                end,
                market_id=str(1000 + i),
            )
        )

        # Open on the cheap side, cross to the other side, then top up.
        cheap_price = round(rng.uniform(0.30, 0.48), 2)
        first_side, other_side = ("Up", "Down") if i % 2 == 0 else ("Down", "Up")
        activity.append(
            fx.trade(condition_id, first_side, "BUY", cheap_price, 100,
                     start + 20, f"0x{i:03d}a", usdc=round(cheap_price * 100, 4))
        )
        cross_price = round(1.0 - cheap_price - 0.03, 2)
        activity.append(
            fx.trade(condition_id, other_side, "BUY", cross_price, 100,
                     start + 20 + rng.randint(5, 60), f"0x{i:03d}b",
                     usdc=round(cross_price * 100, 4))
        )
        # Late escalation into a higher price band, larger clip.
        late_price = round(rng.uniform(0.72, 0.95), 2)
        late_side = first_side if i % 4 else other_side
        activity.append(
            fx.trade(condition_id, late_side, "BUY", late_price, 250,
                     end - 40, f"0x{i:03d}c", usdc=round(late_price * 250, 4))
        )
        if i % 5 == 0:
            # A window that ends up merged back to cash.
            activity.append(
                fx.trade(condition_id, first_side, "BUY", 0.5, 10, end - 20,
                         f"0x{i:03d}m", activity_type="MERGE")
            )
        realized = round(rng.uniform(-8.0, 12.0), 2)
        positions.append(fx.position(condition_id, "tok_up", realized, outcome_index=0))
        positions.append(fx.position(condition_id, "tok_down", 0.0, outcome_index=1))

    # Out-of-scope noise that must be filtered out downstream.
    markets.extend([fx.BTC_HOURLY_MARKET, fx.SOL_5M_MARKET])
    activity.append(fx.trade("0xbtc1h01", "Up", "BUY", 0.5, 10, base + 10, "0xnoise1"))
    activity.append(fx.trade("0xsol5m01", "Up", "BUY", 0.5, 10, base + 10, "0xnoise2"))

    cache.append(config.RAW_ACTIVITY_JSONL, "test://activity", {}, activity)
    cache.append(config.RAW_MARKETS_JSONL, "test://markets", {}, markets)
    cache.append(config.RAW_POSITIONS_JSONL, "test://positions", {}, positions)
    return {"n_windows": 24, "n_trades": 24 * 3}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("l5")
    cache_dir = root / "cache"
    out_dir = root / "out"
    expected = _build_cache(cache_dir)
    findings = root / "L5_STRATEGY_FINDINGS.md"
    # Explicit empty manual dir: without it the run would pick up the real
    # screenshot batches in the repo and stop being hermetic.
    manual_dir = root / "manual"
    manual_dir.mkdir()
    result = pipeline.analyze(
        str(cache_dir), str(out_dir), str(findings), manual_dir=str(manual_dir)
    )
    return {"out_dir": out_dir, "findings": findings, "result": result, "expected": expected}


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class TestCsvOutputs:
    def test_writes_all_four_tables(self, run):
        for name in (
            config.OUT_ALL_TRADES,
            config.OUT_WINDOW_SEQUENCES,
            config.OUT_WINDOWS_SUMMARY,
            "l5_non_trade_activity.csv",
        ):
            assert (run["out_dir"] / name).exists(), name

    def test_out_of_scope_markets_are_excluded(self, run):
        rows = _read_csv(run["out_dir"] / config.OUT_ALL_TRADES)
        assert len(rows) == run["expected"]["n_trades"]
        assert {r["asset"] for r in rows} == {"BTC", "ETH"}
        assert {r["window_minutes"] for r in rows} == {"5", "15"}

    def test_sequences_have_one_row_per_trade(self, run):
        trades = _read_csv(run["out_dir"] / config.OUT_ALL_TRADES)
        sequences = _read_csv(run["out_dir"] / config.OUT_WINDOW_SEQUENCES)
        assert len(sequences) == len(trades)

    def test_summary_has_one_row_per_window(self, run):
        rows = _read_csv(run["out_dir"] / config.OUT_WINDOWS_SUMMARY)
        assert len(rows) == run["expected"]["n_windows"]
        assert len({r["condition_id"] for r in rows}) == run["expected"]["n_windows"]

    def test_merges_land_in_the_non_trade_table(self, run):
        rows = _read_csv(run["out_dir"] / "l5_non_trade_activity.csv")
        assert rows and {r["activity_type"] for r in rows} == {"MERGE"}

    def test_sequence_cumulative_columns_are_monotonic_within_a_window(self, run):
        rows = _read_csv(run["out_dir"] / config.OUT_WINDOW_SEQUENCES)
        by_window: dict[str, list[dict]] = {}
        for row in rows:
            by_window.setdefault(row["condition_id"], []).append(row)
        for window_rows in by_window.values():
            seqs = [int(r["trade_seq"]) for r in window_rows]
            assert seqs == list(range(1, len(seqs) + 1))
            spends = [float(r["cum_dollars_bought"]) for r in window_rows]
            assert spends == sorted(spends), "cumulative spend must never decrease on buys"

    def test_sequence_matches_hand_computed_inventory(self, run):
        rows = _read_csv(run["out_dir"] / config.OUT_WINDOW_SEQUENCES)
        window = [r for r in rows if r["condition_id"] == "0xw001"]
        assert len(window) == 3
        last = window[-1]
        up_qty, down_qty = float(last["up_qty"]), float(last["down_qty"])
        assert float(last["matched_qty"]) == pytest.approx(min(up_qty, down_qty))
        assert float(last["excess_qty"]) == pytest.approx(up_qty - down_qty)
        expected_pair = float(last["up_avg_price"]) + float(last["down_avg_price"])
        assert float(last["matched_pair_cost_per_share"]) == pytest.approx(expected_pair, rel=1e-6)
        assert float(last["matched_pair_cost_total"]) == pytest.approx(
            expected_pair * float(last["matched_qty"]), rel=1e-6
        )

    def test_summary_agrees_with_the_last_sequence_row(self, run):
        sequences = _read_csv(run["out_dir"] / config.OUT_WINDOW_SEQUENCES)
        summaries = {r["condition_id"]: r for r in _read_csv(
            run["out_dir"] / config.OUT_WINDOWS_SUMMARY)}
        last_by_window: dict[str, dict] = {}
        for row in sequences:
            last_by_window[row["condition_id"]] = row
        for condition_id, last in last_by_window.items():
            summary = summaries[condition_id]
            assert float(summary["final_up_qty"]) == pytest.approx(float(last["up_qty"]))
            assert float(summary["final_down_qty"]) == pytest.approx(float(last["down_qty"]))
            assert float(summary["matched_qty"]) == pytest.approx(float(last["matched_qty"]))
            assert int(summary["total_trades"]) == int(last["trade_seq"])


class TestAnalysisOutput:
    def test_writes_every_section(self, run):
        with open(run["out_dir"] / config.OUT_ANALYSIS_JSON, encoding="utf-8") as handle:
            analysis = json.load(handle)
        for key in (
            "coverage", "q1_size_vs_price", "q2_duration_split", "q3_asset_split",
            "q4_cheaper_side", "q5_time_to_opposite", "q6_pair_cost_target",
            "q7_balanced_finish", "q8_pnl_split",
        ):
            assert key in analysis, key

    def test_coverage_counts_match_the_tables(self, run):
        analysis = run["result"]["analysis"]
        assert analysis["coverage"]["n_trades"] == run["expected"]["n_trades"]
        assert analysis["coverage"]["n_windows"] == run["expected"]["n_windows"]
        assert analysis["coverage"]["windows_with_realized_pnl"] == run["expected"]["n_windows"]

    def test_normalize_stats_record_what_was_dropped(self, run):
        stats = run["result"]["normalize_stats"]
        assert stats["dropped_out_of_scope"] == 2
        assert stats["dropped_non_trade"] >= 1


class TestFindingsDocument:
    def test_renders_with_the_expected_sections(self, run):
        text = run["findings"].read_text(encoding="utf-8")
        for heading in (
            "# L5 Strategy Findings",
            "## 1. Coverage",
            "## 10. Candidate behavioural rules",
            "## 11. Implementable pseudocode",
            "## 12. What this analysis cannot tell you",
        ):
            assert heading in text, heading

    def test_states_the_limits_it_must_state(self, run):
        text = run["findings"].read_text(encoding="utf-8")
        assert "executed fills" in text
        assert "rebates" in text
        assert "No order-book state is reconstructed" in text

    def test_names_the_wallet_under_analysis(self, run):
        assert config.TARGET_WALLET in run["findings"].read_text(encoding="utf-8")
