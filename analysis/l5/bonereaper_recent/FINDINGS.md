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

## Spot / price-to-beat / book discrepancy test, 2026-11-17..20

Script `analysis/l5/btc5m/bonereaper_spot_discrepancy.py`, result
`BONEREAPER_SPOT_DISCREPANCY_RESULT.txt`; pulls reproduced by
`harvest_spot_and_tape.py` (the 27 MB all-trader tape is not committed).
957 windows with spot; 42,025 of his taker buys ($1.85M), 4,570 maker
buys ($0.27M), 1.08M buys by other takers ($16.2M).

- 87% of his dollars are TAKER buys. Same window, same price band, his
  taker buys earn 0.5 c/share LESS than other takers' (-0.0050 and
  -0.0057 on the two halves; intervals reach zero). He has no selection
  advantage over the crowd he trades beside.
- Every taker, him included, earns +4 to +9 c/share before fees buying
  at 0.50-0.85 and loses 3 to 9 c buying below 0.50, on both halves.
- A fair value built from Gemini spot does not explain his entries and
  is a worse forecast than the market price (buys where spot said
  "cheap" lose 3-6 c). Gemini trades every ~3 s; 73% of buys have no
  tick in the prior 3 s. The test of a sub-second spot lead is therefore
  NOT answered. Missing source: a tick-level history of the settlement
  feeds (Chainlink BTC/USD stream there, CF Benchmarks BRTI here) or of
  Binance/Coinbase trades; none is reachable from this environment, and
  no order-book history exists publicly for either venue.
- Polymarket's last print does not lead our quotes in a usable way:
  buying here when it is above our ask loses on both halves.

## Candidate that held on other days: mid-priced favourite on our venue

`favourite_band_holdout.py`, `FAVOURITE_BAND_HOLDOUT_RESULT.txt`. Rule:
a side's ask is 0.50-0.70 at a ~10 s sample; buy at the next sample's ask.
Found on 11-17..20 (+0.058 before fee, +0.037 after). On 11-11..16, not
used to find it (1,387 windows): +0.034 [+0.021,+0.047] before fee,
+0.013 [-0.000,+0.026] after our 0.095 fee. Positive before fees on 9 of
10 days. Not established after fees. Unknown: size available at the ask
(the recorder logs none) and fills faster than 10 s.

## CORRECTION (2026-11-20): the mid-priced favourite candidate is withdrawn

The hold-out table above averaged each window's MEAN return, counting a
window with one signal the same as a window with twenty-five. Windows
where the signalled side loses stay in the 0.50-0.70 band longer (11.4
signals against 8.4), so that average favours winners. It is not what
any buying rule earns.

`favourite_band_weighting_check.py`, same signals, prices and fee:
- every signal bought, per share after fee: -0.020 [-0.037,-0.004] on
  11-17..20, -0.037 [-0.050,-0.023] on 11-11..16, -0.030 over ten days.
- one buy per window at the first signal (`favourite_band_one_entry.py`):
  -0.031 per window [-0.050,-0.011] over 2,378 windows.
- no price x time cell inside the band is positive on the hold-out days
  (`favourite_band_where.py`).

Maker against taker for the same signal (`favourite_band_execution.py`,
same per-window averaging, so only the ordering is usable): the resting
bid is worse than the taker under every hold time and both fill
assumptions.

The same per-window averaging was used in the selection test and the
spot-discrepancy tables. Their PAIRED results (his side minus the
favourite, his buys minus other takers in the same window and band) are
differences inside a window and stand. Their LEVELS, including "every
taker earns +4 to +9 c at 0.50-0.85 on Polymarket" and the positive
favourite lines on our venue, carry this bias and should not be relied on.

## Weighting audit

See `analysis/l5/btc5m/WEIGHTING_AUDIT_20261120.md`: several levels above are corrected there; that file supersedes them.

## Inventory trajectory (2026-11-20): what drives his concentration

`inventory_trajectory.py` -> `INVENTORY_TRAJECTORY_RESULT.txt`,
10-second path of the 24 windows in `trajectory_20261120_1900_2100.csv`,
`post_fill_drift.py` -> `POST_FILL_DRIFT_RESULT.txt`. All numbers are
dollars summed over fills (no per-window averaging).

- He does not defend a position. When the side he holds more of is still
  the favourite, ~90% of new dollars go to it. When it has become the
  underdog, only 25-43% do: he moves to the new favourite.
- His choice of side beats a price-and-minute-only rule by +3.8 c per
  dollar [+2.4,+5.1] on 11-19/20 when both are scored at the price 2 s
  before his fill (+10.6 c at the price 10 s before). He buys the side
  that is in the act of moving up.
- At the prices he actually paid, the same fills return -1.0 c per dollar
  on 11-19/20 (-0.4 c on 11-17/18). He pays 0.9 c a share above the
  2-s-old price and 3.8 c above the 10-s-old price.
- After his fills the price does not come back: it sits +0.5 c a share
  above what he paid at 5, 15 and 30 s (both halves, intervals exclude
  zero). So his buying carries a small real short-horizon lead, about
  half a cent, smaller than the venue fee he pays.
- Profitable vs losing windows at 60 s: profitable ones are already net
  long the favourite (+0.27 of shares held, 11-19/20); losing ones are
  balanced (-0.02).

## Settlement index against our quotes: causal rule test (2026-11-20)

`analysis/l5/btc5m/index_lead_rule.py` -> `INDEX_LEAD_RULE_RESULT.txt`.
One-second settlement index (public gateway) and the recorder's full
2-second quotes, 2026-11-17..20; model and threshold fitted on 11-17/18
(576 windows), scored on 11-19/20 (515 windows) with the shared ledger.
Index used only when at least 2 s older than the quote.

- The quote is the better forecast: Brier 0.1625 for the quote mid
  against 0.1725 for the index model on the held-out days.
- Quotes do not move toward the model afterwards: where the model is 3 c
  or more above the quote, the mid moves +0.0001 in 6 s and +0.0002 in
  10 s (held-out).
- The rule (one share per window, buy when model - ask - fee >= 0.15,
  order matched 2 s later, missed if the ask has risen): 382 windows
  traded of 515, 261 orders missed, NET -$6.03, per window run -0.012
  [-0.045,+0.022]; before fees +$1.49. At 4 s: -$3.15. Even matched at
  the same instant (not executable): -$0.52.
- Conclusion: the 60-second-average index does not lead our quotes. Our
  quotes already price something faster than it. No event-driven index
  controller is justified by this data.
