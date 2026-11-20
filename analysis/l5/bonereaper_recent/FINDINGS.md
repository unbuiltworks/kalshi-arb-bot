# Bonereaper's reported profit, reconciled (2026-11-20)

Wallet `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30` on Polymarket (international). All figures
are the platform's own, pulled read-only from its public data API on 2026-11-20 ~19:40Z:
`platform_user_pnl_all_20261120.json` (cumulative daily series), `incentive_credits_20261120.json`
(all 440 rebate and reward credits), `activity_20261117_20261120.jsonl.gz` (62,920 activity
rows). Scripts: `reconcile_platform_pnl.py`, `recent_fills_by_price.py`, `fetch_activity.py`.

## Lifetime, 2026-03-25 to 2026-11-20 (`PLATFORM_PNL_RECONCILIATION.txt`)

| Component | USD | % of volume |
|---|---|---|
| Volume | 166,252,197 (9.34M trades) | |
| Trades, before fees | +1,423,717 | +0.856% |
| Fees charged | −2,480,161 | −1.492% |
| Fees refunded | +906,915 | +0.546% |
| **Market P&L after fees** | **−149,529** | −0.090% |
| Taker rebate | +494,310 | +0.297% |
| Maker rebate | +161,992 | +0.097% |
| Liquidity rewards + referral | +4,955 | +0.003% |
| **Total** | **+511,728** | +0.308% |

The platform's identities hold on all 241 points. Its leaderboard figure for the wallet reads
−$2,302 "pnl" whatever window is asked for, and so does not show the incentives.

## What changed over time

- March–May: maker rebates only ($1k–26k a month); market P&L swung between +$117k and −$101k.
- From June: taker rebates appear and grow to $157k in August; maker rebates fall to $497 in
  November. Fee refunds stopped after July (cumulative unchanged since).
- Every month since June: market P&L after fees is negative and the total is positive.
- Last 30 days: volume $9.18M; trades before fees +$110,580 (+1.21%); fees −$136,997
  (−1.49%); market P&L −$26,418; taker rebate +$54,938 (40% of fees); total +$29,629 (+0.32%).

## The most recent days, fill by fill (`RECENT_FILLS_BY_PRICE.txt`)

2026-11-17 to 11-20: 55,259 fills in 1,031 BTC 5-minute markets, $2.41M. 100% BTC 5m, 100%
buys, 48% of dollars at 0.90 or above and 22% at 0.99 or above. Fees implied by treating
every fill as a taker fill (1.49% of dollars) equal what the platform charged, so he is now
almost entirely a taker.

| | % of dollars | 95% over markets |
|---|---|---|
| Before fee | +1.15 | [+0.24, +1.96] |
| After Polymarket's 0.07 taker fee (actual) | −0.34 | [−1.26, +0.47] |
| With his 40% taker rebate | +0.25 | [−0.67, +1.06] |
| Same fills at Polymarket US's 0.095 coefficient, no rebate | −0.88 | [−1.80, −0.07] |

No single price band is distinguishable from zero on its own. Fills between 0.30 and 0.50
lose after fees (−5.4% [−10.4, −0.6]).

## Answers to the four questions

1. Missing markets: no. The platform series covers everything, and market P&L after fees is
   −$149,529.
2. Incentives: yes. $661,257 of rebates, rewards and referral income, plus $906,915 of fee
   refunds early on, turn that into +$511,728.
3. Accounting: the earlier August sample was three days of an eight-month record and its
   "settled net" was already after fees; it is consistent with this.
4. Strategy: it changed. A maker collecting maker rebates through May; a taker buying mostly
   high-priced favourites and collecting a volume-tier taker rebate since June.

## What transfers to Polymarket US

- His trades have a positive result before fees of about 0.9–1.2% of dollars. That is real and
  measured.
- Polymarket's taker fee takes about 1.5%. The taker rebate gives back 40% of it at his volume.
- Polymarket US charges a higher coefficient (0.095) and pays this account no taker rebate
  below $250k of 30-day taker volume (3% there, 30% at $50M). The same fills would return
  about −0.9% of dollars.
- It is a taker strategy. L5 is maker-only by standing instruction.

Fee and rebate rules from a secondary read of Polymarket's documentation (the docs site is
not reachable from this environment): the taker rebate program started 2026-05-28, tiered on
30-day weighted volume, 44–50% at the top tiers; new tiers and a lower crypto fee take effect
2026-11-20 23:59 UTC. Not verified verbatim.

Not established: whether equivalent asks exist on Polymarket US at the same prices and sizes;
how his fills split between maker and taker (inferred from fees only); the deposits and
withdrawals behind the account (the platform returns none).

## Fee-adjusted taker economics on Polymarket US (2026-11-20 ~20:00Z)

`analysis/l5/btc5m/TAKER_FEE_ADJUSTED_RESULT.txt` (187 PMUS windows, asks as quoted, taker fee
0.095 × p × (1 − p), no rebate) and `LATE_FAVOURITE_TAIL_CHECK_RESULT.txt` (Bonereaper's 1,334
real markets). No size data exists locally for PMUS asks.

- Lifting the offer is negative or indistinguishable from zero in every price band over the
  whole window; favourites priced 0.90–0.95 are the worst (−4 to −5 cents a share).
