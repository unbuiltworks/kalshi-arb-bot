"""WHAT WAS TRUE BEFORE THE FILL. A causal ring, and a label attached at fill.

THE FAILURE THIS EXISTS TO PREDICT. On GEMI-BTC05M2609201555-UP the loose rungs
took 22 first fills, ALL on the NO leg, with markout deteriorating -22 / -36 /
-52 ticks and adverse on 100%, while the complement price was continuously
available and never filled. That is one mechanism, not bad luck: THE FIRST LEG
FILLS BECAUSE THAT SIDE IS BECOMING WRONG, and the profitable complement stays
unfilled precisely because it is profitable.

The question is not whether BTC can be predicted. It is narrower and might be
answerable: **when is a resting YES or NO maker order about to become toxic?**

WHY A RING IS NEEDED AT ALL, STATED PLAINLY. Nothing in this programme kept a
per-symbol time series. The spread census keeps aggregated cells; the fill
sequence keeps forward markouts; neither can answer "what was the book doing 30
seconds before that fill". **So the 22 toxic fills cannot be reconstructed at
this granularity — they seeded the question and are not in the labelled
cohort.** Labelling starts from the deploy that adds this.

TWO GRANULARITIES, AND THE DIFFERENCE MATTERS.

  book-derived signals   sampled at the lane's 8-second cadence, so t-1s and
                         t-5s usually resolve to the SAME sample as
                         "immediately before". Each lookback records the
                         actual age of the sample it used, and returns None
                         rather than the nearest thing when nothing is inside
                         tolerance.
  trade-derived signals  millisecond venue timestamps from the socket, so run
                         length and recent direction ARE available finely.

Reporting an 8-second-old book as "t-1s" would be the kind of quiet fiction
this codebase keeps catching, so the age travels with every row.

EVERYTHING HERE IS CAUSAL. A lookback is assembled from samples strictly at or
before the fill instant. Nothing observed after a fill enters its feature row;
the OUTCOME is attached separately and is the label, never an input.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Deque, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "gemini_econ_prefill/v1"

#: lookback offsets, in seconds before the fill. Preregistered.
OFFSETS_S: Tuple[float, ...] = (60.0, 30.0, 10.0, 5.0, 1.0, 0.0)

#: a sample may stand in for an offset only if it is within this much of it.
#: At an 8s cadence the 1s and 5s offsets will often resolve to the same
#: sample; the tolerance makes that explicit instead of silently pretending.
TOLERANCE_S = 9.0

LABELS = (
    "CAUSAL. Every feature is assembled from samples at or before the fill "
    "instant. The outcome is attached separately and is the LABEL, never an "
    "input.",
    "Book-derived signals are sampled at the lane's 8s cadence; t-1s and t-5s "
    "often resolve to the same sample. Each value carries the AGE of the "
    "sample used, and is None when nothing is inside tolerance.",
    "The 22 toxic fills that motivated this predate the ring and are NOT in "
    "the labelled cohort.",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _f(v: Any) -> Optional[float]:
    d = _dec(v)
    return None if d is None else float(d)


@dataclass
class Sample:
    """One observation of everything causally available at a moment."""

    t: float
    tte_s: Optional[float]
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    spot: Optional[float] = None
    strike: Optional[float] = None
    n_prints: Optional[int] = None
    buy_prints: Optional[int] = None
    sell_prints: Optional[int] = None
    same_side_run_len: Optional[int] = None
    queue_ahead: Optional[float] = None

    def spread_yes(self) -> Optional[float]:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    def bid_sum(self) -> Optional[float]:
        if self.yes_bid is None or self.no_bid is None:
            return None
        return self.yes_bid + self.no_bid

    def distance_from_strike(self) -> Optional[float]:
        if self.spot is None or self.strike is None or not self.strike:
            return None
        return (self.spot - self.strike) / self.strike

    def row(self) -> Dict[str, Any]:
        return {"t": self.t, "tte_s": self.tte_s,
                "yes_bid": self.yes_bid, "yes_ask": self.yes_ask,
                "no_bid": self.no_bid, "no_ask": self.no_ask,
                "spot": self.spot, "strike": self.strike,
                "spread_yes": self.spread_yes(), "bid_sum": self.bid_sum(),
                "distance_from_strike": self.distance_from_strike(),
                "n_prints": self.n_prints, "buy_prints": self.buy_prints,
                "sell_prints": self.sell_prints,
                "same_side_run_len": self.same_side_run_len,
                "queue_ahead": self.queue_ahead}


@dataclass
class PreFillRing:
    """A bounded per-symbol history, and the causal lookbacks taken from it."""

    maxlen: int = 240                  # ~32 minutes at an 8s cadence
    _by_symbol: Dict[str, Deque[Sample]] = field(default_factory=dict)

    def observe(self, symbol: str, sample: Sample) -> None:
        dq = self._by_symbol.get(symbol)
        if dq is None:
            dq = self._by_symbol[symbol] = deque(maxlen=self.maxlen)
        dq.append(sample)

    def drop(self, symbol: str) -> None:
        self._by_symbol.pop(symbol, None)

    def at(self, symbol: str, *, t: float, offset_s: float,
           tolerance_s: float = TOLERANCE_S
           ) -> Tuple[Optional[Sample], Optional[float]]:
        """The newest sample at or before `t - offset_s`, and its age error.

        Returns (None, None) when nothing is inside tolerance -- never the
        nearest available sample dressed up as the requested one.
        """
        dq = self._by_symbol.get(symbol)
        if not dq:
            return None, None
        target = float(t) - float(offset_s)
        best: Optional[Sample] = None
        for s in dq:
            if s.t <= target and (best is None or s.t > best.t):
                best = s
        if best is None:
            return None, None
        err = target - best.t
        if err > tolerance_s:
            return None, None
        return best, round(err, 3)

    def velocity(self, symbol: str, *, t: float, over_s: float
                 ) -> Optional[float]:
        """Fractional spot change per second over the trailing `over_s`."""
        a, _ = self.at(symbol, t=t, offset_s=0.0)
        b, _ = self.at(symbol, t=t, offset_s=over_s)
        if a is None or b is None or a.spot is None or b.spot is None:
            return None
        dt = a.t - b.t
        if dt <= 0 or not b.spot:
            return None
        return ((a.spot - b.spot) / b.spot) / dt

    def acceleration(self, symbol: str, *, t: float, over_s: float
                     ) -> Optional[float]:
        """Change in velocity between the two trailing half-windows."""
        v_now = self.velocity(symbol, t=t, over_s=over_s)
        mid, _ = self.at(symbol, t=t, offset_s=over_s)
        if v_now is None or mid is None:
            return None
        v_prev = self.velocity(symbol, t=mid.t, over_s=over_s)
        if v_prev is None:
            return None
        return v_now - v_prev

    def features_before(self, symbol: str, *, t: float,
                        leg: str) -> Dict[str, Any]:
        """Everything causal, at every preregistered offset, for one leg."""
        out: Dict[str, Any] = {"schema": SCHEMA, "symbol": symbol,
                               "fill_t": float(t), "leg": str(leg).upper(),
                               "offsets": {}, "labels": list(LABELS)}
        held = str(leg).upper()
        for off in OFFSETS_S:
            s, err = self.at(symbol, t=t, offset_s=off)
            key = "t-%gs" % off
            if s is None:
                out["offsets"][key] = {"available": False,
                                       "why": "no sample inside tolerance"}
                continue
            r = s.row()
            r["sample_age_error_s"] = err
            r["available"] = True
            # the leg we would be holding, so the row reads the same way for
            # a YES candidate and a NO candidate
            r["held_bid"] = (s.yes_bid if held == "YES" else s.no_bid)
            r["complement_bid"] = (s.no_bid if held == "YES" else s.yes_bid)
            out["offsets"][key] = r

        # derived motion, computed over the ring rather than from two points
        out["spot_velocity_60s"] = self.velocity(symbol, t=t, over_s=60.0)
        out["spot_velocity_10s"] = self.velocity(symbol, t=t, over_s=10.0)
        out["spot_acceleration_60s"] = self.acceleration(symbol, t=t,
                                                         over_s=60.0)
        now, _ = self.at(symbol, t=t, offset_s=0.0)
        prev60, _ = self.at(symbol, t=t, offset_s=60.0)
        if now is not None and prev60 is not None:
            hb_now = now.yes_bid if held == "YES" else now.no_bid
            hb_prev = prev60.yes_bid if held == "YES" else prev60.no_bid
            out["held_bid_drift_60s"] = (
                None if (hb_now is None or hb_prev is None)
                else hb_now - hb_prev)
            out["bid_sum_drift_60s"] = (
                None if (now.bid_sum() is None or prev60.bid_sum() is None)
                else now.bid_sum() - prev60.bid_sum())
            out["spread_drift_60s"] = (
                None if (now.spread_yes() is None
                         or prev60.spread_yes() is None)
                else now.spread_yes() - prev60.spread_yes())
        out["distance_from_strike"] = (None if now is None
                                       else now.distance_from_strike())
        out["tte_s"] = None if now is None else now.tte_s
        return out


@dataclass
class ToxicityDataset:
    """Labelled first fills: causal features in, realised outcome out."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    max_rows: int = 5000

    def add(self, *, features: Mapping[str, Any], cohort: str, arm: str,
            symbol: str, leg: str, price: Any, t: float) -> Dict[str, Any]:
        row = {"schema": SCHEMA, "cohort": cohort, "arm": arm,
               "symbol": symbol, "leg": str(leg).upper(),
               "price": (None if price is None else str(price)),
               "t": float(t), "features": dict(features),
               "outcome": None, "stage": "shadow"}
        self.rows.append(row)
        if len(self.rows) > self.max_rows:
            self.rows = self.rows[-(self.max_rows // 2):]
        return row

    #: how a labelled row was joined to its fill_sequence row.
    JOIN_FILL_T = "FILL_T_EXACT"
    #: rows labelled before the join was corrected carry no method at all and
    #: are reported as this. They were matched on `t_candidate` against a
    #: capture stamped at FILL time -- two different events on one clock --
    #: with a 12s tolerance, so they are a NON-RANDOM SUBSAMPLE enriched for
    #: fills that arrived within ~1.5 cycles of admission.
    JOIN_LEGACY = "LEGACY_TIMESTAMP_HEURISTIC"

    @staticmethod
    def label(row: Dict[str, Any], *, markout_60s: Optional[float],
              complement_filled: Optional[bool],
              max_adverse_ticks: Optional[float],
              fill_seq_id: Optional[int] = None,
              join_method: Optional[str] = None) -> None:
        """Attach the OUTCOME. Never an input to any feature."""
        toxic = (None if markout_60s is None else bool(markout_60s < 0))
        row["outcome"] = {
            "markout_60s_ticks": markout_60s,
            "complement_filled": complement_filled,
            "max_adverse_ticks": max_adverse_ticks,
            "toxic": toxic,
            # PROVENANCE OF THE LABEL, so a number can never again be read
            # without knowing which fill it came from or how it got here.
            "fill_seq_id": fill_seq_id,
            "join_method": join_method,
            "definition": ("TOXIC = maker-perspective markout at 60s is "
                           "negative for the leg that filled. Complement fill "
                           "and drawdown are reported beside it, never folded "
                           "into the definition."),
            "source": ("markout_60s is COPIED from the fill_sequence row, "
                       "not recomputed. There is exactly ONE markout "
                       "computation in this system, so the two stores cannot "
                       "disagree on a shared row -- only on which rows they "
                       "hold."),
        }

    def reconcile(self, fill_rows: Sequence[Mapping[str, Any]]
                  ) -> Dict[str, Any]:
        """LOUD CHECK. For every labelled row, does the markout we stored
        still equal the fill row it claims to come from?

        This cannot fail while the label is a copy, and that is the point:
        it converts an invariant we are relying on into one the evidence
        report asserts every window instead of assuming.
        """
        by_id = {r.get("seq_id"): r for r in fill_rows
                 if r.get("seq_id") is not None}
        out: Dict[str, Any] = {"checked": 0, "agree": 0, "disagree": 0,
                               "unjoinable": 0, "mismatches": [],
                               "by_join_method": {}}
        for r in self.rows:
            o = r.get("outcome") or {}
            if o.get("toxic") is None and o.get("markout_60s_ticks") is None:
                continue
            m = o.get("join_method") or self.JOIN_LEGACY
            out["by_join_method"][m] = out["by_join_method"].get(m, 0) + 1
            sid = o.get("fill_seq_id")
            if sid is None or sid not in by_id:
                out["unjoinable"] += 1
                continue
            out["checked"] += 1
            theirs = (by_id[sid].get("markout_ticks_by_horizon_s")
                      or {}).get("60")
            ours = o.get("markout_60s_ticks")
            if theirs == ours:
                out["agree"] += 1
            else:
                out["disagree"] += 1
                out["mismatches"].append(
                    {"fill_seq_id": sid, "symbol": r.get("symbol"),
                     "leg": r.get("leg"), "toxicity_markout_60s": ours,
                     "fill_sequence_markout_60s": theirs})
        out["ok"] = out["disagree"] == 0
        out["why"] = (None if out["ok"] else
                      "THE TWO STORES DISAGREE ON A SHARED FILL ID. Every "
                      "leg-based conclusion is void until this is explained.")
        return out

    def reconciliation_rows(self, fill_rows: Sequence[Mapping[str, Any]], *,
                            limit: int = 25) -> List[Dict[str, Any]]:
        """THE ROW-LEVEL TABLE, for every fill present in BOTH stores.

        One row per shared fill id, carrying what each store measured and the
        sample it measured against. Only rows joined by fill id appear: a
        legacy row has no id and cannot honestly be placed in this table.
        """
        by_id = {r.get("seq_id"): r for r in fill_rows
                 if r.get("seq_id") is not None}
        out: List[Dict[str, Any]] = []
        for r in self.rows:
            o = r.get("outcome") or {}
            sid = o.get("fill_seq_id")
            if sid is None or sid not in by_id:
                continue
            f = by_id[sid]
            meta = (f.get("markout_measurement") or {}).get("60") or {}
            theirs = (f.get("markout_ticks_by_horizon_s") or {}).get("60")
            out.append({
                "fill_seq_id": sid,
                "observation_id": f.get("observation_id"),
                "symbol": f.get("symbol"),
                "cohort": f.get("cohort"),
                "arm": r.get("arm"),
                "leg_fill_sequence": f.get("first_fill_leg"),
                "leg_toxicity": r.get("leg"),
                "leg_agrees": (str(f.get("first_fill_leg") or "").upper()
                               == str(r.get("leg") or "").upper()),
                "fill_t_fill_sequence": f.get("first_fill_t"),
                "fill_t_toxicity": r.get("t"),
                "fill_t_agrees": (f.get("first_fill_t") == r.get("t")),
                "fill_price_fill_sequence": f.get("first_fill_price"),
                "fill_price_toxicity": r.get("price"),
                "quantity": f.get("first_fill_qty"),
                # what the 60s number was actually measured against
                "comparison_t_selected": meta.get("ref_t"),
                "comparison_requested_age_s": meta.get("requested_age_s"),
                "comparison_realised_age_s": meta.get("realised_age_s"),
                "comparison_age_error_s": meta.get("age_error_s"),
                "aliased_with_horizons": meta.get("aliased_with"),
                "reference_field_fill_sequence": meta.get("ref_field"),
                "reference_price_fill_sequence": meta.get("ref_price"),
                "markout_60s_fill_sequence": theirs,
                "reference_field_toxicity": ("NONE -- the label is a COPY; "
                                             "this store computes no markout"),
                "markout_60s_toxicity": o.get("markout_60s_ticks"),
                "markouts_agree": theirs == o.get("markout_60s_ticks"),
                "toxicity_label": o.get("toxic"),
                "join_method": o.get("join_method"),
                "formula_fill_sequence": meta.get("formula"),
                "formula_toxicity": ("toxic = markout_60s < 0, on the value "
                                     "copied from fill_sequence"),
            })
            if len(out) >= limit:
                break
        return out

    # -- DURABILITY. The labelled cohort is the one thing a freeze exists to
    # accumulate, and the service runs restartPolicyType ALWAYS: a crash would
    # silently wipe hours of labelling and the wait would produce nothing.
    def save(self, path: str) -> None:
        import json
        import os as _os
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"schema": SCHEMA, "rows": self.rows}, fh, default=str)
        _os.replace(tmp, path)          # atomic; a torn file is not a cohort

    @classmethod
    def load(cls, path: str) -> "ToxicityDataset":
        import json
        try:
            with open(path) as fh:
                d = json.load(fh)
            return cls(rows=list(d.get("rows") or []))
        except (OSError, ValueError, TypeError):
            return cls()

    def labelled(self) -> List[Dict[str, Any]]:
        return [r for r in self.rows
                if (r.get("outcome") or {}).get("toxic") is not None]

    def report(self) -> Dict[str, Any]:
        lab = self.labelled()
        by_leg: Dict[str, Dict[str, int]] = {}
        for r in lab:
            d = by_leg.setdefault(r["leg"], {"n": 0, "toxic": 0})
            d["n"] += 1
            d["toxic"] += 1 if r["outcome"]["toxic"] else 0
        return {"schema": SCHEMA, "rows": len(self.rows),
                "labelled": len(lab),
                "toxic": sum(1 for r in lab if r["outcome"]["toxic"]),
                "by_leg": {k: {**v,
                               "toxic_share": (v["toxic"] / v["n"]
                                               if v["n"] else None)}
                           for k, v in sorted(by_leg.items())},
                "offsets_s": list(OFFSETS_S),
                "labels": list(LABELS)}
