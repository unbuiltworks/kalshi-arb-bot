"""SHADOW BREADTH: many virtual L5s, one live campaign, zero extra dollars.

OWNER 2026-09-10 (GO): "Run the new f9006faf selector continuously against
the entire live sports/game universe. For every R5F-eligible market, maintain
an independent virtual L5 state machine and record hypothetical
OPEN/EXTEND/COMPLETE actions using the exact live policy and safety rules."

THE PROBLEM THIS EXISTS TO SOLVE. Concurrency 1 is a MONEY constraint. It was
silently also an OBSERVATION constraint: on 2026-09-10 the campaign worked 20
markets in 93 minutes, then one county-cricket residual owned the single slot
for 3h20m -- while sitting at 0.77/0.84, outside the 0.25-0.75 band, so the
engine could not even work it. During that stall 32 markets were eligible and
carrying $630k/minute. One residual = zero new experiments. Nothing about
watching a market costs money, so nothing about watching should be serialised
behind a position.

WHAT IS REUSED, NOT REBUILT. The virtual L5 is
pmus_sports_l5_decision.decide_market -- the SAME frozen sequential policy,
the same persistence-v2 model, the same pair bound the live path uses. This
module supplies breadth and ranking; it supplies no new policy and no new
model. If the two ever disagree it is a bug here, not a variant there.

WHAT IS NEW. Which markets get watched. The existing slate ordering ranks by
the collector's own status buckets; this ranks by contemporaneous traded flow
measured from `notionalTraded` deltas (see pmus_sports_opportunity_rank), over
the full live GAME universe rather than the ~2,037 rows the campaign sees.

PROSPECTIVE COHORTS, BECAUSE "UCL IS BETTER" IS NOT YET A RESULT. Every
selected market is stamped with the rank tier it held AT SELECTION TIME, and
outcomes are tallied per tier. The comparison is therefore decided by data
gathered after the ranking, not by re-reading the ranking that produced it.
Assigning a tier after seeing the outcome is the one thing that would make
this whole loop worthless.

READ-ONLY, STRUCTURALLY. No executor import, no credentials, no order path.
A virtual "fill" is `fill_class: possible` exactly as decide_market labels it
-- an upper bound inferred from later trades through the resting price, with
queue position unproven. It is never called a fill.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from decimal import Decimal as D
from collections import deque
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from src import pmus_execution_shadow as exec_shadow
from src import pmus_shadow_proposal_log as plog
from src import pmus_shadow_completion as shadow_completion
from src import pmus_sports_opportunity_rank as rank_mod

GATEWAY = os.getenv("PMUS_GATEWAY", "https://gateway.polymarket.us")
PREFIX = "PMUS_SHADOW_BREADTH "

# Bounded so a widening universe cannot turn into an unbounded request rate.
# BREADTH. OWNER: "Don't let Thursday-night NFL determine how long this takes.
# Run the exact execution-quality shadow across every eligible live sports
# market the selector can observe." Watching costs nothing but request budget,
# and the bottleneck on reaching a decidable sample is the number of markets
# carrying a residual at once -- not the policy, which is unchanged.
WATCH_SLOTS = int(os.getenv("PMUS_SHADOW_WATCH_SLOTS", "96"))
CYCLE_S = float(os.getenv("PMUS_SHADOW_CYCLE_S", "45"))
POLL_PAUSE_S = float(os.getenv("PMUS_SHADOW_POLL_PAUSE_S", "0.25"))
OBS_KEEP = int(os.getenv("PMUS_SHADOW_OBS_KEEP", "240"))

# RAW EVIDENCE. OWNER: "Don't just persist aggregate cohort counters. Persist
# enough timestamped market observations and virtual decisions/fills to
# recompute the funnel after we discover another analysis bug. We should
# never again lose the underlying evidence because a derived field was
# wrong." Twice now a derived field has been wrong -- spread read null on
# every row, movement ran over a variable window -- and in both cases the
# aggregates built on them were unrecoverable because the inputs were gone.
# Counters are a summary of evidence; they are not the evidence.
RAW_DIR = os.getenv("PMUS_SHADOW_RAW", "/data/pmus_shadow/raw")
# Bounded. An unbounded tape is what filled the recorder's volume at 00:40Z
# on 2026-09-08; a collector that dies of its own evidence collects nothing.
RAW_MAX_BYTES = int(os.getenv("PMUS_SHADOW_RAW_MAX_BYTES",
                              str(512 * 1024 * 1024)))
# Flow is measured over a TRAILING WINDOW, not between consecutive polls.
# Measured live 2026-09-10T18:5xZ: differencing adjacent ~45s polls made
# eligibility flap 32 -> 5 -> 1, because most markets simply have no print
# inside any given 45 seconds. That is a sampling artifact, not a market
# going quiet, and gating on it would have thrown away most of the
# universe every cycle.
FLOW_WINDOW_S = float(os.getenv("PMUS_SHADOW_FLOW_WINDOW_S", "300"))
# The movement/band shape is computed over a FIXED trailing window so it
# means the same thing in every market. It previously ran over the WHOLE
# observation deque (OBS_KEEP=240 at CYCLE_S=45 -> up to 3h) while being
# emitted as `mid_stdev_1h`, so the figure was neither an hour nor a
# constant: a market watched since boot and one added ten minutes ago were
# compared on windows of wildly different length, and every redeploy reset
# every window to ~0. Movement is one of the three predictors the funnel
# has to test, and a predictor measured over a variable window cannot be
# ranked against itself.
SHAPE_WINDOW_S = float(os.getenv("PMUS_SHADOW_SHAPE_WINDOW_S", "3600"))
# Rank tiers stamped AT SELECTION. Compared against each other later.
TIERS = ((0, 8, "T1_top8"), (8, 16, "T2_9_16"), (16, 32, "T3_17_32"),
         (32, 64, "T4_33_64"), (64, 10 ** 6, "T5_65_plus"))

LABELS = {
    "order_entry": False,
    "realized_pnl": None,
    "fill_semantics": ("virtual fills are decide_market's `possible` class: "
                       "an upper bound from later trades through the resting "
                       "price, queue position unproven -- never a fill"),
    "policy": "frozen live policy via pmus_sports_l5_decision.decide_market",
    "cohorts": "rank tier stamped at SELECTION time, never after the outcome",
}


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _num(x: Any) -> Optional[float]:
    if isinstance(x, Mapping):
        x = x.get("value")
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# How many price levels a side keeps. Deep enough that a complement posted a
# few ticks back can still count everything ahead of it; bounded because
# unbounded retention is what cost the recorder its volume on 2026-09-08.
LADDER_LEVELS = 25


def _ladder(bids, offers) -> dict:
    """Both sides as [{px, qty}], in the venue's own YES frame.

    Kept in ONE frame on purpose. A NO order's queue lives on the offers
    side at `1 - px`, and storing a pre-converted NO ladder alongside would
    make two representations of one book that could disagree -- the same
    shape as the distance_to_touch bug, which compared a NO price to a YES
    bid."""
    def side(levels):
        out = []
        for lvl in (levels or [])[:LADDER_LEVELS]:
            if not isinstance(lvl, Mapping):
                continue
            px = lvl.get("px")
            px = px.get("value") if isinstance(px, Mapping) else px
            px, qty = rank_mod._f(px), rank_mod._f(lvl.get("qty"))
            if px is not None and qty is not None:
                out.append({"px": px, "qty": qty})
        return out
    return {"bids": side(bids), "offers": side(offers),
            "frame": "YES prices; a NO order at p sits at 1-p on `offers`"}


def observation_row(slug: str, market_data: Mapping[str, Any],
                    now: float) -> dict:
    """One collector-schema observation from a public book payload.

    The schema is the one pmus_status_report already consumes (`t`, `bid`,
    `ask`, `shares_traded_cum`, `last_trade_px`) so infer_trades and
    decide_market read these rows unchanged.

    `offers`, NOT `asks`. The venue serves sell depth under `offers`; reading
    `asks` yields [] on every market and is indistinguishable from a genuinely
    one-sided book (src/pmus_sports_offers_hotfix.py, OWNER 5534675078).
    """
    bids = market_data.get("bids") or []
    offers = market_data.get("offers") or []
    stats = market_data.get("stats") or {}
    bid, bid_q = rank_mod.touch(bids)
    ask, ask_q = rank_mod.touch(offers)
    return {
        "t": _utc(now), "slug": slug,
        "bid": bid, "ask": ask, "bid_qty": bid_q, "ask_qty": ask_q,
        # THE LADDER, not just the touch. OWNER 2026-09-11, on a book showing
        # ~211k at the 81c bid: "we have been asking why can't we fill while
        # throwing away the exact data that tells us whether 50,000 or 250,000
        # shares are standing in front of us." The venue serves every level and
        # this function took levels[0] and dropped the rest -- so queue-ahead
        # was never computable anywhere downstream.
        #
        # Bounded at LADDER_LEVELS per side rather than unbounded: the
        # recorder filled a 50 GB volume with unbounded retention on 09-08,
        # and queue-ahead only needs the levels BETTER than our price, which
        # sit within a few ticks of the touch.
        "book_levels": _ladder(bids, offers),
        "shares_traded_cum": _num(stats.get("sharesTraded")),
        "notional_traded_cum": _num(stats.get("notionalTraded")),
        "last_trade_px": _num(stats.get("lastTradePx")),
        "state": market_data.get("state"),
        "transact_time": market_data.get("transactTime"),
        "_poll_t": now,
    }


def flow_over_window(obs: Sequence[Mapping[str, Any]],
                     now: float, window_s: float = FLOW_WINDOW_S) -> Optional[float]:
    """Traded notional per minute over the trailing window.

    Uses the OLDEST observation still inside the window against the newest,
    rather than the last two polls. A market that printed nothing in the last
    45 seconds is usually just a market nobody traded in those 45 seconds;
    differencing adjacent polls turns that into "ineligible" and empties the
    watch list. Returns None when the span is too short to be a rate.
    """
    inside = [o for o in obs
              if (o.get("_poll_t") or 0) >= now - window_s]
    if len(inside) < 2:
        return None
    return rank_mod.flow_usd_per_min(
        {"notional_traded": inside[0].get("notional_traded_cum"),
         "transact_time": inside[0].get("transact_time")},
        {"notional_traded": inside[-1].get("notional_traded_cum"),
         "transact_time": inside[-1].get("transact_time")})


def shape_window(obs: Sequence[Mapping[str, Any]], now: float,
                 window_s: float = SHAPE_WINDOW_S) -> list:
    """Two-sided observations inside the trailing shape window, oldest first.

    Rows without a parseable timestamp are DROPPED rather than assumed
    recent: an undated observation admitted into a trailing window silently
    widens it, which is the defect this function exists to remove.
    """
    out = []
    for o in obs or []:
        if o.get("bid") is None or o.get("ask") is None:
            continue
        ts = _num(o.get("_poll_t"))
        if ts is None or (now - ts) > window_s:
            continue
        out.append(o)
    return out


NOTIONAL_CENTS_PER_USD = 100.0


def _to_usd(cents: Optional[float]) -> Optional[float]:
    return None if cents is None else cents / NOTIONAL_CENTS_PER_USD


def _flow_cents(prev, cur, flow) -> Optional[float]:
    """Trailing-window flow in CENTS of notional; the adjacent-poll
    difference remains the fallback when the window is too short."""
    if flow is not None:
        return flow
    if not prev:
        return None
    return rank_mod.flow_usd_per_min(
        {"notional_traded": prev.get("notional_traded_cum"),
         "transact_time": prev.get("transact_time")},
        {"notional_traded": cur.get("notional_traded_cum"),
         "transact_time": cur.get("transact_time")})


def scored_row(slug: str, prev: Optional[Mapping[str, Any]],
               cur: Mapping[str, Any],
               history_shape: Optional[Mapping[str, Any]] = None,
               flow: Optional[float] = None) -> dict:
    """A ranking row for one market. `flow` is supplied by the caller from
    the trailing window; `prev` remains the adjacent-poll fallback."""
    shape = history_shape or {}
    return {
        "slug": slug, "state": cur.get("state"),
        "bid": cur.get("bid"), "ask": cur.get("ask"),
        "bid_qty": cur.get("bid_qty"), "ask_qty": cur.get("ask_qty"),
        "book_levels": cur.get("book_levels"),
        "maker_pair_cost": rank_mod.maker_pair_cost(cur.get("bid"),
                                                    cur.get("ask")),
        # NOTIONAL IS IN CENTS. Measured 2026-09-10 on
        # aec-nfl-sf-lar-2026-09-10 across 8 independent volume intervals:
        # d_notional/d_shares came to 35.13-35.40 while the book was
        # 0.35/0.355 -- exactly 100x the price, on every interval. The
        # venue prices notionalTraded 0-100, so the raw delta is CENTS of
        # notional. Ranking was unaffected (a uniform factor is monotonic)
        # but the REPORTED figure overstated dollar flow 100-fold: the top
        # market reads ~$189k/min raw and is really ~$1.9k/min. A number
        # labelled usd has to be usd, so both are carried explicitly.
        "flow_cents_per_min": _flow_cents(prev, cur, flow),
        "flow_usd_per_min": _to_usd(_flow_cents(prev, cur, flow)),
        "in_band_fraction": shape.get("in_band_fraction"),
        # SPREAD WAS NULL ON EVERY ROW EVER EMITTED. The cycle report read
        # r["spread"], and nothing in scored_row ever set that key, so a
        # field the funnel is required to test as a predictor was silently
        # None for the life of the service -- while hour_shape was already
        # computing median_spread and discarding it. The live spread is also
        # exactly recoverable: maker_pair_cost = bid + (1-ask) = 1 - spread.
        "spread": (None if cur.get("bid") is None or cur.get("ask") is None
                   else _num(cur.get("ask")) - _num(cur.get("bid"))),
        "median_spread": shape.get("median_spread"),
        "mid_stdev": shape.get("mid_stdev"),
        "mid_range": shape.get("mid_range"),
        # The window is reported next to the statistic so a short one is
        # visible instead of reading as a calm market.
        "shape_window_s": SHAPE_WINDOW_S,
        "shape_span_s": shape.get("span_s"),
        "shape_points": shape.get("points"),
    }


def _first_leg_of(actions) -> dict:
    """Price and time of the FIRST virtual fill behind this residual.

    The complement price is derived from the first leg (ceiling minus the
    surplus average), so a complement sitting unreachable may be a
    consequence of WHERE THE FIRST LEG FILLED rather than anything about the
    complement. Recording it is what makes that separable.
    """
    for a in actions or []:
        if a.get("decision") == "quote" and a.get("fill_class") == "possible":
            return {"price": a.get("target_price"), "t": a.get("t"),
                    "side": a.get("side"), "relation": a.get("relation"),
                    "fill_latency_s": a.get("fill_latency_s")}
    return {}


def _book_of(row: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """A decision-shaped book from a ranked row, or None when it is not
    two-sided. A one-sided book is not a book with a missing number."""
    if not row or row.get("bid") is None or row.get("ask") is None:
        return None
    return {"bid": D(str(row["bid"])), "ask": D(str(row["ask"]))}


def tier_of(index: int) -> Optional[str]:
    for lo, hi, name in TIERS:
        if lo <= index < hi:
            return name
    return None


def classify_actions(decision: Mapping[str, Any]) -> dict:
    """Collapse one decide_market result into the owner's funnel stages.

    OWNER: "eligible -> selected -> virtual first-fill -> virtual <=$1
    completion -> residual -> settlement". Every stage below is derived from
    the frozen policy's own replayed actions; none is invented here.

    ON THE "<=$1" QUALIFIER. The first version of this function filtered
    completes on a `pair_cost` key. THAT KEY DOES NOT EXIST -- plan_action
    emits `max_pair_cost` -- so the filter matched nothing and the completion
    count would have read 0 forever, in a loop whose entire purpose is
    counting completions. A silent always-zero is worse than a crash.

    What the policy actually guarantees is stronger than the filter was:
    complement_target prices every completion at the tightest of the >=1c
    hard bound (0.99) and the 0.98 observation cap, so a completion above
    $1.00 CANNOT be emitted. The count is therefore of completions, and
    `max_pair_cost` is checked as an ASSERTION rather than a filter -- a
    completion above 1.00 is recorded as a policy violation, not silently
    dropped from the tally.
    """
    acts = decision.get("actions_replayed") or []
    first_fill = extends = complete_le1 = 0
    violations = []
    for a in acts:
        if a.get("decision") != "quote" or a.get("fill_class") != "possible":
            continue
        rel = str(a.get("relation") or "")
        if rel == "open":
            first_fill += 1
        elif rel == "extend":
            extends += 1
        elif rel == "complete":
            mpc = a.get("max_pair_cost")
            try:
                over = mpc is not None and float(mpc) > 1.0
            except (TypeError, ValueError):
                over = False
            if over:
                violations.append({"t": a.get("t"), "max_pair_cost": mpc})
            else:
                complete_le1 += 1
    return {
        "virtual_first_fills": first_fill,
        "virtual_extends": extends,
        "virtual_completions_le_1": complete_le1,
        "virtual_residual_open": bool(first_fill + extends > complete_le1),
        "pair_ceiling_violations": violations,
        "actions_replayed": len(acts),
        "executable_now": bool(decision.get("executable_now")),
    }


def funnel(cohorts: Mapping[str, Mapping[str, Any]]) -> dict:
    """Per-tier funnel plus the owner's primary operational ratio.

    'economic completions per residual dollar' needs a residual DOLLAR, and
    shadow has no settled dollars. So the shadow analogue is reported under
    its own name -- completions per residual-opening market -- and the dollar
    ratio stays where dollars exist, in the settled live cohort. Reusing the
    owner's phrase for a quantity with no dollars in it would be the kind of
    relabelling that makes two different numbers look like one.
    """
    out = {}
    for tier, c in cohorts.items():
        resid_mkts = len(c.get("residual_opening_markets") or ())
        out[tier] = {
            # cycles are not markets: a market watched nine times is one
            # market, and conflating the two inflates every rate below it
            "selection_cycles": c.get("selection_cycles", 0),
            "distinct_markets": len(c.get("markets") or ()),
            "virtual_first_fills": c.get("virtual_first_fills", 0),
            "virtual_extends": c.get("virtual_extends", 0),
            "virtual_completions_le_1": c.get("virtual_completions_le_1", 0),
            "residual_opening_markets": resid_mkts,
            "completing_markets": len(c.get("completing_markets") or ()),
            "completions_per_residual_market": (
                round(c.get("virtual_completions_le_1", 0) / resid_mkts, 4)
                if resid_mkts else None),
        }
    return out


class ShadowBreadth:
    """One cycle = discover -> rank -> poll -> N virtual L5s -> funnel."""

    STATE_PATH = os.getenv("PMUS_SHADOW_STATE",
                           "/data/pmus_shadow/cohorts.json")

    def __init__(self, get_json=None, sleep=time.sleep,
                 slots: int = WATCH_SLOTS, now=time.time,
                 state_path: Optional[str] = None):
        self._get = get_json or self._http
        self._sleep = sleep
        self._slots = slots
        self._now = now
        self.obs: dict[str, deque] = {}
        self.cohorts: dict[str, dict] = {}
        # Every virtual action already counted, keyed (slug, t, relation).
        # decide_market REPLAYS ITS WHOLE WINDOW on every call, so the same
        # virtual OPEN reappears in every cycle for as long as it stays
        # inside the window. Measured live: nine cycles turned five markets
        # into "29 first fills". Counting cumulative totals off a re-replayed
        # window inflates every stage of the funnel -- the same class of
        # error as counting a population twice, and just as invisible.
        self._counted: set = set()
        self.cycles = 0
        self._state_path = state_path or self.STATE_PATH
        self._last_scored: dict[str, dict] = {}
        # Which (proposal id, status) pairs already have a durable row. Held
        # per process rather than persisted: a restart re-emitting a row is
        # harmless duplication that the id+status key makes trivial to
        # de-duplicate offline, whereas persisting it risks a restart that
        # silently emits NOTHING -- the failure mode that is expensive.
        self.proposal_rows_seen: set = set()
        try:
            self.execution = exec_shadow.ExecutionShadow(now=now)
        except Exception:                                       # noqa: BLE001
            self.execution = None
        self._load_state()

    # ---- durability -----------------------------------------------------
    # Cohorts lived ONLY in memory until 2026-09-10T21:32Z, when pushing
    # 502fdf27 -- which touches pmus_sports_opportunity_rank.py, a WATCHED
    # path for this service -- redeployed it and silently reset every
    # prospective tally. A loop whose entire purpose is accumulating
    # evidence across time cannot keep that evidence somewhere a routine
    # push erases. (The commit message for 502fdf27 also claimed nothing
    # restarted; that was wrong, and this is the fix rather than the
    # apology.)
    def _load_state(self) -> None:
        try:
            with open(self._state_path) as fh:
                doc = json.load(fh)
        except Exception:                                       # noqa: BLE001
            return
        for tier, c in (doc.get("cohorts") or {}).items():
            c = dict(c)
            for k in ("markets", "residual_opening_markets",
                      "completing_markets"):
                if k in c:
                    c[k] = set(c[k])
            self.cohorts[tier] = c
        self._counted = {tuple(k) for k in (doc.get("counted") or [])}
        self.cycles = int(doc.get("cycles") or 0)

    # ---- raw evidence ---------------------------------------------------
    def append_raw(self, cycle: int, rows, decisions,
                   now: Optional[float] = None, proposals=None,
                   opportunities=None) -> bool:
        """One JSONL record per cycle: the timestamped observations AND the
        virtual decisions/fills behind that cycle's counters.

        Written so the funnel can be RECOMPUTED later. Both halves are
        needed: observations alone cannot reproduce what the policy chose,
        and decisions alone cannot reproduce the book they were chosen
        against. Best-effort -- a failed write must never stop the loop
        observing, but it is REPORTED rather than swallowed, because a
        silent failure here is exactly how the evidence goes missing again.
        """
        try:
            os.makedirs(RAW_DIR, exist_ok=True)
            day = time.strftime("%Y-%m-%d", time.gmtime(now or self._now()))
            path = os.path.join(RAW_DIR, f"observations-{day}.jsonl")
            if self._raw_over_budget():
                return False
            rec = {"cycle": cycle, "t": _utc(now or self._now()),
                   "observations": list(rows or []),
                   "decisions": list(decisions or [])}
            # Proposals carry state_at_post, reachability and research --
            # everything the band, stratum and opportunity reports are
            # derived from. Without them a future analysis bug is
            # unrecoverable even though the books were saved.
            if proposals is not None:
                rec["proposals"] = list(proposals)
            if opportunities is not None:
                rec["opportunities"] = list(opportunities)
            with open(path, "a") as fh:
                fh.write(json.dumps(rec, separators=(",", ":"),
                                    default=str) + "\n")
            return True
        except Exception:                                   # noqa: BLE001
            return False

    def _raw_over_budget(self) -> bool:
        """Refuse to grow past the cap rather than sweeping blindly: the
        oldest raw day is the one most likely to be the out-of-sample
        evidence, so it is kept and new writes stop instead."""
        try:
            total = 0
            for name in os.listdir(RAW_DIR):
                total += os.path.getsize(os.path.join(RAW_DIR, name))
            return total >= RAW_MAX_BYTES
        except Exception:                                   # noqa: BLE001
            return False

    def _save_state(self) -> None:
        """Best effort: a failed write must not stop the loop observing."""
        try:
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            out = {"cycles": self.cycles,
                   "counted": [list(k) for k in self._counted],
                   "cohorts": {}}
            for tier, c in self.cohorts.items():
                d = dict(c)
                for k in ("markets", "residual_opening_markets",
                          "completing_markets"):
                    if k in d:
                        d[k] = sorted(d[k])
                out["cohorts"][tier] = d
            tmp = self._state_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(out, fh)
            os.replace(tmp, self._state_path)
        except Exception:                                       # noqa: BLE001
            pass

    @staticmethod
    def _http(path: str) -> dict:
        req = urllib.request.Request(GATEWAY + path)
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)

    def poll(self, slugs: Sequence[str]) -> int:
        """Refresh observations for the watched set. A market that fails to
        read is skipped, never zeroed -- an unreadable book is not a quiet
        one."""
        now = self._now()
        ok = 0
        for slug in slugs:
            try:
                md = (self._get(f"/v1/markets/{slug}/book") or {}).get("marketData")
            except Exception:                                   # noqa: BLE001
                continue
            if not isinstance(md, Mapping):
                continue
            self.obs.setdefault(slug, deque(maxlen=OBS_KEEP)).append(
                observation_row(slug, md, now))
            ok += 1
            if self._sleep:
                self._sleep(POLL_PAUSE_S)
        return ok

    def scored(self) -> list:
        """Every watched market scored, eligible or not."""
        rows = []
        for slug, dq in self.obs.items():
            if not dq:
                continue
            cur = dq[-1]
            prev = dq[-2] if len(dq) > 1 else None
            flow = flow_over_window(list(dq), self._now())
            win = shape_window(list(dq), self._now())
            shape = dict(rank_mod.hour_shape(
                [{"longPrice": o["ask"], "shortPrice": 1.0 - o["bid"]}
                 for o in win]))
            spans = [_num(o.get("_poll_t")) for o in win]
            spans = [t for t in spans if t is not None]
            shape["span_s"] = (max(spans) - min(spans)) if len(spans) > 1 else 0.0
            row = scored_row(slug, prev, cur, shape, flow)
            self._last_scored[slug] = row
            rows.append(row)
        return rows

    def ranked(self) -> list:
        """Eligible watched markets, most contemporaneous flow first."""
        return rank_mod.rank(self.scored())

    def watch_set(self, slots: Optional[int] = None) -> list:
        """Which markets to keep polling next cycle.

        A SUPERSET of the eligible ones, ordered by flow. Retaining only the
        currently-eligible markets collapsed the watch set from 32 to 5 in
        one cycle live -- and a market that is briefly ineligible (a
        one-sided moment, a quiet minute) would then never be polled again,
        so it could never be observed BECOMING eligible. Eligibility is a
        property of a moment; the watch set has to outlive the moment.
        """
        n = slots or self._slots
        rows = self.scored()
        rows.sort(key=lambda r: -(r.get("flow_usd_per_min") or -1.0))
        return [r["slug"] for r in rows][:n]

    def decide(self, slugs: Sequence[str], model: Mapping[str, Any]) -> list:
        """Run the FROZEN live policy over each watched market."""
        from src import pmus_sports_l5_decision as dec
        now = self._now()
        out = []
        for idx, slug in enumerate(slugs):
            rows = list(self.obs.get(slug) or ())
            if len(rows) < 2:
                continue
            try:
                d = dec.decide_market(slug, rows, now, model)
            except Exception as exc:                            # noqa: BLE001
                out.append({"slug": slug, "error":
                            f"{type(exc).__name__}:{str(exc)[:90]}"})
                continue
            tier = tier_of(idx)
            summary = classify_actions(d)
            fresh = classify_actions(
                {"actions_replayed": self._new_actions(slug, d),
                 "executable_now": d.get("executable_now")})
            # TWO ARMS, computed HERE because this is the only place the
            # full decide_market result is in scope. The first wiring built
            # them from the row below, which carries neither
            # actions_replayed nor inventory_final -- so arm B saw an empty
            # inventory and reported "flat_nothing_to_complete" on every
            # market in production while looking like a working measurement.
            # Caught in the live logs by residual_final reading null on
            # markets the funnel had already counted first fills for.
            arms = shadow_completion.two_arm(d, _book_of(rows[-1]))
            # EXECUTION SHADOW: every arm-B proposal is FOLLOWED FORWARD.
            # `would_post` is a posted price, never a completion -- the
            # tracker resolves it to WOULD_FILL / VIRTUAL_MATCHED / NO_FILL
            # against the same validated fill rule.
            armb = arms.get(shadow_completion.ARM_B) or {}
            if armb.get("completion_would_post") and self.execution is not None:
                snap = d.get("inventory_final") or {}
                inv_b, _why = shadow_completion._inventory_from_actions(
                    d.get("actions_replayed"), snap)
                surplus = None
                if inv_b is not None and inv_b.residual != 0:
                    surplus = inv_b.avg(inv_b.residual > 0)
                first = _first_leg_of(d.get("actions_replayed"))
                self.execution.record(
                    slug, armb.get("plan") or {}, now,
                    ranked_row=self._last_scored.get(slug),
                    residual_before=snap.get("residual"),
                    surplus_avg=surplus, tier=tier, first_leg=first)
            out.append({"slug": slug, "tier": tier,
                        "window": summary, "new_this_cycle": fresh,
                        "two_arm": arms})
            if tier:
                c = self.cohorts.setdefault(
                    tier, {"selection_cycles": 0, "markets": set()})
                c["selection_cycles"] += 1
                c["markets"].add(slug)
                # ONLY the actions not already counted in an earlier cycle.
                for k in ("virtual_first_fills", "virtual_extends",
                          "virtual_completions_le_1"):
                    c[k] = c.get(k, 0) + fresh[k]
                if fresh["virtual_first_fills"] or fresh["virtual_extends"]:
                    c.setdefault("residual_opening_markets", set()).add(slug)
                if fresh["virtual_completions_le_1"]:
                    c.setdefault("completing_markets", set()).add(slug)
        return out

    def _new_actions(self, slug: str, decision: Mapping[str, Any]) -> list:
        """The replayed actions this cycle has not already counted."""
        fresh = []
        for a in decision.get("actions_replayed") or []:
            key = (slug, a.get("t"), a.get("relation"), a.get("target_price"))
            if key in self._counted:
                continue
            self._counted.add(key)
            fresh.append(a)
        return fresh

    def report(self, decisions: Sequence[Mapping[str, Any]],
               watched: int, eligible: int) -> dict:
        self.cycles += 1
        self._save_state()
        return {
            "kind": "shadow_breadth_cycle", "cycle": self.cycles,
            "at": _utc(self._now()),
            "watched": watched, "eligible": eligible,
            "decided": len([d for d in decisions if "error" not in d]),
            "errors": len([d for d in decisions if "error" in d]),
            "funnel_by_tier": funnel(self.cohorts),
            "labels": LABELS,
        }


DISCOVERY_PAGE_PAUSE_S = float(os.getenv("PMUS_SHADOW_DISCOVERY_PAUSE_S", "0.35"))


def discover_game_slugs(get_json, max_pages: int = 80,
                        page: int = 500, sleep=time.sleep) -> list:
    """Live open GAME markets, futures excluded by TYPE.

    PACED, and that is not politeness. This shares a gateway with the LIVE
    probe: an unpaced burst of ~400 GETs in ~35s once drew 429s that starved
    the campaign's authenticated preflight and halted it with a leg held
    (pmus_incentive_audit.program_list carries the same note and the same
    fix). An unpaced 80-page walk every discovery cycle is that pattern
    again, from a service whose entire justification is that it costs
    nothing.

    Bounded, and a short page ends the walk. A failure returns what was
    gathered -- discovery degrades, never dies, the same contract
    pmus_sports_collector.enumerate_open_markets already keeps.
    """
    rows: list = []
    for p in range(max_pages):
        if p and sleep:
            sleep(DISCOVERY_PAGE_PAUSE_S)
        try:
            resp = get_json(f"/v1/markets?active=true&closed=false"
                            f"&limit={page}&offset={p * page}")
        except Exception:                                       # noqa: BLE001
            break
        ms = (resp or {}).get("markets") if isinstance(resp, Mapping) else None
        if not isinstance(ms, list) or not ms:
            break
        rows.extend(m for m in ms
                    if isinstance(m, Mapping)
                    and str(m.get("category")) == "sports"
                    and rank_mod.is_game(m))
        if len(ms) < page:
            break
    return [str(m.get("slug")) for m in rows if m.get("slug")]


def run(cycles: Optional[int] = None) -> int:
    """Continuous shadow breadth. Read-only; never submits anything.

    Runs forever by default: a long-running service that EXITS reads as a
    crash to the platform, whose repair is a rollback.
    """
    from src import pmus_l5_persistence_v2 as pv2
    sb = ShadowBreadth()
    model = pv2.load_v2()
    watch: list = []
    n = 0
    while cycles is None or n < cycles:
        n += 1
        try:
            if n == 1 or n % 20 == 0 or not watch:
                universe = discover_game_slugs(sb._get)
                sb.poll(universe[:max(WATCH_SLOTS * 8, 128)])
                watch = [r["slug"] for r in sb.ranked()][:sb._slots]
                if not watch:
                    watch = universe[:sb._slots]
            polled = sb.poll(watch)
            ranked = sb.ranked()
            order = [r["slug"] for r in ranked]
            decisions = sb.decide(order, model)
            rep = sb.report(decisions, watched=polled, eligible=len(ranked))
            rep["top"] = [{"slug": r["slug"],
                           "flow_usd_per_min": r["flow_usd_per_min"],
                           "maker_pair_cost": r["maker_pair_cost"],
                           "spread": r.get("spread"),
                           "median_spread": r.get("median_spread"),
                           "mid_stdev": r.get("mid_stdev"),
                           "mid_range": r.get("mid_range"),
                           "shape_span_s": r.get("shape_span_s"),
                           "bid_qty": r.get("bid_qty"),
                           "ask_qty": r.get("ask_qty")}
                          for r in ranked[:5]]
            # per-market rows for cohort analysis: aggregate tallies cannot
            # answer time-to-completion or pair-cost-at-completion
            rep["markets"] = [d for d in decisions
                              if d.get("new_this_cycle", {}).get(
                                  "actions_replayed")]
            # TWO ARMS, never summed. decide_market alone cannot complete
            # once a market reaches residual_cap -- the model keeps
            # proposing extend and plan_action keeps refusing it, so arm A's
            # completion column is structurally zero. Arm B asks the same
            # frozen policy's own COMPLETE branch what it would post. See
            # src/pmus_shadow_completion.py for the measurement.
            by_slug = {r["slug"]: r for r in ranked}
            rep["two_arm"] = [{"slug": d.get("slug"), **d["two_arm"]}
                              for d in decisions if d.get("two_arm")]
            # Follow every open proposal forward against the tape, THEN
            # report. Statuses stay explicit; would_post is never a
            # completion.
            if sb.execution is not None:
                rep["execution"] = sb.execution.advance(
                    {k: list(v) for k, v in sb.obs.items()})
                rep["execution_summary"] = sb.execution.summary()
                rep["execution_predictors"] = sb.execution.predictors_report()
                props = list(sb.execution.proposals.values())
                rep["execution_cohorts"] = exec_shadow.by_cohort(props)
                rep["execution_cohorts_by_sport"] = exec_shadow.by_cohort(
                    props, key="sport")
                rep["execution_reachability"] = \
                    exec_shadow.reachability_report(props)
                rep["execution_vs_history"] = \
                    exec_shadow.compare_to_history(props)
                rep["execution_completion_time"] = \
                    exec_shadow.completion_time_report(props)
                rep["execution_reach_buckets_by_sport"] = \
                    exec_shadow.reachability_buckets(props, key="sport")
                rep["execution_reach_buckets_by_type"] = \
                    exec_shadow.reachability_buckets(props, key="market_type")
                rep["execution_distance_bands"] = \
                    exec_shadow.distance_band_report(props)
                # SELECTOR OPPORTUNITY LOSS, shadow only. One row per eligible
                # market carrying a measured initial gap, at this instant.
                gaps = {p["slug"]: (p.get("reachability") or {}).get(
                    "initial_gap_ticks") for p in props}
                snap = [{"slug": r["slug"],
                         "flow_usd_per_min": r.get("flow_usd_per_min"),
                         "initial_gap_ticks": gaps.get(r["slug"]),
                         "sport": exec_shadow.sport_of(r["slug"]),
                         "market_type": exec_shadow.market_type(r["slug"])}
                        for r in ranked]
                sb.execution.observe_selector(snap)
                resolved_ops = exec_shadow.resolve_opportunities(
                    sb.execution.opportunities, props)
                rep["execution_opportunity_loss"] = \
                    exec_shadow.opportunity_loss_report(resolved_ops)
                rep["execution_stratified_distance"] = \
                    exec_shadow.stratified_distance(props)
                rep["execution_persisted"] = sb.execution.save()
                # PER-PROPOSAL LIFECYCLE ROWS (owner 2026-09-11). The reports
                # above are service-computed AGGREGATES and an aggregate
                # cannot be re-cut; the pre-registered gate needs residual
                # size, side, cost basis and the market's resolution joined on
                # ONE row. This adds no measurement -- it flattens what the
                # shadow already holds into something that survives the
                # process. Emitted once per (id, status) transition, so a
                # 70-second cycle cannot bury them in duplicates.
                try:
                    rep["proposal_rows_emitted"] = plog.emit_new(
                        props, sb.proposal_rows_seen)
                except Exception as exc:                    # noqa: BLE001
                    # an emission that cannot run must never stop the shadow
                    rep["proposal_rows_error"] = repr(exc)[:200]
            # ITEM 5: every raw state needed to REPLAY this later. The
            # derived reports above can all be recomputed from proposals +
            # observations + decisions; none of them is the evidence. Twice
            # tonight a derived field was wrong and the aggregates built on it
            # were only recoverable because the inputs survived.
            rep["raw_persisted"] = sb.append_raw(
                n, [by_slug[k] for k in by_slug], decisions,
                proposals=(list(sb.execution.proposals.values())
                           if sb.execution is not None else None),
                opportunities=(sb.execution.opportunities
                               if sb.execution is not None else None))
            print(PREFIX + json.dumps(rep, separators=(",", ":"), default=str),
                  flush=True)
            watch = sb.watch_set() or watch
        except Exception as exc:                                # noqa: BLE001
            print(PREFIX + json.dumps(
                {"kind": "cycle_error", "cycle": n,
                 "error": f"{type(exc).__name__}:{str(exc)[:160]}"}),
                flush=True)
        time.sleep(CYCLE_S)
    return 0


if __name__ == "__main__":                                      # pragma: no cover
    raise SystemExit(run())
