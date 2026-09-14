"""Continuous-inventory counterfactual on complete Gemini 5m windows.

THE QUESTION, unchanged: does Gemini 5m actually let the UNCHANGED canonical
L5 engine cycle inventory the way L5 does? Gemini 5m scores a habitat of
0.4061 against PMUS's 0.108, yet the unchanged engine reached at most 1
matched campaign in 29 -- good habitat with poor capture, which points at
execution rather than at the venue.

UNCHANGED CANONICAL POLICY. Every decision goes through
`l5_venue_adapter.proposal_v2` -> `pmus_l5_persistence_v2`, the same lookup
the live PMUS probe executes, on a `GeminiAdapter`-normalised book. Nothing
is refit for Gemini. Prices, bands, the residual cap, post-only, the
fee-safe complement bound and maker-only all bind exactly as they do live.

THE ONE THING THAT IS DIFFERENT, and it is the whole experiment:

    THE UNIT OF PERSISTENCE IS THE INVENTORY INTENTION, NOT THE ORDER.
    A TOUCH MOVE TRIGGERS A FRESH EVALUATION AT THE NEW TOUCH. IT DOES NOT
    TERMINATE THE INTENTION.

This is NOT "never cancel". The order IS cancelled when the touch moves and
a new one is placed at the new touch; post-only still binds; every gate is
re-checked against the NEW state, not carried over; and an intention still
ends when the policy says hold, when the complement completes it, when the
residual cap binds, or when the window runs out. What changes is only that
the touch moving is no longer read as the venue refusing the intention.
Canonical L5's own tape is what motivates this: 73.4% of its same-side
continuations happen at a NEW price, one tick away, a median of 1 s later.

TWO ACTION COUNTERS, REPORTED SIDE BY SIDE, NEVER SUMMED:

  L5_BEHAVIORAL_ACTION    a decision that sets or changes the inventory
                          intention (open / extend / complete / hold). This
                          is the unit the canonical L5 run statistics count.
  VENUE_ADMISSIBLE_ACTION every order the venue would actually see, requotes
                          included. This is what an order-rate limit or an
                          order-count cap binds on.

THREE FILL TIERS, KEPT SEPARATE AND NEVER MIXED. All three require an actual
PRINT. A quote touching our price is NEVER a fill -- queue position is
UNOBSERVABLE from public data and stays UNKNOWN in every tier.

  STRICT      a print strictly THROUGH our resting price. The book traded
              past our level, so we were reached whatever our queue
              position. The only tier that needs no queue assumption.
  SUPPORTED   a print AT our price, where the cumulative size traded at or
              through our price since we joined EXCEEDS the size displayed
              at that touch when we joined. Even behind the entire VISIBLE
              queue we would have been reached. Weaker than STRICT: queue
              ahead of us can refresh or be hidden, and that is unobservable.
  OPTIMISTIC  any print AT our price. This assumes front of queue and is an
              explicit UPPER BOUND, not an estimate.

Nothing here is realized, submitted, or armed. No Gemini order is placed.
"""
from __future__ import annotations

import json
import statistics
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src import pmus_l5_persistence_v2 as pv2
from src.l5_venue_adapter import (GeminiAdapter, L5Inventory, open_side_yes,
                                  proposal_v2 as _proposal_v2)

ONE = D("1")
UNIT = D("1")                    # q=1 behavioral unit, as live


class Venue:
    """Adapter + its own normalize signature, bundled.

    PMUSAdapter.normalize(raw, now, settlement_t) and
    GeminiAdapter.normalize(raw, now=None, expiry=None) differ, and neither
    is changed to suit this module. The shim exists so ONE shadow runs on
    BOTH venues -- if each venue had its own replay, every difference between
    the cohorts would be partly an artefact of which replay measured it.
    """

    def __init__(self, adapter: Any, kind: str,
                 settlement_clock: bool = True):
        self.adapter, self.kind = adapter, kind
        self.venue = getattr(adapter, "venue", kind)
        # PMUS SPORTS HAVE NO SETTLEMENT CLOCK. PMUSAdapter falls back to
        # SPORTS_TTE_DEFAULT_S (1e9, the coarsest TTE bucket) when no
        # settlement_t is given, and that is what the LIVE engine does. The
        # shadow must reproduce it rather than hand the policy a day-boundary
        # TTE the live engine never sees -- a different TTE bucket is a
        # different policy state, so that would be a behavioural change
        # dressed as a data fix.
        self.settlement_clock = settlement_clock

    def book(self, raw: Mapping[str, Any], expiry: float):
        if raw is None:
            return None
        if self.kind == "GEMINI":
            return self.adapter.normalize(raw, expiry=expiry)
        return self.adapter.normalize(
            raw, raw.get("t"),
            settlement_t=expiry if self.settlement_clock else None)

    def __getattr__(self, name):
        return getattr(self.adapter, name)


