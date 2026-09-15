"""Continuous dual-venue EXECUTABLE shadow: L5 intent -> PM -> GEMINI -> route.

WHAT THIS ANSWERS, AND WHAT IT CANNOT. One BTC15m observation with a 1-share
touch is not evidence that Gemini cannot execute L5, and one green reading
would not be evidence that it can. So this runs continuously, on BOTH cohorts,
recording the same fields for both venues, and lets the distribution decide.

THE THREE CLOCKS ARE NOT INTERCHANGEABLE, and this is the discipline that makes
the output honest:

    L5_EVENT_CLOCK        the venue's timestamp on L5's own fill
    L5_OBSERVATION_CLOCK  when that fill first became visible to us -- a median
                          15.0s later, p95 29.8s
    MARKET_STATE_CLOCK    when WE read each venue's book

Every record carries all three. A book read at observation time is NOT the book
that existed at L5's event time, so nothing here may be phrased as "we could
have traded alongside L5 at his price". The measurable question is REALTIME
REPLICABILITY: at the moment the action became visible to us, what could each
venue actually supply, and at what all-in cost. `l5_event_to_book_s` is on every
row so the size of that gap is never invisible.

TWO INTENT SOURCES, LABELLED, NEVER MIXED:

  MODEL   the frozen FIDELITY artifact predicts L5's next action. Only for
          cohorts with a versioned artifact -- cohorts are NOT pooled, so
          BTC15m's model is never run on BTC5m states.
  REPLAY  the intent is L5's OWN anchor fill: same side, same size, and a
          ceiling at the price L5 himself achieved. This needs no behavioural
          model, which is why it covers BTC5m today, and it is the sharper
          execution test: if a venue cannot supply what L5 just bought at a
          price no worse than he paid, the replication fails on execution
          regardless of how good the behavioural model is.

THE CEILING IS L5'S OWN ACHIEVED PRICE, on the scale of the side we want (a
Down fill at q implies our Up side at 1-q). It is deliberately not the current
touch: a ceiling set from the book we are about to price against is circular
and always "executable". Expect this to decline the taker path often. That is
the finding, not a bug -- L5's economics live on the maker side, which is why
the maker path below is tested with equal care.

MAKER FILLS REQUIRE SUBSEQUENT OBSERVABLE EVIDENCE. A hypothetical quote is
recorded when placed and resolved LATER against public prints, never in the
same breath. See maker.py.

READ ONLY, and money-safe by construction: no key, no signer and no order path
is imported anywhere in this module or its dependencies. It is a research
service and must never be wired to execution.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from src.l5_adapters.base import AdapterEvaluation
from src.l5_adapters.gemini_mapping import gemini_symbol
from src.l5_intent.action import DesiredInventoryAction
from src.l5_router.route import route
from src.l5_shadow import depth as DEP
from src.l5_shadow import maker as MK
from src.l5_shadow import terms as TERMS
from src.l5_shadow import venues as V
from src.l5_shadow.executable import evaluate_executable

SCHEMA = "l5_executable_shadow/v1"
COHORTS = ("BTC15m", "BTC5m")
POLL_S = 15.0
DIGEST_S = 300.0
# How long a hypothetical maker quote is allowed to rest before we decide it
# from prints. Raised from 120s to 300s on evidence: at 120s, 43 of 44 decided
# placements came back UNDECIDED for want of any print crossing the join price,
# so the horizon was measuring its own shortness rather than the venue. 300s
# covers a whole 5m window and a third of a 15m one, and a longer observation
# window only ever adds evidence -- prints before placement are excluded either
# way, so this cannot manufacture a fill.
MAKER_HORIZON_S = 300.0
# book re-reads after an intent, so "the price moved right after" is recorded
# rather than argued about
EVOLUTION_AT_S = (30.0, 60.0, 120.0)
CEILING_POLICY = "l5_anchor_achieved_price"

INTENTS = "intents.jsonl"
EVALS = "evaluations.jsonl"
ROUTES = "routes.jsonl"
MAKER_OUT = "maker_outcomes.jsonl"
EVOLUTION = "book_evolution.jsonl"


# --------------------------------------------------------------- durable store

def append(out_dir: str, name: str, rec: Dict[str, Any]) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name), "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def read_all(out_dir: str, name: str) -> List[Dict[str, Any]]:
    p = os.path.join(out_dir, name)
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue            # a torn final line never kills a boot
    return out


# ------------------------------------------------------------------- L5 intent

def l5_fills(cohort: str) -> Optional[List[dict]]:
    """L5's fills for one cohort, from the same feed FIDELITY uses."""
    from src.l5_fidelity.service import fetch
    return fetch(cohort)


