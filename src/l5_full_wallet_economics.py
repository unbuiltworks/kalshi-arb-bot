"""Full-wallet economic-target audit: is L5 a profitable trader who receives
incentives, or an incentive-driven liquidity business?

ANSWER: an incentive-driven liquidity business. H2. Trading is -$2,828.77 on
$7,626,612.72 of purchases; incentives are +$71,551.74; total +$68,722.97.

READ-ONLY. No cap, model, gate, re-quote flag or the stopped live lane
changes. This is an economic audit before any further live probe.

=========================================================================
INGESTION: THE WHOLE WALLET, NOT THE 9% WE HAD
=========================================================================
Source: data/l5/raw/activity.jsonl + activity_gap.jsonl.gz, the venue's own
activity feed, unwrapped from its paging envelopes and deduplicated on
(txhash, type, timestamp, conditionId, asset, size, price, side, outcomeIndex).

    779,898 deduped activity items   2026-03-19 -> 2026-08-31
    TRADE 763,966 | REDEEM 15,729 | MAKER_REBATE 110 | TAKER_REBATE 73
    | REWARD 20 | MERGE 0 | SPLIT 0 | CONVERSION 0
    17,046 distinct markets

AND A CORRECTION TO MY OWN SCOPE CLAIM. I said our canonical tape was "a
subset" without sizing it. It is 71,984 fills of 763,966 (9.4%) and 2,216
markets of 17,046 (13.0%). Every economic statement I made about L5 before
this audit rested on a ninth of his trading.

WHAT THE FULL WALLET IS NOT: diversified. Every single trade is a crypto
up-down market. btc 748,668 trades, eth 15,298; 15m 701,150, 5m 62,816. No
sports, no politics, no combinatorial cross-market positions. The article's
"combinatorial arb across linked markets" has no footprint here at all, and
the wallet's category concentration is total.

=========================================================================
STRICT CASH ACCOUNTING -- SOURCE-OBSERVED ONLY, NOTHING INFERRED
=========================================================================
    BUY notional out      -$7,626,612.72   (763,964 buys)
    SELL notional in            +$2.18     (2 sells -- he never sells)
    REDEEM in             +$7,623,781.77
    -----------------------------------------------------
    TRADING NET               -$2,828.77
    INCENTIVES               +$71,551.74
    -----------------------------------------------------
    TOTAL                    +$68,722.97

The article claims "$66k PnL". Source-accounted total is +$68,722.97 -- a 4%
gap, and the composition is unambiguous: trading contributes NEGATIVE
$2,828.77 and every dollar of profit plus more comes from venue payments.

WHY UNREDEEMED MARKETS ARE NOT A MISSING CASHFLOW. 1,529 markets carry buys
and no REDEEM, $35,697.08 of notional, which would otherwise look like an
accounting hole. They are not. Their median buy is $10.48 against $167.21
for redeemed markets, and their median PAIRED share of units is 0.000
against 0.775. They are small, wholly one-sided positions. On this venue you
redeem WINNING tokens; a position that loses entirely has nothing to redeem,
so the absence of a REDEEM row IS the correct recording of a total loss.

FEES: no fee rows exist in the feed. Recorded as not observed, not as zero.

=========================================================================
H1 / H2 / H3
=========================================================================
H1 -- independently positive trading expectancy, amplified by incentives.
   REJECTED. Trading net is negative on the full wallet: -$2,828.77,
   -0.00037 per dollar purchased.

H2 -- approximately break-even to slightly negative before incentives, with
   incentives creating the business.
   SUPPORTED, and it is the whole story: incentives are 25x the absolute
   size of the trading loss and carry the entire positive result.

H3 -- different categories use materially different economics.
   PARTIALLY SUPPORTED, but not in the way the hypothesis anticipated: there
   is only ONE category. Within it the two assets differ in sign --
       btc  13,635 markets  $7,443,412.89 bought  -$3,800.17  -0.00051/$
       eth   3,411 markets    $183,199.83 bought    +$971.40  +0.00530/$
   ETH is positive per dollar and BTC negative, but ETH is 2.4% of the
   notional, so the aggregate is not misleading about the business -- it is
   misleading only about ETH.

=========================================================================
DOES BEHAVIOUR TRACK THE PAYOUT RATE? YES, ON THREE DIMENSIONS
=========================================================================
    month   inc per $    bought $   markets    trades   median residual share
    2026-03   0.00000       7,789        88     1,929        0.206
    2026-04   0.00275     334,235       548    37,371        0.102
    2026-05   0.00281     711,998       919    89,095        0.090
    2026-06   0.00273     671,143     1,031    77,097        0.125
    2026-07   0.00448   1,287,304     3,666   133,996        0.333
    2026-08   0.01323   4,614,144    10,794   424,476        0.361

As the payout rate rose 4.8x (0.275% -> 1.323%), purchases rose 13.8x,
markets touched rose 19.7x, and the median share of inventory left UNPAIRED
rose from about 0.10 to 0.36. He got materially less careful about pairing
exactly as the payment per dollar rose.

CAUSATION IS NOT ESTABLISHED and two readings survive: the rate may be
endogenous to volume (a tier he climbed), or a richer program may have drawn
the volume. The residual-tolerance change is the one that is hard to explain
mechanically -- a volume tier does not force you to leave more inventory
unpaired -- but it is still an association, not a proof.

=========================================================================
INCENTIVE-CAPACITY MAP
=========================================================================
    MAKER_REBATE  110 events  $35,228.32
    REWARD         20 events  $28,797.69
    TAKER_REBATE   73 events   $7,525.73
    concentration: 85.3% of all incentive dollars landed in August alone.

PER-MARKET ATTRIBUTION IS UNKNOWN AND STAYS UNKNOWN. Every incentive row
carries an EMPTY conditionId, so $0.00 of the $71,551.74 can be causally
attributed to a specific market from this feed. Program names, rate
schedules and eligibility rules are likewise absent. We can measure what he
was paid and when; we cannot measure which program paid it or whether it was
capacity-limited. Answering that needs the venue's own program
documentation, which we do not hold.

The rate RISING with activity is evidence against dilution over this window,
but one wallet's receipts cannot establish a program's capacity curve.

=========================================================================
OUR PMUS DEPLOYMENT AGAINST THAT YARDSTICK
=========================================================================
Source-confirmed only; pending and advertised rewards count zero.

    settled markets with venue-confirmed realized : 19
    cost basis deployed                           : $554.4908
    contracts bought                              : 1,167.25
    venue-confirmed realized trading P&L          : -$35.8086
    confirmed maker rebates (credited)            : +$2.1300
    confirmed fees                                : $0.0000
    TOTAL source-accounted                        : -$33.6786

    PER DOLLAR TRADED          PMUS (ours)     L5 (crypto)
      trading                   -0.06458        -0.00037
      incentive                 +0.00384        +0.00275 -> +0.01323
      total                     -0.06074        +0.00901

THE BINDING CONSTRAINT IS NOT THE PAYMENT. Our credited rebate rate, 0.384%
of notional, is already inside L5's own range and above his early months.
The gap is that OUR TRADING LOSES 175 TIMES MORE PER DOLLAR THAN HIS DOES --
6.458% against 0.037%. No incentive program on any venue closes a 6.5%
per-dollar trading loss; at L5's best observed rate we would still be losing
5.1% per dollar.

So "does PMUS pay enough for L5-like behaviour to have positive expectancy"
has a two-part answer: the rate is plausibly adequate, and it is irrelevant
until the trading loss falls by roughly two orders of magnitude. L5's engine
runs on near-perfect pairing at enormous turnover; ours strands residual in
markets that trend for hours.

=========================================================================
WHICH ACCESSIBLE VENUE COULD REPRODUCE THE ENGINE
=========================================================================
Reproducing it requires BOTH: (a) a liquidity/rebate program paying roughly
0.3%+ of notional, and (b) trading loss held below that. We fail (b) by 175x
today, so venue choice is the second question, not the first.

On (a), from our own census artifacts:
  KALSHI KXCRYPTOLEAD15M -- 3,545 liquidity programs since 2026-08-24, one
    per outcome-market per window, resting orders scored once per second on
    size and closeness, NO FILL REQUIRED TO EARN. Five more 15-minute
    commodity series queued. This is the closest structural analogue to L5's
    engine on a venue we can reach. UNITS UNVERIFIED: period_reward 200,000
    is only ~$20 if the unit is centi-cents, and that has not been confirmed.
  KALSHI KXBTC15M / KXETH15M -- the series our shadow actually trades has NO
    active or upcoming programs. Zero subsidy there.
  PMUS liquidity rewards -- UNPROVEN. 4 earnings rows, $0.22, every row
    SKIPPED, zero credit rows in the full activity walk,
    our_markets_in_programs = 0 in every audit.

The honest ranking: Kalshi's incentivized 15-minute series is the only
accessible place where the L5 composition -- short windows, continuous
two-sided quoting, payment for resting rather than for filling -- plausibly
exists. It is also unverified on units and untested by us. PMUS sports pays
us a real but tiny credited rebate on a book whose structure produces our
6.5% loss.

=========================================================================
DELIVERABLE
=========================================================================
L5 is an INCENTIVE-DRIVEN LIQUIDITY BUSINESS whose trading behaviour is the
cost of earning the payments, not a profitable trader who also collects
rebates. His trading loses money at high volume; the venue pays him more
than the loss; the aggressive EXTEND persistence we spent this program
replicating is the ACTIVITY THE PAYMENT REWARDS, not an edge.

Cloning his decisions onto a venue that does not pay that way reproduces
only the cost side. That is what our PMUS numbers show.
"""
from __future__ import annotations

