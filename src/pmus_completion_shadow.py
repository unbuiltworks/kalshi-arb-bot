"""24/7 non-trading shadow: completion economics on the live sports universe.

OWNER (#167, after the accidental armed run of 2026-09-09): "I want the
current strategy running against the full live sports universe and recording
enough information to answer the economic question we just uncovered. In
particular, every proposed COMPLETE should record the complement price,
marginal pair cost, current inventory-weighted pair cost, resulting pair cost
if filled, residual before/after, and whether pair_cost_max would permit it."

PHYSICALLY NON-TRADING
----------------------
This is not a disarmed trader; it is a process with no way to trade. It
imports no order transport, holds no credentials, and issues GET only
against the public gateway. Tests assert both by parsing this module's AST
with docstrings stripped, so prose describing the rule can never satisfy a
check for it. Nothing here places, previews, cancels or prices a live order.

THE QUESTION IT EXISTS TO ANSWER
---------------------------------
On 2026-09-09 an armed session paired a residual to zero at an
inventory-weighted pair cost of 1.0527 -- $17.79 spent for 16.9 pairs that
settle at 16.90, a realized -0.89 -- while the rung advertised
`pair_cost_max: 0.98`. Reading the completion pricer settles why, and this
module records the evidence forward so the reading can be checked against
live data rather than trusted:

  * Under `complement_pricing: "l5_touch"` -- the R5F setting -- the pricer
    returns `"pair_cost_max": None` and labels the row "L5 fidelity: touch
    pricing, pair reported not gated". The 0.98 travels as
    `observation_cap`, REPORTED. It is not a gate. That was a deliberate
    owner decision (5574762432, "above-par pair cost is
    reported/accounted, not behaviorally gated"; 5577710815 approved
    removing the band that had refused L5's touch completion three times
    and stranded -3.42 on tomvoj-ticjir). The only bounds left under
    l5_touch are the venue's own price range.

  * Under the `fee_safe` regimes 0.98 DOES gate, but as a MARGINAL bound:
    the complement price is capped at `ceiling - surplus_avg`, which bounds
    the pair formed by THAT action against the running average. It was
    never an inventory-weighted bound on the finished book.

So the answer to "marginal, mis-scoped, bypassed, or never designed to
constrain totals?" is: **marginal where it binds at all, and not binding at
all in the regime that produced 1.0527**. This module measures both
readings on live data instead of asserting them.

GOOD COMPLETION vs BAD COMPLETION
----------------------------------
The distinction the owner drew is exactly what the emitted row supports:

  good  -- buying the complement converts inventory into pairs whose
           economics stay acceptable (resulting weighted pair cost <= 1.00)
  bad   -- buying the complement removes directional exposure but locks in
           a structurally losing book (resulting weighted pair cost > 1.00)

Both are recorded with the numbers that separate them. This module proposes
NO rule and changes NO gate; it produces the evidence a rule would need.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

GATEWAY = "https://gateway.polymarket.us"
CA_ENV = "PMUS_CA_BUNDLE"
BBO_PATH = "/v1/markets/%s/bbo"
STATE_ENV = "COMPLETION_SHADOW_STATE_DIR"
DEFAULT_STATE = "/data/completion_shadow"
# OWNER GO: 5 s so the 0-30 s bucket is resolvable. The market count falls
# out of the rate budget, it is not a free choice: at BUDGET_RPS 3.0 with
# ~0.5 rps overhead, one bbo call per market per cycle allows
# floor(2.5 * 5) = 12 markets. Polling 60 markets every 5 s would be 12 rps
# and we rate-limited ourselves once already this campaign.
DEFAULT_POLL_S = 5.0
DEFAULT_MAX_MARKETS = 12
BUDGET_RPS = 3.0
# Rotation means a market goes unpolled for a whole pinned phase (~275 s) and
# then gets read again. First-differencing across that gap is arithmetically
# fine and measures the wrong thing: it is a several-minute average landing in
# the same field as 5 s observations, and because flow_summary weights by
# elapsed time one such interval outweighs every contemporaneous one. So an
# over-long interval is a GAP, on the same principle as every other gap here
# -- absence of sampling is not a measurement. At the 5 s poll this allows six
# consecutive missed cycles before the tape is declared broken.
MAX_FLOW_INTERVAL_S = 30.0
# Ranking wants RECENT activity, not a market's whole history. Deep retention
# would let a burst from three rotations ago pin a market that has since gone
# quiet -- stale, though never look-ahead, since all of it precedes the pin.
FLOW_HISTORY = 40

ONE = D("1")
PAR = D("1.00")
OBSERVATION_CAP = D("0.98")          # SPRING_PAIR_TARGET
HYPOTHETICAL_QTY = D("5")            # a nominal action size; never ordered

READ_ONLY = True
PLACES_ORDERS = False
REQUIRES_CREDENTIALS = False

LABELS = {
    "hypothetical": "no order exists; the first leg and the complement are "
                    "modelled at the observed touch to price the decision",
    "measured": "book prices are the venue's own, read GET-only",
    "not_a_rule": "this records economics; it proposes no gate and changes none",
}


def _ssl_context() -> ssl.SSLContext:
    ca = os.getenv(CA_ENV, "").strip()
    return ssl.create_default_context(cafile=ca) if ca \
        else ssl.create_default_context()


def http_get_json(path: str, timeout: float = 15.0) -> Any:
    """GET only. There is deliberately no write helper in this module."""
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"User-Agent": "pmus-completion-shadow"})
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=timeout) as r:
        return json.loads(r.read())


def _money(x: Any) -> Optional[D]:
    if isinstance(x, Mapping):
        x = x.get("value")
    if x in (None, ""):
        return None
    try:
        return D(str(x))
    except ArithmeticError:
        return None


def book_of(payload: Mapping[str, Any]) -> Dict[str, Any]:
    md = payload.get("marketData") or {}
    return {"slug": md.get("marketSlug"), "state": md.get("state"),
            "bid": _money(md.get("bestBid")), "ask": _money(md.get("bestAsk")),
            "bid_depth": md.get("bidDepth"), "ask_depth": md.get("askDepth"),
            "shares_traded": _money(md.get("sharesTraded")),
            # side_hint needs this; without it two_sided is silently ALWAYS
            # false and the whole flow classification is dead on arrival
            "last_trade_px": _money(md.get("lastTradePx"))}


# ------------------------------ the economics ------------------------------

def completion_economics(surplus_qty: D, surplus_spend: D, matched_qty: D,
                         matched_spend: D, complement_px: D,
                         fill_qty: D, regime: str) -> Dict[str, Any]:
    """Every number the owner asked a proposed COMPLETE to carry.

    `marginal` is the pair this ONE action forms against the surplus side's
    running average -- the quantity the fee-safe ceiling bounds when it
    bounds anything. `resulting` is the inventory-weighted pair cost of the
    WHOLE book after the fill, which is the quantity that actually decides
    whether the finished position pays. The 2026-09-09 run showed these two
    can diverge: every marginal pair can look acceptable while the weighted
    total lands above par.
    """
    surplus_avg = (surplus_spend / surplus_qty) if surplus_qty > 0 else None
    before_pairs = matched_qty
    before_cost = (matched_spend / before_pairs) if before_pairs > 0 else None
    take = min(fill_qty, surplus_qty) if surplus_qty > 0 else D(0)
    marginal = (surplus_avg + complement_px) if surplus_avg is not None else None
    after_pairs = before_pairs + take
    after_spend = matched_spend + take * ((surplus_avg or D(0)) + complement_px)
    after_cost = (after_spend / after_pairs) if after_pairs > 0 else None
    gated = regime != "l5_touch"
    return {
        "complement_px": str(complement_px),
        "fill_qty_modelled": str(take),
        "surplus_avg_cost": str(surplus_avg) if surplus_avg is not None else None,
        "marginal_pair_cost": str(marginal) if marginal is not None else None,
        "inventory_weighted_pair_cost_before": (
            str(before_cost) if before_cost is not None else None),
        "resulting_pair_cost_if_filled": (
            str(after_cost) if after_cost is not None else None),
        "residual_before": str(surplus_qty),
        "residual_after": str(surplus_qty - take),
        "regime": regime,
        "pair_cost_max_gates_this_regime": gated,
        "pair_cost_max_permits": (
            None if not gated else
            (marginal is not None and marginal <= OBSERVATION_CAP)),
        "observation_cap": str(OBSERVATION_CAP),
        "above_par_marginal": bool(marginal is not None and marginal > PAR),
        "above_par_resulting": bool(after_cost is not None and after_cost > PAR),
        "classification": classify(after_cost),
    }


def advance_path(st: Dict[str, Any], complement_px: D, tranche: D) -> None:
    """Complete a tranche at the price the book is showing NOW.

    This is the failure mode the entry pair cannot show. The 2026-09-09 run
    did not lose on a bad first completion; it completed repeatedly as the
    market moved, and the inventory-weighted average degraded to 1.0527
    while each individual action still looked defensible.

    No fill is invented: a tranche is only ever priced at a complement the
    venue was actually quoting on that cycle, and the row is labelled
    modelled throughout. Nothing here is realized P&L and it is never
    reported as such.
    """
    take = min(tranche, st["qty"])
    if take <= 0:
        return
    surplus_avg = st["spend"] / st["qty"]
    st["matched"] += take
    st["matched_spend"] += take * (surplus_avg + complement_px)
    st["spend"] -= take * surplus_avg
    st["qty"] -= take
    st["tranches"] = int(st.get("tranches", 0)) + 1


def classify(resulting: Optional[D]) -> str:
    """The owner's two cases, decided on the finished book, not the margin."""
    if resulting is None:
        return "no_pairs"
    if resulting <= PAR:
        return "good_completion:pairs_remain_acceptable"
    return "bad_completion:crystallises_a_losing_book"


