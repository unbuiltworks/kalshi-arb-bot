#!/usr/bin/env python3
"""Build the segmented habitat census from PRESERVED evidence. Read-only.

It opens the recorded soak cycles and the recorded live-window records and
aggregates them. It writes one report and touches nothing else -- no evidence
file is rewritten, and no venue call is made.

ONE RE-READ IS APPLIED, IN THE OPEN: the soak cycles were recorded before the
crossed-book gate existed, so every one of them carries `book_readable: true`.
Re-evaluating the PRESERVED book with the current gate is a fresh reading of
unchanged evidence, and the census reports both numbers -- what the row said
at the time, and what the same book says now.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Any, Dict, List

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import segments as SG

LABEL = "GEMINI_L5_SEGMENTED_HABITAT_CENSUS_V1"


def _load_cycles(path: str) -> List[Dict[str, Any]]:
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "cycle":
                rows.append(r)
    return rows


def _regate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply the CURRENT readability gate to the PRESERVED books."""
    changed = 0
    for r in rows:
        book = r.get("book") or {}
        was = bool((r.get("habitat") or {}).get("book_readable"))
        now = not CP.crossed(book)
        if was != now:
            changed += 1
            hb = r.setdefault("habitat", {})
            hb["book_readable"] = now
            hb["book_why"] = ("crossed or absent quote -- refused by the "
                              "gate added after this cycle was recorded")
    return {"rows_regated": changed, "rows_total": len(rows),
            "why": ("the soak predates `continuous_probe.crossed`; the "
                    "recorded book is unchanged and only its verdict is "
                    "re-read")}


def _live_windows(pattern: str) -> List[Dict[str, Any]]:
    out = []
    for p in sorted(glob.glob(pattern)):
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if not d.get("authorization_id"):
            continue
        out.append({"file": os.path.basename(p), **d})
    return out


