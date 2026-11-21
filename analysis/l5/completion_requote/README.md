# Completion repricing: current vs proposed, on recorded live books

Source: 284 live `L5_RESIDUAL_BOOK_SAMPLE` records from the four settled
BTC15m windows of 2026-10-28, preserved in `tape_20261028.jsonl`. Shadow
(`L5_SHADOW_RESIDUAL_BOOK_SAMPLE`) records are MODELLED and are excluded.

Reproduce with `python3 -I analysis/l5/completion_requote/compare.py`.
Raw output is in `COMPARISON_20261028.txt`.

## What the four windows cost

Settled net **-$2.0463**: matched surplus **+$0.8571** against stranded
residual losses **-$2.9034**. All four windows stranded UP and UP settled
worthless in all four.

## Quote eligibility, over each residual's whole life

|window|samples|touch ok, opening cap|touch ok, completion ceiling|
|---|---|---|---|
|1215z|57|29/57|56/57|
|1245z|88|**5/88**|**78/88**|

The opening cap refused a touch-priced completion in 83 of 1245z's 88
sampled books. Judging that from the single instant the engine happened to
evaluate (12:52:25, where the two-tick quote cleared at a 0.95 pair) is what
led me to call the ceiling change inert; over the residual's life it is the
binding constraint.

## How far the resting quote drifted

|window|resting own px|ticks behind touch|level ever reached|
|---|---|---|---|
|1215z|0.47|median 12, max 50|yes, 8 of 11 samples of its life -- and it still did not fill|
|1245z|0.63|median 18, max 35|never|

Placed `depth_ticks` behind and then FROZEN while the market ran. 1215z is
the instructive case: reaching a level is not a fill, because the venue
publishes no per-order queue position and we were behind the size already
resting there.

## Marketable time, current vs proposed

"Marketable" means our quote sat AT the touch, a price that *can* trade. It
is not a fill and no fill is claimed anywhere here.

|window|current (frozen)|`improves` only|**with redemption cap**|
|---|---|---|---|
|1215z|5/57 (9%)|45/57 (79%)|**36/57 (63%)**|
|1245z|3/88 (3%)|71/88 (81%)|**12/88 (14%)**|
|requotes|-|2 / 10|**1 / 1**|
|final own px, 1245z|0.63|**0.95, a 1.27 pair**|**0.68, a 1.00 pair**|

### Why the better headline was rejected

`improves` alone walked the 1245z quote to own 0.95 against a 0.32 basis: a
pair costing 1.27 for something that redeems 1.00. It passes `improves`
because a certain -$0.68 does beat the -$0.7264 strand -- but it buys
$0.046 of worst-case relief with $2.16 of fresh cash. That is chasing a
falling market to increase fills, which the operating model forbids.

The policy therefore also caps the completion at `1 - realised basis`, so
the matched portion can never settle at a loss however far the market runs.
1245z's modest 3% -> 14% is the honest consequence: by mid-window UP had
collapsed so far that no non-loss-making completion existed, and carrying
the residual was the correct decision.

## What this does NOT establish

- No fill is demonstrated. Marketable time is a necessary condition, not a
  sufficient one, and 1215z shows a reached level failing to fill.
- No reduction in the -$2.9034 is demonstrated. That requires live fills.
- 1245z's late residual was unrescuable at any non-loss price. Some
  stranding is a pricing outcome, not an execution defect.
