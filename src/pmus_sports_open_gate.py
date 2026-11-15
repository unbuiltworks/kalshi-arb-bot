"""Opening gate: open a two-sided quote only when its expected NET value,
measured from this bot's own SETTLED results, is positive.

EVIDENCE (offline ledger 2026-11-15, 682 venue-confirmed fills priced at the
venue's final settlementPx, 77 settled markets): markets that ended fully
paired made +$6.03 (40 markets); markets left with an unmatched leg lost
-$25.8 (37 markets) -- and 0 of those 37 legs won, against ~18 implied by
their own prices. That is the structure of the recovery policy, not bad
luck: a leg the market moves FOR is cheap to complete and becomes a pair; a
leg the market moves AGAINST is too expensive to complete and is held to
zero. So every opening carries a ~48% chance of a loss of about its own
price, against a pair margin of a few cents. Quoting both sides does not
mean both fill.

    EV(open) = P(pair) * margin_per_market
             - P(residual) * loss_per_residual_market
             - fees_per_market

Each term is estimated per sport from settled markets, shrunk toward the
all-sport estimate with PRIOR_N pseudo-markets, so a sport with three
settled markets cannot open on a lucky streak. An opening is rejected when
EV <= MIN_EV. Pure; the engine supplies the settled rows.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, Mapping, Optional

PRIOR_N = D("10")


def market_outcome(mk: Mapping[str, Any]) -> Optional[Dict[str, D]]:
    """(pair margin, residual P&L, residual?) for one SETTLED market."""
    px = mk.get("settle_px")
    if px is None:
        return None
    px = D(str(px))
    y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
    if y == 0 and n == 0:
        return None
    cy, cn = D(mk["cost"]["YES"]), D(mk["cost"]["NO"])
    pairs = min(y, n)
    margin = pairs * (D(1) - (cy / y if y else 0) - (cn / n if n else 0)) \
        if pairs else D(0)
    total = y * px + n * (D(1) - px) - cy - cn
    return {"margin": margin, "residual_pnl": total - margin,
            "residual": D(1) if y != n else D(0), "total": total}


def sport_of(slug: str) -> str:
    parts = str(slug).split("-")
    return parts[1] if len(parts) > 2 else "?"


def stats(markets: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict]:
    """Per-sport and all-sport settled statistics."""
    acc: Dict[str, Dict[str, D]] = {}
    for slug, mk in markets.items():
        o = market_outcome(mk)
        if o is None:
            continue
        for key in (sport_of(slug), "*"):
            a = acc.setdefault(key, {"n": D(0), "res_n": D(0),
                                     "margin": D(0), "res_loss": D(0),
                                     "total": D(0), "fees": D(0)})
            a["n"] += 1
            a["res_n"] += o["residual"]
            a["margin"] += o["margin"]
            a["res_loss"] += -o["residual_pnl"] if o["residual"] else D(0)
            a["total"] += o["total"]
            a["fees"] += D(str(mk.get("fees_est") or "0"))
    return acc


def expected_open_value(sport: str, st: Mapping[str, Mapping[str, D]],
                        prior_n: D = PRIOR_N) -> Optional[Dict[str, Any]]:
    """EV per opened market for `sport`, shrunk to the all-sport figure.
    None when there is no settled evidence at all (caller decides)."""
    g = st.get("*")
    if not g or g["n"] == 0:
        return None
    s = st.get(sport) or {"n": D(0), "res_n": D(0), "margin": D(0),
                          "res_loss": D(0), "total": D(0), "fees": D(0)}

    def shrink(num_s, den_s, num_g, den_g):
        gm = num_g / den_g if den_g else D(0)
        return (num_s + prior_n * gm) / (den_s + prior_n)

    p_res = shrink(s["res_n"], s["n"], g["res_n"], g["n"])
    margin = shrink(s["margin"], s["n"], g["margin"], g["n"])
    # loss per RESIDUAL market (conditional), shrunk on residual counts
    loss = shrink(s["res_loss"], s["res_n"], g["res_loss"], g["res_n"]) \
        if g["res_n"] else D(0)
    fees = shrink(s["fees"], s["n"], g["fees"], g["n"])
    ev = margin - p_res * loss - fees
    return {"ev": ev, "p_residual": p_res, "margin": margin,
            "loss_if_residual": loss, "fees": fees, "n_sport": s["n"],
            "n_all": g["n"]}


def allow_open(sport: str, st: Mapping[str, Mapping[str, D]],
               min_ev: D = D("0"), min_markets: D = D("10")
               ) -> Dict[str, Any]:
    """Decision + reasons. Too little ALL-sport evidence -> allowed (the gate
    has nothing to say yet) and labelled as such."""
    g = st.get("*")
    if not g or g["n"] < min_markets:
        return {"allow": True, "why": "insufficient_settled_evidence",
                "n_all": str(g["n"]) if g else "0"}
    e = expected_open_value(sport, st)
    ok = e["ev"] > min_ev
    return {"allow": ok, "why": "positive_ev" if ok else "negative_ev",
            **{k: str(v) for k, v in e.items()}}