from decimal import Decimal as D

# --- ingestion --------------------------------------------------------------
DEDUPED_ITEMS = 779898
SPAN = ("2026-03-19", "2026-08-31")
ACTIVITY_TYPES = {"TRADE": 763966, "REDEEM": 15729, "MAKER_REBATE": 110,
                  "TAKER_REBATE": 73, "REWARD": 20,
                  "MERGE": 0, "SPLIT": 0, "CONVERSION": 0}
MARKETS = 17046
CANONICAL_TAPE_COVERAGE = {"fills": (71984, 763966), "markets": (2216, 17046)}
TRADES_BY_ASSET = {"btc": 748668, "eth": 15298}
TRADES_BY_DURATION = {"15m": 701150, "5m": 62816}
NON_CRYPTO_TRADES = 0

# --- cash accounting --------------------------------------------------------
BUY_USD = D("7626612.72")
SELL_USD = D("2.18")
REDEEM_USD = D("7623781.77")
INCENTIVES_USD = D("71551.74")
INCENTIVE_SPLIT = {"MAKER_REBATE": D("35228.32"), "REWARD": D("28797.69"),
                   "TAKER_REBATE": D("7525.73")}
FEES = "NOT OBSERVED"
ARTICLE_PNL_CLAIM = D("66000")
UNREDEEMED = {"markets": 1529, "usd": D("35697.08"),
              "median_buy": D("10.48"), "median_paired_share": D("0.000")}
