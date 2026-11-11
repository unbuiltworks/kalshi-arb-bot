"""L5_PASSIVE_SURFACE_PROSPECTIVE_V1 -- score the frozen prospective windows.

WHAT THIS ANSWERS, preregistered before any window was scored:

  PRIMARY   Was liquidity COMPATIBLE with L5's eventual parent (same outcome,
            same price, at least its quantity) already resting in the recorded
            book BEFORE that parent's first chain-proven execution?

  CAUSAL    For every outcome reversal A -> B, was compatible liquidity for the
            FUTURE parent B already present before parent A executed? A surface
            that is merely reactive cannot have been there yet; one that is
            persistent and two-sided must have been.

WHY THE CLOCKS MATTER. A parent carries L5_EVENT_CLOCK, the venue's trade
timestamp. A book snapshot carries MARKET_STATE_CLOCK, the venue's book
timestamp, and L5_OBSERVATION_CLOCK, our wall clock. Only the two VENUE clocks
are comparable to each other, so every book join here uses
MARKET_STATE_CLOCK. Joining on our own clock would import the public feed's
publication lag -- median 22.2s, p90 48.9s -- into every mark, and the -5s and
-1s marks would be meaningless.

INVENTORY IS RECOMPUTED FROM SCRATCH. The recorder writes rows in DISCOVERY
order, not event order, so its own `inventory_before` / `inventory_after` /
`residual_after` fields accumulate out of sequence and are NOT read here. The
parents are stable-sorted by L5_EVENT_CLOCK -- ties keep file order -- and the
ledger is rebuilt by walking that sequence.

ATTRIBUTION IS NEVER PROMOTED.

  OBSERVED_L5_PARENT            the chain names L5 as the order's owner. Only
                                these are L5's actions.
  L5_COMPATIBLE_BOOK_LIQUIDITY  anonymous displayed size at a price and side he
                                later executed at. It MAY be his. That is not
                                evidence that it is, and this module never
                                says it is.
  INFERRED_SURFACE              what our frozen hypothesis predicted. Ours.

So "compatible liquidity was already resting" is a statement about the BOOK,
never a statement that L5 owned it.
"""
from __future__ import annotations

import glob
import json
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.window_forensics import eligibility as EL

LABEL = "L5_PASSIVE_SURFACE_PROSPECTIVE_V1"

#: the marks, preregistered. Negative = before the parent's execution.
MARKS_S: Tuple[int, ...] = (-120, -60, -30, -15, -5, -1)
#: replenishment marks, after it
REPLENISH_S: Tuple[int, ...] = (1, 5, 15, 30, 60)
#: opposite-outcome interaction / recycling marks, after it
RECYCLE_S: Tuple[int, ...] = (5, 15, 30, 60, 120, 300)

#: a book older than this cannot speak for a mark. The recorder samples every
#: ~1.4s, so this is generous; beyond it the answer is UNREADABLE, not zero.
BOOK_STALE_S = 5.0

#: exact-100 tolerance (venue quantities carry rounding), and the near band
EXACT_TOL = 1e-6
NEAR_100_BAND = 5.0

# --------------------------------------------------------------------------
# MECHANISM DECISION RULE, FROZEN HERE BEFORE ANY WINDOW IS SCORED.
#
# Declared as data, above the code that evaluates it, so it cannot be tuned to
# whatever the windows turn out to show. Every branch has an escape: if the
# evidence supports none of them the answer is UNRESOLVED, not the closest fit.
# --------------------------------------------------------------------------
MECHANISM_RULE: Dict[str, Any] = {
    "H3-A PERSISTENT_TWO_SIDED_SURFACE": {
        "requires": [
            "pre_execution_compatible_share >= 0.80 at the -60s mark",
            "reversal OPPOSITE_ALREADY_PRESENT share >= 0.70 of resolved "
            "reversals",
            "both_outcomes_simultaneous_share >= 0.70 of readable snapshots",
        ],
        "reads": ("the surface is already standing on both outcomes; L5's "
                  "fills are the market arriving at it, not it reacting"),
    },
    "H3-B STATE_REFRESHED_PASSIVE_SURFACE": {
        "requires": [
            "pre_execution_compatible_share >= 0.80 at the -60s mark",
            "reversal OPPOSITE_APPEARED_AFTER_FILL share >= 0.70 of resolved "
            "reversals",
        ],
        "reads": ("liquidity is passive and resting, but the OPPOSITE side is "
                  "placed after a fill rather than standing beforehand"),
    },
    "H3-C HYBRID": {
        "requires": [
            "pre_execution_compatible_share >= 0.80 at the -60s mark",
            "neither reversal class reaches 0.70",
        ],
        "reads": "both signatures present; the surface is partly refreshed",
    },
    "H3-REJECTED": {
        "requires": ["pre_execution_compatible_share < 0.50 at the -60s mark"],
        "reads": ("compatible liquidity is typically NOT there beforehand, so "
                  "a resting passive surface is not what produced these fills"),
    },
    "UNRESOLVED": {
        "requires": ["anything else, including too few resolved reversals"],
        "reads": "the evidence does not choose; say so rather than pick",
    },
    "min_resolved_reversals": 8,
    "min_parents": 20,
}


# ------------------------------------------------------------------ reading

def _rows(path: str) -> List[dict]:
    out: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _is_buy(parent: dict) -> Optional[bool]:
    """makerAssetId == 0 means the owner paid collateral, which is a BUY.

    None when the receipt is absent: unknown, never assumed."""
    legs = parent.get("parents") or []
    if not legs:
        return None
    flags = {str(l.get("maker_asset_id")) == "0" for l in legs}
    return flags.pop() if len(flags) == 1 else None


def _fee_paid(parent: dict) -> Optional[float]:
    legs = parent.get("parents") or []
    if not legs:
        return None
    try:
        return sum(float(l.get("fee") or 0) for l in legs)
    except (TypeError, ValueError):
        return None


class Book:
    """One `book_full_joint` snapshot, indexed for price lookups."""

    __slots__ = ("t", "obs_t", "tte", "pre_window", "bids", "asks", "touch",
                 "tick", "one_sided")

    def __init__(self, row: dict):
        outs = row.get("outcomes") or {}
        self.obs_t = row.get("L5_OBSERVATION_CLOCK")
        self.tte = row.get("tte_s")
        self.pre_window = bool(row.get("pre_window"))
        self.bids: Dict[str, Dict[float, float]] = {}
        self.asks: Dict[str, Dict[float, float]] = {}
        self.touch: Dict[str, Dict[str, Any]] = {}
        self.one_sided: Dict[str, bool] = {}
        state: List[float] = []
        self.tick = 0.01
        for name, o in outs.items():
            self.bids[name] = {round(float(p), 4): float(q)
                               for p, q in (o.get("bids") or [])}
            self.asks[name] = {round(float(p), 4): float(q)
                               for p, q in (o.get("asks") or [])}
            s = o.get("summary") or {}
            self.touch[name] = {"bid": s.get("bid"), "ask": s.get("ask")}
            self.one_sided[name] = bool(s.get("one_sided"))
            try:
                self.tick = float(s.get("tick") or self.tick)
            except (TypeError, ValueError):
                pass
            mc = o.get("MARKET_STATE_CLOCK")
            if isinstance(mc, (int, float)):
                state.append(float(mc))
        # the VENUE's clock for this snapshot. Both outcomes are read in one
        # pass, so they differ by milliseconds; the later one bounds what this
        # snapshot can claim to know.
        self.t = max(state) if state else (self.obs_t or 0.0)

    def size_at(self, outcome: str, price: float, side: str) -> Optional[float]:
        """Displayed size, or None when this snapshot cannot answer.

        None (unreadable) and 0.0 (served, and empty) are DIFFERENT answers and
        are never collapsed."""
        book = self.bids if side == "BUY" else self.asks
        if outcome not in book:
            return None
        return float(book[outcome].get(round(float(price), 4), 0.0))

    def ticks_from_touch(self, outcome: str, price: float,
                         side: str) -> Optional[float]:
        t = (self.touch.get(outcome) or {}).get(
            "bid" if side == "BUY" else "ask")
        if not isinstance(t, (int, float)) or not self.tick:
            return None
        d = (float(t) - float(price)) if side == "BUY" else (
            float(price) - float(t))
        return round(d / self.tick, 2)


