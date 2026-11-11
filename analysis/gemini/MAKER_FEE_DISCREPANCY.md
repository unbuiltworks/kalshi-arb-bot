# The maker fee the schedule implies, and the zero the venue reported

**Date:** 2026-09-22 · **Nothing in the fee accounting is changed by this
document.** It establishes what `n` is and what the gap is; the fee line stays
as it is until the gap is explained by a cash reading.

## What is now established

**1. `n` is the fee, from the venue's own field reference.** Gemini's
prediction-markets WebSocket [Order Events reference](https://developer.gemini.com/prediction-markets/websocket/streams)
defines it as:

> `n` — string — "Fee amount (only present in 'FILLED' events)"

So reading `n` as the fill's fee is correct, and `cohort_economics.fees_from_events`
is reading the right field.

**2. A present zero is a STATED zero, not an omission.** The same reference
says:

> "Fields with empty or zero values may be omitted from the event."

That cuts the other way from the obvious worry. Had `n` been absent we could
not have told "no fee" from "field dropped" — but EP016 records
`fees_missing: 0` across all 18 fills, every one carrying `n` explicitly as
`0.0000`. The venue asserted a zero rather than declining to say.

## The gap

The published [predictions fee schedule](https://www.gemini.com/fees/predictions),
effective 2026-03-18, states:

> Maker Rate **0.0175** · Taker Rate 0.07
> Maker Fee = Maker Rate × C × P × (1 − P)
> "Fees are rounded up to the next cent."
> Maker orders incur no fee when cancelled. No settlement or membership fees.
> **No promotional waiver is stated.**

Applied to the eighteen EP016 fills, one contract each:

| | |
|---|---|
| unrounded maker fee, all 18 | **$0.072132** |
| rounded UP per fill, as published | **$0.18** |
| venue reported at fill time | **$0.0000** |
| gross fill spend | $6.86 |
| the same day's rebate accrual | $0.08332590, payable $0.08 |

The rounding rule is what makes this large: 0.0175 × 1 × 0.45 × 0.55 = $0.00433
becomes **$0.01** per fill, so the round-up multiplies a 0.7¢ theoretical
charge into 18¢ across the day — **2.6% of notional**.

**And it would invert the incentive.** $0.18 of fees against $0.08 of rebate
makes the maker programme net **negative** at this size, not additive. That is
the opposite of how every rebate line in this programme has been framed.

## What is NOT established

Three readings survive the evidence, and nothing we hold separates them:

1. **Charged later.** `n` is a fill-time field; the fee may be levied at
   settlement or in a daily sweep. The schedule states no timing.
2. **Waived in practice.** An unpublished promotion or an account-level
   exemption. The schedule states no waiver, but a schedule is not a ledger.
3. **A different basis.** `C` may not be contracts-as-we-count-them, or the
   maker rate may not apply to this product despite the page covering
   predictions.

## The test that would settle it

A **cash** reconciliation, not another read of the same field: account balance
movement and `/v1/transfers` debits across ET day 2026-09-21, against the 18
fills. If $0.18 left the account, reading (1) is right and every matched-pair
figure in this programme is 2.6% of notional too generous. If nothing left,
(2) or (3) holds and `n = 0.0000` is the whole truth.

That read is exactly what the gate's `rebates` step performs, and it is the
next thing to look at — **before** any change to the fee line.

## Status

**UNRESOLVED.** The fee line is unchanged. `n = 0.0000` remains recorded as a
confirmed zero, because that is what the venue said, and the discrepancy with
the published schedule is recorded beside it rather than resolved in either
direction.
