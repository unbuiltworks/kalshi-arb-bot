"""POST-FIRST-FILL: what does L5 do on Polymarket that we cannot do on Gemini?

Gemini's initial fills leak. They EXTEND and never complete. L5's, on the same
underlying and the same window length, evidently do not -- so the question is
what MEASURABLY differs.

THE OBSERVABILITY IS NOT SYMMETRIC, and pretending otherwise would make every
number here a lie:

    Polymarket / L5   we see L5's OWN FILLS and nothing else. No book, no
                      resting quote, no queue, no cancels. Every "quote
                      persistence" statement is therefore an INFERENCE from
                      fill spacing, never an observation.
    Gemini / us       we see the BOOK and the whole public tape, but our fills
                      are HYPOTHETICAL -- reconstructed against a book our own
                      size would have moved.

So the comparison is restricted to what BOTH sides genuinely expose: the
sequence, timing, side, price and TTE of fills. Anything else is named as an
inference or left out.

L5 buys. 71,982 of 71,984 recorded trades are BUYs, so a complement is an
opposite-outcome BUY and never a sale of the held side -- the same shape our
own frozen policy uses.

Nothing here is realized. L5's P&L is not reconstructed and is not claimed.
"""
from __future__ import annotations

import csv
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

UP, DOWN = "Up", "Down"


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def load(path: str, *, asset: str = "BTC",
         window_minutes: str = "5") -> List[Dict[str, Any]]:
    """L5's own fills, one asset and one window length at a time.

    BTC 5-minute is the only slice directly comparable with Gemini BTC5m.
    The 15-minute slice is far larger and is reported SEPARATELY rather than
    pooled, because window length is one of the candidate explanations and
    pooling it would destroy the very contrast being measured.
    """
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if asset and r.get("asset") != asset:
                continue
            if window_minutes and r.get("window_minutes") != window_minutes:
                continue
            ts, px = _dec(r.get("timestamp")), _dec(r.get("price"))
            sh, tte = _dec(r.get("shares")), _dec(r.get("seconds_to_expiry"))
            if None in (ts, px, sh):
                continue
            out.append({"window_id": r.get("window_id"),
                        "ts": ts, "outcome": r.get("outcome"),
                        "side": r.get("side"), "price": px, "shares": sh,
                        "tte_s": tte,
                        "slug": r.get("market_slug")})
    return out


def windows(fills: Iterable[Mapping[str, Any]]) -> Dict[str, List[Dict]]:
    by: Dict[str, List[Dict]] = {}
    for f in fills:
        by.setdefault(f["window_id"], []).append(dict(f))
    for k in by:
        by[k].sort(key=lambda f: f["ts"])
    return by


