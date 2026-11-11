"""OWNER 5569709706 #1 / 5569845340 D -- which L5 EXTENDs create productive
inventory and which create uncompensated residual.

Every BUY print in L5's canonical history is a LOT on one side (YES-terms
for Up, NO at its own price for Down). Lots are matched FIFO against the
opposite side as complements arrive; at settlement the unmatched part of
each lot is residual and is settled at the window's actual outcome. So for
every lot we know how much of it was eventually completed, at what pair
cost, how long the complement took, and what its residual earned or lost.

The lot's relation at the moment it printed: OPEN (flat before), EXTEND
(same side as the residual), COMPLETE (opposite side). The question is
asked of the EXTENDs, conditioned on the state at the print: imbalance
before (residual in action units), price band, same-side run length,
seconds to settlement, position (residual dollars), the pair the complement
would have cost at that price (extend price + the last opposite print --
the closest public proxy for pair-at-touch), and movement (the extend
price against the previous same-side print). Productive share = matched
qty / lot qty; net = pair contribution on the matched part + residual P&L
on the rest, in dollars and per dollar of the lot.

Read-only; nothing here changes R4's cap. Numbers are L5's own history.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_cap_tradeoff_curve as cv

OUT_PATH = os.path.join("analysis", "l5", "l5_extend_productivity.json")
R4_ACTION_USD = D("2.50")


def _pct(xs: Sequence[float]) -> dict:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return {"n": 0}
    q = lambda p: round(xs[min(n - 1, int(p * n))], 4)
    return {"n": n, "p10": q(0.10), "p25": q(0.25), "p50": q(0.50), "p75": q(0.75), "p90": q(0.90),
            "mean": round(sum(xs) / n, 4)}


def prints(rows: Sequence[dict]) -> List[dict]:
    rs = sorted(rows, key=lambda r: (int(r["timestamp"]), int(r["trade_seq"])))
    out = []
    for r in rs:
        if str(r.get("side") or "").upper() != "BUY":
            continue
        up = str(r.get("outcome")) == "Up"
        px = D(str(r["price"])).quantize(D("0.0001"))
        out.append({"t": int(r["timestamp"]), "side": "YES" if up else "NO", "px": px,
                    "qty": D(str(r["quantity"])), "usd": D(str(r["dollars"])),
                    "tts": int(cv._f(r.get("seconds_to_settlement")))})
    return out


def band(px: D) -> str:
    p = float(px)
    return "p<0.35" if p < 0.35 else "p0.35-0.45" if p < 0.45 else "p0.45-0.55" if p < 0.55 else "p0.55-0.65" if p < 0.65 else "p>=0.65"


def imb_bucket(units: float) -> str:
    return "imb<0.5u" if units < 0.5 else "imb0.5-1u" if units < 1 else "imb1-2u" if units < 2 else "imb2-4u" if units < 4 else "imb>=4u"


def tts_bucket(s: int) -> str:
    return "tte<60s" if s < 60 else "tte1-3m" if s < 180 else "tte3-10m" if s < 600 else "tte>10m"


def run_bucket(n: int) -> str:
    return "run1" if n <= 1 else "run2" if n == 2 else "run3-4" if n <= 4 else "run>=5"


def pair_bucket(pair: Optional[D]) -> str:
    if pair is None:
        return "pair_unknown"
    p = float(pair)
    return "pair<0.95" if p < 0.95 else "pair0.95-0.98" if p < 0.98 else "pair0.98-1.00" if p <= 1.0 else "pair>1.00"


def move_bucket(c: Optional[int]) -> str:
    if c is None:
        return "move_first"
    return "move_down>=3c" if c <= -3 else "move_down1-2c" if c < 0 else "move_flat" if c == 0 else "move_up1-2c" if c <= 2 else "move_up>=3c"


def window_lots(ps: Sequence[dict], resolution: Optional[str], unit_usd: D) -> List[dict]:
    """FIFO lot accounting of one window; returns one record per lot."""
    open_lots: Dict[str, List[dict]] = {"YES": [], "NO": []}
    lots: List[dict] = []
    last_px: Dict[str, Optional[D]] = {"YES": None, "NO": None}
    run = {"side": None, "n": 0}
    for p in ps:
        side, other = p["side"], ("NO" if p["side"] == "YES" else "YES")
        res_q = sum(l["open"] for l in open_lots["YES"]) - sum(l["open"] for l in open_lots["NO"])
        res_side = "YES" if res_q > 0 else "NO" if res_q < 0 else None
        res_usd = sum(l["open"] * l["px"] for l in open_lots[res_side]) if res_side else D(0)
        relation = "open" if res_side is None else ("extend" if res_side == side else "complete")
        run["n"] = run["n"] + 1 if run["side"] == side else 1
        run["side"] = side
        opp = last_px[other]
        lot = {"t": p["t"], "side": side, "px": p["px"], "qty": p["qty"], "open": p["qty"], "usd": p["usd"],
               "relation": relation, "tts": p["tts"],
               "imbalance_units_before": float(res_usd / unit_usd) if unit_usd else None,
               "position_usd_before": float(res_usd),
               "run_len": run["n"], "band": band(p["px"]),
               "pair_proxy": (p["px"] + opp) if opp is not None else None,
               "move_c": (int(round(float(p["px"] - last_px[side]) * 100)) if last_px[side] is not None else None),
               "matched": D(0), "pair_cost_x_qty": D(0), "first_match_t": None}
        last_px[side] = p["px"]
        # match against the opposite side's open lots, FIFO
        remaining = p["qty"]
        for ol in open_lots[other]:
            if remaining <= 0:
                break
            if ol["open"] <= 0:
                continue
            m = min(remaining, ol["open"])
            pair = p["px"] + ol["px"]
            for l in (lot, ol):
                l["matched"] += m
                l["pair_cost_x_qty"] += m * pair
                if l["first_match_t"] is None:
                    l["first_match_t"] = p["t"]
            ol["open"] -= m
            remaining -= m
        lot["open"] = remaining
        lots.append(lot)
        open_lots[side].append(lot)
        open_lots[other] = [l for l in open_lots[other] if l["open"] > 0]
    for l in lots:
        win = resolution == l["side"] if resolution else None
        resid_pnl = (l["open"] * ((1 - l["px"]) if win else -l["px"])) if win is not None else D(0)
        matched_share = float(l["matched"] / l["qty"]) if l["qty"] else 0.0
        pair_cost = (l["pair_cost_x_qty"] / l["matched"]) if l["matched"] else None
        pair_pnl = (l["matched"] * (1 - pair_cost)) if pair_cost is not None else D(0)
        l.update({"matched_share": round(matched_share, 4),
                  "pair_cost": float(pair_cost) if pair_cost is not None else None,
                  "pair_pnl_usd": float(pair_pnl), "residual_qty": float(l["open"]),
                  "residual_usd": float(l["open"] * l["px"]), "residual_pnl_usd": float(resid_pnl),
                  "net_usd": float(pair_pnl + resid_pnl),
                  "net_per_usd": float((pair_pnl + resid_pnl) / l["usd"]) if l["usd"] else None,
                  "time_to_complement_s": (l["first_match_t"] - l["t"]) if l["first_match_t"] is not None else None,
                  "buckets": {"imbalance": imb_bucket(l["imbalance_units_before"] or 0.0), "band": l["band"],
                              "run": run_bucket(l["run_len"]), "tte": tts_bucket(l["tts"]),
                              "pair": pair_bucket(l["pair_proxy"]), "move": move_bucket(l["move_c"])}})
        for k in ("open", "pair_cost_x_qty", "first_match_t"):
            l.pop(k, None)
    return lots


def summarize(lots: Sequence[dict]) -> dict:
    n = len(lots)
    if not n:
        return {"lots": 0}
    usd = sum(D(str(l["usd"])) for l in lots)
    matched_usd = sum(D(str(l["usd"])) * D(str(l["matched_share"])) for l in lots)
    pair = sum(l["pair_pnl_usd"] for l in lots)
    resid = sum(l["residual_pnl_usd"] for l in lots)
    ttc = [l["time_to_complement_s"] for l in lots if l["time_to_complement_s"] is not None]
    return {"lots": n, "usd": round(float(usd), 2),
            "productive_share_of_usd": round(float(matched_usd / usd), 4) if usd else None,
            "fully_completed_share": round(sum(1 for l in lots if l["matched_share"] >= 0.999) / n, 4),
            "never_completed_share": round(sum(1 for l in lots if l["matched_share"] <= 0.001) / n, 4),
            "pair_pnl_usd": round(pair, 2), "residual_pnl_usd": round(resid, 2), "net_usd": round(pair + resid, 2),
            "net_per_usd": round((pair + resid) / float(usd), 4) if usd else None,
            "pair_cost_when_completed": _pct([l["pair_cost"] for l in lots if l["pair_cost"] is not None]),
            "time_to_complement_s": _pct(ttc),
            "residual_usd_left": round(sum(l["residual_usd"] for l in lots), 2)}


def study(rows: Optional[Sequence[dict]] = None, path: str = cv.CANONICAL_PATH) -> dict:
    rows = list(rows) if rows is not None else cv.load_rows(path)
    by_win: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_win[r["window_id"]].append(r)
    all_lots: List[dict] = []
    for wid, rs in by_win.items():
        ps = prints(rs)
        if not ps:
            continue
        _f, resolution, unit = cv.window_fills(rs)
        lane = cv._lane({"asset": rs[-1].get("asset", ""), "duration_minutes": int(cv._f(rs[-1].get("duration_minutes")))})
        for l in window_lots(ps, resolution, unit):
            l["lane"] = lane
            l["window_id"] = wid
            all_lots.append(l)
    by_rel = {rel: summarize([l for l in all_lots if l["relation"] == rel]) for rel in ("open", "extend", "complete")}
    ext = [l for l in all_lots if l["relation"] == "extend"]
    cond: Dict[str, Dict[str, dict]] = {}
    for key in ("imbalance", "band", "run", "tte", "pair", "move"):
        groups: Dict[str, List[dict]] = defaultdict(list)
        for l in ext:
            groups[l["buckets"][key]].append(l)
        cond[key] = {k: summarize(v) for k, v in sorted(groups.items())}
    lanes = {lane: summarize([l for l in ext if l["lane"] == lane]) for lane in sorted({l["lane"] for l in ext})}
    # the R4-relevant slice: extends drawn with less than one action unit unmatched
    small = [l for l in ext if (l["imbalance_units_before"] or 0) < 1.0]
    ranked = sorted(((key, k, v) for key, t in cond.items() for k, v in t.items() if v["lots"] >= 200),
                    key=lambda x: -(x[2]["net_per_usd"] or -9))
    out = {"owner_direction": "#167 5569709706 item 1; 5569845340 D",
           "windows": len(by_win), "lots": len(all_lots),
           "by_relation": by_rel,
           "extend_conditioned": cond,
           "extend_by_lane": lanes,
           "extend_below_one_unit_imbalance": summarize(small),
           "extend_best_and_worst_conditions_min200": {
               "best": [{"condition": f"{k}={v}", **s} for k, v, s in ranked[:6]],
               "worst": [{"condition": f"{k}={v}", **s} for k, v, s in ranked[-6:]]},
           "reading": reading(by_rel, cond, summarize(small)),
           "provenance": cv.provenance(rows),
           "labels": ("L5's own prints, FIFO lot matching, residual settled at the actual outcome; pair_proxy = "
                      "extend price + last opposite-side print (public proxy for pair-at-touch); productive share "
                      "= matched qty / lot qty; nothing here is realized or ours")}
    return out


def reading(by_rel: Mapping[str, Any], cond: Mapping[str, Any], small: Mapping[str, Any]) -> str:
    e, o = by_rel["extend"], by_rel["open"]
    imb = cond["imbalance"]
    tte = cond["tte"]
    pr = cond["pair"]
    best_imb = max(imb.items(), key=lambda kv: kv[1]["net_per_usd"] or -9)
    worst_imb = min(imb.items(), key=lambda kv: kv[1]["net_per_usd"] or 9)
    return ("EXTEND lots: %d, $%s; %.1f%% of extended dollars were eventually completed (opens: %.1f%%), %.1f%% of "
            "extend lots never completed; net %s per extended dollar (pair +%s, residual %s) against %s for opens. "
            "By imbalance before the extend: best %s (%s per $, %d lots), worst %s (%s per $, %d lots). "
            "Extends drawn with less than one action unit unmatched -- the only ones R4's cap could admit -- "
            "net %s per $ on %d lots with %.1f%% completed. By time to settlement: %s. By pair proxy: %s."
            % (e["lots"], e["usd"], 100 * (e["productive_share_of_usd"] or 0), 100 * (o["productive_share_of_usd"] or 0),
               100 * e["never_completed_share"], e["net_per_usd"], e["pair_pnl_usd"], e["residual_pnl_usd"], o["net_per_usd"],
               best_imb[0], best_imb[1]["net_per_usd"], best_imb[1]["lots"], worst_imb[0], worst_imb[1]["net_per_usd"], worst_imb[1]["lots"],
               small["net_per_usd"], small["lots"], 100 * (small["productive_share_of_usd"] or 0),
               "; ".join("%s %s per $ (%.0f%% completed, n %d)" % (k, v["net_per_usd"], 100 * (v["productive_share_of_usd"] or 0), v["lots"]) for k, v in tte.items()),
               "; ".join("%s %s per $ (%.0f%% completed, n %d)" % (k, v["net_per_usd"], 100 * (v["productive_share_of_usd"] or 0), v["lots"]) for k, v in pr.items()))
            + " Caveats: the pair-proxy > 1.00 and move-up >= 3 c slices are positive through their RESIDUAL, i.e. the "
              "side that had just risen went on to win more often in this history -- a directional effect of BTC "
              "windows, not a completion effect, and not a rule to trade on; the completion effect proper is the "
              "imbalance gradient (small imbalance -> higher net per $, shorter time to complement). R4's cap admits "
              "an extend only when the unmatched notional before it plus one action stays within one action, which "
              "on this history is the imb<0.5u slice.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    out = study()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    print(out["reading"])
    print(json.dumps(out["by_relation"], indent=1))
    print(json.dumps(out["extend_best_and_worst_conditions_min200"], indent=1)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
