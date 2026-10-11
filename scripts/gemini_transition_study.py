"""DESCRIPTIVE TRANSITION STUDY. Explicitly NOT a trading strategy.

THE QUESTION: does a TRADABLE PRE-DISAPPEARANCE INTERVAL exist at all? For
each window the side that LATER becomes the persistent no-ask / one-sided
leader is identified RETROSPECTIVELY, and then the earlier checkpoints are
described: was that side still executable, at what ask and VWAP, with what
depth and spread, how long before its ask vanished, and did it ever come back.

THE FUTURE LABEL IS QUARANTINED. `eventual_one_sided_leader` is derived from
observations STRICTLY LATER than the checkpoint being described. It appears
only in the `label` block of each row, never in `causal_features`, and the
module refuses to emit a row whose causal block references it -- an assertion,
not a convention. Its purpose is to establish whether an interval exists, not
to trade on it.

CAUSAL FEATURES ARE PRESERVED SEPARATELY, so the prediction question ("can
the transition be called without leakage?") can be asked later from this same
artifact without rebuilding it. Every causal field is computed from
observations at or BEFORE its own checkpoint.

NO THRESHOLD OPTIMIZATION. Nothing is fitted, selected or tuned. This is a
description of 133 windows while the recorder accumulates the first 180
seconds the corpus does not have.

Accounting label: historical replay on Gemini's own recorded books and
settlements. No order was sent.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

LABEL = "GEMINI_TRANSITION_STUDY_V1"
#: a side counts as the PERSISTENT one-sided leader only if it leads the
#: one-sided region and is never dislodged inside it. One isolated one-sided
#: tick is not a transition.
MIN_PERSISTENT_ONE_SIDED = 2


def book_state(r: Dict[str, Any]) -> str:
    a = r.get("up_ask") is not None
    b = r.get("bid") is not None
    return "TWO_SIDED" if (a and b) else ("ONE_SIDED" if (a or b) else "EMPTY")


def leader(r: Dict[str, Any]) -> Optional[str]:
    a, b = r.get("up_ask"), r.get("bid")
    if a is not None and b is not None:
        return "UP" if (a + b) / 2.0 > 0.50 else "DOWN"
    if b is not None:
        return "UP" if b > 0.50 else "DOWN"
    if a is not None:
        return "UP" if a > 0.50 else "DOWN"
    return None


def side_quote(r: Dict[str, Any], side: str
               ) -> Tuple[Optional[float], Optional[float]]:
    """Executable ask and its depth for one side. DOWN pays 1 - the YES bid."""
    if side == "UP":
        return ((None, None) if r.get("up_ask") is None
                else (float(r["up_ask"]),
                      None if r.get("up_ask_sz") is None
                      else float(r["up_ask_sz"])))
    return ((None, None) if r.get("bid") is None
            else (round(1.0 - float(r["bid"]), 6),
                  None if r.get("down_sz") is None
                  else float(r["down_sz"])))


def eventual_one_sided_leader(rows: Dict[float, Dict[str, Any]]
                              ) -> Dict[str, Any]:
    """RETROSPECTIVE. The persistent one-sided leader, or None.

    Walks the window in time order and takes the leader of the final
    uninterrupted run of ONE_SIDED observations. Requires at least
    MIN_PERSISTENT_ONE_SIDED of them, so a single flicker is not a transition.
    """
    order = sorted(rows, reverse=True)       # earliest first
    run: List[Tuple[float, str]] = []
    best: List[Tuple[float, str]] = []
    for c in order:
        r = rows[c]
        if book_state(r) == "ONE_SIDED":
            ld = leader(r)
            if run and ld != run[-1][1]:
                run = []
            if ld is not None:
                run.append((c, ld))
                if len(run) >= len(best):
                    best = list(run)
        else:
            run = []
    if len(best) < MIN_PERSISTENT_ONE_SIDED:
        return {"side": None, "why": "no persistent one-sided run",
                "run_length": len(best)}
    return {"side": best[-1][1],
            "run_length": len(best),
            "run_first_tte": best[0][0],
            "run_last_tte": best[-1][0],
            "definition": ("the leader of the longest uninterrupted ONE_SIDED "
                           "run, requiring >= %d observations"
                           % MIN_PERSISTENT_ONE_SIDED)}


def causal_features(rows: Dict[float, Dict[str, Any]], cp: float
                    ) -> Dict[str, Any]:
    """Everything knowable AT cp, from observations at tte >= cp only."""
    order = [c for c in sorted(rows, reverse=True) if c >= cp]
    here = rows[cp]
    hist = [rows[c] for c in order]
    lds = [leader(r) for r in hist]
    lds = [x for x in lds if x is not None]
    flips = sum(1 for i in range(1, len(lds)) if lds[i] != lds[i - 1])
    px = here.get("coinbase")
    mv30 = mv60 = None
    for back, slot in ((30.0, "mv30"), (60.0, "mv60")):
        want = cp + back
        cand = [rows[c] for c in order
                if abs(c - want) <= 15.0 and rows[c].get("coinbase")
                is not None]
        if cand and px is not None:
            v = float(px) - float(cand[0]["coinbase"])
            if slot == "mv30":
                mv30 = v
            else:
                mv60 = v
    refs = [float(r["coinbase"]) for r in hist if r.get("coinbase")
            is not None]
    up_ask, up_dep = side_quote(here, "UP")
    dn_ask, dn_dep = side_quote(here, "DOWN")
    return {
        "checkpoint_s": cp,
        "book_state": book_state(here),
        "current_leader": leader(here),
        "up_executable_ask": up_ask, "up_depth": up_dep,
        "down_executable_ask": dn_ask, "down_depth": dn_dep,
        "two_sided_ask_sum": (None if (up_ask is None or dn_ask is None)
                              else round(up_ask + dn_ask, 6)),
        "spread": (None if (here.get("up_ask") is None
                            or here.get("bid") is None)
                   else round(float(here["up_ask"]) - float(here["bid"]), 6)),
        "prior_leadership_flips": flips,
        "observations_so_far": len(hist),
        "proxy_reference": px,
        "proxy_distance_from_strike": here.get("cb_dist"),
        "proxy_move_30s": None if mv30 is None else round(mv30, 3),
        "proxy_move_60s": None if mv60 is None else round(mv60, 3),
        "proxy_realized_range_so_far": (None if len(refs) < 2
                                        else round(max(refs) - min(refs), 3)),
        "leakage_note": ("every field here uses observations at tte >= this "
                         "checkpoint only; the eventual-leader label is NOT "
                         "among them"),
    }


def describe(rows: Dict[float, Dict[str, Any]], tk: str) -> Dict[str, Any]:
    order = sorted(rows, reverse=True)
    winner = None
    for c in order:
        if rows[c].get("up_won") is not None:
            winner = "UP" if rows[c]["up_won"] else "DOWN"
            break
    lab = eventual_one_sided_leader(rows)
    side = lab["side"]
    per_cp: List[Dict[str, Any]] = []
    gone_at: Optional[float] = None
    for c in order:
        feats = causal_features(rows, c)
        row: Dict[str, Any] = {"causal_features": feats}
        if side is not None:
            ask, dep = side_quote(rows[c], side)
            # VWAP for the whole displayed size at that level: this tape
            # carries ONE level per side, so VWAP == the touch and that is
            # stated rather than implied to be a multi-level average.
            row["label"] = {
                "eventual_one_sided_leader": side,
                "was_still_executable": ask is not None,
                "its_executable_ask": ask,
                "its_executable_vwap": ask,
                "vwap_note": ("this tape records ONE level per side, so VWAP "
                              "equals the touch; the recorder's full ladders "
                              "will make it a real VWAP"),
                "its_depth": dep,
                "FUTURE_LABEL_WARNING": ("eventual_one_sided_leader is "
                                         "derived from LATER observations and "
                                         "must never be an entry feature"),
            }
            if ask is None and gone_at is None and c < (
                    lab.get("run_first_tte") or 1e9) + 1e-9:
                pass
        per_cp.append(row)
    # disappearance / reappearance of the eventual leader's ask
    timeline = []
    if side is not None:
        for c in order:
            ask, dep = side_quote(rows[c], side)
            timeline.append((c, ask is not None))
        present = [c for c, ok in timeline if ok]
        absent = [c for c, ok in timeline if not ok]
        last_present = min(present) if present else None
        first_absent_after_present = None
        reappeared = False
        if last_present is not None:
            later_absent = [c for c, ok in timeline if not ok and c
                            > last_present]
            # absence BEFORE the last presence, in time order, means it came
            # back after vanishing
            earlier = [(c, ok) for c, ok in timeline if c > last_present]
            reappeared = any(not ok for c, ok in earlier)
            after = [c for c, ok in timeline if c < last_present]
            first_absent_after_present = max(after) if after else None
    else:
        present = absent = []
        last_present = first_absent_after_present = None
        reappeared = False
    return {
        "window": tk, "winner": winner,
        "label_block": dict(lab, FUTURE_LABEL_WARNING=(
            "RETROSPECTIVE. Derived from later observations. Quarantined to "
            "the label block; never a causal feature.")),
        "label_matched_settlement": (None if (side is None or winner is None)
                                     else side == winner),
        "state_sequence": [(c, book_state(rows[c])) for c in order],
        "eventual_leader_ask_present_at": sorted(present, reverse=True),
        "eventual_leader_ask_absent_at": sorted(absent, reverse=True),
        "last_tte_its_ask_was_present": last_present,
        "seconds_from_last_presence_to_expiry": last_present,
        "its_ask_reappeared_after_vanishing": reappeared,
        "tradable_pre_disappearance_interval_s": (
            None if last_present is None else {
                "from_tte": max(present), "to_tte": last_present,
                "span_s": round(max(present) - last_present, 1),
                "checkpoints_in_interval": len(present),
                "note": ("the span over which the eventual leader was "
                         "executable at least at these checkpoints; the grid "
                         "cannot prove continuity inside it")}),
        "checkpoints": per_cp,
    }


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder",
                    default="analysis/gemini/info_edge/ie_tte_ladder.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    raw = [r for r in json.load(open(a.ladder, encoding="utf-8"))
           if str(r.get("ticker", "")).startswith("BTC05M")
           and r.get("tte") is not None]
    by: Dict[str, Dict[float, Dict[str, Any]]] = collections.defaultdict(dict)
    for r in raw:
        by[r["ticker"]][float(r["tte"])] = r
    out = [describe(rows, tk) for tk, rows in by.items()]

    # LEAKAGE ASSERTION, not a convention: no causal block may name the label.
    for w in out:
        for cp in w["checkpoints"]:
            keys = set(cp["causal_features"])
            assert "eventual_one_sided_leader" not in keys, w["window"]
            assert not any("eventual" in k for k in keys), w["window"]

    labelled = [w for w in out if w["label_block"]["side"]]
    spans = [w["tradable_pre_disappearance_interval_s"]["span_s"]
             for w in labelled
             if w["tradable_pre_disappearance_interval_s"]]
    lasts = [w["last_tte_its_ask_was_present"] for w in labelled
             if w["last_tte_its_ask_was_present"] is not None]
    rep = {
        "label": LABEL, "duration": "BTC5M",
        "descriptive_only": ("not a trading strategy; the future label is "
                             "quarantined and asserted out of the causal "
                             "blocks"),
        "no_threshold_optimization": True,
        "windows": len(out),
        "windows_with_a_persistent_one_sided_leader": len(labelled),
        "label_matched_settlement_pct": (
            None if not labelled else round(
                100.0 * sum(1 for w in labelled
                            if w["label_matched_settlement"])
                / len(labelled), 2)),
        "eventual_leader_ever_executable": sum(
            1 for w in labelled if w["last_tte_its_ask_was_present"]
            is not None),
        "eventual_leader_NEVER_executable": sum(
            1 for w in labelled if w["last_tte_its_ask_was_present"] is None),
        "its_ask_reappeared_after_vanishing": sum(
            1 for w in labelled if w["its_ask_reappeared_after_vanishing"]),
        "last_tte_its_ask_was_present": (
            None if not lasts else {
                "median": round(statistics.median(lasts), 1),
                "min": min(lasts), "max": max(lasts),
                "distribution": dict(collections.Counter(lasts))}),
        "pre_disappearance_span_s": (
            None if not spans else {
                "median": round(statistics.median(spans), 1),
                "p10": round(sorted(spans)[len(spans) // 10], 1),
                "max": max(spans)}),
        "coverage_caveat": ("this corpus starts at tte=120s (T+180s), so "
                            "every span here is a LOWER BOUND truncated by "
                            "the grid's own start. The first 180 seconds are "
                            "exactly what the recorder is now collecting."),
        "windows_detail": out,
        "accounting_label": ("historical replay on Gemini's own recorded "
                             "books and settlements; no order was sent"),
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rep, indent=2, sort_keys=True,
                                default=str) + "\n")
    print("%s windows=%d labelled=%d never_executable=%d reappeared=%d"
          % (LABEL, rep["windows"],
             rep["windows_with_a_persistent_one_sided_leader"],
             rep["eventual_leader_NEVER_executable"],
             rep["its_ask_reappeared_after_vanishing"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
