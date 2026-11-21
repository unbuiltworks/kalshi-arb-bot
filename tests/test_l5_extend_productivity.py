"""OWNER 5569709706 #1: FIFO lot productivity of L5 EXTENDs, deterministic."""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_extend_productivity as ep


def _p(t, side, px, qty, tts=300):
    return {"t": t, "side": side, "px": D(px), "qty": D(qty), "usd": D(px) * D(qty), "tts": tts}


def test_fifo_lots_relations_matching_and_settlement():
    # open YES 10@0.40, extend YES 10@0.42, complete NO 15@0.55 (pair 0.95 / 0.97), settle NO
    ps = [_p(0, "YES", "0.40", "10"), _p(10, "YES", "0.42", "10"), _p(40, "NO", "0.55", "15")]
    lots = ep.window_lots(ps, "NO", D("4.00"))
    a, b, c = lots
    assert [l["relation"] for l in lots] == ["open", "extend", "complete"]
    assert a["matched_share"] == 1.0 and a["pair_cost"] == 0.95 and a["time_to_complement_s"] == 40
    assert b["matched_share"] == 0.5 and b["pair_cost"] == 0.97 and b["residual_qty"] == 5.0
    assert round(b["residual_pnl_usd"], 4) == round(-5 * 0.42, 4)            # YES lost
    assert c["matched_share"] == 1.0 and c["residual_pnl_usd"] == 0.0
    assert b["imbalance_units_before"] == 1.0 and b["run_len"] == 2 and b["move_c"] == 2
    assert b["pair_proxy"] is None and c["pair_proxy"] == D("0.97")            # c: 0.55 + last YES 0.42
    assert b["buckets"]["imbalance"] == "imb1-2u" and b["buckets"]["band"] == "p0.35-0.45"
    # pair contribution of the open lot: 10 x (1 - 0.95)
    assert round(a["pair_pnl_usd"], 4) == 0.5


def test_summary_productive_share_and_net_per_dollar():
    ps = [_p(0, "YES", "0.40", "10"), _p(10, "YES", "0.42", "10"), _p(40, "NO", "0.55", "15")]
    lots = ep.window_lots(ps, "YES", D("4.00"))
    s = ep.summarize([l for l in lots if l["relation"] == "extend"])
    assert s["lots"] == 1 and s["productive_share_of_usd"] == 0.5
    # 5 matched at 0.97 -> +0.15; 5 residual YES won -> +5 x 0.58 = 2.90
    assert s["pair_pnl_usd"] == 0.15 and s["residual_pnl_usd"] == 2.9
    assert s["net_per_usd"] == round(3.05 / 4.2, 4)


def test_canonical_history_headline_shape():
    out = ep.study()
    assert out["windows"] == 2216
    r = out["by_relation"]
    assert r["extend"]["lots"] == 35576
    assert r["complete"]["productive_share_of_usd"] > 0.98
    assert r["open"]["net_per_usd"] > r["extend"]["net_per_usd"]
    imb = out["extend_conditioned"]["imbalance"]
    assert imb["imb<0.5u"]["net_per_usd"] > imb["imb>=4u"]["net_per_usd"]
    assert "directional effect" in out["reading"]