def _at(books: Sequence[Book], when: float) -> Optional[Book]:
    """The newest snapshot at or before `when`, if it is fresh enough.

    Deliberately never interpolates and never reaches forward: a mark at -5s
    must be answered by what the book looked like at -5s or not at all."""
    best: Optional[Book] = None
    for b in books:
        if b.t <= when and (best is None or b.t > best.t):
            best = b
    if best is None or (when - best.t) > BOOK_STALE_S:
        return None
    return best


# ------------------------------------------------------- parent reconstruction

CHAIN_RECOVERED = "CHAIN_RECOVERED"

#: every way a receipt lookup can end. RPC failure must never again be
#: indistinguishable from a receipt that genuinely carries no L5 order.
RECEIPT_OK = "RECEIPT_OK"                  # decoded, and L5 owns >=1 order
NO_L5_PARENT = "NO_L5_PARENT"              # decoded, and L5 owns none
RECEIPT_UNAVAILABLE = "RECEIPT_UNAVAILABLE"  # the node returned nothing
RPC_ERROR = "RPC_ERROR"                    # the call itself failed
DECODE_ERROR = "DECODE_ERROR"              # a receipt we could not parse
RECEIPT_STATUSES = (RECEIPT_OK, NO_L5_PARENT, RECEIPT_UNAVAILABLE, RPC_ERROR,
                    DECODE_ERROR)
#: the two that mean "we do not know", as opposed to "we looked and it is not
#: there". Only the latter is evidence.
MISSING_EVIDENCE = (RECEIPT_UNAVAILABLE, RPC_ERROR, DECODE_ERROR)


def _fetch_receipt(tx: str):
    """(status, legs). Never raises, and never conflates failure with absence."""
    from analysis.l5.causal import order_id_forensics as OF
    try:
        rc = OF.rpc("eth_getTransactionReceipt", [tx])
    except Exception:                                         # noqa: BLE001
        return RPC_ERROR, None
    if rc is None:
        return RECEIPT_UNAVAILABLE, None
    try:
        legs = OF.l5_orders_in(rc)
    except Exception:                                         # noqa: BLE001
        return DECODE_ERROR, None
    return (RECEIPT_OK if legs else NO_L5_PARENT), legs


def enrich_chain(rows: Sequence[dict], *, fetch=None) -> Dict[str, Any]:
    """Recover the chain legs the recorder failed to write, from the tapes.

    The recorder pinned a CA bundle that does not exist in the deployed image,
    so every eth_getTransactionReceipt returned None and every execution was
    written with `parents: []`. The FILLS are intact and each carries its
    `tx`, so the chain layer is recoverable here without capturing a single
    new window.

    Mutates the rows in place and reports what it recovered. A tx that still
    cannot be read stays empty and is counted -- an unrecovered receipt is
    never presented as a receipt containing no L5 order, because those two
    look identical and only one of them is a finding.
    """
    fetch = fetch or _fetch_receipt
    seen: Dict[str, Any] = {}
    prov: List[Dict[str, Any]] = []
    already = attempts = 0
    by_status: Dict[str, int] = {k: 0 for k in RECEIPT_STATUSES}
    no_tx = 0
    for r in rows:
        if r.get("kind") != "l5_parent_execution":
            continue
        if r.get("parents"):
            already += 1
            r.setdefault("receipt_status", RECEIPT_OK)
            continue
        tx = r.get("tx")
        if not tx:
            no_tx += 1
            r["receipt_status"] = RECEIPT_UNAVAILABLE
            continue
        if tx not in seen:
            attempts += 1
            try:
                seen[tx] = fetch(tx)
            except Exception:                                 # noqa: BLE001
                seen[tx] = (RPC_ERROR, None)
            st, legs = seen[tx]
            prov.append({"tx": tx, "status": st,
                         "n_l5_order_fills": (None if legs is None
                                              else len(legs)),
                         "order_hashes": ([x["order_hash"] for x in legs]
                                          if legs else [])})
        status, legs = seen[tx]
        by_status[status] = by_status.get(status, 0) + 1
        r["receipt_status"] = status
        if legs:
            r["parents"] = [{"order_hash": x["order_hash"],
                             "maker_asset_id": str(x["maker_asset_id"]),
                             "taker_asset_id": str(x["taker_asset_id"]),
                             "maker_amount_filled": str(x["maker_amount_filled"]),
                             "taker_amount_filled": str(x["taker_amount_filled"]),
                             "fee": str(x["fee"])} for x in legs]
            r["chain_recovery"] = CHAIN_RECOVERED
    missing = sum(by_status.get(k, 0) for k in MISSING_EVIDENCE)
    return {"already_had_legs": already,
            "recovered": by_status.get(RECEIPT_OK, 0),
            "no_l5_parent": by_status.get(NO_L5_PARENT, 0),
            "missing_evidence": missing, "executions_without_tx": no_tx,
            "by_status": by_status, "receipt_attempts": attempts,
            "distinct_tx": len(seen), "provenance": prov,
            "labels": ("RECEIPT_UNAVAILABLE / RPC_ERROR / DECODE_ERROR mean we "
                       "do not know. NO_L5_PARENT means we looked and it is "
                       "not there. Only the second is evidence, and the two "
                       "are never added together.")}


