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
import os
import queue
import signal
import threading
import time
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal as D
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

ONE = D("1")

from src import pmus_rn1_replica as RN1  # noqa: E402  (default OFF)
# daily-crypto reward lane (default OFF and disarmed): its own decisions,
# limits and accounting; a crypto market never reaches RN1 or the sports path
from src import pmus_crypto_rewards as CR  # noqa: E402


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
#: RISK-REDUCING stages: they buy the missing side of a held leg (or exit
#: it) and are never refused by the account-wide limits. Everything else --
#: two_sided_open, two_sided_open_rewarded, two_sided_extend -- adds risk.
COMPLETION_STAGES = ("two_sided_complete", "two_sided_complete_taker",
                     "two_sided_exit", CR.STAGE_COMPLETE)


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


SUBMISSION_LOCK_ENV = "PMUS_TWO_SIDED_SUBMISSION_LOCK"


def submission_locked() -> bool:
    return (os.getenv(SUBMISSION_LOCK_ENV) or "").strip() == "1"


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
OPEN_GATE_ALLOW_SPORTS = frozenset(
    x.strip().lower() for x in os.getenv(
        "PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS", "").split(",") if x.strip())
SHARED_HALT_TTL_S = float(os.getenv("PMUS_TWO_SIDED_SHARED_HALT_TTL_S", "15"))
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
                   "t20i", "ilt20", "hundred", "cricket")


def is_cricket(slug: str) -> bool:
    parts = str(slug).split("-")
    league = parts[1] if len(parts) > 1 else ""
    return league.endswith("cr") or league in CRICKET_LEAGUES or \
        "cricket" in str(slug)


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
        if m is not None and m >= 70:
            return "2H_70+"
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
                    self.d["markets"].items() if not mk.get("done")
                    and not CR.is_crypto_market(s, mk)), D(0))

    def total_unmatched_all(self) -> D:
        """Every lane's open lone-leg cost (account-level exposure): the
        sports figure above plus the crypto lane's. Sports caps read
        total_unmatched() so neither lane can spend the other's room."""
        return sum((self.unmatched_cost(s) for s, mk in
                    self.d["markets"].items() if not mk.get("done")), D(0))

    def resting_pair_exposure(self, slug: str) -> D:
        """Worst NEW lone-leg cost the market's resting orders can create:
        when balanced, one open leg filling alone (the dearer remainder).
        When unbalanced the open order is the completion, which only
        reduces unmatched cost -> 0."""
        mk = self.d["markets"].get(slug)
        if not mk or mk.get("done") or CR.is_crypto_market(slug, mk):
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
        return self.gw.http_get_json(path)

    states: Dict[str, Any] = {}

    def crypto_targets(self) -> list:
        """Daily ETH/SOL/XRP up/down markets for the crypto reward lane
        (src/pmus_crypto_rewards.py); [] unless that lane is on. Never BTC.
        These targets go ONLY to Engine.select_crypto -- they are no longer
        mixed into the sports selection, where RN1 mode would have traded
        them as accumulation (audit 2026-11-22, finding 6). The older
        PMUS_TWO_SIDED_CRYPTO_PREFIXES is no longer read for selection."""
        if not CR.ON:
            return []
        d = self.gget("/v1/events?active=true&closed=false&limit=200"
                      "&categories=crypto") or {}
        evs = d if isinstance(d, list) else d.get("events") or []
        out = []
        for ev in evs:
            for m in (ev.get("markets") or []) if isinstance(ev, Mapping) else []:
                slug = str((m or {}).get("slug") or "")
                if not CR.tradable(slug):
                    continue
                end = m.get("endDate") or ev.get("endDate")
                self.states[slug] = {"type": "crypto-1d", "endDate": end}
                out.append({"slug": slug, "league": "crypto", "end": end,
                            "why": "crypto_reward_lane",
                            "event_state": self.states[slug]})
        return out

    def live_sports(self) -> list:
        if CRYPTO_PREFIXES and not getattr(self, "_legacy_crypto_logged",
                                           False):
            self._legacy_crypto_logged = True
            log("legacy_crypto_prefixes_ignored", prefixes=CRYPTO_PREFIXES,
                label="crypto is quoted only by the crypto reward lane "
                      "(PMUS_CRYPTO_REWARDS); it is never selected into "
                      "the sports / RN1 path")
        return self._live_sports()

    def _live_sports(self) -> list:
        events, _ = self.gw.fetch_live_events(self.gget)
        st: Dict[str, Any] = {}
        for ev in events or []:
            es = ev.get("eventState") if isinstance(ev, Mapping) else None
            for m in (ev.get("markets") or []) if isinstance(ev, Mapping) else []:
                if isinstance(m, Mapping) and m.get("slug"):
                    st[str(m["slug"])] = es or {}
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
        oo = self.t.request("GET", "/v1/orders/open")
        orders = [o for o in (oo.get("orders") or []) if isinstance(o, Mapping)]
        return {"positions": held, "open_orders": orders}

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
                "open": is_open, "state": state}

    def place(self, slug: str, side: str, px: D, qty: D, stage: str,
              post_only: bool = True) -> str:
        return self.micro.place_limit_buy(self.t, slug, side, px, qty, stage,
                                          post_only=post_only)

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


# ------------------------------------------------------------- engine

