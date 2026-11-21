"""OWNER P0: the 2026-09-08 daily-loss stop, and aec-wta-kaique-annrog as a
forensic case study of the L5-vs-safety-shell question.

READ-ONLY. This module states what the tape says. It changes no cap, no
threshold, no gate, no flag and no policy, and it is not an authorisation to
restart anything.

--------------------------------------------------------------------------
1. WHY THE STOP FIRED WHEN THE THREE ROWS I COULD SEE SUMMED TO ONLY -$7.31
--------------------------------------------------------------------------
Because there were TEN realized rows on Sep 8, not three. I had read three
log windows, not the day. Reconstructed from every settlement row the
campaign logged with a non-empty ledger_rows_written, the day sums to
-$18.5873 against a -$15.00 stop. The stop is arithmetically correct and
nothing about it was weakened, bypassed or is proposed to be.

Every candidate defect the owner named was checked and none is present:

  duplicate settlements  NO. Each slug appears exactly once. The ledger key
                         is (kind, slug, settlement_key); a re-audit of the
                         same resolution re-derives the same key and the
                         write is skipped.
  prior-campaign rows    NO. The Sep 7 rows (petbru-alecan, tomvoj-ticjir,
                         laa-bos, scovla-otgand) carry Sep 7 `t` and are
                         outside the window.
  timezone / date window realized_today() takes datetime.now(timezone.utc)
                         and matches rows whose `t` startswith that ISO
                         date; `t` is UTC on both sides, so there is no
                         offset bug. There IS a semantic subtlety worth
                         naming: `t` is the WRITE time, not the settlement
                         time, so a market that resolves late on day N and
                         is audited after 00:00Z lands in day N+1. It did
                         not bite here (no Sep 8 row was written for a Sep 7
                         resolution) but it is a real property of the
                         window.
  stale state            NO. Every row is venue-confirmed.
  goodwill / rewards     NO. kind "realized" is written in exactly one
                         place, append_authoritative_ledger, and only from
                         the venue's own realizedPnl / POSITION_RESOLUTION.
                         No incentive, rebate or mark ever enters it.
  double counting        NO, per the duplicate check above.

EVIDENCE LIMIT, stated rather than hidden: this table is reconstructed from
the campaign's own log mirror of the ledger, because state_dir()/ledger.jsonl
sits on the probe's durable volume and cannot be read from here. Log coverage
on Sep 8 has two gaps (about 06:27-10:40 and 14:28-16:00). A row hidden in a
gap can only move the total; for the stop to have been WRONG the hidden rows
would have to sum to more than +$3.5873, and exactly one positive realized
row exists in the whole harvest (+$1.4610). The verdict is robust; the exact
total is "at least this negative".

--------------------------------------------------------------------------
2. WHAT ACTUALLY HAPPENED IN aec-wta-kaique-annrog-2026-09-08
--------------------------------------------------------------------------
We bought 15.74 NO and never bought a single YES. The venue resolved the
market LONG (YES won), the NO expired worthless, and the realized loss is
exactly the cost basis: -$5.3169 on 15.74 units at an average NO price of
0.33780.

The position was built by the MODEL, not by the shell. Decisions 6-16 are
sampled=open/extend, acted=open/extend, cap_binding=[] throughout. In state
p25-45c|tte>10m|imb10-50 (n=298) the frozen model ranks extend 0.6786 /
complete 0.3214 / open 0.0, and it drew extend six consecutive times while
the market trended away from us. Every fill was maker, at the NO touch, and
the NO touch kept getting CHEAPER as YES rallied 0.62 -> 0.69. That is the
adverse-selection signature: our resting NO bids filled precisely because
the market was repricing to YES.

The shell first bound at decision 17, at 20:58:25.62, when the position was
ALREADY 15.74 units and spent was ALREADY $5.3169. spent + planned 2.4949 =
7.8118 > the 7.50 spend cap. The shell converted a sampled EXTEND into a
COMPLETE. It did this again at decision 20. On both rows the shell wrote its
own counterfactual: would_act_without_safety_cap = "extend",
divergence = "safety_adaptation:spend_cap->complete".

SO THE CAP DID NOT CREATE OR WORSEN THIS POSITION. It bound after the
position was complete and its effect was to stop further accumulation of the
losing side. On this market the cap SAVED money. That runs against the prior
this investigation started from and is stated as measured.

Four complement (COMPLETE) attempts were submitted, all maker at L5's touch,
all unfilled:

    20:58:57  YES 0.61  4.09u  rest 31.4s  no_fill  pair 0.9478  BELOW par
    20:59:29  YES 0.70  3.57u  rest 31.5s  no_fill  pair 1.0378  above par
    21:01:34  YES 0.74  3.37u  rest 32.0s  no_fill  pair 1.0778  above par
    21:02:06  YES 0.96  2.60u  rest 16.5s  no_fill  pair 1.2978  above par
                                        (ended repriced:bbo_unreadable:Halt)

Only the first was below par. The complement was chasing a price running
away from it, which is the same adverse selection seen from the other side.

Three EXTEND orders were REJECTED by the venue while non-crossing
(ORDER_REJECTED_ZERO_FILL, orders CCEDBG49PR3X / CCEDKC4MYR3X / CCEDP8GDMR47).
That is issue #93, unsolved, appearing inside this loss.

THE HALT. At 21:01:35 the book was still two-sided and readable at YES
0.7400 / 0.7500. At 21:02:24 the session ended SPRING_SAFE_REFUSAL with
reason BBO_NOT_TWO_SIDED. From 21:04:35 through 21:17:35 every two-minute
resume read ['err','Halt']. The venue resolved at 21:18:01. So the last
moment at which any exit was priceable was 21:01:35, and the executable
window from the first complement attempt to that point was about three
minutes, in which 111 seconds of maker rest at L5's touch produced no fill.

--------------------------------------------------------------------------
3. THE CAP COUNTERFACTUAL ON THIS LOSS
--------------------------------------------------------------------------
Would a larger cap have let the L5-requested sequence pair, or merely bought
more of the losing side? On this market the tape answers: more of the losing
side, monotonically.

The cap rule is `spent + planned_usd > cap`. planned_usd was 2.4949 and each
filled extend cost about 4.09 contracts x ~0.31 = $1.268.

    cap        additional EXTENDs permitted   extra spend   loss if unpaired
    $7.50  (actual)          0                    $0.000        -$5.3169
    $10.00                   2                    $2.536        -$7.853
    $12.50                   4                    $5.072       -$10.389
    $15.00                   6                    $7.608       -$12.925

Those extra fills are COUNTERFACTUAL EXECUTABLE OPPORTUNITIES, not observed
fills, so the honest bracket applies the fill rate actually observed on the
accumulating side in that window (4 of 7 NO orders filled, 57%):

    $10.00   -$6.76      $12.50   -$8.21      $15.00   -$9.65

Both brackets have the same sign and the same slope. Capacity did not bind
on the complement: 4 complete attempts and 111 seconds of rest returned zero
fills while every non-rejected NO order filled within 4.7-15.1 seconds. The
constraint was the far side of the book, not the budget, and no amount of
budget buys a fill that the other side will not give.

THE ONE EXIT THAT DID EXIST, and why it was not taken. At 21:01:35 the book
read YES 0.7400/0.7500, so the NO bid was 1 - 0.75 = 0.25. Selling 15.74 NO
into that bid returns $3.935 against a $5.3169 basis: a -$1.3819 loss instead
of -$5.3169, saving $3.935. That is a TAKER sell. Taker is disabled by owner
rule and this module does not propose changing it. The saving is also
UNVERIFIABLE in size: we logged the BBO price but not its depth, so whether
15.74 units could clear at 0.25 cannot be known from this tape. Recording
that depth is exactly what the read-only book capture shipped at 21:17Z does,
and it landed 19 minutes after these fills -- every order row in this case
carries book_capture=null.

--------------------------------------------------------------------------
4. HOW MUCH OF THE POPULATION IS THIS CHAIN
--------------------------------------------------------------------------
Across every settled market in the harvest with a venue-confirmed negative
realized (18 markets, -$37.2696 total), 11 markets show the full surface
chain -- a spend/residual cap conversion AND a stranded residual at
settlement -- and those 11 carry -$27.5940, about 74% of the loss dollars.

That correlation is real and the causal reading of it is NOT. Testing each
market for whether the residual was already at its final size when the cap
FIRST bound:

    cap bound only AFTER the residual was fully built      8 of 11
    residual still grew after the first cap conversion     3 of 11

In the 8, the cap capped the position; it did not cause it. In the 3, the
growth between conversions came from acted EXTENDs the model itself drew,
not from the conversion. And the single largest loss in the whole cohort,
asc-cfb-wisc-nd (-$6.2855, 19.27 units stranded, 0 matched), had ZERO cap
conversions -- the chain is absent from the worst case.

What IS population-wide is the fill asymmetry. On these 11 markets the side
that GROWS the position fills at 23.8% (31/130) and the side that CLOSES it
fills at 9.2% (16/174) -- a 2.6x asymmetry, consistent with the 17.7%
COMPLETE fill rate measured across the whole tape. The clone accumulates
much more easily than it can pair off, and the losses live there.

--------------------------------------------------------------------------
VERDICT ON CAUSE
--------------------------------------------------------------------------
The -$5.3169 was caused, in order of contribution:

  1. MODEL BEHAVIOUR. Six consecutive sampled EXTENDs at p=0.6786 into a
     trending market, filling by adverse selection. This built 100% of the
     position with no cap involvement.
  2. UNFILLABLE COMPLEMENT. Four maker attempts at L5's touch, zero fills,
     three of them above par. This is the population-wide 9-18% COMPLETE
     fill problem, not a defect specific to this market.
  3. MARKET HALT. The book stopped being two-sided at 21:02:24 and was
     halted until resolution, closing the last exit at 21:01:35.
  4. SAFETY-SHELL DISTORTION: contributed NOTHING to this loss, and on the
     measured counterfactual reduced it by stopping further accumulation.
"""
from __future__ import annotations