def collapse_to_parents(fills: Sequence[dict]) -> Dict[str, Any]:
    """Unit A (public fills) -> unit C (chain-proven parent orders).

    Fills sharing an `order_hash` are ONE parent order. This is the collapse
    the frozen worked case established -- 58 public fills became 37 proven
    parents there, and 30/10/40 at 7c became one parent of exactly 80.00.

    A fill with no recovered hash is NOT merged with anything. It stays its
    own unit and is labelled UNPROVEN, because guessing which unproven fills
    belong together is precisely the same-second-cluster heuristic that the
    forensics kept separate from chain proof.
    """
    by_hash: Dict[str, List[dict]] = {}
    unproven: List[dict] = []
    for f in fills:
        hashes = [h for h in (f.get("order_hashes") or []) if h]
        if len(set(hashes)) == 1:
            by_hash.setdefault(hashes[0], []).append(f)
        else:
            unproven.append(f)
    parents: List[dict] = []
    for h, group in by_hash.items():
        group = sorted(group, key=lambda x: x["L5_EVENT_CLOCK"])
        first = group[0]
        parents.append({**first,
                        "order_hash": h,
                        "qty": round(sum(g["qty"] for g in group), 6),
                        "n_fills": len(group),
                        "unit": "CHAIN_PROVEN_PARENT",
                        "execution_lifetime_lower_bound_s": round(
                            group[-1]["L5_EVENT_CLOCK"]
                            - first["L5_EVENT_CLOCK"], 3)})
    for f in unproven:
        parents.append({**f, "order_hash": None, "n_fills": 1,
                        "unit": "UNPROVEN_FILL"})
    parents.sort(key=lambda x: x["L5_EVENT_CLOCK"])
    for i, p in enumerate(parents):
        p["idx"] = i
    # inventory must be rebuilt over the COLLAPSED sequence, not carried over
    inv: Dict[str, float] = {}
    for p in parents:
        p["inventory_before"] = dict(inv)
        inv[p["outcome"]] = inv.get(p["outcome"], 0.0) + p["qty"]
        p["inventory_after"] = dict(inv)
    proven = [p for p in parents if p["unit"] == "CHAIN_PROVEN_PARENT"]
    return {"parents": parents, "n_units": len(parents),
            "n_chain_proven": len(proven),
            "n_unproven_fills": len(parents) - len(proven),
            "n_input_fills": len(fills),
            "collapse_ratio": (round(len(parents) / len(fills), 4)
                               if fills else None),
            "labels": ("unit A is a public fill, unit C is a chain-proven "
                       "parent. They are never equated, and an unproven fill "
                       "is never merged with another on a heuristic.")}


def parents_in_event_order(rows: Sequence[dict]) -> List[dict]:
    """Stable-sorted by L5_EVENT_CLOCK, with inventory rebuilt from scratch.

    The recorder's own inventory fields are written in DISCOVERY order and are
    not read. Ties keep file order, which is what `sorted` gives us."""
    ps = [r for r in rows if r.get("kind") == "l5_parent_execution"
          and isinstance(r.get("L5_EVENT_CLOCK"), (int, float))]
    ps = sorted(ps, key=lambda r: float(r["L5_EVENT_CLOCK"]))
    inv: Dict[str, float] = {}
    out: List[dict] = []
    for i, r in enumerate(ps):
        oc, qty = r.get("outcome"), float(r.get("qty") or 0.0)
        before = dict(inv)
        inv[oc] = inv.get(oc, 0.0) + qty
        legs = r.get("parents") or []
        out.append({
            "idx": i,
            "L5_EVENT_CLOCK": float(r["L5_EVENT_CLOCK"]),
            "observation_clock": r.get("L5_OBSERVATION_CLOCK"),
            "publication_lag_s": r.get("publication_lag_s"),
            "tx": r.get("tx"),
            "outcome": oc,
            "price": (float(r["price"]) if isinstance(r.get("price"),
                                                      (int, float)) else None),
            "qty": qty,
            "order_hashes": [l.get("order_hash") for l in legs],
            "n_legs": len(legs),
            "side": ("BUY" if _is_buy(r) else
                     ("SELL" if _is_buy(r) is False else None)),
            "fee_paid": _fee_paid(r),
            "liquidity_role": (None if _fee_paid(r) is None else
                               ("MAKER" if _fee_paid(r) == 0 else "TAKER")),
            "inventory_before": before,
            "inventory_after": dict(inv),
            "attribution": "OBSERVED_L5_PARENT",
        })
    return out


def _lot_class(qty: float) -> str:
    if abs(qty - 100.0) <= EXACT_TOL:
        return "EXACT_100"
    if abs(qty - 100.0) <= NEAR_100_BAND:
        return "NEAR_100"
    if abs(qty - round(qty)) <= EXACT_TOL and qty >= 1:
        return "ROUND_OTHER"
    return "FRACTIONAL"


# ------------------------------------------------------------------ scoring

def score_parent(p: dict, books: Sequence[Book], preds: Sequence[dict],
                 window_end: float) -> Dict[str, Any]:
    """One parent against the book at every preregistered mark."""
    t0, oc, px = p["L5_EVENT_CLOCK"], p["outcome"], p["price"]
    side = p["side"] or "BUY"
    row: Dict[str, Any] = {
        "idx": p["idx"], "tx": p["tx"], "outcome": oc, "price": px,
        "qty": p["qty"], "order_hashes": p["order_hashes"],
        "side": p["side"], "liquidity_role": p["liquidity_role"],
        "lot_class": _lot_class(p["qty"]),
        "inventory_before": p["inventory_before"],
        "inventory_after": p["inventory_after"],
        "residual_before": round(p["inventory_before"].get("Up", 0.0)
                                 - p["inventory_before"].get("Down", 0.0), 6),
        "residual_after": round(p["inventory_after"].get("Up", 0.0)
                                - p["inventory_after"].get("Down", 0.0), 6),
        "tte_s": round(window_end - t0, 1),
        "L5_EVENT_CLOCK": t0,
        "marks": {},
        "replenishment": {},
        "attribution": "OBSERVED_L5_PARENT",
    }
    if px is None:
        row["unscorable"] = "the parent carries no price"
        return row

    for m in MARKS_S:
        b = _at(books, t0 + m)
        if b is None:
            row["marks"][str(m)] = {"readable": False,
                                    "why": "no book within %.0fs" % BOOK_STALE_S}
            continue
        sz = b.size_at(oc, px, side)
        opp = "Down" if oc == "Up" else "Up"
        row["marks"][str(m)] = {
            "readable": True,
            "book_venue_clock": b.t,
            "compatible_qty_displayed": sz,
            "covers_parent_qty": (None if sz is None else sz >= p["qty"]),
            "present": (None if sz is None else sz > 0.0),
            "ticks_from_touch": b.ticks_from_touch(oc, px, side),
            "touch": b.touch.get(oc),
            "one_sided": b.one_sided.get(oc),
            "opposite_touch": b.touch.get(opp),
            "attribution": "L5_COMPATIBLE_BOOK_LIQUIDITY",
        }

    # earliest sighting anywhere before the execution
    first = None
    for b in sorted(books, key=lambda x: x.t):
        if b.t >= t0:
            break
        sz = b.size_at(oc, px, side)
        if sz is not None and sz > 0.0:
            first = b
            break
    row["first_observed_compatible"] = (
        None if first is None else
        {"book_venue_clock": first.t, "lead_s": round(t0 - first.t, 1),
         "qty_displayed": first.size_at(oc, px, side),
         "attribution": "L5_COMPATIBLE_BOOK_LIQUIDITY"})

    # the frozen prediction, which must have been emitted BEFORE this execution
    hit_exact = hit_near = None
    tick = books[0].tick if books else 0.01
    for pr in preds:
        dt = pr.get("decision_time")
        if not isinstance(dt, (int, float)) or dt >= t0:
            continue
        for q in (pr.get("predictions") or []):
            if q.get("outcome") != oc or q.get("side") != side:
                continue
            qp = q.get("price")
            if not isinstance(qp, (int, float)):
                continue
            d = abs(float(qp) - px)
            if d <= EXACT_TOL:
                hit_exact = {"decision_time": dt, "price": qp,
                             "ticks_behind_ask": q.get("ticks_behind_ask"),
                             "qty_base_lot": q.get("qty_base_lot")}
            elif d <= tick + EXACT_TOL and hit_near is None:
                hit_near = {"decision_time": dt, "price": qp,
                            "delta_ticks": round(d / tick, 2)}
    row["predicted_exact"] = hit_exact
    row["predicted_within_1_tick"] = hit_near or hit_exact
    # QUANTITY compatibility is a separate question from price. A surface that
    # names the right level with the wrong lot has predicted the geometry and
    # not the sizing, and collapsing the two would hide that.
    base = (hit_exact or hit_near or {}).get("qty_base_lot")
    row["predicted_qty_base_lot"] = base
    row["qty_compatible_with_prediction"] = (
        None if base is None else bool(abs(float(base) - p["qty"]) <= EXACT_TOL))
    row["qty_within_prediction_lot"] = (
        None if base is None else bool(p["qty"] <= float(base) + EXACT_TOL))
    row["prediction_attribution"] = "INFERRED_SURFACE"

    for m in REPLENISH_S:
        b = _at(books, t0 + m)
        row["replenishment"][str(m)] = (
            {"readable": False} if b is None else
            {"readable": True, "qty_displayed": b.size_at(oc, px, side),
             "attribution": "L5_COMPATIBLE_BOOK_LIQUIDITY"})
    return row