def gemini_venue() -> "Venue":
    return Venue(GeminiAdapter(), "GEMINI")


def pmus_venue(settlement_clock: bool = True) -> "Venue":
    from src.l5_venue_adapter import PMUSAdapter
    return Venue(PMUSAdapter(), "PMUS", settlement_clock=settlement_clock)


def pmus_sports_venue() -> "Venue":
    """As the LIVE engine sees a sports market: no settlement clock, so the
    TTE bucket is the coarsest one."""
    return pmus_venue(settlement_clock=False)
MIN_REEVAL_S = 1.0               # canonical L5 continues at a ~1 s cadence

# The LIVE R5FT rung's residual cap. The l5_venue_adapter module default is
# 3; production runs 6 via env. The live value is used here and stated, so
# the counterfactual is comparable to the engine that actually runs.
LIVE_RESIDUAL_CAP_UNITS = 6

# Research ceiling. Deliberately NOT the live MAX_ACTIONS=6, which covers
# only the first seconds of a 300 s window and is the bound under test.
RESEARCH_ACTION_CEILING = 400

TIERS = ("STRICT", "SUPPORTED", "OPTIMISTIC")

LABELS = ("THEORETICAL continuous-inventory counterfactual on public Gemini "
          "data; zero orders; fills require a PRINT and queue position is "
          "UNKNOWN in every tier; settlement by mid proxy; nothing realized")


# ------------------------------------------------------------------ books

def book_rows(win: Mapping[str, Any]) -> List[dict]:
    """Digest series -> adapter-shaped rows, chronological."""
    exp = float(win["expiry_t"])
    out = []
    for o in win.get("series") or []:
        out.append({"symbol": win["market"], "slug": win["market"],
                    "market_id": win["market"], "t": exp - float(o[0]),
                    "bid": o[1], "ask": o[2],
                    "bid_sz": o[3], "ask_sz": o[4],
                    # PMUSAdapter reads *_qty; GeminiAdapter reads *_sz.
                    # Both keys are supplied so neither adapter is modified.
                    "bid_qty": o[3], "ask_qty": o[4],
                    "tick": win.get("tick_size")})
    out.sort(key=lambda r: r["t"])
    return out


def print_rows(win: Mapping[str, Any]) -> List[dict]:
    """Digest prints -> {t, price, qty, type}. On the UP contract
    buyer_is_maker=True means the AGGRESSOR SOLD."""
    exp = float(win["expiry_t"])
    out = []
    for p in win.get("print_rows") or []:
        out.append({"t": exp - float(p[0]), "price": D(str(p[1])),
                    "qty": D(str(p[2])),
                    "type": "sell" if bool(p[3]) else "buy"})
    out.sort(key=lambda r: r["t"])
    return out


def book_at(rows: List[dict], t: float) -> Optional[dict]:
    """Latest quote at or before t. Never forward-fills past the end."""
    got = None
    for r in rows:
        if r["t"] <= t:
            got = r
        else:
            break
    return got


# ------------------------------------------------------------------ fills

def _hits(p: Mapping[str, Any], side_yes: bool, px: D, strict: bool) -> bool:
    """Would this print have reached a maker resting at `px` on `side_yes`?

    YES rests as a bid at px: a taker SELL at <= px reaches it (< px strictly
    through). NO rests as a YES ask at 1-px: a taker BUY at >= that level
    reaches it (> strictly through)."""
    q = p["price"]
    if side_yes:
        if p["type"] != "sell":
            return False
        return (q < px) if strict else (q <= px)
    lvl = ONE - px
    if p["type"] != "buy":
        return False
    return (q > lvl) if strict else (q >= lvl)


