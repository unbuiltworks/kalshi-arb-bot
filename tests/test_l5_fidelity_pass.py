"""OWNER 5553674206: the offline fidelity pass is deterministic, scores
only reconstructable features, never promotes a non-causal one, and its
stochastic replay / size / aggressiveness / sequence references are
computed from the canonical schema exactly."""
from __future__ import annotations

import csv
import gzip
import json

from src.polymarket_l5 import fidelity_pass as fp
from src.polymarket_l5.action_model import SYSTEMATIC_REGIME_EPOCH

COLS = ["schema_version", "window_id", "market_slug", "asset",
        "duration_minutes", "trade_seq", "timestamp", "datetime_utc",
        "seconds_from_window_start", "seconds_to_settlement", "outcome",
        "side", "price", "quantity", "dollars", "maker_taker", "cum_up_qty",
        "cum_up_spend", "cum_down_qty", "cum_down_spend", "matched_qty",
        "matched_pair_unit_cost", "residual_qty_signed", "residual_side",
        "settlement_outcome", "resolution_source", "source",
        "transaction_hash"]


def _window(wid, t0, fills, mt="unknown"):
    """fills: list of (outcome, price, qty). Builds cumulative columns."""
    rows, up, dn, sup, sdn = [], 0.0, 0.0, 0.0, 0.0
    for i, (outcome, px, q) in enumerate(fills):
        if outcome == "Up":
            up += q; sup += q * px
        else:
            dn += q; sdn += q * px
        matched = min(up, dn)
        pc = ((sup / up if up else 0) + (sdn / dn if dn else 0)) if matched else ""
        rows.append({
            "schema_version": "1", "window_id": wid, "market_slug": "m",
            "asset": "BTC", "duration_minutes": "5", "trade_seq": str(i + 1),
            "timestamp": str(t0 + 10 * i), "datetime_utc": "x",
            "seconds_from_window_start": str(10 * i),
            "seconds_to_settlement": str(300 - 10 * i), "outcome": outcome,
            "side": "BUY", "price": str(px), "quantity": str(q),
            "dollars": str(round(px * q, 6)), "maker_taker": mt,
            "cum_up_qty": str(up), "cum_up_spend": str(sup),
            "cum_down_qty": str(dn), "cum_down_spend": str(sdn),
            "matched_qty": str(matched), "matched_pair_unit_cost": str(pc),
            "residual_qty_signed": str(up - dn),
            "residual_side": "Up" if up >= dn else "Down",
            "settlement_outcome": "Up", "resolution_source": "t",
            "source": "t", "transaction_hash": wid + str(i)})
    return rows


def _write(tmp_path, windows):
    p = tmp_path / "seq.csv.gz"
    with gzip.open(p, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for rows in windows:
            w.writerows(rows)
    return p


def _corpus(tmp_path):
    t_train = SYSTEMATIC_REGIME_EPOCH + 1000
    t_valid = fp.HOLDOUT_EPOCH + 1000
    pat = [("Up", 0.50, 10), ("Up", 0.52, 10), ("Down", 0.46, 10),
           ("Up", 0.55, 5), ("Down", 0.44, 15)]
    wins = [_window(f"w{i}", t_train + 1000 * i, pat, mt="maker")
            for i in range(30)]
    wins += [_window(f"v{i}", t_valid + 1000 * i, pat, mt="taker")
             for i in range(6)]
    return _write(tmp_path, wins)


def test_transitions_split_by_time_and_carry_causal_features(tmp_path):
    train, valid = fp.build_transitions(_corpus(tmp_path))
    assert len(train) == 30 * 4 and len(valid) == 6 * 4
    t = train[1]                     # after Up 10 @0.50, Up 10 @0.52
    assert t["last_rel"] == "extend" and t["relation"] == "complete"
    assert t["imb_fine"] == "i10-25" and t["inv_norm"] == "onesided"
    assert t["path3"] in ("uf", "uu", "ff", "fu", "du", "ud", "dd", "df", "fd")
    assert t["run_len"] == "run2-3"
    assert t["tte_fine"] == "t3-5m"


def test_study_is_deterministic_and_never_promotes_non_causal(tmp_path):
    p = _corpus(tmp_path)
    a = fp.run(p)
    b = fp.run(p)
    a.pop("generated_utc"); b.pop("generated_utc")
    assert a == b
    su = a["state_upgrade"]
    assert su["v1_base"]["extras"] == ["(v1 base)"]
    assert su["v2_current"]["extras"] == ["last_rel", "last_size"]
    for r in su["singles_on_v2"] + su["pairs_on_v2"]:
        if "cadence" in r["extras"]:
            assert r["promotable"] is False
        assert "median_transitions_per_state" in r
    assert "cadence" in su["non_causal_features"]
    assert "book_spread_depth" in su["unsupported_features"]


def test_sampling_is_deterministic_inverse_cdf():
    dist = {"complete|q<5": 0.25, "extend|q5-15": 0.5, "open|q<5": 0.25}
    a, u = fp.sample_from(dist, fp.seed_material("w", 3))
    b, u2 = fp.sample_from(dist, fp.seed_material("w", 3))
    assert (a, u) == (b, u2) and 0.0 <= u < 1.0
    # u below 0.25 -> first sorted key; above 0.75 -> last
    assert fp.sample_from(dist, "00000000")[0] == "complete|q<5"
    assert fp.sample_from(dist, "ffffffff")[0] == "open|q<5"


def test_stochastic_replay_reports_mix_and_calibration(tmp_path):
    train, valid = fp.build_transitions(_corpus(tmp_path))
    out = fp.stochastic_replay(train, valid)
    assert out["n_valid"] == 24
    assert set(out["relation_agreement_pct"]) == {"modal", "sampled"}
    assert set(out["relation_mix"]) == {"actual", "modal", "sampled"}
    assert all(0 <= c["observed_extend"] <= 1 for c in out["calibration_p_extend"])


def test_size_and_aggressiveness_lookups(tmp_path):
    train, valid = fp.build_transitions(_corpus(tmp_path))
    sp = fp.size_policy(train)
    row = fp.l5_target_size(sp, train[1]["base"], "extend")
    assert row["source"] == "state|relation" and row["n"] >= 20
    assert row["shares_p50"] == 10.0
    assert fp.l5_target_size(sp, "nowhere", "extend")["source"] == "relation"
    ag = fp.aggressiveness_policy(train, valid)
    assert ag["known_rows"] == len(train) + len(valid)
    tb = fp.l5_target_aggressiveness(ag, 250.0, "extend")
    assert tb["tte_bucket"] == "tte2-5m" and 0 <= tb["p_taker"] <= 1


def test_sequence_reference_and_trajectory_score(tmp_path):
    train, valid = fp.build_transitions(_corpus(tmp_path))
    ref = fp.sequence_reference(train, valid)
    assert ref["windows"] == 36
    assert ref["actions_per_window"]["p50"] == 5
    assert ref["same_side_run_len"]["max"] >= 1
    ref_json = json.loads(json.dumps(ref))          # keys become strings
    fills = [{"side": "YES", "price": "0.43", "qty": "0.02"},
             {"side": "YES", "price": "0.45", "qty": "0.02"},
             {"side": "NO", "price": "0.50", "qty": "0.02"}]
    sc = fp.trajectory_score(fills, ref_json)
    assert sc["n_fills"] == 3
    assert sc["final"]["imb_units"] == 1.0
    assert abs(sc["final"]["pair_cost"] - 0.94) < 1e-9
    assert sc["by_decile"][0]["imb_units"] == 1.0