def implied_price_for(outcome: str, anchor: dict) -> D:
    """L5's own achieved price, expressed on the side we want.

    A Down fill at 0.30 prices our Up side at 0.70 at that same instant. This
    is arithmetic on ONE observed trade, not a model.
    """
    # the activity feed carries float prices, so 0.03 arrives as
    # 0.0299999993 and its complement as 0.9700000007. Quantizing to 1e-4
    # removes representation noise without moving the price: the venue's own
    # grid is coarser than that by two orders of magnitude.
    p = D(str(anchor["price"])).quantize(D("0.0001"))
    return p if str(anchor["outcome"]) == outcome else (D("1") - p)


def replay_intent(cohort: str, market_key: str, anchor: dict, state: dict,
                  *, now: float) -> Optional[DesiredInventoryAction]:
    """L5's own last action, restated as a venue-neutral intent."""
    outcome = str(anchor["outcome"])
    ceiling = implied_price_for(outcome, anchor)
    if not (D("0") < ceiling < D("1")):
        return None
    qty = D(str(anchor["size"])).quantize(D("0.000001"))
    if qty <= 0:
        return None
    up, dn = D(str(state["inv_up"])), D(str(state["inv_dn"]))
    resid = up - dn
    action = ("OPEN" if resid == 0 else
              "EXTEND" if (resid > 0) == (outcome == "Up") else "SWITCH")
    return DesiredInventoryAction(
        cohort=cohort, market_key=market_key,
        source_state_id=f"{anchor['cid']}@{anchor['t']}",
        t_utc=float(anchor["t"]), tte_s=float(state["tte_s"]),
        action=action, desired_outcome=outcome,
        desired_inventory=str((up if outcome == "Up" else dn) + qty),
        incremental_qty=str(qty), max_acceptable_price=str(ceiling),
        current_matched=str(min(up, dn)), residual=str(resid),
        model_version="replay/l5_anchor_fill",
        notes=("REPLAY intent: L5's own fill restated. No behavioural model is "
               "involved, so this is an EXECUTION test, not a prediction."),
        extra={"intent_source": "REPLAY",
               "l5_anchor_price": str(anchor["price"]),
               "l5_anchor_outcome": str(anchor["outcome"]),
               "l5_anchor_size": str(anchor["size"]),
               "l5_fee_bearing": bool(anchor.get("fee_bearing")),
               "ceiling_policy": CEILING_POLICY,
               "outside_policy_band": TERMS.outside_policy_band(ceiling),
               "observed_at": now,
               "l5_observation_lag_s": round(now - float(anchor["t"]), 3)})


