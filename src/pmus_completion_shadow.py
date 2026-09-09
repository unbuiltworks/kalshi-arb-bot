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
DEFAULT_POLL_S = 30.0
DEFAULT_MAX_MARKETS = 60
BUDGET_RPS = 3.0

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
            "shares_traded": _money(md.get("sharesTraded"))}


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


def discover(limit: int, getter=None) -> List[str]:
    """The venue's own live set, admitted on its `live` flag alone."""
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
    return out[:limit]


def cycle(slugs: Sequence[str], inv: Dict[str, Dict[str, D]],
          getter=http_get_json) -> List[Dict[str, Any]]:
    """One pass. Inventory is SIMULATED: a market with no modelled surplus
    seeds one at the cheaper touch, so the completion decision has something
    to price. Nothing is bought."""
    rows: List[Dict[str, Any]] = []
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
                                   "side": "YES"})
        if st["qty"] <= 0 and st["matched"] <= 0:
            # seed a hypothetical first leg at the cheaper touch, and REMEMBER
            # which side it took -- the complement is priced off that
            no_touch = ONE - ask
            if bid <= no_touch:
                st["side"], px = "YES", bid
            else:
                st["side"], px = "NO", no_touch
            st["qty"], st["spend"] = HYPOTHETICAL_QTY, HYPOTHETICAL_QTY * px
        row = propose(book, st["qty"], st["spend"], st["matched"],
                      st["matched_spend"], surplus_side=str(st["side"]))
        if row:
            rows.append(row)
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
    print(json.dumps({"kind": "shadow_start", "read_only": READ_ONLY,
                      "places_orders": PLACES_ORDERS,
                      "requires_credentials": REQUIRES_CREDENTIALS,
                      "state_dir": state_dir(),
                      "observation_cap": str(OBSERVATION_CAP),
                      "labels": LABELS}), flush=True)
    c = 0
    while True:
        c += 1
        try:
            slugs = discover(a.max_markets)
            rows = cycle(slugs, inv)
        except Exception as e:                       # never die on one cycle
            print(json.dumps({"kind": "cycle_error", "cycle": c,
                              "error": type(e).__name__}), flush=True)
            slugs, rows = [], []
        total += len(rows)
        for r in rows:
            print("PMUS_COMPLETION_SHADOW " + json.dumps(r, default=str),
                  flush=True)
        print("PMUS_COMPLETION_SHADOW " + json.dumps(
            heartbeat(c, len(slugs), total, started), default=str), flush=True)
        if a.once or (a.cycles and c >= a.cycles):
            return 0
        time.sleep(a.poll_s)


if __name__ == "__main__":
    sys.exit(main())
