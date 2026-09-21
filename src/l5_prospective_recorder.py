"""Prospective L5 execution recorder: observe what we have been reconstructing.

WHY THIS EXISTS. Every execution claim about L5 so far has been rebuilt after
the fact from sources that cannot answer the question. The venue `/trades`
endpoint caps at offset 10,000 with no working time filter, so history cannot be
labelled maker/taker. The international book collector fetches the FULL ladder
from `clob/book?token_id=` -- 40+ levels a side -- and throws all but the touch
away before writing. And nothing polls L5's own address at all. So the chain

    PRE-FILL BOOK -> L5 ACTION -> MATCH TYPE -> POST-FILL BOOK

has never once been observed directly. It is cheap to observe going forward, and
L5 is still trading today.

WHAT IT RECORDS, AND WHAT IT NEVER MIXES. Raw source records are written to
their own streams exactly as the venue returned them. Derived event records are
written separately and carry, for every joined source, its provenance and its
TIMING UNCERTAINTY -- how old the snapshot was and what resolution it had. The
data-api timestamp is whole SECONDS while book snapshots carry sub-second fetch
times, so a join is never presented as exact when it is not.

WHAT IT IS NOT. Observation only. Public GETs. No credential, no order
transport, no signer, no arming, and no strategy. Polymarket International
remains non-executable. Nothing here is submitted, filled, settled or realized.

THE PRIMARY PROSPECTIVE MEASUREMENT is one ratio:

    arriving executable flow / resting depth ahead of L5

Its point is to be TESTED, not fitted. No threshold is chosen here and no
selector is built from it.
"""
from __future__ import annotations

import bisect
import collections
import json
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "l5_prospective_event/v1"
DATA_API = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
L5_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"

# The data-api reports trade time in whole seconds. Every join against a book
# snapshot therefore carries at least this much uncertainty, and it is recorded
# rather than rounded away.
TRADE_TS_RESOLUTION_S = 1.0


# ------------------------------------------------------------------ raw shapes
def ladder(side: Sequence[Mapping[str, Any]]) -> List[Tuple[float, float]]:
    """A book side as (price, size) pairs, sorted ascending by price."""
    out = []
    for lv in side or []:
        try:
            out.append((float(lv["price"]), float(lv["size"])))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort()
    return out


def touch(bids, asks) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """(best_bid, bid_size, best_ask, ask_size). None where the side is empty --
    an empty side is unmeasured, never a zero price."""
    b = bids[-1] if bids else (None, None)
    a = asks[0] if asks else (None, None)
    return b[0], b[1], a[0], a[1]


def depth_at(levels: Sequence[Tuple[float, float]], price: float) -> float:
    """Size resting AT a price level. 0.0 means the level is absent from a book
    we did read -- distinct from None, which we never return here."""
    return sum(s for p, s in levels if abs(p - price) < 1e-9)


def depth_ahead(bids: Sequence[Tuple[float, float]], price: float) -> float:
    """Resting size that would be filled BEFORE a new buy at `price`.

    Price priority only: everything bid strictly higher, plus everything already
    queued at the same price. It is an UPPER bound on what is truly ahead, since
    part of the same-price queue may have arrived after L5 did, and we cannot
    see arrival order in anonymous depth.
    """
    return sum(s for p, s in bids if p > price - 1e-9)


class BookRing:
    """Recent full-ladder snapshots for one token, searchable by time."""

    def __init__(self, cap: int = 240) -> None:
        self.t: List[float] = []
        self.rows: List[dict] = []
        self.cap = cap

    def add(self, ts: float, row: dict) -> None:
        self.t.append(ts)
        self.rows.append(row)
        if len(self.t) > self.cap:
            del self.t[0]
            del self.rows[0]

    def before(self, ts: float) -> Optional[dict]:
        """The newest snapshot STRICTLY BEFORE ts. A snapshot taken at or after
        the fill already contains the fill's own effect."""
        i = bisect.bisect_left(self.t, ts) - 1
        return self.rows[i] if i >= 0 else None

    def after(self, ts: float) -> Optional[dict]:
        i = bisect.bisect_right(self.t, ts)
        return self.rows[i] if i < len(self.rows) else None

    def window(self, t0: float, t1: float) -> List[dict]:
        """Every snapshot in (t0, t1], oldest first.

        A single pre-fill book answers "how much was ahead of him once". It
        cannot answer whether that depth was TRADED away or CANCELLED, which is
        the whole question -- so the intermediate snapshots are kept as raw
        rows, not collapsed into a scalar.
        """
        lo = bisect.bisect_right(self.t, t0)
        hi = bisect.bisect_right(self.t, t1)
        return self.rows[lo:hi]


# ------------------------------------------------------- sequential L5 state
class MarketState:
    """L5's running per-market state, built only from fills already seen."""

    def __init__(self) -> None:
        # OBSERVED IN PRODUCTION: the venue served a fill with
        # outcomeIndex=999, and a fixed {0: .., 1: ..} dict raised KeyError
        # inside the recorder thread. The supervisor restarted it, which reset
        # the seed and lost the book ring -- so one odd row from the venue cost
        # minutes of coverage. A recorder must never crash on a value a venue
        # is free to send.
        self.inv: Dict[int, float] = collections.defaultdict(float)
        self.inv[0] = 0.0
        self.inv[1] = 0.0
        self.prev_outcome: Optional[int] = None
        self.run: int = 0
        self.last_t: Optional[int] = None
        self.last_opposite_t: Optional[int] = None
        self.n: int = 0

    def classify(self, outcome_index: int) -> str:
        if self.prev_outcome is None:
            return "OPEN"
        return "EXTEND" if outcome_index == self.prev_outcome else "OPPOSITE"

    def snapshot(self, outcome_index: int, ts: int) -> dict:
        """State BEFORE applying this fill."""
        return {"action": self.classify(outcome_index),
                "run_before": self.run,
                "inv_0_before": self.inv.get(0, 0.0),
                "inv_1_before": self.inv.get(1, 0.0),
                "residual_before": self.inv.get(0, 0.0) - self.inv.get(1, 0.0),
                "fills_before": self.n,
                "secs_since_prev_fill": (None if self.last_t is None else ts - self.last_t),
                "secs_since_prev_opposite":
                    (None if self.last_opposite_t is None else ts - self.last_opposite_t)}

    def apply(self, outcome_index: int, size: float, side: str, ts: int) -> None:
        act = self.classify(outcome_index)
        if act == "OPPOSITE":
            self.last_opposite_t = ts
            self.run = 1
        elif act == "EXTEND":
            self.run += 1
        else:
            self.run = 1
        self.inv[outcome_index] = (self.inv.get(outcome_index, 0.0)
                                   + (size if side == "BUY" else -size))
        self.prev_outcome = outcome_index
        self.last_t = ts
        self.n += 1