REDEEMED_MEDIAN_PAIRED_SHARE = D("0.775")

BY_ASSET = {"btc": {"markets": 13635, "buy": D("7443412.89"), "net": D("-3800.17")},
            "eth": {"markets": 3411, "buy": D("183199.83"), "net": D("971.40")}}

# --- behaviour vs payout rate ----------------------------------------------
MONTHLY = (  # month, incentive per $, bought $, markets, trades, median residual share
    ("2026-03", D("0.00000"), D("7789"), 88, 1929, D("0.206")),
    ("2026-04", D("0.00275"), D("334235"), 548, 37371, D("0.102")),
    ("2026-05", D("0.00281"), D("711998"), 919, 89095, D("0.090")),
    ("2026-06", D("0.00273"), D("671143"), 1031, 77097, D("0.125")),
    ("2026-07", D("0.00448"), D("1287304"), 3666, 133996, D("0.333")),
    ("2026-08", D("0.01323"), D("4614144"), 10794, 424476, D("0.361")),
)
AUGUST_SHARE_OF_INCENTIVES = D("0.853")
INCENTIVE_MARKET_ATTRIBUTION = "UNKNOWN - every incentive row has an empty conditionId"
INCENTIVE_PROGRAM_TERMS = "NOT HELD"

# --- hypotheses -------------------------------------------------------------
HYPOTHESES = {"H1_trading_positive_amplified": "REJECTED",
              "H2_incentives_create_the_business": "SUPPORTED",
              "H3_categories_differ": "PARTIALLY - there is only one category"}