- Cells positive on one half of the windows do not hold on the other (−0.6 and −8.0 cents).
- One state is positive in both halves: an ask at or above 0.97 with at most 60 s left won
  82 of 82 windows (+2.0 and +2.3 cents a share); any ask at or above 0.80 with at most 30 s
  left won 34 of 34.
- That is too few to bound the tail. At 0.975 the gain is about 2.3 cents and a loss about
  97.7, so break-even is a loss rate near 2.3%. Bonereaper's real buys at or above 0.97 with
  at most 60 s left lost 3 of 246 markets (1.2%, at most 3.5%): about +0.05 cents a share at
  the PMUS fee, −2.3 cents at the bound. At or above 0.99 with at most 60 s left, 3 of 183
  lost, which is negative.
- With at most 30 s left and 0.97 or more, he lost 0 of 111 and PMUS showed 0 of 34. Together
  that bounds the loss rate below about 2.5%; break-even there is about 1.2%. Not established.
- Attainable rebates at our volume: none.

Fee change check: Polymarket's own market records at 20:00Z show BTC 5-minute markets for
2026-11-21 still at rate 0.07 with a 0.2 maker-rebate share, under a fee type now named
`crypto_btc_5m_15m_fees`. The reported change is not confirmed by the records.

NO PROMOTION. The only candidate is a taker buy of a 0.97+ favourite in the last 30 s. What it
needs: roughly 250 more PMUS windows with the ask AND its size in the last 30 s, to bound the
loss rate under 1.2% and show the offer is there to be lifted.

## Late-favourite taker candidate on 2,572 PMUS windows: REJECTED (2026-11-20)

`analysis/l5/btc5m/LATE_FAVOURITE_RECORDER_RESULT.txt`; data
`analysis/l5/btc5m/evidence/recorder_late_quotes_20261111_20261120.jsonl.gz` (37,957 quotes
from the existing public recorder's logs, final 30 s of 2,574 windows, 2026-11-11 21:00Z to
2026-11-20 20:00Z; six windows on 11-13 06:00-06:30Z are absent from the logs). 2,572 usable,
2,571 with a known outcome; the two outcome readings agree on all 2,543 windows that have
both. Out of sample = the 2,385 windows after the 187 the candidate was found on.

Candidate, fixed beforehand: ask at or above 0.97 with at most 30 s left. Taker fee
0.095 × p × (1 − p), no rebate. No sizes exist for these markets' final 30 s.

| Out of sample | Opportunities | Filled | Lost | Loss rate (95% upper) | Net per share | At the upper bound |
|---|---|---|---|---|---|---|
| At the trigger quote | 359 (15.1% of windows) | 359 | 5 | 1.39% (3.22%) | +$0.0042 | −$0.0140 |
| One quote later (~2 s) | 359 | 247 | 5 | 2.02% (4.65%) | +$0.0044 | −$0.0221 |
| Two quotes later (~4 s) | 359 | 153 | 5 | 3.27% (7.42%) | −$0.0032 | −$0.0451 |

- On the discovery windows it was 36 of 36 wins, +2.0 cents a share. Out of sample it is
  +0.4 cents, a fifth of that.
- 31% of opportunities have no ask left after ~2 s and 57% after ~4 s. The ones that remain
  include all five losses.
- Break-even loss rate at these prices is about 2.4%; observed 2.0% at a 2 s delay.
- By date at a 2 s delay: six of nine days positive, three negative; the last two days lost
  (2 of 20 on 11-19, 1 of 21 on 11-20). 08-16 UTC is negative.
- Dollars: at a 2 s delay, 247 fills in 2,385 windows at +$0.0044 a share is +$0.00046 per
  share per window, about $0.13 a day per share of clip. One loss costs $0.98 a share: about
  40 average wins.
- Tighter thresholds looked at afterwards (0.98: 1 loss in 197 at a 2 s delay, +1.4 cents)
  are not validated: chosen after seeing this data, and still negative at their bound.

The candidate fails on its own terms: no defensible positive net expected value after fees
and delay. It is rejected; no taker release is proposed.

## Selection test on our venue, 2026-11-17..20 (1,015 shared windows)

Script `analysis/l5/btc5m/bonereaper_selection_test.py`, result
`analysis/l5/btc5m/BONEREAPER_SELECTION_TEST_RESULT.txt`. His Polymarket
buys (side, 10-second bucket, dollars) replayed at OUR recorded asks and
settled on OUR outcome. Discovery 11-17/18 (573 windows), validation
11-19/20 (420 usable).

- 84% of his dollars go to the side that is the favourite here; 71% to a
  favourite whose bid has just risen.
- His side vs the plain favourite at the same moments (paired per window,
  ~10 s delay): -0.017 on discovery, +0.004 on validation, interval spans
  zero on validation. No transferable side-selection signal.
- His moments vs buying the favourite at every point: -0.004 and -0.009.
  His timing does not help here either.
- After our taker fee, with ~10 s delay, validation days: his side -0.011,
  favourite at his times -0.015, favourite every point -0.006 per share;
  every interval includes zero or is negative. The small positives on the
  discovery days did not repeat.
- His underdog buys lose 3 to 7 cents a share here before fees on both
  halves.
- This is a transfer test of his observable entries, not a reconstruction
  of his signal: his decisions were made on Polymarket's book, and our
  quotes are sampled every ~10 s with no sizes.