def sequence(fills: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """One window's post-first-fill path, in the SAME terms as the Gemini one.

    The first fill sets the held side. A later fill on the same outcome is an
    EXTEND, one on the opposite outcome is a COMPLETE. Matched units, residual
    and pair basis are computed exactly as the frozen Gemini inventory does,
    so the two venues' numbers mean the same thing.
    """
    if not fills:
        return {"computable": False, "why": "no fills"}
    first = fills[0]
    held = first["outcome"]
    up_q = up_c = dn_q = dn_c = D("0")

    def add(o, q, p):
        nonlocal up_q, up_c, dn_q, dn_c
        if o == UP:
            up_q += q
            up_c += q * p
        else:
            dn_q += q
            dn_c += q * p

    add(first["outcome"], first["shares"], first["price"])
    extends, completes = [], []
    first_comp = None
    ext_units_before = D("0")
    ext_usd_before = D("0")
    for f in fills[1:]:
        row = {"dt_s": float(f["ts"] - first["ts"]), "tte_s": f["tte_s"],
               "price": str(f["price"]), "shares": str(f["shares"])}
        if f["outcome"] == held:
            extends.append(row)
            if first_comp is None:
                ext_units_before += f["shares"]
                ext_usd_before += f["shares"] * f["price"]
        else:
            completes.append(row)
            if first_comp is None:
                first_comp = f
        add(f["outcome"], f["shares"], f["price"])

    matched = min(up_q, dn_q)
    resid = up_q - dn_q
    gross = up_c + dn_c
    avg_up = (up_c / up_q) if up_q > 0 else None
    avg_dn = (dn_c / dn_q) if dn_q > 0 else None
    basis = (avg_up + avg_dn) if (avg_up is not None and avg_dn is not None) \
        else None
    matched_usd = (matched * basis) if basis is not None else D("0")
    resid_usd = ((resid * avg_up) if resid > 0 and avg_up is not None
                 else ((-resid) * avg_dn) if resid < 0 and avg_dn is not None
                 else D("0"))
    return {
        "computable": True,
        "window_id": fills[0]["window_id"],
        "n_fills": len(fills),
        "held_side": held,
        "first_fill_tte_s": (float(first["tte_s"])
                             if first["tte_s"] is not None else None),
        "first_fill_price": str(first["price"]),
        "extend_fills": len(extends),
        "complement_fills": len(completes),
        "reached_complement": bool(completes),
        "seconds_to_first_complement": (float(first_comp["ts"] - first["ts"])
                                        if first_comp is not None else None),
        "complement_tte_s": (float(first_comp["tte_s"])
                             if first_comp is not None
                             and first_comp["tte_s"] is not None else None),
        "extend_units_before_complement": str(ext_units_before),
        "extend_usd_before_complement": str(ext_usd_before),
        "matched_units": str(matched), "residual_units": str(resid),
        "gross_filled_usd": str(gross), "matched_usd": str(matched_usd),
        "residual_usd": str(resid_usd),
        "matched_over_gross_usd": (str(round(matched_usd / gross, 4))
                                   if gross > 0 else None),
        "residual_over_gross_usd": (str(round(resid_usd / gross, 4))
                                    if gross > 0 else None),
        "effective_matched_pair_cost": (str(basis) if basis is not None
                                        else None),
        "inter_fill_seconds": [round(float(fills[i]["ts"] - fills[i - 1]["ts"]),
                                     1) for i in range(1, len(fills))],
        "labels": ("L5's OWN FILLS. No book, quote, queue or cancel is "
                   "observable, so nothing here is a statement about where L5 "
                   "was resting. Nothing is realized."),
    }


def _med(xs):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def census(seqs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The same aggregate the Gemini side reports, so they can be set side
    by side without translation."""
    ok = [s for s in seqs if s.get("computable")]
    if not ok:
        return {"windows": 0}
    reached = [s for s in ok if s["reached_complement"]]
    gross = sum(_dec(s["gross_filled_usd"]) or D("0") for s in ok)
    matched = sum(_dec(s["matched_usd"]) or D("0") for s in ok)
    resid = sum(_dec(s["residual_usd"]) or D("0") for s in ok)
    bases = sorted(b for b in (_dec(s["effective_matched_pair_cost"])
                               for s in ok) if b is not None)
    ext = sum(s["extend_fills"] for s in ok)
    comp = sum(s["complement_fills"] for s in ok)
    solo = [s for s in ok if s["n_fills"] == 1]
    return {
        "windows": len(ok),
        "windows_reaching_a_complement": len(reached),
        "complement_rate": round(len(reached) / len(ok), 4),
        "single_fill_windows": len(solo),
        "median_seconds_to_first_complement": _med(
            [s["seconds_to_first_complement"] for s in ok]),
        "median_first_fill_tte_s": _med([s["first_fill_tte_s"] for s in ok]),
        "median_complement_tte_s": _med([s["complement_tte_s"] for s in ok]),
        "median_fills_per_window": _med([s["n_fills"] for s in ok]),
        "median_inter_fill_s": _med(
            [x for s in ok for x in s["inter_fill_seconds"]]),
        "extend_fills": ext, "complement_fills": comp,
        "extend_to_complement_fill_ratio": (
            str(round(D(ext) / D(comp), 2)) if comp else
            ("INFINITE (%d extends, 0 complements)" % ext if ext else None)),
        "gross_filled_usd": str(gross), "matched_usd": str(matched),
        "residual_usd": str(resid),
        "matched_over_gross_usd": (str(round(matched / gross, 4))
                                   if gross > 0 else None),
        "residual_over_gross_usd": (str(round(resid / gross, 4))
                                    if gross > 0 else None),
        "median_effective_pair_cost": (str(bases[len(bases) // 2])
                                       if bases else None),
        "pairs_above_par": sum(1 for b in bases if b > D("1")),
        "labels": ("L5's own fills on Polymarket. Not realized, not a P&L, "
                   "and not a claim about where L5 was resting."),
    }


def contrast(l5: Mapping[str, Any], gem: Mapping[str, Any]) -> Dict[str, Any]:
    """What actually differs, named one candidate at a time.

    Each row states the candidate explanation, both venues' numbers and
    whether the evidence here can SEPARATE them. Several cannot be separated
    with what each venue exposes, and those say so instead of guessing.
    """
    rows = [
        {"candidate": "complement is reached at all",
         "l5": l5.get("complement_rate"),
         "gemini": gem.get("matched_pairs_over_initial_fills"),
         "separable": True},
        {"candidate": "time to first complement (s)",
         "l5": l5.get("median_seconds_to_first_complement"),
         "gemini": gem.get("median_seconds_to_complement_fill"),
         "separable": True},
        {"candidate": "EXTEND per COMPLETE",
         "l5": l5.get("extend_to_complement_fill_ratio"),
         "gemini": (str(round(D(gem.get("extend_fills") or 0)
                              / D(gem["complement_fills"]), 2))
                    if gem.get("complement_fills") else
                    "INFINITE (%s extends, 0 complements)"
                    % gem.get("extend_fills")),
         "separable": True},
        {"candidate": "TTE at the first fill (s)",
         "l5": l5.get("median_first_fill_tte_s"),
         "gemini": gem.get("median_first_fill_tte_s"),
         "separable": bool(gem.get("median_first_fill_tte_s") is not None),
         "why": (None if gem.get("median_first_fill_tte_s") is not None
                 else "no Gemini seed carries a readable TTE")},
        {"candidate": "TTE at the complement (s)",
         "l5": l5.get("median_complement_tte_s"),
         "gemini": gem.get("median_complement_tte_s"),
         "separable": bool(gem.get("median_complement_tte_s") is not None),
         "why": (None if gem.get("median_complement_tte_s") is not None
                 else ("no Gemini complement ever filled, so there is no TTE "
                       "to compare -- which is itself the finding"))},
        {"candidate": "matched dollars / gross dollars",
         "l5": l5.get("matched_over_gross_usd"),
         "gemini": gem.get("matched_over_gross_usd"),
         "separable": True},
        {"candidate": "effective pair basis",
         "l5": l5.get("median_effective_pair_cost"),
         "gemini": gem.get("median_effective_pair_cost"),
         "separable": True},
        {"candidate": "fills per window",
         "l5": l5.get("median_fills_per_window"),
         "gemini": None,
         "separable": False,
         "why": ("our Gemini fills are hypothetical and capped by the probe's "
                 "own envelope, so a fill COUNT is not comparable")},
        {"candidate": "quote persistence / queue behaviour",
         "l5": None, "gemini": gem.get("complement_reach_rate"),
         "separable": False,
         "why": ("Polymarket exposes L5's fills but never its resting quotes, "
                 "so persistence cannot be measured on the L5 side at all -- "
                 "only inferred from fill spacing")},
        {"candidate": "flow direction after the first fill",
         "l5": None, "gemini": None, "separable": False,
         "why": ("the Gemini tape gives aggressor direction; Polymarket's "
                 "L5-only extract does not expose the rest of the tape, so "
                 "the two cannot be compared on this axis")},
    ]
    for r in rows:
        if not r["separable"] and not r.get("why"):
            r["why"] = "not comparable with what the two venues expose"
    return {"rows": rows,
            "separable": [r["candidate"] for r in rows if r["separable"]],
            "not_separable": [r["candidate"] for r in rows
                              if not r["separable"]],
            "labels": ("the observability is ASYMMETRIC and every row that "
                       "cannot be separated says so rather than guessing.")}
