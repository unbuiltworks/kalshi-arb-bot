# Adverse selection on our fills — what 17 live fills show

23 Nov 2026. Development analysis only; nothing deployed or changed.
Source: production logs of the two live campaigns (22 Nov NBA, 23 Nov ATP),
extracted to `fills_context.json`; `analyze.py` reproduces every number.
Markouts are marks, not profit. Seventeen fills is a small sample: the
mechanism below is established, its size is not.

## Finding 1: every fill that added exposure was the jump itself

Eight fills opened or added to a side. **All eight happened within about a
second of a price jump of two or more ticks against that side** (or of the
bot's own pull because the price had come within a tick).

- Our bid sits one tick behind the best bid. In these markets the price
  moves in jumps of 2 to 5 ticks on a point or a basket. A bid one tick
  back is filled when a jump passes through it and almost never otherwise.
- Six of the eight had rested **under 15 seconds** (0.5 s, 1.8 s, 2.4 s,
  6.3 s, 8.7 s, 11.4 s). The bot re-posts every 30 to 90 seconds, so its
  orders are always new and always at the back of their price level.
- Five of the eight were placed within a minute after an earlier jump. The
  pause after a jump is 15 seconds; the market was still moving.

So the question is not whether the price was "wrong" when the bid was
placed (the bot's edge test passed each time, at +0.5c). It is that the
fill only happens in the cases where the price has just moved away.

## Finding 2: what those fills were worth afterwards

| Mean mark, per contract | 5 s | 30 s | 2 min | 5 min |
|---|---|---|---|---|
| New exposure (8 fills) | −1.6c | −0.2c | −2.9c | −4.8c |
| Completions (9 fills) | −1.3c | −1.0c | +1.1c | −2.4c |

- At 5 seconds a new-exposure fill is down 1.6c on average: the 1c the
  bot allows for adverse selection plus half a tick. The allowance is
  being used up in full, every time, by construction.
- At 30 seconds the mark is back near zero: part of each jump reverses.
- The 2 and 5 minute means are negative but rest on a few large moves
  (−16.5c, −14.5c, −8.5c against +7.5c, +3.5c). With eight fills the
  spread is far wider than the mean; this is not evidence of a persistent
  loss, and not evidence against one.

## Finding 3: where the money actually came from

Both campaigns made money (+$0.54 and +$0.33) and in both **every dollar
came from completing pairs under the cap**, not from the opening fills.

Put plainly, the controller as it runs is this trade: buy YES when the
price jumps down, buy NO when it jumps up, and collect 3 to 5 cents when
both have happened. It earns when the price swings back and forth and
loses when it keeps going one way and leaves a leg unpaired. Fifteen of
seventeen contracts' worth of fills ended paired, so the swings won. One
trending match with a $1.27 leg left over would have cost four evenings
like these.

This also answers the owner's question from 23 Nov: the evidence so far is
for pair completion, not for accumulation that is profitable by itself.

## Finding 4: rewards may matter more than the trading

Reward rows the venue shows for our markets (the log holds 50 of 187):

- 22 Nov, two Serie A markets quoted in play at two contracts: **$0.52 and
  $0.78, pending.** That is $1.30 for one evening.
- Trading profit of both campaigns together: $0.87.
- No row dated 23 Nov yet. All-time on the account: $22.79 paid, $1.34
  pending, $3.96 skipped.

If those two rows pay, rewards on one evening exceed the trading result of
two. Tennis, where all of 23 Nov's fills came from, has shown no reward
row for our markets in this sample. The ranking should weigh this, but
only on rows that are actually paid; the rule that decides paid against
skipped is still not known.

## What to test, in order (development and shadow first)

1. **Rest deeper, rest longer.** Bid 2 to 3 ticks behind the best and stop
   re-posting unchanged prices. A deeper bid is filled only by a larger
   jump and at a price 1 to 2c better, which is about the size of the 5 s
   loss; an order that is not re-posted keeps its place in the queue and
   its reward-qualifying time. The setting for depth exists
   (`depth_ticks`, now 1). The re-posting is driven by the international
   price flickering in and out; holding an unchanged quote through a
   short gap is the fix, and it is the same change that would raise the
   13% reward-qualifying time. Earlier replay of a keep-the-quote rule
   showed more fills through the bid; it has to be replayed at 2 to 3
   ticks' depth, where a fill through the bid is the intended one.
2. **A longer quiet period before new exposure after a jump** (60 seconds
   instead of 15), completions unaffected. Five of eight bad-timing fills
   fall inside it. Cost: fewer in-play fills; in tennis the price jumps so
   often that this may leave little. Replay on the recorded books before
   anything else.
3. **Measure reversal directly.** For every jump of two or more ticks in
   the recorded in-play books: where is the price 30 s, 2 min and 5 min
   later? That is thousands of events instead of eight fills, needs no
   trading, and says whether buying the jump has an edge at 1, 2 or 3
   ticks' depth. This is the experiment that can settle the question.
4. **Rank by paid rewards.** Use only reward rows with status PAID, per
   competition, per contract-hour quoted, as the reward term in the
   ranking that is already built and off.

Tonight's ATP window adds fills at one contract; the same extraction will
be re-run on them.

## What this note does not show

- That opening fills lose money over time (the sample cannot say).
- That any of the four changes helps. None has been replayed yet.
- What rewards pay for tennis, or why some rows are skipped.

---

## Added 24 Nov, 00:45 UTC: the jump recording, and where the comparisons ended

Two container restarts cut the comparisons short. By the owner's direction
they are not being extended.

**Jump recording** (public prices every 2 s, in-play markets, 23:37 to
00:38 UTC, mostly basketball; `jumps_20261123_2337_0038.jsonl.gz`,
`jump_reversal_result_20261124.txt`). A modelled bid k ticks behind the
best price, counted as filled when the price moves strictly through it:

| Depth | Events | 5 s | 30 s | 2 min | 5 min |
|---|---|---|---|---|---|
| 1 tick | 258 | −2.1c | −2.1c | −1.7c | −1.2c (± 0.6) |
| 2 ticks | 155 | −2.1c | −2.0c | −1.5c | −1.5c (± 0.8) |
| 3 ticks | 100 | −2.1c | −2.2c | −2.1c | −3.0c (± 1.0) |

- The first seconds are partly built into the model (a bid only counts as
  filled once the price is below it). What matters is the recovery, and
  there is little: after five minutes the modelled fill is still about 1
  to 3 cents under water.
- **Bidding deeper did not help.** A deeper bid is filled only by a larger
  jump, and larger jumps left it just as far under water. On this evidence
  there is no case for changing the quote depth, and it stays at one tick.
- So the live picture holds on a larger sample: a fill taken on a jump
  does not pay for itself by the price coming back. In the two campaigns
  the profit came from the opposite jump arriving later and completing the
  pair.
- Limits: about an hour, one evening, mostly basketball, no tennis to
  speak of; a model of the fill with no queue; no rewards.

**Side-by-side dry runs:** about 15 minutes each survived, with no
cancels to compare. Insufficient; no conclusion drawn. The two persistence
rules rest on their tests and on the live observation of 23 Nov, stay off
by default in the release candidate, and are for the owner to switch on.

**What this changes for the 12-hour campaign:** nothing in its settings.
It sharpens what the campaign has to show: that unpaired inventory is
rare and small enough, and paid rewards large enough, to cover fills that
start about 2 cents behind.