def model_intent(cohort: str, market_key: str, anchor: dict, state: dict,
                 model, *, now: float) -> Optional[DesiredInventoryAction]:
    """The FIDELITY prediction, restated as an intent. Ceiling policy is shared."""
    p = model.predict(tte=state["tte_s"], since=None,
                      resid_rel=state.get("residual_rel"),
                      last_outcome=state.get("anchor_outcome"),
                      price=state.get("anchor_price"))
    outcome = str(p.get("predicted_outcome") or "Up")
    ceiling = implied_price_for(outcome, anchor)
    if not (D("0") < ceiling < D("1")):
        return None
    up, dn = D(str(state["inv_up"])), D(str(state["inv_dn"]))
    resid = up - dn
    action = ("OPEN" if resid == 0 else
              "EXTEND" if (resid > 0) == (outcome == "Up") else "SWITCH")
    qty = D("1")
    return DesiredInventoryAction(
        cohort=cohort, market_key=market_key,
        source_state_id=f"{anchor['cid']}@{anchor['t']}#model",
        t_utc=float(anchor["t"]), tte_s=float(state["tte_s"]),
        action=action, desired_outcome=outcome,
        desired_inventory=str((up if outcome == "Up" else dn) + qty),
        incremental_qty=str(qty), max_acceptable_price=str(ceiling),
        current_matched=str(min(up, dn)), residual=str(resid),
        model_version=str(model.version),
        confidence=p.get("p_outcome_up") if outcome == "Up" else (
            None if p.get("p_outcome_up") is None
            else 1.0 - float(p["p_outcome_up"])),
        notes="MODEL intent from the frozen FIDELITY artifact for this cohort.",
        extra={"intent_source": "MODEL", "ceiling_policy": CEILING_POLICY,
               "predicted_size_mode_bin": p.get("predicted_size_mode_bin"),
               "observed_at": now,
               "l5_observation_lag_s": round(now - float(anchor["t"]), 3)})


# ------------------------------------------------------------------- ladders

def pm_ladders(window: dict, outcome: str) -> Dict[str, Any]:
    """Polymarket has a SEPARATE token per outcome, so the Down book is read
    directly rather than derived from 1 - Up bid. Deriving it would price a
    synthetic book against a real one."""
    tok = window.get("token_up") if outcome == "Up" else window.get("token_dn")
    if not tok:
        return {"ok": False, "reason": "no CLOB token for this outcome"}
    bk = V.pm_book(tok)
    if bk is None:
        return {"ok": False, "reason": "CLOB book unreadable: UNKNOWN, not absent"}
    try:
        take, rest = DEP.parse_book(bk, "asks"), DEP.parse_book(bk, "bids")
    except DEP.BookParseError as e:
        # levels were served and none parsed: UNKNOWN. Reporting this as an
        # empty book is how a live 112-level ladder once read as "quotes
        # nothing", and an empty book is a routing fact while this is a defect.
        return {"ok": False, "reason": f"CLOB book unparseable: {e}"}
    return {"ok": True, "book": bk, "token": tok, "take": take, "rest": rest,
            "clob_tick": bk.get("tick"),
            "construction": (f"native {outcome} token asks (take) / bids (rest); "
                             f"no synthetic inversion")}


def gemini_ladders(symbol: str, outcome: str) -> Dict[str, Any]:
    """Gemini lists only the UP contract, so Down is the INVERSION of the Up
    book: buying Down means selling Up, so a Down ask at 1-p sits where an Up
    bid at p is. Recorded explicitly, because getting it backwards silently
    prices the wrong side of every Down intent."""
    bk = V.gemini_book(symbol)
    if bk is None:
        return {"ok": False, "reason": "Gemini book unreadable: UNKNOWN, not absent"}
    try:
        up_asks = DEP.parse_book(bk, "asks")
        up_bids = DEP.parse_book(bk, "bids")
    except DEP.BookParseError as e:
        return {"ok": False, "reason": f"Gemini book unparseable: {e}"}
    if outcome == "Up":
        take, rest, how = up_asks, up_bids, "UP contract asks (take) / bids (rest)"
    else:
        take = DEP.no_ladder_from_yes(up_bids)
        rest = DEP.no_ladder_from_yes(up_asks)
        how = ("DOWN derived from the UP book: Down ask = 1 - Up bid, "
               "Down bid = 1 - Up ask")
    return {"ok": True, "book": bk, "token": symbol, "take": take, "rest": rest,
            "source": bk.get("source", "rest"), "construction": how}


# ------------------------------------------------------------ maker placement