from decimal import Decimal as D

# --- 1. the reconciled Sep 8 ledger, as mirrored by the campaign log -------
# (write time UTC, market, venue-confirmed realized USD)
SEP8_REALIZED: tuple[tuple[str, str, str], ...] = (
    ("01:13:01", "aec-setkameua-malden-stoole-2026-09-08", "-0.4141"),
    ("06:06:18", "aec-setkameua-vervas-kohboh-2026-09-08", "-1.5778"),
    ("11:58:06", "aec-atp-valabo-juamar-2026-09-08", "1.4610"),
    ("13:10:09", "aec-setkameua-zhyole-strole-2026-09-08", "-1.6920"),
    ("16:15:18", "aec-setkameua-skaser-khavit-2026-09-08", "-3.9453"),
    ("16:20:43", "aec-setkamemd-turvic-viradr-2026-09-08", "-2.3119"),
    ("16:21:49", "aec-setkameua-savvol-levana-2026-09-08", "-2.1813"),
    ("17:07:49", "aec-setkamecz-senjak-mezond-2026-09-08", "-1.0643"),
    ("17:28:41", "atc-spl-ett-fai-2026-09-08-draw", "-1.5447"),
    ("21:19:47", "aec-wta-kaique-annrog-2026-09-08", "-5.3169"),
)
DAILY_LOSS_STOP_R5F = D("-15.00")
LOG_COVERAGE_GAPS_UTC = ("06:27-10:40", "14:28-16:00")