class Engine:
    def __init__(self, venue: Venue, book: Book):
        self.v, self.b = venue, book
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
        if not mk or mk.get("done") or CR.is_crypto_market(slug, mk):
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
        if CR.is_crypto_market(slug, mk):
            return          # the crypto lane re-prices in its own step
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
        legs = {o["side"]: D(o["px"]) for o in mk["orders"].values()
                if o.get("open")}
        why = adverse_reasons(legs, {"bid": D(str(new["bid"])),
                                     "ask": D(str(new["ask"]))},
                              tick, depth_ticks(tick))
        if why:
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
            per = R.paying_period(prog, time.time(), event_live=True)
            if per:
                out[s] = per
        return out

    def refresh_active_rewards(self) -> None:
        """Keep each active market's paying period current (a period that
        stops paying drops the market back to normal quoting)."""
        act = self.sports_active()
        if not act:
            return
        try:
            pp = self.reward_programs(act)
        except Exception as exc:                      # noqa: BLE001
            log("reward_discovery_error", err=str(exc)[:160])
            return
        for s in act:
            mk = self.b.m(s)
            if CR.is_crypto_market(s, mk):
                continue             # the crypto lane keeps its own programme
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
                             own_no=float(n[1]) if n else 0.0)
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
        for s_, mk in self.b.d["markets"].items():
            if CR.is_crypto_market(s_, mk):
                continue             # crypto has its own latched loss stop
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
        late = is_late((getattr(self.v, "states", {}) or {}).get(slug)) \
            if LATE_CUTOFF else None
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

    def sports_active(self) -> list:
        """Active markets of the sports lane (slots, caps, guard, rewards);
        crypto-lane markets are stepped but hold no sports slot."""
        return [s for s in self.active()
                if not CR.is_crypto_market(s, self.b.d["markets"][s])]

    def slots_used(self) -> int:
        """Markets holding an OPENING slot. LIVE 2026-11-15: four lone-leg
        markets waiting to complete (one for 6h) plus cap-blocked rewarded
        markets filled all MAX_MARKETS slots, so no new market (CFB) could
        be selected. A market with an unmatched leg keeps completing but no
        longer holds a slot; its exposure is still bounded by TOTAL_CAP and
        the whole book by MAX_TRACKED."""
        return sum(1 for s in self.sports_active()
                   if self.b.unmatched_cost(s) == 0)

    def full(self) -> bool:
        return self.slots_used() >= MAX_MARKETS or \
            len(self.sports_active()) >= MAX_TRACKED

    def _record_fill(self, slug: str, side: str, oid: str, st: Dict) -> None:
        mk = self.b.m(slug)
        o = mk["orders"][oid]
        new = st["qty"] - D(o.get("filled", "0"))
        if new > 0 and st["px"] is not None:
            mk["pos"][side] = str(D(mk["pos"][side]) + new)
            mk["cost"][side] = str(D(mk["cost"][side]) + new * st["px"])
            o["filled"] = str(st["qty"])
            mk.setdefault("last_fill_t", {})[side] = time.time()
            if CR.is_crypto_market(slug, mk):
                CR.record_fill(mk, side, st["px"], new, mk.get("crypto_mid"),
                               time.time())
            log("FILL", slug=slug, side=side, px=st["px"], qty=new,
                order_id=oid, pos=mk["pos"],
                label="venue-reported fill; not realized until settlement")

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
        return acc

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

    def cancel_all_own(self, why: str) -> None:
        """Cancel every own open order by exact id. Each order gets up to
        CANCEL_ATTEMPTS tries with backoff (429s at shutdown left orders
        resting until the watchdog's age limit), inside CANCEL_BUDGET_S.
        No bulk per-market cancel: it would also cancel the owner's manual
        orders in that market."""
        with self._cancel_all_lock:
            self._cancel_all_own(why)

    def _cancel_all_own(self, why: str) -> None:
        t0 = time.monotonic()
        left = []
        for slug, mk in list(self.b.d["markets"].items()):
            for oid, o in list(mk["orders"].items()):
                if not o.get("open"):
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
            st = HG.HaltGate(HG.HttpHaltStore(url)).state()
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
                    log("SHARP_SHADOW", slug=slug, sport=code,
                        start=m.get("gameStartTime"),
                        skipped="no_fair" if fy is None else "no_book")
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
                               "q": {"YES": None if q["YES"] is None
                                     else str(q["YES"]),
                                     "NO": None if q["NO"] is None
                                     else str(q["NO"])}}
        for slug in [k for k, v in state.items()
                     if now - float(v.get("t") or 0) > 6 * 3600]:
            state.pop(slug, None)
        st = dict(self._odds.status(), candidates=len(cands), rows=rows,
                  modelled_fills=fills)
        log("SHARP_STATUS", **st)
        return st

    # ------------------------------------------- account-wide risk (shared)
    def own_exposure_usd(self) -> D:
        """This lane's at-risk dollars: unpaired filled cost plus the
        notional of every resting order it believes live."""
        out = D(self.b.total_unmatched_all())
        for mk in (self.b.d.get("markets") or {}).values():
            for o in (mk.get("orders") or {}).values():
                if o.get("open"):
                    try:
                        left = D(str(o.get("qty"))) - D(str(o.get("filled")
                                                            or "0"))
                        out += max(D(0), left) * D(str(o.get("px")))
                    except Exception:                    # noqa: BLE001
                        return D("Infinity")             # unknown: fail closed
        return out

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

    def account_risk(self) -> Dict[str, Any]:
        """Cached shared-limit verdict; {"ok": True, "off": True} when the
        switch is off. Logs every change of verdict."""
        if not (ACCOUNT_RISK_ON or ACCOUNT_RISK_OBSERVE):
            return {"ok": True, "off": True}
        now = time.monotonic()
        hit = getattr(self, "_acct_risk", None)
        if hit and now - hit[0] < ACCOUNT_RISK_TTL_S:
            return hit[1]
        from src import l5_account_risk as AR
        url = (os.getenv(AR.RISK_URL_ENV) or "").strip()
        doc_box: Dict[str, Any] = {}

        def fetch(u: str) -> Any:
            doc_box["doc"] = AR.fetch_json(u)
            return doc_box["doc"]

        try:
            first = AR.account_risk(url=url, now=datetime.now(timezone.utc),
                                    own_unaccounted=0, fetch=fetch)
            if "doc" in doc_box and first.get("reason") not in (
                    "ACCOUNT_RISK_URL_NOT_SET",):
                own = self.own_unaccounted_losses(
                    AR.retired_markets(doc_box["doc"]))
                v = AR.evaluate(doc_box["doc"],
                                now=datetime.now(timezone.utc),
                                own_unaccounted=own)
            else:
                v = first
            lane = AR.lane_cap()
            expo = self.own_exposure_usd()
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
                "lane_cap_usd", "own_exposure_usd", "age_s")},
                label="shared account-wide limits; refusals block NEW "
                      "openings/extensions only, completions continue")
        self._acct_risk = (now, v)
        return v

    def account_risk_blocks(self, px: Optional[D] = None,
                            qty: Optional[D] = None) -> Optional[str]:
        """Reason a NEW opening/extension is refused, or None."""
        v = self.account_risk()
        if v.get("off") or not ACCOUNT_RISK_ON:          # observe: log only
            return None
        if not v.get("ok"):
            return str(v.get("reason"))
        if px is not None and qty is not None:
            try:
                lane = D(str(v.get("lane_cap_usd")))
                if self.own_exposure_usd() + D(str(px)) * D(str(qty)) > lane:
                    return "ACCOUNT_EXPOSURE_CAP"
            except Exception:                            # noqa: BLE001
                return "ACCOUNT_EXPOSURE_UNREADABLE"
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

    def place(self, slug: str, side: str, px: D, qty: D, stage: str,
              post_only: bool = True) -> None:
        if self.stop:
            log("place_refused_shutdown", slug=slug, side=side, px=px,
                qty=qty, stage=stage)
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
        if CR.is_crypto_market(slug, self.b.d["markets"].get(slug)) or \
                stage in CR.STAGES:
            why = self.crypto_place_block(slug, side, px, qty, stage,
                                          post_only)
            if why:
                log("place_refused_crypto", slug=slug, side=side, px=px,
                    qty=qty, stage=stage, post_only=post_only, reason=why)
                return
        if not is_completion_stage(stage):
            why = self.account_risk_blocks(px, qty)
            if why:
                log("place_refused_account_risk", slug=slug, side=side,
                    px=px, qty=qty, stage=stage, reason=why)
                return
            why = self.combined_block(px, qty)
            if why:
                log("place_refused_combined_exposure", slug=slug, side=side,
                    px=px, qty=qty, stage=stage, reason=why,
                    **{k: str(v) for k, v in
                       self.combined_exposure().items()})
                return
        if submission_locked():
            log("place_refused_submission_lock", slug=slug, side=side, px=px,
                qty=qty, stage=stage)
            return
        if self.shared_halt() == "HALT":
            log("place_refused_shared_halt", slug=slug, side=side, px=px,
                qty=qty, stage=stage)
            return
        oid = (self.v.place(slug, side, px, qty, stage) if post_only else
               self.v.place(slug, side, px, qty, stage, post_only=False))
        mk = self.b.m(slug)
        mk["orders"][oid] = {"side": side, "px": str(px), "qty": str(qty),
                             "filled": "0", "open": True, "stage": stage,
                             "placed_at": time.time()}
        self.b.d["own_order_ids"].append(oid)
        if stage == CR.STAGE_OPEN:
            mk["lane"] = CR.LANE
            days = self._crypto_state()["days"]
            if CR.day_key(slug) not in days:
                days.append(CR.day_key(slug))
        self.b.save()
        log("ORDER", slug=slug, side=side, px=px, qty=qty, stage=stage,
            order_id=oid)

    # ------------------------------------------------ crypto reward lane
    # Decisions, limits and accounting: src/pmus_crypto_rewards.py (pure).
    # Everything below is the engine's side of that lane. DEFAULT OFF.
    def _crypto_state(self) -> Dict[str, Any]:
        st = self.b.d.setdefault("crypto", {})
        st.setdefault("loss_stop", None)
        st.setdefault("days", [])
        return st

    def combined_exposure(self) -> Dict[str, D]:
        """At-risk dollars of BOTH lanes in this account book: sports =
        unpaired filled cost + resting bids; crypto = deployed capital of
        every unsettled crypto market + resting bids."""
        markets = self.b.d.get("markets") or {}
        sports = D(self.b.total_unmatched())
        for s_, mk in markets.items():
            if CR.is_crypto_market(s_, mk):
                continue
            for o in (mk.get("orders") or {}).values():
                if o.get("open"):
                    sports += max(D(0), D(str(o["qty"])) - D(str(
                        o.get("filled") or "0"))) * D(str(o["px"]))
        crypto = CR.exposure(markets)["capital"]
        return {"sports_usd": sports, "crypto_usd": crypto,
                "total_usd": sports + crypto}

    def combined_block(self, px: Any, qty: Any) -> Optional[str]:
        """Why a NEW opening/extension of EITHER lane would break the
        explicit combined ceiling (PMUS_COMBINED_MAX_EXPOSURE_USD), or None.
        Unset: sports is as it was (the crypto lane itself refuses without
        it). Set but unreadable, or exposure unreadable: refuse."""
        raw = (os.getenv(CR.COMBINED_CAP_ENV) or "").strip()
        if not raw:
            return None
        cap = CR.combined_cap()
        if cap is None:
            return "COMBINED_CAP_UNREADABLE"
        try:
            if self.combined_exposure()["total_usd"] + \
                    D(str(px)) * D(str(qty)) > cap:
                return "COMBINED_EXPOSURE_CAP"
        except Exception:                                # noqa: BLE001
            return "COMBINED_EXPOSURE_UNREADABLE"
        return None

    def crypto_loss_stopped(self) -> bool:
        """Latched crypto loss stop. Trips when EITHER the bot's own mark of
        the lane (every loop) or the venue-reconciled settled net (hourly)
        is at or below -LOSS_STOP_USD. Every open crypto order is cancelled
        and none is placed again until the owner clears crypto.loss_stop in
        the book. It bounds NEW risk only: a leg already held can still
        lose more than the stop by settlement."""
        st = self._crypto_state()
        if st.get("loss_stop"):
            return True
        if CR.LOSS_STOP_USD <= 0:
            return False                  # crypto_block refuses without one
        markets = self.b.d.get("markets") or {}
        est = CR.book_pnl(markets)["usd"]
        r = getattr(self, "_crypto_meter", None)
        venue = D(str(r["realized_net_usd"])) if r else None
        if est > -CR.LOSS_STOP_USD and (
                venue is None or venue > -CR.LOSS_STOP_USD):
            return False
        st["loss_stop"] = {"t": now_iso(), "book_estimate_usd": str(est),
                           "venue_realized_net_usd":
                               None if venue is None else str(venue),
                           "limit_usd": str(CR.LOSS_STOP_USD)}
        self.b.save()
        log("CRYPTO_LOSS_STOP", **st["loss_stop"],
            effect="every crypto order cancelled; none placed until the "
                   "owner clears crypto.loss_stop",
            label="bounds new risk; held legs can still lose more")
        for s_, mk in markets.items():
            if not CR.is_crypto_market(s_, mk):
                continue
            legs = [(oid, o) for oid, o in (mk.get("orders") or {}).items()
                    if o.get("open")]
            try:
                self._cancel_legs(s_, legs)
            except Exception as exc:                     # noqa: BLE001
                log("cancel_error", slug=s_, at="crypto_loss_stop",
                    err=str(exc)[:160])       # stays open: next step retries
        return True

    def crypto_meter(self, rows: list, markets: Mapping[str, Any],
                     venue_net: Optional[Mapping[str, Any]] = None) -> None:
        """Venue-reconciled CASH result of the crypto markets (same meter
        as the RN1 pilot, src/pmus_pilot_pnl.py) from this audit's venue
        rows and its complete positions walk (`venue_net`; None = no walk,
        which blocks new crypto quotes)."""
        if not (CR.ON or CR.lane_markets(markets)):
            return
        try:
            from src import pmus_pilot_pnl as PP
            r = PP.meter(CR.lane_markets(markets), rows,
                         venue_net=venue_net, stages=(CR.STAGE_OPEN,))
        except Exception as exc:                          # noqa: BLE001
            self._crypto_meter = None
            log("CRYPTO_PNL", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return
        self._crypto_meter = r
        self._crypto_meter_at = time.monotonic()
        self.crypto_loss_stopped()
        self.crypto_report()

    def crypto_meter_block(self) -> Optional[str]:
        """Fail closed: no current venue-reconciled result, no complete
        venue positions walk behind it, or fills / positions the venue does
        not confirm, stop NEW crypto quotes."""
        r = getattr(self, "_crypto_meter", None)
        at = getattr(self, "_crypto_meter_at", None)
        if r is None or at is None or \
                time.monotonic() - at > CR.METER_MAX_AGE_S:
            return "crypto_meter_unavailable"
        if not r.get("positions_checked"):
            return "crypto_positions_unchecked"
        if r.get("unconfirmed"):
            return "crypto_fills_unconfirmed"
        return None

    def crypto_account_view(self) -> Optional[Mapping[str, Any]]:
        """The last account read if it is fresh enough to size risk on."""
        if self._acct_cache is None or self._acct_ok_at is None or \
                time.monotonic() - self._acct_ok_at > ACCOUNT_FRESH_S:
            return None
        return self._acct_cache

    def crypto_account_block(self) -> Optional[str]:
        """The loss budget is computed from the book, so the book must be
        the whole truth. Refuse NEW crypto quotes (lane-wide) unless a fresh
        account read shows, for every open crypto market, exactly the
        book's net position and no resting order the book does not know.
        A closed market is not compared: its leg is already counted lost
        in full until the venue's settlement price is read."""
        acc = self.crypto_account_view()
        if acc is None:
            return "account_state_unavailable"
        markets = self.b.d.get("markets") or {}
        try:
            known = {oid for mk in markets.values()
                     for oid in (mk.get("orders") or {})}
            for o in acc.get("open_orders") or []:
                s_ = str(o.get("marketSlug") or "")
                if CR.is_crypto(s_) and CR.tradable(s_) and \
                        str(o.get("id")) not in known:
                    return "venue_order_unknown:" + s_
            pos = acc.get("positions") or {}
            for s_, mk in CR.lane_markets(markets).items():
                if mk.get("done") or mk.get("settle_px") is not None:
                    continue
                net = D(str(mk["pos"]["YES"])) - D(str(mk["pos"]["NO"]))
                if D(str(pos.get(s_) or 0)) != net:
                    return "venue_inventory_mismatch:" + s_
            for s_ in pos:
                if CR.tradable(s_) and s_ not in markets:
                    return "venue_inventory_unknown:" + s_
        except Exception as exc:                          # noqa: BLE001
            return "account_state_unreadable:" + type(exc).__name__
        return None

    def crypto_reward_credits(self, e: Mapping[str, Any]) -> None:
        """Reward rows the venue lists for a crypto market, credited vs
        pending. Kept apart from the modelled accrual and from P&L."""
        if not (CR.ON or CR.lane_markets(self.b.d.get("markets") or {})):
            return
        if not e.get("ok"):
            self._crypto_rewards = None
            return
        try:
            from src import l5_realized_bridge as bridge
            self._crypto_rewards = CR.venue_rewards(
                e.get("rows_all") or e.get("rows_detail") or [],
                bridge.CREDITED_REWARD_STATUSES)
            self._crypto_rewards["rows_seen"] = e.get("rows")
        except Exception as exc:                          # noqa: BLE001
            self._crypto_rewards = None
            log("CRYPTO_REWARD_CREDITS", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return
        log("CRYPTO_REWARD_CREDITS", ok=True, **self._crypto_rewards)

    def crypto_modelled_usd(self) -> D:
        return sum((D(str((mk.get("crypto_model") or {}).get("usd") or 0))
                    for mk in CR.lane_markets(
                        self.b.d.get("markets") or {}).values()), D(0))

    def crypto_report(self) -> Dict[str, Any]:
        markets = self.b.d.get("markets") or {}
        rep = CR.net_report(getattr(self, "_crypto_meter", None),
                            getattr(self, "_crypto_rewards", None),
                            self.crypto_modelled_usd(),
                            CR.book_pnl(markets),
                            CR.markout_summary(markets))
        r = getattr(self, "_crypto_meter", None) or {}
        log("CRYPTO_PNL", ok=True, loss_stop=self._crypto_state()["loss_stop"],
            loss_stop_usd=str(CR.LOSS_STOP_USD), **rep,
            by_market=r.get("by_market"),
            modelled_by_market={s: mk.get("crypto_model") for s, mk in
                                CR.lane_markets(markets).items()
                                if mk.get("crypto_model")})
        return rep

    def crypto_status(self) -> Dict[str, Any]:
        """Heartbeat block: lane state at a glance."""
        if not CR.ON:
            return {"on": False}
        markets = self.b.d.get("markets") or {}
        ex = CR.exposure(markets)
        st = self._crypto_state()
        return {"on": True, "arm": CR.arm_state(time.time()),
                "active": [s for s in self.active()
                           if CR.is_crypto_market(s, markets[s])],
                "capital_usd": str(ex["capital"]),
                "unpaired_usd": str(ex["unpaired"]),
                "resting_usd": str(ex["resting"]),
                "open_orders": str(ex["open_orders"]),
                "caps": {"total": str(CR.TOTAL_USD),
                         "unpaired": str(CR.unpaired_cap(markets)),
                         "hard_loss_budget": CR.HARD_LOSS_BUDGET,
                         "open_orders": CR.MAX_OPEN_ORDERS,
                         "combined": CR.combined_cap()},
                "combined": {k: str(v) for k, v in
                             self.combined_exposure().items()},
                "book_estimate_usd": str(CR.book_pnl(markets)["usd"]),
                "loss_stop": st["loss_stop"], "market_days": st["days"],
                "modelled_reward_usd": str(self.crypto_modelled_usd()),
                "step_ms": {k: round(v_, 1) for k, v_ in
                            self.__dict__.pop("_crypto_ms", {}).items()},
                "tape": (self._crypto_tape().stats()
                         if self._crypto_tape() is not None else None),
                "label": "modelled reward is not money; see CRYPTO_PNL"}

    def crypto_block(self, slug: str, mk: Mapping[str, Any],
                     now: float) -> Optional[str]:
        """Why this crypto market may not open NEW quotes, or None. Every
        missing input refuses."""
        if not CR.ON:
            return "crypto_off"
        if os.getenv(ENABLED_ENV, "").upper() != "YES":
            return "not_enabled"
        if not CR.tradable(slug):
            return "not_a_pilot_market"
        if CR.LOSS_STOP_USD <= 0:
            return "crypto_loss_stop_unset"
        if self.crypto_loss_stopped():
            return "crypto_loss_stop"
        if self.shared_halt() is not None:
            return "shared_halt"
        if not ACCOUNT_RISK_ON:
            return "account_risk_not_enforced"
        why = self.account_risk_blocks()
        if why is not None:
            return "account_risk:" + str(why)
        if CR.combined_cap() is None:
            return "combined_cap_unset"
        why = self.crypto_meter_block() or self.crypto_account_block()
        if why:
            return why
        days = self._crypto_state()["days"]
        if CR.day_key(slug) not in days and len(days) >= CR.MARKET_DAYS:
            return "pilot_market_days_used"
        why = CR.late(mk.get("end"), now)
        if why:
            return "late:" + why
        if not mk.get("reward"):
            return "no_paying_program"
        return None

    def crypto_place_block(self, slug: str, side: str, px: D, qty: D,
                           stage: str, post_only: bool) -> Optional[str]:
        """The crypto lane's last check, at the single placement choke
        point: nothing but this lane's own maker stages reaches a crypto
        market, and only while the pilot is armed and inside its limits."""
        mk = self.b.d["markets"].get(slug) or {}
        if stage not in CR.STAGES:
            return "other_lane_stage_on_crypto_market"
        if not CR.tradable(slug):
            return "not_a_pilot_market"
        if not post_only:
            return "taker_not_authorized"
        arm = CR.arm_state(time.time())
        if not arm["armed"]:
            return "disarmed:" + str(arm["why"])
        if self._crypto_state().get("loss_stop"):
            return "crypto_loss_stop"
        y = D(str((mk.get("pos") or {}).get("YES") or 0))
        n = D(str((mk.get("pos") or {}).get("NO") or 0))
        if stage == CR.STAGE_COMPLETE:
            need = (n - y) if side == "YES" else (y - n)
            resting = sum((D(o["qty"]) - D(o.get("filled") or "0")
                           for o in (mk.get("orders") or {}).values()
                           if o.get("open") and o.get("side") == side), D(0))
            if need <= 0 or resting + D(str(qty)) > need + D("0.01"):
                return "completion_would_not_reduce"
            return None
        if y != n:
            return "opening_while_unpaired"
        if D(str(qty)) > CR.QTY:
            return "qty_over_cap"
        try:
            why = self.crypto_block(slug, mk, time.time())
            if why:
                return why
            if not CR.budget_ok(self.b.d.get("markets") or {}, slug,
                                D(str(px)) * D(str(qty))):
                return "crypto_loss_budget"
            ex = CR.exposure(self.b.d.get("markets") or {})
        except Exception as exc:                          # noqa: BLE001
            return "crypto_state_unreadable:" + type(exc).__name__
        if ex["capital"] + D(str(px)) * D(str(qty)) > CR.TOTAL_USD:
            return "crypto_total_cap"
        if ex["open_orders"] + 1 > CR.MAX_OPEN_ORDERS:
            return "crypto_open_order_cap"
        return None

    def crypto_programs(self, slugs: list) -> Dict[str, Any]:
        """{slug: paying daily period} from the venue's live programme rows
        (cached REWARD_REFRESH_S). The pool is shared by the programme's
        markets; M_program is at least the number of pilot assets, so the
        modelled figure is an even-split scenario."""
        from src import pmus_sports_rewards as R
        now = time.monotonic()
        cache = getattr(self, "_cprog", None)
        key = tuple(sorted(slugs))
        if cache and cache[0] == key and now - cache[1] < REWARD_REFRESH_S:
            return cache[2]
        found, diag = R.fetch_programs(self._reward_get(), list(key))
        if diag["errors"]:
            raise RuntimeError(str(diag["errors"][:1]))
        out: Dict[str, Any] = {}
        for s, prog in R.build_program_map(found).items():
            per = R.paying_period(prog, time.time(), event_live=True)
            if per and per.get("period") in ("daily_event", "daily"):
                per["M_program"] = max(int(per.get("M_program") or 1),
                                       len(CR.ASSETS))
                out[s] = per
        self._cprog = (key, now, out)
        return out

    def select_crypto(self, acct: Mapping[str, Any]) -> None:
        """Bring the current daily market of each pilot asset into the book
        under lane=crypto. Selecting places nothing; with the lane on but
        not armed the market is priced and modelled in SHADOW."""
        if not CR.ON or os.getenv(ENABLED_ENV, "").upper() != "YES":
            return
        last = self.__dict__.get("_crypto_disc_at")
        if last is not None and time.monotonic() - last < CR.DISCOVER_S:
            return
        self._crypto_disc_at = time.monotonic()
        try:
            targets = CR.pick_targets(self.v.crypto_targets(), time.time())
            progs = self.crypto_programs(
                sorted({t["slug"] for t in targets} |
                       {s for s in self.active() if CR.tradable(s)}))
        except Exception as exc:                          # noqa: BLE001
            log("crypto_discovery_error", err=str(exc)[:160])
            return
        markets = self.b.d["markets"]
        for s in self.active():
            if CR.is_crypto_market(s, markets[s]) and \
                    (markets[s].get("reward") or None) != progs.get(s):
                markets[s]["reward"] = progs.get(s)
        days = self._crypto_state()["days"]
        for tg in targets:
            slug = tg["slug"]
            if slug in markets:
                continue
            n_act = sum(1 for s in self.active()
                        if CR.is_crypto_market(s, markets[s]))
            why = None
            if n_act >= CR.MAX_MARKETS:
                why = "crypto_max_markets"
            elif CR.day_key(slug) not in days and \
                    len(days) >= CR.MARKET_DAYS:
                why = "pilot_market_days_used"
            elif self.foreign_exposure(slug, acct):
                why = "foreign_exposure"
            elif not progs.get(slug):
                why = "no_paying_program"
            if why:
                seen = self.__dict__.setdefault("_crypto_skip", {})
                if seen.get(slug) != why:
                    seen[slug] = why
                    log("crypto_skip", slug=slug, reason=why)
                continue
            mk = self.b.m(slug)
            mk["lane"] = CR.LANE
            mk["reward"] = progs[slug]
            mk["end"] = tg.get("end")
            log("CRYPTO_SELECTED", slug=slug, end=mk["end"],
                program=progs[slug], arm=CR.arm_state(time.time()),
                label="selected into the crypto lane; orders only if armed "
                      "and every gate allows")
        self.b.save()

    def _crypto_book(self, slug: str, bbo: Optional[Mapping] = None
                     ) -> tuple:
        """(public depth, age seconds): re-read every CR.BOOK_S, and at once
        when the live top of book no longer matches the depth held (the
        market moved). A failed read keeps the last one and lets its age
        grow."""
        cache = self.__dict__.setdefault("_cbook", {})
        hit = cache.get(slug)
        now = time.monotonic()
        moved = False
        if hit is not None and bbo is not None:
            bids, asks = CR.book_levels(hit[1], 0.0001)
            top = (max(bids, default=None), min(asks, default=None))
            moved = top != (int(round(float(bbo["bid"]) / 0.0001)),
                            int(round(float(bbo["ask"]) / 0.0001)))
        if hit is None or moved or now - hit[0] >= CR.BOOK_S:
            try:
                md = (self._reward_get()(f"/v1/markets/{slug}/book") or {}
                      ).get("marketData")
                if isinstance(md, Mapping):
                    hit = cache[slug] = (now, md)
            except Exception as exc:                      # noqa: BLE001
                log("crypto_book_error", slug=slug, err=str(exc)[:120])
        if hit is None:
            return None, float("inf")
        return hit[1], now - hit[0]

    def _crypto_accrue(self, mk: Dict[str, Any], now: float) -> None:
        """MODELLED reward for the interval just ended, at the rate of the
        orders that rested through it. Gaps over two minutes accrue
        nothing. Never money."""
        cm = mk.setdefault("crypto_model", {"usd": "0", "rate_usd_s": 0.0,
                                            "t": now, "resting_s": 0.0})
        gap = now - float(cm.get("t") or now)
        rate = float(cm.get("rate_usd_s") or 0.0)
        if 0 < gap <= 120 and rate > 0:
            cm["usd"] = str(D(str(cm["usd"])) + D(str(rate * gap)))
            cm["resting_s"] = float(cm.get("resting_s") or 0.0) + gap
        cm["t"] = now

    def _crypto_tape(self):
        """The lane's bounded observation tape (src/pmus_crypto_tape.py),
        beside the book on the existing state volume; None when off."""
        if "_ctape" not in self.__dict__:
            self._ctape = None
            try:
                from src import pmus_crypto_tape as CT
                if CR.ON and CT.ON:
                    self._ctape = CT.CryptoTape(
                        self.b.path.parent / "crypto_tape")
                    log("crypto_tape_enabled", **self._ctape.stats())
            except Exception as exc:                      # noqa: BLE001
                log("crypto_tape_error",
                    err=f"{type(exc).__name__}:{str(exc)[:120]}")
        return self._ctape

    def _crypto_observe(self, slug: str, mk: Mapping, book: Mapping,
                        tick: D, resting: list, sized: Mapping,
                        block: Optional[str], armed: bool) -> None:
        """Record the depth and what the venue rule ALONE would quote on a
        flat market at the configured size -- before gates, the loss budget
        and the caps -- plus what the lane actually decided (`sized`).
        Writes a file only; never raises; places and cancels nothing."""
        tape = self._crypto_tape()
        if tape is None or not tape.due(slug):
            return
        try:
            q = CR.quotes(book_md=book, program=mk.get("reward"), tick=tick,
                          pos={"YES": 0, "NO": 0}, cost={"YES": 0, "NO": 0},
                          resting=resting)
            m = q.get("model") or {}
            obs = {"best": q.get("best"), "block": block, "armed": armed,
                   "quote": {s_: (None if q.get(s_) is None else
                                  [str(q[s_]["px"]), str(q[s_]["qty"]),
                                   q[s_]["role"]]) for s_ in ("YES", "NO")},
                   "why": {s_: q.get("why_" + s_) or q.get("why")
                           for s_ in ("YES", "NO")},
                   "elig": {k: m.get(k) for k in (
                       "pays", "why_not", "yes_inside", "no_inside",
                       "bid_adj_px", "ask_adj_px", "bid_raw", "ask_raw",
                       "share", "usd_day")},
                   "sized": {s_: (None if sized.get(s_) is None else
                                  [str(sized[s_]["px"]),
                                   str(sized[s_]["qty"]), sized[s_]["role"]])
                             for s_ in ("YES", "NO")},
                   "sized_why": {s_: sized.get("why_" + s_)
                                 for s_ in ("YES", "NO")},
                   "pos": dict(mk.get("pos") or {}),
                   "label": "MODELLED observation; not a fill, not a "
                            "reward"}
        except Exception:                                 # noqa: BLE001
            obs = None
        tape.snapshot(slug, book, obs)

    def _crypto_fail_closed(self, slug: str, mk: Dict[str, Any],
                            exc: Exception) -> None:
        """Anything the crypto step could not read or compute (a corrupt
        book entry, an unreadable limit): pull this market's opening quotes
        by exact id and place nothing. A resting completion is left: it
        only reduces what is unpaired. The cancel's own failure propagates
        to the loop's market_error and is retried next pass."""
        log("CRYPTO_STEP_ERROR", slug=slug,
            err=f"{type(exc).__name__}:{str(exc)[:200]}",
            effect="opening quotes pulled; nothing placed")
        legs = [(oid, o) for oid, o in (mk.get("orders") or {}).items()
                if o.get("open") and o.get("stage") != CR.STAGE_COMPLETE]
        self._cancel_legs(slug, legs)
        self.b.save()

    def _crypto_step(self, slug: str, mk: Dict[str, Any], spec: Mapping,
                     bbo: Mapping, tick: D) -> None:
        """One pass of the crypto reward lane for one market: price both
        sides from the venue's own scoring rule, reconcile our resting
        post-only bids to that, and keep the lane's accounts. Post-only BUYs
        only; nothing here sells, takes or extends a held side."""
        now = time.time()
        mk.setdefault("lane", CR.LANE)
        if mk.get("end") is None:
            mk["end"] = ((getattr(self.v, "states", {}) or {}).get(slug)
                         or {}).get("endDate")
        y, n = D(mk["pos"]["YES"]), D(mk["pos"]["NO"])
        if y == n and y > 0 and mk.get("pair_logged_at") != str(y):
            cpair = (D(mk["cost"]["YES"]) + D(mk["cost"]["NO"])) / y
            self.b.d["pairs"].append({"slug": slug, "qty": str(y),
                                      "pair_cost": str(cpair),
                                      "t": now_iso()})
            mk["pair_logged_at"] = str(y)
            log("PAIR_LOCKED", slug=slug, pairs=y, avg_pair_cost=cpair,
                locked_edge_per_pair=ONE - cpair, mode="crypto",
                label="locked pair economics; realized at settlement")
        mid = (D(str(bbo["bid"])) + D(str(bbo["ask"]))) / 2
        mk["crypto_mid"] = str(mid)
        CR.update_markouts(mk, mid, now)
        self._crypto_accrue(mk, now)
        cm = mk["crypto_model"]
        cm["rate_usd_s"] = 0.0          # set again below if quotes rest
        arm = CR.arm_state(now)
        stop = self.crypto_loss_stopped()
        block = self.crypto_block(slug, mk, now)
        open_orders = {oid: o for oid, o in mk["orders"].items()
                       if o.get("open")}
        book, age = self._crypto_book(slug, bbo)
        if book is None or age > CR.BOOK_MAX_AGE_S:
            # no current depth: nothing can be priced or shown to score.
            # Opening quotes are pulled; a resting completion is left as is.
            stale = [(oid, o) for oid, o in open_orders.items()
                     if o.get("stage") != CR.STAGE_COMPLETE or stop
                     or not CR.ON]
            if mk.get("crypto_logged") != "book_stale":
                mk["crypto_logged"] = "book_stale"
                log("CRYPTO_QUOTE", slug=slug, block="book_stale",
                    age_s=None if book is None else round(age, 1),
                    pulled=len(stale))
            self._cancel_legs(slug, stale)
            self.b.save()
            return
        resting = [(o["side"], D(o["px"]),
                    D(o["qty"]) - D(o.get("filled") or "0"))
                   for o in open_orders.values()]
        try:
            leg = CR.leg_budget(self.b.d["markets"], slug)
        except Exception as exc:                          # noqa: BLE001
            # the budget cannot be computed from the book: no new risk
            leg, block = D(0), block or (
                "crypto_loss_budget_unreadable:" + type(exc).__name__)
        q = CR.quotes(book_md=book, program=mk.get("reward"), tick=tick,
                      pos=mk["pos"], cost=mk["cost"], resting=resting,
                      min_qty=spec["min_qty"],
                      capital_used=CR.market_capital(
                          {**mk, "orders": {}})["filled"],
                      max_leg_usd=leg)
        self._crypto_observe(slug, mk, book, tick, resting, q, block,
                             arm["armed"])
        want: Dict[str, Any] = {}
        why: Dict[str, Any] = {}
        for side in ("YES", "NO"):
            tg = q.get(side)
            why[side] = q.get("why_" + side) or q.get("why")
            if tg and (stop or not CR.ON):
                tg, why[side] = None, ("crypto_loss_stop" if stop
                                       else "crypto_off")
            if tg and tg["role"] == "open" and block:
                tg, why[side] = None, block
            want[side] = tg
        try:
            CR.fit_caps(self.b.d["markets"], slug, want, why)
        except Exception as exc:                          # noqa: BLE001
            for s_ in ("YES", "NO"):
                if want[s_] and want[s_]["role"] == "open":
                    want[s_] = None
                    why[s_] = "crypto_caps_unreadable:" + type(exc).__name__
        opens = [s_ for s_ in ("YES", "NO")
                 if want[s_] and want[s_]["role"] == "open"]
        if opens:
            # the combined sports + crypto ceiling, on what these quotes
            # ADD to this market's resting openings (place() checks again)
            add = sum((want[s_]["px"] * want[s_]["qty"] for s_ in opens),
                      D(0)) - sum((r_[1] * r_[2] for r_, o in zip(
                          resting, open_orders.values())
                          if o.get("stage") == CR.STAGE_OPEN), D(0))
            cw = self.combined_block(ONE, max(D(0), add))
            if cw:
                for s_ in opens:
                    want[s_], why[s_] = None, cw
        if CR.REQUIRE_BOTH and sum(1 for t_ in want.values() if t_ and
                                   t_["role"] == "open") == 1:
            for side in ("YES", "NO"):
                if want[side] and want[side]["role"] == "open":
                    want[side] = None
        model = q.get("model") or {}
        key = json.dumps({"want": {s: (None if t_ is None else
                                       [str(t_["px"]), str(t_["qty"]),
                                        t_["role"]])
                                   for s, t_ in want.items()},
                          "why": why, "armed": arm["armed"]}, sort_keys=True)
        if mk.get("crypto_logged") != key:
            mk["crypto_logged"] = key
            log("CRYPTO_QUOTE", slug=slug, pos=mk["pos"], cost=mk["cost"],
                best=q.get("best"), block=block, armed=arm["armed"],
                arm_why=arm.get("why"),
                want={s: (None if t_ is None else
                          {"px": str(t_["px"]), "qty": str(t_["qty"]),
                           "role": t_["role"]}) for s, t_ in want.items()},
                why=why,
                eligibility={k: model.get(k) for k in (
                    "pays", "why_not", "yes_inside", "no_inside",
                    "yes_ticks_from_best", "no_ticks_from_best",
                    "bid_adj_px", "ask_adj_px", "bid_raw", "ask_raw")},
                modelled={"share": model.get("share"),
                          "usd_day": model.get("usd_day"),
                          "markets_sharing_pool":
                              model.get("markets_sharing_pool")},
                label="crypto reward lane decision; orders only if armed "
                      "and every gate allows. Modelled figures are not "
                      "money")
        stale, keep = [], {}
        for oid, o in open_orders.items():
            tg = want.get(o["side"])
            left = D(o["qty"]) - D(o.get("filled") or "0")
            role = "complete" if o.get("stage") == CR.STAGE_COMPLETE \
                else "open"
            if tg is not None and o["side"] not in keep and \
                    arm["armed"] and role == tg["role"] and \
                    D(o["px"]) == tg["px"] and left <= tg["qty"]:
                keep[o["side"]] = oid
            else:
                stale.append((oid, o))
        if stale:
            before = (str(mk["pos"]["YES"]), str(mk["pos"]["NO"]))
            self._cancel_legs(slug, stale)
            self.b.save()
            if (str(mk["pos"]["YES"]), str(mk["pos"]["NO"])) != before or \
                    any(o.get("open") for _oid, o in stale):
                return       # a fill raced the cancel, or still pending
        if y == 0 and n == 0 and not keep and \
                str(block or "").startswith("late:"):
            mk["done"] = True
            self.b.save()
            log("released_flat_market", slug=slug, pos=mk["pos"],
                mode="crypto", reason=block)
            return
        if arm["armed"]:
            for side in ("YES", "NO"):
                tg = want.get(side)
                if tg is None or side in keep:
                    continue
                self.place(slug, side, tg["px"], tg["qty"],
                           CR.STAGE_COMPLETE if tg["role"] == "complete"
                           else CR.STAGE_OPEN)
        live = [(o["side"], D(o["px"]),
                 D(o["qty"]) - D(o.get("filled") or "0"))
                for o in mk["orders"].values() if o.get("open")]
        if live and mk.get("reward"):
            was = [(o["side"], D(o["px"]),
                    D(o["qty"]) - D(o.get("filled") or "0"))
                   for oid, o in open_orders.items() if o.get("open")]
            try:
                cm["rate_usd_s"] = CR.assess(book, mk["reward"], tick, live,
                                             was)["rate_usd_s"]
            except Exception as exc:                      # noqa: BLE001
                log("crypto_model_error", slug=slug, err=str(exc)[:120])
        self.b.save()


    # ------------------------------------------------ RN1 replica (default off)
    def rn1_campaign_exposure(self, exclude: Optional[str] = None
                              ) -> Dict[str, D]:
        """Committed spend / unmatched across RN1 markets not yet settled:
        filled cost + completion reserve + resting bids (RN1.committed)."""
        from src import pmus_pilot_pnl as PP
        tot = {"spend": D(0), "unmatched": D(0)}
        markets = self.b.d.get("markets") or {}
        for s_ in PP.pilot_slugs(markets, PILOT_SINCE):
            mk_ = markets[s_]
            if s_ == exclude or mk_.get("settle_px") is not None:
                continue
            resting = [(o["side"], o["px"],
                        D(o["qty"]) - D(o.get("filled") or "0"),
                        is_completion_stage(o.get("stage")))
                       for o in (mk_.get("orders") or {}).values()
                       if o.get("open")]
            c = RN1.committed(mk_["pos"], mk_["cost"], resting)
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
        others = sum(1 for s_, m_ in (self.b.d.get("markets") or {}).items()
                     if s_ != slug and not m_.get("done") and
                     not CR.is_crypto_market(s_, m_) and
                     self._rn1_exposed(m_))
        return others >= MAX_MARKETS

    def rn1_campaign_caps(self, slug: str, mk: Dict[str, Any],
                          want: Dict[str, Any], q: Dict[str, Any]) -> None:
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
            want[drop] = None
            q["why_" + drop] = over

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
        new_block = None
        if os.getenv(ENABLED_ENV, "").upper() != "YES":
            new_block = "not_enabled"
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
        # account-wide blocks hold the slot (they lift); a block about THIS
        # market (late, excluded, open-EV gate) frees a flat market's slot,
        # exactly as the two-sided path releases a gated flat market
        account_block = new_block
        late = is_late((getattr(self.v, "states", {}) or {}).get(slug)) \
            if LATE_CUTOFF else None
        if excluded(slug):
            late = "excluded"
        if not new_block and late:
            new_block = "late:" + str(late)
        if not new_block and OPEN_EV_GATE and not self.open_gate(slug)["allow"]:
            new_block = "open_ev_gate"
        if not new_block and self.ws is not None and \
                now < float(mk.get("jump_pause_until") or 0):
            new_block = "jump_pause"
        if not new_block and self.rn1_market_slots_full(slug, mk):
            new_block = "max_markets"
        q = RN1.quotes(yes_bid=bbo["bid"], yes_ask=bbo["ask"], tick=tick,
                       pos=mk["pos"], cost=mk["cost"],
                       last_fill_t=mk.get("last_fill_t"), now=now,
                       min_qty=spec["min_qty"])
        want: Dict[str, Any] = {}
        for side in ("YES", "NO"):
            tg = q.get(side)
            if tg and tg["role"] != "complete" and new_block:
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
            want[side] = tg
        self.rn1_campaign_caps(slug, mk, want, q)
        key = json.dumps({s: (None if t is None else
                              [str(t["px"]), str(t["qty"]), t["role"]])
                          for s, t in want.items()} |
                         {"block": new_block,
                          "why": {s: q.get("why_" + s) for s in ("YES", "NO")}},
                         sort_keys=True)
        if mk.get("rn1_logged") != key:
            mk["rn1_logged"] = key
            log("RN1_QUOTE", slug=slug, pos=mk["pos"], cost=mk["cost"],
                bid=bbo["bid"], ask=bbo["ask"], block=new_block,
                want={s: (None if t is None else
                          {"px": str(t["px"]), "qty": str(t["qty"]),
                           "role": t["role"]}) for s, t in want.items()},
                why={s: q.get("why_" + s) for s in ("YES", "NO")},
                label="RN1 replica decision; orders only if every gate "
                      "and the submission lock allow")
        # reconcile: keep a resting bid within REPRICE_TICKS of target with
        # no more than the target size left; cancel everything else first
        stale = []
        keep = {}
        for oid, o in mk["orders"].items():
            if not o.get("open"):
                continue
            tg = want.get(o["side"])
            left = D(o["qty"]) - D(o.get("filled") or "0")
            if tg is not None and o["side"] not in keep and \
                    abs(D(o["px"]) - tg["px"]) < RN1.REPRICE_TICKS * tick and \
                    left <= tg["qty"]:
                keep[o["side"]] = oid
            else:
                stale.append((oid, o))
        if stale:
            before = (str(mk["pos"]["YES"]), str(mk["pos"]["NO"]))
            self._cancel_legs(slug, stale)
            self.b.save()
            if (str(mk["pos"]["YES"]), str(mk["pos"]["NO"])) != before or \
                    any(o.get("open") for _oid, o in stale):
                return       # a fill raced the cancel, or still pending
        flat = y == 0 and n == 0
        if flat and not any(want.values()) and not account_block:
            idle = mk.setdefault("idle_since", now)
            if now - float(idle) > 120 and \
                    not any(o.get("open") for o in mk["orders"].values()):
                mk["done"] = True
                self.b.save()
                log("released_flat_market", slug=slug, pos=mk["pos"],
                    bid=bbo["bid"], ask=bbo["ask"], mode="rn1")
            return
        mk.pop("idle_since", None)
        for side in ("YES", "NO"):
            tg = want.get(side)
            if tg is None or side in keep:
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
            st = self.v.status(oid)
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
                if mk.get("lone_since") is not None:
                    self._recovery_resolved(slug, mk, "held_to_close")
                self.b.save()
                log("market_closed", slug=slug, pos=mk["pos"],
                    unmatched_cost=self.b.unmatched_cost(slug))
            return
        tick = spec["tick"]
        self._mark(slug, mk, bbo)
        if reward_first_on() and mk.get("reward") and \
                not CR.is_crypto_market(slug, mk):
            # THEORETICAL accrual for the interval just ended (legs that
            # rested), then refresh the share estimate for the next one
            self._accrue(slug, mk)
            try:
                self._reward_estimate(slug, mk, tick)
            except Exception as exc:                  # noqa: BLE001
                log("reward_estimate_error", slug=slug, err=str(exc)[:120])
        if CR.is_crypto_market(slug, mk):
            # crypto reward lane: its own decisions and limits. Checked
            # BEFORE RN1 and the two-sided path and it always returns, so a
            # crypto market can never be accumulated, extended or taker-
            # exited by them -- with the lane off its orders are cancelled.
            t0 = time.monotonic()
            try:
                self._crypto_step(slug, mk, spec, bbo, tick)
            except Exception as exc:                      # noqa: BLE001
                self._crypto_fail_closed(slug, mk, exc)
            # time this lane takes from the shared loop (heartbeat: crypto)
            tm = self.__dict__.setdefault("_crypto_ms", {"n": 0, "sum": 0.0,
                                                         "max": 0.0})
            ms = (time.monotonic() - t0) * 1000
            tm["n"] += 1
            tm["sum"] += ms
            tm["max"] = max(tm["max"], ms)
            return
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
            late = is_late((getattr(self.v, "states", {}) or {}).get(slug)) \
                if LATE_CUTOFF else None
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

    def select(self, acct: Mapping[str, Any]) -> None:
        if os.getenv(ENABLED_ENV, "").upper() != "YES":
            return
        if self.b.total_unmatched() >= TOTAL_CAP:
            return
        if self.full():
            return
        try:
            targets = (self.v.pregame_sports() if PREGAME
                       else self.v.live_sports())
        except Exception as exc:                      # noqa: BLE001
            log("discovery_error", err=str(exc)[:160])
            return
        if PREGAME:
            self._game_start.update({t["slug"]: t["game_start"]
                                     for t in targets if "game_start" in t})
        scored = []
        for tg in targets:
            slug = tg["slug"]
            if slug in self.b.d["markets"]:
                continue
            meta = tg.get("market_meta") or {}
            b = self.v.gw._qnum(meta.get("bestBidQuote"))
            a = self.v.gw._qnum(meta.get("bestAskQuote"))
            if b is None or a is None:
                continue
            if excluded(slug) or (LATE_CUTOFF and is_late(
                    (getattr(self.v, "states", {}) or {}).get(slug))):
                continue
            b, a = D(str(b)), D(str(a))
            if not (BAND_LO <= b <= BAND_HI):
                continue
            if OPEN_EV_GATE and OPEN_GATE_ALLOW_SPORTS and \
                    not self.open_gate(slug)["allow"]:
                continue         # would be gated at open: don't take a slot
            scored.append((a - b, abs(b - D("0.5")), slug))
        scored.sort()
        if self.guard is not None:
            self.guard.prefetch([s for *_x, s in scored])
            if self.guard.require:
                # open only where intl can price the market (resolved in the
                # guard's background thread; unresolved -> next round)
                scored = [r for r in scored if self.guard.token(r[2])]
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
                break
            self._try_select(slug, acct, rewarded.get(slug))

    def rewarded_active(self) -> int:
        return sum(1 for s in self.sports_active()
                   if self.b.m(s).get("reward"))

    def _try_select(self, slug: str, acct: Mapping[str, Any],
                    rp: Optional[Mapping[str, Any]],
                    est: Optional[Mapping[str, Any]] = None) -> bool:
        if self.foreign_exposure(slug, acct):
            log("skip_foreign_exposure", slug=slug)
            return False
        spec = self.v.spec(slug)
        bbo = self.v.bbo(slug) if spec else None
        if not spec or not bbo or open_prices(bbo["bid"], bbo["ask"],
                                               spec["tick"]) is None:
            return False
        mk = self.b.m(slug)
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
            r = PP.meter(markets, rows, since=PILOT_SINCE,
                         venue_net=venue_net)
        except Exception as exc:                          # noqa: BLE001
            self._pilot = None                    # -> pilot_meter_unavailable
            log("PILOT_PNL", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return
        self._pilot = r
        self._pilot_at = time.monotonic()
        log("PILOT_PNL", ok=True, since=PILOT_SINCE,
            loss_stop_usd=str(PILOT_LOSS_USD),
            new_openings_block=self.pilot_block(),
            cash=self.pilot_cash(r), **r)

    def pilot_cash(self, r: Mapping[str, Any]) -> Dict[str, Any]:
        """Venue account cash next to what the campaign explains. READ-ONLY
        GET, report only (shared account). The baseline is taken once per
        PILOT_SINCE, together with the campaign's expected cash at that
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
        key = str(PILOT_SINCE)
        if cash is not None and (not base or base.get("since") != key) \
                and not r.get("unconfirmed"):
            base = {"since": key, "cash": str(cash), "expected": str(exp),
                    "t": now_iso()}
            self.b.d["pilot_cash_base"] = base
            self.b.save()
        return PP.cash_check(cash, exp, base if base and
                             base.get("since") == key else None)

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

    def account_audit(self) -> None:
        """VENUE TRUTH for this bot's markets, from /v1/portfolio/activities:
        realizedPnl summed per market and per sport (trades + resolutions),
        and the commissions on OUR side of our trades (taker fees charged,
        maker rebates credited). Owner 2026-11-15: P&L from one source."""
        try:
            from src import pmus_settlement_auditor as SA
            from src import pmus_incentive_audit as IA
            walk = SA.activity_pages(self.v.t, since=self.AUDIT_SINCE,
                                     max_pages=200)
        except Exception as exc:                          # noqa: BLE001
            log("ACCOUNT_AUDIT", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return
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
        inv = self.inventory_reconcile()
        # residual = what settled markets realized beyond their completed
        # pairs' gross margin (lone legs, exits; fees if the venue nets them)
        mk_all = self.b.d.get("markets") or {}
        # the venue's PUBLIC final settlement price per market (bounded)
        looked = 0
        from src import pmus_settlement_public_px as SP
        for s_, mk_ in list(mk_all.items()):
            if looked >= SETTLE_LOOKUPS_PER_AUDIT or self.stop:
                break                       # shutdown: cancel first
            if mk_.get("settle_px") is not None or not mk_.get("done") or (
                    D(mk_["pos"]["YES"]) == 0 and D(mk_["pos"]["NO"]) == 0):
                continue
            looked += 1
            if looked > 1 and SETTLE_LOOKUP_GAP_S > 0:
                time.sleep(SETTLE_LOOKUP_GAP_S)
            try:
                ps = SP.public_settlement(self.v.t, s_)
            except Exception:                             # noqa: BLE001
                continue
            if ps.get("final"):
                mk_["settle_px"] = ps["px"]
        if looked:
            self.b.save()
        bs = PNL.book_settled(mk_all)
        self.pilot_meter(mine, mk_all,
                         venue_net=getattr(self, "_venue_net", None))
        self.crypto_meter(mine, mk_all,
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
                return
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

    def reward_check(self) -> None:
        """OBSERVED reward earnings from the venue. Only rows the venue marks
        credited are money; pending/skipped are reported, never counted."""
        try:
            from src import pmus_incentive_audit as IA
            e = IA.earnings(self.v.t)
        except Exception as exc:                          # noqa: BLE001
            log("REWARD_CHECK", ok=False,
                err=f"{type(exc).__name__}:{str(exc)[:160]}")
            return
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
        self.crypto_reward_credits(e)

    def report(self) -> None:
        pairs = self.b.d["pairs"]
        if self.guard is not None:
            self.guard.track(self.sports_active())
        extra = {} if self.ws is None else {
            "ws": self.ws.status(), "reactive_pulls": self.reactive_pulls,
            "orphan_stale_snapshot": self.orphan_stale,
            "jump_pauses": self.jump_pauses}
        if self.guard is not None:
            extra["intl_guard"] = self.guard.status()
        _bud = getattr(getattr(self.v, "t", None), "budget", None)
        if _bud is not None:
            extra = dict(extra, venue_requests=_bud.report())
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
            rewarded_active=self.rewarded_active(),
            crypto=self.crypto_status(),
            reward_caps={"qty": REWARD_QTY,
                         "per_market_lone_leg": REWARD_PER_MARKET_CAP},
            reward_first=reward_first_on(),
            reward_label="program rewards THEORETICAL; not in P&L")

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self.on_sigterm())
        self.require_fresh_account = True     # no new exposure on stale state
        self.mark_recovery_start()
        seed = seed_drift(self.b.d, os.getenv("PMUS_TWO_SIDED_DRIFT_SEED"))
        if seed["seeded"] or seed["error"] or seed["kept_live"]:
            self.b.save()
            log("drift_seed", **seed, label="measured completion drift "
                "preloaded for sports with no live history")
        log("boot", rn1=({"on": True, **RN1.cfg()} if RN1.ON else {"on": False}),
            crypto=({"on": True, **CR.cfg(), "arm": CR.arm_state(time.time()),
                     "combined_max_exposure_usd": CR.combined_cap()}
                    if CR.ON else {"on": False}),
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
            daily_loss_usd=DAILY_LOSS_USD, pregame={"on": PREGAME, "min_lead_s": PREGAME_MIN_LEAD_S,
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
            try:
                if os.getenv(ENABLED_ENV, "").upper() != "YES":
                    self.cancel_all_own("disabled")
                    time.sleep(30)
                    continue
                if time.monotonic() - last_sel > 20:
                    if self.full() and LATE_CUTOFF:
                        try:                       # keep game clocks fresh
                            self.v.live_sports()
                        except Exception:          # noqa: BLE001
                            pass
                    acc = self.read_account()
                    if acc is not None:
                        self.select(acc)
                        self.select_crypto(acc)
                        last_sel = time.monotonic()
                    else:                          # retry sooner, not every pass
                        last_sel = time.monotonic() - 20 + ACCOUNT_RETRY_S
                    if self.guard is not None:
                        self.guard.track(self.sports_active())
                    if rewards_on() and acc is not None:
                        self.refresh_active_rewards()
                for slug in self.active():
                    if self.stop:
                        break
                    self.drain_ws_events()          # no-op unless WS mode
                    try:
                        self.step_market(slug)
                    except Exception as exc:          # noqa: BLE001
                        log("market_error", slug=slug,
                            err=f"{type(exc).__name__}:{str(exc)[:200]}")
                if REWARD_CHECK_S > 0 and \
                        time.monotonic() - self._last_reward > REWARD_CHECK_S:
                    self._last_reward = time.monotonic()
                    self.reward_check()
                    self.account_audit()
                if SHARP_MODE == "shadow" and time.monotonic() - getattr(
                        self, "_last_sharp", -1e9) > SHARP_SHADOW_EVERY_S:
                    self._last_sharp = time.monotonic()
                    try:
                        self.sharp_shadow_tick()
                    except Exception as exc:          # noqa: BLE001
                        log("sharp_shadow_error",
                            err=type(exc).__name__)
                if time.monotonic() - getattr(self, "_last_orphan", 0) > 30:
                    self._last_orphan = time.monotonic()
                    # reuse the account read's open orders when it is fresh
                    # (one signed GET instead of two)
                    fresh = self._acct_cache is not None and \
                        self._acct_ok_at is not None and \
                        time.monotonic() - self._acct_ok_at <= 30
                    self.orphan_sweep(self._acct_cache["open_orders"]
                                      if fresh else None)
                if time.monotonic() - last_hb > 60:
                    self.report()
                    last_hb = time.monotonic()
                if reward_first_on() and \
                        time.monotonic() - last_acc > REWARD_ACCRUAL_LOG_S:
                    last_acc = time.monotonic()
                    self.reward_accrual_report()
                    self.b.save()
            except Exception as exc:                  # noqa: BLE001
                log("loop_error", err=f"{type(exc).__name__}:{str(exc)[:200]}")
            try:
                self.drain_ws_events()
            except Exception as exc:                  # noqa: BLE001
                log("loop_error", err=f"{type(exc).__name__}:{str(exc)[:200]}")
            self._sleep(LOOP_S)
        self.cancel_all_own("sigterm")
        return 0


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
    return {"readable": True, "lane": LANE, "manages_own_resting": True,
            "enabled": os.getenv(ENABLED_ENV, "").upper() == "YES",
            # True ONLY under the lane's own independent lock (above); the
            # shared halt is deliberately NOT counted, since a reset that
            # proved "disabled" via the very latch it clears proves nothing
            "submission_disabled": submission_locked(),
            "orders": rows, "as_of_utc": now_iso(),
            "labels": ("venue order ids pmus_sports_two_sided placed and "
                       "still believes live; it cancels them itself on "
                       "requote, disable and SIGTERM")}


def serve_registry(book: "Book", port: int) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):                               # noqa: N802
            path = self.path.split("?", 1)[0].lstrip("/")
            if path not in (REGISTRY_KEY, "health"):
                self.send_response(404); self.end_headers(); return
            body = json.dumps(registry_doc(book) if path == REGISTRY_KEY
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
            time.sleep(CM.POLL_S)

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
