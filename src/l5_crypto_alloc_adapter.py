"""Shared inventory allocation for the crypto up/down markets (BTC, ETH, SOL,
XRP): an ADAPTER around the existing controller, not a second engine.

`l5_btc5m_controller.decide` stays the execution engine: it proposes the
orders (entries, extensions, completions, cancels) under its own caps. This
module only JUDGES the PLACE orders that add exposure, with the same
market-agnostic allocator the sports lane uses (src/pmus_directional_alloc.py,
identical file), and returns the decision with those orders kept, resized or
removed. It never adds an order, raises a price or size, or touches a
completion or a cancel.

PROBABILITY. `probability_band` is the settlement-aligned digital model the
controller already uses (`p_up_from_spot`: Phi((ref - strike) / (vol * sqrt
(tau)))) with an UNCERTAINTY BAND:
  * volatility: vol x (1 - vol_uncertainty) and x (1 + vol_uncertainty);
  * reference freshness: the reference may have moved vol * sqrt(age) since
    it was read, in either direction; a reference older than max_ref_age_s
    is refused (no probability = no change).
UP is the allocator's YES side, DOWN its NO side.

Symbols: SYMBOLS gives each coin's market prefix and a per-second relative
volatility floor; a caller with a measured volatility passes it instead.
"""
from __future__ import annotations

import math
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import pmus_directional_alloc as DA

ONE, ZERO = D(1), D(0)

SYMBOLS: Dict[str, Dict[str, Any]] = {
    # sigma_rel_floor: minimum volatility per second as a fraction of price
    "BTC": {"prefix": "cpc-btc-", "sigma_rel_floor": 3.5e-5},
    "ETH": {"prefix": "cpceth-eth-", "sigma_rel_floor": 5.0e-5},
    "SOL": {"prefix": "cpcsol-sol-", "sigma_rel_floor": 7.0e-5},
    "XRP": {"prefix": "cpcxrp-xrp-", "sigma_rel_floor": 7.0e-5},
}

DEFAULTS: Dict[str, Any] = {
    "vol_uncertainty": 0.25,
    "max_ref_age_s": 3.0,
    "fill_loss": "0.01",
}

SIDE_MAP = {"UP": "YES", "DOWN": "NO"}

#: sha256 of src/pmus_directional_alloc.py as shared with the sports lane
#: (dev/pmus-directional-allocation); a test fails if the copies diverge.
ALLOCATOR_SHA256 = "5b8b7e0b8e2518c79523a380408fb247a77f246102819d03efe24902d5b50eaa"


