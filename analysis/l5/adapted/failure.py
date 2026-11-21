"""Failure decomposition for ADAPTED_RESIDUAL_CAP_PROSPECTIVE_V0 = FAIL.

WHY THE ARMS FAILED IS NOT THE SAME QUESTION AS WHETHER THEY FAILED. The gate
answered the second. This answers the first, on the FROZEN sample only, and it
tunes nothing: every diagnostic threshold below is a fixed sweep reported in
full, never a search for the value that scores best.

THE QUESTION IT EXISTS TO SETTLE: is cap5's -$110 of residual loss

    BROAD_LEAKAGE   a little lost in most windows, or
    TAIL_BLOWUPS    a handful of windows destroying otherwise sound economics
    MIXED           neither cleanly

classified by a rule fixed BEFORE the numbers are seen:
    top 3 losing windows >= 60% of all residual loss  -> TAIL_BLOWUPS
    no single window >= 20% of it                     -> BROAD_LEAKAGE
    otherwise                                         -> MIXED

THE DIAGNOSTICS ARE NOT CANDIDATE ARMS. They are counterfactual probes over
already-collected events, run to find the STATE VARIABLE responsible for the
damage. Each reports how much matched edge survives beside how much residual
loss disappears, because a probe that removes the loss by removing the business
has explained nothing.

ORACLE_FLAT_AFTER_MATCH is deliberately unrealistic: once meaningful two-sided
inventory exists it forbids any action that increases absolute residual. It is
an UPPER BOUND on the matched edge available under perfect residual control,
not a policy anyone could run.

THREE DEFECTS IN THE FIRST RUN OF THIS INSTRUMENT, corrected here. None changed
a threshold; all three changed what the output MEANT.

1. TTE read `resolved_at`, the wall-clock at which a quote's fill verdict was
   decided -- which lands at or after the window closes. Every tte was <= 0, so
   all three stops blocked everything and collapsed into one probe wearing four
   names alongside the oracle. It now reads `placed_at`: the action being
   filtered is the PLACEMENT, so that is both the correct semantic and the only
   clock that varies within a window.

2. `residual_loss_removed` divided by the NET residual P&L. The net is losses
   plus wins, so the ratio exceeded 1 and printed "171% of the loss removed" --
   not a quantity that can exist. Loss and gain are now carried separately and
   the fraction is taken against the loss alone.

3. Nothing distinguished risk control from a directional re-roll. Residual P&L
   here is a PURE directional bet -- its sign is determined entirely by whether
   the side left over won -- so a probe that changes which side accumulates can
   post a large gain while reducing no risk at all. Every probe now reports the
   residual win rate and the surviving exposure beside the P&L, and identical
   probes are fingerprinted rather than counted twice as agreeing evidence.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Optional, Sequence

from analysis.l5.adapted.collect import SOURCES, _rows, _strict, _resolutions
from src.l5_shadow import adapted_policy as AP
from src.l5_shadow import ledger as LG

LABEL = "ADAPTED_RESIDUAL_CAP_PROSPECTIVE_V0"
WINDOW_S = 900
MEANINGFUL_MATCHED = D("1")      # "two-sided inventory exists" for the oracle

# fixed sweeps. reported in full; none is selected as a threshold.
MAX_RESIDUAL_USD = (5.0, 10.0, 25.0, 50.0)
MAX_IMBALANCE_RATIO = (0.10, 0.25, 0.50)
MATCHED_EDGE_X = (0.5, 1.0, 2.0)
PROB_EXTREMES = (0.10, 0.20, 0.30)
TTE_STOPS = (600.0, 300.0, 120.0)


def _end(mk: str) -> Optional[int]:
    try:
        return int(str(mk).rsplit("-", 1)[1]) + WINDOW_S
    except Exception:                                         # noqa: BLE001
        return None


def events(out_dir: str, *, cohort_prefix: str = "btc-updown-15m"):
    """(window, venue) -> chronological strict-evidenced fills."""
    by: Dict[tuple, List[dict]] = {}
    for r in _rows(out_dir):
        mk = str(r.get("market_key") or "")
        if cohort_prefix and not mk.startswith(cohort_prefix):
            continue
        if not _strict(r):
            continue
        by.setdefault((mk, r.get("venue")), []).append(r)
    return by


def walk(fills: Sequence[dict], *, arm: str, mk: str,
         accept: Optional[Callable[[dict, dict], bool]] = None) -> Dict[str, Any]:
    """Replay one window under one arm, recording every residual-increasing act."""
    st = AP.new_state()
    end = _end(mk)
    creations: List[Dict[str, Any]] = []
    max_abs = 0.0
    max_signed = 0.0
    matched_edge_before_first_resid: Optional[float] = None
    resid_after_two_sided = 0.0
    taken = 0
    for r in fills:
        side = "Up" if str(r.get("desired_outcome")) == "Up" else "Down"
        if arm != "control" and AP.withheld(st, arm=arm) == side:
            continue
        q = D(str(r.get("filled_qty") or r.get("quote_qty") or "0"))
        px = D(str(r.get("quote_price") or "0"))
        if q <= 0:
            continue
        up, dn = st["up_qty"], st["dn_qty"]
        b_resid = up - dn
        b_matched = min(up, dn)
        uv = (st["up_cost"] / up) if up > 0 else None
        dv = (st["dn_cost"] / dn) if dn > 0 else None
        pair = (uv + dv) if (uv is not None and dv is not None) else None
        same_v = uv if side == "Up" else dv
        n_up = up + (q if side == "Up" else 0)
        n_dn = dn + (q if side == "Down" else 0)
        a_resid = n_up - n_dn
        grows = abs(a_resid) > abs(b_resid)
        # TIME-TO-EXPIRY IS MEASURED AT THE DECISION, NOT AT THE VERDICT.
        # `resolved_at` is the wall-clock at which the quote's fill verdict was
        # DECIDED, which is at or after the window closes -- reading it here made
        # tte <= 0 for every fill, so every TTE stop blocked everything and the
        # three thresholds collapsed onto each other and onto the oracle. The
        # action being filtered is the PLACEMENT, so `placed_at` is both the
        # correct semantic and the only one that varies.
        t = r.get("placed_at")
        tte = ((end - t) if (end is not None and t is not None) else None)
        state = {
            "grows_residual": grows,
            "resid_before": float(b_resid), "resid_after": float(a_resid),
            "abs_resid_after": abs(float(a_resid)),
            "matched_before": float(b_matched),
            "matched_edge_before": (float(b_matched * (1 - pair))
                                    if pair is not None else None),
            "pair_before": (float(pair) if pair is not None else None),
            "run_len": st["run_len"], "side": side,
            "exec_price": float(px),
            "same_side_vwap": (float(same_v) if same_v is not None else None),
            "price_minus_vwap": (float(px - same_v) if same_v is not None
                                 else None),
            "tte_s": tte, "qty": float(q), "usd": float(q * px),
            "after_two_sided": bool(b_matched > 0),
        }
        if accept is not None and not accept(r, state):
            continue
        if grows:
            creations.append(state)
            if state["after_two_sided"]:
                resid_after_two_sided += float(q)
            if matched_edge_before_first_resid is None:
                matched_edge_before_first_resid = state["matched_edge_before"]
        st = AP.on_evidenced_fill(st, side=side, qty=q, cost=q * px)
        taken += 1
        cur = float(st["up_qty"] - st["dn_qty"])
        if abs(cur) > max_abs:
            max_abs, max_signed = abs(cur), cur
    return {"state": st, "creations": creations, "fills_taken": taken,
            "max_abs_imbalance": max_abs, "max_signed_imbalance": max_signed,
            "residual_added_after_two_sided": resid_after_two_sided,
            "matched_edge_before_first_residual":
                matched_edge_before_first_resid}


def settle_walk(w: Dict[str, Any], resolution: Optional[dict],
                venue: str, mk: str) -> Dict[str, Any]:
    st = w["state"]
    up, dn = st["up_qty"], st["dn_qty"]
    out = {"matched_qty": float(min(up, dn)),
           "residual_qty": float(abs(up - dn)),
           "settled": False, "matched_pnl": None, "residual_pnl": None,
           "total_pnl": None}
    if not (resolution and resolution.get("resolved")) or (up <= 0 and dn <= 0):
        return out
    pos = {"venue": venue, "cohort": "BTC15m", "market_key": mk,
           "clock": "venue", "n_fills": w["fills_taken"], "intent_ids": [],
           "up_qty": up, "dn_qty": dn, "up_cost": st["up_cost"],
           "dn_cost": st["dn_cost"], "fees": D("0")}
    s = LG.settle(pos, resolution)
    if s.get("state") != "SETTLED":
        return out
    out.update({"settled": True,
                "matched_pnl": float(s["matched_pair_pnl"]),
                "residual_pnl": float(s["residual_pnl"]),
                "total_pnl": float(s["realized_shadow"]),
                "winning_outcome": s.get("winning_outcome"),
                "residual_side": (None if up == dn
                                  else ("Up" if up > dn else "Down"))})
    return out


def classify(losses: Sequence[float]) -> str:
    """Fixed rule, written before the numbers were seen."""
    neg = sorted((abs(x) for x in losses if x < 0), reverse=True)
    tot = sum(neg)
    if not neg or tot <= 0:
        return "BROAD_LEAKAGE"
    top3 = sum(neg[:3]) / tot
    biggest = neg[0] / tot
    if top3 >= 0.60:
        return "TAIL_BLOWUPS"
    if biggest < 0.20:
        return "BROAD_LEAKAGE"
    return "MIXED"


# ------------------------------------------------------------- diagnostics

# EVERY PROBE APPLIES ONLY AFTER TWO-SIDED INVENTORY EXISTS. The opening fill
# of a window ALWAYS grows residual -- there is no other side yet -- so a probe
# that refuses residual growth unconditionally refuses the entry and reproduces
# the known bootstrap failure instead of explaining this one. A test pins that
# no probe blocks a matching fill.
def _post(s: dict) -> bool:
    return bool(s["grows_residual"] and s["after_two_sided"])


def _acc_max_residual(limit: float):
    return lambda r, s: not (_post(s)
                             and s["abs_resid_after"] * s["exec_price"] > limit)


def _acc_max_ratio(limit: float):
    def f(r, s):
        if not _post(s):
            return True
        tot = s["matched_before"] * 2 + s["abs_resid_after"]
        return tot <= 0 or (s["abs_resid_after"] / tot) <= limit
    return f


def _acc_after_edge(mult: float):
    """Stop extending once the matched edge already earned covers X times the
    dollars the extension would put at risk."""
    def f(r, s):
        if not _post(s):
            return True
        edge = s["matched_edge_before"]
        if edge is None or edge <= 0:
            return True
        return s["usd"] * mult <= edge
    return f


def _acc_prob_extreme(band: float):
    return lambda r, s: not (_post(s)
                             and (s["exec_price"] < band
                                  or s["exec_price"] > 1.0 - band))


def _acc_tte(stop: float):
    return lambda r, s: not (_post(s) and s["tte_s"] is not None
                             and s["tte_s"] < stop)


def _acc_oracle(r, s):
    """ORACLE_FLAT_AFTER_MATCH: once two-sided, never grow absolute residual."""
    return not (s["grows_residual"]
                and s["matched_before"] >= float(MEANINGFUL_MATCHED))
    # (the oracle already carries its own post-match condition)


DIAGNOSTICS: Dict[str, Callable] = {}
for _v in MAX_RESIDUAL_USD:
    DIAGNOSTICS[f"MAX_RESIDUAL_${_v:g}"] = _acc_max_residual(_v)
for _v in MAX_IMBALANCE_RATIO:
    DIAGNOSTICS[f"MAX_IMBALANCE_RATIO_{_v:g}"] = _acc_max_ratio(_v)
for _v in MATCHED_EDGE_X:
    DIAGNOSTICS[f"STOP_EXTENSION_AFTER_MATCHED_EDGE_{_v:g}x"] = _acc_after_edge(_v)
for _v in PROB_EXTREMES:
    DIAGNOSTICS[f"STOP_EXTENSION_AT_PROBABILITY_EXTREMES_{_v:g}"] = _acc_prob_extreme(_v)
for _v in TTE_STOPS:
    DIAGNOSTICS[f"STOP_EXTENSION_BY_TTE_{_v:g}s"] = _acc_tte(_v)
DIAGNOSTICS["ORACLE_FLAT_AFTER_MATCH"] = _acc_oracle


def _concentration(losses: Sequence[float]) -> Dict[str, Any]:
    neg = sorted((abs(x) for x in losses if x < 0), reverse=True)
    tot = sum(neg)
    if not neg or tot <= 0:
        return {"total_residual_loss": 0.0, "n_losing": 0,
                "top1_share_of_loss": None, "top3_share_of_loss": None,
                "top5_share_of_loss": None}
    return {"total_residual_loss": round(-tot, 4), "n_losing": len(neg),
            "worst_window_loss": round(-neg[0], 4),
            "top1_share_of_loss": round(neg[0] / tot, 4),
            "top3_share_of_loss": round(sum(neg[:3]) / tot, 4),
            "top5_share_of_loss": round(sum(neg[:5]) / tot, 4),
            "median_losing_window": round(-neg[len(neg) // 2], 4)}


def _split(rows: Sequence[dict]) -> Dict[str, Any]:
    """Residual P&L is a NET of two large opposing quantities. Reporting only
    the net hides whether a probe removed losses or merely re-rolled the
    direction, so loss and gain are always carried separately."""
    s = [r for r in rows if r["settled"]]
    loss = sum(r["residual_pnl"] for r in s if r["residual_pnl"] < 0)
    gain = sum(r["residual_pnl"] for r in s if r["residual_pnl"] > 0)
    nl = sum(1 for r in s if r["residual_pnl"] < 0)
    nw = sum(1 for r in s if r["residual_pnl"] > 0)
    return {"residual_loss": round(loss, 4), "residual_gain": round(gain, 4),
            "n_losing": nl, "n_winning": nw,
            "residual_win_rate": (round(nw / (nw + nl), 4) if (nw + nl) else None),
            "residual_qty": round(sum(r["residual_qty"] for r in rows), 2)}


def _by_venue(rows: Sequence[dict]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for v in sorted({r["venue"] for r in rows}):
        rs = [r for r in rows if r["venue"] == v]
        s = [r for r in rs if r["settled"]]
        losses = [r["residual_pnl"] for r in s]
        out[v] = {"windows": len(rs), "settled": len(s),
                  "matched_pnl": round(sum(r["matched_pnl"] for r in s), 4),
                  "residual_pnl": round(sum(r["residual_pnl"] for r in s), 4),
                  "FAILURE_MODE": classify(losses),
                  "concentration": _concentration(losses),
                  **_split(rs),
                  # a probe that only acts after two-sided inventory exists is
                  # INERT on a venue that never forms a pair; say so rather than
                  # letting those windows dilute every share reported above.
                  "windows_with_matched_inventory":
                      sum(1 for r in rs if r["matched_qty"] > 0)}
    return out


def report(out_dir: str, *, arm: str = "cap5") -> Dict[str, Any]:
    ev = events(out_dir)
    res = _resolutions(out_dir, sorted({mk for mk, _ in ev}))
    base: List[Dict[str, Any]] = []
    for (mk, venue), fills in sorted(ev.items()):
        w = walk(fills, arm=arm, mk=mk)
        row = {"window": mk, "venue": venue, **settle_walk(w, res.get(mk),
                                                           venue, mk)}
        row.update({
            "fills_taken": w["fills_taken"],
            "residual_creations": len(w["creations"]),
            "max_abs_imbalance": w["max_abs_imbalance"],
            "max_signed_imbalance": w["max_signed_imbalance"],
            "residual_added_after_two_sided":
                w["residual_added_after_two_sided"],
            "matched_edge_before_first_residual":
                w["matched_edge_before_first_residual"],
            "residual_dollars_entering": round(
                sum(c["usd"] for c in w["creations"] if c["grows_residual"]), 4),
            "creations_before_two_sided":
                sum(1 for c in w["creations"] if not c["after_two_sided"]),
            "creations_after_two_sided":
                sum(1 for c in w["creations"] if c["after_two_sided"]),
        })
        base.append(row)
    settled = [r for r in base if r["settled"]]
    b_match = sum(r["matched_pnl"] for r in settled)
    b_resid = sum(r["residual_pnl"] for r in settled)
    losses = [r["residual_pnl"] for r in settled]
    b_split = _split(base)
    b_loss = b_split["residual_loss"]
    diag: Dict[str, Any] = {}
    prints: Dict[tuple, str] = {}
    for name, acc in DIAGNOSTICS.items():
        rows, taken = [], []
        for (mk, venue), fills in sorted(ev.items()):
            w = walk(fills, arm=arm, mk=mk, accept=acc)
            rows.append(settle_walk(w, res.get(mk), venue, mk))
            taken.append((mk, venue, w["fills_taken"]))
        s = [r for r in rows if r["settled"]]
        m = sum(r["matched_pnl"] for r in s)
        rr = sum(r["residual_pnl"] for r in s)
        sp = _split(rows)
        # TWO PROBES THAT ACCEPT THE SAME FILLS ARE ONE PROBE. Reporting them as
        # independent evidence is how a mislabelled or vacuous threshold gets
        # read as confirmation; the fingerprint names the duplicate instead.
        fp = tuple(taken)
        dup = prints.get(fp)
        prints.setdefault(fp, name)
        diag[name] = {
            "matched_pnl": round(m, 4), "residual_pnl": round(rr, 4),
            "total_pnl": round(m + rr, 4),
            "matched_edge_retained": (round(m / b_match, 4)
                                      if b_match else None),
            # a TRUE fraction of the LOSS removed, against the loss alone and
            # never against the net -- the net is losses plus wins, so dividing
            # by it produced "171% of the loss removed", which is not a
            # quantity that can exist.
            "residual_loss_removed": (round(1 - (sp["residual_loss"] / b_loss), 4)
                                      if b_loss else None),
            "residual_gain_kept": (round(sp["residual_gain"]
                                         / b_split["residual_gain"], 4)
                                   if b_split["residual_gain"] else None),
            **sp,
            "matched_qty": round(sum(r["matched_qty"] for r in rows), 2),
            "identical_to": dup,
        }
    return {
        "label": LABEL, "verdict": "FAIL", "arm": arm,
        "windows": len(base), "settled_windows": len(settled),
        "baseline": {"matched_pnl": round(b_match, 4),
                     "residual_pnl": round(b_resid, 4),
                     "total_pnl": round(b_match + b_resid, 4)},
        "losing_residual_windows": sum(1 for x in losses if x < 0),
        "winning_residual_windows": sum(1 for x in losses if x > 0),
        "flat_residual_windows": sum(1 for x in losses if x == 0),
        "FAILURE_MODE": classify(losses),
        "concentration": _concentration(losses),
        "baseline_split": b_split,
        "by_venue": _by_venue(base),
        "residual_sign_is_pure_direction": all(
            (r["residual_pnl"] < 0)
            == (r.get("residual_side") != r.get("winning_outcome"))
            for r in settled if r["residual_qty"] > 0),
        "window_rows": sorted(base, key=lambda r: (r["residual_pnl"]
                                                   if r["residual_pnl"]
                                                   is not None else 0)),
        "diagnostics": diag,
        "labels": ("diagnostic counterfactuals over the FROZEN sample; not "
                   "candidate arms, and no threshold is selected. Incentives "
                   "are excluded entirely."),
    }
