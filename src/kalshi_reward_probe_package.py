"""The bounded Kalshi reward probe. DISARMED. Specification and economics
only -- this module places no orders and holds no credentials.

--- WHAT THIS PROBE IS FOR, AND IT IS NOT PROFIT ---
Every other question about Kalshi is now answered or answerable from public
data. One is not, and no amount of public data will answer it: OUR SHARE OF
SCORE. Kalshi normalises a participant's reward against competing qualifying
liquidity and never publishes either the weighting or anyone's score.

So this probe is a MEASUREMENT PURCHASE, not a trade. Its deliverable is one
credited-reward number on a real account. Judge it on whether it buys that
measurement cheaply, not on its P&L.

--- WHY THE MEASUREMENT IS WORTH BUYING ---
The $1 minimum payout against a $20 pool means we need a 5% share or we are
paid nothing at all. The two readings of the qualifying rule land on
opposite sides of that line:

    narrow band (near-mid competition only)   ~78% share on KXNATGAS15M
    full book (all resting size)              ~0.7% share -> $0.21 -> ZERO

One credited payout tells us which regime we are in. Nothing else does.

--- THE DESIGN POINT: REWARD IS EARNED ON RESTING, FEE ONLY ON FILL ---
This is the asymmetry that makes the probe cheap. A post-only two-sided
quote that never trades earns whatever reward it qualifies for and pays no
fee and takes no position. The trading risk is therefore not the size of the
quote -- it is the probability that the quote is HIT, and on what side.

    both sides fill   -> a manufactured pair at ~1.02, costing ~2% of
                         notional. On a $30 quote that is about -$0.60.
    one side fills    -> naked inventory to settlement. THIS is the real
                         risk and it is bounded by the 15-minute market
                         life and by the quote size, not by a stop.
    neither fills     -> $0.00 cost, and the reward observation still lands.

The last row is the point. The probe can succeed at zero trading cost.

--- WHY DEMO CANNOT SUBSTITUTE, TESTED AND CLOSED ---
demo-api.kalshi.co serves /incentive_programs, so it looked like a free
answer. It is not: 143 programs, ZERO active, none paid_out, the newest
ended 2026-07-30. Demo does not run live incentive programs and therefore
cannot credit a reward. Recorded so nobody re-proposes it.

--- TARGET SELECTION, FROM THE MEASURED LEG RANKING ---
KXNATGAS15M and KXCOPPER15M. They carry the venue's highest observed reward
density (0.2667 per capital dollar per hour, 6x the next series found in a
52,000-program scan) at a pair cost of 1.02, and the thinnest near-mid
competition of any leg with a meaningful pool (30.2 and 78.5 contracts
within 2c). If share of score is winnable anywhere on this venue with $111,
it is winnable here; if it is not winnable here, the venue is closed to us
at this bankroll and we have learned that for the price of one period.
"""

from decimal import Decimal as D

ARMED = False
ORDER_ENTRY = False
REQUIRES_OWNER_GO = True

PURPOSE = ("buy ONE credited-reward observation to resolve share of score. "
           "This is a measurement purchase, not a trade, and it must not be "
           "justified or judged on expected profit.")

TARGETS = ("KXNATGAS15M", "KXCOPPER15M")

# ------------------------------------------------------------- the bounds
BOUNDS = {
    "max_quote_notional_usd": D("30.00"),   # per side, per market
    "max_markets_concurrent": 1,
    "max_periods": 1,                       # ONE 15-minute window, then stop
    "post_only": True,                      # never take, never pay taker fee
    "two_sided": True,                      # both sides or the snapshot cannot count
    "cancel_at_period_end": True,
    "deadman_seconds": 60,                  # cancel if the session loses its heartbeat
    "no_requote": True,                     # a re-quote surrenders queue position
    "max_trading_loss_usd": D("1.50"),      # hard stop; both-sides-fill costs ~$0.60
    "hold_residual_to_settlement": True,    # 15 minutes; no panic exit
}

WHAT_IT_MUST_NOT_DO = (
    "no taker orders, ever -- post_only or nothing",
    "no re-quote on a moved touch; the Gemini shells measured that as a "
    "five-fold fill-rate cost and this probe wants LOW fill anyway",
    "no second period without reading the first period's credited reward",
    "no reuse of the PMUS spring probe's arming keys or its capital",
    "no change to any PMUS cap, gate, threshold or the daily stop",
)

# ------------------------------------------------- what counts as success
SUCCESS_IS_A_NUMBER_NOT_A_PROFIT = (
    "The probe SUCCEEDS if it returns a credited reward figure, including "
    "$0.00. A credited $0.00 is a real result: it proves the full-book "
    "denominator and closes Kalshi at this bankroll, which is worth far "
    "more than the dollar it cost to learn.")

READOUT = {
    "primary": "credited reward for the period, from the venue's own ledger",
    "secondary": ("which denominator regime that implies, by comparing the "
                  "credited figure against the share curve already measured "
                  "for that leg"),
    "tertiary": ("realised fills, if any, with pair cost and maker fee, "
                 "kept strictly separate from the reward figure"),
}

ACCOUNTING = (
    "SUBMITTED -> FILLED -> SETTLED -> REALIZED, venue numbers only. The "
    "reward figure is CREDITED and is a fourth, separate status. Never sum "
    "a credited reward into a realised trading P&L, and never infer either "
    "from a mark.")

# ============================================================ PREFLIGHT
# Owner comment 5594247824: $3,419 confirmed on the Kalshi account, and a
# request for the full preflight. Bankroll is no longer the blocker; the
# reward-OBSERVATION path is, and it turned out to be weaker than assumed.

