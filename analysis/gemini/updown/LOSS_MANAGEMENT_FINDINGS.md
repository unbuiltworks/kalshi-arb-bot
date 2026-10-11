# BTC5m loss-management harness -- EXPLORATORY / IN-SAMPLE / NOT VALIDATION

`scripts/gemini_btc5m_loss_management.py`. Research only. It sends nothing,
arms nothing, reads no sealed partition, and changes neither frozen analyzer
(f2800e3e...960e90b, b2f6d50a...142f9bc6), the recorder, nor the authorized
underdog cohort. No live SELL, hedge, added inventory or recovery sizing is
authorized by its existence.

## Methodology, ported from the real file

`_hourly_stop_backtest.py` is in unbuiltworks/kalshi-longshot-fader
(c004adca), not in this repository. I first searched here, said so, and
built against the stated principles; the owner pointed me at the right repo,
I cloned and read it, and three of its conventions turned out to be STRICTER
than my first pass:

1. **Trigger on the adverse extreme, fill worse and later.** The original
   triggers on a minute's `yes_ask` HIGH and fills at that minute's CLOSE --
   "the price we act on is worse than the one that tripped the rule". This
   tape has one observation per checkpoint rather than an OHLC bar, so the
   faithful analogue is: trigger at the breaching observation, fill at the
   NEXT observation's liquidation price, never better than the trigger. My
   first version filled at the triggering touch, which flatters the stop.
2. **A no-offer is not a price.** The original drops 0.9999 asks with no
   book; a 0.98/0.99 level of size 1 here is the same artifact and is now
   refused rather than read as a 98c quote.
3. **An explicit population ledger.** Its `Population` class counts raw,
   excluded-by-reason and included and verifies they reconcile. Reproduced:
   133 raw, 56 included, 77 excluded (`entry not executable both sides at
   the unit, or a tie`), reconciles true.

Its **saved vs cost** decomposition is also adopted. Its thresholds are NOT:
15-50c is calibrated to SELLING yes at ~7c, a different trade in a different
direction. The grid here is a preregistered sweep and 30c/60c have no
special status.

## What the corpus supports, before any policy number

The only pre-existing BTC5m executable path is the tte ladder: 11 points per
window starting at **tte=120s, i.e. T+180**. It is a genuine causal path,
and it is NOT the early window. **Entries at T+15..T+120 cannot be studied
here at all**, so nothing below transfers to the window the live cohorts
trade. 56 of 133 settled windows have a usable entry. The sell-side fee is
**UNRECONCILED** -- this programme has never sold on this venue, so every
exit fee is the published formula, not a charged one.

## EV(HOLD) vs EV(EXIT) vs EV(HEDGE) vs EV(L5_INVENTORY)

| policy | EV/entry | EV/all-window | avg loss | worst | max DD | winner retention | false stop |
|---|---|---|---|---|---|---|---|
| **A HOLD** (control) | **-0.1934** | -0.0814 | -0.4728 | -0.84 | **-12.52** | 100% | n/a |
| B EXIT 3c | -0.3234 | -0.1362 | -0.4683 | -0.84 | -18.72 | 52.9% | 72.7% |
| B EXIT 5c | -0.2707 | -0.1140 | -0.4730 | -0.84 | -15.87 | 70.6% | 62.5% |
| B EXIT 10c | -0.2588 | -0.1089 | -0.4779 | -0.84 | -15.20 | 76.5% | 66.7% |
| B EXIT 15c | -0.2429 | -0.1023 | -0.4781 | -0.84 | -14.31 | 82.4% | 75.0% |
| B EXIT 20c | -0.2280 | -0.0960 | -0.4780 | -0.84 | -13.48 | 88.2% | 100% |
| B EXIT >=25c | -0.1934 | -0.0814 | -0.4728 | -0.84 | -12.52 | 100% | never fires |
| C COMPLEMENT (all) | -0.1934 | -0.0814 | -0.4728 | -0.84 | -12.52 | - | never completes |
| D L5 5c | -0.2768 | -0.1165 | -0.6362 | **-1.60** | -17.19 | - | - |
| D L5 10c | -0.2359 | -0.0993 | -0.5613 | -1.43 | -14.90 | - | - |
| D L5 20c | -0.2016 | -0.0849 | -0.4846 | -1.12 | -12.98 | - | - |

