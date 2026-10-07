"""LANE D — sequential ACTION-PREDICTION SCORECARD for the current L5 model.

HISTORICAL REPLAY SCORING ONLY. Nothing here is submitted, filled, settled or
realized profit for our account. Every figure describes the public activity of
Polymarket trader `l5zn1bwom8etsk` and how well OUR EXISTING behavioural model
predicts its NEXT ACTION.

WHY THIS EXISTS. CLAUDE.md: "Positive aggregate replay P&L with materially
different behavior is not sufficient evidence of replication." So we do not
score P&L here. At each observable L5 state we ask what the model says the next
action is, and compare it to what L5 actually did — alongside NAIVE BASELINES
computed from the SAME training rows and evaluated on the SAME test rows.

WHAT IS SCORED (fields)
  exposure_action   relation in {open, extend, complete, sell}   (the repo's own
                    vocabulary from `src.polymarket_l5.action_model._relation`)
  switch            next buy on the opposite outcome to the previous buy
                    (`analysis/l5/EXTEND_AND_SWITCH.md` vocabulary)
  side              Up / Down of the next fill
  size_bucket       q<5 / q5-15 / q15-40 / q>40 (production SIZE_BUCKETS)
  timing_bucket     gap<2s / gap2-10 / gap10-30 / gap>30 (production cadence
                    boundaries, shared with analysis/l5/act_wait/model.py)

WHAT IS NOT SCORED
  price_offset_ticks_vs_touch  — UNSCORABLE. There is no contemporaneous order
                    book for L5's windows (`analysis/l5/L5_TOUCH_PROXIMITY.md`
                    §0: "L5's distance from the touch is NOT COMPUTABLE"), and
                    the deployed model emits no touch-relative price at all.
  withdraw          — UNSCORABLE. Cancels/reprices of resting orders are not in
                    any corpus; "L5 waited" and "L5 quoted and was not hit" are
                    the same observation (L5_OPERATING_MODEL.md §2a).
  wait              — NOT RE-SCORED HERE. It is not a row in the fill-transition
                    space; it needs the opportunity grid already built and
                    scored in `analysis/l5/act_wait/` (Stage 1), whose numbers
                    this scorecard cites rather than duplicates.

MODELS SCORED (both are EXISTING implementations, re-fitted on train rows only;
neither is modified or replaced)
  M_V1    the deployed state-only lookup — `action_model.state_key`
          (price band x tte bucket x |residual| bucket) -> modal action.
  M_V1_1  the deployed v2 family — the same base state plus the real sequential
          context (`last_rel`, `last_size`) with alpha=10 shrinkage and
          full -> base -> marginal backoff, exactly as
          `src/polymarket_l5/action_model_v2.py` and the V1.1 fix in
          `analysis/l5/act_wait/FINDINGS.md` §1.
  M_T     timing, the Stage-3 family: (base state, since_last bucket) -> modal
          gap bucket, backoff to since bucket then marginal.

BASELINES, for every field, from the same train rows
  majority      always the train modal class
  persistence   the previous row's value of that same field (window-local; the
                first transition in a window has no predecessor and falls back
                to the train modal class — counted and reported)
  marginal      seeded draw from the train class distribution (reported with its
                analytic expectation sum p_i^2 so the draw cannot flatter it)

PROTOCOL — no tuning anywhere. Every bucket boundary, feature set and shrinkage
constant is taken from the already-frozen production modules; nothing in this
file was chosen to improve a score, so there is no discovery cell. Splits are
CHRONOLOGICAL BY WINDOW with zero window overlap: a window is wholly in train or
wholly in test. The headline split is global (~70/30 of transitions). Strata
that lie entirely on one side of the global cut (notably era E1, which ends
2026-04-20) are additionally scored under a WITHIN-STRATUM chronological split,
with the model re-fitted on that stratum's train rows only. Held-out data never
touches a count used to score it; `tests/test_l5_action_scorecard.py` pins that.

    python3 -m analysis.l5.action_scorecard.score
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.polymarket_l5 import action_model as am

ROOT = Path(__file__).resolve().parents[3]
SEQUENCE_CSV = ROOT / "analysis" / "l5" / "l5_canonical_sequence.csv.gz"
OUT_DIR = ROOT / "analysis" / "l5" / "action_scorecard"

#: analysis/l5/norebate/q1.py FIRST_REBATE — reused, not redefined.
FIRST_REBATE_DAY = "2026-04-21"
#: analysis/l5/canon_regimes.json primary change points, reused verbatim.
REGIME_BOUNDS = (("R1", "2026-03-19", "2026-04-24"),
                 ("R2", "2026-04-25", "2026-06-22"),
                 ("R3", "2026-06-23", "2026-08-18"),
                 ("R4", "2026-08-19", "2026-08-31"))
#: analysis/l5/act_wait/model.py GAP_BUCKETS (== the artifact's cadence_modal).
GAP_BUCKETS = (("gap<2s", 2.0), ("gap2-10", 10.0), ("gap10-30", 30.0),
               ("gap>30", float("inf")))
ALPHA = 10.0            # the v2 artifact's own shrinkage strength
TRAIN_FRACTION = 0.70   # fixed a priori; never searched
SEED = 20261007

FIELDS = ("exposure_action", "switch", "side", "size_bucket", "timing_bucket")
UNSCORABLE = {
    "price_offset_ticks_vs_touch": (
        "No contemporaneous order book exists for L5's windows, so the offset "
        "in ticks from the same-side best bid is not computable for any row "
        "(analysis/l5/L5_TOUCH_PROXIMITY.md §0). The deployed model also emits "
        "no touch-relative price. MISSING ARTIFACT: a per-second L2 top-of-book "
        "time series covering L5's traded windows. The only book we hold is "
        "Zenodo 22084977 (2026-08-21..24 BTC15m, analysis/l5/historical_l2/), "
        "whose overlap with the canonical sequence is ZERO windows, and whose "
        "own measurement found 26.4% of L5 maker fills at or above the "
        "prevailing ask — i.e. not placeable as a touch offset at all."),
    "withdraw": (
        "No order-lifecycle data. The corpus is a FILL tape; resting, "
        "cancelled and repriced L5 orders are unobservable, so a WITHDRAW is "
        "indistinguishable from a WAIT. MISSING ARTIFACT: L5 order "
        "placement/cancel events, which the venue does not serve."),
    "wait": (
        "Not a row in the fill-transition space. Scored separately on an "
        "opportunity grid in analysis/l5/act_wait/ (Stage 1); cited, not "
        "re-scored, here."),
}


# --------------------------------------------------------------------------
# row construction
# --------------------------------------------------------------------------

def _f(v, d=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def gap_bucket(seconds: Optional[float]) -> str:
    if seconds is None:
        return "gap>30"
    for name, hi in GAP_BUCKETS:
        if seconds < hi:
            return name
    return "gap>30"


def era_of(day: str) -> str:
    return "E3_INCENTIVE_ERA" if day >= FIRST_REBATE_DAY else "E1_PRE_REBATE"


def regime_of(day: str) -> str:
    for label, lo, hi in REGIME_BOUNDS:
        if lo <= day <= hi:
            return label
    return "UNKNOWN"


def _windows(csv_path: Path = SEQUENCE_CSV) -> Iterable[List[Dict[str, str]]]:
    opener = gzip.open if str(csv_path).endswith(".gz") else open
    with opener(csv_path, "rt", newline="", encoding="utf-8") as fh:
        cur: List[Dict[str, str]] = []
        wid = None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if cur:
                    yield cur
                cur, wid = [], row["window_id"]
            cur.append(row)
        if cur:
            yield cur


def build_rows(csv_path: Path = SEQUENCE_CSV,
               since_epoch: Optional[int] = am.SYSTEMATIC_REGIME_EPOCH
               ) -> List[Dict[str, Any]]:
    """One row per observed L5 state -> next-action transition.

    Every feature on a row is knowable AT the decision instant; every `truth_*`
    is the action that followed. `settle_pnl_per_share` is an OUTCOME, carried
    only for economic weighting of mismatches and never used as a feature.
    """
    rows: List[Dict[str, Any]] = []
    for w in _windows(csv_path):
        if since_epoch is not None and int(_f(w[0]["timestamp"])) < since_epoch:
            continue
        w = sorted(w, key=lambda r: (int(_f(r["timestamp"])),
                                     int(_f(r["trade_seq"]))))
        w_start = int(_f(w[0]["timestamp"]))
        prev_rel, prev_size, prev_gap = "open", "q<5", "gap>30"
        for i in range(1, len(w)):
            prev, nxt = w[i - 1], w[i]
            residual = _f(prev["residual_qty_signed"])
            tte = _f(nxt["seconds_to_settlement"])
            qty = _f(nxt["quantity"])
            gap = _f(nxt["timestamp"]) - _f(prev["timestamp"])
            # context for the PREVIOUS fill (knowable at T)
            if i >= 2:
                prev_rel = am._relation(_f(w[i - 2]["residual_qty_signed"]),
                                        prev["outcome"], prev["side"])
                prev_size = am._bucket(_f(prev["quantity"]), am.SIZE_BUCKETS)
                prev_gap = gap_bucket(_f(prev["timestamp"])
                                      - _f(w[i - 2]["timestamp"]))
            else:
                prev_rel, prev_size, prev_gap = "open", "q<5", "gap>30"
            rel = am._relation(residual, nxt["outcome"], nxt["side"])
            day = nxt["datetime_utc"][:10]
            px = _f(nxt["price"])
            settled = nxt["settlement_outcome"] or ""
            pnl_ps = ((1.0 if nxt["outcome"] == settled else 0.0) - px
                      if settled else None)
            rows.append({
                "window_id": prev["window_id"],
                "window_start": w_start,
                "ts": int(_f(nxt["timestamp"])),
                "day": day,
                "duration": f"{nxt['duration_minutes']}m",
                "asset": nxt["asset"],
                "era": era_of(day),
                "regime": regime_of(day),
                # ---- features (all knowable at the decision instant) ----
                "state": am.state_key(_f(prev["price"]), tte, residual),
                "prev_rel": prev_rel,
                "prev_size_bucket": prev_size,
                "prev_outcome": prev["outcome"],
                "prev_residual": residual,
                "since_last_bucket": prev_gap,
                # ---- truth ----
                "truth_exposure_action": rel,
                "truth_side": nxt["outcome"],
                "truth_switch": "switch" if nxt["outcome"] != prev["outcome"]
                                else "stay",
                "truth_size_bucket": am._bucket(qty, am.SIZE_BUCKETS),
                "truth_timing_bucket": gap_bucket(gap),
                # ---- outcome, for economics only ----
                "qty": qty,
                "price": px,
                "settle_pnl_per_share": pnl_ps,
            })
    return rows


# --------------------------------------------------------------------------
# models (re-fitted on train rows only; same families as production)
# --------------------------------------------------------------------------

def _argmax(counter: Counter) -> Optional[str]:
    if not counter:
        return None
    return max(sorted(counter.items()), key=lambda kv: kv[1])[0]


class ModalLookup:
    """Shrunk conditional frequency table, the deployed model family.

    `keyfn` picks the full key; backoff is full -> base state -> marginal, with
    P(a|full) = (n_full*p_full + ALPHA*p_base) / (n_full + ALPHA).
    """

    def __init__(self, field: str, keyfn, basefn=lambda r: r["state"]):
        self.field, self.keyfn, self.basefn = field, keyfn, basefn
        self.full: Dict[Any, Counter] = defaultdict(Counter)
        self.base: Dict[Any, Counter] = defaultdict(Counter)
        self.marginal: Counter = Counter()

    def fit(self, rows: Sequence[Dict[str, Any]]) -> "ModalLookup":
        t = "truth_" + self.field
        for r in rows:
            self.full[self.keyfn(r)][r[t]] += 1
            self.base[self.basefn(r)][r[t]] += 1
            self.marginal[r[t]] += 1
        return self

    def dist(self, r: Dict[str, Any]) -> Tuple[Dict[str, float], str]:
        def norm(c: Counter) -> Dict[str, float]:
            n = sum(c.values())
            return {k: v / n for k, v in c.items()} if n else {}
        mg = norm(self.marginal)
        bs = self.base.get(self.basefn(r))
        p_base, tier = (norm(bs), "base") if bs else (mg, "marginal")
        fl = self.full.get(self.keyfn(r))
        if not fl:
            return p_base, tier
        n = sum(fl.values())
        p_full = norm(fl)
        keys = set(p_full) | set(p_base)
        return ({k: (n * p_full.get(k, 0.0) + ALPHA * p_base.get(k, 0.0))
                 / (n + ALPHA) for k in keys}, "full")

    def predict(self, r: Dict[str, Any]) -> Optional[str]:
        d, _ = self.dist(r)
        return max(sorted(d.items()), key=lambda kv: kv[1])[0] if d else None


def _k_v1(r):
    return r["state"]


def _k_v11(r):
    return (r["state"], r["prev_rel"], r["prev_size_bucket"])


def _k_timing(r):
    return (r["state"], r["since_last_bucket"])


def fit_models(train: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """field -> {model_name: predictor}. Train rows only."""
    out: Dict[str, Dict[str, Any]] = {}
    for field in ("exposure_action", "switch", "size_bucket"):
        out[field] = {
            "M_V1": ModalLookup(field, _k_v1).fit(train),
            "M_V1_1": ModalLookup(field, _k_v11).fit(train),
        }
    out["timing_bucket"] = {
        "M_T": ModalLookup("timing_bucket", _k_timing,
                           basefn=lambda r: r["since_last_bucket"]).fit(train),
    }
    # SIDE is not emitted by the model as its own field: it is DERIVED from the
    # predicted relation plus the sign of the residual already on the book. On a
    # flat book (relation `open`) the model is silent about side, and we record
    # that silence rather than inventing a side.
    out["side"] = {
        "M_V1": SideFromRelation(out["exposure_action"]["M_V1"]),
        "M_V1_1": SideFromRelation(out["exposure_action"]["M_V1_1"]),
    }
    return out


class SideFromRelation:
    """Derive Up/Down from a predicted relation and the standing residual."""

    def __init__(self, rel_model: ModalLookup):
        self.rel_model = rel_model

    def predict(self, r: Dict[str, Any]) -> Optional[str]:
        rel = self.rel_model.predict(r)
        res = r["prev_residual"]
        if rel in (None, "open", "sell") or abs(res) < 1.0:
            return None          # model-silent, counted as such
        surplus = "Up" if res > 0 else "Down"
        deficit = "Down" if res > 0 else "Up"
        return surplus if rel == "extend" else deficit


# --------------------------------------------------------------------------
# baselines
# --------------------------------------------------------------------------

class Majority:
    def __init__(self, field: str):
        self.field, self.cls = field, None

    def fit(self, rows):
        self.cls = _argmax(Counter(r["truth_" + self.field] for r in rows))
        return self

    def predict(self, r):
        return self.cls


class Persistence:
    """Previous row's value of the same field, window-local.

    Strictly backward-looking: `prev_field` is written from rows already seen
    in the same window, so no future label can reach a prediction.
    """

    def __init__(self, field: str):
        self.field, self.fallback = field, None

    def fit(self, rows):
        self.fallback = _argmax(Counter(r["truth_" + self.field]
                                        for r in rows))
        return self

    def predict_sequence(self, rows: Sequence[Dict[str, Any]]) -> List[str]:
        last: Dict[str, Optional[str]] = {}
        out = []
        for r in rows:
            prev = last.get(r["window_id"])
            out.append(prev if prev is not None else self.fallback)
            last[r["window_id"]] = r["truth_" + self.field]
        return out


class MarginalRandom:
    def __init__(self, field: str, seed: int = SEED):
        self.field, self.seed = field, seed
        self.classes: List[str] = []
        self.weights: List[float] = []

    def fit(self, rows):
        c = Counter(r["truth_" + self.field] for r in rows)
        n = sum(c.values())
        self.classes = sorted(c)
        self.weights = [c[k] / n for k in self.classes]
        return self

    def predict_sequence(self, rows) -> List[str]:
        rng = random.Random(self.seed)
        return rng.choices(self.classes, self.weights, k=len(rows))

    @property
    def analytic_accuracy(self) -> float:
        return sum(w * w for w in self.weights)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def _acc(preds: Sequence[Optional[str]], truth: Sequence[str]) -> Dict[str, Any]:
    scored = [(p, t) for p, t in zip(preds, truth) if p is not None]
    hits = sum(1 for p, t in scored if p == t)
    return {"n_rows": len(truth), "n_scored": len(scored),
            "n_silent": len(truth) - len(scored),
            "accuracy": round(hits / len(scored), 4) if scored else None}


def _confusion(preds, truth) -> Dict[str, Dict[str, Any]]:
    cells: Dict[str, Counter] = defaultdict(Counter)
    for p, t in zip(preds, truth):
        cells[t][p if p is not None else "(silent)"] += 1
    out = {}
    for t, c in sorted(cells.items()):
        n = sum(c.values())
        out[t] = {"n": n,
                  "pred": {k: round(v / n, 4)
                           for k, v in sorted(c.items(), key=lambda kv: -kv[1])}}
    return out


def score_split(train: Sequence[Dict[str, Any]], test: Sequence[Dict[str, Any]],
                label: str, confusions: bool = True) -> Dict[str, Any]:
    """Fit on `train`, score model AND every baseline on exactly `test`."""
    models = fit_models(train)
    res: Dict[str, Any] = {
        "label": label,
        "n_train_rows": len(train), "n_test_rows": len(test),
        "n_train_windows": len({r["window_id"] for r in train}),
        "n_test_windows": len({r["window_id"] for r in test}),
        "window_overlap": len({r["window_id"] for r in train}
                              & {r["window_id"] for r in test}),
        "train_span": (min((r["day"] for r in train), default=None),
                       max((r["day"] for r in train), default=None)),
        "test_span": (min((r["day"] for r in test), default=None),
                      max((r["day"] for r in test), default=None)),
        "fields": {},
    }
    if not train or not test:
        return res
    for field in FIELDS:
        truth = [r["truth_" + field] for r in test]
        entry: Dict[str, Any] = {"class_mix_test": {
            k: round(v / len(truth), 4)
            for k, v in Counter(truth).most_common()}}
        # --- baselines, fitted on the same train rows ---
        maj = Majority(field).fit(train)
        per = Persistence(field).fit(train)
        mar = MarginalRandom(field).fit(train)
        base: Dict[str, Any] = {
            "majority": dict(_acc([maj.predict(r) for r in test], truth),
                             predicts=maj.cls),
            "persistence": _acc(per.predict_sequence(test), truth),
            "marginal_random": dict(
                _acc(mar.predict_sequence(test), truth),
                analytic_expected_accuracy=round(mar.analytic_accuracy, 4)),
        }
        best_name, best_acc = None, -1.0
        for name, b in base.items():
            if b["accuracy"] is not None and b["accuracy"] > best_acc:
                best_name, best_acc = name, b["accuracy"]
        entry["baselines"] = base
        entry["best_baseline"] = {"name": best_name, "accuracy": best_acc}
        # --- models, scored on the same rows ---
        entry["models"] = {}
        for name, m in models.get(field, {}).items():
            preds = [m.predict(r) for r in test]
            sc = _acc(preds, truth)
            sc["lift_over_best_baseline"] = (
                round(sc["accuracy"] - best_acc, 4)
                if sc["accuracy"] is not None else None)
            sc["beats_best_baseline"] = (
                None if sc["accuracy"] is None else sc["accuracy"] > best_acc)
            if confusions:
                sc["confusion"] = _confusion(preds, truth)
            entry["models"][name] = sc
        res["fields"][field] = entry
    return res


def bootstrap_lift(train, test, n_boot: int = 1000, seed: int = SEED
                   ) -> Dict[str, Any]:
    """Window-block bootstrap of (model accuracy - best baseline accuracy).

    Resampling WINDOWS, not rows: consecutive fills inside a window are not
    independent, so a row bootstrap would understate the interval.
    """
    models = fit_models(train)
    by_win: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in test:
        by_win[r["window_id"]].append(r)
    wids = sorted(by_win)
    out: Dict[str, Any] = {}
    for field in FIELDS:
        truth_all = [r["truth_" + field] for r in test]
        maj = Majority(field).fit(train)
        per = Persistence(field).fit(train)
        mar = MarginalRandom(field).fit(train)
        base_pred = {
            "majority": {id(r): maj.predict(r) for r in test},
            "marginal_random": dict(zip((id(r) for r in test),
                                        mar.predict_sequence(test))),
        }
        pers_pred = dict(zip((id(r) for r in test), per.predict_sequence(test)))
        base_pred["persistence"] = pers_pred
        accs = {k: sum(1 for r, t in zip(test, truth_all) if v[id(r)] == t)
                / len(test) for k, v in base_pred.items()}
        best = max(sorted(accs.items()), key=lambda kv: kv[1])[0]
        for mname, m in models.get(field, {}).items():
            mp = {id(r): m.predict(r) for r in test}
            rng = random.Random(seed)
            diffs = []
            for _ in range(n_boot):
                sample = [by_win[rng.choice(wids)] for _ in wids]
                hm = hb = n = 0
                for blk in sample:
                    for r in blk:
                        t = r["truth_" + field]
                        n += 1
                        hm += (mp[id(r)] == t)
                        hb += (base_pred[best][id(r)] == t)
                if n:
                    diffs.append((hm - hb) / n)
            diffs.sort()
            out[f"{field}::{mname}"] = {
                "best_baseline": best,
                "point_lift": round(
                    sum(1 for r, t in zip(test, truth_all) if mp[id(r)] == t)
                    / len(test) - accs[best], 4),
                "ci95": [round(diffs[int(0.025 * len(diffs))], 4),
                         round(diffs[int(0.975 * len(diffs)) - 1], 4)],
                "n_boot": n_boot, "blocks": len(wids),
            }
    return out


def chronological_split(rows: Sequence[Dict[str, Any]],
                        fraction: float = TRAIN_FRACTION
                        ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split by WINDOW START so no window straddles the cut."""
    starts = sorted({(r["window_start"], r["window_id"]) for r in rows})
    per_window = Counter(r["window_id"] for r in rows)
    target, seen, cut = fraction * len(rows), 0, None
    for start, wid in starts:
        if seen >= target:
            cut = start
            break
        seen += per_window[wid]
    if cut is None:
        cut = starts[-1][0]
    train = [r for r in rows if r["window_start"] < cut]
    test = [r for r in rows if r["window_start"] >= cut]
    return train, test


