"""Which evidence each lane can actually reach, and what that forbids.

WHY THIS EXISTS. Both lanes are told to race toward a bounded live canary, so
the thing that decides the race is not which lane looks more promising -- it
is which lane can EVIDENCE what a canary requires. A lane that cannot observe
a stage cannot pass it, and a shadow that quietly substitutes a weaker
observation for a missing one produces a canary package built on a stage
nobody measured.

So each lane declares, per ladder stage, the source that can supply it and
whether that source is actually available to us. A stage with no available
source is UNREACHABLE, and a lane with an unreachable stage is not
canary-ready however good its numbers look.

MEASURED 2026-09-17, both lanes, independently verified rather than assumed:

  PMUS SPORTS
    two-sided BBO           23 / 150 live markets = 0.153
    depth on BOTH sides     23 / 23 of those, so a two-sided BBO means a real
                            two-sided book here, not a one-level artifact
    book levels             median 13, max 36
    spread                  median 0.030, p25 0.010, p75 0.080
    fee-safe pair <= 0.96   10 of 23
    TRADE PRINTS            NONE on the public gateway. /trades, /history,
                            /ohlc, /price-history and /v1/trades?marketSlug
                            all 404, re-verified against a live slug. The
                            public surface serves `sharesTraded`, a CUMULATIVE
                            PER-MARKET counter -- it says trades happened
                            somewhere in the market, never at our price.

  GEMINI BTC5m
    books                   public and readable, both sides
    trade prints            public, per symbol, with price and size
    SETTLEMENT              NOT PUBLISHED. An expired contract closes with an
                            empty book and no terminal value anywhere public.

THE ASYMMETRY THAT DECIDES THE RACE. Each lane is blocked at a different end
of the same ladder, and the two blockers are not equally solvable:

  PMUS   blocked in the MIDDLE, at TRADE_FLOW. VOLUME_AHEAD_CLEARED needs
         volume traded THROUGH our price, and a market-wide cumulative
         counter cannot supply it at a price level. The authenticated WS
         does carry a real trade tape, and this operation holds PMUS
         credentials, so the blocker is a deployment away rather than a
         venue limitation.
  GEMINI blocked at the END, at SETTLED. Public data cannot establish it at
         all, and the account endpoints that could are authenticated.

Neither lane may be advanced past its blocker by weakening the evidence rule.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from src.l5_surface import evidence as EV
from src.l5_surface import lanes as LN

LABEL = "L5_LANE_GATES_V1"

#: a source we can read today, without credentials we do not hold
AVAILABLE = "AVAILABLE"
#: a source that exists but needs something we have not deployed or exercised
NEEDS_DEPLOYMENT = "NEEDS_DEPLOYMENT"
#: no source exists at all. Not "not yet"; none.
UNREACHABLE = "UNREACHABLE"

STATUSES = (AVAILABLE, NEEDS_DEPLOYMENT, UNREACHABLE)


def _src(source: str, status: str, why: str) -> Dict[str, str]:
    if status not in STATUSES:
        raise ValueError("unknown source status %r" % (status,))
    return {"source": source, "status": status, "why": why}


#: per lane, per ladder stage: where the evidence would come from
SOURCES: Dict[str, Dict[str, Dict[str, str]]] = {
    LN.PMUS_SPORTS: {
        EV.DESIRED_SURFACE: _src("the surface engine", AVAILABLE,
                                 "ours; it needs no venue at all"),
        EV.RESTABLE: _src("gateway /v1/markets/{slug}/bbo", AVAILABLE,
                          "unsigned public read"),
        EV.BOOK_REACHED: _src("gateway /v1/markets/{slug}/book", AVAILABLE,
                              "unsigned; bids and offers with px and qty, "
                              "median 13 levels on a two-sided market"),
        EV.TRADE_FLOW: _src(
            "authenticated WS wss://api.polymarket.us/v1/ws/markets",
            NEEDS_DEPLOYMENT,
            "the public gateway serves NO print tape (verified: five paths "
            "404). sharesTraded is cumulative per market and cannot say a "
            "trade happened at OUR price, which is what "
            "VOLUME_AHEAD_CLEARED requires. src.pmus_ws_evidence_recorder "
            "already reads this stream read-only."),
        EV.EVIDENCED_FILL: _src("follows TRADE_FLOW", NEEDS_DEPLOYMENT,
                                "cannot be evidenced while TRADE_FLOW is not"),
        EV.INVENTORY_UPDATE: _src("follows EVIDENCED_FILL", NEEDS_DEPLOYMENT,
                                  "inventory moves on evidenced fills only"),
        EV.OPPOSITE_RECYCLING: _src("follows EVIDENCED_FILL", NEEDS_DEPLOYMENT,
                                    "both legs must be evidenced fills"),
        EV.SETTLED: _src("authenticated settlement read", NEEDS_DEPLOYMENT,
                         "src.pmus_settlement_auditor exists and is signed"),
        EV.REALIZED: _src("authenticated cash", NEEDS_DEPLOYMENT,
                          "realized is a venue cash fact"),
    },
    LN.GEMINI_CRYPTO: {
        EV.DESIRED_SURFACE: _src("the surface engine", AVAILABLE,
                                 "ours; it needs no venue at all"),
        EV.RESTABLE: _src("/v1/book/{symbol}", AVAILABLE, "public"),
        EV.BOOK_REACHED: _src("/v1/book/{symbol}", AVAILABLE,
                              "public, both sides, with level timestamps"),
        EV.TRADE_FLOW: _src("/v1/trades/{symbol}", AVAILABLE,
                            "public prints with price and size, per symbol -- "
                            "this is what PMUS lacks"),
        EV.EVIDENCED_FILL: _src("VOLUME_AHEAD_CLEARED over the prints",
                                AVAILABLE,
                                "a model of an unpublished queue, and the "
                                "strictest one available here"),
        EV.INVENTORY_UPDATE: _src("evidenced fills", AVAILABLE,
                                  "shadow inventory, never a venue position"),
        EV.OPPOSITE_RECYCLING: _src("evidenced fills, both legs", AVAILABLE,
                                    "measured; currently zero on this venue"),
        EV.SETTLED: _src("/v1/balances + /v1/transfers", NEEDS_DEPLOYMENT,
                         "the venue publishes NO settlement value: an expired "
                         "contract closes with an empty book and a last print "
                         "that is not a terminal value. See "
                         "src.gemini_settlement."),
        EV.REALIZED: _src("authenticated cash", NEEDS_DEPLOYMENT,
                          "realized is a venue cash fact"),
    },
}

#: the stages a bounded live canary needs before it can be PROPOSED.
CANARY_REQUIRES = (EV.DESIRED_SURFACE, EV.RESTABLE, EV.BOOK_REACHED,
                   EV.TRADE_FLOW, EV.EVIDENCED_FILL, EV.OPPOSITE_RECYCLING)

#: and the stages it needs to be SCORED afterwards. Kept separate because
#: they fail separately, and collapsing them hides the state that matters:
#: a lane that can place a canary but cannot settle it would spend real money
#: on an experiment whose result nobody can read. Under settlement-only
#: realized labelling that is not a canary, it is an unscored loss.
SCORING_REQUIRES = (EV.SETTLED, EV.REALIZED)

#: reachable is not the same as available: a source we could deploy still
#: has to be deployed before a canary is proposed
REACHABLE = (AVAILABLE,)


def lane_report(lane: str) -> Dict[str, Any]:
    if lane not in SOURCES:
        raise ValueError("unknown lane %r" % (lane,))
    per = SOURCES[lane]
    blocked = [s for s in CANARY_REQUIRES if per[s]["status"] not in REACHABLE]
    unscorable = [s for s in SCORING_REQUIRES
                  if per[s]["status"] not in REACHABLE]
    unreachable = [s for s in EV.STAGES if per[s]["status"] == UNREACHABLE]
    return {
        "label": LABEL, "lane": lane,
        "stages": {s: dict(per[s]) for s in EV.STAGES},
        "available": [s for s in EV.STAGES if per[s]["status"] == AVAILABLE],
        "needs_deployment": [s for s in EV.STAGES
                             if per[s]["status"] == NEEDS_DEPLOYMENT],
        "unreachable": unreachable,
        "canary_blocked_by": blocked,
        "can_propose_canary": not blocked,
        "scoring_blocked_by": unscorable,
        "can_score_canary": not unscorable,
        # BOTH, because a canary nobody can score is not a canary
        "canary_ready": not (blocked or unscorable),
        "labels": [
            "canary_ready means the EVIDENCE can be produced, never that a "
            "canary is approved. Live authorization is a separate human "
            "decision and nothing here grants it.",
            "a stage nobody can observe cannot be passed by substituting a "
            "weaker one for it.",
            "can_propose_canary WITHOUT can_score_canary is the dangerous "
            "state: real money spent on a result nobody can read.",
        ],
    }


def race() -> Dict[str, Any]:
    """Which lane is closest to being able to evidence a canary, and why.

    Distance is counted in BLOCKED STAGES, not in promise: a lane with better
    numbers and an unobservable stage is further away than a lane with worse
    numbers and none.
    """
    reports = {l: lane_report(l) for l in LN.LANES}
    ranked = sorted(reports.values(),
                    key=lambda r: (len(r["canary_blocked_by"])
                                   + len(r["scoring_blocked_by"]), r["lane"]))
    leader = ranked[0]
    return {
        "label": LABEL,
        "lanes": {r["lane"]: {
            "canary_ready": r["canary_ready"],
            "can_propose_canary": r["can_propose_canary"],
            "can_score_canary": r["can_score_canary"],
            "blocked_by": r["canary_blocked_by"],
            "scoring_blocked_by": r["scoring_blocked_by"],
            "n_blocked": (len(r["canary_blocked_by"])
                          + len(r["scoring_blocked_by"]))}
                  for r in ranked},
        "closest": leader["lane"],
        "closest_blocked_by": (leader["canary_blocked_by"]
                               + leader["scoring_blocked_by"]),
        "any_ready": any(r["canary_ready"] for r in reports.values()),
        "labels": [
            "distance is counted in stages nobody can evidence yet, not in "
            "how promising a lane looks.",
            "neither lane may be advanced past its blocker by weakening the "
            "evidence rule, and neither lane blocks the other.",
        ],
    }