def first_fill(prints: List[dict], side_yes: bool, px: D, t_from: float,
               t_until: float, tier: str, displayed_ahead: D) -> Optional[dict]:
    """First print that fills a maker resting at `px`, under one tier.

    `displayed_ahead` is the size shown AT our touch when we joined -- the
    visible queue we would sit behind. It is used only by SUPPORTED.
    """
    seen = D("0")
    for p in prints:
        if p["t"] <= t_from or p["t"] > t_until:
            continue
        through = _hits(p, side_yes, px, strict=True)
        at_or_through = _hits(p, side_yes, px, strict=False)
        if tier == "STRICT":
            if through:
                return {"t": p["t"], "print_price": str(p["price"]),
                        "basis": "print_through_our_price"}
            continue
        if tier == "OPTIMISTIC":
            if at_or_through:
                return {"t": p["t"], "print_price": str(p["price"]),
                        "basis": "print_at_our_price_front_of_queue_assumed"}
            continue
        # SUPPORTED
        if through:
            return {"t": p["t"], "print_price": str(p["price"]),
                    "basis": "print_through_our_price"}
        if at_or_through:
            seen += p["qty"]
            if seen > displayed_ahead:
                return {"t": p["t"], "print_price": str(p["price"]),
                        "basis": f"cumulative_{seen}_exceeds_displayed_"
                                 f"{displayed_ahead}"}
    return None


# ------------------------------------------------------------------ replay

def _touch_px(adapter: Any, raw: Mapping[str, Any], expiry: float,
              side_yes: bool) -> Tuple[Optional[D], Optional[D]]:
    """(join price, size displayed at that touch) or (None, None)."""
    b = adapter.book(raw, expiry) if isinstance(adapter, Venue) \
        else adapter.normalize(raw, expiry=expiry)
    if b is None or b.yes_bid is None or b.yes_ask is None:
        return None, None
    px = b.join_price(side_yes)
    if px is None or not adapter.entry_ok(px):
        return None, None
    opp = b.opposite_ask(side_yes)
    if opp is None or px >= opp:          # post-only still binds
        return None, None
    sz = b.yes_bid_qty if side_yes else b.yes_ask_qty
    return px, (sz if sz is not None else D("0"))


# Opening-side rules, for the A/B ONLY. "CANONICAL" is the unchanged
# production behaviour (l5_venue_adapter.open_side_yes -- cheaper touch, ties
# to YES) and is the default everywhere. "DEARER" exists solely to test the
# canonical-history finding on identical observed states; it is NOT proposed,
# implemented in production, or used by any default path.
OPEN_SIDE_RULES = ("CANONICAL", "DEARER")


def _open_side(rule: str, book: Any) -> Optional[bool]:
    if rule == "CANONICAL":
        return open_side_yes(book)
    y, n = book.yes_bid, book.no_bid
    if y is None and n is None:
        return None
    if n is None:
        return True
    if y is None:
        return False
    return y > n                       # the DEARER touch, ties to NO


