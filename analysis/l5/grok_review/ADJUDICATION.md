# Independent-review claims, tested against the canonical dataset and fresh data

Read-only. No production change, no order, no policy change. Grok's conclusions
were not accepted; each novel claim was tested, and two of my own earlier
statements turned out to be wrong and are corrected here.

## Fresh data: L5 is active, and our cache was stale

The canonical cache ends **2026-08-31**. A read-only fetch of the public
activity endpoint returned **59,949 deduped rows for 2026-09-11 .. 09-14** —
none of it seen by any model fitted in this program. Dedup uses the same
multiset-union rule as the canonical ledger (multiplicity = max seen in any one
response, never the sum, never one).

## Two corrections I owe

**1. "Incentives ended 2026-08-31" — WRONG.** That was an artefact of the cache
ending there, and of a first fetch that truncated at 4,500 rows before any
incentive row appeared. Incentives are alive and larger than ever:

| day | fills | turnover $ | confirmed incentives $ |
|---|---|---|---|
| 09-11 | 11,275 | 147,531 | 0.00 |
| 09-12 | 19,582 | 276,903 | **1,646.75** |
| 09-13 | 14,732 | 190,924 | **2,572.06** |
| 09-14 | 12,958 | 166,632 | **1,544.15** |

**$5,762.96 over four days (~$1,900/day)** against **$559/day** historically —
roughly tripled, not stopped.

**2. "L5 has concentrated almost entirely into BTC15m" — overstated.** BTC15m
was **already 91.2%** of canonical fills (693,916 of 761,022) and 83.8% of
turnover. Today's ~95% is intensification, not a change of business.

That second point matters for this whole program: **the 5m cohort our P0
targets is 6.8% of L5's fills.** BTC15m is the business and always was.

## Claim 1 — "the DEARER bias may be weak" : REFUTED

First-side DEARER rate on data no model here was fitted to (2026-09-14, markets
possibly truncated at the fetch boundary excluded):

| cohort | markets | DEARER rate |
|---|---|---|
| BTC5m | 67 | **0.7015** |
| BTC15m | 34 | **0.6765** |
| ETH5m | 29 | **0.8276** |
| ETH15m | 10 | **0.8000** |
| canonical BTC5m | 7,155 | 0.6739 |
| canonical ETH5m | 2,309 | 0.7549 |

Every cohort is at or above our ~66% estimate out of sample. The bias is not
weak.

## Claim 2 — "5m/15m differences may be small" : REFUTED

Same day, all markets in cohort (not the switched-only subset that inflated my
earlier BTC15m figures):

| cohort | fills p50 | switches p50 | first px p50 | size p50 |
|---|---|---|---|---|
| BTC5m | 2 | **0** | 0.64 | 68.0 |
| **BTC15m** | **107** | **32** | 0.54 | 16.0 |
| ETH5m | 1 | **0** | 0.75 | 19.0 |
| ETH15m | 2 | **0** | 0.74 | 12.0 |

BTC15m runs ~50x the fills and is the only cohort that switches at all at the
median. The difference is enormous and specific to BTC15m.

## Claim 3 — "typical recurring clip sizes" : CONFIRMED, and it survives normalisation

The test that distinguishes a real clip from a round-number artefact: if L5
sized in DOLLARS, `size = usd/price` would be ragged and `usdcSize` round. The
opposite holds.

| series | % integer | % mult-5 | % mult-10 |
|---|---|---|---|
| canonical **share** size | **68.6** | 62.5 | **50.2** |
| canonical **usd** size | **0.0** | 0.0 | 0.0 |
| today share size | 51.9 | 38.0 | 27.8 |
| today usd size | 5.2 | 0.7 | 0.6 |

**Zero percent** of 59,562 canonical dollar amounts are integers, against 68.6%
of share counts. He sizes in shares. Grok is right on this one.

The modal clip has shifted: canonical 50.0 (18.0%) and 30.0 (13.0%); today 80.0
(14.9%), then 10.0 and 5.0. Clustering persists, the modes moved.

## Experiment 4 — the incentive audit, from the public record

