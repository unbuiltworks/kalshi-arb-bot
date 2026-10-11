"""RESIDUAL_EXIT_FORENSICS_V1 -- how does L5 get OUT of directional imbalance?

OWNER 2026-09-17. Behavioral research. This module reads only L5's public
print tape and the redemption rows. It changes nothing in the frozen PMUS
sweep (`analysis/l5/sweep/`), proposes no policy and moves no money.

THE QUESTION. Every time L5's inventory leaves flat he is carrying directional
risk he did not have a moment earlier. The replication argument has been that
he is patient about closing it. Patient until when, and closing it how? This
reconstructs every such episode chronologically and segments it by the way it
ended.

WHAT AN EPISODE IS. BTC15m contains 693,916 fills and ZERO sells -- inventory
on each side only ever increases. So residual moves only by buying, and the
whole vocabulary collapses to four transitions:

    OPEN      residual leaves 0
    EXTEND    same-side buy; |residual| grows
    COMPLETE  opposite-side buy; |residual| shrinks
              -> COMPLETE_FLAT when it lands exactly on 0
              -> COMPLETE_PARTIAL when it does not
    SWITCH    opposite-side buy large enough to cross 0

An episode runs from OPEN to whichever of these ends it: COMPLETE_FLAT,
SWITCH (which ends one episode and opens the next in the same fill), or the
window settling with residual still on the book -- CARRIED_TO_SETTLEMENT.

WHAT IS NOT OBSERVABLE, and is named rather than imputed. Everything in
UNOBSERVABLE below. In particular there is NO historical book: the only prices
in this dataset are the prices of L5's OWN fills. A field called `p_up` here
means "the up-equivalent price of L5's last print", never "the mid" and never
"the touch". A quote he placed and did not fill leaves no trace at all, so
"L5 waited" and "L5 quoted and was not hit" are INDISTINGUISHABLE here.

TRAIN/OOS IS CHRONOLOGICAL, never random. A shuffled split would leak the
regime; these episodes are one account's behaviour evolving over weeks.
"""
from __future__ import annotations

import collections
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from analysis.l5.redemption import audit as A

QTY_TOL = 1e-9
TRUNCATION_S = 86400          # same capture-tail rule as the redemption audit
FEE_RATE = 0.07
BEARING_CUT = FEE_RATE * 0.5

#: named so a reader never mistakes an absence for a zero
UNOBSERVABLE = (
    "book state at any instant (bid, ask, spread, depth, imbalance)",
    "queue position, and therefore whether any quote would have filled",
    "L5's unfilled, resting or cancelled orders -- a quote that did not fill "
    "leaves NO trace, so 'L5 waited' and 'L5 quoted and was not hit' are the "
    "same observation here",
    "maker vs taker per fill (only fee-bearing vs fee-free is recoverable)",
    "L5's reward/rebate credits per window (not published for another account)",
    "any price at an instant L5 did not trade",
)


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _bearing(px: float, sz: float, usd: float) -> bool:
    den = px * (1.0 - px)
    if den <= 0 or sz <= 0:
        return False
    return ((usd - px * sz) / sz) / den > BEARING_CUT


# ---------------------------------------------------------------------------
# tape

def load_windows(cohort: str = "BTC15m") -> Tuple[Dict[str, dict], int]:
    """conditionId -> {fills, meta, redeem}. One pass over the activity cache."""
    win: Dict[str, dict] = {}
    capture_end = 0
    for r in A.iter_activity():
        w = A.window_of(r)
        if not w:
            continue
        if "%s%s" % (w[0].upper(), w[1]) != cohort:
            continue
        cid = str(r.get("conditionId") or "")
        if not cid:
            continue
        d = win.setdefault(cid, {"fills": [], "meta": w, "redeem": None})
        capture_end = max(capture_end, w[2])
        t = str(r.get("type") or "").upper()
        if t == "TRADE":
            side = str(r.get("side") or "").upper()
            px, sz, usd = _f(r.get("price")), _f(r.get("size")), _f(r.get("usdcSize"))
            if side != "BUY" or px <= 0 or sz <= 0:
                # BTC15m has no sells; anything else is flagged, never silently
                # folded into the inventory walk
                d.setdefault("nonbuy", 0)
                d["nonbuy"] += 1
                continue
            d["fills"].append((int(_f(r.get("timestamp"))), px,
                               "Up" if str(r.get("outcome")) == "Up" else "Down",
                               sz, usd))
        elif t == "REDEEM":
            rd = d["redeem"] or {"usd": 0.0, "outcome": None}
            rd["usd"] += _f(r.get("usdcSize"))
            rd["outcome"] = rd["outcome"] or str(r.get("outcome"))
            d["redeem"] = rd
    for d in win.values():
        d["fills"].sort(key=lambda x: x[0])
    return win, capture_end


