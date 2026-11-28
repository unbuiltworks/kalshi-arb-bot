"""Thin venue adapters to the common fill record. READ-ONLY.

Each adapter returns {episode_id: [fill, ...]} with fills chronological and
shaped {t, side, price, qty, tte_s, sampled, acted}. Nothing venue-specific
may reach the state maths -- see src/l5_state_trajectory.
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

UP, DN = "up", "dn"


def _f(x) -> Optional[float]:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ep(s) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:                                        # noqa: BLE001
        return None


def l5_canonical(path: Path) -> dict:
    """L5's public fills. `outcome` is the side; `side` is BUY/SELL and is
    99.997% BUY, so the two SELL prints are dropped rather than modelled."""
    out = defaultdict(list)
    dropped = 0
    with gzip.open(path, "rt") as fh:
        for r in csv.DictReader(fh):
            if r.get("side") != "BUY":
                dropped += 1
                continue
            side = UP if r.get("outcome") == "Up" else DN
            out[r["window_id"]].append({
                "t": _f(r.get("timestamp")),
                "side": side,
                "price": _f(r.get("price")),
                "qty": _f(r.get("quantity")),
                "tte_s": _f(r.get("seconds_to_settlement")),
                "maker_taker": r.get("maker_taker"),
                "asset": r.get("asset"),
                "duration_minutes": r.get("duration_minutes"),
                "settlement_outcome": r.get("settlement_outcome"),
                "seq": int(r.get("trade_seq") or 0)})
    for v in out.values():
        v.sort(key=lambda f: (f["seq"],))
    return dict(out), dropped


def pmus_from_inventory_deltas(path: Path) -> dict:
    """PMUS fills recovered from the engine's OWN inventory ledger.

    Order rows carry no timestamp, so a fill's moment is unrecoverable from
    them. Consecutive `inventory_before` snapshots on TIMESTAMPED decisions
    do carry it: a side whose quantity grew between two decisions filled in
    between, and its price falls out of the average-cost move exactly

        px = (q1*avg1 - q0*avg0) / (q1 - q0)

    The fill is stamped with the decision that OBSERVED it, which is an
    upper bound on its true moment -- so PMUS cadence is an upper bound and
    is labelled one wherever it is reported.
    """
    by_slug = defaultdict(list)
    for line in gzip.open(path, "rt"):
        line = line.strip()
        if not line:
            continue
        for d in (json.loads(line).get("decisions") or []):
            by_slug[d.get("slug")].append(d)

    out, unrecovered = defaultdict(list), 0
    for slug, ds in by_slug.items():
        ds.sort(key=lambda d: (str(d.get("t_utc") or ""),
                               int(d.get("decision_idx") or 0)))
        prev = None
        for d in ds:
            inv = d.get("inventory_before") or {}
            cur = {UP: _f(inv.get("up")) or 0.0, DN: _f(inv.get("dn")) or 0.0,
                   "avg_up": _f(inv.get("avg_up")),
                   "avg_dn": _f(inv.get("avg_dn"))}
            if prev is not None:
                for side, ak in ((UP, "avg_up"), (DN, "avg_dn")):
                    dq = cur[side] - prev[side]
                    if dq <= 1e-9:
                        continue
                    an, ao = cur[ak], prev[ak] or 0.0
                    if an is None:
                        unrecovered += 1
                        continue
                    px = (cur[side] * an - prev[side] * ao) / dq
                    if not (0 < px < 1):
                        unrecovered += 1
                        continue
                    sh = (d.get("shadow") or {}).get("stochastic") or {}
                    out[slug].append({
                        "t": _ep(d.get("t_utc")), "side": side,
                        "price": px, "qty": dq,
                        "tte_s": None,          # not recorded per fill
                        "tte_bucket_from_state": (
                            str(d.get("state") or "").split("|")[1]
                            if len(str(d.get("state") or "").split("|")) > 1
                            else None),
                        "sampled": sh.get("sampled"),
                        "acted": d.get("acted")})
            prev = cur
    return dict(out), unrecovered


def pmus_orders_in_sequence(path: Path) -> dict:
    """Every COMPLETE/EXTEND/OPEN order in session order, filled or not.

    Used ONLY for the perfect-conversion counterfactual, which needs the
    attempts that did not fill. Carries no timestamps, so no cadence field
    may be derived from it.
    """
    out = defaultdict(list)
    for line in gzip.open(path, "rt"):
        line = line.strip()
        if not line:
            continue
        s = json.loads(line)
        ds = s.get("decisions") or []
        slug = ds[0].get("slug") if ds else s.get("slug")
        for i, o in enumerate(s.get("orders") or []):
            out[slug].append({"_ts": str(s.get("_ts")), "_i": i, **o})
    for v in out.values():
        v.sort(key=lambda o: (o["_ts"], o["_i"]))
    return dict(out)
