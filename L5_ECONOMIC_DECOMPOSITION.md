# L5's economic mechanism, consolidated — and what transfers at $5

One decomposition, reconciled from existing reports. No new study. Sources named per line.
**Documented transactions and reconstructed estimates are separated throughout.**

## VERDICT

**L5's mechanism is 83.3% incentives on a rate-per-turnover basis. It is transferable in KIND
but not in SIZE: $480/day is 0.97x L5's own daily turnover, needs ~$907 of working capital at
his observed recycling, and our $5 authorization caps us at $5.10/day even if we matched his
rate exactly — 94x short. And at our own measured trading edge the same turnover yields
−$24.66/day, so capital added before the trading edge reaches ~0 scales a loss.**

**No economically transferable behaviour is demonstrated at our capital.** The honest answer to
"which parts transfer" is: the maker-rebate RATE already does, and it is worth $5.10/day at our
ceiling. Nothing else is demonstrated.

## 1. Settlement trading P&L before incentives — RECONSTRUCTED

| | |
|---|---|
| turnover | **$7.4M** (documented, `src/pmus_incentive_audit.py`) |
| trading edge, net of residual and timing | **0.185% of turnover = $13,687** |
| share of his money | **16.7%** |
| per day over the 165-day frozen corpus | **$83/day** |

**An inconsistency I previously mis-read and now reconcile.** The source says *"maker rebates
+$33,929, liquidity rewards +$26,956, taker rebates +$7,323 of +$81,895 total"*. I earlier read
$81,895 as incentives-only. It is **total money**: the three incentive lines sum to $68,208,
which is **83.3%** of $81,895, and the residual $13,687 is **0.185%** of $7.4M — matching the
separately stated "0.18% of turnover". **Both figures in that line hold and are consistent.**
My earlier "$81,895 of incentives, $496/day of incentives" was wrong; incentives are $413/day.

## 2. Incentives by venue and verified credit status

| venue | line | amount | status |
|---|---|---|---|
| **PMUS — canonical L5** | maker rebates | **$33,929** (0.459% of turnover) | documented in the audit header |
| | liquidity rewards | **$26,956** (0.364%) | documented |
| | taker rebates | **$7,323** (0.099%) | documented |
| | **total** | **$68,208 = 83.3% of his money** | |
| **PMUS — OUR account** | credited maker rebates | **$0.42 over 25 prints** | **VENUE-CONFIRMED** |
| | liquidity rewards | **$0.00, ever** | **VENUE-CONFIRMED ZERO** |
| | Oct 25 campaign | estimate $0.0048, **credited $0.00** | `UNVERIFIED_NO_VENUE_CREDIT_RECORD` |
| **Gemini — our account** | credits | **$0.51** | venue-confirmed, different venue |
| **Kalshi — our account** | all incentives | **$0.00** | `ORDER_ENTRY=False`; never placed one order |

Rate comparison: **our maker-rebate capture is 0.460% of turnover against his 0.459%** — we
match him on the one component we actually collect. **Liquidity rewards are 0.364% for him and
exactly zero for us, forever**, despite the programme covering 96–100% of PMUS markets.

**A constraint on even that match:** at our $0.80 fixed notional the credit is cent-rounded and
**dies above p≈0.49** (raw $0.0050 at p=0.50 rounds to $0.00). On our 7 venue-confirmed
openings, **4 of 7 would earn $0.01, 3 would earn nothing — $0.04 in total.**

## 3. Sequential inventory management and weighted-average acquisition cost — DOCUMENTED

From `analysis/l5/inventory_trajectory/trajectory.json`, his 527 windows / 62,020 fills:

| checkpoint | matched pair VWAP | matched qty | imbalance |
|---|---|---|---|
| after 2 fills | 0.9900 | 0.00 | 1.0000 |
| after 10 | 0.9938 | 50.24 | 0.2777 |
| after 100 | **0.9828** | **827.48** | 0.0761 |

He grinds the matched VWAP down ~1.1 cents between fills 10 and 100 while accumulating 827
matched contracts. At 0.9828 that is `827 x 0.0172 = $14.23/window` of gross matched-pair
economics — **and this is the mechanism behind the 0.185% trading edge, not a separate one.**
It is an OUTCOME of ~100 fills, which is the finding already recorded: our 0.96 cap is a
precondition built out of this outcome.

## 4. Capital, turnover, fill frequency — L5 vs our actual PMUS execution

| | L5 | ours (actual) | ratio |
|---|---|---|---|
| median window capital | $937.68 | $5.00 authorized | **188x** |
| turnover / day | **$44,848** | ~$1–20 actual; **$460.80 ceiling** | **97x** on the ceiling |
| recycles / day | 47.8 | ≤96 (1/window) | — |
| fills / window (median) | **119** | 0.25–0.50 actual; **6** ceiling | **20x** on the ceiling |
| clip as % of capital | 0.25–0.41% | 13.7% realized, 16% gate | **34x coarser** |
| residual as % of capital | 3.53–4.58% | 44–50% limit | **9.6x looser** |
| **trading edge** | **+0.185%** of turnover | **−6.458%** on the PMUS lane | **sign** |

## 5. What transfers at our capital — the arithmetic

His blended rate is **1.107% of turnover**. The mechanism is a **rate**, so income scales with
turnover, and turnover = capital x recycles.