def settlement(d: dict, capture_end: int) -> Tuple[Optional[bool], str]:
    """(won_up, source). Identical rule to the redemption audit: a redeem row
    is authoritative; inside the capture tail the answer is INDETERMINATE and
    is never guessed; otherwise a missing redeem row means the held side lost."""
    end = d["meta"][2]
    red = d["redeem"] or {}
    if red.get("outcome"):
        return red["outcome"] == "Up", "redeem_row"
    if (capture_end - end) < TRUNCATION_S:
        return None, "indeterminate_capture_tail"
    up = sum(x[3] for x in d["fills"] if x[2] == "Up")
    dn = sum(x[3] for x in d["fills"] if x[2] != "Up")
    return (dn > up), "no_redeem_implies_held_side_lost"


# ---------------------------------------------------------------------------
# episodes

def _classify(res_before: float, res_after: float) -> str:
    b, a = res_before, res_after
    if abs(a) <= QTY_TOL:
        return "COMPLETE_FLAT"
    if abs(b) <= QTY_TOL:
        return "OPEN"
    if (b > 0) != (a > 0):
        return "SWITCH"
    return "EXTEND" if abs(a) > abs(b) else "COMPLETE_PARTIAL"


def episodes_for_window(cid: str, d: dict, capture_end: int) -> List[dict]:
    """Walk one window's fills chronologically and cut it into episodes.

    SWITCH CONVENTION, stated rather than hidden: one fill can both close an
    imbalance and open the opposite one. Its shares are booked to the episode
    it CLOSES, and the new episode starts from the post-fill inventory. Any
    other choice double-counts the same shares in two episodes.
    """
    fills = d["fills"]
    if not fills:
        return []
    asset, horizon, end = d["meta"]
    dur = A.DURATION_S[horizon]
    start_t = end - dur
    won_up, src = settlement(d, capture_end)

    # whole-window accounting, on the SAME basis the btc15m decomposition uses
    # (usdcSize is fee-inclusive; price*size is not)
    up_q = sum(x[3] for x in fills if x[2] == "Up")
    dn_q = sum(x[3] for x in fills if x[2] != "Up")
    up_u = sum(x[4] for x in fills if x[2] == "Up")
    dn_u = sum(x[4] for x in fills if x[2] != "Up")
    avg_up = (up_u / up_q) if up_q else None
    avg_dn = (dn_u / dn_q) if dn_q else None
    w_matched = min(up_q, dn_q)
    w_residual = up_q - dn_q
    if won_up is None:
        w_matched_pnl = w_residual_pnl = None
    else:
        w_matched_pnl = (w_matched * (1.0 - (avg_up + avg_dn))
                         if (w_matched > 0 and avg_up is not None
                             and avg_dn is not None) else 0.0)
        if abs(w_residual) <= QTY_TOL:
            w_residual_pnl = 0.0
        else:
            r_up = w_residual > 0
            r_avg = (avg_up if r_up else avg_dn) or 0.0
            w_residual_pnl = ((abs(w_residual) if r_up == won_up else 0.0)
                              - abs(w_residual) * r_avg)
    w_ctx = {
        "window_won_up": won_up, "window_outcome_source": src,
        "window_matched_qty": w_matched, "window_residual_signed": w_residual,
        "window_matched_pnl": w_matched_pnl,
        "window_residual_pnl": w_residual_pnl, "window_n_fills": len(fills),
        "window_avg_up_cost": avg_up, "window_avg_dn_cost": avg_dn,
    }

    up = dn = upu = dnu = 0.0
    p_up_hist: List[Tuple[int, float]] = []   # (t, up-equivalent print price)
    last_up_px = last_dn_px = None
    run_side, run_len = None, 0

    out: List[dict] = []
    ep: Optional[dict] = None

    for i, (t, px, oc, sz, usd) in enumerate(fills):
        res_before = up - dn
        pre = (up, dn, upu, dnu)          # inventory BEFORE this fill
        p_up = px if oc == "Up" else 1.0 - px
        run_here = (run_len + 1) if run_side == oc else 1

        if oc == "Up":
            up += sz
            upu += usd
            last_up_px = px
        else:
            dn += sz
            dnu += usd
            last_dn_px = 1.0 - px
        res_after = up - dn
        kind = _classify(res_before, res_after)

        # ---- this fill is an ACTION inside an already-open episode. Record it
        # BEFORE any close, so an episode closed by its own next fill still has
        # that fill as its `next_action` rather than a silent None.
        if ep is not None:
            if ep["next_action"] is None:
                ep["next_action"] = kind
                ep["next_action_dt_s"] = t - ep["t"]
                ep["next_action_qty"] = sz
                ep["next_action_px_up_equiv"] = p_up
            if (ep["t_to_opposite_fill_s"] is None
                    and oc != ep["residual_side"]):
                ep["t_to_opposite_fill_s"] = t - ep["t"]
                ep["px_opposite_fill_up_equiv"] = p_up
            ep["max_subsequent_residual_abs"] = max(
                ep["max_subsequent_residual_abs"], abs(res_after))

        # ---- close
        if ep is not None and kind in ("COMPLETE_FLAT", "SWITCH"):
            ep["exit_mode"] = ("COMPLETED_FLAT" if kind == "COMPLETE_FLAT"
                               else "FLIPPED")
            ep["exit_t"] = t
            ep["exit_px_up_equiv"] = p_up
            ep["duration_s"] = t - ep["t"]
            ep["fills_in_episode"] = i - ep["_i0"] + 1
            _close_episode(ep, up, dn, upu, dnu, won_up, w_ctx)
            out.append(ep)
            ep = None

        # ---- open (a fresh OPEN, or the far side of the SWITCH just closed)
        if abs(res_after) > QTY_TOL and ep is None:
            dp3 = (p_up - p_up_hist[-3][1]) if len(p_up_hist) >= 3 else None
            dp60 = None
            for tt, pp in reversed(p_up_hist):
                if t - tt >= 60:
                    dp60 = p_up - pp
                    break
            fav = "Up" if p_up > 0.5 else ("Down" if p_up < 0.5 else "NONE")
            ep = {
                "cid": cid, "cohort": "%s%s" % (asset.upper(), horizon),
                "t": t, "window_start": start_t, "window_end": end, "_i0": i,
                # ---- observable at the instant the imbalance appeared
                "residual_signed": res_after, "residual_abs": abs(res_after),
                "residual_side": "Up" if res_after > 0 else "Down",
                "residual_rel": (abs(res_after) / (up + dn)) if (up + dn) else None,
                "opened_by": kind,
                "p_up_last_print": p_up, "favored": fav,
                "residual_is_favored": (fav != "NONE"
                                        and (res_after > 0) == (fav == "Up")),
                "dp_up_3_prints": dp3, "dp_up_60s": dp60,
                "same_side_run_len": run_here,
                "pair_cost_last_prints": ((last_up_px + last_dn_px)
                                          if (last_up_px is not None
                                              and last_dn_px is not None)
                                          else None),
                "pair_cost_realised_so_far": (((upu / up) + (dnu / dn))
                                              if (up > 0 and dn > 0) else None),
                "tte_s": end - t,
                "window_progress": ((t - start_t) / dur) if dur else None,
                "n_fills_so_far": i + 1,
                "open_fill_qty": sz, "open_fill_px": px,
                "open_fill_fee_bearing": _bearing(px, sz, usd),
                "matched_qty_so_far": min(up, dn),
                # ---- filled in as it plays out
                "next_action": None, "next_action_dt_s": None,
                "next_action_qty": None, "next_action_px_up_equiv": None,
                "t_to_opposite_fill_s": None,
                "px_opposite_fill_up_equiv": None,
                "max_subsequent_residual_abs": abs(res_after),
                "exit_mode": None, "exit_t": None, "exit_px_up_equiv": None,
                "duration_s": None, "fills_in_episode": None,
                # ACCOUNTING BASELINE. A fill that OPENS an episode from
                # flat belongs to that episode, so the baseline is the
                # inventory BEFORE it. A fill that SWITCHES was already booked
                # to the episode it closed, so the baseline is AFTER it.
                # Getting this backwards silently drops the opening leg and
                # leaves one-fill episodes with no pair cost at all.
                "_base": (pre if kind == "OPEN" else (up, dn, upu, dnu)),
            }
            ep.update(w_ctx)

        run_side, run_len = oc, run_here
        p_up_hist.append((t, p_up))

    if ep is not None:
        ep["exit_mode"] = "CARRIED_TO_SETTLEMENT"
        ep["exit_t"] = end
        ep["duration_s"] = end - ep["t"]
        ep["fills_in_episode"] = len(fills) - ep["_i0"]
        if ep["next_action"] is None:
            # no fill ever followed. INDISTINGUISHABLE from "quoted and never
            # hit": unfilled orders leave no trace in this dataset.
            ep["next_action"] = "NONE_CARRIED"
        _close_episode(ep, up, dn, upu, dnu, won_up, w_ctx)
        out.append(ep)
    return out


