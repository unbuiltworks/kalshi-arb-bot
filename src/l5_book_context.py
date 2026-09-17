"""L5_BOOK_CONTEXT_V1 -- the book around every L5 fill, recorded prospectively.

OWNER 2026-09-17, P0 research. READ-ONLY: no client, no submit path, no order
can originate here.

WHY THIS EXISTS. L5_CAUSAL_MECHANISM_V1 concluded that the BTC15m tape is best
explained by a passive quote surface whose side and run sequence are largely set
by incoming flow. That conclusion rests entirely on CONSEQUENCES -- clustering,
buying the falling side, a rising run hazard -- because the historical dataset
has no book at any instant and no unfilled orders at all. The surface itself was
never seen and is not claimed to have been.

This records, forward in time, the one thing that was missing: the public book
immediately before and after each L5 fill. With it, competing latent surfaces
stop being equally consistent with the evidence.

THE HARD RULE OF THIS MODULE. An inferred resting order is NEVER an observed L5
order. Nothing written here may be read as "L5 had an order at X". The strongest
statement any candidate earns is "a surface of this shape, given the actual
public book evolution, could have produced the fills that were observed".
Unfilled L5 orders remain UNOBSERVABLE_OR_UNRESOLVED, and the row schema says so
on every record.

THREE CLOCKS, NEVER MERGED. Collapsing them is how a 4-second publication lag
becomes a false claim about what the book looked like when he traded.

    MARKET_STATE_CLOCK    the venue's own timestamp on the book payload: when
                          that book state was true according to the venue.
    L5_EVENT_CLOCK        the venue's timestamp on L5's trade: when the fill
                          happened according to the venue.
    L5_OBSERVATION_CLOCK  our wall clock when WE saw it. Ours alone.

Every emitted row carries all three plus the derived lags, and no downstream
consumer is permitted to substitute one for another.

WHAT IS RECORDED PER EVENT. The nearest book snapshot at or before
event - {1, 5, 10, 30, 60}s, and the evolution after it out to +60s, for BOTH
outcome tokens of the market. Snapshots are kept exactly as served.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.l5_shadow import venues as V
from src.l5_chain_observer import observer as OB
from src import l5_residual_episode_clock as CLK
from analysis.l5.causal import order_id_forensics as OF

LABEL = "L5_BOOK_CONTEXT_V1"
LOOKBACK_S = (1, 5, 10, 30, 60)
FOLLOW_S = 60
FOLLOW_MARKS_S = (1, 5, 10, 30, 60)

#: EXTENSION 2026-09-17 (owner, P0). Four SYNCHRONIZED sources per market:
#:   1 full UP book   2 full DOWN book   3 public prints   4 L5 chain-proven
#: parent executions with reconstructed inventory.
#: Books are now recorded CONTINUOUSLY and in FULL, not only around L5
#: activity -- otherwise the tape only exists where he traded, which is the
#: selection bias that made the habitat measure unusable.
#: Both outcomes go in ONE row so they are synchronized by construction: two
#: separately-timestamped rows cannot be aligned after the fact to the
#: precision this needs.
FULL_BOOK_INTERVAL_S = 2.0
PRINTS_INTERVAL_S = 4.0
RING_S = 200.0                    # keep enough for 60s back + 60s forward
UNRESOLVED = "UNOBSERVABLE_OR_UNRESOLVED"

#: the candidate latent surfaces this data is meant to separate. Named here so
#: the recorder and the scorer cannot drift apart.
CANDIDATE_SURFACES = (
    "single_level",
    "multi_level_ladder",
    "touch_following",
    "fixed_distance_maker",
    "inventory_skewed_maker",
    "two_sided_symmetric",
    "two_sided_asymmetric",
)

SCHEMA_NOTE = (
    "Book states are PUBLIC and observed. L5's unfilled/resting/cancelled "
    "orders are " + UNRESOLVED + " and no field here asserts one. A candidate "
    "surface is scored on whether it COULD have generated the observed fills "
    "given the actual public evolution -- never presented as L5's orders."
)


def _top(levels: List[dict], side: str) -> Optional[Tuple[float, float]]:
    """(price, size) at the touch. The CLOB serves both sides worst-first, so
    the touch is the max bid and the min ask -- taking [0] would silently read
    the far end of the ladder."""
    best = None
    for lv in levels or []:
        try:
            p, s = float(lv.get("price")), float(lv.get("size"))
        except (TypeError, ValueError):
            continue
        if best is None or (p > best[0] if side == "bid" else p < best[0]):
            best = (p, s)
    return best


def summarize(book: Optional[dict]) -> Optional[dict]:
    """A compact, loss-free-enough view. The full ladder is kept too."""
    if not book:
        return None
    b, a = _top(book.get("bids"), "bid"), _top(book.get("asks"), "ask")
    return {
        "bid": b[0] if b else None, "bid_size": b[1] if b else None,
        "ask": a[0] if a else None, "ask_size": a[1] if a else None,
        "spread": (a[0] - b[0]) if (a and b) else None,
        "n_bid_levels": len(book.get("bids") or []),
        "n_ask_levels": len(book.get("asks") or []),
        "bid_depth": sum(float(x.get("size") or 0) for x in book.get("bids") or []),
        "ask_depth": sum(float(x.get("size") or 0) for x in book.get("asks") or []),
        "one_sided": not (book.get("bids") and book.get("asks")),
        "last_trade_price": book.get("last_trade_price"),
        "tick": book.get("tick"),
        "MARKET_STATE_CLOCK": book.get("source_ts"),
        "L5_OBSERVATION_CLOCK": book.get("observed_at"),
    }


def ladder(book: Optional[dict], side: str) -> List[List[float]]:
    """[[price, size], ...] exactly as served, no truncation."""
    out = []
    for lv in (book or {}).get("bids" if side == "bid" else "asks") or []:
        p, z = _f(lv.get("price")), _f(lv.get("size"))
        if p is not None:
            out.append([p, z or 0.0])
    return out


def size_at(book: Optional[dict], side: str, px: float,
            tol: float = 1e-9) -> Optional[float]:
    """Displayed size at one price level, or None when the book is unreadable.

    None and 0.0 ARE DIFFERENT and are kept apart: 0.0 means the level was
    served and empty, None means we could not see."""
    if not book:
        return None
    tot = 0.0
    for p, z in ladder(book, side):
        if abs(p - px) <= tol:
            tot += z
    return tot


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Ring:
    """Per-token snapshot history, indexed by MARKET_STATE_CLOCK where the
    venue served one and by our observation clock where it did not -- and the
    row says which was used, so a later reader is never guessing."""

    def __init__(self) -> None:
        self.snaps: collections.deque = collections.deque()

    def add(self, book: dict) -> None:
        t = book.get("source_ts")
        basis = "MARKET_STATE_CLOCK"
        if not t:
            t = book.get("observed_at")
            basis = "L5_OBSERVATION_CLOCK_fallback"
        self.snaps.append((float(t), basis, book))
        cut = time.time() - RING_S
        while self.snaps and self.snaps[0][0] < cut:
            self.snaps.popleft()

    def at_or_before(self, t: float) -> Optional[tuple]:
        best = None
        for ts, basis, b in self.snaps:
            if ts <= t and (best is None or ts > best[0]):
                best = (ts, basis, b)
        return best

    def after(self, t0: float, t1: float) -> List[tuple]:
        return [(ts, basis, b) for ts, basis, b in self.snaps if t0 < ts <= t1]


def reconstruct(ring: "Ring", *, side: str, px: float, event_t: float
                ) -> Dict[str, Any]:
    """Was the level L5 eventually executed at visibly present beforehand, and
    who moved -- the market into him, or him into the market?

    EVERY FIELD HERE IS ABOUT ANONYMOUS PUBLIC LIQUIDITY. That size at that
    price may be L5's order or anyone's. Nothing below asserts ownership; the
    row carries `identity: NOT_ESTABLISHED` and the fingerprint is a
    CANDIDATE."""
    marks: Dict[str, Any] = {}
    sizes: List[Tuple[float, Optional[float]]] = []
    for k in sorted(set(LOOKBACK_S)):
        hit = ring.at_or_before(event_t - k)
        b = hit[2] if hit else None
        sm = summarize(b)
        sz = size_at(b, side, px)
        sizes.append((-k, sz))
        touch = (sm or {}).get("bid" if side == "bid" else "ask")
        opp = (sm or {}).get("ask" if side == "bid" else "bid")
        mid = ((sm["bid"] + sm["ask"]) / 2.0
               if sm and sm.get("bid") is not None and sm.get("ask") is not None
               else None)
        marks["t_minus_%ds" % k] = {
            "snapshot_clock": hit[0] if hit else None,
            "clock_basis": hit[1] if hit else None,
            "size_at_level": sz,
            "level_present": (None if sz is None else sz > 0),
            "touch": touch, "opposite_touch": opp, "mid": mid,
            "ticks_from_touch": (None if (touch is None) else
                                 round(abs(touch - px) / 0.01, 2)),
            "ticks_from_mid": (None if mid is None else
                               round(abs(mid - px) / 0.01, 2)),
        }
    present = [(k, v) for k, v in marks.items() if v["level_present"]]
    first_seen = None
    for k in sorted(LOOKBACK_S, reverse=True):
        if marks["t_minus_%ds" % k]["level_present"]:
            first_seen = -k
            break
    # replenishment: size at the level RISING between consecutive marks
    seq = [z for _t, z in sorted(sizes) if z is not None]
    replen = sum(1 for a, b in zip(seq, seq[1:]) if b > a + 1e-9)
    drains = sum(1 for a, b in zip(seq, seq[1:]) if b < a - 1e-9)
    # who moved first: did the touch come TO the level, or the level to the touch?
    tk = [marks["t_minus_%ds" % k]["ticks_from_touch"] for k in
          sorted(LOOKBACK_S, reverse=True)]
    tk = [x for x in tk if x is not None]
    verdict = "INDETERMINATE"
    if first_seen is not None and len(tk) >= 2:
        if first_seen <= -30 and tk[0] > tk[-1] + 0.5:
            verdict = "MARKET_MOVED_INTO_THE_LEVEL"
        elif first_seen >= -5:
            verdict = "LEVEL_APPEARED_LATE"
        elif tk[0] <= tk[-1] + 0.5:
            verdict = "LEVEL_TRACKED_THE_TOUCH"
    return {
        "side": side, "price": px,
        "marks": marks,
        "level_first_seen_s_before": first_seen,
        "marks_with_level_present": len(present),
        "persistence_marks": len(present),
        "replenishments": replen, "drains": drains,
        "ticks_from_touch_over_time": tk,
        "who_moved": verdict,
        "identity": "NOT_ESTABLISHED",
        "labels": ("size at a price is ANONYMOUS public liquidity. It may be "
                   "L5's order or anyone's, and nothing here asserts which. "
                   "who_moved compares when the level first became visible "
                   "against how the touch approached it."),
    }


def outcome_tokens(market_key: str) -> Optional[dict]:
    """Tokens keyed by OUTCOME, taken from the venue's own ordering.

    `pm_window` already resolves Up/Down against the market's `outcomes` array
    rather than assuming index order; swapping them would price every Down
    intent against the Up book, so the mapping is carried through by name and
    never by position."""
    w = V.pm_window(market_key)
    if not w or not w.get("token_up") or not w.get("token_dn"):
        return None
    return {
        "market_key": market_key, "condition_id": w.get("condition_id"),
        "token_by_outcome": {"Up": str(w["token_up"]), "Down": str(w["token_dn"])},
        "tokens": [str(w["token_up"]), str(w["token_dn"])],
        "outcomes": w.get("outcomes"),
        "accepting_orders": w.get("accepting_orders"),
        "end_date": w.get("end_date"),
        # DECLARED venue terms, carried because the habitat score needs them
        # and because None means "the venue did not say", never zero
        "terms": {"fee_rate": w.get("fee_rate"),
                  "fee_taker_only": w.get("fee_taker_only"),
                  "fee_rebate_rate": w.get("fee_rebate_rate"),
                  "tick": w.get("tick"),
                  "order_min_size": w.get("order_min_size"),
                  "rewards_min_size": w.get("rewards_min_size"),
                  "rewards_max_spread": w.get("rewards_max_spread"),
                  "source": w.get("terms_source")},
    }


def run(out_dir: str, *, minutes: float, book_interval: float,
        activity_interval: float, cohorts: Tuple[str, ...]) -> int:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("l5_book_context_%s.jsonl"
                % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    fh = open(path, "a", encoding="utf-8")

    def emit(row: dict) -> None:
        row.setdefault("label", LABEL)
        row.setdefault("schema_note", SCHEMA_NOTE)
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()

    emit({"kind": "session_start", "cohorts": list(cohorts),
          "book_interval_s": book_interval,
          "activity_interval_s": activity_interval,
          "lookback_s": list(LOOKBACK_S), "follow_s": FOLLOW_S,
          "candidate_surfaces": list(CANDIDATE_SURFACES),
          "clocks": {"MARKET_STATE_CLOCK": "venue timestamp on the book payload",
                     "L5_EVENT_CLOCK": "venue timestamp on L5's trade",
                     "L5_OBSERVATION_CLOCK": "our wall clock"},
          "L5_OBSERVATION_CLOCK": time.time()})

    rings: Dict[str, Ring] = collections.defaultdict(Ring)
    meta: Dict[str, dict] = {}
    seen: set = set()
    pending: List[dict] = []
    clock = CLK.Clock("PMUS")
    # L5's inventory AS SEEN SINCE THIS PROCESS STARTED. It is not his position:
    # anything he held before we attached is invisible, and the label says so.
    inv: Dict[str, Dict[str, float]] = collections.defaultdict(
        lambda: {"Up": 0.0, "Down": 0.0})
    l5_fills: Dict[str, List[dict]] = collections.defaultdict(list)
    deadline = time.time() + minutes * 60.0
    last_activity = 0.0
    last_summary = 0.0
    last_full = 0.0
    last_prints = 0.0
    n_books = n_events = 0
    prints_seen: Dict[str, set] = collections.defaultdict(set)
    receipts: Dict[str, Any] = {}
    parents_by_tx: Dict[str, List[dict]] = {}

    while time.time() < deadline:
        loop_t = time.time()
        # --- refresh the open windows and their tokens
        for w in V.live_windows():
            if w["cohort"] not in cohorts:
                continue
            mk = w["market_key"]
            if mk not in meta:
                m = outcome_tokens(mk)
                if m:
                    m["cohort"] = w["cohort"]
                    m["window_start"] = w["window_start"]
                    m["window_end"] = w["window_end"]
                    meta[mk] = m
                    emit({"kind": "market_open", "market": m,
                          "L5_OBSERVATION_CLOCK": time.time()})

        # --- books
        for mk, m in list(meta.items()):
            if m["window_end"] < loop_t - 120:
                meta.pop(mk, None)
                continue
            for oc, tok in (m.get("token_by_outcome") or {}).items():
                b = V.pm_book(str(tok))
                if b:
                    rings[str(tok)].add(b)
                    n_books += 1
                    # a periodic PUBLIC book summary, so habitat can be scored
                    # on this venue at all. Without it the tape only exists
                    # where L5 happened to trade, which is precisely the
                    # selection bias a habitat measure must not have.
                    if loop_t - last_summary >= 5.0:
                        emit({"kind": "book_observation", "slug": mk,
                              "cohort": m.get("cohort"), "outcome": oc,
                              "token": str(tok),
                              "tte_s": round(m["window_end"] - loop_t, 1),
                              **(summarize(b) or {}),
                              "labels": "PUBLIC BOOK STATE ONLY -- never a "
                                        "fill, never realized, and no claim "
                                        "about L5's orders."})
            if loop_t - last_summary >= 5.0:
                last_summary = loop_t

            # ---- SOURCES 1 AND 2: both full books in ONE row, so the two
            # outcomes are synchronized by construction rather than aligned
            # after the fact
            if loop_t - last_full >= FULL_BOOK_INTERVAL_S:
                joint = {}
                for oc, tok in (m.get("token_by_outcome") or {}).items():
                    hit = rings[str(tok)].at_or_before(time.time())
                    b = hit[2] if hit else None
                    joint[oc] = {
                        "token": str(tok),
                        "MARKET_STATE_CLOCK": (b or {}).get("source_ts"),
                        "L5_OBSERVATION_CLOCK": (b or {}).get("observed_at"),
                        "bids": ladder(b, "bid"), "asks": ladder(b, "ask"),
                        "summary": summarize(b)}
                if joint:
                    emit({"kind": "book_full_joint", "slug": mk,
                          "cohort": m.get("cohort"),
                          "tte_s": round(m["window_end"] - loop_t, 1),
                          "outcomes": joint,
                          "labels": "FULL public ladders, both outcomes, one "
                                    "row. Never a fill and no claim about "
                                    "whose orders these are."})

        if loop_t - last_full >= FULL_BOOK_INTERVAL_S:
            last_full = loop_t

        # ---- SOURCE 3: public prints for the whole market, not just L5's
        if loop_t - last_prints >= PRINTS_INTERVAL_S:
            last_prints = loop_t
            for mk, m in list(meta.items()):
                cid = m.get("condition_id")
                if not cid:
                    continue
                tr = V.pm_trades(cid) or []
                fresh = []
                for x in tr:
                    k = "%s|%s|%s|%s" % (x["t"], x["price"], x["size"],
                                         x.get("asset"))
                    if k in prints_seen[mk]:
                        continue
                    prints_seen[mk].add(k)
                    fresh.append({"t": float(x["t"]), "price": str(x["price"]),
                                  "size": str(x["size"]),
                                  "side": x.get("side"),
                                  "asset": x.get("asset")})
                if fresh:
                    emit({"kind": "print_tape", "slug": mk,
                          "condition_id": cid, "n": len(fresh),
                          "prints": fresh,
                          "L5_OBSERVATION_CLOCK": time.time(),
                          "labels": "EVERYONE's prints in this market, not "
                                    "L5's. A print is a trade, never a book "
                                    "movement."})

        # --- L5 activity
        if loop_t - last_activity >= activity_interval:
            last_activity = loop_t
            obs_clock = time.time()
            fills = OB.activity_fills(limit=200)
            if fills is None:
                emit({"kind": "activity_read_failed",
                      "L5_OBSERVATION_CLOCK": obs_clock})
            else:
                for tx, e in fills.items():
                    for i, f in enumerate(e["fills"]):
                        key = (tx, i, f.get("slug"), f.get("price"),
                               f.get("size"), f.get("outcome"))
                        if key in seen:
                            continue
                        slug = str(f.get("slug") or "")
                        if slug not in meta:
                            continue          # not a window we are recording
                        seen.add(key)
                        n_events += 1
                        pending.append({
                            "tx": tx, "fill_index": i, "slug": slug,
                            "price": f.get("price"), "size": f.get("size"),
                            "outcome": f.get("outcome"), "side": f.get("side"),
                            "L5_EVENT_CLOCK": float(f.get("ts") or 0),
                            "L5_OBSERVATION_CLOCK": obs_clock,
                        })
                        oc = "Up" if str(f.get("outcome")) == "Up" else "Down"
                        try:
                            fq = float(f.get("size") or 0)
                            fp = float(f.get("price") or 0)
                        except (TypeError, ValueError):
                            fq = fp = 0.0
                        before = dict(inv[slug])
                        inv[slug][oc] += fq
                        l5_fills[slug].append(
                            {"t": float(f.get("ts") or 0), "outcome": oc,
                             "px": fp, "qty": fq})
                        # a residual episode opens when session-local inventory
                        # leaves flat on one side only
                        if (before["Up"] == 0) and (before["Down"] == 0):
                            run = sum(1 for x in l5_fills[slug]
                                      if x["outcome"] == oc)
                            ep = clock.open_episode(
                                "%s|%s|%s" % (slug, oc, f.get("ts")),
                                symbol=slug, t=float(f.get("ts") or 0),
                                side=oc, px=fp, qty=fq,
                                tte_s=(meta[slug]["window_end"]
                                       - float(f.get("ts") or 0)),
                                run_position=run)
                            emit({"kind": "residual_episode_opened",
                                  "label": CLK.LABEL, "episode": ep.key,
                                  "slug": slug, "marks_s": list(CLK.MARKS_S),
                                  "L5_EVENT_CLOCK": float(f.get("ts") or 0),
                                  "L5_OBSERVATION_CLOCK": obs_clock})
                        # ---- SOURCE 4: the CHAIN-PROVEN parent order behind
                        # this public fill. One receipt answers for every fill
                        # in the transaction, so it is fetched once and cached.
                        if tx not in receipts:
                            rc = OF.rpc("eth_getTransactionReceipt", [tx])
                            receipts[tx] = rc if rc is not None else False
                            parents_by_tx[tx] = (OF.l5_orders_in(rc)
                                                 if rc else [])
                            emit({"kind": "l5_parent_execution", "tx": tx,
                                  "slug": slug,
                                  "receipt_available": bool(rc),
                                  "parents": [{
                                      "order_hash": x["order_hash"],
                                      "maker": x["maker"], "taker": x["taker"],
                                      "maker_asset_id": str(x["maker_asset_id"]),
                                      "taker_asset_id": str(x["taker_asset_id"]),
                                      "maker_amount_filled": str(x["maker_amount_filled"]),
                                      "taker_amount_filled": str(x["taker_amount_filled"]),
                                      "fee": str(x["fee"])}
                                      for x in parents_by_tx[tx]],
                                  "inventory_after_session_local": dict(inv[slug]),
                                  "L5_EVENT_CLOCK": float(f.get("ts") or 0),
                                  "L5_OBSERVATION_CLOCK": obs_clock,
                                  "labels": ("CHAIN-PROVEN. order_hash is the "
                                             "parent order; fee>0 is the "
                                             "fee-bearing label and NOT a "
                                             "maker/taker claim. Inventory is "
                                             "SESSION-LOCAL.")})
                        emit({"kind": "l5_event_seen",
                              "tx": tx, "fill_index": i, "slug": slug,
                              "L5_EVENT_CLOCK": float(f.get("ts") or 0),
                              "L5_OBSERVATION_CLOCK": obs_clock,
                              "publication_lag_s": round(
                                  obs_clock - float(f.get("ts") or 0), 3),
                              "note": "recorded immediately; the joined context "
                                      "follows once +%ds of book evolution "
                                      "exists" % FOLLOW_S})

        # --- preregistered residual-episode marks that have come due
        for ep, mark in list(clock.due_marks(time.time())):
            slug = ep.symbol
            m = meta.get(slug) or {}
            fl = l5_fills[slug]
            opp = [x for x in fl
                   if x["outcome"] != ep.first_side and x["t"] > ep.opened_t]
            same = [x for x in fl if x["outcome"] == ep.first_side]
            # is a maker order at the first fill's price still legal NOW?
            valid = None
            tok = (m.get("token_by_outcome") or {}).get(ep.first_side)
            if tok:
                hit = rings[str(tok)].at_or_before(time.time())
                if hit:
                    sm = summarize(hit[2])
                    valid = {"still_restable": (sm["ask"] is not None
                                                and ep.first_px < sm["ask"]),
                             "bid": sm["bid"], "ask": sm["ask"],
                             "one_sided": sm["one_sided"],
                             "clock_basis": hit[1]}
            comp_px = opp[0]["px"] if opp else None
            emit(ep.record(mark, {
                "evidenced_complement": bool(opp),
                "complement_dt_s": (round(opp[0]["t"] - ep.opened_t, 3)
                                    if opp else None),
                "quote_restable": (valid or {}).get("still_restable"),
                "book_reached_quote": None,
                "trade_flow_through_quote": bool(opp) or len(same) > 1,
                CLK.STRICT: bool(opp),
                "inventory": {**inv[slug],
                              "basis": "SESSION-LOCAL: only fills observed "
                                       "since this process attached. NOT L5's "
                                       "position."},
                "residual": inv[slug]["Up"] - inv[slug]["Down"],
                "quote_validity": valid,
                "pair_economics": CLK.pair_economics(
                    "PMUS", buy_px=ep.first_px, complement_buy_px=comp_px),
            }) | {"slug": slug,
                  "L5_OBSERVATION_CLOCK": time.time(),
                  "l5_resting_orders": UNRESOLVED})
            if mark == CLK.MARKS_S[-1]:
                clock.retire(ep.key)

        # --- events whose forward window has now elapsed
        still: List[dict] = []
        for ev in pending:
            et = ev["L5_EVENT_CLOCK"]
            if time.time() < et + FOLLOW_S:
                still.append(ev)
                continue
            m = meta.get(ev["slug"])
            ctx: Dict[str, Any] = {}
            for tok in (m or {}).get("tokens", []):
                r = rings[str(tok)]
                pre = {}
                for k in LOOKBACK_S:
                    hit = r.at_or_before(et - k)
                    pre["t_minus_%ds" % k] = (
                        None if not hit else
                        {"snapshot_clock": hit[0], "clock_basis": hit[1],
                         "age_vs_target_s": round((et - k) - hit[0], 3),
                         "summary": summarize(hit[2]),
                         "bids": hit[2].get("bids"), "asks": hit[2].get("asks")})
                post = [{"snapshot_clock": ts, "clock_basis": basis,
                         "dt_from_event_s": round(ts - et, 3),
                         "summary": summarize(b)}
                        for ts, basis, b in r.after(et, et + FOLLOW_S)]
                ctx[str(tok)] = {"preceding": pre, "subsequent": post,
                                 "n_subsequent": len(post)}
            # the reconstruction the owner asked for, on BOTH outcomes, at
            # the price L5 actually executed
            recon = {}
            try:
                same = (m.get("token_by_outcome") or {}).get(ev["outcome"]) \
                    if ev.get("outcome") else None
                other_oc = ("Down" if ev.get("outcome") == "Up" else "Up")
                oth = (m.get("token_by_outcome") or {}).get(other_oc)
                if same:
                    recon["same_outcome"] = reconstruct(
                        rings[str(same)], side="bid",
                        px=float(ev.get("price") or 0), event_t=et)
                if oth:
                    recon["opposite_outcome_complement"] = reconstruct(
                        rings[str(oth)], side="bid",
                        px=round(1.0 - float(ev.get("price") or 0), 4),
                        event_t=et)
                recon["forward_marks"] = {}
                for k in FOLLOW_MARKS_S:
                    fwd = {}
                    for oc2, tok2 in (m.get("token_by_outcome") or {}).items():
                        hit2 = rings[str(tok2)].at_or_before(et + k)
                        fwd[oc2] = summarize(hit2[2]) if hit2 else None
                    recon["forward_marks"]["t_plus_%ds" % k] = fwd
            except Exception as exc:                          # noqa: BLE001
                recon = {"reconstruction_failed": str(exc)}
            emit({"kind": "l5_event_context", **ev,
                  "market": m, "book_context_by_token": ctx,
                  "reconstruction": recon,
                  "l5_resting_orders": UNRESOLVED,
                  "labels": ("The book states are OBSERVED and public. Whether "
                             "L5 had a resting order anywhere in them is "
                             + UNRESOLVED + " and is never asserted here.")})
        pending = still

        slept = book_interval - (time.time() - loop_t)
        if slept > 0:
            time.sleep(slept)

    emit({"kind": "session_end", "books_recorded": n_books,
          "l5_events_recorded": n_events,
          "residual_episodes_opened": clock.n_opened,
          "residual_episodes_still_open": len(clock.open),
          "events_pending_at_close": len(pending),
          "L5_OBSERVATION_CLOCK": time.time()})
    fh.close()
    print("L5_BOOK_CONTEXT wrote %s (books=%d events=%d)"
          % (path, n_books, n_events), flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--book-interval", type=float, default=1.0)
    ap.add_argument("--activity-interval", type=float, default=3.0)
    ap.add_argument("--out-dir", default="data/l5_book_context")
    ap.add_argument("--cohorts", default="BTC5m,BTC15m")
    a = ap.parse_args()
    return run(a.out_dir, minutes=a.minutes, book_interval=a.book_interval,
               activity_interval=a.activity_interval,
               cohorts=tuple(x.strip() for x in a.cohorts.split(",") if x.strip()))


if __name__ == "__main__":
    raise SystemExit(main())
