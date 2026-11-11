"""L5_PASSIVE_SURFACE_SHADOW_V1 -- four placements, one observation stream.

OWNER 2026-09-17. Read-only, zero orders, no theoretical fill in any economic
number.

THE SHADOW DECIDES BEFORE IT SEES. Each arm builds its surface from the book
snapshot at decision time t, and every fact used to judge that surface comes
from t' > t. Nothing reacts to the activity feed and then calls itself a
prediction -- the recorder's tape is replayed forward and the decision index is
never allowed to look right.

ONE SHARED OBSERVATION STREAM, BY CONSTRUCTION. All four arms are evaluated on
the SAME `book_full_joint` rows and the SAME `print_tape` rows from
L5_BOOK_CONTEXT_V1, in the same order. Placement is the only axis that varies;
inventory policy is held at FIDELITY for every arm, so a difference in outcome
cannot be a difference in two things at once.

SIX STATES, NEVER COLLAPSED:

    RESTABLE        a legal maker order at this instant. Nothing more.
    BOOK_REACHED    the touch later moved to or through our price.
                    Executability and staleness. NOT flow.
    TRADE_FLOW      a print actually occurred at or through our price.
                    Real opposing flow. Still not our fill.
    EVIDENCED_FILL  VOLUME_AHEAD_CLEARED: the size resting at our price when
                    we placed has since been consumed by prints at or through
                    it. The ONLY thing that moves inventory here, and still a
                    conservative model -- queue position is unpublished and we
                    assume we joined behind everything.
    SETTLED         the market resolved. Not reached in a shadow.
    REALIZED        venue cash confirmed it. Never reached in a shadow.

POLYMARKET PAIR MAPPING. Two outcome tokens, both BOUGHT: a surface rests a BID
on Up at a and a BID on Down at b, and the pair costs a + b. That is a
different object from the Gemini one-contract synthetic and the two are never
mixed.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import l5_residual_episode_clock as CLK

LABEL = "L5_PASSIVE_SURFACE_SHADOW_V1"
TICK = 0.01
STATES = ("RESTABLE", "BOOK_REACHED", "TRADE_FLOW", "EVIDENCED_FILL",
          "SETTLED", "REALIZED")
RECYCLE_MARKS_S = CLK.MARKS_S           # 5/15/30/60/120/300, preregistered
UNIT = 5.0                              # the venue's declared order_min_size

ARMS = ("inferred_l5_surface", "nearest_maker", "one_behind", "pair_target")

#: the frozen control: the rule the live probe actually used. It prices to a
#: pair target and therefore CROSSES whenever the complement is cheap, which is
#: the mechanism the frozen PMUS sweep measured rejecting 47% of EXTENDs.
PAIR_TARGET = 0.98


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def touch(side_book: dict) -> Tuple[Optional[float], Optional[float]]:
    bids = side_book.get("bids") or []
    asks = side_book.get("asks") or []
    bb = max((p for p, _z in bids), default=None)
    aa = min((p for p, _z in asks), default=None)
    return bb, aa


def size_at(side_book: dict, which: str, px: float) -> Optional[float]:
    lv = side_book.get("bids" if which == "bid" else "asks")
    if lv is None:
        return None
    return sum(z for p, z in lv if abs(p - px) < 1e-9)


# ---------------------------------------------------------------------------
# the four placements. Each returns bids to rest on THIS outcome's book.

def place(arm: str, bb: Optional[float], aa: Optional[float],
          other_bb: Optional[float], spec: Dict[str, Any]) -> List[float]:
    if bb is None or aa is None:
        return []
    if arm == "nearest_maker":
        return [round(aa - TICK, 4)] if aa - TICK >= 0.01 else []
    if arm == "one_behind":
        return [round(aa - 2 * TICK, 4)] if aa - 2 * TICK >= 0.01 else []
    if arm == "pair_target":
        # the live rule: pay whatever makes the pair hit its target against the
        # other outcome's touch. It is free to cross, and that is the point of
        # keeping it as the control.
        if other_bb is None:
            return []
        want = round(PAIR_TARGET - other_bb, 4)
        return [want] if 0.01 <= want <= 0.99 else []
    if arm == "inferred_l5_surface":
        # a ladder behind the touch, from the observed parent-order geometry
        out = []
        for k in spec.get("ladder_ticks", (1, 2, 3)):
            px = round(aa - k * TICK, 4)
            if px >= 0.01:
                out.append(px)
        return out
    return []


class Quote:
    __slots__ = ("arm", "outcome", "px", "qty", "t", "volume_ahead",
                 "restable", "post_only_rejected", "book_reached",
                 "trade_flow", "cleared", "filled", "fill_t",
                 "touch_at_decision", "inv_before")

    def __init__(self, arm, outcome, px, qty, t, volume_ahead, restable,
                 touch_at_decision, inv_before):
        self.arm, self.outcome, self.px, self.qty, self.t = arm, outcome, px, qty, t
        self.volume_ahead = volume_ahead
        self.restable = restable
        self.post_only_rejected = not restable
        self.book_reached = self.trade_flow = self.filled = False
        self.cleared = 0.0
        self.fill_t = None
        self.touch_at_decision = touch_at_decision
        self.inv_before = inv_before


class ArmState:
    def __init__(self, arm: str):
        self.arm = arm
        self.quotes: List[Quote] = []
        self.inv = {"Up": 0.0, "Down": 0.0}
        self.usd = {"Up": 0.0, "Down": 0.0}
        self.episodes: List[dict] = []
        self.n_placed = 0

    @property
    def residual(self):
        return self.inv["Up"] - self.inv["Down"]

    @property
    def matched(self):
        return min(self.inv["Up"], self.inv["Down"])


# ---------------------------------------------------------------------------
# L5_PASSIVE_SURFACE_SPEC_V1 -- every field carries its evidence and its
# confidence, and an unknown is written as an unknown.

SPEC: Dict[str, Any] = {
    "label": "L5_PASSIVE_SURFACE_SPEC_V1",
    "scope": "BTC15m / Polymarket. Not promoted to any other cohort or venue.",
    "base_quantity": {
        "value": "base lot 100 shares; working sizes median 24-38",
        "evidence": ("1,142 chain-proven parents, 0 receipts missing: 100 is "
                     "the dominant mode and appears in EVERY probability "
                     "regime (0.131-0.301)"),
        "confidence": "HIGH that 100 is a real lot; MEDIUM on the working range",
        "unknown": ("executed_qty is a LOWER BOUND on order size -- a 200 that "
                    "filled 100 and cancelled is indistinguishable from a 100"),
    },
    "sizing_function": {
        "value": "NOT dollar-scaled. Share count is the controlled quantity.",
        "evidence": ("cv(executed_qty) = 0.797 against cv(notional) = 1.212 -- "
                     "a probability-scaled notional rule would make notional "
                     "the TIGHTER quantity and it is 52% more dispersed"),
        "confidence": "HIGH (falsification, not a fit)",
        "unknown": ("why 100 is used 2.3x more at p>0.90 (0.301) than at "
                    "0.70-0.90 (0.131). Frequency varies with regime even "
                    "though the lot does not."),
    },
    "inventory_conditioning": {
        "value": "WEAK and NOT monotone",
        "evidence": ("median executed qty by inventory: 0 -> 37.2, <=25 -> "
                     "24.4, 25-100 -> 28.5, 100-400 -> 26.2, >400 -> 38.0"),
        "confidence": "LOW -- no clean scaling law is supported",
        "unknown": "whether price skew (rather than size) carries the conditioning",
    },
    "simultaneous_levels": {
        "value": "multi-level ladder, 1-3 ticks apart",
        "evidence": ("290 distinct same-outcome parents within 5s at different "
                     "prices; tick gaps 1 (x113), 2 (x57), 3 (x37); median "
                     "separation 3s"),
        "confidence": "HIGH that ladders exist; MEDIUM on the depth",
        "unknown": ("levels that never executed are invisible, so the ladder's "
                    "full width is UNOBSERVABLE_OR_UNRESOLVED"),
    },
    "ladder_ticks": (1, 2, 3),
    "level_is_not_size_specific": {
        "value": "the 100-lot is NOT specifically a ladder rung",
        "evidence": "0.3674 of 100s sit in a ladder against 0.3700 of all others",
        "confidence": "HIGH (the two are indistinguishable)",
    },
    "two_sided": {
        "value": ("YES, but not tick-simultaneous. Both outcomes are worked on "
                  "a ~18s median cadence."),
        "evidence": ("1,015 opposite-outcome parent pairs: 3.8% within 1s, "
                     "20.2% within 5s, 44.1% within 15s, 64.0% within 30s, "
                     "79.9% within 60s; median dt 18s"),
        "confidence": "HIGH",
        "note": ("the implied pair is cheaper the closer the two sides are in "
                 "time -- share below 1.00 falls 0.769 (same second) -> 0.644 "
                 "(5s) -> 0.565 (60s), with a median of 0.99 at every horizon"),
    },
    "refresh_cadence": {
        "value": "a new parent roughly every 8s in a market",
        "evidence": "median gap 8s, p25 3s, p75 21s; 16.0% within 1s, 38.4% within 5s",
        "confidence": "MEDIUM-HIGH",
    },
    "persistence": {
        "value": "execution-lifetime LOWER BOUND p50 2s, p90 6s, max 28s",
        "evidence": "11.0-11.2% of parents execute at more than one timestamp",
        "confidence": "HIGH as a lower bound, NONE as an order lifetime",
        "unknown": ("when an order was submitted or cancelled is not on the "
                    "chain. This is not persistence and must never be read as "
                    "rest duration."),
    },
    "withdrawal_conditions": {
        "value": "UNKNOWN",
        "evidence": "none -- cancellations are not observable in any source held here",
        "confidence": "NONE",
    },
    "tte_dependence": {
        "value": ("matched economics concentrate late: TTE <=60s earned "
                  "258.7 -> 649.3 bp/$ TRAIN -> OOS while >700s earned "
                  "48.2 -> -11.0"),
        "evidence": "MATCHED_SHARE_LINEAGE_V1 (frozen)",
        "confidence": "MEDIUM -- historical, and explicitly NOT a gate",
    },
    "reversal_relationship": {
        "value": "reversals, not drift, generate matched pairs",
        "evidence": ("0 reversals -> +14.8 bp/$ OOS, 6+ -> +93.9; L5's own "
                     "venue runs 3.49 reversals/min at a 1.0-tick spread"),
        "confidence": "MEDIUM",
    },
    "recycling_clock": {
        "value": "60-300s on L5's own episodes, NOT 5-30s",
        "evidence": ("prospective residual clock: 0.00/0.00/0.00/0.40/0.60/"
                     "1.00 at 5/15/30/60/120/300s, n=6 episodes"),
        "confidence": "LOW (tiny n) and the early marks are CENSORED by a "
                      "22.2s median public-feed lag",
        "unknown": ("whether the 5-30s zeros are real or publication-lag "
                    "artifacts -- they must not be read literally"),
    },
}


# ---------------------------------------------------------------------------
# the replay

def load(paths: Sequence[str]) -> Dict[str, dict]:
    """One shared observation stream per market, from the recorder's tape."""
    mk: Dict[str, dict] = collections.defaultdict(
        lambda: {"books": [], "prints": collections.defaultdict(list),
                 "token_of": {}})
    for p in paths:
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = r.get("kind")
            if k == "book_full_joint":
                s = str(r.get("slug"))
                t = None
                for oc, d in (r.get("outcomes") or {}).items():
                    mk[s]["token_of"][str(d.get("token"))] = oc
                    t = t or _f(d.get("L5_OBSERVATION_CLOCK"))
                if t is not None:
                    mk[s]["books"].append((t, r["outcomes"],
                                           _f(r.get("tte_s"))))
            elif k == "print_tape":
                s = str(r.get("slug"))
                for x in r.get("prints") or []:
                    mk[s]["prints"][str(x.get("asset"))].append(
                        (_f(x.get("t")), _f(x.get("price")), _f(x.get("size"))))
    for s, d in mk.items():
        d["books"].sort(key=lambda x: x[0])
        for a in d["prints"]:
            d["prints"][a].sort(key=lambda x: x[0] or 0.0)
    return dict(mk)