def _close_episode(ep: dict, up: float, dn: float, upu: float, dnu: float,
                   won_up: Optional[bool], w_ctx: dict) -> None:
    """Pair economics for the shares acquired WITHIN this episode only."""
    u0, d0, uu0, du0 = ep.pop("_base")
    du, dd = up - u0, dn - d0
    duu, ddu = upu - uu0, dnu - du0
    ep["episode_up_qty"], ep["episode_dn_qty"] = du, dd
    ep["episode_matched_qty"] = min(du, dd)
    ep["episode_pair_cost"] = (((duu / du) + (ddu / dd))
                               if (du > QTY_TOL and dd > QTY_TOL) else None)
    ep["episode_matched_pnl"] = (
        ep["episode_matched_qty"] * (1.0 - ep["episode_pair_cost"])
        if ep["episode_pair_cost"] is not None else None)
    ep.pop("_i0", None)

    if ep["exit_mode"] == "CARRIED_TO_SETTLEMENT":
        res = up - dn
        ep["settlement_residual_signed"] = res
        if won_up is None:
            ep["episode_residual_pnl"] = None
            ep["episode_residual_pnl_basis"] = "UNOBSERVABLE:" + str(
                w_ctx["window_outcome_source"])
        else:
            r_up = res > 0
            r_avg = (w_ctx["window_avg_up_cost"] if r_up
                     else w_ctx["window_avg_dn_cost"])
            ep["episode_residual_pnl"] = (
                ((abs(res) if r_up == won_up else 0.0) - abs(res) * r_avg)
                if (abs(res) > QTY_TOL and r_avg is not None) else 0.0)
            # the residual shares were acquired across the whole window, so the
            # only honest cost basis for them is the window's average
            ep["episode_residual_pnl_basis"] = "window_avg_cost_of_residual_side"
    else:
        ep["settlement_residual_signed"] = 0.0
        ep["episode_residual_pnl"] = None
        ep["episode_residual_pnl_basis"] = (
            "N/A: extinguished by trading, not by settlement -- the economics "
            "are in episode_pair_cost")

    ep["resolved_flat"] = ep["exit_mode"] == "COMPLETED_FLAT"
    ep["resolved_economically"] = bool(
        ep["resolved_flat"] and ep["episode_pair_cost"] is not None
        and ep["episode_pair_cost"] <= 1.0)