def sep8_total() -> D:
    return sum((D(r) for _, _, r in SEP8_REALIZED), D("0"))


def sep8_running() -> list[tuple[str, str, D, bool]]:
    """(t, market, cumulative, would the stop have fired at this row)."""
    out, tot = [], D("0")
    for t, slug, r in SEP8_REALIZED:
        tot += D(r)
        out.append((t, slug, tot, tot <= DAILY_LOSS_STOP_R5F))
    return out


def stop_was_correct() -> bool:
    return sep8_total() <= DAILY_LOSS_STOP_R5F


def margin_needed_to_overturn() -> D:
    """Hidden rows would have to sum to MORE than this (positive) to make
    the stop wrong."""
    return (sep8_total() - DAILY_LOSS_STOP_R5F).copy_abs()


# --- 2. the kaique-annrog case --------------------------------------------
CASE = "aec-wta-kaique-annrog-2026-09-08"
CASE_REALIZED_USD = D("-5.3169")
CASE_COST_BASIS_USD = D("5.3169")
CASE_NO_UNITS = D("15.74")
CASE_YES_UNITS = D("0")
CASE_MATCHED_PAIRS = D("0")
CASE_AVG_NO_PX = D("0.3377954256670902160101651842")
CASE_RESOLUTION_SIDE = "POSITION_RESOLUTION_SIDE_LONG"
CASE_STATE = "p25-45c|tte>10m|imb10-50"
CASE_STATE_N = 298
CASE_RELATION_RANKED = (("extend", D("0.6786")), ("complete", D("0.3214")),
                        ("open", D("0.0")))