def reconciled_fills(pattern: str) -> Dict[str, Dict[str, Any]]:
    """Venue-confirmed `filled` rows from the read-only slot reconciliations.

    THE TERMINAL SNAPSHOT IS NOT THE FILL COUNT. CONTINUOUS-5's terminal read
    at 21:05:33 showed two orders resting and zero fills; one of them filled
    at 21:06:29 -- 57 seconds AFTER the window stopped -- and settled against
    us. A census built from `after.fills` alone silently drops that fill, and
    with it the clearest instance of the resting-time pattern it is trying to
    measure.

    So post-window fills are read from the venue's own order rows in the
    preserved reconciliation artifacts, and any window whose terminal read
    left orders resting has its fill count marked a FLOOR.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        ve = d.get("venue_evidence") or {}
        for h in (ve.get("history_per_status_raw") or []):
            if h.get("status_filter") != "filled":
                continue
            row = h.get("sample") or {}
            oid = str(row.get("orderId") or "")
            if not oid or str(row.get("status")) != "filled":
                continue
            out.setdefault(oid, {**row, "evidence_file": os.path.basename(path)})
    return out


def _placed_ids(win: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """venue order id -> the PLACE that created it, for this window."""
    out: Dict[str, Dict[str, Any]] = {}
    for m in (win.get("mutations_observed") or []):
        if m.get("kind") != "PLACE":
            continue
        oid = str(((m.get("result") or {}).get("venue_order_id")) or "")
        if oid:
            out[oid] = m
    return out


def _fill_segments(win: Dict[str, Any],
                   reconciled: Dict[str, Dict[str, Any]]
                   ) -> List[Dict[str, Any]]:
    """Place each venue-confirmed fill in the state its rung was placed at.

    A fill is attributed to the DECISION that produced the rung, which is the
    only honest attribution: the state that caused the order is the state the
    segment is about. When that state was never captured -- C4 and C6 predate
    book-state capture -- the price dimensions read NOT_PRESENTED rather than
    borrowing a later cycle's book.
    """
    after = win.get("after") or {}
    placed = _placed_ids(win)
    orders: Dict[str, Dict[str, Any]] = {}
    for o in (after.get("orders_seen") or []):
        if str(o.get("status")) == "filled":
            orders[str(o.get("orderId"))] = dict(o)
    # post-window fills: an order THIS window placed that the venue later
    # recorded as filled. C5's is the whole reason this path exists.
    for oid, row in reconciled.items():
        if oid in orders or oid not in placed:
            continue
        orders[oid] = {**row, "post_window": True}

    finish = str(win.get("finished_utc") or "")
    decisions = win.get("decisions") or []
    out = []
    for oid, o in sorted(orders.items()):
        px = str(o.get("price"))
        leg = "YES" if str(o.get("outcome")).lower() == "yes" else "NO"
        updated = str(o.get("updatedAt") or "")
        post = bool(o.get("post_window")) or (
            bool(updated) and bool(finish) and updated > finish)
        seg = {"window": win.get("authorization_id"), "leg": leg,
               "price": px, "qty": o.get("filledQuantity"),
               "venue_order_id": oid,
               "venue_created": o.get("createdAt"),
               "venue_updated": updated,
               "post_window_fill": post,
               "evidence_file": o.get("evidence_file")}
        found = None
        for d in decisions:
            for a in (d.get("actions") or []):
                if str(a.get("price")) == px and str(a.get("leg")) == leg:
                    found = d
                    break
            if found:
                break
        if found is None:
            seg.update({"spread": SG.NOT_PRESENTED,
                        "probability_band": SG.NOT_PRESENTED,
                        "touch_depth": SG.NOT_PRESENTED,
                        "flow": SG.NOT_PRESENTED,
                        "l5_state": SG.NOT_PRESENTED,
                        "tte": _tte_from_venue(o),
                        "state_why": ("no decision record names this price; "
                                      "book-state capture did not exist in "
                                      "this window")})
            out.append(seg)
            continue
        bs = found.get("book_state") or {}
        pres = bs.get("presented") or {}
        lad = ((bs.get("depth") or {}).get("bids") or {})
        acts = sorted({str(a.get("action")) for a in (found.get("actions")
                                                      or [])
                       if a.get("action")})
        # NO BOOK CAPTURE AT ALL is a different fact from A BOOK WE REFUSED.
        # C6 decided with no book_state because that instrumentation did not
        # exist yet; calling that UNREADABLE would report a failed read that
        # was never attempted.
        absent = not bs
        readable = bool(pres.get("readable")) and not CP.crossed(pres)
        miss = SG.NOT_PRESENTED if absent else SG.UNREADABLE
        seg.update({
            "decision_cycle": found.get("cycle"),
            "tte": SG.tte_bucket(found.get("tte_s")),
            "l5_state": ("+".join(acts) if acts else "NO_ACTION"),
            "flow": SG.NOT_PRESENTED,
            "spread": (SG.spread_bucket((pres.get("yes") or {}).get("spread"))
                       if readable else miss),
            "probability_band": (
                SG.probability_bucket((pres.get("yes") or {}).get("bid"))
                if readable else miss),
            "touch_depth": (SG.depth_bucket(lad.get("best_qty"))
                            if readable and lad else miss),
            "book_capture_absent": absent,
            "presented_yes_bid": (pres.get("yes") or {}).get("bid"),
            "presented_yes_spread": (pres.get("yes") or {}).get("spread"),
            "touch_qty": lad.get("best_qty"),
        })
        out.append(seg)
    return out


def _tte_from_venue(order: Dict[str, Any]) -> str:
    """TTE at placement from the venue's OWN expiry and OWN createdAt."""
    from datetime import datetime
    meta = order.get("contractMetadata") or {}
    exp, made = meta.get("expiryDate"), order.get("createdAt")
    if not exp or not made:
        return SG.UNREADABLE
    try:
        a = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(made).replace("Z", "+00:00"))
    except ValueError:
        return SG.UNREADABLE
    return SG.tte_bucket((a - b).total_seconds())