def build(cohort: str = "BTC15m") -> List[dict]:
    win, capture_end = load_windows(cohort)
    eps: List[dict] = []
    for cid, d in win.items():
        eps.extend(episodes_for_window(cid, d, capture_end))
    eps.sort(key=lambda e: (e["t"], e["cid"]))
    return eps


# ---------------------------------------------------------------------------
# segmentation and predictor search

def split_chrono(eps: Sequence[dict], frac: float = 0.7):
    e = sorted(eps, key=lambda x: x["t"])
    c = int(len(e) * frac)
    return e[:c], e[c:]


def _mean(xs: Sequence[Optional[float]]) -> Optional[float]:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def _med(xs: Sequence[Optional[float]]) -> Optional[float]:
    v = sorted(x for x in xs if x is not None)
    return v[len(v) // 2] if v else None


def segment(eps: Sequence[dict]) -> Dict[str, dict]:
    """By exit mode -- the owner's four comparison groups. FLIPPED is split by
    whether the new residual sits on the favored side, because 'switches toward
    the favored side' is a different claim from 'switches'."""
    g: Dict[str, List[dict]] = collections.defaultdict(list)
    for e in eps:
        k = e["exit_mode"]
        if k == "FLIPPED":
            tf = _flip_toward_favored(e)
            # p_up == 0.50 exactly is COMMON here and there is no favored side
            # at all then. Folding those into "away" would have manufactured a
            # tilt that is not in the tape.
            k = ("FLIPPED_NO_FAVORED_SIDE" if tf is None else
                 "FLIPPED_TOWARD_FAVORED" if tf else "FLIPPED_AWAY_FROM_FAVORED")
        g[k].append(e)
    out = {}
    for k, v in g.items():
        out[k] = {
            "n": len(v),
            "share": round(len(v) / len(eps), 4) if eps else None,
            "median_duration_s": _med([x["duration_s"] for x in v]),
            "median_residual_abs_at_open": _med([x["residual_abs"] for x in v]),
            "median_max_subsequent_residual": _med(
                [x["max_subsequent_residual_abs"] for x in v]),
            "median_tte_s_at_open": _med([x["tte_s"] for x in v]),
            "median_fills_in_episode": _med([x["fills_in_episode"] for x in v]),
            "opened_on_favored_side_share": round(
                sum(1 for x in v if x["residual_is_favored"]) / len(v), 4),
            "median_p_up_last_print": _med([x["p_up_last_print"] for x in v]),
            "median_pair_cost_last_prints": _med(
                [x["pair_cost_last_prints"] for x in v]),
            "median_episode_pair_cost": _med([x["episode_pair_cost"] for x in v]),
            "sum_episode_matched_pnl": sum(
                x["episode_matched_pnl"] or 0.0 for x in v),
            "sum_episode_residual_pnl": sum(
                x["episode_residual_pnl"] or 0.0 for x in v),
            "n_residual_pnl_determinate": sum(
                1 for x in v if x["episode_residual_pnl"] is not None),
            "median_t_to_opposite_fill_s": _med(
                [x["t_to_opposite_fill_s"] for x in v]),
            "n_no_opposite_fill_UNOBSERVABLE_WHY": sum(
                1 for x in v if x["t_to_opposite_fill_s"] is None),
            "next_action_mix": dict(collections.Counter(
                x["next_action"] for x in v)),
        }
    return out


def _flip_toward_favored(e: dict) -> Optional[bool]:
    """Did the flip land on the side the last print favored? The residual sign
    reverses, so the NEW side is the opposite of the episode's own side."""
    if e["favored"] == "NONE":
        return None
    new_side = "Down" if e["residual_side"] == "Up" else "Up"
    return new_side == e["favored"]


# ---- binning ---------------------------------------------------------------

def _bin(name: str, e: dict) -> Optional[str]:
    v = e.get(name)
    if v is None:
        return None
    if name == "residual_abs":
        for hi, lab in ((1, "<=1"), (5, "1-5"), (20, "5-20"), (100, "20-100")):
            if v <= hi:
                return lab
        return ">100"
    if name == "tte_s":
        for hi, lab in ((60, "<=60s"), (180, "60-180s"), (420, "180-420s"),
                        (700, "420-700s")):
            if v <= hi:
                return lab
        return ">700s"
    if name == "p_up_last_print":
        for hi, lab in ((0.1, "<=0.10"), (0.3, "0.10-0.30"),
                        (0.5, "0.30-0.50"), (0.7, "0.50-0.70"),
                        (0.9, "0.70-0.90")):
            if v < hi:
                return lab
        return ">=0.90"
    if name == "pair_cost_last_prints":
        for hi, lab in ((0.96, "<=0.96"), (0.99, "0.96-0.99"),
                        (1.00, "0.99-1.00"), (1.02, "1.00-1.02")):
            if v <= hi:
                return lab
        return ">1.02"
    if name == "same_side_run_len":
        return {1: "1", 2: "2", 3: "3"}.get(int(v), "4+")
    if name == "dp_up_60s":
        for hi, lab in ((-0.05, "<=-0.05"), (-0.01, "-0.05..-0.01"),
                        (0.01, "-0.01..0.01"), (0.05, "0.01..0.05")):
            if v <= hi:
                return lab
        return ">0.05"
    if name == "residual_is_favored":
        return "favored" if v else "against"
    if name == "favored":
        return str(v)
    if name == "window_progress":
        for hi, lab in ((0.25, "q1"), (0.5, "q2"), (0.75, "q3")):
            if v <= hi:
                return lab
        return "q4"
    return str(v)


FEATURES = ("residual_abs", "tte_s", "p_up_last_print", "pair_cost_last_prints",
            "same_side_run_len", "dp_up_60s", "residual_is_favored",
            "window_progress")


def conditional(eps: Sequence[dict], feature: str, target: str) -> Dict[str, dict]:
    g: Dict[str, List[dict]] = collections.defaultdict(list)
    for e in eps:
        b = _bin(feature, e)
        if b is not None:
            g[b].append(e)
    out = {}
    for b, v in g.items():
        if target == "resolved_flat":
            out[b] = {"n": len(v),
                      "rate": round(sum(1 for x in v if x["resolved_flat"])
                                    / len(v), 4)}
        elif target == "resolved_economically":
            out[b] = {"n": len(v),
                      "rate": round(sum(1 for x in v
                                        if x["resolved_economically"]) / len(v), 4)}
        elif target == "next_action":
            c = collections.Counter(x["next_action"] for x in v)
            out[b] = {"n": len(v),
                      "mix": {k: round(n / len(v), 4) for k, n in c.most_common()}}
        elif target == "p_extend":
            out[b] = {"n": len(v),
                      "rate": round(sum(1 for x in v
                                        if x["next_action"] == "EXTEND")
                                    / len(v), 4)}
    return out


MIN_BIN = 200      # a rate on a handful of episodes is not a predictor


def predictor_table(cohort: str = "BTC15m", eps: Optional[Sequence[dict]] = None
                    ) -> dict:
    eps = eps if eps is not None else build(cohort)
    tr, oos = split_chrono(eps)
    res = {"n_episodes": len(eps), "n_train": len(tr), "n_oos": len(oos),
           "train_span_utc": [tr[0]["t"], tr[-1]["t"]] if tr else None,
           "oos_span_utc": [oos[0]["t"], oos[-1]["t"]] if oos else None,
           "targets": {}}
    for target in ("p_extend", "resolved_flat", "resolved_economically"):
        res["targets"][target] = {}
        for f in FEATURES:
            a, b = conditional(tr, f, target), conditional(oos, f, target)
            keys = [k for k in a if a[k]["n"] >= MIN_BIN and k in b
                    and b[k]["n"] >= MIN_BIN]
            if len(keys) < 2:
                continue
            ta = {k: a[k]["rate"] for k in keys}
            tb = {k: b[k]["rate"] for k in keys}
            # does the ORDERING of the bins replicate out of sample?
            order_a = sorted(keys, key=lambda k: ta[k])
            order_b = sorted(keys, key=lambda k: tb[k])
            res["targets"][target][f] = {
                "train": {k: {"n": a[k]["n"], "rate": ta[k]} for k in keys},
                "oos": {k: {"n": b[k]["n"], "rate": tb[k]} for k in keys},
                "train_spread": round(max(ta.values()) - min(ta.values()), 4),
                "oos_spread": round(max(tb.values()) - min(tb.values()), 4),
                "ordering_replicates": order_a == order_b,
                "rank_corr": _spearman([ta[k] for k in keys],
                                       [tb[k] for k in keys]),
            }
    return res


def _spearman(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    n = len(a)
    if n < 3:
        return None
    ra = {v: i for i, v in enumerate(sorted(a))}
    rb = {v: i for i, v in enumerate(sorted(b))}
    x = [ra[v] for v in a]
    y = [rb[v] for v in b]
    mx, my = sum(x) / n, sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den = math.sqrt(sum((xi - mx) ** 2 for xi in x)
                    * sum((yi - my) ** 2 for yi in y))
    return round(num / den, 4) if den else None


# ---------------------------------------------------------------------------
# EXPLORATORY_V2 falsification test, run against L5 rather than against us
#
# The hypothesis to be tested was: once pair economics exceed 1.00 and an
# economical completion is unavailable, L5 disproportionately accumulates the
# FAVORED outcome. It is tested here as a side-choice question, chronologically,
# with the obvious confound controlled: L5 continues an existing run about two
# thirds of the time, so "he bought the favored side" is mostly a restatement of
# "his residual was already on the favored side" unless that is conditioned out.

def _opposite(side: str) -> str:
    return "Down" if side == "Up" else "Up"


def next_fill_side(e: Mapping[str, Any]) -> Optional[str]:
    """The outcome L5 actually bought next. EXTEND is the residual's own side;
    every completing or switching action is the other one."""
    a = e.get("next_action")
    if a in (None, "NONE_CARRIED"):
        return None
    return e["residual_side"] if a == "EXTEND" else _opposite(e["residual_side"])


def favored_side_next(eps: Sequence[dict], min_n: int = MIN_BIN) -> dict:
    """P(next fill lands on the favored side), by attainable pair cost, with
    and without conditioning on where the residual already sits."""
    tr, oos = split_chrono(eps)
    out: Dict[str, Any] = {
        "definition": ("favored = the outcome whose up-equivalent LAST PRINT "
                       "price exceeds 0.50. Ties at exactly 0.50 are EXCLUDED, "
                       "not assigned. There is no book here, so this is L5's "
                       "own print, never a mid or a touch."),
        "confound_controlled": ("P(extend) is ~0.66 unconditionally, so the "
                                "second table conditions on whether the "
                                "residual is ALREADY favored. Without that, "
                                "run persistence masquerades as favor."),
    }
    for name, S in (("train", tr), ("oos", oos)):
        flat: Dict[str, List[int]] = collections.defaultdict(lambda: [0, 0])
        cond: Dict[str, List[int]] = collections.defaultdict(lambda: [0, 0])
        base = [0, 0]
        for e in S:
            if e["favored"] == "NONE":
                continue
            ns = next_fill_side(e)
            if ns is None:
                continue
            b = _bin("pair_cost_last_prints", e)
            if b is None:
                continue
            hit = int(ns == e["favored"])
            base[0] += hit
            base[1] += 1
            flat[b][0] += hit
            flat[b][1] += 1
            k = "%s|%s" % ("res_favored" if e["residual_is_favored"]
                           else "res_against", b)
            cond[k][0] += hit
            cond[k][1] += 1
        out[name] = {
            "base_rate": round(base[0] / base[1], 4) if base[1] else None,
            "n": base[1],
            "by_pair_cost": {k: {"n": v[1], "p_favored": round(v[0] / v[1], 4)}
                             for k, v in flat.items() if v[1] >= min_n},
            "by_residual_side_and_pair_cost": {
                k: {"n": v[1], "p_favored": round(v[0] / v[1], 4)}
                for k, v in cond.items() if v[1] >= min_n},
        }
    return out


def monthly(eps: Sequence[dict]) -> Dict[str, dict]:
    """Chronological drift. Pooling months hides that the behaviour changed."""
    import datetime as _dt
    g: Dict[str, List[dict]] = collections.defaultdict(list)
    for e in eps:
        g[_dt.datetime.utcfromtimestamp(e["t"]).strftime("%Y-%m")].append(e)
    out = {}
    for k in sorted(g):
        v = g[k]
        out[k] = {
            "n_episodes": len(v),
            "completed_flat": sum(1 for x in v if x["resolved_flat"]),
            "completed_flat_rate": round(
                sum(1 for x in v if x["resolved_flat"]) / len(v), 5),
            "flipped_rate": round(
                sum(1 for x in v if x["exit_mode"] == "FLIPPED") / len(v), 4),
            "p_extend": round(
                sum(1 for x in v if x["next_action"] == "EXTEND") / len(v), 4),
            "median_residual_abs_at_open": _med([x["residual_abs"] for x in v]),
            "median_episode_pair_cost": _med([x["episode_pair_cost"] for x in v]),
        }
    return out


def reconciliation(eps: Sequence[dict]) -> dict:
    """Does this decomposition agree with the authoritative window-level one?

    RESIDUAL MUST RECONCILE EXACTLY: every window contributes exactly one
    CARRIED_TO_SETTLEMENT episode holding the whole window residual, so the sum
    is the same quantity `analysis/l5/btc15m/decompose.py` reports.

    MATCHED MUST NOT, and the difference is not an error. A window's matched
    quantity is min(total_up, total_dn) over ALL its fills; an episode's is
    min over the fills inside that episode. Partitioning the window into
    episodes can only shrink the sum. Reporting the episode sum as if it were
    the window's matched P&L would understate it.
    """
    return {
        "sum_episode_residual_pnl": sum(
            e["episode_residual_pnl"] or 0.0 for e in eps),
        "expected_window_residual_pnl": sum(
            e["window_residual_pnl"] or 0.0
            for e in eps if e["exit_mode"] == "CARRIED_TO_SETTLEMENT"),
        "sum_episode_matched_pnl": sum(
            e["episode_matched_pnl"] or 0.0 for e in eps),
        "windows_covered": len({e["cid"] for e in eps}),
        "episodes_with_indeterminate_settlement": sum(
            1 for e in eps if e["window_won_up"] is None),
        "note": ("residual is expected to agree to the cent with the "
                 "btc15m decomposition; matched is a different partition and "
                 "is expected to be SMALLER -- see the docstring"),
    }


def completion_improvement(eps: Sequence[dict]) -> dict:
    """Did waiting get a CHEAPER opposite leg than the price at the moment the
    imbalance appeared?

    THIS IS SELECTION-BIASED AND THE BIAS IS NOT REMOVABLE HERE. It conditions
    on a completing fill having HAPPENED. Episodes where L5 quoted patiently and
    was never hit leave no trace at all -- they end up in the carried bucket
    with `t_to_opposite_fill_s = None` and are excluded. So this answers "given
    that the other side eventually traded, how good was that price" and NOT
    "what is a waiting policy worth". The second question needs the book, which
    does not exist in this dataset.
    """
    tr, oos = split_chrono(eps)
    out: Dict[str, Any] = {"caveat": completion_improvement.__doc__.strip()}
    for name, S in (("train", tr), ("oos", oos)):
        g: Dict[str, List[float]] = collections.defaultdict(list)
        for e in S:
            p1 = e["px_opposite_fill_up_equiv"]
            if p1 is None:
                continue
            p0 = e["p_up_last_print"]
            up = e["residual_side"] == "Up"       # completing leg is the other
            v = ((1.0 - p0) - (1.0 - p1)) if up else (p0 - p1)
            w = e["t_to_opposite_fill_s"]
            g["<=5s" if w <= 5 else "5-15s" if w <= 15 else "15-60s" if w <= 60
              else "60-180s" if w <= 180 else ">180s"].append(v)
        out[name] = {k: {"n": len(v), "median": _med(v),
                         "mean": round(sum(v) / len(v), 6),
                         "p_improved": round(
                             sum(1 for x in v if x > 0) / len(v), 4)}
                     for k, v in g.items() if len(v) >= MIN_BIN}
    return out


def carried_outcomes(eps: Sequence[dict]) -> dict:
    """What actually happened to the residual L5 took into settlement."""
    tr, oos = split_chrono(eps)
    out: Dict[str, Any] = {}
    for name, S in (("train", tr), ("oos", oos)):
        car = [e for e in S if e["exit_mode"] == "CARRIED_TO_SETTLEMENT"
               and e["episode_residual_pnl"] is not None]
        if not car:
            continue
        g: Dict[str, List[dict]] = collections.defaultdict(list)
        for e in car:
            g["favored" if e["residual_is_favored"] else
              ("tie" if e["favored"] == "NONE" else "against")].append(e)
        out[name] = {
            "n": len(car),
            "p_residual_profitable": round(
                sum(1 for e in car if e["episode_residual_pnl"] > 0) / len(car), 4),
            "sum_residual_pnl": sum(e["episode_residual_pnl"] for e in car),
            "median_residual_pnl": _med(
                [e["episode_residual_pnl"] for e in car]),
            "by_side_at_open": {
                k: {"n": len(v),
                    "p_win": round(sum(1 for e in v
                                       if e["episode_residual_pnl"] > 0) / len(v), 4),
                    "sum_residual_pnl": sum(e["episode_residual_pnl"] for e in v)}
                for k, v in g.items() if len(v) >= 100},
        }
    return out


def report(cohort: str = "BTC15m") -> dict:
    eps = build(cohort)
    return {
        "cohort": cohort,
        "unobservable": list(UNOBSERVABLE),
        "n_episodes": len(eps),
        "segments": segment(eps),
        "predictors": predictor_table(eps=eps),
        "favored_side_next": favored_side_next(eps),
        "completion_improvement": completion_improvement(eps),
        "carried_outcomes": carried_outcomes(eps),
        "monthly": monthly(eps),
        "reconciliation": reconciliation(eps),
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="RESIDUAL_EXIT_FORENSICS_V1")
    ap.add_argument("--cohort", default="BTC15m")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    r = report(a.cohort)
    text = json.dumps(r, indent=2, default=str)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote %s (%d episodes)" % (a.out, r["n_episodes"]))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