# ------------------------------------------------- the causal reversal test

OPPOSITE_ALREADY_PRESENT = "OPPOSITE_ALREADY_PRESENT"
OPPOSITE_APPEARED_AFTER_FILL = "OPPOSITE_APPEARED_AFTER_FILL"
OPPOSITE_NEVER_OBSERVED = "OPPOSITE_NEVER_OBSERVED"
UNRESOLVED = "UNRESOLVED"


def reversals(parents: Sequence[dict], books: Sequence[Book]
              ) -> List[Dict[str, Any]]:
    """For every outcome reversal A -> B: was compatible liquidity for the
    FUTURE parent B already resting BEFORE A executed?

    This is the test that separates a standing surface from a reacting one. A
    reactive quoter cannot have had B's level up before A's fill, because B's
    level is a response to A. A persistent two-sided surface must have.

    The window is bounded at both ends on purpose. `before A` excludes A's own
    execution instant, and the search starts no earlier than A's own first
    sighting, so a level that merely happened to exist all session does not
    count as a prediction about B.
    """
    out: List[Dict[str, Any]] = []
    ordered = sorted(books, key=lambda x: x.t)
    for a, b in zip(parents, parents[1:]):
        if a["outcome"] == b["outcome"]:
            continue
        rec: Dict[str, Any] = {
            "from_idx": a["idx"], "to_idx": b["idx"],
            "from": {"outcome": a["outcome"], "price": a["price"],
                     "qty": a["qty"], "t": a["L5_EVENT_CLOCK"]},
            "to": {"outcome": b["outcome"], "price": b["price"],
                   "qty": b["qty"], "t": b["L5_EVENT_CLOCK"]},
            "gap_s": round(b["L5_EVENT_CLOCK"] - a["L5_EVENT_CLOCK"], 2),
        }
        if b["price"] is None or a["price"] is None:
            rec["verdict"] = UNRESOLVED
            rec["why"] = "a parent carries no price"
            out.append(rec)
            continue

        side = b["side"] or "BUY"
        pre = [x for x in ordered if x.t < a["L5_EVENT_CLOCK"]]
        mid = [x for x in ordered
               if a["L5_EVENT_CLOCK"] <= x.t < b["L5_EVENT_CLOCK"]]
        pre_read = [x for x in pre if x.size_at(b["outcome"], b["price"], side)
                    is not None]
        mid_read = [x for x in mid if x.size_at(b["outcome"], b["price"], side)
                    is not None]

        if not pre_read:
            rec["verdict"] = UNRESOLVED
            rec["why"] = ("the book before A never answered for B's level; "
                          "absence of a reading is not a reading of absence")
            out.append(rec)
            continue

        pre_hits = [x for x in pre_read
                    if (x.size_at(b["outcome"], b["price"], side) or 0.0) > 0.0]
        mid_hits = [x for x in mid_read
                    if (x.size_at(b["outcome"], b["price"], side) or 0.0) > 0.0]
        if pre_hits:
            last = max(pre_hits, key=lambda x: x.t)
            rec["verdict"] = OPPOSITE_ALREADY_PRESENT
            rec["evidence"] = {
                "snapshots_before_A_showing_B_level": len(pre_hits),
                "snapshots_before_A_readable": len(pre_read),
                "latest_before_A": {
                    "book_venue_clock": last.t,
                    "lead_s": round(a["L5_EVENT_CLOCK"] - last.t, 1),
                    "qty_displayed": last.size_at(b["outcome"], b["price"],
                                                  side)},
                "covers_B_qty": any(
                    (x.size_at(b["outcome"], b["price"], side) or 0.0)
                    >= b["qty"] for x in pre_hits),
                "attribution": "L5_COMPATIBLE_BOOK_LIQUIDITY",
            }
        elif mid_hits:
            rec["verdict"] = OPPOSITE_APPEARED_AFTER_FILL
            first = min(mid_hits, key=lambda x: x.t)
            rec["evidence"] = {
                "snapshots_before_A_readable": len(pre_read),
                "all_read_zero_before_A": True,
                "first_after_A": {
                    "book_venue_clock": first.t,
                    "delay_after_A_s": round(first.t - a["L5_EVENT_CLOCK"], 1),
                    "qty_displayed": first.size_at(b["outcome"], b["price"],
                                                   side)},
                "attribution": "L5_COMPATIBLE_BOOK_LIQUIDITY",
            }
        elif mid_read:
            rec["verdict"] = OPPOSITE_NEVER_OBSERVED
            rec["evidence"] = {
                "snapshots_before_A_readable": len(pre_read),
                "snapshots_between_readable": len(mid_read),
                "reads": ("B's level was served and empty both before and "
                          "after A, right up to B's own execution"),
            }
        else:
            rec["verdict"] = UNRESOLVED
            rec["why"] = "no readable snapshot between A and B"
        out.append(rec)
    return out


# --------------------------------------------------------- aggregate measures

def _share(n: int, d: int) -> Optional[float]:
    return None if not d else round(n / d, 4)