def replay(paths: Sequence[str], *, decide_every_s: float = 10.0,
           horizon_s: float = 300.0) -> Dict[str, Any]:
    data = load(paths)
    arms = {a: ArmState(a) for a in ARMS}
    rows: List[dict] = []
    for slug, d in data.items():
        books, prints = d["books"], d["prints"]
        tok_of = d["token_of"]
        by_oc_prints: Dict[str, list] = collections.defaultdict(list)
        for a, lst in prints.items():
            by_oc_prints[tok_of.get(a, "?")].extend(lst)
        for oc in by_oc_prints:
            by_oc_prints[oc].sort(key=lambda x: x[0] or 0.0)
        if len(books) < 3:
            continue
        last_decision = -1e9
        for i, (t, joint, tte) in enumerate(books):
            if t - last_decision < decide_every_s:
                continue
            last_decision = t
            tt = {oc: touch(b) for oc, b in joint.items()}
            for arm, st in arms.items():
                for oc, b in joint.items():
                    bb, aa = tt.get(oc, (None, None))
                    other = "Down" if oc == "Up" else "Up"
                    obb = tt.get(other, (None, None))[0]
                    for px in place(arm, bb, aa, obb, SPEC):
                        restable = (aa is not None and px < aa)
                        q = Quote(arm, oc, px, UNIT, t,
                                  size_at(b, "bid", px) or 0.0, restable,
                                  (bb, aa), dict(st.inv))
                        st.n_placed += 1
                        rows.append(_evaluate(q, books, by_oc_prints[oc],
                                              i, horizon_s, slug, tte, st))
    return {"label": LABEL, "spec": SPEC,
            "markets": len(data), "quotes": len(rows),
            "arms": {a: summarise(a, rows) for a in ARMS},
            "rows": rows}


