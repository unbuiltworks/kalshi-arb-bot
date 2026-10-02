"""Side models S0-S5, frozen. Each returns (p_up, detail) with p_up in [0,1].

No settlement input. No future print. ABSTAIN is handled by the caller when the
winning side's probability falls below config.ABSTAIN_BELOW.
"""
from __future__ import annotations
from . import config as C

_PRIOR_UP = 0.50          # empirical side base rate; frozen, not fitted here


def _clip(p):
    return max(0.01, min(0.99, p))


def s0_prior(s):
    return _PRIOR_UP, {"basis": "empirical side prior"}


def s1_flow(s):
    """Opposite recent taker pressure: if takers are buying Up, favour Down.

    Definition unchanged. Source per defect D1: the public trade tape lags
    minutes, so unlagged book-delta consumption is preferred when available and
    the lagged tape is the fallback. Which source was used is recorded.
    """
    sh, src = s.get("bookflow_up_share"), "bookflow"
    if sh is None:
        sh, src = s.get("taker_up_share_60"), "trade_tape"
    if sh is None:
        return 0.5, {"reason": "no flow", "source": None}
    return _clip(1.0 - sh), {"up_share": sh, "source": src,
                             "trade_tape_lag_s": s.get("trade_tape_lag_s")}


def s2_cheap_h1(s):
    """H1: favour the outcome that has cheapened against L5's own basis.
    Uses the executable ask (what acquisition would cost) versus his basis."""
    sc = {}
    for tag, key in (("up", "l5_obs_up_basis"), ("dn", "l5_obs_dn_basis")):
        ask, basis = s.get(f"{tag}_ask"), s.get(key)
        sc[tag] = (basis - ask) if (ask is not None and basis is not None) else None
    if sc["up"] is None and sc["dn"] is None:
        return 0.5, {"reason": "no basis yet"}
    u = sc["up"] if sc["up"] is not None else 0.0
    d = sc["dn"] if sc["dn"] is not None else 0.0
    return _clip(0.5 + 2.0 * (u - d)), {"cheapness": sc}


def s3_paircost_h3(s):
    """H3: the side whose passive acquisition most improves matched-pair basis."""
    pc = {}
    for tag, other in (("up", "dn"), ("dn", "up")):
        bid = s.get(f"{tag}_bid")
        ob = s.get(f"l5_obs_{other}_basis")
        pc[tag] = (bid + ob) if (bid is not None and ob is not None) else None
    if pc["up"] is None and pc["dn"] is None:
        return 0.5, {"reason": "no complement basis"}
    u = pc["up"] if pc["up"] is not None else 9.9
    d = pc["dn"] if pc["dn"] is not None else 9.9
    return _clip(0.5 + 2.0 * (d - u)), {"projected_pair_cost": pc}


def s4_h1_h3(s):
    a, da = s2_cheap_h1(s)
    b, db = s3_paircost_h3(s)
    return _clip(0.5 * (a + b)), {"h1": da, "h3": db}


def s5_full(s):
    """H1 + H3 + run persistence + recent flow + time. Few features by design."""
    p, det = s4_h1_h3(s)
    adj = 0.0
    if s.get("l5_prev_up") is not None and s.get("l5_run"):
        lean = 0.02 * min(s["l5_run"], 5)
        adj += lean if s["l5_prev_up"] == 1 else -lean
    sh = s.get("taker_up_share_60")
    if sh is not None:
        adj += 0.10 * (0.5 - sh)
    tts = s.get("secs_to_settle")
    if tts is not None and tts < 120:
        adj *= 0.5                      # late-window: lean less on persistence
    return _clip(p + adj), {**det, "adj": adj}


REGISTRY = {"S0_PRIOR": s0_prior, "S1_FLOW": s1_flow, "S2_CHEAP_H1": s2_cheap_h1,
            "S3_PAIRCOST_H3": s3_paircost_h3, "S4_H1_H3": s4_h1_h3, "S5_FULL": s5_full}


def predict_all(s):
    out = {}
    for name, fn in REGISTRY.items():
        p_up, det = fn(s)
        side = "up" if p_up >= 0.5 else "dn"
        conf = p_up if side == "up" else 1.0 - p_up
        out[name] = {"p_up": p_up, "side": side, "confidence": conf,
                     "abstain": conf < C.ABSTAIN_BELOW, "detail": det}
    return out
