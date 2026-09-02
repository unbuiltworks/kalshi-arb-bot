"""Deterministic tests for the read-only PMUS status/report layer."""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from decimal import Decimal as D

from src import pmus_status_report as rep


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _obs(ts, bid, ask, cum, last, bid_qty="500", ask_missing=True):
    return {"kind": "bbo_book", "t": _iso(ts), "slug": "aec-x",
            "league": "mlb", "bid": bid, "ask": ask, "bid_qty": bid_qty,
            "ask_book_missing": ask_missing, "last_trade_px": last,
            "shares_traded_cum": cum}


def test_direction_inference_observed_vs_unknown():
    obs = [_obs(0, "0.40", "0.41", "100", "0.40"),
           _obs(5, "0.40", "0.41", "150", "0.41"),   # buy at ask
           _obs(10, "0.40", "0.41", "180", "0.40"),  # sell at bid
           _obs(15, "0.40", "0.42", "200", "0.405")]  # inside -> unknown
    tr = rep.infer_trades(obs)
    assert [t["dir_inferred"] for t in tr] == ["buy", "sell", "unknown"]
    assert tr[0]["vol"] == 50.0
    # no volume delta -> no inferred trade
    assert rep.infer_trades([_obs(0, "0.4", "0.41", "100", "0.4"),
                             _obs(5, "0.4", "0.41", "100", "0.41")]) == []


def test_slot_replay_matched_hold_nofill_sequential_semantics():
    base = 1000.0
    obs = [_obs(base + i * 5, "0.48", "0.50", str(100 + i), "0.48")
           for i in range(60)]
    # sell at bid fills the maker-first YES join; later buy >= 1-comp matches
    obs[4]["shares_traded_cum"] = "200"; obs[4]["last_trade_px"] = "0.48"
    obs[30]["shares_traded_cum"] = "300"; obs[30]["last_trade_px"] = "0.53"
    obs[30]["ask"] = "0.53"                        # buy AT ask -> inferred buy
    row = rep.slot_replay(obs, rep.infer_trades(obs), base)
    assert row["status"] == "MATCHED"
    assert row["pair_cost"] == "0.99" and row["locked_edge_theoretical"] == "0.01"
    assert "rebates_separate" in row               # rewards reported apart
    # without the buy -> one-sided hold, taker option evaluated separately
    obs2 = [dict(o) for o in obs]
    obs2[30]["shares_traded_cum"] = "100"
    hold = rep.slot_replay(obs2, rep.infer_trades(obs2), base)
    assert hold["status"] == "ONE_SIDED_HOLD"
    assert "taker_completion_fee_safe" in hold
    assert "never counted as maker" in hold["taker_completion_note"]
    # no trades at all -> NO_FILL
    quiet = [_obs(base + i * 5, "0.48", "0.50", "100", "0.48")
             for i in range(60)]
    assert rep.slot_replay(quiet, [], base)["status"] == "NO_FILL"


def test_taker_completion_requires_served_ask_and_fee_safety():
    base = 1000.0
    obs = [_obs(base + i * 5, "0.48", "0.50", str(100 + i), "0.48")
           for i in range(60)]
    obs[4]["shares_traded_cum"] = "200"            # first fill via sell
    # served ask AFTER fill at 0.50: NO cost 0.50, taker fee ~1.5c -> not safe
    for o in obs[5:]:
        o["ask_book_missing"] = False
    hold = rep.slot_replay(obs, rep.infer_trades(obs), base)
    assert hold["status"] == "ONE_SIDED_HOLD"
    assert hold["taker_completion_fee_safe"] is False


def test_market_status_gate_honesty():
    base = 1000.0
    # two-sided inferred flow + q=1 depth but ask never served publicly
    obs = []
    cum = 100
    for i in range(130):
        o = _obs(base + i * 5, "0.48", "0.50", str(cum), "0.48")
        if i in (4, 34, 64, 94):
            cum += 10; o["shares_traded_cum"] = str(cum)
            o["last_trade_px"] = "0.48"            # sells
        if i in (20, 50, 80, 110):
            cum += 10; o["shares_traded_cum"] = str(cum)
            o["last_trade_px"] = "0.50"            # buys at ask
        obs.append(o)
    a = rep.analyze_market(obs, base + 130 * 5, 3600)
    assert a["two_sided_inferred_flow"] is True
    assert a["matched"] >= 2
    # ask side never served -> cannot clear the q1 gate (no fabrication)
    assert a["ask_side_served_pct"] == 0.0
    assert a["status"] == "SHADOW_CANDIDATE"
    # same evidence WITH served ask -> gate can clear
    for o in obs:
        o["ask_book_missing"] = False
    a2 = rep.analyze_market(obs, base + 130 * 5, 3600)
    assert a2["status"] == "Q1_GATE_CLEARED"


def test_gap_detection_blocks_readiness():
    base = 1000.0
    obs = [_obs(base, "0.48", "0.50", "100", "0.48"),
           _obs(base + 400, "0.48", "0.50", "200", "0.48")]  # 400s gap
    a = rep.analyze_market(obs, base + 500, 3600)
    assert a["collector_gaps"] >= 1 and a["status"] == "NOT_READY"


def test_summarize_shape_and_theoretical_labels(tmp_path):
    raw = tmp_path / "raw"; raw.mkdir()
    now = datetime.now(timezone.utc)
    rows = [{"kind": "collector_start", "t": now.isoformat()},
            _obs(now.timestamp(), "0.48", "0.50", "100", "0.48")]
    (raw / f"pmus_{now:%Y%m%d}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    r = rep.summarize(tmp_path)
    assert "THEORETICAL" in r["labeling"]
    assert set(r["windows"]) == {"15m", "1h", "since_restart"}
    for w in r["windows"].values():
        assert set(w["statuses"]) == {"Q1_GATE_CLEARED", "SHADOW_CANDIDATE",
                                      "NOT_READY"}
    assert r["markets_tracked"] == 1


def test_report_layer_is_read_only():
    src = inspect.getsource(rep)
    import re
    assert re.findall(r"https://", src) == []      # no network at all
    for forbidden in ("urllib", "http", "order", "cancel", "X-PM-"):
        assert forbidden not in src, forbidden
