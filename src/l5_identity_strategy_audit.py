"""Identity / strategy-consistency audit: does l5zn1bwom8etsk behave like the
strategy the 0xSurferX article claims to operate?

Answer: NO on every load-bearing operational claim, and the decisive one is
new evidence found during this audit.

Standing rule applied: the article is a hypothesis source; the wallet's own
public activity is the evidence. No model, cap, gate, flag or the stopped
live lane changes here, and nothing from the article is implemented.

=========================================================================
TWO CORRECTIONS FIRST
=========================================================================

CORRECTION 1, AND IT IS MINE. I told the owner twice that incentive
intensity was "NOT SOURCEABLE" for L5 and that any proxy would be
unfalsifiable. THAT WAS WRONG. I searched for reward-program TERMS and never
looked for reward RECEIPTS, which were sitting in our own repository at
data/l5/raw/activity.jsonl. The wallet's activity feed carries REWARD,
MAKER_REBATE and TAKER_REBATE events with timestamps and dollar amounts.
Hypothesis D is testable, and it is now tested.

CORRECTION 2. The directive cites "~97% maker in our existing
reconstruction". That is not our figure and no module states it. The
canonical tape reads 55,647 maker, 11,068 taker, 5,269 unknown of 71,984
fills: 77.3% maker overall, or 83.4% of the classified subset. The maker
lean is real; 97% is not.

=========================================================================
THE DECISIVE FINDING: L5 HAS NEVER MERGED
=========================================================================
The article's operational core is "merge complete pairs back into $1 of
collateral immediately, pocket the edge, and free the capital in seconds".

Across 784,500 activity items for this wallet:

    TRADE           768,325
    REDEEM           15,971
    MAKER_REBATE        114
    TAKER_REBATE         73
    REWARD               20
    MERGE                 0        <-- zero
    SPLIT                 0
    CONVERSION            0

AND THIS IS NOT AN ABSENCE-OF-EVIDENCE ARGUMENT, because we have a control.
Three other traders' activity feeds in our own repository, pulled through
the same endpoint and parser, DO carry the type:

    0xf705fa04   MERGE 1,561   SPLIT 179
    justdance    MERGE 9,044   CONVERSION 72
    bonereaper   MERGE    35

The feed emits MERGE when a trader merges. This wallet never does. What it
does instead is REDEEM -- 15,729 deduplicated events, $7,623,781.77 -- which
is SETTLEMENT REDEMPTION of resolved positions, the opposite of merging
before resolution to recycle capital.

=========================================================================
WHERE THE MONEY ACTUALLY COMES FROM
=========================================================================
Incentive receipts credited to the wallet, 2026-04-21 to 2026-08-31:

    MAKER_REBATE   110 events   $35,228.32
    REWARD          20 events   $28,797.69
    TAKER_REBATE    73 events    $7,525.73
    ------------------------------------------
    TOTAL          203 events   $71,551.74

Against that, the fills we reconstructed (BTC/ETH up-down windows only) net
-$1,050.57: pairs +$18,912.86, residuals -$19,963.43.

THE ARTICLE CLAIMS "$66k PnL". OUR MEASURED INCENTIVES ALONE ARE $71,551.74.
The claimed profit is therefore fully accountable by venue incentives with
no trading edge whatsoever, and the earlier open question -- is the
aggressive EXTEND behaviour intrinsic, or is he paid to provide activity --
now has an evidence-backed answer: THE PAYMENT IS THE BUSINESS. That is a
reconciliation, not a proof of causation: we cannot see his trading P&L
outside up-down windows, so a trading contribution elsewhere is not excluded.

This also retires my earlier framing that L5's "edge, if any, is in
incentives we cannot see". We can see them. They are $71.5k.

=========================================================================
THE SEVEN AUDIT ITEMS
=========================================================================

1. NEUTRALITY / RESIDUAL
   2,193 of 2,216 windows (99.0%) end carrying residual; 23 (1.0%) end flat.
   Naked inventory: 11,996.0 dollar-hours. Paired inventory: 70,573.2
   dollar-hours. Naked is 14.5% of all inventory dollar-time. Residual units
   are 25.8% of all units and carry -$19,963.43.
   The article calls naked legs "the single most common way arb bots blow
   up" and claims a risk agent prevents them. This wallet ends 99% of its
   windows with one.

2. PAIR-FIRST BEHAVIOUR
   Eventual matched inventory by realised pair cost, unit-weighted:
       < 1.00   520,125 units   80.3%   (985 windows)
       = 1.00         0 units    0.0%   (0 windows)
       > 1.00   127,897 units   19.7%   (625 windows)
   Roughly a fifth of everything he eventually pairs is paired ABOVE par --
   a guaranteed loss on those units, taken deliberately. And he keeps buying
   as completion gets more expensive: measured earlier, same-side purchase
   probability rises monotonically with attainable pair cost, 37.5% below
   0.90 to 62.0% above 1.20.

3. IMMEDIATE MERGE
   CONTRADICTED, with control. Zero MERGE events; see above. The only
   observed capital-returning event is REDEEM at settlement.

4. SIMULTANEOUS ARBITRAGE
   Contemporaneous executability is UNVERIFIABLE -- we hold fills, never a
   book, so what the far side quoted at leg-1 time cannot be recovered.
   What IS measurable: median 47s between complementary legs, 1.3% of pair
   records in the same second, 6.6% within five seconds; and of profitable
   pairs 78.0% are movement-dominated, with median favourable movement
   +0.1000 against median profit +0.0700. Same-second pairs earn a median
   0.02; pairs formed more than two minutes apart earn 0.11.

5. EXECUTION: "BUYS AT THE ASK"
   Buying at the ask is taker behaviour. Measured: 77.3% maker (83.4% of
   classified). The article's own section 6 argues the opposite of its
   section 4, and the tape sides with section 6. Note this is a fill-role
   classification from the venue's own feed, verified for this audit rather
   than assumed.

6. CAPITAL VELOCITY
   The only capital release we can observe is settlement, because there are
   no merges. First fill to window settlement: median 5.0 minutes, mean 7.5,
   p90 15.0. The article says capital is freed "in seconds". It is freed in
   minutes, by resolution, not by merging.

7. CLAIM LEDGER -- see CLAIM_LEDGER below.

=========================================================================
P0 DELIVERABLE
=========================================================================
Does the public behaviour of l5zn1bwom8etsk resemble the strategy this
author claims to operate?

NO. Six of the article's operational claims are contradicted or
unsupported by the wallet's own activity, one is internally inconsistent,
and the single most specific mechanical claim -- immediate merge and
seconds-scale capital recycling -- is contradicted with a control proving
the data would have shown it.

WHAT THIS DOES NOT ESTABLISH: whether 0xSurferX controls the wallet. He may
control it and describe it inaccurately, or promote a strategy he does not
run, or not control it at all. Identity is NOT resolved by this audit and is
not needed for the operational conclusion: whatever the wallet is doing, it
is not the strategy in the article, and the strategy in the article is not
what our clone should be aimed at.
"""
from __future__ import annotations