# --------------------------------------------------------------------------
# economics of mismatch (historical replay; never realized P&L)
# --------------------------------------------------------------------------

def mismatch_economics(train, test, model_name: str = "M_V1_1") -> Dict[str, Any]:
    """Attach measurable economic consequence to each field's errors.

    Only two outcome quantities are used, both source-backed per fill:
      * settlement value of the share bought: (1 if outcome == settlement
        else 0) - price, i.e. the realized-at-settlement directional P&L per
        share of that leg, from the canonical sequence's own
        `settlement_outcome`;
      * notional, price * quantity.
    No modelled fills, no rebates, no marks.
    """
    models = fit_models(train)
    rel_m = models["exposure_action"][model_name]
    side_m = models["side"][model_name]
    size_m = models["size_bucket"][model_name]
    tim_m = models["timing_bucket"]["M_T"]

    def mid(bucket: str) -> float:
        return {"q<5": 2.5, "q5-15": 10.0, "q15-40": 27.5, "q>40": 70.0}[bucket]

    out: Dict[str, Any] = {}

    # 1. SIDE errors -> directional settlement consequence per share.
    wrong = [r for r in test
             if side_m.predict(r) is not None
             and side_m.predict(r) != r["truth_side"]
             and r["settle_pnl_per_share"] is not None]
    right = [r for r in test
             if side_m.predict(r) is not None
             and side_m.predict(r) == r["truth_side"]
             and r["settle_pnl_per_share"] is not None]

    def agg(rs):
        if not rs:
            return None
        shares = sum(r["qty"] for r in rs)
        pnl = sum(r["qty"] * r["settle_pnl_per_share"] for r in rs)
        return {"n": len(rs), "shares": round(shares, 1),
                "l5_settlement_pnl_usd": round(pnl, 2),
                "notional_usd": round(sum(r["qty"] * r["price"] for r in rs), 2),
                "pnl_per_share": round(pnl / shares, 5) if shares else None}
    out["side_errors"] = {
        "model": model_name,
        "wrong_side": agg(wrong), "right_side": agg(right),
        "note": ("Per-share settlement P&L of the legs the model mislabels, "
                 "versus the legs it labels correctly. A clone that takes the "
                 "model's side instead of L5's on the wrong rows buys the "
                 "complement at (1 - the pair's other price), so the sign of "
                 "this difference is the directional cost of the side error."),
    }

    # 2. SIZE errors -> notional scale error.
    sz = [(size_m.predict(r), r) for r in test]
    sz = [(p, r) for p, r in sz if p is not None]
    pred_not = sum(mid(p) * r["price"] for p, r in sz)
    true_not = sum(r["qty"] * r["price"] for p, r in sz)
    out["size_errors"] = {
        "model": model_name, "n": len(sz),
        "l5_notional_usd": round(true_not, 2),
        "model_notional_usd": round(pred_not, 2),
        "notional_ratio_model_over_l5": round(pred_not / true_not, 4)
        if true_not else None,
        "bucket_mix_l5": {k: round(v / len(sz), 4) for k, v in
                          Counter(r["truth_size_bucket"] for _, r in sz)
                          .most_common()},
        "bucket_mix_model": {k: round(v / len(sz), 4) for k, v in
                             Counter(p for p, _ in sz).most_common()},
        "note": ("Bucket midpoints (2.5 / 10 / 27.5 / 70 shares) are a stated "
                 "convention, not a measurement; the ratio is indicative of "
                 "scale error, not an exact dollar figure."),
    }

    # 3. TIMING errors -> which cadence regime is lost.
    tpred = [tim_m.predict(r) for r in test]
    tt = [r["truth_timing_bucket"] for r in test]
    miss_fast = [r for p, r in zip(tpred, test)
                 if r["truth_timing_bucket"] == "gap<2s" and p != "gap<2s"]
    out["timing_errors"] = {
        "model": "M_T",
        "n": len(test),
        "never_predicted_classes": sorted(set(tt) - set(p for p in tpred
                                                        if p is not None)),
        "gap<2s_rows": sum(1 for x in tt if x == "gap<2s"),
        "gap<2s_missed": len(miss_fast),
        "notional_in_missed_bursts_usd": round(
            sum(r["qty"] * r["price"] for r in miss_fast), 2),
        "share_of_test_notional": round(
            sum(r["qty"] * r["price"] for r in miss_fast)
            / sum(r["qty"] * r["price"] for r in test), 4)
        if test else None,
    }

    # 4. EXTEND<->COMPLETE confusion -> residual trajectory consequence.
    rel_pred = [rel_m.predict(r) for r in test]
    conf = Counter((r["truth_exposure_action"], p)
                   for p, r in zip(rel_pred, test))
    ec = [r for p, r in zip(rel_pred, test)
          if r["truth_exposure_action"] == "extend" and p == "complete"]
    ce = [r for p, r in zip(rel_pred, test)
          if r["truth_exposure_action"] == "complete" and p == "extend"]

    def resid_pnl(rs):
        rs = [r for r in rs if r["settle_pnl_per_share"] is not None]
        if not rs:
            return None
        sh = sum(r["qty"] for r in rs)
        return {"n": len(rs), "shares": round(sh, 1),
                "l5_settlement_pnl_usd": round(
                    sum(r["qty"] * r["settle_pnl_per_share"] for r in rs), 2),
                "notional_usd": round(sum(r["qty"] * r["price"] for r in rs), 2)}
    out["relation_confusion_economics"] = {
        "model": model_name,
        "extend_called_complete": resid_pnl(ec),
        "complete_called_extend": resid_pnl(ce),
        "confusion_counts": {f"{t}->{p}": n for (t, p), n in conf.most_common()},
        "note": ("Each extend-called-complete row is a clone that flattens "
                 "inventory L5 kept building, and each complete-called-extend "
                 "row is a clone that doubles down where L5 reduced. The "
                 "settlement P&L shown is L5's own on those legs."),
    }
    return out


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def run(csv_path: Path = SEQUENCE_CSV) -> Dict[str, Any]:
    rows = build_rows(csv_path)
    train, test = chronological_split(rows)
    out: Dict[str, Any] = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "label": "HISTORICAL REPLAY SCORING — not shadow, not live, "
                 "not submitted, not filled, not settled, not realized",
        "source": str(csv_path),
        "n_transitions": len(rows),
        "n_windows": len({r["window_id"] for r in rows}),
        "span": (min(r["day"] for r in rows), max(r["day"] for r in rows)),
        "corpus_coverage_caveat": (
            "The canonical sequence holds 22 active days: 2026-03-19..04-21 "
            "and 2026-08-28..31. Regimes R2/R3 of canon_regimes.json and era "
            "E2_EARLY_REBATE have ZERO rows here — a gap in OUR collection, "
            "not in L5's activity. No regime is invented."),
        "protocol": {
            "split": "chronological by window start, no window overlap",
            "train_fraction_target": TRAIN_FRACTION,
            "tuning": "none — every bucket, feature set and alpha is taken "
                      "from the frozen production modules",
            "discovery_cells": [],
        },
        "unscorable_fields": UNSCORABLE,
        "class_mix_all_rows": {
            f: {k: round(v / len(rows), 4) for k, v in
                Counter(r["truth_" + f] for r in rows).most_common()}
            for f in FIELDS},
        "splits": {},
        "economics": {},
    }
    out["splits"]["GLOBAL"] = score_split(train, test, "GLOBAL")
    out["bootstrap_lift_GLOBAL"] = bootstrap_lift(train, test)
    out["economics"]["GLOBAL"] = mismatch_economics(train, test)

    # Breakouts: same global model, metrics on the stratum's test rows.
    for dim in ("duration", "era", "regime", "asset"):
        for val in sorted({r[dim] for r in rows}):
            sub_test = [r for r in test if r[dim] == val]
            if sub_test:
                out["splits"][f"GLOBALMODEL::{dim}={val}"] = score_split(
                    train, sub_test, f"global model, test rows {dim}={val}",
                    confusions=False)
            # within-stratum chronological split, so strata that lie entirely
            # on one side of the global cut are still scored out of sample
            sub = [r for r in rows if r[dim] == val]
            if len({r["window_id"] for r in sub}) >= 20:
                s_tr, s_te = chronological_split(sub)
                if s_tr and s_te:
                    out["splits"][f"WITHIN::{dim}={val}"] = score_split(
                        s_tr, s_te, f"within-stratum split, {dim}={val}",
                        confusions=(dim in ("duration", "era")))
                    if dim in ("duration", "era"):
                        out["economics"][f"WITHIN::{dim}={val}"] = \
                            mismatch_economics(s_tr, s_te)
    return out


def main() -> int:
    res = run()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "SCORECARD.json").write_text(json.dumps(res, indent=1))
    g = res["splits"]["GLOBAL"]["fields"]
    print(f"transitions={res['n_transitions']} windows={res['n_windows']} "
          f"span={res['span']}")
    for f, e in g.items():
        bb = e["best_baseline"]
        line = f"{f:<16} best_baseline={bb['name']}:{bb['accuracy']:.4f}"
        for m, sc in e["models"].items():
            line += f"  {m}={sc['accuracy']} (lift {sc['lift_over_best_baseline']})"
        print(line)
    print(f"wrote {OUT_DIR / 'SCORECARD.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
