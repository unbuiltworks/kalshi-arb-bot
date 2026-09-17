"""FROZEN evaluator: replay the PMUS book tape through the ADAPTED candidates.

OWNER 2026-09-17, Lane C. Written before the tape closed, like the candidates
and the Gemini metrics, so the comparison cannot be tuned to its own output.

WHAT THIS MEASURES, AND WHAT IT REFUSES TO
------------------------------------------
The tape is book state: (t, slug, bid, ask, depth). That is enough to decide,
at every instant and for every candidate, whether a price would have CROSSED,
what the nearest legal maker price was, whether a complement was priceable and
at what pair cost, and how fast a quote went stale. Those are the questions
ADAPTED_V1 actually changes the answer to, and they are answered exactly.

It is NOT enough to decide whether a resting order would have been FILLED. Our
order would have joined a queue behind other makers, and the tape does not say
who was ahead of us. So this module reports EXECUTABLE and RESTABLE counts and
never converts them into fills, P&L or realized anything. A metric that needs a
fill is reported as unavailable with the reason, never estimated -- estimating
it is how a sweep talks itself into promoting a policy that loses money.

P&L THEREFORE REQUIRES A SETTLEMENT JOIN and is reported only for markets whose
resolution is present. For the rest the honest output is "no settlement in
tape", which is a smaller claim than a number and a truer one.

THE CONTROL IS TODAY'S BEHAVIOUR. The grid's first value in each dimension is
what production does now (pair_target / off / off), so every candidate is
measured against the thing that actually produced the 2026-09-17 cohort rather
than against an idealisation.
"""
from __future__ import annotations

import gzip
import itertools
import json
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Iterable, Optional

from analysis.l5.sweep import candidates as C

ZERO = D("0")
#: a quote is stale once the touch has moved away from it
STALE_TICKS = 1