from decimal import Decimal as D

# --- corrections ------------------------------------------------------------
MY_EARLIER_CLAIM_D_NOT_SOURCEABLE = "WRONG - receipts were in data/l5/raw/activity.jsonl"
DIRECTIVE_MAKER_FIGURE = D("0.97")     # as cited to me
MEASURED_MAKER_SHARE = D("0.773")      # 55,647 / 71,984
MEASURED_MAKER_SHARE_CLASSIFIED = D("0.834")   # 55,647 / 66,715
MAKER_TAKER_UNKNOWN = (55647, 11068, 5269)

# --- the merge control ------------------------------------------------------
L5_ACTIVITY_TYPES = {"TRADE": 768325, "REDEEM": 15971, "MAKER_REBATE": 114,
                     "TAKER_REBATE": 73, "REWARD": 20,
                     "MERGE": 0, "SPLIT": 0, "CONVERSION": 0}
CONTROL_TRADERS_MERGE = {"0xf705fa04": 1561, "justdance": 9044, "bonereaper": 35}
L5_REDEEM_EVENTS = 15729
L5_REDEEM_USD = D("7623781.77")

# --- incentives (hypothesis D, now testable) --------------------------------
INCENTIVES = {"MAKER_REBATE": (110, D("35228.32")),
              "REWARD": (20, D("28797.69")),
              "TAKER_REBATE": (73, D("7525.73"))}
INCENTIVE_SPAN = ("2026-04-21", "2026-08-31")
ARTICLE_PNL_CLAIM = D("66000")
UPDOWN_FILL_NET = D("-1050.57")