def complement_touch(book: Mapping[str, Any], surplus_side: str) -> Optional[D]:
    """Price of the side that PAIRS the surplus, at its own touch.

    The book is quoted in YES terms, and which side the complement is
    decides which quote prices it. Getting this backwards is not a rounding
    error: on a 0.90/0.94 book a NO surplus at 0.06 pairs with YES at the
    0.90 bid for 0.96, but reading the complement as 1-ask gives 0.06 and a
    "pair" of 0.12, which is not a pair at all.

        surplus YES  ->  complement NO,  bought at the NO touch = 1 - yes_ask
        surplus NO   ->  complement YES, bought at the YES touch = yes_bid
    """
    bid, ask = book.get("bid"), book.get("ask")
    if bid is None or ask is None:
        return None
    return (ONE - ask) if surplus_side == "YES" else bid


def propose(book: Mapping[str, Any], surplus_qty: D, surplus_spend: D,
            matched_qty: D, matched_spend: D, surplus_side: str = "YES",
            regime: str = "l5_touch") -> Optional[Dict[str, Any]]:
    """Model the COMPLETE this book would offer against held inventory.

    No order is created; the complement is priced at the touch that would
    fill it.
    """
    bid, ask = book.get("bid"), book.get("ask")
    if bid is None or ask is None or ask < bid:
        return None
    if surplus_qty <= 0:
        return None
    complement_px = complement_touch(book, surplus_side)
    if complement_px is None or complement_px <= 0 or complement_px >= ONE:
        return None
    row = completion_economics(surplus_qty, surplus_spend, matched_qty,
                               matched_spend, complement_px,
                               HYPOTHETICAL_QTY, regime)
    row.update({"slug": book.get("slug"), "yes_bid": str(bid),
                "yes_ask": str(ask), "market_state": book.get("state"),
                "surplus_side": surplus_side,
                "complement_side": "NO" if surplus_side == "YES" else "YES"})
    return row


