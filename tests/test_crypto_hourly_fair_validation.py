"""Synthetic check of the THEORETICAL hourly-fair validation script."""
import datetime as dt
import gzip
import importlib.util
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hourly_fair_validation",
    ROOT / "analysis/l5/crypto_hourly/hourly_fair_validation.py")
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)

T0 = dt.datetime(2026, 11, 7, 17, tzinfo=dt.timezone.utc)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path, rows):
    with gzip.open(path, "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _synthetic(tmp, hours=24 * 9, sd_5m=0.002, seed=7):
    rnd = random.Random(seed)
    px, five, marks = 100.0, [], []
    t = T0
    for _ in range(hours * 12 + 1):
        marks.append((t, round(px, 2)))
        px *= math.exp(rnd.gauss(0, sd_5m))
        t += dt.timedelta(minutes=5)
    for (a, pa), (b, pb) in zip(marks, marks[1:]):
        five.append({"ws": _iso(a), "we": _iso(b), "ptb": f"{pa:.2f}",
                     "settle": f"{pb:.2f}", "status": "MARKET_STATUS_RESOLVED",
                     "outcomePrices": json.dumps(["1", "0"] if pb >= pa
                                                 else ["0", "1"])})
    hourly = []
    for i in range(0, len(marks) - 12, 12):
        (a, pa), (b, pb) = marks[i], marks[i + 12]
        hourly.append({"slug": f"cpc-btc-updown-1h-{a:%Y-%m-%d-%H%M}z",
                       "ws": _iso(a), "we": _iso(b), "ptb": f"{pa:.2f}",
                       "settle": f"{pb:.2f}", "idx": "BRTI",
                       "status": "MARKET_STATUS_RESOLVED",
                       "outcomePrices": json.dumps(["1", "0"] if pb >= pa
                                                   else ["0", "1"])})
    _write(tmp / "hourly_btc.jsonl.gz", hourly)
    _write(tmp / "5m_btc.jsonl.gz", five)
    return hourly


def test_integrity_and_heldout_scores(tmp_path):
    _synthetic(tmp_path)
    out = V.main(["x", str(tmp_path)])
    b = out["assets"]["btc"]
    i = b["integrity"]
    assert i["chain_settle_eq_next_ptb"][0] == i["chain_settle_eq_next_ptb"][1]
    assert i["outcome_matches_ge_rule"][0] == i["windows"]
    assert i["hourly_ptb_eq_5m_ptb"][0] == i["hourly_ptb_eq_5m_ptb"][1] > 0
    # true vol of the synthetic walk: sd_5m per 5 minutes
    true_ann = 0.002 * math.sqrt(12 * 24 * 365.25)
    assert abs(b["vol_train"]["annual_vol"] / true_ann - 1) < 0.25
    tc = b["test_checkpoints"]
    assert tc["windows"] == 24 * 2
    # a correct model must beat a coin flip on held-out windows
    assert tc["model_const_vol"]["brier"] < tc["coin_flip_0.5"]["brier"]
    assert out["labels"].startswith("THEORETICAL")
    assert out["venue_compare"]["n"] == 0


def test_venue_compare_labels_and_assumed_fills(tmp_path):
    hourly = _synthetic(tmp_path)
    last = hourly[-1]
    ws = V._ts(last["ws"])
    books = [{"t": ws + 60 * m + 1, "slug": last["slug"], "bid": 0.01,
              "ask": 0.99} for m in V.CHECK_MIN]
    _write(tmp_path / "hourly_books_live.jsonl.gz", books)
    vc = V.main(["x", str(tmp_path)])["venue_compare"]
    assert vc["windows"] == 1 and vc["checkpoints"] >= 1
    assert vc["labels"] == "THEORETICAL"
    assert "NOT P&L" in vc["maker_edge_THEORETICAL"]["assumptions"]