# --- audit measurements -----------------------------------------------------
WINDOWS = 2216
WINDOWS_ENDING_RESIDUAL = 2193
NAKED_DOLLAR_HOURS = D("11996.0")
PAIRED_DOLLAR_HOURS = D("70573.2")
PAIR_COST_UNITS = {"<1.00": 520125, "=1.00": 0, ">1.00": 127897}
SAME_SIDE_P_BY_PAIR_COST = ((D("0.90"), D("0.375")), (D("1.20"), D("0.620")))
MEDIAN_LEG_GAP_S = D("47.0")
SAME_SECOND_SHARE = D("0.013")
MOVEMENT_DOMINATED_SHARE = D("0.780")
CAPITAL_LOCK_MIN = {"median": D("5.0"), "mean": D("7.5"), "p90": D("15.0")}
SIMULTANEOUS_EXECUTABILITY = "UNVERIFIABLE"

CLAIM_LEDGER = (
    # (claim, evidence, verdict, strength, provenance)
    ("simultaneously fillable YES+NO < $1",
     "median 47s between legs; 1.3% same-second; 78% movement-dominated",
     "CONTRADICTED", "strong", "canonical fills 71,984"),
    ("verify both legs / depth before firing",
     "no order-level data exists; cannot be observed either way",
     "UNVERIFIABLE", "n/a", "fills carry no orders"),
    ("stay market-neutral, avoid naked legs",
     "99.0% of windows end carrying residual; 14.5% of inventory "
     "dollar-time is naked; residuals -$19,963.43",
     "CONTRADICTED", "strong", "canonical fills"),
    ("predominantly GTC / maker",
     "77.3% maker (83.4% of classified)",
     "SUPPORTS", "strong", "venue maker/taker flag"),
    ("hunter buys at the ask",
     "taker behaviour; contradicts the article's own GTC section; tape is "
     "predominantly maker",
     "CONTRADICTED", "strong", "venue maker/taker flag"),
    ("complete both legs",
     "19.7% of eventually matched units pair ABOVE par; 25.8% of units "
     "never pair at all",
     "CONTRADICTED", "strong", "canonical fills"),
    ("immediately merge complete pairs back to $1",
     "ZERO MERGE events in 784,500 items, while three control traders in "
     "the same feed show 1,561 / 9,044 / 35",
     "CONTRADICTED", "decisive", "raw activity feed + control"),
    ("rapidly recycle capital, freed in seconds",
     "only observable release is settlement; first fill to settlement "
     "median 5.0 min, p90 15.0 min",
     "CONTRADICTED", "strong", "canonical fills + zero merges"),
    ("every millisecond between legs matters",
     "median gap between legs is 47 seconds",
     "CONTRADICTED", "strong", "canonical fills"),
    ("$66k PnL",
     "incentive receipts alone total $71,551.74; up-down fills net "
     "-$1,050.57; trading P&L outside up-down windows is unknown",
     "EXPLAINED BY INCENTIVES", "strong", "REWARD/REBATE activity rows"),
    ("copying the wallet is useless; you never see timing or risk logic",
     "we reached the same limit independently",
     "SUPPORTS", "moderate", "our own evidence limits"),
    ("combinatorial arb across linked markets",
     "our ingestion holds only single binary up-down windows",
     "UNVERIFIABLE", "n/a", "ingestion gap"),
)

RESEMBLES_THE_CLAIMED_STRATEGY = False
IDENTITY_RESOLVED = False
AUTHORISES_MODEL_CHANGE = ()
AUTHORISES_RESTART = ()
IMPLEMENTS_OUTSIDE_STRATEGY = False


def total_incentives() -> D:
    return sum((usd for _, usd in INCENTIVES.values()), D("0"))


def incentives_exceed_claimed_pnl() -> bool:
    return total_incentives() > ARTICLE_PNL_CLAIM


def l5_has_never_merged() -> bool:
    return L5_ACTIVITY_TYPES["MERGE"] == 0


def merge_control_is_valid() -> bool:
    """The feed demonstrably emits MERGE for other traders."""
    return all(v > 0 for v in CONTROL_TRADERS_MERGE.values())


def naked_dollar_time_share() -> D:
    return (NAKED_DOLLAR_HOURS / (NAKED_DOLLAR_HOURS + PAIRED_DOLLAR_HOURS)
            ).quantize(D("0.001"))


def above_par_matched_share() -> D:
    t = sum(PAIR_COST_UNITS.values())
    return (D(PAIR_COST_UNITS[">1.00"]) / D(t)).quantize(D("0.001"))


def verdicts(kind: str) -> tuple:
    return tuple(c for c, _, v, _, _ in CLAIM_LEDGER if v == kind)