def _evaluate(q: Quote, books: list, oc_prints: list, i0: int,
              horizon_s: float, slug: str, tte: Optional[float],
              st: ArmState) -> dict:
    """Everything after the decision index. The decision never looks right."""
    end = q.t + horizon_s
    for j in range(i0 + 1, len(books)):
        t2, joint2, _ = books[j]
        if t2 > end:
            break
        b2 = joint2.get(q.outcome) or {}
        bb2, _aa2 = touch(b2)
        if bb2 is not None and bb2 <= q.px:
            q.book_reached = True
    for (tp, px, sz) in oc_prints:
        if tp is None or tp <= q.t or tp > end:
            continue
        if px is not None and px <= q.px:
            q.trade_flow = True
            q.cleared += (sz or 0.0)
            if not q.filled and q.cleared > q.volume_ahead:
                q.filled = True
                q.fill_t = tp
    state = ("EVIDENCED_FILL" if q.filled else
             "TRADE_FLOW" if q.trade_flow else
             "BOOK_REACHED" if q.book_reached else
             "RESTABLE" if q.restable else "POST_ONLY_REJECTED")
    if q.filled:
        st.inv[q.outcome] += q.qty
        st.usd[q.outcome] += q.qty * q.px
    # opposite-side recycling clock, from the evidenced fill only
    recycle = None
    if q.filled:
        other = "Down" if q.outcome == "Up" else "Up"
        recycle = {"opposite_outcome": other,
                   "marks": {"t_plus_%ds" % m: None for m in RECYCLE_MARKS_S}}
    return {
        "kind": "surface_quote", "label": LABEL, "slug": slug,
        "arm": q.arm, "outcome": q.outcome,
        "decision_time": q.t, "tte_s_at_decision": tte,
        "touch_at_decision": {"bid": q.touch_at_decision[0],
                              "ask": q.touch_at_decision[1]},
        "price": q.px, "qty": q.qty,
        "ticks_from_touch": (None if q.touch_at_decision[1] is None else
                             round((q.touch_at_decision[1] - q.px) / TICK, 2)),
        "inventory_before": q.inv_before,
        "volume_ahead_at_placement": q.volume_ahead,
        "RESTABLE": q.restable,
        "POST_ONLY_REJECTED_counterfactual": q.post_only_rejected,
        "BOOK_REACHED": q.book_reached,
        "TRADE_FLOW": q.trade_flow,
        "volume_cleared": round(q.cleared, 4),
        "VOLUME_AHEAD_CLEARED": q.filled,
        "EVIDENCED_FILL": q.filled,
        "fill_time": q.fill_t,
        "fill_latency_s": (round(q.fill_t - q.t, 2) if q.fill_t else None),
        "inventory_after": dict(st.inv),
        "recycling": recycle,
        "SETTLED": False, "REALIZED": False,
        "labels": ("EVIDENCED_FILL is VOLUME_AHEAD_CLEARED, a conservative "
                   "queue model, not a venue confirmation. SETTLED and "
                   "REALIZED are unreachable in a shadow and stay false."),
    }


