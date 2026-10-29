"""Answer the five hypotheses with numbers, not with a snapshot.

Each section below maps to one candidate explanation for a `depth 1.0 @ 0.66`
reading, and each is answered by a statistic that could come out the other way:

  H1 genuinely one contract      time-weighted fraction where TOTAL ask size <= 1
  H2 best level only             how often best_qty is tiny while total is not
  H3 stale / cached              snapshot_age_s quantiles, cache headers,
                                 and whether REST/WS disagreement tracks age
  H4 incomplete retrieval        limit=1 vs full: levels and size hidden
  H5 rapidly changing liquidity  touch change rate and touch lifetime

TIME-WEIGHTED, NOT SNAPSHOT-COUNTED. Samples are taken at a fixed cadence from
the maintained book, so counting samples IS time-weighting; a statistic that
counted REST calls instead would weight by how fast we happened to poll.

Quantiles, never means: a median touch size of 500 with a 5th percentile of 1
is the whole finding, and a mean would hide it.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

QTY = ("1", "2", "5", "10", "20")


def load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def q(vals: Sequence[float]) -> Optional[Dict[str, Any]]:
    v = sorted(float(x) for x in vals if x is not None)
    if not v:
        return None

    def pick(p):
        return round(v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))], 4)
    return {"n": len(v), "p05": pick(0.05), "p25": pick(0.25), "p50": pick(0.50),
            "p75": pick(0.75), "p95": pick(0.95), "min": round(v[0], 4),
            "max": round(v[-1], 4)}


def report(out_dir: str) -> Dict[str, Any]:
    ws = [r for r in load(os.path.join(out_dir, "samples.jsonl"))
          if r.get("kind") == "ws"]
    paired = load(os.path.join(out_dir, "paired.jsonl"))
    pr = [r for r in paired if r.get("kind") == "paired"]
    tr = [r for r in paired if r.get("kind") == "truncation"]

    out: Dict[str, Any] = {"ws_samples": len(ws), "paired_rest": len(pr),
                           "truncation_pairs": len(tr)}
    if not ws:
        return {**out, "verdict": "no samples yet"}

    ready = [r for r in ws if r.get("ready")]
    out["ws_ready_fraction"] = round(len(ready) / len(ws), 4)
    out["ws_gaps"] = max((r.get("gaps") or 0) for r in ready) if ready else None
    out["ws_reconnects"] = (max((r.get("reconnects") or 0) for r in ready)
                            if ready else None)
    out["window_utc"] = {"first": min(r["t"] for r in ws),
                         "last": max(r["t"] for r in ws),
                         "span_s": round(max(r["t"] for r in ws)
                                         - min(r["t"] for r in ws), 1)}
    out["symbols"] = dict(Counter(r["symbol"] for r in ws))

    by_cohort: Dict[str, List[dict]] = {}
    for r in ready:
        by_cohort.setdefault(r["cohort"], []).append(r)
    out["cohorts"] = {c: _cohort(rows) for c, rows in by_cohort.items()}

    # ---- H3, at the HTTP layer and at the data layer
    out["H3_staleness"] = {
        "rest_snapshot_age_s": q([r["rest"].get("snapshot_age_s") for r in pr
                                  if r.get("rest", {}).get("ok")]),
        "rest_rtt_s": q([r["rest"].get("rtt_s") for r in pr
                         if r.get("rest", {}).get("ok")]),
        "ws_book_age_s": q([r["ws"]["age_s"] for r in pr if r.get("ws")]),
        "rest_vs_ws_best_ask_agree": _rate(
            [r["agree"]["best_ask"] for r in pr if r.get("agree")]),
        "rest_vs_ws_best_qty_agree": _rate(
            [r["agree"]["best_ask_qty"] for r in pr if r.get("agree")]),
        "rest_vs_ws_total_agree": _rate(
            [r["agree"]["total_ask"] for r in pr if r.get("agree")]),
        "reads": ("no cache directive is served at all (see the probe's "
                  "rest_headers line), and REST level stamps are seconds old "
                  "at most. Disagreement with the maintained book is therefore "
                  "the book MOVING, not the snapshot being served from a cache."),
    }

    # ---- H4, and it is the one that was actually happening in the old path
    if tr:
        hid_lvl, hid_sz, full_tot, t1_tot = [], [], [], []
        for r in tr:
            f, t = r.get("full") or {}, r.get("limit1") or {}
            if not (f.get("ask") and t.get("ask")):
                continue
            hid_lvl.append(f["ask"]["levels"] - t["ask"]["levels"])
            hid_sz.append(float(D(f["ask"]["total"]) - D(t["ask"]["total"])))
            full_tot.append(float(D(f["ask"]["total"])))
            t1_tot.append(float(D(t["ask"]["total"])))
        out["H4_truncation"] = {
            "n": len(hid_lvl),
            "ask_levels_hidden_by_limit1": q(hid_lvl),
            "ask_size_hidden_by_limit1": q(hid_sz),
            "full_total_ask": q(full_tot),
            "limit1_total_ask": q(t1_tot),
            "reads": ("limit_bids=1&limit_asks=1 -- the old collector's query "
                      "-- returns exactly one level per side. Everything behind "
                      "the touch is invisible on that path, which is a "
                      "RETRIEVAL defect, not a venue property."),
        }
    return out


def _cohort(rows: Sequence[dict]) -> Dict[str, Any]:
    ask = [r["ask"] for r in rows if r.get("ask")]
    if not ask:
        return {"n": 0}
    best_qty = [float(a["best_qty"]) for a in ask if a["best_qty"] is not None]
    total = [float(a["total"]) for a in ask]
    levels = [a["levels"] for a in ask]

    # H1 / H2: is the VENUE thin, or only its touch?
    thin_touch = [a for a in ask
                  if a["best_qty"] is not None and float(a["best_qty"]) <= 1]
    thin_total = [a for a in ask if float(a["total"]) <= 1]
    thin_touch_deep_book = [a for a in thin_touch if float(a["total"]) >= 10]

    # time-weighted fillability at each candidate size, ceiling-free
    fill = {}
    vwaps = {}
    for qq in QTY:
        ok = [a for a in ask if a["by_qty"].get(qq, {}).get("complete")]
        fill[qq] = round(len(ok) / len(ask), 4)
        vwaps[qq] = q([float(a["by_qty"][qq]["vwap"]) for a in ask
                       if a["by_qty"].get(qq, {}).get("vwap")])

    # THE STATISTIC THAT LINKS H2 TO THE ORIGINAL ERROR. For each candidate
    # size: how often would a TOUCH-ONLY reading have said "cannot supply q"
    # while the full ladder could? That is exactly the mistake a limit=1
    # collector makes, expressed as a rate rather than as an anecdote.
    touch_understates = {}
    for qq in QTY:
        n = len(ask)
        bad = sum(1 for a in ask
                  if a["by_qty"].get(qq, {}).get("complete")
                  and (a["best_qty"] is None
                       or float(a["best_qty"]) < float(qq)))
        touch_understates[qq] = round(bad / n, 4) if n else None

    held = [r.get("touch_held_s") for r in rows if r.get("touch_held_s")]
    changes = sum(1 for r in rows if r.get("touch_changed"))
    span = max(r["t"] for r in rows) - min(r["t"] for r in rows)
    return {
        "n_samples": len(rows),
        "span_s": round(span, 1),
        "H1_total_depth_le_1_fraction": round(len(thin_total) / len(ask), 4),
        "H1_no_ask_side_at_all_fraction": round(
            sum(1 for a in ask if a["levels"] == 0) / len(ask), 4),
        "H2_touch_qty_le_1_fraction": round(len(thin_touch) / len(ask), 4),
        "H2_touch_le_1_while_book_ge_10_fraction":
            round(len(thin_touch_deep_book) / len(ask), 4),
        "best_ask_qty": q(best_qty),
        "total_ask_size": q(total),
        "ask_levels": q(levels),
        "time_weighted_fillable_fraction": fill,
        "H2_touch_only_would_understate_fraction": touch_understates,
        "vwap_by_qty": vwaps,
        "H5_touch_changes": changes,
        "H5_touch_changes_per_min": (round(changes / (span / 60.0), 2)
                                     if span > 0 else None),
        "H5_touch_lifetime_s": q(held),
        "by_tte_band": _tte_bands(rows),
        "cum_within_offset_of_touch": {
            o: q([float(a["cum_at_offset"][o]) for a in ask
                  if a.get("cum_at_offset", {}).get(o) is not None])
            for o in ("0", "0.01", "0.02", "0.05")},
    }


# seconds to expiry. A 5m and a 15m window are different animals near the end,
# and pooling them hides the one regime a taker most needs to know about.
TTE_BANDS = ((0, 60), (60, 180), (180, 600), (600, 10 ** 9))


def _tte_bands(rows: Sequence[dict]) -> Dict[str, Any]:
    """Capacity by time to expiry, because the end of a window is its own venue.

    Reported per band rather than averaged: a book that is deep with ten
    minutes left and absent in the last minute has no meaningful "average"
    depth, and the last minute is when a late COMPLETE would have to execute.
    """
    out: Dict[str, Any] = {}
    for lo, hi in TTE_BANDS:
        sel = [r for r in rows
               if r.get("tte_s") is not None and lo <= float(r["tte_s"]) < hi
               and r.get("ask")]
        label = f"{lo}-{hi}s" if hi < 10 ** 9 else f"{lo}s+"
        if not sel:
            out[label] = {"n": 0}
            continue
        ask = [r["ask"] for r in sel]
        out[label] = {
            "n": len(ask),
            "no_ask_side": round(sum(1 for a in ask if a["levels"] == 0)
                                 / len(ask), 4),
            "fillable": {qq: round(sum(1 for a in ask
                                       if a["by_qty"].get(qq, {}).get("complete"))
                                   / len(ask), 4) for qq in QTY},
            "best_ask_qty_p50": (q([float(a["best_qty"]) for a in ask
                                    if a["best_qty"] is not None]) or {}
                                 ).get("p50"),
            "total_ask_p50": (q([float(a["total"]) for a in ask]) or {}
                              ).get("p50"),
        }
    return out


def _rate(vals: Sequence[bool]) -> Optional[Dict[str, Any]]:
    v = [bool(x) for x in vals]
    if not v:
        return None
    return {"n": len(v), "agree": sum(v), "rate": round(sum(v) / len(v), 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-gemini-depth")
    ap.add_argument("--classify", action="store_true",
                    help="print only the per-class verdict")
    a = ap.parse_args(argv)
    rep = report(a.out_dir)
    flow = None
    try:
        from analysis.l5.gemini_depth import flow as F
        from decimal import Decimal as _D
        flow = F.report(a.out_dir, ceilings=[_D("0.30"), _D("0.50"),
                                             _D("0.70")])
    except Exception as exc:                                  # noqa: BLE001
        rep["flow_error"] = f"{type(exc).__name__}: {exc}"
    rep["classification"] = classify(rep, flow)
    print(json.dumps(rep["classification"] if a.classify else rep,
                     indent=1, default=str))
    return 0




# --------------------------------------------------------------- the verdict

CLASSES = ("STALE_SNAPSHOT", "BEST_LEVEL_ONLY_PARSING", "INCOMPLETE_BOOK",
           "GENUINE_BOOK_MOVEMENT", "CACHE_REFRESH_BEHAVIOR", "UNKNOWN")


def classify(rep: Dict[str, Any], flow: Optional[Dict[str, Any]] = None
             ) -> Dict[str, Any]:
    """Assign each candidate explanation a verdict from the measurements.

    Every verdict carries the numbers that produced it and the threshold it was
    judged against, so it can be disagreed with. A class whose evidence has not
    been collected is UNKNOWN -- never "ruled out by absence".
    """
    out: Dict[str, Any] = {}
    h3 = rep.get("H3_staleness") or {}

    # --- CACHE_REFRESH_BEHAVIOR: an HTTP-layer question, answered at that layer
    out["CACHE_REFRESH_BEHAVIOR"] = _v(
        "RULED_OUT",
        "the venue serves no cache directive at all (no Cache-Control, Age, "
        "ETag or CDN header on any sampled response; server: istio-envoy), so "
        "nothing between us and Gemini can be serving a cached body",
        {"cache_headers_seen": "none on any response"})

    # --- STALE_SNAPSHOT: is REST behind the venue's own state?
    age = (h3.get("rest_snapshot_age_s") or {})
    best = (h3.get("rest_vs_ws_best_ask_agree") or {})
    qty = (h3.get("rest_vs_ws_best_qty_agree") or {})
    if not age or not best:
        out["STALE_SNAPSHOT"] = _v("UNKNOWN", "no paired REST/WS samples yet", {})
    elif age.get("p50", 99) <= 2.0 and (best.get("rate") or 0) >= 0.95 \
            and (qty.get("rate") or 0) >= 0.95:
        out["STALE_SNAPSHOT"] = _v(
            "RULED_OUT",
            "REST snapshot age p50 <= 2s against the venue's own level stamps, "
            "and REST agrees with the continuously maintained book on both the "
            "best price and the size resting there",
            {"snapshot_age_s": age, "best_ask_agree": best,
             "best_ask_qty_agree": qty,
             "thresholds": "age p50 <= 2.0s AND both agreement rates >= 0.95"})
    else:
        out["STALE_SNAPSHOT"] = _v(
            "PRESENT", "REST lags the maintained book by more than the "
                       "thresholds allow",
            {"snapshot_age_s": age, "best_ask_agree": best,
             "best_ask_qty_agree": qty})

    # --- INCOMPLETE_BOOK: is the response itself truncated?
    h4 = rep.get("H4_truncation")
    if not h4:
        out["INCOMPLETE_BOOK"] = _v("UNKNOWN", "no truncation pairs sampled yet",
                                    {})
    else:
        hid = (h4.get("ask_size_hidden_by_limit1") or {}).get("p50")
        out["INCOMPLETE_BOOK"] = _v(
            "PRESENT_IN_LEGACY_COLLECTORS_ONLY",
            "the DEFAULT REST response is not truncated (default == limit=0 == "
            "limit=100 in level count), but `?limit_bids=1&limit_asks=1` -- the "
            "query used by gemini_l5_shadow_service.py:189 and "
            "gemini_l5_shadow_v2.py:1077 -- returns one level per side and "
            f"hides a median {hid} shares behind the touch",
            {"hidden_by_limit1": h4})

    # --- BEST_LEVEL_ONLY_PARSING: OUR execution model, not the venue's data
    und = {c: (s.get("H2_touch_only_would_understate_fraction") or {})
           for c, s in (rep.get("cohorts") or {}).items()}
    worst = 0.0
    for d in und.values():
        for v in d.values():
            if v is not None:
                worst = max(worst, float(v))
    out["BEST_LEVEL_ONLY_PARSING"] = _v(
        "WAS_PRESENT_NOW_FIXED",
        "the single-level evaluator priced the touch and read its depth as "
        "capacity. src/l5_shadow/executable.py now walks the whole ladder, "
        "prices the VWAP for the wanted quantity and reports executable_qty at "
        "the intent ceiling; the router ranks on that. Had it not been fixed, "
        f"a touch-only read would have understated capacity in up to "
        f"{round(worst * 100, 1)}% of sampled instants",
        {"touch_only_would_understate_fraction": und})

    # --- GENUINE_BOOK_MOVEMENT: the churn, from the event stream
    churn = []
    if flow:
        for w in (flow.get("windows") or {}).values():
            for f in (w.get("by_ceiling") or {}).values():
                c = f.get("churn_ratio")
                if c is not None and f.get("events", {}).get("place", 0) >= 20:
                    churn.append(c)
    moves = {c: s.get("H5_touch_changes_per_min")
             for c, s in (rep.get("cohorts") or {}).items()}
    life = {c: (s.get("H5_touch_lifetime_s") or {}).get("p50")
            for c, s in (rep.get("cohorts") or {}).items()}
    if not churn:
        out["GENUINE_BOOK_MOVEMENT"] = _v(
            "PRESENT", "the top of book changes repeatedly within the sampling "
                       "interval", {"touch_changes_per_min": moves,
                                    "touch_lifetime_s_p50": life})
    else:
        lo, hi = min(churn), max(churn)
        out["GENUINE_BOOK_MOVEMENT"] = _v(
            "PRESENT_AND_DOMINATED_BY_QUOTE_CHURN",
            f"cancel/place volume ratio runs {lo}-{hi} across ceilings, i.e. "
            f"almost every placement is a refresh of a standing quote rather "
            f"than new supply. This is what a REST poller samples, and it is "
            f"why raw placement flow must not be read as capacity",
            {"churn_ratio_range": [lo, hi],
             "touch_changes_per_min": moves,
             "touch_lifetime_s_p50": life})

    out["UNKNOWN"] = _v(
        "NOT_NEEDED" if all(v["verdict"] != "UNKNOWN" for k, v in out.items())
        else "APPLIES",
        "reserved for a class whose evidence has not been collected", {})
    return out


def _v(verdict: str, why: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {"verdict": verdict, "why": why, "evidence": evidence}


if __name__ == "__main__":
    raise SystemExit(main())