def maker_plan(intent, terms, rest_levels: Sequence, *, venue: str,
               now: float) -> Dict[str, Any]:
    """Where we would rest, what is ahead of us, and the maker economics.

    Nothing is claimed to fill here. The row is written at placement and
    decided later from prints.
    """
    ceiling = D(intent.max_acceptable_price)
    qty = D(intent.incremental_qty)
    out: Dict[str, Any] = {
        "venue": venue, "intent_id": intent.id, "placed_at": now,
        "qty": str(qty), "ceiling": str(ceiling),
        "maker_fee_basis": (terms.extra.get("maker_fee_basis")
                            if venue == "PMUS" else
                            "ceil-cent 0.0175*C*P*(1-P); waiver NOT applied"),
    }
    if terms.maker_fee is None:
        out.update(submittable=False,
                   reason="venue did not declare a maker fee; refusing to price")
        return out
    if terms.tick is None:
        out.update(submittable=False, reason="venue tick unknown")
        return out
    px = MK.join_price(rest_levels, tick=terms.tick, ceiling=ceiling)
    if px is None:
        out.update(submittable=False,
                   reason=(f"no bid to join at or under the ceiling {ceiling} "
                           f"(best bid "
                           f"{rest_levels[0][0] if rest_levels else None})"))
        return out
    if terms.min_order_size is not None and qty < D(str(terms.min_order_size)):
        out.update(submittable=False, price=str(px),
                   reason=(f"venue minimum order size is {terms.min_order_size}; "
                           f"{qty} cannot rest here at all"))
        return out
    fee = terms.maker_fee(px, qty)
    eff = px + (D(str(fee)) / qty if qty else D("0"))
    ahead = MK.queue_ahead(rest_levels, px)
    out.update(submittable=True, price=str(px), fee=str(fee),
               effective_cost=str(eff.quantize(D("0.000001"))),
               within_ceiling=bool(eff <= ceiling),
               queue_ahead=str(ahead),
               outside_policy_band=TERMS.outside_policy_band(px),
               reason="ok",
               labels=("PLACEMENT ONLY. Whether this quote fills is decided "
                       "later from public prints, never here."))
    return out


# -------------------------------------------------------------------- one pass

def observe_intent(out_dir: str, intent, *, window: Optional[dict],
                   now: float) -> Dict[str, Any]:
    """Evaluate ONE frozen intent on BOTH venues and route it.

    Both venues are scored through the same `evaluate_executable`, so a
    difference in the output is a difference in venue economics rather than in
    how they were measured.
    """
    outcome = intent.desired_outcome
    ev_rows, maker_rows = [], []
    clocks = {
        "L5_EVENT_CLOCK": intent.t_utc,
        "L5_OBSERVATION_CLOCK": (intent.extra or {}).get("observed_at", now),
        "MARKET_STATE_CLOCK": now,
        "l5_event_to_book_s": round(now - intent.t_utc, 3),
        "note": ("the book below is the one that existed when WE read it, NOT "
                 "the one L5 traded against. This measures REALTIME "
                 "REPLICABILITY, not event-time fidelity."),
    }

    # ---- Polymarket
    pm_t = TERMS.pm_terms(window)
    pm = pm_ladders(window, outcome) if window else {
        "ok": False, "reason": "gamma window unreadable: UNKNOWN, not absent"}
    if pm.get("ok"):
        ev = evaluate_executable(
            intent, pm_t, pm["take"], venue_market_id=pm["token"],
            mapping_confidence="exact",
            mapping_reason="native Polymarket window; this IS L5's own market",
            book_source_ts=pm["book"].get("source_ts"),
            observed_at=pm["book"].get("observed_at"),
            ladder_construction=pm["construction"], now=now)
        maker_rows.append(maker_plan(intent, pm_t, pm["rest"], venue="PMUS",
                                     now=now))
    else:
        ev = evaluate_executable(intent, pm_t, None, mapping_reason=pm["reason"],
                                 now=now)
    ev.extra["clocks"] = clocks
    ev_rows.append(ev)

    # ---- Gemini
    gm_t = TERMS.gemini_terms()
    cand = gemini_symbol(intent.market_key)
    if cand is None:
        gev = evaluate_executable(
            intent, gm_t, None,
            mapping_reason=f"no Gemini candidate for {intent.market_key}",
            now=now)
    else:
        gl = gemini_ladders(cand["symbol"], outcome)
        if gl.get("ok"):
            gev = evaluate_executable(
                intent, gm_t, gl["take"], venue_market_id=cand["symbol"],
                mapping_confidence="exact",
                mapping_reason=cand["basis"],
                book_source_ts=gl["book"].get("source_ts"),
                observed_at=gl["book"].get("observed_at"),
                ladder_construction=gl["construction"], now=now)
            gev.extra["book_source"] = gl.get("source")
            maker_rows.append(maker_plan(intent, gm_t, gl["rest"],
                                         venue="GEMINI", now=now))
        else:
            gev = evaluate_executable(intent, gm_t, None,
                                      venue_market_id=cand["symbol"],
                                      mapping_reason=gl["reason"], now=now)
    gev.extra["clocks"] = clocks
    ev_rows.append(gev)

    # LIKE WITH LIKE, in one number. Measured 2026-09-15 13:18Z on the same
    # 5m window (mapping verified: both expire 13:20:00Z exactly), with ~77s
    # left: Polymarket quoted Up at 0.05 and Gemini at 0.14 with no bid at all.
    # A 9c divergence near expiry is a venue fact worth a column rather than an
    # anecdote, so every route row carries the gap and which venue it favours.
    gap = _venue_gap(ev_rows)
    dec = route(intent, ev_rows, now=now)
    for e in ev_rows:
        append(out_dir, EVALS, {**e.as_dict(), "schema": SCHEMA + "/evaluation"})
    for m in maker_rows:
        append(out_dir, EVALS, {"schema": SCHEMA + "/maker_placement",
                                "market_key": intent.market_key,
                                "desired_outcome": outcome, "clocks": clocks,
                                **m})
    append(out_dir, ROUTES, {**dec.as_dict(), "schema": SCHEMA + "/route",
                             "cohort": intent.cohort,
                             "market_key": intent.market_key,
                             "intent_source": (intent.extra or {}).get(
                                 "intent_source"),
                             "desired_outcome": outcome,
                             "incremental_qty": intent.incremental_qty,
                             "max_acceptable_price": intent.max_acceptable_price,
                             "venue_gap": gap,
                             "tte_at_book_s": round(
                                 intent.tte_s - (now - intent.t_utc), 1),
                             "clocks": clocks})
    return {"route": dec.route, "reason": dec.reason, "evaluations": ev_rows,
            "maker": maker_rows, "decision": dec}