# ------------------------------------------------------------- the event join
def provenance(name: str, fetched_at: Optional[float], event_t: float,
               resolution_s: float, note: str = "") -> dict:
    """Where a joined field came from and how uncertain its timing is."""
    return {"source": name, "fetched_at": fetched_at,
            "age_s": (None if fetched_at is None else round(event_t - fetched_at, 3)),
            "resolution_s": resolution_s, "note": note}


def bid_consuming(p: Mapping[str, Any], outcome_index: int, price: float,
                  tol: float = 1e-9) -> bool:
    """Does print `p` consume resting BUY depth at `price` on `outcome_index`?

    TWO CONVENTIONS, ONE BOOK. Selling outcome A at p and buying outcome B at
    1-p are the same event against the same resting order, and the tape reports
    whichever leg the taker took. Counting only the first convention silently
    halves the numerator of the reconciliation, which would manufacture
    unexplained queue disappearance out of a bookkeeping choice.
    """
    try:
        oi = int(p.get("outcomeIndex", -1))
        px = float(p.get("price"))
    except (TypeError, ValueError):
        return False
    side = str(p.get("side") or "").upper()
    if oi == outcome_index and side == "SELL":
        return px >= price - tol
    if oi == (1 - outcome_index) and side == "BUY":
        return (1.0 - px) >= price - tol
    return False


def ahead_trail(rows: Sequence[Mapping[str, Any]], outcome_index: int,
                price: float) -> List[dict]:
    """Depth ahead / depth at `price` at each raw snapshot, in order.

    The ladders are the BUY side for `outcome_index`. Under the mirror
    convention a snapshot of the complement token exposes the same queue as its
    ask ladder, so rows carrying `_mirror: True` are read that way.
    """
    out = []
    for r in rows:
        if r.get("_mirror"):
            lv = [(round(1.0 - p, 6), s) for p, s in ladder(r.get("asks"))]
            lv.sort()
        else:
            lv = ladder(r.get("bids"))
        out.append({"t": r.get("_t"),
                    "depth_ahead": depth_ahead(lv, price),
                    "depth_at": depth_at(lv, price),
                    "mirror": bool(r.get("_mirror"))})
    return out


