# Realtime L5 → PMUS BTC15m copyability — first evidence

**Running now.** Read-only, shadow only, no order on either venue, no
authorization. The PMUS public collector, the authenticated-recorder check-in,
Gemini B2 and Kalshi are all unchanged. Sample below is a snapshot at
2026-09-23T15:39Z; the observer keeps collecting across rolling windows.

---

## The question this answers, and why history cannot

A backtest sees L5's 15:13:19 trade and PMUS's 15:13:19 book together. **We do
not.** We see the trade when the feed publishes it, and the only honest PMUS
state to price it against is the one visible *at that moment*. So:

> when we LEARN what L5 actually did, is a comparable PMUS action still there?

`src/l5_pmus_realtime_observer.py` answers it with three clocks on every row —
L5's event time, our receipt time, and the age of the PMUS snapshot we priced
against — and the snapshot is taken **at receipt, never at the event**.

### Integrity, asserted on every row

| | |
|---|---|
| `no_backdating_ok` on all 283 actions | **true** |
| window alignment verified on all actions | **true** |
| PMUS snapshot age at pricing, p90 | **0.0 s** |

**Alignment was verified, not assumed.** Both venues stamp the window *start* —
but that is exactly the thing worth checking, because Kalshi stamps the *close*
in the same position and an off-by-one-window comparison would look plausible
and be entirely wrong. `verify_alignment` reads PMUS's own `startTime` back and
compares it to L5's epoch. Confirmed on every window seen.

---

## 1 · L5 is trading BTC15m right now, heavily

| | |
|---|---|
| actions observed | **283** (107 realtime, 176 backlog) |
| windows | 5 |
| actions per window | **56.6** |
| sides | DOWN 162 / UP 121 |
| relations | **open 24 · extend 145 · complete 114** |
| **P(extend)** | **0.512** (L5 BTC15m historical: 0.66) |
| **sells** | **0** — consistent with the corpus's zero sells |

The OPEN → EXTEND → COMPLETE vocabulary is firing on live data, with EXTEND
dominant, exactly as the behavioural thesis says.

**Backlog is reported separately and never mixed in.** The first poll sees
every action the feed still holds, including windows closed hours ago; their
"latency" measures when the observer started. They count toward L5's behaviour
and are excluded from every copyability and latency figure — including on
restart, or hydration would reimport the distortion.

## 2 · Observation latency — we learn fast

| p50 | p90 | p99 | max |
|---|---|---|---|
| **21.5 s** | 44.4 s | 182.4 s | 183.4 s |

Half of L5's actions reach us inside 22 seconds of his trade, on a 900-second
window. Latency is not the binding constraint.

## 3 · Copyability at receipt — the answer is *sometimes*

| class | n | % |
|---|---|---|
| **MAKER_COPYABLE** — we could rest at his price without crossing | 35 | **32.7%** |
| **REQUIRES_CROSSING** — reaching his price means taking | 34 | **31.8%** |
| BAND_INADMISSIBLE — outside the frozen 0.20–0.80 | 16 | 15.0% |
| NO_BOOK — PMUS unreadable or one-sided at that instant | 22 | 20.6% |
| same PMUS side available at all | | **79.4%** |
| economically inadmissible (band + no book) | | **35.5%** |

**Source → PMUS price difference** (L5's price minus our own touch):
p10 −$0.05 · **p50 +$0.02** · p90 +$0.10 · mean +$0.027.

So the median L5 action arrives two cents *above* our bid — close enough to
rest at, which is why a third is still maker-copyable, and close enough that
another third has already been crossed through.

## 4 · Repair availability

Of the 2 windows that produced any copyable action, **2 later produced a
copyable action on the opposite side** (100%). Small n, but it is the first
direct evidence that the opposite side of an L5 sequence is reachable on PMUS
in realtime rather than in theory.

## 5 · Three tracks, never summed

**ACTUAL_L5** — his own published inventory. Live windows show real recycling:
one window ran to residual −24 with 6 matched pairs at an effective pair cost
of **0.9950**.

**MIRROR_RAW** — his action translated to a PMUS q=1 intent, advancing only
where it was copyable at receipt:

| | |
|---|---|
| complete mirrored sequences | 2 |
| matched pairs | **16** |
| matched cost | $14.1586 → **avg pair cost 0.8849** |
| matched P&L | **+$1.8414** |
| residual | 3 units, **deliberately unmarked** |
| maker rebate accrual | $0.0988 |
| taker fees | $0.0000 |
| liquidity rewards | **$0.00** — eligibility and allocation not established |

> **THE ASSUMPTION, stated on the line itself:** MIRROR_RAW advances on every
> action that was MAKER_COPYABLE at receipt. That assumes we could have rested
> at L5's price *and been filled as he was*. **No PMUS fill has been observed
> and the public book cannot supply one.** Every pair and P&L figure inherits
> that assumption, and none of it is realized.

An avg pair cost of **0.8849** is well under $1 and materially better than the
frozen clone's 0.9805 — but it is 16 assumed pairs across 2 windows, and it is
the assumption, not the venue, doing a lot of that work.

**CLONE_MODEL** — the frozen engine from PMUS state alone, knowing nothing of
L5's feed: 58 QUOTE (all `open`), 11 HOLD (all `entry_band`).

> It stays at OPEN **by construction**: it advances inventory only on fill
> evidence, and the public book supplies none. That is a limit of the FEED, not
> a finding about the model, and it is the same wall everything else hits.

## 6 · Interaction evidence

Frozen classes, unchanged. The public book reaches only `NO_INTERACTION` and
`QUEUE_AMBIGUOUS`; `TRADE_AT_PRICE`, `TRADE_THROUGH_PRICE` and
`OBSERVABLE_FILL_PLAUSIBLE` are structurally unreachable and their absence is
**not** evidence of absence. `is_fill` is False everywhere.

When the authenticated tape arrives it attaches to this running analysis
without touching the class definitions, and `audit_dir_vocabulary` must clear
the aggressor field **before** any interaction count is believed.

---

## What this already tells us

1. **Latency is not the problem.** p50 21.5 s on a 15-minute window.
2. **The instrument is genuinely parallel.** Alignment verified; the same side
   is available 79.4% of the time; L5's price lands a median two cents from our
   own touch.
3. **About a third of L5's actions are still maker-copyable when we hear about
   them** — and about a third have already been crossed through. That ratio,
   not latency, is the real constraint on copying.
4. **The inherited band costs 15% of his actions outright.** Recorded as
   binding; **not** widened.
5. **Everything downstream of "would it have filled" is still an assumption**,
   on every track, for the same single reason: no authenticated PMUS trade
   tape yet.

## What is still missing for the gate

Only one thing, and it is unchanged: **observed PMUS interaction**. Until the
authenticated recorder delivers a trade tape, MIRROR_RAW's pairs are assumed,
CLONE_MODEL cannot leave OPEN, and no manifest is warranted. No strategy
parameter was optimized against this sample and none was changed.