# ------------------------------------------------------- deferred resolutions

def resolve_maker(out_dir: str, pend: Dict[str, Any], *, now: float) -> bool:
    """Decide ONE hypothetical quote from LATER public prints. True if decided."""
    venue, ik = pend["venue"], pend["intent_id"]
    if venue == "PMUS":
        prints = V.pm_trades(pend["condition_id"]) if pend.get("condition_id") \
            else None
        if prints is not None and pend.get("token"):
            prints = [p for p in prints if p.get("asset") == pend["token"]]
    else:
        prints, src = None, None
        sym = pend.get("symbol")
        if sym:
            # THE SOCKET'S OWN TRADE EVENTS FIRST. `/v1/trades` is a poll of a
            # capped list, so a burst can push a print off the end before we
            # ask; the websocket delivers every trade as it happens. It is used
            # only when the socket's continuous view started before the quote
            # was placed -- otherwise "we saw nothing" would really mean "we
            # were not watching", and prints_since returns None to say so.
            try:
                bk = V.gemini_feed().watch(sym)
                prints = bk.prints_since(pend["placed_at"])
                src = "websocket" if prints is not None else None
            except Exception:                                 # noqa: BLE001
                prints = None
            if prints is None:
                prints = V.gemini_trades(sym)
                src = "rest_poll" if prints is not None else None
        pend["_print_source"] = src
        if prints is not None and pend.get("desired_outcome") == "Down":
            # our Down quote at x rests where an Up ask at 1-x does; an Up
            # print at or ABOVE 1-x is what would have traded against it, so
            # the prints are mirrored onto the Down scale before the test.
            prints = [{**p, "price": D("1") - D(str(p["price"]))} for p in prints]
    res = MK.evidence_for_quote(
        quote_px=pend["price"], quote_qty=pend["qty"], ahead=pend["queue_ahead"],
        placed_at=pend["placed_at"], prints=prints, observed_until=now)
    append(out_dir, MAKER_OUT, {
        "schema": SCHEMA + "/maker_outcome", "intent_id": ik, "venue": venue,
        "market_key": pend.get("market_key"),
        "desired_outcome": pend.get("desired_outcome"),
        "placed_at": pend["placed_at"], "resolved_at": now,
        "quote_price": pend["price"], "quote_qty": pend["qty"],
        "print_source": pend.get("_print_source"),
        "horizon_s": MAKER_HORIZON_S, **res})
    return True