def live_states(wins: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The EXTEND/COMPLETE census, which SHADOW cannot produce at all.

    With nothing resting there are no fills, so shadow inventory stays flat
    and `classify()` can only ever return OPEN. Every post-fill state this
    programme has on record therefore comes from the live windows, and there
    is exactly one of them.
    """
    counts: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []
    for w in wins:
        for d in (w.get("decisions") or []):
            cls = d.get("classification") or []
            key = "+".join(sorted({str(c) for c in cls})) if cls else "NO_ACTION"
            counts[key] = counts.get(key, 0) + 1
            if key not in ("OPEN", "NO_ACTION"):
                rows.append({
                    "authorization_id": w.get("authorization_id"),
                    "cycle": d.get("cycle"), "utc": d.get("utc"),
                    "tte_s": d.get("tte_s"), "state": d.get("state"),
                    "classification": cls,
                    "actions": d.get("actions"),
                    "inventory": d.get("inventory_before_decision")})
    return {"decision_states": counts, "post_fill_decisions": rows,
            "why": ("SHADOW can never reach EXTEND or COMPLETE: with no "
                    "order resting there is no fill, inventory stays flat "
                    "and `classify()` derives the action from the residual "
                    "alone. This census is live-only for that reason.")}


def adverse_after_fills(wins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Where the presented mid went after each fill we have a book for."""
    out: List[Dict[str, Any]] = []
    for w in wins:
        after = w.get("after") or {}
        for o in (after.get("orders_seen") or []):
            if str(o.get("status")) != "filled":
                continue
            when = str(o.get("updatedAt") or "")
            later = []
            for d in (w.get("decisions") or []):
                if str(d.get("utc") or "") <= when:
                    continue
                pres = ((d.get("book_state") or {}).get("presented") or {})
                readable = bool(pres.get("readable")) and not CP.crossed(pres)
                later.append({
                    "utc": d.get("utc"),
                    "habitat": {
                        "book_readable": readable,
                        "yes_bid": (pres.get("yes") or {}).get("bid"),
                        "yes_ask": (pres.get("yes") or {}).get("ask"),
                        "no_bid": (pres.get("no") or {}).get("bid"),
                        "no_ask": (pres.get("no") or {}).get("ask")}})
            leg = "YES" if str(o.get("outcome")).lower() == "yes" else "NO"
            adv = SG.adverse_movement(leg=leg, fill_price=o.get("price"),
                                      later=later)
            adv.update({"authorization_id": w.get("authorization_id"),
                        "filled_utc": when,
                        "cycles_after_fill_on_record": len(later)})
            out.append(adv)
    return out


def build(*, jsonl: str, runs: str,
          reconcile: str = "analysis/gemini/reconcile_slot_*.json"
          ) -> Dict[str, Any]:
    cycles = _load_cycles(jsonl)
    regate = _regate(cycles)
    wins = _live_windows(runs)
    recon = reconciled_fills(reconcile)

    fills: List[Dict[str, Any]] = []
    rungs: List[Dict[str, Any]] = []
    per_window: List[Dict[str, Any]] = []
    for w in wins:
        wf = _fill_segments(w, recon)
        after = w.get("after") or {}
        filled_px = [str(f.get("price")) for f in wf]
        filled_at = {str(f.get("price")): str(f.get("venue_updated"))
                     for f in wf if f.get("venue_updated")}
        wr = SG.rung_intervals(w.get("mutations_observed") or [],
                               window_end=w.get("finished_utc"),
                               filled_prices=filled_px,
                               filled_utc=filled_at)
        fills.extend(wf)
        rungs.extend(wr)
        ds = w.get("decisions") or []
        per_window.append({
            "authorization_id": w.get("authorization_id"),
            "file": w.get("file"),
            "entry_tte_s": ((w.get("entry_schedule") or {}).get(
                "entered_tte_s") or (ds[0].get("tte_s") if ds else None)),
            "decisions": len(ds),
            "places": sum(1 for m in (w.get("mutations_observed") or [])
                          if m.get("kind") == "PLACE"),
            "cancels": sum(1 for m in (w.get("mutations_observed") or [])
                           if m.get("kind") == "CANCEL"),
            "fills": len(wf),
            "fill_prices": [f.get("price") for f in wf],
            "post_window_fills": sum(1 for f in wf
                                     if f.get("post_window_fill")),
            # A TERMINAL READ THAT LEFT ORDERS RESTING CANNOT COUNT FILLS.
            # C5's did, and one of those orders filled 57 s later.
            "fills_are_a_floor": bool(
                [o for o in (after.get("open_orders") or [])
                 if str(o.get("status")) in ("open", "live", "accepted")]),
            "rungs_resolved": len(wr),
            "resting_seconds_total": round(sum(
                float(r["resting_s"]) for r in wr
                if r.get("resting_s") is not None), 2),
        })

    cen = SG.census(cycles, fill_segments=fills, rungs=rungs)
    return {
        "label": LABEL,
        "sources": {"shadow_cycles": jsonl, "live_windows": runs},
        "regate": regate,
        "live_windows": per_window,
        "live_states": live_states(wins),
        "adverse_movement": adverse_after_fills(wins),
        "fills": fills,
        "rungs": rungs,
        "census": cen,
        "labels": ("EVIDENCE ONLY -- model B. Read-only over preserved "
                   "records. No rate is stated, nothing here reaches the L5 "
                   "policy, and no line here is realized P&L."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--runs",
                    default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--out",
                    default="analysis/gemini/segment_census.json")
    a = ap.parse_args(argv)
    rep = build(jsonl=a.jsonl, runs=a.runs)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    c = rep["census"]
    print("SEGMENT_CENSUS " + json.dumps({
        "cycles": c["cycles"], "refused": c["book_refused_cycles"],
        "refused_share": c["book_refused_share"],
        "fills": c["fills_total"], "rates_stated": c["rates_stated"],
        "rungs": c["repricing"]["rungs_total"]}, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
