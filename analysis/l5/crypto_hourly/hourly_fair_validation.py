"""Held-out validation of src/pmus_crypto_hourly_fair.py.  THEORETICAL.

Read-only research over files already on disk; no network, no orders.

Inputs (data/pmus_crypto_hourly/, all from the public PMUS gateway
`/v1/market/slug/<slug>` of RESOLVED markets, `assetPriceTerms`):
  hourly_<asset>.jsonl.gz   1h windows: priceToBeat, settlementPrice,
                            outcomePrices
  5m_<asset>.jsonl.gz       5m windows: their priceToBeat values are the
                            venue's official 60-print CF Benchmarks average
                            at every 5-minute mark -> an intra-hour spot
                            PROXY on the settlement index itself
  hourly_books_live.jsonl.gz  optional bounded live capture of hourly
                            top-of-book (BOOK STATE ONLY, never a fill)

What can and cannot be concluded:
  * vol is fitted on the first window block only (train) and every score
    is on later windows (test);
  * the spot proxy is a 60 s average ending at the mark, not the latest
    print: its extra variance (~sigma^2 * 20 s) is small against the
    remaining variance except at the last checkpoints;
  * checkpoints inside one hour share an outcome (not independent): the
    number of WINDOWS is the honest sample size;
  * the venue-mid comparison and the maker-edge figure need venue books,
    which exist only for the bounded live capture. Fills there are ASSUMED
    (no queue, no adverse selection): an upper bound, never P&L.

Usage: python3 analysis/l5/crypto_hourly/hourly_fair_validation.py \
           data/pmus_crypto_hourly [out.json]
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src import pmus_crypto_hourly_fair as H  # noqa: E402

ASSETS = {"btc": "BRTI", "eth": "ETHUSD_RTI", "sol": "SOLUSD_RTI",
          "xrp": "XRPUSD_RTI"}
DECIMALS = {"btc": 2, "eth": 2, "sol": 4, "xrp": 5}
CHECK_MIN = tuple(range(5, 60, 5))          # 5..55 min after the start
TRAIN_DAYS = 7
EWMA_HALF_LIFE_H = 24.0
EDGE_MIN = 0.03                             # model minus price, THEORETICAL


def _ts(s: str) -> float:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _load(p: Path) -> list:
    if not p.exists():
        return []
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "rt") as f:
        return [json.loads(x) for x in f if x.strip()]


def _up(r) -> bool:
    return json.loads(r["outcomePrices"])[0] == "1"


def integrity(hourly: list, five: dict) -> dict:
    rows = sorted([r for r in hourly if r.get("ptb") and r.get("settle")],
                  key=lambda r: r["ws"])
    pairs = [(a, b) for a, b in zip(rows, rows[1:]) if a["we"] == b["ws"]]
    chain_ok = sum(a["settle"] == b["ptb"] for a, b in pairs)
    rule_ok = sum(_up(r) == (float(r["settle"]) >= float(r["ptb"]))
                  for r in rows)
    ties = sum(float(r["settle"]) == float(r["ptb"]) for r in rows)
    x5 = sum(1 for r in rows if _ts(r["ws"]) in five
             and five[_ts(r["ws"])] == r["ptb"])
    n5 = sum(1 for r in rows if _ts(r["ws"]) in five)
    return {"windows": len(rows), "chain_settle_eq_next_ptb": [chain_ok,
            len(pairs)], "outcome_matches_ge_rule": [rule_ok, len(rows)],
            "exact_ties": ties, "hourly_ptb_eq_5m_ptb": [x5, n5],
            "up_rate": round(sum(map(_up, rows)) / max(1, len(rows)), 4)}


def _ann(var_h: float) -> float:
    # boundary-to-boundary of two 60 s averages: diffusion T - L/3 seconds
    return math.sqrt(var_h * H.YEAR_S / (H.HORIZON_S - H.FIX_WINDOW_S / 3))


def score(ps, ys) -> dict:
    n = len(ps)
    if not n:
        return {"n": 0}
    eps = 1e-6
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / n
    ll = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
              for p, y in zip(ps, ys)) / n
    return {"n": n, "brier": round(brier, 5), "log_loss": round(ll, 5)}


def calib(ps, ys, bins=10) -> list:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, y) for p, y in zip(ps, ys)
               if lo <= p < hi or (b == bins - 1 and p == 1)]
        if sel:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel),
                        "mean_p": round(sum(p for p, _ in sel) / len(sel), 3),
                        "freq_up": round(sum(y for _, y in sel) / len(sel), 3)})
    return out


def run_asset(a: str, hourly: list, five_rows: list) -> dict:
    ix = ASSETS[a]
    five = {_ts(r["ws"]): r["ptb"] for r in five_rows if r.get("ptb")}
    rows = sorted([r for r in hourly if r.get("ptb") and r.get("settle")],
                  key=lambda r: r["ws"])
    res = {"integrity": integrity(rows, five)}
    t0 = _ts(rows[0]["ws"])
    split = t0 + TRAIN_DAYS * 86400
    rets = [(_ts(r["ws"]), math.log(float(r["settle"]) / float(r["ptb"])))
            for r in rows]
    train = [x for t, x in rets if _ts(rows[0]["ws"]) <= t < split]
    sig_const = _ann(sum(x * x for x in train) / len(train))
    res["vol_train"] = {"windows": len(train), "annual_vol": round(sig_const,
                        4), "train_end": dt.datetime.fromtimestamp(
                        split, dt.timezone.utc).isoformat()}
    # causal EWMA of squared hourly returns (uses only settled windows)
    lam = 0.5 ** (1 / EWMA_HALF_LIFE_H)
    ew, ewma_at = None, {}
    for t, x in rets:
        ewma_at[t] = None if ew is None else _ann(ew)
        ew = x * x if ew is None else lam * ew + (1 - lam) * x * x
    # test windows: z-scores of the hourly return under the train vol
    test = [(t, x) for t, x in rets if t >= split]
    sd_h = sig_const * math.sqrt((H.HORIZON_S - H.FIX_WINDOW_S / 3)
                                 / H.YEAR_S)
    z = [x / sd_h for _, x in test]
    res["vol_test"] = {
        "windows": len(z),
        "realized_annual_vol": round(_ann(sum(x * x for _, x in test)
                                          / len(test)), 4),
        "z_std": round(math.sqrt(sum(v * v for v in z) / len(z)), 3),
        "frac_abs_z_lt_1": round(sum(abs(v) < 1 for v in z) / len(z), 3),
        "frac_abs_z_lt_2": round(sum(abs(v) < 2 for v in z) / len(z), 3),
        "normal_ref": [0.683, 0.954]}
    ze = [x / (ewma_at[t] * math.sqrt((H.HORIZON_S - H.FIX_WINDOW_S / 3)
                                      / H.YEAR_S))
          for t, x in test if ewma_at.get(t)]
    res["vol_test"]["ewma_z_std"] = round(
        math.sqrt(sum(v * v for v in ze) / len(ze)), 3) if ze else None
    res["vol_test"]["ewma_frac_abs_z_lt_2"] = round(
        sum(abs(v) < 2 for v in ze) / len(ze), 3) if ze else None
    # checkpoints
    out = {"const": ([], []), "ewma": ([], [])}
    per_min = {m: ([], []) for m in CHECK_MIN}
    windows_used = 0
    for r in rows:
        ws = _ts(r["ws"])
        if ws < split:
            continue
        y = 1 if _up(r) else 0
        used = False
        for m in CHECK_MIN:
            now = ws + 60 * m
            spot = five.get(now)
            if spot is None:
                continue
            for name, vol in (("const", sig_const), ("ewma", ewma_at.get(ws))):
                if vol is None:
                    continue
                p = H.hourly_fair(
                    spot=spot, strike=r["ptb"], annual_vol=vol, now=now,
                    observed_at=now, start=ws, end=ws + 3600,
                    reference_verified=True, spot_index=ix,
                    settlement_index=ix, price_decimals=DECIMALS[a])
                if p is None:
                    continue
                out[name][0].append(p)
                out[name][1].append(y)
                if name == "const":
                    per_min[m][0].append(p)
                    per_min[m][1].append(y)
                used = True
        windows_used += used
    ys = out["const"][1]
    res["test_checkpoints"] = {
        "windows": windows_used,
        "model_const_vol": score(*out["const"]),
        "model_ewma_vol": score(*out["ewma"]),
        "coin_flip_0.5": score([0.5] * len(ys), ys),
        "by_minute_const": {m: score(*per_min[m]) for m in CHECK_MIN},
        "calibration_const": calib(*out["const"])}
    return res


def venue_compare(books: list, hourly: dict, five: dict, vols: dict) -> dict:
    """Model vs venue mid at 5-min marks of captured windows. THEORETICAL."""
    by = {}
    for b in books:
        if b.get("bid") is None or b.get("ask") is None:
            continue
        by.setdefault(b["slug"], []).append(b)
    rows, edge = [], []
    for a, ix in ASSETS.items():
        hrs = {r["slug"]: r for r in hourly.get(a, []) if r.get("settle")}
        f5 = five.get(a, {})
        for slug, bs in by.items():
            r = hrs.get(slug)
            if not r:
                continue
            ws = _ts(r["ws"])
            y = 1 if _up(r) else 0
            for m in CHECK_MIN:
                now = ws + 60 * m
                spot = f5.get(now)
                snap = [b for b in bs if now <= b["t"] <= now + 30]
                if spot is None or not snap:
                    continue
                b = snap[0]
                p = H.hourly_fair(
                    spot=spot, strike=r["ptb"], annual_vol=vols[a], now=now,
                    observed_at=now, start=ws, end=ws + 3600,
                    reference_verified=True, spot_index=ix,
                    settlement_index=ix, price_decimals=DECIMALS[a])
                if p is None:
                    continue
                mid = (b["bid"] + b["ask"]) / 2
                rows.append((slug, m, p, mid, y))
                # THEORETICAL maker: rest at our side's best price, ASSUME
                # filled; maker fee taken as 0, no rebate, no reward
                if p - b["bid"] >= EDGE_MIN:
                    edge.append(y - b["bid"])
                elif b["ask"] - p >= EDGE_MIN:
                    edge.append(b["ask"] - y)
    if not rows:
        return {"n": 0, "note": "no captured window has settled yet"}
    ys = [x[4] for x in rows]
    return {"windows": len({x[0] for x in rows}), "checkpoints": len(rows),
            "model": score([x[2] for x in rows], ys),
            "venue_mid": score([x[3] for x in rows], ys),
            "mean_abs_model_minus_mid": round(
                sum(abs(x[2] - x[3]) for x in rows) / len(rows), 4),
            "maker_edge_THEORETICAL": {
                "edge_min": EDGE_MIN, "signals": len(edge),
                "mean_payoff_per_contract": round(sum(edge) / len(edge), 4)
                if edge else None,
                "assumptions": "every signal filled at our best price, no "
                               "queue, no adverse selection, maker fee 0, no "
                               "rebate/reward; upper bound, NOT P&L"},
            "labels": "THEORETICAL"}


def main(argv) -> dict:
    d = Path(argv[1])
    out = {"labels": "THEORETICAL -- research replay, never P&L",
           "module": "src/pmus_crypto_hourly_fair.py", "assets": {}}
    hourly_all, five_all, vols = {}, {}, {}
    for a in ASSETS:
        hourly = _load(d / f"hourly_{a}.jsonl.gz")
        fr = _load(d / f"5m_{a}.jsonl.gz")
        if not hourly:
            continue
        hourly_all[a] = hourly
        five_all[a] = {_ts(r["ws"]): r["ptb"] for r in fr if r.get("ptb")}
        out["assets"][a] = run_asset(a, hourly, fr)
        vols[a] = out["assets"][a]["vol_train"]["annual_vol"]
    books = _load(d / "hourly_books_live.jsonl.gz")
    out["venue_compare"] = venue_compare(books, hourly_all, five_all, vols)
    if len(argv) > 2:
        Path(argv[2]).write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv), indent=1))
