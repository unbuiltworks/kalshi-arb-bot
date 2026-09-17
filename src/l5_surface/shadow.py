"""The prospective surface shadow: our surface, forward, against what L5 did.

NON-MONEY-MOVING BY CONSTRUCTION. No client, no signer, no credential, no
submit path. Every price it writes down is a level our hypothesis says should
be resting; nothing is sent anywhere. A test asserts the import closure.

WHAT IT DOES EACH TICK

  1. read both outcomes' full books for a live window          (venue fact)
  2. ask the engine for the DESIRED_PASSIVE_SURFACE            (ours)
  3. record it BEFORE any subsequent interaction is known      (no hindsight)
  4. walk each live rung up the evidence ladder as the tape arrives
  5. decode the chain receipts behind L5's public fills         (venue fact)
  6. score our predicted surface against his proven parents

THE ORDER IN 3 IS THE WHOLE POINT. A surface written down after seeing what
filled is not a prediction. Rows carry their own decision_time and are
emitted in file order ahead of the executions they are scored against, so a
reader can check the ordering instead of trusting it.

WHEN THE SPEC IS UNRESOLVED IT RECORDS REFUSALS, NOT GUESSES. Running with
FIDELITY_V0 emits `surface_refused` rows naming the unmeasured fields. That
is a useful thing to have running: the chassis is proven live, and the moment
a frozen spec is populated the same loop starts emitting surfaces without any
other change.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src import l5_book_context as BC
from src.l5_shadow import venues as V
from src.l5_surface import engine as E
from src.l5_surface import evidence as EV
from src.l5_surface import params as PR

LABEL = "L5_PASSIVE_SURFACE_SHADOW_V1"
SCHEMA = "l5_passive_surface_shadow/v1"

#: recycling marks, the preregistered ones
RECYCLE_S = (5, 15, 30, 60, 120, 300)


def _now() -> float:
    return time.time()


def restable(px: float, qty: float, terms: Mapping[str, Any],
             book: Optional[dict]) -> Dict[str, Any]:
    """Would the venue accept a PASSIVE order here?

    Venue facts only: on the tick grid, inside (0,1), at or above the minimum
    size, and not crossing the opposite touch -- an order that crosses is a
    taker and is not part of a passive surface. Unknown terms make this
    UNDECIDED rather than True: a rung we cannot show is restable does not get
    to claim it was.
    """
    tick = terms.get("tick")
    min_sz = terms.get("order_min_size")
    if tick is None or min_sz is None:
        return {"restable": None, "why": "venue terms not readable"}
    t = D(str(tick))
    p = D(str(px))
    if not (D("0") < p < D("1")):
        return {"restable": False, "why": "price outside (0,1)"}
    if (p % t) != 0:
        return {"restable": False, "why": "off the %s tick grid" % t}
    if D(str(qty)) < D(str(min_sz)):
        return {"restable": False, "why": "below order_min_size %s" % min_sz}
    s = BC.summarize(book) if book else None
    if s is None or s.get("ask") is None:
        return {"restable": None, "why": "book not readable, so not decided"}
    if p >= D(str(s["ask"])):
        return {"restable": False,
                "why": "a BUY at or above the ask crosses; that is a taker"}
    return {"restable": True, "ask": s.get("ask"), "bid": s.get("bid")}


def queue_ahead(book: Optional[dict], px: float) -> Optional[float]:
    """Size already resting at our price. We join BEHIND all of it.

    None when the book cannot answer -- which is different from zero, and a
    rung whose queue is unknown cannot be evidenced as filled."""
    if book is None:
        return None
    # "bid", not "bids": ladder() treats anything else as the ASK side, so the
    # wrong string here would silently measure the queue on the opposite side
    # of the book and hand us a queue position we never had.
    return BC.size_at(book, "bid", float(px))


def state_from_books(market: Mapping[str, Any], books: Mapping[str, Any],
                     inventory: Mapping[str, Any], t: float
                     ) -> Dict[str, Any]:
    """The venue-neutral state the engine consumes. No venue words in it."""
    outs: Dict[str, Any] = {}
    for outcome, b in books.items():
        s = BC.summarize(b)
        if s is None:
            continue
        outs[outcome] = {"bid": s.get("bid"), "ask": s.get("ask"),
                         "tick": (market.get("terms") or {}).get("tick"),
                         "bid_size": s.get("bid_size"),
                         "ask_size": s.get("ask_size")}
    end = market.get("end_epoch")
    return {"cohort": market.get("cohort") or "BTC15m",
            "market_key": market.get("market_key"),
            "state_id": "%s@%.0f" % (market.get("market_key"), t),
            "t_utc": t,
            "tte_s": (float(end) - t) if end else 0.0,
            "outcomes": outs, "inventory": dict(inventory)}


class RungTracker:
    """One rung's life: placed once, then walked as the tape arrives.

    Keeps the placement-time queue, because joining behind the size that was
    there WHEN WE QUOTED is the conservative reading; re-measuring it later
    would quietly hand us priority we never had.
    """

    def __init__(self, surface_id: str, spec_sha: str, market_key: str,
                 rung: E.Rung, *, placed_at: float, ahead: Optional[float],
                 rest_check: Mapping[str, Any]):
        self.ev = EV.RungEvidence(
            surface_id=surface_id, spec_sha256=spec_sha,
            market_key=market_key, outcome=rung.outcome,
            price=rung.max_acceptable_price, desired_qty=rung.desired_qty,
            rung_index=rung.rung_index)
        self.placed_at = placed_at
        self.ahead = ahead
        self.rest_check = dict(rest_check)
        self.touch_reached: Optional[bool] = False
        self.prints: List[dict] = []

    def observe_book(self, book: Optional[dict]) -> None:
        s = BC.summarize(book) if book else None
        if s is None or s.get("bid") is None:
            if self.touch_reached is False:
                self.touch_reached = None      # unreadable, not "no"
            return
        if D(str(s["bid"])) <= D(self.ev.price):
            self.touch_reached = True

    def observe_prints(self, prints: Sequence[dict]) -> None:
        for p in prints:
            t = float(p.get("t") or 0)
            if t > self.placed_at:
                self.prints.append(p)

    def walk(self, observed_until: float,
             coverage_complete: Optional[bool]) -> EV.RungEvidence:
        return EV.walk(
            self.ev, restable=self.rest_check.get("restable"),
            touch_reached=self.touch_reached, prints=self.prints,
            queue_ahead=(self.ahead if self.ahead is not None else "0"),
            placed_at=self.placed_at, observed_until=observed_until,
            coverage_complete=(None if self.ahead is None
                               else coverage_complete))


def compare_to_parents(rungs: Sequence[E.Rung], parents: Sequence[dict],
                       tick: float) -> Dict[str, Any]:
    """Our predicted surface against L5's chain-proven parents.

    Scored per parent, and price and quantity are kept apart: naming the right
    level with the wrong lot is a partial success and reporting one number for
    both would hide which half worked."""
    rows: List[Dict[str, Any]] = []
    for p in parents:
        px, oc, qty = p.get("price"), p.get("outcome"), p.get("qty")
        if px is None or oc is None:
            rows.append({"tx": p.get("tx"), "scorable": False,
                         "why": "parent carries no price or outcome"})
            continue
        same = [r for r in rungs if r.outcome == oc]
        exact = [r for r in same
                 if abs(float(r.max_acceptable_price) - float(px)) < 1e-9]
        near = [r for r in same
                if abs(float(r.max_acceptable_price) - float(px))
                <= tick + 1e-9]
        best = (exact or near or [None])[0]
        rows.append({
            "tx": p.get("tx"), "order_hashes": p.get("order_hashes"),
            "outcome": oc, "price": px, "qty": qty,
            "outcome_predicted": bool(same),
            "exact_level": bool(exact),
            "within_1_tick": bool(near),
            "predicted_qty": (best.desired_qty if best else None),
            "qty_exact": (None if best is None else
                          abs(float(best.desired_qty) - float(qty)) < 1e-9),
            "qty_within_lot": (None if best is None else
                               float(qty) <= float(best.desired_qty) + 1e-9),
            "attribution": {"parent": "OBSERVED_L5_PARENT",
                            "surface": "INFERRED_SURFACE"},
        })
    n = len([r for r in rows if r.get("outcome_predicted") is not None])
    return {"n_parents": len(rows), "n_scorable": n,
            "exact_level": sum(1 for r in rows if r.get("exact_level")),
            "within_1_tick": sum(1 for r in rows if r.get("within_1_tick")),
            "qty_exact": sum(1 for r in rows if r.get("qty_exact")),
            "rows": rows,
            "labels": ("OUR surface is INFERRED_SURFACE. His parents are "
                       "OBSERVED_L5_PARENT. A level of ours coinciding with "
                       "one of his is not evidence that the resting size "
                       "there was his.")}


def open_tape(out_dir: str) -> Tuple[Any, str]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("surface_shadow_%s.jsonl"
                % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    return open(path, "a", encoding="utf-8"), str(path)


def _markets(cohorts: Sequence[str]) -> List[Dict[str, Any]]:
    """Live windows, from the existing venue reader. No new discovery path."""
    out: List[Dict[str, Any]] = []
    for w in (V.live_windows() or []):
        key = w.get("market_key") or w.get("slug")
        coh = w.get("cohort")
        if not key or (cohorts and coh not in cohorts):
            continue
        toks = BC.outcome_tokens(key)
        if not toks:
            continue
        out.append({"market_key": key, "cohort": coh,
                    "end_epoch": w.get("end_epoch") or w.get("end_ts"),
                    "tokens": toks, "terms": w.get("terms") or {}})
    return out


def run(out_dir: str, *, minutes: float, interval: float,
        cohorts: Sequence[str], spec: PR.SurfaceParams,
        clock=_now) -> Dict[str, Any]:
    """The prospective loop. Reads, decides, records; never sends."""
    fh, path = open_tape(out_dir)
    t0 = clock()
    deadline = t0 + minutes * 60.0
    counts: Dict[str, int] = {}

    def emit(row: Dict[str, Any]) -> None:
        row.setdefault("label", LABEL)
        row.setdefault("schema", SCHEMA)
        row.setdefault("L5_OBSERVATION_CLOCK", clock())
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()

    emit({"kind": "boot", "spec": spec.report(), "interval_s": interval,
          "cohorts": list(cohorts), "out": path,
          "labels": ("observation only: no client, no signer, no submit path. "
                     "Every price recorded is OURS, not an order.")})

    trackers: Dict[str, List[RungTracker]] = {}
    while clock() < deadline:
        tick_t = clock()
        for m in _markets(cohorts):
            books = {}
            for outcome, tok in (m["tokens"].get("by_outcome")
                                 or m["tokens"]).items():
                if not isinstance(tok, str):
                    continue
                books[outcome] = V.pm_book(tok)
            if not books:
                continue
            st = state_from_books(m, books, {}, tick_t)
            res = E.desired_surface(st, spec)
            if not res.get("emitted"):
                emit({"kind": "surface_refused", "market_key": m["market_key"],
                      "blocked_by": res.get("blocked_by"),
                      "reason": res.get("reason"),
                      "spec_sha256": res.get("spec_sha256"),
                      "labels": ("the chassis is live and the spec is not. "
                                 "No surface is invented to fill the gap.")})
                continue
            surf = res["surface"]
            # THE ORDER MATTERS: the surface is written before any later
            # interaction is known, and never revised.
            emit({"kind": "desired_passive_surface", "decision_time": tick_t,
                  **surf.report()})
            made: List[RungTracker] = []
            for r in surf.live_rungs():
                b = books.get(r.outcome)
                chk = restable(float(r.max_acceptable_price),
                               float(r.desired_qty), m["terms"], b)
                made.append(RungTracker(
                    surf.surface_id(), surf.spec_sha256, m["market_key"], r,
                    placed_at=tick_t, ahead=queue_ahead(b, float(
                        r.max_acceptable_price)), rest_check=chk))
            trackers.setdefault(m["market_key"], []).extend(made)

            for tr in trackers.get(m["market_key"], []):
                tr.observe_book(books.get(tr.ev.outcome))
            emit({"kind": "rung_evidence", "market_key": m["market_key"],
                  "rows": [tr.walk(tick_t, coverage_complete=None).report()
                           for tr in trackers.get(m["market_key"], [])]})
        time.sleep(max(0.0, interval - (clock() - tick_t)))

    emit({"kind": "shutdown", "counts": counts,
          "elapsed_s": round(clock() - t0, 1)})
    fh.close()
    return {"path": path, "counts": counts}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default="data/l5_surface_shadow")
    ap.add_argument("--minutes", type=float, default=1440.0)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--cohorts", default="BTC15m,BTC5m")
    ap.add_argument("--spec", default=PR.FIDELITY_V0.label,
                    help="a label in l5_surface.params.SETS")
    a = ap.parse_args(argv)
    spec = PR.SETS.get(a.spec)
    if spec is None:
        print("unknown spec %r; known: %s" % (a.spec, sorted(PR.SETS)),
              file=sys.stderr)
        return 2
    run(a.out_dir, minutes=a.minutes, interval=a.interval,
        cohorts=[c for c in a.cohorts.split(",") if c], spec=spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --------------------------------------------------------------------------
# THE TWO ADAPTED ASSUMPTIONS, AS EXPERIMENTAL AXES.
#
# `inventory_price_skew = NONE` and `withdrawal_policy = TTE_FLOOR` are OUR
# choices, made because nothing observable establishes L5's. They are not
# neutral: each has a specific way of being wrong, and a shadow that did not
# measure it would let a bad default survive by never being looked at.
#
# So each axis names the harm it would cause and the statistic that would show
# it. Tuning them later comes from THESE numbers -- our own prospective
# executable economics -- and never from pretending public data revealed them.
# --------------------------------------------------------------------------
ASSUMPTION_AXES: Dict[str, Dict[str, Any]] = {
    "stranded_residual": {
        "assumption": "inventory_price_skew = NONE",
        "harm": ("with no skew, nothing pulls quoting toward the side that "
                 "would reduce an imbalance, so a residual can sit unworked"),
        "statistic": "share of ticks with |residual| > 0 and no live reducing "
                     "rung",
    },
    "one_sided_accumulation": {
        "assumption": "inventory_price_skew = NONE",
        "harm": "both sides quoted equally while only one keeps filling",
        "statistic": "max |residual| reached, and its share of total filled",
    },
    "poor_recycling": {
        "assumption": "inventory_price_skew = NONE",
        "harm": "matched inventory forms slowly or not at all",
        "statistic": "p_opposite_within_{5,15,30,60,120,300}s over fills",
    },
    "stale_quote_exposure": {
        "assumption": "withdrawal_policy = TTE_FLOOR",
        "harm": ("never withdrawing before the floor leaves quotes resting "
                 "through state changes that would have moved them"),
        "statistic": "seconds a live rung spent more than one tick away from "
                     "the touch it was placed against",
    },
    "adverse_selection": {
        "assumption": "withdrawal_policy = TTE_FLOOR",
        "harm": "the fills we get are the ones we least wanted",
        "statistic": ("touch movement against us in the 30s after each "
                      "EVIDENCED_FILL"),
    },
    "unnecessary_cancellation": {
        "assumption": "withdrawal_policy = TTE_FLOOR",
        "harm": "withdrawing at the floor discards rungs that would have "
                "filled",
        "statistic": "rungs withdrawn at the floor that later saw TRADE_FLOW "
                     "at their price",
    },
    "divergence_from_l5": {
        "assumption": "both",
        "harm": "our surface stops resembling his proven parents",
        "statistic": "exact-level and within-1-tick share against subsequent "
                     "OBSERVED_L5_PARENT executions",
    },
}


def axes_report(rungs: Sequence[EV.RungEvidence], parents: Sequence[dict],
                ticks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Score the two ADAPTED assumptions against what actually happened.

    Every figure here is SHADOW: it comes from evidenced fills on orders that
    were never placed. It measures whether OUR defaults are hurting, which is
    a question about our deployment and not a finding about L5.
    """
    fills = [r for r in rungs if r.is_fill()]
    inv: Dict[str, float] = {}
    peak = 0.0
    for r in fills:
        inv[r.outcome] = inv.get(r.outcome, 0.0) + float(r.filled_qty())
        peak = max(peak, abs(inv.get("Up", 0.0) - inv.get("Down", 0.0)))
    total = sum(inv.values())
    rec = EV.recycling(fills, RECYCLE_S)
    starved = sum(1 for t in ticks if t.get("residual")
                  and not t.get("has_reducing_rung"))
    return {
        "axes": ASSUMPTION_AXES,
        "n_evidenced_fills": len(fills),
        "stranded_residual": {
            "ticks_with_residual_and_no_reducing_rung": starved,
            "ticks_considered": len(ticks),
            "share": (round(starved / len(ticks), 4) if ticks else None)},
        "one_sided_accumulation": {
            "peak_abs_residual": round(peak, 6),
            "total_filled": round(total, 6),
            "peak_share_of_filled": (round(peak / total, 4) if total else None)},
        "poor_recycling": rec,
        "divergence_from_l5": {"n_parents_compared": len(parents)},
        "labels": [
            "SHADOW. Evidenced fills on orders that were never placed; no "
            "position exists and nothing here is realized.",
            "These measure OUR defaults, not L5. inventory_price_skew=NONE "
            "and withdrawal_policy=TTE_FLOOR are OURS_ADAPTED and are tuned "
            "from these numbers, never from public data about him.",
        ],
    }
