# How should frozen R2 actually be executed?

Execution research only. **R2 is untouched** — qualification is imported from
the frozen rule; this asks only what to *do* once R2 has said yes. Existing
prospective tapes, no new collection, no live orders. Tool
`r2_execution_policies.py`, raw output `r2_execution_policies.json`.

Population: **65 R2 opportunities across 42 independent windows** (145
eligible prospective windows).

Method: no `touch=fill`. A resting order fills only on the proven Gemini
methodology, with tiers reported **separately** — `THROUGH` (a print strictly
through our price; price-time priority cannot leave us behind it) and
`DEPLETION` (cumulative same-price volume exceeds the size displayed ahead of
us at placement) are defensible; `OPTIMISTIC` (any same-price print) is a
bound and is **never counted as a fill**. All six maker fills below are
`THROUGH` — none rest on a queue assumption.

## The answer: take immediately. Maker execution is actively harmful.

| policy | opps | fills | fill% | avg px | correct given fill | fees | **EV/opp** | EV/fill | EV/window | t_fill |
|---|---|---|---|---|---|---|---|---|---|---|
| **A immediate taker** | 65 | **65** | **100%** | 0.810 | **86.2%** | 0.870 | **+0.0377** | +0.0377 | **+0.0583** | 0.0 s |
| B maker at best bid | 65 | 6 | 9.2% | 0.410 | **16.7%** | 0.060 | −0.0234 | −0.2533 | −0.0362 | 140 s |
| C improve one tick | 65 | 6 | 9.2% | 0.420 | **16.7%** | 0.060 | −0.0243 | −0.2633 | −0.0376 | 140 s |
| D5 maker 5 s → taker | 65 | 38 | 58.5% | 0.847 | 81.6% | 0.490 | −0.0258 | −0.0442 | −0.0400 | 5 s |
| D15 maker 15 s → taker | 65 | 33 | 50.8% | 0.815 | 78.8% | 0.470 | −0.0211 | −0.0415 | −0.0326 | 15 s |

**A is the only policy with positive EV.** Rebates change nothing: every maker
fill landed outside the $0.20–0.80 eligible band or floor-rounded to zero, so
the rebate column is **0.000** for all policies and the with/without-rebate EV
is identical. No rebate is called realized.

## Why they differ — it is adverse selection, not price

This is the question that mattered, and the answer is unambiguous.

| policy | correct \| filled | correct \| NOT filled | correct \| all | price advantage vs taker | selection penalty |
|---|---|---|---|---|---|
| B | **16.7%** | 93.2% | 86.2% | **+0.332** | **−69.5 pp** |
| C | **16.7%** | 93.2% | 86.2% | +0.322 | −69.5 pp |
| D5 | 81.6% | 92.6% | 86.2% | +0.001 | −4.6 pp |
| D15 | 78.8% | 93.8% | 86.2% | +0.005 | −7.4 pp |

**Maker execution does obtain a better price — 33¢ per contract — and it is
worth less than nothing.** A resting bid on the favoured side gets filled in
9.2% of opportunities, and in those it is right **16.7%** of the time against
86.2% unconditionally. You are not buying the favoured side cheaply; you are
being handed it by someone who knows it is about to lose. The 33¢ discount
does not come close to covering a 69-point collapse in accuracy.

D5/D15 are worse than A on **both** axes at once: a slightly worse average
price (0.847 / 0.815 vs 0.810) *and* worse accuracy (81.6% / 78.8% vs 86.2%).
Waiting five seconds buys nothing and costs both. The mechanism is in the tier
breakdown — when R2 is right the fallback cannot execute:

```
D5   TAKER_FALLBACK 38 | FALLBACK_ABOVE_CEILING 13 | NO_VALID_PRICE 9 | FALLBACK_NOT_EXECUTABLE  5
D15  TAKER_FALLBACK 33 | FALLBACK_NOT_EXECUTABLE 12 | FALLBACK_ABOVE_CEILING 11 | NO_VALID_PRICE 9
```

Eighteen (D5) and twenty-three (D15) opportunities were lost because the price
ran above the original ceiling or the offer vanished entirely — and the
missed-profitable count confirms those were the good ones: **25 of 27** D5
misses and **30 of 32** D15 misses settled correct. B and C miss 55 winners
out of 59 unfilled.

## "NOT_EXECUTABLE" means effectively NEVER — and that is the whole story

The 65 R2 opportunities are, by the rule's own construction, executable at the
qualifying instant. So their time-to-first-executable is 0 s by definition and
tells us nothing. **I am not reporting that as a finding.**

The population that answers the question is the one the rule *excludes*:
instants where `|z| ≥ 1.0` but the favoured side had no offer.

```
z >= 1.0 and favoured side NOT executable:  986 instants, 139 independent windows
  became executable before expiry:            57 / 986  =  5.8%
  NEVER executable:                          929 / 986  = 94.2%
  of the 929 never-executable:               929 settled CORRECT = 100.0%
  of the 57 that did appear:                  47 correct = 82.5%
      delay   p10 3.0s   p50 23.0s   p90 88.1s   max 216.1s
      price   p10 0.40   p50 0.87    p90 0.94
```

**When R2 is right, nobody will sell you the winning side at any price, ever.**
929 instants, 100% correct, zero executable. That is not a timing artifact to
be waited out — it is the market correctly withdrawing a near-certain winner,
and no execution policy can reach it.

The 5.8% that do appear are *worse* than the R2 population (82.5% vs 86.2%) at
a median price of 0.87 after a median 23 s wait. Chasing them is not an
improvement either.

This also reframes R2's own edge honestly: **+0.0377/contract exists only in
the minority of states where a counterparty is still willing to sell the
favoured side**, and those states are precisely the less certain ones —
86.2% correct against 100% in the unreachable ones. The adverse selection
measured on DEV at the aggregate level is, at the instant level, nearly total.

## Capacity and other lines

- Displayed size at the touch: p10 **25**, p50 **681**, p90 **7500** contracts.
  q=1 is never size-constrained; nothing here supports sizing beyond that.
- Fees are modelled from the venue's own formulae and reported as their own
  line: taker `ceil(0.07·C·P·(1−P), ¢)`, maker `ceil(0.0175·C·P·(1−P), ¢)`.
  **No fee here is venue-confirmed** — SEMANTICS-1 established the order
  surface carries no fee field.
- Rebates: `floor(0.021·C·P·(1−P), ¢)` in the 0.20–0.80 band, capped at
  0.525¢. Zero across every policy, as noted.
- Adverse-selection markout after maker fills is reported as the correctness
  gap above rather than a price markout: with a 5-minute binary settling to
  0 or 1, settlement correctness *is* the markout, and a mid-price markout
  would be a weaker proxy for the same thing.

## Recommendation

**Keep the live runner exactly as it is: immediate taker at the frozen
executable price, q=1.** Policy A is not merely the best of the five — it is
the only one that is not negative, and the reason is structural rather than a
sample artifact. Do not add a maker leg, a maker-first leg, or a wait.

Two things I would *not* conclude from this:

1. **This does not validate R2's profitability.** EV/opp +0.0377 here is the
   same in-sample-free prospective figure already reported, with
   `P(mean ≤ 0) = 0.304`. Execution research cannot substitute for the sealed
   100-window gate, and nothing above touched it.
2. **This does not mean maker execution is bad on Gemini generally** — only
   that resting on the *R2-favoured* side selects against you. That is a
   statement about this signal, not about the venue.

R2 unchanged. Live runner unchanged and still waiting.