def build_event(fill: Mapping[str, Any], *, is_taker: Optional[bool],
                state: MarketState, pre: Optional[dict], post: Optional[dict],
                prints: Sequence[Mapping[str, Any]],
                settle_epoch: Optional[float],
                chain: Optional[Mapping[str, Any]] = None,
                label_source: str = "",
                trail: Optional[Sequence[Mapping[str, Any]]] = None) -> dict:
    """One immutable observation of an L5 fill in its book context.

    `pre` must be strictly BEFORE the fill and `post` strictly after; the caller
    guarantees that via BookRing. Every derived quantity names the raw snapshot
    it came from, and any quantity we could not measure is null WITH A REASON --
    never a zero, because a zero depth and an unread book are different facts.
    """
    ts = int(fill["timestamp"])
    oi = int(fill["outcomeIndex"])
    px = float(fill["price"])
    qty = float(fill["size"])
    st = state.snapshot(oi, ts)

    pb = ladder((pre or {}).get("bids")) if pre else []
    pa = ladder((pre or {}).get("asks")) if pre else []
    bb, bbs, ba, bas = touch(pb, pa)
    ob = ladder((post or {}).get("bids")) if post else []
    oa = ladder((post or {}).get("asks")) if post else []

    # Arriving traded volume strictly between the pre-fill snapshot and the
    # fill. EXECUTABLE flow only: a resting BUY at px on this outcome can only
    # be filled by someone selling this outcome at or below px. Counting the
    # whole market's prints -- including the other outcome, and including
    # trades above px -- would inflate the numerator with volume that could
    # never have reached him.
    t0 = (pre or {}).get("_t")
    window = [p for p in prints
              if t0 is not None and t0 < float(p.get("t", 0)) <= ts]
    arriving = [p for p in window
                if int(p.get("outcomeIndex", -1)) == oi
                and float(p.get("price", 1e9)) <= px + 1e-9]
    arriving_qty = sum(float(p.get("size") or 0.0) for p in arriving)
    all_market_qty = sum(float(p.get("size") or 0.0) for p in window)
    ahead = depth_ahead(pb, px) if pre else None

    # ---- queue trail: what the public book did over a LOOKBACK ending at the
    # pre-fill snapshot.
    #
    # THIS WAS WRONG ONCE AND THE SYMPTOM WAS SILENT. The trail was first taken
    # as the snapshots BETWEEN the pre-book and the fill -- but `pre` is by
    # definition the NEWEST snapshot before the fill, so that window is empty by
    # construction. Every event came back with `trail: 0`, and the whole
    # cancellation/disappearance measurement was degenerate while looking like
    # a fact about quiet markets. The trail must END at the pre-book and reach
    # BACKWARDS from it.
    tr = [dict(r) for r in (trail or [])]
    tr_scalars = ahead_trail(tr, oi, px) if (pre and tr) else []
    # `first look` = earliest snapshot in the lookback; `last look` = the
    # pre-fill book itself, which is also the trail's final element.
    ahead_first = (tr_scalars[0]["depth_ahead"] if tr_scalars else ahead)
    first_t = (tr_scalars[0]["t"] if tr_scalars else t0)
    last_t = t0
    ahead_last = ahead
    at_last = (depth_at(pb, px) if pre else None)
    # Full ladders are kept for the snapshots nearest the fill, where the
    # accounting is decided. The rest keep their scalars here and their raw
    # ladder in raw_books.jsonl, joinable on (token, _t).
    tr_raw = tr[-TRAIL_RAW_SNAPSHOTS:]

    # Publicly observed volume that consumed the queue AT OR AHEAD of his
    # price, over exactly the interval the trail covers.
    # The reconciliation window starts at the FIRST look, so the volume that
    # explains the observed drop is measured over the same interval the drop is.
    rwin = [p for p in prints
            if first_t is not None and first_t < float(p.get("t", 0)) <= ts]
    consumed = [p for p in rwin if bid_consuming(p, oi, px)]
    consumed_qty = sum(float(p.get("size") or 0.0) for p in consumed)
    comp = [p for p in rwin if int(p.get("outcomeIndex", -1)) == (1 - oi)]
    comp_qty = sum(float(p.get("size") or 0.0) for p in comp)

    disappeared = (None if (ahead_first is None or ahead_last is None)
                   else round(ahead_first - ahead_last, 6))
    unexplained_drop = (None if disappeared is None
                        else round(disappeared - consumed_qty, 6))

    # Price movement across the fill, in ticks, from the two raw ladders.
    def _mv(a, b):
        return None if (a is None or b is None) else round(b - a, 6)
    ob_b, ob_bs, ob_a, ob_as = touch(ob, oa)

    return {
        "schema": SCHEMA,
        "observed_at": time.time(),
        # identity
        "conditionId": fill.get("conditionId"), "asset": str(fill.get("asset") or ""),
        "slug": fill.get("slug"), "eventSlug": fill.get("eventSlug"),
        "outcome": fill.get("outcome"), "outcome_index": oi,
        "transactionHash": fill.get("transactionHash"),
        # the action
        "timestamp": ts, "timestamp_resolution_s": TRADE_TS_RESOLUTION_S,
        "price": px, "size": qty, "side": fill.get("side"),
        "is_taker": is_taker,
        "maker_taker_available": is_taker is not None,
        # sequential state, from earlier fills only
        **st,
        "secs_to_settlement": (None if settle_epoch is None else settle_epoch - ts),
        # book context
        "pre_fill_book": ({"bids": pb, "asks": pa, "t": t0} if pre else None),
        "pre_fill_book_reason": (None if pre else "no snapshot strictly before the fill"),
        "post_fill_book": ({"bids": ob, "asks": oa, "t": (post or {}).get("_t")}
                           if post else None),
        "post_fill_book_reason": (None if post else "no snapshot after the fill yet"),
        "touch_bid": bb, "touch_bid_size": bbs, "touch_ask": ba, "touch_ask_size": bas,
        "depth_at_fill_price": (depth_at(pb, px) if pre else None),
        "depth_ahead_of_fill_price": ahead,
        "rested_at_touch": (None if (pre is None or bb is None) else abs(px - bb) < 1e-9),
        "ticks_behind_touch": (None if (pre is None or bb is None)
                               else round((bb - px) / 0.01)),

        # ---- H-QUEUE-MISMEASURED instrumentation ------------------------
        # Every one of these is computed independently from a raw row that is
        # preserved in the same record, so the derivation can be re-audited
        # without re-fetching anything.

        # (3) cancellations / depth disappearance before the fill
        "queue_trail": tr_raw,
        "queue_trail_raw_kept": len(tr_raw),
        "queue_trail_raw_note":
            "full ladders for the snapshots nearest the fill; earlier ones keep "
            "their scalars here and their raw ladder in raw_books.jsonl, "
            "joinable on (token, _t)",
        "queue_trail_scalars": tr_scalars,
        "queue_trail_snapshots": len(tr),
        "queue_trail_t0": first_t,
        "queue_trail_t1": last_t,
        "queue_trail_lookback_s": TRAIL_LOOKBACK_S,
        "depth_ahead_at_first_look": ahead_first,
        "depth_ahead_at_pre": ahead,
        "depth_ahead_at_last_look": ahead_last,
        "depth_at_price_at_last_look": at_last,
        "depth_ahead_disappeared": disappeared,
        "queue_consuming_volume": (consumed_qty if pre else None),
        "queue_consuming_prints": len(consumed),
        "depth_drop_not_explained_by_trades": unexplained_drop,
        "depth_drop_definition":
            "pre-snapshot depth ahead minus last-look depth ahead, minus "
            "publicly observed volume consuming at/ahead of his price over the "
            "SAME interval. Positive => depth left the book without a public "
            "trade (cancellation, or a trade the tape had not published).",

        # (5) price movement through the fill
        "post_touch_bid": ob_b, "post_touch_bid_size": ob_bs,
        "post_touch_ask": ob_a, "post_touch_ask_size": ob_as,
        "bid_move_through_fill": _mv(bb, ob_b),
        "ask_move_through_fill": _mv(ba, ob_a),
        "mid_move_through_fill": (
            None if None in (bb, ba, ob_b, ob_a)
            else round(((ob_b + ob_a) - (bb + ba)) / 2.0, 6)),

        # (6) snapshot age at the fill, and (7) the timing uncertainty that
        # bounds every one of these quantities
        "pre_snapshot_age_s": (None if t0 is None else round(float(ts) - float(t0), 3)),
        "last_look_age_s": (None if last_t is None else round(float(ts) - float(last_t), 3)),
        "unobserved_interval_s": (None if last_t is None
                                  else round(float(ts) - float(last_t), 3)),
        "fill_time_lo": float(ts),
        "fill_time_hi": float(ts) + TRADE_TS_RESOLUTION_S,
        "fill_time_uncertainty_s": TRADE_TS_RESOLUTION_S,
        "fill_time_note":
            "the venue timestamps trades to whole seconds, so the true fill "
            "instant lies in [lo, hi). Any depth-ahead residual smaller than "
            "what the market trades in one second is inside the noise.",

        # (8) complement-side arrivals
        "complement_arrival_volume": (comp_qty if pre else None),
        "complement_arrival_prints": len(comp),
        "complement_outcome_index": 1 - oi,

        # (10) atomic multi-fill: the grouping key. Whether this fill shares a
        # transaction with others is a cross-event fact, joined offline.
        "atomic_group_key": fill.get("transactionHash"),
        "atomic_group_size": None,
        "atomic_group_reason": "joined across events offline, not at capture",
        # flow
        "recent_prints": [dict(p) for p in arriving][:50],
        # The WHOLE window, unfiltered and time-stamped. `recent_prints` is a
        # filtered view for the primary ratio; the reconciliation has to split
        # the window at the last book look and to see complement-side legs, so
        # it cannot be built from a pre-filtered list.
        "window_prints": [dict(p) for p in rwin][:WINDOW_PRINTS_CAP],
        "window_prints_n_total": len(rwin),
        # THE RECONCILIATION RE-DERIVES ITS TERMS FROM THIS LIST. If it is
        # truncated, that re-derivation undercounts the consuming volume and
        # would report UNEXPLAINED for a busy market purely because we capped a
        # list. The flag makes the truncation a REFUSAL rather than a finding.
        "window_prints_truncated": len(rwin) > WINDOW_PRINTS_CAP,
        "window_prints_t0": first_t,
        "window_prints_t1": float(ts),
        "flow_window_t0": t0,
        "flow_window_note":
            "window_prints spans the RECONCILIATION window (first look -> "
            "fill). The H-FLOW numerator uses the narrower flow window "
            "(pre-snapshot -> fill) and is reported separately.",
        "arriving_volume_since_pre_snapshot": (arriving_qty if pre else None),
        "arriving_volume_definition":
            "same outcome, price <= L5 price: flow that could have reached him",
        "arriving_volume_whole_market": (all_market_qty if pre else None),
        # THE PRIMARY PROSPECTIVE MEASUREMENT
        "arriving_flow_over_depth_ahead":
            (None if (ahead is None or ahead <= 0) else arriving_qty / ahead),
        "arriving_flow_over_depth_ahead_reason":
            (None if (ahead is not None and ahead > 0)
             else ("no pre-fill book" if ahead is None
                   else "zero depth ahead: ratio undefined, not infinite")),
        # chain
        "chain": (dict(chain) if chain else None),
        "chain_reason": (None if chain else "receipt not joined at capture time"),
        # provenance for every joined source
        "provenance": {
            "fill": provenance("data-api/trades", float(ts), float(ts),
                               TRADE_TS_RESOLUTION_S,
                               "venue trade record; timestamp is whole seconds"),
            "maker_taker": provenance(label_source or "data-api/trades?takerOnly",
                                      float(ts), float(ts), TRADE_TS_RESOLUTION_S,
                                      "membership of the takerOnly subset"),
            "pre_fill_book": provenance("clob/book?token_id", t0, float(ts), 0.001,
                                        "full ladder, strictly before the fill"),
            "post_fill_book": provenance("clob/book?token_id",
                                         (post or {}).get("_t"), float(ts), 0.001,
                                         "full ladder, first snapshot after the fill"),
            "prints": provenance("data-api/trades (all traders)", t0, float(ts),
                                 TRADE_TS_RESOLUTION_S,
                                 "arriving volume between pre-snapshot and fill"),
        },
        "labels": ("OBSERVATION ONLY. Public GETs. No credential, no order path. "
                   "Nothing here is submitted, filled, settled or realized. "
                   "depth_ahead is an UPPER bound: anonymous depth hides arrival "
                   "order, so part of the same-price queue may post-date L5."),
    }


