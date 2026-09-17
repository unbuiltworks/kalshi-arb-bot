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

LABEL = "L5_BOOK_CONTEXT_V1"
LOOKBACK_S = (1, 5, 10, 30, 60)
FOLLOW_S = 60
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
    n_books = n_events = 0

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
            emit({"kind": "l5_event_context", **ev,
                  "market": m, "book_context_by_token": ctx,
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