def summarise(arm: str, rows: Sequence[dict]) -> Dict[str, Any]:
    r = [x for x in rows if x["arm"] == arm]
    n = len(r) or 1
    fills = [x for x in r if x["EVIDENCED_FILL"]]
    lat = sorted(x["fill_latency_s"] for x in fills if x["fill_latency_s"])
    return {
        "quotes": len(r),
        "restable_rate": round(sum(1 for x in r if x["RESTABLE"]) / n, 4),
        "post_only_rejection_counterfactual": round(
            sum(1 for x in r if x["POST_ONLY_REJECTED_counterfactual"]) / n, 4),
        "book_reached_rate": round(sum(1 for x in r if x["BOOK_REACHED"]) / n, 4),
        "trade_flow_rate": round(sum(1 for x in r if x["TRADE_FLOW"]) / n, 4),
        "evidenced_fill_rate": round(len(fills) / n, 4),
        "median_fill_latency_s": (lat[len(lat) // 2] if lat else None),
        "median_ticks_from_touch": (
            sorted(x["ticks_from_touch"] for x in r
                   if x["ticks_from_touch"] is not None)[len(r) // 2]
            if any(x["ticks_from_touch"] is not None for x in r) else None),
        "turnover_usd": round(sum(x["price"] * x["qty"] for x in fills), 2),
        "labels": ("rates are over QUOTES PLACED. RESTABLE, BOOK_REACHED, "
                   "TRADE_FLOW and EVIDENCED_FILL are nested but separate "
                   "states and none of them is SETTLED or REALIZED."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", default="data/l5_book_context/*.jsonl")
    ap.add_argument("--decide-every-s", type=float, default=10.0)
    ap.add_argument("--horizon-s", type=float, default=300.0)
    ap.add_argument("--out", default="analysis/l5/surface/shadow_v1.json")
    a = ap.parse_args()
    paths = sorted(glob.glob(a.tape))
    res = replay(paths, decide_every_s=a.decide_every_s, horizon_s=a.horizon_s)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in res.items() if k != "rows"}
    keep["n_rows"] = len(res["rows"])
    Path(a.out).write_text(json.dumps(keep, indent=2, default=str),
                           encoding="utf-8")
    print(json.dumps({"markets": res["markets"], "quotes": res["quotes"],
                      "arms": {k: {kk: vv for kk, vv in v.items()
                                   if kk != "labels"}
                               for k, v in res["arms"].items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