def _d(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return D(str(v))
    except Exception:                                    # noqa: BLE001
        return None


def read_book_tape(paths: Iterable[Path]) -> list:
    """Book observations, chronological, one dict per row."""
    rows = []
    for p in paths:
        p = Path(p)
        op = gzip.open if p.suffix == ".gz" else open
        try:
            with op(p, "rt", encoding="utf-8") as fh:            # type: ignore
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("kind") == "bbo":
                        rows.append(r)
        except OSError:
            continue
    rows.sort(key=lambda r: (str(r.get("slug")), str(r.get("t"))))
    return rows


def by_market(rows: list) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[str(r.get("slug"))].append(r)
    return out


# --------------------------------------------------------------------------
# the per-market replay

def replay_market(obs: list, cfg: dict) -> dict:
    """One market's book trajectory under one candidate configuration.

    The inventory model is deliberately minimal and stated rather than hidden:
    we count what the SHELL would have allowed, not what we would have earned.
    An action that is EXECUTABLE increments the opportunity counters; it never
    becomes a fill, so residual here is the residual the shell would PERMIT,
    labelled as such.
    """
    place = cfg["extend_placement"]
    max_pair = D(cfg["complete_max_pair_cost"])
    supp = cfg["extend_suppression"]
    gate = cfg["acquisition_gate"]
    proposed_cap = bool(cfg["proposed_residual_cap"])
    cap = D(cfg.get("residual_cap_units", "3"))
    unit = D(cfg.get("unit", "0.19"))
    protect = D(cfg.get("min_matched_to_protect", "0.30"))

    ext_req = ext_restable = ext_cross = ext_wait = 0
    cmp_req = cmp_exec = cmp_wait = cmp_cross = cmp_restable = 0
    rest_diag: list = []
    supp_fired = gate_blocked = cap_blocked = 0
    pair_costs: list = []
    one_sided = 0
    residual = ZERO
    matched = ZERO
    held_avg = D("0.45")            # representative acquisition cost
    max_res = ZERO
    run = 0
    stale = 0
    prev_touch: Optional[D] = None

    for idx, r in enumerate(obs):
        bid, ask = _d(r.get("bid")), _d(r.get("ask"))
        if bid is None or ask is None:
            one_sided += 1
        touch = bid if bid is not None else ask
        if prev_touch is not None and touch is not None:
            if abs(touch - prev_touch) >= C.TICK * STALE_TICKS:
                stale += 1
        prev_touch = touch

        # --- an EXTEND request at this instant --------------------------
        ext_req += 1
        blocked = False
        if gate != "off":
            g = C.acquisition_gate(matched, abs(residual), protect, mode=gate)
            if not g["allow"]:
                gate_blocked += 1
                blocked = True
        if not blocked and supp != "off":
            s = C.extend_suppression(
                abs(residual), cap, run, None, None,
                one_sided=(bid is None or ask is None), mode=supp)
            if D(s["suppress_p"]) >= D("0.5"):
                supp_fired += 1
                blocked = True
        if not blocked and proposed_cap:
            pc = C.proposed_residual_ok(abs(residual), unit, True, cap)
            if not pc["allow"]:
                cap_blocked += 1
                blocked = True
        if not blocked:
            p = C.extend_placement(place, False, D("0.28"), bid, ask)
            if p["outcome"] == C.CROSS:
                ext_cross += 1
            elif p["outcome"] == C.RESTABLE:
                ext_restable += 1
                residual -= unit           # PERMITTED, not filled
                run += 1
                max_res = max(max_res, abs(residual))
                # DIAGNOSTIC ONLY (owner 2026-09-17 item 2). RESTABLE is not
                # FILLED and this does not make it one. The question it
                # answers is narrower and worth answering on its own: does a
                # book-aware price merely dodge rejection, or does it sit
                # where flow actually is? A quote 30 ticks from the touch is
                # legal and useless -- which is exactly what the live
                # COMPLETEs were. Nothing here feeds behaviour or ranking.
                _px = _d(p.get("px"))
                if _px is not None and bid is not None:
                    rest_diag.append({"at": idx, "px": _px, "bid_at_quote": bid})
            else:
                ext_wait += 1

        # --- a COMPLETE request at this instant -------------------------
        if residual != ZERO:
            cmp_req += 1
            cp = C.complete_plan(True, held_avg, abs(residual), bid, ask, max_pair)
            if cp["outcome"] == C.EXECUTABLE:
                cmp_exec += 1
                pair_costs.append(D(cp["pair_cost"]))
                matched += abs(residual)
                residual = ZERO
                run = 0
            elif cp["outcome"] == C.CROSS:
                cmp_cross += 1
            else:
                cmp_wait += 1
                if cp.get("px") is not None:
                    cmp_restable += 1      # legal price, refused on economics

    # ---- RESTABLE diagnostic, computed after the walk so it can look
    # forward. DIAGNOSTIC ONLY: none of this becomes a fill, and none of it
    # feeds ranking. It answers "was the quote anywhere near the flow?", which
    # rejection rate alone cannot.
    diag = []
    for q in rest_diag:
        i, px = q["at"], q["px"]
        stale_after = None
        moved = D("0")
        touched = False
        for j in range(i + 1, len(obs)):
            b2 = _d(obs[j].get("bid"))
            if b2 is None:
                continue
            moved = max(moved, abs(b2 - q["bid_at_quote"]))
            # the quote becomes marketable once the bid reaches it: that is
            # the moment a resting NO buy would have been interacted with
            if not touched and b2 <= px:
                touched = True
                stale_after = j - i
        diag.append({"ticks_from_touch": int(abs(
                        (q["bid_at_quote"] - px) / C.TICK)),
                     "obs_until_marketable": stale_after,
                     "max_subsequent_touch_move": str(moved),
                     "book_reached_the_quote": touched})
    reached = [d for d in diag if d["book_reached_the_quote"]]
    ticks = [d["ticks_from_touch"] for d in diag]
    waits = [d["obs_until_marketable"] for d in reached
             if d["obs_until_marketable"] is not None]

    n = len(obs) or 1
    return {
        "observations": len(obs),
        "one_sided_obs": one_sided,
        "one_sided_share": round(one_sided / n, 4),
        "quote_stale_events": stale,
        "extend_requests": ext_req,
        "extend_restable": ext_restable,
        "extend_cross": ext_cross,
        "extend_wait": ext_wait,
        "extend_rejection_rate": (round(ext_cross / ext_req, 4)
                                  if ext_req else None),
        "extend_suppressed": supp_fired,
        "extend_blocked_by_gate": gate_blocked,
        "extend_blocked_by_proposed_cap": cap_blocked,
        "complete_requests": cmp_req,
        "complete_executable": cmp_exec,
        "complete_cross": cmp_cross,
        "complete_wait": cmp_wait,
        "complete_restable_but_uneconomic": cmp_restable,
        "complete_executable_rate": (round(cmp_exec / cmp_req, 4)
                                     if cmp_req else None),
        "restable_diagnostic": {
            "quotes": len(diag),
            "median_ticks_from_touch": (sorted(ticks)[len(ticks) // 2]
                                        if ticks else None),
            "max_ticks_from_touch": max(ticks) if ticks else None,
            "book_later_reached_the_quote": len(reached),
            "share_reached": (round(len(reached) / len(diag), 4)
                              if diag else None),
            "median_obs_until_marketable": (sorted(waits)[len(waits) // 2]
                                            if waits else None),
            "labels": ("DIAGNOSTIC ONLY. `book_later_reached_the_quote` means "
                       "the touch subsequently moved to or through our price "
                       "-- evidence the quote was near real flow. It is NOT a "
                       "fill: we would have queued behind makers this tape "
                       "does not name, and no count here is ever converted "
                       "into one."),
        },
        "pair_completions": len(pair_costs),
        "pair_cost_mean": (str(sum(pair_costs, ZERO) / len(pair_costs))[:6]
                           if pair_costs else None),
        "pair_cost_above_par": sum(1 for p in pair_costs if p > D("1")),
        "max_permitted_residual": str(max_res),
        "final_permitted_residual": str(residual),
        # metrics that need a FILL, and therefore are not available here
        "matched_pnl_usd": None,
        "residual_pnl_usd": None,
        "total_after_fees_usd": None,
        "unavailable_reason": (
            "P&L requires fills and settlement. This tape is book state: it "
            "proves what was EXECUTABLE, never what filled. Estimating a fill "
            "here would manufacture the result."),
    }


def sweep(tape_paths: list, grid: Optional[dict] = None,
          max_markets: int = 200) -> dict:
    """Every frozen combination, over every market in the tape."""
    grid = grid or C.GRID
    rows = read_book_tape(tape_paths)
    mkts = by_market(rows)
    keys = ("extend_placement", "complete_max_pair_cost", "extend_suppression",
            "acquisition_gate", "proposed_residual_cap")
    results = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        cfg = dict(zip(keys, combo))
        agg = defaultdict(int)
        pair_all, n_mk = [], 0
        for slug, obs in itertools.islice(mkts.items(), max_markets):
            r = replay_market(obs, cfg)
            n_mk += 1
            for k in ("extend_requests", "extend_restable", "extend_cross",
                      "extend_wait", "extend_suppressed",
                      "extend_blocked_by_gate", "extend_blocked_by_proposed_cap",
                      "complete_requests", "complete_executable",
                      "complete_cross", "complete_wait", "pair_completions",
                      "pair_cost_above_par", "one_sided_obs",
                      "quote_stale_events", "observations"):
                agg[k] += r[k]
            if r["pair_cost_mean"]:
                pair_all.append(D(r["pair_cost_mean"]))
        results.append({
            "config": {**cfg, "control": (
                cfg["extend_placement"] == "pair_target"
                and cfg["extend_suppression"] == "off"
                and cfg["acquisition_gate"] == "off"
                and not cfg["proposed_residual_cap"])},
            "markets": n_mk,
            **dict(agg),
            "extend_rejection_rate": (
                round(agg["extend_cross"] / agg["extend_requests"], 4)
                if agg["extend_requests"] else None),
            "complete_executable_rate": (
                round(agg["complete_executable"] / agg["complete_requests"], 4)
                if agg["complete_requests"] else None),
            "pair_cost_mean": (str(sum(pair_all, ZERO) / len(pair_all))[:6]
                               if pair_all else None),
        })
    return {
        "tape_rows": len(rows), "markets_in_tape": len(mkts),
        "combinations": len(results), "results": results,
        "labels": ("EXECUTABLE / RESTABLE counts from real book state. NO "
                   "FILLS, NO P&L: those need a settlement join and are "
                   "reported as unavailable rather than estimated."),
    }


#: a rate computed on fewer than this many requests is not reported as a rate
MIN_DENOMINATOR = 50


def rank(sweep_out: dict, top: int = 12) -> list:
    """Order candidates by what the tape can actually establish: fewest
    post-only rejections, then most executable completions. Deliberately NOT
    by P&L -- the tape cannot produce one, and ranking by an unavailable
    number is how a sweep launders a guess into a recommendation.

    COMPLETE RATES ARE NOT COMPARABLE ACROSS CANDIDATES WITHOUT THEIR
    DENOMINATOR, and the first version of this function ignored that. A
    candidate whose EXTENDs are mostly rejected accumulates almost no residual,
    so it asks for almost no completions, so its completion RATE approaches 1.0
    on a handful of requests -- which made the worst placement policy look like
    the best completer. The rate is therefore suppressed below
    MIN_DENOMINATOR requests and the absolute counts travel with it.
    """
    def key(r):
        rej = r.get("extend_rejection_rate")
        rate = r.get("complete_executable_rate") or 0.0
        if (r.get("complete_requests") or 0) < MIN_DENOMINATOR:
            rate = 0.0                      # not evidence, so not a tiebreak
        return (rej if rej is not None else 1.0, -rate)

    rows = sorted(sweep_out.get("results") or [], key=key)
    out = []
    for r in rows[:top]:
        row = dict(r)
        if (r.get("complete_requests") or 0) < MIN_DENOMINATOR:
            row["complete_executable_rate"] = None
            row["complete_rate_unavailable"] = (
                "only %d completion requests; a rate on that denominator is "
                "noise, not evidence" % (r.get("complete_requests") or 0))
        out.append(row)
    return out
