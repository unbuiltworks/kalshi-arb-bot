"""Turn the chronological trace into a compact human-readable summary.

THE MECHANISM IS COMPUTED, NOT ASSERTED. Each candidate explanation below is
reduced to a number the trace can confirm or refute, so the write-up reports
what separated the two policies rather than a story that fits:

  M1 MINT ACCESS -- L5's fills that priced at or above the prevailing ask and
     were mint-feasible. A complete-set mint needs no offer on the buyer's own
     book, only a counterparty bidding the complement at >= 1-p. S1 places
     resting bids and nothing else, so this count is inventory S1 had no
     mechanism to acquire at all.
  M2 QUEUE POSITION -- S1 quotes at offset_from_bid_ticks = 0, i.e. it JOINS
     the touch behind whatever size is already there. Reported: queue ahead at
     placement, and how often the frozen classifier returned
     QUEUE_CLEARED_FRONT_OF_BOOK -- the queue drained but S1 would be at the
     front, still resting, which is explicitly NOT a fill.
  M3 EVIDENCE REACHABILITY -- how often the touch actually moved strictly
     below S1's resting price, which in Arm A is the ONLY path to an
     evidenced fill.
  M4 RESIDUAL / TTE SUPPRESSION -- ticks lost to the residual cap and to the
     TTE shutdown, so a quoting gap is never mistaken for a fill failure.

Any of these can come back zero. A zero is reported as a zero.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List


def load_trace(p: str) -> List[dict]:
    rows = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def hhmmss(t: int, start: int) -> str:
    d = t - start
    return "%02d:%02d" % (d // 60, d % 60)


def analyse(rows: List[dict], rep: Dict[str, Any], arm: Dict[str, Any]
            ) -> Dict[str, Any]:
    start, end = rep["window_utc"]
    live = [r for r in rows if "l5" in r]
    ev = arm.get("events") or []

    l5_fills = [e for e in ev if e["who"] == "L5" and e["kind"] == "fill"]
    s1_fills = [e for e in ev if e["who"] == "S1" and e["kind"] == "fill"]
    s1_placed = [e for e in ev if e.get("kind") == "order_placed"]
    s1_unc = [e for e in ev if e.get("kind") == "uncertain"]

    # M1 -- mint-consistent L5 fills: inventory S1 has no mechanism to get
    m1 = [f for f in l5_fills
          if f.get("t_minus_1_class") == "AT_OR_ABOVE_ASK_NOT_MEASURABLE"
          and f.get("mint_consistent") is True]
    m1_shares = sum(f["size"] for f in m1)
    resting = [f for f in l5_fills
               if f.get("t_minus_1_class") == "RESTING_BID_CONSISTENT"]

    # M2 -- queue ahead at placement, and the front-of-book verdict
    qa = sorted(e.get("queue_ahead") or 0.0 for e in s1_placed)
    def pct(v):
        return round(qa[int(v * (len(qa) - 1))], 2) if qa else None
    qcf = len([e for e in s1_unc
               if e["verdict"] == "QUEUE_CLEARED_FRONT_OF_BOOK"])

    # M3 -- did the touch ever move strictly through a resting S1 price?
    #
    # COUNTED FROM THE EVENTS, NOT FROM THE ROW STATE. `s1_resting_orders` is
    # recorded AFTER the policy block, and an evidenced fill replenishes
    # within the same tick, so on exactly the ticks where the touch moved
    # through us the row already shows the REPLACEMENT order sitting at the
    # new touch. Reading the row therefore scored 0 while the same arm
    # reported real EVIDENCED_LEVEL_CONSUMED fills -- the numerator has to
    # come from the verdicts themselves.
    through = len([e for e in s1_fills
                   if e.get("evidence") == "EVIDENCED_LEVEL_CONSUMED"])
    rest_ticks = 0
    for r in live:
        for side in ("up", "down"):
            if (r.get("s1_resting_orders") or {}).get(side):
                rest_ticks += 1

    # M4 -- ticks with no order resting, and why
    leg = rep["candidate"]["quantity_shares"]
    cap = rep["candidate"]["residual_cap_shares"]
    tte_sd = rep["candidate"]["tte_shutdown_s"]
    tte_ticks = len([r for r in live if r["tte_s"] < tte_sd])
    no_order = len([r for r in live
                    if not (r.get("s1_resting_orders") or {}).get("up")
                    and not (r.get("s1_resting_orders") or {}).get("down")])

    mint_feasible_ticks = len([r for r in live if r.get("mint_feasible")])

    # WHAT HAPPENS AFTER THE DIVERGENCE. Checkpoints every 60 s from the
    # designated marker to the window end, so the trajectory is visible
    # rather than only the endpoints.
    dv = (arm.get("divergence") or {})
    first = dv.get("designated_first_material_divergence")
    after: List[dict] = []
    if first is not None:
        want = list(range(first, end, 60)) + [end - 1]
        by_t = {r["t"]: r for r in live}
        for t in want:
            r = by_t.get(t)
            if r is None:
                continue
            after.append({
                "t": t, "at": hhmmss(t, start), "tte_s": r["tte_s"],
                "l5_matched": r["l5"]["matched"],
                "l5_residual": r["l5"]["residual"],
                "l5_pair_cost": r["l5"]["pair_cost"],
                "s1_matched": r["s1"]["matched"],
                "s1_residual": r["s1"]["residual"],
                "s1_pair_cost": r["s1"]["pair_cost"],
                "s1_evidenced_fills_so_far": r["s1"]["n_fills"],
                "l5_fill_legs_so_far": r["l5"]["n_fills"]})

    return {
        "after_divergence": after,
        "window": {"slug": rep["market_slug"], "start_utc": start,
                   "end_utc": end, "seconds": len(rows),
                   "resolvable_seconds": len(live),
                   "unresolvable_ticks": arm.get("unresolvable_ticks")},
        "L5": {**arm["l5_final"],
               "n_fill_legs": len(l5_fills),
               "resting_bid_consistent": len(resting),
               "mint_consistent": len(m1),
               "mint_consistent_shares": round(m1_shares, 2)},
        "S1": {**arm["s1_final"],
               "orders_placed": len(s1_placed),
               "evidenced_fills": len(s1_fills),
               "uncertain_verdicts": arm.get("s1_uncertain_counts") or {}},
        "M1_mint_access": {
            "l5_mint_consistent_legs": len(m1),
            "l5_mint_consistent_shares": round(m1_shares, 2),
            "share_of_l5_legs": (round(len(m1) / len(l5_fills), 4)
                                 if l5_fills else None),
            "s1_mint_capable": False,
            "why": ("S1 places resting bids only -- levels 1, "
                    "offset_from_bid_ticks 0, no_aggressive_opposite_side. "
                    "A complete-set mint is not in its action set, so these "
                    "legs are inventory it had no mechanism to acquire."),
            "ticks_where_a_mint_was_feasible_on_the_touch":
                mint_feasible_ticks},
        "M2_queue_position": {
            "s1_offset_from_bid_ticks":
                rep["candidate"]["offset_from_bid_ticks"],
            "queue_ahead_at_placement": {
                "n": len(qa), "min": (qa[0] if qa else None),
                "p50": pct(0.5), "p90": pct(0.9),
                "max": (qa[-1] if qa else None)},
            "QUEUE_CLEARED_FRONT_OF_BOOK_verdicts": qcf,
            "why": ("joining the touch puts S1 BEHIND the size already "
                    "there; when that size drains the frozen classifier "
                    "returns QUEUE_CLEARED_FRONT_OF_BOOK, which is recorded "
                    "and excluded, NOT a fill")},
        "M3_evidence_reachability": {
            "ticks_with_an_s1_order_resting": rest_ticks,
            "evidenced_level_consumed_fills": through,
            "rate": (round(through / rest_ticks, 6) if rest_ticks else None),
            "note": ("in Arm A, EVIDENCED_LEVEL_CONSUMED is the only "
                     "reachable evidence, because the frozen module never "
                     "assigns last_prints")},
        "M4_suppression": {
            "residual_cap_shares": cap, "leg_shares": leg,
            "ticks_in_tte_shutdown": tte_ticks,
            "ticks_with_no_order_on_either_side": no_order},
        "divergence": arm.get("divergence"),
    }


def md(a: Dict[str, Any], rep: Dict[str, Any], rows: List[dict],
       arm_name: str) -> str:
    start = a["window"]["start_utc"]
    d = a["divergence"] or {}
    first = d.get("designated_first_material_divergence")
    L, S = a["L5"], a["S1"]
    o: List[str] = []
    o.append("# August L5 forensic replay -- frozen S1 against the real tape")
    o.append("")
    o.append("**DIAGNOSTIC HISTORICAL REPLAY ONLY.** Not validation. Not part")
    o.append("of the prospective 25-market Shadow S1 cohort. These artifacts")
    o.append("must not be used to modify Candidate S1 while that cohort is")
    o.append("running. Candidate fingerprint asserted unchanged:")
    o.append("`%s`." % rep["frozen_candidate"]["candidate_fingerprint_sha256"])
    o.append("")
    o.append("* window: `%s`, %ds, UTC %d..%d" % (
        a["window"]["slug"], a["window"]["seconds"], start,
        a["window"]["end_utc"]))
    o.append("* L2 seconds resolvable: **%d / %d**  (unresolvable %s)" % (
        a["window"]["resolvable_seconds"], a["window"]["seconds"],
        a["window"]["unresolvable_ticks"]))
    o.append("* arm: **%s**" % arm_name)
    o.append("")
    o.append("## 1. The side-by-side result")
    o.append("")
    o.append("| | L5 (actual) | S1 (frozen, replayed) |")
    o.append("|---|---:|---:|")
    o.append("| fill legs | %d | %d |" % (L["n_fill_legs"],
                                          S["evidenced_fills"]))
    o.append("| UP inventory | %.2f | %.2f |" % (L["inv_up"], S["inv_up"]))
    o.append("| DOWN inventory | %.2f | %.2f |" % (L["inv_down"],
                                                   S["inv_down"]))
    o.append("| matched inventory | %.2f | %.2f |" % (L["matched"],
                                                      S["matched"]))
    o.append("| residual | %.2f | %.2f |" % (L["residual"], S["residual"]))
    o.append("| pair cost | %s | %s |" % (L["pair_cost"], S["pair_cost"]))
    o.append("| orders placed | n/a | %d |" % S["orders_placed"])
    o.append("")
    o.append("S1's uncertain verdicts, recorded and **excluded from**")
    o.append("**economics**: `%s`." % json.dumps(S["uncertain_verdicts"]))
    o.append("")
    o.append("## 2. First material divergence")
    o.append("")
    if first is None:
        o.append("No second in this window has L5 holding a matched pair")
        o.append("while S1 holds none, so the designated marker never fires.")
    else:
        o.append("**t = %d (%s into the window, TTE %ds).** %s" % (
            first, hhmmss(first, start), a["window"]["end_utc"] - first,
            d.get("definition", "")))
    o.append("")
    o.append("| marker | second | offset |")
    o.append("|---|---:|---:|")
    for k in ("D1_first_l5_fill", "D2_l5_matched_while_s1_zero",
              "D3_first_s1_evidenced_fill",
              "D4_residual_gap_ge_one_leg"):
        v = d.get(k)
        o.append("| `%s` | %s | %s |" % (
            k, v if v is not None else "never",
            hhmmss(v, start) if v is not None else "--"))
    o.append("")
    o.append("## 3. What the trace says caused it")
    o.append("")
    m1, m2, m3, m4 = (a["M1_mint_access"], a["M2_queue_position"],
                      a["M3_evidence_reachability"], a["M4_suppression"])
    o.append("**M1 -- mint access.** %d of %d L5 fill legs (%s) priced at or"
             % (m1["l5_mint_consistent_legs"], L["n_fill_legs"],
                ("%.1f%%" % (100 * m1["share_of_l5_legs"]))
                if m1["share_of_l5_legs"] is not None else "n/a"))
    o.append("above the prevailing ask one second earlier AND were")
    o.append("mint-feasible -- %.2f shares. A resting bid cannot price above"
             % m1["l5_mint_consistent_shares"])
    o.append("the ask, and S1's action set is resting bids only, so this")
    o.append("inventory was unreachable to it by construction. A mint was")
    o.append("feasible on the touch itself in %d of %d resolvable seconds."
             % (m1["ticks_where_a_mint_was_feasible_on_the_touch"],
                a["window"]["resolvable_seconds"]))
    o.append("")
    o.append("**M2 -- queue position.** S1 quotes at offset %d ticks, i.e. it"
             % m2["s1_offset_from_bid_ticks"])
    q = m2["queue_ahead_at_placement"]
    o.append("joins the touch behind the size already resting: queue ahead")
    o.append("p50 **%s**, p90 %s, max %s shares over %d placements. The"
             % (q["p50"], q["p90"], q["max"], q["n"]))
    o.append("frozen classifier returned `QUEUE_CLEARED_FRONT_OF_BOOK`")
    o.append("**%d** times -- the queue drained, S1 would be at the front,"
             % m2["QUEUE_CLEARED_FRONT_OF_BOOK_verdicts"])
    o.append("still resting. That is explicitly not a fill.")
    o.append("")
    o.append("**M3 -- evidence reachability.** An S1 order was resting on")
    o.append("%d side-ticks; the touch moved strictly through its price, the"
             % m3["ticks_with_an_s1_order_resting"])
    o.append("one reachable evidence standard in this arm, **%d** times"
             % m3["evidenced_level_consumed_fills"])
    o.append("(rate %s per resting side-tick)." % m3["rate"])
    o.append("")
    o.append("**M4 -- suppression accounting.** %d ticks fell inside the"
             % m4["ticks_in_tte_shutdown"])
    o.append("TTE shutdown and %d had no order resting on either side, so a"
             % m4["ticks_with_no_order_on_either_side"])
    o.append("quoting gap is not being read as a fill failure.")
    o.append("")
    o.append("## 4. What happens after the divergence")
    o.append("")
    if not a.get("after_divergence"):
        o.append("No designated divergence, so there is no trajectory to")
        o.append("trace.")
    else:
        o.append("| at | TTE | L5 matched | L5 resid | L5 pair | "
                 "S1 matched | S1 resid | S1 pair | S1 fills |")
        o.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for c in a["after_divergence"]:
            o.append("| %s | %d | %.2f | %.2f | %s | %.2f | %.2f | %s | %d |"
                     % (c["at"], c["tte_s"], c["l5_matched"],
                        c["l5_residual"], c["l5_pair_cost"],
                        c["s1_matched"], c["s1_residual"],
                        c["s1_pair_cost"], c["s1_evidenced_fills_so_far"]))
    o.append("")
    o.append("## 5. Full trace")
    o.append("")
    o.append("`TRACE_%s.csv` / `.jsonl` carry one row per second with")
    o.append("timestamp, TTE, UP/DOWN BBO and depth, L5's fills and ledger,")
    o.append("S1's resting orders, evidenced fills and ledger.")
    return "\n".join(o).replace("TRACE_%s.csv", "TRACE_%s.csv" % arm_name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="analysis/l5/august_replay/REPLAY.json")
    ap.add_argument("--dir", default="analysis/l5/august_replay")
    a = ap.parse_args(argv)
    rep = json.load(open(a.replay))
    out = {}
    for arm_name, arm in rep["arms"].items():
        if arm.get("skipped"):
            out[arm_name] = arm
            continue
        tp = os.path.join(a.dir, "TRACE_%s.jsonl" % arm_name)
        rows = load_trace(tp)
        an = analyse(rows, rep, arm)
        out[arm_name] = an
        with open(os.path.join(a.dir, "SUMMARY_%s.md" % arm_name), "w") as fh:
            fh.write(md(an, rep, rows, arm_name) + "\n")
    with open(os.path.join(a.dir, "MECHANISM.json"), "w") as fh:
        json.dump({"market_slug": rep["market_slug"],
                   "window_utc": rep["window_utc"],
                   "classification": rep["classification"],
                   "arms": out}, fh, indent=1, default=str)
    print(json.dumps(out, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
