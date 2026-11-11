"""Evidence-driven ramp ladder modeled on L5's April pattern (OWNER
5554127702 #4). PREBUILT, NOT ACTIVE: the campaign runs the rung named by
PMUS_SPRING_RUNG (default R1, the rung already authorized in 5553189142)
and nothing here promotes a rung by itself -- `evaluate()` turns
venue-confirmed evidence into a PROMOTE / HOLD / ROLLBACK verdict with
the exact reasons, and the owner flips the env.

Order of scaling, as L5 did it in April (analysis/l5/l5_april_ramp.json,
l5_ramp_analysis.json): actions per window and windows per day first,
size second, concurrency last. Every rung states its worst case in
dollars; the daily-loss stop (-$1.00) stays the absolute bound above all
of them and is never touched here.

Evidence keys (all from venue-confirmed rows or the campaign's own logs):
  settled_markets_clean   markets FILLED -> SETTLED -> REALIZED with
                          ledger == venue, no orphan, no halt
  fill_rate               filled orders / submitted orders
  shadow_mix_l1           L1 distance between the executed relation mix
                          and the shadow (L5-distribution) relation mix
  matched_pairs           venue-confirmed matched pairs formed
  pair_cost_max           worst matched pair cost (<= 0.98 required)
  adverse_selection_c     mean mark move AGAINST a fill 5 min after it,
                          cents (None = not measured -> fails closed)
  max_drawdown_usd        worst peak-to-trough of venue-confirmed realized
  realized_usd            venue-confirmed realized over the rung
  mismatches              inventory mismatches + orphans + halts
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Mapping, Optional

RUNG_ENV = "PMUS_SPRING_RUNG"
DAILY_LOSS_STOP = D("-1.00")

# per-market filled OPEN/EXTEND cap, sequential markets per campaign,
# campaigns per day the arm allows, max maker actions per session,
# concurrency; worst case = cap x markets x campaigns (complement legs
# only ever form pairs and cannot add to the directional worst case)
RUNGS: tuple[dict, ...] = (
    {"id": "R1", "name": "authorized: 3 sequential markets",
     "per_market_cap_usd": "0.05", "residual_units": 3, "markets_per_campaign": 3,
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "lever": "baseline", "promote": {"settled_markets_clean": 3,
                                      "fill_rate_min": 0.15,
                                      "shadow_mix_l1_max": 0.30,
                                      "adverse_selection_c_max": 5.0}},
    {"id": "R2", "name": "windows/day: 6 markets, 2 campaigns",
     "per_market_cap_usd": "0.05", "residual_units": 3, "markets_per_campaign": 6,
     "campaigns_per_day": 2, "max_actions_per_session": 6, "concurrency": 1,
     "lever": "windows_per_day", "promote": {"settled_markets_clean": 8,
                                             "fill_rate_min": 0.20,
                                             "shadow_mix_l1_max": 0.25,
                                             "matched_pairs_min": 1,
                                             "adverse_selection_c_max": 4.0}},
    {"id": "R3", "name": "actions/window: 12 actions per session",
     "per_market_cap_usd": "0.05", "residual_units": 3, "markets_per_campaign": 6,
     "campaigns_per_day": 2, "max_actions_per_session": 12, "concurrency": 1,
     "lever": "actions_per_window", "promote": {"settled_markets_clean": 16,
                                                "fill_rate_min": 0.20,
                                                "shadow_mix_l1_max": 0.20,
                                                "matched_pairs_min": 3,
                                                "adverse_selection_c_max": 3.0}},
    # was "R4"; renamed when the owner approved the REAL R4 (5567269295) so
    # the two can never share an id. This prebuilt rung never ran and is
    # reachable only through the inert automatic ladder (next_rung/evaluate).
    {"id": "R4-AUTO", "name": "size: $0.10 per market (prebuilt, never run)",
     "per_market_cap_usd": "0.10", "residual_units": 3, "markets_per_campaign": 6,
     "campaigns_per_day": 2, "max_actions_per_session": 12, "concurrency": 1,
     "lever": "size", "promote": {"settled_markets_clean": 30,
                                  "fill_rate_min": 0.25,
                                  "shadow_mix_l1_max": 0.20,
                                  "matched_pairs_min": 6,
                                  "adverse_selection_c_max": 3.0}},
    {"id": "R5", "name": "concurrency 2",
     "per_market_cap_usd": "0.10", "residual_units": 3, "markets_per_campaign": 6,
     "campaigns_per_day": 2, "max_actions_per_session": 12, "concurrency": 2,
     "lever": "concurrency", "promote": None},   # top of the prebuilt ladder
)

# OWNER 5560851070 (REPLICATE -> PROVE FIDELITY (behavioral) -> RUN): the
# smallest rung that lets materially more of the proven L5 policy reach
# the venue, derived from the R1 cohort's 203 clean decisions rather than
# picked: every one of the 36 residual-cap forcings fired at exactly 3
# units while L5's own draw at that point was EXTEND 61% of the time and
# 75% of L5's post-fill transitions sit above 5 units; the 16 spend-cap
# forcings fired at $0.054-$0.112 of filled notional. Replaying the
# cohort's measured draw masses and fill rates: residual cap 6 removes
# ~92% of the residual-cap forcing (14.5% -> 1.2% of post-fill decisions),
# a spend cap of $0.15 clears every observed spend forcing; releasing
# both moves the cohort's acted L1 from 0.54 to ~0.05-0.08 and the acted
# post-fill gap from 30 pp to ~4-5 pp. Only the two caps change: maker
# only, fee-safe 0.98, HOLD-to-settlement, concurrency 1, 6 actions per
# session, the deadman and the -$1.00 daily stop (>= the $0.90 per-campaign
# worst case) are unchanged. Outside the L5-order ladder above; never
# promoted to or from automatically.
OWNER_RUNGS: tuple[dict, ...] = (
    # OWNER AUTHORIZATION 2026-09-16 "Option A". The ONLY purpose of this rung
    # is to make the existing min(rung, mandate) architecture capable of
    # exercising the separately authorized live mandate. It is NOT a ladder
    # position, NOTHING promotes to or from it (`promote: None`), and it is
    # deliberately in OWNER_RUNGS rather than RUNGS so the auto-ladder's lever
    # order is untouched.
    #
    # WHY residual_units STAYS 3 AND IS NOT AN APPROXIMATION OF $3.00.
    # The owner required that a contract count must never be able to exceed the
    # mandate's $3.00 economic residual ceiling. A prediction-market contract
    # settles at $0 or $1, so its cost basis is at most $1.00 and THREE
    # unmatched contracts carry at most $3.00 of economic exposure -- the bound
    # holds at every price, by the instrument's definition, not by a price
    # assumption. The transport's $3.00 `max_residual` remains the authoritative
    # economic ceiling; this unit count can only ever bind EARLIER, never later.
    #
    # BREADTH WIDENED 3 -> 10 BY OWNER AUTHORIZATION 2026-09-17, and ONLY
    # breadth. Every other lever is untouched: $5.00/market, 1 concurrent, 6
    # actions per session, residual 3 units, maker-only, fee-safe completion,
    # $0.10/$0.125 sizing, the -$6.00 daily stop.
    #
    # WIDENED IN PLACE RATHER THAN AS A NEW RUNG, deliberately. Durable state
    # stores the rung ID and `known_rung()` resolves it against this table, so
    # a NEW id (pinned) against the stored "CP1" would have met B3's
    # `tightest()` -- markets_per_campaign IS a risk dimension -- and the
    # stored 3 would have silently clamped a 10-market authorization back to
    # 3. Widening CP1 itself means the stored id resolves to these values and
    # nothing is clamped. The identity strings are updated with it so the rung
    # does not go on describing itself as the three-market one.
    #
    # WHAT NOW BINDS THE CAMPAIGN'S DOLLARS. `worst_case()` derives
    # per_campaign as cap x markets, so it reads $5.00 x 10 = $50.00 where it
    # used to read $15.00. That is a true statement about THE RUNG, and it is
    # no longer the binding constraint: the owner held max_total_spend at
    # $15.00 while widening breadth, and the immutable mandate enforces that
    # at the wire through GuardedTransport. At 3 markets the two coincided at
    # $15.00 and nothing distinguished them; at 10 they do not, and the
    # MANDATE is the campaign-spend ceiling. Breadth buys observations, not
    # dollars.
    {"id": "CP1", "name": "bounded continuous live probe, 10 markets "
                          "(OWNER 2026-09-17 execution-measurement breadth)",
     "per_market_cap_usd": "5.00", "residual_units": 3, "markets_per_campaign": 10,
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "daily_loss_stop_usd": "-6.00",
     # OWNER AUTHORIZATION 2026-09-16 "CP1 explicit sizing fix". STATED, not
     # inherited. CP1 previously named neither lever, and absence is not "no
     # lever" -- it is DEFAULT_TARGET_NOTIONAL $0.01 / DEFAULT_MAX_LEG_NOTIONAL
     # $0.02, so the probe sized at $0.0090 per action and ~$0.054 per market
     # against a $5.00 authorization: the cap was raised but the spend was not.
     #
     # DERIVED, NOT PICKED, from the frozen historical evidence. L5's own
     # per-fill median is 8.84-25.0 contracts by price band (l5_behavior.json
     # q5, 71,982 buy fills) and $1.88-$23.55 (l5_analysis_stats.json q1);
     # scaled by the authorized k=0.00619 that is 0.055-0.155 contracts and
     # $0.012-$0.146, with p75 reaching $0.285. These values put our action
     # INSIDE that k-scaled distribution rather than outside it.
     #
     # SMALLEST PRACTICAL, as the owner required -- not the largest that fits.
     # $0.10/$0.125 yields 0.11-0.22 contracts (11-22 lots of the 0.01 venue
     # minimum), which is the floor at which a PARTIAL fill and a queue
     # position are observable at all; 2 lots under the micro default are not.
     # Larger candidates were computed and rejected: $0.25/$0.40 reaches 27.5x
     # the micro regime but pushes the worst-case residual overshoot to $0.64.
     #
     # WHY max_leg ABOVE target: max_leg bounds the WORST binary leg, which is
     # max(p, 1-p) >= 0.5, so a target reachable at mid prices needs headroom
     # or choose_micro_quantity Halts MICRO_MIN_LOT_EXCEEDS_MAX at the tails.
     # target > max_leg Halts MICRO_BAD_NOTIONAL_BOUNDS, so the order matters.
     #
     # NOT R3L's $2.50/$3.25. Those are 25x and 26x these and are explicitly
     # excluded; B3's intersection keeps them out even if R3L is on the volume.
     "target_notional_usd": "0.10", "max_leg_notional_usd": "0.125",
     "lever": "caps", "promote": None,
     "derived_from": "OWNER live authorization 2026-09-17 "
                     "(execution-measurement breadth), superseding the "
                     "2026-09-16 three-market Option A: max_spend_per_market "
                     "$5.00, max_residual $3.00, max_total_spend $15.00 "
                     "UNCHANGED, max_markets 1, max_total_markets 3 -> 10, "
                     "daily stop $6.00, 24h expiry, k 0.00619 unchanged, "
                     "sizing $0.10/$0.125 unchanged. BREADTH ONLY."},
    {"id": "R2C", "name": "caps: $0.15 per market, residual 6 units (OWNER 5560851070)",
     "per_market_cap_usd": "0.15", "residual_units": 6, "markets_per_campaign": 6,
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "lever": "caps", "promote": None,
     "derived_from": "R1 cohort 2026-09-06, 203 clean decisions / 9 markets"},
    # OWNER live decision 2026-09-06 (uptime): R2C's caps EXACTLY, with
    # concurrency 2. Measured cause: under concurrency 1 the campaign is
    # idle whenever one market is held to settlement -- 19:51Z to 21:28Z
    # (97 min) on one college-football total, then held again minutes
    # later. L5's own engine never had this problem because its windows
    # settled every 5-15 minutes; multi-hour sports markets make the
    # hold-to-settlement rule a throughput ceiling, not a risk control.
    # Nothing about the money limits changes except the number of markets
    # that may be OPEN at once: same $0.15 per market, same residual 6,
    # same -$1.00 daily stop; only the CONCURRENT exposure
    # moves, $0.15 -> $0.30. One session at a time, one outstanding
    # order, maker-only, fee-safe 0.98 and HOLD-to-settlement are all
    # untouched: the campaign works its held markets round-robin.
    # OWNER live 2026-09-07 ("keep going, let's go live"): shortened from 6
    # markets to 3. The shell is already PROVEN in production -- 23:59-00:22Z
    # the campaign held two markets and worked them round-robin, one session
    # at a time, with no concurrency_breach -- so the remaining three markets
    # would only delay the owner-approved SIZED rung behind it. Strictly less
    # exposure: worst case per campaign $0.90 -> $0.45.
    {"id": "R2C2", "name": "R2C caps, concurrency 2 (OWNER live 2026-09-06)",
     "per_market_cap_usd": "0.15", "residual_units": 6, "markets_per_campaign": 3,
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 2,
     "lever": "concurrency", "promote": None,
     "derived_from": "R2C cohort 2026-09-06: 97 minutes idle on one held "
                     "market, 3 of 6 markets filled in 2 hours"},
    # OWNER 5561465636 (LIVE AUTHORIZATION, $110 account): the first
    # L5-SIZED rung, derived from L5's own per-action distribution
    # (analysis/l5/l5_canonical_sequence.csv.gz, 55,647 maker actions:
    # shares p10/p25/p50 = 2 / 5 / 10.7, dollars p25/p50 = $0.94 / $3.73;
    # window spend p25/p50 = $32 / $93; final |residual| p50 = 55 shares)
    # and the venue's published maker-rebate arithmetic (0.0125 x C x
    # p(1-p), rounded to the cent): the smallest per-action size that is
    # inside L5's own size distribution (5 shares = L5's maker p25) AND
    # produces a non-rounded rebate at every price in the 35-65 band
    # (5 x 0.0125 x 0.2275..0.25 = $0.014-0.016 -> $0.01-0.02 per fill,
    # vs $0.000062 -> $0.00 at the R1/R2C 0.02 shares). $2.50 target at
    # the entry price (5.0 shares at 0.50, 5.55 at 0.45, 3.85 at 0.65),
    # either leg <= $3.25. Per-market cap $7.50 = three first legs (23% of
    # L5's p25 window spend, the honest bound), residual cap 6 units
    # (~30 shares, 55% of L5's median final residual), 3 markets per
    # campaign, 1 campaign per day, concurrency 1, 6 actions per session.
    # Worst case $7.50 per market / $22.50 per campaign (20.5% of the
    # $110 account; needs three consecutive markets to lose every first
    # leg with no pair formed); the rung's own daily realized-loss stop
    # is -$15.00 (13.6%), checked before every session, so the per-day
    # bound is the stop plus at most the one in-flight market. Ends at
    # CAMPAIGN_RUNG_COMPLETE for the owner: no automatic promotion and
    # no automatic repeat.
    {"id": "R3L", "name": "L5 size: $2.50 per action (~5 shares), $7.50 per market, "
                          "residual 6 units, 3 markets (OWNER 5561465636)",
     "per_market_cap_usd": "7.50", "residual_units": 6, "markets_per_campaign": 3,
     # OWNER DECISION 5564173746 (2026-09-07T02:23:51Z): "Keep execution
     # concurrency = 1 for R2C/R3L." One market at a time, its full L5
     # inventory sequence run to terminal, then rotate to the next best.
     # This REVERSES the concurrency 2 I applied at 01:00Z on the earlier
     # chat decision: replicating L5 comes first, and concurrency >1 is to
     # be proposed only when L5's OWN history supports it, the bankroll
     # supports it, and R3L has shown clean sized fills -- never to
     # manufacture throughput. Max concurrent exposure returns to $7.50.
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "target_notional_usd": "2.50", "max_leg_notional_usd": "3.25",
     "daily_loss_stop_usd": "-15.00", "lever": "size", "promote": None,
     "derived_from": "L5 canonical maker actions (shares p25 = 5.0) + published "
                     "rebate rounding (>= 1.62 shares for one cent at 0.45); "
                     "R1/R2C cohorts 2026-09-06"},
    # OWNER DECISION 5567269295 (2026-09-07T07:53:37Z): "APPROVE R4, with one
    # accounting/economic guardrail." Built around the demonstrated transfer
    # mismatch -- residual DURATION in sports -- and nothing else:
    #   * size UNCHANGED from R3L ($2.50 per action, either leg <= $3.25,
    #     $7.50 per market, 3 markets, concurrency 1, -$15.00 daily stop);
    #   * the residual cap becomes DOLLARS: at most $2.50 of UNMATCHED
    #     exposure per market (unmatched shares x their own average cost,
    #     the amount lost if that side expires worthless). Anchored to L5's
    #     own envelope (analysis/l5/l5_residual_risk.json): at material size
    #     L5 held peak unmatched to 10.8-13.7% of deployed capital, which at
    #     our $7.50 cap is $1.03 -- below one action -- so one action is the
    #     tightest implementable cap. The unit cap (6) is kept as the
    #     behavioral EXTEND bound; the dollar cap binds first;
    #   * the completion bound rises 0.98 -> 1.00 for COMPLETE (deficit-side)
    #     orders only: the MEASURED R3L rebate ($0.2800 on $34.6402 filled,
    #     0.808%) pays for a pair up to ~1.003, and the owner's guardrail is
    #     that 1.00 is a HARD ceiling -- "do not operationally spend rebate
    #     beyond par" until R4's own credited rebates are in hand. The ">=1c
    #     after known fees" rule still governs OPEN/EXTEND; par is permitted
    #     only where it REDUCES unmatched exposure.
    # Campaign worst case in dollars AT RISK: 3 x $2.50 = $7.50 (6.9% of the
    # $108.9985 balance at approval); capital DEPLOYED is still bounded by
    # 3 x $7.50 = $22.50 and the sized-rung preflight keeps requiring it.
    # Ends at CAMPAIGN_RUNG_COMPLETE; "No automatic R5 promotion."
    {"id": "R4", "name": "L5 size, residual in DOLLARS: $2.50 unmatched cap, "
                         "completion to par (OWNER 5567269295)",
     "per_market_cap_usd": "7.50", "residual_units": 6, "markets_per_campaign": 3,
     "max_unmatched_usd": "2.50", "pair_cost_max": "1.00",
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "target_notional_usd": "2.50", "max_leg_notional_usd": "3.25",
     "daily_loss_stop_usd": "-15.00", "lever": "residual_duration", "promote": None,
     "derived_from": "R3L (19 fills, -$1.5302 net, reconciled to the cent); L5 "
                     "residual envelope 2,216 windows (peak unmatched / spend "
                     "0.108-0.137 at material size); measured maker rebate "
                     "0.808% of filled dollars"},
    # OWNER PROGRAM STATEMENT (chat 2026-09-07 17:55Z, recorded as #167
    # 5574156328): "L5 is the strategy. ... If L5 says OPEN, we open. If
    # he says EXTEND, our goal is to extend. If he says COMPLETE at 1.02,
    # our replication target is 1.02 -- not an arbitrary 1.00 ... Any
    # safety override gets recorded explicitly as our deviation from L5.
    # We still need a hard catastrophic-loss shell around the ~$111
    # account, but inside that shell I want maximum L5 fidelity."
    # R5F = the FIDELITY rung, built from the restriction audit
    # (analysis/l5/l5_restriction_audit.json, 427 R4/R3L decisions):
    #   REMOVED  pair_cost_ceiling   -> complement_pricing "l5_touch": a
    #            COMPLETE is priced at L5's touch (join the deficit-side
    #            bid), above par included; the pair it implies is
    #            REPORTED, never gated. Par is no longer a ceiling.
    #   REMOVED  unmatched_usd_cap   -> None: an EXTEND is refused only by
    #            the per-market spend cap and the residual-units bound.
    #   REMOVED  maker_rest_30s      -> rest_policy "model": the order
    #            rests for the model's own next_reevaluation_s (floor 5 s),
    #            cancelled early when the action stops being L5's action.
    #   KEPT     concurrency_1 (initially): OWNER 5574408755 "Preserve
    #            one-market-at-a-time initially unless L5 concurrency
    #            evidence supports otherwise"; the L5 study
    #            (L5_CONCURRENCY_AND_SWITCHING.md) found
    #            max_markets_open_within_one_lane == 1 in every mode, so
    #            concurrency 1 IS L5's distribution; >1 would be N tracks
    #            following turnover, a later rung on that evidence.
    #   REMOVED  cohort_3_markets    -> markets_per_campaign None: the
    #            campaign is CONTINUOUS, bounded by the daily stop and the
    #            horizon, not by a market count.
    #   KEPT     action_size $2.50 / leg $3.25 (bankroll), per-market cap
    #            $7.50, the frozen strict evidence gate, maker-only
    #            (post-only), the off-distribution hold, daily stop -$15.
    # The shell: max simultaneous dollars $7.50 (one market, the spend
    # cap; all of it can be unmatched), preflight $7.50 x 3 = $22.50 of
    # buying power, daily realized-loss stop -$15.00 (+ one in-flight cap
    # = $22.50 worst day) -- ~20% of the $111.14 balance. Every remaining
    # restriction that binds on a decision is written on that decision as
    # the deviation from L5.
    {"id": "R5F", "name": "L5 FIDELITY: complete at L5's touch, no unmatched "
                          "dollar cap, model rest, one market at a time, "
                          "continuous (OWNER PROGRAM 5574156328 / 5574408755)",
     "per_market_cap_usd": "7.50", "residual_units": 6, "markets_per_campaign": None,
     "max_unmatched_usd": None, "pair_cost_max": None,
     "complement_pricing": "l5_touch", "rest_policy": "model", "rest_floor_s": "5",
     "campaigns_per_day": 1, "max_actions_per_session": 6, "concurrency": 1,
     "max_sessions_per_campaign": 10080,
     "target_notional_usd": "2.50", "max_leg_notional_usd": "3.25",
     "daily_loss_stop_usd": "-15.00", "lever": "fidelity", "promote": None,
     "derived_from": "R4 (3 markets, $2.50 unmatched cap, par ceiling) + the "
                     "L5 restriction audit: 4 restrictions removed (pair "
                     "ceiling, unmatched cap, 30 s rest, 3-market cohort), "
                     "concurrency 1 kept on L5's own one-lane evidence, 2 "
                     "staged (observation_band, maker_only), 3 kept "
                     "(action_size, off_distribution_hold, strict_evidence_gate)"},
    # OWNER DECISION 2026-09-11 ("go 3" + "Join the touch, hard cap <=1.00").
    # R5F-T is R5F WITH ITS OWN APPROVAL HONOURED. The 2026-09-07 approval row
    # reads "L5-touch COMPLETE pricing; above-par pair cost is
    # reported/accounted, NOT behaviorally gated" -- and R5F shipped
    # pair_cost_max: None, which falls through to DEFAULT_PAIR_COST_MAX 0.98.
    # Measured consequence on 2382 live complement preflights: pair_cost_max
    # read 0.98 on 2215 of them and the chosen price sat 2c below the opposing
    # ask on 2258, which is behind the touch -- and complements behind the
    # touch traded through 0.74% of the time against 47.6% at two ticks.
    #
    # This rung states the ceiling EXPLICITLY at par rather than inheriting a
    # default. Par remains absolute: pair_cost_max() floors every rung at 1.00
    # whatever it asks for, so this cannot buy a pair that settlement cannot
    # return. Only the price lever moves; every other R5F lever is copied
    # unchanged so a fill difference is attributable to price and nothing else.
    #
    # Sized as a CANARY, not a campaign: one market, one campaign, smaller
    # notional, fewer actions. The measurements are the return here, not P&L.
    {"id": "R5FT", "name": "R5F AT THE TOUCH: complement joins the best "
                           "non-crossing maker price, pair capped at par "
                           "(OWNER 2026-09-11 canary)",
     # OWNER 2026-09-11 "lets raise it to $30 or something", answering BOTH
     # to which number: the per-market cap 3.00 -> 30.00 AND the daily
     # realized-loss stop -15.00 -> -30.00. Worst day is the stop plus one
     # in-flight market = $60 against a ~$109 balance.
     #
     # THE ACTION SIZE IS DELIBERATELY NOT RAISED WITH THEM. CLAUDE.md:
     # "Favor increasing frequency before size." $30 is therefore reached
     # by MANY $1.00 maker actions accumulating against the per-market cap
     # across sessions, not by one $10 swing. residual_units stays 6, so
     # matched inventory may grow to the cap while ONE-SIDED exposure
     # stays where the canary put it -- the dollar cap governs capital,
     # the unit cap governs direction, and raising one does not raise the
     # other.
     "per_market_cap_usd": "30.00", "residual_units": 6,
     "markets_per_campaign": 1,
     "max_unmatched_usd": None, "pair_cost_max": "1.00",
     "complement_pricing": "l5_touch", "rest_policy": "model", "rest_floor_s": "5",
     "campaigns_per_day": 1, "max_actions_per_session": 4, "concurrency": 1,
     "max_sessions_per_campaign": 10080,
     "target_notional_usd": "1.00", "max_leg_notional_usd": "1.50",
     "daily_loss_stop_usd": "-30.00", "lever": "price", "promote": None,
     "derived_from": "R5F with pair_cost_max stated at par instead of "
                     "inheriting 0.98, per the rung's own 2026-09-07 approval "
                     "text; one market, $1.00 actions, and the owner's "
                     "2026-09-11 $30 capital/stop raise"},
)
ALL_RUNGS: tuple[dict, ...] = RUNGS + OWNER_RUNGS
# fail closed at import: rung() returns the FIRST id match, so a duplicate id
# would silently run one rung's money limits under another rung's name
assert len({r["id"] for r in ALL_RUNGS}) == len(ALL_RUNGS), \
    "duplicate rung id in the ramp ladder"
DEFAULT_PAIR_COST_MAX = D("0.98")      # the fee-safe observation cap, R1..R3L
DEFAULT_RESIDUAL_UNITS = 3
DEFAULT_TARGET_NOTIONAL = D("0.01")     # R1..R2C per-action target (micro)
DEFAULT_MAX_LEG_NOTIONAL = D("0.02")    # R1..R2C either-leg bound (micro)


def rung(rung_id: Optional[str] = None) -> dict:
    rid = (rung_id or os.getenv(RUNG_ENV, "R1")).strip().upper()
    for r in ALL_RUNGS:
        if r["id"] == rid:
            return dict(r)
    return dict(RUNGS[0])                 # unknown -> the authorized rung


# The ACTIVE rung follows the campaign, not the process: the continuous
# session sets it from its durable state at boot and from the owner
# approval it consumes at the advance gate, so a held R1 market keeps R1
# caps while the next campaign runs under its own rung. Unset = the env
# rung (default R1).
_ACTIVE: dict = {"id": None, "rung": None}


def set_active(rung_id: Optional[str]) -> dict:
    r = rung(rung_id) if rung_id else None
    _ACTIVE["id"] = r["id"] if r else None
    _ACTIVE["rung"] = None          # an id selection drops any intersection
    return active()


def set_active_effective(r: Mapping[str, Any]) -> dict:
    """Make an EXACT rung dict active, not an id lookup (OWNER "B3").

    `tightest()` returns a rung that is CP1 by identity but carries tightened
    numbers, so it cannot be re-derived from its id -- `rung("CP1")` would
    hand back the untightened table entry. The dict itself has to be what the
    accessors read, or the intersection would be computed and then thrown
    away on the next `active()` call.
    """
    _ACTIVE["rung"] = dict(r)
    _ACTIVE["id"] = str(r.get("id") or "") or None
    return active()


def known_rung(rung_id: Optional[str]) -> Optional[dict]:
    """The rung with this id, or None if there is no such rung.

    `rung()` falls back to R1 for an unknown id, which is right for an env
    typo -- the tightest rung is the safe default -- but wrong for stored
    state: an unrecognizable stored id is not evidence of a tighter
    authorization, and silently intersecting against R1 would shrink the
    owner's pinned envelope by 100x without saying so.
    """
    rid = (rung_id or "").strip().upper()
    for r in ALL_RUNGS:
        if r["id"] == rid:
            return dict(r)
    return None


def active() -> dict:
    if _ACTIVE.get("rung") is not None:
        return dict(_ACTIVE["rung"])
    return rung(_ACTIVE["id"]) if _ACTIVE["id"] else rung()


def shell_version(r: Optional[Mapping[str, Any]] = None) -> str:
    """The safety-shell label written on every live decision row
    (OWNER 5560559108 #5); R1 renders exactly the label the cohort carries."""
    r = dict(r) if r is not None else active()
    if complement_pricing(r) == "l5_touch":
        # OWNER PROGRAM 5574156328: the fidelity rung names every lever it
        # removed so the frozen evaluator splits this cohort from R4
        rest = (f"rest-model{rest_floor_s(r)}s" if rest_policy(r) == "model"
                else "rest30s")
        label = (f"{r['id']}/cap{r['per_market_cap_usd']}"
                 f"/unmatched-none/complete-l5touch"
                 f"/conc{r['concurrency']}/maker-only/{rest}")
    elif r.get("max_unmatched_usd"):
        # OWNER 5567269295: a dollar-residual rung names BOTH levers it
        # changed -- the unmatched cap and the completion ceiling -- so the
        # frozen evaluator splits this cohort from every fee-safe0.98 one
        label = (f"{r['id']}/cap{r['per_market_cap_usd']}"
                 f"/unmatched{r['max_unmatched_usd']}"
                 f"/complete{r.get('pair_cost_max') or DEFAULT_PAIR_COST_MAX}"
                 f"/conc{r['concurrency']}/maker-only/rest30s")
    else:
        label = (f"{r['id']}/cap{r['per_market_cap_usd']}"
                 f"/residual{int(r.get('residual_units') or DEFAULT_RESIDUAL_UNITS)}"
                 f"/conc{r['concurrency']}/maker-only/fee-safe0.98/rest30s")
    if r.get("target_notional_usd"):
        # OWNER 5561465636: a sized rung carries its per-action size in
        # the label; the R1 / R2C labels are unchanged
        label += f"/action{r['target_notional_usd']}"
    return label


def max_unmatched_usd(r: Optional[Mapping[str, Any]] = None) -> Optional[D]:
    """The ACTIVE rung's cap on UNMATCHED dollars per market (OWNER
    5567269295), or None for a rung that caps residual in units only."""
    r = dict(r) if r is not None else active()
    v = r.get("max_unmatched_usd")
    return D(str(v)) if v else None


def pair_cost_max(r: Optional[Mapping[str, Any]] = None) -> D:
    """The ACTIVE rung's HARD ceiling on a completed pair's cost: 0.98
    (fee-safe observation cap) unless the owner's rung states its own.
    Never read above 1.00 -- par is the owner's guardrail, whatever a
    rung says."""
    r = dict(r) if r is not None else active()
    v = D(str(r.get("pair_cost_max") or DEFAULT_PAIR_COST_MAX))
    return min(v, D("1.00"))


# OWNER PROGRAM 5574156328 (R5F): the fidelity levers. Each reads None /
# the R4 default for every rung that does not state it, so R1..R4 run
# exactly as before.
def complement_pricing(r: Optional[Mapping[str, Any]] = None) -> str:
    """How a COMPLETE is priced: "fee_safe" (R1..R4: the tighter of the
    rung ceiling and the >=1c bound) or "l5_touch" (R5F: L5's own target,
    the deficit-side touch, above par included; the implied pair is
    reported, not gated)."""
    r = dict(r) if r is not None else active()
    return str(r.get("complement_pricing") or "fee_safe")


def rest_policy(r: Optional[Mapping[str, Any]] = None) -> str:
    """"fixed30" (R1..R4: a maker order rests up to 30 s) or "model"
    (R5F: it rests for the model's own next_reevaluation_s, floored)."""
    r = dict(r) if r is not None else active()
    return str(r.get("rest_policy") or "fixed30")


def rest_floor_s(r: Optional[Mapping[str, Any]] = None) -> D:
    r = dict(r) if r is not None else active()
    return D(str(r.get("rest_floor_s") or "5"))


def markets_per_campaign(r: Optional[Mapping[str, Any]] = None) -> Optional[int]:
    """The rung's market allowance per campaign; None = CONTINUOUS (R5F:
    bounded by the daily stop and the horizon, not a market count)."""
    r = dict(r) if r is not None else active()
    v = r.get("markets_per_campaign")
    return int(v) if v is not None else None


def max_sessions_per_campaign(r: Optional[Mapping[str, Any]] = None,
                              default: int = 48) -> int:
    r = dict(r) if r is not None else active()
    return int(r.get("max_sessions_per_campaign") or default)


def target_notional(r: Optional[Mapping[str, Any]] = None) -> D:
    """Per-action maker target notional of the ACTIVE rung (OWNER
    5561465636 size lever); rungs without one keep the micro default."""
    r = dict(r) if r is not None else active()
    return D(str(r.get("target_notional_usd") or DEFAULT_TARGET_NOTIONAL))


def max_leg_notional(r: Optional[Mapping[str, Any]] = None) -> D:
    r = dict(r) if r is not None else active()
    return D(str(r.get("max_leg_notional_usd") or DEFAULT_MAX_LEG_NOTIONAL))


# OWNER 2026-09-11, after seeing what an account-wide override would cost:
# "Do not make -$30 override R1/R2/R4 or any historical/micro-rung stop ...
# effective stop = the stop belonging to the currently authorized
# campaign/rung."
#
# An earlier commit (2234bd1e) DID make it account-wide and was reverted
# here. It was reverted because it silently rewrote the binding stop of every
# rung ever defined, including the -$1.00 the R1/R2C micro rungs carry, to
# unblock one boot. The transition case it was trying to solve is real and is
# now handled where it belongs: src/pmus_transition_stop.py, which lets the
# BOOT PREFLIGHT -- and only the boot preflight -- evaluate the stop of a
# rung the owner has an exact unconsumed approval for, before that approval
# is consumed. Every other reader keeps the active rung's own number.
def daily_loss_stop(r: Optional[Mapping[str, Any]] = None) -> D:
    """The daily realized-loss stop the ACTIVE rung runs under: the
    absolute -$1.00 unless the owner's rung states its own, scaled to
    its worst case (OWNER 5560851070 / 5561465636).

    There is NO account-wide override. A rung's declared stop is its
    binding stop. The one exception is the boot re-arm preflight, which
    may consult pmus_transition_stop.transition_stop() for an approved
    pending transition; that never changes what this function returns."""
    r = dict(r) if r is not None else active()
    return D(str(r.get("daily_loss_stop_usd") or DAILY_LOSS_STOP))


# --------------------------------------------------------------------------
# OWNER AUTHORIZATION 2026-09-16 "B3": a stored rung may TIGHTEN the pinned
# configuration. It may never loosen, enlarge or replace it.
#
# THE DEFECT THIS CLOSES. `run_campaign` restores durable campaign state on
# the ARMED path only and calls `_apply_rung(persisted["rung"])`, which called
# `set_active()` and REPLACED the image-pinned rung wholesale. On 2026-09-16
# the volume carried "R3L" -- the 2026-09-16 breach's own end state, a $7.50
# rung -- so a process pinned to CP1 would have armed as R3L. The halted
# process disclosed CP1 and the armed process would have run R3L.
#
# WHY THIS IS NOT A LADDER-ORDER COMPARISON. The owner was explicit: "Do not
# simply choose a rung by name/order unless the ordering is formally proven to
# mean 'no less permissive' across every risk dimension." It is not proven and
# it is not true -- R2C2 has concurrency 2 where R3L has 1, while R3L has the
# larger cap, so neither dominates the other. So this compares the EFFECTIVE
# value of every dimension independently and takes the tighter one of each.
#
# EFFECTIVE, NOT DECLARED. Every comparison runs on the value the accessors
# actually yield, defaults applied. That matters most where a rung states
# nothing: an absent `target_notional_usd` is not "no lever", it is the $0.01
# micro default, which is 250x TIGHTER than R3L's $2.50. Comparing raw dict
# values would have read absence as unknown and let R3L's sizing through.
#
# FAIL CLOSED ON ANYTHING UNCLASSIFIED. A key not named below is never taken
# from the stored rung -- the pinned value stands. `test_every_rung_key_is_
# classified` fails the suite if a rung ever gains a lever nobody sorted into
# one of these tables, so a new risk dimension cannot enter unnoticed.

#: For each risk-bearing dimension: the accessor that yields its EFFECTIVE
#: value, and which direction is tighter. "lower"/"higher" are numeric;
#: "meet" names an explicit lattice meet for a non-numeric lever.
#:
#: `absent` is the effective value when a rung states nothing, and it is the
#: whole reason this is a table rather than a dict comprehension: for some
#: dimensions absence is the TIGHTEST possible value (target_notional $0.01)
#: and for others it is the LOOSEST (max_unmatched_usd = no cap at all).
RISK_DIMENSIONS: dict[str, dict[str, Any]] = {
    "per_market_cap_usd":       {"kind": "decimal", "tighter": "lower"},
    "residual_units":           {"kind": "int",     "tighter": "lower"},
    "campaigns_per_day":        {"kind": "int",     "tighter": "lower"},
    "max_actions_per_session":  {"kind": "int",     "tighter": "lower"},
    "concurrency":              {"kind": "int",     "tighter": "lower"},
    # absent = the micro defaults, which are TIGHTER than any stated value
    "target_notional_usd":      {"kind": "decimal", "tighter": "lower",
                                 "absent": DEFAULT_TARGET_NOTIONAL},
    "max_leg_notional_usd":     {"kind": "decimal", "tighter": "lower",
                                 "absent": DEFAULT_MAX_LEG_NOTIONAL},
    "pair_cost_max":            {"kind": "decimal", "tighter": "lower",
                                 "absent": DEFAULT_PAIR_COST_MAX},
    # absent = NO dollar cap on unmatched, i.e. the loosest possible value,
    # so a stated cap legitimately tightens
    "max_unmatched_usd":        {"kind": "decimal", "tighter": "lower",
                                 "absent": None},
    # absent = CONTINUOUS, i.e. unbounded by market count
    "markets_per_campaign":     {"kind": "int",     "tighter": "lower",
                                 "absent": None},
    "max_sessions_per_campaign": {"kind": "int",    "tighter": "lower",
                                  "absent": None},
    # NEGATIVE dollars: -6.00 is TIGHTER than -15.00, so the tighter one is
    # the GREATER number. Getting this backwards is how a -$6 stop becomes a
    # -$15 one, so it is named rather than inferred from the sign.
    "daily_loss_stop_usd":      {"kind": "decimal", "tighter": "higher",
                                 "absent": DAILY_LOSS_STOP},
    # fee_safe never prices a completion above par; l5_touch may. The meet is
    # fee_safe, so either side asking for it wins.
    "complement_pricing":       {"kind": "meet", "absent": "fee_safe",
                                 "meet": ("fee_safe", "l5_touch")},
}

#: Dimensions with NO PROVEN ORDERING. The pinned value always wins, in both
#: directions: stored state cannot change them at all. Being honest that an
#: ordering is unproven is the point -- inventing one to look complete is how
#: a "tightening" silently loosens something.
UNORDERED_LEVERS: tuple[str, ...] = ("rest_policy", "rest_floor_s")

#: Not risk: identity, provenance and the promotion gate. Always the pinned
#: rung's, so a stored rung can never rename the pinned one into itself.
IDENTITY_KEYS: tuple[str, ...] = (
    "id", "name", "lever", "promote", "derived_from", "why")


def _effective(r: Mapping[str, Any], key: str) -> Any:
    """The value this dimension actually runs at, defaults applied."""
    spec = RISK_DIMENSIONS[key]
    v = r.get(key)
    if v is None or v == "":
        return spec.get("absent", None) if "absent" in spec else None
    return D(str(v)) if spec["kind"] == "decimal" else (
        int(v) if spec["kind"] == "int" else str(v))


def _tighter_of(key: str, a: Any, b: Any) -> Any:
    """The tighter of two EFFECTIVE values under this dimension's ordering."""
    spec = RISK_DIMENSIONS[key]
    if spec["kind"] == "meet":
        order = spec["meet"]                 # index 0 is the tightest
        ia = order.index(a) if a in order else len(order)
        ib = order.index(b) if b in order else len(order)
        return a if ia <= ib else b
    # None is the ABSENCE of a bound here (no cap / continuous), so it is the
    # loosest value and loses to any number.
    if a is None:
        return b
    if b is None:
        return a
    return (min(a, b) if spec["tighter"] == "lower" else max(a, b))


def tightest(pinned: Mapping[str, Any],
             stored: Optional[Mapping[str, Any]]) -> dict:
    """The pinned rung, tightened wherever `stored` is strictly more
    restrictive. Never looser than `pinned` on any dimension.

    Identity is always the pinned rung's, so CP1 stays CP1: a stored rung can
    restrict what CP1 does, never turn the probe into a different rung.
    """
    out = dict(pinned)
    if not stored:
        return out
    for key, spec in RISK_DIMENSIONS.items():
        p, s = _effective(pinned, key), _effective(stored, key)
        win = _tighter_of(key, p, s)
        if win == p:
            continue                       # pinned already at least as tight
        # Write the EFFECTIVE value, not the stored rung's raw one: the two
        # differ exactly when the stored rung states nothing and its default
        # is what actually binds.
        out[key] = str(win) if spec["kind"] == "decimal" else win
    return out


def next_rung(rung_id: str) -> Optional[dict]:
    ids = [r["id"] for r in RUNGS]
    i = ids.index(rung_id) if rung_id in ids else 0
    return dict(RUNGS[i + 1]) if i + 1 < len(RUNGS) else None


def worst_case(r: Mapping[str, Any]) -> dict:
    cap = D(str(r["per_market_cap_usd"]))
    markets = markets_per_campaign(r)
    concurrent = cap * r["concurrency"]
    stop = daily_loss_stop(r)
    if markets is None:
        # OWNER PROGRAM 5574156328 (R5F): a CONTINUOUS campaign has no
        # market count; capital DEPLOYED at once is bounded by the
        # concurrency, and the preflight demands three turns of it (the
        # in-flight markets plus the next cohort while they settle).
        # Dollars at risk per day are bounded by the daily stop.
        per_campaign = concurrent * 3
        per_day_raw = None
        per_day = -stop + cap
    else:
        per_campaign = cap * markets
        per_day_raw = per_campaign * r["campaigns_per_day"]
        # the stop is checked before every session, so the one in-flight
        # market can still lose its cap after the stop is reached
        per_day = min(per_day_raw, -stop + cap)
    out = {"per_market_usd": str(cap), "per_campaign_usd": str(per_campaign),
           "per_day_uncapped_usd": (str(per_day_raw) if per_day_raw is not None
                                    else "continuous: bounded by the daily stop"),
           "per_day_bounded_by_daily_stop_usd": str(per_day),
           "max_concurrent_open_exposure_usd": str(concurrent),
           "daily_loss_stop_usd": str(stop)}
    if markets is None:
        out["cohort"] = "continuous"
        out["preflight_basis"] = "per-market cap x concurrency x 3"
        out["max_at_risk_per_market_usd"] = str(cap)
        out["complement_pricing"] = complement_pricing(r)
        out["pair_cost_max"] = None
    if r.get("target_notional_usd"):
        out["per_action_target_usd"] = str(r["target_notional_usd"])
        out["per_leg_max_usd"] = str(r.get("max_leg_notional_usd"))
    if r.get("max_unmatched_usd"):
        # OWNER 5567269295: capital DEPLOYED (per_market / per_campaign
        # above) is still bounded by the spend cap; the dollars actually AT
        # RISK are the unmatched ones -- a matched pair pays $1 whatever
        # happens -- so both are reported and never conflated
        at_risk = D(str(r["max_unmatched_usd"]))
        out["max_at_risk_per_market_usd"] = str(at_risk)
        out["per_campaign_at_risk_usd"] = str(at_risk * (markets or 1))
        out["pair_cost_max"] = str(pair_cost_max(r))
    return out


def ladder() -> list[dict]:
    return [{**r, "worst_case": worst_case(r)} for r in ALL_RUNGS]


def evaluate(rung_id: str, ev: Mapping[str, Any]) -> dict:
    """PROMOTE / HOLD / ROLLBACK for the rung the evidence was gathered
    on. Missing evidence never promotes (fail closed); rollback needs a
    real breach, not a missing number."""
    r = rung(rung_id)
    reasons: list[str] = []
    verdict = "HOLD"
    wc = D(str(worst_case(r)["per_campaign_usd"]))
    mism = int(ev.get("mismatches") or 0)
    realized = ev.get("realized_usd")
    dd = ev.get("max_drawdown_usd")
    if mism > 0:
        verdict, reasons = "ROLLBACK", [f"mismatches={mism}"]
    elif dd is not None and D(str(dd)) <= -wc / 2:
        verdict, reasons = "ROLLBACK", [f"drawdown {dd} <= -half worst case {wc}"]
    elif realized is not None and D(str(realized)) <= -wc / 2:
        verdict, reasons = "ROLLBACK", [f"realized {realized} <= -half worst case {wc}"]
    else:
        crit = r.get("promote")
        if crit is None:
            reasons.append("top of prebuilt ladder")
        else:
            ok = True
            n = int(ev.get("settled_markets_clean") or 0)
            if n < crit["settled_markets_clean"]:
                ok = False; reasons.append(
                    f"settled_clean {n} < {crit['settled_markets_clean']}")
            fr = ev.get("fill_rate")
            if fr is None or float(fr) < crit["fill_rate_min"]:
                ok = False; reasons.append(f"fill_rate {fr} < {crit['fill_rate_min']}")
            l1 = ev.get("shadow_mix_l1")
            if l1 is None or float(l1) > crit["shadow_mix_l1_max"]:
                ok = False; reasons.append(f"shadow_mix_l1 {l1} > {crit['shadow_mix_l1_max']}")
            mp = crit.get("matched_pairs_min")
            if mp is not None and int(ev.get("matched_pairs") or 0) < mp:
                ok = False; reasons.append(f"matched_pairs {ev.get('matched_pairs')} < {mp}")
            pc = ev.get("pair_cost_max")
            if pc is not None and D(str(pc)) > D("0.98"):
                ok = False; reasons.append(f"pair_cost_max {pc} > 0.98")
            adv = ev.get("adverse_selection_c")
            if adv is None or float(adv) > crit["adverse_selection_c_max"]:
                ok = False; reasons.append(
                    f"adverse_selection_c {adv} not measured or > {crit['adverse_selection_c_max']}")
            if ok:
                verdict = "PROMOTE"
                reasons.append("all promotion criteria met on venue-confirmed evidence")
    nxt = next_rung(r["id"])
    return {"rung": r["id"], "verdict": verdict, "reasons": reasons,
            "next_rung": nxt["id"] if nxt else None,
            "next_worst_case": worst_case(nxt) if nxt else None,
            "activation": "owner sets PMUS_SPRING_RUNG; nothing promotes itself",
            "labels": "evidence = venue-confirmed FILLED->SETTLED->REALIZED plus "
                      "the campaign's own action-by-action shadow comparison"}


def evidence_from_campaign(report: Mapping[str, Any]) -> dict:
    """Pull the evidence keys the ladder scores from a campaign report
    (the continuous session's own structure). Adverse selection is only
    filled when the report carries it; otherwise None (fails closed)."""
    sessions = report.get("sessions") or []
    orders = [o for s in sessions for o in (s.get("orders") or [])]
    filled = [o for o in orders if str(o.get("outcome")) == "filled"]
    decisions = [d for s in sessions for d in (s.get("decisions") or [])]
    acted = [d.get("acted") for d in decisions if d.get("acted") in
             ("open", "extend", "complete")]
    shadow = [((d.get("shadow") or {}).get("stochastic") or {}).get("sampled")
              for d in decisions]
    shadow = [x for x in shadow if x in ("open", "extend", "complete")]

    def _mix(xs):
        n = len(xs) or 1
        return {k: xs.count(k) / n for k in ("open", "extend", "complete")}
    l1 = (sum(abs(_mix(acted)[k] - _mix(shadow)[k]) for k in
              ("open", "extend", "complete")) if acted and shadow else None)
    settles = report.get("settlements") or []
    clean = [s for s in settles if s.get("status") == "REALIZED"]
    gate = report.get("advance_gate") or {}
    mism = int(gate.get("inventory_mismatches") or 0) + \
        int(gate.get("orphan_cancels") or 0) + int(gate.get("halts") or 0)
    pairs = sum(float((s.get("breakdown") or {}).get("matched_pairs") or 0)
                for s in settles)
    pcs = [D(str((s.get("breakdown") or {}).get("matched_pair_cost")))
           for s in settles if (s.get("breakdown") or {}).get("matched_pair_cost")]
    realized = report.get("realized_pnl")
    return {"settled_markets_clean": len(clean) if mism == 0 else 0,
            "fill_rate": (len(filled) / len(orders)) if orders else None,
            "shadow_mix_l1": round(l1, 4) if l1 is not None else None,
            "matched_pairs": pairs,
            "pair_cost_max": str(max(pcs)) if pcs else None,
            "adverse_selection_c": report.get("adverse_selection_c"),
            "max_drawdown_usd": report.get("max_drawdown_usd"),
            "realized_usd": realized, "mismatches": mism}


def main() -> int:
    import json
    print(json.dumps({"active": rung()["id"], "ladder": ladder()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