def symbol_of(slug: str) -> Optional[str]:
    for sym, m in SYMBOLS.items():
        if str(slug).startswith(m["prefix"]):
            return sym
    return None


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def probability_band(*, reference: Any, strike: Any, tau_s: Any,
                     vol_per_sqrt_s: Any = None, symbol: str = "BTC",
                     ref_age_s: Any = 0.0,
                     cfg: Optional[Mapping[str, Any]] = None
                     ) -> Optional[Dict[str, Any]]:
    """The allocator's probability band for UP (= YES), or None.

    vol_per_sqrt_s: index dollars per sqrt-second; None = the symbol's
    floor x reference. tau_s <= 0 (decided) or any unreadable input = None:
    the allocator then changes nothing."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    try:
        s, k, t = float(reference), float(strike), float(tau_s)
        age = max(0.0, float(ref_age_s or 0.0))
    except (TypeError, ValueError):
        return None
    if not (s > 0 and k > 0) or t <= 0:
        return None
    if age > float(c["max_ref_age_s"]):
        return None
    v = float(vol_per_sqrt_s) if vol_per_sqrt_s is not None else \
        SYMBOLS.get(symbol, SYMBOLS["BTC"])["sigma_rel_floor"] * s
    if not v > 0:
        return None
    u = float(c["vol_uncertainty"])
    drift = v * math.sqrt(age)          # how far the reference can have moved
    vals = []
    for vv in (v * (1 - u), v, v * (1 + u)):
        for ds in (-drift, 0.0, drift):
            vals.append(_phi((s + ds - k) / (vv * math.sqrt(t))))
    p = _phi((s - k) / (v * math.sqrt(t)))
    eps = 1e-4
    lo, hi = max(eps, min(vals)), min(1 - eps, max(vals))
    p = min(max(p, lo), hi)
    q = D("0.000001")
    return DA.probability(D(repr(p)).quantize(q), D(repr(lo)).quantize(q),
                          D(repr(hi)).quantize(q),
                          source="digital:%s" % symbol,
                          age_s=None)


def filter_decision(decision: Mapping[str, Any], *,
                    inventory: Mapping[str, Mapping[str, Any]],
                    prob: Optional[Mapping[str, Any]],
                    tick: Any = "0.01", min_qty: Any = "1",
                    fill_loss: Any = None,
                    alloc_cfg: Optional[Mapping[str, Any]] = None
                    ) -> Dict[str, Any]:
    """The controller's decision with its exposure-adding PLACE orders judged
    by the shared allocator. An order on the lighter side (it reduces the
    unpaired position: a completion) and every non-PLACE op pass unchanged.
    With no probability the decision is returned unchanged."""
    out = dict(decision)
    orders = list(decision.get("orders") or [])
    rec: Dict[str, Any] = {"active": prob is not None, "judged": []}
    if prob is None:
        rec["why"] = "no_probability"
        out["allocation"] = rec
        return out
    c = dict(DEFAULTS)
    fl = D(str(fill_loss if fill_loss is not None else c["fill_loss"]))
    held = {SIDE_MAP[s]: D(str((inventory.get(s) or {}).get("qty") or 0))
            for s in ("UP", "DOWN")}
    kept = []
    for o in orders:
        if o.get("op", "PLACE") != "PLACE" or o.get("side") not in SIDE_MAP:
            kept.append(o)
            continue
        sd = SIDE_MAP[o["side"]]
        other = "NO" if sd == "YES" else "YES"
        if held[sd] < held[other]:
            kept.append(o)                       # risk-reducing: unchanged
            rec["judged"].append({"side": o["side"], "why": "reduces"})
            continue
        if not (alloc_cfg or {}).get("judge_openings", True) and                 held[sd] == ZERO:
            kept.append(o)                       # opening: not judged
            rec["judged"].append({"side": o["side"], "why": "opening"})
            continue
        a = DA.assess(prob=prob, side=sd, px=o["price"], qty=o["qty"],
                      pos=held, fill_loss=fl, min_qty=min_qty,
                      step=D(str(min_qty)), cfg=alloc_cfg)
        rec["judged"].append({"side": o["side"], "px": str(o["price"]),
                              "asked": str(o["qty"]), "qty": a["qty"],
                              "ev_per_contract": a.get("ev_per_contract"),
                              "why": a.get("why")})
        q = D(a["qty"])
        if q <= ZERO:
            continue
        kept.append(dict(o, qty=q) if q != D(str(o["qty"])) else o)
        held[sd] += q
    out["orders"] = kept
    out["allocation"] = rec
    return out


def probability_from_model(p_up: Mapping[str, Any], *,
                           index_age_s: Any = 0.0,
                           cfg: Optional[Mapping[str, Any]] = None
                           ) -> Optional[Dict[str, Any]]:
    """Band from the settlement-aligned reference model's own output
    (l5_btc_reference: `distance_to_strike` of the expected settlement and
    its standard deviation `sd`, `sigma_per_second`), the form the
    controller receives as p_up. The model's value is the centre; the band
    is sd x (1 -/+ vol_uncertainty) and the reference moving
    sigma_per_second x sqrt(age). None when the model is not ok."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    try:
        if not p_up.get("ok") or p_up.get("value") is None:
            return None
        dist, sd = float(p_up["distance_to_strike"]), float(p_up["sd"])
        sig = float(p_up.get("sigma_per_second") or 0.0)
        age = max(0.0, float(index_age_s or 0.0))
        val = float(p_up["value"])
    except (TypeError, ValueError, KeyError):
        return None
    if not sd > 0 or age > float(c["max_ref_age_s"]):
        return None
    u = float(c["vol_uncertainty"])
    drift = sig * math.sqrt(age)
    vals = [_phi((dist + ds) / s_) for s_ in (sd * (1 - u), sd, sd * (1 + u))
            for ds in (-drift, 0.0, drift)]
    eps = 1e-4
    lo, hi = max(eps, min(vals)), min(1 - eps, max(vals))
    val = min(max(val, lo), hi)
    q = D("0.000001")
    return DA.probability(D(repr(val)).quantize(q), D(repr(lo)).quantize(q),
                          D(repr(hi)).quantize(q), source="reference_model")
