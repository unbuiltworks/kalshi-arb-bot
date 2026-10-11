# Testing the characterisation of L5's economics

Not "an incentive business around a break-even book". The banded evidence
supports something more specific, and one part of the proposed reading fails.

Authoritative lifetime accounting, unchanged: trading **+$15,707.45**,
incentives **+$71,551.74**, grand total **+$87,259.19** — with the
**+$2,021.01** delta from the 2026-08-31 census explained entirely by
additional history (545 more markets, 18,058 more trades, 486 more redeems,
3 more income rows), not by any change of accounting method.

## The proposed characterisation, tested band by band

> *"Profitable trading is concentrated in economically favourable inventory
> cycles, while a large volume of poor pair cycles destroys trading P&L and
> may be partially subsidised by incentives."*

| pair band at first switch | markets | turnover | trading cash | incentive proxy | combined |
|---|---|---|---|---|---|
| **≤ 0.90** | 3,196 | $911,610 | **+$50,465** | $8,331 | +$58,797 |
| 0.90–0.96 | 2,062 | $1,434,441 | +$11,296 | $13,486 | +$24,781 |
| 0.96–1.00 | 2,327 | $2,278,680 | +$4,219 | $21,991 | +$26,210 |
| 1.00–1.05 | 1,744 | $1,434,180 | −$1,067 | $13,933 | +$12,867 |
| **> 1.05** | 3,648 | $1,361,545 | **−$62,681** | $12,425 | **−$50,255** |

**CONFIRMED — concentration.** The ≤0.90 band is **12.0% of switched
turnover** and produces **+$50,465**, against +$15,707 of lifetime trading
cash in total. Favourable cycles are not merely better; they carry the entire
trading result and then some.

**CONFIRMED — destruction.** Cycles above 1.05 are 17.9% of turnover and
−$62,681, almost exactly cancelling the good band.

**REFUTED — "subsidised by incentives".** The worst band receives only
**$12,425** of proxied incentive against **−$62,681** of trading loss. It is
**not** subsidised; it is a net **−$50,255** even after crediting it its
volume share. The subsidy story *does* hold for the middle: 0.96–1.05 earns
$35,924 of proxied incentive on $3,152 of trading cash, so there the
incentives are essentially the whole return.

## The corrected three-regime picture

1. **≤ 0.90 — genuinely profitable trading.** +553.6 bp on trading cash alone;
   incentives are a rounding error beside it.
2. **0.90 – 1.05 — trading is a coin flip; incentives are the business.**
   Trading cash +$14,448 across $5.1M of turnover (~28 bp); proxied
   incentives $49,410. This is the band the original "tiny edge plus
   incentives over enormous turnover" description actually fits.
3. **> 1.05 — value destruction that incentives do not cover.**

So L5 is **not** one business. He is a profitable inventory trader in
favourable states, an incentive harvester in ordinary ones, and a loser in bad
ones — and the aggregate is the sum of three quite different activities, not a
single thin edge.

## What this does NOT license

* **Both incentive splits are proxies.** Incentive rows carry no
  `conditionId`, so no per-market attribution exists. Splits by notional and
  by `p(1−p)·qty` agree, but neither is source-accounted.
* **It does not authorise copying L5's size.** Hold-to-resolution is now
  canonical L5 behaviour (760,815 BUY against 2 SELL, MERGE/SPLIT/CONVERSION
  all zero), but absolute residual risk stays bounded by our own envelope.
* **It does not identify a causal gate for us.** These are bands of L5's own
  realised history on his venue with his rebate schedule, none of which is
  confirmed for our account.