def both_sides_simultaneous(books: Sequence[Book], preds: Sequence[dict]
                            ) -> Dict[str, Any]:
    """How often BOTH outcomes simultaneously show size at a predicted level.

    Judged per snapshot against the most recent prediction set that preceded
    it, so the levels tested are the ones the frozen hypothesis had actually
    committed to at that moment."""
    ps = sorted((p for p in preds
                 if isinstance(p.get("decision_time"), (int, float))),
                key=lambda p: p["decision_time"])
    readable = both = one = neither = 0
    for b in sorted(books, key=lambda x: x.t):
        cur = None
        for p in ps:
            if p["decision_time"] <= b.t:
                cur = p
            else:
                break
        if cur is None:
            continue
        sides: Dict[str, bool] = {}
        unreadable = False
        for q in (cur.get("predictions") or []):
            oc, px, sd = q.get("outcome"), q.get("price"), q.get("side")
            if not isinstance(px, (int, float)):
                continue
            sz = b.size_at(oc, float(px), sd or "BUY")
            if sz is None:
                unreadable = True
                continue
            sides[oc] = sides.get(oc, False) or sz > 0.0
        if unreadable and not sides:
            continue
        readable += 1
        live = sum(1 for v in sides.values() if v)
        if live >= 2:
            both += 1
        elif live == 1:
            one += 1
        else:
            neither += 1
    return {"readable_snapshots": readable, "both_outcomes": both,
            "one_outcome": one, "neither": neither,
            "both_outcomes_share": _share(both, readable),
            "attribution": "L5_COMPATIBLE_BOOK_LIQUIDITY vs INFERRED_SURFACE"}


def adjacent_tick_geometry(parents: Sequence[dict], tick: float
                           ) -> Dict[str, Any]:
    """Gaps between consecutive parents on the SAME outcome, in ticks."""
    gaps: Dict[str, List[float]] = {}
    last: Dict[str, dict] = {}
    for p in parents:
        oc = p["outcome"]
        if p["price"] is None:
            continue
        if oc in last:
            gaps.setdefault(oc, []).append(
                round(abs(p["price"] - last[oc]["price"]) / tick, 2))
        last[oc] = p
    flat = [g for v in gaps.values() for g in v]
    hist: Dict[str, int] = {}
    for g in flat:
        hist[str(int(round(g)))] = hist.get(str(int(round(g))), 0) + 1
    return {"n": len(flat), "by_outcome_n": {k: len(v) for k, v in gaps.items()},
            "tick_gap_histogram": dict(sorted(hist.items(),
                                              key=lambda kv: int(kv[0]))),
            "same_tick_share": _share(hist.get("0", 0), len(flat)),
            "one_tick_share": _share(hist.get("1", 0), len(flat))}


def qty_distribution(parents: Sequence[dict]) -> Dict[str, Any]:
    qs = [p["qty"] for p in parents]
    classes: Dict[str, int] = {}
    for p in parents:
        c = _lot_class(p["qty"])
        classes[c] = classes.get(c, 0) + 1
    qs_sorted = sorted(qs)

    def pct(f: float) -> Optional[float]:
        if not qs_sorted:
            return None
        return round(qs_sorted[min(len(qs_sorted) - 1,
                                   int(f * len(qs_sorted)))], 4)
    return {"n": len(qs), "classes": classes,
            "exact_100_share": _share(classes.get("EXACT_100", 0), len(qs)),
            "p25": pct(0.25), "p50": pct(0.50), "p75": pct(0.75),
            "p90": pct(0.90), "max": (max(qs) if qs else None),
            "min": (min(qs) if qs else None)}


