# Why L5 has throughput and we do not

Measured from **70,435 of L5's own venue trades** in
`data/l5/raw/activity.jsonl` — 62,020 of them in `btc-updown-15m`, the same
family we trade. Not a model, not a replay. L5's actual fills and
redemptions.

## 1. What L5 actually does

| | L5 (btc15m) | us |
|---|---|---|
| trades per window | **mean 31.8, median 6, p90 135, max 453** | 5 submitted, 3 filled |
| quoting span in a 900s window | **median 764s** | opening burst |
| sides | 35,056 Up / 35,379 Down — **49.8/50.2** | balanced by design |
| direction | 70,433 BUY / 2 SELL — **buys both outcomes, never sells** | same |
| spend per window | **~$1,065** | $1 |
| median pair basis | **0.9644** | ceiling 0.96 |
| residual carried | **median 17.2%** of the larger side; only 0.2% of windows balanced | treated as a defect |

**L5 is a high-frequency two-sided maker that quotes continuously across the
whole window and routinely carries unmatched inventory.** It is not a
careful opener of one clean pair.

## 2. It is profitable, and throughput is what makes it so

Spend and redemptions are both directly observed — no attribution:

```
total spend    $535,434        total redeem   $571,652
net            +$36,218 over 527 windows  =  +$68.7 / window
windows positive 68.7%        p05 -$69.3   p95 +$292.9
```

It loses in 31.3% of windows. Aggregate, not per-window, is the objective —
exactly as the mandate says.

**The correlation with throughput is the finding:**

| trades in window | n | net per window |
|---|---|---|
| 0–10 | 32 | **+$0.51** |
| 10–50 | 139 | −$1.98 |
| 50–150 | 131 | **+$61.06** |
| 150+ | 199 | **+$146.22** |

Low-throughput windows make nothing. **The money is in the windows where L5
fills 150+ times.** Our best window ever submitted five orders.

## 3. The decomposition, and the parameter I had wrong

On the **186 windows where the redemption equals one side's quantity
exactly**, so the outcome is directly determinable and nothing is inferred:

| | contracts | basis | outcome | P&L | per contract |
|---|---|---|---|---|---|
| matched pairs | 121,837 | 0.9641 | redeem $1 | +$4,375.78 | **+$0.0359** |
| residual | 20,069 | 0.3489 | **won 38.6%** | +$737.26 | **+$0.0367** |

Parts sum to within 4% of the observed net, and the matched figure (+0.0359)
matches the full 501-window sample (+0.0356), so the subset is
representative.

**An unmatched leg is worth about +$0.037, not −$0.244.**

My reopening gate priced it at **−$0.2440**, taken from `hold $` in
`QUEUE_DEPLETION_FINDINGS.md` — measured on recorded books under **PROVEN**
fills. A PROVEN fill is one where the opposite touch *crossed our price*, so
every leg in that sample filled **because the market moved against it**. The
constant measured the sampling rule, not the market. At 6.4× the pair edge
it refused every reopening at the deployed depth, which turned a gate meant
to *price* repeated rounds into one that **forbade** them. The owner's
correction was right.

It is also what this module's own EV identity already said:
`EV(hold) = EV(complete) = h(1−p) − c` **exactly**, so a leg bought at a
fair price carries no expectancy penalty and only its **worst case**
differs. I established that first and then overrode it with the artefact.

**An earlier draft of this document reported residual P&L of +$18,734
(+17.95¢/contract).** That was wrong: it inferred the residual's payout as
`redeem − matched`, assuming every matched pair redeems exactly $1 and the
remainder belonged to the residual. On some windows that produced payouts
above $1.00 per binary contract, which is impossible. The determinable-
outcome subset above replaces it.

## 4. What changed in the code

`REOPEN_P_PAIR` **0.525 → 0.727** and `REOPEN_P_SINGLE` **0.423 → 0.273**,
from our own 34 venue-confirmed orders (8 of 11 rounds that saw a first fill
went on to pair).

`REOPEN_SINGLE_OUTCOME_USD` **−0.2440 → 0**. Set to **zero, not to L5's
measured +0.0367** — banking an edge we have not reproduced ourselves would
be assuming the conclusion, and zero is the identity's own answer.

