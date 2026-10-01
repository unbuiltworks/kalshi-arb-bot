# q1 ground-truth validation against the authenticated 19Z tape

Accounting-repair checkpoint `8784bfdc`. Tape `pmus_ws_20260930_19.jsonl`
(128,845,660 B), both gz and raw SHA256 verified. Episode: the +$0.0600 REALIZED
q1 probe on `cpc-btc-updown-15m-2026-09-30-1915z`. Calibration campaign remains
**NOT ARMED**; no authorization requested.

## 1. What the q1 episode VALIDATES

**Maker fill prediction — TRUE POSITIVE.** Our real maker was BUY YES 1 @ 0.3500
(`CTJ1B3GJTYBQ`, `ORDER_STATE_FILLED`). The tape shows 0.3500 was the best bid
across **281 book updates**, from t+53.02s to t+778.46s into the window, with
**40,795.37** of SELL-aggressor volume printing at or through 0.3500. Displayed
quantity at our exact price was a median of 932.52 (min 0.32, max 3050.00), so
the queue ahead of us was large but was consumed many times over. Given only
tape-available information, the simulator correctly predicts a fill.

**Reactive TAKE price availability — CONSISTENT.** The complement was taker NO 1 @
0.5700, i.e. a YES ask of 0.4300. That ask is present in **215 book updates**,
first at t+0.91s, so a fee-safe crossing was genuinely available as the live run
recorded.

**Ordinary resting persistence — CONSISTENT.** The live run reports
`holds 36, requotes 0, cancels 0`. The tape shows 0.3500 remaining the best bid
over 281 updates spanning ~725s, so holding the level was the correct behaviour
and the engine's refusal to chase matches what the book did.

**Fee, inventory and settlement accounting — ALREADY VALIDATED.** Venue
commission matched `pmus_fees.py` with `cents=True` exactly (taker NO @ 0.57 →
0.02; maker YES @ 0.35 → 0.00), settlement was `MARKET_STATE_EXPIRED` with
settlementPx 0.0000, and the balance delta was +$0.06000 against a pair cost of
0.9200. The repaired `_dispose()` invariants do not disturb this episode because
no flatten occurred in it.

## 2. What the episode CONTRADICTS — a THIRD defect, and it is large

**DEFECT C: the fill model ignores aggressor side, which the tape actually
provides on every single trade.**

`on_trade` consumes `leg.ahead` from ANY print at or through our price. But a
print's aggressor determines which side of the book it consumed: a BUY-aggressor
lifts OFFERS and cannot consume our resting BID queue. On this one window, at or
through 0.3500:

| | prints | quantity |
|---|---|---|
| what the frozen model counts | 3,753 | 126,301.99 |
| SELL-aggressor — actually consumes BID queue | 1,482 | 40,795.37 |
| BUY-aggressor — lifts offers, must NOT count | 2,271 | 85,506.62 |
| **over-count** | | **67.7% of volume** |

The tape carries `trade.taker.side` and `trade.maker.side` on **6,874 of 6,874**
trades in this window, so this is not a data limitation -- the simulator simply
never looked. Direction of the error: queue is consumed far too fast, so resting
orders fill far too easily, in EVERY arm and EVERY batch. Combined with the
`ahead0 = 0`-from-absence artifact, replay fills were systematically too generous.

Not patched. This is reported, not fixed, per instruction.

## 3. What remains UNVALIDATED

* **Resting-maker touch-through semantics — THE central question.** Our maker sat
  at 0.3500 and the book moved UP to 0.43/0.44; it fell BEHIND the touch and was
  never reached from above. The episode contains zero crossed-resting cases, so it
  cannot validate what PMUS does to a resting maker the market moves through, nor
  the maker/taker role such a fill would carry.
* **Fill timing.** The venue fill timestamp is not in our episode record and the
  recorder carries no private-order stream, so "did the simulator fill at
  approximately the right moment" cannot be scored from this episode -- only
  "fill vs no fill".
* **Partial-fill sequencing of our own order.** Quantity 1 cannot partially fill.
* **True negatives.** This episode has no real non-fill or cancel, so the
  false-fill rate cannot be measured from it at all.
* **Queue position we actually held.** Displayed quantity at our price is
  observable, but our place within it is not.

The corpus is two orders from one window. It does not support a general claim
about the simulator's fill realism.

## 4. Is the authenticated event ordering sufficient for the live campaign? YES

Measured on this window:

| property | value |
|---|---|
| book updates | 8,382 per 15m window = **558.8/min**, median gap **0.101s** |
| ladder depth | 18 bids / 21 offers (full depth, not top-of-book) |
| receipt timestamp | microsecond, e.g. `2026-09-30T19:15:00.802777+00:00` |
| venue trade timestamp | **nanosecond** `tradeTime` |
| venue book timestamp | `transactTime` on every `marketData` |
| trade identity | per-trade `id` |
| **aggressor identification** | **6,874 / 6,874 trades** carry `taker.side` + `maker.side` |
| displayed quantity at an arbitrary price | available from full depth |

That is enough to order book transitions against prints, attribute each print to
the side of the book it consumed, and reconstruct how much queue sat at our price
and when it was eaten -- which is exactly what a touch-through observation needs.

**One gap the campaign must close itself:** the recorder captures market data
only. Our own order's private state transitions are NOT in this tape; they come
from the executor. The preflight already requires capturing them, and the two
streams must be joined on venue timestamps. Without that join, a live episode
would be as unscorable for timing as this one is.

## Status

No simulator repair beyond the accounting fix. No replay economics re-run. No
strategy change. Calibration campaign NOT armed. Stopping for authorization.
