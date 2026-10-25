# What is holding us back, in one number — and the rebate we are forfeiting

Ran the most aggressive profit search in this program: completion ceilings 0.96 -> 1.02,
opening notional $0.80 -> $25, defensive completion on and off, all five crypto 15m series,
with the venue's OWN credited-rebate formula included. Below is everything it found.

## 1. The economics of one completed pair

Production-faithful (defensive completion OFF, as the live runtime reports), the pair ceiling
binds correctly: realized pair cost **0.9573 - 0.9600**, so a completed pair grosses ~4c.

| cost line | value | note |
|---|---|---|
| gross pair edge | +4.0c | 1.00 - 0.96 realized |
| fees | -3.0c at $0.80, -1.4c at $1.60, -0.2c at $10 | $0.01/order is fixed, so it amortizes |
| maker rebate | +0.0c to +0.62c | venue formula, cent-rounded |
| **net per completed pair** | **+1.6c to +4.4c** | |
| **cost of ONE strand** | **-45c** | measured, 0/6 win rate, t=-7.3 and -11.4 |

A strand costs **10 to 28 times** what a good pair earns.

## 2. THE NUMBER

```
max tolerable strand rate = net_per_pair / (strand_cost + net_per_pair)

  $0.80 notional  ->  3.5%
  $1.60           ->  6.7%
  $4.00           ->  8.2%
  $10.00          ->  8.9%

OBSERVED:  held-out BTC15m 7/25 = 28.0%     TRAIN non-BTC 18/54 = 33.3%
```

**We need the strand rate under ~9%. It is 28-33%. That is 3.1-3.7x too high, and it is the
entire reason this is not profitable.** Win rate is actually 76% (19/25) — we win often and
small, then lose ~10x on the 28% we cannot complete.

Everything already tested against this: cancellation rules (reject, adverse rate got worse),
defensive completion (EV-neutral — ceiling 1.20 -> unlimited moves net 0.5%), entry-spread
filters (all strands had the tightest possible 0.01 spread), order-size scaling, two-sided
quoting (-$0.1868/window on 97 windows), taker-open (worse in 85/90 paired windows). And
strands are not predictable at entry: at n=71 fills the only separating observable is the
entry price itself (AUC 0.119), which is tautological — cheap legs lose more often, which is
the market's own probability, already in the price.

## 3. THE REBATE WE ARE THROWING AWAY

The programme is real and has paid us: **25 credited prints, $0.42 on $56.86 notional =
0.351c/contract observed**, and the published formula reproduces 23/25 of those credits.

But the credit is **cent-rounded per print**, and the minimum fill for a nonzero credit is:

| fill price | min contracts |
|---|---|
| 0.20 | 2.50 |
| 0.30 | 1.90 |
| 0.40 | 1.67 |
| 0.50 | 1.60 |

At our $0.80 opening notional every print is ~1.2-1.35 contracts, so:

**0 of 70 TRAIN prints and 0 of 38 held-out prints earned a single cent. We are earning
exactly $0.00 in rebates, by rounding.** That is why our own accounting reads
`eligibility: UNVERIFIED_NO_VENUE_CREDIT_RECORD`.

Raising opening notional to **$1.60** starts paying: 44 of 89 prints (TRAIN), 32 of 43
(held-out). This is free money on trades we already place, worth up to 0.62c/pair.

**It does not fix the strategy** — 0.62c against a 45c strand is nothing — but it is the one
change that is unambiguously positive-value and costs no new edge.

NOTE A REAL TENSION WITH `CLAUDE.md`: "favor increasing frequency before size" is actively
wrong for rebate capture. Hundreds of small prints earn $0; fewer, larger prints earn the
full rate. Frequency and rebates trade off against each other through the cent rounding.

## 4. Why "hundreds of trades" cannot happen on the current configuration

One market, one outstanding order, 0.4 fills/window, 96 windows/day -> **~38 fills/day
maximum**. Hundreds of trades requires CONCURRENCY across many markets, which is the last
step of the spring order and is not enabled.

And at the measured -$0.155/window, 300 trades is ~750 windows is **about -$116**, against
$14.7543 of remaining L5-scoped headroom. The gates would stop us at ~95 windows. Hundreds
of live trades is not blocked by caution; it is blocked by the loss budget meeting a negative
per-trade expectancy.

## 5. Honest methodology note

The held-out BTC15m set is no longer clean: I swept ceilings and sizes on it in this
session. Every apparently positive held-out result in it was ONE window — at $1.60 the top
window is 131% of the total (ex-best -$0.4158); at $10 it is 100% (ex-best +$0.0040); t=+0.64
and +0.85. Per series at $1.60: BTC +1.34 (one window), ETH -3.66 (t=-1.77), SOL -1.06,
XRP -0.41, DOGE -0.11. **Every series is negative or noise.** TRAIN is significantly negative
at every size (t=-2.40 to -2.81), reaching **-$46.56 at $10 notional**.

That last figure is the point of the discipline: the configuration that looked best on the
contaminated holdout loses $46 on the larger clean sample.

## 6. What would actually have to be true

1. **Strand rate below ~9%.** Not achievable by any rule tested; strands are unpredictable at
   entry and completion is EV-neutral once the market has moved. This needs a market where
   both legs are reliably acquirable, not a better rule on 15m crypto binaries.
2. **Order size >= ~2.5 contracts per print**, or the rebate is mathematically zero.
3. **Concurrency** across many markets, or hundreds of trades is arithmetically impossible.

The existing PMUS full-slate discovery already screens hundreds of markets across every
category. Screening it for *strand rate / two-leg acquirability* — rather than spread or
volume — is the search that matches the actual binding constraint, costs no capital, and is
the only one of these three that could plausibly produce a positive-expectancy market.