# ------------------------------------------------------------------ the loop
import os                                                        # noqa: E402
import ssl                                                       # noqa: E402
import threading                                                 # noqa: E402
from concurrent.futures import ThreadPoolExecutor                 # noqa: E402
import urllib.parse                                              # noqa: E402
import urllib.request                                            # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"
CTX = ssl.create_default_context(cafile="/root/.ccr/ca-bundle.crt") \
    if os.path.exists("/root/.ccr/ca-bundle.crt") else ssl.create_default_context()
# Cadences are env-tunable because this now shares a container -- and the
# venue's rate limits -- with the international book collector. If book_errors
# climb after deploy, these can be backed off without a code change or a
# rebuild. The 3 s book sweep is the one that matters: it is what makes the
# pre-fill snapshot NEAR the fill, and loosening it widens
# `unobserved_interval_s` on every reconciliation.
BOOK_CADENCE_S = float(os.getenv("L5_PROSPECTIVE_BOOK_CAD", "3"))
FILL_CADENCE_S = float(os.getenv("L5_PROSPECTIVE_FILL_CAD", "3"))
PRINTS_CADENCE_S = float(os.getenv("L5_PROSPECTIVE_PRINTS_CAD", "10"))
DISCOVER_CADENCE_S = float(os.getenv("L5_PROSPECTIVE_DISCOVER_CAD", "15"))
POST_FILL_GRACE_S = float(os.getenv("L5_PROSPECTIVE_GRACE_S", "8"))