Effect at the deployed `d=2`:

```
E = 0.727 x 0.0500 + 0.273 x 0     = +0.0364   PERMITTED
```

It opens even on PROVEN's pessimistic pairing rate (+0.0262), which shows
the pair rate was never what blocked it — **the single-leg constant was
doing all the work**, and `test_the_old_adverse_selection_constant_is_what_
forbade_throughput` pins that dependency.

**Nothing about risk was relaxed.** Capacity is untouched and still reserves
`qty × the dearer leg` against the exposure headroom and the daily loss
room, per side, counting held inventory and live orders as if they filled.
PRESERVE, the 1415z per-side rule, the claims-fail-closed safeguard and the
patience floor all stand. The tests that now assert a refusal assert it on
**capacity**, which is the bound that should be doing that work.

### Replay effect, out of sample, 13 windows

| fill model | d | B (deployed) | C (before) | **C (now)** |
|---|---|---|---|---|
| PROVEN | 2 | −0.1738 | +0.0008 | **−0.0554** |
| POSSIBLE | 2 | +0.1265 | +0.0508 | **+0.1265** |

C now opens **32 rounds against B's 34** where it previously opened 15. Under
the optimistic model it matches B exactly; under the pessimistic one it is
still three times better than B because capacity refuses the rounds it
cannot afford.

**C's PROVEN number got worse (+0.0008 → −0.0554) and that is the honest
cost of throughput under a pessimistic fill model.** The reason to accept it
is that PROVEN is now independently known to be wrong in two ways: our own
orders pair 72.7% against its 52.5%, and L5's residuals are +3.7¢ against its
−24.4¢.

## 5. What still prevents profitable continuous operation

Three things, in order of size. None is the strategy logic.

**(a) Capital. L5 spends ~$1,065 a window; we are authorized $1.** At L5's
realized rate of **6.4% of turnover**, $480/day needs about **$7,500/day of
turnover**, roughly **$78 per window**. At $1 per window the same edge yields
about **$0.06/day**. This is the dominant constraint by three orders of
magnitude and no code change touches it.

**(b) Throughput authorization, not throughput capability.** `L5_MAX_LIVE_ORDERS=2`
permits exactly one pair in flight; L5's profitable windows carry 150–453
fills. One market at a time, a per-window submission budget and the entry
cutoff all bind before the controller does. These are deployed variables and
the freeze forbids touching them — but they, not the controller, are what
caps our fill count now that the gate prices rounds correctly.

**(c) The pair ceiling sits at L5's median.** `L5_TS_MAX_PAIR_COST=0.96`
against an L5 median basis of **0.9644** — so we decline roughly **half the
pairs L5 takes** (48.4% of its pairs are ≤0.96). That is a deliberate
spring-discipline choice and `CLAUDE.md` locks near-$1 tolerance until the
economics are source-confirmed. This document is evidence about L5's
economics, not about ours, so **the ceiling is not changed here** — it is
flagged as the next thing worth an owner decision, since it is the single
largest remaining multiplier on pair count that is not capital.

## 6. What is still not known

- **Whether we can reproduce L5's residual outcome.** Its residuals won
  38.6% against a 34.9% basis. Ours, over 5 settled legs, lost 4 of 5. Both
  samples are far from conclusive at opposite ends, and n=5 cannot refute
  20,069 contracts any more than it can confirm them.
- **Why the cheap side wins more often than its price implies.** A 38.6% win
  rate on a 34.9% basis is a 3.7-point edge. The obvious candidate is
  intra-window mean reversion in BTC, which would make a mid-window
  underdog underpriced at expiry. This is **not** tested here and no
  accumulation logic is enabled on it; `ACQUIRE_UNFAVORED` still refuses for
  want of an independent estimate.
- **Whether our fill rate holds at L5's frequency.** Our 34 orders were
  placed a handful per window. Nothing says the 72.7% pair rate survives at
  135 quotes a window.

## 7. No profitability is claimed

Our confirmed settled realized net is **−$15.6109 all-time**. Everything
above is L5's money or a replay, and neither is ours.
