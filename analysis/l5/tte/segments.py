#!/usr/bin/env python3
"""L5's OWN BTC15m executions, segmented by the TTE of the window's first fill.

THE QUESTION. Our Gemini clone quotes only at `tte <= 300`. L5's measured
TTE_policy is `[0, 900]` -- the whole window. If L5's recycling comes
predominantly from the interval our clone refuses to enter, then the clone is
not failing to reproduce L5's behaviour; it is being prevented from attempting
it.

WHAT THIS READS. `data/l5/raw/activity.jsonl`: L5's ACTUAL Polymarket fills,
as the venue reported them. Price, size, side, outcome and timestamp are
observed, not modelled. The event slug carries the window's START epoch --
verified against the human-readable title -- so time-to-expiry is
`start + duration - timestamp`, computed and never estimated.

WHAT IT IS NOT. This is OBSERVED_RETROSPECTIVE evidence about L5. It is not
evidence about our clone, not a backtest of our clone, and not realized P&L
for anybody. A pair basis here is what L5 PAID; it is never our cost basis.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Sequence

#: window length by slug family, in seconds
FAMILY_S = {"btc-updown-15m": 900, "btc-updown-5m": 300,
            "eth-updown-15m": 900, "eth-updown-5m": 300}

#: the segmentation asked for, as (label, lo, hi) on the FIRST fill's TTE
BUCKETS = ((">600", 600.0, float("inf")),
           ("300-600", 300.0, 600.0),
           ("120-300", 120.0, 300.0),
           ("60-120", 60.0, 120.0),
           ("<60", 0.0, 60.0))

#: the clone's own quoting band, read for reporting only and never applied
CLONE_MAX_TTE_S = 300.0

LABELS = ("OBSERVED_RETROSPECTIVE: L5's own venue-reported fills. Not our "
          "clone, not a backtest of it, and never our cost basis or realized "
          "P&L.")


def bucket_of(tte: float) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= tte < hi:
            return name
    return "<60" if tte < 0 else ">600"


def load_fills(path: str, family: str = "btc-updown-15m"
               ) -> Dict[str, List[Dict[str, Any]]]:
    """Every fill of one contract family, grouped by window, time-ordered."""
    dur = FAMILY_S[family]
    by_window: Dict[str, List[Dict[str, Any]]] = {}
    seen: set = set()
    dropped: Dict[str, int] = {"duplicate": 0, "no_side": 0}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                page = json.loads(line)
            except ValueError:
                continue
            for p in (page.get("payload") or []):
                slug = str(p.get("eventSlug") or "")
                if not slug.startswith(family + "-"):
                    continue
                try:
                    start = int(slug.rsplit("-", 1)[1])
                    ts = int(p["timestamp"])
                except (ValueError, KeyError, TypeError):
                    continue
                side = str(p.get("side") or "")
                if side not in ("BUY", "SELL"):
                    # a handful of rows carry an empty side. A fill whose
                    # direction is unknown cannot be netted into inventory,
                    # and guessing it would invent a position.
                    dropped["no_side"] += 1
                    continue
                # THE PAGES OVERLAP. This file is paginated by offset and the
                # same fill appears on more than one page; counting it twice
                # inflates both the fill counts and the inventory. The venue's
                # transaction hash plus the asset, side, size and timestamp
                # identify one fill.
                key = (p.get("transactionHash"), p.get("asset"), side,
                       str(p.get("size")), str(p.get("price")), ts)
                if key in seen:
                    dropped["duplicate"] += 1
                    continue
                seen.add(key)
                by_window.setdefault(slug, []).append({
                    "ts": ts,
                    "tte_s": float(start + dur - ts),
                    "outcome": str(p.get("outcome") or ""),
                    "side": side,
                    "price": D(str(p.get("price") or "0")),
                    "size": D(str(p.get("size") or "0")),
                    "usdc": D(str(p.get("usdcSize") or "0")),
                })
    for rows in by_window.values():
        rows.sort(key=lambda r: r["ts"])
    load_fills.dropped = dict(dropped)                       # type: ignore
    return by_window


def window_economics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """One window's matched/residual economics from L5's actual fills.

    A matched pair on this venue is one Up and one Down: they settle to $1
    together, so the pair basis is the sum of the two average prices. BUY and
    SELL are netted per outcome before anything is matched -- a leg that was
    bought and sold again is not inventory.
    """
    qty = {"Up": D("0"), "Down": D("0")}
    spend = {"Up": D("0"), "Down": D("0")}
    gross = D("0")
    for r in rows:
        o = r["outcome"]
        if o not in qty:
            continue
        signed = r["size"] if r["side"] == "BUY" else -r["size"]
        qty[o] += signed
        spend[o] += (r["usdc"] if r["side"] == "BUY" else -r["usdc"])
        if r["side"] == "BUY":
            gross += r["size"]
    up, dn = qty["Up"], qty["Down"]
    matched = min(up, dn) if up > 0 and dn > 0 else D("0")
    residual = up - dn
    basis = None
    degenerate = None
    if matched > 0 and up > 0 and dn > 0:
        b = (spend["Up"] / up) + (spend["Down"] / dn)
        # A PAIR BASIS IS A PRICE AND CANNOT BE NEGATIVE OR ABSURD. When a
        # window's buys and sells nearly cancel, the surviving quantity is a
        # rounding remnant and the average price it implies is meaningless --
        # dividing a residual dollar amount by a residual share count. Those
        # windows are reported as degenerate rather than folded into a median
        # that they would dominate; an earlier run of this study produced a
        # median basis of -12.7 by averaging them in.
        if D("0") < b <= D("2"):
            basis = b
        else:
            degenerate = str(b)
    return {
        "fills": len(rows),
        "first_tte_s": float(rows[0]["tte_s"]) if rows else None,
        "last_tte_s": float(rows[-1]["tte_s"]) if rows else None,
        "up_qty": up, "down_qty": dn,
        "gross": gross, "matched": matched, "residual": residual,
        "two_sided": bool(up > 0 and dn > 0),
        "pair_basis": basis,
        "degenerate_basis": degenerate,
        "par_constrained": bool(basis is not None and basis <= D("1")),
    }


def _median(xs: List[float]):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def segment(by_window: Mapping[str, Sequence[Mapping[str, Any]]]
            ) -> Dict[str, Any]:
    """Per first-fill-TTE bucket, what L5 actually achieved."""
    after_expiry = 0
    degenerate = 0
    agg: Dict[str, Dict[str, Any]] = {}
    for name, _lo, _hi in BUCKETS:
        agg[name] = {"windows": 0, "fills": 0, "two_sided": 0,
                     "par": 0, "matched": D("0"), "gross": D("0"),
                     "bases": [], "residuals": [], "first_ttes": []}
    for _slug, rows in by_window.items():
        if not rows:
            continue
        e = window_economics(rows)
        if e["first_tte_s"] is None or e["first_tte_s"] < 0:
            # a first fill timestamped after the window's own expiry is not a
            # TTE observation; it is a clock or slug problem and is counted
            # apart rather than forced into the <60 bucket.
            after_expiry += 1
            continue
        b = agg[bucket_of(e["first_tte_s"])]
        b["windows"] += 1
        b["fills"] += e["fills"]
        b["two_sided"] += 1 if e["two_sided"] else 0
        b["par"] += 1 if e["par_constrained"] else 0
        b["matched"] += e["matched"]
        b["gross"] += e["gross"]
        b["first_ttes"].append(e["first_tte_s"])
        b["residuals"].append(float(abs(e["residual"])))
        if e["pair_basis"] is not None:
            b["bases"].append(float(e["pair_basis"]))
        elif e["degenerate_basis"] is not None:
            degenerate += 1
            b["degenerate"] = b.get("degenerate", 0) + 1
    out = {}
    for name, b in agg.items():
        w = b["windows"]
        out[name] = {
            "windows": w,
            "fills": b["fills"],
            "fills_per_window": (round(b["fills"] / w, 1) if w else None),
            "two_sided_windows": b["two_sided"],
            "complement_rate": (round(b["two_sided"] / w, 4) if w else None),
            "par_constrained_windows": b["par"],
            "par_constrained_rate": (round(b["par"] / w, 4) if w else None),
            "matched_contracts": str(b["matched"]),
            "gross_contracts": str(b["gross"]),
            "matched_over_gross": (str(round(b["matched"] / b["gross"], 4))
                                   if b["gross"] > 0 else None),
            "median_pair_basis": (round(_median(b["bases"]), 4)
                                  if b["bases"] else None),
            "median_abs_residual": (round(_median(b["residuals"]), 4)
                                    if b["residuals"] else None),
            "median_first_fill_tte_s": (round(_median(b["first_ttes"]), 1)
                                        if b["first_ttes"] else None),
            "degenerate_basis_windows": b.get("degenerate", 0),
        }
    return {"buckets": out, "clone_max_tte_s": CLONE_MAX_TTE_S,
            "windows_first_fill_after_expiry": after_expiry,
            "degenerate_basis_windows": degenerate,
            "labels": LABELS}


def fills_by_tte(by_window: Mapping[str, Sequence[Mapping[str, Any]]]
                 ) -> Dict[str, Any]:
    """Where L5's fills actually happen, against the clone's 300s ceiling.

    Counted over EVERY fill rather than per window, because the question is
    how much of his activity the clone's band excludes.
    """
    inside = outside = after = 0
    ttes: List[float] = []
    for rows in by_window.values():
        for r in rows:
            t = float(r["tte_s"])
            if t < 0:
                after += 1
                continue
            ttes.append(t)
            if t <= CLONE_MAX_TTE_S:
                inside += 1
            else:
                outside += 1
    n = inside + outside
    return {
        "fills": n,
        "fills_after_expiry_excluded": after,
        "inside_clone_band": inside,
        "outside_clone_band": outside,
        "share_outside": (round(outside / n, 4) if n else None),
        "median_fill_tte_s": (round(_median(ttes), 1) if ttes else None),
        "labels": LABELS,
    }