def replay_window(win: Mapping[str, Any], *, model: Mapping[str, Any],
                  tier: str, residual_cap: int = LIVE_RESIDUAL_CAP_UNITS,
                  action_ceiling: int = RESEARCH_ACTION_CEILING,
                  adapter: Optional[Any] = None,
                  open_side_rule: str = "CANONICAL") -> dict:
    adapter = adapter or gemini_venue()
    expiry = float(win["expiry_t"])
    rows = book_rows(win)
    prints = print_rows(win)
    if len(rows) < 2:
        return {"market": win.get("market"), "tier": tier,
                "status": "NO_QUOTE_COVERAGE"}
    inv = L5Inventory()
    t = rows[0]["t"]
    stop_at = rows[-1]["t"]
    behavioral = venue_orders = requotes = 0
    one_sided_waits = 0
    decisions: List[dict] = []
    fills: List[dict] = []
    intentions: List[dict] = []
    stop_reason = None

    while t < stop_at and behavioral < action_ceiling:
        raw = book_at(rows, t)
        if raw is None:
            stop_reason = "no_book"
            break
        book = (adapter.book(raw, expiry) if isinstance(adapter, Venue)
                else adapter.normalize(raw, expiry=expiry))
        if book is None or book.yes_bid is None or book.yes_ask is None:
            one_sided_waits += 1
            t += MIN_REEVAL_S
            continue
        prop = _proposal_v2(model, book, inv)
        state = prop["lookup"]["state"]
        relation = "open" if inv.total == 0 else prop["recommended_relation"]
        refusal = None
        if prop.get("off_distribution") and inv.total > 0:
            relation, refusal = "hold", "off_distribution_hold"
        elif relation == "extend" and abs(inv.behavioral_residual()) >= residual_cap:
            relation, refusal = "hold", (
                f"residual_cap:{abs(inv.behavioral_residual())}>={residual_cap}")
        elif relation == "sell":
            relation, refusal = "hold", "sell_is_noop"

        # PARALLEL LABELS: what L5's policy wants, and what this venue's
        # state would actually admit. They are recorded separately so a
        # venue refusal is never mistaken for L5 declining to act.
        behavioral_action = relation
        decisions.append({"t": t, "tte_s": round(expiry - t, 2),
                          "state": state,
                          "L5_BEHAVIORAL_ACTION": behavioral_action,
                          "VENUE_ADMISSIBLE_ACTION": None,
                          "refusal": refusal,
                          "residual_units": str(inv.behavioral_residual())})
        dec = decisions[-1]
        behavioral += 1

        if relation == "hold":
            dec["VENUE_ADMISSIBLE_ACTION"] = "hold"
            t += max(float(prop["next_reevaluation_s"]), MIN_REEVAL_S)
            continue

        if relation in ("open", "extend"):
            side_yes = (_open_side(open_side_rule, book) if inv.residual == 0
                        else inv.residual > 0)
            if side_yes is None:
                dec["VENUE_ADMISSIBLE_ACTION"] = "refused:no_priceable_side"
                t += MIN_REEVAL_S
                continue
            px, ahead = _touch_px(adapter, raw, expiry, side_yes)
            if px is None:
                dec["VENUE_ADMISSIBLE_ACTION"] = "refused:post_only_or_band"
                t += MIN_REEVAL_S
                continue
            qty = UNIT
        else:                                   # complete: deficit side only
            if inv.residual == 0:
                dec["VENUE_ADMISSIBLE_ACTION"] = "hold"
                stop_reason = "flat_complete"
                break
            side_yes = inv.residual < 0         # buy the DEFICIT side
            surplus_avg = inv.avg_dn() if side_yes else inv.avg_up()
            opp = book.opposite_ask(side_yes)
            if opp is None:
                dec["VENUE_ADMISSIBLE_ACTION"] = "refused:no_opposite_ask"
                t += MIN_REEVAL_S
                continue
            px, econ = adapter.complement_price(surplus_avg, opp, book.tick)
            dec["pair_economics"] = econ
            if px is None:
                dec["VENUE_ADMISSIBLE_ACTION"] = "refused:no_fee_safe_complement"
                t += MIN_REEVAL_S
                continue
            ahead = (book.yes_bid_qty if side_yes else book.yes_ask_qty) or D("0")
            qty = min(inv.residual.copy_abs(), UNIT)

        # ---- the intention rests, and PERSISTS ACROSS TOUCH MOVES ----
        dec["VENUE_ADMISSIBLE_ACTION"] = relation
        t_cur, px_cur, ahead_cur = t, px, ahead
        venue_orders += 1
        local_requotes = 0
        f = None
        seg_until = stop_at
        while True:
            nxt = _next_touch_move(rows, adapter, expiry, side_yes,
                                   px_cur, t_cur, stop_at)
            seg_until = nxt if nxt is not None else stop_at
            f = first_fill(prints, side_yes, px_cur, t_cur, seg_until, tier,
                           ahead_cur)
            if f is not None or nxt is None:
                break
            # THE TOUCH MOVED. Re-evaluate at the new touch; the INTENTION
            # survives. Every gate is re-checked against the NEW book.
            nraw = book_at(rows, nxt)
            npx, nahead = (_touch_px(adapter, nraw, expiry, side_yes)
                           if nraw is not None else (None, None))
            if npx is None:
                break                      # venue no longer admits it
            local_requotes += 1
            venue_orders += 1
            t_cur, px_cur, ahead_cur = nxt, npx, nahead
            if venue_orders > action_ceiling * 4:
                break                      # runaway guard, never expected
        requotes += local_requotes
        intentions.append({"t": t, "relation": relation,
                           "side": "UP" if side_yes else "DOWN",
                           "px_first": str(px), "px_last": str(px_cur),
                           "requotes": local_requotes,
                           "filled": f is not None,
                           "rest_s": round((f["t"] if f else t_cur) - t, 2)})
        if f is not None:
            inv.record(side_yes, px_cur, qty, relation, f["t"])
            fills.append({"t": f["t"], "tte_s": round(expiry - f["t"], 2),
                          "relation": relation,
                          "side": "UP" if side_yes else "DOWN",
                          "px": str(px_cur), "qty": str(qty),
                          "tier": tier, "basis": f["basis"],
                          "requotes_before_fill": local_requotes,
                          "rest_s": round(f["t"] - t, 2)})
            t = f["t"] + MIN_REEVAL_S
        else:
            # THE ORDER IS STILL RESTING. Time advances to where the rest
            # actually ENDED -- the touch move we could not re-join, or the
            # end of the series -- not by one re-evaluation tick.
            #
            # DEFECT THIS FIXES, found by the numbers rather than by reading:
            # advancing by MIN_REEVAL_S re-placed a NEW venue order every
            # second against an order that was already resting unfilled. On a
            # STATIC book (Gemini sports never moved in 8 minutes) that
            # reported 243 intentions and 8,000 venue orders per window for
            # what is really ONE order resting the whole time. It inflated
            # exactly the cohorts that deserved the lowest counts.
            t = max(seg_until, t + MIN_REEVAL_S)

    status = ("MATCHED" if inv.matched > 0 and inv.residual == 0 else
              "RESIDUAL_HOLD" if inv.total > 0 else "NO_FILL")
    return {"market": win.get("market"), "asset": win.get("asset"),
            "tier": tier, "status": status, "stop_reason": stop_reason,
            "residual_cap_units": residual_cap,
            "open_side_rule": open_side_rule,
            "action_ceiling": action_ceiling,
            "action_ceiling_bound": behavioral >= action_ceiling,
            "L5_BEHAVIORAL_ACTIONS": behavioral,
            "VENUE_ADMISSIBLE_ACTIONS": venue_orders,
            "requotes": requotes,
            "one_sided_book_waits": one_sided_waits,
            "intentions": intentions,
            "intentions_n": len(intentions),
            "fills": fills, "fills_n": len(fills),
            "inventory": inv.snapshot(),
            "pair_unit_cost": str(inv.pair_cost()),
            "prints_in_window": len(prints),
            "decisions_n": len(decisions)}