| | |
|---|---|
| $480/day at 1.107% needs | **$43,373/day of turnover** |
| that is | **0.97x L5's OWN daily turnover** |
| capital required at his 47.8 recycles/day | **~$907** |
| our ceiling: 6 prints x $0.80 x 96 windows | **$460.80/day turnover** |
| at his rate that earns | **$5.10/day** |
| **shortfall** | **94x** |

**$480/day is not a margin on our operation — it is L5's operation at L5's scale.**

And the sign problem dominates the scale problem:

> at our ceiling turnover of $460.80/day, `1.107% incentive − 6.458% trading` = **−$24.66/day**.

**Adding capital before the trading edge reaches ~0 scales a loss, not a profit.**

## 6. The single highest-impact transferable behaviour — NONE IS DEMONSTRATED

Stated explicitly as the owner requires.

The largest *candidate* is **liquidity-reward capture**: 0.364% of turnover for L5, one third of
his incentive income, and **$0.00 for us ever**, on a programme covering 96–100% of PMUS
markets. It is the only line where he is large and we are at exactly zero.

**But it is not demonstrated, for two specific reasons:**

1. **Eligibility is unverified for our account.** We have never been credited a liquidity
   reward. Whether we qualify, and at what resting size, is unread — the audit module reads
   `/v1/incentives/earnings` and `/v1/incentives`, and this container cannot reach either.
2. **It is a rate on turnover like the others**, so at $460.80/day of ceiling turnover a perfect
   0.364% capture is **$1.68/day**. Even a complete success on the largest missing component is
   worth under two dollars a day at our capital.

**What real evidence is missing**, precisely:

- **venue-read confirmation** of whether our account is eligible for, and has ever been scored
  by, the PMUS liquidity-reward programme, and the resting size it requires
- **a trading edge at or near zero.** Every path to the target multiplies turnover by a rate; at
  −6.458% the multiplier is negative. This is the precondition, and it is unresolved: our best
  evidence is 7 venue-confirmed windows totalling −$2.1448.
- **working capital of ~$400–900** to make any rate meaningful. This is an owner decision and
  not something to infer from a model.

## 7. One next executable action

**Run `src/pmus_incentive_audit.py` read-only against our own account** — `/v1/incentives/earnings`,
`/v1/portfolio/activities`, `/v1/incentives`. It places no orders, moves no capital, needs no
risk change, and answers the one missing fact that is cheap to obtain: whether the
liquidity-reward programme has ever scored us and what size it requires.

It cannot run from this container (outbound to `polymarket.us` returns 000); it must run where
the credentials already are. **That is the whole action — no new service, no strategy, no
activation.**

It will not produce $480/day. It will tell us whether the one component where we sit at zero is
available at all, which is the last unmeasured piece of the decomposition above.

**No production change, no new service, no risk-limit increase, no live activation. Cap 0.96,
`armed=false`, `shadow_lock=true`, `campaign_id=null`.**

---

# ADDENDUM — the seven windows separate on COMPLETION vs STRAND, not price

A peer session withdrew its skew null and made a structural point that is testable on my own
seven venue-confirmed windows. It holds, and it kills my price hypothesis without needing
their tape.

| window | p | qty | acq cost = q·p | realized | loss/acq | character |
|---|---|---|---|---|---|---|
| 1245z | 0.57 | 1.40 | 0.7980 | −0.2940 | 0.368 | partial |
| **1300z** | 0.63 | 1.26 | 0.7938 | **−0.7938** | **1.000** | **exact full strand** |
| 1315z | 0.46 | 1.63 | 0.7498 | +0.0815 | — | pair margin |
| 1330z | 0.33 | 1.29 | 0.4257 | +0.2130 | — | pair margin |
| 1345z | 0.58 | 1.50 | 0.8700 | −0.7206 | 0.828 | partial |
| 1400z | 0.23 | 1.11 | 0.2553 | +0.0555 | — | pair margin |
| **1945z** | 0.44 | 1.56 | 0.6864 | **−0.6864** | **1.000** | **exact full strand** |

**Two of the four losses are EXACTLY the first-leg acquisition cost** — full strands settling
worthless. The other two are partials.

**Losses are not monotone in price:** ordered by p they are 0.44 → 0.6864, 0.57 → **0.2940**,
0.58 → 0.7206, 0.63 → 0.7938. The *smallest* loss sits at p=0.57. **"Dear loses more" is false
within my own seven.**

**Losses are approximately the NOTIONAL, not price-scaled.** Three cluster at 0.7938 / 0.7206 /
0.6864 (mean **0.7336**) against the $0.80 opening notional. Since clip = 0.80/p, a full strand
costs ~$0.80 *whatever* the entry price.

**Winners are pair margins, not settlement wins.** All three are small (+0.0555, +0.2130,
+0.0815). A winning residual at p=0.44 on 1.56 contracts would pay **+$0.8736** — none is
anywhere near it. So the winners completed pairs and took the margin; the losers stranded and
lost acquisition cost.

**So all three price readings — my price effect, my rebate artifact, their skew null — were
looking at the wrong variable.** The separator is **completion versus strand**, and price is
near-irrelevant to the magnitude because the notional is fixed. That is the one diagnosis that
survived today, reached independently from the venue-confirmed side.

It does not change the verdict above: the mechanism is a rate on turnover, and at $5 the
ceiling is $5.10/day against his rate and −$24.66/day against ours.

**Ledger closed: one diagnosis, two process findings (the three unexercised gates finding is
JOINT — the `MAX_UNMATCHED` attribution and the pattern-spotting came from different
sessions), zero surviving economic estimates, thirteen withdrawals. Both survivors predate
today and were already in committed files.**