PREFLIGHT = {
    "candidate": "KXNATGAS15M (primary), KXCOPPER15M (fallback)",
    "why_this_candidate": (
        "ranks 1-5 of 300 active series on reward density in the complete "
        "venue census (183,329 programs), at 0.2667 per capital dollar per "
        "hour against a venue median of 0.00060; pair cost 1.02 measured "
        "over ~97 snapshots each; and the thinnest near-mid competition of "
        "any leg carrying a real pool (58.6 and 78.5 contracts within 2c)"),

    "quote_construction": {
        "side": "BUY both sides -- YES and NO -- simultaneously",
        "type": "limit, post_only=true on BOTH legs",
        "price": ("at the prevailing best bid on each side, NOT improving "
                  "it. Improving the touch buys queue position we do not "
                  "need and raises fill probability, which this probe wants "
                  "LOW: a fill is a cost, the reward is earned by resting"),
        "size": ("floor($30 notional / price) per side, so exposure is "
                 "bounded in DOLLARS rather than contracts"),
        "count_markets": 1,
        "count_periods": 1,
        "requote": "NEVER -- one placement, then observe",
    },

    "max_exposure": {
        "capital_committed_usd": "<= 60.00 (two sides x $30)",
        "worst_case_both_fill_usd": "-0.60 at pair cost 1.02",
        "worst_case_one_side_fills_usd": (
            "-30.00 absolute worst case, if the filled leg settles worthless "
            "and the market moves fully against us inside 15 minutes. This "
            "is the REAL risk and it is not the -$0.60 headline."),
        "hard_loss_stop_usd": "1.50",
        "stop_note": ("the stop cannot prevent the one-sided-fill case, "
                      "because a 15-minute market can gap; it bounds "
                      "DISCRETIONARY loss, not settlement risk. Sizing is "
                      "the real control and that is why it is $30."),
    },

    "cancel_and_deadman": {
        "cancel_at": "period end, unconditionally",
        "deadman_seconds": 60,
        "on_disconnect": "cancel both legs; do not re-place",
        "on_one_side_filled": ("cancel the unfilled leg, HOLD the filled "
                               "leg to settlement, do NOT chase a complete "
                               "-- chasing is what produced the PMUS "
                               "residual that cost -6.458%/$"),
    },

    # THE PART THAT CHANGED. See kalshi_account_preflight_readonly.
    "reward_credit_observation_path": {
        "method": ("BALANCE RESIDUE, because Kalshi exposes NO reward-ledger "
                   "endpoint -- 31 plausible names return 404, and only "
                   "balance/fills/settlements/orders/positions exist"),
        "formula": ("credited_reward = balance_delta - realised_fill_cash "
                    "- settlement_cash"),
        "requires": ("an OTHERWISE IDLE account across the window. Any "
                     "other Kalshi activity in the same period contaminates "
                     "the only instrument available and voids the run."),
        "snapshots": ("/portfolio/balance immediately BEFORE placement and "
                      "again AFTER the market settles and the reward period "
                      "closes; /portfolio/fills and /portfolio/settlements "
                      "for the same window to subtract the explained cash"),
        "timing_caveat": ("reward crediting lags: paid_out flips to true "
                          "between 12h and 48h after a period ends, so the "
                          "AFTER snapshot must be taken at least 48h later, "
                          "not at settlement. A same-hour read showing $0.00 "
                          "means NOTHING and must not be reported as a "
                          "credited zero."),
    },

    "pass_fail": {
        "PASS": ("a non-zero credited reward is observed as an unexplained "
                 "balance residue. Share of score is then MEASURED for this "
                 "leg and the chain's open link closes."),
        "ALSO_PASS": ("a credited $0.00 confirmed at >=48h with the account "
                      "idle. That proves the full-book denominator and "
                      "closes Kalshi at this bankroll -- a real result."),
        "FAIL_VOID": ("any other account activity in the window, or an "
                      "AFTER snapshot taken before 48h. Void the run and "
                      "repeat; do not interpret a contaminated residue."),
        "FAIL_HARD": ("both legs fill and the realised pair cost exceeds "
                      "1.05, indicating the book moved far worse than the "
                      "~97-snapshot measurement predicted"),
    },

    "what_it_does_not_establish": (
        "One period on one leg is one observation. It settles WHETHER we "
        "are paid at all -- which is binary and currently unknown -- not "
        "the rate, the variance, or the residual economics. Do not scale on "
        "a single credited number."),
}

PREFLIGHT_BLOCKER = (
    "NOT YET RUN. This session holds no Kalshi credentials, and the two "
    "routes to them are closed by standing rules: reading Railway service "
    "variables would pull secrets into the transcript, and the existing "
    "authenticated transport runs on AWS where no work is authorised. The "
    "owner must place read-only credentials somewhere this session can "
    "reach, OR run src/kalshi_account_preflight_readonly.py where they "
    "already live and paste back its non-secret output.")


# ------------------------------------------------------- honest escalation
NOT_YET_A_POSITIVE_EXPECTANCY_CLAIM = (
    "This package does NOT assert defensibly positive total expectancy, and "
    "must not be escalated as though it did. Two terms remain open: share "
    "of score (which is what the probe buys) and residual economics (for "
    "which we hold no Kalshi fills at all). What IS defensible today is "
    "narrower and still useful: the best-ranked leg on this venue, by a "
    "measured margin, and a probe whose downside is bounded near zero.")

PRECONDITIONS_FOR_ARMING = (
    "explicit owner go, naming this package",
    "a funded Kalshi account with read access to its own reward ledger, "
    "confirmed BEFORE any order -- an uncreditable account makes the probe "
    "pointless",
    "the leg ranking re-run inside the hour before arming, since the "
    "target's competition is what the probe is betting on and it moves",
    "PMUS remains stopped and untouched throughout",
)
