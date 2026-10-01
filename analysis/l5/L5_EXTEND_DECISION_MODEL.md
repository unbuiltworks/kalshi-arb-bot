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

---

# REPLICATION on the PMUS-overlap period (2026-09-27..09-30)

Harvested with `scripts/l5_harvest_overlap_activity.py`: 41,602 rows
(TRADE 40,599, REDEEM 995, MAKER_REBATE 4, TAKER_REBATE 4).

**Completeness verified independently**, not merely asserted by the harvester:
all **96 of 96** hours carry rows, **zero empty hours**, no hour sits at an exact
multiple of 500 rows (the paging-truncation signature), max 1,758 rows/hour
against a 4,500 offset-suspect threshold, so **0 subdivisions and 0 untrusted
slices** were needed. Days: 09-27 14,555 / 09-28 9,175 / 09-29 8,576 /
09-30 9,296.

**This period overlaps the PMUS archive**, so window-level cross-venue comparison
is now possible for the first time.

| finding | Sep 01-10 | Sep 27-30 (overlap) | survives? |
|---|---|---|---|
| P(EXTEND) | 0.5090 | **0.5124** | yes -- coin flip |
| P(EXTEND) across residual bins | 0.495-0.569 | 0.449-0.555 | yes -- residual-independent |
| same-side share of follow-ups | 0.672 | **0.630** | yes |
| EXTENDs that improve basis | 0.451 | **0.459** | yes -- about half |
| fills on BOTH outcomes in the same second | 7.09% | **8.86%** | yes, higher |
| window coverage | 88.3% | 85.7% | yes |
| actions per market (median) | 113 | 72 | yes, lower |
| REOPEN count | 25 / 861 markets | 60 / 384 markets | yes -- continuous book |
| median abs residual before action | 140.4 | **63.7** | directionally yes |
| median size | 18.3 | 20.0 | yes |

Residual and action rate are roughly half the September 1-10 levels, so activity
scale varies by period. Every QUALITATIVE property replicates, including the two
that matter most: the EXTEND/COMPLETE label is a coin flip independent of
inventory, and L5 holds simultaneous two-sided exposure (now 8.86% of active
seconds show both outcomes filling within one second).

Median residual of 63.7 contracts still exceeds the frozen clone's observed
maximum of 1 by ~64x.
