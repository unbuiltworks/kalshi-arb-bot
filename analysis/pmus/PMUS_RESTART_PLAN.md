# PMUS restart plan — prepared in advance, executed only on GEMINI_STOP

**Status: STANDBY.** This is deliverable B's second half, written before the
decoder gate resolves so that a stop costs no thinking time. Nothing here runs
unless the gate fails.

## Why PMUS and not another Gemini round

Three things are already measured and do not depend on the decoder:

| | Gemini BTC5m | PMUS live sports | Polymarket International |
|---|---|---|---|
| median quoted spread | **0.280** | 0.005–0.02 | 0.01 |
| reach within 120s | 24.4% | 29.9% (off-peak, provisional) | **80.6%** |
| median reach | 72s | 40s | **16s** |
| completion executable (book sweep) | — | **64.5%** at pair ≤ 0.96 | — |

Gemini's book is 30× wider and its flow is one-sided; PMUS is the opposite
problem, and the one PMUS number that speaks to completion — 64.5% executable
over 4,947 completion requests across 200 markets — was never the basis of its
rejection. The rejection came from a spread/touch-movement screen whose own
document says its reach figures are invalid: the sample ran 02:40–03:10 UTC with
176 of 229 markets being NFL pregame.

## What gets measured, and when

**Peak US sports hours, 23:00–03:00 UTC (7–11pm ET), which is when the previous
sample was NOT taken.**

1. **Collect.** `analysis/pmus/` already holds the gateway client. Poll
   `/v1/markets/{slug}/bbo` plus cumulative `sharesTraded` at 8s for the live
   slate. PMUS publishes no trade tape, so flow is inferred from first
   differences of the venue's own cumulative counter — a lower bound, and
   labelled as one.
2. **Measure the completion-centric metrics**, the same eleven P9-D asked of
   Gemini, minus the four that need a trade tape:
   frequency of fillable opposite-side opportunity · time to complement ·
   achievable matched pair cost · one-sided frequency · maker eligibility ·
   confirmed fees and rebates.
3. **Decide on the same rule P9 used**: does a two-sided quote have positive
   pre-rebate expectancy at the venue's own spread, with orphan losses priced at
   settlement.

## What PMUS does NOT inherit

* no Gemini threshold, cap or policy constant carries over
* no historical Gemini P&L is used as a prior
* the 09-23+ Gemini holdout stays sealed and unread regardless

## The first decision point

One peak-hours session is enough to answer whether the touch moves. If
`reach120` at peak is still near 30% the venue is rejected on its own evidence
and the L5 programme needs a different venue entirely, not a different
threshold.