# ------------------------------ the loop ------------------------------

def flow_delta(prev: Optional[Mapping[str, Any]],
               cur: Mapping[str, Any], now: float) -> Dict[str, Any]:
    """Contemporaneous flow from the SAME poll that prices the completion.

    The two variables the admission rule needs -- does this market transact
    on both sides, and what does completing in it cost -- have to be measured
    on the same market at the same moment or they cannot be crossed. A
    separate collector cannot guarantee that, and a local one is not durable:
    the first flow collector ran as a background process in an ephemeral
    container and died silently after 41 cycles while a `pgrep` that matched
    its own command line reported it healthy. Same poll, same service, no
    liveness claim that isn't a written row.

    A gap is not zero flow and is never folded into a rate.
    """
    if prev is None:
        return {"gap": True, "why": "first_sample"}
    p, c = prev.get("shares_traded"), cur.get("shares_traded")
    if p is None or c is None:
        return {"gap": True, "why": "shares_traded_unreadable"}
    if c < p:
        return {"gap": True, "why": "counter_went_backwards"}
    dt = now - float(prev.get("t", now))
    if dt <= 0:
        return {"gap": True, "why": "non_positive_interval"}
    if dt > MAX_FLOW_INTERVAL_S:
        return {"gap": True, "why": "sampling_gap", "interval_s": round(dt, 3)}
    d = c - p
    return {"gap": False, "shares": d, "interval_s": round(dt, 3),
            "printed": d > 0, "shares_per_min": float(d) * 60.0 / dt,
            "side_hint": side_hint(cur)}