# --- PMUS -------------------------------------------------------------------
PMUS = {"markets_confirmed": 19, "cost_basis": D("554.4908"),
        "contracts": D("1167.25"), "trading_pnl": D("-35.8086"),
        "rebates_credited": D("2.1300"), "fees": D("0.0000")}
PER_DOLLAR = {"pmus_trading": D("-0.06458"), "pmus_incentive": D("0.00384"),
              "pmus_total": D("-0.06074"),
              "l5_trading": D("-0.00037"), "l5_incentive_max": D("0.01323"),
              "l5_total": D("0.00901")}

# --- venue map --------------------------------------------------------------
VENUE_MAP = {
    "kalshi_KXCRYPTOLEAD15M": "3,545 liquidity programs since 2026-08-24; resting "
                              "orders scored, NO FILL REQUIRED; units UNVERIFIED",
    "kalshi_KXBTC15M_KXETH15M": "no active or upcoming programs; zero subsidy",
    "pmus_liquidity_rewards": "UNPROVEN - $0.00 credited, all earnings rows SKIPPED",
}

VERDICT = "INCENTIVE_DRIVEN_LIQUIDITY_BUSINESS"
BINDING_CONSTRAINT = "trading loss per dollar, not the incentive rate"
AUTHORISES_RESTART = ()
AUTHORISES_MODEL_CHANGE = ()


def trading_net() -> D:
    return (REDEEM_USD + SELL_USD - BUY_USD).quantize(D("0.01"))


def total_economics() -> D:
    return (trading_net() + INCENTIVES_USD).quantize(D("0.01"))


def reconciles_with_claim(tol: D = D("0.05")) -> bool:
    return abs(total_economics() - ARTICLE_PNL_CLAIM) / ARTICLE_PNL_CLAIM < tol


def incentives_exceed_trading_loss_multiple() -> D:
    return (INCENTIVES_USD / trading_net().copy_abs()).quantize(D("0.1"))


def canonical_tape_fill_coverage() -> D:
    a, b = CANONICAL_TAPE_COVERAGE["fills"]
    return (D(a) / D(b)).quantize(D("0.001"))


def payout_rate_rose_and_so_did_residual() -> bool:
    paid = [m for m in MONTHLY if m[1] > 0]
    return paid[-1][1] > paid[0][1] and paid[-1][5] > paid[0][5]


def our_trading_loss_multiple_of_l5() -> D:
    return (PER_DOLLAR["pmus_trading"] / PER_DOLLAR["l5_trading"]).quantize(D("0.1"))


def incentive_rate_is_the_binding_constraint() -> bool:
    """Would L5's BEST rate rescue our trading loss? No."""
    return (PER_DOLLAR["pmus_trading"] + PER_DOLLAR["l5_incentive_max"]) > 0
