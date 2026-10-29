# Four actions for unmatched inventory, scored against holding

Reproduce with `analysis/l5/campaign9/four_way_inventory_compare.py`.
74 samples, two windows, real authenticated books. No production change.

## The baseline, and the flaw it exposes in my own module

`l5_book_completion.decide` refuses a completion whose pair cost exceeds
**0.9532**. You were right that scoring against a fixed threshold is the
wrong test. Done properly, with q held of side H at a cost already **sunk**
and p the probability H settles at $1.00:

```
E[hold]     = q·p
E[complete] = q·1.00 − q·x            (x = complement's own price)
incremental = q·(1 − x − p)
```

**The sunk first-leg cost cancels** — which is why this comparison is
possible on a tape that does not carry it, and why nothing is invented here.
p is the **observed mid** of the held side, never modelled: the operating
model says not to assume residual exposure has predictive skill, so
substituting a view of our own would be the thing it forbids.

Since x and p are complements, `x_mid ≈ 1 − p`. So **at the mid, completing
and holding are a wash**, and the whole incremental result is which side of
the book you transact on, plus fees.

## The four actions, measured

| | 2100z (15) | 2115z (59) |
|---|---|---|
| **1. maker completion** beats holding | **15 / 15** | **59 / 59** |
| …and queue-reachable (≤50× need) | **0** | **5** |
| sum incremental if all filled | +$0.7500 | +$2.5850 |
| sum incremental, reachable only | $0 | **+$0.2500** |
| **2. marketable limit** beats holding | **0 / 15** | **0 / 59** |
| median incremental | **−$0.0127** | **−$0.0214** |
| **3. reduce the position** beats holding | **0 / 15** | **0 / 59** |
| median incremental | **−$0.0127** | **−$0.0214** |
| **4. hold** | baseline | baseline |
| refused by the 0.9532 threshold | 15 | 59 |
| …**but beats holding** on the incremental test | **15** | **59** |

## Answering the question as asked

**Which rejections are genuinely uneconomic?** Every crossing alternative, at
every one of the 74 samples. The arithmetic is exact: crossing pays the
half-spread (0.005 on a 1 ¢ book) plus the taker fee (`0.0695·p·(1−p)` =
$0.0174/contract at p=0.50) ≈ **$0.022/contract against holding** — which is
the −$0.0214 median measured. Reducing is the same trade in the other
direction and scores identically. **Marketable completion and position
reduction are not being blocked by a rule; they lose money.**

**Which are caused by the maker-only restriction?** **None.** Maker-only
costs us nothing here, because the crossing alternative it forbids is worse
than holding in 74 of 74 samples.

**Which are caused by the price-band / threshold rule?** Nominally all 74.
And this is where the answer turns.

## Why I am not recommending the threshold change

The incremental test says maker completion beats holding by ~$0.05/contract
— the half-spread. The settled analysis says that half-spread is exactly
what adverse selection takes back (`campaign8/ACCOUNTING_AND_DWELL_VERDICT.md`
line 180):

> *"a resting completion is not free money: it fills when the market moves
> toward it, which is exactly when the leg we already hold is becoming a
> winner. Completion sells the favourable tail for a locked pair edge. At
> 0.953 the trade is exactly fair."*

The incremental test uses p as observed **before** the fill. Our fills are
not random draws from that distribution — they are conditionally selected to
arrive when the market has moved against the completion. **0.9532 is the
break-even measured from settled outcomes, so it already embeds the adverse
selection the incremental test omits.**

So the two methods do not actually disagree. One says the gross maker edge is
the half-spread; the other says the net, after selection, is zero. Both are
consistent with a net of approximately nothing — and the second is measured
on settled money while the first is measured on pre-fill prices.

**No policy change is supported.** Replacing 0.9532 with `x + p < 1` would
be trading a threshold calibrated on settled outcomes for one calibrated on
quotes, and would book the half-spread as profit twice.

## The real bottleneck is neither the trigger nor the rule

**It is queue position.** 5 of 74 samples are queue-reachable even on the
generous ≤50× test; the rest show queue multiples of **745× and 808×**. The
reachable subset is worth **+$0.25 gross across 475 s of two windows** —
≈$0.125/window gross, ≈$0.076/window at the measured 60.9 % fill rate,
**before** the adverse selection above, which the settled evidence says
consumes it.

That is the third independent route to the same place: throughput, trigger
and now pricing rule all come back to a book where our passive order is
behind hundreds of times the quantity we need.

## Honest limits

Modelled shadow inventory (so counts describe the book's opportunity set,
not money held); 74 samples across two windows on one date; p from the mid
assumes the mid is unbiased, which is the standard assumption and is exactly
what adverse selection violates in our favour-of-caution direction;
displayed ladder depth is displayed, not guaranteed, and a marketable walk
is refused outright when the ladder is short.

---

# The record-only path, with a stop condition

For the next authorized campaign. **No live submission. No capability object.
No limit changed.**

**Configuration:** `L5_BOOK_COMPLETION=1`. The path evaluates every 5 s
against real inventory, real books and real order state and emits its
decision and refusal class. It cannot submit: reaching the wire needs a
`ControllerLiveAuthorization` that `Runner` does not construct.

**What it collects** — the join no existing tape has. Per decision: the
four-way incremental scores above, the queue multiple, the refusal class,
and whether an order was resting.

**Stop condition — whichever comes first:**

1. **400 decisions recorded against real inventory**, or
2. **the end of the authorized campaign's last window**, or
3. **20 decisions with `queue_reachable` true** — enough to estimate the
   reachable rate to ±10 % — or
4. **any `journal_errors` > 0 or heartbeat gap > 180 s**, which stops the
   recorder and is reported as a defect rather than a result.

Then it is **turned off and reported**. Not monitored further, not extended,
no second campaign on the same question.

**The decision rule after it stops, fixed in advance so the result cannot be
read to taste:**

- **reachable rate < 10 %** → delete the submitting path; keep the module as
  a recorder. This is the outcome the current evidence predicts (5/74 = 6.8 %).
- **reachable rate ≥ 10 % AND the reachable subset's incremental, discounted
  by the 60.9 % fill rate and by the settled adverse-selection haircut, is
  ≥ +$0.05/window** → propose Stage 3 for separate authorization.
- **anything between** → no change, and say so.