def resolve_evolution(out_dir: str, pend: Dict[str, Any], *,
                      now: float) -> bool:
    """Re-read both books after the fact: did the price move toward us or away?

    A single snapshot cannot distinguish "too expensive" from "too expensive for
    two seconds", and patience is the L5 behaviour under test.
    """
    intent_ceiling = D(str(pend["ceiling"]))
    row: Dict[str, Any] = {
        "schema": SCHEMA + "/book_evolution", "intent_id": pend["intent_id"],
        "market_key": pend["market_key"], "desired_outcome": pend["outcome"],
        "since_intent_s": round(now - pend["t0"], 1), "observed_at": now,
        "ceiling": str(intent_ceiling)}
    w = V.pm_window(pend["market_key"])
    pm = pm_ladders(w, pend["outcome"]) if w else {"ok": False}
    row["PMUS"] = _evo(pm.get("take") if pm.get("ok") else None, intent_ceiling)
    cand = gemini_symbol(pend["market_key"])
    gl = gemini_ladders(cand["symbol"], pend["outcome"]) if cand else {"ok": False}
    row["GEMINI"] = _evo(gl.get("take") if gl.get("ok") else None,
                         intent_ceiling)
    append(out_dir, EVOLUTION, row)
    return True


def _evo(levels: Optional[Sequence], ceiling: D) -> Dict[str, Any]:
    if levels is None:
        return {"readable": False,
                "reason": "book unreadable at this instant: UNKNOWN"}
    if not levels:
        return {"readable": True, "best": None, "executable_at_ceiling": "0",
                "reason": "venue quotes nothing on this side"}
    inside = sum((D(str(s)) for p, s in levels if D(str(p)) <= ceiling), D("0"))
    return {"readable": True, "best": str(levels[0][0]),
            "best_depth": str(levels[0][1]),
            "executable_at_ceiling": str(inside),
            "crossed_ceiling": bool(D(str(levels[0][0])) <= ceiling)}


# ------------------------------------------------------------------ the runner

def recover_pending(out_dir: str) -> tuple:
    """Rebuild the pending sets from the durable log, never from memory.

    A maker placement that was never resolved is still pending after a restart.
    Rebuilding from the log rather than a side file means the two cannot drift.
    """
    decided = {(r.get("intent_id"), r.get("venue"))
               for r in read_all(out_dir, MAKER_OUT)}
    makers = []
    for r in read_all(out_dir, EVALS):
        if r.get("schema") != SCHEMA + "/maker_placement":
            continue
        if not r.get("submittable"):
            continue
        if (r.get("intent_id"), r.get("venue")) in decided:
            continue
        makers.append(r)
    return makers, []


