# Independent review request — one anonymous trader's executed decisions

You are given the complete executed trading record of a single anonymous
participant on binary prediction markets: **760,817 fills across 17,046
markets, 2026-03-19 to 2026-08-31 UTC.** Files and field definitions are in
`DATA_DICTIONARY.md`.

We have our own analysis of this trader. **It is deliberately withheld.**
We want your independent reading, including the parts where you conclude
the data cannot answer the question.

## Structural facts you may rely on

- Each market is binary: sides `A` and `B` are complementary, exactly one
  pays 1.00 per share at resolution.
- One share of A plus one share of B therefore pays exactly 1.00 regardless
  of outcome; the cost of that pair is `price_A + price_B`.
- Every recorded fill is a **buy**. There are no sells; positions resolve.
- The corpus contains **fills only**. Unexecuted orders, cancellations and
  maker/taker status are not observable.

## Questions

1. **What is the policy?** Describe the decision rule this trader appears to
   follow, as a function of state you can actually compute from the data
   (inventory in the market, prices paid so far, position in the market's
   window, time since the previous fill). Be specific about what triggers
   the next fill, on which side, at what size.
2. **Where does the money come from?** Decompose the economics: matched-pair
   cost, directional residual exposure, and anything else you can identify.
   Quantify each. If you cannot separate them from this data, say so.
3. **Is there a regime change?** The corpus spans a mid-period date of
   2026-06-23. Test whether behavior differs before and after, and describe
   the difference without assuming one exists.
4. **How predictable is the next action?** Given a fill and the state it
   produced, how well can the next action in that market be predicted —
   same side, opposite side, or none? Report out-of-sample accuracy against
   explicit baselines (majority class, prior-matching, a simple
   pair-completion heuristic), and hold out by market as well as by time.
5. **What is this trader exposed to?** Characterise the risk actually
   carried: size and duration of directional residuals, concurrency across
   markets, worst observed cases.
6. **What cannot be determined from this data at all?** Be explicit and
   exhaustive. We care about this answer as much as the others.
7. **Would this policy transfer?** State the market conditions — spread,
   depth, flow, resolution mechanics — under which the policy you described
   would and would not work, and what you would need to measure on a
   different venue before risking capital there.

## Ground rules

- Show the computation behind each quantitative claim.
- Distinguish OBSERVED facts from INFERRED behavior, every time.
- Report null and negative results. "The data cannot distinguish X from Y"
  is a finding, not a failure.
- Do not assume the trader was profitable. Do not assume they were not.
- If you believe the structure implies a mechanism you cannot verify here,
  name the additional data that would test it.
