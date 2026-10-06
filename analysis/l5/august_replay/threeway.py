"""L5 / frozen S1 / diagnostic S2 on one window, with full trajectories.

DIAGNOSTIC ONLY. Settlement cash is reported because the brief asks for it and
is labelled diagnostic throughout: it is computed from observed fill prices
against the settled outcome, models no fee or gas, and is NOT a P&L claim. The
outcome is read only for this reporting and is never visible to any policy --
every engine here is driven strictly forward off the tape.

NOTHING IS OPTIMIZED HERE. S2 arrives frozen at fingerprint 5650ca16... and
this module may not alter it; it asserts the fingerprint before running.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book, placement_consistency
from analysis.l5.historical_l2.mint_match import feasible
from analysis.l5.august_replay.replay import (assert_frozen, divergence,
                                              l5_fills_by_second,
                                              prints_by_second, replay)
from analysis.l5.august_replay.s2 import (S2_CANDIDATE, S2_FINGERPRINT,
                                          displacement_ticks, replay_s2)
from analysis.l5.august_replay.select_window import load_books
from src.polymarket_l5.owned_order_shadow import CANDIDATE as S1_CANDIDATE
from src.l5_chain_observer import observer as O

S2_EXPECT = "5650ca16943cd3e2da00254578d2e0bc1fbd146bfa76c9e59fc1ba7e396f7865"
DISP = S2_CANDIDATE["reprice_on_displacement_ticks"]


def winner_of(parquet: str, slug: str) -> Dict[str, Any]:
    """Settled outcome, for DIAGNOSTIC reporting only. No policy reads this."""
    import pyarrow.parquet as pq
    t = pq.read_table(parquet, columns=["market_slug", "token_id", "outcome",
                                        "is_winning_outcome"])
    d = t.to_pydict()
    out = {}
    for i, s in enumerate(d["market_slug"]):
        if s != slug:
            continue
        out[str(d["token_id"][i])] = bool(d["is_winning_outcome"][i])
    return out


def load_window(parquet: str, backfill: str, block_times: str, slug: str,
                prints_path: Optional[str] = None) -> Dict[str, Any]:
    """Everything one window needs, including the L5-excluded print set."""
    books, wins = load_books(parquet, slugs={slug})
    toks = wins[slug]["tokens"]
    tok_up = next(t for t, o in toks.items() if str(o).lower() == "up")
    tok_dn = next(t for t, o in toks.items() if str(o).lower() == "down")
    start = int(slug.rsplit("-", 1)[1])
    end = start + 900
    bt = {int(k): v for k, v in
          json.load(open(block_times))["block_times"].items()}
    bf = next(w for w in json.load(open(backfill))["rows"]
              if w["market_slug"] == slug)

    comp = {tok_up: tok_dn, tok_dn: tok_up}
    fills = []
    for f in bf["fills"]:
        g = dict(f)
        g["_ts"] = bt.get(f.get("block"))
        if g["_ts"] is not None:
            bk = books.get(g["token_id"])
            snap = bk.at(g["_ts"] - 1, max_age_s=MAX_CARRY_S) if bk else None
            g["_class"] = placement_consistency(snap, g["price"])
            other = comp.get(g["token_id"])
            osnap = (books[other].at(g["_ts"] - 1, max_age_s=MAX_CARRY_S)
                     if other in books else None)
            g["_mint_consistent"] = feasible(
                g["price"], osnap.get("best_bid") if osnap else None)
        fills.append(g)

    pr_all: Dict[int, List[dict]] = {}
    pr_third: Dict[int, List[dict]] = {}
    pmeta: Dict[str, Any] = {"supplied": False}
    if prints_path and os.path.exists(prints_path):
        pj = json.load(open(prints_path))
        if pj.get("market_slug") == slug:
            pbt = {int(k): v for k, v in (pj.get("block_times") or {}).items()}
            legs = pj.get("maker_order_legs") or []
            pad = O.PAD.lower()

            def is_l5(l):
                return (str(l.get("owner_topic") or "").lower() == pad
                        or str(l.get("counterparty_topic") or "").lower() == pad)
            third = [l for l in legs if not is_l5(l)]
            pr_all = prints_by_second(legs, pbt)
            pr_third = prints_by_second(third, pbt)
            pmeta = {"supplied": True, "chunk_holes": pj.get("chunk_holes"),
                     "coverage": pj.get("coverage"),
                     "maker_order_legs_total": len(legs),
                     "maker_order_legs_with_l5_as_a_party":
                         len(legs) - len(third),
                     "maker_order_legs_third_party_only": len(third),
                     "note": ("arm C uses the THIRD-PARTY set only, so S2 is "
                              "never credited off L5's own activity")}
    return {"slug": slug, "books": books, "tok_up": tok_up, "tok_dn": tok_dn,
            "start": start, "end": end, "fills": fills,
            "l5_by_s": l5_fills_by_second(fills, start, end),
            "prints_all": pr_all, "prints_third": pr_third,
            "prints_meta": pmeta, "winner": winner_of(parquet, slug)}


def cadence(ts: List[int]) -> Dict[str, Any]:
    g = [b - a for a, b in zip(sorted(ts), sorted(ts)[1:])]
    s = sorted(g)
    def qq(p):
        return (s[int(p * (len(s) - 1))] if s else None)
    return {"n_fills": len(ts), "n_gaps": len(g),
            "gap_p50": qq(0.5), "gap_p95": qq(0.95),
            "gap_max": (s[-1] if s else None),
            "fills_per_min": (round(60.0 * len(ts) / 900.0, 2))}


def legs_of(events, who):
    return [e for e in events if e.get("who") == who and e["kind"] == "fill"]


def econ(legs, tok_up, tok_dn, winner) -> Dict[str, Any]:
    up = [e for e in legs if e["outcome"] == "up"]
    dn = [e for e in legs if e["outcome"] == "down"]
    qu, qd = sum(e["size"] for e in up), sum(e["size"] for e in dn)
    cu, cd = (sum(e["price"] * e["size"] for e in up),
              sum(e["price"] * e["size"] for e in dn))
    tot = qu + qd
    matched = min(qu, qd)
    vu = (cu / qu) if qu else None
    vd = (cd / qd) if qd else None
    win_up = winner.get(tok_up)
    payout = (qu if win_up else qd) if winner else None
    return {"n_legs": len(legs), "shares_total": round(tot, 4),
            "inv_up": round(qu, 4), "inv_down": round(qd, 4),
            "matched": round(matched, 4),
            "residual": round(abs(qu - qd), 4),
            "matched_fraction_of_shares": (round(2 * matched / tot, 4)
                                           if tot else None),
            "vwap_up": (round(vu, 6) if vu is not None else None),
            "vwap_down": (round(vd, 6) if vd is not None else None),
            "pair_cost_vwap_sum": (round(vu + vd, 6)
                                   if None not in (vu, vd) else None),
            "spend": round(cu + cd, 4),
            "settlement_payout_diagnostic": (round(payout, 4)
                                             if payout is not None else None),
            "settlement_net_diagnostic": (round(payout - (cu + cd), 4)
                                          if payout is not None else None),
            "both_sides_active": bool(up) and bool(dn)}


def stale_time(rows, key) -> Dict[str, Any]:
    """Side-ticks spent with a quote more than DISP ticks behind the touch,
    and the worst displacement reached. This is the S1 failure, quantified."""
    beyond = 0
    any_behind = 0
    worst = 0
    held = 0
    for r in rows:
        if key not in r:
            continue
        for side in ("up", "down"):
            o = (r[key] or {}).get(side)
            if not o:
                continue
            held += 1
            d = o.get("displacement_ticks")
            if d is None:
                bb = r.get(side + "_bid")
                d = displacement_ticks(bb, o["price"])
            if d is None:
                continue
            if d > 0:
                any_behind += 1
            if d > DISP:
                beyond += 1
            worst = max(worst, d)
    return {"side_ticks_with_a_quote": held,
            "side_ticks_behind_the_touch": any_behind,
            "side_ticks_more_than_%d_ticks_behind" % DISP: beyond,
            "share_of_held_ticks_beyond_threshold": (round(beyond / held, 4)
                                                     if held else None),
            "worst_displacement_ticks": worst}


def both_side_ticks(rows, key) -> Dict[str, Any]:
    both = one = none = 0
    for r in rows:
        if key not in r:
            continue
        o = r[key] or {}
        n = sum(1 for s in ("up", "down") if o.get(s))
        both += n == 2
        one += n == 1
        none += n == 0
    return {"ticks_quoting_both_sides": both, "ticks_quoting_one_side": one,
            "ticks_quoting_neither": none}


def run_window(w: Dict[str, Any], *, arm: str) -> Dict[str, Any]:
    """arm 'A' = book only; arm 'C' = third-party prints, L5 excluded."""
    use_prints = arm == "C"
    pr = w["prints_third"] if use_prints else {}
    s1 = replay(w["books"], w["tok_up"], w["tok_dn"], w["start"], w["end"],
                w["l5_by_s"], pr, use_prints=use_prints)
    s2 = replay_s2(w["books"], w["tok_up"], w["tok_dn"], w["start"], w["end"],
                   w["l5_by_s"], pr, use_prints=use_prints)
    s1["divergence"] = divergence(s1["rows"])

    l5_legs = legs_of(s2["events"], "L5") or legs_of(s1["events"], "L5")
    # L5's legs come from the chain and are identical in both engines; take
    # them from the S2 run's trace rows so the timestamps line up with it.
    l5_legs = [{"t": r["t"], **f} for r in s2["rows"] if "l5" in r
               for f in r["l5_fills_this_second"]]
    s1_legs = [{"t": e["t"], **e} for e in legs_of(s1["events"], "S1")]
    s2_legs = [{"t": e["t"], **e} for e in legs_of(s2["events"], "S2")]

    mint_l5 = [f for f in l5_legs
               if f.get("t_minus_1_class") == "AT_OR_ABOVE_ASK_NOT_MEASURABLE"
               and f.get("mint_consistent") is True]

    out = {
        "market_slug": w["slug"], "window_utc": [w["start"], w["end"]],
        "arm": arm,
        "arm_definition": ("A: book only, the frozen module's own condition "
                           "(last_prints never assigned). C: third-party "
                           "prints with every L5 leg removed."),
        "prints": w["prints_meta"],
        "unresolvable_ticks": {"S1": s1["unresolvable_ticks"],
                               "S2": s2["unresolvable_ticks"]},
        "L5": {**econ(l5_legs, w["tok_up"], w["tok_dn"], w["winner"]),
               **cadence([f["t"] for f in l5_legs]),
               "mint_consistent_legs": len(mint_l5),
               "mint_consistent_shares": round(
                   sum(f["size"] for f in mint_l5), 2)},
        "S1": {**econ(s1_legs, w["tok_up"], w["tok_dn"], w["winner"]),
               **cadence([f["t"] for f in s1_legs]),
               "orders_placed": s1["orders_placed"],
               "quote_replacements": 0,
               "uncertain": s1["s1_uncertain_counts"],
               "stale_quote": stale_time(s1["rows"], "s1_resting_orders"),
               "both_side": both_side_ticks(s1["rows"], "s1_resting_orders"),
               "mint_opportunities": None},
        "S2": {**econ(s2_legs, w["tok_up"], w["tok_dn"], w["winner"]),
               **cadence([f["t"] for f in s2_legs]),
               "orders_placed": s2["orders_placed"],
               "quote_replacements": s2["reprices_total"],
               "quote_replacements_by_trigger": s2["reprices"],
               "uncertain": s2["s2_uncertain_counts"],
               "stale_quote": stale_time(s2["rows"], "s2_resting_orders"),
               "both_side": both_side_ticks(s2["rows"], "s2_resting_orders"),
               "mint_opportunities": {
                   "ticks": s2["mint_opportunity_ticks"],
                   "shares_bounded": s2["mint_opportunity_shares_bounded"],
                   "label": "MINT_OPPORTUNITY, never a fill"}},
        "s1_divergence": s1["divergence"],
        "selection_bias_note": s2["selection_bias_note"],
        "settlement_is_diagnostic_only": True,
    }
    # the theoretical ceiling the mint mechanism could address
    gap = out["L5"]["n_legs"] - out["S2"]["n_legs"]
    out["mint_ceiling_on_fill_count_gap"] = {
        "l5_legs": out["L5"]["n_legs"], "s2_legs": out["S2"]["n_legs"],
        "gap_legs": gap,
        "l5_mint_consistent_legs": len(mint_l5),
        "max_share_of_gap_mint_could_address": (
            round(min(len(mint_l5), gap) / gap, 4) if gap > 0 else None),
        "note": ("even granting every mint opportunity, the remainder of the "
                 "gap is resting-bid flow neither S1 nor S2 obtained"),
    }
    return out, s1, s2


def write_trace(path: str, s1: Dict[str, Any], s2: Dict[str, Any]) -> None:
    """One row per second, all three actors side by side."""
    s1_by_t = {r["t"]: r for r in s1["rows"]}
    rp: Dict[int, List[str]] = {}
    for e in s2["events"]:
        if e["kind"] == "reprice":
            rp.setdefault(e["t"], []).append(
                "%s:%s" % (e["outcome"], e["why"].replace("REPRICE_ON_", "")))

    def fmt(fs):
        return ";".join(
            "%s@%.4f x%.2f%s" % (
                f["outcome"], f["price"], f["size"],
                "/" + f["evidence"] if f.get("evidence")
                else ("/" + str(f.get("t_minus_1_class") or "")))
            for f in fs)

    def rfmt(o):
        if not o:
            return ""
        d = o.get("displacement_ticks")
        return "%.4f q%.2f age%ds ahead%.2f%s" % (
            o["price"], o["qty"], o["age_s"], o["queue_ahead_at_placement"],
            (" disp%d" % d) if d else "")

    cols = ["t", "tte_s", "up_bid", "up_bid_depth", "up_ask", "down_bid",
            "down_bid_depth", "down_ask", "mint_sum",
            "l5_fills", "l5_inv_up", "l5_inv_down", "l5_matched",
            "l5_residual", "l5_pair_cost",
            "s1_rest_up", "s1_rest_down", "s1_fills", "s1_inv_up",
            "s1_inv_down", "s1_matched", "s1_residual", "s1_pair_cost",
            "s2_rest_up", "s2_rest_down", "s2_fills", "s2_reprice",
            "s2_mint_opps", "s2_inv_up", "s2_inv_down", "s2_matched",
            "s2_residual", "s2_pair_cost"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r2 in s2["rows"]:
            t = r2["t"]
            r1 = s1_by_t.get(t, {})
            if "l5" not in r2:
                w.writerow([t, r2.get("tte_s"), "UNRESOLVABLE"]
                           + [""] * (len(cols) - 3))
                continue
            a = r2["s2_resting_orders"]
            b = r1.get("s1_resting_orders") or {}
            w.writerow([
                t, r2["tte_s"], r2["up_bid"], r2["up_bid_depth"],
                r2["up_ask"], r2["down_bid"], r2["down_bid_depth"],
                r2["down_ask"], r2["mint_sum"],
                fmt(r2["l5_fills_this_second"]),
                r2["l5"]["inv_up"], r2["l5"]["inv_down"], r2["l5"]["matched"],
                r2["l5"]["residual"], r2["l5"]["pair_cost"],
                rfmt(b.get("up")), rfmt(b.get("down")),
                fmt(r1.get("s1_fills_this_second") or []),
                (r1.get("s1") or {}).get("inv_up"),
                (r1.get("s1") or {}).get("inv_down"),
                (r1.get("s1") or {}).get("matched"),
                (r1.get("s1") or {}).get("residual"),
                (r1.get("s1") or {}).get("pair_cost"),
                rfmt(a.get("up")), rfmt(a.get("down")),
                fmt(r2["s2_fills_this_second"]),
                ";".join(rp.get(t, [])),
                len(r2["s2_mint_opportunities"]),
                r2["s2"]["inv_up"], r2["s2"]["inv_down"],
                r2["s2"]["matched"], r2["s2"]["residual"],
                r2["s2"]["pair_cost"]])


def trajectory(s1: Dict[str, Any], s2: Dict[str, Any], start: int, end: int,
               step: int = 60) -> List[Dict[str, Any]]:
    """Checkpoints across the WHOLE window -- the brief's first priority."""
    s1_by_t = {r["t"]: r for r in s1["rows"]}
    out = []
    want = list(range(start, end, step)) + [end - 1]
    for t in want:
        r2 = next((r for r in s2["rows"] if r["t"] == t), None)
        if r2 is None or "l5" not in r2:
            continue
        r1 = s1_by_t.get(t, {})
        d = t - start
        out.append({
            "at": "%02d:%02d" % (d // 60, d % 60), "t": t,
            "tte_s": r2["tte_s"],
            "up_bid": r2["up_bid"], "down_bid": r2["down_bid"],
            "l5_matched": r2["l5"]["matched"],
            "l5_residual": r2["l5"]["residual"],
            "l5_legs": r2["l5"]["n_fills"],
            "l5_pair_cost": r2["l5"]["pair_cost"],
            "s1_matched": (r1.get("s1") or {}).get("matched"),
            "s1_residual": (r1.get("s1") or {}).get("residual"),
            "s1_legs": (r1.get("s1") or {}).get("n_fills"),
            "s1_pair_cost": (r1.get("s1") or {}).get("pair_cost"),
            "s2_matched": r2["s2"]["matched"],
            "s2_residual": r2["s2"]["residual"],
            "s2_legs": r2["s2"]["n_fills"],
            "s2_pair_cost": r2["s2"]["pair_cost"],
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times", required=True)
    ap.add_argument("--selection", required=True)
    ap.add_argument("--prints", default=None)
    ap.add_argument("--outdir", default="analysis/l5/august_replay")
    a = ap.parse_args(argv)

    frozen = assert_frozen()
    assert S2_FINGERPRINT == S2_EXPECT, "S2 CHANGED -- refusing to run"
    print(json.dumps({"s1": frozen, "s2_fingerprint": S2_FINGERPRINT},
                     indent=1))

    sel = json.load(open(a.selection))
    slug = sel["selected"]
    w = load_window(a.parquet, a.backfill, a.block_times, slug, a.prints)
    os.makedirs(a.outdir, exist_ok=True)

    rep: Dict[str, Any] = {
        "classification": ("HISTORICAL_DIAGNOSTIC_ONLY_NO_SHADOW_NO_LIVE"),
        "not_a_replacement_for_prospective_s1": True,
        "s2_not_optimized_against_this_window": True,
        "frozen_s1": frozen, "s1_candidate": S1_CANDIDATE,
        "s2_fingerprint": S2_FINGERPRINT, "s2_candidate": S2_CANDIDATE,
        "market_slug": slug, "arms": {},
    }
    for arm in ("A", "C"):
        if arm == "C" and not w["prints_third"]:
            rep["arms"][arm] = {"skipped": "no third-party prints supplied"}
            continue
        res, s1, s2 = run_window(w, arm=arm)
        res["trajectory"] = trajectory(s1, s2, w["start"], w["end"])
        rep["arms"][arm] = res
        write_trace(os.path.join(a.outdir, "TRACE3_arm%s.csv" % arm), s1, s2)
    with open(os.path.join(a.outdir, "THREEWAY.json"), "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    for arm, r in rep["arms"].items():
        if r.get("skipped"):
            print("arm", arm, r["skipped"]); continue
        print("\n===== ARM", arm)
        for who in ("L5", "S1", "S2"):
            x = r[who]
            print(" %-3s legs=%-4s shares=%-9s matched=%-9s resid=%-8s "
                  "mfrac=%-7s pair=%-9s both=%s" % (
                      who, x["n_legs"], x["shares_total"], x["matched"],
                      x["residual"], x["matched_fraction_of_shares"],
                      x["pair_cost_vwap_sum"], x["both_sides_active"]))
        print(" S1 stale beyond %d ticks: %s / %s held side-ticks (worst %s)"
              % (DISP,
                 r["S1"]["stale_quote"]["side_ticks_more_than_%d_ticks_behind" % DISP],
                 r["S1"]["stale_quote"]["side_ticks_with_a_quote"],
                 r["S1"]["stale_quote"]["worst_displacement_ticks"]))
        print(" S2 stale beyond %d ticks: %s / %s held side-ticks (worst %s)"
              % (DISP,
                 r["S2"]["stale_quote"]["side_ticks_more_than_%d_ticks_behind" % DISP],
                 r["S2"]["stale_quote"]["side_ticks_with_a_quote"],
                 r["S2"]["stale_quote"]["worst_displacement_ticks"]))
        print(" S2 replacements:", r["S2"]["quote_replacements"],
              r["S2"]["quote_replacements_by_trigger"])
        print(" S2 mint opportunities:", r["S2"]["mint_opportunities"])
        print(" mint ceiling on gap:",
              json.dumps(r["mint_ceiling_on_fill_count_gap"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