def digest(out_dir: str, *, cycles: int, feed_lag: Dict[str, Any],
           errors: int, pending_makers: int, pending_evo: int) -> Dict[str, Any]:
    routes = read_all(out_dir, ROUTES)
    outs = read_all(out_dir, MAKER_OUT)
    counts: Dict[str, int] = {}
    by_cohort: Dict[str, Dict[str, int]] = {}
    for r in routes:
        counts[r.get("route", "?")] = counts.get(r.get("route", "?"), 0) + 1
        c = by_cohort.setdefault(r.get("cohort", "?"), {})
        c[r.get("route", "?")] = c.get(r.get("route", "?"), 0) + 1
    ev_fill = sum(1 for o in outs if o.get("evidenced_fill") is True)
    undecided = sum(1 for o in outs if o.get("evidenced_fill") is None)
    return {
        "kind": "digest", "utc": round(time.time(), 1), "cycles": cycles,
        "feed_lag_s_by_cohort": feed_lag, "errors": errors,
        "intents": len(read_all(out_dir, INTENTS)),
        "routes_total": len(routes), "routes": counts, "by_cohort": by_cohort,
        "maker_outcomes": len(outs),
        "maker_evidenced_fills": ev_fill,
        "maker_undecided_no_prints": undecided,
        "pending_maker_quotes": pending_makers,
        "pending_book_evolution": pending_evo,
        "labels": ("evidenced fills are MODELLED maker fills backed by later "
                   "public prints. They are NOT venue-confirmed fills and are "
                   "never realized P&L."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-shadow")
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)

    models = {}
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    for c in COHORTS:
        art = os.path.join(root, "artifacts", "l5_fidelity", f"{c}.json.gz")
        if os.path.exists(art):
            from src.l5_fidelity.model import FidelityV0
            models[c] = FidelityV0.load(art)
    print(f"L5_SHADOW boot cohorts={list(COHORTS)} "
          f"models={ {k: v.version for k, v in models.items()} } "
          f"out={a.out_dir}", flush=True)
    print("L5_SHADOW note MODEL intents run only where a versioned artifact "
          "exists for that cohort; cohorts are never pooled. REPLAY intents "
          "cover every cohort and need no model.", flush=True)

    pending_makers, _ = recover_pending(a.out_dir)
    pending_evo: List[Dict[str, Any]] = []
    print(f"L5_SHADOW recovered pending_maker_quotes={len(pending_makers)}",
          flush=True)

    seen_anchor: set = set()
    seen_intent: set = set()
    windows_cache: Dict[str, dict] = {}
    cycles = errors = 0
    # PER COHORT. A single `feed_lag` was overwritten by whichever cohort ran
    # last, so a quiet BTC5m reported its 183s staleness as BTC15m's -- while
    # the same digest carried an 18s observation lag from a BTC15m anchor. Two
    # cohorts, two clocks.
    feed_lag: Dict[str, Optional[float]] = {c: None for c in COHORTS}
    last_digest = 0.0
    deadline = time.time() + a.hours * 3600 if a.hours else None

    while True:
        cycles += 1
        now = time.time()
        try:
            for cohort in COHORTS:
                fills = l5_fills(cohort)
                if fills is None:
                    errors += 1
                    continue
                if fills:
                    feed_lag[cohort] = round(now - fills[-1]["t"], 1)
                live = [f for f in fills if f["window_end"] > now]
                if not live:
                    continue
                anchor = live[-1]
                key = (anchor["cid"], anchor["t"])
                if key in seen_anchor:
                    continue
                seen_anchor.add(key)
                wf = [f for f in live if f["cid"] == anchor["cid"]]
                from src.l5_fidelity import anchored
                st = anchored.state_after_fill(cohort=cohort, anchor=anchor,
                                               window_fills=wf)
                dur = 900 if cohort.endswith("15m") else 300
                mk = (f"btc-updown-{'15m' if dur == 900 else '5m'}-"
                      f"{int(anchor['window_end']) - dur}")
                if mk not in windows_cache:
                    w = V.pm_window(mk)
                    if w:
                        windows_cache[mk] = w
                window = windows_cache.get(mk)

                cands = [replay_intent(cohort, mk, anchor, st, now=now)]
                if cohort in models:
                    cands.append(model_intent(cohort, mk, anchor, st,
                                              models[cohort], now=now))
                for intent in cands:
                    if intent is None or intent.id in seen_intent:
                        continue
                    seen_intent.add(intent.id)
                    append(a.out_dir, INTENTS,
                           {**intent.as_dict(), "schema": SCHEMA + "/intent"})
                    res = observe_intent(a.out_dir, intent, window=window,
                                         now=now)
                    for m in res["maker"]:
                        if not m.get("submittable"):
                            continue
                        pending_makers.append({
                            **m, "market_key": intent.market_key,
                            "desired_outcome": intent.desired_outcome,
                            "condition_id": (window or {}).get("condition_id"),
                            "token": ((window or {}).get("token_up")
                                      if intent.desired_outcome == "Up"
                                      else (window or {}).get("token_dn")),
                            "symbol": (gemini_symbol(intent.market_key) or {}
                                       ).get("symbol")})
                    for dt in EVOLUTION_AT_S:
                        pending_evo.append({
                            "intent_id": intent.id, "market_key": mk,
                            "outcome": intent.desired_outcome,
                            "ceiling": intent.max_acceptable_price,
                            "t0": now, "due": now + dt})
                    print("L5_SHADOW " + json.dumps({
                        "kind": "intent", "cohort": cohort,
                        "source": (intent.extra or {}).get("intent_source"),
                        "market": mk, "action": intent.action,
                        "outcome": intent.desired_outcome,
                        "qty": intent.incremental_qty,
                        "ceiling": intent.max_acceptable_price,
                        "l5_lag_s": (intent.extra or {}).get(
                            "l5_observation_lag_s"),
                        "route": res["route"],
                        "PMUS": _brief(res["evaluations"][0]),
                        "GEMINI": _brief(res["evaluations"][1]),
                        "reason": res["reason"][:160],
                    }, default=str), flush=True)

            # sockets follow live windows only; an expired one is dropped so
            # a long-running service does not accumulate connections.
            live_syms = []
            for w in V.live_windows():
                c = gemini_symbol(w["market_key"])
                if c:
                    live_syms.append(c["symbol"])
            V.prune_gemini_feed(live_syms)

            due = [p for p in pending_makers
                   if now - p["placed_at"] >= MAKER_HORIZON_S]
            for p in due:
                resolve_maker(a.out_dir, p, now=now)
                pending_makers.remove(p)
            due_evo = [p for p in pending_evo if now >= p["due"]]
            for p in due_evo:
                resolve_evolution(a.out_dir, p, now=now)
                pending_evo.remove(p)
        except Exception as exc:                              # noqa: BLE001
            errors += 1
            print("L5_SHADOW " + json.dumps({
                "kind": "cycle_error", "err": f"{type(exc).__name__}: {exc}",
            }), flush=True)

        if now - last_digest > DIGEST_S:
            print("L5_SHADOW " + json.dumps(digest(
                a.out_dir, cycles=cycles, feed_lag=feed_lag, errors=errors,
                pending_makers=len(pending_makers),
                pending_evo=len(pending_evo)), default=str), flush=True)
            last_digest = now
        if len(seen_anchor) > 20000:
            seen_anchor = set(list(seen_anchor)[-5000:])
        if len(seen_intent) > 20000:
            seen_intent = set(list(seen_intent)[-5000:])
        if a.once:
            break
        if deadline is not None and time.time() >= deadline:
            break
        time.sleep(POLL_S)
    return 0


def _venue_gap(ev_rows: Sequence[AdapterEvaluation]) -> Dict[str, Any]:
    """Best acquirable price on each venue, and the spread between them.

    Compared on the price actually quoted for the side we want, not on a mid:
    a mid hides a one-sided book, and a one-sided book is exactly what Gemini
    showed here.
    """
    px = {}
    for e in ev_rows:
        px[e.venue] = (D(e.available_price) if e.available_price is not None
                       else None)
    a, b = px.get("PMUS"), px.get("GEMINI")
    if a is None or b is None:
        return {"PMUS": (str(a) if a is not None else None),
                "GEMINI": (str(b) if b is not None else None),
                "gap": None,
                "reason": "at least one venue quoted no price on this side"}
    return {"PMUS": str(a), "GEMINI": str(b), "gap": str(abs(a - b)),
            "cheaper": ("PMUS" if a < b else "GEMINI" if b < a else "tie")}


def _brief(ev: AdapterEvaluation) -> Dict[str, Any]:
    return {"px": ev.available_price, "touch_depth": ev.available_depth,
            "exec_qty": ev.executable_qty, "vwap": ev.submission_price,
            "fee": ev.expected_fee, "all_in": ev.effective_cost,
            "within": ev.within_ceiling, "submit": ev.would_submit,
            "why": (ev.would_submit_reason or ev.mapping_reason or "")[:110]}


if __name__ == "__main__":
    raise SystemExit(main())