L5's public activity contains real incentive events: **MAKER_REBATE 110,
TAKER_REBATE 73, REWARD 20** over 2026-04-21 .. 08-31.

| | confirmed |
|---|---|
| MAKER_REBATE | $35,228.32 |
| TAKER_REBATE | $7,525.73 |
| REWARD | $28,797.69 |
| **total** | **$71,551.74** |
| incentives / turnover | **94.05 bp** |
| incentives / fill | $0.0940 |
| incentives / day | $559.00 over 128 trading days |
| trading-only cash | +$15,707.45 (20.6 bp) |
| **grand total** | **+$87,259.19** |

**Incentives are 82% of L5's total economics and ~4.6x his trading P&L.**

**Attribution is impossible beyond timestamp -> transaction -> amount, and this
is now established rather than assumed.** All 203 incentive rows carry an empty
`conditionId` and an empty `slug`; only `transactionHash` and `timestamp` are
populated. And **none of the 203 incentive transaction hashes appears on any
TRADE row** — the incentive is its own on-chain transfer. So no market, motif,
horizon or asset can be tied to a credited amount without inferring it, which
the public record does not support.

## Experiment 1 — answered structurally, not statistically

The primary question was whether the path into the price explains entry better
than the absolute price. For L5's dominant cohort **neither does, because there
is no entry decision to explain.**

**Entry delay (L5's first fill minus the market's own start epoch):**

| cohort | markets | p50 delay | % within 30s |
|---|---|---|---|
| **BTC15m** | 356 | **13 s** | **80.6** |
| BTC5m | 705 | 130 s | 23.3 |
| ETH5m | 374 | 193 s | 10.7 |
| ETH15m | 153 | 588 s | 4.6 |

**Market coverage over an 89.8-hour window:**

| cohort | traded | on the grid | coverage |
|---|---|---|---|
| **BTC15m** | 356 | 360 | **98.9%** |
| BTC5m | 705 | 1,078 | 65.4% |
| ETH15m | 153 | 355 | 43.1% |
| ETH5m | 374 | 1,062 | 35.2% |

He takes **essentially every BTC15m market, 13 seconds after it opens.** No
30/60/120 s pre-entry path exists at that point, so no path feature can explain
the entry, and no entry rule needs fitting. Entry selection is real only in the
minority cohorts, which are 5-7% of his fills.

This is why the market-wide tapes could not deliver the originally-specified
test: the endpoint returns newest-first, and for a market L5 enters at second 13
the pre-entry window is 13 seconds long. The honest answer is the structural
one, not a regression on a window that does not exist.

## What L5 actually is

Inside-market behaviour, recent window, BUY fills:

| cohort | markets | fills p50 | switches p50 | matched frac | % of market life active | Up share |
|---|---|---|---|---|---|---|
| **BTC15m** | 356 | **128** | **30** | **0.9067** | **88.6%** | 0.4844 |
| BTC5m | 455 | 3 | 1 | 0.2968 | 27.0% | 0.4831 |
| ETH5m | 167 | 2 | 0 | 0.0 | 5.3% | 0.3902 |
| ETH15m | 90 | 3 | 0 | 0.0 | 6.3% | 0.5000 |

**L5 is a continuous two-sided market maker in BTC15m.** He joins ~every market
at open, works it for ~89% of its life with ~128 fills and ~30 side switches,
and finishes ~91% matched and side-balanced. The 5m cohorts are opportunistic
dabbling by comparison.

Set beside the incentive audit, the business is legible:

* ~94 bp of turnover in confirmed incentives, **82% of total economics**
* ~20.6 bp in trading cash
* ~$0.098 of confirmed incentive per fill

The "inventory cycle" this program has been reconstructing **is market making**,
and the profit is predominantly the rebate on volume, not the pair economics.

**This is the material finding for replication.** Our PMUS probe earns $0.0126
of confirmed rebate per fill against L5's $0.098 — 7.8x lower — while
reproducing the inventory mechanics faithfully. That gap, not side selection,
not the pair gate, and not the residual cap, is what separates our realized
economics from his.

No production change is proposed on the strength of this.