def _corr(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return round(sum((x - mx) * (y - my)
                     for x, y in zip(xs, ys)) / (sx * sy), 4)


def qty_vs_inventory(parents: Sequence[dict]) -> Dict[str, Any]:
    tot = [sum(p["inventory_before"].values()) for p in parents]
    res = [abs(p["inventory_before"].get("Up", 0.0)
               - p["inventory_before"].get("Down", 0.0)) for p in parents]
    qs = [p["qty"] for p in parents]
    return {"n": len(qs),
            "corr_total_inventory_vs_qty": _corr(tot, qs),
            "corr_abs_residual_vs_qty": _corr(res, qs),
            "reads": ("a fixed base lot shows near-zero correlation; a "
                      "size-scaled surface would not")}


def qty_vs_tte(scored: Sequence[dict]) -> Dict[str, Any]:
    """Does the lot shrink as expiry approaches, or is it flat?"""
    pairs = [(r["tte_s"], r["qty"]) for r in scored
             if isinstance(r.get("tte_s"), (int, float))]
    buckets: Dict[str, List[float]] = {}
    for tte, q in pairs:
        k = ("tte>600" if tte > 600 else "tte300-600" if tte > 300
             else "tte120-300" if tte > 120 else "tte<=120")
        buckets.setdefault(k, []).append(q)
    return {"n": len(pairs),
            "corr_tte_vs_qty": _corr([t for t, _ in pairs],
                                     [q for _, q in pairs]),
            "by_bucket": {k: {"n": len(v),
                              "mean_qty": round(sum(v) / len(v), 4),
                              "exact_100": sum(1 for x in v
                                               if abs(x - 100.0) <= EXACT_TOL)}
                          for k, v in sorted(buckets.items())}}


def coverage(books: Sequence["Book"], parents: Sequence[dict],
             window_start: float, window_end: float) -> Dict[str, Any]:
    """Was anyone LOOKING, and for how much of the window?

    A finding of "no compatible liquidity" is only a finding over intervals we
    actually observed, so the gaps are reported rather than smoothed over."""
    ts = sorted(b.t for b in books)
    gaps = [round(b - a, 2) for a, b in zip(ts, ts[1:])]
    big = [g for g in gaps if g > BOOK_STALE_S]
    with_receipt = sum(1 for p in parents if p["n_legs"] > 0)
    return {
        "book_snapshots": len(books),
        "book_first_venue_clock": (ts[0] if ts else None),
        "book_last_venue_clock": (ts[-1] if ts else None),
        "book_median_interval_s": (
            round(sorted(gaps)[len(gaps) // 2], 2) if gaps else None),
        "book_gaps_over_stale_threshold": len(big),
        "book_largest_gap_s": (max(gaps) if gaps else None),
        "book_seconds_covered": (round(ts[-1] - ts[0], 1) if len(ts) > 1
                                 else 0.0),
        "capture_span_required_s": round((window_end + 300)
                                         - (window_start - 120), 1),
        "chain_parents": len(parents),
        "chain_parents_with_receipt": with_receipt,
        "chain_receipt_share": _share(with_receipt, len(parents)),
        "parents_missing_price": sum(1 for p in parents
                                     if p["price"] is None),
        "labels": ("an absence of compatible liquidity is a claim only over "
                   "intervals a snapshot actually covered"),
    }


def opposite_recycling(parents: Sequence[dict]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"n": len(parents)}
    for m in RECYCLE_S:
        hit = 0
        for i, p in enumerate(parents):
            t0, oc = p["L5_EVENT_CLOCK"], p["outcome"]
            if any(q["outcome"] != oc and 0 < q["L5_EVENT_CLOCK"] - t0 <= m
                   for q in parents[i + 1:]):
                hit += 1
        out["p_opposite_within_%ds" % m] = _share(hit, len(parents))
        out["n_opposite_within_%ds" % m] = hit
    return out


def replenishment_summary(scored: Sequence[dict]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for m in REPLENISH_S:
        rs = [r["replenishment"].get(str(m)) or {} for r in scored]
        read = [x for x in rs if x.get("readable")]
        live = [x for x in read if (x.get("qty_displayed") or 0.0) > 0.0]
        out["at_+%ds" % m] = {
            "readable": len(read), "present": len(live),
            "present_share": _share(len(live), len(read))}
    return out


def pair_economics(parents: Sequence[dict]) -> Dict[str, Any]:
    """Average-cost matched-pair economics, labelled for what it is.

    The matched / residual split is ACCOUNTING-METHOD DEPENDENT -- avg-cost and
    FIFO give materially different splits of the same total -- so this reports
    the average-cost view and says so. Nothing here is realized: no position is
    settled inside a 15-minute window.
    """
    spend: Dict[str, float] = {}
    qty: Dict[str, float] = {}
    for p in parents:
        if p["price"] is None:
            continue
        spend[p["outcome"]] = spend.get(p["outcome"], 0.0) + p["price"] * p["qty"]
        qty[p["outcome"]] = qty.get(p["outcome"], 0.0) + p["qty"]
    up, dn = qty.get("Up", 0.0), qty.get("Down", 0.0)
    avg = {k: (spend[k] / qty[k] if qty.get(k) else None) for k in qty}
    matched = min(up, dn)
    pair = None
    if avg.get("Up") is not None and avg.get("Down") is not None:
        pair = round(avg["Up"] + avg["Down"], 6)
    return {
        "qty_by_outcome": {k: round(v, 6) for k, v in qty.items()},
        "avg_cost_by_outcome": {k: (None if v is None else round(v, 6))
                                for k, v in avg.items()},
        "matched_qty": round(matched, 6),
        "residual_qty": round(up - dn, 6),
        "effective_pair_cost_avg_cost_basis": pair,
        "below_par": (None if pair is None else pair < 1.0),
        "labels": [
            "AVERAGE-COST BASIS. The matched/residual split is "
            "accounting-method dependent; FIFO on the same fills gives a "
            "different split of the same total cash.",
            "NOT REALIZED and not a profit. Nothing settles inside a window; "
            "this is the cost of inventory acquired, no more.",
        ],
    }


# --------------------------------------------------- mechanism classification

def classify(agg: Dict[str, Any]) -> Dict[str, Any]:
    """Apply MECHANISM_RULE, which was frozen above before any scoring.

    Returns UNRESOLVED rather than the nearest fit when no branch is satisfied,
    and refuses entirely below the declared minimum sample."""
    pre = (agg.get("primary") or {}).get("share_at_-60s")
    rv = agg.get("reversals") or {}
    resolved = rv.get("resolved", 0)
    already = rv.get("share_already_present")
    after = rv.get("share_appeared_after_fill")
    both = (agg.get("both_sides") or {}).get("both_outcomes_share")
    n_par = (agg.get("parents") or {}).get("n", 0)

    ev = {"pre_execution_compatible_share_at_-60s": pre,
          "reversals_resolved": resolved,
          "share_already_present": already,
          "share_appeared_after_fill": after,
          "both_outcomes_simultaneous_share": both,
          "parents": n_par}

    if n_par < MECHANISM_RULE["min_parents"]:
        return {"verdict": UNRESOLVED, "evidence": ev,
                "why": "fewer than %d parents" % MECHANISM_RULE["min_parents"]}
    if pre is None:
        return {"verdict": UNRESOLVED, "evidence": ev,
                "why": "the -60s mark was not readable often enough to have a "
                       "share at all"}
    if pre < 0.50:
        return {"verdict": "H3-REJECTED", "evidence": ev,
                "why": "compatible liquidity was usually NOT resting before "
                       "the execution, so a standing passive surface did not "
                       "produce these fills"}
    if pre < 0.80:
        return {"verdict": UNRESOLVED, "evidence": ev,
                "why": "the pre-execution share falls between the frozen "
                       "rejection and acceptance thresholds"}
    if resolved < MECHANISM_RULE["min_resolved_reversals"]:
        return {"verdict": UNRESOLVED, "evidence": ev,
                "why": "only %d resolved reversals; the causal test needs %d"
                       % (resolved, MECHANISM_RULE["min_resolved_reversals"])}
    if (already is not None and already >= 0.70
            and both is not None and both >= 0.70):
        return {"verdict": "H3-A PERSISTENT_TWO_SIDED_SURFACE", "evidence": ev,
                "why": "the opposite level was already standing before the "
                       "fill that preceded it, and both outcomes are quoted "
                       "simultaneously"}
    if after is not None and after >= 0.70:
        return {"verdict": "H3-B STATE_REFRESHED_PASSIVE_SURFACE",
                "evidence": ev,
                "why": "liquidity rests passively, but the opposite level "
                       "appears only after a fill"}
    if already is not None and already >= 0.70:
        return {"verdict": "H3-C HYBRID", "evidence": ev,
                "why": "the opposite level is usually already present, but "
                       "both outcomes are not simultaneously quoted often "
                       "enough for the persistent two-sided reading"}
    return {"verdict": "H3-C HYBRID", "evidence": ev,
            "why": "neither reversal class reaches the frozen 0.70, so both "
                   "signatures are present"}


UNRESOLVED_VARIABLES = [
    "OWNERSHIP. Every book quantity here is anonymous displayed size. No "
    "public source attributes a resting order to L5, so whether the "
    "compatible liquidity was HIS is unresolved and is not resolved by this "
    "study.",
    "ORDER LIFETIME. executed_qty is a LOWER BOUND on the parent's size and "
    "first-to-last execution is an execution-lifetime lower bound, never the "
    "order's lifetime. When a parent was placed is unobservable.",
    "CANCELLATIONS. Unfilled and cancelled orders are not in any feed we "
    "have. A surface that was placed and pulled without trading leaves no "
    "trace here.",
    "QUEUE POSITION. Size at a price says nothing about where in that queue "
    "an order sat, so whether a compatible quote was actually reachable is "
    "unresolved.",
    "INCENTIVES. Maker rebates and liquidity rewards are not observable per "
    "order and are NOT netted into any economics reported here.",
    "SETTLEMENT. Nothing settles inside a 15-minute window, so no number here "
    "is realized profit.",
]


# ----------------------------------------------------------------- reporting

def analyse_window(path: str, *, enrich: bool = False,
                   fetch=None) -> Dict[str, Any]:
    rows = _rows(path)
    pre = next((r for r in rows if r.get("kind") == "preregistration"), None)
    end_row = next((r for r in rows if r.get("kind") == "window_end"), None)
    books = [Book(r) for r in rows if r.get("kind") == "book_full_joint"]
    preds = [r for r in rows if r.get("kind") == "surface_prediction"]
    enrichment = enrich_chain(rows, fetch=fetch) if enrich else None
    fills = parents_in_event_order(rows)
    collapse = collapse_to_parents(fills)
    # scoring runs on the COLLAPSED units: a parent order is the thing L5
    # placed, and three fills of it are one decision, not three
    parents = collapse["parents"]
    w_end = float((pre or {}).get("window_end") or 0.0)
    tick = books[0].tick if books else 0.01

    scored = [score_parent(p, books, preds, w_end) for p in parents]
    revs = reversals(parents, books)

    # PRIMARY: compatible liquidity resting before the first chain-proven
    # execution, at each mark
    primary: Dict[str, Any] = {}
    for m in MARKS_S:
        rd = [r for r in scored
              if (r["marks"].get(str(m)) or {}).get("readable")
              and (r["marks"][str(m)].get("present") is not None)]
        hit = [r for r in rd if r["marks"][str(m)]["present"]]
        cov = [r for r in rd if r["marks"][str(m)].get("covers_parent_qty")]
        primary["share_at_%ds" % m] = _share(len(hit), len(rd))
        primary["n_readable_at_%ds" % m] = len(rd)
        primary["n_present_at_%ds" % m] = len(hit)
        primary["share_covering_qty_at_%ds" % m] = _share(len(cov), len(rd))

    firsts = [r["first_observed_compatible"]["lead_s"] for r in scored
              if r.get("first_observed_compatible")]
    firsts_sorted = sorted(firsts)
    counts = {
        "resolved": sum(1 for r in revs if r["verdict"] != UNRESOLVED),
        "already_present": sum(1 for r in revs
                               if r["verdict"] == OPPOSITE_ALREADY_PRESENT),
        "appeared_after_fill": sum(
            1 for r in revs if r["verdict"] == OPPOSITE_APPEARED_AFTER_FILL),
        "never_observed": sum(1 for r in revs
                              if r["verdict"] == OPPOSITE_NEVER_OBSERVED),
        "unresolved": sum(1 for r in revs if r["verdict"] == UNRESOLVED),
        "total": len(revs),
    }
    counts["share_already_present"] = _share(counts["already_present"],
                                             counts["resolved"])
    counts["share_appeared_after_fill"] = _share(counts["appeared_after_fill"],
                                                 counts["resolved"])

    ex = [r for r in scored if r.get("predicted_exact")]
    nr = [r for r in scored if r.get("predicted_within_1_tick")]
    return {
        "path": path,
        "window_start": (pre or {}).get("window_start"),
        "window_end": (pre or {}).get("window_end"),
        "slug": (pre or {}).get("slug"),
        "chain_enrichment": enrichment,
        "collapse": {k: v for k, v in collapse.items() if k != "parents"},
        "counts": {"books": len(books), "predictions": len(preds),
                   "public_fills": len(fills), "parents": len(parents),
                   "recorder_reported": {
                       "books": (end_row or {}).get("books"),
                       "predictions": (end_row or {}).get("predictions"),
                       "parent_executions": (
                           end_row or {}).get("parent_executions")}},
        "parents": {"n": len(parents),
                    "chain_proven": collapse["n_chain_proven"],
                    "unproven_fills": collapse["n_unproven_fills"],
                    "maker": sum(1 for p in parents
                                 if p.get("liquidity_role") == "MAKER"),
                    "taker": sum(1 for p in parents
                                 if p.get("liquidity_role") == "TAKER"),
                    "role_unknown": sum(1 for p in parents
                                        if p.get("liquidity_role") is None)},
        "primary": primary,
        "first_observed_compatible_lead_s": {
            "n": len(firsts),
            "p50": (firsts_sorted[len(firsts_sorted) // 2]
                    if firsts_sorted else None),
            "min": (firsts_sorted[0] if firsts_sorted else None),
            "max": (firsts_sorted[-1] if firsts_sorted else None)},
        "prediction_hits": {
            "n_parents": len(scored),
            "outcome_predicted": sum(
                1 for r in scored if r.get("predicted_within_1_tick")),
            "exact": len(ex), "exact_share": _share(len(ex), len(scored)),
            "within_1_tick": len(nr),
            "within_1_tick_share": _share(len(nr), len(scored)),
            "qty_exactly_compatible": sum(
                1 for r in scored
                if r.get("qty_compatible_with_prediction")),
            "qty_within_lot": sum(1 for r in scored
                                  if r.get("qty_within_prediction_lot")),
            "qty_judgeable": sum(1 for r in scored
                                 if r.get("predicted_qty_base_lot")
                                 is not None),
            "attribution": "INFERRED_SURFACE vs OBSERVED_L5_PARENT"},
        "reversals": counts,
        "reversal_rows": revs,
        "both_sides": both_sides_simultaneous(books, preds),
        "geometry": adjacent_tick_geometry(parents, tick),
        "qty": qty_distribution(parents),
        "qty_vs_inventory": qty_vs_inventory(parents),
        "qty_vs_tte": qty_vs_tte(scored),
        "coverage": coverage(books, parents, float((pre or {}).get(
            "window_start") or 0.0), w_end),
        "recycling": opposite_recycling(parents),
        "replenishment": replenishment_summary(scored),
        "pair_economics": pair_economics(parents),
        "scored_parents": scored,
    }


N_REQUIRED = EL.N_REQUIRED
INTEGRITY_FAILURE = "INTEGRITY_FAILURE_NO_VERDICT"


def integrity(el: Dict[str, Any], windows: Sequence[Dict[str, Any]],
              state_root: Optional[str]) -> Dict[str, Any]:
    """What the sample IS, before anything is said about what it shows.

    The preregistered sample is three COMPLETE windows. Below that there is no
    mechanism verdict to give -- not UNRESOLVED, which is a finding, but no
    verdict at all, because a preregistered test run on a short sample is not
    that test."""
    import hashlib
    mod = "src/l5_window_forensics.py"
    sha = None
    if os.path.exists(mod):
        sha = hashlib.sha256(open(mod, "rb").read()).hexdigest()
    gates: List[Dict[str, Any]] = []
    for c in el["captures"]:
        gates.append({"window_start": c.get("window_start"),
                      "verdict": c["verdict"], "durable": c.get("durable"),
                      "declared_ineligible": c.get("declared_ineligible"),
                      "failed": c.get("failed"), "path": c.get("path")})
    cov = None
    if state_root:
        try:
            from analysis.l5 import coverage as CV
            rep = CV.report(state_root)
            cov = {k: rep.get(k) for k in
                   ("markers", "boots", "heartbeats", "restarts", "exits",
                    "span_s")}
            cov["window_forensics"] = (rep.get("per_stream")
                                       or {}).get("window_forensics")
        except Exception as exc:                              # noqa: BLE001
            cov = {"available": False,
                   "err": f"{type(exc).__name__}: {exc}"[:200]}
    return {
        "eligible_n": el["n_complete"],
        "n_required": N_REQUIRED,
        "sample_complete": el["n_complete"] >= N_REQUIRED,
        "eligible_window_ids": el["complete_windows"],
        "diagnostic_captures": el.get("diagnostic_captures"),
        "frozen_module": mod,
        "frozen_module_sha256": sha,
        "gates_per_capture": gates,
        "supervisor_coverage": cov,
        "chain_recovery_counts": {
            "public_fills": sum(w["counts"]["public_fills"] for w in windows),
            "unique_tx": sum((w.get("chain_enrichment") or {}).get(
                "distinct_tx", 0) for w in windows),
            "receipt_attempts": sum((w.get("chain_enrichment") or {}).get(
                "receipt_attempts", 0) for w in windows),
            "receipt_ok": sum((w.get("chain_enrichment") or {}).get(
                "recovered", 0) for w in windows),
            "no_l5_parent": sum((w.get("chain_enrichment") or {}).get(
                "no_l5_parent", 0) for w in windows),
            "missing_evidence": sum((w.get("chain_enrichment") or {}).get(
                "missing_evidence", 0) for w in windows),
            "decoded_l5_executions": sum(
                w["counts"]["public_fills"]
                - (w.get("chain_enrichment") or {}).get("missing_evidence", 0)
                - (w.get("chain_enrichment") or {}).get("no_l5_parent", 0)
                for w in windows),
            "proven_parents": sum(w["collapse"]["n_chain_proven"]
                                  for w in windows),
            "unresolved_units": sum(w["collapse"]["n_unproven_fills"]
                                    for w in windows),
            "labels": ("missing_evidence is NOT absence. A unit counted there "
                       "is one we could not read, and it is never merged with "
                       "no_l5_parent, which is one we read and found empty."),
        },
        "chain_units": [
            {"window_start": w["window_start"],
             "public_fills": w["counts"]["public_fills"],
             "units_after_collapse": w["collapse"]["n_units"],
             "chain_proven_parents": w["collapse"]["n_chain_proven"],
             "unproven_fills": w["collapse"]["n_unproven_fills"],
             "enrichment": w.get("chain_enrichment")}
            for w in windows],
        "per_window_coverage": [
            {"window_start": w["window_start"], **w["coverage"]}
            for w in windows],
        "labels": [
            "lead / close / follow / origin are the four frozen gates; a "
            "capture failing any one is not a window of this sample.",
            "Below %d eligible windows there is NO mechanism verdict. That is "
            "an integrity failure to report, not a result to interpret."
            % N_REQUIRED,
        ],
    }


def report(*dirs: str, state_root: Optional[str] = None,
           enrich: bool = False) -> Dict[str, Any]:
    """Eligibility FIRST, then the strategy result -- never the other way."""
    el = EL.report(*dirs)
    eligible = [c["path"] for c in el["captures"]
                if c["verdict"] == EL.COMPLETE]
    windows = [analyse_window(p, enrich=enrich) for p in sorted(eligible)]

    pooled_primary: Dict[str, Any] = {}
    for m in MARKS_S:
        rd = sum(w["primary"]["n_readable_at_%ds" % m] for w in windows)
        hp = sum(w["primary"]["n_present_at_%ds" % m] for w in windows)
        pooled_primary["share_at_%ds" % m] = _share(hp, rd)
        pooled_primary["n_readable_at_%ds" % m] = rd
        pooled_primary["n_present_at_%ds" % m] = hp
    pooled_rev = {k: sum((w["reversals"].get(k) or 0) for w in windows)
                  for k in ("resolved", "already_present",
                            "appeared_after_fill", "never_observed",
                            "unresolved", "total")}
    pooled_rev["share_already_present"] = _share(pooled_rev["already_present"],
                                                 pooled_rev["resolved"])
    pooled_rev["share_appeared_after_fill"] = _share(
        pooled_rev["appeared_after_fill"], pooled_rev["resolved"])
    bs_read = sum(w["both_sides"]["readable_snapshots"] for w in windows)
    bs_both = sum(w["both_sides"]["both_outcomes"] for w in windows)
    agg = {
        "parents": {"n": sum(w["parents"]["n"] for w in windows),
                    "maker": sum(w["parents"]["maker"] for w in windows),
                    "taker": sum(w["parents"]["taker"] for w in windows)},
        "primary": pooled_primary,
        "reversals": pooled_rev,
        "both_sides": {"readable_snapshots": bs_read, "both_outcomes": bs_both,
                       "both_outcomes_share": _share(bs_both, bs_read)},
    }
    integ = integrity(el, windows, state_root)
    # THE GATE. A preregistered test run on a short sample is not that test,
    # so an incomplete sample yields no verdict at all rather than a weak one.
    mech = (classify(agg) if integ["sample_complete"] else {
        "verdict": INTEGRITY_FAILURE,
        "why": ("the preregistered sample is %d complete windows and only %d "
                "are eligible. No mechanism verdict is produced from an "
                "incomplete preregistered sample."
                % (N_REQUIRED, integ["eligible_n"])),
        "next": ("continue the identical frozen capture procedure, without "
                 "retuning, until %d eligible durable windows exist"
                 % N_REQUIRED),
        "evidence": {"eligible_n": integ["eligible_n"]}})
    return {
        "label": LABEL,
        "integrity": integ,
        "eligibility": {k: v for k, v in el.items() if k != "captures"},
        "eligible_windows": [w["window_start"] for w in windows],
        "eligibility_captures": el["captures"],
        "mechanism_rule": MECHANISM_RULE,
        "pooled": agg,
        "mechanism": mech,
        "unresolved_variables": UNRESOLVED_VARIABLES,
        "windows": windows,
        "labels": [
            "POOLED, not averaged over windows: per-window counts are summed. "
            "Nothing is merged ACROSS windows -- a level in one window can "
            "never answer for a parent in another.",
            "Every book quantity is L5_COMPATIBLE_BOOK_LIQUIDITY. It is never "
            "called L5-owned.",
            "Inventory is recomputed from a stable sort on L5_EVENT_CLOCK. The "
            "recorder's discovery-order inventory fields are not read.",
        ],
    }


def persist(rep: Dict[str, Any], out_dir: str) -> Optional[str]:
    """Write the COMPLETE artifact next to the tapes.

    The log carries a summary and the log rotates; the artifact is the record.
    Written whole -- every scored parent and every reversal row -- because a
    mechanism label without the rows behind it cannot be re-checked."""
    try:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, LABEL + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, default=str, indent=1)
        os.replace(tmp, path)          # atomic: never a half-written artifact
        return path
    except OSError:
        return None


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="*",
                    default=["/state/l5-research/l5-window-forensics"])
    ap.add_argument("--state-root", default=None,
                    help="supervisor state root, for coverage markers")
    ap.add_argument("--enrich", action="store_true",
                    help="recover missing chain receipts from recorded tx")
    ap.add_argument("--rows", action="store_true",
                    help="also emit one L5_PSP_ROW line per scored parent")
    a = ap.parse_args(argv)
    dirs = a.dirs or ["/state/l5-research/l5-window-forensics"]
    rep = report(*dirs, state_root=a.state_root, enrich=a.enrich)
    written = persist(rep, dirs[0])
    rep["artifact_path"] = written
    slim = {k: v for k, v in rep.items() if k != "windows"}
    slim["windows"] = [{k: v for k, v in w.items()
                        if k not in ("scored_parents", "reversal_rows")}
                       for w in rep["windows"]]
    print("L5_PSP_SUMMARY " + json.dumps(slim, default=str), flush=True)
    if a.rows:
        for w in rep["windows"]:
            for r in w["scored_parents"]:
                print("L5_PSP_ROW " + json.dumps(
                    {"window": w["window_start"], **r}, default=str),
                    flush=True)
            for r in w["reversal_rows"]:
                print("L5_PSP_REV " + json.dumps(
                    {"window": w["window_start"], **r}, default=str),
                    flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