def side_hint(sample: Mapping[str, Any]) -> Optional[str]:
    """Where the last print sat. INFERENCE -- the venue does not say."""
    px, b, a = sample.get("last_trade_px"), sample.get("bid"), sample.get("ask")
    if px is None or b is None or a is None or a < b:
        return None
    return "at_bid" if px <= b else ("at_ask" if px >= a else "inside")


# Deciding two-sidedness needs a print on each side, so it cannot be decided
# at all below two side-carrying prints. Measured 2026-09-09 on the first live
# pin: every one of 18 scored markets came back two_sided false off 1 print in
# 4 intervals -- not a fact about the venue, a term that structurally could
# not vary, multiplying the whole ranking by the same constant. Same shape as
# every other silent failure this campaign has found.
MIN_PRINTS_TO_DECIDE_TWO_SIDED = 2


def flow_summary(deltas: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    good = [d for d in deltas if not d.get("gap")]
    if not good:
        return {"intervals": 0, "gaps": len(deltas), "prints_seen": 0,
                "shares_per_min": None, "two_sided": False,
                "two_sided_resolvable": False, "sided_prints": 0}
    obs = sum(d["interval_s"] for d in good)
    shares = sum((d["shares"] for d in good), D(0))
    printed = [d for d in good if d["printed"]]
    sided = [d for d in printed if d.get("side_hint")]
    sides = {d["side_hint"] for d in sided}
    return {"intervals": len(good), "gaps": len(deltas) - len(good),
            "prints_seen": len(printed),
            "shares_per_min": round(float(shares) * 60.0 / obs, 4) if obs else None,
            "two_sided": ("at_bid" in sides and "at_ask" in sides),
            # UNRESOLVED is not one-sided. A market with one print has not
            # shown us a second side; it has not shown us anything.
            "two_sided_resolvable": len(sided) >= MIN_PRINTS_TO_DECIDE_TWO_SIDED,
            "sided_prints": len(sided)}


def _pw():
    from src import pmus_persistence_window as pw
    return pw


def state_dir() -> str:
    return os.getenv(STATE_ENV, DEFAULT_STATE)


def heartbeat(cycle: int, markets: int, rows: int, started: float,
              out_dir: Optional[str] = None) -> Dict[str, Any]:
    hb = {"kind": "heartbeat", "cycle": cycle, "markets": markets,
          "completion_rows": rows, "uptime_s": round(time.time() - started, 1),
          "t": time.time(), "read_only": READ_ONLY,
          "places_orders": PLACES_ORDERS}
    d = out_dir or state_dir()
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "heartbeat.json"), "w", encoding="utf-8") as f:
            json.dump(hb, f)
    except OSError as e:
        hb["heartbeat_write_failed"] = type(e).__name__
    return hb


def discover(limit: Optional[int] = None, getter=None) -> List[str]:
    """The venue's own live set, admitted on its `live` flag alone.

    `limit=None` returns the WIDE universe. Taking the first N here was the
    flow-blind selection: at a 5 s poll the rate budget affords 12 markets,
    and cutting the venue's list at 12 handed the persistence experiment ten
    county cricket matches. The cut now belongs to `pmus_flow_roster`, which
    makes it on measured activity instead of list order.
    """
    from src.pmus_sports_collector import fetch_live_events, live_event_targets
    getter = getter or http_get_json
    try:
        events, _meta = fetch_live_events(getter)
    except Exception:
        events = []
    out: List[str] = []
    for t in live_event_targets(events):
        if t["slug"] not in out:
            out.append(t["slug"])
    return out if limit is None else out[:limit]


