# Where L5 actually transacts — and the one thing we cannot see

Owner: *"reconstruct, wherever public data permits, L5's trade price versus
contemporaneous bid/ask... Do not make unsupported claims about L5 queue
position or self-trading."*

**Headline: L5 is a maker 83.4% of the time, so he is not buying the offer —
and his median matched pair costs 0.9876, which our own gates would refuse.
Only 24.6% of his trades clear our ≤0.96 preference and only 62.8% clear our
hard ≤1.00 ceiling. We are demanding a better price than L5 himself achieves.**

---

## 0. What cannot be measured, stated before anything else

**There is no contemporaneous order book for L5's windows.** Nothing on disk
carries a bid/ask time series for them and the venue does not serve historical
depth. Therefore:

- **L5's distance from the touch is NOT COMPUTABLE.**
- **L5's queue position is NOT COMPUTABLE.**
- Neither is estimated from his prices, and a test fails if any
  distance/queue field ever appears in this module's output.

**Self-trading is not tested.** The trade rows carry a transaction hash but no
counterparty identity, so the question is unanswerable from this data. No
claim is made in either direction.

## 1. L5 does not pay the touch — he rests and gets hit

```
fills               71,984
maker               55,647
taker               11,068
unlabelled           5,269   (outside the takerOnly walk's era; never assumed)

maker share of labelled fills          83.4%
```

Unlabelled fills are reported separately and never folded into either side:
the walk covered one era, and treating an early-era fill as maker would
manufacture the headline.

**This refutes the simplest version of "L5 gets closer to the action."** He is
not crossing the spread. Like us, he is a passive maker in the large majority
of his fills. Whatever separates him from us, it is not that he pays up.

## 2. What his pairs actually cost — the measurement that stands in for distance

A trader resting far behind the touch collects a cheap pair rarely; one
resting at the front collects a thin pair often. The distribution shows which,
without needing a book.

```
n = 66,642 matched pairs

p05    0.9080
p25    0.9605
median 0.9876
p75    1.0151
p95    1.0949
mean   0.9928

share below 1.00   62.7%
share at or below 0.98   42.4%
```

By liquidity role — when he crosses, he pays up, exactly as expected:

| | n | median | mean | share < 1.00 |
|---|---:|---:|---:|---:|
| maker | 54,429 | 0.9877 | 0.9901 | 63.9% |
| taker | 7,533 | 0.9972 | 1.0241 | 51.2% |

## 3. Against our own gates — the answer to "are we optimising pair price too hard?"

**Yes. Measurably.**

| L5's pairs, scored by OUR live gates | share |
|---|---:|
| ≤0.96 — our spring preference | **24.6%** |
| 0.96–0.98 — our observation cap | 17.8% |
| 0.98–1.00 — our hard ceiling | 20.4% |
| **>1.00 — we refuse outright** | **37.2%** |

```
would clear our <=0.96 preference    24.6%
would clear our <=1.00 hard ceiling  62.8%
```

**Our engine would have refused 37.2% of L5's own trades, and our preferred
target would have refused 75.4% of them.**

L5 takes roughly **1.2 cents** of pair edge at the median. Our discipline
demands four or more. That is the "beautiful theoretical price nobody trades
against" pattern, quantified against the trader we are copying.

By window length, and the direction is consistent:

| duration | n | median pair | share above par |
|---|---:|---:|---:|
| 15m | 61,706 | 0.9871 | 35.8% |
| 5m | 4,936 | 1.0247 | 55.1% |

On the fast 5-minute windows he pays **above par more often than not**.

## 4. What this does and does not license

**It does not license raising the ceiling.** `CLAUDE.md` is explicit: near-$1
pair tolerance belongs to later incentive-era L5 behaviour and stays locked
unless equivalent economics are source-confirmed for our account. L5's
above-par pairs were presumably paid for by rewards we have not confirmed we
receive — our measured credited rebate is **$0.0134 per fill**, and nothing
here establishes his. This section is a measurement, not a proposal.

**What it does establish** is that the gap between us and L5 is not explained
by aggression — we are both makers — and that our price discipline is
materially tighter than his. Combined with the shadow's finding that
proposals 3–4 ticks out fill at 0.74% against 47.6% at 2 ticks, the coherent
reading is that **we are buying price at the cost of fills, and L5 is not**.

That reading is now testable rather than speculative: the queue-aware rows
deployed at 18:2xZ will say whether our non-fills sit behind large
better-priced queues. This analysis cannot say it for L5, and does not.

---

*Read-only, from data already on disk. No deployment, no orders, R5F off.*