def _next_touch_move(rows: List[dict], adapter: Any, expiry: float,
                     side_yes: bool, px: D, t_from: float,
                     t_until: float) -> Optional[float]:
    """First time in (t_from, t_until] at which our side's touch is no
    longer `px`. A touch MOVE, not a trade."""
    for r in rows:
        if r["t"] <= t_from or r["t"] > t_until:
            continue
        npx, _ = _touch_px(adapter, r, expiry, side_yes)
        if npx is None or npx != px:
            return r["t"]
    return None


def _p50(xs: List[float]) -> Optional[float]:
    return round(statistics.median(xs), 4) if xs else None


def summarize(rows: List[dict]) -> dict:
    usable = [r for r in rows if r.get("status") != "NO_QUOTE_COVERAGE"]
    filled = [r for r in usable if r.get("fills_n")]
    return {
        "windows": len(rows), "usable": len(usable),
        "matched_campaigns": sum(1 for r in usable if r["status"] == "MATCHED"),
        "residual_hold": sum(1 for r in usable if r["status"] == "RESIDUAL_HOLD"),
        "no_fill": sum(1 for r in usable if r["status"] == "NO_FILL"),
        "windows_with_any_fill": len(filled),
        "fills_total": sum(r.get("fills_n", 0) for r in usable),
        "complement_fills": sum(1 for r in usable for f in r.get("fills") or []
                                if f["relation"] == "complete"),
        "L5_BEHAVIORAL_ACTIONS_p50": _p50([r["L5_BEHAVIORAL_ACTIONS"]
                                           for r in usable]),
        "VENUE_ADMISSIBLE_ACTIONS_p50": _p50([r["VENUE_ADMISSIBLE_ACTIONS"]
                                              for r in usable]),
        "requotes_p50": _p50([r["requotes"] for r in usable]),
        "requotes_total": sum(r["requotes"] for r in usable),
        "intentions_p50": _p50([r["intentions_n"] for r in usable]),
        "action_ceiling_bound_n": sum(1 for r in usable
                                      if r.get("action_ceiling_bound")),
        "one_sided_waits_p50": _p50([r["one_sided_book_waits"] for r in usable]),
    }


def run(windows: List[dict], *, model: Optional[Mapping[str, Any]] = None,
        **kw) -> dict:
    model = model if model is not None else pv2.load_v2()
    out: Dict[str, Any] = {"labels": LABELS,
                           "model": model.get("_sha256_16"),
                           "policy_source": "pmus_l5_persistence_v2 (UNCHANGED)",
                           "persistence_unit": "inventory intention",
                           "touch_move": "fresh evaluation, not termination",
                           "tiers": {}}
    for tier in TIERS:
        rows = [replay_window(w, model=model, tier=tier, **kw) for w in windows]
        out["tiers"][tier] = {**summarize(rows), "windows_detail": rows}
    return out