def cycle(slugs: Sequence[str], inv: Dict[str, Dict[str, Any]],
          getter=http_get_json, now: Optional[float] = None,
          tranche: D = D("1"), roster: Any = None) -> List[Dict[str, Any]]:
    """One pass. Inventory is SIMULATED: a market with no modelled surplus
    seeds one at the cheaper touch, so the completion decision has something
    to price. Nothing is bought."""
    rows: List[Dict[str, Any]] = []
    now = time.time() if now is None else now
    for slug in slugs:
        try:
            book = book_of(getter(BBO_PATH % slug))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
                TimeoutError, OSError):
            continue
        bid, ask = book.get("bid"), book.get("ask")
        if bid is None or ask is None:
            continue
        st = inv.setdefault(slug, {"qty": D(0), "spend": D(0),
                                   "matched": D(0), "matched_spend": D(0),
                                   "side": "YES", "flow": [], "prev": None,
                                   "tranches": 0})
        st["flow"].append(flow_delta(st.get("prev"), book, now))
        st["flow"] = st["flow"][-FLOW_HISTORY:]
        st["prev"] = dict(book, t=now)
        fs = flow_summary(st["flow"])
        if st["qty"] <= 0:
            # RE-SEED. The hypothetical book completes in a handful of
            # tranches; without this the market stops being proposed on and
            # therefore stops being watched, and flow never accumulates on
            # the markets the study is about. Measured 2026-09-09: every
            # market went silent after 5 rows, no row ever reached more than
            # 4 flow intervals, and two-sidedness -- which needs both an
            # at_bid and an at_ask print -- could not be classified at all.
            # A null result from that window would have been an artifact of
            # this loop, not a fact about the venue.
            st["matched"], st["matched_spend"] = D(0), D(0)
            st["sessions"] = int(st.get("sessions", 0)) + 1
            st["tranches"] = 0
        if st["qty"] <= 0:
            # seed a hypothetical first leg at the cheaper touch, and REMEMBER
            # which side it took -- the complement is priced off that
            no_touch = ONE - ask
            if bid <= no_touch:
                st["side"], px = "YES", bid
            else:
                st["side"], px = "NO", no_touch
            st["qty"], st["spend"] = HYPOTHETICAL_QTY, HYPOTHETICAL_QTY * px
        # OWNER GO: watch the 30-60s interval we never operated in. The
        # window is deliberately INDEPENDENT of the hypothetical inventory
        # lifecycle: the surplus completes in a handful of tranches and
        # re-seeds, and tying the window to that reset it every ~5 cycles so
        # it never survived to 120s and the 30-60s bucket stayed empty.
        # ...and only on a market the roster PINNED on flow measured before
        # this window opened. Without that gate the experiment observes
        # whatever the venue happens to list first, which is how the 5 s run
        # produced 4 opportunities in 2,136 observations.
        may_observe = roster is None or roster.eligible_for_window(slug)
        if st.get("window") is None and may_observe:
            cside = "NO" if st["side"] == "YES" else "YES"
            cpx = _pw().complement_price_now(book, cside)
            if cpx is not None:
                st["window"] = _pw().open_window(now, cpx, cside, slug)
        w = st.get("window")
        if w and not w.get("closed"):
            d = st["flow"][-1] if st["flow"] else {}
            st["window"] = _pw().observe(
                w, book, now, bool(d.get("printed")),
                d.get("shares") or D(0))
            if st["window"].get("closed"):
                summary = _pw().summarise(st["window"])
                # PROVENANCE. The log carries windows from BOTH selection
                # regimes -- the flow-blind slate ran until 14:54Z today --
                # and aggregating across them would answer the persistence
                # question partly on markets chosen by list order. The
                # reader refuses to mix these, so the row has to say which
                # regime produced it and on what pre-window score.
                summary["selection"] = ("flow_ranked" if roster is not None
                                        else "unranked")
                if roster is not None:
                    summary["roster_pin"] = roster.pins
                    summary["pre_window_flow_score"] = \
                        roster.pinned_scores.get(slug)
                print("PMUS_PERSISTENCE_WINDOW " + json.dumps(
                    summary, default=str), flush=True)
                st["window"] = None
        row = propose(book, st["qty"], st["spend"], st["matched"],
                      st["matched_spend"], surplus_side=str(st["side"]))
        if row:
            row["flow"] = fs
            row["tranches_completed"] = int(st.get("tranches", 0))
            row["sessions"] = int(st.get("sessions", 0))
            row["path_modelled"] = True
            rows.append(row)
            cpx = _money(row["complement_px"])
            if cpx is not None:
                advance_path(st, cpx, tranche)
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=0, help="0 = forever")
    ap.add_argument("--poll-s", type=float, default=DEFAULT_POLL_S)
    ap.add_argument("--max-markets", type=int, default=DEFAULT_MAX_MARKETS)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    started = time.time()
    inv: Dict[str, Dict[str, D]] = {}
    total = 0
    from src.pmus_persistence_window import cadence_plan
    from src.pmus_flow_roster import Roster, rotation_plan
    roster = Roster(roster_size=a.max_markets)
    print(json.dumps({"kind": "shadow_start", "read_only": READ_ONLY,
                      "cadence_plan": cadence_plan(a.poll_s),
                      "selection": "flow_ranked_rotation",
                      "roster": roster.status(),
                      "places_orders": PLACES_ORDERS,
                      "requires_credentials": REQUIRES_CREDENTIALS,
                      "state_dir": state_dir(),
                      "observation_cap": str(OBSERVATION_CAP),
                      "labels": LABELS}), flush=True)
    c = 0
    last_start = None
    while True:
        c += 1
        cycle_started = time.time()
        measured_cadence = (None if last_start is None
                            else round(cycle_started - last_start, 3))
        last_start = cycle_started
        try:
            # Wide discovery happens once per survey pass, not once per
            # cycle: the universe is the thing being ranked, and re-cutting
            # it every poll would keep re-deciding the experiment's slate.
            if roster.needs_universe():
                roster.set_universe(discover())
                # The real cost of one rotation, stated once the universe is
                # actually known rather than assumed. The survey is the price
                # of not being flow-blind and it is not small; the duty cycle
                # belongs in the log, not in a reader's arithmetic.
                print("PMUS_FLOW_ROSTER " + json.dumps(
                    {"kind": "roster_universe",
                     "plan": rotation_plan(len(roster.universe),
                                           a.max_markets, a.poll_s,
                                           roster.pin_cycles)}), flush=True)
            slugs = roster.current()
            rows = cycle(slugs, inv, roster=roster)
            # Rank on flow measured BEFORE any pinned window opens. `advance`
            # reads this only at the end of a survey pass and ignores it
            # entirely while pinned, so a window can never feed its own
            # selection.
            transition = roster.advance(
                {s: flow_summary((inv.get(s) or {}).get("flow") or [])
                 for s in roster.universe})
            if transition:
                print("PMUS_FLOW_ROSTER " + json.dumps(transition,
                                                       default=str), flush=True)
        except Exception as e:                       # never die on one cycle
            print(json.dumps({"kind": "cycle_error", "cycle": c,
                              "error": type(e).__name__}), flush=True)
            slugs, rows = [], []
        total += len(rows)
        for r in rows:
            print("PMUS_COMPLETION_SHADOW " + json.dumps(r, default=str),
                  flush=True)
        hb = heartbeat(c, len(slugs), total, started)
        # The cadence we ACTUALLY ran at, not the one we asked for.
        hb["measured_cadence_s"] = measured_cadence
        hb["roster_phase"] = roster.phase
        hb["roster_pins"] = roster.pins
        hb["universe"] = len(roster.universe)
        print("PMUS_COMPLETION_SHADOW " + json.dumps(hb, default=str),
              flush=True)
        if a.once or (a.cycles and c >= a.cycles):
            return 0
        # Sleep the REMAINDER of the interval, not the whole of it. Sleeping
        # poll_s after the work makes the true cadence poll_s + work, and the
        # work is 12 sequential HTTP round trips. Measured on the first live
        # rotation: a nominal 5 s poll ran at ~7.5 s, and every 30 s bucket
        # got 4 observations where cadence_plan promised 6. Still resolvable,
        # but the cadence the experiment is designed around has to be the
        # cadence it runs at, or the plan is fiction. This also holds the
        # request rate at the 2.4 rps the 12-market slate was derived from.
        time.sleep(max(0.0, a.poll_s - (time.time() - cycle_started)))


if __name__ == "__main__":
    sys.exit(main())
