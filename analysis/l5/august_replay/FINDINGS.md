# What made frozen S1 diverge from L5 — one August BTC15m window, second by second

**DIAGNOSTIC HISTORICAL REPLAY ONLY.** Not validation. Not part of the
prospective 25-market Shadow S1 cohort. **These artifacts must not be used to
modify Candidate S1 while that cohort is running.** Candidate S1 was read,
never written; the fingerprint was asserted before the first tick and matched:
`441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe`
(module file sha256 `50a1dd1b85e0…`).

## 0. The window, and why this one

`btc-updown-15m-1787300100` — **2026-08-21 08:15:00Z → 08:30:00Z**.

Chosen by gates fixed *before* any replay, none of which mentions S1
(`select_window.py` imports no candidate and has no notion of a fill or a
P&L). 12 of the dataset's 33 BTC15m windows are ones L5 traded; 9 passed all
five gates; this one ranked first on the only ranking key, fill count.

| | |
|---|---|
| L2 seconds resolvable, both tokens, carry ≤ 60 s | **900 / 900** |
| chain chunk holes | **0** |
| L5 fill legs in window | **398** |
| L5 fills per 180 s sub-interval | **85, 89, 38, 102, 84** |
| fill mechanisms present | 305 resting-bid-consistent **and** 91 mint-consistent |

The backfill reproduced the prior verified analysis exactly: **12** windows
traded, **3,166** L5 fills, **0** holes, **1,695 / 1,695** blocks with real
timestamps (interpolation residual p50 = 0.0 s, range −0.5..0.0).

## 1. The result

Arm A is the headline: it reproduces what the frozen code actually does.

| | L5 (actual) | S1 (frozen, replayed) |
|---|---:|---:|
| fill legs | **398** | **10** |
| shares acquired | 9,855.33 | 236.10 |
| UP inventory | 4,813.44 | 70.83 |
| DOWN inventory | 5,041.89 | 165.27 |
| **matched inventory** | **4,813.44** | **70.83** |
| residual | 228.45 | 94.44 |
| residual as % of matched | 4.75 % | **133 %** |
| UP VWAP | 0.7653 | 0.4100 |
| DOWN VWAP | 0.2258 | 0.4571 |
| pair cost (VWAP sum) | 0.9911 | 0.8671 |
| orders placed | n/a | **11** |

S1's uncertain verdicts — recorded, **excluded from economics**:
`UNCERTAIN_PARTIAL` ×5. `QUEUE_CLEARED_FRONT_OF_BOOK` ×0.

**S1's 0.8671 pair cost is not an achievement and must not be read as one.**
It is the sum of two VWAPs over *all* inventory, while only 70.83 shares are
matched; the other 94.44 DOWN shares are an unhedged directional position.
Post-hoc (settlement is **never** used by the replay): this market resolved
**Up**, so that residual paid $0.

| settlement, post-hoc | spend | payout | net |
|---|---:|---:|---:|
| L5 | $4,822.08 | $4,813.44 | **−$8.64** |
| S1, arm A | $104.59 | $70.83 | **−$33.76** |
| S1, arm B | $115.93 | $70.83 | **−$45.10** |

Fees and gas are not modelled; these are fill prices against settlement.

## 2. The first material divergence

**t = 1787300134, i.e. 00:34 into the window (TTE 866 s)** — the first second
at which L5 holds a matched pair and S1 holds none. The marker was defined
before it was looked for, and the others are reported beside it:

| marker | at |
|---|---|
| `D1` first L5 fill | 00:07 |
| **`D2` L5 matched while S1 zero** (designated) | **00:34** |
| `D3` first S1 *evidenced* fill | 00:02 |
| `D4` residual gap ≥ one leg | 00:02 |

**S1 is not slow.** Its first evidenced fill is at 00:02, five seconds *before*
L5's first fill. The divergence is not latency. It is what happens next.

## 3. What happens afterward — the whole tail is one event

S1's eleven orders and ten fills, in full:

```
00:00 PLACE up  @0.440 (queue ahead 4.46)   00:00 PLACE down @0.550 (15.44)
00:02 FILL  up  @0.440  LEVEL_CONSUMED  ->  PLACE up @0.400 (55.00)
00:15 FILL  up  @0.400  LEVEL_CONSUMED  ->  PLACE up @0.390 (45.00)
00:24 FILL  up  @0.390  LEVEL_CONSUMED  ->  PLACE up @0.380 (160.00)   <-- last UP quote
00:41 FILL  down@0.550  LEVEL_CONSUMED  ->  PLACE down @0.540 (57.04)
00:46 FILL  down@0.540  LEVEL_CONSUMED  ->  PLACE down @0.500 (10.00)
00:58 FILL  down@0.500  LEVEL_CONSUMED  ->  PLACE down @0.490 (304.20)
01:31 FILL  down@0.490  LEVEL_CONSUMED  ->  PLACE down @0.460 (5.20)
01:41 FILL  down@0.460  LEVEL_CONSUMED  ->  PLACE down @0.340 (163.80)
02:59 FILL  down@0.340  LEVEL_CONSUMED  ->  PLACE down @0.320 (10.00)
03:19 FILL  down@0.320  LEVEL_CONSUMED  ->  residual 94.44 >= cap 80: DOWN GOES DARK
```

Every single fill carries the same evidence tag, and every re-quote is at a
**lower** price than the one before. After 03:19 **nothing happens again**:

| at | TTE | UP touch | S1's UP order | DOWN quoted | L5 matched | S1 matched |
|---|---:|---:|---|---|---:|---:|
| 03:19 | 701 | 0.75 | 0.3800 | — | 1,217.95 | 70.83 |
| 06:40 | 500 | 0.71 | 0.3800 | — | 1,820.02 | 70.83 |
| 10:00 | 300 | 0.68 | 0.3800 | — | 2,287.97 | 70.83 |
| 14:59 | 1 | **0.999** | **0.3800** | — | **4,813.44** | **70.83** |

**701 of the window's 900 seconds — 78 % — are spent in that state.** L5
books 305 more fill legs during them, 164 of them DOWN. S1 books zero.

## 4. The mechanism, in the order it bites

### (a) The only reachable fill evidence *is* adverse selection

`last_prints` is initialised empty in the frozen module and **never
assigned**, so `cum` is always 0 and `EVIDENCED_TRADE_THROUGH` is unreachable
— live S1 polls `GET /book`, which carries no trades. That leaves
`EVIDENCED_LEVEL_CONSUMED`, which fires **iff** the best bid falls *strictly
below* S1's own resting price.

A resting bid is only provably consumed, under that standard, **when the
market moves against it.** So S1 can recognise only its adversely-selected
fills. All ten are `LEVEL_CONSUMED`; the five benign ones — size leaving its
level with the touch holding — are `UNCERTAIN_PARTIAL` and excluded by design.
Rate: **10 per 1,099 resting side-ticks (0.9 %)**.

This is visible in the prices. S1 bought DOWN at VWAP **0.4571** while L5
bought DOWN at **0.2258**: S1 paid **twice as much** for the losing side,
because it only ever got filled on the way down.

### (b) A single 23.61-share order per side, re-quoted only *after* a fill

`levels: 1`, `quantity_shares: 23.61`, `order_lifetime_s: None`,
`replenish_after_evidenced_fill: True`. Throughput is therefore capped at
23.61 × (evidenced fills) = **236 shares**, against L5's 9,855 — a 42×
ceiling that no price improvement could lift.

Worse, because the order is replaced *only* on an evidenced fill and never on
elapsed time, **a quote that stops filling is never re-priced.** S1's UP order
was placed at 00:24 at 0.3800 and rested there for the remaining 876 seconds
while UP's touch climbed 0.38 → 0.999. It ends the window stranded **62 cents
below the market**, permanently un-fillable and permanently un-replaced,
because the only trigger for replacement is the fill it can no longer get.

### (c) The residual cap converts one-sided accumulation into a full stop

Adverse-selection fills arrive one-sided by construction — the side the market
is leaving. S1 took 7 DOWN against 3 UP, residual reached **94.44 ≥ 80** at
03:19, and `residual_control: QUOTE_SUPPRESSION_ONLY` removed the DOWN quote
for every one of the remaining 701 ticks. With suppression as the *only*
control and `no_aggressive_opposite_side: True`, there is **no mechanism that
can reduce a residual** once incurred. The cap is a latch, not a throttle.

So both sides are dead for 78 % of the window, for two different reasons: UP
stranded at a stale price, DOWN suppressed by the cap.

### (d) Underneath all of it: 27.5 % of L5's volume was never available to S1

**91 of L5's 398 legs (2,711 of 9,855 shares, 27.5 %)** priced at or above the
prevailing best ask one second earlier *and* were mint-feasible. A resting bid
cannot price above the ask; these are complete-set mints, which need no offer
on the buyer's own book — only a counterparty bidding the complement at
≥ 1−p. S1's action set is one resting bid at the touch. **A mint is not in
it.** The two touches summed to ≈0.99 for essentially the whole window (a mint
on the touches alone was feasible on only 34 of 900 seconds), so L5's near-par
pairing is being built with a primitive S1 does not have.

### Answer

**The observable mechanism is that S1's single reachable fill-evidence
standard selects exclusively for adverse fills, and its replenishment and
residual rules then latch that selection into a permanent stop.**

Concretely, and in the order observed: S1 can only prove a fill when the touch
moves through its own bid, so it buys only the side the market is abandoning
(DOWN at 0.4571 against L5's 0.2258); each such fill re-quotes it one step
lower, so it rides the book down; these fills are one-sided, so the residual
hits the 80-share cap at 03:19 and quote-suppression — the only residual
control — takes that side dark for the remaining 78 % of the window; and the
other side, replaced only on a fill it can no longer earn, stays stranded at
0.3800 while the market runs to 0.999. Separately and structurally, 27.5 % of
L5's volume came through the complete-set mint, which is not in S1's action
set at all.

### What it is *not*

* **Not latency.** S1's first evidenced fill precedes L5's first fill by 5 s.
* **Not the dead `TRADE_THROUGH` standard.** Arm B supplies the real chain
  prints (2,355 maker-order legs, 0 holes). `TRADE_THROUGH` then fires 5
  times, all ten fills land by 00:52 instead of 03:19 — and the outcome is
  **worse**, −$45.10 against −$33.76, because faster evidence means faster
  loading of the falling side. Final inventory, matched, and residual are
  identical. Repairing that defect does not repair the divergence; it
  accelerates it.
* **Not a quoting gap.** S1 had an order resting on at least one side in all
  900 seconds, and UP was quoted in 900 of 900.
* **Not a data artifact.** 900/900 seconds resolvable, 0 chunk holes on both
  the L5 and the all-trader scans, real block timestamps throughout.

## 5. Guard rails that held

* **No crediting S1 with L5's fills** — separate ledgers; S1 inventory moved
  only on an `EVIDENCED_*` verdict on S1's own order.
* **No touch = fill** — uncertain verdicts recorded and excluded.
* **No future information** — `Book.at(t)` reads at or before `t`, prints from
  `(t-1, t]`, tape walked strictly forward. Settlement (§1) is labelled
  post-hoc and was never read by the replay.
