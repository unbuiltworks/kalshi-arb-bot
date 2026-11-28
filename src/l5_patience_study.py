"""OWNER 5569709706 #2 -- L5 patience and repricing, inferred from public
fills, against our 30 s resting deadline.

Public data shows prints, not orders. Two things can still be read off it:

  * PRINT CLUSTERS: consecutive same-window, same-side BUY prints at the
    same price. A cluster that spans time is an order (or a re-posted order
    at the same level) that kept getting filled; its duration is a LOWER
    bound on how long L5 stayed at that level, and the gap between two
    prints in it is time L5 waited at the level between fills. Under our
    30 s deadline a follow-on print more than 30 s after the previous one
    would not have been ours -- we would have cancelled.
  * REPRICES: consecutive same-side BUY prints at different prices. The
    step (in cents; + = paid more, - = paid less) and the time between them
    is L5's observable repricing.

Ours comes from the R4 order records (analysis/l5/r4_orders.jsonl): rest
time per order by outcome, time-to-fill on the fills, reprices, post-only
rejects. Nothing here changes the rest deadline; it is a frozen rung
parameter and the mismatch is reported for the owner.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_cap_tradeoff_curve as cv

OUT_PATH = os.path.join("analysis", "l5", "l5_patience_study.json")
R4_ORDERS_PATH = os.path.join("analysis", "l5", "r4_orders.jsonl")
OUR_DEADLINE_S = 30.0
GAP_BUCKETS = ((0, 1), (1, 5), (5, 15), (15, 30), (30, 60), (60, 120), (120, 300), (300, None))


def _pct(xs: Sequence[float]) -> dict:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return {"n": 0}
    q = lambda p: xs[min(n - 1, int(p * n))]
    return {"n": n, "p10": q(0.10), "p25": q(0.25), "p50": q(0.50), "p75": q(0.75), "p90": q(0.90),
            "max": xs[-1], "mean": round(sum(xs) / n, 4)}


def _bucket(x: float) -> str:
    for lo, hi in GAP_BUCKETS:
        if hi is None or x < hi:
            return f"{lo}-{hi}s" if hi is not None else f">={lo}s"
    return "?"


def window_prints(rows: Sequence[dict]) -> List[dict]:
    """BUY prints of one window in time order, YES-terms price kept per
    side (a Down buy is a NO buy at its own price)."""
    rs = sorted(rows, key=lambda r: (int(r["timestamp"]), int(r["trade_seq"])))
    out = []
    for r in rs:
        if str(r.get("side") or "").upper() != "BUY":
            continue
        side = "YES" if str(r.get("outcome")) == "Up" else "NO"
        out.append({"t": int(r["timestamp"]), "side": side, "px": D(str(r["price"])).quantize(D("0.01")),
                    "qty": D(str(r["quantity"])), "usd": D(str(r["dollars"])),
                    "tts": int(cv._f(r.get("seconds_to_settlement"))),
                    "sfs": int(cv._f(r.get("seconds_from_window_start")))})
    return out


def study_window(prints: Sequence[dict]) -> dict:
    """Clusters and reprices of one window, per side."""
    clusters, reprices, gaps_same_level = [], [], []
    by_side: Dict[str, List[dict]] = defaultdict(list)
    for p in prints:
        by_side[p["side"]].append(p)
    for side, ps in by_side.items():
        cur = None
        for i, p in enumerate(ps):
            if cur is not None and p["px"] == cur["px"]:
                gap = p["t"] - cur["last_t"]
                gaps_same_level.append({"gap_s": gap, "usd": float(p["usd"]), "side": side})
                cur["prints"] += 1
                cur["usd"] += p["usd"]
                cur["last_t"] = p["t"]
            else:
                if cur is not None:
                    clusters.append(cur)
                    reprices.append({"step_c": int(round(float(p["px"] - cur["px"]) * 100)),
                                     "dt_s": p["t"] - cur["last_t"], "side": side})
                cur = {"side": side, "px": cur_px(p), "first_t": p["t"], "last_t": p["t"],
                       "prints": 1, "usd": p["usd"]}
        if cur is not None:
            clusters.append(cur)
    for c in clusters:
        c["duration_s"] = c["last_t"] - c["first_t"]
        c["usd"] = float(c["usd"])
        c["px"] = float(c["px"])
    return {"clusters": clusters, "reprices": reprices, "gaps_same_level": gaps_same_level}


def cur_px(p: dict) -> D:
    return p["px"]          # already cent-quantized: sub-cent dust is one level


def l5_patience(rows: Optional[Sequence[dict]] = None, path: str = cv.CANONICAL_PATH) -> dict:
    rows = list(rows) if rows is not None else cv.load_rows(path)
    by_win: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_win[r["window_id"]].append(r)
    clusters, reprices, gaps = [], [], []
    first_print_sfs, last_print_tts = [], []
    lanes: Dict[str, dict] = defaultdict(lambda: {"clusters": [], "reprices": [], "gaps": []})
    for wid, rs in by_win.items():
        ps = window_prints(rs)
        if not ps:
            continue
        lane = cv._lane({"asset": rs[-1].get("asset", ""), "duration_minutes": int(cv._f(rs[-1].get("duration_minutes")))})
        w = study_window(ps)
        clusters += w["clusters"]; reprices += w["reprices"]; gaps += w["gaps_same_level"]
        lanes[lane]["clusters"] += w["clusters"]; lanes[lane]["reprices"] += w["reprices"]; lanes[lane]["gaps"] += w["gaps_same_level"]
        first_print_sfs.append(ps[0]["sfs"])
        last_print_tts.append(ps[-1]["tts"])

    def summarize(cl, rp, gp) -> dict:
        multi = [c for c in cl if c["prints"] > 1]
        gap_usd = sum(g["usd"] for g in gp)
        late = [g for g in gp if g["gap_s"] > OUR_DEADLINE_S]
        steps = Counter("same" if False else ("up" if r["step_c"] > 0 else "down") for r in rp)
        return {
            "prints": sum(c["prints"] for c in cl),
            "clusters": len(cl),
            "prints_per_cluster": _pct([c["prints"] for c in cl]),
            "single_print_clusters_share": round(sum(1 for c in cl if c["prints"] == 1) / len(cl), 4) if cl else None,
            "multi_print_cluster_duration_s": _pct([c["duration_s"] for c in multi]),
            "multi_print_clusters_longer_than_our_deadline_share": (
                round(sum(1 for c in multi if c["duration_s"] > OUR_DEADLINE_S) / len(multi), 4) if multi else None),
            "same_level_follow_on_gap_s": _pct([g["gap_s"] for g in gp]),
            "same_level_follow_on_gap_buckets": dict(Counter(_bucket(g["gap_s"]) for g in gp)),
            "same_level_follow_on_after_our_deadline": {
                "prints": len(late), "share_of_follow_on_prints": round(len(late) / len(gp), 4) if gp else None,
                "usd": round(sum(g["usd"] for g in late), 2),
                "share_of_follow_on_usd": round(sum(g["usd"] for g in late) / gap_usd, 4) if gap_usd else None},
            "reprices": len(rp),
            "reprice_step_cents": _pct([r["step_c"] for r in rp]),
            "reprice_direction": {"paid_more": steps.get("up", 0), "paid_less": steps.get("down", 0)},
            "reprice_abs_step_cents": _pct([abs(r["step_c"]) for r in rp]),
            "time_to_reprice_s": _pct([r["dt_s"] for r in rp]),
            "reprices_within_our_deadline_share": round(sum(1 for r in rp if r["dt_s"] <= OUR_DEADLINE_S) / len(rp), 4) if rp else None,
        }

    out = {"windows": len(by_win), "overall": summarize(clusters, reprices, gaps),
           "by_lane": {lane: summarize(v["clusters"], v["reprices"], v["gaps"]) for lane, v in sorted(lanes.items())},
           "first_print_seconds_from_window_start": _pct(first_print_sfs),
           "last_print_seconds_to_settlement": _pct(last_print_tts),
           "labels": ("public prints only: a cluster is consecutive same-side BUY prints at one price, its duration a LOWER "
                      "bound on time at that level (a re-post at the same price is indistinguishable); a reprice is the "
                      "next same-side print at a different price; timestamps are 1 s resolution")}
    return out


def our_patience(path: str = R4_ORDERS_PATH) -> dict:
    if not os.path.exists(path):
        return {"orders": 0}
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    filled = [o for o in rows if cv._f(o.get("filled_qty")) > 0]
    deadline = [o for o in rows if o.get("end_reason") == "deadline"]
    repriced = [o for o in rows if str(o.get("end_reason") or "").startswith("repriced")]
    rejected = [o for o in rows if str(o.get("end_reason") or "").startswith("rejected")]
    steps = []
    for o in repriced:
        try:
            new = D(str(o["end_reason"]).rsplit(":", 1)[-1])
            steps.append(int(round(float(new - D(str(o["px"]))) * 100)))
        except Exception:  # noqa: BLE001 - malformed reason is reported as no step
            pass
    return {"orders": len(rows), "filled": len(filled), "fill_share": round(len(filled) / len(rows), 4) if rows else None,
            "rest_s_all": _pct([cv._f(o.get("rest_s")) for o in rows]),
            "time_to_fill_s": _pct([cv._f(o.get("rest_s")) for o in filled]),
            "fills_after_20s_share": round(sum(1 for o in filled if cv._f(o.get("rest_s")) > 20) / len(filled), 4) if filled else None,
            "deadline_cancels": len(deadline), "deadline_rest_s": _pct([cv._f(o.get("rest_s")) for o in deadline]),
            "reprices": len(repriced), "reprice_step_cents": steps,
            "post_only_rejects": len(rejected), "reject_rest_s": _pct([cv._f(o.get("rest_s")) for o in rejected]),
            "deadline_s": OUR_DEADLINE_S,
            "labels": "R4 order records: rest_s is submit-to-terminal; a reprice is our own cancel when the touch moved"}


def compare(l5: Mapping[str, Any], ours: Mapping[str, Any]) -> dict:
    o = l5["overall"]
    late = o["same_level_follow_on_after_our_deadline"]
    ttf = ours.get("time_to_fill_s") or {}
    return {
        "l5_same_level_follow_on_gap_p50_s": o["same_level_follow_on_gap_s"].get("p50"),
        "l5_same_level_follow_on_gap_p90_s": o["same_level_follow_on_gap_s"].get("p90"),
        "l5_follow_on_prints_after_30s_share": late["share_of_follow_on_prints"],
        "l5_follow_on_usd_after_30s_share": late["share_of_follow_on_usd"],
        "l5_multi_print_clusters_longer_than_30s_share": o["multi_print_clusters_longer_than_our_deadline_share"],
        "l5_time_to_reprice_p50_s": o["time_to_reprice_s"].get("p50"),
        "l5_reprices_within_30s_share": o["reprices_within_our_deadline_share"],
        "l5_reprice_abs_step_p50_c": o["reprice_abs_step_cents"].get("p50"),
        "our_fill_share": ours.get("fill_share"), "our_time_to_fill_p50_s": ttf.get("p50"), "our_time_to_fill_max_s": ttf.get("max"),
        "our_deadline_cancels": ours.get("deadline_cancels"), "our_reprices": ours.get("reprices"),
        "reading": reading(o, ours),
    }


def reading(o: Mapping[str, Any], ours: Mapping[str, Any]) -> str:
    late = o["same_level_follow_on_after_our_deadline"]
    gap = o["same_level_follow_on_gap_s"]
    rp = o["time_to_reprice_s"]
    ttf = ours.get("time_to_fill_s") or {}
    l5_faster = (rp.get("p50") is not None and rp["p50"] < OUR_DEADLINE_S
                 and (late["share_of_follow_on_prints"] or 0) < 0.10)
    head = ("L5 is not patient at a level: %s%% of its price clusters are a single print, a follow-on print at the "
            "same level arrives p50 %s s / p90 %s s after the previous one, and only %s%% of follow-on prints "
            "(%s%% of follow-on dollars) arrive more than %.0f s later. L5's NEXT print is at a different price "
            "after a median %s s (p75 %s s, p90 %s s) with a median step of %s c (%s paid more, %s paid less); "
            "%s%% of those reprices happen inside our %.0f s deadline."
            % (round(100 * (o["single_print_clusters_share"] or 0), 1), gap.get("p50"), gap.get("p90"),
               round(100 * (late["share_of_follow_on_prints"] or 0), 1), round(100 * (late["share_of_follow_on_usd"] or 0), 1),
               OUR_DEADLINE_S, rp.get("p50"), rp.get("p75"), rp.get("p90"), o["reprice_abs_step_cents"].get("p50"),
               o["reprice_direction"]["paid_more"], o["reprice_direction"]["paid_less"],
               round(100 * (o["reprices_within_our_deadline_share"] or 0), 1), OUR_DEADLINE_S))
    ours_s = (" Ours: %s of %s orders filled (time to fill p50 %s s, max %s s), %s cancelled untouched at the %.0f s "
              "deadline, %s repriced, %s post-only rejects."
              % (ours.get("filled"), ours.get("orders"), ttf.get("p50"), ttf.get("max"), ours.get("deadline_cancels"),
                 OUR_DEADLINE_S, ours.get("reprices"), ours.get("post_only_rejects")))
    if l5_faster:
        verdict = (" The mismatch is therefore NOT rest length: L5 would rarely have rested 30 s at one price. It is "
                   "reprice cadence -- L5 moves its price within seconds when the touch moves, while we hold one "
                   "price for the full deadline, cancel, and only re-decide on the next cycle (%s of our %s orders "
                   "ended that way). Executable lever: a touch-following reprice inside the rest window (the "
                   "'repriced:touch_moved' path exists and fired %s times); the 30 s deadline itself is a frozen "
                   "rung parameter. Reported, not changed."
                   % (ours.get("deadline_cancels"), ours.get("orders"), ours.get("reprices")))
    else:
        verdict = (" L5 rests longer than our deadline at a level; the executable mismatch is the 30 s deadline "
                   "itself, a frozen rung parameter. Reported, not changed.")
    return head + ours_s + verdict


def study(rows: Optional[Sequence[dict]] = None, path: str = cv.CANONICAL_PATH,
          orders_path: str = R4_ORDERS_PATH) -> dict:
    rows = list(rows) if rows is not None else cv.load_rows(path)
    l5 = l5_patience(rows, path)
    ours = our_patience(orders_path)
    return {"owner_direction": "#167 5569709706 item 2", "l5": l5, "ours": ours, "comparison": compare(l5, ours),
            "provenance": cv.provenance(rows),
            "labels": "read-only; the rest deadline is a frozen rung parameter and is not changed here"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    out = study()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True, default=str)
    print(json.dumps(out["comparison"], indent=1))
    print(json.dumps({k: v for k, v in out["l5"]["overall"].items()}, indent=1, default=str)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