**NOT ONE POLICY BEATS HOLDING, AT ANY THRESHOLD.** Every intervention is
EV-negative against the control, and every one makes drawdown WORSE rather
than better -- the opposite of the trade-off a loss-management rule is
supposed to buy. The advancement rule ("reduces loss severity and drawdown
without reducing expectancy enough to make the strategy worse") is not met
by anything here, so nothing advances.

The saved/cost split says why, and it is brutal:

| threshold | saved on positions it helped | cost on positions it hurt |
|---|---|---|
| 3c | $0.18 | $7.46 |
| 5c | $0.18 | $4.51 |
| 10c | $0.08 | $3.74 |
| 15c | $0.06 | $2.83 |
| 20c | $0.00 | $1.94 |

At 20c the stop saved **nothing at all** and cost $1.94. There is no
threshold at which the stop earns its keep; there is only a threshold at
which it stops firing (>=25c) and degenerates into HOLD.

## The MFE/MAE path study answers the question, and it is the mechanism

**Eventual WINNERS suffer LARGER adverse excursions than eventual losers** --
the reverse of the pattern a stop needs:

| adverse threshold | % of winners touching | % of losers touching | gap |
|---|---|---|---|
| 3c | **47.1%** | 33.3% | -13.7 |
| 5c | **41.2%** | 33.3% | -7.9 |
| 10c | **29.4%** | 18.0% | -11.5 |
| 15c | **17.6%** | 5.1% | -12.5 |
| 20c | **17.6%** | 2.6% | -15.1 |
| >=30c | 0% | 0% | 0 |

Median MAE: winners **-0.03**, losers **0.00**. Worst MAE: winners -0.30,
losers -0.20. At every threshold a stop is MORE likely to hit a winner than
a loser, which is why winner retention falls to 52.9% at 3c while the
disasters it was built to prevent are barely touched. A stop cannot separate
these two populations because, on this corpus, adverse excursion is a
property of the eventual WINNERS.

The reason is structural rather than mysterious: by T+180 a BTC5m loser is
usually already cheap and stays cheap -- it never has far to fall -- while a
winner is the side that was marked down and then recovered. Stopping the
drawdown means stopping the recovery.

## C never fires, and that is a finding, not a gap

COMPLEMENT completed **zero** times at every threshold, and **zero** matched
pairs above $1 were constructed. The explicit arithmetic refused them all:
completing costs original + complement + all fees against a $1 matched
payout, and that total never beat the prospective value of continuing. This
is the harness declining to call a >$1 book protection because exposure
fell -- the thing the brief forbids -- rather than an absence of
opportunities to check.

## D makes it worse in exactly the predicted way

L5 inventory deepens the worst loss from -0.84 to **-1.60** at 5c and raises
capital deployed from $27.83 to $36.50, for an EV that is worse than HOLD at
every threshold. Sizing is constant by construction -- no code path
multiplies the unit, so martingale is inexpressible rather than disabled --
and the loss grows purely from adding units to a position that was already
wrong.

## What this does and does not establish

In-sample, 56 entries, one entry checkpoint, at T+180 rather than the early
window the live cohorts actually trade, with an unreconciled sell-side fee.
It does not establish that loss management is useless in general. What it
does establish, on the only executable BTC5m paths we possess, is that
**the EXIT family is dead on arrival for the reason the path study names**:
the adverse excursion it keys on belongs to the winners. A rule whose
trigger is more common among the positions you want to keep cannot be
rescued by tuning the trigger, and no amount of threshold search on this
corpus would have found a level that works -- the sweep already covers the
whole range and the best outcome in it is "never fires".

The HOLD control's own EV is -0.1934/entry, which is itself a reminder that
the underdog entry at T+180 is not a strategy either. Both facts are
in-sample and neither advances anything.
