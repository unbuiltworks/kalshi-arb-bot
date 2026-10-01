# What makes L5 EXTEND instead of COMPLETE? Evidence from 104,416 decisions

Source: L5 public activity 2026-09-01..09-10, 861 BTC15m markets, 105,302
grouped actions (fill fragments sharing (ts, slug, outcome, price) merged into
one order). Clone benchmark `49799e99` untouched.

NOT AVAILABLE, never inferred: maker/taker role, placements, cancels, requotes,
resting quotes, book depth. The feed is fills only.

## Headline: the EXTEND/COMPLETE choice is very nearly UNPREDICTABLE

`P(EXTEND) = 0.5090` over 104,416 EXTEND/COMPLETE decisions. No state variable
reconstructible from his own fills moves it far from a coin flip:

| variable | range of P(EXTEND) across bins |
|---|---|
| absolute residual (0 -> 400+) | 0.495 - 0.569 |
| seconds left (0 -> 900) | 0.489 - 0.527 |
| effective pair cost (<0.90 -> >1.05) | 0.489 - 0.560 |
| gap since last action | 0.463 - 0.526 |
| matched inventory | 0.444 - 0.740 |

**Residual magnitude -- the obvious candidate -- is almost irrelevant.** At
residual 0-1 P(EXTEND)=0.545; at 150-400 it is 0.495. He does not extend
*because* he is imbalanced, nor complete *because* he is imbalanced.

Best single conditions (delta vs complement):

| condition | n | P(EXTEND) | delta |
|---|---|---|---|
| no opposite-side inventory yet | 3,414 | 0.748 | +0.247 |
| price <= 0.10 | 13,097 | 0.643 | +0.153 |
| price >= 0.90 | 6,766 | 0.368 | -0.151 |
| previous action same side | 70,135 | 0.552 | +0.131 |
| price below own same-side basis | 60,338 | 0.552 | +0.102 |

## A hypothesis I tested and must REJECT

The price lifts suggested "L5 buys whichever outcome is cheap". On a binary,
price < 0.50 identifies the cheaper side, so this is directly testable. It fails:

- he bought the cheaper side in only **55.8%** of actions;
- `P(EXTEND | cheap side) = 0.5056` vs `P(EXTEND | expensive side) = 0.5133` --
  indistinguishable.

"Buy the cheap side" predicts the observed side barely better than chance, and
no better than "buy the deficient side" (0.491). Recorded as rejected.

## What the evidence DOES support: the label is emergent, not chosen

Three measurements together:

1. **7.09%** of the 76,035 active seconds contain fills on **BOTH outcomes in
   the same second**. A sequential "choose a side" policy cannot produce that.
2. He is present for **88.3%** of the window: median first action 10s in, median
   last at 816s of 900, median span 795s, median 113 actions per market.
3. **69.7%** of inter-action gaps are <= 5s; p99 = 54s.

This is the signature of **continuous, simultaneous, two-sided resting quotes
held for essentially the whole window**. Under that structure, which side fills
next is decided by order flow, not by L5. So:

> EXTEND vs COMPLETE is not a decision L5 makes. It is a LABEL WE ASSIGN
> afterwards to whichever side the market happened to hit.

That single structure explains every otherwise-puzzling number: P(EXTEND) ~ 0.5
by construction; near-independence from residual and time; and imbalance in the
hundreds of contracts as an EMERGENT consequence of asymmetric fill rates rather
than a tolerated risk choice.

It also explains why "only extend if basis improves" cannot describe him: 37.3%
of his EXTENDs worsen his basis because he never decided to extend at all. The
basis-worsening ones are simply the high-priced fills -- median price 0.72 for
worsening vs 0.22 for improving -- i.e. the side that became the favourite.

## Smallest rule set that explains the largest discrepancies

Note what is ABSENT: no EXTEND trigger is required.

1. **Rest quotes on BOTH outcomes continuously for the whole window** (not
   one leg then its complement).
2. **Do not gate acquisition on basis improvement.**
3. **Let directional residual float far** -- it is an output, not an input.
4. Size (last, per the PATIENCE -> FREQUENCY -> SIZE order).

Our frozen clone already does (1) in REPLICATION form, but `clip=1` and
`residual_cap=2` make (3) impossible, and the extend gate makes (2) impossible,
so it cannot express the structure at all.

## Open questions this cannot answer

- Whether he reprices or lets quotes rest (needs order data we do not have).
- Maker/taker split (4 MAKER_REBATE and 7 rebate rows in 78,000).
- Response to adverse vs favourable moves (needs a venue price path).
- Settlement-conditioned behaviour (REDEEM presence only).

## Next

The 2026-09-27..09-30 harvest is in flight and will allow re-measuring
`same_side_share_of_followups` and the both-sides-same-second rate on the
PMUS-overlapping period, plus window-level (NOT same-book) cross-venue
sequential comparison.