def _get(url: str, timeout: float = 20.0):
    r = urllib.request.Request(url, headers={"User-Agent": "l5-research/1.0",
                                             "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as h:
        return json.loads(h.read())


# How far back the queue trail reaches from the pre-fill book, and how many of
# its snapshots keep a full raw ladder inside the event.
TRAIL_LOOKBACK_S = 60.0
TRAIL_RAW_SNAPSHOTS = 6
# How many raw prints of the reconciliation window travel inside the event.
WINDOW_PRINTS_CAP = 1000

RAW_FILL_KEYS_CAP = 200_000
RAW_PRINT_KEYS_CAP = 400_000
BOOK_KEEPALIVE_S = 60.0


def _remember(seen: set, order, key, cap: int) -> None:
    """Remember `key`, evicting the oldest once `cap` is reached.

    An evicted key can be re-written once if the venue serves that row again,
    which duplicates a row rather than losing one. That is the right way round:
    a duplicate is auditable, a dropped observation is not.
    """
    seen.add(key)
    order.append(key)
    while len(order) > cap:
        seen.discard(order.popleft())


def fill_key(x: Mapping[str, Any]) -> tuple:
    return (x.get("transactionHash"), x.get("conditionId"), int(x["outcomeIndex"]),
            x.get("side"), round(float(x["price"]), 6), round(float(x["size"]), 6),
            int(x["timestamp"]))


UPDOWN_COINS = ("btc", "eth", "sol", "xrp", "doge")
UPDOWN_PERIODS = (("5m", 300), ("15m", 900))


def active_habitat_slugs(now: Optional[float] = None) -> List[str]:
    """The up/down windows trading RIGHT NOW, derived rather than searched.

    WHY NOT QUERY GAMMA BY DATE. `?closed=false&order=startDate&ascending=false`
    returns the markets starting FURTHEST in the future -- measured, every one
    of the 112 it returned ended 11+ hours out, and none were trading. The
    currently-live window cannot be found that way.

    The slug carries its own window start (`btc-updown-15m-<epoch>`), so the
    live one is arithmetic: floor(now / period) * period. Ten markets, twenty
    tokens, no search. The previous window is included because a fill can land
    in it moments after rollover.
    """
    now = now if now is not None else time.time()
    out = []
    for coin in UPDOWN_COINS:
        for dur, per in UPDOWN_PERIODS:
            ep = int(now // per * per)
            out.append("%s-updown-%s-%d" % (coin, dur, ep))
            out.append("%s-updown-%s-%d" % (coin, dur, ep - per))
    return out


def discover_habitat(now: Optional[float] = None) -> List[dict]:
    """Resolve the live up/down windows to their two CLOB tokens each.

    This is what makes a PRE-FILL book possible at all: without it we only
    learn a market when L5 trades it, and the book ring for that token then
    starts AFTER his fill -- so every pre_fill_book would be null by
    construction, which is not an observation of anything.
    """
    out: List[dict] = []
    for slug in active_habitat_slugs(now):
        try:
            ms = _get("%s/markets?slug=%s" % (GAMMA, urllib.parse.quote(slug)))
        except Exception:
            continue
        for m in ms or []:
            if m.get("closed"):
                continue
            try:
                toks = json.loads(m["clobTokenIds"])
            except Exception:
                continue
            for i, t in enumerate(toks[:2]):
                out.append({"token": str(t), "conditionId": m.get("conditionId"),
                            "slug": m.get("slug"), "outcome_index": i,
                            "endDate": m.get("endDate")})
    return out


class Recorder:
    """Raw streams and derived events are written to SEPARATE files, always."""

    def __init__(self, root: str) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.books: Dict[str, BookRing] = {}
        self.states: Dict[str, MarketState] = {}
        self.seen: set = set()
        self.pending: List[dict] = []
        self.tokens: Dict[str, dict] = {}
        self.prints: Dict[str, List[dict]] = {}
        self.settle: Dict[str, float] = {}
        self.counts: Dict[str, int] = {"raw_books": 0, "raw_fills": 0,
                                       "events": 0, "book_errors": 0}
        self._lk = threading.Lock()
        self._shedding = False
        self.token_order: List[str] = []
        # Raw rows already persisted. Bounds `raw_fills`/`raw_prints` to one
        # write per distinct observation instead of one per poll. FIFO-capped,
        # because an unbounded set of print keys is its own leak -- at observed
        # print rates it would reach hundreds of MB of RSS in a day, trading a
        # disk problem for a memory one.
        self._raw_seen: set = set()
        self._raw_order: collections.deque = collections.deque()
        self._raw_print_seen: set = set()
        self._raw_print_order: collections.deque = collections.deque()
        self._book_hash: Dict[str, str] = {}
        self._book_written: Dict[str, float] = {}

    # `events.jsonl` is the evidence and is NEVER rotated or dropped. The raw
    # firehoses are bounded, because at a 3 s cadence over ~40 full ladders
    # raw_books alone grows by order gigabytes per day and a full volume takes
    # the whole collector down with it -- which has happened here before. Each
    # event already embeds the specific raw rows its derivations used
    # (`pre_fill_book`, `queue_trail`, `post_fill_book`, `window_prints`), so a
    # rotated firehose never costs us the ability to re-audit a finding.
    # 3 GB x 3 raw streams x 2 generations = 18 GB steady state, against a
    # 50 GB volume whose COLLECTOR streams are unbounded and already at 2.7 GB.
    # Measured raw rate after deduplication is ~2.5 GB/day, so this keeps
    # roughly 2-4 days of firehose -- which exists to reconstruct book states at
    # times with NO fill (the unfilled controls H-FLOW needs), since every
    # fill's own ladders are embedded in its event and never rotate.
    ROTATE_BYTES = float(os.getenv("L5_PROSPECTIVE_ROTATE_BYTES", str(3 * 2 ** 30)))
    NEVER_ROTATE = ("events", "rotations", "gaps")
    DISK_FLOOR_BYTES = float(os.getenv("L5_PROSPECTIVE_DISK_FLOOR", str(2 * 2 ** 30)))

    def free_bytes(self) -> Optional[float]:
        try:
            st = os.statvfs(self.root)
            return float(st.f_bavail) * float(st.f_frsize)
        except (OSError, AttributeError):
            return None

    def _rotate(self, path: str, name: str) -> None:
        prev = path + ".1"
        try:
            size = os.path.getsize(path)
            if os.path.exists(prev):
                os.remove(prev)
            os.rename(path, prev)
        except OSError:
            return
        # The DROP is itself an observation, recorded where it cannot be
        # rotated away.
        self._write("rotations", {"_t": time.time(), "stream": name,
                                  "dropped_to": os.path.basename(prev),
                                  "bytes": size,
                                  "note": "raw firehose rotation; events.jsonl "
                                          "is never rotated"})

    def _write(self, name: str, row: Mapping[str, Any]) -> None:
        path = os.path.join(self.root, name + ".jsonl")
        if name not in self.NEVER_ROTATE:
            try:
                if os.path.getsize(path) >= self.ROTATE_BYTES:
                    self._rotate(path, name)
            except OSError:
                pass
            free = self.free_bytes()
            if free is not None and free < self.DISK_FLOOR_BYTES:
                # Shed the firehose, keep the evidence. Recorded once per
                # transition so the shedding is visible, not silent.
                if not self._shedding:
                    self._shedding = True
                    self._write("gaps", {"_t": time.time(), "reason":
                                         "raw streams shed: free space below "
                                         "floor", "free_bytes": free,
                                         "floor": self.DISK_FLOOR_BYTES})
                self.counts["raw_shed"] = self.counts.get("raw_shed", 0) + 1
                return
            if self._shedding:
                self._shedding = False
                self._write("gaps", {"_t": time.time(),
                                     "reason": "raw streams resumed",
                                     "free_bytes": free})
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    # ---- raw capture
    def poll_books(self, workers: int = 12) -> None:
        """Sweep every watched token's full ladder, IN PARALLEL.

        Sequentially this was ~40 requests at ~0.3 s each, and with the
        per-market print sweep on top the loop took over 20 s -- so new L5
        fills were not noticed for tens of seconds and the book ring had huge
        holes. The sweep must finish well inside the book cadence or the
        "pre-fill" snapshot stops being near the fill.
        """
        items = list(self.tokens.items())

        def one(it):
            tok, meta = it
            try:
                bk = _get("%s/book?token_id=%s" % (CLOB, tok))
            except Exception:
                # A rolled-over window's market closes and its book 404s.
                # Left in the sweep it burns request budget every cycle.
                self.counts["book_errors"] += 1
                meta["errors"] = int(meta.get("errors", 0)) + 1
                if meta["errors"] >= 3:
                    self.tokens.pop(tok, None)
                    self.counts["tokens_retired"] = \
                        self.counts.get("tokens_retired", 0) + 1
                return None
            meta["errors"] = 0
            t = time.time()
            row = dict(bk)
            row["_t"] = t
            row["_token"] = tok
            return tok, meta, t, row, bk

        with ThreadPoolExecutor(workers) as ex:
            for r in ex.map(one, items):
                if r is None:
                    continue
                tok, meta, t, row, bk = r
                with self._lk:
                    self.books.setdefault(tok, BookRing()).add(t, row)
                    self.counts["raw_books"] += 1
                # WRITE ON CHANGE. The venue stamps each book with a `hash`;
                # an unchanged hash is the same ladder we already persisted, and
                # at a 3 s cadence over ~40 tokens re-writing it measured
                # ~2 GB/day. A keepalive row every BOOK_KEEPALIVE_S records that
                # the book was READ and found unchanged, so a quiet market and
                # an unpolled one stay distinguishable. The in-memory ring still
                # receives EVERY poll, so the trail and the pre/post books in
                # each event are unaffected by this.
                h = bk.get("hash")
                changed = (h is None or self._book_hash.get(tok) != h)
                stale = (t - self._book_written.get(tok, 0.0)) >= BOOK_KEEPALIVE_S
                if changed or stale:
                    self._book_hash[tok] = h
                    self._book_written[tok] = t
                    self._write("raw_books", {
                        "_t": t, "token": tok, "slug": meta.get("slug"),
                        "bids": bk.get("bids"), "asks": bk.get("asks"),
                        "tick_size": bk.get("tick_size"),
                        "last_trade_price": bk.get("last_trade_price"),
                        "hash": h, "source": "clob/book?token_id",
                        "write_reason": ("changed" if changed else "keepalive")})
                else:
                    self.counts["book_unchanged"] = \
                        self.counts.get("book_unchanged", 0) + 1

    def poll_prints(self, keep_s: float = 600.0) -> None:
        """The trade tape for the markets we watch, fetched PER MARKET.

        The unfiltered global tape does not work for this. It returns the
        newest 500 prints across ~86 markets spanning ~30 s, so a quiet market
        like a single btc-updown window is crowded out entirely -- measured,
        its print list came back EMPTY while 122 other markets had rows, and
        every arriving-flow numerator was therefore 0. `?market=<conditionId>`
        returns that market's own prints (100 rows, ~130 s), which is the
        series the primary ratio actually needs.
        """
        conds = {m.get("conditionId") for m in self.tokens.values()
                 if m.get("conditionId")}
        t = time.time()
        cut = t - keep_s

        def fetch_one(cond):
            try:
                return cond, _get("%s/trades?%s" % (DATA_API, urllib.parse.urlencode(
                    {"market": cond, "limit": 100})))
            except Exception:
                return cond, None

        with ThreadPoolExecutor(8) as ex:
            got = list(ex.map(fetch_one, list(conds)[:24]))
        for cond, d in got:
            if d is None:
                continue
            # Raw prints are PERSISTED, not just counted: a derived feature
            # whose source rows were thrown away cannot be re-audited. Each
            # distinct print is written once -- the endpoint re-serves the same
            # ~100 rows every sweep, so writing the payload verbatim measured
            # ~6 GB/day of duplicates.
            newp = []
            for x in (d or []):
                k = (cond, x.get("timestamp"), x.get("price"), x.get("size"),
                     x.get("proxyWallet"), x.get("outcomeIndex"))
                if k not in self._raw_print_seen:
                    _remember(self._raw_print_seen, self._raw_print_order, k,
                              RAW_PRINT_KEYS_CAP)
                    newp.append(x)
            prow = {"_t": t, "market": cond,
                    "source": "data-api/trades?market",
                    "n_returned": len(d or []), "n_new": len(newp)}
            if newp:
                prow["payload"] = newp
            self._write("raw_prints", prow)
            with self._lk:
                lst = self.prints.setdefault(cond, [])
                have = {(q["t"], q["price"], q["size"], q["proxyWallet"]) for q in lst}
                for x in d or []:
                    r = {"t": float(x["timestamp"]), "price": float(x["price"]),
                         "size": float(x["size"]), "side": x.get("side"),
                         "outcomeIndex": int(x.get("outcomeIndex", -1)),
                         "proxyWallet": x.get("proxyWallet")}
                    k = (r["t"], r["price"], r["size"], r["proxyWallet"])
                    if k not in have:
                        have.add(k)
                        lst.append(r)
                self.prints[cond] = sorted(
                    [q for q in lst if q["t"] >= cut], key=lambda q: q["t"])[-600:]

    def poll_fills(self, emit: bool = True) -> None:
        """emit=False marks what is already there as seen WITHOUT emitting.

        THE FIRST POLL RETURNS L5's WHOLE RECENT BACKLOG -- 500 fills that
        happened before this process existed. Emitting them produced 500 event
        records whose pre-fill book was null BY CONSTRUCTION, which is not an
        observation of anything: we were not watching when they occurred. The
        chain this recorder exists to capture begins at the first fill we were
        already polling the book for."""
        raw: Dict[tuple, dict] = {}
        taker: set = set()
        for flag in ("false", "true"):
            try:
                d = _get("%s/trades?%s" % (DATA_API, urllib.parse.urlencode(
                    {"user": L5_WALLET, "limit": 500, "offset": 0, "takerOnly": flag})))
            except Exception:
                continue
            # PERSIST EACH RAW ROW ONCE, NOT ONCE PER POLL. The endpoint serves
            # L5's newest 500 fills every time, so writing the payload verbatim
            # at a 3 s cadence re-wrote the same rows ~28,800 times a day --
            # measured at 30.7 MB in two minutes, on course for ~17 GB/day
            # against a 50 GB volume shared with the collector streams. That is
            # not preservation, it is duplication, and it ends with a full disk
            # taking the whole container down.
            #
            # What is preserved is unchanged: every DISTINCT raw row is written
            # exactly once with the fetch that first served it, and a poll that
            # returned nothing new still writes a row, so "we did not look" and
            # "we looked and there was nothing" stay different facts.
            new = [x for x in (d or []) if fill_key(x) not in self._raw_seen]
            for x in new:
                _remember(self._raw_seen, self._raw_order, fill_key(x),
                          RAW_FILL_KEYS_CAP)
            rec = {"_t": time.time(), "takerOnly": flag,
                   "source": "data-api/trades", "n_returned": len(d or []),
                   "n_new": len(new)}
            if new:
                rec["payload"] = new
            self._write("raw_fills", rec)
            self.counts["raw_fills"] += len(new)
            for x in d or []:
                k = fill_key(x)
                if flag == "true":
                    taker.add(k)
                else:
                    raw[k] = x
        with self._lk:
            for k, x in sorted(raw.items(), key=lambda kv: kv[0][6]):
                if k in self.seen:
                    continue
                self.seen.add(k)
                if emit:
                    self.pending.append({"fill": x, "key": k,
                                         "is_taker": k in taker,
                                         "seen_at": time.time()})
                else:
                    self.counts["seeded_backlog"] = \
                        self.counts.get("seeded_backlog", 0) + 1
                tok = str(x.get("asset") or "")
                if tok and tok not in self.tokens:
                    self.tokens[tok] = {"token": tok, "slug": x.get("slug"),
                                        "conditionId": x.get("conditionId")}

    # ---- derive
    def drain(self, now: Optional[float] = None) -> List[dict]:
        now = now or time.time()
        out, keep = [], []
        with self._lk:
            pend = list(self.pending)
            self.pending = []
        for p in pend:
            x = p["fill"]
            ts = int(x["timestamp"])
            tok = str(x.get("asset") or "")
            ring = self.books.get(tok)
            post = ring.after(ts) if ring else None
            if post is None and (now - p["seen_at"]) < POST_FILL_GRACE_S:
                keep.append(p)
                continue
            cond = x.get("conditionId") or ""
            st = self.states.setdefault(cond, MarketState())
            pre = ring.before(ts) if ring else None
            # Snapshots over the lookback ENDING AT the pre-fill book. A single
            # pre-book cannot separate a traded queue from a cancelled one; the
            # sequence leading up to it can.
            trail = (ring.window(float(pre["_t"]) - TRAIL_LOOKBACK_S,
                                 float(pre["_t"]))
                     if (ring and pre) else [])
            ev = build_event(
                x, is_taker=p["is_taker"], state=st,
                pre=pre, post=post,
                prints=self.prints.get(cond, []),
                settle_epoch=self.settle.get(cond),
                label_source="data-api/trades?takerOnly",
                trail=trail)
            st.apply(int(x["outcomeIndex"]), float(x["size"]), x.get("side"), ts)
            self._write("events", ev)
            self.counts["events"] += 1
            out.append(ev)
        with self._lk:
            self.pending.extend(keep)
        return out

    def refresh_habitat_for_settlement(self) -> None:
        for m in discover_habitat():
            m = dict(m, habitat=True)
            cur = self.tokens.get(m["token"])
            if cur is None:
                self.tokens[m["token"]] = m
            else:
                cur["habitat"] = True
            if m.get("endDate") and m.get("conditionId"):
                try:
                    import datetime as dt
                    self.settle[m["conditionId"]] = dt.datetime.strptime(
                        m["endDate"], "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=dt.timezone.utc).timestamp()
                except Exception:
                    pass


MAX_TOKENS = 64          # habitat (~38) plus the markets L5 is working


def prune_tokens(rec: "Recorder", keep: int = MAX_TOKENS) -> None:
    """Keep the tokens L5 is actually working, newest first.

    Polling his whole habitat at cadence would be ~50 req/s for markets he is
    not in. The tokens that matter are the ones his recent fills name.
    """
    if len(rec.tokens) <= keep:
        return
    order = {t: i for i, t in enumerate(rec.token_order)}
    # HABITAT TOKENS ARE NEVER PRUNED. They are the only reason a PRE-FILL book
    # exists: dropping them would leave us discovering each market from L5's own
    # fill, which is precisely the blindness this recorder was built to remove.
    # (The first version sorted by token_order and habitat tokens, absent from
    # it, sorted to -1 and were evicted FIRST -- exactly backwards.)
    droppable = [t for t in rec.tokens if not rec.tokens[t].get("habitat")]
    n = len(rec.tokens) - keep
    for t in sorted(droppable, key=lambda x: order.get(x, -1))[:n]:
        rec.tokens.pop(t, None)
        rec.books.pop(t, None)


class Driver:
    """The observation loop, factored out of `run()` so the bounded runner and
    the durable service thread execute THE SAME code.

    Duplicating the loop would have meant duplicating the rollover detection
    below, which is the part that was wrong once already and is easy to get
    wrong again.
    """

    def __init__(self, rec: "Recorder") -> None:
        self.rec = rec
        self.last_book = 0.0
        self.last_prints = 0.0
        self.last_disc = 0.0
        self.last_slugs: tuple = ()
        self.seeded = False

    def seed(self) -> None:
        """Learn L5's active tokens and mark the backlog seen, EMITTING
        NOTHING. Book polling starts here, so the first emitted event is the
        first fill we were actually watching for."""
        rec = self.rec
        rec.poll_fills(emit=False)
        rec.refresh_habitat_for_settlement()
        rec.poll_prints()
        for k in list(rec.tokens):
            if k not in rec.token_order:
                rec.token_order.append(k)
        rec.refresh_habitat_for_settlement()
        self.last_disc = time.time()
        self.last_slugs = tuple(active_habitat_slugs(self.last_disc))
        self.seeded = True

    def step(self, now: Optional[float] = None) -> List[dict]:
        rec = self.rec
        now = time.time() if now is None else now
        # ROLLOVER IS ARITHMETIC, SO DETECT IT EXACTLY RATHER THAN POLL FOR IT.
        # A fixed 120 s refresh let a 15 m window roll at :00, L5 fill 34 s in,
        # and our first book snapshot of that market land 88 s AFTER his first
        # fill -- so every event in it had a null pre-fill book. The live slug
        # set is pure arithmetic on the clock, so compute it every loop for
        # free and touch the network only when it actually changes.
        slugs = tuple(active_habitat_slugs(now))
        if slugs != self.last_slugs or now - self.last_disc >= DISCOVER_CADENCE_S:
            self.last_slugs = slugs
            self.last_disc = now
            rec.refresh_habitat_for_settlement()
        if now - self.last_book >= BOOK_CADENCE_S:
            self.last_book = now
            prune_tokens(rec)
            rec.poll_books()
        if now - self.last_prints >= PRINTS_CADENCE_S:
            self.last_prints = now
            rec.poll_prints()
        rec.poll_fills()
        for t in rec.tokens:
            if t not in rec.token_order:
                rec.token_order.append(t)
        return rec.drain()


def describe(e: Mapping[str, Any]) -> str:
    return ("EVENT %s %s %s px=%.3f q=%.2f %s pre_book=%s ahead=%s flow/ahead=%s"
            % (e["slug"], e["outcome"], e["action"], e["price"], e["size"],
               ("TAKER" if e["is_taker"] else "MAKER"),
               "yes" if e["pre_fill_book"] else "NO",
               e["depth_ahead_of_fill_price"],
               (None if e["arriving_flow_over_depth_ahead"] is None
                else round(e["arriving_flow_over_depth_ahead"], 4))))


def run(root: str, seconds: float = 600.0, verbose: bool = True) -> "Recorder":
    """Observe for `seconds`. Read-only; no order path exists in this process."""
    rec = Recorder(root)
    drv = Driver(rec)
    t_end = time.time() + seconds
    drv.seed()
    while time.time() < t_end:
        for e in drv.step():
            if verbose:
                print(describe(e), flush=True)
        time.sleep(FILL_CADENCE_S)
    rec.drain(now=time.time() + 1e6)       # flush anything still waiting
    return rec


# ------------------------------------------------------- flow backfill (late)
PRINTS_LAG_BUDGET_S = 300.0


def backfill_flow(events: Sequence[dict], prints_by_market: Mapping[str, Sequence[dict]]
                  ) -> List[dict]:
    """Re-join arriving flow to events AFTER the trade tape has caught up.

    MEASURED, NOT ASSUMED: `/trades?market=<conditionId>` lags. For one captured
    fill at t=1789957631 the newest print the endpoint would serve for that
    market was t=1789957543 -- 88 s behind -- and the pre-fill window was 1.3 s
    wide. So at capture time the numerator is not merely small, it is
    STRUCTURALLY UNAVAILABLE: the prints that would fill it have not been
    published yet.

    The book side has no such lag, so the event is emitted immediately with its
    ladders intact and the flow terms are filled in here, later, from the same
    public endpoint. Events whose window still has no coverage keep a null and a
    reason -- a zero would claim we looked and found no flow.
    """
    out = []
    for e in events:
        r = dict(e)
        pre = r.get("pre_fill_book")
        cond = r.get("conditionId")
        ts = float(r.get("timestamp") or 0)
        rows = sorted(prints_by_market.get(cond, []), key=lambda q: float(q["t"]))
        if not pre or not rows:
            r["flow_backfill"] = {"status": "NO_COVERAGE",
                                  "reason": ("no pre-fill book" if not pre
                                             else "no prints fetched for this market")}
            out.append(r)
            continue
        t0 = float(pre["t"])
        newest = float(rows[-1]["t"])
        if newest < ts:
            r["flow_backfill"] = {
                "status": "TAPE_STILL_BEHIND", "newest_print_t": newest,
                "lag_s": ts - newest,
                "reason": ("the tape has not yet published prints covering this "
                           "fill; null is not zero flow")}
            out.append(r)
            continue
        oi = int(r.get("outcome_index", -1))
        px = float(r.get("price") or 0)
        # TWO WINDOWS, AND THEY ARE NOT THE SAME ONE.
        #   flow window   (t0, ts]      t0 = pre-fill book  -> the H-FLOW ratio
        #   recon window  (tr0, ts]     tr0 = FIRST look    -> the reconciliation
        # Recomputing the reconciliation terms over the flow window here (which
        # is what the first version did) would have made a backfilled event
        # disagree with the same event at capture time, over the same prints,
        # for no reason but which variable the loop happened to reuse.
        tr0 = r.get("queue_trail_t0")
        tr0 = t0 if tr0 is None else float(tr0)
        window = [q for q in rows if t0 < float(q["t"]) <= ts]
        recon = [q for q in rows if tr0 < float(q["t"]) <= ts]
        execu = [q for q in window if int(q.get("outcomeIndex", -1)) == oi
                 and float(q.get("price", 1e9)) <= px + 1e-9]
        aq = sum(float(q.get("size") or 0.0) for q in execu)
        ahead = r.get("depth_ahead_of_fill_price")
        r["arriving_volume_since_pre_snapshot"] = aq
        r["arriving_volume_whole_market"] = sum(float(q.get("size") or 0.0)
                                                for q in window)
        r["recent_prints"] = [dict(q) for q in execu][:50]
        r["window_prints"] = [dict(q) for q in recon][:WINDOW_PRINTS_CAP]
        r["window_prints_n_total"] = len(recon)
        r["window_prints_truncated"] = len(recon) > WINDOW_PRINTS_CAP
        r["window_prints_t0"] = tr0
        r["window_prints_t1"] = ts
        r["flow_window_t0"] = t0
        # The reconciliation terms are re-derived from the backfilled window,
        # because at capture time the tape had not published these prints and
        # every consuming-volume figure was structurally zero.
        cons = [q for q in recon if bid_consuming(q, oi, px)]
        r["queue_consuming_volume"] = sum(float(q.get("size") or 0.0) for q in cons)
        r["queue_consuming_prints"] = len(cons)
        cmp_ = [q for q in recon if int(q.get("outcomeIndex", -1)) == (1 - oi)]
        r["complement_arrival_volume"] = sum(float(q.get("size") or 0.0) for q in cmp_)
        r["complement_arrival_prints"] = len(cmp_)
        dis = r.get("depth_ahead_disappeared")
        r["depth_drop_not_explained_by_trades"] = (
            None if dis is None else round(dis - r["queue_consuming_volume"], 6))
        r["arriving_flow_over_depth_ahead"] = (
            None if (ahead is None or ahead <= 0) else aq / ahead)
        r["arriving_flow_over_depth_ahead_reason"] = (
            None if (ahead is not None and ahead > 0)
            else ("no pre-fill book" if ahead is None
                  else "zero depth ahead: ratio undefined, not infinite"))
        r["flow_backfill"] = {"status": "JOINED", "window_s": ts - t0,
                              "recon_window_s": ts - tr0,
                              "prints_in_window": len(window),
                              "prints_in_recon_window": len(recon),
                              "executable_in_window": len(execu),
                              "newest_print_t": newest}
        out.append(r)
    return out