# the two rows on which the shell overrode the model, by the shell's own labels
CAP_CONVERSIONS = (
    {"decision_idx": 17, "t_utc": "2026-09-08T20:58:25.621834+00:00",
     "sampled": "extend", "acted": "complete",
     "would_act_without_safety_cap": "extend", "cap_binding": ("spend_cap",),
     "divergence": "safety_adaptation:spend_cap->complete",
     "residual_before": D("-15.74"), "spent_before": D("5.3169")},
    {"decision_idx": 20, "t_utc": "2026-09-08T21:01:34.049860+00:00",
     "sampled": "extend", "acted": "complete",
     "would_act_without_safety_cap": "extend", "cap_binding": ("spend_cap",),
     "divergence": "safety_adaptation:spend_cap->complete",
     "residual_before": D("-15.74"), "spent_before": D("5.3169")},
)
# every complement attempt: (t, yes_px, units, rest_s, filled, pair_at_touch)
COMPLETE_ATTEMPTS = (
    ("20:58:57", D("0.61"), D("4.09"), 31.435, False, D("0.9478")),
    ("20:59:29", D("0.70"), D("3.57"), 31.526, False, D("1.0378")),
    ("21:01:34", D("0.74"), D("3.37"), 31.978, False, D("1.0778")),
    ("21:02:06", D("0.96"), D("2.60"), 16.488, False, D("1.2978")),
)
VENUE_REJECTED_NONCROSSING_ORDERS = ("CCEDBG49PR3X", "CCEDKC4MYR3X",
                                     "CCEDP8GDMR47")
HALT = {"last_two_sided_utc": "2026-09-08T21:01:35",
        "last_two_sided_book_yes": (D("0.7400"), D("0.7500")),
        "not_two_sided_utc": "2026-09-08T21:02:24",
        "halted_from_utc": "2026-09-08T21:04:35",
        "halted_until_utc": "2026-09-08T21:17:35",
        "venue_resolution_utc": "2026-09-08T21:18:01"}


def residual_was_built_before_cap_bound() -> bool:
    """The load-bearing fact: the cap first bound at the FINAL position."""
    return all(c["residual_before"].copy_abs() == CASE_NO_UNITS
               for c in CAP_CONVERSIONS)


def complete_fill_count() -> int:
    return sum(1 for *_, filled, _ in COMPLETE_ATTEMPTS if filled)


