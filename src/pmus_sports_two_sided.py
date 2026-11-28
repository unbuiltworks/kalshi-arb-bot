"""Live sports: rest maker bids N ticks below the touch on BOTH sides.

OWNER 2026-11-14: "Just look at the live markets. Place orders 2 ticks below
the price on both sides." Bounded by the approved R5FB budget ($1.50 per
market, 3 markets, $4.50 total).

Per market, one pair at a time:
  YES bid = yes_bid - D ticks         NO bid = (1 - yes_ask) - D ticks
  pair cost = 1 - spread - 2D ticks < 1, so a completed pair is locked.
  Both legs unfilled -> follow the touch (keep D ticks under it).
  One leg filled     -> the other is the COMPLETION: it never moves down;
                        it may move up toward its own bid while the pair
                        stays <= PAIR_CAP (patient, fee-safe).
  Both filled        -> pair locked; start the next pair in that market.

RISK. Post-only maker orders only. QTY contracts per order. A market is
entered only when the account holds no position and no open order there
that this module did not place (the owner's manual trades, the BTC bots).
Unmatched (lone-leg) cost is capped per market and in total; at the cap no
new pair opens anywhere. Worst case = total lone-leg cost <= TOTAL_CAP.
Kill switch: PMUS_TWO_SIDED_ENABLED != YES -> cancel own orders, idle.
On exit (SIGTERM) every own open order is cancelled.

LABELS. Fills are venue-reported. A completed pair's edge is LOCKED pair
economics, not realized until settlement/redemption; a lone leg is open
directional exposure until the market settles.
"""
from __future__ import annotations

import json
import contextlib
import os
import re
import queue
import signal
import threading
import time
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal as D
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

ONE = D("1")

from src import pmus_rn1_replica as RN1  # noqa: E402  (default OFF)
from src import pmus_sports_phase as PH  # noqa: E402  (pure)


def _env_d(name: str, default: str) -> D:
    try:
        return D(str(os.getenv(name, default)).strip())
    except Exception:
        return D(default)


ENABLED_ENV = "PMUS_TWO_SIDED_ENABLED"
DEPTH_TICKS = int(os.getenv("PMUS_TWO_SIDED_DEPTH_TICKS", "2"))
# OWNER 2026-11-15 "when I was saying ticks I meant cents": openings sit
# OPEN_OFFSET_PX (2c) under each side's touch whatever the market's tick, so
# a half-cent CFB market opens 4 ticks deep, not 1c. depth_ticks(tick) turns
# that into ticks; PMUS_TWO_SIDED_OPEN_OFFSET_PX=0 restores DEPTH_TICKS.
OPEN_OFFSET_PX = _env_d("PMUS_TWO_SIDED_OPEN_OFFSET_PX", "0.02")


def depth_ticks(tick) -> int:
    t = D(str(tick or "0.01"))
    if OPEN_OFFSET_PX <= 0 or t <= 0:
        return DEPTH_TICKS
    return max(1, int((OPEN_OFFSET_PX / t).to_integral_value(
        rounding=ROUND_CEILING)))
QTY = _env_d("PMUS_TWO_SIDED_QTY", "1")
MAX_MARKETS = int(os.getenv("PMUS_TWO_SIDED_MAX_MARKETS", "3"))
# hard bound on markets tracked at once (opening slots + lone-leg markets)
MAX_TRACKED = int(os.getenv("PMUS_TWO_SIDED_MAX_TRACKED",
                            str(2 * MAX_MARKETS)))
PER_MARKET_CAP = _env_d("PMUS_TWO_SIDED_PER_MARKET_USD", "1.50")
TOTAL_CAP = _env_d("PMUS_TWO_SIDED_TOTAL_USD", "4.50")
PAIR_CAP = _env_d("PMUS_TWO_SIDED_PAIR_CAP", "0.98")
# OWNER 2026-11-15: tennis completes 88% of pairs, so keep more edge there.
PAIR_CAP_TENNIS = _env_d("PMUS_TWO_SIDED_PAIR_CAP_TENNIS", "0.96")
TENNIS_TAGS = ("-atp-", "-wta-", "-itfme-", "-itfwo-", "-utr-", "-atpdb-",
               "-wtadb-", "-atpcq-", "-wtt-")


def pair_cap(slug: str) -> D:
    return PAIR_CAP_TENNIS if any(t in "-" + str(slug) for t in TENNIS_TAGS) \
        else PAIR_CAP
BAND_LO = _env_d("PMUS_TWO_SIDED_BAND_LO", "0.25")
BAND_HI = _env_d("PMUS_TWO_SIDED_BAND_HI", "0.75")
MAX_SPREAD_TICKS = int(os.getenv("PMUS_TWO_SIDED_MAX_SPREAD_TICKS", "6"))
LOOP_S = float(os.getenv("PMUS_TWO_SIDED_LOOP_S", "5"))
# 2026-11-21 rate-limit audit: a 429 on the account read threw out of the
# loop and skipped EVERY market's management (fills, completions, cancels)
# that pass, ~3 times a minute. The read is now isolated: management always
# runs, but NEW exposure requires an account read no older than this.
ACCOUNT_FRESH_S = float(os.getenv("PMUS_TWO_SIDED_ACCOUNT_FRESH_S", "60"))
ACCOUNT_RETRY_S = float(os.getenv("PMUS_TWO_SIDED_ACCOUNT_RETRY_S", "10"))


def _pilot_since(raw: str) -> Optional[float]:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        from datetime import datetime
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()


#: RN1 live pilot (owner 2026-11-21): the meter counts markets the pilot
#: opened at/after PMUS_RN1_PILOT_SINCE (epoch s or ISO-8601; unset = all
#: RN1 markets); PMUS_RN1_PILOT_LOSS_USD > 0 latches a campaign stop at that
#: venue-settled net loss. Default 0 = no stop (the lock is the control).
PILOT_SINCE = _pilot_since(os.getenv("PMUS_RN1_PILOT_SINCE", ""))
PILOT_LOSS_USD = _env_d("PMUS_RN1_PILOT_LOSS_USD", "0")
#: with the stop on, a meter result older than this blocks new openings
#: (the audit that feeds it runs every REWARD_CHECK_S, default 1 h)
PILOT_METER_MAX_AGE_S = float(os.getenv("PMUS_RN1_PILOT_METER_MAX_AGE_S",
                                        str(2 * 3600 + 600)))
# SIGTERM / disable: per-order cancel attempts and the total time budget
# (Railway drains for 30 s).
CANCEL_ATTEMPTS = int(os.getenv("PMUS_TWO_SIDED_CANCEL_ATTEMPTS", "4"))
CANCEL_BUDGET_S = float(os.getenv("PMUS_TWO_SIDED_CANCEL_BUDGET_S", "20"))
# OWNER 2026-11-14 "If a pair is just sitting there for a while, cancel and
# resubmit closer": every STALE_S unfilled, one tick closer to the touch.
STALE_S = float(os.getenv("PMUS_TWO_SIDED_STALE_S", "60"))
OPEN_LADDER = os.getenv("PMUS_TWO_SIDED_OPEN_LADDER", "1").strip() != "0"
# OWNER 2026-11-14 "set up the daily reward credit check": the venue's own
# /v1/incentives/earnings (credited vs pending split) every REWARD_CHECK_S.
REWARD_CHECK_S = float(os.getenv("PMUS_TWO_SIDED_REWARD_CHECK_S", "3600"))
# CAMPAIGN ECONOMICS REPORT (owner 2026-11-26, default OFF; read-only,
# logging only; see src/pmus_campaign_economics.py). After each audit round
# (REWARD_CHECK then PILOT_PNL), at most once per ECONOMICS_MIN_S, one
# CAMPAIGN_ECONOMICS line joins by market the pilot meter's venue-reconciled
# trading with the venue reward rows for that market, by sport and for the
# campaign: settled trading P&L (realized), rewards PAID (realized income,
# separate), rewards PENDING / SKIPPED (not realized), net = settled + PAID.
ECONOMICS_REPORT = os.getenv("PMUS_ECONOMICS_REPORT", "0").strip() == "1"
ECONOMICS_MIN_S = float(os.getenv("PMUS_ECONOMICS_REPORT_MIN_S") or "600")
ECONOMICS_REWARD_ROWS = int(os.getenv("PMUS_ECONOMICS_REWARD_ROWS")
                            or "2000")
# OWNER 2026-11-14 "close out single-side fills early": a lone leg unpaired
# for EXIT_S is completed by TAKING the complement at its ask, but only while
# that pair costs <= EXIT_MAX_PAIR (a small, bounded realized loss). Beyond
# that the loss is already sunk and the maker completion keeps resting.
# OWNER 2026-11-15 "handle these stranded legs better": a stranded leg is
# cheap to close only in the first seconds; by minutes the price has run
# (aec-cs2-brute-eid: 10-lot YES@0.60 lone 2h, complement never back under
# a 1.07 pair, now ~-$5). Exit windows are now seconds, not minutes.
EXIT_S = float(os.getenv("PMUS_TWO_SIDED_EXIT_S", "30"))
EXIT_MAX_PAIR = _env_d("PMUS_TWO_SIDED_EXIT_MAX_PAIR", "1.03")
# OWNER 2026-11-14: college football swings from ~45c to <10c within minutes,
# past the 180s/1.03 exit. CFB lone legs exit sooner and accept a little more.
EXIT_S_CFB = float(os.getenv("PMUS_TWO_SIDED_EXIT_S_CFB", "20"))
EXIT_MAX_PAIR_CFB = _env_d("PMUS_TWO_SIDED_EXIT_MAX_PAIR_CFB", "1.05")


def exit_rule(slug: str) -> tuple:
    if "-cfb-" in slug:
        return EXIT_S_CFB, EXIT_MAX_PAIR_CFB
    return EXIT_S, EXIT_MAX_PAIR


# OWNER 2026-11-15 FEE-AWARE EXIT (DEFAULT ON). Venue audit: $3.76 of taker
# commissions on 578 trades since 2026-11-14T15:30Z and $0 maker rebates;
# the lone-leg taker EXIT pays the fee. The exit now takes only if
#   lead_px + take_px + taker_fee_per_contract(take_px) <= exit max pair,
# fee = TAKER_FEE_RATE * qty * p * (1 - p) rounded UP to the cent per order
# (as the venue rounds), and a lone leg larger than EXIT_MAX_TAKER_QTY
# contracts never taker-exits: it keeps maker-completing (stepping up as
# before). PMUS_TWO_SIDED_EXIT_FEE_AWARE=0 restores the fee-blind check;
# PMUS_TWO_SIDED_EXIT_MAX_TAKER_QTY=0 removes the size gate.
EXIT_FEE_AWARE = os.getenv("PMUS_TWO_SIDED_EXIT_FEE_AWARE", "1").strip() \
    not in ("0", "no", "false", "off")
# PMUS fee schedule effective 2026-11-14 (docs.polymarket.us/fees): taker
# coefficient by trailing-30-day taker volume -- Tier 1 (< $10k) 0.095,
# Tier 2 0.085, Tier 3 0.075, Tier 4 0.0695. Was 0.06. Default is Tier 1,
# the worst case for this account's volume; makers still pay nothing on
# standard markets. The venue now rounds to the nearest cent (half-even);
# rounding UP here stays conservative.
TAKER_FEE_RATE = _env_d("PMUS_TWO_SIDED_TAKER_FEE_RATE", "0.095")
# 0 = no size gate. The fee is per contract either way, and the gate only
# turned 10-lot reward legs into permanent strands.
EXIT_MAX_TAKER_QTY = _env_d("PMUS_TWO_SIDED_EXIT_MAX_TAKER_QTY", "0")
CENT = D("0.01")
# OWNER 2026-11-15 "close patiently first": a lone leg's maker completion
# rests at the pair cap, and once it has been lone for MAKER_PHASE_FRAC of
# its exit window it moves to JOIN this side's bid, up to a break-even pair
# (COMPLETE_MAX_PAIR, default 1.00) -- still post-only, no fee, buying the
# bid instead of paying the ask. The taker exit fires only at the deadline.
COMPLETE_MAX_PAIR = _env_d("PMUS_TWO_SIDED_COMPLETE_MAX_PAIR", "1.00")
MAKER_PHASE_FRAC = float(os.getenv("PMUS_TWO_SIDED_MAKER_PHASE_FRAC", "0.5"))


def taker_fee(px: D, qty: D, rate: Optional[D] = None) -> D:
    """Estimated taker fee for ONE order: rate * qty * p * (1 - p), rounded
    up to the cent. ESTIMATE from the published schedule, not a venue
    figure."""
    from decimal import ROUND_CEILING
    r = TAKER_FEE_RATE if rate is None else rate
    raw = r * D(qty) * D(px) * (ONE - D(px))
    return raw.quantize(CENT, rounding=ROUND_CEILING)
STATE_DIR = Path(os.getenv("PMUS_TWO_SIDED_STATE_DIR",
                           "/state/pmus-spring/two_sided"))
SPORTS_PREFIXES = ("aec-", "asc-", "tsc-", "atc-", "astatc-")

# OWNER 2026-11-14 liquidity-reward mode. DEFAULT OFF: with
# PMUS_TWO_SIDED_REWARDS unset every code path below is skipped and the bot
# behaves exactly as before. When on: live sports markets carrying a paying
# liquidity program (GET /v1/incentives) are selected FIRST; in them both legs
# join the touch (shallowest pair-cap-legal depth whose legs sit within the
# program's maxSpread of mid) at REWARD_QTY contracts; the per-market cap
# there is REWARD_PER_MARKET_CAP on WORST LONE-LEG cost; and for EVERY market
# a new pair opens only if total unmatched + other markets' resting-pair
# worst lone leg + this pair's worst lone leg <= TOTAL_CAP. Program rewards
# are THEORETICAL and never enter P&L.
REWARDS_ENV = "PMUS_TWO_SIDED_REWARDS"
REWARD_QTY = _env_d("PMUS_TWO_SIDED_REWARD_QTY", "10")
REWARD_PER_MARKET_CAP = _env_d("PMUS_TWO_SIDED_REWARD_PER_MARKET_USD", "7.50")
REWARD_REFRESH_S = float(os.getenv("PMUS_TWO_SIDED_REWARD_REFRESH_S", "300"))
REWARD_LOG_S = float(os.getenv("PMUS_TWO_SIDED_REWARD_LOG_S", "60"))
# optional comma-separated slug prefixes; empty = any rewarded sports market
REWARD_ALLOW_ENV = "PMUS_TWO_SIDED_REWARD_ALLOW"

# OWNER 2026-11-15 REWARD-FIRST MODE. DEFAULT OFF (PMUS_TWO_SIDED_REWARD_FIRST
# unset -> nothing below runs). When on (it implies the reward machinery
# above):
#   * selection ranks live, in-band, not-late markets carrying a paying
#     liquidity period in REWARD_FIRST_PERIODS by THEORETICAL expected reward
#     for our REWARD_QTY quote (pool rate * our share from the current book,
#     venue formula); our order inside the target walk first; up to
#     REWARD_MARKETS of them take slots, the rest are selected as before;
#   * a rewarded market rests BOTH legs at the touch (pair-cap legal depth)
#     and is NOT pulled by 1-tick touch moves: it re-prices only when a leg
#     is >= REWARD_REPRICE_TICKS from its touch or leaves the target walk;
#     jump pauses and the late-game cutoff still pull;
#   * REWARD_ACCRUAL every REWARD_ACCRUAL_LOG_S: THEORETICAL est_today_usd per
#     market per ET day vs the venue's $1.00 payout floor; reward_floor_at_risk
#     from REWARD_FLOOR_CHECK_HOUR_ET on (REPORT ONLY, no size change).
# Every figure is THEORETICAL and never enters P&L.
REWARD_FIRST_ENV = "PMUS_TWO_SIDED_REWARD_FIRST"
# OWNER 2026-11-15 "Focus on liquidity rewards": every slot may be a
# rewarded market, and in reward-first mode NO non-rewarded market is
# selected (their lone legs ate the TOTAL_CAP that reward quotes need).
# PMUS_TWO_SIDED_REWARD_ONLY=0 restores filler pairs (e.g. CFB).
REWARD_MARKETS = int(os.getenv("PMUS_TWO_SIDED_REWARD_MARKETS",
                               str(MAX_MARKETS)))
REWARD_ONLY = os.getenv("PMUS_TWO_SIDED_REWARD_ONLY", "1").strip().lower() \
    in ("1", "yes", "true", "on")
REWARD_FIRST_PERIODS = tuple(
    p.strip() for p in os.getenv("PMUS_TWO_SIDED_REWARD_FIRST_PERIODS",
                                 "live").split(",") if p.strip())
REWARD_REPRICE_TICKS = int(os.getenv("PMUS_TWO_SIDED_REWARD_REPRICE_TICKS",
                                     "2"))
REWARD_WALK_CHECK_S = float(os.getenv("PMUS_TWO_SIDED_REWARD_WALK_CHECK_S",
                                      "30"))
REWARD_SCAN_MAX = int(os.getenv("PMUS_TWO_SIDED_REWARD_SCAN_MAX", "12"))
REWARD_ACCRUAL_LOG_S = float(os.getenv("PMUS_TWO_SIDED_REWARD_ACCRUAL_LOG_S",
                                       "600"))
REWARD_ACCRUAL_MAX_GAP_S = float(os.getenv(
    "PMUS_TWO_SIDED_REWARD_ACCRUAL_MAX_GAP_S", "120"))
REWARD_FLOOR_USD = _env_d("PMUS_TWO_SIDED_REWARD_FLOOR_USD", "1.00")
REWARD_FLOOR_CHECK_HOUR_ET = int(os.getenv(
    "PMUS_TWO_SIDED_REWARD_FLOOR_CHECK_HOUR_ET", "18"))
REWARD_PROJECT_HORIZON_S = float(os.getenv(
    "PMUS_TWO_SIDED_REWARD_PROJECT_HORIZON_S", "7200"))
REWARDED_STAGE = "two_sided_open_rewarded"
# OWNER 2026-11-15 "We're bleeding money" / "make this system work": a
# DAILY LOSS STOP. The bot marks its own book every step (matched pairs at
# $1, a lone leg at its side's bid, minus everything paid) and freezes each
# market's mark when it closes. If today's change (UTC day) falls below
# -DAILY_LOSS_USD, no NEW pair or reward quote opens until the next UTC day;
# completions of open legs continue. MARK-TO-MARKET ESTIMATE, never P&L.
DAILY_LOSS_USD = _env_d("PMUS_TWO_SIDED_DAILY_LOSS_USD", "3")
# OWNER 2026-11-15 "Eliminate stranded legs through active inventory
# management": every unmatched position goes to ONE decision function
# (src/pmus_sports_recovery.py) every loop. DEFAULT OFF -- the deployed
# phase/deadline logic below stays in force until the owner enables it.
# Taker recovery additionally needs PMUS_TWO_SIDED_RECOVERY_TAKER=1.
RECOVERY_ON = os.getenv("PMUS_TWO_SIDED_RECOVERY", "0").strip() == "1"
RECOVERY_TAKER = os.getenv("PMUS_TWO_SIDED_RECOVERY_TAKER",
                           "0").strip() == "1"
RECOVERY_TAKER_CHUNK = _env_d("PMUS_TWO_SIDED_RECOVERY_TAKER_CHUNK", "0")
# OWNER 2026-11-17 "lean toward rebalancing": holding-risk multiplier for
# the recovery decision (1 = unchanged). Higher -> the completion goes to
# the touch at once. The lock-loss cap and every halt are untouched.
RECOVERY_LEAN = _env_d("PMUS_TWO_SIDED_RECOVERY_LEAN", "1")
# OWNER 2026-11-18: the SHARED stranded-leg policy (src/l5_completion_policy
# via src/pmus_sports_completion). Default OFF; live only when BOTH this and
# L5_COMPLETION_POLICY=ON. COMPLETION_SPORTS_CAP is the owner's candidate
# stage-0 pair cap (stricter-only); a sports market never completes above
# 1.00. MAX_STRANDED (0 = off): no new first leg while that many markets
# hold an unpaired leg.
COMPLETION_POLICY_ON = os.getenv("PMUS_TWO_SIDED_COMPLETION_POLICY",
                                 "0").strip() == "1"
COMPLETION_SPORTS_CAP = _env_d("PMUS_TWO_SIDED_COMPLETION_SPORTS_CAP", "0.97")
MAX_STRANDED = int(os.getenv("PMUS_TWO_SIDED_MAX_STRANDED", "0"))
# MARKET STRAND-LOSS RE-ENTRY GUARD (2026-11-18, default 0 = OFF).
# Evidence (36 settled strands, night of 2026-11-17, RECORDED): strands in a
# market that had ALREADY completed a pair above $1.00 lost $8.49 of the
# night's $11.78; first strands in a market lost $2.35. Re-quoting two-sided
# into a market that has just adversely selected us is the defect. When the
# market's cumulative completion overpay (sum of qty * (pair_cost - 1) over
# its paired strands) reaches this many dollars, it takes no NEW opening
# legs. Existing legs are pulled through the late-cutoff path and
# completions of any stranded leg continue unchanged.
# NAMED FOR WHAT IT MEASURES (owner 2026-11-18): completion OVERPAY, not
# total realized strand loss. The old name is still read so the live
# deployment's variable keeps working; the new name wins when both are set.
OVERPAY_BUDGET_STRICTEST = D("0.000001")


def overpay_budget_from(env: Mapping[str, str]) -> D:
    """New name first, then the legacy name, else 0 (off). A SET but
    unparseable value fails CLOSED to the strictest budget (any recorded
    overpay stops new opens in that market), never silently off."""
    for k in ("PMUS_TWO_SIDED_MARKET_OVERPAY_BUDGET_USD",
              "PMUS_TWO_SIDED_MARKET_STRAND_LOSS_USD"):
        v = (env.get(k) or "").strip()
        if v:
            try:
                return D(v)
            except Exception:                            # noqa: BLE001
                return OVERPAY_BUDGET_STRICTEST
    return D(0)


MARKET_OVERPAY_BUDGET_USD = overpay_budget_from(os.environ)


# SUBMISSION LOCK (2026-11-18, default OFF). A lock INDEPENDENT of the shared
# halt: exactly "1" refuses EVERY order this lane would send -- openings,
# extensions AND completions -- at the single placement choke point
# (Engine.place), and the ownership registry publishes it as
# submission_disabled. It exists so an owner-gated halt reset can verify THIS
# lane cannot send while the latch is cleared, instead of substituting another
# lane's lock (2026-11-18 12:53Z reset had to). Read live on every call, so
# the published answer cannot drift from the enforced one. Cancels are never
# blocked.
# ACCOUNT-WIDE RISK (owner 2026-11-18, default OFF until deployed with its
# config). With PMUS_TWO_SIDED_ACCOUNT_RISK=1 this lane obeys the SAME
# account-wide daily-loss, lifetime-loss and exposure limits as BTC5m, read
# through src/l5_account_risk.py from the same published record
# (L5_ACCOUNT_RISK_URL) and the same split (L5_ACCOUNT_MAX_EXPOSURE_USD /
# L5_ACCOUNT_LANES). Any unreadable, stale or breached state REFUSES new
# opening and extension orders; completions are never blocked (they reduce
# risk). The lane's own $DAILY_LOSS_USD stop is kept and still applies.
# "1" ENFORCES. "observe" reads, evaluates and logs ACCOUNT_RISK exactly as
# enforcement would, but refuses nothing -- the read-only first step, so the
# published record's readability and figures are verified in production
# before any order depends on them. Anything else is off.
_ACCOUNT_RISK_MODE = os.getenv("PMUS_TWO_SIDED_ACCOUNT_RISK", "0").strip()
ACCOUNT_RISK_ON = _ACCOUNT_RISK_MODE == "1"
ACCOUNT_RISK_OBSERVE = _ACCOUNT_RISK_MODE.lower() == "observe"
ACCOUNT_RISK_TTL_S = float(os.getenv("PMUS_TWO_SIDED_ACCOUNT_RISK_TTL_S", "10"))
# LIVE 2026-11-24 18:27-18:50Z: the cached verdict's capacity counted only
# the bids resting when it was computed, and every new bid was checked
# against that same figure, so several bids inside one cache period each
# passed (account figure + resting bids read 3.06 to 3.36 against 3.00).
# Owner: "Reserve capital for each submitted order immediately, including
# in-flight and uncertain submissions. Prevent subsequent orders from
# spending the same cached available capacity. Release reservations only
# after confirmed cancellation, rejection or reconciliation."
#   * a placement is checked against a verdict computed AT THAT MOMENT;
#   * a submission is reserved before it is sent; a failed send keeps its
#     reservation until an account read newer than it has been swept;
#   * a bid must leave this much room, so one that exactly fits cannot put
#     the reading at the limit and have every bid pulled (the place-and-
#     cancel cycle of that half hour).
ACCOUNT_RISK_ROOM_MARGIN_USD = _env_d(
    "PMUS_TWO_SIDED_ACCOUNT_RISK_ROOM_MARGIN_USD", "0.01")
RESERVATION_HOLD_S = float(os.getenv(
    "PMUS_TWO_SIDED_RESERVATION_HOLD_S", "5"))
# The account-risk document is read on its OWN thread once the loop runs
# (ACCOUNT_RISK_ASYNC, on): the loop only looks at the last snapshot. A
# snapshot older than ACCOUNT_RISK_SNAPSHOT_MAX_S, or none yet, refuses new
# openings and extensions; cancels and completions are untouched.
ACCOUNT_RISK_ASYNC = os.getenv("PMUS_TWO_SIDED_ACCOUNT_RISK_ASYNC",
                               "1").strip() != "0"
ACCOUNT_RISK_READ_S = float(os.getenv("PMUS_TWO_SIDED_ACCOUNT_RISK_READ_S",
                                      "5"))
# LIVE 2026-11-23: the ledger's drawdown figure did not move while $4-$9 of
# this bot's bids rested; it moved only on fills. "0" (default) = resting
# orders are NOT in the ledger's figure, so all of them are subtracted from
# the capacity. "1" = the earlier assumption (resting already inside).
ACCOUNT_RISK_RESTING_IN_LEDGER = os.getenv(
    "PMUS_TWO_SIDED_ACCOUNT_RISK_RESTING_IN_LEDGER", "0").strip() == "1"
ACCOUNT_RISK_SNAPSHOT_MAX_S = float(os.getenv(
    "PMUS_TWO_SIDED_ACCOUNT_RISK_SNAPSHOT_MAX_S", "30"))
#: RISK-REDUCING stages: they buy the missing side of a held leg (or exit
#: it) and are never refused by the account-wide limits. Everything else --
#: two_sided_open, two_sided_open_rewarded, two_sided_extend -- adds risk.
COMPLETION_STAGES = ("two_sided_complete", "two_sided_complete_taker",
                     "two_sided_exit")


def is_completion_stage(stage: str) -> bool:
    return str(stage or "") in COMPLETION_STAGES


# PRE-GAME SHARP-ODDS MODE (owner 2026-11-18, default OFF). "shadow" runs
# src/pmus_sports_sharp.py against live pre-game markets and The Odds API
# (ODDS_API_KEY) and LOGS what it would quote plus MODELLED touch-fills; it
# places, cancels and authorizes nothing. Free-tier budget: a few sports,
# odds polled every PMUS_SHARP_POLL_S (default 3 h).
SHARP_MODE = os.getenv("PMUS_TWO_SIDED_SHARP", "0").strip().lower()
SHARP_SHADOW_EVERY_S = float(os.getenv("PMUS_SHARP_SHADOW_EVERY_S", "600"))
SHARP_SPORTS = tuple(s.strip() for s in os.getenv(
    "PMUS_SHARP_SPORTS", "nba,nhl").split(",") if s.strip())
SHARP_MAX_MARKETS = int(os.getenv("PMUS_SHARP_MAX_MARKETS", "30"))


# OWNER 2026-11-22 minimum-liquidity gate for NEW exposure (openings and
# extensions; completions are never refused by it). A bid is priced off its
# own side's touch -- YES off the best YES bid, NO off the best NO bid (the
# YES ask) -- so that touch must show at least MIN_TOUCH_QTY contracts that
# are not ours. Live 2026-11-22: Serie A outcome books showed 38,389 x 0.52
# and 41,386 x 0.67 contracts: a one-contract touch is not a price, and a
# bid one tick behind it sits above the real market. Unknown depth blocks
# (fail closed). This is a liquidity floor only; it says nothing about
# whether a market is profitable. 0 disables.
MIN_TOUCH_QTY = _env_d("PMUS_TWO_SIDED_MIN_TOUCH_QTY", "10")
DEPTH_TTL_S = float(os.getenv("PMUS_TWO_SIDED_DEPTH_TTL_S", "10"))

# OWNER 2026-11-22 reward-aware selection. Evidence (14-18 Nov, every fill
# reconciled): the 74 markets that earned a liquidity reward netted +$0.63
# (rewards +15.63, trading -15.00); the 167 that earned none netted -$25.71.
# In RN1 mode candidates carrying a paying liquidity programme are selected
# first, richest pool rate first; PMUS_RN1_REWARD_ONLY=1 selects nothing
# else. The programme read is the public incentives endpoint the bot already
# uses, cached REWARD_REFRESH_S. A programme is a published pool, not a
# payment: whether we were paid is known only days later, from the venue's
# earnings record.
RN1_REWARD_RANK = os.getenv("PMUS_RN1_REWARD_RANK", "1").strip() != "0"
RN1_REWARD_ONLY = os.getenv("PMUS_RN1_REWARD_ONLY", "0").strip() == "1"
#: extra edge (ticks) an add to an unpaired side needs, by the event's type:
#: how far one scoring event moves the price. ESTIMATED priors.
RN1_JUMP_TICKS = {
    k.strip().lower(): int(v) for k, v in (
        tok.split(":") for tok in os.getenv(
            "PMUS_RN1_JUMP_TICKS",
            "soccer:3,hockey:2,football:1,tennis:1,table-tennis:1"
        ).split(",") if ":" in tok)}
#: credit (ticks) for a market with a paying liquidity programme
RN1_REWARD_CREDIT_TICKS = int(os.getenv("PMUS_RN1_REWARD_CREDIT_TICKS", "1"))
#: A PROGRAMME EXISTING IS NOT A PAYMENT (issue #251: "A quote must not pass
#: solely on speculative incentive payouts"). In the venue's own earnings
#: rows (Sept and 14-18 Nov) every day whose rewards totalled under $1 was
#: skipped entirely, while single paid rows are as small as $0.01. So the
#: credit above is applied only while the THEORETICAL reward of what this
#: bot is actually resting, over RN1_REWARD_PROJECT_H hours across its
#: active markets, reaches RN1_REWARD_MIN_DAY_USD. It is still an estimate
#: of a payment nobody has made; it only ever offsets the inventory-risk
#: allowance of the marginal test, never the trading edge itself.
#: A FAVOURABLE FAIR VALUE HAS TO LAST. LIVE 2026-11-22 20:18:11Z,
#: Baltimore-Carolina: the international mid spiked from 0.365 to about
#: 0.395 for some three seconds and came back. In that moment YES "worth
#: 0.395" at 0.37 passed the marginal test, an order went out, and by the
#: time it reached the venue the market was back at 0.3625 / 0.365 -- the
#: post-only bid would have crossed and the venue rejected it. A minute
#: later the price was 3c lower. A jump in the fair value is exactly when
#: the fair value is least reliable.
#:
#: So each side is judged against the fair value LEAST favourable to it
#: over the last RN1_FAIR_STABLE_S seconds: a YES bid against the lowest
#: international mid in that period, a NO bid against the highest. A move
#: against a resting bid counts at once (it is cancelled on the next pass);
#: a move in its favour counts only after it has lasted the whole period.
#: Until the bot has that many seconds of unbroken fair value for a market
#: (after a start, a feed gap, a newly selected market) there is no fair
#: value and so no bid.
RN1_FAIR_STABLE_S = float(os.getenv("PMUS_RN1_FAIR_STABLE_S", "10"))
#: ...AND THE FAIR VALUE HAS TO BE CALM. LIVE 2026-11-22 21:36:21Z,
#: Detroit-Atlanta, the first fill of the profit-first build: the
#: international mid had gone 0.555 -> 0.615 -> 0.585 inside 45 seconds and
#: was still falling. YES at 0.57 passed (lowest mid of the last 10 s was
#: 0.585), the bid was filled 1.8 s later, and the mid was 0.50 fifteen
#: seconds after that: -3.5c at 5 s and at 30 s by the bot's own markout.
#: The lowest-recent-value rule cannot see that a price is in motion. So
#: while the international mid has moved more than RN1_FAIR_MAX_RANGE_USD
#: within the stability period there is NO fair value, for either side:
#: nothing new is bid and resting bids come off. A resting maker bid is
#: only worth having while the price is quiet.
RN1_FAIR_MAX_RANGE_USD = _env_d("PMUS_RN1_FAIR_MAX_RANGE_USD", "0.01")
RN1_REWARD_MIN_DAY_USD = float(os.getenv("PMUS_RN1_REWARD_MIN_DAY_USD", "1"))
RN1_REWARD_PROJECT_H = float(os.getenv("PMUS_RN1_REWARD_PROJECT_H", "3"))
# REWARD QUALIFICATION SCOPE (2026-11-27, default "market" = unchanged).
# LIVE 2026-11-27 16:02Z: RN1_TOUCH_EV scored copmih-dumvit at +$0.166/h
# (calibrated reward $0.146/h a bid) while the marginal test credited $0
# ("below_minimum_payout"): the standing-start projection counted only THAT
# market's planned bids over 3 h (~$0.88), whereas the regular projection
# (rn1_incentive) counts every active market, the selection check counted
# every active market WITHOUT the calibration multiplier, and RN1_TOUCH_EV
# applied no minimum at all. Four sites, three scopes. The venue's records
# show the minimum is not per market (paid rows of $0.01;
# analysis/l5/rewards/REWARD_PAYOUT_THRESHOLD_20261122.md).
# "account_day": ONE qualification for all four sites -- the calibrated
# theoretical reward already accrued today on every market, plus the
# calibrated rate of every active market (what rests, or the bids it is
# about to rest when nothing does yet) and of the candidate, over
# min(RN1_REWARD_PROJECT_H, time left in the trading window, time left in
# the venue's reward day), must reach RN1_REWARD_MIN_DAY_USD. RN1_TOUCH_EV
# then credits a reward only when that qualification holds, so selection,
# placement and the order gate agree. The minimum itself is unchanged.
# "program_period" (27 Nov, correction): the venue's liquidity programme
# documentation (data/pmus/sports_incentives_2026-09-10/doc_incentives_
# liquidity.md) defines each Time Period -- early pre-game (listing to 6 h
# before), day-of pre-game (6 h to start), live (start to settlement) -- as
# its own reward pool, calculated when that period ends, and says "Rewards
# under $1.00 are not paid out" without stating the scope. External research
# (Grok, 27 Nov) reports the minimum is applied per programme and period.
# With this scope each pool is tested on ITS OWN total: accrual keyed by
# (programId, period), plus the calibrated rate of active markets in the SAME
# pool and the candidate, over min(3 h, window left, period left). An
# expired period's accrual, another programme or another period never
# counts toward a bid, and a market with no paying pool qualifies nothing.
# "account_day" stays available but is NOT recommended: no source supports
# account-wide daily aggregation.
# "contract_period" (27 Nov, release correction; RECOMMENDED): a programme id
# is NOT one pool. The venue's own payloads show one programmeId|period
# spanning many contracts (atp_wta_moneyline_live_20261018 has run since
# 18 Oct over every ATP/WTA match; cfb_t2_ml_live_20261125 over 43 games),
# each carrying the same rewardPool, and scoring is normalised per contract
# book per snapshot. The pool is therefore one CONTRACT's time period: key
# (market slug, programId, period, start). Only that contract's own accrual
# in that period and its own resting/planned rate count; no other match,
# no other period, no expired period. Data/pmus/incentives_2026-11-27/.
RN1_REWARD_QUAL_SCOPE = os.getenv("PMUS_RN1_REWARD_QUAL_SCOPE",
                                  "market").strip().lower()
#: the scopes that use the one unified qualification at every site
RN1_QUAL_UNIFIED = ("account_day", "program_period", "contract_period")
RN1_POOL_SCOPES = ("program_period", "contract_period")
#: a planned (not yet resting) market's rate is used for this long
RN1_REWARD_PLAN_TTL_S = float(os.getenv("PMUS_RN1_REWARD_PLAN_TTL_S", "120"))

# OWNER 2026-11-22 "The bot must reliably stop and cancel orders without
# depending on Claude's session." LIVE that morning: a reconciliation
# mismatch at 14:28:33Z was a stop condition, and the relock issued from a
# coding session reached production 45 minutes late. The SAFETY LATCH is the
# bot stopping ITSELF: when one of the conditions below holds, it records a
# latch in its own durable book, cancels every own order by exact id and
# refuses every placement (completions included). The latch survives
# restarts and variable changes. It is cleared ONLY by the owner setting
# PMUS_TWO_SIDED_SAFETY_LATCH_ACK to that latch's id -- an acknowledgement of
# one specific stop, which can never pre-clear a future one.
#
#   position_mismatch       the venue's position in a market differs from
#                           the book for longer than MISMATCH_GRACE_S, after
#                           the bot has re-read its own orders' fills and a
#                           complete venue positions walk confirms it
#   position_unverifiable   the difference persists 3x that long and no
#                           complete walk could be made (fail closed)
#   order_qty_exceeds_clip  an own resting order larger than the RN1 clip
#   campaign_cap_breached   FILLED spend or FILLED unmatched cost above the
#   market_cap_breached     campaign / per-market caps (the caps are applied
#                           before placing, so this means they failed)
#   loop_errors             LOOP_ERROR_LATCH_N consecutive failed loop passes
# OWNER 2026-11-22: "a bot that detects its own failure and stops is safer,
# yet that alone does not prove the independent watchdog can shut it down if
# the bot itself stalls." It could not. The watchdog exempts this lane's
# resting orders from its order-age latch while this lane's registry
# document says `manages_own_resting` with an `as_of_utc` under 60 s old --
# and that stamp was the time of the HTTP REQUEST, written by the registry
# thread. A trading loop wedged on a socket or a lock kept answering "fresh,
# managing" for ever, so its resting orders stayed exempt for ever (the
# watchdog's own notes call this the WEDGED executor case).
#
# The document now reports the trading loop's own progress. The loop thread
# beats at the top of every pass, after every market step, before every
# sleep and after every venue request it makes. While the last beat is at
# most LOOP_STALL_S old the document is exactly as before. Past that it says
# `manages_own_resting: false` (and `loop_stalled: true`) while staying
# freshly stamped and still listing this bot's order ids:
#   * the exemption ends at once (the watchdog requires manages_own_resting
#     true), so its order-age latch applies to these orders again;
#   * the ownership claim stays readable and fresh, which the watchdog's
#     cancel path requires -- a frozen stamp would have latched the halt and
#     left the orders resting ("ownership registry named no ids").
# The ids are still truthfully ours; what stopped is the managing. Other
# threads cannot beat for the loop. If the whole process dies the registry is
# unreachable: the watchdog latches the halt and, having no ownership claim,
# cancels nothing -- that case is unchanged and needs a person.
LOOP_STALL_S = float(os.getenv("PMUS_TWO_SIDED_LOOP_STALL_S", "120"))
_LOOP: Dict[str, Any] = {"beat": None, "thread": None}


def loop_beat() -> None:
    """Progress of the trading loop. Only the loop's own thread counts once
    the loop has started. The longest gap between two beats since the last
    heartbeat is kept, so how close the loop comes to LOOP_STALL_S in
    ordinary running can be read from the bot's own log."""
    if _LOOP["thread"] in (None, threading.get_ident()):
        now = time.time()
        last = _LOOP.get("beat")
        if last is not None:
            _LOOP["max_gap"] = max(float(_LOOP.get("max_gap") or 0.0),
                                   now - float(last))
        _LOOP["beat"] = now


def loop_report(reset: bool = True) -> Dict[str, Any]:
    """{"idle_s", "max_gap_s", "stall_s", "stalled"} for the heartbeat."""
    idle = loop_idle_s()
    gap = max(float(_LOOP.get("max_gap") or 0.0), idle or 0.0)
    oidle = order_service_idle_s()
    ogap = max(float(_ORDERS.get("max_gap") or 0.0), oidle or 0.0)
    if reset:
        _LOOP["max_gap"] = 0.0
        _ORDERS["max_gap"] = 0.0
    return {"idle_s": None if idle is None else round(idle, 1),
            "max_gap_s": round(gap, 1), "stall_s": LOOP_STALL_S,
            "stalled": (idle is not None and idle > LOOP_STALL_S)
            or order_service_stalled(),
            "order_service_idle_s": None if oidle is None
            else round(oidle, 1),
            "order_service_max_gap_s": round(ogap, 1),
            "order_service_stall_s": ORDER_SERVICE_STALL_S}


# OWNER 2026-11-22: "Verify the independent watchdog can detect a stalled
# sports execution loop and cancel its owned resting orders." The loop
# cannot be wedged from outside the process, so the test needs a switch:
#
#   PMUS_TWO_SIDED_STALL_DRILL=window:<startZ>/<endZ>
#
# Inside the window the trading loop does NOTHING and reports no progress --
# no reads, no cancels, no orders -- exactly a wedged loop. The registry
# thread keeps serving, so after LOOP_STALL_S the watchdog sees
# `manages_own_resting: false` with the bot's order ids still claimed. At
# the end the loop resumes and its ordinary lock handling runs. Bounded: a
# window longer than STALL_DRILL_MAX_S, malformed, or not this exact form is
# NO drill. It can only ever stop this bot from acting; it cannot place or
# keep an order. SIGTERM still ends the process during a drill.
# SELF-EXPIRING ORDERS (the case nothing else covers). If the whole bot
# process is gone its registry is unreachable, and the watchdog -- which
# cancels only what an owner claims -- latches the halt and cancels nothing.
# The venue documents an order that expires by itself (see l5_order_body).
# With PMUS_TWO_SIDED_ORDER_TTL_S > 0 every order this bot places carries
# an expiry that many seconds ahead; an expired order is seen as closed by
# the venue and simply re-quoted on the next pass, so a live bot keeps
# quoting and a dead one stops being exposed within the TTL. OFF (0) by
# default: the venue's handling has not been observed yet, and turning it
# on is a production setting for the owner. Bounded 60 s .. 1 h.
ORDER_TTL_S = float(os.getenv("PMUS_TWO_SIDED_ORDER_TTL_S", "0") or 0)


def order_good_till(now: Optional[float] = None) -> Optional[str]:
    """RFC 3339 UTC expiry for an order placed now, or None when off."""
    if not (60.0 <= ORDER_TTL_S <= 3600.0):
        return None
    t = (time.time() if now is None else now) + ORDER_TTL_S
    return datetime.fromtimestamp(int(t), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


STALL_DRILL_ENV = "PMUS_TWO_SIDED_STALL_DRILL"
STALL_DRILL_MAX_S = 600.0


# THE PROCESS-DEATH DRILL (owner 2026-11-23: "I would not approve another
# extended live campaign until the independent cancellation path is deployed
# and verified"). A restart cannot stand in for a death: on SIGTERM the bot
# cancels its own orders. So, for a drill only:
#
#   PMUS_TWO_SIDED_DEATH_DRILL=window:<startZ>/<endZ>
#
# At the window's start the running process exits at once with os._exit --
# no cancel, no shutdown handler, the registry gone with it. Every restart
# inside the window waits, serving nothing and touching nothing, until the
# window ends, then boots normally (under a lock it cancels what is left).
# Whatever was resting is therefore unmanaged for the window and only the
# independent watchdog can cancel it. Same bounds as the stall drill: a
# window longer than STALL_DRILL_MAX_S or malformed is NO drill. It can only
# stop this bot from acting; it cannot place or keep an order.
DEATH_DRILL_ENV = "PMUS_TWO_SIDED_DEATH_DRILL"


def death_drill(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    return stall_drill(now, env=DEATH_DRILL_ENV)


def death_drill_exit_if_due(exit_fn: Optional[Callable[[int], None]] = None
                            ) -> None:
    """In the running loop: inside the window, die without cleaning up."""
    d = death_drill()
    if d and d["active"]:
        log("DEATH_DRILL", phase="exit", end=_iso(d["end"]),
            label="deliberate drill: the process exits with no cancel and "
                  "no shutdown handling")
        (exit_fn or os._exit)(70)


def death_drill_boot_hold(sleep: Callable[[float], None] = time.sleep
                          ) -> bool:
    """At boot: inside the window, do nothing until it ends. True if held."""
    held = False
    while True:
        d = death_drill()
        if not d or not d["active"]:
            return held
        if not held:
            held = True
            log("DEATH_DRILL", phase="boot_hold", end=_iso(d["end"]),
                label="deliberate drill: nothing is served or touched until "
                      "the window ends")
        sleep(1.0)


def stall_drill(now: Optional[float] = None, env: Optional[str] = None
                ) -> Optional[Dict[str, Any]]:
    """{"start", "end", "active"} for a well-formed drill window, else None."""
    raw = (os.getenv(env or STALL_DRILL_ENV) or "").strip()
    if not raw.startswith("window:"):
        return None
    try:
        a, b = raw[len("window:"):].split("/")
        st = datetime.fromisoformat(a.strip().replace("Z", "+00:00"))
        en = datetime.fromisoformat(b.strip().replace("Z", "+00:00"))
        if st.tzinfo is None or en.tzinfo is None:
            return None
        st, en = st.timestamp(), en.timestamp()
    except (ValueError, TypeError):
        return None
    if not (0 < en - st <= STALL_DRILL_MAX_S):
        return None
    now = time.time() if now is None else now
    return {"start": st, "end": en, "active": st <= now < en}


def loop_idle_s(now: Optional[float] = None) -> Optional[float]:
    b = _LOOP["beat"]
    if b is None:
        return None
    return max(0.0, (time.time() if now is None else now) - float(b))


# LIVE 2026-11-22 22:08:55Z-22:10:21Z: the hourly audit ran on the trading
# loop's own thread, about 85 read-only requests paced at one a second. For
# 87 s nothing read an order's status and nothing drained the queued price
# events: a fill was recorded 88.8 s late and a cancel the price guard asked
# for waited 93 s. `loop_beat` counts every venue request as progress, so
# the stall figure read 5 s throughout and the watchdog saw a healthy loop.
#
# Two changes. (1) Order management runs BETWEEN the audit's requests
# (Engine.service_pass; AUDIT_INTERLEAVE, on). (2) A second, stricter
# measure: when the bot's markets were last actually served. With
# ORDER_SERVICE_STALL_S set, a gap longer than that reads as a stalled loop
# in the registry (`manages_own_resting: false`), so the watchdog cancels
# what rests exactly as for a wedged loop. 0 = measured and reported only.
AUDIT_INTERLEAVE = os.getenv("PMUS_TWO_SIDED_AUDIT_INTERLEAVE",
                             "1").strip() != "0"
ORDER_SERVICE_STALL_S = float(os.getenv(
    "PMUS_TWO_SIDED_ORDER_SERVICE_STALL_S", "0"))
_ORDERS: Dict[str, Any] = {"served": None, "max_gap": 0.0}


def orders_served() -> None:
    """One complete pass of order management has just finished."""
    if _LOOP["thread"] in (None, threading.get_ident()):
        now = time.time()
        last = _ORDERS.get("served")
        if last is not None:
            _ORDERS["max_gap"] = max(float(_ORDERS.get("max_gap") or 0.0),
                                     now - float(last))
        _ORDERS["served"] = now


def order_service_idle_s(now: Optional[float] = None) -> Optional[float]:
    b = _ORDERS["served"]
    if b is None:
        return None
    return max(0.0, (time.time() if now is None else now) - float(b))


def order_service_stalled(now: Optional[float] = None) -> bool:
    idle = order_service_idle_s(now)
    return ORDER_SERVICE_STALL_S > 0 and idle is not None and \
        idle > ORDER_SERVICE_STALL_S


class ServicedTransport:
    """A transport that runs `service` after every request it makes, so a
    long read-only job cannot hold order management still. Everything else
    is the wrapped transport's."""

    def __init__(self, t: Any, service: Callable[[], None]):
        self._t, self._service = t, service

    def request(self, *a: Any, **k: Any) -> Any:
        try:
            return self._t.request(*a, **k)
        finally:
            try:
                self._service()
            except Exception as exc:                      # noqa: BLE001
                log("service_pass_error",
                    err=f"{type(exc).__name__}:{str(exc)[:160]}")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._t, name)


SAFETY_LATCH_ON = os.getenv("PMUS_TWO_SIDED_SAFETY_LATCH",
                            "1").strip() != "0"
SAFETY_LATCH_ACK_ENV = "PMUS_TWO_SIDED_SAFETY_LATCH_ACK"
MISMATCH_GRACE_S = float(os.getenv("PMUS_TWO_SIDED_MISMATCH_GRACE_S", "45"))
MISMATCH_CONFIRMED_TTL_S = float(os.getenv(
    "PMUS_TWO_SIDED_MISMATCH_CONFIRMED_TTL_S", "600"))
LOOP_ERROR_LATCH_N = int(os.getenv("PMUS_TWO_SIDED_LOOP_ERROR_LATCH_N", "10"))
_CAP_TOL = D("0.01")

SUBMISSION_LOCK_ENV = "PMUS_TWO_SIDED_SUBMISSION_LOCK"
# OWNER 2026-11-22: "any future unlock must be restricted to the exact
# approved time window, with automatic relocking at the end." The lock
# variable is the ONLY unlock, and in production it is never "0":
#
#   1                         locked (the resting state)
#   window:<start>/<end>      ISO-8601 UTC instants. Locked before <start>,
#                             open in [start, end), locked again from <end>
#                             with NO restart and no human: placements are
#                             refused and the bot's own resting orders are
#                             cancelled by exact id.
#   unset / 0                 legacy "unlocked" (tests, local runs)
#   anything else             LOCKED (fail closed: a typo never unlocks)
#
# A window longer than WINDOW_MAX_S, reversed, or unparseable is locked.
# WINDOW_COMPLETE_GRACE_S (default 0 = everything stops at <end>): for that
# long after <end>, MAKER completion bids of already-held unpaired legs may
# still rest -- they only reduce exposure; openings/extensions are refused
# from <end> regardless. The window start is also the campaign marker: the
# pilot meter and campaign caps never count markets opened before it, so a
# stale PMUS_RN1_PILOT_SINCE cannot leak an old campaign into a new one.
WINDOW_MAX_S = float(os.getenv("PMUS_TWO_SIDED_WINDOW_MAX_S", "21600"))
WINDOW_COMPLETE_GRACE_S = float(os.getenv(
    "PMUS_TWO_SIDED_WINDOW_COMPLETE_GRACE_S", "0"))


def _utc_instant(raw: str) -> float:
    from datetime import datetime
    d = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("no timezone")
    return d.timestamp()


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _iso_precise(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()


def lock_window(raw: Optional[str] = None) -> Optional[tuple]:
    """(start, end) epoch seconds of a VALID window value, else None."""
    raw = (os.getenv(SUBMISSION_LOCK_ENV) or "") if raw is None else raw
    raw = raw.strip()
    if not raw.lower().startswith("window:"):
        return None
    try:
        a, b = raw[len("window:"):].split("/")
        start, end = _utc_instant(a), _utc_instant(b)
    except Exception:                                     # noqa: BLE001
        return None
    if not (0 < end - start <= WINDOW_MAX_S):
        return None
    return start, end


# ---------------------------------------------------------------------------
# RUNTIME CAMPAIGN AUTHORIZATION (owner 2026-11-23). With the lock value
# `runtime` the decision to trade comes from a signed, expiring campaign
# file the RUNNING bot reads (src/pmus_campaign_auth.py), not from a
# settings change that restarts it. Everything else about the lock is
# unchanged: `1` still locks whatever any file says, and a window value
# still works as before.
#   * No file, an unreadable, unsigned, expired, revoked, wrong-build or
#     out-of-bounds file, or a refresh that failed: fully locked, own
#     orders cancelled (no grace).
#   * The file chooses competitions, market count, order size, slate and
#     the campaign and per-market caps INSIDE the deployed values, which
#     are the hard ceilings. It cannot touch the account limit, the loss
#     stop, the daily halt, the pair cap or anything about a halt.
#   * The emergency halt is checked before this on every order. A campaign
#     that has seen a latched halt is ended for good.
RUNTIME_LOCK = "runtime"
CAMPAIGN_AUTH_URL_ENV = "PMUS_CAMPAIGN_AUTH_URL"
CAMPAIGN_AUTH_SECRET_ENV = "PMUS_CAMPAIGN_AUTH_SECRET"
CAMPAIGN_AUTH_BEARER_ENV = "PMUS_CAMPAIGN_AUTH_BEARER"
CAMPAIGN_AUTH_ACCEPT_ENV = "PMUS_CAMPAIGN_AUTH_ACCEPT"
CAMPAIGN_AUTH_SERVICE_ENV = "PMUS_CAMPAIGN_AUTH_SERVICE"
CAMPAIGN_AUTH_POLL_S = float(os.getenv("PMUS_CAMPAIGN_AUTH_POLL_S", "20"))
#: the last SUCCESSFUL read may be at most this old (two polls and a bit)
CAMPAIGN_AUTH_MAX_AGE_S = float(os.getenv(
    "PMUS_CAMPAIGN_AUTH_MAX_AGE_S", "45"))
#: the engine's own decision must be this fresh for the lock to open: a
#: loop that has stopped evaluating stops permitting
CAMPAIGN_DECISION_MAX_AGE_S = float(os.getenv(
    "PMUS_CAMPAIGN_DECISION_MAX_AGE_S", "15"))
#: `1` = read and evaluate the campaign file and SHOW the verdict in the
#: heartbeat while the lock value is not `runtime`. It permits nothing,
#: records nothing and applies nothing: a non-trading preflight.
CAMPAIGN_AUTH_REPORT = os.getenv("PMUS_CAMPAIGN_AUTH_REPORT",
                                 "0").strip() == "1"
_RUNTIME: Dict[str, Any] = {"at": None, "decision": None}


def runtime_mode() -> bool:
    return (os.getenv(SUBMISSION_LOCK_ENV) or "").strip().lower() \
        == RUNTIME_LOCK


def runtime_state(now: Optional[float] = None) -> Dict[str, Any]:
    """The lock state under the `runtime` value, from the engine's last
    decision. Anything short of a fresh, permitted decision inside the
    campaign's own window is fully locked."""
    def locked(why: str) -> Dict[str, Any]:
        return {"new": True, "all": True, "why": "runtime:" + why}
    at, d = _RUNTIME.get("at"), _RUNTIME.get("decision")
    if at is None or not isinstance(d, Mapping):
        return locked("AUTH_NOT_EVALUATED")
    if time.monotonic() - float(at) > CAMPAIGN_DECISION_MAX_AGE_S:
        return locked("AUTH_DECISION_STALE")
    if not d.get("permit"):
        return locked(str(d.get("reason") or "AUTH_REFUSED"))
    c = d.get("campaign") or {}
    try:
        start, end = float(c["start"]), float(c["end"])
    except Exception:                                     # noqa: BLE001
        return locked("AUTH_WINDOW_UNREADABLE")
    wall = time.time() if now is None else now
    if wall < start:
        return locked("AUTH_NOT_STARTED")
    if wall >= end:
        return locked("AUTH_EXPIRED")
    return {"new": False, "all": False, "why": None, "start": start,
            "end": end, "campaign_id": c.get("campaign_id")}


def lock_state(now: Optional[float] = None) -> Dict[str, Any]:
    """`new` = openings/extensions (and everything else that adds risk) are
    refused; `all` = maker completions are refused too. `why` names it."""
    raw = (os.getenv(SUBMISSION_LOCK_ENV) or "").strip()
    if raw in ("", "0"):
        return {"new": False, "all": False, "why": None}
    if raw == "1":
        return {"new": True, "all": True, "why": "locked"}
    if raw.lower() == RUNTIME_LOCK:
        return runtime_state(now)
    w = lock_window(raw)
    if w is None:
        return {"new": True, "all": True,
                "why": "window_invalid" if raw.lower().startswith("window:")
                else "lock_value_unrecognized"}
    now = time.time() if now is None else now
    start, end = w
    win = {"start": start, "end": end}
    if now < start:
        return {"new": True, "all": True, "why": "window_not_started", **win}
    if now < end:
        return {"new": False, "all": False, "why": None, **win}
    if now < end + max(0.0, WINDOW_COMPLETE_GRACE_S):
        return {"new": True, "all": False,
                "why": "window_completions_only", **win}
    return {"new": True, "all": True, "why": "window_ended", **win}


def submission_locked(stage: Optional[str] = None,
                      post_only: bool = True) -> bool:
    """True = this order may not be sent. With no stage: is NEW exposure
    locked. A maker completion is refused only when everything is."""
    st = lock_state()
    if stage is not None and post_only and stage == "two_sided_complete":
        return st["all"]
    return st["new"]


def fully_locked() -> bool:
    return lock_state()["all"]


_w = lock_window()
if _w is not None and (PILOT_SINCE is None or PILOT_SINCE < _w[0]):
    PILOT_SINCE = _w[0]            # the window start is the campaign marker
del _w


_CEIL: Dict[str, Any] = {}


def campaign_ceilings() -> Dict[str, Any]:
    """The hard ceilings a campaign file must stay inside: the DEPLOYED
    values, captured once before any file is applied."""
    if not _CEIL:
        _CEIL.update({
            "max_window_s": WINDOW_MAX_S, "max_markets": MAX_MARKETS,
            "order_qty": rn1_max_clip(),
            "campaign_spend_usd": RN1.TOTAL_SPEND_USD,
            "campaign_unpaired_usd": RN1.TOTAL_UNMATCHED_USD,
            "per_market_spend_usd": RN1.PER_MARKET_USD,
            "per_market_unpaired_usd": RN1.RESIDUAL_CAP_USD,
            "competitions": sorted(OPEN_GATE_ALLOW_SPORTS),
            "_qty": RN1.QTY, "_pregame": RN1_PREGAME_QTY,
            "_live": RN1_LIVE_QTY, "_strict": STRICT_SPORTS,
            "_slate": SLATE})
    return _CEIL


def apply_campaign(c: Optional[Mapping[str, Any]]) -> None:
    """Put a permitted campaign's values in force, each no higher than its
    ceiling (the file was already refused if any exceeded one; the min()
    here is the second lock on the same door). None = back to the deployed
    values."""
    global MAX_MARKETS, OPEN_GATE_ALLOW_SPORTS, STRICT_SPORTS, SLATE
    global RN1_PREGAME_QTY, RN1_LIVE_QTY
    k = campaign_ceilings()
    if c is None:
        MAX_MARKETS = k["max_markets"]
        RN1.QTY, RN1_PREGAME_QTY, RN1_LIVE_QTY = \
            k["_qty"], k["_pregame"], k["_live"]
        RN1.TOTAL_SPEND_USD = k["campaign_spend_usd"]
        RN1.TOTAL_UNMATCHED_USD = k["campaign_unpaired_usd"]
        RN1.PER_MARKET_USD = k["per_market_spend_usd"]
        RN1.RESIDUAL_CAP_USD = k["per_market_unpaired_usd"]
        OPEN_GATE_ALLOW_SPORTS = frozenset(k["competitions"])
        STRICT_SPORTS, SLATE = k["_strict"], k["_slate"]
        return
    q = D(str(c["order_qty"]))
    MAX_MARKETS = min(int(k["max_markets"]), int(D(str(c["max_markets"]))))
    RN1.QTY = min(D(str(k["_qty"])), q)
    RN1_PREGAME_QTY = None if k["_pregame"] is None \
        else min(D(str(k["_pregame"])), q)
    RN1_LIVE_QTY = None if k["_live"] is None \
        else min(D(str(k["_live"])), q)
    RN1.TOTAL_SPEND_USD = min(D(str(k["campaign_spend_usd"])),
                              D(str(c["campaign_spend_usd"])))
    RN1.TOTAL_UNMATCHED_USD = min(D(str(k["campaign_unpaired_usd"])),
                                  D(str(c["campaign_unpaired_usd"])))
    RN1.PER_MARKET_USD = min(D(str(k["per_market_spend_usd"])),
                             D(str(c["per_market_spend_usd"])))
    RN1.RESIDUAL_CAP_USD = min(D(str(k["per_market_unpaired_usd"])),
                               D(str(c["per_market_unpaired_usd"])))
    OPEN_GATE_ALLOW_SPORTS = frozenset(c["competitions"]) & \
        frozenset(k["competitions"])
    STRICT_SPORTS, SLATE = True, str(c["slate"])


def adopt_campaign_marker(book: Any) -> Optional[float]:
    """Keep the campaign marker across a relock. LIVE 2026-11-22: after the
    14:05Z window the lock went back to `1`, the marker fell back to the
    stale PMUS_RN1_PILOT_SINCE (01:15Z) and the meter reported two campaigns
    as one (+0.63 = last night's +0.35 and today's +0.28). The latest window
    start is stored in the bot's book and stays the marker until a later
    window (or a later PMUS_RN1_PILOT_SINCE) replaces it. Returns the
    marker in force for this book."""
    w = lock_window()
    stored = book.d.get("campaign_since")
    # LIVE 2026-11-22 20:16Z: a window armed for 21:35 was moved to start at
    # 20:16. The stored marker only ever moved LATER, so it stayed at 21:35:
    # orders placed before then would not have counted toward the campaign
    # spend cap, the unmatched cap or the campaign loss stop (the window was
    # put back before anything filled). A configured window IS the campaign:
    # whenever the window differs from the one this book last ran under, its
    # start becomes the marker -- earlier or later. The same window after a
    # restart keeps its marker; no window (a plain lock) keeps the last one.
    if w is not None:
        cur = [float(w[0]), float(w[1])]
        if book.d.get("campaign_window") != cur or stored is None \
                or float(stored) < w[0]:
            book.d["campaign_window"] = cur
            book.d["campaign_since"] = stored = w[0]
            book.save()
    if stored is not None and (PILOT_SINCE is None or
                               PILOT_SINCE < float(stored)):
        return float(stored)
    return PILOT_SINCE


# ADVERSE-SELECTION-ADJUSTED OPENING CAP (2026-11-18, default OFF).
# Evidence (23 strands 12:53-13:35Z, bot ORDER/FILL logs): when the first
# leg filled, the two resting opening quotes summed to 0.88-0.93 (mean
# 0.927) -- every opening was sound as quoted -- yet the completed pair
# averaged 1.033, because the complement was bought on average 10.6c above
# the quote that had been resting on that side (3c..22c; ITF tennis 7-19c,
# setka/ATP 4-6c). A one-sided fill is itself evidence the price is
# moving, so the quoted margin must cover the EXPECTED completion drift,
# not just be under $1.
#
# With PMUS_TWO_SIDED_DRIFT_CAP=1 the opening pair cap for a sport becomes
#   min(pair_cap(slug), 1 - median(recent drifts of that sport) - DRIFT_EDGE)
# once that sport has DRIFT_MIN_N measured strands. It can only TIGHTEN the
# existing cap (deeper quotes, or none if nothing fits); it never loosens
# it, never touches completions, and a sport without enough evidence keeps
# the existing cap unchanged.
DRIFT_CAP_ON = os.getenv("PMUS_TWO_SIDED_DRIFT_CAP", "0").strip() == "1"
DRIFT_EDGE = _env_d("PMUS_TWO_SIDED_DRIFT_EDGE", "0.01")
DRIFT_MIN_N = int(os.getenv("PMUS_TWO_SIDED_DRIFT_MIN_N", "5"))
DRIFT_KEEP = int(os.getenv("PMUS_TWO_SIDED_DRIFT_KEEP", "20"))
DRIFT_MAX_EXTRA_TICKS = int(os.getenv("PMUS_TWO_SIDED_DRIFT_MAX_EXTRA_TICKS",
                                      "8"))


def sport_of(slug: str) -> str:
    parts = str(slug).split("-")
    return parts[1] if len(parts) > 1 else "?"


def event_key(slug: str) -> str:
    """The game a market belongs to: league-teams-date, whatever the market
    structure. A soccer match's three outcome markets (atc-...-juv, -draw,
    -ven) and a game's winner/spread/total markets share one key."""
    parts = str(slug).split("-")
    for i in range(1, len(parts) - 2):
        y, mo, d = parts[i:i + 3]
        if len(y) == 4 and y.isdigit() and len(mo) == 2 and mo.isdigit() \
                and len(d) == 2 and d.isdigit():
            return "-".join(parts[1:i + 3])
    return "-".join(parts[1:]) or str(slug)


def record_drift(d: Dict[str, Any], slug: str, drift: D) -> None:
    lst = d.setdefault("completion_drift", {}).setdefault(sport_of(slug), [])
    lst.append(str(drift))
    del lst[:-DRIFT_KEEP]


def seed_drift(d: Dict[str, Any], raw: Optional[str]) -> Dict[str, Any]:
    """PRELOAD measured completion drifts so the drift cap can act from the
    first minute (owner 2026-11-18 "prepare it"). `raw` is JSON
    {sport: ["0.11", ...]} -- MEASURED values only (the 23 strands of
    12:53-13:35Z reconstructed from ORDER/FILL logs). A sport that already
    has live history is NEVER overwritten: live measurements always win and
    the seed rolls out of the DRIFT_KEEP window as new strands arrive.
    Unparseable input seeds nothing and is reported, never guessed."""
    out = {"seeded": {}, "kept_live": [], "error": None}
    if not raw or not str(raw).strip():
        return out
    try:
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise ValueError("not an object")
        clean = {str(k): [str(D(str(x))) for x in v]
                 for k, v in data.items() if isinstance(v, list)}
    except Exception as exc:                             # noqa: BLE001
        out["error"] = f"{type(exc).__name__}"
        return out
    hist = d.setdefault("completion_drift", {})
    for sport, xs in clean.items():
        if hist.get(sport):
            out["kept_live"].append(sport)
            continue
        hist[sport] = xs[-DRIFT_KEEP:]
        out["seeded"][sport] = len(hist[sport])
    return out


def drift_estimate(d: Mapping[str, Any], slug: str) -> Optional[D]:
    """Median recent completion drift for this market's sport, or None when
    fewer than DRIFT_MIN_N strands have been measured."""
    lst = ((d.get("completion_drift") or {}).get(sport_of(slug)) or [])
    try:
        xs = sorted(D(str(x)) for x in lst)
    except Exception:                                    # noqa: BLE001
        return None
    if len(xs) < max(1, DRIFT_MIN_N):
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def open_cap(d: Mapping[str, Any], slug: str) -> D:
    """The opening pair cap: the existing pair_cap, tightened (never
    loosened) by the sport's measured completion drift when enabled."""
    base = pair_cap(slug)
    if not DRIFT_CAP_ON:
        return base
    est = drift_estimate(d, slug)
    if est is None or est <= 0:
        return base
    return min(base, ONE - est - DRIFT_EDGE)


def market_overpay_blocked(mk: Mapping[str, Any]) -> bool:
    """True when the re-entry guard is ON and this market's recorded
    completion overpay has reached the budget. Off (0) -> never."""
    if MARKET_OVERPAY_BUDGET_USD <= 0:
        return False
    try:
        spent = D(str(mk.get("strand_overpay_usd") or "0"))
    except Exception:                                    # noqa: BLE001
        return True              # unreadable ledger: fail closed (no opens)
    return spent >= MARKET_OVERPAY_BUDGET_USD


def completion_policy_live() -> bool:
    if not COMPLETION_POLICY_ON:
        return False
    try:
        from src import l5_completion_policy as CP
        return bool(CP.enabled())
    except Exception:                                    # noqa: BLE001
        return False
# HALT_NEW_EXPOSURE: no new first legs anywhere while unmatched cost is
# at/above this (recovery mode only).
UNMATCHED_HALT_USD = _env_d("PMUS_TWO_SIDED_UNMATCHED_HALT_USD", "2")
RECOVERY_LOG_KEEP = 500
# OWNER 2026-11-15 "build pre-game quoting": all 77 settled markets were
# traded IN-PLAY (the bot only ever selected live games), where every point
# moves the price through resting orders. PREGAME mode selects games that
# have NOT started (start PREGAME_MIN_LEAD_S..PREGAME_MAX_LEAD_S ahead) and
# pulls every opening order PREGAME_CUTOFF_S before the venue's
# gameStartTime; an unmatched leg keeps its maker-only recovery after that.
# DEFAULT OFF.
PREGAME = os.getenv("PMUS_TWO_SIDED_PREGAME", "0").strip() == "1"
PREGAME_MIN_LEAD_S = float(os.getenv("PMUS_TWO_SIDED_PREGAME_MIN_LEAD_S",
                                     "900"))
PREGAME_MAX_LEAD_S = float(os.getenv("PMUS_TWO_SIDED_PREGAME_MAX_LEAD_S",
                                     "21600"))
PREGAME_CUTOFF_S = float(os.getenv("PMUS_TWO_SIDED_PREGAME_CUTOFF_S", "300"))
PREGAME_CACHE_S = float(os.getenv("PMUS_TWO_SIDED_PREGAME_CACHE_S", "120"))
# OWNER 2026-11-22: "I want BOTH pregame and in-game liquidity provision ...
# one continuous, reward-aware, inventory-aware sports controller ... At
# kickoff, revalidate or cancel stale orders before continuing. Pause during
# unsafe price moves, then resume when conditions permit."
# SLATE says which games the one controller may pick up: "live" (as before),
# "pregame" (what PREGAME=1 selects) or "both". On the RN1 path a market
# with a known start time goes pregame -> kickoff -> live
# (src/pmus_sports_phase): from KICKOFF_PULL_S before the start every
# resting order in it is cancelled and nothing is placed until the venue
# lists the game as live, KICKOFF_RESUME_MIN_S have passed since the start,
# the book reads and (when the marginal test is on) the fair value has
# settled. A re-check that has not passed KICKOFF_MAX_HOLD_S after the start
# leaves the market with no new exposure (completions continue).
SLATE = (os.getenv("PMUS_TWO_SIDED_SLATE") or "").strip().lower()


def slate() -> str:
    """live | pregame | both. Unset (or an unknown value) follows the older
    PREGAME switch, so yesterday's configuration selects what it did."""
    if SLATE in ("live", "pregame", "both"):
        return SLATE
    return "pregame" if PREGAME else "live"


KICKOFF_PULL_S = float(os.getenv("PMUS_TWO_SIDED_KICKOFF_PULL_S",
                                 str(PREGAME_CUTOFF_S)))
KICKOFF_RESUME_MIN_S = float(os.getenv(
    "PMUS_TWO_SIDED_KICKOFF_RESUME_MIN_S", "120"))
KICKOFF_MAX_HOLD_S = float(os.getenv(
    "PMUS_TWO_SIDED_KICKOFF_MAX_HOLD_S", "1800"))
# Order size by phase (RN1 path). Unset = RN1.QTY in both phases, as before.
# The in-game size is halved while the venue's own mid has ranged
# VOL_SHRINK_USD or more inside VOL_WINDOW_S; a range of VOL_PAUSE_USD or
# more pulls opening and extension bids for VOL_CALM_S (completions, which
# only lock a pair inside the pair cap, keep resting). 0 = that rule off.
RN1_PREGAME_QTY = (_env_d("PMUS_RN1_PREGAME_QTY", "0")
                   if (os.getenv("PMUS_RN1_PREGAME_QTY") or "").strip()
                   else None)
RN1_LIVE_QTY = (_env_d("PMUS_RN1_LIVE_QTY", "0")
                if (os.getenv("PMUS_RN1_LIVE_QTY") or "").strip() else None)
VOL_WINDOW_S = float(os.getenv("PMUS_TWO_SIDED_VOL_WINDOW_S", "30"))
VOL_PAUSE_USD = _env_d("PMUS_TWO_SIDED_VOL_PAUSE_USD", "0")
VOL_SHRINK_USD = _env_d("PMUS_TWO_SIDED_VOL_SHRINK_USD", "0")
VOL_CALM_S = float(os.getenv("PMUS_TWO_SIDED_VOL_CALM_S", "60"))
# OWNER 2026-11-22: "a cap on the total value of outstanding orders, not
# just filled inventory, so simultaneous fills cannot bypass the intended
# exposure limit". One number for the whole bot: unpaired filled cost plus
# the value of EVERY resting order (own_exposure_usd) plus the order being
# placed must stay within it. Enforced in Engine.place, the one gate every
# order passes. 0 = not configured (the other caps still apply).
AT_RISK_BUDGET_USD = _env_d("PMUS_TWO_SIDED_AT_RISK_BUDGET_USD", "0")
# Selection order on the RN1 path: expected trading result of a first bid
# plus the theoretical reward per contract over RANK_HORIZON_H, cut by
# RANK_REWARD_HAIRCUT and counted only when the projected day total clears
# the minimum payout. A market with no fair value gets no ranking number.
# At most RANK_NEW_PER_ROUND candidates are priced per selection round and
# a ranking number is reused for RANK_TTL_S (one public book read each).
RN1_RANK_NET = os.getenv("PMUS_RN1_RANK_NET", "0").strip() == "1"
# RANK BY OUR SHARE, NOT THE POOL (owner 2026-11-25: "Rank live markets by
# estimated reward capture relative to capital committed, rather than
# headline reward pool size"). Live 24-25 Nov: ranked by pool rate, every
# slot went to ATP (pool $61 an hour, 1,400 contracts at the touch, our
# modelled share 0.3 cents an hour for one contract) ahead of thin markets
# where one contract's modelled share was 15 to 40 cents an hour.
# With RN1_RANK_SHARE on, candidates carrying a paying programme are
# ordered by the THEORETICAL reward of OUR quote (our size, one tick-depth
# behind each touch, the venue's published scoring rule) per dollar that
# quote commits. It needs one public book read per candidate: at most
# RANK_SHARE_NEW_PER_ROUND new ones per selection round, taken in turn
# across competitions, each kept RANK_SHARE_TTL_S. No signed request.
# Ranking only: every bid still passes every gate and cap, and nothing
# here is a payment. 0 = off (pool rate, as before).
RN1_RANK_SHARE = os.getenv("PMUS_RN1_RANK_SHARE", "0").strip() == "1"
RANK_SHARE_NEW_PER_ROUND = int(os.getenv(
    "PMUS_RN1_RANK_SHARE_NEW_PER_ROUND", "3"))
RANK_SHARE_TTL_S = float(os.getenv("PMUS_RN1_RANK_SHARE_TTL_S", "300"))
# HYSTERESIS for a resting bid (see RN1.quotes): kept at its own price while
# it misses the marginal test by no more than RN1_KEEP_TOL_USD, for at most
# RN1_KEEP_MAX_S in a row; then it is pulled like any failing bid. 0 = off
# (every failing bid is pulled at once, as before).
RN1_KEEP_TOL_USD = _env_d("PMUS_RN1_KEEP_TOL_USD", "0")
# EXACT MARKETS AND SIDES (owner 2026-11-23: "Prepare Option B using a
# minimal market-and-side pinning setting, default off, with regression
# tests"). PMUS_RN1_PIN="<slug>:YES,<slug>:NO,<slug>" names the ONLY markets
# the RN1 path may open in and, where a side is given, the only side it may
# bid there. Unset = no pin (every market and side as before). With a pin:
#   - selection considers pinned markets only;
#   - in a pinned market an opening or extension bid on the other side is
#     dropped ("pinned_other_side");
#   - a market that is not pinned gets no new exposure ("not_pinned").
# A completion is never dropped by the pin: it only pairs what is held. Every
# other gate still applies; a pin can only narrow what the bot may do. A
# malformed entry pins nothing AND opens nothing (fail closed).


def parse_pin(raw: Optional[str]) -> Optional[Dict[str, Optional[str]]]:
    """None = no pin configured. {} = a pin that allows nothing (malformed
    input). Otherwise {slug: "YES" | "NO" | None (both sides)}."""
    raw = (raw or "").strip()
    if not raw:
        return None
    out: Dict[str, Optional[str]] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        slug, sep, side = part.partition(":")
        slug, side = slug.strip(), side.strip().upper()
        if not slug.startswith(SPORTS_PREFIXES) or \
                (sep and side not in ("YES", "NO")) or slug in out:
            return {}
        out[slug] = side if sep else None
    return out


def rn1_pin() -> Optional[Dict[str, Optional[str]]]:
    return parse_pin(os.getenv("PMUS_RN1_PIN"))
RN1_KEEP_MAX_S = float(os.getenv("PMUS_RN1_KEEP_MAX_S", "60"))
# HOLD THROUGH A SHORT FAIR-VALUE GAP. LIVE 2026-11-23: the same prices were
# cancelled and re-posted every 30-90 s because the international fair value
# dropped out for a pass ("no_fair": 444 of 1,111 side decisions). Six of
# eight exposure-adding fills had rested under 15 s, and only 13% of
# pre-game resting time counted for rewards. Every re-post goes to the back
# of its price level and restarts the reward clock.
# With RN1_GAP_HOLD_S > 0 an opening/extension bid ALREADY RESTING is left
# where it is when the ONLY reason to pull it is that the fair value is
# missing this pass, and ALL of:
#   * the same bid at the same price passed the full marginal test no more
#     than RN1_GAP_HOLD_S ago (a hold never renews itself);
#   * the venue's own best bid and ask have not changed since that pass;
#   * the international price is not known to be moving (a fair value
#     that is unsettled BECAUSE it moved pulls the bid, as now);
#   * the depth rule still passes, and nothing blocks new exposure (any
#     block, pause, lock or halt pulls it, as now).
# It never places a new bid and never moves one. 0 = off (today).
RN1_GAP_HOLD_S = float(os.getenv("PMUS_RN1_GAP_HOLD_S", "0"))
#: 1 (default) = a market with an unpaired leg holds a quoting slot, as
#: since 2026-11-22. 0 = only a market with a resting opening/extension bid
#: does (see Engine._rn1_slot_holder).
RN1_SLOTS_COUNT_INVENTORY = os.getenv(
    "PMUS_RN1_SLOTS_COUNT_INVENTORY", "1").strip() != "0"
# FILL DETECTION FROM ONE LIST READ (owner 2026-11-25: "Eliminate redundant
# requests ... Support substantially more concurrent markets within the
# existing venue request budget"). Measured live, 4 markets: about 70% of
# all signed requests were one GET /v1/order/{id} per open order per pass
# (2,669 of them in 70 minutes); at 8 markets those polls alone exceed the
# one-request-a-second budget, and they are never deferred, so placements
# and audits starve.
# With FILL_LIST_POLL on, one GET /v1/orders/open per pass stands in for
# them. An order's own status is still read EXACTLY, as now, unless the
# list PROVES nothing happened to it, i.e. ALL of:
#   * the list was read no more than FILL_LIST_MAX_AGE_S ago;
#   * the order was placed at least FILL_LIST_SETTLE_S before that read;
#   * the order is in the list, in an ordinary resting state;
#   * the list row carries a readable filled quantity and it equals the
#     quantity the book has already recorded;
#   * the order's exact status was read no more than FILL_EXACT_EVERY_S ago.
# A fill is recorded only from the exact read, never from the list. An
# order missing from the list, changed, unreadable or too new is read
# exactly at once, so detection is as fast as before. A failed list read
# falls back to exact polls for every order. 0 = off (as before).
FILL_LIST_POLL = os.getenv("PMUS_TWO_SIDED_FILL_LIST_POLL",
                           "0").strip() == "1"
FILL_LIST_REFRESH_S = float(os.getenv(
    "PMUS_TWO_SIDED_FILL_LIST_REFRESH_S", "4"))
FILL_LIST_MAX_AGE_S = float(os.getenv(
    "PMUS_TWO_SIDED_FILL_LIST_MAX_AGE_S", "8"))
FILL_LIST_SETTLE_S = float(os.getenv(
    "PMUS_TWO_SIDED_FILL_LIST_SETTLE_S", "2"))
FILL_EXACT_EVERY_S = float(os.getenv(
    "PMUS_TWO_SIDED_FILL_EXACT_EVERY_S", "60"))
# REQUEST AUDIT 2026-11-28 (analysis/l5/sports/REQUEST_AUDIT_20261128.md).
# Both default OFF = exactly the requests made before.
#
# ACCT_SEEDS_FILL_LIST: the account read (every 20 s) already fetches
# GET /v1/orders/open, the same list FILL_LIST_POLL reads every
# FILL_LIST_REFRESH_S. With "1", a SUCCESSFUL account read hands its list to
# the fill poll, stamped with the moment just before that request was sent,
# so the fill poll's next own read is due one refresh period later (about
# one list read in five is not made). Nothing else changes: every proof the
# list must give (age, settle time, state, filled quantity, the 60 s exact
# read) is checked exactly as for a list the fill poll read itself, a fill
# is still recorded only from the exact read, and a failed account read
# seeds nothing (the fill poll reads the list itself, as before).
ACCT_SEEDS_FILL_LIST = os.getenv(
    "PMUS_TWO_SIDED_ACCT_SEEDS_FILL_LIST", "0").strip() == "1"
# LIVE_EVENTS_TTL_S: Venue.live_sports() (one 500-row public events page,
# plus the crypto page when configured) is read by selection every 20 s, a
# second time in the SAME pass to keep game clocks fresh when the slots are
# full, and again by a kick-off hold. With a value > 0 a read newer than
# this many seconds is reused. 0 (default) = every call reads. Public
# gateway only; never on the signed budget.
LIVE_EVENTS_TTL_S = float(os.getenv(
    "PMUS_TWO_SIDED_LIVE_EVENTS_TTL_S", "0") or 0)
# A PAUSE IS NOT A REASON TO ABANDON A MARKET. LIVE 2026-11-24 01:20Z: a
# flat market with nothing to quote for 120 s gives up its slot, a
# price-move pause counted as "nothing to quote", and a released market was
# never looked at again. In-play tennis pauses on almost every point: all
# three markets were dropped within eight minutes and the bot sat idle for
# the rest of an authorised hour while one of them traded in band.
#   RN1_PAUSED_RELEASE_S  how long a flat market that is only PAUSED (price
#       move, or the international price missing) keeps its slot before it
#       is released. It is still released after that, so a market that
#       never calms down does not hold a slot against better ones.
#       Default 120 = today.
#   RN1_RESELECT          1 = a market released flat is a candidate again
#       once RN1_RESELECT_COOLDOWN_S has passed, through the SAME filters
#       as any other candidate (in the slate, in band, in scope, not late,
#       priced). Its record (orders, fills, inventory, costs) is the same
#       one, so nothing about its accounting restarts. A market that
#       closed, settled or holds anything is never reselected. Default 0.
RN1_PAUSED_RELEASE_S = float(os.getenv("PMUS_RN1_PAUSED_RELEASE_S", "120"))
# ADAPTIVE PAUSE DEPTH (2026-11-27, default OFF). Campaign 27 Nov
# 13:45-16:45Z: 373 of 466 side-outcomes the reward-adjusted placement scored
# positive were blocked by jump_pause / vol_pause, and bids rested only 6.7%
# of the window. With PMUS_RN1_PAUSE_DEPTH=1 a jump or volatility pause no
# longer withdraws every opening/extension bid: they may rest no closer than
# the 30 s mid range (rounded up to ticks) plus PMUS_RN1_PAUSE_DEPTH_EXTRA_TICKS
# below the touch -- below where the move has already been -- and only if
# every later gate (marginal EV with the behind-the-touch fill loss, the
# international guard, every cap, the final gate) still passes. The touch is
# never joined during a pause. Completions are unchanged. 0 = as before.
RN1_PAUSE_DEPTH = os.getenv("PMUS_RN1_PAUSE_DEPTH", "0").strip() == "1"
RN1_PAUSE_DEPTH_EXTRA_TICKS = int(os.getenv(
    "PMUS_RN1_PAUSE_DEPTH_EXTRA_TICKS", "1"))
#: with RN1_PAUSE_DEPTH, rest the venue minimum size while paused (1 = on)
RN1_PAUSE_MIN_SIZE = os.getenv("PMUS_RN1_PAUSE_MIN_SIZE", "1").strip() == "1"
# PAUSED-MARKET ROTATION (default 0 = as before). A flat market with nothing
# resting that is only paused (jump/vol) releases its slot after this many
# seconds instead of max(120, RN1_PAUSED_RELEASE_S), so another eligible
# market can be quoted; with RN1_RESELECT it may be selected again later.
RN1_PAUSE_ROTATE_S = float(os.getenv("PMUS_RN1_PAUSE_ROTATE_S", "0"))
RN1_RESELECT = os.getenv("PMUS_RN1_RESELECT", "0").strip() == "1"
RN1_RESELECT_COOLDOWN_S = float(os.getenv(
    "PMUS_RN1_RESELECT_COOLDOWN_S", "60"))
# FINAL ORDER GATE (owner 2026-11-24). On the RN1 path the order size, the
# campaign and per-market ceilings and the pair-cost ceiling were applied
# where the quote is BUILT and not again where the order is SENT; an
# oversize order was only caught on the next pass. With FINAL_GATE on
# (default) every order is checked again, independently, immediately before
# submission, and a violation prevents the submission:
#   * size:      qty <= the largest size any phase may use
#   * maker:     on the RN1 path a non-post-only order is refused unless
#                ALLOW_TAKER=1 (the older two-sided path keeps its own
#                taker authorization)
#   * ceilings:  this market's and the campaign's worst-case spend and
#                unpaired cost, counting every resting order already
#                recorded PLUS this one (so two orders that each fit alone
#                cannot both be sent when together they do not)
#   * pair cost: this bid plus the dearest thing it could pair with -- the
#                other side's filled average if it would complete held
#                inventory, and every bid resting on the other side -- must
#                not exceed the pair cap. It never refuses a bid for NOT
#                pairing: adding to the heavier side is untouched.
# The checks already at the gate stay where they are: authorization (lock /
# window / campaign file), the emergency halt, strict scope, the account
# limit with its fail-closed read, the portfolio budget and depth.
FINAL_GATE = os.getenv("PMUS_TWO_SIDED_FINAL_GATE", "1").strip() != "0"
ALLOW_TAKER = os.getenv("PMUS_TWO_SIDED_ALLOW_TAKER", "0").strip() == "1"
# DO NOT CHASE THE BEST PRICE UPWARD. Today a resting bid is cancelled and
# re-posted whenever its target moves by one tick, in either direction.
# When the target has moved AWAY (the best price improved, so our bid is
# now deeper than intended) the resting bid is still wanted, at a better
# price than the one the test just passed, and it has its place in the
# queue. With RN1_KEEP_DEEPER_TICKS = n an opening/extension bid up to n
# ticks BELOW its target is left alone. A bid ABOVE its target (the market
# came toward us) is re-priced or pulled exactly as now, and so is a
# completion. 0 = off (today).
RN1_KEEP_DEEPER_TICKS = int(os.getenv("PMUS_RN1_KEEP_DEEPER_TICKS", "0"))
# COMPLETION HOLD (28 Nov, default 0 = off). LIVE 28 Nov 00:16-03:15Z
# (6e6cc2d5d): 76 completion orders for 8 completion fills, re-placed a
# median 20 s apart; 49 re-places only moved the price (mostly 1 tick as the
# touch moved) and 9 changed nothing, while 30 completion orders were
# deferred by the shared request budget. With PMUS_RN1_COMPLETE_KEEP_TICKS
# = k a RESTING completion bid is kept when the new completion target is
# 0..k ticks ABOVE it (so the resting bid is never dearer than the target:
# the pair-cost ceiling and every gate still hold) and its remaining size
# fits the target. A lower target (cheaper completion), a larger shortfall
# or any other change re-places it as before.
RN1_COMPLETE_KEEP_TICKS = int(os.getenv("PMUS_RN1_COMPLETE_KEEP_TICKS")
                              or "0")
# COMPLETIONS IN THE SAFETY CLASS (28 Nov, default off). LIVE 28 Nov: 30 of
# 33 budget deferrals were completion orders, queued behind 11 HTTP 429
# cool-downs as "normal" like openings. With PMUS_RN1_COMPLETION_SAFETY_CLASS
# =1 a completion placement (and its post-only preview) is a safety request:
# the shared budget lets it wait up to its safety wait instead of deferring
# it, and normal traffic yields to it. The rate, burst and cool-downs are
# unchanged; openings and extensions stay normal.
RN1_COMPLETION_SAFETY_CLASS = os.getenv("PMUS_RN1_COMPLETION_SAFETY_CLASS",
                                        "0").strip() == "1"
# INVENTORY AT SEVERAL PRICE LEVELS (owner 2026-11-25: "Build inventory at
# different price levels", "Maintain multiple resting orders", "We want
# sustained inventory accumulation, not isolated two-contract transactions
# followed by inactivity"). Until now each side of a market held ONE
# resting bid, so a side could buy at most one clip per price move and
# then waited for the next re-quote. With RN1_LADDER_LEVELS = N > 1, behind
# every opening or same-side extension bid the controller also rests up to
# N-1 further bids of the same size, each RN1_LADDER_STEP_TICKS deeper.
# A falling price then fills them in turn: the side accumulates at
# progressively lower prices instead of one clip at a time.
# Bounded by everything that bounds one bid: each level is added only
# while the market's worst case WITH every level filled stays inside the
# per-market spend and unpaired caps and the campaign caps, the price
# floor applies, the final gate re-checks every order against all resting
# orders, and the account limit counts each order as it rests. Completion
# bids are never laddered. When the price moves, a resting bid that now
# sits at another level's price is kept, not cancelled and re-placed.
# 1 = one bid per side, exactly as before.
RN1_LADDER_LEVELS = max(1, int(os.getenv("PMUS_RN1_LADDER_LEVELS", "1")))
RN1_LADDER_STEP_TICKS = max(1, int(os.getenv(
    "PMUS_RN1_LADDER_STEP_TICKS", "2")))
# PULL ONLY THE THREATENED BID (owner 2026-11-25: "Determine whether
# economically acceptable individual ladder levels are being unnecessarily
# pulled. Preserve hard safety cancellations."). The reactive pull fired
# when ANY resting bid came within a tick of its touch or of crossing, and
# then cancelled EVERY bid in the market: the other side and every deeper
# ladder level with it (live 18:23-18:38 UTC 25 Nov: median bid life 21 s).
# With 1, only the bids that themselves meet a pull condition are
# cancelled; the rest keep their place and the next pass re-prices as
# usual. The jump pause (a move of 2+ ticks) still cancels everything, and
# so do every lock, halt, cap and account block. 0 = as before.
# THE CAMPAIGN LOSS STOP COUNTS RESTING BIDS (found 2026-11-25 19:40 UTC).
# The stop refuses new openings once realized results plus every held
# unpaired contract settling worthless reach -PILOT_LOSS_USD. Resting bids
# were not counted, so several ladder levels could fill at once and carry
# the worst case past the stop: live 18:40:57, two YES levels filled
# together (worst case -1.67 - 1.78 - 2.60 = -6.05 against a $4 stop).
# On (default), the final gate also refuses an opening or extension when
# the worst case of the book PLUS every resting and reserved bid PLUS this
# order (each filling and losing, the same arithmetic as the account
# limit) would reach the stop. Completions are never refused by it.
RN1_LOSS_STOP_COUNTS_RESTING = os.getenv(
    "PMUS_RN1_LOSS_STOP_COUNTS_RESTING", "1").strip() == "1"
# REWARD-QUALIFIED QUOTING (owner 2026-11-25: "Determine whether we can
# maintain reward-qualifying size at the best bid while accumulating at
# deeper levels"; "Prioritize markets that currently satisfy reward
# qualification requirements. Continuously replace markets that cease
# qualifying"). Measured on live books 20:23 UTC: a 10-contract bid scores
# about 3x more AT the best bid than one tick behind, and most books are
# one tick wide, where the bot never joined.
#   RN1_JOIN_ONE_TICK=1     the join rule (RN1_JOIN_TOUCH) may join on a
#                           one-tick spread when calm; a joined bid is no
#                           longer pulled for sitting one tick from the
#                           offer (it is post-only: it cannot take). The
#                           pair-cost limit still allows only one side at
#                           the best bid on a one-tick spread.
#   RN1_LADDER_QTY          size of every deeper ladder level (level 0
#                           keeps the full clip); empty = same as level 0.
#   RN1_RELEASE_UNQUALIFIED_S  a market holding no inventory whose quote
#                           would earn no reward under the venue's rule
#                           for this long is released, so selection can
#                           replace it. 0 = off.
RN1_JOIN_ONE_TICK = os.getenv("PMUS_RN1_JOIN_ONE_TICK", "0").strip() == "1"
_lq = os.getenv("PMUS_RN1_LADDER_QTY", "").strip()
RN1_LADDER_QTY = D(_lq) if _lq else None
RN1_RELEASE_UNQUALIFIED_S = float(os.getenv(
    "PMUS_RN1_RELEASE_UNQUALIFIED_S", "0") or 0)
# RELEASE BALANCED INVENTORY (27 Nov, default OFF). LIVE 27 Nov 21:32Z: a
# market whose pair completed (2 YES / 2 NO, no unpaired leg) wanted no bid
# for the rest of the match but kept one of the two quoting slots, because
# the idle release only applied to a FLAT (0/0) market. With
# PMUS_RN1_RELEASE_BALANCED=1 a balanced market (YES = NO > 0) with no bid
# wanted and no order open is released after the same idle time as a flat
# one. Its inventory stays in the book and in every limit, the meter and
# the audit (accounting reads all markets); only the slot is freed.
RN1_RELEASE_BALANCED = os.getenv("PMUS_RN1_RELEASE_BALANCED",
                                 "0").strip() == "1"
RN1_PULL_THREATENED_ONLY = os.getenv(
    "PMUS_RN1_PULL_THREATENED_ONLY", "0").strip() == "1"
# NET-PROFIT RANKING (owner 2026-11-25: "Rank markets by expected net
# profit after inventory acquisition losses, not gross projected rewards";
# "Compare joining the best bid against resting one tick deeper on a
# market-by-market basis"; "Recalculate marginal economics after each fill
# before replenishing"). With RN1_NET_RANK on (needs RN1_RANK_SHARE), each
# candidate is priced at two placements, at the best bid and one tick
# behind it, with our own clip on both sides:
#   net per hour = THEORETICAL reward of that quote (venue rule)
#                  - expected contracts filled per hour x loss per contract
# expected contracts filled per hour = clip x FILL_RATE (contracts filled
# per resting contract-hour, measured live, by placement and competition)
# per side. The better placement is kept per market: the join rule may
# join only where it is the better one. Markets are ranked by that net per
# dollar committed; a market estimated at zero or below is not selected,
# a flat one is released (RN1_RELEASE_UNQUALIFIED_S), and one holding
# inventory stops replenishing (openings and extensions; completions
# continue) until a fresh estimate is positive again. Every fill clears the
# market's estimate, so the next pass re-prices it on the new book first.
# Estimates only, not payments; every bid still passes every gate and cap.
RN1_NET_RANK = os.getenv("PMUS_RN1_NET_RANK", "0").strip() == "1"
# Own resting orders are deducted from the public book's competition only
# once they are at least this old (the public snapshot can lag our order's
# acknowledgement; until then the order is left in the denominator).
RN1_NET_OWN_LAG_S = float(os.getenv("PMUS_RN1_NET_OWN_LAG_S", "5") or 5)
# HOLD THROUGH A DIP (owner 2026-11-26: "Do not rotate away merely because
# one side fills or a temporary estimate fluctuates"; "Preserve productive
# resting orders and queue priority"). Live 26 Nov: estimates near zero
# flipped sign every minute or two, and every flip cancelled the ladder.
# With RN1_NET_HOLD_S > 0, once the fresh net estimate turns not positive
# nothing NEW is placed (no opening, extension or replenishment), but bids
# already resting are kept for up to RN1_NET_HOLD_S, as long as each still
# sits at or behind its own side's best bid (it never becomes the price-
# setting bid by staying). After that, or on any other block (pause, late,
# cap, lock), they are cancelled as before. Completions are unaffected.
# 0 = off (cancel at once, as before).
RN1_NET_HOLD_S = float(os.getenv("PMUS_RN1_NET_HOLD_S", "0") or 0)
# DURATION-AWARE RANKING (owner 2026-11-26, default OFF; see
# src/pmus_duration_rank.py). A market about to end should not take a slot
# from one with a long remaining life. With RN1_DURATION_RANK on, each
# candidate's expected remaining quoting time (to the late cut-off) comes
# from its competition's expected duration (PMUS_RN1_DURATION_MIN, merged
# over conservative defaults), its scheduled start and whether the venue
# lists it live. Then:
#   - a candidate with a KNOWN remaining time below RN1_MIN_REMAINING_S is
#     not a candidate (an unknown one never is excluded for that alone);
#   - the share/net ranking (RN1_RANK_SHARE, RN1_NET_RANK) scales each
#     positive per-hour estimate by min(remaining, horizon) / horizon;
#   - the per-contract ranking (RN1_RANK_NET) counts the reward term only
#     for min(remaining, horizon) instead of the whole horizon.
# Unknown remaining time gets RN1_DURATION_UNKNOWN_FACTOR (no full-life
# credit). Ranking only: every bid still passes every gate and cap.
RN1_DURATION_RANK = os.getenv("PMUS_RN1_DURATION_RANK", "0").strip() == "1"
RN1_MIN_REMAINING_S = float(os.getenv("PMUS_RN1_MIN_REMAINING_S")
                            or "900")              # 0 = no exclusion
RN1_DURATION_HORIZON_S = float(os.getenv("PMUS_RN1_DURATION_HORIZON_S")
                               or "3600")
RN1_DURATION_UNKNOWN_FACTOR = float(os.getenv(
    "PMUS_RN1_DURATION_UNKNOWN_FACTOR") or "0.5")
RN1_DURATION_MIN = os.getenv("PMUS_RN1_DURATION_MIN", "")
# LIVE-FIRST SLOTS (owner 2026-11-27, default OFF). LIVE 26-27 Nov, two
# slots: both went to table-tennis pre-game markets starting hours later
# (theoretical +$0.04-0.41/h), while the capital they tie up stays tied
# until settlement. With RN1_LIVE_FIRST on (src/pmus_duration_rank.py):
#   - each candidate is classed live / near (start within
#     RN1_NEAR_START_S) / distant / unknown (no start on record);
#   - the share/net ranking (RN1_RANK_SHARE) orders positive estimates by
#     per_dollar_hour x (H/3600) x min(1, H/R), R = remaining quoting time
#     (time-to-settlement proxy), H = RN1_DURATION_HORIZON_S, unknown R ->
#     RN1_DURATION_UNKNOWN_FACTOR; this replaces the duration factor when
#     RN1_DURATION_RANK is also on (never applied twice);
#   - at most RN1_DISTANT_MAX_SLOTS slots may be claimed by distant
#     markets at selection;
#   - when every slot is claimed and a live/near candidate has a fresh
#     positive estimate, ONE flat distant market (no contracts, no
#     completion bid) yields: its resting openings are cancelled through
#     the normal cancel path and it is released flat (reselectable as any
#     flat release). A market holding contracts is never preempted.
# Selection only: every bid still passes every gate and cap.
RN1_LIVE_FIRST = os.getenv("PMUS_RN1_LIVE_FIRST", "0").strip() == "1"
# RANK BY NET PER SLOT (27 Nov, default "per_dollar" = unchanged). LIVE 27
# Nov: with RN1_LIVE_FIRST / RN1_DURATION_RANK the share ranking orders
# positive estimates by net per dollar x turnover (min(1, H / time to
# settlement)), so a pregame programme market starting in 3 h is scored at
# about 0.29 of its value, while capital is not binding (mean $1.30 used of
# $60) and the two quoting SLOTS are. With PMUS_RN1_RANK_BY=net_per_hour
# positive markets are ordered by the slot's absolute reward-adjusted net
# per hour (the touch-EV / net estimate), with no turnover, duration or
# live-first factor; a non-positive estimate is never selected ahead of a
# positive one. Use with PMUS_RN1_LIVE_FIRST=0 (its distant-slot cap and
# pre-emption are slot rules of their own).
RN1_RANK_BY = os.getenv("PMUS_RN1_RANK_BY", "per_dollar").strip().lower()
# LIVE SLOT CAP (28 Nov, default 0 = off: one cap, MAX_MARKETS, as before).
# RN1 (30 d, 274,695 fills) works a median 21 conditions at once (p90 89);
# L5 works MAX_MARKETS = 2. Our positive economics are pregame (day-of
# programmes, fill rate 0.20 vs 3.0 per contract-hour, milder markouts);
# live markets carry the negative markouts and 72% of quote decisions are
# jump/vol paused. With PMUS_RN1_MAX_LIVE_MARKETS = k > 0, at most k LIVE
# markets may hold a quoting slot (selection skips a live candidate and a
# live market may add no new exposure while k other live markets hold
# slots; completions are never blocked); MAX_MARKETS still bounds the total.
# A pregame market that goes live while the live slots are full can only
# complete until one frees. Every dollar cap, stop and gate is unchanged:
# raising MAX_MARKETS itself remains an owner decision.
RN1_MAX_LIVE_MARKETS = int(os.getenv("PMUS_RN1_MAX_LIVE_MARKETS") or "0")
RN1_NEAR_START_S = float(os.getenv("PMUS_RN1_NEAR_START_S") or "1800")
RN1_DISTANT_MAX_SLOTS = int(os.getenv("PMUS_RN1_DISTANT_MAX_SLOTS") or "1")
# NO SELECT/RELEASE LOOP (owner 2026-11-27, default OFF). LIVE: with every
# slot held (one by a market carrying inventory) a third market was
# selected, blocked `max_markets`, released flat after 2 minutes and
# re-selected, again and again. With RN1_SELECT_NO_CHURN on, a market is
# not selected while the slot claimants (slot holders, see _rn1_slot_holder,
# plus selected markets with no unpaired leg still waiting to quote) already
# number MAX_MARKETS. Logged once per change.
RN1_SELECT_NO_CHURN = os.getenv("PMUS_RN1_SELECT_NO_CHURN",
                                "0").strip() == "1"
# MARGINAL NET EV PER BID (external review of 1b496656c, findings 1, 2, 4).
# The market-level net estimate (RN1_NET_RANK) stops every opening and
# extension in a market whose estimated reward does not exceed a fixed
# expected fill loss, even where a bid is cheap against the international
# fair value; and a market-level positive estimate lets through a bid that
# adds no reward and loses on its fills. With PMUS_RN1_MARGINAL_EV=1 every
# opening/extension target AND every deeper ladder level is judged on its
# own (src/pmus_rn1_replica.marginal_ev), per resting hour:
#   fills/h x (fair - px - maker fee - adverse - concentration risk)
#   + incremental THEORETICAL reward of this order (same-snapshot marginal
#     ladder score, credited only when the day's projection clears the
#     venue's minimum payout, then x RANK_REWARD_HAIRCUT)
#   + rebates: none (no rebate is sourced; 0)
#   fills/h      = qty x RN1_FILL_RATE (touch / behind), per competition
#   adverse      = the larger of the marginal test's allowance
#                  (ADVERSE_TICKS) and RN1_FILL_LOSS for the placement
#   fair, risk   = exactly as the marginal test (RN1.edge) computes them;
#                  each deeper level counts the levels above it as filled
# A bid is kept only when that total is positive AND its trade edge at the
# marginal test's own allowance is not negative (rewards never rescue a
# negative trade edge). Without a fair value (marginal test off, or none
# fresh) nothing is evaluated and the controller behaves exactly as before.
# Where capital is short, the campaign cap removes the target of lowest
# marginal EV first instead of the dearest (finding 2). A market whose
# market-level estimate is not positive keeps quoting the bids that pass
# on their own; only when none passes is it blocked "net_not_positive"
# (hold, release and cancellation then exactly as before), so neither the
# hold expiry nor the unqualified release cancels bids that pass (finding
# 4). Completions are not evaluated here. Every cap, lock, halt and gate is
# unchanged; this can only remove bids from, or stop the market-level block
# removing bids from, the plan the existing rules produce. 0 = off.
RN1_MARGINAL_EV = os.getenv("PMUS_RN1_MARGINAL_EV", "0").strip() == "1"
# MARGINAL NET EV WITHOUT A FAIR VALUE (live 26 Nov: doubles, table
# tennis, UTR and ITF have no international feed, so RN1_MARGINAL_EV judged
# none of their bids). With PMUS_RN1_MARGINAL_EV_NO_FAIR=1 (needs
# PMUS_RN1_MARGINAL_EV=1) a market with no international fair value has
# each opening/extension target and each deeper level judged on its own
# (src/pmus_rn1_replica.marginal_ev_no_fair), per resting hour:
#   incremental THEORETICAL reward (same-snapshot marginal ladder score,
#     only when the day's projection clears the minimum payout, x
#     RANK_REWARD_HAIRCUT)
#   - fills/h x (RN1_FILL_LOSS + maker fee + concentration risk)
#   + rebates: none (0)
#   fills/h = qty x RN1_FILL_RATE (touch / behind), per competition; the
#   risk charge is RN1.concentration_risk, exactly the marginal test's.
# NO TRADE-EDGE TERM: there is no fair value and none is substituted (not
# the venue's mid, not a model). RN1_FILL_LOSS -- the settings cost the
# market-level net estimate charges per fill -- is the adverse-selection
# proxy. A bid rests only if that total is positive, so with no qualifying
# reward nothing does. A market WITH a fair value is judged by
# RN1.marginal_ev exactly as before.
# ONLY WITH PMUS_RN1_EDGE_GATE=0. With the marginal test on, a market with
# no fair value gets no bid at all (RN1.quotes refuses it "no_fair"); this
# mode never overrides that and is inert there. With the gate off it can
# only remove bids from the plan the existing rules produce, or (as
# RN1_MARGINAL_EV does for fair-value markets) keep the market-level
# net_not_positive block off bids that pass on their own. Every cap, lock,
# halt and maker-only rule is unchanged. 0 = off.
RN1_MARGINAL_EV_NO_FAIR = os.getenv("PMUS_RN1_MARGINAL_EV_NO_FAIR",
                                    "0").strip() == "1"
RN1_MARGINAL_BOOK_TTL_S = float(os.getenv(
    "PMUS_RN1_MARGINAL_BOOK_TTL_S", "60") or 60)
# FIT EVERY LEVEL (review finding 3). PMUS_RN1_FIT_QTY sizes level 0 down to
# the per-market caps only, and ladder levels only while it is on; the
# campaign caps and the two-bid per-market cap drop a whole bid, and the
# first deeper level that does not fit ends its side's ladder. With
# PMUS_RN1_FIT_ALL_LEVELS=1 every one of those is first tried at each
# smaller whole size down to the venue minimum, and a level that does not
# fit at the minimum is skipped rather than ending the ladder (a deeper,
# cheaper level may still fit). No cap changes. 0 = off.
RN1_FIT_ALL_LEVELS = os.getenv("PMUS_RN1_FIT_ALL_LEVELS",
                               "0").strip() == "1"


def _comp_rates(name: str, default: str) -> Dict[str, float]:
    """"0.2" or "0.2,utr:0.5,atpdb:0.1" -> {"": 0.2, "utr": 0.5, ...}."""
    out: Dict[str, float] = {"": float(default)}
    for part in (os.getenv(name) or "").split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.rpartition(":")
        try:
            out[k.strip().lower()] = float(v)
        except ValueError:
            continue
    return out


# Clip sizes the net ranking compares per market (each capped at the
# configured clip): in a thin book our reward share stops growing with size
# while the expected fill cost keeps growing, so the smallest size that
# qualifies often nets the most. The chosen size caps that market's clip.
RN1_NET_SIZES = tuple(sorted({D(x.strip()) for x in os.getenv(
    "PMUS_RN1_NET_SIZES", "1,2,5,10").split(",") if x.strip()}))
RN1_FILL_RATE = {0: _comp_rates("PMUS_RN1_FILL_RATE_TOUCH", "0.5"),
                 1: _comp_rates("PMUS_RN1_FILL_RATE_BEHIND", "0.2")}
RN1_FILL_LOSS = {0: _comp_rates("PMUS_RN1_FILL_LOSS_TOUCH", "0.05"),
                 1: _comp_rates("PMUS_RN1_FILL_LOSS_BEHIND", "0.035")}

# DIRECTIONAL INVENTORY ALLOCATION (2026-11-27, default OFF; see
# src/pmus_directional_alloc.py). With PMUS_RN1_DIRECTIONAL=1 every opening
# and same-side addition bid the quoting rule wants is judged on the
# international fair value (intl_fair_band: the mid and its range over
# RN1_FAIR_STABLE_S, only once settled): it is kept, resized or refused by its
# marginal EV per contract after the competition's fill loss and an
# inventory-concentration charge, the side the fair value does not favour
# rests a tick further back, and ladder levels are judged on the inventory
# they would add. Without a settled fair value nothing changes. Completions,
# pair-cost limits, caps, loss stops, the guard and every other gate are
# untouched; it can only remove, shrink or deepen a bid, never add one.
RN1_DIRECTIONAL = os.getenv("PMUS_RN1_DIRECTIONAL", "0").strip() == "1"
RN1_DIR_CFG = {
    "risk_aversion": os.getenv("PMUS_RN1_DIR_RISK_AVERSION", "0.005"),
    "min_ev": os.getenv("PMUS_RN1_DIR_MIN_EV", "0"),
    "hysteresis": os.getenv("PMUS_RN1_DIR_HYSTERESIS", "0.01"),
    "depth_unfavoured_ticks": int(os.getenv(
        "PMUS_RN1_DIR_DEPTH_UNFAVOURED_TICKS", "1")),
    "touch_edge": os.getenv("PMUS_RN1_DIR_TOUCH_EDGE", "0.02"),
    # 0 (default): openings on a flat side pass unchanged so both sides
    # still open and pair; only same-side additions (and ladder levels
    # behind a bid) are judged. Backtest 2026-11-27 (recorded two-venue
    # books, 21 Nov): judging openings too made inventory one-sided.
    "judge_openings": os.getenv("PMUS_RN1_DIR_JUDGE_OPENINGS",
                                "0").strip() == "1",
    "up_mult": os.getenv("PMUS_RN1_DIR_UP_MULT", "1"),
    "extra_levels": int(os.getenv("PMUS_RN1_DIR_EXTRA_LEVELS", "0")),
    "upsize_edge": os.getenv("PMUS_RN1_DIR_UPSIZE_EDGE", "0.03"),
}
# Same-side additions priced by their marginal EV against the probability
# instead of the average-cost "don't chase" cap (only while a probability
# is in force; the residual, per-market, campaign and account caps apply).
RN1_DIR_PRICE_ADDS = os.getenv("PMUS_RN1_DIR_PRICE_ADDS", "0").strip() == "1"
# Sportsbook probability (the sharp shadow feed's de-vigged fair value) when
# no international fair value is settled. Refused past MAX_AGE_S; younger
# readings widen the band by DRIFT per second of age.
RN1_DIR_SHARP = os.getenv("PMUS_RN1_DIR_SHARP", "0").strip() == "1"
RN1_DIR_SHARP_MAX_AGE_S = float(os.getenv("PMUS_RN1_DIR_SHARP_MAX_AGE_S",
                                          "900"))
RN1_DIR_SHARP_DRIFT = os.getenv("PMUS_RN1_DIR_SHARP_DRIFT", "0.00002")

# TRADING-FIRST ADMISSION (owner #251, 28 Nov; default 0 = unchanged).
# Every bid that ADDS directional exposure -- an opening, a same-side
# addition, or a deeper ladder level that is not a completion -- must pass a
# PRE-REWARD trading test of its own:
#   * an independent probability (rn1_directional_prob: the settled
#     international fair, or with RN1_DIR_SHARP the age-limited sportsbook
#     fair; never the traded market's own price) -- none = refused
#     ("tf_no_fair");
#   * the bid at least RN1_TF_EDGE_USD under the CONSERVATIVE fair for its
#     side (band low for YES, 1 - band high for NO) -- otherwise refused
#     ("tf_negative_ev"). Reward credit plays no part in this test, so a
#     projected reward can never rescue a directional opening or addition.
# Optional, default off: RN1_TF_FAV_ONLY (openings/additions only on the
# side the independent fair makes >= RN1_TF_FAV_MIN) and RN1_TF_FALLING_S /
# _TICKS (none on a side whose venue mid fell >= ticks over the window).
# Completions are exempt (pair-cost limits govern them, unchanged); nothing
# here adds, enlarges or reprices a bid. Any error refuses the bid.
# Tape evidence (THEORETICAL, 21 Nov two-venue recording, pre-reward):
# analysis/l5/sports/directional/TRADING_FIRST_*_20261128.txt.
_TF_UNSET = object()
# PMUS_RN1_TF_BYPASS_NET=1 (with RN1_TRADING_FIRST; default 0 = unchanged):
# the reward-aware net estimate (RN1_NET_RANK block, RN1_MARGINAL_EV per-bid
# drop) no longer decides openings and additions -- the pre-reward edge gate
# does. Without it a market with no reward programme can never open, and in
# a rewarded market the reward would still be what admits a bid. Every cap,
# pause, guard, pair cap and loss stop is unchanged.
RN1_TF_BYPASS_NET = os.getenv("PMUS_RN1_TF_BYPASS_NET", "0").strip() == "1"


def tf_net_bypass() -> bool:
    return bool(RN1_TRADING_FIRST and RN1_TF_BYPASS_NET)
# EXPANDED MARKETS (28 Nov, PR #253 integration; all default off):
#   PMUS_SHARP_LINES=1   price exact full-game spreads / game totals of each
#                        sharp candidate's event (needs PMUS_SHARP_MARKETS to
#                        include spreads,totals) into the shadow state
#   PMUS_RN1_LINE_MARKETS=1  priced line markets join the selection slate
SHARP_LINES = os.getenv("PMUS_SHARP_LINES", "0").strip() == "1"
SHARP_LINE_REFRESH_S = float(os.getenv("PMUS_SHARP_LINE_REFRESH_S", "600"))
SHARP_LINE_GAP_S = float(os.getenv("PMUS_SHARP_LINE_GAP_S", "0.5"))
RN1_LINE_MARKETS = os.getenv("PMUS_RN1_LINE_MARKETS", "0").strip() == "1"
# SOCCER THREE-WAY (28 Nov; every flag default off = unchanged):
#   PMUS_SHARP_SOCCER=1  a background thread prices pre-game soccer
#                        full-time-winner outcomes (atc-...-<home|draw|away>)
#                        from The Odds API 3-way h2h (pmus_sports_soccer_fair,
#                        SOCCER_KEYS, the same OddsClient cache and quota)
#                        into the sharp shadow state as kind "soccer3". The
#                        trading loop only copies the thread's results; it
#                        makes no request for this.
#   PMUS_RN1_SOCCER=1 with PMUS_RN1_SOCCER_LEAGUES (strict allowlist, empty
#                        = none): for those leagues only, the three outcome
#                        markets of one match are ONE risk -- the independent
#                        fair is the settled international band whose three
#                        outcome mids sum to 1 within PMUS_RN1_SOCCER_SUM_TOL
#                        (else all three refused), or pre-game the sportsbook
#                        3-way fair; the existing per-market spend / unpaired
#                        caps also bind the MATCH (exact worst case over home,
#                        draw, away incl. resting bids); the loss stop and the
#                        resting worst case use that joint arithmetic; the
#                        match's other outcome markets may be held together;
#                        and a trading-first hedge may buy another outcome
#                        at <= fair + RN1_TF_HEDGE_GIVE_USD when it lowers the
#                        match worst case. Without the flag, or outside the
#                        allowlist, soccer behaves exactly as before.
SHARP_SOCCER = os.getenv("PMUS_SHARP_SOCCER", "0").strip() == "1"
SHARP_SOCCER_REFRESH_S = float(os.getenv("PMUS_SHARP_SOCCER_REFRESH_S",
                                         "600"))
RN1_SOCCER = os.getenv("PMUS_RN1_SOCCER", "0").strip() == "1"
RN1_SOCCER_LEAGUES = tuple(x.strip().lower() for x in os.getenv(
    "PMUS_RN1_SOCCER_LEAGUES", "").split(",") if x.strip())
RN1_SOCCER_SUM_TOL = D(os.getenv("PMUS_RN1_SOCCER_SUM_TOL", "0.04"))
RN1_TRADING_FIRST = os.getenv("PMUS_RN1_TRADING_FIRST", "0").strip() == "1"
RN1_TF_EDGE_USD = D(os.getenv("PMUS_RN1_TF_EDGE_USD", "0.02"))
RN1_TF_FAV_ONLY = os.getenv("PMUS_RN1_TF_FAV_ONLY", "0").strip() == "1"
RN1_TF_FAV_MIN = D(os.getenv("PMUS_RN1_TF_FAV_MIN", "0.5"))
RN1_TF_FALLING_S = float(os.getenv("PMUS_RN1_TF_FALLING_S", "0"))
RN1_TF_FALLING_TICKS = int(os.getenv("PMUS_RN1_TF_FALLING_TICKS", "2"))
# Hybrid refinements (all inside RN1_TRADING_FIRST; 0 = off):
#   DOG_EXTRA_USD  favourite bias: a bid on the side the independent fair
#                  makes < 0.5 needs this much MORE edge (value underdogs
#                  remain admissible; underdogs without value are not)
#   CONC_USD       replenishment: required edge rises by this much per
#                  contract that would be left unpaired on the side after
#                  the bid fills (marginal EV falls as inventory concentrates)
#   WEAKEN_USD/_S  no opening/addition on a side whose INDEPENDENT fair fell
#                  by >= WEAKEN_USD over WEAKEN_S (weakening favourite)
# Underdog hedging is the existing completion path (pair cap unchanged): a
# completion locks the pair whatever the fair, so it is never refused here.
# Selection under RN1_TRADING_FIRST: "off" (default), "first" (markets with
# an independent fair source rank ahead, order otherwise kept) or "only"
# (markets without one take no slot; an error then selects nothing new).
RN1_TF_SELECT = os.getenv("PMUS_RN1_TF_SELECT", "off").strip().lower()
# ECONOMICAL HEDGE under RN1_TRADING_FIRST (unset = off): a completion bid is
# priced no higher than the independent fair for its side + HEDGE_GIVE_USD
# (and never above the pair-cap ceiling, which still applies). Completing
# above fair converts a favourable residual into a locked pair at an EV cost;
# below it the hedge is EV-neutral or better. Without an independent fair the
# completion is unchanged (the pair cap alone governs it).
_hg = os.getenv("PMUS_RN1_TF_HEDGE_GIVE_USD", "").strip()
RN1_TF_HEDGE_GIVE_USD: Optional[D] = D(_hg) if _hg else None
RN1_TF_DOG_EXTRA_USD = D(os.getenv("PMUS_RN1_TF_DOG_EXTRA_USD", "0"))
RN1_TF_CONC_USD = D(os.getenv("PMUS_RN1_TF_CONC_USD", "0"))
RN1_TF_WEAKEN_USD = D(os.getenv("PMUS_RN1_TF_WEAKEN_USD", "0"))
RN1_TF_WEAKEN_S = float(os.getenv("PMUS_RN1_TF_WEAKEN_S", "300"))


# PREGAME FILL RATE (27 Nov, default 1.0 = unchanged). The fill rates above
# were measured on live play. Lifetime heartbeat phases (27 Nov 19:20Z):
# live 85 fills over 28.2 resting contract-hours (3.0 / contract-hour),
# pregame 26 fills over 127.7 (0.20 / contract-hour), 15x lower. With
# PMUS_RN1_FILL_RATE_PREGAME_MULT < 1 a market the engine has seen as NOT
# live (pregame) uses rate x mult; a market whose phase is unknown keeps the
# live rate (conservative). The fill LOSS per contract is unchanged.
# Per competition, like the other fill settings: "0.25,setkameua:0.35,
# atp:0.1" (unlisted competitions use the first value). Measured 22-27 Nov
# (ORDER/FILL logs): 25 of 26 pregame fills were setkameua (8.6% of ordered
# quantity, as often per order as ATP live); atp/atpdb/utr/wta pregame
# filled 0.34 of ~250 contracts. Pregame 300 s markouts: setka openings
# -1.35c (n 13), additions +0.17c (n 9), vs live openings -2.5c (n 29).
RN1_FILL_RATE_PREGAME_MULT_T = {k: max(0.0, min(1.0, v)) for k, v in
                                _comp_rates("PMUS_RN1_FILL_RATE_PREGAME_MULT",
                                            "1").items()}
RN1_FILL_RATE_PREGAME_MULT = RN1_FILL_RATE_PREGAME_MULT_T[""]
#: slug -> "live" / "pregame", recorded by Engine.event_live
RN1_PHASE: Dict[str, str] = {}


def rn1_fill_param(table: Mapping[int, Mapping[str, float]], depth: int,
                   slug: str) -> float:
    t = table[min(1, max(0, int(depth)))]
    comp = slug.split("-")[1].lower() if slug.count("-") >= 1 else ""
    v = float(t.get(comp, t[""]))
    if table is RN1_FILL_RATE and RN1_PHASE.get(slug) == "pregame":
        tab = dict(RN1_FILL_RATE_PREGAME_MULT_T)
        tab[""] = RN1_FILL_RATE_PREGAME_MULT
        m = float(tab.get(comp, tab[""]))
        if m < 1.0:
            v *= m
    return v


# REWARD PAYOUT RULE (27 Nov, default "projection" = unchanged). The venue
# says "Rewards under $1.00 are not paid out" without a scope. Our own
# reward rows contradict a per-market or per-competition-day $1 test: 34 of
# 39 (competition, day) groups were PAID with totals under $1, rows of $0.01
# are paid, and no group mixed PAID and SKIPPED (data/pmus/..., analysis/l5/
# rewards/REWARD_PAYOUT_THRESHOLD_20261122.md). "projection" credits nothing
# until the projected total reaches $1, so at our clip size every reward was
# credited as $0. "paid_fraction" instead credits each theoretical reward
# (after calibration) times the share of the venue's resolved reward value
# that was actually PAID: PAID / (PAID + SKIPPED) from the latest
# REWARD_CHECK (pending rows excluded; at least RN1_PAID_FRACTION_MIN_USD
# resolved, at most RN1_PAID_FRACTION_MAX_AGE_S old). Unknown or stale ->
# no reward is credited at all (fail closed). Rewards are still never
# realized P&L, and never rescue a negative trading edge beyond their value.
RN1_REWARD_MIN_RULE = os.getenv("PMUS_RN1_REWARD_MIN_RULE",
                                "projection").strip().lower()
RN1_PAID_FRACTION_MIN_USD = float(os.getenv(
    "PMUS_RN1_PAID_FRACTION_MIN_USD") or "5")
RN1_PAID_FRACTION_MAX_AGE_S = float(os.getenv(
    "PMUS_RN1_PAID_FRACTION_MAX_AGE_S") or "14400")
_PAID_FRACTION: Dict[str, Any] = {"v": None, "t": 0.0}


def rn1_set_paid_fraction(by_status: Any, now: Optional[float] = None
                          ) -> Optional[float]:
    """Record PAID / (PAID + SKIPPED) from a REWARD_CHECK's by_status."""
    v = None
    try:
        paid = float(((by_status or {}).get("PAID") or {}).get("usd") or 0)
        skip = float(((by_status or {}).get("SKIPPED") or {}).get("usd")
                     or 0)
        if paid >= 0 and skip >= 0 and paid + skip >= \
                RN1_PAID_FRACTION_MIN_USD:
            v = max(0.0, min(1.0, paid / (paid + skip)))
    except Exception:                                     # noqa: BLE001
        v = None
    _PAID_FRACTION.update(v=v, t=time.time() if now is None else now)
    return v


def rn1_paid_fraction(now: Optional[float] = None) -> Optional[float]:
    """The fresh paid fraction, or None (unknown, stale or unreadable)."""
    now = time.time() if now is None else now
    v = _PAID_FRACTION.get("v")
    if v is None or now - float(_PAID_FRACTION.get("t") or 0) > \
            RN1_PAID_FRACTION_MAX_AGE_S:
        return None
    return float(v)


def rn1_paid_rule() -> bool:
    """The paid fraction is applied as a calibration multiplier (both the
    "paid_fraction" and the "threshold" rule)."""
    return RN1_REWARD_MIN_RULE in ("paid_fraction", "threshold")


# THRESHOLD RULE (28 Nov, issue #251; PMUS_RN1_REWARD_MIN_RULE=threshold).
# The venue documents "Rewards under $1.00 are not paid out" per programme
# and time period. Our sub-$1 PAID rows cannot be reconciled to programme ids
# (earnings rows carry none; the venue no longer serves programme records
# for settled markets; our logs from 15-18 Nov are no longer retained), so
# the minimum is NOT treated as waived. Expected payable reward of a bid =
# its own calibrated incremental reward x the venue paid fraction, credited
# only when our projected total for that PROGRAMME AND PERIOD (accrual plus
# every active market paying from it plus the candidate, over min(3 h, time
# left)) reaches $1; otherwise $0. Score share stays per contract
# (contract_period pool scope); only the threshold aggregates per programme.
# Unknown paid fraction or programme: no reward credited (fail closed).
def rn1_threshold_rule() -> bool:
    return RN1_REWARD_MIN_RULE == "threshold"


def rn1_comp_value(table: Mapping[str, float], slug: str) -> float:
    """A per-competition setting (see _comp_rates) for this market."""
    comp = slug.split("-")[1].lower() if slug.count("-") >= 1 else ""
    return float(table.get(comp, table[""]))


_CAL_PARSED: Dict[str, Any] = {}


def rn1_reward_cal_table(raw: Optional[str] = None) -> Dict[str, float]:
    """PMUS_RN1_REWARD_CALIBRATION_TABLE parsed: {"program=<id>": m,
    "<competition>": m, "*": m}; malformed entries are ignored."""
    raw = RN1_REWARD_CAL_TABLE if raw is None else raw
    hit = _CAL_PARSED.get(raw)
    if hit is not None:
        return hit
    out: Dict[str, float] = {}
    for part in (raw or "").split(","):
        k, _, v = part.strip().rpartition(":")
        if not k:
            continue
        try:
            f = float(v)
        except ValueError:
            continue
        if f == f:                                   # not NaN
            out[k.strip().lower()] = f
    _CAL_PARSED[raw] = out
    return out


def rn1_reward_mult(slug: str, per: Optional[Mapping[str, Any]] = None
                    ) -> float:
    """The multiplier applied to a THEORETICAL reward credited for this
    market (see RN1_REWARD_CALIBRATION). Flag off: the clamped
    RANK_REWARD_HAIRCUT, exactly what the haircut sites applied."""
    hc = max(0.0, min(1.0, RANK_REWARD_HAIRCUT))
    if not RN1_REWARD_CALIBRATION:
        if rn1_paid_rule():
            return hc * (rn1_paid_fraction() or 0.0)
        return hc
    tab = rn1_reward_cal_table()
    comp = slug.split("-")[1].lower() if slug.count("-") >= 1 else ""
    prog = str((per or {}).get("programId") or "").lower()
    m = None
    for k in (("program=" + prog) if prog else None, comp, "*"):
        if k and k in tab:
            m = tab[k]
            break
    m = hc if m is None else max(0.0, min(1.0, float(m)))
    div = 1.0
    if RN1_REWARD_POOL_SCOPE == "event":
        div = float((per or {}).get("M_event") or 1)
    elif RN1_REWARD_POOL_SCOPE == "program":
        div = float((per or {}).get("M_program") or 1)
    m = m / max(1.0, div)
    if rn1_paid_rule():
        m *= rn1_paid_fraction() or 0.0            # unknown: nothing credited
    return m


def rn1_pool_key(per: Optional[Mapping[str, Any]],
                 slug: Optional[str] = None) -> Optional[str]:
    """The reward pool a paying period belongs to. With `slug` (scope
    "contract_period"): "slug|programId|period|start", one contract's time
    period. Without: "programId|period". None without the needed parts."""
    if not per:
        return None
    pid, prd = per.get("programId"), per.get("period")
    if not pid or not prd:
        return None
    if slug is not None:
        return "%s|%s|%s|%s" % (slug, pid, prd, per.get("start") or "")
    return "%s|%s" % (pid, prd)


def rn1_scope_pool_key(per: Optional[Mapping[str, Any]],
                       slug: Optional[str]) -> Optional[str]:
    """The pool key for the configured scope."""
    if RN1_REWARD_QUAL_SCOPE == "contract_period":
        return rn1_pool_key(per, slug) if slug else None
    return rn1_pool_key(per)


def rn1_reward_cal(slug: str, per: Optional[Mapping[str, Any]] = None
                   ) -> float:
    """At a site that credited the raw theoretical reward before: 1.0 with
    the calibration flag off (unchanged), rn1_reward_mult with it on."""
    return rn1_reward_mult(slug, per) if RN1_REWARD_CALIBRATION else 1.0


# JOIN THE BEST BID WHEN IT IS JUSTIFIED (owner 2026-11-25: "Competitive
# maker quoting that can join the best bid when economically justified").
# PMUS_RN1_DEPTH_TICKS=1 rests every bid one tick behind the touch. The
# venue's reward rule scores only the levels needed to reach the target
# size, so behind a touch that already meets the target the bid scores
# nothing, and it fills only after the touch is traded through.
# With RN1_JOIN_TOUCH on, a market's bids rest AT the touch (still
# post-only, never crossing) while ALL of these hold, and one tick back
# otherwise:
#   - the spread is at least RN1_JOIN_MIN_SPREAD_TICKS (at one tick a
#     joined bid is within a tick of crossing: never joined);
#   - the mid moved no more than RN1_JOIN_MAX_RANGE_USD in the last
#     VOL_WINDOW_S;
#   - no price jump in the last RN1_JOIN_CALM_S.
# Pair cost, every cap, the thin-touch rule and the final gate apply to
# the joined price exactly as to any other. 0 = off (as before).
RN1_JOIN_TOUCH = os.getenv("PMUS_RN1_JOIN_TOUCH", "0").strip() == "1"
RN1_JOIN_MIN_SPREAD_TICKS = int(os.getenv(
    "PMUS_RN1_JOIN_MIN_SPREAD_TICKS", "2"))
RN1_JOIN_MAX_RANGE_USD = float(os.getenv(
    "PMUS_RN1_JOIN_MAX_RANGE_USD", "0.01"))
RN1_JOIN_CALM_S = float(os.getenv("PMUS_RN1_JOIN_CALM_S", "60"))
# PER-COMPETITION VOLATILITY THRESHOLD (owner 2026-11-27). The hard
# "price_moving" refusal above uses one RN1_JOIN_MAX_RANGE_USD for every
# competition. PMUS_RN1_JOIN_MAX_RANGE_COMP ("atp:0.01,utr:0.02") sets it per
# competition (the 30 s mid range above which the touch is never joined);
# a competition not listed keeps RN1_JOIN_MAX_RANGE_USD. Unset = exactly as
# before. jump_pause, recent_jump, reactive_pull, vol_pause and every loss
# stop and cap are unchanged hard protections.
RN1_JOIN_MAX_RANGE = _comp_rates("PMUS_RN1_JOIN_MAX_RANGE_COMP",
                                 str(RN1_JOIN_MAX_RANGE_USD))
# ECONOMICALLY JUSTIFIED PLACEMENT (owner 2026-11-27, default OFF). With
# RN1_TOUCH_EV on, the placement of each opening/extension bid (the touch,
# one tick behind, ... RN1_TOUCH_EV_MAX_DEPTH ticks behind) is the one with
# the best reward-adjusted marginal value per resting hour:
#   EV(d) = calibrated incremental reward at that placement (the venue's
#           ladder rule, pmus_sports_rewards.marginal_ladder_reward, times
#           rn1_reward_mult)
#         - fills/h(d) x qty x (fill loss(d) + maker fee + concentration
#           charge), fill rate and loss from RN1_FILL_RATE / RN1_FILL_LOSS
#           for that placement and competition.
# Only a placement with EV > 0 is chosen (ties: the deeper one); with none,
# the bid stays at max(1, RN1.DEPTH_TICKS) behind, as without the flag.
# The touch is never chosen while a hard protection holds (spread under the
# join minimum, the competition's volatility threshold, a recent jump), when
# the bid would cross or lock the book, when the side's unpaired cost would
# exceed the residual cap, or when the pair of both bids would exceed the
# pair cap. Market selection scores every candidate with the SAME function
# (rn1_touch_ev) on the same inputs, so a market is selected on the
# placement it will be quoted at. Completion bids keep today's placement.
RN1_TOUCH_EV = os.getenv("PMUS_RN1_TOUCH_EV", "0").strip() == "1"
# SIDES REPLENISH INDEPENDENTLY (owner 2026-11-27, default OFF). When a
# pass cancels a stale bid and the venue has not yet confirmed that cancel
# (no fill raced it: inventory unchanged), the pass used to stop there, so
# the OTHER side's new or replenishing bid waited a pass for an unrelated
# cancel. With RN1_SIDE_INDEPENDENT on only the side whose cancel is
# pending waits; the other side's targets are placed this pass (each still
# through the final gate, which counts the still-open order). A fill that
# raced a cancel still stops the whole pass (inventory changed).
RN1_SIDE_INDEPENDENT = os.getenv("PMUS_RN1_SIDE_INDEPENDENT",
                                 "0").strip() == "1"
# EV-AWARE HOLD THROUGH A BRIEF DIP (owner 2026-11-27, default OFF; needs
# PMUS_RN1_MARGINAL_EV=1). A RESTING opening/extension bid whose marginal EV
# turns negative is kept, instead of cancelled, only while
#   EV/h x RN1_EV_HOLD_MAX_S/3600 + RN1_REPLACE_COST_USD >= 0
# (its expected value over the bounded hold window plus the saved cost of
# cancelling and re-placing it: two signed requests and the queue position)
# and for at most RN1_EV_HOLD_MAX_S since it first failed, and only while
# its reward is still credited this pass (a lost, unread or unqualified
# reward withdraws at once). A NEW bid with
# marginal EV <= 0 is never posted. With this flag on, RN1_NET_HOLD_S no
# longer keeps openings regardless of their EV. Hard blocks still pull.
RN1_EV_HOLD = os.getenv("PMUS_RN1_EV_HOLD", "0").strip() == "1"
RN1_EV_HOLD_MAX_S = float(os.getenv("PMUS_RN1_EV_HOLD_MAX_S") or "60")
RN1_REPLACE_COST_USD = float(os.getenv("PMUS_RN1_REPLACE_COST_USD")
                             or "0.002")
# REPOST HYSTERESIS (owner 2026-11-27, default 0 = off; Stage 1 audit: a
# market whose net estimate oscillated around 0 placed 5 bids and cancelled
# them about 5 minutes later, four times). With RN1_NET_REPOST_MIN_S > 0
# (net ranking on), once a market has been `net_not_positive`, its
# openings/extensions are re-posted only after the market has stayed
# unblocked (positive) for RN1_NET_REPOST_MIN_S; meanwhile it stays
# `net_not_positive` (completions continue; RN1_NET_HOLD_S keeps whatever
# it already keeps).
RN1_NET_REPOST_MIN_S = float(os.getenv("PMUS_RN1_NET_REPOST_MIN_S")
                             or "0")
RN1_TOUCH_EV_MAX_DEPTH = max(1, int(os.getenv("PMUS_RN1_TOUCH_EV_MAX_DEPTH")
                                    or "2"))
# REWARD CALIBRATION (owner 2026-11-27, default OFF). Every theoretical
# reward the bot credits (ranking, marginal EV, standing start, the day's
# projection, the touch EV) is multiplied by rn1_reward_mult(slug):
#   flag off: RANK_REWARD_HAIRCUT where a haircut applied before, nothing
#             where none did (exactly as before);
#   flag on:  PMUS_RN1_REWARD_CALIBRATION_TABLE, e.g.
#             "program=atp_wta_moneyline_live_20261018:0.4,atpdb:0.45,*:0.4"
#             (programme id first, then competition, then "*", else
#             RANK_REWARD_HAIRCUT), clamped to [0, 1], divided by the pool
#             scope divisor PMUS_RN1_REWARD_POOL_SCOPE: "market" (default,
#             1: the evidence says each market has its own pool), "event"
#             (M_event) or "program" (M_program: shared across the
#             programme's markets on the slate).
# A read-only routine (src/pmus_reward_calibration.py) logs
# REWARD_CALIBRATION after each reward check (at most every
# RN1_REWARD_CAL_LOG_S): venue rows (PAID preferred, PENDING flagged) over
# the bot's own logged theoretical accrual per market and day, by
# competition. It is never applied automatically; the owner sets the table.
RN1_REWARD_CALIBRATION = os.getenv("PMUS_RN1_REWARD_CALIBRATION",
                                   "0").strip() == "1"
RN1_REWARD_CAL_TABLE = os.getenv("PMUS_RN1_REWARD_CALIBRATION_TABLE", "")
RN1_REWARD_POOL_SCOPE = (os.getenv("PMUS_RN1_REWARD_POOL_SCOPE") or
                         "market").strip().lower()
RN1_REWARD_CAL_LOG_S = float(os.getenv("PMUS_RN1_REWARD_CAL_LOG_S")
                             or "3600")
# venue reward rows are dated by the venue's day (US Eastern, 25 Nov rows =
# 05:00-05:00 UTC): the theoretical accrual is keyed the same way
RN1_REWARD_CAL_DAY_OFFSET_H = float(os.getenv(
    "PMUS_RN1_REWARD_CAL_DAY_OFFSET_H") or "-5")
# DURABLE HALT ON A RECONCILIATION FAILURE, with no session involved
# (owner 2026-11-23: "reconciliation failures must trigger a durable halt";
# LIVE 2026-11-22 22:10Z a fill was recorded 89 s after the venue made it,
# the position check then agreed again, nothing latched, and the manual
# relock arrived 22 minutes late). Both use the existing safety latch: every
# own order is cancelled, nothing is placed, it survives a restart and only
# the owner's acknowledgement clears it.
#   FILL_LATE_LATCH_S   a fill recorded more than this long after the
#                       venue's own stamp on the order latches
#                       ("fill_seen_late"). 0 = measure and log only.
#   AUDIT_LATCH         the audit's two reconciliations (venue positions
#                       against the book; venue trade rows against recorded
#                       fills) latch when the SAME one fails
#                       AUDIT_LATCH_STRIKES audits in a row. The first
#                       failure blocks new exposure at once and brings the
#                       next audit forward to AUDIT_RECHECK_S, so a fill
#                       that landed between the two reads is not a halt.
FILL_LATE_LATCH_S = float(os.getenv("PMUS_TWO_SIDED_FILL_LATE_LATCH_S", "0"))
AUDIT_LATCH = os.getenv("PMUS_TWO_SIDED_AUDIT_LATCH", "0").strip() == "1"
AUDIT_LATCH_STRIKES = max(1, int(os.getenv(
    "PMUS_TWO_SIDED_AUDIT_LATCH_STRIKES", "2")))
AUDIT_RECHECK_S = float(os.getenv("PMUS_TWO_SIDED_AUDIT_RECHECK_S", "30"))
# LIVE 2026-11-23: both hourly audits of a campaign were refused by the
# request budget and nothing retried, so the bot quoted for 1 h 45 min with
# no account audit.
#   AUDIT_RETRY_S    an audit (or reward read) that did not complete is
#                    tried again after this long, not after another hour.
#   AUDIT_MAX_AGE_S  with no COMPLETED audit (venue walk and position
#                    reconciliation both read) within this long -- none yet
#                    since boot counts -- new exposure is blocked
#                    ("audit_stale"). Cancels and completions are not.
#                    0 = off (today's behaviour).
AUDIT_RETRY_S = float(os.getenv("PMUS_TWO_SIDED_AUDIT_RETRY_S", "60"))
AUDIT_MAX_AGE_S = float(os.getenv("PMUS_TWO_SIDED_AUDIT_MAX_AGE_S", "0"))
RANK_HORIZON_H = float(os.getenv("PMUS_RN1_RANK_HORIZON_H", "1"))
RANK_REWARD_HAIRCUT = float(os.getenv("PMUS_RN1_RANK_REWARD_HAIRCUT", "0.5"))
RANK_TTL_S = float(os.getenv("PMUS_RN1_RANK_TTL_S", "300"))
RANK_NEW_PER_ROUND = int(os.getenv("PMUS_RN1_RANK_NEW_PER_ROUND", "3"))
# A candidate can only be priced once the international feed follows it, so
# the first RANK_WATCH_MAX candidates of the cheap screen are followed
# alongside the active markets. A "no fair value" answer is retried after
# RANK_NO_FAIR_TTL_S. With the marginal test on, a candidate that has no
# ranking number yet is not selected: it could not bid anyway.
RANK_WATCH_MAX = int(os.getenv("PMUS_RN1_RANK_WATCH_MAX", "8"))
RANK_NO_FAIR_TTL_S = float(os.getenv("PMUS_RN1_RANK_NO_FAIR_TTL_S", "30"))


def rn1_pregame_qty() -> D:
    return D(str(RN1.QTY)) if RN1_PREGAME_QTY is None else RN1_PREGAME_QTY


def rn1_live_qty() -> D:
    return D(str(RN1.QTY)) if RN1_LIVE_QTY is None else RN1_LIVE_QTY


def rn1_max_clip() -> D:
    """Largest order size any phase may send (the clip the latch checks)."""
    return max(D(str(RN1.QTY)), rn1_pregame_qty(), rn1_live_qty())


def parse_start(v: Any) -> Optional[float]:
    """Epoch seconds of an ISO time, or None."""
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")
                                      ).timestamp()
    except Exception:                                     # noqa: BLE001
        return None


def pregame_targets(events: Iterable[Mapping[str, Any]], now: float
                    ) -> list:
    """Sports markets whose game has not started and starts within the lead
    window. Each target carries the venue's gameStartTime (epoch)."""
    out, seen = [], set()
    for ev in events or []:
        if not isinstance(ev, Mapping) or ev.get("live") is True:
            continue
        for m in ev.get("markets") or []:
            if not isinstance(m, Mapping):
                continue
            slug = str(m.get("slug") or "")
            if not slug.startswith(SPORTS_PREFIXES) or slug in seen:
                continue
            if learn_cricket(slug, m.get("sportsMarketType"),
                             (ev.get("eventState") or {}).get("type")
                             if isinstance(ev.get("eventState"), Mapping)
                             else None):
                continue
            g = parse_start(m.get("gameStartTime") or ev.get("startTime"))
            if g is None:
                continue
            lead = g - now
            if not (PREGAME_MIN_LEAD_S <= lead <= PREGAME_MAX_LEAD_S):
                continue
            seen.add(slug)
            out.append({"slug": slug, "game_start": g,
                        "market_meta": {k: m.get(k) for k in
                                        ("bestBidQuote", "bestAskQuote")
                                        if m.get(k) is not None}})
    return out
# OWNER 2026-11-15: "never override watchdog or reconciliation halts". The
# shared latch (l5_halt_gate, the record the watchdog writes and BTC5m
# reads) is read every SHARED_HALT_TTL_S when L5_HALT_URL is set:
#   halt asserted -> NO order of any kind is placed (cancels stay allowed)
#   unreadable    -> no NEW first legs; risk-reducing recovery continues
SHARED_HALT_URL_ENV = "L5_HALT_URL"
# every closed market is looked up (paced); the old cap of 25 per audit left
# the settled ledger incomplete for hours
SETTLE_LOOKUPS_PER_AUDIT = int(os.getenv(
    "PMUS_TWO_SIDED_SETTLE_LOOKUPS", "400"))
SETTLE_LOOKUP_GAP_S = float(os.getenv("PMUS_TWO_SIDED_SETTLE_GAP_S", "0.2"))
# LIVE 2026-11-23: the campaign spend ceiling counts a market until its
# settlement price is recorded, and that was only looked up inside the
# hourly audit -- which never ran, so nothing was ever released. With
# SETTLE_CHECK_S > 0 the loop itself looks up closed markets that still
# hold contracts (the venue's public final price, a keyless read that is
# not on the signed budget), at most SETTLE_CHECK_MAX per pass. Capital is
# released only by a FINAL settlement price, never because a position is
# balanced. 0 = off (today's behaviour).
SETTLE_CHECK_S = float(os.getenv("PMUS_TWO_SIDED_SETTLE_CHECK_S", "0"))
SETTLE_CHECK_MAX = max(1, int(os.getenv(
    "PMUS_TWO_SIDED_SETTLE_CHECK_MAX", "2")))
# OWNER 2026-11-15 "Prevent losing first legs": open a two-sided quote only
# when its expected NET value from this bot's own SETTLED results is
# positive (src/pmus_sports_open_gate.py). DEFAULT OFF.
OPEN_EV_GATE = os.getenv("PMUS_TWO_SIDED_OPEN_EV_GATE", "0").strip() == "1"
# OWNER 2026-11-15 "work every market until it's profitable": bounded
# same-side EXTEND while a market is stranded (needs the recovery controller)
EXTEND_ON = os.getenv("PMUS_TWO_SIDED_EXTEND", "0").strip() == "1"
# OWNER 2026-11-17 cheap-leg-first: two_sided (default, unchanged) |
# cheap_first | expensive_first (comparison arm) | auto (gated by the bot's
# own episode evidence). See src/pmus_sports_open_policy.py.
OPEN_POLICY = os.getenv("PMUS_TWO_SIDED_OPEN_POLICY",
                        "two_sided").strip().lower()
CHEAP_FIRST_BUFFER = D(os.getenv("PMUS_TWO_SIDED_CHEAP_FIRST_BUFFER", "0.02"))
EXTEND_QTY = D(os.getenv("PMUS_TWO_SIDED_EXTEND_QTY", "1"))
EXTEND_MAX_IMBALANCE = D(os.getenv("PMUS_TWO_SIDED_EXTEND_MAX_IMBALANCE",
                                   "2"))
EXTEND_STEP_PX = D(os.getenv("PMUS_TWO_SIDED_EXTEND_STEP_PX", "0.03"))
EXTEND_MAX_USD = D(os.getenv("PMUS_TWO_SIDED_EXTEND_MAX_USD", "1.50"))
OPEN_EV_MIN = _env_d("PMUS_TWO_SIDED_OPEN_EV_MIN", "0")
OPEN_EV_MIN_MARKETS = _env_d("PMUS_TWO_SIDED_OPEN_EV_MIN_MARKETS", "10")
# OWNER 2026-11-19 "Get me liquidity rewards": a bounded live test lets ONLY
# these sports past the open-EV gate (comma list of slug sport codes, e.g.
# "dota2,utr"). Every other sport stays gated exactly as before. Empty
# (default) = no exception. The gate's own statistics are unchanged.
# OWNER 2026-11-23: "The campaign must not silently expand beyond its
# authorization." OPEN_GATE_ALLOW_SPORTS is not a whitelist: it lets the
# listed competitions past the open-EV gate, and an UNLISTED competition
# still opens whenever its own settled history shows positive expected
# value. With STRICT_SPORTS=1 the list is exactly the scope: no opening or
# extension in any other competition, and no slot taken by one. Completions
# and cancels are never blocked. An empty list with STRICT on permits
# nothing. Default 0 = today's behaviour.
STRICT_SPORTS = os.getenv("PMUS_TWO_SIDED_STRICT_SPORTS",
                          "0").strip() == "1"
OPEN_GATE_ALLOW_SPORTS = frozenset(
    x.strip().lower() for x in os.getenv(
        "PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS", "").split(",") if x.strip())
SHARED_HALT_TTL_S = float(os.getenv("PMUS_TWO_SIDED_SHARED_HALT_TTL_S", "15"))
# How old the watchdog's "no halt" attestation may be before this bot stops
# trusting it. The watchdog writes one every sweep (20 s); the default of
# 900 s meant a dead watchdog, or one withholding its attestation because
# the operator's halt channel is down, left this bot opening exposure for
# up to 15 more minutes. A stale attestation blocks NEW exposure only
# (it reads as "unavailable", not as a halt); completions and cancels go
# on. Default 900 = today.
SHARED_HALT_MAX_AGE_S = float(os.getenv(
    "PMUS_TWO_SIDED_SHARED_HALT_MAX_AGE_S", "900"))
# OWNER 2026-11-15 "We're bleeding money" -> restart: reward quotes no
# longer rest AT the touch, where they were picked off (10-lot CS2 YES@0.60
# -> 0.08). They rest REWARD_MIN_DEPTH_PX (2c) under it like every other
# opening, and the adverse-move pull applies to them too. 0 = old behavior.
REWARD_MIN_DEPTH_PX = _env_d("PMUS_TWO_SIDED_REWARD_MIN_DEPTH_PX", "0.02")


def reward_min_depth(tick) -> int:
    t = D(str(tick or "0.01"))
    if REWARD_MIN_DEPTH_PX <= 0 or t <= 0:
        return 0
    return int((REWARD_MIN_DEPTH_PX / t).to_integral_value(
        rounding=ROUND_CEILING))


def reward_first_on() -> bool:
    return os.getenv(REWARD_FIRST_ENV, "").strip().lower() in (
        "1", "yes", "true", "on")


def _et_offset_h(utc: datetime) -> int:
    """US Eastern UTC offset: EDT (-4) from the 2nd Sunday of March 07:00Z to
    the 1st Sunday of November 06:00Z, else EST (-5). Stdlib only."""
    y = utc.year
    m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    start = datetime(y, 3, 1 + (6 - m1.weekday()) % 7 + 7, 7,
                     tzinfo=timezone.utc)
    n1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    end = datetime(y, 11, 1 + (6 - n1.weekday()) % 7, 6, tzinfo=timezone.utc)
    return -4 if start <= utc < end else -5


def et_clock(ts: float) -> tuple:
    """(ET date 'YYYY-MM-DD', ET hour, seconds left to ET midnight)."""
    from datetime import timedelta
    utc = datetime.fromtimestamp(ts, timezone.utc)
    loc = utc + timedelta(hours=_et_offset_h(utc))
    secs = loc.hour * 3600 + loc.minute * 60 + loc.second + \
        loc.microsecond / 1e6
    return loc.strftime("%Y-%m-%d"), loc.hour, 86400.0 - secs


# OWNER 2026-11-14 realtime reaction ("stop being picked off"). DEFAULT OFF:
# with PMUS_TWO_SIDED_WS unset no socket is opened, no key is written and
# every code path below behaves exactly as before. When on, a read-only
# market-data socket (src/pmus_two_sided_ws.py) feeds Venue.bbo (fresh <= 3s,
# else the gateway poll) and every touch change wakes the main loop at once:
#   * an UNFILLED pair whose touch moved >= 1 tick since it was priced (or
#     whose opposite side moved toward it) is pulled together and re-set by
#     the normal step at the new price;
#   * a move of >= JUMP_TICKS ticks in one update pulls unfilled opening legs
#     and blocks a new pair in that market for JUMP_PAUSE_S. Completion legs
#     (one side filled) are never pulled by this; they lock the pair.
WS_ENV = "PMUS_TWO_SIDED_WS"
JUMP_TICKS = int(os.getenv("PMUS_TWO_SIDED_JUMP_TICKS", "2"))
# A jump is also measured in PRICE: on 0.005-tick books (college football)
# 2 ticks is 1c of noise, which paused CFB ~60 times in 2 min live (19:05Z).
JUMP_MIN_PX = _env_d("PMUS_TWO_SIDED_JUMP_MIN_PX", "0.02")
JUMP_PAUSE_S = float(os.getenv("PMUS_TWO_SIDED_JUMP_PAUSE_S", "15"))


def ws_on() -> bool:
    return os.getenv(WS_ENV, "").strip().lower() in ("1", "yes", "true", "on")


def ticks_moved(prev: Mapping[str, D], new: Mapping[str, D], tick: D) -> int:
    """Largest best-bid / best-ask move between two tops, in whole ticks."""
    m = D(0)
    for k in ("bid", "ask"):
        if prev.get(k) is not None and new.get(k) is not None:
            m = max(m, abs(D(new[k]) - D(prev[k])))
    return int((m / tick).to_integral_value(rounding=ROUND_DOWN))


def stale_reasons(quoted: Mapping[str, Any], new: Mapping[str, D],
                  tick: D) -> list:
    """Why an unfilled pair priced off `quoted` is stale at `new`.
    YES leg's touch = best bid, its opposite = best ask (moving DOWN is
    toward it). NO leg's touch = 1 - best ask, its opposite = 1 - best bid
    (best bid moving UP is toward it)."""
    qb, qa = D(str(quoted["bid"])), D(str(quoted["ask"]))
    out = []
    if abs(new["bid"] - qb) >= tick:
        out.append("yes_touch_moved")
        if new["bid"] > qb:
            out.append("no_opposite_toward")
    if abs(new["ask"] - qa) >= tick:
        out.append("no_touch_moved")
        if new["ask"] < qa:
            out.append("yes_opposite_toward")
    return out


def adverse_reasons(legs: Mapping[str, D], new: Mapping[str, D], tick: D,
                    depth: int) -> list:
    """OWNER 2026-11-15 'it keeps reopening the same pair -- be aware of
    what's sitting': pull a resting opening leg ONLY when the move is against
    it -- it now sits closer than `depth` ticks to its own touch (it would buy
    above the market's new level) or within one tick of crossing. A touch
    moving AWAY leaves the leg deeper and safe; it is left resting."""
    out = []
    for side, px in legs.items():
        px = D(str(px))
        if side == "YES":
            touch, opp = new["bid"], new["ask"]
        else:
            touch, opp = ONE - new["ask"], ONE - new["bid"]
        if px > touch - depth * tick:
            out.append("%s_closer_than_%dt" % (side.lower(), depth))
        if opp - px <= tick:
            out.append("%s_near_cross" % side.lower())
    return out


def rewards_on() -> bool:
    """Reward machinery on: PMUS_TWO_SIDED_REWARDS, or implied by
    PMUS_TWO_SIDED_REWARD_FIRST."""
    return os.getenv(REWARDS_ENV, "").strip().lower() in (
        "1", "yes", "true", "on") or reward_first_on()


def reward_allowed(slug: str) -> bool:
    raw = os.getenv(REWARD_ALLOW_ENV, "").strip()
    if not raw:
        return True
    return any(slug.startswith(p.strip()) for p in raw.split(",")
               if p.strip())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#: keys of a venue order row that can carry the time of its last execution
_ORDER_TIME_KEYS = ("lastTransactTime", "transactTime", "lastUpdateTime",
                    "updateTime", "updatedAt")


def order_time(o: Any) -> Optional[float]:
    """Epoch seconds of the venue's own last-update stamp on an order row,
    or None. Used only to time a fill for the markout record; a row with no
    readable stamp leaves the fill timed at the moment the bot saw it."""
    if not isinstance(o, Mapping):
        return None
    for k in _ORDER_TIME_KEYS:
        raw = o.get(k)
        if raw in (None, ""):
            continue
        try:
            txt = str(raw).strip().replace("Z", "+00:00")
            m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", txt)
            if m:
                txt = m.group(1) + (m.group(2) or "")[:7] + m.group(3)
            t = datetime.fromisoformat(txt)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t.timestamp()
        except (TypeError, ValueError):
            continue
    return None


# OWNER 2026-11-15: record depth, quote age, movement and fills going
# forward (src/pmus_sports_tape.py). Set in main(); None = not recording.
TAPE = None


def log(kind: str, **kw: Any) -> None:
    print("PMUS_TWO_SIDED " + json.dumps({"kind": kind, "t": now_iso(), **kw},
                                         default=str, separators=(",", ":")),
          flush=True)
    if TAPE is not None:
        TAPE.event(kind, kw)


# ------------------------------------------------------------- pure pricing

# OWNER 2026-11-14 late-game cutoff: 20/20 settled lone legs LOST, nearly all
# "N vs N+1" -- the last pair opened as a game was being decided strands its
# losing leg. No NEW pair once a game is late (completions/exits continue).
LATE_CUTOFF = os.getenv("PMUS_TWO_SIDED_LATE_CUTOFF", "1").strip() != "0"


# OWNER 2026-11-15 "Can we not trade cricket": substrings excluded from
# selection; an active excluded market stops opening pairs and frees its slot
# once flat (completions continue).
EXCLUDE_TAGS = tuple(t.strip() for t in os.getenv(
    "PMUS_TWO_SIDED_EXCLUDE", "cr-").split(",") if t.strip())


# OWNER 2026-11-15 "I want NO cricket trades at all": a hard rule, not a
# setting. Cricket is never selected, and any cricket market already in the
# book is abandoned (orders cancelled, nothing re-placed, completions too).
CRICKET_LEAGUES = ("ipl", "bbl", "psl", "cpl", "wpl", "sa20", "t20", "odi",
                   "t20i", "ilt20", "hundred", "cricket",
                   # FOUND 2026-11-25: live on the venue, labelled cricket by
                   # the venue, and NOT caught by the names above
                   "sheffield", "wbbl", "smat", "csafc", "pro50", "county",
                   "ranji", "bpl", "lpl", "mlc", "wbbl", "plunket",
                   "supersmash", "vitality", "marsh")
#: league codes the VENUE ITSELF labelled cricket (event state type, or a
#: market type starting "cricket"), learned as the slate is read. A name
#: list goes stale every time the venue lists a new competition; the
#: venue's own label does not. Only ever added to.
_CRICKET_BY_VENUE: set = set()


def _league_of(slug: Any) -> str:
    parts = str(slug).split("-")
    return parts[1] if len(parts) > 1 else ""


def learn_cricket(slug: Any, *labels: Any) -> bool:
    """Record this market's competition as cricket when any venue label
    says so. Returns True when it is (now) known as cricket."""
    if any("cricket" in str(x or "").lower() for x in labels):
        lg = _league_of(slug)
        if lg and lg not in _CRICKET_BY_VENUE:
            _CRICKET_BY_VENUE.add(lg)
            log("CRICKET_LEARNED", league=lg, example=str(slug),
                label="the venue labels this competition cricket: never "
                      "selected, never quoted")
        return True
    return False


def is_cricket(slug: str) -> bool:
    league = _league_of(slug)
    return league.endswith("cr") or league in CRICKET_LEAGUES or \
        league in _CRICKET_BY_VENUE or "cricket" in str(slug)


def excluded(slug: str) -> bool:
    if is_cricket(slug):
        return True
    parts = str(slug).split("-")
    league = parts[1] if len(parts) > 1 else ""
    return any(league.endswith(t.rstrip("-")) if t.endswith("-") else t in slug
               for t in EXCLUDE_TAGS)


# OWNER 2026-11-15 "cancel that Zimbabwe cricket order ... it keeps coming
# back": markets the owner abandons. Their open orders are cancelled, the
# market is closed in the book and nothing is ever placed there again; any
# position simply rides to settlement. Comma-separated slugs (or slug
# fragments) in PMUS_TWO_SIDED_ABANDON.
def abandoned(slug: str) -> bool:
    toks = [t.strip() for t in os.getenv("PMUS_TWO_SIDED_ABANDON",
                                         "").split(",") if t.strip()]
    return is_cricket(slug) or any(t in str(slug) for t in toks)


CRYPTO_PREFIXES = tuple(p.strip() for p in os.getenv(
    "PMUS_TWO_SIDED_CRYPTO_PREFIXES", "").split(",") if p.strip())
CRYPTO_CUTOFF_S = float(os.getenv("PMUS_TWO_SIDED_CRYPTO_CUTOFF_S", "7200"))


def _clock_s(v: Any) -> Optional[float]:
    try:
        mm, ss = str(v).strip().rstrip("'").split(":")[:2]
        return int(mm) * 60 + float(ss)
    except Exception:                                     # noqa: BLE001
        return None


def _minute(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip().rstrip("'").split("+")[0].split(":")[0])
    except Exception:                                     # noqa: BLE001
        return None


# ESPORTS WITH NO PUBLISHED FORMAT. Checked on the venue 2026-11-25 (live
# Valorant match, event list, event state, market record, market text):
# the venue publishes a map score ("1-1") and a period ("Map 3") and
# nowhere a best-of. So every esports match is refused, Dota 2 included.
# With this setting on, a match of unknown format may open ONLY before any
# map has been won (score 0-0, period not past the first map) -- the one
# state the best-of-3 rule also allows. Everything later stays refused.
# KNOWN GAP, the reason this is off by default and needs the owner's
# approval: a best-of-1 is decided by its first map and a best-of-2 can
# end level (the venue settles a tie at $0.50); neither can be told apart
# from the first map of a best-of-3. 0 = refuse every unknown format.
ESPORTS_UNKNOWN_FIRST_MAP = os.getenv(
    "PMUS_TWO_SIDED_ESPORTS_UNKNOWN_FIRST_MAP", "0").strip() == "1"


def _esports_first_map(state: Mapping[str, Any]) -> bool:
    """True only for score 0-0 with a period that is not a later map."""
    if str(state.get("score") or "").replace(" ", "") != "0-0":
        return False
    nums = re.findall(r"\d+", str(state.get("period") or ""))
    return all(int(n) <= 1 for n in nums)


def esports_late(state: Mapping[str, Any]) -> Optional[str]:
    """OWNER 2026-11-22 (CS2 was on the allowlist with NO late rule: it would
    have been quoted to the last round). Conservative and FAIL CLOSED: a new
    pair may open only while the match format and the map score are both
    known and no team is one map from winning. The venue's esports state
    carries a map score ("1-0") and a period ("Live", "Map 2") but, as seen
    live, no best-of -- so until a format is published this returns a block
    for every esports match, which is the intended state.

      format unknown / unreadable            -> esports_format_unknown
      map score unreadable                   -> esports_score_unknown
      a team one map from the match, or the
      deciding map (incl. every best-of-1)   -> esports_deciding_map"""
    bo = None
    for src in (state, state.get("esportsState") or {}):
        for k in ("bestOf", "best_of", "seriesLength", "numberOfMaps"):
            v = src.get(k) if isinstance(src, Mapping) else None
            if v not in (None, ""):
                bo = v
                break
        if bo is not None:
            break
    try:
        bo = int(bo)
    except (TypeError, ValueError):
        if ESPORTS_UNKNOWN_FIRST_MAP and _esports_first_map(state):
            return None
        return "esports_format_unknown"
    if bo < 1 or bo > 7 or bo % 2 == 0:
        return "esports_format_unknown"
    try:
        a, b = (int(x) for x in str(state.get("score") or "").split("-"))
    except ValueError:
        return "esports_score_unknown"
    need = bo // 2 + 1
    if a < 0 or b < 0 or max(a, b) >= need:
        return "esports_score_unknown"
    if max(a, b) >= need - 1 or a + b >= bo - 1:
        return "esports_deciding_map"
    return None


#: Leagues that are esports whatever the venue says about the match. LIVE
#: 2026-11-22: CS2 events reach the slate with NO eventState at all
#: ({"type": null}); a rule keyed only on state.type would have let those
#: open. The league code decides, the state can only add to it.
ESPORTS_LEAGUES = frozenset(
    x.strip().lower() for x in os.getenv(
        "PMUS_TWO_SIDED_ESPORTS_LEAGUES",
        "cs2,cs,csgo,lol,val,valorant,dota2,dota,ow,cod,r6,rl,sc2,mlbb,"
        "kog,hok,apex,pubg,ff,esports").split(",") if x.strip())


def safeguards_cfg() -> Dict[str, Any]:
    """The opening safeguards as this process actually runs them (boot log,
    so a deployment can be verified from its own output)."""
    from src import pmus_sports_xv as XV
    return {"min_touch_qty": str(MIN_TOUCH_QTY),
            "depth_ttl_s": DEPTH_TTL_S,
            "late_cutoff": LATE_CUTOFF,
            "esports_rule": "fail_closed_without_format",
            "esports_leagues": sorted(ESPORTS_LEAGUES),
            "intl_name_match": list(XV.NAME_MATCH_LEAGUES),
            "intl_three_way": list(XV.THREE_WAY_LEAGUES),
            "one_market_per_game": True,
            "loop_stall_s": LOOP_STALL_S,
            "rn1_reward_rank": RN1_REWARD_RANK,
            **({"rn1_duration_rank": {
                "min_remaining_s": RN1_MIN_REMAINING_S,
                "horizon_s": RN1_DURATION_HORIZON_S,
                "unknown_factor": RN1_DURATION_UNKNOWN_FACTOR,
                "table_override": RN1_DURATION_MIN}}
               if RN1_DURATION_RANK else {}),
            **({"economics_report": {"min_interval_s": ECONOMICS_MIN_S}}
               if ECONOMICS_REPORT else {}),
            "rn1_reward_only": RN1_REWARD_ONLY,
            "rn1_edge_gate": {"on": RN1.EDGE_GATE,
                              "adverse_ticks": RN1.ADVERSE_TICKS,
                              "risk_step_ticks": RN1.RISK_STEP_TICKS,
                              "allowance_step_usd": str(RN1.EDGE_UNIT_USD),
                              "add_jump_ticks": RN1_JUMP_TICKS,
                              "reward_credit_ticks": RN1_REWARD_CREDIT_TICKS,
                              "reward_min_day_usd": RN1_REWARD_MIN_DAY_USD,
                              "reward_project_h": RN1_REWARD_PROJECT_H,
                              "no_fair_means": "no_bid",
                              "fair_stable_s": RN1_FAIR_STABLE_S,
                              "fair_max_range_usd":
                                  str(RN1_FAIR_MAX_RANGE_USD)},
            "markout_horizons_s": [5, 30, 120, 300],
            "stall_drill": stall_drill(),
            "order_ttl_s": (ORDER_TTL_S if order_good_till() else 0),
            "rn1_extend_ev": {"on": RN1.EXTEND_EV,
                              "edge_ticks": RN1.EXTEND_EDGE_TICKS,
                              "step_ticks": RN1.EXTEND_STEP_TICKS,
                              "jump_ticks": RN1_JUMP_TICKS,
                              "reward_credit_ticks":
                                  RN1_REWARD_CREDIT_TICKS},
            "safety_latch": {"on": SAFETY_LATCH_ON,
                             "mismatch_grace_s": MISMATCH_GRACE_S,
                             "loop_error_n": LOOP_ERROR_LATCH_N}}


def is_esports(slug: str, state: Optional[Mapping[str, Any]] = None) -> bool:
    return sport_of(slug) in ESPORTS_LEAGUES or \
        str((state or {}).get("type") or "").lower() == "esports"


def late_for(slug: str, state: Optional[Mapping[str, Any]]
             ) -> Optional[str]:
    """Why NEW exposure is refused in this market for phase reasons, else
    None. The one entry every opening path uses. Esports is judged ALWAYS
    -- with the late cutoff switched off, and with no match state at all
    (a missing state is an unknown format: blocked) -- because its rule is
    a safety floor, not a tuning choice."""
    if is_esports(slug, state):
        why = esports_late(state or {})
        if why:
            return why
    return is_late(state) if LATE_CUTOFF else None


def is_late(state: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Reason string if the game is in its deciding stretch, else None.
    Unknown sports/shapes -> None (the price band still applies)."""
    if not state:
        return None
    typ = str(state.get("type") or "").lower()
    per = str(state.get("period") or "").upper()
    if typ == "crypto-1d":
        try:
            end = datetime.fromisoformat(str(state.get("endDate"))
                                         .replace("Z", "+00:00"))
            left = (end - datetime.now(timezone.utc)).total_seconds()
        except Exception:                                 # noqa: BLE001
            return "crypto_end_unknown"
        return "crypto_last_%ds" % int(CRYPTO_CUTOFF_S) \
            if left <= CRYPTO_CUTOFF_S else None
    if state.get("ended"):
        return "ended"
    if per.startswith("OT") or per in ("SO", "PEN", "ET1", "ET2"):
        return "overtime:" + per
    clock = _clock_s(state.get("elapsed"))
    if typ in ("football", "basketball"):
        if per == "Q4":
            return "Q4"
        if typ == "basketball" and per == "H2" and clock is not None \
                and clock <= 600:
            return "H2_last10"
    if typ == "college-basketball" and per == "H2" and clock is not None \
            and clock <= 600:
        return "H2_last10"
    if typ == "hockey" and per == "P3":
        return "P3"
    if typ == "soccer" and per == "2H":
        m = _minute(state.get("elapsed"))
        if m is None:
            # owner 2026-11-22: fail closed. A second half whose minute
            # cannot be read may be its last one.
            return "2H_minute_unknown"
        if m >= 70:
            return "2H_70+"
    if typ == "esports":
        return esports_late(state)
    if typ == "tennis" and per.startswith("S"):
        try:
            if int(per[1:]) >= 3:
                return "set_" + per
        except ValueError:
            pass
    if typ == "table-tennis" and per.startswith("S"):
        try:
            if int(per[1:]) >= 4:
                return "set_" + per
        except ValueError:
            pass
    if typ == "ufc":
        rounds = (state.get("ufcState") or {}).get("rounds")
        if rounds and per == "R%s" % rounds:
            return "final_round"
    return None


def floor_tick(px: D, tick: D) -> D:
    return (px / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def open_prices(yes_bid: D, yes_ask: D, tick: D, depth: Optional[int] = None,
                cap: Optional[D] = None
                ) -> Optional[Dict[str, D]]:
    """Both opening bids, D ticks under each side's touch. None if refused."""
    if depth is None:
        depth = depth_ticks(tick)
    if yes_bid is None or yes_ask is None or yes_ask <= yes_bid:
        return None
    if not (BAND_LO <= yes_bid <= BAND_HI):
        return None
    if (yes_ask - yes_bid) > MAX_SPREAD_TICKS * tick:
        return None
    y = floor_tick(yes_bid - depth * tick, tick)
    n = floor_tick((ONE - yes_ask) - depth * tick, tick)
    if y < tick or n < tick or y + n > (PAIR_CAP if cap is None else cap):
        return None
    return {"YES": y, "NO": n}


def reward_open_prices(yes_bid: D, yes_ask: D, tick: D,
                       max_spread: Optional[float],
                       cap: Optional[D] = None) -> Optional[tuple]:
    """(prices, depth) for a rewarded market, or None.

    Depth 0 = both legs AT the touch (YES bid at best bid, NO bid at
    1 - best ask, i.e. a YES offer at best ask). If that pair breaks
    PAIR_CAP (spread < 2c at a 0.98 cap) take the next deeper legal depth,
    exactly as the normal ladder does. The chosen legs must ALSO sit within
    the program's maxSpread of mid (|YES - mid| and |(1 - NO) - mid|);
    deeper is only farther from mid, so the first legal depth that fails
    maxSpread ends the search (None -> quote as a non-rewarded market)."""
    from src.pmus_sports_rewards import legs_within_max_spread
    d0 = reward_min_depth(tick)
    for d in range(d0, max(d0, depth_ticks(tick)) + 1):
        p = open_prices(yes_bid, yes_ask, tick, depth=d, cap=cap)
        if p is None:
            continue
        if legs_within_max_spread(float(p["YES"]), float(p["NO"]),
                                  float(yes_bid), float(yes_ask), max_spread):
            return p, d
        return None
    return None


def side_bid(side: str, yes_bid: D, yes_ask: D) -> D:
    return yes_bid if side == "YES" else ONE - yes_ask


def completion_price(side: str, current: D, filled_other_px: D,
                     yes_bid: D, yes_ask: D, tick: D) -> D:
    """Never lower; up to this side's own bid while pair <= PAIR_CAP."""
    target = min(side_bid(side, yes_bid, yes_ask),
                 floor_tick(PAIR_CAP - filled_other_px, tick))
    return max(current, floor_tick(target, tick))


# ------------------------------------------------------------- state

class Book:
    """Durable per-market state: own orders, fills, locked pairs."""

    def __init__(self, path: Path):
        self.path = path
        self.d: Dict[str, Any] = {"markets": {}, "own_order_ids": [],
                                  "pairs": [], "lone": {}}
        if path.exists():
            try:
                self.d = json.loads(path.read_text())
            except Exception:
                pass

    _save_lock = threading.RLock()

    def save(self) -> None:
        """Atomic write. Serialised: the SIGTERM cancel thread and the main
        loop may both save; a dict mutated mid-dump is retried."""
        with self._save_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            for attempt in range(5):
                try:
                    text = json.dumps(self.d, default=str)
                    break
                except RuntimeError:                  # changed during dump
                    if attempt == 4:
                        raise
                    time.sleep(0.01)
            tmp.write_text(text)
            tmp.replace(self.path)

    def m(self, slug: str) -> Dict[str, Any]:
        return self.d["markets"].setdefault(slug, {"orders": {}, "pos": {
            "YES": "0", "NO": "0"}, "cost": {"YES": "0", "NO": "0"},
            "tick": None, "min_qty": None, "done": False})

    def unmatched_cost(self, slug: str) -> D:
        mk = self.d["markets"].get(slug)
        if not mk:
            return D(0)
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        cy, cn = D(mk["cost"]["YES"]), D(mk["cost"]["NO"])
        if y > n and y > 0:
            return cy * (y - n) / y
        if n > y and n > 0:
            return cn * (n - y) / n
        return D(0)

    def total_unmatched(self) -> D:
        """OPEN lone-leg exposure only. A market marked done is either
        released flat (0) or CLOSED -- its lone leg has resolved and is no
        longer at risk. Counting closed markets (LIVE 2026-11-15: 20+ settled
        legs) pinned the cap at 11.999/12 and stopped all new pairs."""
        return sum((self.unmatched_cost(s) for s, mk in
                    self.d["markets"].items() if not mk.get("done")), D(0))

    def resting_pair_exposure(self, slug: str) -> D:
        """Worst NEW lone-leg cost the market's resting orders can create:
        when balanced, one open leg filling alone (the dearer remainder).
        When unbalanced the open order is the completion, which only
        reduces unmatched cost -> 0."""
        mk = self.d["markets"].get(slug)
        if not mk or mk.get("done"):
            return D(0)
        if D(mk["pos"]["YES"]) != D(mk["pos"]["NO"]):
            return D(0)
        legs = [D(o["px"]) * (D(o["qty"]) - D(o.get("filled", "0")))
                for o in mk["orders"].values() if o.get("open")]
        return max(legs, default=D(0))

    def committed_excluding(self, slug: str) -> D:
        """Total unmatched + every OTHER market's resting-pair exposure."""
        return self.total_unmatched() + sum(
            (self.resting_pair_exposure(s) for s in self.d["markets"]
             if s != slug), D(0))


# ------------------------------------------------------------- venue I/O

class Venue:
    def __init__(self):
        from src import pmus_q1_executor_v4 as q1
        from src import pmus_micro_executor as micro
        from src import pmus_spring_gateway_candidates as gw
        os.environ.setdefault("PMUS_Q1_STATE_DIR", str(STATE_DIR / "q1"))
        self.q1, self.micro, self.gw = q1, micro, gw
        # every SIGNED call goes through one shared budget: cancels and
        # order-status first, research only from spare (2026-11-21 429s)
        from src import pmus_request_budget as RB
        self.t = RB.GovernedTransport(q1.Transport(), RB.from_env())
        self._spec: Dict[str, Any] = {}

    def gget(self, path: str) -> Any:
        """One public (keyless) gateway GET. Counted per route family for
        the heartbeat (`gateway_requests`); the count decides nothing."""
        self._gw_note("by_route", path)
        try:
            return self.gw.http_get_json(path)
        except Exception as exc:                          # noqa: BLE001
            self._gw_note("errors_by_route", path)
            if getattr(exc, "code", None) == 429:
                self._gw_note("http_429_by_route", path)
            raise

    def _gw_note(self, table: str, path: str) -> None:
        try:
            from src import pmus_request_budget as RB
            st = self.__dict__.get("_gw_stats")
            if st is None:
                st = self.__dict__.setdefault("_gw_stats", {
                    "by_route": {}, "errors_by_route": {},
                    "http_429_by_route": {}, "lock": threading.Lock(),
                    "t0": time.monotonic()})
            with st["lock"]:
                RB.count_route(st[table], RB.route_family("GET", path))
        except Exception:                                 # noqa: BLE001
            pass

    def gateway_report(self) -> Dict[str, Any]:
        st = self.__dict__.get("_gw_stats")
        if st is None:
            return {"sent": 0, "by_route": {}}
        with st["lock"]:
            up = max(1e-9, time.monotonic() - st["t0"])
            sent = sum(st["by_route"].values())
            return {"sent": sent, "sent_rps": round(sent / up, 3),
                    "by_route": dict(st["by_route"]),
                    "errors_by_route": dict(st["errors_by_route"]),
                    "http_429_by_route": dict(st["http_429_by_route"]),
                    "label": "public keyless gateway reads since start; "
                             "not on the signed budget"}

    states: Dict[str, Any] = {}

    def crypto_targets(self) -> list:
        """OWNER 2026-11-15 SOL/ETH 1d reward trial: explicit prefixes only
        (never cpc-btc-: the BTC5m/BTC15m lanes)."""
        if not CRYPTO_PREFIXES:
            return []
        d = self.gget("/v1/events?active=true&closed=false&limit=200"
                      "&categories=crypto") or {}
        evs = d if isinstance(d, list) else d.get("events") or []
        out = []
        for ev in evs:
            for m in (ev.get("markets") or []) if isinstance(ev, Mapping) else []:
                slug = str((m or {}).get("slug") or "")
                if not slug.startswith(CRYPTO_PREFIXES) or \
                        slug.startswith("cpc-btc-"):
                    continue
                self.states[slug] = {"type": "crypto-1d",
                                     "endDate": m.get("endDate")
                                     or ev.get("endDate")}
                out.append({"slug": slug, "league": "crypto",
                            "why": "crypto_reward_trial",
                            "event_state": self.states[slug],
                            "market_meta": {k: m.get(k) for k in
                                            ("bestBidQuote", "bestAskQuote")
                                            if m.get(k) is not None}})
        return out

    def live_sports(self) -> list:
        if LIVE_EVENTS_TTL_S > 0:
            hit = self.__dict__.get("_live_cache")
            if hit and time.monotonic() - hit[0] < LIVE_EVENTS_TTL_S:
                return list(hit[1])
            # an error is never cached: the next call reads again
            out = self._live_sports_read()
            self.__dict__["_live_cache"] = (time.monotonic(), out)
            return list(out)
        return self._live_sports_read()

    def _live_sports_read(self) -> list:
        crypto = []
        try:
            crypto = self.crypto_targets()
        except Exception as exc:                          # noqa: BLE001
            log("crypto_discovery_error", err=str(exc)[:160])
        return self._live_sports() + crypto

    def _live_sports(self) -> list:
        events, _ = self.gw.fetch_live_events(self.gget)
        st: Dict[str, Any] = {}
        for ev in events or []:
            es = ev.get("eventState") if isinstance(ev, Mapping) else None
            for m in (ev.get("markets") or []) if isinstance(ev, Mapping) else []:
                if isinstance(m, Mapping) and m.get("slug"):
                    st[str(m["slug"])] = es or {}
                    learn_cricket(m["slug"], (es or {}).get("type")
                                  if isinstance(es, Mapping) else None,
                                  m.get("sportsMarketType"))
        if st:
            keep = {k: v for k, v in self.states.items()
                    if (v or {}).get("type") == "crypto-1d"}
            self.states = {**keep, **st}
        return [tg for tg in self.gw.live_event_targets(events)
                if str(tg["slug"]).startswith(SPORTS_PREFIXES)]

    _pregame_cache = None

    def pregame_sports(self) -> list:
        now = time.time()
        hit = self._pregame_cache
        if hit and now - hit[0] < PREGAME_CACHE_S:
            return hit[1]
        lo = datetime.fromtimestamp(now, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        hi = datetime.fromtimestamp(now + PREGAME_MAX_LEAD_S,
                                    timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        d = self.gget("/v1/events?active=true&closed=false&categories=sports"
                      f"&limit=500&startDateMin={lo}&startDateMax={hi}")
        evs = d.get("events") if isinstance(d, Mapping) else d
        out = pregame_targets(evs or [], now)
        self._pregame_cache = (now, out)
        return out

    ws = None              # src.pmus_two_sided_ws.TwoSidedWs when WS is on
    guard = None           # src.pmus_sports_xv.IntlGuard when the guard is on

    def bbo(self, slug: str) -> Optional[Dict[str, D]]:
        if self.ws is not None:
            hit = self.ws.fresh_bbo(slug)
            if hit is not None:
                return hit
        md = (self.gget(f"/v1/markets/{slug}/bbo") or {}).get("marketData") or {}
        b, a = self.q1._d(md.get("bestBid")), self.q1._d(md.get("bestAsk"))
        if b is None or a is None:
            return None
        return {"bid": b, "ask": a}

    def top_depth(self, slug: str) -> Optional[Dict[str, D]]:
        """{'bid','bid_qty','ask','ask_qty'} from the public book (keyless
        GET), cached DEPTH_TTL_S; None when it cannot be read."""
        cache = self.__dict__.setdefault("_depth", {})
        hit = cache.get(slug)
        if hit and time.monotonic() - hit[0] < DEPTH_TTL_S:
            return hit[1]
        out = None
        try:
            md = (self.gget(f"/v1/markets/{slug}/book") or {}
                  ).get("marketData") or {}

            def top(rows, best):
                lv = []
                for r in rows or []:
                    px, q = self.q1._d(r.get("px")), self.q1._d(r.get("qty"))
                    if px is not None and q is not None:
                        lv.append((px, q))
                if not lv:
                    return None
                px = best(p for p, _ in lv)
                return px, sum((q for p, q in lv if p == px), D(0))
            b, a = top(md.get("bids"), max), top(md.get("offers"), min)
            if b and a:
                out = {"bid": b[0], "bid_qty": b[1],
                       "ask": a[0], "ask_qty": a[1]}
        except Exception:                                 # noqa: BLE001
            out = None
        cache[slug] = (time.monotonic(), out)
        return out

    def spec(self, slug: str) -> Optional[Dict[str, Any]]:
        hit = self._spec.get(slug)
        if hit and time.monotonic() - hit[0] < 60:
            return hit[1]
        out = self._spec_fetch(slug)
        self._spec[slug] = (time.monotonic(), out)
        return out

    def _spec_fetch(self, slug: str) -> Optional[Dict[str, Any]]:
        m = (self.gget(f"/v1/market/slug/{slug}") or {}).get("market") or {}
        if not (m.get("active") is True and m.get("closed") is False
                and m.get("archived") is False):
            return None
        tick = self.q1._d(m.get("orderPriceMinTickSize")) or D("0.01")
        mq = self.q1._d(m.get("minimumTradeQty")) or D("1")
        return {"tick": tick, "min_qty": mq}

    def account(self) -> Dict[str, Any]:
        pos = self.t.request("GET", "/v1/portfolio/positions")
        rows = pos.get("positions") if isinstance(pos, Mapping) else None
        held: Dict[str, D] = {}
        if isinstance(rows, Mapping):
            for slug, p in rows.items():
                if isinstance(p, Mapping):
                    v = self.q1._d(p.get("netPositionDecimal") or
                                   p.get("netPosition") or p.get("quantity"))
                    if v:
                        held[slug] = v
        elif isinstance(rows, list):
            for p in rows:
                if isinstance(p, Mapping) and p.get("marketSlug"):
                    v = self.q1._d(p.get("netPositionDecimal") or
                                   p.get("netPosition") or p.get("quantity"))
                    if v:
                        held[p["marketSlug"]] = v
        # stamped BEFORE the request, like the fill poll's own list read:
        # an order is only "in time to be listed" relative to this moment
        oo_mono, oo_wall = time.monotonic(), time.time()
        oo = self.t.request("GET", "/v1/orders/open")
        orders = [o for o in (oo.get("orders") or []) if isinstance(o, Mapping)]
        out = {"positions": held, "open_orders": orders}
        if isinstance(oo, Mapping) and isinstance(oo.get("orders"), list):
            # the list was readable in the form Venue.open_orders() demands
            out["open_orders_read"] = {"mono": oo_mono, "wall": oo_wall}
        return out

    def open_orders(self) -> Dict[str, Any]:
        """The venue's open-order list in ONE signed read, never deferred
        by the request budget (it replaces reads that never were).
        {"rows": {order id: row}}. A list may be one page of several: an
        order MISSING from it therefore proves nothing and is read
        exactly; an order PRESENT in it is the venue's own word."""
        kw = {"budget_class": "critical"} if hasattr(self.t, "budget") \
            else {}
        oo = self.t.request("GET", "/v1/orders/open", **kw)
        if not isinstance(oo, Mapping) or \
                not isinstance(oo.get("orders"), list):
            raise ValueError("open-order list unreadable")
        return {"rows": {str(o.get("id")): o for o in oo["orders"]
                         if isinstance(o, Mapping) and o.get("id")}}

    def positions_snapshot(self, max_pages: int = 20) -> Dict[str, Any]:
        """EVERY venue position row (account-wide, all strategies) plus all
        open orders. Read-only GETs. /v1/portfolio/positions pages with
        nextCursor/eof; account() reads page one only, so this walks the
        cursor and says whether the walk was complete."""
        from src import pmus_sports_inventory as INV
        get = self.q1.base._get_with_retry
        resp = get(self.t, "/v1/portfolio/positions")
        rows = INV.rows_of(resp)
        pages, complete = 1, True
        while isinstance(resp, Mapping) and not resp.get("eof") and \
                resp.get("nextCursor"):
            if pages >= max_pages:
                complete = False
                break
            resp = get(self.t, "/v1/portfolio/positions?cursor="
                       + str(resp["nextCursor"]))
            page = INV.rows_of(resp)
            if not page:
                break
            rows.extend(page)
            pages += 1
        oo = get(self.t, "/v1/orders/open")
        orders = [o for o in (oo.get("orders") or []) if isinstance(o, Mapping)] \
            if isinstance(oo, Mapping) else []
        return {"rows": rows, "open_orders": orders, "pages": pages,
                "complete": complete}

    def status(self, oid: str) -> Dict[str, Any]:
        """{'qty': filled, 'px': outcome-terms avg, 'open': bool}."""
        try:
            o = self.q1._order_status(self.t, oid)
        except self.q1.RejectedNoFill:
            return {"qty": D(0), "px": None, "open": False, "state": "REJECTED"}
        return self.status_from(o)

    def status_from(self, o: Mapping[str, Any]) -> Dict[str, Any]:
        state = str(o.get("state") or o.get("status") or "").upper()
        f = self.micro.authoritative_fill(o, D(str(o.get("quantity") or
                                                  o.get("qty") or "1e9")),
                                          accept_partial=True)
        # A cancel the venue has ACCEPTED but not applied (PENDING_CANCEL /
        # *_PENDING) can still fill. LIVE 2026-11-17 aec-utr-gaskan-hanbra:
        # a YES 5 @0.31 fill most likely raced the cancel, the order was
        # booked closed with 0 filled and never re-read, and the book missed
        # $3.45 the venue paid. Pending states stay OPEN, so the order is
        # polled again until it is truly terminal and every fill is recorded.
        is_open = not any(x in state for x in
                          ("CANCEL", "EXPIRED", "REJECT", "FILLED")) or \
            "PARTIALLY" in state or "PENDING" in state
        return {"qty": f["qty"] if f else D(0), "px": f["price"] if f else None,
                "open": is_open, "state": state, "t": order_time(o)}

    def place(self, slug: str, side: str, px: D, qty: D, stage: str,
              post_only: bool = True) -> str:
        b = getattr(self.t, "budget", None)
        if RN1_COMPLETION_SAFETY_CLASS and b is not None and \
                hasattr(b, "safety") and is_completion_stage(stage):
            # a completion reduces risk: it waits its turn in the safety
            # class (ahead of openings) instead of being deferred
            # capped at the normal wait: a cool-down must not hold the
            # trading loop (and the cancels it issues) any longer than it
            # holds a normal order
            with b.safety(max_wait_s=getattr(b, "normal_wait_s", 3.0)):
                return self._place(slug, side, px, qty, stage, post_only)
        return self._place(slug, side, px, qty, stage, post_only)

    def _place(self, slug: str, side: str, px: D, qty: D, stage: str,
               post_only: bool = True) -> str:
        gt = order_good_till()
        # records whether the order itself was handed to the venue, so a
        # failure BEFORE that point is known to have left nothing behind
        t = self.last_order_post = OrderPostWatch(self.t)
        if gt is None:
            return self.micro.place_limit_buy(t, slug, side, px, qty,
                                              stage, post_only=post_only)
        return self.micro.place_limit_buy(t, slug, side, px, qty, stage,
                                          post_only=post_only, good_till=gt)

    def cancel(self, oid: str) -> bool:
        return self.q1.cancel_exact(self.t, oid)

    def cancel_fast(self, oid: str, slug: str) -> bool:
        """Exact-id cancel without the pre-read: the bot already knows the
        market of its own order. POST once, then confirm a terminal state
        (short polls). Same venue route and body as cancel_exact."""
        self.t.request("POST", f"/v1/order/{oid}/cancel", {"marketSlug": slug})
        last = None
        for _ in range(12):
            try:
                o = self.q1._order_status(self.t, oid)
            except self.q1.RejectedNoFill:
                return {"qty": D(0), "px": None, "open": False,
                        "state": "REJECTED"}
            st = self.status_from(o)
            last = st
            if not st["open"]:
                return st          # terminal: this read IS the final state
            time.sleep(0.1)
        return last                # still open after ~1.2s: caller sees open


class OrderPostWatch:
    """Pass-through for the signed transport that notes when the ORDER
    itself (POST /v1/orders) is handed on. Everything before that -- the
    venue's preview, the local post-only check -- sends no order."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.attempted = False

    def request(self, method: str, path: str, *a: Any, **kw: Any) -> Any:
        if str(method).upper() == "POST" and \
                str(path).split("?", 1)[0].rstrip("/") == "/v1/orders":
            self.attempted = True
        return self._inner.request(method, path, *a, **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def send_never_left(venue: Any, exc: BaseException) -> Optional[str]:
    """Why a failed placement is KNOWN to have sent no order, else None
    (owner 2026-11-25: "Release capital reservations immediately when a
    send is definitively deferred locally and never transmitted. Preserve
    reservations for every uncertain submission").
      * the shared request budget refused the request: it raises before
        anything is written to the wire (pmus_request_budget.acquire);
      * the failure came before the order POST was attempted (the venue's
        preview refused, or the local post-only check refused).
    A failure at or after the order POST -- a timeout, a dropped
    connection, an answer without an order id -- is NOT known, and is not
    released here."""
    if type(exc).__name__ == "BudgetDeferred":
        return "deferred_by_request_budget"
    w = getattr(venue, "last_order_post", None)
    if isinstance(w, OrderPostWatch) and not w.attempted:
        return "failed_before_the_order_was_sent"
    return None


# ------------------------------------------------------------- engine

class Engine:
    def __init__(self, venue: Venue, book: Book):
        self.v, self.b = venue, book
        #: campaign marker in force (window start kept across a relock)
        self.since = adopt_campaign_marker(book)
        # account freshness (new exposure needs a fresh read; set by run())
        self.require_fresh_account = False
        self._acct_ok_at: Optional[float] = None
        self._acct_cache: Optional[Dict[str, Any]] = None
        self._acct_fail = 0
        self._stale_try_at = -1e9
        self._cancel_all_lock = threading.Lock()
        self.orphan_stale = 0
        self._shutdown_thread: Optional[threading.Thread] = None
        self._last_reward = time.monotonic() - REWARD_CHECK_S + 120  # first ~2min after boot
        self.stop = False
        # reward mode only: {slug: (fetched_monotonic, program_row | None)}
        self._prog: Dict[str, Any] = {}
        self._rget = None
        # realtime mode only (PMUS_TWO_SIDED_WS=1 -> venue.ws is set)
        self.ws = getattr(venue, "ws", None)
        self._game_start: Dict[str, float] = {}
        self._wake = threading.Event()
        self._evq: "queue.Queue" = queue.Queue()
        self.reactive_pulls = 0
        self.jump_pauses = 0
        if self.ws is not None:
            self.ws.on_change = self._on_ws_change
        # intl fair-value guard on OPENING quotes (PMUS_TWO_SIDED_INTL_GUARD)
        self.guard = getattr(venue, "guard", None)
        self._intlq: "queue.Queue" = queue.Queue()
        if self.guard is not None:
            self.guard.on_change = self._on_intl_change

    # ------------------------------------------------ realtime (WS) mode
    def _on_ws_change(self, slug: str, prev: Mapping, new: Mapping,
                      t: float) -> None:
        """SOCKET THREAD: enqueue and wake the main loop; nothing else.
        Stamped with this process's monotonic receipt time (latency_ms)."""
        self._evq.put((slug, dict(prev), dict(new), time.monotonic()))
        self._wake.set()

    def _on_intl_change(self, slug: str) -> None:
        """INTL SOCKET THREAD: enqueue and wake the main loop; nothing else."""
        self._intlq.put((slug, time.monotonic()))
        self._wake.set()

    def drain_intl_events(self) -> None:
        """MAIN THREAD: intl moved for these markets -- re-check every
        resting OPENING quote against intl fair value; pull unsafe pairs at
        once (PMUS has usually not moved yet, so react() would not fire)."""
        if self.guard is None:
            return
        seen: Dict[str, float] = {}
        while True:
            try:
                slug, t = self._intlq.get_nowait()
            except queue.Empty:
                break
            seen.setdefault(slug, t)
        for slug, t in seen.items():
            try:
                self._intl_check(slug, t)
            except Exception as exc:                      # noqa: BLE001
                log("market_error", slug=slug, at="intl_guard",
                    err=f"{type(exc).__name__}:{str(exc)[:200]}")

    def _intl_check(self, slug: str, t0: float) -> None:
        mk = self.b.d["markets"].get(slug)
        if not mk or mk.get("done"):
            return
        if D(mk["pos"]["YES"]) != D(mk["pos"]["NO"]):
            # unbalanced: completions are never guarded; an EXTEND is
            ext = [(oid, o) for oid, o in mk["orders"].items()
                   if o.get("open") and o.get("stage") == "two_sided_extend"]
            if not ext:
                return
            tick = (self.v.spec(slug) or {}).get("tick") or D("0.01")
            oid, o = ext[0]
            v = self.guard.verdict(slug, {o["side"]: D(o["px"])}, tick)
            if not v["ok"]:
                self.guard.stats["pulled"] += 1
                log("intl_guard_pull", slug=slug, legs={o["side"]: o["px"]},
                    stage="two_sided_extend", **v)
                self._cancel_legs(slug, [(oid, o)])
            return
        legs = {o["side"]: D(o["px"]) for o in mk["orders"].values()
                if o.get("open")}
        if not legs:
            return
        tick = (self.v.spec(slug) or {}).get("tick") or D("0.01")
        v = self.guard.verdict(slug, legs, tick)
        if v["ok"]:
            return
        self.guard.stats["pulled"] += 1
        log("intl_guard_pull", slug=slug, legs={k: str(x) for k, x in
                                               legs.items()}, **v)
        self._pull(slug, "intl_guard:" + v["why"], t0)

    def drain_ws_events(self) -> None:
        """MAIN THREAD: act on every queued touch change, one pass per slug."""
        self.drain_intl_events()
        if self.ws is None:
            return
        self.ws.set_slugs(self.active())
        moves: Dict[str, list] = {}
        while True:
            try:
                slug, prev, new, t = self._evq.get_nowait()
            except queue.Empty:
                break
            moves.setdefault(slug, []).append((prev, new, t))
        for slug, mv in moves.items():
            try:
                self.react(slug, mv)
            except Exception as exc:                  # noqa: BLE001
                log("market_error", slug=slug, at="ws_react",
                    err=f"{type(exc).__name__}:{str(exc)[:200]}")

    def react(self, slug: str, moves: list) -> None:
        mk = self.b.d["markets"].get(slug)
        if not mk or mk.get("done"):
            return
        spec = self.v.spec(slug) or {}
        tick = spec.get("tick") or D("0.01")
        t0 = moves[0][2]
        jump = max(ticks_moved(p, n, tick) for p, n, _t in moves)
        flat = D(mk["pos"]["YES"]) == D(mk["pos"]["NO"])
        open_legs = [o for o in mk["orders"].values() if o.get("open")]
        new = moves[-1][1]
        if jump >= JUMP_TICKS and jump * tick >= JUMP_MIN_PX:
            mk["jump_pause_until"] = time.time() + JUMP_PAUSE_S
            self.jump_pauses += 1
            self.b.save()
            log("jump_pause", slug=slug, ticks=jump, prev=moves[0][0],
                new=new, pause_s=JUMP_PAUSE_S,
                pulls_opening_legs=bool(flat and open_legs),
                completion_kept=bool(not flat and open_legs))
            if flat and open_legs:
                self._pull(slug, f"jump_{jump}_ticks", t0)
            return
        if not flat or not open_legs:
            return                 # completion legs: re-evaluated as today
        if self.reward_first_quoting(mk) and REWARD_MIN_DEPTH_PX <= 0:
            # REWARD-FIRST: time on book is the score -- no 1-tick reactive
            # pull; the main loop's step_market (every LOOP_S) re-prices on
            # a >= REWARD_REPRICE_TICKS move or a target-walk exit. The jump
            # pause above still pulls at once.
            return
        legs: Dict[str, D] = {}
        for o in mk["orders"].values():
            if o.get("open"):
                # with several levels on a side, the highest is the one the
                # move threatens first
                legs[o["side"]] = max(legs.get(o["side"], D(0)), D(o["px"]))
        # a bid deliberately joined at the touch is not "too close" to it;
        # the near-cross rule below still pulls it when the spread closes
        joined = bool(getattr(self, "_rn1_joined", {}).get(slug))
        why = adverse_reasons(legs, {"bid": D(str(new["bid"])),
                                     "ask": D(str(new["ask"]))},
                              tick, 0 if joined else depth_ticks(tick))
        if joined and RN1_JOIN_ONE_TICK:
            # a post-only bid at the best bid of a one-tick book is one tick
            # from the offer by design; it is pulled only if it would cross
            nb_ = {"bid": D(str(new["bid"])), "ask": D(str(new["ask"]))}
            why = [w for w in why if not w.endswith("_near_cross")] + [
                "%s_crossed" % sd.lower() for sd, px_ in legs.items()
                if px_ >= (nb_["ask"] if sd == "YES"
                           else ONE - nb_["bid"])]
        if why and RN1.ON and RN1_PULL_THREATENED_ONLY:
            nb = {"bid": D(str(new["bid"])), "ask": D(str(new["ask"]))}
            dep = 0 if joined else depth_ticks(tick)
            hit = [(oid, o) for oid, o in mk["orders"].items()
                   if o.get("open") and adverse_reasons(
                       {o["side"]: D(o["px"])}, nb, tick, dep)]
            kept = sum(1 for o in mk["orders"].values() if o.get("open")) \
                - len(hit)
            c0 = time.monotonic()
            self._cancel_legs(slug, hit)
            self.reactive_pulls += 1
            log("reactive_pull", slug=slug, reason=",".join(why),
                legs=len(hit), kept=kept, mode="threatened_only",
                latency_ms=round((time.monotonic() - t0) * 1000),
                cancel_ms=round((time.monotonic() - c0) * 1000),
                pos=mk["pos"])
            self.step_market(slug)
        elif why:
            self._pull(slug, ",".join(why), t0)
            self.step_market(slug)          # re-set both legs at the new touch

    def _cancel_legs(self, slug: str, legs: list) -> None:
        """OWNER 2026-11-15 'be quicker with cancellations': cancel every
        listed own leg IN PARALLEL, then read each final state and record any
        fill that raced the cancel. Exact own ids only; nothing is re-placed
        until every leg has been read back. The first error is re-raised
        after all legs were attempted (fail closed, market_error path)."""
        if not legs:
            return
        from concurrent.futures import ThreadPoolExecutor
        fast = getattr(self.v, "cancel_fast", None)

        def _one(item):
            oid, _o = item
            try:
                if fast is not None:
                    st = fast(oid, slug)          # final state, one read
                    if st is None:
                        st = self.v.status(oid)
                    return oid, st, None
                self.v.cancel(oid)
                return oid, self.v.status(oid), None
            except Exception as exc:                      # noqa: BLE001
                return oid, None, exc
        if len(legs) == 1:
            results = [_one(legs[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(legs)) as ex:
                results = list(ex.map(_one, legs))
        first_err = None
        by_id = dict(legs)
        for oid, st, err in results:
            o = by_id[oid]
            if err is not None:
                first_err = first_err or err
                continue
            self._record_fill(slug, o["side"], oid, st)
            o["open"] = st["open"]
        self.b.save()
        if first_err is not None:
            raise first_err

    def _pull(self, slug: str, reason: str, t0: float) -> None:
        mk = self.b.m(slug)
        open_legs = [(oid, o) for oid, o in mk["orders"].items()
                     if o.get("open")]
        legs = len(open_legs)
        c0 = time.monotonic()
        self._cancel_legs(slug, open_legs)
        cancel_ms = round((time.monotonic() - c0) * 1000)
        queue_ms = round((c0 - t0) * 1000) if t0 else None
        self.reactive_pulls += 1
        log("reactive_pull", slug=slug, reason=reason, legs=legs,
            latency_ms=round((time.monotonic() - t0) * 1000),
            queue_ms=queue_ms, cancel_ms=cancel_ms,
            pos=mk["pos"])

    def _sleep(self, seconds: float) -> None:
        """LOOP_S sleep; in WS mode every touch change is handled at once."""
        if self.ws is None and self.guard is None:
            time.sleep(seconds)
            return
        end = time.monotonic() + seconds
        while not self.stop:
            left = end - time.monotonic()
            if left <= 0:
                break
            if self._wake.wait(left):
                self._wake.clear()
                self.drain_ws_events()

    # ------------------------------------------------ reward programs
    def _reward_get(self):
        if self._rget is None:
            from src.pmus_sports_rewards import SpacedGet
            self._rget = SpacedGet(self.v.gget)
        return self._rget

    def reward_programs(self, slugs: list) -> Dict[str, Any]:
        """{slug: paying period dict} for the given slugs; programs cached
        REWARD_REFRESH_S, only stale/unseen slugs are queried (<= 2 rps)."""
        from src import pmus_sports_rewards as R
        now = time.monotonic()
        stale = [s for s in slugs if s not in self._prog or
                 now - self._prog[s][0] > REWARD_REFRESH_S]
        if stale:
            found, diag = R.fetch_programs(self._reward_get(), stale)
            pm = R.build_program_map(found)
            for s in stale:
                self._prog[s] = (now, pm.get(s))
            log("reward_discovery", queried=len(stale), rewarded=len(pm),
                requests=diag["requests"], errors=diag["errors"][:3],
                label=R.LABEL)
        out = {}
        for s in slugs:
            prog = (self._prog.get(s) or (0, None))[1]
            if not prog or not reward_allowed(s):
                continue
            per = R.paying_period(prog, time.time(),
                                  event_live=self.event_live(s))
            if per:
                out[s] = per
        return out

    def event_live(self, slug: str) -> bool:
        """Is this market's game on? Decides which reward period can be
        paying. A market with no start time on record came from the live
        slate. One with a start time is live once the venue lists it as
        live or the start has passed. PMUS_TWO_SIDED_SLATE=live keeps the
        earlier answer (every market on the slate is live). The answer is
        recorded in RN1_PHASE (pregame fill rate)."""
        live = self._event_live(slug)
        RN1_PHASE[slug] = "live" if live else "pregame"
        return live

    def _event_live(self, slug: str) -> bool:
        if slate() == "live":
            return True
        gs = (self.b.d["markets"].get(slug) or {}).get("game_start")
        if gs is None:
            gs = self._game_start.get(slug)
        if gs is None:
            return True
        if slug in (getattr(self.v, "states", {}) or {}):
            return True
        return time.time() >= float(gs)

    def refresh_active_rewards(self) -> None:
        """Keep each active market's paying period current (a period that
        stops paying drops the market back to normal quoting)."""
        act = self.active()
        if not act:
            return
        try:
            pp = self.reward_programs(act)
        except Exception as exc:                      # noqa: BLE001
            log("reward_discovery_error", err=str(exc)[:160])
            return
        for s in act:
            mk = self.b.m(s)
            new = pp.get(s)
            if reward_first_on():
                # only markets selected INTO a reward slot carry a program
                # (keeps the REWARD_MARKETS cap), and only listed periods
                if not mk.get("reward_slot") or (
                        new and new.get("period") not in REWARD_FIRST_PERIODS):
                    new = None
            if (mk.get("reward") or None) != new:
                mk["reward"] = new
        self.b.save()

    def _reward_quote(self, slug: str, mk: Dict[str, Any], bbo: Mapping,
                      tick: D) -> Optional[Dict[str, Any]]:
        """Reward-mode prices for a rewarded market, else None."""
        if not rewards_on():
            return None
        rp = mk.get("reward")
        if not rp:
            return None
        r = reward_open_prices(bbo["bid"], bbo["ask"], tick,
                               rp.get("maxSpread"),
                               cap=open_cap(self.b.d, slug)
                               if reward_first_on() else None)
        self._log_reward(slug, mk, rp, bbo, tick, r)
        if r is None:
            return None
        return {"want": r[0], "depth": r[1]}

    # ------------------------------------------------ reward-first mode
    @staticmethod
    def reward_first_quoting(mk: Mapping[str, Any]) -> bool:
        """Reward-first market whose open legs are the rewarded quote."""
        if not reward_first_on() or not mk.get("reward"):
            return False
        legs = [o for o in (mk.get("orders") or {}).values() if o.get("open")]
        return bool(legs) and all(o.get("stage") == REWARDED_STAGE
                                  for o in legs)

    def _open_legs(self, mk: Mapping[str, Any]) -> Dict[str, tuple]:
        """{side: (px, remaining qty)} of our open orders in a market."""
        out: Dict[str, tuple] = {}
        for o in (mk.get("orders") or {}).values():
            if o.get("open"):
                out[o["side"]] = (D(o["px"]),
                                  D(o["qty"]) - D(o.get("filled", "0")))
        return out

    def _reward_estimate(self, slug: str, mk: Dict[str, Any], tick: D,
                         force: bool = False) -> Optional[Dict[str, Any]]:
        """THEORETICAL share of our CURRENT open legs (cached
        REWARD_WALK_CHECK_S, recomputed when the legs change). Our own size
        is removed from the public book first, then added once."""
        rp = mk.get("reward")
        if not rp:
            return None
        legs = self._open_legs(mk)
        key = {s: [str(p), str(q)] for s, (p, q) in sorted(legs.items())}
        est = mk.get("reward_est")
        if not force and est and est.get("legs") == key and \
                time.time() - float(est.get("t") or 0) < REWARD_WALK_CHECK_S:
            return est
        if not legs:
            est = {"t": time.time(), "legs": key, "share": 0.0,
                   "rate_usd_s": 0.0, "inside_walk": False}
            mk["reward_est"] = est
            return est
        from src import pmus_sports_rewards as R
        book = (self._reward_get()(f"/v1/markets/{slug}/book") or {}
                ).get("marketData") or {}
        y, n = legs.get("YES"), legs.get("NO")
        q = float(max(q for _p, q in legs.values()))
        r = R.quote_estimate(book, rp, float(y[0]) if y else None,
                             float(n[0]) if n else None, q, float(tick),
                             own_yes=float(y[1]) if y else 0.0,
                             own_no=float(n[1]) if n else 0.0,
                             yes_qty=float(y[1]) if y else 0.0,
                             no_qty=float(n[1]) if n else 0.0)
        est = {"t": time.time(), "legs": key, **r}
        mk["reward_est"] = est
        return est

    def _reward_hold(self, slug: str, mk: Dict[str, Any],
                     open_by_side: Mapping, bbo: Mapping, tick: D,
                     qty: D, shrunk: bool = False) -> bool:
        """REWARD-FIRST: keep resting rewarded legs unless a leg is
        >= REWARD_REPRICE_TICKS from its touch, the quote no longer sits
        within maxSpread / the pair cap, or our order left the target walk.
        True = keep (no cancel/replace)."""
        if set(open_by_side) != {"YES", "NO"}:
            return False
        legs = {sd: open_by_side[sd][1] for sd in ("YES", "NO")}
        if any(o.get("stage") != REWARDED_STAGE or
               (D(o["qty"]) > qty if shrunk else D(o["qty"]) != qty)
               for o in legs.values()):
            return False
        px = {sd: D(o["px"]) for sd, o in legs.items()}
        why = []
        for sd in ("YES", "NO"):
            dist = abs(side_bid(sd, bbo["bid"], bbo["ask"]) - px[sd]
                       - reward_min_depth(tick) * tick) / tick
            if dist >= REWARD_REPRICE_TICKS:
                why.append(f"{sd.lower()}_touch_{int(dist)}_ticks")
        if px["YES"] + px["NO"] > pair_cap(slug):
            why.append("pair_cap")
        from src.pmus_sports_rewards import legs_within_max_spread
        if not legs_within_max_spread(float(px["YES"]), float(px["NO"]),
                                      float(bbo["bid"]), float(bbo["ask"]),
                                      (mk.get("reward") or {}).get(
                                          "maxSpread")):
            why.append("max_spread")
        if not why:
            try:
                est = self._reward_estimate(slug, mk, tick)
            except Exception as exc:                  # noqa: BLE001
                est = None
                log("reward_estimate_error", slug=slug, err=str(exc)[:120])
            if est is not None and est.get("legs") and \
                    not est.get("inside_walk", True):
                why.append("left_target_walk")
        if why:
            log("reward_reprice", slug=slug, reasons=why,
                legs={k: str(v) for k, v in px.items()}, bid=bbo["bid"],
                ask=bbo["ask"], label="THEORETICAL reward quoting")
            return False
        return True

    def _accrue(self, slug: str, mk: Dict[str, Any]) -> None:
        """THEORETICAL accrual for the time since the last step: pool rate *
        estimated share of the legs that rested over that interval. Gaps
        longer than REWARD_ACCRUAL_MAX_GAP_S are not credited."""
        now = time.time()
        last = mk.get("accrual_t")
        mk["accrual_t"] = now
        if last is None or not mk.get("reward"):
            return
        dt = min(max(0.0, now - float(last)), REWARD_ACCRUAL_MAX_GAP_S)
        est = mk.get("reward_est") or {}
        rate = float(est.get("rate_usd_s") or 0.0) if est.get("legs") else 0.0
        day = et_clock(now)[0]
        acc = self.b.d.setdefault("reward_accrual", {})
        row = acc.setdefault(day, {}).setdefault(
            slug, {"est_usd": 0.0, "rest_s": 0.0})
        row["est_usd"] = float(row["est_usd"]) + rate * dt
        if rate > 0:
            row["rest_s"] = float(row["rest_s"]) + dt
        row["rate_usd_s"] = rate
        row["share"] = est.get("share")
        for d in sorted(acc)[:-7]:                    # keep 7 ET days
            acc.pop(d, None)

    def reward_accrual_report(self, now: Optional[float] = None) -> Dict:
        """REWARD_ACCRUAL (THEORETICAL) per market for today (ET) against the
        venue's $1.00 payout floor. projected_eod_usd = est_today + current
        rate * min(seconds to ET midnight, REWARD_PROJECT_HORIZON_S) for an
        active rewarded market (0 for inactive ones). From
        REWARD_FLOOR_CHECK_HOUR_ET on, a market (or the total) projected
        under the floor logs reward_floor_at_risk. Report only."""
        now = time.time() if now is None else now
        day, hour, left = et_clock(now)
        rows = (self.b.d.get("reward_accrual") or {}).get(day) or {}
        active = set(self.active())
        horizon = min(left, REWARD_PROJECT_HORIZON_S)
        floor = float(REWARD_FLOOR_USD)
        mkts = {}
        for slug, r in sorted(rows.items()):
            est = float(r.get("est_usd") or 0.0)
            rate = float(r.get("rate_usd_s") or 0.0) if slug in active \
                else 0.0
            proj = est + rate * horizon
            mkts[slug] = {"est_today_usd": round(est, 6),
                          "rest_s": round(float(r.get("rest_s") or 0.0)),
                          "rate_usd_s": rate, "share": r.get("share"),
                          "projected_eod_usd": round(proj, 6),
                          "floor": ("met" if est >= floor else
                                    "on_track" if proj >= floor else
                                    "at_risk")}
        tot = sum(m["est_today_usd"] for m in mkts.values())
        tot_proj = sum(m["projected_eod_usd"] for m in mkts.values())
        log("REWARD_ACCRUAL", et_day=day, et_hour=hour,
            est_today_usd=round(tot, 6), projected_eod_usd=round(tot_proj, 6),
            floor_usd=floor, floor_met=tot >= floor,
            projection_horizon_s=round(horizon), markets=mkts,
            label="THEORETICAL reward estimate (pool rate x est share x "
                  "resting time); NOT credited, never P&L; venue pays "
                  "nothing under $1.00")
        if hour >= REWARD_FLOOR_CHECK_HOUR_ET:
            for slug, m in mkts.items():
                if m["floor"] == "at_risk":
                    log("reward_floor_at_risk", et_day=day, slug=slug,
                        est_today_usd=m["est_today_usd"],
                        projected_eod_usd=m["projected_eod_usd"],
                        floor_usd=floor, action="report_only_no_size_change",
                        label="THEORETICAL")
            if mkts and tot_proj < floor:
                log("reward_floor_at_risk", et_day=day, slug="ALL",
                    est_today_usd=round(tot, 6),
                    projected_eod_usd=round(tot_proj, 6), floor_usd=floor,
                    action="report_only_no_size_change", label="THEORETICAL")
        return mkts

    def reward_first_rank(self, rewarded: Mapping[str, Any]) -> list:
        """[(slug, estimate)] best first: live paying programs only; our
        REWARD_QTY quote at the touch (pair-cap legal depth) scored against
        the current public book with the venue formula. Our order inside
        the target walk first, then higher THEORETICAL reward rate. Markets
        whose quote would earn nothing are left out."""
        from src import pmus_sports_rewards as R
        cands = sorted(((s, p) for s, p in rewarded.items()
                        if p.get("period") in REWARD_FIRST_PERIODS),
                       key=lambda sp: -float(sp[1]["pool_rate_usd_s"]))
        out = []
        for slug, per in cands[:max(0, REWARD_SCAN_MAX)]:
            try:
                spec = self.v.spec(slug)
                bbo = self.v.bbo(slug) if spec else None
                if not spec or not bbo:
                    continue
                r = reward_open_prices(bbo["bid"], bbo["ask"], spec["tick"],
                                       per.get("maxSpread"),
                                       cap=pair_cap(slug))
                if r is None:
                    continue
                book = (self._reward_get()(f"/v1/markets/{slug}/book") or {}
                        ).get("marketData") or {}
                qty = max(REWARD_QTY, spec["min_qty"])
                est = R.quote_estimate(book, per, float(r[0]["YES"]),
                                       float(r[0]["NO"]), float(qty),
                                       float(spec["tick"]))
            except Exception as exc:                  # noqa: BLE001
                log("reward_rank_error", slug=slug, err=str(exc)[:120])
                continue
            est["depth"] = r[1]
            if est["rate_usd_s"] > 0:
                out.append((slug, est))
        out.sort(key=lambda se: (0 if se[1]["inside_walk"] else 1,
                                 -se[1]["rate_usd_s"], se[0]))
        log("REWARD_FIRST_RANK", candidates=len(cands), scored=len(out),
            top=[{"slug": s, "inside_walk": e["inside_walk"],
                  "share": e["share"], "depth": e["depth"],
                  "pool_rate_usd_s": rewarded[s]["pool_rate_usd_s"],
                  "est_usd_per_hour": e["est_usd_per_hour"],
                  "est_usd_per_day": e["est_usd_per_day"]}
                 for s, e in out[:8]],
            label="THEORETICAL reward ranking; never P&L")
        return out

    def _log_reward(self, slug: str, mk: Dict[str, Any], rp: Mapping,
                    bbo: Mapping, tick: D, r: Optional[tuple]) -> None:
        last = float(mk.get("reward_logged_at") or 0)
        if time.time() - last < REWARD_LOG_S:
            return
        mk["reward_logged_at"] = time.time()
        est: Dict[str, Any] = {"qualifies": None, "est_share": None}
        try:
            from src import pmus_sports_rewards as R
            book = (self._reward_get()(f"/v1/markets/{slug}/book") or {}
                    ).get("marketData") or {}
            est = R.estimate(book, rp, float(REWARD_QTY), float(tick))
        except Exception as exc:                      # noqa: BLE001
            est["book_error"] = str(exc)[:120]
        log("reward_program", slug=slug, program_id=rp.get("programId"),
            pool=rp.get("pool"), period=rp.get("period"),
            period_s=rp.get("period_s"), targetSize=rp.get("targetSize"),
            maxSpread=rp.get("maxSpread"), bid=bbo["bid"], ask=bbo["ask"],
            quote_depth=(r[1] if r else None),
            quote=({k: str(v) for k, v in r[0].items()} if r else None),
            price_within_max_spread=r is not None,
            qualifies=est.get("qualifies"), est_share=est.get("est_share"),
            est=est, label="THEORETICAL reward estimate; never P&L")

    def own(self) -> set:
        return set(self.b.d["own_order_ids"])

    def foreign_exposure(self, slug: str, acct: Mapping[str, Any]) -> bool:
        mine = slug in self.b.d["markets"]
        if acct["positions"].get(slug) and not mine:
            return True
        for o in acct["open_orders"]:
            if o.get("marketSlug") == slug and \
                    str(o.get("id")) not in self.own():
                return True
        return False

    @staticmethod
    def mark_value(mk: Mapping[str, Any], bbo: Mapping[str, D]) -> D:
        """Book value now minus cash paid: matched pairs at $1, a lone leg
        at its own side's bid. MARK-TO-MARKET ESTIMATE."""
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        paid = D(mk["cost"]["YES"]) + D(mk["cost"]["NO"])
        matched = min(y, n)
        val = matched
        if y > n:
            val += (y - n) * D(str(bbo["bid"]))
        elif n > y:
            val += (n - y) * (ONE - D(str(bbo["ask"])))
        return val - paid

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _mark(self, slug: str, mk: Dict[str, Any], bbo: Mapping) -> None:
        """Each market's change is measured from its FIRST mark of the UTC
        day, so a restart or an old lone leg never books yesterday's loss
        as today's."""
        try:
            v = self.mark_value(mk, bbo)
        except Exception:                             # noqa: BLE001
            return
        today = self._today()
        base = mk.get("mark0") or {}
        if base.get("date") != today:
            mk["mark0"] = {"date": today, "value": str(v)}
        mk["mark"] = str(v)
        self.daily_stopped()

    def day_change(self) -> D:
        today = self._today()
        out = D(0)
        for mk in self.b.d["markets"].values():
            base = mk.get("mark0") or {}
            if base.get("date") == today and mk.get("mark") is not None:
                out += D(mk["mark"]) - D(base["value"])
        return out

    def daily_stopped(self) -> bool:
        if DAILY_LOSS_USD <= 0:
            return False
        today = self._today()
        day = self.b.d.setdefault("day", {})
        if day.get("date") != today:
            day.clear()
            day["date"] = today
        change = self.day_change()
        if change < -DAILY_LOSS_USD and not day.get("stopped"):
            day["stopped"] = True
            self.b.save()
            log("daily_loss_stop", date=today, change=change,
                limit=-DAILY_LOSS_USD, label="MARK-TO-MARKET ESTIMATE; no "
                "new openings until next UTC day, completions continue")
        return bool(day.get("stopped"))

    def _gate_stats(self):
        now = time.monotonic()
        hit = getattr(self, "_gate_cache", None)
        if hit and now - hit[0] < 300:
            return hit[1]
        from src import pmus_sports_open_gate as G
        st = G.stats(self.b.d.get("markets") or {})
        self._gate_cache = (now, st)
        return st

    def scope_block(self, slug: str) -> Optional[str]:
        """"competition_not_permitted" when the strict scope is on and this
        market's competition is not on the owner's list; else None."""
        if not STRICT_SPORTS:
            return None
        from src import pmus_sports_open_gate as G
        return None if G.sport_of(slug) in OPEN_GATE_ALLOW_SPORTS \
            else "competition_not_permitted"

    def open_gate(self, slug: str) -> Dict[str, Any]:
        from src import pmus_sports_open_gate as G
        sport = G.sport_of(slug)
        if sport in OPEN_GATE_ALLOW_SPORTS:
            return {"allow": True, "why": "owner_allow_sport", "sport": sport}
        return G.allow_open(sport, self._gate_stats(),
                            min_ev=OPEN_EV_MIN,
                            min_markets=OPEN_EV_MIN_MARKETS)

    def open_gate_snapshot(self) -> Dict[str, Any]:
        from src import pmus_sports_open_gate as G
        st = self._gate_stats()
        out = {"on": OPEN_EV_GATE,
               "allow_sports": sorted(OPEN_GATE_ALLOW_SPORTS)}
        for sp in sorted(k for k in st if k != "*"):
            e = G.expected_open_value(sp, st)
            if e:
                out[sp] = {"ev": str(e["ev"].quantize(D("0.0001"))),
                           "n": str(e["n_sport"])}
        return out

    def halt_new_exposure(self) -> bool:
        if not RECOVERY_ON:
            return False
        from src.pmus_sports_recovery import halt_new_exposure
        stop = halt_new_exposure(self.b.total_unmatched(), UNMATCHED_HALT_USD)
        stranded = self.stranded_count()
        too_many = MAX_STRANDED > 0 and stranded >= MAX_STRANDED
        stop = stop or too_many
        if stop != bool(self.b.d.get("halt_new_exposure")):
            self.b.d["halt_new_exposure"] = stop
            log("HALT_NEW_EXPOSURE" if stop else "resume_new_exposure",
                unmatched_cost=self.b.total_unmatched(),
                limit=UNMATCHED_HALT_USD, stranded=stranded,
                max_stranded=MAX_STRANDED,
                why=("max_stranded" if too_many else "unmatched_cost")
                if stop else None)
        return stop

    def _recovery_resolved(self, slug: str, mk: Dict[str, Any],
                           outcome: str) -> None:
        """Durable record of how each unmatched position ended, for the
        paired %, time-to-completion and age metrics."""
        since = mk.get("lone_since")
        if since is None:
            return
        rec = {"slug": slug, "outcome": outcome, "t": now_iso(),
               "age_s": round(time.time() - float(since)),
               "actions": mk.get("recovery_actions", {})}
        ls = mk.pop("lone_start", None) or {}
        rec["controller"] = ls.get("controller", "legacy")
        rec["open_policy"] = ls.get("open_policy", "two_sided")
        if outcome == "paired" and ls:
            lag = ls["lag"]
            dq = D(mk["pos"][lag]) - D(ls["qty0"])
            if dq > 0:
                paid = (D(mk["cost"][lag]) - D(ls["cost0"])) / dq
                rec["completion_px"] = str(paid)
                rec["start_taker_px"] = ls["taker_px"]
                if ls.get("lead_px") is not None:
                    rec["lead_px"] = ls["lead_px"]
                    rec["pair_cost"] = str(D(ls["lead_px"]) + paid)
                # improved: cheaper than taking at once when it went lone
                rec["improved"] = paid < D(ls["taker_px"])
                if ls.get("opp_quote_px") not in (None, "None"):
                    drift = paid - D(str(ls["opp_quote_px"]))
                    rec["completion_drift_px"] = str(drift)
                    record_drift(self.b.d, slug, drift)
                if ls.get("lead_px") is not None:
                    over = dq * (D(ls["lead_px"]) + paid - ONE)
                    if over > 0:
                        mk["strand_overpay_usd"] = str(D(str(
                            mk.get("strand_overpay_usd") or "0")) + over)
                        rec["market_completion_overpay_usd"] = \
                            mk["strand_overpay_usd"]
        lst = self.b.d.setdefault("recoveries", [])
        lst.append(rec)
        del lst[:-RECOVERY_LOG_KEEP]
        mk.pop("recovery_actions", None)
        mk.pop("recovery_logged", None)
        log("RECOVERY_RESOLVED", **rec)

    def mark_recovery_start(self) -> None:
        """Once, the first time the recovery controller runs: positions
        that went lone before this are LEGACY and are reported apart."""
        if RECOVERY_ON and not self.b.d.get("recovery_since"):
            self.b.d["recovery_since"] = time.time()
            self.b.save()
            log("recovery_controller_activated", since=now_iso())

    def recovery_metrics(self, controller: Optional[str] = None
                         ) -> Dict[str, Any]:
        if controller is None:
            return {"legacy": self.recovery_metrics("legacy"),
                    "recovery": self.recovery_metrics("recovery"),
                    "open": self._open_unmatched()}
        recs = [r for r in (self.b.d.get("recoveries") or [])
                if r.get("controller", "legacy") == controller]
        paired = sorted(r["age_s"] for r in recs if r["outcome"] == "paired")
        q = (lambda xs, f: xs[min(len(xs) - 1, int(f * len(xs)))]
             if xs else None)
        judged = [r for r in recs if "improved" in r]
        return {"resolved": len(recs),
                "paired_pct": (round(100 * len(paired) / len(recs), 1)
                               if recs else None),
                "time_to_pair_s_median": q(paired, 0.5),
                "time_to_pair_s_p95": q(paired, 0.95),
                "improved_vs_immediate_taker": sum(
                    1 for r in judged if r["improved"]),
                "worse_than_immediate_taker": sum(
                    1 for r in judged if not r["improved"]),
                "held_to_close": sum(1 for r in recs
                                     if r["outcome"] == "held_to_close")}

    def _open_unmatched(self) -> Dict[str, Any]:
        now = time.time()
        ages = {"lt_60s": 0, "lt_10m": 0, "lt_1h": 0, "ge_1h": 0}
        notional = D(0)
        for s_ in self.active():
            mk = self.b.m(s_)
            if mk.get("lone_since") is None:
                continue
            a = now - float(mk["lone_since"])
            k = ("lt_60s" if a < 60 else "lt_10m" if a < 600 else
                 "lt_1h" if a < 3600 else "ge_1h")
            ages[k] += 1
            notional += self.b.unmatched_cost(s_)
        return {"by_age": ages, "notional_usd": str(notional)}

    def extend_target(self, slug: str, mk: Mapping[str, Any], bbo: Mapping,
                      tick: D, lead: str, need: D, lead_px: D, qty: D
                      ) -> Dict[str, Any]:
        """Where (if anywhere) a same-side EXTEND bid may rest. Pure apart
        from the guard/gate reads. Never larger than EXTEND_QTY, never past
        EXTEND_MAX_IMBALANCE, only BELOW the average cost by EXTEND_STEP_PX
        (so a later completion can lock a cheaper pair), always a maker
        DEPTH ticks under this side's own bid, and inside every cap."""
        if need + qty > EXTEND_MAX_IMBALANCE:
            return {"px": None, "why": "max_imbalance"}
        if os.getenv(ENABLED_ENV, "").upper() != "YES" or \
                self.daily_stopped() or self.halt_new_exposure() or \
                self.shared_halt() is not None or \
                self.account_risk_blocks() is not None:
            return {"px": None, "why": "blocked"}
        late = late_for(
            slug, (getattr(self.v, "states", {}) or {}).get(slug))
        if late or excluded(slug):
            return {"px": None, "why": f"late:{late or 'excluded'}"}
        lead_bid = side_bid(lead, bbo["bid"], bbo["ask"])
        px = floor_tick(min(lead_bid - depth_ticks(tick) * tick,
                            lead_px - EXTEND_STEP_PX), tick)
        if px < max(tick, BAND_LO):
            return {"px": None, "why": "below_band"}
        add = px * qty
        if self.b.unmatched_cost(slug) + add > EXTEND_MAX_USD:
            return {"px": None, "why": "per_market_usd"}
        if self.b.total_unmatched() + add > TOTAL_CAP:
            return {"px": None, "why": "total_cap"}
        if OPEN_EV_GATE:
            g = self.open_gate(slug)
            if not g["allow"]:
                return {"px": None, "why": "open_ev_gate"}
        if self.guard is not None:
            gv = self.guard.verdict(slug, {lead: px}, tick)
            if not gv["ok"]:
                return {"px": None, "why": "intl_guard:" + gv["why"]}
        return {"px": px, "why": "ok"}

    def _extend(self, slug: str, mk: Dict[str, Any], spec: Mapping,
                bbo: Mapping, tick: D, lead: str, need: D, lead_px: D,
                resting) -> None:
        """OWNER 2026-11-15: keep working a stranded market -- OPEN ->
        EXTEND -> COMPLETE -> new pair -- instead of only waiting. A bounded
        same-side bid below our average cost: if it fills, the average
        drops and the completion (re-sized by the recovery controller) can
        lock a cheaper pair. Fixed size, hard imbalance and dollar caps, so
        it can never become a martingale."""
        qty = max(EXTEND_QTY, spec["min_qty"])
        tg = self.extend_target(slug, mk, bbo, tick, lead, need, lead_px,
                                qty)
        if resting is not None:
            oid, o = resting
            keep = (tg["px"] is not None and
                    tg["px"] - tick <= D(o["px"]) <= tg["px"])
            if keep:
                return
            self.v.cancel(oid)
            st = self.v.status(oid)
            self._record_fill(slug, lead, oid, st)
            o["open"] = st["open"]
            self.b.save()
            log("extend_pulled", slug=slug, side=lead, px=o["px"],
                why=tg["why"], target=str(tg["px"]))
            return
        if tg["px"] is None:
            if mk.get("extend_logged") != tg["why"]:
                mk["extend_logged"] = tg["why"]
                log("extend_skip", slug=slug, side=lead, need=need,
                    lead_px=lead_px, why=tg["why"])
            return
        mk.pop("extend_logged", None)
        log("EXTEND", slug=slug, side=lead, px=tg["px"], qty=qty,
            need_before=need, avg_cost_before=lead_px,
            label="same-side maker add below average cost; bounded")
        self.place(slug, lead, tg["px"], qty, "two_sided_extend")

    def _recover(self, slug: str, mk: Dict[str, Any], spec: Mapping,
                 bbo: Mapping, tick: D, lead: str, lag: str, need: D,
                 lead_px: D, cur, lone_since: float) -> None:
        """ONE decision per loop for an unmatched position, executed through
        the existing order path (exact-id cancel confirmed by a status read
        before any replacement; partial fills booked from that read)."""
        from src import pmus_sports_recovery as RC
        late = bool(is_late((getattr(self.v, "states", {}) or {}).get(slug))
                    ) if LATE_CUTOFF else False
        resting_px = None
        if cur:
            rem = D(cur[1]["qty"]) - D(cur[1]["filled"])
            if rem == need and cur[1].get("stage") != "two_sided_exit":
                resting_px = D(cur[1]["px"])
        if completion_policy_live():
            return self._recover_shared(slug, mk, spec, bbo, tick, lead, lag,
                                        need, lead_px, cur, lone_since, late,
                                        resting_px)
        dec = RC.decide(
            lead=lead, lead_px=lead_px, need=need, bid=bbo.get("bid"),
            ask=bbo.get("ask"), tick=tick, age_s=time.time() - lone_since,
            late=late, resting_px=resting_px,
            taker_allowed=RECOVERY_TAKER,
            taker_chunk=(RECOVERY_TAKER_CHUNK if RECOVERY_TAKER_CHUNK > 0
                         else None),
            min_qty=spec["min_qty"],
            params={"taker_fee_rate": TAKER_FEE_RATE,
                    "lean": RECOVERY_LEAN,
                    # cheap-leg-first episodes: a taker completion only at
                    # a positive net settlement margin (owner 2026-11-17)
                    "taker_max_pair": (
                        D(1) - CHEAP_FIRST_BUFFER
                        if (mk.get("lone_start") or {}).get("open_policy")
                        == "cheap_first" else None)})
        act = dec["action"]
        acts = mk.setdefault("recovery_actions", {})
        key = f"{act}:{dec.get('px')}:{dec.get('qty')}"
        if mk.get("recovery_logged") != key:
            mk["recovery_logged"] = key
            acts[act] = acts.get(act, 0) + 1
            log("RECOVERY_DECISION", slug=slug, lead=lead, lead_px=lead_px,
                need=need, age_s=round(time.time() - lone_since),
                late=late, decision=dec,
                label="model-based choice; values are ESTIMATES relative "
                      "to holding at mid, never P&L")
        if act == "KEEP":
            return
        if cur:
            # anything else replaces or removes the resting order first:
            # exact-id cancel, then book what the venue says it filled
            self.v.cancel(cur[0])
            st = self.v.status(cur[0])
            self._record_fill(slug, lag, cur[0], st)
            cur[1]["open"] = st["open"]
            self.b.save()
            return                       # re-decide on the reconciled book
        if act == "HOLD" or dec["qty"] < spec["min_qty"]:
            return
        if act in ("COMPLETE_TAKER", "PARTIAL_REBALANCE"):
            log("EXIT_LONE_LEG", slug=slug, lead=lead, lead_px=lead_px,
                take_side=lag, take_px=dec["px"], qty=dec["qty"],
                pair_cost=lead_px + dec["px"], recovery=act,
                label="recovery taker; bounded by the engine's lock-loss "
                      "cap; settles at par")
            self.place(slug, lag, dec["px"], dec["qty"], "two_sided_exit",
                       post_only=False)
            return
        self.place(slug, lag, dec["px"], dec["qty"], "two_sided_complete")

    def _recover_shared(self, slug, mk, spec, bbo, tick, lead, lag, need,
                        lead_px, cur, lone_since, late, resting_px) -> None:
        """The SHARED stranded-leg policy (owner 2026-11-18). Maker only:
        rest the completion at the effective pair cap and wait; HOLD
        removes any resting completion. Taker never; REDUCE never."""
        from src import pmus_sports_completion as SC
        dec = SC.decide(lead=lead, lead_px=lead_px, need=need,
                        yes_bid=bbo["bid"], yes_ask=bbo["ask"], tick=tick,
                        late=late, sports_cap=COMPLETION_SPORTS_CAP,
                        game_start=mk.get("game_start"), now=time.time(),
                        stranded_now=self.stranded_count(),
                        caller_max_stranded=MAX_STRANDED or None,
                        resting_px=resting_px)
        act = dec["action"]
        key = f"shared:{act}:{dec.get('px')}"
        if mk.get("recovery_logged") != key:
            mk["recovery_logged"] = key
            acts = mk.setdefault("recovery_actions", {})
            acts[act] = acts.get(act, 0) + 1
            pol = dec.get("policy") or {}
            log("RECOVERY_DECISION", slug=slug, lead=lead, lead_px=lead_px,
                need=need, age_s=round(time.time() - lone_since), late=late,
                controller="shared_policy",
                decision={"action": act, "px": dec.get("px"),
                          "qty": dec.get("qty"), "why": dec.get("why"),
                          "effective_ceiling": dec.get("effective_ceiling"),
                          "stage": (dec.get("stage") or {}).get("stage_name"),
                          "p_win": pol.get("p_win"),
                          "p_win_source": pol.get("p_win_source"),
                          "expected_net_usd": pol.get("expected_net_usd"),
                          "hold_net_usd": pol.get("hold_net_usd")},
                label="shared policy; values MODELLED, never P&L")
        if act == "KEEP":
            return
        if cur:
            self.v.cancel(cur[0])
            st = self.v.status(cur[0])
            self._record_fill(slug, lag, cur[0], st)
            cur[1]["open"] = st["open"]
            self.b.save()
            return                       # re-decide on the reconciled book
        if act == "HOLD" or dec["qty"] < spec["min_qty"]:
            return
        self.place(slug, lag, dec["px"], dec["qty"], "two_sided_complete")

    def stranded_count(self) -> int:
        """Markets currently holding an unpaired (lone) leg."""
        return sum(1 for mk in (self.b.d.get("markets") or {}).values()
                   if mk.get("lone_since") is not None
                   and not mk.get("done"))

    def active(self) -> list:
        return [s for s, mk in self.b.d["markets"].items() if not mk["done"]]

    def slots_used(self) -> int:
        """Markets holding an OPENING slot. LIVE 2026-11-15: four lone-leg
        markets waiting to complete (one for 6h) plus cap-blocked rewarded
        markets filled all MAX_MARKETS slots, so no new market (CFB) could
        be selected. A market with an unmatched leg keeps completing but no
        longer holds a slot; its exposure is still bounded by TOTAL_CAP and
        the whole book by MAX_TRACKED."""
        return sum(1 for s in self.active() if self.b.unmatched_cost(s) == 0)

    def full(self) -> bool:
        return self.slots_used() >= MAX_MARKETS or \
            len(self.active()) >= MAX_TRACKED

    def _record_fill(self, slug: str, side: str, oid: str, st: Dict) -> None:
        mk = self.b.m(slug)
        o = mk["orders"][oid]
        new = st["qty"] - D(o.get("filled", "0"))
        if new > 0 and st["px"] is not None:
            mk["pos"][side] = str(D(mk["pos"][side]) + new)
            mk["cost"][side] = str(D(mk["cost"][side]) + new * st["px"])
            # PER-ORDER COST, for the ownership feed (`fills_doc`). An order
            # that already held fills from before this field existed starts
            # from its limit price for that earlier quantity.
            _had = D(o.get("filled", "0"))
            _base = (D(str(o["fill_cost"])) if o.get("fill_cost") is not None
                     else _had * D(str(o["px"])))
            o["fill_cost"] = str(_base + new * st["px"])
            o["filled"] = str(st["qty"])
            self._markout_open(slug, mk, side, oid, st["px"], new,
                               st.get("t"), o.get("stage"))
            mk.setdefault("last_fill_t", {})[side] = time.time()
            if RN1_NET_RANK:
                self.rn1_net_forget(slug)
            late = None
            try:
                if st.get("t") is not None:
                    late = round(max(0.0, time.time() - float(st["t"])), 1)
            except (TypeError, ValueError):
                late = None
            if o.get("phase"):
                self._phase_count(mk, o["phase"], "fills", 1)
                self._phase_count(mk, o["phase"], "filled_contracts", new)
            log("FILL", slug=slug, side=side, px=st["px"], qty=new,
                order_id=oid, pos=mk["pos"], seen_late_s=late,
                phase=o.get("phase"),
                label="venue-reported fill; not realized until settlement")
            if late is not None and FILL_LATE_LATCH_S > 0 and \
                    late > FILL_LATE_LATCH_S:
                self.latch("fill_seen_late", slug=slug, order_id=oid,
                           side=side, late_s=late,
                           limit_s=FILL_LATE_LATCH_S)

    # ------------------------------------------------ post-fill markout
    #: fills kept per market in the durable book (newest last)
    MARKOUT_KEEP = 60

    def _mids(self):
        from src import pmus_fill_markout as MK
        m = getattr(self, "_mk_mids", None)
        if m is None:
            m = self._mk_mids = MK.Mids()
        return m

    def _markout_open(self, slug: str, mk: Dict[str, Any], side: str,
                      oid: str, px: Any, qty: Any, t_venue: Any,
                      stage: Any) -> None:
        """Start a markout record for one fill. Measurement only: nothing
        here can place, cancel or block an order, and it never raises."""
        try:
            from src import pmus_fill_markout as MK
            seen = time.time()
            # the venue's stamp is used only when it is plausible: not in
            # the future and not older than the history we keep
            t_fill, src = seen, "seen_by_bot"
            if t_venue is not None and \
                    seen - MK.KEEP_S / 2 <= float(t_venue) <= seen + 2:
                t_fill, src = min(float(t_venue), seen), "venue_order_row"
            other = "NO" if side == "YES" else "YES"
            before = D(str(mk["pos"][side])) - D(str(qty)) - \
                D(str(mk["pos"][other]))
            role = {"two_sided_complete": "complete", "rn1_extend": "extend",
                    "rn1_open": "open"}.get(str(stage), str(stage))
            rec = MK.new_record(
                slug=slug, side=side, px=px, qty=qty, order_id=oid,
                t_fill=t_fill, t_seen=seen, t_source=src, mids=self._mids(),
                fair_yes=self.intl_fair(slug), unpaired_before=before,
                role=role)
            rec["phase"] = (mk["orders"].get(oid) or {}).get("phase")
            rows = mk.setdefault("markouts", [])
            rows.append(rec)
            del rows[:-self.MARKOUT_KEEP]
        except Exception as exc:                          # noqa: BLE001
            log("markout_error", slug=slug, where="open", err=str(exc)[:120])

    def _markout_step(self, slug: str, mk: Dict[str, Any],
                      bbo: Optional[Mapping]) -> None:
        """Feed the book just read into the rolling history and decide
        every horizon that is due. One FILL_MARKOUT line per fill, when its
        last horizon is decided."""
        try:
            from src import pmus_fill_markout as MK
            now = time.time()
            if bbo is not None:
                self._mids().add(slug, now, bbo.get("bid"), bbo.get("ask"))
            dirty = False
            for rec in mk.get("markouts") or ():
                if rec.get("logged"):
                    continue
                if MK.resolve(rec, self._mids(), now):
                    dirty = True
                if MK.complete(rec):
                    rec["logged"] = True
                    dirty = True
                    log("FILL_MARKOUT", slug=slug, side=rec["side"],
                        px=rec["px"], qty=rec["qty"],
                        order_id=rec["order_id"], role=rec.get("role"),
                        phase=rec.get("phase"),
                        unpaired_before=rec.get("unpaired_before"),
                        t_source=rec.get("t_source"),
                        seen_after_s=round(rec["t_seen"] - rec["t"], 1),
                        book_before=rec.get("book_before"),
                        fair_before=rec.get("fair_before"),
                        h=rec["h"], label=MK.LABEL)
            if dirty:
                self.b.save()
        except Exception as exc:                          # noqa: BLE001
            log("markout_error", slug=slug, where="step", err=str(exc)[:120])

    def markout_summary(self) -> Dict[str, Any]:
        from src import pmus_fill_markout as MK
        rows = [r for mk in (self.b.d.get("markets") or {}).values()
                for r in (mk.get("markouts") or ())]
        # the book history of a market the bot no longer works is dropped
        # once none of its fills is waiting on a horizon (bounded memory)
        live = set(self.active())
        for slug in list(self._mids()._t):
            if slug in live:
                continue
            mk = (self.b.d.get("markets") or {}).get(slug) or {}
            if all(MK.complete(r) for r in (mk.get("markouts") or ())):
                self._mids().drop(slug)
        return MK.summary(rows)

    def account_fresh(self) -> bool:
        """True unless run() requires freshness and the last good account
        read is missing or older than ACCOUNT_FRESH_S."""
        if not self.require_fresh_account:
            return True
        return self._acct_ok_at is not None and \
            time.monotonic() - self._acct_ok_at <= ACCOUNT_FRESH_S

    def read_account(self) -> Optional[Dict[str, Any]]:
        """One account read. Success refreshes the snapshot; failure (429,
        5xx, network) is logged and returns None -- never raises into the
        loop, so market management still runs."""
        try:
            acc = self.v.account()
        except Exception as exc:                          # noqa: BLE001
            self._acct_fail += 1
            if self._acct_fail == 1 or self._acct_fail % 10 == 0:
                log("account_read_error", consecutive=self._acct_fail,
                    err=f"{type(exc).__name__}:{str(exc)[:160]}",
                    effect="no new markets or openings until a fresh read; "
                           "existing orders are still managed")
            return None
        if self._acct_fail:
            log("account_read_recovered", after_failures=self._acct_fail)
        self._acct_fail = 0
        self._acct_ok_at = time.monotonic()
        self._acct_cache = acc
        self._seed_fill_list(acc)
        return acc

    def _seed_fill_list(self, acc: Mapping[str, Any]) -> None:
        """ACCT_SEEDS_FILL_LIST: a successful account read's open-order
        list becomes the fill poll's current list (same venue route, same
        rows), dated to just before that request was sent. Only a list
        NEWER than the one held replaces it. Never raises."""
        if not (ACCT_SEEDS_FILL_LIST and FILL_LIST_POLL):
            return
        try:
            at = acc.get("open_orders_read")
            rows = acc.get("open_orders")
            if not isinstance(at, Mapping) or not isinstance(rows, list):
                return
            mono, wall = float(at["mono"]), float(at["wall"])
            cur = getattr(self, "_oo", None)
            if cur is not None and float(cur.get("t") or 0) >= mono:
                return
            self._oo = {"t": mono, "wall": wall, "ok": True,
                        "rows": {str(o.get("id")): o for o in rows
                                 if isinstance(o, Mapping) and o.get("id")}}
            st = self._fill_stats()
            st["list_seeded"] = st.get("list_seeded", 0) + 1
        except Exception:                                 # noqa: BLE001
            pass

    def on_sigterm(self) -> None:
        """LIVE 2026-11-22T01:31Z: a SIGTERM arriving while the main loop
        was inside a long pass (the hourly account audit) never reached
        cancel_all_own before the 30 s drain ended, and an own completion
        bid kept resting after the restart. Now the signal stops new
        placements at once and cancels every own open order in its own
        thread; the main loop's exit cancel runs again (idempotent)."""
        self.stop = True
        if self._shutdown_thread is None:
            self._shutdown_thread = threading.Thread(
                target=self.cancel_all_own, args=("sigterm_immediate",),
                name="sigterm-cancel", daemon=True)
            self._shutdown_thread.start()

    def cancel_all_own(self, why: str, keep_completions: bool = False
                       ) -> None:
        """Cancel every own open order by exact id. Each order gets up to
        CANCEL_ATTEMPTS tries with backoff (429s at shutdown left orders
        resting until the watchdog's age limit), inside CANCEL_BUDGET_S.
        No bulk per-market cancel: it would also cancel the owner's manual
        orders in that market. `keep_completions` leaves maker completion
        bids resting (the window's completions-only phase)."""
        with self._cancel_all_lock:
            self._cancel_all_own(why, keep_completions)

    def open_own_orders(self, new_exposure_only: bool = False) -> int:
        return sum(1 for mk in self.b.d["markets"].values()
                   for o in (mk.get("orders") or {}).values()
                   if o.get("open") and not (
                       new_exposure_only and
                       o.get("stage") == "two_sided_complete"))

    # ------------------------------------------------ autonomous safety latch
    def safety_latch(self) -> Optional[Dict[str, Any]]:
        """The active latch, or None. An acknowledgement that names THIS
        latch's id clears it (logged); any other value is ignored."""
        cur = self.b.d.get("safety_latch")
        if not cur:
            return None
        ack = (os.getenv(SAFETY_LATCH_ACK_ENV) or "").strip()
        if ack and ack == str(cur.get("id")):
            self.b.d.setdefault("safety_latch_history", []).append(
                dict(cur, cleared_at=now_iso()))
            self.b.d["safety_latch"] = None
            self.b.save()
            log("SAFETY_LATCH_CLEARED", id=cur.get("id"), why=cur.get("why"),
                label="owner acknowledged this latch by its id")
            return None
        return cur

    def latch(self, why: str, **detail: Any) -> None:
        """Stop this bot: durable, cancels everything, refuses everything.
        Idempotent -- the FIRST reason is kept."""
        if not SAFETY_LATCH_ON:
            log("SAFETY_LATCH_DISABLED_WOULD_LATCH", why=why, **detail)
            return
        if self.b.d.get("safety_latch"):
            return
        cur = {"id": now_iso(), "why": why,
               "detail": {k: str(v) for k, v in detail.items()}}
        self.b.d["safety_latch"] = cur
        self.b.save()
        log("SAFETY_LATCH", id=cur["id"], why=why, **cur["detail"],
            effect="every own order is cancelled and every placement "
                   "refused until the owner sets %s to this id; held "
                   "positions are kept and reconciled" % SAFETY_LATCH_ACK_ENV)

    # ---- fill detection from the open-order list (FILL_LIST_POLL) -------
    _FILLED_KEYS = ("cumQuantity", "cumQuantityDecimal", "cumQty",
                    "filledQuantity", "filledQuantityDecimal")

    def _exact_read(self, oid: str) -> None:
        ex = getattr(self, "_exact_at", None)
        if ex is None:
            ex = self._exact_at = {}
        ex[oid] = time.monotonic()
        st = self._fill_stats()
        st["exact_reads"] += 1
        if len(ex) > 5000:                      # closed orders drop out
            keep = sorted(ex, key=ex.get)[-1000:]
            self._exact_at = {k: ex[k] for k in keep}

    def _fill_stats(self) -> Dict[str, int]:
        st = getattr(self, "_fill_poll", None)
        if st is None:
            st = self._fill_poll = {"list_reads": 0, "list_errors": 0,
                                    "exact_reads": 0, "skipped": 0}
        return st

    def _fill_list(self) -> Optional[Dict[str, Any]]:
        """The current open-order list, read at most once per
        FILL_LIST_REFRESH_S; None when it cannot be had (then every order
        is read exactly, as before)."""
        now = time.monotonic()
        cur = getattr(self, "_oo", None)
        if cur is not None and now - cur["t"] < FILL_LIST_REFRESH_S:
            return cur if cur.get("ok") else None
        st = self._fill_stats()
        try:
            wall = time.time()
            got = self.v.open_orders()
            st["list_reads"] += 1
            rows = got["rows"]
            if rows and not getattr(self, "_oo_keys_logged", False):
                self._oo_keys_logged = True
                log("fill_list_row_keys",
                    keys=sorted(str(k) for k in next(iter(rows.values()))),
                    label="field NAMES of one open-order row, logged once")
            self._oo = {"t": now, "wall": wall, "rows": rows, "ok": True}
            return self._oo
        except Exception as exc:                          # noqa: BLE001
            st["list_errors"] += 1
            self._oo = {"t": now, "ok": False}
            if now - getattr(self, "_oo_err_at", -1e9) > 60:
                self._oo_err_at = now
                log("fill_list_error", err=type(exc).__name__,
                    effect="every open order is read exactly this pass")
            return None

    def _row_filled(self, row: Mapping[str, Any]) -> Optional[D]:
        """The filled quantity a list row states, or None when it states
        none in a form this can read (then nothing is proved)."""
        try:
            for k in self._FILLED_KEYS:
                if row.get(k) is not None and str(row.get(k)).strip() != "":
                    v = D(str(row.get(k)))
                    return v if v.is_finite() and v >= 0 else None
            q = next((row.get(k) for k in ("quantity", "quantityDecimal",
                                           "qty") if row.get(k) is not None),
                     None)
            lv = next((row.get(k) for k in ("leavesQuantity",
                                            "leavesQuantityDecimal",
                                            "leavesQty")
                       if row.get(k) is not None), None)
            if q is not None and lv is not None:
                v = D(str(q)) - D(str(lv))
                return v if v.is_finite() and v >= 0 else None
        except Exception:                                 # noqa: BLE001
            return None
        return None

    def fill_list_proves_unchanged(self, oid: str,
                                   o: Mapping[str, Any]) -> bool:
        """True only when the venue's open-order list proves this order is
        still resting with nothing filled beyond what the book holds (see
        FILL_LIST_POLL). Any doubt is False: the caller reads it exactly."""
        if not FILL_LIST_POLL:
            return False
        try:
            last = (getattr(self, "_exact_at", None) or {}).get(oid)
            if last is None or \
                    time.monotonic() - last > FILL_EXACT_EVERY_S:
                return False
            lst = self._fill_list()
            if lst is None or \
                    time.monotonic() - lst["t"] > FILL_LIST_MAX_AGE_S:
                return False
            placed = o.get("placed_at")
            if placed is None or \
                    float(placed) > lst["wall"] - FILL_LIST_SETTLE_S:
                return False
            row = lst["rows"].get(str(oid))
            if row is None:
                return False     # gone, or on a page this read lacks
            state = str(row.get("state") or row.get("status") or "").upper()
            if any(x in state for x in ("CANCEL", "PENDING", "FILLED",
                                        "REJECT", "EXPIRED", "PARTIAL")):
                return False
            got = self._row_filled(row)
            if got is None or got != D(str(o.get("filled") or "0")):
                return False
            self._fill_stats()["skipped"] += 1
            return True
        except Exception:                                 # noqa: BLE001
            return False

    def refresh_fills(self, slug: str) -> None:
        """Re-read every own open order of one market from the venue now
        (fills are otherwise learned on the market's next step)."""
        mk = self.b.d["markets"].get(slug) or {}
        for oid, o in list((mk.get("orders") or {}).items()):
            if not o.get("open"):
                continue
            st = self.v.status(oid)
            self._record_fill(slug, o["side"], oid, st)
            o["open"] = st["open"]

    @staticmethod
    def _book_net(mk: Mapping[str, Any]) -> D:
        return abs(D(str(mk["pos"]["YES"])) - D(str(mk["pos"]["NO"])))

    def _venue_net_complete(self, slug: str) -> Optional[D]:
        """|venue position| from a COMPLETE positions walk, else None."""
        from src import pmus_sports_inventory as INV
        try:
            snap = self.v.positions_snapshot()
        except Exception:                                 # noqa: BLE001
            return None
        if not snap.get("complete"):
            return None
        for row in snap["rows"]:
            pr = INV.parse_row(row)
            if pr["slug"] == slug:
                return abs(D(str(pr["net"] or 0)))
        return D(0)

    def safety_check(self, acct: Optional[Mapping[str, Any]]) -> None:
        """Every account read (~20 s). Book-only invariants first, then the
        venue's positions against the book. Never raises into the loop."""
        if self.safety_latch():
            return
        markets = self.b.d.get("markets") or {}
        if RN1.ON:
            from src import pmus_pilot_pnl as PP
            spend = unm = D(0)
            for slug in PP.pilot_slugs(markets, self.since):
                mk = markets[slug]
                if mk.get("settle_px") is not None:
                    continue
                c = D(str(mk["cost"]["YES"])) + D(str(mk["cost"]["NO"]))
                u = RN1.unmatched_usd(mk["pos"], mk["cost"])
                if c > RN1.PER_MARKET_USD + _CAP_TOL or \
                        u > RN1.RESIDUAL_CAP_USD + _CAP_TOL:
                    return self.latch("market_cap_breached", slug=slug,
                                      spend=c, unmatched=u)
                spend += c
                unm += u
            if spend > RN1.TOTAL_SPEND_USD + _CAP_TOL or \
                    unm > RN1.TOTAL_UNMATCHED_USD + _CAP_TOL:
                return self.latch("campaign_cap_breached", spend=spend,
                                  unmatched=unm)
            for slug, mk in markets.items():
                for oid, o in (mk.get("orders") or {}).items():
                    if o.get("open") and D(str(o["qty"])) > rn1_max_clip():
                        return self.latch("order_qty_exceeds_clip",
                                          slug=slug, order_id=oid,
                                          qty=o["qty"])
        if acct is None:
            return
        pos = acct.get("positions") or {}
        now = time.time()
        for slug, mk in list(markets.items()):
            if mk.get("done"):
                continue
            book = self._book_net(mk)
            seen = abs(D(str(pos.get(slug) or 0)))
            ok = mk.get("venue_net_ok") or {}
            if abs(book - seen) <= D("0.0001") or (
                    ok.get("book") == str(book) and
                    now - float(ok.get("t") or 0) < MISMATCH_CONFIRMED_TTL_S):
                mk.pop("mismatch_since", None)
                continue
            if not mk.get("mismatch_since"):
                # most often our own fill, not yet read: read it now
                try:
                    self.refresh_fills(slug)
                except Exception:                         # noqa: BLE001
                    pass
                book = self._book_net(mk)
                if abs(book - seen) <= D("0.0001"):
                    continue
                mk["mismatch_since"] = now
                log("position_mismatch_suspected", slug=slug, book_net=book,
                    venue_net_first_page=seen, grace_s=MISMATCH_GRACE_S)
                continue
            age = now - float(mk["mismatch_since"])
            if age < MISMATCH_GRACE_S:
                continue
            try:
                self.refresh_fills(slug)
            except Exception:                             # noqa: BLE001
                pass
            book = self._book_net(mk)
            full = self._venue_net_complete(slug)
            if full is None:
                if age >= 3 * MISMATCH_GRACE_S:
                    return self.latch("position_unverifiable", slug=slug,
                                      book_net=book, age_s=int(age))
                continue
            if abs(book - full) <= D("0.0001"):
                mk.pop("mismatch_since", None)
                mk["venue_net_ok"] = {"book": str(book), "t": now}
                continue
            return self.latch("position_mismatch", slug=slug, book_net=book,
                              venue_net=full, age_s=int(age))

    # --------------------------------------- runtime campaign authorization
    def start_campaign_reader(self) -> None:
        if not (runtime_mode() or CAMPAIGN_AUTH_REPORT) or \
                getattr(self, "_camp_reader", None) is not None:
            return
        from src import pmus_campaign_auth as CA
        campaign_ceilings()                  # before anything is applied
        self._camp_reader = CA.FileReader(
            (os.getenv(CAMPAIGN_AUTH_URL_ENV) or "").strip(),
            bearer=(os.getenv(CAMPAIGN_AUTH_BEARER_ENV) or "").strip(),
            accept=(os.getenv(CAMPAIGN_AUTH_ACCEPT_ENV) or "").strip(),
            every_s=CAMPAIGN_AUTH_POLL_S).start()
        log("campaign_auth_reader_started", poll_s=CAMPAIGN_AUTH_POLL_S,
            max_age_s=CAMPAIGN_AUTH_MAX_AGE_S,
            url_set=bool((os.getenv(CAMPAIGN_AUTH_URL_ENV) or "").strip()),
            secret_set=bool((os.getenv(CAMPAIGN_AUTH_SECRET_ENV)
                             or "").strip()),
            ceilings={k: str(v) for k, v in campaign_ceilings().items()
                      if not k.startswith("_")},
            label="trading is permitted only by a signed, unexpired "
                  "campaign file read just now; no file = locked")

    def _campaign_store(self) -> Dict[str, Any]:
        st = self.b.d.get("campaign_auth")
        if not isinstance(st, dict):
            st = self.b.d["campaign_auth"] = {"active": None, "ended": {}}
        st.setdefault("active", None)
        st.setdefault("ended", {})
        return st

    def refresh_campaign_auth(self, force: bool = False
                              ) -> Optional[Dict[str, Any]]:
        """Evaluate the campaign file and publish the decision the lock
        reads. No-op unless the lock value is `runtime`."""
        report_only = not runtime_mode()
        if report_only and getattr(self, "_camp_reader", None) is None:
            return None
        nowm = time.monotonic()
        last = getattr(self, "_camp_eval", None)
        if not force and last and nowm - last[0] < 1.0:
            if report_only:
                return None
            _RUNTIME.update(at=nowm, decision=last[1])
            return last[1]
        from src import pmus_campaign_auth as CA
        ceil = {k: v for k, v in campaign_ceilings().items()
                if not k.startswith("_")}
        st = self._campaign_store()
        act = st["active"] if isinstance(st.get("active"), dict) else None
        reader = getattr(self, "_camp_reader", None)
        snap = reader.snapshot() if reader is not None else {
            "raw": None, "age_s": None, "err": "AUTH_READER_NOT_STARTED",
            "missing": False}
        utc = datetime.now(timezone.utc)
        try:
            d = CA.decide(
                snap, max_age_s=CAMPAIGN_AUTH_MAX_AGE_S,
                secret=(os.getenv(CAMPAIGN_AUTH_SECRET_ENV) or ""),
                service=((os.getenv(CAMPAIGN_AUTH_SERVICE_ENV) or "").strip()
                         or (os.getenv("RAILWAY_SERVICE_NAME") or "").strip()),
                build=(os.getenv("RAILWAY_GIT_COMMIT_SHA") or "").strip(),
                ceilings=ceil, now=utc, ended_ids=list(st["ended"]),
                active=act)
        except Exception as exc:                          # noqa: BLE001
            d = {"permit": False, "campaign": None,
                 "reason": "AUTH_EVAL_ERROR:%s" % type(exc).__name__}
        camp = d.get("campaign")
        if report_only:
            # shown, never acted on: no state, no values, no permission
            self._camp_eval = (nowm, d)
            return None
        changed = False

        def end(cid: str, why: str) -> None:
            nonlocal changed, act
            if cid in st["ended"]:
                return
            st["ended"][cid] = {"why": why, "t": utc.isoformat()}
            if act and act.get("id") == cid:
                st["active"] = act = None
            changed = True
            log("CAMPAIGN_ENDED", campaign_id=cid, why=why,
                label="this campaign id is never accepted again")

        halt = self.shared_halt()
        if halt == "HALT":
            # precedence: a latched halt ends the campaign in force and any
            # campaign that could otherwise run now; neither comes back
            if act:
                end(str(act["id"]), "emergency_halt")
            if camp and d.get("reason") in (None, "AUTH_NOT_STARTED"):
                end(str(camp["campaign_id"]), "emergency_halt")
            d = dict(d, permit=False, reason="EMERGENCY_HALT")
        elif halt is not None:
            d = dict(d, permit=False, reason="HALT_STATE_UNAVAILABLE")
        else:
            if act:
                aid = str(act["id"])
                if time.time() >= float(act.get("end") or 0):
                    end(aid, "expired")
                elif d.get("reason") == "AUTH_FILE_ABSENT":
                    end(aid, "revoked:file_removed")
                elif d.get("permit") and camp and \
                        camp["campaign_id"] != aid:
                    end(aid, "revoked_by:%s" % camp["campaign_id"])
            if d.get("permit") and camp and st["active"] is None:
                if camp["campaign_id"] in st["ended"]:
                    d = dict(d, permit=False, reason="AUTH_CAMPAIGN_ENDED")
                else:
                    st["active"] = act = {
                        "id": camp["campaign_id"], "digest": camp["digest"],
                        "start": camp["start"], "end": camp["end"]}
                    self.b.d["campaign_window"] = [camp["start"], camp["end"]]
                    self.b.d["campaign_since"] = camp["start"]
                    self.since = float(camp["start"])
                    changed = True
                    log("CAMPAIGN_STARTED", **{k: camp[k] for k in camp
                                               if k != "digest"},
                        label="values in force are these or the deployed "
                              "ceilings, whichever is lower")
        if d.get("permit") and act and float(self.since or 0) != \
                float(act["start"]):
            self.since = float(act["start"])     # restart: original start
        apply_campaign(camp if d.get("permit") else None)
        if changed:
            self.b.save()
        key = (d.get("permit"), d.get("reason"),
               (camp or {}).get("campaign_id"))
        if key != getattr(self, "_camp_key", None):
            self._camp_key = key
            log("CAMPAIGN_AUTH", permit=bool(d.get("permit")),
                reason=d.get("reason"),
                campaign_id=(camp or {}).get("campaign_id"),
                expires=(camp or {}).get("expires"),
                active_id=(act or {}).get("id"),
                snapshot_age_s=snap.get("age_s"), fetch_error=snap.get("err"),
                detail={k: d[k] for k in ("field", "fields", "outside",
                                          "active_id") if k in d})
        self._camp_eval = (nowm, d)
        _RUNTIME.update(at=nowm, decision=d)
        return d

    def campaign_status(self) -> Dict[str, Any]:
        """For the heartbeat: which campaign is in force and, if none, why
        not. Computed whatever the lock value is, so a locked deployment
        shows what the file WOULD decide (it permits nothing while locked
        with `1`)."""
        if not runtime_mode() and getattr(self, "_camp_reader", None) is None:
            return {"mode": "off"}
        d = (getattr(self, "_camp_eval", None) or (0, {}))[1] or {}
        c = d.get("campaign") or {}
        st = self._campaign_store()
        return {"mode": "runtime" if runtime_mode() else "report_only",
                "permit": bool(d.get("permit")), "reason": d.get("reason"),
                "campaign_id": c.get("campaign_id"),
                "expires": c.get("expires"),
                "active_id": (st.get("active") or {}).get("id"),
                "ended": len(st.get("ended") or {}),
                "in_force": {"max_markets": MAX_MARKETS,
                             "order_qty": str(rn1_max_clip()),
                             "competitions": sorted(OPEN_GATE_ALLOW_SPORTS),
                             "slate": slate()}}

    def enforce_lock(self) -> Optional[str]:
        """The lock acts on what is ALREADY resting, not only on new
        placements: when the window ends (or the lock is on) every own
        order the lock forbids is cancelled by exact id, each pass until
        none is left. Logs each change of lock state once. The safety
        latch comes first and overrides every lock value."""
        self.refresh_campaign_auth()
        lt = self.safety_latch()
        if lt:
            if self.open_own_orders():
                self.cancel_all_own("safety_latch:" + str(lt.get("why")))
            return "safety_latch:" + str(lt.get("why"))
        st = lock_state()
        if st["why"] != getattr(self, "_lock_why", "?"):
            self._lock_why = st["why"]
            log("SUBMISSION_LOCK_STATE", new_exposure_locked=st["new"],
                completions_locked=st["all"], why=st["why"],
                window=({"start": _iso(st["start"]), "end": _iso(st["end"]),
                         "complete_grace_s": WINDOW_COMPLETE_GRACE_S}
                        if "start" in st else None),
                campaign_since=self.since)
        if st["all"] and self.open_own_orders():
            self.cancel_all_own("submission_lock:" + str(st["why"]))
        elif st["new"] and self.open_own_orders(new_exposure_only=True):
            self.cancel_all_own("submission_lock:" + str(st["why"]),
                                keep_completions=True)
        return st["why"]

    def _cancel_all_own(self, why: str, keep_completions: bool = False
                        ) -> None:
        t0 = time.monotonic()
        left = []
        for slug, mk in list(self.b.d["markets"].items()):
            for oid, o in list(mk["orders"].items()):
                if not o.get("open"):
                    continue
                if keep_completions and \
                        o.get("stage") == "two_sided_complete":
                    continue
                err = None
                for attempt in range(max(1, CANCEL_ATTEMPTS)):
                    try:
                        self.v.cancel(oid)
                        st = self.v.status(oid)
                        self._record_fill(slug, o["side"], oid, st)
                        o["open"] = st["open"]
                        err = None
                        if not st["open"]:
                            break
                    except Exception as exc:          # noqa: BLE001
                        err = str(exc)[:160]
                    if time.monotonic() - t0 > CANCEL_BUDGET_S:
                        break
                    self._sleep(min(4.0, 0.5 * (2 ** attempt)))
                if err is not None or o.get("open"):
                    left.append(oid)
                    log("cancel_error", slug=slug, order_id=oid, err=err,
                        still_open=bool(o.get("open")))
        self.b.save()
        log("cancelled_all_own", why=why, still_open=left)

    def shared_halt(self) -> Optional[str]:
        """None = permitted; "HALT" = the watchdog asserted the shared halt;
        "UNAVAILABLE" = configured but unreadable/stale. Not configured ->
        None (logged once), the behavior before this check existed."""
        url = (os.getenv(SHARED_HALT_URL_ENV) or "").strip()
        if not url:
            if not getattr(self, "_halt_unset_logged", False):
                self._halt_unset_logged = True
                log("shared_halt_not_configured", env=SHARED_HALT_URL_ENV)
            return None
        now = time.monotonic()
        hit = getattr(self, "_halt_cache", None)
        if hit and now - hit[0] < SHARED_HALT_TTL_S:
            return hit[1]
        try:
            from src import l5_halt_gate as HG
            st = HG.HaltGate(HG.HttpHaltStore(url),
                             max_age_s=SHARED_HALT_MAX_AGE_S).state()
            why = st.get("why")
            val = None if st.get("permitted") else (
                "HALT" if why in ("halt_asserted", "env_halt")
                else "UNAVAILABLE")
        except Exception as exc:                          # noqa: BLE001
            why, val = f"{type(exc).__name__}", "UNAVAILABLE"
        if val != (hit[1] if hit else "init"):
            log("shared_halt", state=val or "PERMITTED", why=why)
        self._halt_cache = (now, val)
        return val

    # ------------------------------------- pre-game sharp mode (SHADOW)
    def sharp_candidates(self, now: float) -> List[Mapping[str, Any]]:
        """Pre-game moneyline markets in the sharp window, from the public
        gateway (no key). Only SHARP_SPORTS with a known odds sport key."""
        from src import pmus_sports_sharp as SH
        lo = datetime.fromtimestamp(now + SH.CUTOFF_S, timezone.utc)
        hi = datetime.fromtimestamp(now + SH.MAX_LEAD_S, timezone.utc)
        d = self.v.gget("/v1/events?active=true&closed=false&categories=sports"
                        "&limit=500&startDateMin=%s&startDateMax=%s" % (
                            lo.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            hi.strftime("%Y-%m-%dT%H:%M:%SZ")))
        evs = d.get("events") if isinstance(d, Mapping) else d
        # the same listing feeds the soccer refresher (no second request)
        self._sharp_events = evs if isinstance(evs, list) else []
        out = []
        for ev in evs or []:
            for m in (ev.get("markets") or []) if isinstance(ev, Mapping) \
                    else []:
                if not isinstance(m, Mapping):
                    continue
                slug = str(m.get("slug") or "")
                code = SH.sport_code(slug)
                if not slug.startswith("aec-") or code not in SHARP_SPORTS \
                        or code not in SH.SPORT_KEYS:
                    continue
                if str(m.get("sportsMarketType") or "") == "futures":
                    continue
                if SH.market_teams(m) is None:
                    continue
                if not SH.in_window(m.get("gameStartTime"), now):
                    continue
                out.append(m)
        return out[:SHARP_MAX_MARKETS]

    def sharp_shadow_tick(self, now: Optional[float] = None) -> Dict[str, Any]:
        """SHADOW ONLY: what the sharp mode would quote now, plus MODELLED
        touch-fills of the previous tick's would-quotes. Places nothing."""
        from src import pmus_sports_sharp as SH
        now = time.time() if now is None else now
        if getattr(self, "_odds", None) is None:
            self._odds = SH.OddsClient()
            if os.getenv("PMUS_SHARP_COVERAGE_LOG", "0").strip() == "1":
                # read-only, once per process: what the provider offers for
                # our allowlist and its measured quota cost (issue #251)
                try:
                    log("SHARP_COVERAGE", **self._odds.coverage())
                except Exception as exc:                  # noqa: BLE001
                    log("SHARP_COVERAGE", ok=False,
                        why=type(exc).__name__)
        state = self.b.d.setdefault("sharp_shadow", {})
        rows, fills = 0, 0
        cands = self.sharp_candidates(now)
        by_sport: Dict[str, List[Mapping[str, Any]]] = {}
        for m in cands:
            by_sport.setdefault(SH.sport_code(m["slug"]), []).append(m)
        for code, ms in by_sport.items():
            events = self._odds.fetch(SH.SPORT_KEYS[code])
            for m in ms:
                slug = m["slug"]
                fy = SH.fair_yes(m, events, now=now)
                bbo = self.v.bbo(slug)
                prev = state.get(slug) or {}
                # MODELLED fills of what we WOULD have had resting
                if bbo and prev.get("q"):
                    pq = prev["q"]
                    hit = []
                    if pq.get("YES") is not None and \
                            D(str(bbo["ask"])) <= D(str(pq["YES"])):
                        hit.append(("YES", pq["YES"]))
                    if pq.get("NO") is not None and \
                            ONE - D(str(bbo["bid"])) <= D(str(pq["NO"])):
                        hit.append(("NO", pq["NO"]))
                    for side, px in hit:
                        fills += 1
                        log("SHARP_SHADOW_FILL", slug=slug, side=side, px=px,
                            fair_yes_at_quote=prev.get("fair_yes"),
                            fair_yes_now=None if fy is None
                            else str(fy["fair_yes"]),
                            label="MODELLED touch-fill of a shadow quote; "
                                  "nothing was placed; not P&L")
                if fy is None or not bbo:
                    diag = {}
                    if fy is None and os.getenv(
                            "PMUS_SHARP_COVERAGE_LOG", "0").strip() == "1":
                        diag = dict(SH.diagnose(m, events, now=now),
                                    feed_cache_age_s=self._odds.cache_age_s(
                                        SH.SPORT_KEYS[code]))
                    log("SHARP_SHADOW", slug=slug, sport=code,
                        start=m.get("gameStartTime"),
                        skipped="no_fair" if fy is None else "no_book",
                        **({"diag": diag} if diag else {}))
                    state.pop(slug, None)
                    continue
                tick = (self.v.spec(slug) or {}).get("tick") or D("0.01")
                q = SH.quote(fy["fair_yes"], bbo["bid"], bbo["ask"], tick)
                mid = (D(str(bbo["bid"])) + D(str(bbo["ask"]))) / 2
                log("SHARP_SHADOW", slug=slug, sport=code,
                    start=m.get("gameStartTime"),
                    fair_yes=str(round(fy["fair_yes"], 4)),
                    source=fy["source"], odds_age_s=fy["age_s"],
                    bid=bbo["bid"], ask=bbo["ask"],
                    mid_minus_fair=str(round(mid - fy["fair_yes"], 4)),
                    mode=q["mode"], yes_px=q["YES"], no_px=q["NO"],
                    pair=q["pair"], why=q["why"],
                    label="SHADOW: would-quote only; nothing placed")
                rows += 1
                state[slug] = {"t": now, "fair_yes": str(fy["fair_yes"]),
                               "odds_age_s": fy.get("age_s"),
                               "source": fy.get("source"),
                               "q": {"YES": None if q["YES"] is None
                                     else str(q["YES"]),
                                     "NO": None if q["NO"] is None
                                     else str(q["NO"])}}
        if SHARP_LINES:
            rows += self.sharp_line_shadow(cands, by_sport, state, now)
        if SHARP_SOCCER:
            rows += self.sharp_soccer_shadow(state, now)
        for slug in [k for k, v in state.items()
                     if now - float(v.get("t") or 0) > 6 * 3600]:
            state.pop(slug, None)
        st = dict(self._odds.status(), candidates=len(cands), rows=rows,
                  modelled_fills=fills)
        log("SHARP_STATUS", **st)
        return st

    # ------------------------------------------------ line markets (shadow)
    def sharp_line_events(self, siblings: List[Mapping[str, Any]]) -> None:
        """Hand the moneyline candidates to the background line refresher
        (started once). The loop never fetches event listings itself."""
        self._line_want = {str(m.get("slug")): dict(m) for m in siblings
                           if str(m.get("slug") or "").startswith("aec-")}
        if getattr(self, "_line_thread", None) is None:
            self._line_cache = getattr(self, "_line_cache", {})
            th = threading.Thread(target=self._line_refresh_loop,
                                  name="sharp-lines", daemon=True)
            self._line_thread = th
            th.start()

    def _line_refresh_loop(self) -> None:
        from src import pmus_sports_line_fair as LF
        while not getattr(self, "stop", False):
            for sib, m in list((getattr(self, "_line_want", None) or {}
                                ).items()):
                ev = sib[4:]
                hit = self._line_cache.get(ev)
                if hit and time.time() - hit["t"] < SHARP_LINE_REFRESH_S:
                    continue
                try:
                    d = self.v.gget("/v1/events?slug=%s" % ev)
                    evs = d.get("events") if isinstance(d, Mapping) else None
                    ms = [x for e in (evs or []) if isinstance(e, Mapping)
                          for x in e.get("markets") or []
                          if isinstance(x, Mapping) and str(
                              x.get("sportsMarketType") or "") in
                          LF.LINE_TYPES]
                    self._line_cache[ev] = {"t": time.time(), "markets": ms,
                                            "sibling": m}
                except Exception as exc:                  # noqa: BLE001
                    log("sharp_line_refresh_error", event=ev,
                        err=type(exc).__name__)
                time.sleep(SHARP_LINE_GAP_S)
            time.sleep(max(5.0, SHARP_LINE_REFRESH_S / 10))

    def sharp_line_shadow(self, cands: List[Mapping[str, Any]],
                          by_sport: Mapping[str, List[Mapping[str, Any]]],
                          state: Dict[str, Any], now: float) -> int:
        """Exact full-game spread / game total fairs (pmus_sports_line_fair)
        from the odds ALREADY fetched this tick for the sibling moneyline's
        sport; stored in the same sharp_shadow state the trading-first gate
        reads (RN1_DIR_SHARP). Logs every refusal reason. Places nothing."""
        from src import pmus_sports_line_fair as LF
        from src import pmus_sports_sharp as SH
        self.sharp_line_events(cands)
        rows = 0
        why_n: Dict[str, int] = {}
        for code, ms in by_sport.items():
            events = self._odds.cache.get(SH.SPORT_KEYS[code],
                                          (0, []))[1]
            for sib in ms:
                hit = (getattr(self, "_line_cache", None) or {}).get(
                    str(sib.get("slug"))[4:])
                for lm in (hit or {}).get("markets") or []:
                    f, why = LF.line_market_fair(lm, sib, events, now=now)
                    why_n[why] = why_n.get(why, 0) + 1
                    slug = str(lm.get("slug"))
                    if f is None:
                        state.pop(slug, None)
                        continue
                    rows += 1
                    state[slug] = {"t": now, "fair_yes": str(f["fair_yes"]),
                                   "odds_age_s": f.get("age_s") or 0.0,
                                   "source": f["source"], "kind": f["kind"],
                                   "point": f["point"],
                                   "sibling": str(sib.get("slug")),
                                   "q": {"YES": None, "NO": None}}
                    self._line_targets = getattr(self, "_line_targets", {})
                    self._line_targets[slug] = {
                        "slug": slug, "game_start": parse_start(
                            sib.get("gameStartTime")),
                        "market_meta": {k: lm.get(k) for k in
                                        ("bestBidQuote", "bestAskQuote")
                                        if lm.get(k) is not None}}
        log("SHARP_LINES", priced=rows, reasons=why_n,
            events_cached=len(getattr(self, "_line_cache", {}) or {}),
            label="exact full-game spread/total fairs (shadow state); "
                  "nothing placed")
        return rows

    # ------------------------------------------ soccer three-way (shadow)
    def sharp_soccer_events(self, now: float) -> Dict[str, Any]:
        """{event slug: {"markets": [...], "sport_key"}} for pre-game soccer
        events in the sharp window, from the listing sharp_candidates just
        read (no request). Only leagues in SOCCER_KEYS."""
        from src import pmus_sports_sharp as SH
        from src import pmus_sports_soccer_fair as SF
        out: Dict[str, Any] = {}
        for ev in getattr(self, "_sharp_events", None) or []:
            if not isinstance(ev, Mapping):
                continue
            ms = [m for m in ev.get("markets") or [] if isinstance(m, Mapping)
                  and str(m.get("sportsMarketType") or "") == SF.SOCCER_TYPE]
            if not ms:
                continue
            mo = SF.match_of(ms[0].get("slug"))
            if mo is None or mo["league"] not in SF.SOCCER_KEYS or \
                    not SH.in_window(ms[0].get("gameStartTime"), now):
                continue
            out[mo["match"]] = {"markets": [dict(m) for m in ms],
                                "sport_key": SF.SOCCER_KEYS[mo["league"]]}
        return out

    def _soccer_refresh_loop(self) -> None:
        """Background: fetch each wanted soccer league's odds (shared
        OddsClient: same cache, quota and lock) and price every outcome
        market. Results are handed to the loop through _soccer_fairs."""
        from src import pmus_sports_soccer_fair as SF
        while not getattr(self, "stop", False):
            want = dict(getattr(self, "_soccer_want", None) or {})
            keys = sorted({v["sport_key"] for v in want.values()})
            odds = {}
            for k in keys:
                try:
                    odds[k] = self._odds.fetch(k)
                except Exception as exc:                  # noqa: BLE001
                    log("sharp_soccer_fetch_error", key=k,
                        err=type(exc).__name__)
                    odds[k] = []
            now = time.time()
            res: Dict[str, Any] = {}
            for ev, v in want.items():
                for m in v["markets"]:
                    try:
                        f, why = SF.soccer_market_fair(
                            m, v["markets"], odds.get(v["sport_key"]) or [],
                            now=now)
                    except Exception as exc:              # noqa: BLE001
                        f, why = None, "error:%s" % type(exc).__name__
                    res[str(m.get("slug"))] = {
                        "t": now, "why": why, "fair": f,
                        "game_start": m.get("gameStartTime")}
            self._soccer_fairs = res
            time.sleep(max(5.0, SHARP_SOCCER_REFRESH_S / 10))

    def sharp_soccer_shadow(self, state: Dict[str, Any], now: float) -> int:
        """Copy the soccer refresher's 3-way fairs into the sharp shadow
        state (kind "soccer3"; read for trading only under PMUS_RN1_SOCCER
        and its league allowlist). Starts the refresher once. No request."""
        self._soccer_want = self.sharp_soccer_events(now)
        if getattr(self, "_soccer_thread", None) is None:
            th = threading.Thread(target=self._soccer_refresh_loop,
                                  name="sharp-soccer", daemon=True)
            self._soccer_thread = th
            th.start()
        rows, why_n = 0, {}
        for slug, r in list((getattr(self, "_soccer_fairs", None) or {}
                             ).items()):
            why_n[r["why"]] = why_n.get(r["why"], 0) + 1
            f = r.get("fair")
            if f is None:
                if (state.get(slug) or {}).get("kind") == "soccer3":
                    state.pop(slug, None)
                continue
            rows += 1
            state[slug] = {"t": float(r["t"]), "fair_yes": str(f["fair_yes"]),
                           "odds_age_s": f.get("age_s") or 0.0,
                           "source": f["source"], "kind": "soccer3",
                           "outcome": f.get("outcome"),
                           "game_start": r.get("game_start"),
                           "q": {"YES": None, "NO": None}}
        log("SHARP_SOCCER", priced=rows, reasons=why_n,
            events=len(self._soccer_want),
            label="soccer 3-way sportsbook fairs (shadow state); nothing "
                  "placed")
        return rows

    # ------------------------------------------ soccer: one match, one risk
    def rn1_soccer_ok(self, slug: str) -> bool:
        """PMUS_RN1_SOCCER on and this is a soccer full-time-winner outcome
        of an allowlisted league (slug shape; the type is the venue's)."""
        if not RN1_SOCCER or not RN1_SOCCER_LEAGUES:
            return False
        from src import pmus_sports_soccer_fair as SF
        mo = SF.match_of(slug)
        return bool(mo and mo["league"] in RN1_SOCCER_LEAGUES)

    def rn1_soccer_prob(self, slug: str) -> Optional[Dict[str, Any]]:
        """Independent fair for an allowlisted soccer outcome: the settled
        international band when all three outcome mids are fresh and sum to
        1 within RN1_SOCCER_SUM_TOL (an inconsistent set refuses every
        outcome of the match, sportsbook included); otherwise, PRE-GAME
        only, the sportsbook 3-way fair (kind soccer3) within
        RN1_DIR_SHARP_MAX_AGE_S. None = no fair (fail closed)."""
        from src import pmus_directional_alloc as DA
        from src import pmus_sports_soccer_fair as SF
        mo = SF.match_of(slug)
        if mo is None:
            return None
        mids = [self.intl_fair(s_) for s_ in mo["siblings"]]
        if all(m is not None for m in mids):
            ok, tot = SF.three_way_consistent(mids, RN1_SOCCER_SUM_TOL)
            if not ok:
                key = (mo["match"], None if tot is None else str(tot))
                if getattr(self, "_soccer_sum_logged", None) != key:
                    self._soccer_sum_logged = key
                    log("RN1_SOCCER_SUM_REFUSED", match=mo["match"],
                        mids=[str(m) for m in mids],
                        sum=None if tot is None else str(tot),
                        tol=str(RN1_SOCCER_SUM_TOL),
                        label="three outcome mids inconsistent: no fair for "
                              "any outcome of this match")
                return None
            band = self.intl_fair_band(slug)
            if band.get("settled") and band.get("mid") is not None:
                return DA.probability(band["mid"], band.get("lo"),
                                      band.get("hi"), source="intl3")
        st = ((self.b.d.get("sharp_shadow") or {}).get(slug) or {})
        if st.get("kind") != "soccer3" or st.get("fair_yes") is None or \
                not st.get("t"):
            return None
        start = parse_start(st.get("game_start"))
        if start is None or time.time() >= start:
            return None                    # sportsbook fair is pre-game only
        age = max(0.0, time.time() - float(st["t"])) + \
            float(st.get("odds_age_s") or 0.0)
        return DA.probability(st["fair_yes"], source="sportsbook3:%s" % (
            st.get("source") or "odds"), age_s=round(age, 1),
            max_age_s=RN1_DIR_SHARP_MAX_AGE_S,
            drift_per_s=RN1_DIR_SHARP_DRIFT)

    def rn1_soccer_group(self, slug: str,
                         extra: Optional[tuple] = None,
                         filled_extra: bool = False
                         ) -> Optional[Dict[str, Any]]:
        """The match of an allowlisted soccer outcome as ONE book:
        {"outcomes", "holdings", "resting", "slugs", "per_market"}; resting
        = every open and reserved bid of its outcome markets (+ `extra`
        = (slug, side, px, qty) as a bid, or as filled when filled_extra).
        None when not soccer-ok, or a sibling is settled / unreadable."""
        from src import pmus_sports_soccer_fair as SF
        if not self.rn1_soccer_ok(slug):
            return None
        mo = SF.match_of(slug)
        markets = self.b.d.get("markets") or {}
        res = {}
        for r in self.reserved_orders():
            res.setdefault(str(r.get("slug")), []).append(r)
        hold, rest, per = {}, [], {}
        for s_, o in zip(mo["siblings"], mo["outcomes"]):
            mk_ = markets.get(s_)
            r_m = []
            if mk_ is not None:
                if mk_.get("settle_px") is not None or mk_.get("closed"):
                    return None
                hold[o] = {"pos": dict(mk_["pos"]), "cost": dict(mk_["cost"])}
                r_m += [(od["side"], D(str(od["px"])),
                         D(str(od["qty"])) - D(str(od.get("filled") or "0")))
                        for od in (mk_.get("orders") or {}).values()
                        if od.get("open")]
            r_m += [(r["side"], D(str(r["px"])), D(str(r["qty"])))
                    for r in res.get(s_, [])]
            if extra is not None and extra[0] == s_:
                if filled_extra:
                    h = hold.setdefault(o, {"pos": {"YES": "0", "NO": "0"},
                                            "cost": {"YES": "0", "NO": "0"}})
                    sd = "YES" if str(extra[1]).upper() == "YES" else "NO"
                    h = {"pos": dict(h["pos"]), "cost": dict(h["cost"])}
                    h["pos"][sd] = str(D(str(h["pos"].get(sd) or 0)) +
                                       D(str(extra[3])))
                    h["cost"][sd] = str(D(str(h["cost"].get(sd) or 0)) +
                                        D(str(extra[2])) * D(str(extra[3])))
                    hold[o] = h
                else:
                    r_m.append((extra[1], D(str(extra[2])),
                                D(str(extra[3]))))
            per[s_] = r_m
            rest += [(o, sd, px, q) for sd, px, q in r_m]
        return {"outcomes": mo["outcomes"], "holdings": hold, "resting": rest,
                "slugs": mo["siblings"], "per_market": per,
                "match": mo["match"]}

    def rn1_soccer_group_why(self, slug: str, side: str, px: Any,
                             qty: Any) -> Optional[str]:
        """The existing per-market caps applied to the WHOLE match (no new
        or larger limit): committed spend of its outcome markets <=
        RN1.PER_MARKET_USD and the exact match worst-case loss (home, draw,
        away; every resting bid filling adversely) <= RN1.RESIDUAL_CAP_USD.
        Unreadable = refuse."""
        from src import pmus_sports_soccer_fair as SF
        try:
            g = self.rn1_soccer_group(slug, extra=(slug, side, px, qty))
            if g is None:
                return "soccer_group_unreadable"
            markets = self.b.d.get("markets") or {}
            spend = D(0)
            for s_ in g["slugs"]:
                mk_ = markets.get(s_) or {"pos": {"YES": "0", "NO": "0"},
                                          "cost": {"YES": "0", "NO": "0"}}
                spend += RN1.committed(mk_["pos"], mk_["cost"], [
                    (a, b, c, False) for a, b, c in g["per_market"][s_]
                ])["spend"]
            loss = SF.match_loss(g["holdings"], g["outcomes"], g["resting"])
            tol = D("0.000001")
            if spend > D(str(RN1.PER_MARKET_USD)) + tol:
                return "match_spend:%s>%s" % (spend, RN1.PER_MARKET_USD)
            if loss > D(str(RN1.RESIDUAL_CAP_USD)) + tol:
                return "match_worst_loss:%s>%s" % (loss,
                                                   RN1.RESIDUAL_CAP_USD)
            return None
        except Exception as exc:                          # noqa: BLE001
            return "soccer_group_error:%s" % type(exc).__name__

    def rn1_soccer_groups(self, slugs: Iterable[str]) -> Dict[str, list]:
        """{match: [its allowlisted outcome slugs among `slugs`]}."""
        from src import pmus_sports_soccer_fair as SF
        out: Dict[str, list] = {}
        for s_ in slugs:
            if self.rn1_soccer_ok(s_):
                out.setdefault(SF.match_of(s_)["match"], []).append(s_)
        return out

    def rn1_soccer_book_adjust(self) -> D:
        """Loss-stop correction for allowlisted matches: the bot's book
        worst case values each market alone (its unpaired side worthless);
        for one match the exact worst over home / draw / away replaces the
        sum of its markets' terms. Either sign (exact, never assumed).
        Groups with a settled, unpiloted or unreadable market: 0."""
        if not RN1_SOCCER:
            return D(0)
        from src import pmus_pilot_pnl as PP
        from src import pmus_sports_soccer_fair as SF
        markets = self.b.d.get("markets") or {}
        pilot = set(PP.pilot_slugs(markets, self.since))
        adj = D(0)
        for _m, ss in self.rn1_soccer_groups(list(markets)).items():
            try:
                g = self.rn1_soccer_group(ss[0])
                if g is None:
                    continue
                held = [s_ for s_ in g["slugs"] if s_ in markets]
                if any(s_ not in pilot for s_ in held):
                    continue
                per = D(0)
                for s_ in held:
                    mk_ = markets[s_]
                    per += min(D(str(mk_["pos"]["YES"])),
                               D(str(mk_["pos"]["NO"]))) - \
                        D(str(mk_["cost"]["YES"])) - D(str(mk_["cost"]["NO"]))
                adj += SF.match_worst(g["holdings"], g["outcomes"]) - per
            except Exception:                             # noqa: BLE001
                continue
        return adj

    # ------------------------------------------- account-wide risk (shared)
    def own_exposure_usd(self) -> D:
        """This lane's at-risk dollars: unpaired filled cost plus the
        notional of every resting order it believes live."""
        out = D(self.b.total_unmatched())
        for mk in (self.b.d.get("markets") or {}).values():
            for o in (mk.get("orders") or {}).values():
                if o.get("open"):
                    try:
                        left = D(str(o.get("qty"))) - D(str(o.get("filled")
                                                            or "0"))
                        out += max(D(0), left) * D(str(o.get("px")))
                    except Exception:                    # noqa: BLE001
                        return D("Infinity")             # unknown: fail closed
        try:
            for r in self.reserved_orders():
                out += D(str(r["px"])) * D(str(r["qty"]))
        except Exception:                                # noqa: BLE001
            return D("Infinity")
        return out

    # ---- reservations: a submission counts from before it is sent --------
    def _reservations(self) -> Dict[str, Any]:
        res = self.b.d.setdefault("acct_reservations", {})
        if not getattr(self, "_res_rebased", False):
            # a reservation from an earlier process is as old as this one
            self._res_rebased = True
            for r in res.values():
                r["mono"] = time.monotonic()
                r["state"] = "uncertain"
        return res

    def reserved_orders(self, slug: Optional[str] = None) -> list:
        return [r for r in self._reservations().values()
                if slug is None or r.get("slug") == slug]

    def reserve_order(self, slug: str, side: str, px: Any, qty: Any,
                      stage: str) -> str:
        self._res_seq = getattr(self, "_res_seq", 0) + 1
        rid = "res-%d-%d" % (int(time.time() * 1000), self._res_seq)
        self._reservations()[rid] = {
            "slug": slug, "side": side, "px": str(px), "qty": str(qty),
            "stage": stage, "state": "in_flight", "at": time.time(),
            "mono": time.monotonic()}
        return rid

    def release_reservation(self, rid: str) -> None:
        self._reservations().pop(rid, None)

    def reservation_uncertain(self, rid: str, exc: BaseException) -> None:
        r = self._reservations().get(rid)
        if not r:
            return
        r["state"] = "uncertain"
        try:
            self.b.save()
        except Exception:                                # noqa: BLE001
            pass
        log("ORDER_RESERVATION_HELD", slug=r["slug"], side=r["side"],
            px=r["px"], qty=r["qty"], stage=r["stage"],
            err=type(exc).__name__,
            label="the send failed without a definite answer: its capital "
                  "stays reserved until an account read newer than it has "
                  "been reconciled")

    def release_reconciled_reservations(
            self, read_mono: Optional[float],
            acct: Optional[Mapping[str, Any]] = None) -> int:
        """Release an uncertain submission's reservation ONLY on definitive
        venue evidence (owner 2026-11-24: "Require definitive venue order
        and position reconciliation before releasing reserved capital").
        All of:
          * an orphan sweep has run over an account read (`acct`, taken at
            `read_mono`) at least RESERVATION_HOLD_S newer than the send;
          * every order the venue shows open in that market is an order in
            the book (an accepted order would have been adopted by now);
          * a COMPLETE positions walk shows the venue's position in that
            market equal to the book's. An unanswered order that was
            accepted and filled in full is in no open-order list; only its
            position shows it. Then the reservation stays, and the
            position-mismatch latch stops the bot.
        Anything unread, incomplete or unequal holds the reservation. A
        ledger reading plays no part."""
        if read_mono is None or acct is None:
            return 0
        res = self._reservations()
        due = [k for k, r in res.items() if r.get("state") == "uncertain"
               and float(r.get("mono") or 1e18) + RESERVATION_HOLD_S
               <= read_mono]
        if not due:
            return 0
        try:
            venue_open: Dict[str, set] = {}
            for o in acct.get("open_orders") or []:
                venue_open.setdefault(str(o.get("marketSlug") or ""),
                                      set()).add(str(o.get("id")))
        except Exception:                                # noqa: BLE001
            return 0
        gone = []
        for k in due:
            r = res[k]
            slug = str(r.get("slug") or "")
            why = None
            try:
                mk = self.b.m(slug)
                known = set((mk.get("orders") or {}).keys())
                if not venue_open.get(slug, set()) <= known:
                    why = "venue_order_not_in_book"
                else:
                    net = self._venue_net_complete(slug)
                    have = abs(D(str(mk["pos"]["YES"])) -
                               D(str(mk["pos"]["NO"])))
                    if net is None:
                        why = "venue_positions_not_complete"
                    elif net != have:
                        why = "venue_position_differs_from_book"
            except Exception as exc:                     # noqa: BLE001
                why = "reconcile_error:%s" % type(exc).__name__
            if why is None:
                gone.append(k)
            elif r.get("held_why") != why:
                r["held_why"] = why
                log("ORDER_RESERVATION_HELD", slug=slug, side=r.get("side"),
                    px=r.get("px"), qty=r.get("qty"), stage=r.get("stage"),
                    err=why, label="not released: the venue's orders and "
                                   "positions do not yet prove this "
                                   "submission left nothing behind")
        for k in gone:
            r = res.pop(k)
            log("ORDER_RESERVATION_RELEASED", slug=r.get("slug"),
                side=r.get("side"), px=r.get("px"), qty=r.get("qty"),
                why="venue_orders_and_positions_reconciled")
        if gone:
            try:
                self.b.save()
            except Exception:                            # noqa: BLE001
                pass
        return len(gone)

    def own_unaccounted_losses(self, retired: set) -> D:
        """Today's mark-to-market change of markets the shared ledger has NOT
        yet booked (only losses count, in AR.evaluate)."""
        today = self._today()
        out = D(0)
        for slug, mk in (self.b.d.get("markets") or {}).items():
            if slug in retired:
                continue
            base = mk.get("mark0") or {}
            if base.get("date") == today and mk.get("mark") is not None:
                out += D(mk["mark"]) - D(base["value"])
        return out

    def account_risk(self, fresh: bool = False) -> Dict[str, Any]:
        """Cached shared-limit verdict; {"ok": True, "off": True} when the
        switch is off. Logs every change of verdict. `fresh` recomputes it
        from the last ledger snapshot and the book as they are NOW (no
        network): a placement must never be judged on room that an earlier
        placement has already taken."""
        if not (ACCOUNT_RISK_ON or ACCOUNT_RISK_OBSERVE):
            return {"ok": True, "off": True}
        now = time.monotonic()
        hit = getattr(self, "_acct_risk", None)
        if hit and not fresh and now - hit[0] < ACCOUNT_RISK_TTL_S:
            return hit[1]
        from src import l5_account_risk as AR
        url = (os.getenv(AR.RISK_URL_ENV) or "").strip()
        try:
            expo = self.own_exposure_usd()
            rest = None
            if ACCOUNT_RISK_RESTING_IN_LEDGER:
                added = self._own_added_since(expo)
            else:
                # the ledger's figure moves on fills, not on resting bids:
                # count every resting dollar, and only NEW unpaired fills
                unp = D(self.b.total_unmatched())
                rest = self.own_resting_worst()
                added = self._own_added_since(unp)
            reader = getattr(self, "_risk_reader", None)
            doc, v = None, None
            if reader is not None:
                # the loop never waits on the ledger: last snapshot only
                snap = reader.snapshot()
                if snap["doc"] is None:
                    v = {"ok": False, "reason": snap["err"] or
                         "ACCOUNT_RISK_NOT_READ_YET"}
                elif snap["age_s"] is None or \
                        snap["age_s"] > ACCOUNT_RISK_SNAPSHOT_MAX_S:
                    v = {"ok": False, "reason": "ACCOUNT_RISK_SNAPSHOT_STALE",
                         "age_s": None if snap["age_s"] is None
                         else round(snap["age_s"], 1), "last_error":
                         snap["err"]}
                else:
                    doc = snap["doc"]
            elif not url:
                v = {"ok": False, "reason": "ACCOUNT_RISK_URL_NOT_SET",
                     "env": AR.RISK_URL_ENV}
            else:
                try:
                    doc = AR.fetch_json(url)
                except Exception as exc:                 # noqa: BLE001
                    v = {"ok": False, "reason": "ACCOUNT_RISK_UNREADABLE:%s"
                         % type(exc).__name__}
            if v is None:
                own = self.own_unaccounted_losses(AR.retired_markets(doc))
                v = AR.evaluate(doc, now=datetime.now(timezone.utc),
                                own_unaccounted=own, own_at_risk=expo,
                                own_added_since=added, own_resting=rest)
            lane = AR.lane_cap()
            v = dict(v, lane_cap_usd=None if lane is None else str(lane),
                     own_exposure_usd=str(expo))
            if v.get("ok") and lane is None:
                v = dict(v, ok=False, reason="ACCOUNT_CAP_UNSET_OR_UNREADABLE")
            elif v.get("ok") and expo >= lane:
                v = dict(v, ok=False, reason="ACCOUNT_EXPOSURE_CAP")
        except Exception as exc:                         # noqa: BLE001
            v = {"ok": False, "reason": "ACCOUNT_RISK_ERROR:%s"
                 % type(exc).__name__}
        prev = (hit[1] if hit else {}).get("reason", "init")
        if v.get("reason") != prev:
            log("ACCOUNT_RISK", **{k: v.get(k) for k in (
                "ok", "reason", "utc_day_realized_usd", "daily_halt_usd",
                "lifetime_realized_usd", "cumulative_loss_ceiling_usd",
                "own_losses_not_yet_in_account_ledger_usd", "loss_room_usd",
                "lane_cap_usd", "own_exposure_usd", "age_s", "drawdown")},
                label="shared account-wide limits; refusals block NEW "
                      "openings/extensions only, completions continue")
        self._acct_risk = (now, v)
        return v

    def _own_added_since(self, expo: D) -> D:
        """How much this bot's at-risk money has grown over the period a
        ledger snapshot may be old: now, less the LOWEST it has been in
        that period. The ledger's figure cannot contain it yet. Erring high
        only makes the capacity smaller."""
        from src import l5_account_risk as AR
        hist = getattr(self, "_expo_hist", None)
        if hist is None:
            hist = self._expo_hist = []
        now = time.monotonic()
        keep = AR.RISK_MAX_AGE_S + ACCOUNT_RISK_SNAPSHOT_MAX_S + 10
        hist.append((now, expo))
        while len(hist) > 1 and now - hist[0][0] > keep:
            hist.pop(0)
        try:
            low = min(e for _t, e in hist)
            return max(D(0), expo - low) if low.is_finite() and \
                expo.is_finite() else D("Infinity")
        except Exception:                                # noqa: BLE001
            return D("Infinity")

    def start_risk_reader(self) -> None:
        """Begin reading the account-risk document off the loop."""
        if not (ACCOUNT_RISK_ON or ACCOUNT_RISK_OBSERVE) or \
                not ACCOUNT_RISK_ASYNC or \
                getattr(self, "_risk_reader", None) is not None:
            return
        from src import l5_account_risk as AR
        self._risk_reader = AR.RiskReader(
            lambda: os.getenv(AR.RISK_URL_ENV) or "",
            every_s=ACCOUNT_RISK_READ_S).start()
        log("account_risk_reader_started", every_s=ACCOUNT_RISK_READ_S,
            max_age_s=ACCOUNT_RISK_SNAPSHOT_MAX_S,
            label="the trading loop no longer waits on the account ledger")

    def account_limit_status(self) -> Dict[str, Any]:
        """The account-limit verdict for the heartbeat, computed whether or
        not the bot is locked (owner 2026-11-23: the verdict must be
        checkable in a non-trading preflight). `would_block_new` is what a
        new opening would be told right now."""
        if not (ACCOUNT_RISK_ON or ACCOUNT_RISK_OBSERVE):
            return {"mode": "off"}
        try:
            v = self.account_risk()
            dd = v.get("drawdown") if isinstance(v.get("drawdown"),
                                                 Mapping) else {}
            return {"mode": "enforce" if ACCOUNT_RISK_ON else "observe",
                    "ok": bool(v.get("ok")), "reason": v.get("reason"),
                    "would_block_new": self.account_risk_blocks(),
                    "ledger_figure_usd": dd.get("committed_and_realized_usd"),
                    "limit_usd": dd.get("max_drawdown_usd"),
                    "capacity_usd": dd.get("capacity_usd"),
                    "own_resting_worst_usd": dd.get("own_resting_orders_usd"),
                    "resting_counted_separately":
                        dd.get("resting_counted_separately"),
                    "drawdown_state": dd.get("state"),
                    "snapshot_age_s": v.get("age_s")}
        except Exception as exc:                         # noqa: BLE001
            return {"mode": "enforce" if ACCOUNT_RISK_ON else "observe",
                    "ok": False, "reason": "STATUS_ERROR:%s"
                    % type(exc).__name__}

    def own_resting_worst(self, extra: Optional[tuple] = None) -> D:
        """The most this bot's RESTING bids can add to its unpaired cost,
        over every way they could fill, summed over markets. A bid on each
        side of one market is not the sum of the two: if both fill they
        are a pair bought under the pair cap, which the ledger values at
        $1. `extra` = (slug, side, px, qty) adds one hypothetical bid.
        Unknown figures return Infinity (refuse)."""
        try:
            tot = D(0)
            markets = self.b.d.get("markets") or {}
            seen = False
            contrib: Dict[str, D] = {}
            held: Dict[str, list] = {}
            for r in self.reserved_orders():
                held.setdefault(r["slug"], []).append(
                    (r["side"], D(str(r["px"])), D(str(r["qty"])), False))
            for s_, legs in held.items():
                if s_ not in markets:          # no book entry: face value
                    tot += sum(px_ * q_ for _sd, px_, q_, _c in legs)
            for s_, mk_ in markets.items():
                resting = [(o["side"], o["px"], D(str(o["qty"])) - D(str(
                    o.get("filled") or "0")), False)
                    for o in (mk_.get("orders") or {}).values()
                    if o.get("open")]
                resting += held.get(s_, [])
                if extra is not None and extra[0] == s_:
                    resting.append((extra[1], extra[2], extra[3], False))
                    seen = True
                if not resting:
                    continue
                worst = RN1.committed(mk_["pos"], mk_["cost"],
                                      resting)["unmatched"]
                inc = max(D(0), worst - RN1.unmatched_usd(
                    mk_["pos"], mk_["cost"]))
                tot += inc
                contrib[s_] = inc
            if extra is not None and not seen:
                tot += D(str(extra[2])) * D(str(extra[3]))
            if RN1_SOCCER and contrib:
                tot += self._soccer_resting_adjust(contrib, extra, seen)
            return tot if tot.is_finite() else D("Infinity")
        except Exception:                                # noqa: BLE001
            return D("Infinity")

    def _soccer_resting_adjust(self, contrib: Mapping[str, D],
                               extra: Optional[tuple],
                               seen: bool = True) -> D:
        """PMUS_RN1_SOCCER: for each allowlisted match, replace the sum of
        its markets' resting-bid increments with the EXACT joint increment
        (match worst P&L held minus worst with every resting bid filling
        adversely). An `extra` bid on a sibling with no book entry was
        counted at face value by the caller; when the joint figure includes
        it, that face value is taken back out (no double count). Either
        sign; an unreadable group raises (caller -> Infinity, refuse)."""
        from src import pmus_sports_soccer_fair as SF
        adj = D(0)
        for _m, ss in self.rn1_soccer_groups(list(contrib)).items():
            ex = extra if extra is not None and extra[0] in \
                SF.match_of(ss[0])["siblings"] else None
            g = self.rn1_soccer_group(ss[0], extra=ex)
            if g is None:
                raise ValueError("soccer_group_unreadable")
            joint = SF.match_worst(g["holdings"], g["outcomes"]) - \
                SF.match_worst(g["holdings"], g["outcomes"], g["resting"])
            adj += max(D(0), joint) - sum(contrib[s_] for s_ in ss)
            if ex is not None and not seen:
                adj -= D(str(ex[2])) * D(str(ex[3]))
        return adj

    def account_risk_blocks(self, px: Optional[D] = None,
                            qty: Optional[D] = None,
                            slug: Optional[str] = None,
                            side: Optional[str] = None) -> Optional[str]:
        """Reason a NEW opening/extension is refused, or None. With a price
        and quantity (a placement) the verdict is computed now, so its
        capacity already excludes every bid resting or reserved."""
        v = self.account_risk(fresh=px is not None and qty is not None)
        if v.get("off") or not ACCOUNT_RISK_ON:          # observe: log only
            return None
        if not v.get("ok"):
            return str(v.get("reason"))
        if px is not None and qty is not None:
            need = D(str(px)) * D(str(qty))
            try:
                lane = D(str(v.get("lane_cap_usd")))
                if self.own_exposure_usd() + need > lane:
                    return "ACCOUNT_EXPOSURE_CAP"
            except Exception:                            # noqa: BLE001
                return "ACCOUNT_EXPOSURE_UNREADABLE"
            from src import l5_account_risk as AR
            cap_need = need
            if slug is not None and side is not None and \
                    not ACCOUNT_RISK_RESTING_IN_LEDGER:
                # what THIS bid adds to the worst case, not its face value
                cap_need = self.own_resting_worst(
                    (slug, side, D(str(px)), D(str(qty)))) - \
                    self.own_resting_worst()
            dd_ = v.get("drawdown")
            if isinstance(dd_, Mapping) and dd_.get("state") == "ON":
                cap_need = cap_need + ACCOUNT_RISK_ROOM_MARGIN_USD
            if not AR.capacity_allows(v, cap_need):
                return "ACCOUNT_RISK_CAPACITY"
        return None

    def stale_completion_block(self, slug: str, side: str,
                               qty: D) -> Optional[str]:
        """Owner 2026-11-21: a completion spends cash, so on a STALE account
        it is allowed only against fresh, venue-confirmed inventory and
        order state. One fresh account read (at most every ACCOUNT_RETRY_S),
        then ALL of:
          * the venue's net position in this market equals the book's
            YES - NO (unproven or disagreeing inventory -> refuse);
          * this side REDUCES the venue-confirmed imbalance, and this qty
            plus our open same-side orders does not exceed it (a completion
            can never flip or extend the position);
          * the venue's open orders in this market are exactly the book's
            open orders (no unknown, foreign or manual orders).
        Returns a refusal reason, or None. The submission lock and shared
        halt are still checked by place() afterwards."""
        now = time.monotonic()
        if now - self._stale_try_at < ACCOUNT_RETRY_S:
            return "account_stale_retry_wait"
        self._stale_try_at = now
        acc = self.read_account()
        if acc is None:
            return "account_unreadable"
        mk = self.b.m(slug)
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        try:
            venue_net = D(str((acc.get("positions") or {}).get(slug) or 0))
        except Exception:                                 # noqa: BLE001
            return "venue_position_unreadable"
        if venue_net != y - n:
            return "venue_inventory_mismatch"
        excess = venue_net if side == "NO" else -venue_net
        if excess <= 0:
            return "would_not_reduce_venue_imbalance"
        venue_open = {str(o.get("id")) for o in acc.get("open_orders") or []
                      if str(o.get("marketSlug") or "") == slug}
        book_open = {oid for oid, o in mk["orders"].items() if o.get("open")}
        if venue_open != book_open:
            return "venue_order_state_mismatch"
        resting = sum((D(o["qty"]) - D(o.get("filled") or "0")
                       for o in mk["orders"].values()
                       if o.get("open") and o.get("side") == side), D(0))
        if resting + qty > excess:
            return "exceeds_venue_imbalance"
        return None

    def depth_block(self, slug: str, side: str) -> Optional[str]:
        """Why a NEW bid on `side` fails the minimum-liquidity gate, else
        None. Our own resting contracts at that touch do not count."""
        if MIN_TOUCH_QTY <= 0:
            return None
        fn = getattr(self.v, "top_depth", None)
        d = None
        if fn is not None:
            try:
                d = fn(slug)
            except Exception:                             # noqa: BLE001
                d = None
        if not d:
            return "depth_unknown"
        if side == "YES":
            touch, have = d["bid"], d["bid_qty"]
        else:
            touch, have = ONE - d["ask"], d["ask_qty"]
        mk = self.b.d["markets"].get(slug) or {}
        own = sum((D(str(o["qty"])) - D(str(o.get("filled") or "0"))
                   for o in (mk.get("orders") or {}).values()
                   if o.get("open") and o.get("side") == side
                   and D(str(o["px"])) == touch), D(0))
        if have - own < MIN_TOUCH_QTY:
            return "thin_touch:%s<%s" % (have - own, MIN_TOUCH_QTY)
        return None

    def place(self, slug: str, side: str, px: D, qty: D, stage: str,
              post_only: bool = True) -> None:
        if self.stop:
            log("place_refused_shutdown", slug=slug, side=side, px=px,
                qty=qty, stage=stage)
            return
        lt = self.b.d.get("safety_latch")
        if lt:
            log("place_refused_safety_latch", slug=slug, side=side, px=px,
                qty=qty, stage=stage, why=lt.get("why"), id=lt.get("id"))
            return
        if is_completion_stage(stage) and not self.account_fresh():
            why = self.stale_completion_block(slug, side, qty)
            if why:
                log("place_refused_stale_completion", slug=slug, side=side,
                    px=px, qty=qty, stage=stage, reason=why,
                    label="stale account: completion needs fresh "
                          "venue-confirmed inventory and order state")
                return
        if not is_completion_stage(stage) and not self.account_fresh():
            log("place_refused_account_stale", slug=slug, side=side, px=px,
                qty=qty, stage=stage,
                age_s=None if self._acct_ok_at is None else
                round(time.monotonic() - self._acct_ok_at, 1),
                label="new exposure needs a fresh account read; "
                      "management continues; completions need venue "
                      "confirmation")
            return
        if not is_completion_stage(stage):
            why = self.account_risk_blocks(px, qty, slug, side)
            if why:
                log("place_refused_account_risk", slug=slug, side=side,
                    px=px, qty=qty, stage=stage, reason=why)
                return
            cur = self.own_exposure_usd()
            if not PH.at_risk_allows(cur, D(str(px)) * D(str(qty)),
                                     AT_RISK_BUDGET_USD):
                key = "%s:%s:%s" % (side, px, qty)
                mk_ = self.b.m(slug)
                if mk_.get("at_risk_logged") != key:
                    mk_["at_risk_logged"] = key
                    log("place_refused_at_risk_budget", slug=slug, side=side,
                        px=px, qty=qty, stage=stage, at_risk_usd=cur,
                        budget_usd=AT_RISK_BUDGET_USD,
                        label="unpaired filled cost plus every resting "
                              "order plus this one would exceed the "
                              "portfolio budget")
                return
        self.refresh_campaign_auth()
        if submission_locked(stage, post_only):
            log("place_refused_submission_lock", slug=slug, side=side, px=px,
                qty=qty, stage=stage, why=lock_state()["why"])
            return
        if not is_completion_stage(stage):
            # universal backstop for every opening path (RN1 withholds the
            # side earlier, so this normally never fires there)
            why = self.depth_block(slug, side)
            if why:
                log("place_refused_depth", slug=slug, side=side, px=px,
                    qty=qty, stage=stage, reason=why,
                    min_touch_qty=str(MIN_TOUCH_QTY))
                return
        if not is_completion_stage(stage) and self.scope_block(slug):
            log("place_refused_scope", slug=slug, side=side, px=px, qty=qty,
                stage=stage, reason=self.scope_block(slug),
                allow_sports=sorted(OPEN_GATE_ALLOW_SPORTS))
            return
        if self.shared_halt() == "HALT":
            log("place_refused_shared_halt", slug=slug, side=side, px=px,
                qty=qty, stage=stage)
            return
        why = self.final_gate(slug, side, px, qty, stage, post_only)
        if why:
            log("place_refused_final_gate", slug=slug, side=side, px=px,
                qty=qty, stage=stage, post_only=post_only, reason=why,
                label="refused immediately before submission; nothing "
                      "was sent")
            return
        rid = self.reserve_order(slug, side, px, qty, stage)
        if hasattr(self.v, "last_order_post"):
            self.v.last_order_post = None        # never judge by an old send
        try:
            oid = (self.v.place(slug, side, px, qty, stage) if post_only
                   else self.v.place(slug, side, px, qty, stage,
                                     post_only=False))
        except BaseException as exc:
            why = send_never_left(self.v, exc)
            if type(exc).__name__ == "RejectedNoFill":
                self.release_reservation(rid)    # the venue said no
            elif why:
                self.release_reservation(rid)    # nothing was transmitted
                log("ORDER_NOT_SENT", slug=slug, side=side, px=px, qty=qty,
                    stage=stage, why=why, err=type(exc).__name__,
                    label="no order left the bot; nothing is reserved")
            else:
                self.reservation_uncertain(rid, exc)
            raise
        mk = self.b.m(slug)
        mk["orders"][oid] = {"side": side, "px": str(px), "qty": str(qty),
                             "filled": "0", "open": True, "stage": stage,
                             "placed_at": time.time()}
        self.release_reservation(rid)            # the book holds it now
        if mk.get("phase_now"):
            mk["orders"][oid]["phase"] = mk["phase_now"]
            self._phase_count(mk, mk["phase_now"], "orders", 1)
        self.b.d["own_order_ids"].append(oid)
        self.b.save()
        log("ORDER", slug=slug, side=side, px=px, qty=qty, stage=stage,
            order_id=oid, phase=mk.get("phase_now"))

    # ------------------------------------------------ RN1 replica (default off)
    def final_gate(self, slug: str, side: str, px: Any, qty: Any,
                   stage: str, post_only: bool = True) -> Optional[str]:
        """Why this order may not be sent, or None. Independent of whatever
        built the order. Unknown or unreadable figures refuse."""
        if not FINAL_GATE:
            return None
        try:
            p, q = D(str(px)), D(str(qty))
            if not (p.is_finite() and q.is_finite()) or q <= 0 or \
                    not (D(0) < p < D(1)):
                return "order_unreadable"
            if not RN1.ON:
                # the older two-sided path keeps its own limits and its
                # own, separately authorized, taker exits
                return None
            if not post_only and not ALLOW_TAKER:
                return "taker_not_authorized"
            if q > rn1_max_clip():
                return "size_above_maximum:%s>%s" % (q, rn1_max_clip())
            mk = self.b.m(slug)
            sd = "YES" if str(side).upper() == "YES" else "NO"
            ot = "NO" if sd == "YES" else "YES"
            pos = {k: D(str(mk["pos"][k])) for k in ("YES", "NO")}
            cost = {k: D(str(mk["cost"][k])) for k in ("YES", "NO")}
            rest = [(o["side"], D(str(o["px"])),
                     D(str(o["qty"])) - D(str(o.get("filled") or "0")))
                    for o in (mk.get("orders") or {}).values()
                    if o.get("open")]
            rest += [(r["side"], D(str(r["px"])), D(str(r["qty"])))
                     for r in self.reserved_orders(slug)]
            # ---- pair cost: against what this bid could pair with
            cap = D(str(RN1.PAIR_CAP))
            partners = [rp for rs, rp, rl in rest if rs == ot and rl > 0]
            if pos[ot] > pos[sd] and pos[ot] > 0:
                partners.append(cost[ot] / pos[ot])   # held, unpaired
            if partners and p + max(partners) > cap + D("0.0000001"):
                return "pair_cost_above_cap:%s+%s>%s" % (
                    p, max(partners).quantize(D("0.0001")), cap)
            if is_completion_stage(stage):
                # it reduces risk and its cost is already reserved; the
                # pair-cost ceiling above is its limit
                return None
            # LOSS STOPS, again, immediately before submission (owner
            # 2026-11-26). The decision layer (step_market) already refuses
            # new exposure under either stop; this backstop holds even if a
            # path reaches place() without it. Same evaluations, no new
            # limit: the campaign stop and its fail-closed meter checks
            # (pilot_block) and the bot's UTC-day stop (daily_stopped).
            # Completions returned above and are never refused here.
            pb = self.pilot_block()
            if pb:
                return "campaign_loss_stop:%s" % pb
            if self.daily_stopped():
                return "daily_loss_stop"
            if RN1_LOSS_STOP_COUNTS_RESTING and PILOT_LOSS_USD > 0:
                from src import pmus_pilot_pnl as PP
                worst = PP.book_worst_case(self.b.d.get("markets") or {},
                                           self.since) - \
                    self.own_resting_worst(extra=(slug, sd, p, q))
                if RN1_SOCCER:
                    worst += self.rn1_soccer_book_adjust()
                if worst <= -D(str(PILOT_LOSS_USD)):
                    return "campaign_loss_stop_with_resting:%s<=-%s" % (
                        worst.quantize(D("0.01")), PILOT_LOSS_USD)
            # ---- ceilings: everything recorded plus this order
            every = [(rs, rp, rl, False) for rs, rp, rl in rest] + \
                [(sd, p, q, False)]
            c = RN1.committed(mk["pos"], mk["cost"], every)
            tol = D("0.000001")
            if c["spend"] > D(str(RN1.PER_MARKET_USD)) + tol:
                return "per_market_spend:%s>%s" % (
                    c["spend"], RN1.PER_MARKET_USD)
            if c["unmatched"] > D(str(RN1.RESIDUAL_CAP_USD)) + tol:
                return "per_market_unpaired:%s>%s" % (
                    c["unmatched"], RN1.RESIDUAL_CAP_USD)
            if self.rn1_soccer_ok(slug):
                gw = self.rn1_soccer_group_why(slug, sd, p, q)
                if gw:
                    return gw
            other = self.rn1_campaign_exposure(exclude=slug)
            if other["spend"] + c["spend"] > \
                    D(str(RN1.TOTAL_SPEND_USD)) + tol:
                return "campaign_spend:%s>%s" % (
                    other["spend"] + c["spend"], RN1.TOTAL_SPEND_USD)
            if other["unmatched"] + c["unmatched"] > \
                    D(str(RN1.TOTAL_UNMATCHED_USD)) + tol:
                return "campaign_unpaired:%s>%s" % (
                    other["unmatched"] + c["unmatched"],
                    RN1.TOTAL_UNMATCHED_USD)
            return None
        except Exception as exc:                          # noqa: BLE001
            return "gate_error:%s" % type(exc).__name__

    def rn1_campaign_exposure(self, exclude: Optional[str] = None
                              ) -> Dict[str, D]:
        """Committed spend / unmatched across RN1 markets not yet settled:
        filled cost + completion reserve + resting bids (RN1.committed)."""
        from src import pmus_pilot_pnl as PP
        tot = {"spend": D(0), "unmatched": D(0)}
        markets = self.b.d.get("markets") or {}
        # in-flight or uncertain submissions in OTHER markets count from
        # before they are sent, exactly as at-risk counts them (owner
        # 2026-11-24: "Include outstanding and in-flight orders")
        reserved: Dict[str, list] = {}
        for r in self.reserved_orders():
            if r.get("slug") != exclude:
                reserved.setdefault(str(r.get("slug")), []).append(r)
        seen = set()
        for s_ in PP.pilot_slugs(markets, self.since):
            mk_ = markets[s_]
            if s_ == exclude or mk_.get("settle_px") is not None:
                continue
            seen.add(s_)
            resting = [(o["side"], o["px"],
                        D(o["qty"]) - D(o.get("filled") or "0"),
                        is_completion_stage(o.get("stage")))
                       for o in (mk_.get("orders") or {}).values()
                       if o.get("open")]
            resting += [(r["side"], r["px"], D(str(r["qty"])),
                         is_completion_stage(r.get("stage")))
                        for r in reserved.get(s_, [])]
            c = RN1.committed(mk_["pos"], mk_["cost"], resting)
            tot["spend"] += c["spend"]
            tot["unmatched"] += c["unmatched"]
        for s_, rs in reserved.items():
            if s_ in seen:
                continue
            mk_ = markets.get(s_) or {"pos": {"YES": "0", "NO": "0"},
                                      "cost": {"YES": "0", "NO": "0"}}
            if mk_.get("settle_px") is not None:
                continue
            c = RN1.committed(mk_["pos"], mk_["cost"],
                              [(r["side"], r["px"], D(str(r["qty"])),
                                is_completion_stage(r.get("stage")))
                               for r in rs])
            tot["spend"] += c["spend"]
            tot["unmatched"] += c["unmatched"]
        return tot

    @staticmethod
    def _rn1_exposed(mk: Mapping[str, Any]) -> bool:
        """A market holding new-exposure risk: an unpaired leg or a resting
        opening/extension bid."""
        if D(str(mk["pos"]["YES"])) != D(str(mk["pos"]["NO"])):
            return True
        return any(o.get("open") and not is_completion_stage(o.get("stage"))
                   for o in (mk.get("orders") or {}).values())

    def rn1_market_slots_full(self, slug: str, mk: Mapping[str, Any]) -> bool:
        """Owner 2026-11-22: two markets maximum. MAX_MARKETS bounded only
        the SELECTION of new markets; already-tracked markets kept opening
        (live, locked: three markets quoted at once). A market may add new
        exposure only while fewer than MAX_MARKETS OTHER markets hold some
        -- if more than MAX_MARKETS ever do (e.g. after a restart), each can
        only complete until they drain. Completions are never blocked."""
        others = [s_ for s_, m_ in (self.b.d.get("markets") or {}).items()
                  if s_ != slug and not m_.get("done") and
                  self._rn1_slot_holder(m_)]
        if len(others) >= MAX_MARKETS:
            return True
        return self.rn1_live_slots_full(slug, others)

    def rn1_live_slots_full(self, slug: str,
                            holders: Optional[list] = None) -> bool:
        """RN1_MAX_LIVE_MARKETS: is `slug` live while that many OTHER live
        markets already hold slots (or claim one, at selection)?"""
        if RN1_MAX_LIVE_MARKETS <= 0:
            return False
        try:
            if not self.event_live(slug):
                return False
            if holders is None:
                holders = [s_ for s_ in self.slot_claimants() if s_ != slug]
            live = sum(1 for s_ in holders if s_ != slug and
                       self.event_live(s_))
            return live >= RN1_MAX_LIVE_MARKETS
        except Exception:                                 # noqa: BLE001
            return True                                   # unknown: no

    def _rn1_slot_holder(self, mk: Mapping[str, Any]) -> bool:
        """Does this market hold one of the MAX_MARKETS quoting slots?
        Default (as since 2026-11-22): any market with an unpaired leg or
        a resting opening/extension bid. LIVE 2026-11-25: a carried
        unpaired leg in a competition no longer permitted held a slot for
        68 minutes, so three markets were quoted where four were
        authorized (24 `max_markets` blocks), and the fourth selection was
        released and re-selected in a loop. Owner: "Separate passive
        inventory tracking from active quoting slots, while retaining full
        exposure accounting and position management."
        With RN1_SLOTS_COUNT_INVENTORY off a slot is held only by a market
        with a RESTING opening or extension bid -- the thing a slot is for.
        A market that merely carries inventory keeps its completion bid,
        its place in the book, the registry and the audit, and every dollar
        of it still counts in the per-market, campaign, at-risk and account
        limits; the number of tracked markets stays bounded by
        MAX_TRACKED."""
        if RN1_SLOTS_COUNT_INVENTORY:
            return self._rn1_exposed(mk)
        return any(o.get("open") and not is_completion_stage(o.get("stage"))
                   for o in (mk.get("orders") or {}).values())

    def rn1_quote_qualifies(self, slug: str, mk: Dict[str, Any]) -> bool:
        """True when our standard quote in this market would earn a reward
        under the venue's rule right now (see RN1_RELEASE_UNQUALIFIED_S):
        a paying programme and a positive modelled reward. One public book
        read at most every 60 s per market; legacy reward-only mode retains a slot on read failure;
        net ranking must not authorize openings on a failed read."""
        try:
            per = self.rn1_reward(slug)
            if not per:
                return False
            hit = mk.get("qualify_check")
            if hit and time.time() - float(hit.get("t") or 0) < 60:
                return bool(hit.get("ok"))
            got = self._reward_share_score(slug, per)
            if "why" in got and got.get("why") not in ("no_two_sided_book",
                                                       "no_room_for_a_bid"):
                return not RN1_NET_RANK
            ok = float(got.get("net_usd_per_hour" if RN1_NET_RANK
                               else "usd_per_hour") or 0.0) > 0
            mk["qualify_check"] = {"t": time.time(), "ok": ok,
                                   "usd_per_hour": got.get("usd_per_hour"),
                                   "net_usd_per_hour":
                                       got.get("net_usd_per_hour")}
            return ok
        except Exception:                                 # noqa: BLE001
            return not RN1_NET_RANK

    @staticmethod
    def rn1_pause_depth(rng: Any, tick: Any) -> int:
        """RN1_PAUSE_DEPTH: ticks below the touch an opening/extension bid
        rests at while the market is paused: the 30 s mid range rounded up
        to ticks plus RN1_PAUSE_DEPTH_EXTRA_TICKS, and at least JUMP_TICKS
        plus that (a jump pause can follow a single update)."""
        t = D(str(tick))
        r = max(D(0), D(str(rng or 0)))
        ticks = int((r / t).to_integral_value(rounding="ROUND_CEILING")) \
            if t > 0 else 0
        return max(ticks, JUMP_TICKS) + max(0, RN1_PAUSE_DEPTH_EXTRA_TICKS)

    def rn1_directional_prob(self, slug: str) -> Optional[Dict[str, Any]]:
        """The probability band for RN1_DIRECTIONAL, or None (= no change):
        the international fair band once settled; otherwise, with
        RN1_DIR_SHARP, the sportsbook fair value from the sharp shadow
        state, refused past RN1_DIR_SHARP_MAX_AGE_S and widened by its age.
        Never the traded market's own price."""
        from src import pmus_directional_alloc as DA
        try:
            if self.rn1_soccer_ok(slug):
                return self.rn1_soccer_prob(slug)
            band = self.intl_fair_band(slug)
            if band.get("settled") and band.get("mid") is not None:
                return DA.probability(band["mid"], band.get("lo"),
                                      band.get("hi"), source="intl")
            if RN1_DIR_SHARP:
                st = ((self.b.d.get("sharp_shadow") or {}).get(slug) or {})
                if st.get("kind") == "soccer3":
                    return None        # soccer 3-way: only via RN1_SOCCER
                if st.get("fair_yes") is not None and st.get("t"):
                    age = max(0.0, time.time() - float(st["t"])) + \
                        float(st.get("odds_age_s") or 0.0)
                    return DA.probability(
                        st["fair_yes"], source="sportsbook:%s" % (
                            st.get("source") or "odds"),
                        age_s=round(age, 1),
                        max_age_s=RN1_DIR_SHARP_MAX_AGE_S,
                        drift_per_s=RN1_DIR_SHARP_DRIFT)
        except Exception:                                 # noqa: BLE001
            return None
        return None

    def rn1_fair_coverage(self, slug: str) -> Dict[str, Any]:
        """READ-ONLY (with PMUS_SHARP_COVERAGE_LOG): which independent
        probability sources this market has right now -- the international
        guard (configured, token mapped, fresh mid) and the sportsbook shadow
        state (fair and its age). Never the market's own price."""
        out: Dict[str, Any] = {"guard_configured": self.guard is not None}
        try:
            if self.guard is not None:
                tok = self.guard.token(slug)
                out["intl_token"] = bool(tok)
                mid = self.intl_fair(slug)
                out["intl_mid"] = None if mid is None else str(mid)
            st = ((self.b.d.get("sharp_shadow") or {}).get(slug) or {})
            out["sportsbook_fair"] = st.get("fair_yes")
            out["sportsbook_age_s"] = None if not st.get("t") else round(
                time.time() - float(st["t"]) +
                float(st.get("odds_age_s") or 0.0), 1)
            out["any"] = bool(out.get("intl_mid") or out["sportsbook_fair"])
        except Exception as exc:                          # noqa: BLE001
            out["error"] = type(exc).__name__
        return out

    def rn1_has_independent_fair(self, slug: str) -> bool:
        """An independent probability source exists for this market: the
        international guard maps it, or (with RN1_DIR_SHARP) the sportsbook
        shadow holds a fair within RN1_DIR_SHARP_MAX_AGE_S."""
        if self.guard is not None and self.guard.token(slug):
            return True
        if self.rn1_soccer_ok(slug):
            st = ((self.b.d.get("sharp_shadow") or {}).get(slug) or {})
            if st.get("kind") == "soccer3" and st.get("t") and \
                    st.get("fair_yes") is not None:
                age = time.time() - float(st["t"]) + \
                    float(st.get("odds_age_s") or 0.0)
                return age <= RN1_DIR_SHARP_MAX_AGE_S
            return False
        if RN1_DIR_SHARP:
            st = ((self.b.d.get("sharp_shadow") or {}).get(slug) or {})
            if st.get("kind") == "soccer3":
                return False
            if st.get("fair_yes") is not None and st.get("t"):
                age = time.time() - float(st["t"]) + \
                    float(st.get("odds_age_s") or 0.0)
                return age <= RN1_DIR_SHARP_MAX_AGE_S
        return False

    def rn1_tf_select(self, scored: list) -> list:
        try:
            has = {r[2]: self.rn1_has_independent_fair(r[2]) for r in scored}
        except Exception as exc:                          # noqa: BLE001
            log("RN1_TF_SELECT", error=type(exc).__name__, mode=RN1_TF_SELECT)
            return [] if RN1_TF_SELECT == "only" else scored
        out = [r for r in scored if has[r[2]]] + \
            ([] if RN1_TF_SELECT == "only" else
             [r for r in scored if not has[r[2]]])
        key = (RN1_TF_SELECT, len(scored), sum(has.values()))
        if key != getattr(self, "_tf_sel_logged", None):
            self._tf_sel_logged = key
            log("RN1_TF_SELECT", mode=RN1_TF_SELECT, candidates=len(scored),
                with_independent_fair=sum(has.values()),
                first=[r[2] for r in out[:4]],
                label="trading-first selection: independent fair first")
        return out

    def rn1_tf_hedge_price(self, mk: Dict[str, Any], side: str,
                           tg: Dict[str, Any], tick: Any,
                           prob: Optional[Mapping[str, Any]]
                           ) -> Dict[str, Any]:
        """See RN1_TF_HEDGE_GIVE_USD: the completion repriced down to
        fair + give (never up). No fair or any error: unchanged."""
        if prob is None:
            return tg
        try:
            from src import pmus_directional_alloc as DA
            t = D(str(tick))
            cap = DA.fair_for(prob, side, conservative=False) + \
                RN1_TF_HEDGE_GIVE_USD
            cap = (cap / t).to_integral_value(rounding="ROUND_FLOOR") * t
            rec = mk.setdefault("_tf_last", {})
            rec["hedge_" + side] = {"px": str(tg["px"]), "cap": str(cap)}
            if D(str(tg["px"])) > cap:
                if cap <= 0:
                    return None
                return dict(tg, px=cap, hedge_capped=True)
            return tg
        except Exception:                                 # noqa: BLE001
            return tg

    def rn1_tf_note_mid(self, slug: str, mk: Dict[str, Any],
                        bbo: Mapping[str, Any], now: float) -> None:
        """Venue YES-mid history for RN1_TF_FALLING_S (diagnostic input to
        a refusal only; never a probability)."""
        if RN1_TF_FALLING_S <= 0:
            return
        h = mk.setdefault("_tf_mid", [])
        h.append((now, (D(str(bbo["bid"])) + D(str(bbo["ask"]))) / 2))
        while h and now - h[0][0] > RN1_TF_FALLING_S:
            h.pop(0)

    def rn1_tf_note_fair(self, mk: Dict[str, Any],
                         prob: Optional[Mapping[str, Any]],
                         now: float) -> None:
        """Independent-fair history for RN1_TF_WEAKEN_USD. A missing fair
        clears it (weakening is measured on an unbroken series only)."""
        if RN1_TF_WEAKEN_USD <= 0:
            return
        h = mk.setdefault("_tf_fair", [])
        if prob is None:
            h.clear()
            return
        try:
            h.append((now, D(str(prob["p"]))))
        except Exception:                                 # noqa: BLE001
            h.clear()
            return
        while len(h) > 1 and now - h[0][0] > RN1_TF_WEAKEN_S:
            h.pop(0)

    def rn1_trading_first_why(self, slug: str, mk: Dict[str, Any],
                              side: str, px: Any, tick: Any,
                              prob: Optional[Mapping[str, Any]],
                              qty: Any = 0) -> Optional[str]:
        """None = the exposure-adding bid passes RN1_TRADING_FIRST, else the
        refusal reason. Records the pre-reward trading edge (reward EV is
        never part of it)."""
        try:
            from src import pmus_directional_alloc as DA
            if prob is None:
                return "tf_no_fair"
            fair_c = DA.fair_for(prob, side, conservative=True)
            fair_m = DA.fair_for(prob, side, conservative=False)
            edge = fair_c - D(str(px))
            other = "NO" if side == "YES" else "YES"
            pos = mk.get("pos") or {}
            unp = max(D(0), D(str(pos.get(side) or 0)) + D(str(qty or 0)) -
                      D(str(pos.get(other) or 0)))
            need = RN1_TF_EDGE_USD + RN1_TF_CONC_USD * unp + (
                RN1_TF_DOG_EXTRA_USD if fair_m < D("0.5") else D(0))
            rec = mk.setdefault("_tf_last", {})
            rec[side] = {"px": str(px), "fair_conservative": str(fair_c),
                         "trading_edge": str(edge), "required": str(need),
                         "favourite": fair_m >= D("0.5"),
                         "source": prob.get("source")}
            if edge < need:
                hw = self.rn1_soccer_hedge_ok(slug, side, px, qty, fair_m)
                rec[side]["cross_hedge"] = hw
                if hw != "ok":
                    return "tf_negative_ev"
                return None
            fh = mk.get("_tf_fair") or []
            if RN1_TF_WEAKEN_USD > 0 and len(fh) > 1:
                d = fh[-1][1] - fh[0][1]
                if side == "NO":
                    d = -d
                if d <= -RN1_TF_WEAKEN_USD:
                    return "tf_weakening"
            if RN1_TF_FAV_ONLY and \
                    DA.fair_for(prob, side, conservative=False) < \
                    RN1_TF_FAV_MIN:
                return "tf_not_favourite"
            h = mk.get("_tf_mid") or []
            if RN1_TF_FALLING_S > 0 and h:
                d = h[-1][1] - h[0][1]
                if side == "NO":
                    d = -d
                if d <= -D(str(tick)) * RN1_TF_FALLING_TICKS:
                    return "tf_falling"
            return None
        except Exception as exc:                          # noqa: BLE001
            return "tf_error:%s" % type(exc).__name__   # fail closed

    def rn1_soccer_hedge_ok(self, slug: str, side: str, px: Any, qty: Any,
                            fair: Any) -> str:
        """ECONOMICAL CROSS-OUTCOME HEDGE (PMUS_RN1_SOCCER, allowlisted
        league, RN1_TF_HEDGE_GIVE_USD set): a bid that fails the edge test
        is still admitted when it is priced <= its independent fair +
        RN1_TF_HEDGE_GIVE_USD AND, filled, it LOWERS the match's exact worst
        case (filled inventory of the three outcome markets). "ok" or the
        reason it is not a hedge. Never forces a pairing."""
        try:
            if not self.rn1_soccer_ok(slug):
                return "not_soccer"
            if RN1_TF_HEDGE_GIVE_USD is None:
                return "no_hedge_give"
            q = D(str(qty or 0))
            if q <= 0:
                return "no_qty"
            if D(str(px)) > D(str(fair)) + RN1_TF_HEDGE_GIVE_USD:
                return "above_fair_plus_give"
            from src import pmus_sports_soccer_fair as SF
            g0 = self.rn1_soccer_group(slug)
            g1 = self.rn1_soccer_group(slug, extra=(slug, side, px, q),
                                       filled_extra=True)
            if g0 is None or g1 is None:
                return "group_unreadable"
            w0 = SF.match_worst(g0["holdings"], g0["outcomes"])
            w1 = SF.match_worst(g1["holdings"], g1["outcomes"])
            return "ok" if w1 > w0 else "does_not_reduce_worst"
        except Exception as exc:                          # noqa: BLE001
            return "error:%s" % type(exc).__name__

    def rn1_directional_inputs(self, slug: str, want: Mapping[str, Any],
                               bbo: Mapping[str, Any]) -> tuple:
        """(touch prices, fill loss per side): the competition's
        RN1_FILL_LOSS for each bid's placement (at the best bid = touch,
        behind it = behind)."""
        touch = {"YES": D(str(bbo["bid"])), "NO": ONE - D(str(bbo["ask"]))}
        fl = {}
        for sd in ("YES", "NO"):
            tg = want.get(sd)
            depth = 0 if tg is not None and D(str(tg["px"])) >= touch[sd] \
                else 1
            fl[sd] = rn1_fill_param(RN1_FILL_LOSS, depth, slug)
        return touch, fl

    def rn1_directional(self, slug: str, mk: Dict[str, Any],
                        q: Dict[str, Any], want: Dict[str, Any],
                        bbo: Mapping[str, Any], tick: Any, min_qty: Any,
                        prob: Optional[Mapping[str, Any]] = None,
                        priced: bool = False
                        ) -> Optional[Dict[str, Any]]:
        """See RN1_DIRECTIONAL. Changes `want` in place (remove, resize or
        deepen opening/extension bids only) and records the decision in
        q["directional"]. Returns the probability used (None = no change).
        Any error leaves `want` as it was -- except that additions priced
        without the average-cost cap (`priced`) are then withdrawn, since
        nothing judged them (fail closed)."""
        try:
            from src import pmus_directional_alloc as DA
            touch, fl = self.rn1_directional_inputs(slug, want, bbo)
            res = DA.allocate(prob=prob, want=want, pos=mk["pos"], tick=tick,
                              min_qty=min_qty, touch=touch, fill_loss=fl,
                              prev_favoured=mk.get("dir_favoured"),
                              cfg=RN1_DIR_CFG)
            rec = res.get("directional") or {}
            q["directional"] = rec
            if prob is None:
                return None
            if rec.get("favoured"):
                mk["dir_favoured"] = rec["favoured"]
            for sd in ("YES", "NO"):
                if want.get(sd) is None:
                    continue
                if res.get(sd) is None:
                    want[sd] = None
                    q["why_" + sd] = res.get("why_" + sd) or "directional"
                else:
                    want[sd] = res[sd]
            return prob
        except Exception as exc:                          # noqa: BLE001
            q["directional"] = {"active": False,
                                "why": "error:%s" % type(exc).__name__}
            if priced:
                for sd in ("YES", "NO"):
                    tg = want.get(sd)
                    if tg is not None and tg.get("role") == "extend":
                        want[sd] = None
                        q["why_" + sd] = "directional_error_unpriced_add"
            return None

    def rn1_directional_ladder(self, slug: str, mk: Dict[str, Any],
                               q: Dict[str, Any], want: Mapping[str, Any],
                               ladder: list, bbo: Mapping[str, Any],
                               tick: Any, min_qty: Any,
                               prob: Mapping[str, Any]) -> list:
        """Deeper ladder levels judged on the inventory they would add (see
        RN1_DIRECTIONAL). Any error keeps the ladder unchanged."""
        try:
            from src import pmus_directional_alloc as DA
            fl = {sd: rn1_fill_param(RN1_FILL_LOSS, 1, slug)
                  for sd in ("YES", "NO")}       # deeper = behind the touch
            kept, refused = DA.allocate_ladder(
                prob=prob, ladder=ladder, front=want, pos=mk["pos"],
                min_qty=min_qty, fill_loss=fl, cfg=RN1_DIR_CFG)
            if refused:
                rec = q.setdefault("directional", {})
                rec["ladder_refused"] = refused
            return kept
        except Exception:                                 # noqa: BLE001
            return ladder

    def rn1_ladder(self, slug: str, mk: Dict[str, Any],
                   want: Mapping[str, Any], tick: Any,
                   clip: Any = None, min_qty: Any = 1,
                   extra_levels: Optional[Mapping[str, int]] = None) -> list:
        """Deeper bids behind each side's target (see RN1_LADDER_LEVELS;
        `extra_levels` adds levels to one side for RN1_DIRECTIONAL, under
        exactly the same per-level caps):
        [{"side","px","qty","role","level"}]. A level is added only while
        this market's worst case with EVERY planned bid filled stays inside
        the per-market and campaign caps; the first level that would not
        fit ends that side's ladder.

        Behind a COMPLETION bid the deeper levels are ordinary opening bids
        of the normal clip on that side (LIVE-TESTED 2026-11-25 in replay:
        after one fill the lighter side otherwise fell back to a single
        completing bid, so neither side could keep accumulating). They are
        cheaper than the completion, so each still pairs inside the
        pair-cost limit with what the other side holds."""
        xl = {sd: max(0, int((extra_levels or {}).get(sd) or 0))
              for sd in ("YES", "NO")}
        if RN1_LADDER_LEVELS <= 1 and not any(xl.values()):
            return []
        t = D(str(tick))
        out: list = []
        planned = [(sd, tg["px"], tg["qty"], tg["role"] == "complete")
                   for sd, tg in want.items() if tg is not None]
        other = self.rn1_campaign_exposure(exclude=slug)
        tol = D("0.000001")
        for side in ("YES", "NO"):
            tg = want.get(side)
            if tg is None or tg.get("kept"):
                continue
            behind_completion = tg["role"] == "complete"
            qty = D(str(clip)) if (behind_completion and clip is not None) \
                else D(str(tg["qty"]))
            if RN1_LADDER_QTY is not None and RN1_LADDER_QTY > 0:
                qty = min(qty, RN1_LADDER_QTY)
            role = "open" if behind_completion else tg["role"]
            for k in range(1, RN1_LADDER_LEVELS + xl[side]):
                px = D(str(tg["px"])) - k * RN1_LADDER_STEP_TICKS * t
                if px < D(str(RN1.PX_MIN)) or px <= 0:
                    break

                def _fits(qq: D) -> bool:
                    trial = planned + [(l_["side"], l_["px"], l_["qty"],
                                        False) for l_ in out] + \
                        [(side, px, qq, False)]
                    c = RN1.committed(mk["pos"], mk["cost"], trial)
                    return not (
                        c["spend"] > D(str(RN1.PER_MARKET_USD)) + tol or
                        c["unmatched"] > D(str(RN1.RESIDUAL_CAP_USD)) + tol
                        or other["spend"] + c["spend"] >
                        D(str(RN1.TOTAL_SPEND_USD)) + tol or
                        other["unmatched"] + c["unmatched"] >
                        D(str(RN1.TOTAL_UNMATCHED_USD)) + tol)
                lq = qty
                if RN1_FIT_ALL_LEVELS:
                    mq = max(D(1), D(str(min_qty)))
                    while lq - 1 >= mq and not _fits(lq):
                        lq -= 1
                    if lq < mq or not _fits(lq):
                        continue        # a deeper, cheaper level may fit
                elif RN1.FIT_QTY:
                    while lq > 1 and not _fits(lq):
                        lq -= 1
                if not _fits(lq):
                    break
                out.append({"side": side, "px": px, "qty": lq,
                            "role": role, "level": k})
        return out

    def rn1_campaign_caps(self, slug: str, mk: Dict[str, Any],
                          want: Dict[str, Any], q: Dict[str, Any],
                          min_qty: Any = 1) -> None:
        """Owner 2026-11-22: $16 total spend and $6 unmatched across the
        campaign, counting filled inventory AND resting commitments. Drops
        opening/extension targets (dearest first) until this market's plan
        fits; completions are never dropped (they reduce unmatched and their
        cost is already reserved)."""
        other = self.rn1_campaign_exposure(exclude=slug)
        while True:
            resting = [(sd, t["px"], t["qty"], t["role"] == "complete")
                       for sd, t in want.items() if t is not None]
            c = RN1.committed(mk["pos"], mk["cost"], resting)
            over = ("total_spend_cap" if other["spend"] + c["spend"] >
                    RN1.TOTAL_SPEND_USD else
                    "total_unmatched_cap" if other["unmatched"] +
                    c["unmatched"] > RN1.TOTAL_UNMATCHED_USD else None)
            if over is None:
                return
            opt = [sd for sd, t in want.items()
                   if t is not None and t["role"] != "complete"]
            if not opt:
                return
            drop = max(opt, key=lambda sd: want[sd]["px"] * want[sd]["qty"])
            if RN1_MARGINAL_EV and len(opt) > 1 and \
                    all(want[sd].get("mev") for sd in opt):
                # ECONOMIC PRIORITY (RN1_MARGINAL_EV): among the targets
                # whose removal alone brings the plan inside both campaign
                # caps, the one of lowest marginal net EV goes, not the
                # dearest. When no single removal fits (the dearest is
                # usually the binding one) the dearest-first rule stays.
                def _fits_without(sd_: str) -> bool:
                    r_ = [(s2, t2["px"], t2["qty"], t2["role"] == "complete")
                          for s2, t2 in want.items()
                          if t2 is not None and s2 != sd_]
                    c2 = RN1.committed(mk["pos"], mk["cost"], r_)
                    return other["spend"] + c2["spend"] <= \
                        RN1.TOTAL_SPEND_USD and other["unmatched"] + \
                        c2["unmatched"] <= RN1.TOTAL_UNMATCHED_USD
                fix = [sd for sd in opt if _fits_without(sd)]
                if len(fix) > 1:
                    drop = min(fix, key=lambda sd: (
                        want[sd]["mev"]["ev_usd_per_hour"],
                        -(want[sd]["px"] * want[sd]["qty"])))
            mq = max(D(1), D(str(min_qty)))
            if RN1_FIT_ALL_LEVELS and D(str(want[drop]["qty"])) - 1 >= mq:
                # size down by one whole contract and test again
                want[drop] = dict(want[drop],
                                  qty=D(str(want[drop]["qty"])) - 1)
                q["why_" + drop] = over + "_sized"
                continue
            want[drop] = None
            q["why_" + drop] = over

    def intl_fair(self, slug: str) -> Optional[D]:
        """International mid for the market's YES side, or None when the
        guard has no fresh two-sided quote for it."""
        g = self.guard
        if g is None:
            return None
        try:
            tok = g.token(slug)
            it = g.intl.fresh(tok, g.max_age) if tok else None
            if not it or it.get("bid") is None or it.get("ask") is None \
                    or it["ask"] <= it["bid"]:
                return None
            return (D(str(it["bid"])) + D(str(it["ask"]))) / 2
        except Exception:                                 # noqa: BLE001
            return None            # unreadable fair value = no fair value

    def intl_fair_band(self, slug: str) -> Dict[str, Any]:
        """{"mid", "lo", "hi", "settled", "span_s"}: the international mid
        now and its lowest and highest value over the last
        RN1_FAIR_STABLE_S seconds. `settled` is False (and nothing may be
        bid) when there is no fresh fair value now, or the unbroken history
        does not yet cover the period. A missing fair value clears the
        history: stability is counted from the next good reading."""
        hist = getattr(self, "_fair_hist", None)
        if hist is None:
            hist = self._fair_hist = {}
        h = hist.setdefault(slug, [])
        now = time.time()
        mid = self.intl_fair(slug)
        if mid is None:
            h.clear()
            return {"mid": None, "lo": None, "hi": None, "settled": False,
                    "span_s": 0.0}
        h.append((now, mid))
        w = max(0.0, RN1_FAIR_STABLE_S)
        # keep one reading older than the period, so its start is covered
        while len(h) > 1 and now - h[1][0] >= w:
            h.pop(0)
        span = now - h[0][0]
        vals = [m for _t, m in h]
        lo, hi = min(vals), max(vals)
        calm = (hi - lo) <= RN1_FAIR_MAX_RANGE_USD
        return {"mid": mid, "lo": lo, "hi": hi,
                "settled": span >= w and calm, "calm": calm,
                "range": hi - lo, "span_s": round(span, 1)}

    def campaign_marker_mismatch(self) -> bool:
        """True when a trading window is configured and the campaign marker
        is not at (or before) its start: orders placed from the window's
        start up to the marker would count toward no campaign cap and no
        campaign loss stop (LIVE 2026-11-22 20:16Z). Fail closed: no new
        exposure while it holds."""
        w = lock_window()
        if w is None and runtime_mode():
            act = self._campaign_store().get("active")
            if act:
                w = (float(act["start"]), float(act["end"]))
        if w is None:
            return False
        return self.since is None or float(self.since) > float(w[0]) + 1e-6

    def rn1_reward(self, slug: str) -> Optional[Dict[str, Any]]:
        """The paying liquidity period for this market from the cached
        programme read (no request here), or None. A cache entry older
        than 2 x REWARD_REFRESH_S (two missed refreshes) is no programme:
        a programme withdrawn at the venue must not keep being credited
        just because nothing re-read it (fail closed until refreshed)."""
        ent = self._prog.get(slug) or (0, None)
        prog = ent[1]
        if not prog:
            return None
        try:
            if time.monotonic() - float(ent[0]) > 2 * REWARD_REFRESH_S:
                return None
        except Exception:                                 # noqa: BLE001
            return None
        try:
            from src import pmus_sports_rewards as R
            return R.paying_period(prog, time.time(),
                                   event_live=self.event_live(slug))
        except Exception:                                 # noqa: BLE001
            return None

    # ------------------------------------------------ one controller, phases
    def _vol(self, slug: str, bbo: Optional[Mapping], now: float) -> D:
        """Record the venue's mid just read and return its range over
        VOL_WINDOW_S. No book = no reading (the range is over what was
        seen)."""
        hist = getattr(self, "_vol_hist", None)
        if hist is None:
            hist = self._vol_hist = {}
        h = hist.setdefault(slug, [])
        try:
            if bbo and bbo.get("bid") is not None and \
                    bbo.get("ask") is not None:
                h.append((now, (D(str(bbo["bid"])) + D(str(bbo["ask"]))) / 2))
            PH.prune(h, now, max(VOL_WINDOW_S, 1.0))
            return PH.price_range(h, now, VOL_WINDOW_S)
        except Exception:                                 # noqa: BLE001
            return D("0")

    # MEASURED SEPARATELY BY PHASE (owner 2026-11-23: "Measure qualifying
    # resting time, actual fills, adverse markouts, and credited rewards
    # separately"). Per market, per phase: orders sent, fills, contracts
    # filled, contract-seconds resting, and contract-seconds resting while
    # the venue's scoring rule gives the resting quotes a share of a paying
    # reward pool (theoretical; a credit is only what the venue pays).
    # Measurement only: nothing here places, cancels or blocks an order.
    @staticmethod
    def _phase_count(mk: Dict[str, Any], phase: str, key: str,
                     amount: Any) -> None:
        try:
            row = mk.setdefault("phase_stats", {}).setdefault(str(phase), {})
            row[key] = str(D(str(row.get(key) or 0)) + D(str(amount)))
        except Exception:                                 # noqa: BLE001
            pass

    def _phase_resting(self, mk: Dict[str, Any], phase: str,
                       now: float) -> None:
        """Add the time since this market's last pass to what was resting
        through it. A gap longer than 60 s is not counted as resting (the
        bot was not watching)."""
        last = mk.get("phase_t")
        mk["phase_t"] = now
        if last is None:
            return
        dt = now - float(last)
        if not (0 < dt <= 60):
            return
        left = D(0)
        for o in mk["orders"].values():
            if o.get("open"):
                left += max(D(0), D(str(o["qty"])) -
                            D(str(o.get("filled") or "0")))
        if left <= 0:
            return
        self._phase_count(mk, phase, "resting_contract_s",
                          left * D(str(round(dt, 3))))
        est = mk.get("rn1_reward_est") or {}
        if float(est.get("share") or 0.0) > 0 and \
                now - float(est.get("t") or 0) <= 4 * REWARD_WALK_CHECK_S:
            self._phase_count(mk, phase, "qualifying_contract_s",
                              left * D(str(round(dt, 3))))

    def phase_summary(self) -> Dict[str, Any]:
        """Totals by phase across this bot's markets, for the heartbeat."""
        out: Dict[str, Dict[str, D]] = {}
        try:
            for mk in (self.b.d.get("markets") or {}).values():
                for ph, row in (mk.get("phase_stats") or {}).items():
                    agg = out.setdefault(ph, {})
                    for k, v in row.items():
                        agg[k] = agg.get(k, D(0)) + D(str(v))
            return {ph: {k: str(v) for k, v in sorted(row.items())}
                    for ph, row in sorted(out.items())}
        except Exception:                                 # noqa: BLE001
            return {}

    def market_phase(self, slug: str, mk: Dict[str, Any], bbo: Optional[
            Mapping], now: float) -> Dict[str, Any]:
        """pregame / kickoff / live for this market (src/pmus_sports_phase),
        storing the kickoff re-check once it passes."""
        gs = mk.get("game_start")
        ko = mk.get("kickoff") or {}
        fair_ok = True
        if gs is not None and not ko.get("resumed_at") and RN1.EDGE_GATE \
                and now >= float(gs):
            fair_ok = bool(self.intl_fair_band(slug).get("settled"))
        ph = PH.phase(
            now=now, game_start=gs, pull_s=KICKOFF_PULL_S,
            resume_min_s=KICKOFF_RESUME_MIN_S, max_hold_s=KICKOFF_MAX_HOLD_S,
            resumed=bool(ko.get("resumed_at")),
            confirmed_live=slug in (getattr(self.v, "states", {}) or {}),
            fair_ok=fair_ok,
            book_ok=bool(bbo and bbo.get("bid") is not None
                         and bbo.get("ask") is not None))
        if ph["resume"]:
            mk["kickoff"] = dict(ko, resumed_at=now)
            self.b.save()
            log("KICKOFF_RESUME", slug=slug, game_start=gs,
                held_s=round(now - float(ko.get("hold_since") or now), 1),
                pos=mk["pos"], label="re-checked as live: quoting continues "
                                     "with the in-game size")
        return ph

    def _kickoff_hold(self, slug: str, mk: Dict[str, Any],
                      ph: Mapping[str, Any], now: float) -> None:
        """Cancel EVERY resting order in this market (completions too: a
        price from before the start is stale) and place nothing."""
        ko = mk.get("kickoff") or {}
        if not ko.get("hold_since"):
            ko = dict(ko, hold_since=now)
            mk["kickoff"] = ko
        if now - float(getattr(self, "_ko_slate_at", 0.0)) >= 30.0:
            # the re-check needs the venue's live list; selection may not be
            # running (slots full, a cap reached), so read it here too
            self._ko_slate_at = now
            try:
                self.v.live_sports()
            except Exception as exc:                      # noqa: BLE001
                log("discovery_error", err=str(exc)[:160], at="kickoff_hold")
        live = [(oid, o) for oid, o in mk["orders"].items() if o.get("open")]
        if live:
            self._cancel_legs(slug, live)
        if ko.get("logged") != ph["why"] or live:
            ko["logged"] = ph["why"]
            log("KICKOFF_HOLD", slug=slug, why=ph["why"],
                game_start=mk.get("game_start"), cancelled=len(live),
                still_open=sum(1 for o in mk["orders"].values()
                               if o.get("open")),
                pos=mk["pos"], label="no order rests through the start; "
                                     "quoting resumes after the re-check")
        self.b.save()

    def _rn1_mature_own(self, mk: Mapping[str, Any], now: float
                        ) -> Dict[str, Dict[D, D]]:
        """{side: {px: qty left}} of this market's own open orders old
        enough (RN1_NET_OWN_LAG_S) to be in the public book.
        Count all mature owned resting levels, not only the nearest bid.
        Young orders are omitted until the next measurement; the public
        book may not yet contain them."""
        owned = set(self.b.d.get("own_order_ids", []))
        levels: Dict[str, Dict[D, D]] = {"YES": {}, "NO": {}}
        for oid, o in (mk.get("orders") or {}).items():
            side = o.get("side")
            if (oid not in owned or not o.get("open") or side not in levels
                    or o.get("stage") == "orphan"
                    or not o.get("placed_at")
                    or now - float(o["placed_at"]) < RN1_NET_OWN_LAG_S):
                continue
            px = D(o["px"])
            left = D(o["qty"]) - D(o.get("filled", "0"))
            if left > 0:
                levels[side][px] = levels[side].get(px, D(0)) + left
        return levels

    @staticmethod
    def _rn1_visible_own(book: Mapping[str, Any],
                         levels: Mapping[str, Mapping[D, D]], tick: Any
                         ) -> tuple:
        """(yes, no) [(contract px, qty)] of our own levels, each capped
        at the size the public book shows at that price."""
        from src import pmus_sports_rewards as R
        bids, asks = R.book_sides(book)

        def visible_levels(side, public):
            visible: Dict[int, float] = {}
            for p, q in public:
                k = round(float(p) / float(tick))
                visible[k] = visible.get(k, 0.0) + float(q)
            result = []
            for p, q in levels[side].items():
                price = float(p) if side == "YES" else 1.0 - float(p)
                amount = min(float(q), visible.get(
                    round(price / float(tick)), 0.0))
                if amount > 0:
                    result.append((float(p), amount))
            return result
        return visible_levels("YES", bids), visible_levels("NO", asks)

    def rn1_marginal_ev(self, slug: str, mk: Dict[str, Any],
                        bbo: Mapping[str, Any], tick: D,
                        qcfg: Mapping[str, Any], cands: list,
                        planned: list) -> Optional[list]:
        """See RN1_MARGINAL_EV. `cands`: the opening/extension bids to judge,
        each {"side","px","qty","role"}, level order per side (level 0
        first). `planned`: every bid this market would rest (cands and any
        completion), for the incremental reward. Returns one
        RN1.marginal_ev result per candidate, or None when there is no fair
        value to judge against (the caller then changes nothing) -- unless
        RN1_MARGINAL_EV_NO_FAIR applies (marginal test off, no
        international fair value): then each candidate is judged by
        RN1.marginal_ev_no_fair, reward against fill cost, no trade edge."""
        no_fair = False
        if not qcfg.get("edge_gate") or qcfg.get("fair_yes") is None:
            if not (RN1_MARGINAL_EV_NO_FAIR and not RN1.EDGE_GATE
                    and not qcfg.get("edge_gate")
                    and self.intl_fair(slug) is None):
                return None
            no_fair = True
        t = D(str(tick))
        pos = {s_: D(str(mk["pos"][s_])) for s_ in ("YES", "NO")}
        clip = D(str(qcfg.get("qty", RN1.QTY)))
        touch = {"YES": D(str(bbo["bid"])), "NO": ONE - D(str(bbo["ask"]))}
        adv_cfg = qcfg.get("adverse_ticks")
        # reward: credited only when the day's projection qualifies
        inc = (getattr(self, "_rn1_inc", {}) or {}).get(slug) or {}
        per = self.rn1_reward(slug) if inc.get("why") == "qualifies" \
            else None
        # STANDING START (no fair value only). The day's projection counts
        # only what already rests, so before the first bid it is $0 and no
        # no-fair market could ever get one. When a programme IS paying
        # and nothing at all is counted yet (projected $0), the projection
        # is instead that of the ladder that would rest: every planned bid
        # (completions, kept bids, each candidate and the levels above and
        # beside it) except a candidate that is itself refused, under the
        # existing formula (ladder_quote_estimate x RN1_REWARD_PROJECT_H;
        # see _rn1_boot_projection). Only if that self-supporting set
        # reaches RN1_REWARD_MIN_DAY_USD is any of it credited, and each
        # credit is still the haircut incremental reward of the bid alone.
        boot = False
        if no_fair and per is None and \
                inc.get("why") == "below_minimum_payout" and (
                    float(inc.get("projected_usd") or 0.0) <= 0.0 or
                    RN1_REWARD_QUAL_SCOPE in RN1_QUAL_UNIFIED):
            # account_day: the planned ladder is projected together with
            # everything else (accrued today, the other active markets),
            # so it is tested whenever the total is still short
            per = self.rn1_reward(slug)
            boot = bool(per)
        book, own_y, own_n = None, [], []
        rw_err = None
        if per:
            try:
                cache = getattr(self, "_mev_book", None)
                if cache is None:
                    cache = self._mev_book = {}
                hit = cache.get(slug)
                if hit and time.monotonic() - hit[0] < \
                        RN1_MARGINAL_BOOK_TTL_S:
                    book = hit[1]
                else:
                    book = (self._reward_get()(
                        f"/v1/markets/{slug}/book") or {}
                    ).get("marketData") or {}
                    cache[slug] = (time.monotonic(), book)
                own_y, own_n = self._rn1_visible_own(
                    book, self._rn1_mature_own(mk, time.time()), t)
            except Exception as exc:                  # noqa: BLE001
                book, rw_err = None, "book_error:%s" % type(exc).__name__
        if boot and book is not None:
            # fixed point: a refused candidate never carries the others'
            # projection; repeat until the credited set is self-supporting
            alive = {id(c) for c in cands}
            last_seen: Dict[int, Dict[str, Any]] = {}
            while True:
                proj = self._rn1_boot_projection(book, per, planned, cands,
                                                 alive, t, own_y, own_n,
                                                 slug)
                # Judge only the surviving ladder. Rejected candidates
                # must not contribute reward or concentration to survivors.
                if RN1_REWARD_QUAL_SCOPE in RN1_QUAL_UNIFIED:
                    candidate_ids = {id(c) for c in cands}
                    feasible = [c for c in cands if id(c) in alive]
                    feasible_planned = [
                        x for x in planned
                        if id(x) not in candidate_ids or id(x) in alive
                    ]
                    judged = self._rn1_judge(
                        slug, mk, feasible, feasible_planned, no_fair,
                        pos, clip, touch, adv_cfg, qcfg, t,
                        per, book, own_y, own_n, rw_err,
                        {i: proj for i in alive})
                    by_id = {id(c): result
                             for c, result in zip(feasible, judged)}
                    last_seen.update(by_id)
                    # a candidate refused earlier keeps the judgement it was
                    # refused with (its own figures, never a survivor's)
                    out = [by_id.get(id(c)) or last_seen.get(id(c)) or
                           self._rn1_rejected(c) for c in cands]
                else:
                    out = self._rn1_judge(slug, mk, cands, planned, no_fair,
                                          pos, clip, touch, adv_cfg, qcfg, t,
                                          per, book, own_y, own_n, rw_err,
                                          {i: proj for i in alive})
                ok = {id(c) for c, r in zip(cands, out) if r.get("ok")} \
                    & alive
                if ok == alive:
                    return out
                alive = ok
        if RN1_REWARD_QUAL_SCOPE in RN1_QUAL_UNIFIED and not boot:
            # the same surviving-ladder rule outside a standing start: a
            # bid refused on its own economics is taken out of the plan and
            # every survivor is judged again without it (its reward share
            # and the inventory it would have added ahead), until no
            # survivor changes. Survivors only ever see a smaller plan.
            ids = {id(c) for c in cands}
            alive = set(ids)
            seen: Dict[int, Dict[str, Any]] = {}
            while True:
                feasible = [c for c in cands if id(c) in alive]
                fplan = [x for x in planned
                         if id(x) not in ids or id(x) in alive]
                judged = self._rn1_judge(slug, mk, feasible, fplan, no_fair,
                                         pos, clip, touch, adv_cfg, qcfg, t,
                                         per, book, own_y, own_n, rw_err,
                                         None)
                by_id = {id(c): r for c, r in zip(feasible, judged)}
                seen.update(by_id)
                ok = {i for i, r in by_id.items() if r.get("ok")}
                if ok == alive:
                    return [by_id.get(id(c)) or seen.get(id(c)) or
                            self._rn1_rejected(c) for c in cands]
                alive = ok
        return self._rn1_judge(slug, mk, cands, planned, no_fair, pos, clip,
                               touch, adv_cfg, qcfg, t,
                               None if boot else per, book, own_y, own_n,
                               rw_err, None)

    @staticmethod
    def _rn1_rejected(c: Mapping[str, Any]) -> Dict[str, Any]:
        """The result for a candidate taken out of the feasible ladder: the
        same keys every reader of a marginal-EV result uses (the campaign
        caps' economic priority reads ev_usd_per_hour), credited nothing."""
        return {"ok": False, "why": "marginal_ev_not_positive",
                "side": c["side"], "level": c.get("level", 0),
                "px": str(c["px"]), "qty": str(c["qty"]),
                "reward_why": "rejected_from_feasible_ladder",
                "reward_usd_per_hour": 0.0, "ev_usd_per_hour": 0.0,
                "trading_usd_per_hour": 0.0}

    def _rn1_ev_hold(self, mk: Dict[str, Any], c: Mapping[str, Any],
                     r: Mapping[str, Any], tick: Any, now: float,
                     seen: set) -> bool:
        """RN1_EV_HOLD: keep this failing candidate only if it is ALREADY
        resting (an open opening/extension order on its side at its
        price), its hold began at most RN1_EV_HOLD_MAX_S ago, and
        EV/h x window + RN1_REPLACE_COST_USD >= 0. Never for a new bid."""
        try:
            px = D(str(c["px"]))
            resting = any(
                o.get("open") and o.get("side") == c["side"] and
                not is_completion_stage(o.get("stage")) and
                abs(D(str(o["px"])) - px) < RN1.REPRICE_TICKS * D(str(tick))
                for o in (mk.get("orders") or {}).values())
            if not resting:
                return False
            # only a dip in an economics that is still being read: never
            # when the reward is lost, unread or unqualified (programme
            # withdrawn or ended, feed or book read failing, projection
            # below the minimum payout) -- those withdraw at once
            if r.get("reward_why") is not None or \
                    float(r.get("reward_usd_per_hour") or 0.0) <= 0.0:
                return False
            ev = float(r.get("ev_usd_per_hour"))
            key = "%s|%s" % (c["side"], px)
            hold = mk.setdefault("ev_hold", {})
            t0 = float(hold.setdefault(key, now))
            seen.add(key)
            if now - t0 > RN1_EV_HOLD_MAX_S:
                return False
            return ev * RN1_EV_HOLD_MAX_S / 3600.0 + RN1_REPLACE_COST_USD \
                >= 0.0
        except Exception:                                 # noqa: BLE001
            return False

    def _rn1_boot_projection(self, book, per, planned, cands, alive, t,
                             own_y, own_n, slug: str = "") -> float:
        """THEORETICAL projected USD over RN1_REWARD_PROJECT_H of the
        ladder that would rest: every planned bid except a candidate not
        in `alive` (refused). The existing projection's formula
        (rn1_incentive: ladder_quote_estimate of the resting set x
        RN1_REWARD_PROJECT_H), applied to the planned set in place of the
        resting one. Never raises (doubt = 0)."""
        try:
            from src import pmus_sports_rewards as RW
            ids = {id(x) for x in cands}
            ys, ns = [], []
            for x in planned:
                if id(x) in ids and id(x) not in alive:
                    continue
                (ys if x["side"] == "YES" else ns).append(
                    (float(x["px"]), float(x["qty"])))
            if not (ys or ns):
                return 0.0
            est = RW.ladder_quote_estimate(book, per, ys, ns, float(t),
                                           own_y, own_n)
            rate = max(0.0, float(est.get("est_usd_per_hour") or 0.0)) * \
                rn1_reward_cal(slug, per)
            if RN1_REWARD_QUAL_SCOPE in RN1_QUAL_UNIFIED:
                # the same total as every other site (scope-dependent):
                # this market's planned ladder plus what the scope adds
                return float(self.rn1_reward_qualifies(
                    slug, extra_usd_h=rate)["projected_usd"])
            return rate * RN1_REWARD_PROJECT_H
        except Exception:                                 # noqa: BLE001
            return 0.0

    def _rn1_judge(self, slug, mk, cands, planned, no_fair, pos, clip,
                   touch, adv_cfg, qcfg, t, per, book, own_y, own_n, rw_err,
                   qual) -> list:
        """One RN1.marginal_ev(_no_fair) result per candidate (see
        rn1_marginal_ev). `qual`: None, or {id(candidate): standing-start
        projected USD}; then only a candidate whose projection reaches
        RN1_REWARD_MIN_DAY_USD is credited any reward."""
        from src import pmus_sports_rewards as RW
        out = []
        ahead = {"YES": D(0), "NO": D(0)}
        for x in planned:            # a completion fills before what is behind
            if x.get("role") == "complete" and x["side"] in ahead:
                ahead[x["side"]] += D(str(x["qty"]))
        for c in cands:
            side, px, qty = c["side"], D(str(c["px"])), D(str(c["qty"]))
            other = "NO" if side == "YES" else "YES"
            held = max(D(0), pos[side] + ahead[side] - pos[other])
            clips = int((held / clip).to_integral_value(
                rounding="ROUND_CEILING")) if clip > 0 else 0
            if no_fair:
                ev = {"risk": RN1.concentration_risk(
                    tick=t, unpaired_clips=clips,
                    risk_step_ticks=qcfg.get("risk_step_ticks")),
                    "fee": RN1.fee(px, RN1.MAKER_FEE_RATE)}
            else:
                adv = adv_cfg.get(c["role"], adv_cfg.get("default")) \
                    if isinstance(adv_cfg, Mapping) else adv_cfg
                fy = qcfg.get("fair_yes_lo" if side == "YES"
                              else "fair_yes_hi", qcfg["fair_yes"])
                if fy is None:
                    fy = qcfg["fair_yes"]
                ev = RN1.edge(side=side, px=px, tick=t, fair_yes=fy,
                              adds_exposure=True, unpaired_clips=clips,
                              fee_rate=RN1.MAKER_FEE_RATE,
                              adverse_ticks=adv,
                              risk_step_ticks=qcfg.get("risk_step_ticks"),
                              incentive_ticks=0)
            if not no_fair and ev.get("why") == "no_fair":
                out.append({"ok": False, "why": "no_fair"})
                ahead[side] += qty
                continue
            depth = max(0, int(((touch[side] - px) / t).to_integral_value(
                rounding="ROUND_HALF_UP")))
            loss = D(str(rn1_fill_param(RN1_FILL_LOSS, depth, slug)))
            rew, why_r = 0.0, rw_err or ("no_qualifying_reward"
                                          if not per else None)
            boot_usd = None if qual is None else qual.get(id(c), 0.0)
            if boot_usd is not None and not rw_err and \
                    boot_usd + 1e-9 < RN1_REWARD_MIN_DAY_USD:
                why_r = "below_minimum_payout"
            if per and book is not None and (
                    boot_usd is None or why_r is None):
                try:
                    rest = [x for x in planned if x is not c]
                    m = RW.marginal_ladder_reward(
                        book, per,
                        [(float(x["px"]), float(x["qty"])) for x in rest
                         if x["side"] == "YES"],
                        [(float(x["px"]), float(x["qty"])) for x in rest
                         if x["side"] == "NO"],
                        float(t), own_y, own_n, side, float(px), float(qty))
                    # flag off: the clamped RANK_REWARD_HAIRCUT, as before
                    rew = max(0.0, float(m["incremental_reward_usd_per_hour"])
                              ) * rn1_reward_mult(slug, per)
                except Exception as exc:              # noqa: BLE001
                    rew, why_r = 0.0, "reward_error:%s" % type(exc).__name__
            if no_fair:
                # no trade edge: there is no fair value (see
                # RN1_MARGINAL_EV_NO_FAIR); the fill loss is the
                # adverse-selection proxy
                r = RN1.marginal_ev_no_fair(
                    px=px, qty=qty,
                    fill_rate=rn1_fill_param(RN1_FILL_RATE, depth, slug),
                    fill_loss=loss, fee=ev["fee"], risk=ev["risk"],
                    reward_usd_per_hour=rew, rebate=D(0))
            else:
                r = RN1.marginal_ev(
                    fair=ev["fair"], px=px, qty=qty,
                    fill_rate=rn1_fill_param(RN1_FILL_RATE, depth, slug),
                    fee=ev["fee"], gate_adverse=ev["adverse"],
                    adverse=max(D(ev["adverse"]), loss), risk=ev["risk"],
                    reward_usd_per_hour=rew, rebate=D(0))
            r.update(side=side, depth=depth, level=c.get("level", 0),
                     reward_why=why_r)
            if boot_usd is not None:
                r["reward_projection"] = {
                    "basis": "standing_start_planned_ladder",
                    "projected_usd": round(boot_usd, 4),
                    "min_payout_usd": RN1_REWARD_MIN_DAY_USD,
                    "label": "THEORETICAL reward projection; not a payment"}
            out.append(r)
            ahead[side] += qty
        return out

    def _rn1_theo_accrue(self, slug: str, mk: Dict[str, Any],
                         now: float) -> None:
        """RN1_REWARD_CALIBRATION: accrue this market's THEORETICAL reward
        (rn1_reward_est, the venue rule on the resting legs, full pool, no
        haircut) over the time since the last pass (at most 300 s per
        step), keyed by the venue's reward day, in mk["reward_theo"]. Only
        while a programme is paying and the estimate is fresh. Recording
        only: nothing reads it to decide an order."""
        try:
            last = mk.get("reward_theo_t")
            mk["reward_theo_t"] = now
            if last is None:
                return
            dt = max(0.0, min(300.0, now - float(last)))
            est = mk.get("rn1_reward_est") or {}
            rate = float(est.get("est_usd_per_hour") or 0.0)
            if dt <= 0 or rate <= 0 or time.time() - float(
                    est.get("t") or 0) > 4 * REWARD_WALK_CHECK_S or \
                    not self.rn1_reward(slug):
                return
            day = datetime.fromtimestamp(
                now + RN1_REWARD_CAL_DAY_OFFSET_H * 3600.0,
                timezone.utc).strftime("%Y-%m-%d")
            th = mk.setdefault("reward_theo", {})
            th[day] = round(float(th.get(day) or 0.0) + rate * dt / 3600.0,
                            6)
            # the pool this accrual belongs to (RN1_REWARD_QUAL_SCOPE
            # "program_period"): programme id + period, never pooled with
            # another programme or another period of the same market
            per_ = self.rn1_reward(slug) or {}
            tp = None
            for pk in {rn1_pool_key(per_), rn1_pool_key(per_, slug)}:
                if pk:
                    tp = mk.setdefault("reward_theo_pool", {}) \
                        if tp is None else tp
                    tp[pk] = round(float(tp.get(pk) or 0.0) +
                                   rate * dt / 3600.0, 6)
        except Exception:                                 # noqa: BLE001
            return

    def reward_calibration(self, rows: Any) -> Optional[Dict[str, Any]]:
        """RN1_REWARD_CALIBRATION: log REWARD_CALIBRATION (at most every
        RN1_REWARD_CAL_LOG_S) from the venue rows just read and the bot's
        own theoretical accrual. READ-ONLY; never applied automatically."""
        if not RN1_REWARD_CALIBRATION:
            return None
        nowm = time.monotonic()
        last = getattr(self, "_cal_logged", None)
        if last is not None and nowm - last < RN1_REWARD_CAL_LOG_S:
            return None
        try:
            from src import pmus_reward_calibration as RC
            theo = {s_: dict(m_.get("reward_theo") or {})
                    for s_, m_ in (self.b.d.get("markets") or {}).items()
                    if m_.get("reward_theo")}
            got = RC.calibrate(rows, theo)
        except Exception as exc:                          # noqa: BLE001
            log("REWARD_CALIBRATION", ok=False,
                err="%s:%s" % (type(exc).__name__, str(exc)[:120]))
            return None
        self._cal_logged = nowm
        log("REWARD_CALIBRATION", ok=True,
            by_competition=got["by_competition"],
            by_program=got["by_program"],
            table_in_force=rn1_reward_cal_table(),
            pool_scope=RN1_REWARD_POOL_SCOPE,
            haircut_fallback=RANK_REWARD_HAIRCUT,
            day_offset_h=RN1_REWARD_CAL_DAY_OFFSET_H,
            label=got["label"])
        return got

    def _rn1_reward_estimate(self, slug: str, mk: Dict[str, Any],
                             per: Mapping[str, Any], tick: D) -> None:
        """THEORETICAL reward rate of this market's CURRENT resting legs
        under the venue's published scoring rule, kept in `rn1_reward_est`
        (one public book read per REWARD_WALK_CHECK_S, or when the legs
        change). Its own key: the reward-first path's state is untouched."""
        now = time.time()
        levels = self._rn1_mature_own(mk, now)
        key = {side: [[str(p), str(q)] for p, q in sorted(lv.items())]
               for side, lv in levels.items() if lv}
        est = mk.get("rn1_reward_est")
        if est and est.get("legs") == key and now - float(
                est.get("t") or 0) < REWARD_WALK_CHECK_S:
            return
        if not key:
            mk["rn1_reward_est"] = {"t": now, "legs": key,
                                    "est_usd_per_hour": 0.0}
            return
        from src import pmus_sports_rewards as R
        book = (self._reward_get()(f"/v1/markets/{slug}/book") or {}
                ).get("marketData") or {}
        y, n = self._rn1_visible_own(book, levels, tick)
        r = R.ladder_quote_estimate(book, per, y, n, float(tick), y, n)
        mk["rn1_reward_est"] = {"t": time.time(), "legs": key,
                                "share": r.get("share"),
                                "est_usd_per_hour": r.get("est_usd_per_hour"),
                                "max_spread_ok": r.get("max_spread_ok")}

    def rn1_reward_day_projection(self, slug: Optional[str] = None,
                                  extra_usd_h: float = 0.0,
                                  now: Optional[float] = None
                                  ) -> Dict[str, Any]:
        """RN1_REWARD_QUAL_SCOPE "account_day": the THEORETICAL, calibrated
        day total the minimum payout is tested against. Pure reads; never
        raises (doubt = 0). `slug`'s own rate is replaced by `extra_usd_h`
        (already calibrated) when that is given."""
        now = time.time() if now is None else now
        out = {"scope": "account_day", "accrued_today_usd": 0.0,
               "rate_usd_per_hour": 0.0, "horizon_h": 0.0,
               "projected_usd": 0.0, "markets": 0,
               "label": "THEORETICAL reward projection; not a payment"}
        try:
            off = RN1_REWARD_CAL_DAY_OFFSET_H * 3600.0
            dt_day = datetime.fromtimestamp(now + off, timezone.utc)
            day = dt_day.strftime("%Y-%m-%d")
            day_end = datetime(dt_day.year, dt_day.month, dt_day.day,
                               tzinfo=timezone.utc).timestamp() + 86400.0 \
                - off
            acc = 0.0
            for s_, m_ in (self.b.d.get("markets") or {}).items():
                th = (m_.get("reward_theo") or {}).get(day)
                if th:
                    acc += float(th) * rn1_reward_cal(s_, self.rn1_reward(s_))
            rate = 0.0
            plan = getattr(self, "_net_choice", None) or {}
            seen = 0
            for s_ in self.active():
                if s_ == slug and extra_usd_h:
                    continue
                per = self.rn1_reward(s_)
                if not per:
                    continue
                est = (self.b.m(s_).get("rn1_reward_est") or {})
                r_ = 0.0
                if now - float(est.get("t") or 0) <= \
                        4 * REWARD_WALK_CHECK_S:
                    r_ = float(est.get("est_usd_per_hour") or 0.0) * \
                        rn1_reward_cal(s_, per)
                pl = plan.get(s_) or {}
                if r_ <= 0.0 and now - float(pl.get("t") or 0) <= \
                        RN1_REWARD_PLAN_TTL_S:
                    r_ = float(pl.get("reward_usd_per_hour") or 0.0)
                rate += max(0.0, r_)
                seen += 1
            rate += max(0.0, float(extra_usd_h or 0.0))
            h = max(0.0, RN1_REWARD_PROJECT_H)
            w = lock_window()
            if w is not None and float(w[1]) > now:
                h = min(h, (float(w[1]) - now) / 3600.0)
            h = min(h, max(0.0, (day_end - now) / 3600.0))
            out.update(accrued_today_usd=round(acc, 4),
                       rate_usd_per_hour=round(rate, 6),
                       horizon_h=round(h, 4), markets=seen,
                       projected_usd=round(acc + rate * h, 4))
        except Exception:                                 # noqa: BLE001
            pass
        return out

    def rn1_reward_pool_projection(self, slug: Optional[str],
                                   extra_usd_h: float = 0.0,
                                   now: Optional[float] = None,
                                   program_level: bool = False
                                   ) -> Dict[str, Any]:
        """RN1_REWARD_QUAL_SCOPE "program_period": the THEORETICAL,
        calibrated total of `slug`'s own reward pool (programId|period):
        what this bot has accrued in that pool, plus the calibrated rate of
        every active market currently paying from the SAME pool (resting
        estimate, or fresh planned rate when nothing rests) and `extra_usd_h`
        for `slug`, over min(RN1_REWARD_PROJECT_H, window left, period
        left). No paying pool for `slug` = 0. Never raises."""
        now = time.time() if now is None else now
        out = {"scope": RN1_REWARD_QUAL_SCOPE if RN1_REWARD_QUAL_SCOPE in
               RN1_POOL_SCOPES else "program_period", "pool": None,
               "accrued_pool_usd": 0.0, "rate_usd_per_hour": 0.0,
               "horizon_h": 0.0, "projected_usd": 0.0, "markets": 0,
               "label": "THEORETICAL reward projection; not a payment"}
        try:
            per = self.rn1_reward(slug) if slug else None
            contract = out["scope"] == "contract_period" and \
                not program_level
            if program_level:
                out["scope"] = "program_period"
            pk = rn1_pool_key(per, slug) if contract else rn1_pool_key(per)
            if not pk:
                out["why"] = "no_paying_pool"
                return out
            out["pool"] = pk
            acc = 0.0
            for s_, m_ in (self.b.d.get("markets") or {}).items():
                if contract and s_ != slug:
                    continue                 # another contract's pool
                v_ = (m_.get("reward_theo_pool") or {}).get(pk)
                if v_:
                    acc += float(v_) * rn1_reward_cal(s_, per)
            rate, seen = 0.0, 0
            plan = getattr(self, "_net_choice", None) or {}
            for s_ in self.active():
                if s_ == slug and extra_usd_h:
                    continue
                per_s = self.rn1_reward(s_)
                if (rn1_pool_key(per_s, s_) if contract else
                        rn1_pool_key(per_s)) != pk:
                    continue                 # another contract/programme/period
                est = (self.b.m(s_).get("rn1_reward_est") or {})
                r_ = 0.0
                if now - float(est.get("t") or 0) <= \
                        4 * REWARD_WALK_CHECK_S:
                    r_ = float(est.get("est_usd_per_hour") or 0.0) * \
                        rn1_reward_cal(s_, per_s)
                pl = plan.get(s_) or {}
                if r_ <= 0.0 and now - float(pl.get("t") or 0) <= \
                        RN1_REWARD_PLAN_TTL_S:
                    r_ = float(pl.get("reward_usd_per_hour") or 0.0)
                rate += max(0.0, r_)
                seen += 1
            rate += max(0.0, float(extra_usd_h or 0.0))
            h = max(0.0, RN1_REWARD_PROJECT_H)
            w = lock_window()
            if w is not None and float(w[1]) > now:
                h = min(h, (float(w[1]) - now) / 3600.0)
            left = self.rn1_period_left_s(slug, per, now)
            if left is not None:
                h = min(h, max(0.0, left) / 3600.0)
            out.update(accrued_pool_usd=round(acc, 4),
                       rate_usd_per_hour=round(rate, 6),
                       horizon_h=round(h, 4), markets=seen,
                       projected_usd=round(acc + rate * h, 4))
        except Exception:                                 # noqa: BLE001
            pass
        return out

    def rn1_period_left_s(self, slug: Optional[str],
                          per: Optional[Mapping[str, Any]],
                          now: float) -> Optional[float]:
        """Seconds left in the paying period, where its end is known: the
        event start for day-of pre-game, event start - 6 h for early
        pre-game. Live runs to settlement (unknown): None."""
        try:
            prd = str((per or {}).get("period") or "")
            gs = (self.b.d["markets"].get(slug) or {}).get("game_start") \
                if slug else None
            if gs is None and slug:
                gs = (getattr(self, "_game_start", {}) or {}).get(slug)
            if gs is None:
                return None
            gs = float(gs) if not isinstance(gs, str) else \
                datetime.fromisoformat(gs.replace("Z", "+00:00")).timestamp()
            if prd == "day_of":
                return gs - now
            if prd == "early":
                return gs - 6 * 3600.0 - now
        except Exception:                                 # noqa: BLE001
            return None
        return None

    def rn1_reward_qualifies(self, slug: Optional[str] = None,
                             extra_usd_h: float = 0.0) -> Dict[str, Any]:
        """The one qualification every site uses (see RN1_REWARD_QUAL_SCOPE):
        "program_period" tests `slug`'s own pool; "account_day" the
        account's day total."""
        if rn1_threshold_rule():
            pj = self.rn1_reward_pool_projection(slug, extra_usd_h,
                                                 program_level=True)
            pf = rn1_paid_fraction()
            pj["min_payout_usd"] = RN1_REWARD_MIN_DAY_USD
            pj["rule"] = "threshold"
            pj["paid_fraction"] = pf
            pj["qualifies"] = bool(pf) and bool(pj.get("pool")) and \
                pj["projected_usd"] + 1e-9 >= RN1_REWARD_MIN_DAY_USD
            return pj
        if RN1_REWARD_QUAL_SCOPE in RN1_POOL_SCOPES:
            pj = self.rn1_reward_pool_projection(slug, extra_usd_h)
        else:
            pj = self.rn1_reward_day_projection(slug, extra_usd_h)
        pj["min_payout_usd"] = RN1_REWARD_MIN_DAY_USD
        pj["qualifies"] = pj["projected_usd"] + 1e-9 >= \
            RN1_REWARD_MIN_DAY_USD
        if rn1_paid_rule():
            pf = rn1_paid_fraction()
            per_ = self.rn1_reward(slug) if slug else None
            pool_ok = bool(per_) and (
                RN1_REWARD_QUAL_SCOPE not in RN1_POOL_SCOPES or
                bool(pj.get("pool")))           # a pool scope needs its pool
            pj.update(rule="paid_fraction", paid_fraction=pf,
                      qualifies=bool(pf) and pool_ok)
        return pj

    def rn1_incentive(self, slug: str, mk: Optional[Dict[str, Any]] = None,
                      tick: Any = None) -> Dict[str, Any]:
        """{"ticks", "why", ...}: the reward credit the marginal test may
        apply for this market right now. Zero unless a programme is paying
        AND the theoretical reward of what the bot is resting, projected
        over RN1_REWARD_PROJECT_H across its active markets, reaches the
        venue's minimum payout. Never raises; any doubt is zero."""
        out: Dict[str, Any] = {"ticks": 0, "why": "no_paying_programme"}
        try:
            per = self.rn1_reward(slug)
            if not per or RN1_REWARD_CREDIT_TICKS <= 0:
                return out
            mk = self.b.m(slug) if mk is None else mk
            if tick is not None:
                self._rn1_reward_estimate(slug, mk, per, D(str(tick)))
            if rn1_threshold_rule():
                pf = rn1_paid_fraction()
                pj = self.rn1_reward_qualifies(slug)
                out.update(rule="threshold", paid_fraction=pf,
                           projected_usd=pj.get("projected_usd"),
                           min_payout_usd=RN1_REWARD_MIN_DAY_USD,
                           pool=pj.get("pool"), horizon_h=pj.get("horizon_h"),
                           label="THEORETICAL reward x venue paid fraction, "
                                 "credited only above the programme-period "
                                 "minimum; not a payment")
                if not pf:
                    out["why"] = "paid_fraction_unavailable"
                    return out
                if not pj.get("qualifies"):
                    out["why"] = "below_minimum_payout"
                    return out
                out.update(ticks=RN1_REWARD_CREDIT_TICKS, why="qualifies")
                return out
            if rn1_paid_rule():
                pf = rn1_paid_fraction()
                out.update(rule="paid_fraction", paid_fraction=pf,
                           label="THEORETICAL reward x venue paid fraction; "
                                 "not a payment")
                if not pf:
                    out["why"] = "paid_fraction_unavailable"
                    return out
                out.update(ticks=RN1_REWARD_CREDIT_TICKS, why="qualifies")
                return out
            if RN1_REWARD_QUAL_SCOPE in RN1_QUAL_UNIFIED:
                pj = self.rn1_reward_qualifies(slug)
                out.update(projected_usd=pj["projected_usd"],
                           min_payout_usd=RN1_REWARD_MIN_DAY_USD,
                           scope=RN1_REWARD_QUAL_SCOPE,
                           pool=pj.get("pool"),
                           accrued_usd=pj.get("accrued_pool_usd",
                                              pj.get("accrued_today_usd")),
                           horizon_h=pj["horizon_h"],
                           label="THEORETICAL reward projection; not a "
                                 "payment")
                if not pj["qualifies"]:
                    out["why"] = "below_minimum_payout"
                    return out
                out.update(ticks=RN1_REWARD_CREDIT_TICKS, why="qualifies")
                return out
            total = 0.0
            for s in self.active():
                est = (self.b.m(s).get("rn1_reward_est") or {})
                if time.time() - float(est.get("t") or 0) > \
                        4 * REWARD_WALK_CHECK_S:
                    continue                  # an old estimate is not one
                total += float(est.get("est_usd_per_hour") or 0.0) * (
                    rn1_reward_cal(s, self.rn1_reward(s))
                    if RN1_REWARD_CALIBRATION else 1.0)
            proj = total * RN1_REWARD_PROJECT_H
            out.update(projected_usd=round(proj, 4),
                       min_payout_usd=RN1_REWARD_MIN_DAY_USD,
                       label="THEORETICAL reward projection; not a payment")
            if proj + 1e-9 < RN1_REWARD_MIN_DAY_USD:
                out["why"] = "below_minimum_payout"
                return out
            out.update(ticks=RN1_REWARD_CREDIT_TICKS, why="qualifies")
            return out
        except Exception as exc:                          # noqa: BLE001
            return {"ticks": 0, "why": "reward_estimate_error:%s"
                    % type(exc).__name__}

    def rn1_quote_cfg(self, slug: str, mk: Optional[Dict[str, Any]] = None,
                      tick: Any = None) -> Dict[str, Any]:
        """Inputs to RN1.quotes for this market. With the marginal test on
        (RN1.EDGE_GATE) every bid needs the international fair value; the
        adverse-selection allowance is RN1.ADVERSE_TICKS, plus the event
        type's jump allowance for a bid that adds to an unpaired side."""
        if not RN1.EDGE_GATE:
            # MEASUREMENT ONLY (owner 2026-11-25: "Restore reward
            # qualification measurement independently of international
            # pricing"). Live 25 Nov: with this test off the reward
            # estimate was never computed, so three hours of resting bids
            # showed no qualifying time and `incentive: null`. The estimate
            # and its projection are refreshed here in every mode; the
            # result is recorded and returned to nobody, so no bid changes.
            self._rn1_inc = getattr(self, "_rn1_inc", {})
            self._rn1_inc[slug] = dict(self.rn1_incentive(slug, mk, tick),
                                       measured_only=True)
            return self.rn1_extend_cfg(slug)
        typ = str(((getattr(self.v, "states", {}) or {}).get(slug) or {}
                   ).get("type") or "").lower()
        base = RN1.ADVERSE_TICKS
        inc = self.rn1_incentive(slug, mk, tick)
        self._rn1_inc = getattr(self, "_rn1_inc", {})
        self._rn1_inc[slug] = inc
        band = self.intl_fair_band(slug)
        self._rn1_fair = getattr(self, "_rn1_fair", {})
        self._rn1_fair[slug] = {k: (None if v is None else str(v))
                                if k in ("mid", "lo", "hi", "range") else v
                                for k, v in band.items()}
        return {"edge_gate": True,
                "fair_yes": band["mid"] if band["settled"] else None,
                "fair_yes_lo": band["lo"], "fair_yes_hi": band["hi"],
                "adverse_ticks": {"open": base, "complete": base,
                                  "extend": base + RN1_JUMP_TICKS.get(typ, 0),
                                  "default": base},
                "risk_step_ticks": RN1.RISK_STEP_TICKS,
                "incentive_ticks": inc["ticks"]}

    def rn1_extend_cfg(self, slug: str) -> Dict[str, Any]:
        """Inputs to the marginal test for adding to an unpaired side."""
        if not RN1.EXTEND_EV or self.guard is None:
            return {}
        typ = str(((getattr(self.v, "states", {}) or {}).get(slug) or {}
                   ).get("type") or "").lower()
        extra = RN1_JUMP_TICKS.get(typ, 0)
        if self.rn1_reward(slug):
            extra -= RN1_REWARD_CREDIT_TICKS
        return {"extend_ev": True, "fair_yes": self.intl_fair(slug),
                "extend_extra_ticks": extra}

    def _gap_hold(self, slug: str, mk: Dict[str, Any], q: Dict[str, Any],
                  want: Dict[str, Any], bbo: Mapping,
                  new_block: Optional[str], now: float) -> None:
        """See RN1_GAP_HOLD_S. Records each exposure-adding bid that passed
        the full test, and puts back a RESTING one whose only failure this
        pass is a missing fair value."""
        lp = mk.setdefault("last_pass", {})
        for side in ("YES", "NO"):
            tg = want.get(side)
            if tg is not None:
                if tg["role"] != "complete":
                    lp[side] = {"t": now, "px": str(tg["px"]),
                                "role": tg["role"], "bid": str(bbo["bid"]),
                                "ask": str(bbo["ask"])}
                else:
                    lp.pop(side, None)
                continue
            rec = lp.get(side)
            if RN1_GAP_HOLD_S <= 0 or new_block or rec is None or \
                    q.get("why_" + side) != "no_fair":
                lp.pop(side, None)       # any other failure ends the claim
                continue
            if now - float(rec["t"]) > RN1_GAP_HOLD_S:
                lp.pop(side, None)
                q["why_" + side] = "no_fair:hold_expired"
                continue
            if str(bbo["bid"]) != rec["bid"] or str(bbo["ask"]) != rec["ask"]:
                lp.pop(side, None)
                q["why_" + side] = "no_fair:venue_price_moved"
                continue
            band = (getattr(self, "_rn1_fair", {}) or {}).get(slug) or {}
            if band.get("calm") is False:
                lp.pop(side, None)
                q["why_" + side] = "no_fair:intl_price_moving"
                continue
            # the bid that passed, or one left resting below it (never one
            # above the price the test passed)
            rest = [o for o in mk["orders"].values()
                    if o.get("open") and o.get("side") == side
                    and not is_completion_stage(o.get("stage"))
                    and D(str(o["px"])) <= D(rec["px"])]
            if len(rest) != 1:
                lp.pop(side, None)
                continue
            if self.depth_block(slug, side):
                lp.pop(side, None)
                q["why_" + side] = "no_fair:depth"
                continue
            o = rest[0]
            want[side] = {"px": D(str(o["px"])),
                          "qty": D(str(o["qty"])) - D(str(o.get("filled")
                                                          or "0")),
                          "role": rec["role"], "held": True}
            q["why_" + side] = "held_fair_gap"
            self._gap_holds = int(getattr(self, "_gap_holds", 0)) + 1

    def _rn1_step(self, slug: str, mk: Dict[str, Any], spec: Mapping,
                  bbo: Mapping, tick: D) -> None:
        """One RN1-replica pass for one market: compute RN1's resting maker
        bids (src/pmus_rn1_replica.quotes) and reconcile our open orders to
        them. Openings/extensions obey every new-exposure gate; completions
        (profitable pairs only) continue under them, as elsewhere. Post-only
        BUYs only: nothing here sells or takes."""
        now = time.time()
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        if TAPE is not None:
            (TAPE.priority.discard if y == n else TAPE.priority.add)(slug)
        if y == n and y > 0 and mk.get("pair_logged_at") != str(y):
            cpair = (D(mk["cost"]["YES"]) + D(mk["cost"]["NO"])) / y
            self.b.d["pairs"].append({"slug": slug, "qty": str(y),
                                      "pair_cost": str(cpair),
                                      "t": now_iso()})
            mk["pair_logged_at"] = str(y)
            log("PAIR_LOCKED", slug=slug, pairs=y, avg_pair_cost=cpair,
                locked_edge_per_pair=ONE - cpair, mode="rn1",
                label="locked pair economics; realized at settlement")
        rng = self._vol(slug, bbo, now)
        if RN1_REWARD_CALIBRATION:
            self._rn1_theo_accrue(slug, mk, now)
        ph = self.market_phase(slug, mk, bbo, now)
        self._phase_resting(mk, mk.get("phase_now") or ph["phase"], now)
        mk["phase_now"] = ph["phase"]
        if ph["phase"] == PH.KICKOFF:
            self._kickoff_hold(slug, mk, ph, now)
            return
        new_block = None
        pause_reason: Optional[str] = None   # see RN1_PAUSE_DEPTH
        if os.getenv(ENABLED_ENV, "").upper() != "YES":
            new_block = "not_enabled"
        elif submission_locked():
            new_block = "submission_lock:" + str(lock_state()["why"])
        elif self.b.total_unmatched() >= TOTAL_CAP:
            new_block = "total_cap"
        elif self.daily_stopped():
            new_block = "daily_loss_stop"
        elif self.halt_new_exposure():
            new_block = "halt_new_exposure"
        elif self.shared_halt() is not None:
            new_block = "shared_halt"
        elif self.account_risk_blocks() is not None:
            new_block = "account_risk"
        elif self.pilot_block():
            new_block = self.pilot_block()
        elif self.campaign_marker_mismatch():
            new_block = "campaign_marker_mismatch"
        elif self.audit_blocks():
            new_block = self.audit_blocks()
        # account-wide blocks hold the slot (they lift); a block about THIS
        # market (late, excluded, open-EV gate) frees a flat market's slot,
        # exactly as the two-sided path releases a gated flat market
        account_block = new_block
        late = late_for(
            slug, (getattr(self.v, "states", {}) or {}).get(slug))
        if excluded(slug):
            late = "excluded"
        if not new_block and late:
            new_block = "late:" + str(late)
        if not new_block and self.scope_block(slug):
            new_block = self.scope_block(slug)
        if not new_block and OPEN_EV_GATE and not self.open_gate(slug)["allow"]:
            new_block = "open_ev_gate"
        if not new_block and self.ws is not None and \
                now < float(mk.get("jump_pause_until") or 0):
            if RN1_PAUSE_DEPTH:
                pause_reason = "jump_pause"
            else:
                new_block = "jump_pause"
        if not new_block and slate() == "live" and \
                ph["phase"] == "pregame":
            # LIVE 2026-11-25 17:08Z: with the slate switched to in-play
            # only, a market already tracked from before still placed five
            # pre-game opening bids. The slate decides selection; this
            # holds the same rule for markets already on the list. Only
            # openings and extensions stop; completions continue.
            new_block = "slate_live_pregame"
        if not new_block and ph["expired"]:
            new_block = "kickoff_unconfirmed"
        pin = rn1_pin()
        if not new_block and pin is not None and slug not in pin:
            new_block = "not_pinned"
        vp = PH.pause_until(self._vol_hist.get(slug) or (), now,
                            window_s=VOL_WINDOW_S, move_usd=VOL_PAUSE_USD,
                            calm_s=VOL_CALM_S,
                            prev_until=float(mk.get("vol_pause_until") or 0))
        if vp != float(mk.get("vol_pause_until") or 0):
            mk["vol_pause_until"] = vp
            log("VOL_PAUSE", slug=slug, range=rng, window_s=VOL_WINDOW_S,
                threshold=VOL_PAUSE_USD, calm_s=VOL_CALM_S, phase=ph["phase"],
                label="price moving fast: opening and extension bids "
                      "pulled until it is calm")
        if not new_block and now < vp:
            if RN1_PAUSE_DEPTH:
                pause_reason = pause_reason or "vol_pause"
            else:
                new_block = "vol_pause"
        if not new_block and self.rn1_market_slots_full(slug, mk):
            new_block = "max_markets"
        net_neg = False
        if not new_block and RN1_NET_RANK and not tf_net_bypass() and \
                not self.rn1_quote_qualifies(slug, mk):
            # no opening or extension while the fresh estimate (re-priced
            # after every fill) is not positive, flat or not; completions
            # go on. LIVE 25 Nov 23:45: markets carried over from before
            # the window quoted for up to RELEASE_UNQUALIFIED_S at a
            # negative estimate, and one filled.
            # With RN1_MARGINAL_EV the block is decided below, per bid.
            if RN1_MARGINAL_EV:
                net_neg = True
            else:
                new_block = "net_not_positive"
        qcfg = dict(self.rn1_quote_cfg(slug, mk, tick))
        if RN1_MARGINAL_EV:
            qcfg["ev_priority"] = True
        if RN1_FIT_ALL_LEVELS:
            qcfg["fit_qty"] = True
            qcfg["fit_pair_cap"] = True
        join_why = self.rn1_join_touch(slug, mk, bbo, tick, rng, now)
        if join_why is None and not pause_reason:
            qcfg["depth_ticks"] = 0
        pq = PH.qty_for(ph["phase"], pregame_qty=rn1_pregame_qty(),
                        live_qty_now=PH.live_qty(rn1_live_qty(),
                                                 spec["min_qty"], rng,
                                                 VOL_SHRINK_USD))
        nc_ = (getattr(self, "_net_choice", None) or {}).get(slug)
        if (RN1_NET_RANK or RN1_TOUCH_EV) and nc_ and \
                nc_.get("qty") is not None:
            pq = max(min(pq, D(str(nc_["qty"]))), D(str(spec["min_qty"])))
        if pq != D(str(RN1.QTY)):
            qcfg["qty"] = pq
        if RN1_TOUCH_EV:
            # per-side placement by reward-adjusted marginal EV, on the
            # same function the selection used (see RN1_TOUCH_EV)
            tev = self.rn1_touch_ev_quote(slug, mk, bbo, tick, rng, pq,
                                          qcfg, now)
            qcfg["depth_ticks_side"] = tev["depth_side"]
        if pause_reason:
            # RN1_PAUSE_DEPTH: rest below where the move has already been
            pdep = self.rn1_pause_depth(rng, tick)
            base = qcfg.get("depth_ticks_side") or {}
            dflt = int(qcfg.get("depth_ticks", RN1.DEPTH_TICKS))
            qcfg["depth_ticks_side"] = {
                sd: max(int(base.get(sd, dflt)), pdep)
                for sd in ("YES", "NO")}
            qcfg["depth_ticks"] = max(dflt, pdep)
            if RN1_PAUSE_MIN_SIZE:
                # volatility-adaptive size: the venue minimum while paused
                qcfg["qty"] = min(D(str(qcfg.get("qty", RN1.QTY))),
                                  max(D(1), D(str(spec["min_qty"]))))
        if RN1_KEEP_TOL_USD > 0 and not new_block:
            qcfg["keep_tol_usd"] = RN1_KEEP_TOL_USD
            qcfg["keep_px"] = {o["side"]: o["px"]
                               for o in mk["orders"].values()
                               if o.get("open")}
        dir_prob = None
        if RN1_DIRECTIONAL:
            dir_prob = self.rn1_directional_prob(slug)
            if dir_prob is not None:
                try:
                    from src import pmus_directional_alloc as DA
                    touch0 = {"YES": D(str(bbo["bid"])),
                              "NO": ONE - D(str(bbo["ask"]))}
                    fl0 = {sd: rn1_fill_param(RN1_FILL_LOSS, 0, slug)
                           for sd in ("YES", "NO")}
                    qcfg["qty_side"] = DA.size_hint(
                        prob=dir_prob, touch=touch0,
                        clip=qcfg.get("qty", RN1.QTY), fill_loss=fl0,
                        prev_favoured=mk.get("dir_favoured"),
                        cfg=RN1_DIR_CFG, max_qty=rn1_max_clip())
                    mk["_dir_levels"] = DA.ladder_hint(
                        prob=dir_prob, touch=touch0, fill_loss=fl0,
                        prev_favoured=mk.get("dir_favoured"),
                        cfg=RN1_DIR_CFG)
                    if RN1_DIR_PRICE_ADDS:
                        qcfg["adds_priced_externally"] = True
                except Exception:                         # noqa: BLE001
                    dir_prob = None
        q = RN1.quotes(yes_bid=bbo["bid"], yes_ask=bbo["ask"], tick=tick,
                       pos=mk["pos"], cost=mk["cost"],
                       last_fill_t=mk.get("last_fill_t"), now=now,
                       min_qty=spec["min_qty"], c=qcfg)
        want: Dict[str, Any] = {}
        tf_prob: Any = _TF_UNSET
        if RN1_TRADING_FIRST:
            self.rn1_tf_note_mid(slug, mk, bbo, now)
        ks = mk.setdefault("keep_since", {})
        for side in ("YES", "NO"):
            tg = q.get(side)
            if tg and tg.get("kept"):
                since = float(ks.setdefault(side, now))
                if now - since > RN1_KEEP_MAX_S:
                    q["why_" + side] = "keep_expired"
                    tg = None
            else:
                ks.pop(side, None)
            if tg and tg["role"] != "complete" and new_block:
                tg = None
            if tg and tg["role"] != "complete" and pin is not None and \
                    pin.get(slug) not in (None, side):
                q["why_" + side] = "pinned_other_side"
                tg = None
            if tg and tg["role"] != "complete":
                dwhy = self.depth_block(slug, side)
                if dwhy:
                    q["why_" + side] = "depth:" + dwhy
                    tg = None
            if tg and tg["role"] != "complete" and self.guard is not None:
                # 2026-11-21 two-venue recording: international leads PMUS by
                # 3-30 s and 91% of PMUS mid drops >= 1c were preceded by a
                # lower international mid. An opening/extension bid the
                # international fair says is not cheap is withheld (the same
                # guard the two-sided openings use). A profitable completion
                # locks its pair whatever fair says, so it is exempt.
                gv = self.guard.verdict(slug, {side: tg["px"]}, tick)
                if not gv["ok"]:
                    self.guard.stats["blocked"] += 1
                    q["why_" + side] = "intl_guard:" + str(gv.get("why"))
                    tg = None
            if tg and tg["role"] == "complete" and RN1_TRADING_FIRST and \
                    RN1_TF_HEDGE_GIVE_USD is not None:
                if tf_prob is _TF_UNSET:
                    tf_prob = self.rn1_directional_prob(slug)
                    self.rn1_tf_note_fair(mk, tf_prob, now)
                tg = self.rn1_tf_hedge_price(mk, side, tg, tick, tf_prob)
            if tg and tg["role"] != "complete" and RN1_TRADING_FIRST:
                if tf_prob is _TF_UNSET:
                    tf_prob = self.rn1_directional_prob(slug)
                    self.rn1_tf_note_fair(mk, tf_prob, now)
                twhy = self.rn1_trading_first_why(slug, mk, side, tg["px"],
                                                  tick, tf_prob,
                                                  tg.get("qty", 0))
                if twhy:
                    q["why_" + side] = twhy
                    tg = None
            want[side] = tg
        if RN1_DIRECTIONAL:
            dir_prob = self.rn1_directional(slug, mk, q, want, bbo, tick,
                                            spec["min_qty"], dir_prob,
                                            priced=bool(qcfg.get(
                                                "adds_priced_externally")))
        self._gap_hold(slug, mk, q, want, bbo, new_block, now)
        mev_on = RN1_MARGINAL_EV and not new_block and not tf_net_bypass()
        mev_pass = False
        if mev_on:
            # ECONOMIC PRIORITY for the campaign caps (finding 2)
            lv0 = [dict(tg, side=sd) for sd, tg in want.items()
                   if tg is not None and tg["role"] != "complete"]
            got = self.rn1_marginal_ev(
                slug, mk, bbo, tick, qcfg, lv0,
                lv0 + [dict(tg, side=sd) for sd, tg in want.items()
                       if tg is not None and tg["role"] == "complete"])
            for c_, r_ in zip(lv0, got or ()):
                want[c_["side"]] = dict(want[c_["side"]], mev=r_)
        self.rn1_campaign_caps(slug, mk, want, q, min_qty=spec["min_qty"])
        ladder = self.rn1_ladder(slug, mk, want, tick,
                                 clip=qcfg.get("qty", RN1.QTY),
                                 min_qty=spec["min_qty"],
                                 extra_levels=(mk.pop("_dir_levels", None)
                                               if dir_prob is not None
                                               else None))
        if RN1_DIRECTIONAL and dir_prob is not None and ladder:
            ladder = self.rn1_directional_ladder(slug, mk, q, want, ladder,
                                                 bbo, tick, spec["min_qty"],
                                                 dir_prob)
        if RN1_TRADING_FIRST and ladder:
            if tf_prob is _TF_UNSET:
                tf_prob = self.rn1_directional_prob(slug)
                self.rn1_tf_note_fair(mk, tf_prob, now)
            kept_l, ref_l = [], []
            # each level is judged with the levels in front of it filled
            lq = {sd: D(str((want.get(sd) or {}).get("qty") or 0))
                  if (want.get(sd) or {}).get("role") != "complete" else D(0)
                  for sd in ("YES", "NO")}
            for l_ in ladder:
                lw = None
                if l_.get("role") != "complete":
                    lq[l_["side"]] += D(str(l_.get("qty") or 0))
                    lw = self.rn1_trading_first_why(
                        slug, mk, l_["side"], l_["px"], tick, tf_prob,
                        lq[l_["side"]])
                if lw:
                    ref_l.append({"side": l_["side"],
                                  "level": l_.get("level"), "why": lw})
                else:
                    kept_l.append(l_)
            if ref_l:
                q["tf_ladder_refused"] = ref_l
            ladder = kept_l
        mev_log = None
        if mev_on:
            # MARGINAL NET EV PER BID (finding 1): level-0 targets and every
            # deeper level, each against everything else planned
            lv0 = [dict(tg, side=sd, level=0) for sd, tg in want.items()
                   if tg is not None and tg["role"] != "complete"
                   and not tg.get("kept") and not tg.get("held")]
            cands = lv0 + [l_ for l_ in ladder
                           if l_.get("role") != "complete"]
            judged = {c_["side"] for c_ in lv0}
            planned = lv0 + [dict(tg, side=sd) for sd, tg in want.items()
                             if tg is not None and sd not in judged] + \
                list(ladder)
            got = self.rn1_marginal_ev(slug, mk, bbo, tick, qcfg, cands,
                                       planned)
            if got is None:
                if net_neg:
                    new_block = "net_not_positive"   # exactly as before
            else:
                mev_log = []
                drop_ids = set()
                ev_seen: set = set()
                for c_, r_ in zip(cands, got):
                    mev_log.append({"side": c_["side"],
                                    "level": c_.get("level", 0),
                                    "px": str(c_["px"]),
                                    "qty": str(c_["qty"]),
                                    "ok": r_.get("ok"), "why": r_.get("why"),
                                    "ev_usd_per_hour":
                                        r_.get("ev_usd_per_hour"),
                                    "reward_usd_per_hour":
                                        r_.get("reward_usd_per_hour")})
                    if r_.get("basis"):
                        mev_log[-1]["basis"] = r_["basis"]
                    if not r_.get("ok") and RN1_EV_HOLD and \
                            self._rn1_ev_hold(mk, c_, r_, tick, now,
                                              ev_seen):
                        mev_log[-1]["held"] = True
                        continue           # resting, kept through the dip
                    if not r_.get("ok"):
                        drop_ids.add(id(c_))
                        if c_.get("level", 0) == 0:
                            want[c_["side"]] = None
                            q["why_" + c_["side"]] = \
                                "marginal_ev:" + str(r_.get("why"))
                ladder = [l_ for l_ in ladder if id(l_) not in drop_ids]
                if RN1_EV_HOLD:
                    # a bid that passes again starts a fresh hold window
                    hold_ = mk.get("ev_hold") or {}
                    for k_ in [k_ for k_ in hold_ if k_ not in ev_seen]:
                        hold_.pop(k_, None)
                mev_pass = any(
                    tg is not None and tg["role"] != "complete"
                    for tg in want.values()) or any(
                    l_.get("role") != "complete" for l_ in ladder)
                if net_neg and not mev_pass:
                    new_block = "net_not_positive"
            if new_block:
                for sd in ("YES", "NO"):
                    if want.get(sd) is not None and \
                            want[sd]["role"] != "complete":
                        want[sd] = None
        if RN1_NET_REPOST_MIN_S > 0 and RN1_NET_RANK and \
                new_block in (None, "net_not_positive"):
            # REPOST HYSTERESIS (see RN1_NET_REPOST_MIN_S)
            if new_block == "net_not_positive":
                mk["net_repost_neg_t"] = now
                mk.pop("net_repost_pos_since", None)
            elif mk.get("net_repost_neg_t") is not None:
                ps_ = float(mk.setdefault("net_repost_pos_since", now))
                if now - ps_ < RN1_NET_REPOST_MIN_S:
                    new_block = "net_not_positive"
                    q["net_repost_wait_s"] = round(
                        RN1_NET_REPOST_MIN_S - (now - ps_), 1)
                    for sd in ("YES", "NO"):
                        if want.get(sd) is not None and \
                                want[sd]["role"] != "complete":
                            want[sd] = None
                else:
                    mk.pop("net_repost_neg_t", None)
                    mk.pop("net_repost_pos_since", None)
        if new_block:
            # A block stops every opening and extension. The levels behind
            # a completing bid are openings (see rn1_ladder), so they stop
            # too; only completions continue. LIVE 25 Nov 2026: with the
            # campaign loss stop in force two such opening bids rested
            # behind the completing bid.
            ladder = [l_ for l_ in ladder if l_.get("role") == "complete"]
        key = json.dumps({s: (None if t is None else
                              [str(t["px"]), str(t["qty"]), t["role"]])
                          for s, t in want.items()} |
                         {"ladder": [[l_["side"], str(l_["px"])]
                                     for l_ in ladder]} |
                         {"block": new_block, "phase": ph["phase"],
                          "why": {s: q.get("why_" + s) for s in ("YES", "NO")}},
                         sort_keys=True)
        if mk.get("rn1_logged") != key:
            mk["rn1_logged"] = key
            log("RN1_QUOTE", slug=slug, pos=mk["pos"], cost=mk["cost"],
                bid=bbo["bid"], ask=bbo["ask"], block=new_block,
                phase=ph["phase"], range=rng,
                want={s: (None if t is None else
                          {"px": str(t["px"]), "qty": str(t["qty"]),
                           "role": t["role"]}) for s, t in want.items()},
                why={s: q.get("why_" + s) for s in ("YES", "NO")},
                ladder=[{"side": l_["side"], "px": str(l_["px"]),
                         "qty": str(l_["qty"]), "level": l_["level"]}
                        for l_ in ladder] or None,
                extend_ev=q.get("extend_ev"),
                edge=q.get("edge"),
                marginal_ev=mev_log,
                directional=q.get("directional"),
                **({"fair_coverage": self.rn1_fair_coverage(slug)}
                   if os.getenv("PMUS_SHARP_COVERAGE_LOG", "0").strip() ==
                   "1" else {}),
                **({"trading_first": {
                    "pre_reward_edge": mk.get("_tf_last"),
                    "edge_min_usd": str(RN1_TF_EDGE_USD),
                    "ladder_refused": q.get("tf_ladder_refused"),
                    "label": "pre-reward trading edge vs independent "
                             "fair; reward EV reported separately"}}
                   if RN1_TRADING_FIRST else {}),
                pause_depth=(None if not pause_reason else
                             {"reason": pause_reason,
                              "ticks": self.rn1_pause_depth(rng, tick)}),
                fair=(getattr(self, "_rn1_fair", {}) or {}).get(slug),
                incentive=(getattr(self, "_rn1_inc", {}) or {}).get(slug),
                reward=bool(self.rn1_reward(slug)),
                label="RN1 replica decision; orders only if every gate "
                      "and the submission lock allow")
        # reconcile: keep a resting bid within REPRICE_TICKS of target with
        # no more than the target size left; cancel everything else first
        # every bid this market should have: one per side (level 0) plus
        # any deeper levels (RN1_LADDER_LEVELS). Each resting order is kept
        # for the first target it matches; nothing is kept twice.
        targets = [(sd, tg, 0) for sd, tg in want.items() if tg is not None]
        targets += [(l_["side"], l_, l_["level"]) for l_ in ladder]
        stale = []
        matched: set = set()
        # A side waiting out its refill cooldown keeps the deeper bids it
        # already has resting (LIVE-TESTED 2026-11-25 in replay: one fill
        # otherwise cancelled the rest of that side's ladder, so inventory
        # could not build through successive fills). Nothing new is placed
        # on that side until the cooldown ends, and only when no market
        # block (pause, late, cap, lock) applies.
        cooling = set() if new_block or RN1_LADDER_LEVELS <= 1 else {
            sd for sd in ("YES", "NO") if want.get(sd) is None and
            q.get("why_" + sd) == "refill_cooldown"}
        if new_block == "net_not_positive" and RN1_NET_HOLD_S > 0 and \
                not (RN1_EV_HOLD and RN1_MARGINAL_EV):
            neg0 = float(mk.setdefault("net_neg_since", now))
            if now - neg0 < RN1_NET_HOLD_S:
                touch = {"YES": D(str(bbo["bid"])),
                         "NO": ONE - D(str(bbo["ask"]))}
                for oid_, o_ in list(mk["orders"].items()):
                    if o_.get("open") and \
                            not is_completion_stage(o_.get("stage")) and \
                            D(str(o_["px"])) > touch.get(o_["side"], ONE):
                        # it would now set the price: not kept
                        o_["_hold_drop"] = True
                cooling = {"YES", "NO"}
        else:
            mk.pop("net_neg_since", None)
        for oid, o in mk["orders"].items():
            if not o.get("open"):
                continue
            left = D(o["qty"]) - D(o.get("filled") or "0")
            if o["side"] in cooling and \
                    not is_completion_stage(o.get("stage")) and \
                    not o.pop("_hold_drop", False):
                continue                         # kept through the cooldown
            hit = None
            for i, (sd, tg, lv) in enumerate(targets):
                if i in matched or sd != o["side"]:
                    continue
                gap = tg["px"] - D(o["px"])
                near = abs(gap) < RN1.REPRICE_TICKS * tick
                deeper = lv == 0 and RN1_KEEP_DEEPER_TICKS > 0 and \
                    tg["role"] != "complete" and \
                    not is_completion_stage(o.get("stage")) and \
                    D(0) < gap <= RN1_KEEP_DEEPER_TICKS * tick
                hold_c = RN1_COMPLETE_KEEP_TICKS > 0 and \
                    tg["role"] == "complete" and \
                    is_completion_stage(o.get("stage")) and \
                    D(0) <= gap <= RN1_COMPLETE_KEEP_TICKS * tick
                if (near or deeper or hold_c) and left <= tg["qty"]:
                    hit = i
                    if hold_c and not near:
                        self._kept_completion = int(getattr(
                            self, "_kept_completion", 0)) + 1
                    if deeper and not near:
                        self._kept_deeper = int(getattr(
                            self, "_kept_deeper", 0)) + 1
                    break
            if hit is None:
                stale.append((oid, o))
            else:
                matched.add(hit)
        wait_sides: set = set()
        if stale:
            before = (str(mk["pos"]["YES"]), str(mk["pos"]["NO"]))
            self._cancel_legs(slug, stale)
            self.b.save()
            raced = (str(mk["pos"]["YES"]), str(mk["pos"]["NO"])) != before
            pending = {o["side"] for _oid, o in stale if o.get("open")}
            if raced or (pending and not RN1_SIDE_INDEPENDENT):
                return       # a fill raced the cancel, or still pending
            # RN1_SIDE_INDEPENDENT: only the side still cancelling waits
            wait_sides = pending
        flat = y == 0 and n == 0
        # Trading-first uses the economic idle-release path below, not reward eligibility.
        if RN1_TRADING_FIRST:
            mk.pop("unqualified_since", None)
        if not RN1_TRADING_FIRST and flat and RN1_RELEASE_UNQUALIFIED_S > 0 and not mev_pass and \
                new_block in (None, "net_not_positive"):
            if self.rn1_quote_qualifies(slug, mk):
                mk.pop("unqualified_since", None)
            else:
                since_u = float(mk.setdefault("unqualified_since", now))
                if now - since_u > RN1_RELEASE_UNQUALIFIED_S and \
                        not any(o.get("open") for o in mk["orders"].values()):
                    mk["done"] = True
                    mk["released_flat"] = now
                    self.b.save()
                    log("released_unqualified_market", slug=slug,
                        unqualified_s=round(now - since_u, 1),
                        label="our quote here would earn no reward under "
                              "the venue's rule; slot freed for a market "
                              "that qualifies")
                    return
                if now - since_u > RN1_RELEASE_UNQUALIFIED_S:
                    self._cancel_legs(slug, [(oid, o) for oid, o in
                                             mk["orders"].items()
                                             if o.get("open")])
                    self.b.save()
                    return
        balanced = RN1_RELEASE_BALANCED and y == n and y > 0
        if (flat or balanced) and not any(want.values()) and \
                not account_block:
            idle = mk.setdefault("idle_since", now)
            # only paused: the price is moving, or the international price
            # is missing this pass. Anything else (late, out of scope, no
            # edge, thin) is about the market itself.
            paused = new_block in ("jump_pause", "vol_pause") or \
                bool(pause_reason) or (
                new_block is None and any(
                    str(q.get("why_" + s_) or "").startswith("no_fair")
                    for s_ in ("YES", "NO")))
            limit = max(120.0, RN1_PAUSED_RELEASE_S) if paused else 120.0
            if paused and RN1_PAUSE_ROTATE_S > 0:
                # RN1_PAUSE_ROTATE_S: a paused flat market frees its slot
                limit = max(15.0, min(limit, RN1_PAUSE_ROTATE_S))
            if now - float(idle) > limit and \
                    not any(o.get("open") for o in mk["orders"].values()):
                mk["done"] = True
                mk["released_flat"] = now
                self.b.save()
                log("released_flat_market", slug=slug, pos=mk["pos"],
                    bid=bbo["bid"], ask=bbo["ask"], mode="rn1",
                    paused=paused, idle_s=round(now - float(idle), 1),
                    reselectable=RN1_RESELECT)
            return
        mk.pop("idle_since", None)
        for i, (side, tg, _lv) in enumerate(targets):
            if i in matched or side in wait_sides:
                continue
            stage = {"complete": "two_sided_complete",
                     "extend": "rn1_extend"}.get(tg["role"], "rn1_open")
            self.place(slug, side, tg["px"], tg["qty"], stage)

    def step_market(self, slug: str) -> None:
        mk = self.b.m(slug)
        # 1. refresh own orders
        for oid, o in list(mk["orders"].items()):
            if not o.get("open"):
                continue
            if self.fill_list_proves_unchanged(oid, o):
                continue                 # resting, nothing filled: no read
            st = self.v.status(oid)
            self._exact_read(oid)
            self._record_fill(slug, o["side"], oid, st)
            o["open"] = st["open"]
            if not st["open"] and D(o.get("filled") or "0") < D(o["qty"]):
                # closed by the VENUE (we did not cancel it here): say why,
                # so a silent re-place loop is visible (owner 2026-11-15)
                log("order_closed_by_venue", slug=slug, side=o["side"],
                    px=o["px"], qty=o["qty"], filled=o.get("filled"),
                    state=st.get("state"), stage=o.get("stage"),
                    age_s=round(time.time() - float(o.get("placed_at")
                                                    or time.time())))
        self.b.save()
        if abandoned(slug):
            for oid, o in mk["orders"].items():
                if o.get("open"):
                    try:
                        self.v.cancel(oid)
                        st = self.v.status(oid)
                        self._record_fill(slug, o["side"], oid, st)
                        o["open"] = st["open"]
                    except Exception:                 # noqa: BLE001
                        pass
            if not any(o.get("open") for o in mk["orders"].values()):
                mk["done"] = True
                if mk.get("lone_since") is not None:
                    self._recovery_resolved(slug, mk, "abandoned")
                log("market_abandoned", slug=slug, pos=mk["pos"],
                    label="owner abandoned: orders cancelled, no new "
                          "orders; position rides to settlement")
            self.b.save()
            return
        spec = self.v.spec(slug)
        bbo = self.v.bbo(slug)
        self._markout_step(slug, mk, bbo)
        if spec is None or bbo is None:
            if spec is None:
                # LIVE 2026-11-15 20:03Z: this marked every order closed
                # whether or not the cancel took, then closed the market --
                # a still-resting order dropped out of the registry and the
                # watchdog latched 5s later. An order is closed only when
                # the venue says so; the market stays open (and named in
                # the registry) until every order is terminal.
                for oid, o in mk["orders"].items():
                    if o.get("open"):
                        try:
                            self.v.cancel(oid)
                            st = self.v.status(oid)
                            self._record_fill(slug, o["side"], oid, st)
                            o["open"] = st["open"]
                        except Exception:             # noqa: BLE001
                            pass                      # stays open: retried
                if any(o.get("open") for o in mk["orders"].values()):
                    self.b.save()
                    return
                mk["done"] = True
                mk["closed"] = True               # never reselected
                mk.pop("released_flat", None)
                if mk.get("lone_since") is not None:
                    self._recovery_resolved(slug, mk, "held_to_close")
                self.b.save()
                log("market_closed", slug=slug, pos=mk["pos"],
                    unmatched_cost=self.b.unmatched_cost(slug))
            return
        tick = spec["tick"]
        self._mark(slug, mk, bbo)
        if reward_first_on() and mk.get("reward"):
            # THEORETICAL accrual for the interval just ended (legs that
            # rested), then refresh the share estimate for the next one
            self._accrue(slug, mk)
            try:
                self._reward_estimate(slug, mk, tick)
            except Exception as exc:                  # noqa: BLE001
                log("reward_estimate_error", slug=slug, err=str(exc)[:120])
        if RN1.ON:
            # OWNER 2026-11-21: scaled-down RN1 behavioural replica. Replaces
            # the two-sided open/complete/recovery decisions for this market;
            # every gate, lock, cap and the bookkeeping stay the engine's.
            self._rn1_step(slug, mk, spec, bbo, tick)
            return
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        if TAPE is not None:
            # a market holding an unpaired leg records its book even when the
            # day's ordinary-frame budget is spent (stranded-leg replay)
            (TAPE.priority.discard if y == n else TAPE.priority.add)(slug)
        open_by_side = {o["side"]: (oid, o) for oid, o in mk["orders"].items()
                        if o.get("open")}
        if y == n:
            # flat or paired: (re)quote both sides D ticks under the touch
            if y > 0 and not mk.get("pair_logged_at") == str(y):
                cpair = (D(mk["cost"]["YES"]) + D(mk["cost"]["NO"])) / y
                self.b.d["pairs"].append({"slug": slug, "qty": str(y),
                                          "pair_cost": str(cpair), "t": now_iso()})
                mk["pair_logged_at"] = str(y)
                mk["pair_set_at"] = time.time()
                log("PAIR_LOCKED", slug=slug, pairs=y, avg_pair_cost=cpair,
                    locked_edge_per_pair=ONE - cpair,
                    label="locked pair economics; realized at settlement")
            mk.pop("complete_px", None)
            if mk.get("lone_since") is not None:
                self._recovery_resolved(slug, mk, "paired")
            mk.pop("lone_since", None)
            if self.ws is not None and \
                    time.time() < float(mk.get("jump_pause_until") or 0):
                # JUMP PAUSE: no opening legs rest in this market until the
                # pause ends (completion legs never reach this branch)
                if open_by_side:
                    self._pull(slug, "jump_pause_hold", time.monotonic())
                return
            set_at = float(mk.setdefault("pair_set_at", time.time()))
            # OWNER 2026-11-15: openings stay DEPTH_TICKS under the touch;
            # only a stranded leg's completion steps closer.
            steps = int((time.time() - set_at) // STALE_S) \
                if (STALE_S > 0 and OPEN_LADDER) else 0
            want = None
            rq = self._reward_quote(slug, mk, bbo, tick)   # None unless on
            if rq is not None:
                want = rq["want"]
                qty = max(REWARD_QTY, spec["min_qty"])
            else:
                dmax = depth_ticks(tick)
                cap = open_cap(self.b.d, slug)
                for d in range(max(0, dmax - steps), dmax + 1):
                    want = open_prices(bbo["bid"], bbo["ask"], tick, depth=d,
                                       cap=cap)
                    if want is not None:
                        break
                if want is None and cap < pair_cap(slug):
                    # the drift cap tightened the pair: rest DEEPER (bounded)
                    # rather than quote a pair whose margin the measured
                    # completion drift would erase
                    for d in range(dmax + 1, dmax + 1 + DRIFT_MAX_EXTRA_TICKS):
                        want = open_prices(bbo["bid"], bbo["ask"], tick,
                                           depth=d, cap=cap)
                        if want is not None:
                            break
                qty = max(QTY, spec["min_qty"])
            blocked = (self.b.total_unmatched() >= TOTAL_CAP or
                       os.getenv(ENABLED_ENV, "").upper() != "YES" or
                       self.daily_stopped() or self.halt_new_exposure() or
                       self.shared_halt() is not None or
                       self.account_risk_blocks() is not None)
            late = late_for(
                slug, (getattr(self.v, "states", {}) or {}).get(slug))
            if excluded(slug):
                late = "excluded"
            elif PREGAME:
                gs = mk.get("game_start")
                if gs is None:
                    late = "pregame_no_start_time"
                elif time.time() >= float(gs) - PREGAME_CUTOFF_S:
                    late = "pregame_cutoff"
            if not late and market_overpay_blocked(mk):
                late = "market_overpay_budget"
            if late:
                if mk.get("late_logged") != late:
                    mk["late_logged"] = late
                    log("late_cutoff", slug=slug, reason=late,
                        pos=mk["pos"], label="no new pairs; completions continue")
                want = None          # pulls opening legs; frees the slot later
            if OPEN_EV_GATE and want is not None and not blocked:
                g = self.open_gate(slug)
                if not g["allow"]:
                    if mk.get("gate_logged") != g["why"]:
                        mk["gate_logged"] = g["why"]
                        log("open_ev_reject", slug=slug, **g,
                            label="expected net value of opening from "
                                  "SETTLED results <= min; no new pair")
                    want = None
            guard_hold = False
            if self.guard is not None and want is not None and not blocked:
                gv = self.guard.verdict(slug, want, tick)
                if not gv["ok"]:
                    self.guard.stats["blocked"] += 1
                    if mk.get("guard_logged") != gv["why"]:
                        mk["guard_logged"] = gv["why"]
                        log("intl_guard_block", slug=slug,
                            want={k: str(x) for k, x in want.items()}, **gv,
                            label="opening withheld: intl fair value says "
                                  "the quote would be picked off")
                    # a matched market waits for intl to agree again; only a
                    # market intl cannot price is released like any idle one
                    guard_hold = bool(gv.get("matched", True))
                    want = None
                else:
                    mk.pop("guard_logged", None)
            worst_leg = max(want.values()) * qty if want else D(0)
            shrunk = False
            if rq is not None and want is not None and not blocked and \
                    self.b.committed_excluding(slug) + worst_leg > TOTAL_CAP:
                # LIVE 2026-11-15: a 10-lot reward quote never fit under the
                # cap and the market sat idle. Quote the size that DOES fit
                # (never above REWARD_QTY, never below the venue minimum).
                room = TOTAL_CAP - self.b.committed_excluding(slug)
                fit = (room / max(want.values())).to_integral_value(
                    rounding=ROUND_DOWN) if room > 0 else D(0)
                if fit >= spec["min_qty"] and fit < qty:
                    qty, shrunk = fit, True
                    worst_leg = max(want.values()) * qty
            if rewards_on() and want is not None and not blocked and \
                    self.b.committed_excluding(slug) + worst_leg > TOTAL_CAP:
                # every market, reward mode: a new pair opens only if all
                # lone legs that could exist at once stay within TOTAL_CAP
                blocked = True
                if mk.get("precheck_logged") != str(worst_leg):
                    mk["precheck_logged"] = str(worst_leg)
                    log("total_cap_precheck_block", slug=slug,
                        committed=self.b.committed_excluding(slug),
                        worst_leg=worst_leg, total_cap=TOTAL_CAP)
            # HYSTERESIS: a resting leg AT its target or up to one tick
            # deeper stays put (no cancel/re-place churn on flicker); only a
            # leg closer than target, or 2+ ticks too deep, is re-set.
            pol = {"policy": "two_sided", "sides": ("YES", "NO")}
            if want is not None and not blocked and \
                    OPEN_POLICY != "two_sided":
                from src import pmus_sports_open_policy as OP
                pol = OP.choose(OPEN_POLICY, want, bbo["bid"], bbo["ask"],
                                records=self.b.d.get("recoveries") or [])
                key = f"{pol['policy']}:{pol['why']}:{pol.get('cheap')}"
                if mk.get("open_policy_logged") != key:
                    mk["open_policy_logged"] = key
                    log("open_policy", slug=slug, want={k: str(x) for k, x
                                                        in want.items()},
                        **{k: v for k, v in pol.items() if k != "sides"},
                        sides=list(pol["sides"]))
                if not pol["sides"]:
                    want = None
            sides = tuple(pol["sides"])
            in_place = (want is not None and not blocked and
                        set(open_by_side) == set(sides) and
                        all(want[sd] - (tick if (not OPEN_LADDER and
                                                 not rewards_on()) else 0)
                            <= D(open_by_side[sd][1]["px"])
                            <= want[sd] for sd in sides) and
                        (not rewards_on() or
                         all((D(open_by_side[sd][1]["qty"]) <= qty) if shrunk
                             else (D(open_by_side[sd][1]["qty"]) == qty)
                             for sd in sides)))
            if not in_place and rq is not None and reward_first_on() and \
                    want is not None and not blocked:
                # REWARD-FIRST: time on book is the score; no step ladder,
                # no 1-tick chase -- hold unless >= REWARD_REPRICE_TICKS
                # away / out of the target walk / maxSpread / pair cap
                in_place = self._reward_hold(slug, mk, open_by_side, bbo,
                                             tick, qty, shrunk)
            if in_place:
                return
            # OWNER 2026-11-14 "Pairs should be set simultaneously": never
            # leave one side resting alone. Pull every open leg together,
            # then (still flat) set both legs together.
            self._cancel_legs(slug, [v for v in open_by_side.values()])
            if D(mk["pos"]["YES"]) != D(mk["pos"]["NO"]) or \
                    any(o.get("open") for o in mk["orders"].values()):
                return                  # a leg filled while pulling: complete it
            if want is None and not blocked and not guard_hold:
                # flat and out of band/too wide: free the slot for another
                # live market after 2 minutes (pairs already locked stay).
                idle = mk.setdefault("idle_since", time.time())
                if time.time() - float(idle) > 120:
                    mk["done"] = True
                    self.b.save()
                    log("released_flat_market", slug=slug, pos=mk["pos"],
                        bid=bbo["bid"], ask=bbo["ask"])
                return
            mk.pop("idle_since", None)
            if want is None or blocked:
                return
            if rq is not None:
                # rewarded: cap the WORST LONE-LEG cost in this market
                if self.b.unmatched_cost(slug) + worst_leg > \
                        REWARD_PER_MARKET_CAP:
                    return
            elif (want["YES"] + want["NO"]) * qty > PER_MARKET_CAP:
                return
            mk["partner_px"] = {k: str(v) for k, v in want.items()}
            stage = "two_sided_open_rewarded" if rq is not None \
                else "two_sided_open"
            if pol["policy"] != "two_sided":
                stage = f"{stage}_{pol['policy']}"      # e.g. _cheap_first
            mk["open_policy"] = pol["policy"]
            if self.ws is not None:
                mk["quoted_bbo"] = {"bid": str(bbo["bid"]),
                                    "ask": str(bbo["ask"])}
            for side in sides:
                self.place(slug, side, want[side], qty, stage)
            return
        # unbalanced: the lagging side is the completion
        lag = "NO" if y > n else "YES"
        lead = "YES" if lag == "NO" else "NO"
        need = abs(y - n)
        lead_px = D(mk["cost"][lead]) / D(mk["pos"][lead])
        # stop adding to the leading side -- except a bounded EXTEND order,
        # which _extend manages (keeps, re-prices or pulls) below
        if lead in open_by_side and not (
                EXTEND_ON and RECOVERY_ON and
                open_by_side[lead][1].get("stage") == "two_sided_extend"):
            oid, o = open_by_side[lead]
            self.v.cancel(oid)
            st = self.v.status(oid)
            self._record_fill(slug, lead, oid, st)
            # LIVE 2026-11-15: this was `False` unconditionally -- a cancel
            # the venue had not yet applied left an order resting that the
            # book called closed (the orphan that latched the watchdog).
            o["open"] = st["open"]
            self.b.save()
            return
        cur = open_by_side.get(lag)
        if mk.get("lone_since") is None:
            # durable at once: an unmatched position must survive a restart
            # with its age (it was only saved by a later order action)
            mk["lone_since"] = time.time()
            # the counterfactual: completing by taker at the moment it went
            # lone (ask + estimated fee) -- the yardstick for every later
            # recovery outcome
            c_ask = bbo["ask"] if lag == "YES" else ONE - bbo["bid"]
            mk["lone_start"] = {"lag": lag, "cost0": mk["cost"][lag],
                                "qty0": mk["pos"][lag],
                                "lead_px": str(lead_px),
                                "open_policy": mk.get("open_policy",
                                                      "two_sided"),
                                "taker_px": str(c_ask + taker_fee(
                                    c_ask, need) / need),
                                "controller": "recovery" if RECOVERY_ON
                                else "legacy",
                                # the complement quote resting when the first
                                # leg filled: the pair the OPENING promised
                                "opp_quote_px": (str(cur[1].get("px"))
                                                 if cur else None)}
            self.b.save()
        lone_since = float(mk["lone_since"])
        if RECOVERY_ON:
            self._recover(slug, mk, spec, bbo, tick, lead, lag, need,
                          lead_px, cur, lone_since)
            if EXTEND_ON:
                self._extend(slug, mk, spec, bbo, tick, lead, need, lead_px,
                             open_by_side.get(lead))
            return
        comp_ask = (bbo["ask"] if lag == "YES" else ONE - bbo["bid"])
        exit_s, exit_max = exit_rule(slug)
        exit_due = exit_s > 0 and time.time() - lone_since >= exit_s \
            and need >= spec["min_qty"]
        fee = taker_fee(comp_ask, need) if EXIT_FEE_AWARE else D(0)
        fee_pc = (fee / need) if need > 0 else D(0)
        exit_ok = exit_due and lead_px + comp_ask + fee_pc <= exit_max
        if exit_due and EXIT_MAX_TAKER_QTY > 0 and need > EXIT_MAX_TAKER_QTY:
            exit_ok = False
            self._log_exit_skip(slug, mk, "exit_skipped_size", lead=lead,
                                take_side=lag, qty=need,
                                max_taker_qty=EXIT_MAX_TAKER_QTY,
                                take_px=comp_ask, est_fee_usd=fee)
        elif exit_due and not exit_ok and EXIT_FEE_AWARE and \
                lead_px + comp_ask <= exit_max:
            self._log_exit_skip(slug, mk, "exit_skipped_fee", lead=lead,
                                take_side=lag, qty=need, lead_px=lead_px,
                                take_px=comp_ask, est_fee_usd=fee,
                                fee_per_contract=fee_pc,
                                pair_cost_with_fee=lead_px + comp_ask + fee_pc,
                                exit_max_pair=exit_max)
        if exit_ok:
            if cur:                          # pull the resting maker first
                self.v.cancel(cur[0])
                st = self.v.status(cur[0])
                self._record_fill(slug, lag, cur[0], st)
                cur[1]["open"] = st["open"]
                self.b.save()
                return
            log("EXIT_LONE_LEG", slug=slug, lead=lead, lead_px=lead_px,
                take_side=lag, take_px=comp_ask, qty=need,
                pair_cost=lead_px + comp_ask,
                est_fee_usd=fee, fee_per_contract=fee_pc,
                pair_cost_with_fee=lead_px + comp_ask + fee_pc,
                exit_max_pair=exit_max, fee_aware=EXIT_FEE_AWARE,
                age_s=round(time.time() - lone_since),
                label="taker completion; realized pair loss bounded by "
                      "EXIT_MAX_PAIR incl. ESTIMATED taker fee, settles "
                      "at par")
            self.place(slug, lag, comp_ask, need, "two_sided_exit",
                       post_only=False)
            return
        # The partner was set together with the filled leg: leave it at its
        # price (pair locked <= PAIR_CAP). Only a partial fill re-sizes it.
        pcap = pair_cap(slug)
        lone_age = time.time() - lone_since
        # a multi-contract (reward-size) lone leg joins the bid at once
        patient = exit_s > 0 and COMPLETE_MAX_PAIR > pcap and \
            (lone_age >= exit_s * MAKER_PHASE_FRAC or need > 2)
        cap_px = floor_tick((COMPLETE_MAX_PAIR if patient else pcap)
                            - lead_px, tick)
        lag_bid = side_bid(lag, bbo["bid"], bbo["ask"])
        if cur:
            rem_open = D(cur[1]["qty"]) - D(cur[1]["filled"])
            age = time.time() - float(cur[1].get("placed_at") or time.time())
            closer = min(D(cur[1]["px"]) + tick, lag_bid, cap_px)
            if patient and D(cur[1]["px"]) < min(lag_bid, cap_px):
                # maker phase: join the bid now (break-even at worst)
                closer = min(lag_bid, cap_px)
                if mk.get("maker_phase_logged") != str(closer):
                    mk["maker_phase_logged"] = str(closer)
                    log("maker_complete_join_bid", slug=slug, side=lag,
                        from_px=cur[1]["px"], to_px=closer, lead_px=lead_px,
                        pair_cost=lead_px + closer, lone_s=round(lone_age),
                        label="post-only completion at the bid; no fee")
                if rem_open == need:
                    mk["complete_px"] = str(closer)
                    self.v.cancel(cur[0])
                    st = self.v.status(cur[0])
                    self._record_fill(slug, lag, cur[0], st)
                    cur[1]["open"] = st["open"]
                    self.b.save()
                    return
            if rem_open == need and (age < STALE_S or
                                     closer <= D(cur[1]["px"])):
                return
            if rem_open == need:
                # sat unfilled: resubmit one tick closer (never past the
                # pair cap, never above this side's own bid)
                mk["complete_px"] = str(closer)
                log("tighten_complete", slug=slug, side=lag,
                    from_px=cur[1]["px"], to_px=closer, age_s=round(age))
            self.v.cancel(cur[0])
            st = self.v.status(cur[0])
            self._record_fill(slug, lag, cur[0], st)
            cur[1]["open"] = st["open"]
            self.b.save()
            return
        px = D(mk.get("complete_px") or mk.get("partner_px", {}).get(lag)
               or "0") or floor_tick(
            min(side_bid(lag, bbo["bid"], bbo["ask"]) - depth_ticks(tick) * tick,
                pcap - lead_px), tick)
        px = min(px, cap_px)
        if px < tick or px >= side_bid(lag, bbo["bid"], bbo["ask"]) + \
                (bbo["ask"] - bbo["bid"]):
            return
        if need >= spec["min_qty"]:
            self.place(slug, lag, px, need, "two_sided_complete")

    @staticmethod
    def _log_exit_skip(slug: str, mk: Dict[str, Any], kind: str,
                       **kw: Any) -> None:
        """Once per (reason, qty, take price) per market: no log spam."""
        key = f"{kind}:{kw.get('qty')}:{kw.get('take_px')}"
        if mk.get("exit_skip_logged") == key:
            return
        mk["exit_skip_logged"] = key
        log(kind, slug=slug, **kw,
            label="no taker exit; maker completion keeps resting/stepping")

    def slate_targets(self) -> list:
        """Candidates for the one controller: games in progress, games not
        yet started, or both (PMUS_TWO_SIDED_SLATE). With both, a game in
        progress comes first and a market is listed once."""
        out = self._slate_targets()
        if RN1_LINE_MARKETS:
            # priced line markets (sharp_line_shadow) join the slate; the
            # trading-first gate still needs their fresh independent fair
            seen = {t["slug"] for t in out}
            now = time.time()
            for slug, tg in list((getattr(self, "_line_targets", None)
                                  or {}).items()):
                st = ((self.b.d.get("sharp_shadow") or {}).get(slug) or {})
                if slug in seen or not st.get("fair_yes") or \
                        now - float(st.get("t") or 0) > SHARP_SHADOW_EVERY_S * 2:
                    continue
                out.append(tg)
        return out

    def _slate_targets(self) -> list:
        sl = slate()
        if sl == "pregame":
            return self.v.pregame_sports()
        live = self.v.live_sports()
        if sl == "live":
            return live
        seen = {t["slug"] for t in live}
        return live + [t for t in self.v.pregame_sports()
                       if t["slug"] not in seen]

    def _reward_share_score(self, slug: str,
                            per: Mapping[str, Any]) -> Dict[str, Any]:
        """THEORETICAL reward per dollar committed for our own quote in
        this market: {"usd_per_hour", "capital_usd", "per_dollar_hour",
        "share"} or {"why": ...}. One public book read."""
        from src import pmus_sports_rewards as R
        spec = self.v.spec(slug)
        if not spec:
            return {"why": "no_spec"}
        tick = D(str(spec["tick"]))
        book = (self._reward_get()(f"/v1/markets/{slug}/book") or {}
                ).get("marketData") or {}
        bids, asks = R.book_sides(book)
        if not bids or not asks:
            return {"why": "no_two_sided_book"}
        bb, ba = D(str(bids[0][0])), D(str(asks[0][0]))
        if RN1_TOUCH_EV:
            # the placement the controller will quote (same function)
            return self._touch_ev_score(slug, book, per, bb, ba, tick)
        depth = max(0, int(RN1.DEPTH_TICKS))
        yes_px = bb - depth * tick
        no_px = (ONE - ba) - depth * tick
        if not (D(0) < yes_px < ONE and D(0) < no_px < ONE):
            return {"why": "no_room_for_a_bid"}
        qty = rn1_max_clip()
        if RN1_NET_RANK:
            return self._net_score(slug, book, per, bb, ba, tick, qty, R)
        est = R.quote_estimate(book, per, float(yes_px), float(no_px),
                               float(qty), float(tick))
        cap = qty * (yes_px + no_px)
        rate = float(est.get("est_usd_per_hour") or 0.0) * \
            rn1_reward_cal(slug, per)
        return {"usd_per_hour": round(rate, 6),
                "capital_usd": str(cap),
                "per_dollar_hour": round(rate / float(cap), 6)
                if cap > 0 else 0.0,
                "share": est.get("share"),
                "max_spread_ok": est.get("max_spread_ok")}

    def _net_score(self, slug: str, book: Mapping[str, Any],
                   per: Mapping[str, Any], bb: D, ba: D, tick: D, qty: D,
                   R: Any) -> Dict[str, Any]:
        """See RN1_NET_RANK: the better of two placements (0 = at the best
        bid, 1 = one tick behind) by THEORETICAL net per hour. Where both
        bids at a placement would exceed the pair-cost limit, the dearer
        one is lowered to fit, as the quoting rule does."""
        cap_px = D(str(RN1.PAIR_CAP))
        sizes = [x for x in RN1_NET_SIZES if 0 < x <= qty] or [qty]
        best_q = None
        for q_ in sizes:
            got = self._net_rows(slug, book, per, bb, ba, tick, q_, R, cap_px)
            if got is None:
                continue
            if best_q is None or got[1]["net_usd_per_hour"] > \
                    best_q[2][1]["net_usd_per_hour"] + 1e-12:
                best_q = (q_, got[0], got)
        if best_q is None:
            return {"why": "no_room_for_a_bid"}
        qty, best, (_d, b, rows) = best_q[0], best_q[1], best_q[2]
        out = {"usd_per_hour": b["reward_usd_per_hour"],
               "net_usd_per_hour": b["net_usd_per_hour"],
               "capital_usd": str(b["capital_usd"]),
               "per_dollar_hour": round(b["net_usd_per_hour"]
                                        / b["capital_usd"], 6)
               if b["capital_usd"] > 0 else 0.0,
               "depth": best, "qty": str(qty), "placements": rows,
               "share": b["share"], "max_spread_ok": b["max_spread_ok"]}
        choice = getattr(self, "_net_choice", None)
        if choice is None:
            choice = self._net_choice = {}
        prev = choice.get(slug)
        choice[slug] = {"t": time.time(), "depth": best, "qty": qty,
                        "net_usd_per_hour": b["net_usd_per_hour"]}
        if not prev or prev.get("depth") != best or \
                prev.get("qty") != qty or \
                (prev.get("net_usd_per_hour", 0) > 0) != \
                (b["net_usd_per_hour"] > 0):
            log("RN1_NET_PLACEMENT", slug=slug, placement=(
                "best_bid" if best == 0 else "one_tick_behind"),
                qty=str(qty), placements=rows,
                label="THEORETICAL reward minus expected fill cost per "
                      "hour; an estimate, not a payment")
        return out

    def _net_rows(self, slug: str, book: Mapping[str, Any],
                  per: Mapping[str, Any], bb: D, ba: D, tick: D, qty: D,
                  R: Any, cap_px: D) -> Optional[tuple]:
        """(best depth, its row, {depth: row}) for one clip size."""
        rows = {}
        for d in (0, 1):
            y = bb - d * tick
            n = (ONE - ba) - d * tick
            over = y + n - cap_px
            if over > 0:
                if y >= n:
                    y -= over
                else:
                    n -= over
            if not (D(0) < y < ONE and D(0) < n < ONE):
                rows[d] = {"why": "no_room_for_a_bid"}
                continue
            # Only acknowledged, locally owned open orders at the candidate
            # price are replacements. Other ladder levels remain in the
            # denominator (conservative until multi-level scoring exists).
            mk = self.b.d.get("markets", {}).get(slug, {})
            owned = set(self.b.d.get("own_order_ids", []))
            own = {"YES": D(0), "NO": D(0)}
            for oid, order in (mk.get("orders") or {}).items():
                side = order.get("side")
                if (oid not in owned or not order.get("open") or
                        side not in own or order.get("stage") == "orphan"):
                    continue
                # an order younger than the public book's lag may not be in
                # the snapshot yet: deducting it would remove a competitor's
                # size instead of ours (optimistic), so it is not deducted
                if time.time() - float(order.get("placed_at") or 0) < \
                        RN1_NET_OWN_LAG_S:
                    continue
                if D(str(order["px"])) == (y if side == "YES" else n):
                    own[side] += max(D(0), D(str(order["qty"])) -
                                     D(str(order.get("filled", "0"))))
            kwargs = {}
            if own["YES"] or own["NO"]:
                # Do not subtract more than the visible level. A public
                # snapshot can lag private acknowledgements/fills.
                bids, asks = R.book_sides(book)
                visible_y = sum(q for p, q in bids
                                if D(str(p)) == y)
                visible_n = sum(q for p, q in asks
                                if D(str(p)) == ONE - n)
                kwargs = {"own_yes": min(float(own["YES"]), float(visible_y)),
                          "own_no": min(float(own["NO"]), float(visible_n))}
            est = R.quote_estimate(book, per, float(y), float(n),
                                   float(qty), float(tick), **kwargs)
            rew = float(est.get("est_usd_per_hour") or 0.0) * \
                rn1_reward_cal(slug, per)
            cost = 2 * float(qty) * rn1_fill_param(RN1_FILL_RATE, d, slug) \
                * rn1_fill_param(RN1_FILL_LOSS, d, slug)
            capital = float(qty * (y + n))
            net = rew - cost
            rows[d] = {"reward_usd_per_hour": round(rew, 6),
                       "fill_cost_usd_per_hour": round(cost, 6),
                       "net_usd_per_hour": round(net, 6),
                       "capital_usd": round(capital, 4),
                       "yes_px": str(y), "no_px": str(n),
                       "share": est.get("share"),
                       "max_spread_ok": est.get("max_spread_ok")}
        ok = [d for d in (0, 1) if "net_usd_per_hour" in rows[d]]
        if not ok:
            return None
        best = max(ok, key=lambda d: (rows[d]["net_usd_per_hour"], d))
        return best, rows[best], rows

    def rn1_net_forget(self, slug: str) -> None:
        """A fill changes the book and our inventory: drop the market's net
        estimate so the next pass prices it afresh (RN1_NET_RANK)."""
        mk = self.b.m(slug) if slug in self.b.d.get("markets", {}) else None
        if mk is not None:
            mk.pop("qualify_check", None)
        for name in ("_net_choice", "_share_cache"):
            c = getattr(self, name, None)
            if c:
                c.pop(slug, None)

    def rn1_touch_hard(self, slug: str, mk: Mapping[str, Any],
                       bbo: Mapping[str, Any], tick: Any,
                       rng: Any) -> Optional[str]:
        """The hard protections against resting AT the touch (None = none
        applies): a spread under the join minimum, the 30 s mid range above
        the competition's threshold (RN1_JOIN_MAX_RANGE; unknown = moving),
        a price jump within RN1_JOIN_CALM_S. Used by rn1_join_touch and by
        rn1_touch_ev, at selection and when quoting."""
        spread = D(str(bbo["ask"])) - D(str(bbo["bid"]))
        last_jump = float(mk.get("jump_pause_until") or 0) - JUMP_PAUSE_S
        min_t = 1 if RN1_JOIN_ONE_TICK else \
            max(2, RN1_JOIN_MIN_SPREAD_TICKS)
        if spread < min_t * D(str(tick)):
            return "spread_too_tight"
        if rng is None or float(rng) > rn1_comp_value(RN1_JOIN_MAX_RANGE,
                                                       slug):
            return "price_moving"
        if mk.get("jump_pause_until") and \
                time.time() - last_jump < RN1_JOIN_CALM_S:
            return "recent_jump"
        return None

    def rn1_join_touch(self, slug: str, mk: Dict[str, Any],
                       bbo: Mapping[str, Any], tick: D, rng: Any,
                       now: float) -> Optional[str]:
        """None = this market's bids may rest at the touch this pass (see
        RN1_JOIN_TOUCH); otherwise why not. Records the answer for the
        reactive pull and logs each change once."""
        joined = getattr(self, "_rn1_joined", None)
        if joined is None:
            joined = self._rn1_joined = {}
        why: Optional[str] = None
        nc = (getattr(self, "_net_choice", None) or {}).get(slug)
        if not RN1_JOIN_TOUCH:
            why = "off"
        elif RN1_NET_RANK and nc and nc.get("depth") == 1:
            why = "net_prefers_one_tick_behind"
        else:
            why = self.rn1_touch_hard(slug, mk, bbo, tick, rng)
        was = joined.get(slug)
        joined[slug] = why is None
        if RN1_JOIN_TOUCH and was is not None and was != (why is None):
            log("RN1_JOIN_TOUCH", slug=slug, joined=why is None, why=why,
                bid=str(bbo["bid"]), ask=str(bbo["ask"]),
                label="bids rest at the best bid while the spread is wide "
                      "and the price is calm; one tick back otherwise")
        return why

    # ------------------------------------------------ RN1_TOUCH_EV
    def rn1_touch_ev(self, slug: str, book: Optional[Mapping[str, Any]],
                     per: Optional[Mapping[str, Any]],
                     bbo: Mapping[str, Any], tick: Any, qty: Any,
                     pos: Mapping[str, Any], cost: Mapping[str, Any],
                     hard: Optional[str], own_y: Any = (), own_n: Any = (),
                     risk_step_ticks: Any = None,
                     completion_depth: Optional[int] = None
                     ) -> Dict[str, Any]:
        """See RN1_TOUCH_EV. Pure in its inputs (no request, no state
        change): the same call scores a selection candidate and decides
        where a tracked market's bids rest.

        Per side that would OPEN or EXTEND, every placement d = 0 (the
        touch) .. RN1_TOUCH_EV_MAX_DEPTH is scored
            EV(d) = rn1_reward_mult x incremental THEORETICAL reward(d)
                    - fill_rate(d) x qty x (fill_loss(d) + fee + risk)
        and the best d with EV > 0 is chosen (ties: deeper). A side with
        none rests at max(1, RN1.DEPTH_TICKS) ("fallback"). A COMPLETION
        side is not decided here: it keeps `completion_depth` (today's
        placement). Returns {"sides", "depth_side", "net_usd_per_hour",
        "reward_usd_per_hour", "capital_usd", "touch", "hard"}. Every
        figure is THEORETICAL."""
        from src import pmus_sports_rewards as RW
        t = D(str(tick))
        q = D(str(qty))
        yb, ya = D(str(bbo["bid"])), D(str(bbo["ask"]))
        p = {s_: D(str(pos.get(s_) or 0)) for s_ in ("YES", "NO")}
        c = {s_: D(str(cost.get(s_) or 0)) for s_ in ("YES", "NO")}
        touch = {"YES": yb, "NO": ONE - ya}
        opp_ask = {"YES": ya, "NO": ONE - yb}
        fallback = max(1, int(RN1.DEPTH_TICKS))
        comp_d = int(RN1.DEPTH_TICKS if completion_depth is None
                     else completion_depth)
        cap = D(str(RN1.PAIR_CAP))
        rcap = D(str(RN1.RESIDUAL_CAP_USD))
        pmin, pmax = D(str(RN1.PX_MIN)), D(str(RN1.PX_MAX))
        mult = rn1_reward_mult(slug, per)
        clip = D(str(RN1.QTY)) if D(str(RN1.QTY)) > 0 else q
        sides: Dict[str, Any] = {}
        role = {}
        for s_, o_ in (("YES", "NO"), ("NO", "YES")):
            role[s_] = "complete" if p[o_] > p[s_] else (
                "open" if p[s_] == 0 else "extend")
        for s_, o_ in (("YES", "NO"), ("NO", "YES")):
            if role[s_] == "complete":
                sides[s_] = {"role": "complete", "depth": comp_d,
                             "why": "completion_keeps_placement"}
                continue
            held = max(D(0), p[s_] - p[o_])
            clips = int((held / clip).to_integral_value(
                rounding="ROUND_CEILING")) if clip > 0 else 0
            risk = RN1.concentration_risk(tick=t, unpaired_clips=clips,
                                          risk_step_ticks=risk_step_ticks)
            # the other side's bid as it would rest, for the pair check and
            # (with maxSpread, where both sides must qualify) the reward
            o_d = comp_d if role[o_] == "complete" else fallback
            o_px = touch[o_] - o_d * t
            rows: Dict[int, Any] = {}
            for d in range(0, RN1_TOUCH_EV_MAX_DEPTH + 1):
                px = touch[s_] - d * t
                row: Dict[str, Any] = {"px": str(px)}
                rows[d] = row
                if not (D(0) < px < ONE) or px < pmin or px > pmax:
                    row["why"] = "price_band"
                    continue
                if px >= opp_ask[s_]:
                    row["why"] = "would_cross_or_lock"   # post-only holds
                    continue
                if d == 0 and hard:
                    row["why"] = "hard:" + str(hard)
                    continue
                avg = (c[s_] + px * q) / (p[s_] + q)
                if max(D(0), p[s_] + q - p[o_]) * avg > rcap + \
                        D("0.000001"):
                    row["why"] = "residual_cap"
                    continue
                if role[o_] == "complete" and D(0) < o_px < ONE and \
                        px + RN1.fee(px, RN1.MAKER_FEE_RATE) + o_px > \
                        cap + D("0.0000001"):
                    row["why"] = "pair_cap"
                    continue
                rew = 0.0
                if per and book:
                    try:
                        ys = [(float(o_px), float(q))] if (
                            o_ == "YES" and per.get("maxSpread")
                            and D(0) < o_px < ONE) else []
                        ns = [(float(o_px), float(q))] if (
                            o_ == "NO" and per.get("maxSpread")
                            and D(0) < o_px < ONE) else []
                        m = RW.marginal_ladder_reward(
                            book, per, ys, ns, float(t), list(own_y),
                            list(own_n), s_, float(px), float(q))
                        rew = max(0.0, float(
                            m["incremental_reward_usd_per_hour"])) * mult
                    except Exception as exc:              # noqa: BLE001
                        row["reward_error"] = type(exc).__name__
                        rew = 0.0
                fr = rn1_fill_param(RN1_FILL_RATE, d, slug)
                fl = rn1_fill_param(RN1_FILL_LOSS, d, slug)
                fe = RN1.fee(px, RN1.MAKER_FEE_RATE)
                cost_h = fr * float(q) * (fl + float(fe) + float(risk))
                row.update(reward_usd_per_hour=round(rew, 6),
                           fill_cost_usd_per_hour=round(cost_h, 6),
                           ev_usd_per_hour=round(rew - cost_h, 6),
                           fill_rate=fr, fill_loss=fl, risk=str(risk))
            ok = [d for d, r in rows.items()
                  if r.get("ev_usd_per_hour") is not None
                  and r["ev_usd_per_hour"] > 0]
            best = max(ok, key=lambda d: (rows[d]["ev_usd_per_hour"], d)) \
                if ok else None
            sides[s_] = {"role": role[s_], "rows": rows, "chosen": best,
                         "depth": fallback if best is None else best,
                         "why": None if best is not None
                         else "no_positive_placement"}
        # PAIR CAP for two simultaneous openings: the touch is left (the
        # dearer touch bid first) until the pair of both bids fits
        def _px(s_):
            return touch[s_] - int(sides[s_]["depth"]) * t

        def _over() -> bool:
            if not all(sides[s_].get("role") != "complete"
                       for s_ in ("YES", "NO")):
                return False
            y, n = _px("YES"), _px("NO")
            return y + RN1.fee(y, RN1.MAKER_FEE_RATE) + n + \
                RN1.fee(n, RN1.MAKER_FEE_RATE) > cap + D("0.0000001")
        while _over():
            at = [s_ for s_ in ("YES", "NO") if sides[s_]["depth"] == 0]
            if not at:
                break                     # the quoting rule reprices it
            s_ = max(at, key=_px)
            rows = sides[s_]["rows"]
            rows[0]["why"] = "pair_cap"
            ok = [d for d, r in rows.items() if d >= 1 and
                  r.get("ev_usd_per_hour") is not None and
                  r["ev_usd_per_hour"] > 0]
            best = max(ok, key=lambda d: (rows[d]["ev_usd_per_hour"], d)) \
                if ok else None
            sides[s_].update(chosen=best,
                             depth=fallback if best is None else best,
                             why=None if best is not None
                             else "no_positive_placement")
        net = rew_t = cap_usd = 0.0
        for s_ in ("YES", "NO"):
            sd = sides[s_]
            if sd.get("role") == "complete":
                continue
            r = sd["rows"].get(sd["depth"]) or {}
            if r.get("ev_usd_per_hour") is None:
                continue
            net += float(r["ev_usd_per_hour"])
            rew_t += float(r["reward_usd_per_hour"])
            cap_usd += float(D(r["px"]) * q)
        return {"sides": sides,
                "depth_side": {s_: int(sides[s_]["depth"])
                               for s_ in ("YES", "NO")},
                "net_usd_per_hour": round(net, 6),
                "reward_usd_per_hour": round(rew_t, 6),
                "capital_usd": round(cap_usd, 4),
                "touch": any(sides[s_]["depth"] == 0 and
                             sides[s_].get("role") != "complete"
                             for s_ in ("YES", "NO")),
                "hard": hard, "qty": str(q), "reward_mult": mult,
                "label": "THEORETICAL calibrated reward minus expected "
                         "fill cost per resting hour; not a payment"}

    def _rn1_book(self, slug: str) -> Optional[Mapping[str, Any]]:
        """The public book, cached RN1_MARGINAL_BOOK_TTL_S (the marginal-EV
        cache). None when it cannot be read (no reward is then credited)."""
        try:
            cache = getattr(self, "_mev_book", None)
            if cache is None:
                cache = self._mev_book = {}
            hit = cache.get(slug)
            if hit and time.monotonic() - hit[0] < RN1_MARGINAL_BOOK_TTL_S:
                return hit[1]
            book = (self._reward_get()(f"/v1/markets/{slug}/book") or {}
                    ).get("marketData") or {}
            cache[slug] = (time.monotonic(), book)
            return book
        except Exception:                                 # noqa: BLE001
            return None

    def rn1_touch_ev_quote(self, slug: str, mk: Dict[str, Any],
                           bbo: Mapping[str, Any], tick: Any, rng: Any,
                           qty: Any, qcfg: Mapping[str, Any],
                           now: float) -> Dict[str, Any]:
        """RN1_TOUCH_EV for a tracked market's quote this pass: the same
        rn1_touch_ev the selection scored it with, on this pass's book,
        inventory, hard protections and size. Records whether a bid rests
        at the touch (the reactive pull reads it) and logs each change."""
        per = self.rn1_reward(slug)
        book = self._rn1_book(slug) if per else None
        own_y, own_n = [], []
        if book:
            try:
                own_y, own_n = self._rn1_visible_own(
                    book, self._rn1_mature_own(mk, time.time()),
                    D(str(tick)))
            except Exception:                             # noqa: BLE001
                own_y, own_n = [], []
        hard = self.rn1_touch_hard(slug, mk, bbo, tick, rng)
        out = self.rn1_touch_ev(
            slug, book, per, bbo, tick, qty, mk["pos"], mk["cost"], hard,
            own_y, own_n, risk_step_ticks=qcfg.get("risk_step_ticks"),
            completion_depth=int(qcfg.get("depth_ticks", RN1.DEPTH_TICKS)))
        out = self._touch_ev_qualified(
            slug, out, lambda: self.rn1_touch_ev(
                slug, None, None, bbo, tick, qty, mk["pos"], mk["cost"],
                hard, own_y, own_n,
                risk_step_ticks=qcfg.get("risk_step_ticks"),
                completion_depth=int(qcfg.get("depth_ticks",
                                              RN1.DEPTH_TICKS))))
        joined = getattr(self, "_rn1_joined", None)
        if joined is None:
            joined = self._rn1_joined = {}
        joined[slug] = bool(out["touch"]) or any(
            out["sides"][s_].get("role") == "complete" and
            out["depth_side"][s_] == 0 for s_ in ("YES", "NO"))
        key = json.dumps([out["depth_side"], out["hard"]], sort_keys=True)
        if mk.get("touch_ev_logged") != key:
            mk["touch_ev_logged"] = key
            log("RN1_TOUCH_EV", slug=slug, depth=out["depth_side"],
                hard=out["hard"], qty=out["qty"],
                net_usd_per_hour=out["net_usd_per_hour"],
                reward_mult=out["reward_mult"],
                qualification=out.get("qualification"),
                unqualified_reward_usd_per_hour=out.get(
                    "unqualified_reward_usd_per_hour"),
                sides={s_: {k: v for k, v in sd.items() if k != "rows"} |
                       {"rows": {str(d): {k2: r.get(k2) for k2 in (
                           "px", "why", "ev_usd_per_hour",
                           "reward_usd_per_hour",
                           "fill_cost_usd_per_hour")}
                           for d, r in (sd.get("rows") or {}).items()}}
                       for s_, sd in out["sides"].items()},
                label=out["label"])
        return out

    def _touch_ev_qualified(self, slug: str, out: Dict[str, Any],
                            unrewarded: Any) -> Dict[str, Any]:
        """RN1_REWARD_QUAL_SCOPE "account_day": RN1_TOUCH_EV's reward
        counts only when the account/day qualification holds with this
        market's scored bids added; otherwise the same scoring with no
        reward (`unrewarded()`). Scope "market": unchanged."""
        if RN1_REWARD_QUAL_SCOPE not in RN1_QUAL_UNIFIED or \
                float(out.get("reward_usd_per_hour") or 0.0) <= 0.0:
            return out
        try:
            q = self.rn1_reward_qualifies(
                slug, extra_usd_h=float(out["reward_usd_per_hour"]))
            if q["qualifies"]:
                return dict(out, qualification=q)
            alt = unrewarded()
            return dict(alt, qualification=q,
                        unqualified_reward_usd_per_hour=out[
                            "reward_usd_per_hour"])
        except Exception:                                 # noqa: BLE001
            return out

    def _touch_ev_score(self, slug: str, book: Mapping[str, Any],
                        per: Mapping[str, Any], bb: D, ba: D, tick: D
                        ) -> Dict[str, Any]:
        """RN1_TOUCH_EV at selection: rn1_touch_ev on the candidate's book,
        its inventory (none if untracked), its hard protections (the same
        30 s mid range the controller reads) and each clip size the net
        ranking compares (capped at the phase size the controller would
        send). Stores the chosen size as the net ranking does."""
        now = time.time()
        mk = (self.b.d.get("markets") or {}).get(slug) or {}
        pos = mk.get("pos") or {"YES": "0", "NO": "0"}
        cost = mk.get("cost") or {"YES": "0", "NO": "0"}
        bbo = {"bid": bb, "ask": ba}
        rng = self._vol(slug, bbo, now)
        hard = self.rn1_touch_hard(slug, mk, bbo, tick, rng)
        own_y, own_n = [], []
        if mk.get("orders"):
            try:
                own_y, own_n = self._rn1_visible_own(
                    book, self._rn1_mature_own(mk, now), tick)
            except Exception:                             # noqa: BLE001
                own_y, own_n = [], []
        top = rn1_live_qty() if self.event_live(slug) else rn1_pregame_qty()
        top = min(top, rn1_max_clip())
        sizes = [x for x in RN1_NET_SIZES if 0 < x <= top] or [top]
        best = None
        for q_ in sizes:
            got = self.rn1_touch_ev(slug, book, per, bbo, tick, q_, pos,
                                    cost, hard, own_y, own_n)
            got = self._touch_ev_qualified(
                slug, got, lambda q_=q_: self.rn1_touch_ev(
                    slug, None, None, bbo, tick, q_, pos, cost, hard,
                    own_y, own_n))
            if best is None or got["net_usd_per_hour"] > \
                    best["net_usd_per_hour"] + 1e-12:
                best = got
        cap_usd = float(best["capital_usd"])
        dep = min(best["depth_side"].values())
        out = {"usd_per_hour": best["reward_usd_per_hour"],
               "net_usd_per_hour": best["net_usd_per_hour"],
               "capital_usd": str(best["capital_usd"]),
               "per_dollar_hour": round(best["net_usd_per_hour"] / cap_usd,
                                        6) if cap_usd > 0 else 0.0,
               "depth": dep, "depth_side": best["depth_side"],
               "qty": best["qty"], "touch_ev": True, "hard": hard}
        choice = getattr(self, "_net_choice", None)
        if choice is None:
            choice = self._net_choice = {}
        choice[slug] = {"t": now, "depth": dep, "qty": D(best["qty"]),
                        "reward_usd_per_hour":
                            float(best.get("reward_usd_per_hour") or 0.0),
                        "net_usd_per_hour": best["net_usd_per_hour"],
                        "depth_side": best["depth_side"]}
        return out

    def duration_info(self, slug: str,
                      now: Optional[float] = None) -> Dict[str, Any]:
        """See RN1_DURATION_RANK: expected remaining quoting time (to the
        late cut-off) and the ranking factor it gives this candidate."""
        from src import pmus_duration_rank as DR
        tab = getattr(self, "_dur_table", None)
        if tab is None or tab[0] != RN1_DURATION_MIN:
            tab = self._dur_table = (RN1_DURATION_MIN,
                                     DR.parse_table(RN1_DURATION_MIN))
        states = getattr(self.v, "states", {}) or {}
        gs = (self.b.d["markets"].get(slug) or {}).get("game_start")
        if gs is None:
            gs = self._game_start.get(slug)
        rem, why = DR.remaining_s(
            slug, time.time() if now is None else now, gs,
            states.get(slug), slug in states, tab[1])
        return {"remaining_s": rem, "why": why,
                "factor": DR.factor(rem, RN1_DURATION_HORIZON_S,
                                    RN1_DURATION_UNKNOWN_FACTOR),
                "excluded": DR.excluded(rem, RN1_MIN_REMAINING_S)}

    def live_first_class(self, slug: str,
                         now: Optional[float] = None) -> str:
        """See RN1_LIVE_FIRST: live / near / distant / unknown."""
        from src import pmus_duration_rank as DR
        states = getattr(self.v, "states", {}) or {}
        gs = (self.b.d["markets"].get(slug) or {}).get("game_start")
        if gs is None:
            gs = self._game_start.get(slug)
        return DR.start_class(time.time() if now is None else now, gs,
                              slug in states, RN1_NEAR_START_S)

    def live_first_info(self, slug: str, per_dollar_hour: float,
                        now: Optional[float] = None) -> Dict[str, Any]:
        """See RN1_LIVE_FIRST: class, remaining time, turnover factor and
        the ranking score for one per-dollar-hour estimate."""
        from src import pmus_duration_rank as DR
        di = self.duration_info(slug, now)
        cls_ = self.live_first_class(slug, now)
        rem = di["remaining_s"]
        return {"class": cls_, "rank": DR.CLASS_RANK.get(cls_, 2),
                "remaining_s": None if rem is None else int(rem),
                "turnover": round(DR.turnover(
                    rem, RN1_DURATION_HORIZON_S,
                    RN1_DURATION_UNKNOWN_FACTOR), 4),
                "score": round(DR.live_first_score(
                    per_dollar_hour, rem, RN1_DURATION_HORIZON_S,
                    RN1_DURATION_UNKNOWN_FACTOR), 8)}

    def slot_claimants(self) -> list:
        """Markets that hold, or are waiting for, a quoting slot: every
        slot holder (_rn1_slot_holder, so RN1_SLOTS_COUNT_INVENTORY decides
        whether a market carrying an unpaired leg counts), plus each active
        market with no unpaired leg (it will try to open). With
        RN1_SLOTS_COUNT_INVENTORY=0 a market carrying an unpaired leg and no
        resting opening/extension bid is not a claimant; it stays tracked,
        completed, audited and counted in every limit as before."""
        out = []
        for s_, m_ in (self.b.d.get("markets") or {}).items():
            if m_.get("done"):
                continue
            try:
                bal = D(str(m_["pos"]["YES"])) == D(str(m_["pos"]["NO"]))
            except Exception:                             # noqa: BLE001
                bal = False
            if self._rn1_slot_holder(m_) or bal:
                out.append(s_)
        return out

    def _lf_positive(self, slug: str) -> bool:
        hit = (getattr(self, "_share_cache", None) or {}).get(slug)
        if not hit or time.monotonic() - hit[0] > RANK_SHARE_TTL_S:
            return False
        o = hit[1]
        v = o.get("net_usd_per_hour") if RN1_NET_RANK else \
            o.get("usd_per_hour")
        return float(v or 0.0) > 0 and \
            float(o.get("per_dollar_hour") or 0.0) > 0

    def _lf_note(self, kind: str, key: Any, **kw: Any) -> None:
        seen = getattr(self, "_lf_logged", None)
        if seen is None:
            seen = self._lf_logged = {}
        k = json.dumps(key, sort_keys=True, default=str)
        if seen.get(kind) != k:
            seen[kind] = k
            log(kind, **kw)

    def _lf_could_select(self, slug: str, acct: Mapping[str, Any]) -> bool:
        """The side-effect-free preconditions of _try_select, so a market
        is never released for a candidate that could not take its slot."""
        if self.foreign_exposure(slug, acct):
            return False
        ek = event_key(slug)
        # PMUS_RN1_SOCCER (allowlisted leagues): the match's OTHER outcome
        # markets may be held together -- the match is one risk under the
        # joint caps (rn1_soccer_group_why). Anything else of the same game
        # still blocks, as before.
        if any(s_ != slug and event_key(s_) == ek and not (
                self.rn1_soccer_ok(slug) and self.rn1_soccer_ok(s_))
               for s_ in self.active()):
            return False
        spec = self.v.spec(slug)
        bbo = self.v.bbo(slug) if spec else None
        return bool(spec and bbo and open_prices(
            bbo["bid"], bbo["ask"], spec["tick"]) is not None)

    def _lf_yield_one(self, cand: str) -> Optional[str]:
        """Release ONE flat distant market for a live/near candidate (see
        RN1_LIVE_FIRST). Flat = no contracts on either side and no
        completion bid resting. Its opening/extension bids are cancelled
        through _cancel_legs; it is released only if it is still flat with
        nothing open afterwards (a fill that raced the cancel keeps it,
        managed as any market holding inventory). Returns the slug
        released, or None."""
        now = time.time()
        pool = []
        for s_, m_ in (self.b.d.get("markets") or {}).items():
            if m_.get("done") or self.live_first_class(s_, now) != "distant":
                continue
            try:
                if D(str(m_["pos"]["YES"])) != 0 or \
                        D(str(m_["pos"]["NO"])) != 0:
                    continue
            except Exception:                             # noqa: BLE001
                continue
            opn = [(oid, o) for oid, o in (m_.get("orders") or {}).items()
                   if o.get("open")]
            if any(is_completion_stage(o.get("stage")) for _i, o in opn):
                continue
            hit = (getattr(self, "_share_cache", None) or {}).get(s_)
            pdh = float(((hit or (0, {}))[1]).get("per_dollar_hour") or 0.0)
            sc = self.live_first_info(s_, pdh, now)["score"] if pdh > 0 \
                else pdh
            pool.append((sc, s_, opn))
        if not pool:
            return None
        pool.sort(key=lambda x: (x[0], x[1]))
        _sc, slug, opn = pool[0]
        mk = self.b.m(slug)
        try:
            self._cancel_legs(slug, opn)
        except Exception as exc:                          # noqa: BLE001
            log("live_first_yield_error", slug=slug, for_slug=cand,
                err=str(exc)[:160])
            return None
        if D(str(mk["pos"]["YES"])) != 0 or D(str(mk["pos"]["NO"])) != 0 \
                or any(o.get("open") for o in mk["orders"].values()):
            log("live_first_yield_kept", slug=slug, for_slug=cand,
                pos=mk["pos"], label="a fill raced the cancel or an order "
                                     "is still open: market kept")
            return None
        mk["done"] = True
        mk["released_flat"] = now
        mk.pop("idle_since", None)
        self.b.save()
        log("released_flat_market", slug=slug, pos=mk["pos"], mode="rn1",
            why="live_first_yield", for_slug=cand,
            cancelled=len(opn), reselectable=RN1_RESELECT,
            label="flat distant pre-game market yields its slot to a "
                  "live/near-start market with a positive estimate")
        return slug

    def slot_gate(self, slug: str, acct: Mapping[str, Any]) -> bool:
        """RN1_LIVE_FIRST / RN1_SELECT_NO_CHURN: may `slug` be selected
        now? Off (both flags) this is never called."""
        claim = self.slot_claimants()
        cls_ = self.live_first_class(slug) if RN1_LIVE_FIRST else None
        if RN1_LIVE_FIRST and cls_ == "distant":
            dist = [s_ for s_ in claim
                    if self.live_first_class(s_) == "distant"]
            if len(dist) >= max(0, RN1_DISTANT_MAX_SLOTS):
                self._lf_note("RN1_LIVE_FIRST_DISTANT_CAP", sorted(dist),
                              distant=sorted(dist),
                              max_slots=RN1_DISTANT_MAX_SLOTS,
                              label="distant pre-game markets already "
                                    "claim their maximum slots")
                return False
        if len(claim) < MAX_MARKETS:
            return True
        if RN1_LIVE_FIRST and cls_ in ("live", "near") and \
                self._lf_positive(slug) and \
                self._lf_could_select(slug, acct):
            if self._lf_yield_one(slug) is not None and \
                    len(self.slot_claimants()) < MAX_MARKETS:
                return True
        if RN1_SELECT_NO_CHURN:
            self._lf_note("RN1_SELECT_NO_CHURN", sorted(claim),
                          claimants=sorted(claim), max_markets=MAX_MARKETS,
                          label="every slot is held by a market that "
                                "cannot be released: nothing selected")
            return False
        return True

    def rank_by_reward_share(self, scored: list,
                             rn1_rw: Mapping[str, Any]) -> list:
        """See RN1_RANK_SHARE. Order: programme markets with a positive
        estimate (best per dollar first), then programme markets not yet
        estimated (pool rate, as before), then those estimated at zero or
        unreadable, then markets with no paying programme. Never drops a
        candidate."""
        cache = getattr(self, "_share_cache", None)
        if cache is None:
            cache = self._share_cache = {}
        nowm = time.monotonic()
        have = [r for r in scored if r[2] in rn1_rw]
        todo = [r[2] for r in have
                if not (cache.get(r[2]) and
                        nowm - cache[r[2]][0] <= RANK_SHARE_TTL_S)]
        # take the new ones in turn across competitions, so one deep
        # competition with many markets cannot use up every read
        by_comp: Dict[str, list] = {}
        for s_ in todo:
            by_comp.setdefault(s_.split("-")[1] if "-" in s_ else "", []
                               ).append(s_)
        pick: list = []
        while by_comp and len(pick) < max(0, RANK_SHARE_NEW_PER_ROUND):
            for comp in sorted(by_comp):
                if len(pick) >= max(0, RANK_SHARE_NEW_PER_ROUND):
                    break
                pick.append(by_comp[comp].pop(0))
                if not by_comp[comp]:
                    del by_comp[comp]
        for s_ in pick:
            try:
                got = self._reward_share_score(s_, rn1_rw[s_])
            except Exception as exc:                      # noqa: BLE001
                got = {"why": "share_error:%s" % type(exc).__name__}
            cache[s_] = (nowm, got)

        def cls(r: tuple) -> tuple:
            slug = r[2]
            if slug not in rn1_rw:
                return (3, 0.0)
            hit = cache.get(slug)
            if not hit or nowm - hit[0] > RANK_SHARE_TTL_S:
                return (1, -float(rn1_rw[slug]["pool_rate_usd_s"]))
            v = float(hit[1].get("per_dollar_hour") or 0.0)
            if RN1_RANK_BY == "net_per_hour":
                # slots, not capital, bind: the absolute reward-adjusted
                # net of the slot, no turnover / duration / live preference
                nv = hit[1].get("net_usd_per_hour")
                nv = float(nv if nv is not None else 0.0) if v > 0 else 0.0
                return (0, -nv) if nv > 0 else (2, 0.0)
            if RN1_LIVE_FIRST and v > 0:
                lf = self.live_first_info(slug, v)
                return (0, -lf["score"], lf["rank"])
            if RN1_DURATION_RANK and v > 0:
                v *= dur(slug)
            return (0, -v) if v > 0 else (2, 0.0)
        _dur: Dict[str, float] = {}

        def dur(slug: str) -> float:
            if slug not in _dur:
                _dur[slug] = float(self.duration_info(slug)["factor"])
            return _dur[slug]
        out = sorted(scored, key=lambda r: (cls(r), r))
        if RN1_NET_RANK:
            # only markets with a fresh positive net estimate are selected
            out = [r for r in out if cls(r)[0] == 0]
        top = [(r[2], (cache.get(r[2]) or (0, {}))[1]) for r in out[:4]]
        key = json.dumps([(s_, o.get("per_dollar_hour")) for s_, o in top])
        if key != getattr(self, "_share_rank_logged", None):
            self._share_rank_logged = key
            log("RN1_SHARE_RANK", candidates=len(scored),
                with_paying_programme=len(have),
                estimated=sum(1 for r in have if cache.get(r[2])),
                first=[{"slug": s_,
                        "per_dollar_hour": o.get("per_dollar_hour"),
                        "usd_per_hour": o.get("usd_per_hour"),
                        "capital_usd": o.get("capital_usd"),
                        "why": o.get("why"),
                        **({"duration_factor": round(dur(s_), 4)}
                           if RN1_DURATION_RANK else {}),
                        **({"live_first": self.live_first_info(
                            s_, float(o.get("per_dollar_hour") or 0.0))}
                           if RN1_LIVE_FIRST else {})}
                       for s_, o in top],
                label="THEORETICAL reward of our own quote per dollar "
                      "committed; ranking only, not a payment")
        return out

    def rank_net(self, scored: list) -> list:
        """Reorder selection candidates by src/pmus_sports_phase.opportunity:
        the marginal test's net for a first bid on the better side, plus the
        theoretical reward per contract (haircut, and only when the day's
        projection clears the minimum payout). Ranking only: every bid still
        has to pass the marginal test and every cap when it is quoted."""
        from src import pmus_sports_rewards as R
        cache = getattr(self, "_rank_cache", None)
        if cache is None:
            cache = self._rank_cache = {}
        nowm = time.monotonic()
        fresh = 0
        rows = []
        self._rank_watch = [r[2] for r in scored[:max(0, RANK_WATCH_MAX)]]
        for r in scored:
            slug = r[2]
            hit = cache.get(slug)
            if hit and nowm - hit[0] <= (
                    RANK_TTL_S if hit[1].get("net") is not None
                    else min(RANK_TTL_S, RANK_NO_FAIR_TTL_S)):
                rows.append((slug, hit[1]))
                continue
            if fresh >= max(0, RANK_NEW_PER_ROUND):
                rows.append((slug, {"net": None, "why": "not_priced_yet"}))
                continue
            fresh += 1
            try:
                opp = self._opportunity(slug, R)
            except Exception as exc:                  # noqa: BLE001
                opp = {"net": None, "why": "rank_error:%s"
                       % type(exc).__name__}
            cache[slug] = (nowm, opp)
            rows.append((slug, opp))
        if RN1_DURATION_RANK:
            rows = [(s, self._duration_opp(s, o)) for s, o in rows]
        order = [s for s, _o in PH.rank(rows)]
        by = {r[2]: r for r in scored}
        top = [(s, dict(rows)[s]) for s in order[:4]]
        key = json.dumps([(s, str(o.get("net"))) for s, o in top])
        if key != getattr(self, "_rank_logged", None):
            self._rank_logged = key
            log("RN1_NET_RANK", candidates=len(scored),
                first=[{"slug": s, "net": o.get("net"),
                        "trade": o.get("trade"), "reward": o.get("reward"),
                        "why": o.get("why"),
                        **({"duration_factor": o.get("duration_factor")}
                           if RN1_DURATION_RANK else {})}
                       for s, o in top],
                label="expected trading result plus haircut theoretical "
                      "reward, per contract; an estimate, not a payment")
        if RN1.EDGE_GATE:
            priced = {s for s, o in rows if o.get("net") is not None}
            order = [s for s in order if s in priced]
        return [by[s] for s in order]

    def _duration_opp(self, slug: str, o: Mapping[str, Any]
                      ) -> Dict[str, Any]:
        """RN1_RANK_NET under RN1_DURATION_RANK: the reward term counts
        only for min(remaining, horizon), i.e. it is scaled by the factor.
        The trading term (per fill, not per hour) is unchanged; a market
        with no ranking number keeps none. The cached estimate is not
        modified."""
        f = float(self.duration_info(slug)["factor"])
        if o.get("net") is None or o.get("trade") is None:
            return dict(o, duration_factor=round(f, 4))
        rw = D(str(o.get("reward") or 0)) * D(str(round(f, 6)))
        return dict(o, net=D(str(o["trade"])) + rw, reward=rw,
                    duration_factor=round(f, 4))

    def guard_tracked(self) -> list:
        """Markets the international feed should follow: the active ones,
        plus the few candidates the ranking is trying to price."""
        act = list(self.active())
        out = act + [s for s in getattr(self, "_rank_watch", ())
                     if s not in act]
        if RN1_SOCCER:
            # the three-way sum check needs every outcome's intl mid
            from src import pmus_sports_soccer_fair as SF
            for s_ in list(out):
                if self.rn1_soccer_ok(s_):
                    out += [x for x in SF.match_of(s_)["siblings"]
                            if x not in out]
        return out

    def _opportunity(self, slug: str, R: Any) -> Dict[str, Any]:
        spec = self.v.spec(slug)
        bbo = self.v.bbo(slug) if spec else None
        fair = self.intl_fair(slug)
        if not spec or not bbo or fair is None:
            return PH.opportunity(edge_yes=None, edge_no=None)
        tick = D(str(spec["tick"]))
        live = self.event_live(slug)
        qty = max(rn1_live_qty() if live else rn1_pregame_qty(),
                  D(str(spec["min_qty"])))
        ybid, nbid = D(str(bbo["bid"])), D("1") - D(str(bbo["ask"]))
        e = {s: RN1.edge(side=s, px=p, tick=tick, fair_yes=fair,
                         adds_exposure=True).get("net_edge")
             for s, p in (("YES", ybid), ("NO", nbid))}
        per_h, pays = 0.0, False
        prog = (self._prog.get(slug) or (0, None))[1]
        per = R.paying_period(prog, time.time(), event_live=live) \
            if prog and reward_allowed(slug) else None
        if per:
            book = (self._reward_get()(f"/v1/markets/{slug}/book") or {}
                    ).get("marketData") or {}
            est = R.quote_estimate(book, per, float(ybid), float(nbid),
                                   float(qty), float(tick))
            usd_h = float(est.get("est_usd_per_hour") or 0.0)
            per_h = usd_h / float(qty)
            active = sum(float((self.b.m(s).get("rn1_reward_est") or {}
                                ).get("est_usd_per_hour") or 0.0)
                         for s in self.active())
            pays = (active + usd_h) * RN1_REWARD_PROJECT_H + 1e-9 >= \
                RN1_REWARD_MIN_DAY_USD
            if RN1_REWARD_QUAL_SCOPE in RN1_QUAL_UNIFIED:
                pays = self.rn1_reward_qualifies(
                    slug, extra_usd_h=usd_h * rn1_reward_cal(slug, per)
                )["qualifies"]
        return PH.opportunity(
            edge_yes=e["YES"], edge_no=e["NO"],
            reward_usd_per_contract_hour=per_h, horizon_h=RANK_HORIZON_H,
            reward_haircut=(rn1_reward_mult(slug, per)
                            if RN1_REWARD_CALIBRATION and per
                            else RANK_REWARD_HAIRCUT), reward_pays=pays)

    def select(self, acct: Mapping[str, Any]) -> None:
        if os.getenv(ENABLED_ENV, "").upper() != "YES":
            return
        if self.b.total_unmatched() >= TOTAL_CAP:
            return
        if self.full() and not (RN1.ON and RN1_LIVE_FIRST and
                                len(self.active()) < MAX_TRACKED):
            # RN1_LIVE_FIRST: full slots may still yield to a live or
            # near-start candidate (slot_gate); the tracked bound holds
            return
        try:
            targets = self.slate_targets()
        except Exception as exc:                      # noqa: BLE001
            log("discovery_error", err=str(exc)[:160])
            return
        if slate() != "live":
            self._game_start.update({t["slug"]: t["game_start"]
                                     for t in targets if "game_start" in t})
        scored = []
        # LIVE 2026-11-23 14:30Z: a pinned market was never selected and the
        # bot logged no reason. For a PINNED market every way of not becoming
        # a candidate is now named (logged when it changes).
        _pin_why: Dict[str, str] = {}
        _pinned = rn1_pin() if RN1.ON else None
        _seen = set()
        _dur_out: list = []
        for tg in targets:
            slug = tg["slug"]
            _seen.add(slug)
            if slug in self.b.d["markets"] and \
                    not self.reselectable(self.b.d["markets"][slug]):
                if _pinned and slug in _pinned and \
                        self.b.d["markets"][slug].get("done"):
                    _pin_why[slug] = "already_traded_and_released"
                continue
            meta = tg.get("market_meta") or {}
            b = self.v.gw._qnum(meta.get("bestBidQuote"))
            a = self.v.gw._qnum(meta.get("bestAskQuote"))
            if b is None or a is None:
                _pin_why[slug] = "no_two_sided_quote_in_listing"
                continue
            _late = late_for(
                slug, (getattr(self.v, "states", {}) or {}).get(slug))
            if excluded(slug) or _late:
                _pin_why[slug] = "excluded" if excluded(slug) \
                    else "late:%s" % _late
                continue
            b, a = D(str(b)), D(str(a))
            if not (BAND_LO <= b <= BAND_HI):
                _pin_why[slug] = "bid_outside_band:%s not in %s..%s" % (
                    b, BAND_LO, BAND_HI)
                continue
            if self.scope_block(slug):
                _pin_why[slug] = "competition_not_permitted"
                continue         # outside the owner's scope: no slot
            if OPEN_EV_GATE and OPEN_GATE_ALLOW_SPORTS and \
                    not self.open_gate(slug)["allow"]:
                _pin_why[slug] = "competition_not_permitted"
                continue         # would be gated at open: don't take a slot
            if RN1_DURATION_RANK:
                _di = self.duration_info(slug)
                if _di["excluded"]:
                    _pin_why[slug] = "remaining_below_min:%ds<%ds" % (
                        int(_di["remaining_s"]), int(RN1_MIN_REMAINING_S))
                    _dur_out.append((slug, int(_di["remaining_s"])))
                    continue     # about to end: no slot
            scored.append((a - b, abs(b - D("0.5")), slug))
        if RN1_DURATION_RANK:
            _dk = json.dumps(sorted(s_ for s_, _r in _dur_out))
            if _dk != getattr(self, "_dur_logged", None):
                self._dur_logged = _dk
                log("RN1_DURATION_RANK", excluded=len(_dur_out),
                    min_remaining_s=RN1_MIN_REMAINING_S,
                    horizon_s=RN1_DURATION_HORIZON_S,
                    first=[{"slug": s_, "remaining_s": r_}
                           for s_, r_ in sorted(_dur_out)[:20]],
                    label="expected remaining quoting time below the "
                          "minimum: not a candidate; ranking only")
        _pin_why = {k: v for k, v in _pin_why.items()
                    if _pinned and k in _pinned}
        if _pinned:
            for slug in _pinned:
                if slug not in _seen and slug not in self.b.d["markets"]:
                    _pin_why[slug] = "not_in_slate:%s" % slate()
            key = json.dumps(_pin_why, sort_keys=True)
            if key != getattr(self, "_pin_why_logged", None):
                self._pin_why_logged = key
                if _pin_why:
                    log("PIN_NOT_CANDIDATE", reasons=_pin_why,
                        targets=len(targets),
                        label="why a pinned market is not being selected")
        if RN1.ON and rn1_pin() is not None:
            _pin = rn1_pin()
            scored = [r for r in scored if r[2] in _pin]
        scored.sort()
        if self.guard is not None:
            self.guard.prefetch([s for *_x, s in scored])
            if self.guard.require:
                # open only where intl can price the market (resolved in the
                # guard's background thread; unresolved -> next round)
                if _pinned:
                    _no = sorted(r[2] for r in scored if r[2] in _pinned
                                 and not self.guard.token(r[2]))
                    if _no and _no != getattr(self, "_pin_noguard", None):
                        log("PIN_NOT_CANDIDATE",
                            reasons={x: "no_international_price_yet"
                                     for x in _no})
                    self._pin_noguard = _no
                scored = [r for r in scored if self.guard.token(r[2])]
        if RN1.ON and RN1_REWARD_RANK and scored:
            rn1_rw: Dict[str, Any] = {}
            try:
                rn1_rw = self.reward_programs([s for *_x, s in scored])
            except Exception as exc:                  # noqa: BLE001
                log("reward_discovery_error", err=str(exc)[:160])
            scored.sort(key=lambda r: (
                0 if r[2] in rn1_rw else 1,
                -rn1_rw[r[2]]["pool_rate_usd_s"] if r[2] in rn1_rw else 0,
                r))
            if RN1_RANK_SHARE:
                scored = self.rank_by_reward_share(scored, rn1_rw)
            n_all = len(scored)
            if RN1_REWARD_ONLY:
                scored = [r for r in scored if r[2] in rn1_rw]
            key = (n_all, len(rn1_rw), RN1_REWARD_ONLY,
                   scored[0][2] if scored else None)
            if key != getattr(self, "_rn1_rank_logged", None):
                self._rn1_rank_logged = key
                log("RN1_REWARD_RANK", candidates=n_all,
                    with_paying_programme=len(rn1_rw),
                    reward_only=RN1_REWARD_ONLY,
                    first=[{"slug": r[2],
                            "pool_rate_usd_s": (rn1_rw.get(r[2]) or {}).get(
                                "pool_rate_usd_s"),
                            "period": (rn1_rw.get(r[2]) or {}).get("period")}
                           for r in scored[:4]],
                    label="published programme, not a payment")
        if RN1.ON and RN1_RANK_NET and scored:
            scored = self.rank_net(scored)
        if RN1.ON and RN1_TRADING_FIRST and RN1_TF_SELECT in ("first",
                                                             "only") \
                and scored:
            scored = self.rn1_tf_select(scored)
        rewarded: Dict[str, Any] = {}
        if rewards_on() and scored:
            try:
                rewarded = self.reward_programs([s for *_x, s in scored])
            except Exception as exc:                  # noqa: BLE001
                log("reward_discovery_error", err=str(exc)[:160])
            if not reward_first_on():
                # rewarded in-band markets FIRST, richest pool rate first;
                # the rest keep today's order (tightest spread, nearest 0.50)
                scored.sort(key=lambda r: (
                    0 if r[2] in rewarded else 1,
                    -rewarded[r[2]]["pool_rate_usd_s"] if r[2] in rewarded
                    else 0, r))
        if reward_first_on() and rewarded:
            # REWARD-FIRST: up to REWARD_MARKETS rewarded markets, best
            # THEORETICAL expected reward first; then today's order with no
            # reward tag (a rewarded market past the cap quotes normally)
            ranked = self.reward_first_rank(rewarded) \
                if self.rewarded_active() < REWARD_MARKETS else []
            for slug, est in ranked:
                if self.full() or \
                        self.rewarded_active() >= REWARD_MARKETS:
                    break
                self._try_select(slug, acct, rewarded[slug], est)
            if REWARD_ONLY:
                return
            for _sp, _c, slug in scored:
                if self.full():
                    break
                if slug not in self.b.d["markets"]:
                    self._try_select(slug, acct, None)
            return
        if reward_first_on() and REWARD_ONLY:
            return                     # no rewarded market in band right now
        for _sp, _c, slug in scored:
            if self.full():
                if not (RN1.ON and RN1_LIVE_FIRST) or \
                        len(self.active()) >= MAX_TRACKED:
                    break
                # RN1_LIVE_FIRST: only a yield can make room
                if self.slot_gate(slug, acct) and not self.full():
                    self._try_select(slug, acct, rewarded.get(slug))
                continue
            if RN1.ON and (RN1_LIVE_FIRST or RN1_SELECT_NO_CHURN) and \
                    not self.slot_gate(slug, acct):
                continue
            if RN1.ON and self.rn1_live_slots_full(slug):
                continue                  # RN1_MAX_LIVE_MARKETS: live full
            self._try_select(slug, acct, rewarded.get(slug))

    def rewarded_active(self) -> int:
        return sum(1 for s in self.active() if self.b.m(s).get("reward"))

    @staticmethod
    def reselectable(mk: Mapping[str, Any],
                     now: Optional[float] = None) -> bool:
        """A market the bot released while FLAT may be picked again. Never
        one that closed, settled, still holds contracts or has an order
        resting, and not before the cooldown."""
        if not RN1_RESELECT or not mk.get("done"):
            return False
        at = mk.get("released_flat")
        if at is None or mk.get("settle_px") is not None or \
                mk.get("closed"):
            return False
        try:
            if D(str(mk["pos"]["YES"])) != 0 or D(str(mk["pos"]["NO"])) != 0:
                return False
        except Exception:                                 # noqa: BLE001
            return False
        if any(o.get("open") for o in (mk.get("orders") or {}).values()):
            return False
        now = time.time() if now is None else now
        cooldown = RN1_RESELECT_COOLDOWN_S
        if RN1_TRADING_FIRST:
            # Repeated flat releases consume scarce request capacity. Bound
            # the backoff to five minutes; legacy mode is unchanged.
            repeats = max(0, int(mk.get("reselected") or 0))
            cooldown = max(cooldown, min(300.0, 60.0 * (2 ** min(repeats, 3))))
        return now - float(at) >= cooldown

    def _try_select(self, slug: str, acct: Mapping[str, Any],
                    rp: Optional[Mapping[str, Any]],
                    est: Optional[Mapping[str, Any]] = None) -> bool:
        if self.foreign_exposure(slug, acct):
            log("skip_foreign_exposure", slug=slug)
            return False
        # OWNER 2026-11-22: each soccer outcome is traded as its own market,
        # with no cross-outcome hedging -- so never hold two markets of the
        # SAME game at once: their results are tied together and would put
        # both slots, and both per-market caps, on one match.
        ek = event_key(slug)
        # PMUS_RN1_SOCCER (allowlisted leagues): the match's OTHER outcome
        # markets may be held together -- the match is one risk under the
        # joint caps (rn1_soccer_group_why). Anything else of the same game
        # still blocks, as before.
        if any(s_ != slug and event_key(s_) == ek and not (
                self.rn1_soccer_ok(slug) and self.rn1_soccer_ok(s_))
               for s_ in self.active()):
            return False
        spec = self.v.spec(slug)
        bbo = self.v.bbo(slug) if spec else None
        if not spec or not bbo or open_prices(bbo["bid"], bbo["ask"],
                                               spec["tick"]) is None:
            return False
        mk = self.b.m(slug)
        if mk.get("done"):
            if not self.reselectable(mk):
                return False             # closed, settled or holding: never
            mk["done"] = False
            mk.pop("released_flat", None)
            mk.pop("idle_since", None)
            mk["reselected"] = int(mk.get("reselected") or 0) + 1
            log("RESELECTED", slug=slug, times=mk["reselected"],
                label="released flat earlier; eligible again; the same "
                      "record, so its orders, fills and costs carry on")
        if slug in self._game_start:
            mk["game_start"] = self._game_start[slug]
        if rp:
            mk["reward"] = rp
            if reward_first_on():
                mk["reward_slot"] = True
        self.b.save()
        log("SELECTED", slug=slug, bid=bbo["bid"], ask=bbo["ask"],
            tick=spec["tick"], **({"reward_first": True} if est else {}))
        if rp:
            log("reward_program", slug=slug, at="select",
                program_id=rp["programId"], pool=rp["pool"],
                period=rp["period"], period_s=rp["period_s"],
                targetSize=rp["targetSize"], maxSpread=rp["maxSpread"],
                pool_rate_usd_s=rp["pool_rate_usd_s"],
                qualifies=None if est is None else est.get("share", 0) > 0,
                est_share=None if est is None else est.get("share"),
                inside_walk=None if est is None else est.get("inside_walk"),
                est_usd_per_day=None if est is None
                else est.get("est_usd_per_day"),
                label="THEORETICAL reward program; never P&L")
        return True

    def _latest_pairs(self) -> Dict[str, Any]:
        last: Dict[str, Any] = {}
        for p in self.b.d.get("pairs") or []:
            last[p["slug"]] = p
        return last

    AUDIT_SINCE = os.getenv("PMUS_TWO_SIDED_AUDIT_SINCE",
                            "2026-11-14T15:30:00Z")

    def pilot_meter(self, rows: list, markets: Mapping[str, Any],
                    venue_net: Optional[Mapping[str, Any]] = None) -> None:
        """RN1 pilot net P&L on a CASH basis (src/pmus_pilot_pnl.py) from
        this audit's venue rows; feeds the campaign loss stop. `venue_net`
        is the complete positions walk of the same audit (None = no walk:
        the position check is skipped and reported as such)."""
        try:
            from src import pmus_pilot_pnl as PP
            r = PP.meter(markets, rows, since=self.since,
                         venue_net=venue_net)
        except Exception as exc:                          # noqa: BLE001
            self._pilot = None                    # -> pilot_meter_unavailable
            log("PILOT_PNL", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return
        self._pilot = r
        self._pilot_at = time.monotonic()
        self.audit_result("fills_unconfirmed", bool(r.get("unconfirmed")),
                          unconfirmed=r.get("unconfirmed"))
        log("PILOT_PNL", ok=True, since=self.since,
            loss_stop_usd=str(PILOT_LOSS_USD),
            new_openings_block=self.pilot_block(),
            cash=self.pilot_cash(r), **r)

    def pilot_cash(self, r: Mapping[str, Any]) -> Dict[str, Any]:
        """Venue account cash next to what the campaign explains. READ-ONLY
        GET, report only (shared account). The baseline is taken once per
        self.since, together with the campaign's expected cash at that
        moment, so a restart mid-campaign does not move it."""
        from src import pmus_pilot_pnl as PP
        try:
            cash = PP.cash_usd(self.v.t.request("GET",
                                                "/v1/account/balances"))
        except Exception as exc:                          # noqa: BLE001
            return {"ok": False,
                    "why": f"{type(exc).__name__}:{str(exc)[:120]}"}
        exp = D(r["expected_cash_change_usd"])
        base = self.b.d.get("pilot_cash_base")
        key = str(self.since)
        if cash is not None and (not base or base.get("since") != key) \
                and not r.get("unconfirmed"):
            base = {"since": key, "cash": str(cash), "expected": str(exp),
                    "t": now_iso()}
            self.b.d["pilot_cash_base"] = base
            self.b.save()
        return PP.cash_check(cash, exp, base if base and
                             base.get("since") == key else None)

    def audit_result(self, kind: str, failed: bool, **detail: Any) -> None:
        """Count consecutive failures of one of the audit's
        reconciliations; see AUDIT_LATCH. A clean result clears its count."""
        st = getattr(self, "_audit_strikes", None)
        if st is None:
            st = self._audit_strikes = {}
        if not failed:
            st.pop(kind, None)
            return
        if not AUDIT_LATCH:
            return
        n = st[kind] = int(st.get(kind, 0)) + 1
        log("AUDIT_FAILED", check=kind, strikes=n,
            latch_at=AUDIT_LATCH_STRIKES, **detail,
            label="new exposure blocked; the next audit is brought forward")
        if n >= AUDIT_LATCH_STRIKES:
            self.latch("audit_" + kind, strikes=n, **detail)
            return
        # bring the next audit forward
        self._last_reward = time.monotonic() - REWARD_CHECK_S + \
            max(0.0, AUDIT_RECHECK_S)

    def audit_age_s(self) -> Optional[float]:
        """Seconds since the last COMPLETED audit; None = none since boot."""
        at = getattr(self, "_audit_ok_at", None)
        return None if at is None else max(0.0, time.monotonic() - at)

    def audit_stale(self) -> bool:
        if AUDIT_MAX_AGE_S <= 0:
            return False
        age = self.audit_age_s()
        return age is None or age > AUDIT_MAX_AGE_S

    def audit_blocks(self) -> Optional[str]:
        st = getattr(self, "_audit_strikes", None)
        if st:
            return "audit_failed:" + ",".join(sorted(st))
        return "audit_stale" if self.audit_stale() else None

    def safety_reads(self) -> Any:
        """Context for the audit's own venue reads: they take the request
        budget's safety class (ahead of quote traffic)."""
        b = getattr(getattr(self.v, "t", None), "budget", None)
        return b.safety() if b is not None and hasattr(b, "safety") \
            else contextlib.nullcontext()

    def audit_round(self) -> bool:
        """The periodic reward read and account audit. False = one of them
        did not complete; the loop then retries after AUDIT_RETRY_S."""
        r_ok = self.reward_check()
        a_ok = self.account_audit()
        ok = bool(r_ok) and bool(a_ok)
        if ECONOMICS_REPORT:
            try:
                self.economics_report(bool(r_ok), bool(a_ok))
            except Exception as exc:                      # noqa: BLE001
                log("CAMPAIGN_ECONOMICS", ok=False,
                    err=f"{type(exc).__name__}:{str(exc)[:160]}")
        if not ok:
            self._audit_incomplete = int(getattr(
                self, "_audit_incomplete", 0)) + 1
            # never later than a re-check an audit failure already asked for
            self._last_reward = min(
                self._last_reward,
                time.monotonic() - REWARD_CHECK_S + max(0.0, AUDIT_RETRY_S))
            log("AUDIT_RETRY", reward_ok=bool(r_ok), audit_ok=bool(a_ok),
                retry_in_s=AUDIT_RETRY_S,
                incomplete_in_a_row=self._audit_incomplete,
                audit_age_s=self.audit_age_s(),
                blocks_new_exposure=self.audit_stale())
        else:
            self._audit_incomplete = 0
        return ok

    def economics_report(self, reward_ok: bool, audit_ok: bool,
                         force: bool = False) -> Optional[Dict[str, Any]]:
        """See ECONOMICS_REPORT. READ-ONLY: joins what the audit round just
        read (the pilot meter, the reward rows) and logs one bounded line.
        No trading effect; nothing here is read by any gate."""
        from src import pmus_campaign_economics as CE
        nowm = time.monotonic()
        last = getattr(self, "_econ_at", None)
        if not force and last is not None and nowm - last < ECONOMICS_MIN_S:
            return None
        rr = getattr(self, "_reward_rows", None)
        if not reward_ok or rr is None or not rr[1]:
            return None              # no reward rows read: nothing to join
        self._econ_at = nowm
        pilot = getattr(self, "_pilot", None)
        pat = getattr(self, "_pilot_at", None)
        win = self.b.d.get("campaign_window") or None
        camp = (self.b.d.get("campaign_auth") or {}).get("active") or {}
        until = None
        if win and self.since is not None and \
                float(win[0]) == float(self.since):
            until = float(win[1])
        r = CE.economics(pilot, rr[2], since=self.since, until=until,
                         our_markets=list(self.b.d.get("markets") or {}),
                         campaign_id=camp.get("id"), rows_total=rr[3])
        log("CAMPAIGN_ECONOMICS", ok=True, reward_ok=reward_ok,
            audit_ok=audit_ok,
            pilot_age_s=None if pat is None else round(nowm - pat, 1),
            reward_rows_age_s=round(nowm - rr[0], 1), **r)
        return r

    def pilot_block(self) -> Optional[str]:
        """Why the pilot's accounting forbids NEW RN1 exposure, or None.
        Owner 2026-11-21: the meter must fail CLOSED -- missing or
        unreconciled venue fills, or no current meter result, stop new
        openings rather than drop out of the loss total. Only the realized
        stop latches; the others lift when the meter is clean again."""
        if PILOT_LOSS_USD <= 0:
            return None
        if self.pilot_loss_stopped():
            return "pilot_loss_stop"
        # every step, from the book: the hourly audit is too slow to be the
        # only at-risk check inside a 60-90 minute window
        from src import pmus_pilot_pnl as PP
        if PP.book_worst_case(self.b.d.get("markets") or {}, self.since) + (
                self.rn1_soccer_book_adjust() if RN1_SOCCER else D(0)) \
                <= -PILOT_LOSS_USD:
            return "pilot_loss_at_risk_book"
        r = getattr(self, "_pilot", None)
        at = getattr(self, "_pilot_at", None)
        if r is None or at is None or \
                time.monotonic() - at > PILOT_METER_MAX_AGE_S:
            return "pilot_meter_unavailable"
        if r.get("unconfirmed"):
            return "pilot_fills_unconfirmed"
        # realized cash (completed pairs - fees + settled residuals) plus
        # every outstanding unpaired contract settling worthless
        if D(r["realized_net_usd"]) + D(r["open_residual_worst_usd"]) \
                <= -PILOT_LOSS_USD:
            return "pilot_loss_at_risk"
        return None

    def pilot_loss_stopped(self) -> bool:
        """Campaign stop: REALIZED pilot net (completed-pair cash - fees +
        settled residual results) <= -PILOT_LOSS_USD blocks NEW RN1
        openings/extensions (completions continue). Latched in the book: a
        restart or a later recovery does not lift it -- the owner clears
        `pilot_loss_stop` explicitly."""
        if PILOT_LOSS_USD <= 0:
            return False
        if self.b.d.get("pilot_loss_stop"):
            return True
        r = getattr(self, "_pilot", None)
        if r and D(r["realized_net_usd"]) <= -PILOT_LOSS_USD:
            self.b.d["pilot_loss_stop"] = {"t": now_iso(),
                                           "net": r["realized_net_usd"]}
            self.b.save()
            log("PILOT_LOSS_STOP", net=r["realized_net_usd"],
                limit=str(PILOT_LOSS_USD),
                effect="no new RN1 openings/extensions until the owner "
                       "clears pilot_loss_stop")
            return True
        return False

    def reconcile_orders_now(self) -> None:
        """The 30-second orphan sweep and, over a fresh account read, the
        release of reconciled reservations. One place, called from the
        main loop and from service_pass, so a long read-only job cannot
        hold either back."""
        if time.monotonic() - getattr(self, "_last_orphan", 0) <= 30:
            return
        self._last_orphan = time.monotonic()
        # reuse the account read's open orders when it is fresh (one
        # signed GET instead of two)
        fresh = self._acct_cache is not None and \
            self._acct_ok_at is not None and \
            time.monotonic() - self._acct_ok_at <= 30
        read_at = self._acct_ok_at if fresh else None
        self.orphan_sweep(self._acct_cache["open_orders"]
                          if fresh else None)
        if fresh and not getattr(self, "_orphan_sweep_failed", False):
            # only over a fresh account read: its order list is the one
            # the sweep has just reconciled
            self.release_reconciled_reservations(read_at, self._acct_cache)

    def service_pass(self) -> None:
        """Order management in the middle of a long read-only job (the
        audit): queued price events are drained every time, and every
        LOOP_S the lock is enforced and each market gets its ordinary step
        (fills read, stale bids pulled, quotes kept current). The account
        read and position check run on their usual 20 s. Never re-enters."""
        if getattr(self, "_in_service", False) or self.stop:
            return
        self._in_service = True
        try:
            self.drain_ws_events()
            now = time.monotonic()
            if now - float(getattr(self, "_last_service", 0.0)) < LOOP_S:
                return
            self._last_service = now
            latched = str(self.enforce_lock() or "").startswith(
                "safety_latch")
            if now - float(getattr(self, "_svc_acct_at", 0.0)) > 20:
                self._svc_acct_at = now
                acc = self.read_account()
                try:
                    self.safety_check(acc)
                except Exception as exc:                  # noqa: BLE001
                    log("safety_check_error", at="service_pass",
                        err=f"{type(exc).__name__}:{str(exc)[:160]}")
                latched = latched or bool(self.b.d.get("safety_latch"))
                # LIVE 2026-11-25: while the hourly audit ran (about ten
                # minutes) the orphan sweep and the release of reconciled
                # reservations waited for it, so capital stayed reserved
                # for up to 402 s, and no heartbeat was logged. Both run
                # here on their usual cadence; neither reads anything the
                # audit depends on.
                self.reconcile_orders_now()
            if time.monotonic() - float(getattr(self, "_last_hb",
                                                0.0)) > 60:
                self._last_hb = time.monotonic()
                try:
                    self.report()
                except Exception as exc:                  # noqa: BLE001
                    log("report_error", at="service_pass",
                        err=type(exc).__name__)
            if latched:
                self.enforce_lock()
            for slug in ([] if latched else list(self.active())):
                if self.stop:
                    break
                self.drain_ws_events()
                try:
                    self.step_market(slug)
                except Exception as exc:                  # noqa: BLE001
                    log("market_error", slug=slug, at="service_pass",
                        err=f"{type(exc).__name__}:{str(exc)[:200]}")
                loop_beat()
            orders_served()
        finally:
            self._in_service = False

    def audit_transport(self) -> Any:
        """The transport the audit's long walk uses: the venue transport,
        with order management run between its requests."""
        from src import pmus_request_budget as RB
        t = RB.SafetyTransport(self.v.t)
        if not AUDIT_INTERLEAVE:
            return t
        return ServicedTransport(t, self.service_pass)

    def account_audit(self) -> bool:
        """VENUE TRUTH for this bot's markets, from /v1/portfolio/activities:
        realizedPnl summed per market and per sport (trades + resolutions),
        and the commissions on OUR side of our trades (taker fees charged,
        maker rebates credited). Owner 2026-11-15: P&L from one source."""
        try:
            from src import pmus_settlement_auditor as SA
            from src import pmus_incentive_audit as IA
            walk = SA.activity_pages(self.audit_transport(),
                                     since=self.AUDIT_SINCE, max_pages=200)
        except Exception as exc:                          # noqa: BLE001
            log("ACCOUNT_AUDIT", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return False
        ours = set(self.b.d.get("markets") or {})
        types: Dict[str, int] = {}
        mine = []
        for r in walk.get("rows") or []:
            body = None
            for k in ("trade", "positionResolution", "accountBalanceChange"):
                if isinstance(r.get(k), Mapping):
                    body = r[k]
                    break
            if not body or str(body.get("marketSlug") or "") not in ours:
                continue
            mine.append(r)
            t = str(r.get("type") or "?")
            types[t] = types.get(t, 0) + 1
        try:
            # OWNER 2026-11-18: study the owner's MANUAL sports fills from
            # this same walk -- read-only, no extra venue call
            from src import pmus_sports_manual_study as MS
            log("MANUAL_FILL_STUDY", **MS.summarize(walk.get("rows") or [],
                                                    ours))
            _mo = MS.manual_orders(walk.get("rows") or [], ours)
            if _mo and _mo != getattr(self, "_manual_orders_logged", None):
                self._manual_orders_logged = _mo
                log("MANUAL_ORDER_IDS", orders=_mo,
                    label="venue order ids behind the owner's manual sports "
                          "trades, from the account activity feed; for the "
                          "ledger's manual-order attestation. Read-only.")
        except Exception as exc:                          # noqa: BLE001
            log("MANUAL_FILL_STUDY", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
        from src import pmus_sports_pnl as PNL
        vr = PNL.venue_realized(mine, ours)
        try:
            fees = IA.credited_on_fills(mine)
        except Exception as exc:                          # noqa: BLE001
            fees = {"error": repr(exc)[:120]}
        book = PNL.book_economics(self.b.d.get("markets") or {})
        with self.safety_reads():
            inv = self.inventory_reconcile()
        # residual = what settled markets realized beyond their completed
        # pairs' gross margin (lone legs, exits; fees if the venue nets them)
        mk_all = self.b.d.get("markets") or {}
        # the venue's PUBLIC final settlement price per market (bounded)
        self.settle_lookups(SETTLE_LOOKUPS_PER_AUDIT)
        bs = PNL.book_settled(mk_all)
        self.pilot_meter(mine, mk_all,
                         venue_net=getattr(self, "_venue_net", None))
        since = self.b.d.get("recovery_since")
        bs_new = PNL.book_settled(mk_all, since=since) if since else None
        settled_margin = D(PNL.book_economics(
            {s: mk_all[s] for s in vr["settled_slugs"]
             if s in mk_all})["gross_pair_margin_usd"])
        log("ACCOUNT_AUDIT", ok=True, since=self.AUDIT_SINCE,
            walk_ended=walk.get("ended"), rows_ours=len(mine), types=types,
            report={
                "1_orders_submitted": book["orders_submitted"],
                "2_venue_confirmed_filled_contracts": book["filled_contracts"],
                "3_completed_pairs": book["completed_pairs"],
                "4_gross_pair_margin_usd": book["gross_pair_margin_usd"],
                "5_fees_charged_usd": fees.get("charged_to_us_usd"),
                "5_rebates_credited_usd": fees.get("credited_to_us_usd"),
                "6_residual_settled_usd": str(
                    vr["settled_realized_usd"] - settled_margin),
                # LIVE 2026-11-15: this read book["unmatched_cost_usd"], the
                # LIFETIME lone-leg cost of every market incl. settled ones
                # ($26.72 while nothing was open). Open = venue-confirmed.
                "6_unmatched_cost_open_usd": (
                    inv["sports_open_unmatched_cost_usd"] if inv
                    else str(self.b.total_unmatched())),
                "6_unmatched_cost_open_source": (
                    "venue_reconciled" if inv and inv["reconciled"]
                    else "venue_unreconciled" if inv else "book_only"),
                "6_unmatched_cost_pending_resolution_usd": (
                    inv["sports_pending_resolution_cost_usd"] if inv
                    else None),
                "6_unmatched_cost_lifetime_usd": book["unmatched_cost_usd"],
                "7_settled_realized_usd": str(vr["settled_realized_usd"]),
                "7_settled_markets": vr["settled_markets"],
                "unsettled_trade_realized_usd": str(
                    vr["unsettled_trade_realized_usd"]),
                "trades_missing_realized": vr["trades_missing_realized"],
                "7b_book_settled_usd": str(bs["settled_usd"]),
                "7b_book_settled_markets": bs["markets"],
                "7b_book_settled_by_sport": bs["by_sport"],
                "7b_worst_market": bs["worst"],
                "7b_since_recovery_on": (
                    {"usd": str(bs_new["settled_usd"]),
                     "markets": bs_new["markets"],
                     "by_sport": bs_new["by_sport"]} if bs_new else None),
                "open_gate": self.open_gate_snapshot(),
                "settled_lookups_pending": sum(
                    1 for m_ in mk_all.values()
                    if m_.get("done") and m_.get("settle_px") is None and (
                        D(m_["pos"]["YES"]) or D(m_["pos"]["NO"]))),
                "venue_vs_book_discrepancies": PNL.discrepancies(
                    vr["by_market"], bs["by_market"], vr["settled_slugs"])},
            settled_by_sport=vr["settled_by_sport"],
            realized_by_market=vr["by_market"],
            label="venue rows only: realized = trade.realizedPnl + "
                  "resolution afterPosition.realized delta; fees are OUR "
                  "side per isAggressor and reported separately; 1-4 and "
                  "unmatched cost are the bot's own book. Rewards are "
                  "separate (REWARD_CHECK).")
        # complete = the venue walk AND the position reconciliation read
        if inv is None:
            return False
        self._audit_ok_at = time.monotonic()
        return True

    def settle_lookups(self, limit: int) -> Dict[str, int]:
        """Record the venue's PUBLIC final settlement price for closed
        markets that still hold contracts, at most `limit` lookups. Only a
        final price of exactly 0 or 1 is recorded; anything else leaves the
        market counted as it was."""
        from src import pmus_settlement_public_px as SP
        looked = found = 0
        mk_all = self.b.d.get("markets") or {}
        # resume after the last market looked at, so a short limit still
        # reaches every market over successive passes
        slugs = list(mk_all)
        start = getattr(self, "_settle_cursor", 0) % max(1, len(slugs))
        for s_ in slugs[start:] + slugs[:start]:
            mk_ = mk_all[s_]
            if looked >= limit or self.stop:
                break                       # shutdown: cancel first
            if mk_.get("settle_px") is not None or not mk_.get("done") or (
                    D(mk_["pos"]["YES"]) == 0 and D(mk_["pos"]["NO"]) == 0):
                continue
            looked += 1
            self._settle_cursor = slugs.index(s_) + 1
            if looked > 1 and SETTLE_LOOKUP_GAP_S > 0:
                time.sleep(SETTLE_LOOKUP_GAP_S)
            try:
                ps = SP.public_settlement(self.v.t, s_)
            except Exception:                             # noqa: BLE001
                continue
            if ps.get("final"):
                mk_["settle_px"] = ps["px"]
                found += 1
                log("SETTLEMENT_RECORDED", slug=s_, px=ps["px"],
                    label="venue public final price; this market no longer "
                          "counts toward the campaign spend ceiling")
        if looked:
            self.b.save()
        return {"looked": looked, "found": found}

    def capital_breakdown(self) -> Dict[str, str]:
        """This campaign's dollars in four separate states. Only the last
        is released; the first three all count toward the spend ceiling."""
        from src import pmus_pilot_pnl as PP
        markets = self.b.d.get("markets") or {}
        rest = unp = paired = released = D(0)
        for s_ in PP.pilot_slugs(markets, self.since):
            mk_ = markets[s_]
            qy, qn = D(str(mk_["pos"]["YES"])), D(str(mk_["pos"]["NO"]))
            cy, cn = D(str(mk_["cost"]["YES"])), D(str(mk_["cost"]["NO"]))
            pairs = min(qy, qn)
            pair_cost = (pairs * cy / qy if qy else D(0)) + \
                        (pairs * cn / qn if qn else D(0))
            settled = mk_.get("settle_px") is not None
            if settled:
                released += cy + cn
                continue
            paired += pair_cost
            unp += RN1.unmatched_usd(mk_["pos"], mk_["cost"])
            for o in (mk_.get("orders") or {}).values():
                if o.get("open"):
                    rest += D(str(o["px"])) * (
                        D(str(o["qty"])) - D(str(o.get("filled") or "0")))
        return {"resting_orders_usd": str(rest),
                "unpaired_inventory_usd": str(unp),
                "paired_awaiting_settlement_usd": str(paired),
                "settled_released_usd": str(released),
                "label": "campaign markets only; settled = the venue's "
                         "public final price is recorded"}

    def inventory_reconcile(self) -> Optional[Dict[str, Any]]:
        """Account-wide venue positions vs this bot's book; logged every
        audit. READ-ONLY: never cancels, sells or adopts anything -- other
        strategies' positions and orders are attributed and left alone."""
        from src import pmus_sports_inventory as INV
        try:
            snap = self.v.positions_snapshot()
        except Exception as exc:                          # noqa: BLE001
            self._venue_net = None       # no walk: position check skipped
            log("INVENTORY_RECONCILE", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return None
        # signed venue position per market for the pilot meter's netting
        # check; only a COMPLETE walk can prove a market is flat. Expired
        # rows are kept: a closed game's unpaired leg is still held until
        # the venue resolves it, and must not read as "position missing".
        self._venue_net = None
        if snap.get("complete"):
            self._venue_net = {}
            for row_ in snap["rows"]:
                pr_ = INV.parse_row(row_)
                if pr_["slug"]:
                    self._venue_net[pr_["slug"]] = str(pr_["net"] or 0)
        rep = INV.reconcile(snap["rows"], snap["open_orders"],
                            self.b.d.get("markets") or {},
                            self.own(), walk_complete=snap["complete"])
        log("INVENTORY_RECONCILE", ok=True, pages=snap["pages"],
            book_open_unmatched_usd=str(self.b.total_unmatched()), **rep)
        # only a COMPLETE walk can show a real difference
        bad = bool(snap.get("complete")) and bool(rep.get("mismatches"))
        self.audit_result("position_mismatch", bad,
                          mismatches=(rep.get("mismatches") or [])[:5])
        return rep

    def orphan_sweep(self, orders: Optional[list] = None) -> None:
        """LIVE 2026-11-15T09:12Z the watchdog latched on DQX6ACVG13ZK: an
        order of OURS resting 310s that our book no longer listed as open.
        Every sweep: any venue-open AUTOMATIC order in a market this bot has
        traded that is NOT an open order in our book is adopted back into the
        book (so the registry names it) and cancelled. Manual orders and
        markets we never traded are never touched."""
        if orders is None:
            try:
                oo = self.v.t.request("GET", "/v1/orders/open") or {}
                orders = [o for o in (oo.get("orders") or [])
                          if isinstance(o, Mapping)]
            except Exception as exc:                      # noqa: BLE001
                log("orphan_sweep_error", err=str(exc)[:160])
                self._orphan_sweep_failed = True
                return
        self._orphan_sweep_failed = False
        mine = set(self.b.d.get("markets") or {})
        tracked = {oid for mk in self.b.d["markets"].values()
                   for oid, o in (mk.get("orders") or {}).items()
                   if o.get("open")}
        for o in orders:
            slug = str(o.get("marketSlug") or "")
            oid = str(o.get("id") or "")
            ind = str(o.get("manualOrderIndicator") or "")
            if not oid or slug not in mine or oid in tracked:
                continue
            if ind and "AUTOMATIC" not in ind:
                continue                      # a human's order: never ours
            mk = self.b.m(slug)
            rec = mk["orders"].get(oid) or {}
            if rec:
                # LIVE 2026-11-22T01:26-01:31Z: five "orphans" were orders
                # the book had ALREADY closed on the venue's own exact
                # status (cancelled or filled); the open-orders list used
                # here can be up to 30 s old. Confirm with the exact order
                # status before adopting: a terminal order is recorded
                # (any fill included) and is not an orphan.
                try:
                    st = self.v.status(oid)
                except Exception as exc:              # noqa: BLE001
                    st = None
                    log("orphan_confirm_error", slug=slug, order_id=oid,
                        err=str(exc)[:160])
                if st is not None and not st["open"]:
                    self._record_fill(slug, rec.get("side") or "YES", oid, st)
                    self.b.save()
                    self.orphan_stale += 1
                    continue
            side = rec.get("side") or ("NO" if "NO" in str(
                o.get("outcomeSide") or "").upper() else "YES")
            mk["orders"][oid] = {**rec, "side": side,
                                 "px": rec.get("px") or str(o.get("price") or "0"),
                                 "qty": rec.get("qty") or str(o.get("quantity") or "0"),
                                 "filled": rec.get("filled") or "0",
                                 "open": True, "stage": rec.get("stage") or "orphan",
                                 "placed_at": rec.get("placed_at") or time.time()}
            self.b.save()
            log("ORPHAN_ADOPTED", slug=slug, order_id=oid, side=side,
                known_before=bool(rec), label="venue-open, missing from book; cancelling")
            try:
                self._cancel_legs(slug, [(oid, mk["orders"][oid])])
            except Exception as exc:                      # noqa: BLE001
                log("orphan_cancel_error", slug=slug, order_id=oid,
                    err=str(exc)[:160])

    def reward_check(self) -> bool:
        """OBSERVED reward earnings from the venue. Only rows the venue marks
        credited are money; pending/skipped are reported, never counted."""
        try:
            from src import pmus_incentive_audit as IA
            with self.safety_reads():
                e = IA.earnings(self.v.t, detail_max=max(
                    50, ECONOMICS_REWARD_ROWS)) if (
                    ECONOMICS_REPORT or RN1_REWARD_CALIBRATION) \
                    else IA.earnings(self.v.t)
        except Exception as exc:                          # noqa: BLE001
            log("REWARD_CHECK", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return False
        if e.get("ok"):
            rn1_set_paid_fraction(e.get("by_status"))
        else:
            rn1_set_paid_fraction(None)               # unreadable: unknown
        if RN1_REWARD_CALIBRATION and e.get("ok"):
            self.reward_calibration(list(e.get("rows_detail") or []))
        if ECONOMICS_REPORT:
            # the economics line reads every row; REWARD_CHECK below keeps
            # reading the first 50 exactly as before
            self._reward_rows = (time.monotonic(), bool(e.get("ok")),
                                 list(e.get("rows_detail") or []),
                                 e.get("rows"))
        if ECONOMICS_REPORT or RN1_REWARD_CALIBRATION:
            e = dict(e, rows_detail=list(e.get("rows_detail") or [])[:50])
        ours = set(self.b.d.get("markets") or {})
        mine = [r for r in (e.get("rows_detail") or [])
                if str(r.get("marketSlug") or "") in ours]
        log("REWARD_CHECK", ok=bool(e.get("ok")), endpoint=e.get("endpoint"),
            rows=e.get("rows"), credited_usd=e.get("credited_usd"),
            accrued_not_credited_usd=e.get("accrued_not_credited_usd"),
            by_status=e.get("by_status"), by_program=e.get("by_program"),
            rows_on_our_markets=len(mine), sample_ours=mine[:10],
            rows_brief=[{k: r.get(k) for k in r if not isinstance(
                r.get(k), (dict, list))} for r in
                (e.get("rows_detail") or [])[:50]],
            error=e.get("error"),
            label="venue /v1/incentives/earnings; ONLY credited_usd is money")
        return bool(e.get("ok"))

    def report(self) -> None:
        pairs = self.b.d["pairs"]
        if self.guard is not None:
            self.guard.track(self.guard_tracked())
        extra = {} if self.ws is None else {
            "ws": self.ws.status(), "reactive_pulls": self.reactive_pulls,
            "orphan_stale_snapshot": self.orphan_stale,
            "fill_poll": dict(self._fill_stats(), on=FILL_LIST_POLL),
            "jump_pauses": self.jump_pauses}
        if self.guard is not None:
            extra["intl_guard"] = self.guard.status()
        _bud = getattr(getattr(self.v, "t", None), "budget", None)
        if _bud is not None:
            extra = dict(extra, venue_requests=_bud.report())
        _gwr = getattr(self.v, "gateway_report", None)
        if _gwr is not None:
            try:
                extra = dict(extra, gateway_requests=_gwr())
            except Exception:                             # noqa: BLE001
                pass
        _ls = lock_state()
        extra = dict(extra, safety_latch=self.b.d.get("safety_latch") or None)
        extra = dict(extra, loop=loop_report())
        if RN1.ON:
            try:
                extra = dict(extra, markout=self.markout_summary(),
                             phases=self.phase_summary(),
                             capital=self.capital_breakdown(),
                             account_limit=self.account_limit_status(),
                             campaign=self.campaign_status(),
                             audit={"last_complete_age_s": self.audit_age_s(),
                                    "max_age_s": AUDIT_MAX_AGE_S,
                                    "stale_blocks_new": self.audit_stale(),
                                    "incomplete_in_a_row": int(getattr(
                                        self, "_audit_incomplete", 0))})
            except Exception:                             # noqa: BLE001
                pass
        extra = dict(extra, submission_lock={
            "new_exposure_locked": _ls["new"], "completions_locked":
            _ls["all"], "why": _ls["why"],
            "window_end": _iso(_ls["end"]) if "end" in _ls else None})
        log("heartbeat", **extra, active=self.active(),
            unmatched_cost_total=self.b.total_unmatched(),
            recovery=dict(self.recovery_metrics(), on=RECOVERY_ON,
                          taker=RECOVERY_TAKER),
            tape=TAPE.stats() if TAPE is not None else None,
            pairs=len(pairs),
            # each PAIR_LOCKED row carries the market's CUMULATIVE pairs and
            # average cost, so only the LATEST row per market counts (summing
            # every row double-counted; owner report 18:28Z corrected).
            matched_pairs=str(sum((D(p["qty"]) for p in
                                   self._latest_pairs().values()), D(0))),
            locked_edge_usd=str(sum(((ONE - D(p["pair_cost"])) * D(p["qty"])
                                     for p in self._latest_pairs().values()),
                                    D(0)).quantize(D("0.0001"))),
            enabled=os.getenv(ENABLED_ENV, ""), depth_ticks=DEPTH_TICKS,
            caps={"per_market": PER_MARKET_CAP, "total": TOTAL_CAP,
                  "markets": MAX_MARKETS, "pair_cap": PAIR_CAP, "qty": QTY},
            rewards_enabled=rewards_on(),
            rewarded_active=sum(1 for s in self.active()
                                if self.b.m(s).get("reward")),
            reward_caps={"qty": REWARD_QTY,
                         "per_market_lone_leg": REWARD_PER_MARKET_CAP},
            reward_first=reward_first_on(),
            reward_label="program rewards THEORETICAL; not in P&L")

    def _hook_loop_beat(self) -> None:
        """Every venue request the loop completes is progress (the hourly
        audit is one long pass of them). Other threads' requests do not
        count: loop_beat() checks the thread."""
        t = getattr(self.v, "t", None)
        if t is None or getattr(t, "_loop_beat_hooked", False) or \
                not hasattr(t, "request"):
            return
        orig = t.request

        def request(*a: Any, **k: Any) -> Any:
            try:
                return orig(*a, **k)
            finally:
                loop_beat()
        try:
            t.request = request
            t._loop_beat_hooked = True
        except Exception:                                 # noqa: BLE001
            pass

    def run(self) -> int:
        _LOOP["thread"] = threading.get_ident()
        loop_beat()
        self._hook_loop_beat()
        signal.signal(signal.SIGTERM, lambda *_: self.on_sigterm())
        self.require_fresh_account = True     # no new exposure on stale state
        self.start_risk_reader()
        self.start_campaign_reader()
        self.mark_recovery_start()
        seed = seed_drift(self.b.d, os.getenv("PMUS_TWO_SIDED_DRIFT_SEED"))
        if seed["seeded"] or seed["error"] or seed["kept_live"]:
            self.b.save()
            log("drift_seed", **seed, label="measured completion drift "
                "preloaded for sports with no live history")
        log("boot", rn1=({"on": True, **RN1.cfg()} if RN1.ON else {"on": False}),
            safeguards=safeguards_cfg(),
            recovery={"on": RECOVERY_ON, "taker": RECOVERY_TAKER, "lean": RECOVERY_LEAN,
                              "shared_policy": completion_policy_live(),
                              "sports_cap": COMPLETION_SPORTS_CAP,
                              "max_stranded": MAX_STRANDED,
                              "market_overpay_budget_usd": MARKET_OVERPAY_BUDGET_USD,
                              "account_risk": {"on": ACCOUNT_RISK_ON,
                                               "observe": ACCOUNT_RISK_OBSERVE,
                                               "url_set": bool((os.getenv(
                                                   "L5_ACCOUNT_RISK_URL")
                                                   or "").strip())},
                              "sharp": {"mode": SHARP_MODE,
                                        "sports": list(SHARP_SPORTS),
                                        "key_set": bool((os.getenv(
                                            "ODDS_API_KEY") or "").strip())},
                              "drift_cap": {"on": DRIFT_CAP_ON,
                                            "edge": DRIFT_EDGE,
                                            "min_n": DRIFT_MIN_N},
                              "unmatched_halt_usd": UNMATCHED_HALT_USD,
                              "since": self.b.d.get("recovery_since"),
                              "shared_halt_url_set": bool((os.getenv(
                                  SHARED_HALT_URL_ENV) or "").strip())},
            daily_loss_usd=DAILY_LOSS_USD,
            controller={"slate": slate(), "kickoff_pull_s": KICKOFF_PULL_S,
                        "kickoff_resume_min_s": KICKOFF_RESUME_MIN_S,
                        "kickoff_max_hold_s": KICKOFF_MAX_HOLD_S,
                        "pregame_qty": rn1_pregame_qty(),
                        "live_qty": rn1_live_qty(),
                        "vol": {"window_s": VOL_WINDOW_S,
                                "pause_usd": VOL_PAUSE_USD,
                                "shrink_usd": VOL_SHRINK_USD,
                                "calm_s": VOL_CALM_S},
                        "at_risk_budget_usd": AT_RISK_BUDGET_USD,
                        "at_risk_usd": self.own_exposure_usd(),
                        "rank_net": RN1_RANK_NET,
                        "net_rank": RN1_NET_RANK and {
                            "sizes": [str(x) for x in RN1_NET_SIZES],
                            "fill_rate": {str(k): v for k, v in
                                          RN1_FILL_RATE.items()},
                            "fill_loss": {str(k): v for k, v in
                                          RN1_FILL_LOSS.items()}},
                        "pin": rn1_pin(),
                        "select": {"band_lo": BAND_LO, "band_hi": BAND_HI,
                                   "max_spread_ticks": MAX_SPREAD_TICKS,
                                   "max_markets": MAX_MARKETS,
                                   "max_tracked": MAX_TRACKED,
                                   "strict_sports": STRICT_SPORTS,
                                   "exclude": os.getenv(
                                       "PMUS_TWO_SIDED_EXCLUDE", "cr-"),
                                   "allow_sports": sorted(
                                       OPEN_GATE_ALLOW_SPORTS)}},
            pregame={"on": PREGAME, "min_lead_s": PREGAME_MIN_LEAD_S,
                     "max_lead_s": PREGAME_MAX_LEAD_S,
                     "cutoff_s": PREGAME_CUTOFF_S}, **({"ws": True} if self.ws is not None else {}), caps={"per_market": PER_MARKET_CAP, "total": TOTAL_CAP,
                          "markets": MAX_MARKETS, "max_tracked": MAX_TRACKED, "open_offset_px": OPEN_OFFSET_PX, "depth_ticks": DEPTH_TICKS,
                          "qty": QTY, "pair_cap": PAIR_CAP},
            exit={"fee_aware": EXIT_FEE_AWARE, "taker_fee_rate": TAKER_FEE_RATE,
                  "max_taker_qty": EXIT_MAX_TAKER_QTY, "exit_s": EXIT_S, "exit_s_cfb": EXIT_S_CFB, "complete_max_pair": COMPLETE_MAX_PAIR},
            reward_first=reward_first_on(),
            **({"reward_first_cfg": {
                "markets": REWARD_MARKETS, "reward_only": REWARD_ONLY, "periods": REWARD_FIRST_PERIODS,
                "reprice_ticks": REWARD_REPRICE_TICKS, "qty": REWARD_QTY,
                "floor_usd": REWARD_FLOOR_USD}} if reward_first_on() else {}))
        last_sel = 0.0
        last_hb = 0.0
        last_acc = time.monotonic()
        while not self.stop:
            loop_beat()
            try:
                if os.getenv(ENABLED_ENV, "").upper() != "YES":
                    self.cancel_all_own("disabled")
                    orders_served()
                    time.sleep(30)
                    continue
                latched = str(self.enforce_lock() or "").startswith(
                    "safety_latch")
                if time.monotonic() - last_sel > 20:
                    if self.full() and LATE_CUTOFF:
                        try:                       # keep game clocks fresh
                            self.v.live_sports()
                        except Exception:          # noqa: BLE001
                            pass
                    acc = self.read_account()
                    try:
                        self.safety_check(acc)
                    except Exception as exc:       # noqa: BLE001
                        log("safety_check_error",
                            err=f"{type(exc).__name__}:{str(exc)[:160]}")
                    latched = latched or bool(self.b.d.get("safety_latch"))
                    if acc is not None and latched:
                        last_sel = time.monotonic()    # no new markets
                    elif acc is not None:
                        self.select(acc)
                        last_sel = time.monotonic()
                    else:                          # retry sooner, not every pass
                        last_sel = time.monotonic() - 20 + ACCOUNT_RETRY_S
                    if self.guard is not None:
                        self.guard.track(self.guard_tracked())
                    if rewards_on() and acc is not None:
                        self.refresh_active_rewards()
                if latched:
                    self.enforce_lock()             # cancel what rests, now
                for slug in ([] if latched else self.active()):
                    if self.stop:
                        break
                    self.drain_ws_events()          # no-op unless WS mode
                    try:
                        self.step_market(slug)
                    except Exception as exc:          # noqa: BLE001
                        log("market_error", slug=slug,
                            err=f"{type(exc).__name__}:{str(exc)[:200]}")
                    loop_beat()
                if not self.stop:
                    self._last_service = time.monotonic()
                    orders_served()
                if REWARD_CHECK_S > 0 and \
                        time.monotonic() - self._last_reward > REWARD_CHECK_S:
                    self._last_reward = time.monotonic()
                    self.audit_round()
                if SHARP_MODE == "shadow" and time.monotonic() - getattr(
                        self, "_last_sharp", -1e9) > SHARP_SHADOW_EVERY_S:
                    self._last_sharp = time.monotonic()
                    try:
                        self.sharp_shadow_tick()
                    except Exception as exc:          # noqa: BLE001
                        log("sharp_shadow_error",
                            err=type(exc).__name__)
                self.reconcile_orders_now()
                if SETTLE_CHECK_S > 0 and time.monotonic() - getattr(
                        self, "_last_settle", -1e9) > SETTLE_CHECK_S:
                    self._last_settle = time.monotonic()
                    try:
                        self.settle_lookups(SETTLE_CHECK_MAX)
                    except Exception as exc:              # noqa: BLE001
                        log("settle_lookup_error",
                            err=f"{type(exc).__name__}:{str(exc)[:160]}")
                if time.monotonic() - max(last_hb, float(getattr(
                        self, "_last_hb", 0.0))) > 60:
                    self.report()
                    last_hb = self._last_hb = time.monotonic()
                if reward_first_on() and \
                        time.monotonic() - last_acc > REWARD_ACCRUAL_LOG_S:
                    last_acc = time.monotonic()
                    self.reward_accrual_report()
                    self.b.save()
                self._loop_errors = 0
            except Exception as exc:                  # noqa: BLE001
                log("loop_error", err=f"{type(exc).__name__}:{str(exc)[:200]}")
                self._loop_errors = getattr(self, "_loop_errors", 0) + 1
                if self._loop_errors >= LOOP_ERROR_LATCH_N:
                    try:
                        self.latch("loop_errors", consecutive=self._loop_errors,
                                   last=f"{type(exc).__name__}")
                        self.enforce_lock()
                    except Exception:                 # noqa: BLE001
                        pass
            try:
                self.drain_ws_events()
            except Exception as exc:                  # noqa: BLE001
                log("loop_error", err=f"{type(exc).__name__}:{str(exc)[:200]}")
            loop_beat()
            death_drill_exit_if_due()
            self._sleep(LOOP_S)
            self._stall_drill_hold()
        self.cancel_all_own("sigterm")
        return 0

    def _stall_drill_hold(self) -> None:
        """Hold the loop still for a configured drill window (see
        STALL_DRILL_ENV). No beat, no read, no order, until the window ends
        or the process is told to stop."""
        d = stall_drill()
        if not d or not d["active"]:
            return
        log("STALL_DRILL", phase="begin", end=_iso(d["end"]),
            open_orders=sum(1 for mk in self.b.d["markets"].values()
                            for o in mk["orders"].values() if o.get("open")),
            label="deliberate drill: the loop stops acting and reporting "
                  "progress until the window ends")
        while not self.stop:
            d = stall_drill()
            if not d or not d["active"]:
                break
            time.sleep(1.0)
        log("STALL_DRILL", phase="end", idle_s=loop_idle_s())


_LAST_ACCESS_LOG = [0.0]
REGISTRY_KEY = "l5/live_orders.json"      # same path as l5_order_registry_server
LANE = "pmus-sports-two-sided"


# an order listed as live stays listed this long after it closes (see below)
REGISTRY_GRACE_S = float(os.getenv("PMUS_TWO_SIDED_REGISTRY_GRACE_S", "60"))
_REGISTRY_SEEN: Dict[str, Any] = {}


def registry_doc(book: "Book", clock=None) -> Dict[str, Any]:
    """The orders this bot placed and still believes live, in the shape the
    l5 watchdog's ownership oracle reads, plus its own freshness stamp. A
    fresh document is the bot attesting that it is ALIVE AND MANAGING these
    resting orders (maker bids that legitimately rest for minutes)."""
    rows = []
    now = time.time() if clock is None else clock()
    live = set()
    for slug, mk in (book.d.get("markets") or {}).items():
        for oid, o in (mk.get("orders") or {}).items():
            if o.get("open"):
                row = {"venue_order_id": oid, "market": slug,
                       "side": o.get("side"), "session_id": LANE,
                       "price": o.get("px"), "qty": o.get("qty")}
                rows.append(row)
                live.add(oid)
                _REGISTRY_SEEN[oid] = (dict(row), now)
    # LIVE 2026-11-17 23:40:11Z: the watchdog read the venue's open orders at
    # 23:40:05 and this document ~6 s later. In between the bot cancelled two
    # 426 s-old ETH quotes (a routine reprice) and dropped them from this
    # list, so the watchdog saw two "unmanaged" resting orders and latched
    # the account-wide halt -- its own cancel then found them already
    # CANCELED. An order this bot listed as live stays listed, marked
    # "closing", for REGISTRY_GRACE_S after it stops being open, so a sweep
    # that straddles the cancel still sees it as managed. Bounded and
    # forgotten after the grace; never adds an id the bot did not place.
    for oid, (row, seen) in list(_REGISTRY_SEEN.items()):
        if oid in live:
            continue
        if now - seen > REGISTRY_GRACE_S:
            _REGISTRY_SEEN.pop(oid, None)
            continue
        rows.append({**row, "closing": True})
    _idle = loop_idle_s(now)
    _stalled = (_idle is not None and _idle > LOOP_STALL_S) or \
        order_service_stalled(now)
    return {"readable": True, "lane": LANE,
            "manages_own_resting": not _stalled,
            "enabled": os.getenv(ENABLED_ENV, "").upper() == "YES",
            # True ONLY under the lane's own independent lock (above); the
            # shared halt is deliberately NOT counted, since a reset that
            # proved "disabled" via the very latch it clears proves nothing
            "submission_disabled": fully_locked() or bool(
                book.d.get("safety_latch")),
            "safety_latch": book.d.get("safety_latch") or None,
            "loop_idle_s": (None if _idle is None else round(_idle, 1)),
            "loop_stalled": _stalled,
            "order_service_idle_s": (
                None if order_service_idle_s(now) is None
                else round(order_service_idle_s(now), 1)),
            "orders": rows,
            "loop_last_progress_utc": (None if _LOOP["beat"] is None
                                       else _iso_precise(_LOOP["beat"])),
            "as_of_utc": now_iso(),
            "labels": ("venue order ids pmus_sports_two_sided placed and "
                       "still believes live; it cancels them itself on "
                       "requote, disable and SIGTERM")}


# ------------------------------------------------- the ownership (fills) feed
# OWNER 2026-11-22: "The BTC service must not adopt or settle positions
# belonging to the sports controller. Every fill and position needs durable
# ownership attribution." The account-wide ledger (l5-pmus-live) holds a
# position it did not open at $1 a contract and books its settlement at that
# bound; on 2026-11-22 that read -13.00 for a day whose cash moved -2.05.
# It already restates another strategy's market when that strategy's own
# order ledger and the venue's fills agree ORDER BY ORDER. This is the
# sports ledger in the shape it reads (the BTC5m feed's shape): every order
# of this bot that ever filled, by venue order id, from the durable book.
# READ-ONLY. No credential, no account id, nothing but this bot's own orders.
FILLS_KEY = "l5/sports_fills.json"
FILLS_STRATEGY = "SPORTS"


def fills_doc(book: "Book") -> Dict[str, Any]:
    """{"readable", "strategy", "as_of_utc", "orders": [...]}. An order is
    listed only once it has filled; `filled_cost` is the venue-reported cost
    where this bot recorded it and quantity x limit price for an order that
    filled before it did (`cost_from` says which). The reader checks every
    figure against the venue itself and refuses on any difference, so a
    wrong figure here can only leave its own bound in place."""
    for _attempt in range(3):
        try:
            orders = []
            for slug, mk in sorted((book.d.get("markets") or {}).items()):
                for oid, o in sorted((mk.get("orders") or {}).items()):
                    q = D(str(o.get("filled") or "0"))
                    if q <= 0:
                        continue
                    px = D(str(o["px"]))
                    side = str(o.get("side") or "")
                    if side not in ("YES", "NO") or not str(oid).strip():
                        return {"readable": False, "strategy": FILLS_STRATEGY,
                                "orders": [], "why": "order_unreadable"}
                    rec = o.get("fill_cost")
                    cost = D(str(rec)) if rec is not None else q * px
                    orders.append({
                        "venue_order_id": str(oid), "market": slug,
                        "side": side, "price": str(cost / q),
                        "limit_price": str(px), "filled_qty": str(q),
                        "filled_cost": str(cost),
                        "cost_from": ("VENUE_REPORTED" if rec is not None
                                      else "LIMIT_PRICE"),
                        "state": "OPEN" if o.get("open") else "CLOSED"})
            return {"readable": True, "strategy": FILLS_STRATEGY,
                    "lane": LANE, "as_of_utc": now_iso(), "orders": orders,
                    "labels": ("every order this bot placed that has filled, "
                               "with its own outcome side (YES/NO), filled "
                               "quantity and cost in that outcome's price. "
                               "Not a P&L and not a position: the reader "
                               "reconciles it against the venue's fills.")}
        except RuntimeError:               # book changed size while iterating
            time.sleep(0.01)
        except Exception:                                     # noqa: BLE001
            break
    return {"readable": False, "strategy": FILLS_STRATEGY, "orders": [],
            "why": "book_unreadable"}


def serve_registry(book: "Book", port: int) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):                               # noqa: N802
            path = self.path.split("?", 1)[0].lstrip("/")
            if path not in (REGISTRY_KEY, FILLS_KEY, "health"):
                self.send_response(404); self.end_headers(); return
            body = json.dumps(registry_doc(book) if path == REGISTRY_KEY
                              else fills_doc(book) if path == FILLS_KEY
                              else {"ok": True}, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *a):
            # one line per registry read, so a cross-service fetch (the l5
            # watchdog) is visible here; health checks stay quiet
            if REGISTRY_KEY in (self.path or ""):
                now = time.monotonic()
                if now - _LAST_ACCESS_LOG[0] > 60:
                    _LAST_ACCESS_LOG[0] = now
                    log("registry_read", client=self.client_address[0])

    try:                       # Railway private networking is IPv6
        import socket as _sock

        class V6(ThreadingHTTPServer):
            address_family = _sock.AF_INET6
        srv = V6(("::", port), H)
    except OSError:
        srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("registry_serving", port=port, path=REGISTRY_KEY,
        family=("ipv6_dualstack" if srv.address_family != 2 else "ipv4"))
    # SELF-TEST through the private DNS name other services use
    host = os.getenv("RAILWAY_PRIVATE_DOMAIN", "")
    if host:
        def _probe():
            import urllib.request
            time.sleep(2)
            url = f"http://{host}:{port}/{REGISTRY_KEY}"
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    log("registry_selftest", ok=True, url=url, status=r.status)
            except Exception as exc:                     # noqa: BLE001
                log("registry_selftest", ok=False, url=url,
                    err=f"{type(exc).__name__}:{str(exc)[:120]}")
        threading.Thread(target=_probe, daemon=True).start()


def main() -> int:
    death_drill_boot_hold()      # a drill window: stay down until it ends
    book = Book(STATE_DIR / "state.json")
    try:
        serve_registry(book, int(os.getenv("PMUS_TWO_SIDED_REGISTRY_PORT",
                                           "8090")))
    except Exception as exc:                             # noqa: BLE001
        log("registry_error", err=f"{type(exc).__name__}:{str(exc)[:160]}")
    venue = Venue()
    global TAPE
    if os.getenv("PMUS_TWO_SIDED_TAPE", "1").strip() != "0":
        try:
            from src.pmus_sports_tape import Tape
            TAPE = Tape(STATE_DIR / "tape", max_bytes_per_day=int(os.getenv(
                "PMUS_TWO_SIDED_TAPE_MAX_BYTES", "40000000")),
                # owner 2026-11-21: keep the 14-18 Nov book tapes for the RN1
                # replica replay; 0 = never prune. Default unchanged (7).
                keep_days=int(os.getenv("PMUS_TWO_SIDED_TAPE_KEEP_DAYS",
                                        "7")))
            log("tape_enabled", dir=str(STATE_DIR / "tape"))
        except Exception as exc:                         # noqa: BLE001
            log("tape_error", err=f"{type(exc).__name__}:{str(exc)[:120]}")
    if ws_on():
        try:
            from src.pmus_two_sided_ws import TwoSidedWs
            venue.ws = TwoSidedWs(log=log)
            if TAPE is not None:
                venue.ws.tape_fn = TAPE.book
            venue.ws.set_slugs([s for s, mk in book.d["markets"].items()
                                if not mk.get("done")])
            venue.ws.start()
            log("ws_enabled", jump_ticks=JUMP_TICKS, jump_pause_s=JUMP_PAUSE_S,
                fresh_s=venue.ws.fresh_s)
        except Exception as exc:                         # noqa: BLE001
            venue.ws = None
            log("ws_error", err=f"{type(exc).__name__}:{str(exc)[:160]}",
                fallback="gateway_poll_only")
    start_xv_paper(venue)
    start_intl_guard(venue)
    start_combo_study(venue)
    start_completion_replay(venue)
    eng = Engine(venue, book)
    start_combo_maker(venue, eng)
    return eng.run()


XV_PAPER_ENV = "PMUS_TWO_SIDED_XV_PAPER"
COMBO_STUDY_ENV = "PMUS_TWO_SIDED_COMBO_STUDY"
COMPLETION_REPLAY_ENV = "PMUS_TWO_SIDED_COMPLETION_REPLAY"


def _public_settlement(venue: "Venue", slug: str) -> Optional[D]:
    """YES settlement price (0/1) from the KEYLESS gateway, or None."""
    try:
        md = (venue.gget(f"/v1/markets/{slug}/bbo") or {}).get(
            "marketData") or {}
    except Exception:                                    # noqa: BLE001
        return None
    if md.get("state") not in ("MARKET_STATE_EXPIRED",
                               "MARKET_STATE_RESOLVED",
                               "MARKET_STATE_SETTLED"):
        return None
    v = md.get("settlementPx")
    v = v.get("value") if isinstance(v, Mapping) else v
    try:
        px = D(str(v))
    except Exception:                                    # noqa: BLE001
        return None
    return px if px in (D(0), D(1)) else None


def start_completion_replay(venue: "Venue") -> Optional[Any]:
    """READ-ONLY replay of the lane's own stranded legs (owner 2026-11-18):
    production recovery vs the shared policy at sports caps 0.97 / 0.99.
    Default OFF. Reads the tape files on this volume and keyless public
    settlement prices; logs COMPLETION_REPLAY lines; places, cancels and
    authorizes nothing. Refuses while the live shared policy is on."""
    if os.getenv(COMPLETION_REPLAY_ENV, "0").strip() != "1":
        return None
    if completion_policy_live():
        log("COMPLETION_REPLAY", ok=False, why="live_policy_on_refused")
        return None
    import threading

    def run() -> None:
        try:
            from src import pmus_sports_completion_replay as RP
            files = sorted((STATE_DIR / "tape").glob("tape_*.jsonl"))
            lines = []
            for f in files:
                with open(f, encoding="utf-8") as fh:
                    lines.extend(fh)
            books, events = RP.load(lines)
            st = RP.strands(events)
            slugs = sorted({s["slug"] for s in st})
            settle = {sl: _public_settlement(venue, sl) for sl in slugs}
            res = RP.run(books, events, settle)
            log("COMPLETION_REPLAY", ok=True, tape_files=[f.name for f in files],
                tape_lines=len(lines), book_markets=len(books),
                strands=len(st), markets=len(slugs),
                settled_markets=sum(1 for v in settle.values()
                                    if v is not None), **res)
            rows = []
            for s in st:
                rows.append({"slug": s["slug"], "lead": s["lead"],
                             "lead_px": str(s["lead_px"]),
                             "need": str(s["need"]),
                             "prod_outcome": s["outcome"],
                             "prod_pair_cost": str(s["pair_cost"]),
                             "late_seen": s["late_t"] is not None,
                             "yes_settle": str(settle.get(s["slug"])),
                             "frames": len(books.get(s["slug"]) or [])})
            for i in range(0, len(rows), 25):
                log("COMPLETION_REPLAY_ROWS", part=i // 25, rows=rows[i:i + 25],
                    label="RECORDED strand inputs for the replay above")
        except Exception as exc:                          # noqa: BLE001
            log("COMPLETION_REPLAY", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:200]}")

    th = threading.Thread(target=run, daemon=True, name="completion-replay")
    th.start()
    log("completion_replay_started", label="READ-ONLY; no order path")
    return th


def combo_maker_gates(eng: "Engine") -> Optional[str]:
    """Why a LIVE combo-maker write is not allowed now, else None. The same
    lane locks the sports opener obeys: its own submission lock, the shared
    halt (any state but PERMITTED blocks), and -- when enforced -- the
    account-wide risk verdict. Errors block."""
    if submission_locked():
        return "submission_lock"
    if eng.shared_halt() is not None:
        return "shared_halt"
    why = eng.account_risk_blocks()
    return "account_risk:%s" % why if why else None


def start_combo_maker(venue: "Venue", eng: "Engine") -> Optional[Any]:
    """Combo RFQ maker (src/pmus_combo_maker.py). PMUS_COMBO_MAKER=shadow
    logs would-quotes; =live quotes and confirms in last look under hard
    caps and combo_maker_gates; default OFF. Own thread and own state file
    (STATE_DIR/combo_maker.json) so its caps survive restarts."""
    from src import pmus_combo_maker as CM
    if CM.MODE not in ("shadow", "live"):
        return None
    import threading
    path = STATE_DIR / "combo_maker.json"
    try:
        state = json.loads(path.read_text()) if path.exists() else {}
    except Exception:                                    # noqa: BLE001
        state = {}

    def save() -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, default=str))
        tmp.replace(path)

    def get(path_: str) -> Any:
        return venue.q1.base._get_with_retry(venue.t, path_)

    def books(syms: List[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for sy in syms:
            try:
                out[sy] = venue.bbo(sy)
            except Exception:                            # noqa: BLE001
                out[sy] = None
            if CM.INDEPENDENT in ("prefer", "require") and out[sy]:
                # independent leg fair (PMUS_COMBO_INDEPENDENT): the fresh
                # sportsbook / line shadow fair only, never the venue mid
                try:
                    st = ((eng.b.d.get("sharp_shadow") or {}).get(sy) or {})
                    age = time.time() - float(st.get("t") or 0) + \
                        float(st.get("odds_age_s") or 0.0)
                    if st.get("fair_yes") is not None and \
                            age <= RN1_DIR_SHARP_MAX_AGE_S:
                        out[sy] = dict(out[sy], fair_yes=st["fair_yes"])
                except Exception:                        # noqa: BLE001
                    pass
        return out

    mk = CM.ComboMaker(
        get=get, post=lambda p, b: venue.t.request("POST", p, b),
        put=lambda p, b: venue.t.request("PUT", p, b),
        delete=lambda p: venue.t.request("DELETE", p),
        books=books, log=log, gates=lambda: combo_maker_gates(eng),
        state=state)

    def run() -> None:
        last_status = 0.0
        while not getattr(eng, "stop", False):
            try:
                st = mk.pass_once()
                if st["confirmed"] or st["quoted"] or st["declined"]:
                    save()
                if time.time() - last_status > 60:
                    last_status = time.time()
                    log("COMBO_MAKER_STATUS", **mk.status(), last_pass=st)
                    save()
            except Exception as exc:                     # noqa: BLE001
                log("combo_maker_error", err=type(exc).__name__)
            time.sleep(CM.poll_interval())

    th = threading.Thread(target=run, daemon=True, name="combo-maker")
    th.start()
    log("combo_maker_enabled", **mk.status())
    return mk


def start_combo_study(venue: "Venue") -> Optional[Any]:
    """READ-ONLY combo (parlay) study (src/pmus_sports_combo_study.py),
    default OFF. Given a signed-GET function and the public gateway GET --
    never the venue -- so it can only read."""
    if os.getenv(COMBO_STUDY_ENV, "0").strip() != "1":
        return None
    try:
        from src import pmus_sports_combo_study as CS

        def signed_get(path: str) -> Any:
            return venue.q1.base._get_with_retry(venue.t, path)
        cs = CS.ComboStudy(signed_get, venue.gget, log,
                           state_dir=STATE_DIR / "combo_study")
        cs.start()
        log("combo_study_enabled", label="READ-ONLY: nothing quoted")
        return cs
    except Exception as exc:                             # noqa: BLE001
        log("combo_study_error", err=f"{type(exc).__name__}:{str(exc)[:160]}")
        return None
INTL_GUARD_ENV = "PMUS_TWO_SIDED_INTL_GUARD"


def start_intl_guard(venue: "Venue") -> Optional[Any]:
    """Intl fair-value guard on OPENING quotes, default OFF. Read functions
    only; the engine owns every cancel. A guard that fails to start leaves
    openings BLOCKED in require mode (venue.guard set, never fed) rather
    than silently trading unguarded."""
    if os.getenv(INTL_GUARD_ENV, "0").strip() != "1":
        return None
    from src import pmus_sports_xv as XV
    g = XV.IntlGuard(
        venue.gget, XV.gamma_get,
        margin_ticks=int(os.getenv("PMUS_TWO_SIDED_INTL_GUARD_MARGIN_TICKS",
                                   "1")),
        max_age_s=float(os.getenv("PMUS_TWO_SIDED_INTL_GUARD_MAX_AGE_S",
                                  "20")),
        require=os.getenv("PMUS_TWO_SIDED_INTL_GUARD_REQUIRE",
                          "1").strip() != "0",
        log=log)
    venue.guard = g
    try:
        g.start()
        log("intl_guard_enabled", margin_ticks=g.margin_ticks,
            max_age_s=g.max_age, require=g.require)
    except Exception as exc:                             # noqa: BLE001
        log("intl_guard_error", err=f"{type(exc).__name__}:{str(exc)[:160]}",
            effect="openings blocked (require mode)" if g.require
            else "unguarded")
    return g


def start_xv_paper(venue: "Venue") -> Optional[Any]:
    """PAPER-ONLY cross-venue signal (src/pmus_sports_xv.py), default OFF.
    Given READ functions only -- discovery and public gateway GETs -- never
    the venue object, so it cannot place or cancel an order."""
    if os.getenv(XV_PAPER_ENV, "0").strip() != "1":
        return None
    try:
        from src import pmus_sports_xv as XV

        def targets() -> list:
            out = list(venue.live_sports())
            try:
                out += list(venue.pregame_sports())
            except Exception:                           # noqa: BLE001
                pass
            return out
        kalshi = None
        if os.getenv("PMUS_TWO_SIDED_XV_KALSHI", "1").strip() != "0":
            # Kalshi FREE public market data: a second fair-value source,
            # read-only GETs, no key, no account (src/pmus_sports_kalshi.py)
            from src import pmus_sports_kalshi as KS
            kalshi = KS.KalshiFair()
        xv = XV.XvPaper(targets, venue.gget, XV.gamma_get, log,
                        max_markets=int(os.getenv(
                            "PMUS_TWO_SIDED_XV_MARKETS", "20")),
                        tape_event=(TAPE.event if TAPE is not None
                                    else None),
                        kalshi=kalshi)
        xv.start()
        log("xv_paper_enabled", kalshi=kalshi is not None,
            label="PAPER ONLY: no orders")
        return xv
    except Exception as exc:                             # noqa: BLE001
        log("xv_paper_error", err=f"{type(exc).__name__}:{str(exc)[:160]}")
        return None


if __name__ == "__main__":
    raise SystemExit(main())