def complete_rest_seconds() -> float:
    return round(sum(a[3] for a in COMPLETE_ATTEMPTS), 3)


def below_par_attempts() -> int:
    return sum(1 for *_, pair in COMPLETE_ATTEMPTS if pair < D("1"))


# --- 3. the cap counterfactual --------------------------------------------
PLANNED_USD_PER_ACTION = D("2.4949")
OBSERVED_EXTEND_FILL_USD = D("1.268")     # 4.09 contracts x ~0.31
ACCUMULATION_FILL_RATE = D("0.5714")      # 4 of 7 NO orders filled


def extends_permitted(cap_usd: D, spent: D = D("5.3169")) -> int:
    """How many further EXTENDs the cap rule `spent + planned > cap` allows,
    assuming each one fills at the last observed NO price."""
    n = 0
    while spent + PLANNED_USD_PER_ACTION <= D(str(cap_usd)):
        spent += OBSERVED_EXTEND_FILL_USD
        n += 1
    return n


def counterfactual_loss(cap_usd: D, apply_fill_rate: bool = False) -> D:
    """Loss if the extra units are bought and, as happened, never paired.
    Every NO unit paid $0 at resolution, so incremental loss == incremental
    spend. NOT an observed outcome: a counterfactual executable opportunity."""
    extra = OBSERVED_EXTEND_FILL_USD * extends_permitted(cap_usd)
    if apply_fill_rate:
        extra *= ACCUMULATION_FILL_RATE
    return (CASE_REALIZED_USD - extra).quantize(D("0.0001"))


# the taker exit that existed, priced from the last two-sided book.
# UNVERIFIABLE IN SIZE: BBO price was logged, depth was not.
TAKER_EXIT = {"utc": "2026-09-08T21:01:35", "no_bid": D("0.25"),
              "units": CASE_NO_UNITS, "proceeds_usd": D("3.9350"),
              "loss_usd": D("-1.3819"), "saving_usd": D("3.9350"),
              "requires": "taker sell; disabled by owner rule (maker-only)",
              "verifiable": False,
              "why_not_verifiable": "the BBO price was recorded, its depth "
                                    "was not; book_capture was null on every "
                                    "order row in this market"}

# --- 4. the population -----------------------------------------------------
LOSING_MARKETS_N = 18
LOSING_MARKETS_USD = D("-37.2696")
CHAIN_MARKETS_N = 11
CHAIN_MARKETS_USD = D("-27.5940")
CHAIN_CAP_BOUND_AFTER_RESIDUAL_BUILT = 8
CHAIN_RESIDUAL_GREW_AFTER_CONVERSION = 3
LARGEST_LOSS_HAD_NO_CAP_CONVERSION = ("asc-cfb-wisc-nd-2026-09-06-pos-10pt5",
                                      D("-6.2855"))
GROW_SIDE_FILLS = (31, 130)      # filled, submitted
CLOSE_SIDE_FILLS = (16, 174)


def chain_share_of_loss_dollars() -> D:
    return (CHAIN_MARKETS_USD / LOSING_MARKETS_USD).quantize(D("0.0001"))


def fill_asymmetry() -> D:
    g = D(GROW_SIDE_FILLS[0]) / D(GROW_SIDE_FILLS[1])
    c = D(CLOSE_SIDE_FILLS[0]) / D(CLOSE_SIDE_FILLS[1])
    return (g / c).quantize(D("0.01"))


# --- the recommendation this module supports -------------------------------
RECOMMENDATION = "DO_NOT_RESTART_YET"
RECOMMENDATION_IS_ADVISORY_ONLY = True
AUTHORISES_RESTART = ()
CAUSE = ("model behaviour (sampled EXTEND persistence into adverse "
         "selection) + unfillable complement + market halt; the safety "
         "shell contributed nothing and on the counterfactual reduced the "
         "loss")
