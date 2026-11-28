# Campaigns C & D — actual-inventory lifecycle reconstruction

Scope: every **real venue fill** in the two authorized campaigns of 2026-11-04.
No shadow inventory is used anywhere in this document. Every price below is an
**own-outcome** price (wire is YES/UP; DOWN own = 1 - wire).

Sources: `L5_LIVE_LIFECYCLE`, `L5_FILL_CALIB_PRESUBMIT`, `L5_FILL_BOOK_EVIDENCE`,
`L5_RESIDUAL_BOOK_SAMPLE`, `L5_MISSED_PAIR`, `L5_LIVE_SETTLED` (Railway, service
`l5-pmus-live`, deploy `1d673c49`).

## 0. Population

**18 venue fills across 7 windows.** Campaign C (camp1.9f3c71a4...) filled in
1745z, 1800z, 1815z, 1830z, 1845z, 1900z; Campaign D (camp1.5b2fa83e...) filled
only in 2000z. 12 opening legs, 5 matched pairs, 7 stranded legs.

| window | legs (own px, qty) | pair formed | outcome |
|---|---|---|---|
| 1745z | DOWN .47 / UP .52 | **0.99** | matched |
| 1745z | DOWN .45 / UP .53 | **0.98** | matched |
| 1745z | DOWN .39 / UP .56 | **0.95** | matched |
| 1745z | DOWN .36 x0.69 | — | stranded |
| 1800z | DOWN .12 | — | stranded |
| 1815z | UP .46 / DOWN .54 | **1.00** | matched |
| 1815z | UP .46 | — | stranded |
| 1830z | UP .47 | — | stranded |
| 1845z | DOWN .42 | — | stranded |
| 1900z | UP .19 | — | stranded |
| 2000z | UP .37 / DOWN .56 | **0.93** | matched |
| 2000z | UP .40 | — | stranded (won) |

Pair gross vs $1.00 redemption: +0.01, +0.02, 0.00, +0.05, +0.07 = **+$0.15 on
five pairs**. Settled totals were Campaign C **-$0.8084** and Campaign D
**+$0.67**, so the ~-$0.29 balance is strand P&L. **Three of five pairs formed at
0.98-1.00 and contributed +$0.03 between them. All of the day's pair edge came
from the two pairs at 0.93 and 0.95.**

## 1. Why pairs form at 0.98-1.00: the completion ceiling is not an economic test

`src/l5_two_sided_probe.py:2442`

    COMPLETION_CEILINGS_IN_FORCE = ("improves", "within_budget")

`max_completion_price` computes four ceilings; only these two are in force.
`pair_cap` (the 0.96 `MAX_PAIR_COST`) and `positive_both` are **computed and
discarded**. `improves` is a worst-case-dominance test:

    held_worst = min(held_qty, deficient_qty) - sunk      # = -basis for a one-sided book
    improves   = (1 - cost) > held_worst

For 1 DOWN held at 0.47 this permits any own price `q` with
`0.47 + q + fee < 1.00`, i.e. **q up to ~0.98 — a pair cost up to ~1.45.** The
0.96 pair cap an *opening* must clear is bypassed for any held inventory, via
the "COMPLETION RESCUE" branch at `l5_two_sided_probe.py:2653-2670`
(`judged_as: "COMPLETION"`).

This is visible live. 1745z, after the DOWN .47 fill at 17:47:08, the engine's
own requote ladder (`L5_FILL_CALIB_PRESUBMIT`, own_px against the UP bid):

| t | own_px | bid | implied pair |
|---|---|---|---|
| 17:47:11 | 0.50 | 0.52 | 0.97 |
| 17:47:14 | 0.49 | 0.50 | 0.96 |
| 17:47:21 | 0.48 | 0.50 | **0.95** |
| 17:47:25 | 0.51 | 0.53 | 0.98 |
| 17:47:28 | 0.50 | 0.52 | 0.97 |
| 17:47:32 | 0.51 | 0.53 | 0.98 |
| 17:47:35 | 0.52 | 0.54 | **0.99** -> FILLED |

**Seven cancel/replace cycles in 24 seconds, each one chasing the touch upward
and going to the back of the new queue, filling at the dearest price of the
sequence.** The requote prices on the completion ceiling (~0.98), so nothing
stopped the chase. `REQUOTE_MIN_TICKS_BEHIND = 2` triggers *more* often as the
book runs away, so a rallying complement produces maximal chasing.

### Why adding `pair_cap` to the tuple is NOT the fix

Charging the taker fee inside the ceiling, as `max_completion_price` does, the
pair-cap ceiling for each of today's five pairs is: 0.47 basis -> 0.47 max;
0.45 -> 0.49; 0.39 -> 0.54; 0.46 -> 0.48; 0.385 -> 0.555. **Every one of the
five actual completion fills (0.52, 0.53, 0.56, 0.54, 0.56) is above its
ceiling.** Adding `pair_cap` would have refused all five pairs and left twelve
stranded legs instead of seven. It is the obvious-looking one-line fix and it
is wrong.

The reason is arithmetic, and it is the central constraint on this strategy:
a complement's fair own price is ~`1 - basis`, while a 0.96 cap net of the
~1.7c fee charge demands ~`0.943 - basis`. **Completing an existing leg
therefore requires the complement ~5.7c below fair value on a book whose spread
is 1c.** That edge is not available by resting; it arrives only when the book
sweeps through our quote, which is precisely adverse selection. `improves`
exists because the honest cap is near-infeasible for a *sequential* pair — and
it converts that infeasibility into a stream of certain small losses.

Note also that completing is EV-neutral by construction: at pair cost `p` both
completing and carrying to settlement are worth `1 - p`, so completion buys
variance reduction and pays the spread for it. Throughput of *completions*
cannot produce profit. Only throughput of *cheap pairs* can.

## 2. Where cheap pairs actually were: the residual cap

1815z, 18:18:00-18:22:00Z, 101 `L5_MISSED_PAIR` records, every one with
`evidence.executable_evidence: true` on the authenticated current book:

| fresh two-sided pair at the touch | gain/contract | count | blocker |
|---|---|---|---|
| 0.95 | $0.05 | 71 | `RESIDUAL_ABOVE_CAP` |
| 0.94 | $0.06 | 29 | `RESIDUAL_ABOVE_CAP` |
| 0.93 | $0.07 | 1 | `RESIDUAL_ABOVE_CAP` |

`inventory.held_qty: 1`. The residual cap is `min(k * use_clip, 5)` with
**k=1 at a 1-contract clip = 1**, so one stranded contract consumed the entire
cap and no fresh pair could be attempted. In the same four minutes the engine
emitted 88 `DECIDE_COOLDOWN` and 12 correct `PAIR_ABOVE_REDEMPTION` refusals
while working the completion of that one stranded leg at 0.98-1.00.

**Honest limit on this evidence:** these are *displayed levels*, not fills. The
record's own label says so, and `missed_profitable_pair_in_state` is downgraded
to `N/A` with `UNMATCHED_INVENTORY_HELD`. The defensible claim is that **the
price level for a 5-7c pair existed on 101 observations in four minutes and the
cap prevented any attempt** — not that 101 pairs were available to us.

## 3. Earliest realistically executable completion (owner item 4)

Measured on `L5_RESIDUAL_BOOK_SAMPLE`, which is keyed to the **real** residual
and carries the price the engine actually quotes (`pair_if_maker_behind_touch`).

**1815z stranded UP .46 — 125 samples, `order_resting: false` on every one:**

| t | behind-touch pair |
|---|---|
| 18:17:55 (fill +1s) | 0.97 |
| 18:18:12 | **0.94** |
| 18:18:35 | **0.93** |
| 18:19:05 / 18:19:11 | 0.95 / 0.95 |
| 18:21:33 / 18:21:38 | **0.91 / 0.90** |
| 18:23 onward | 1.18 -> 1.28, never recovers |

A ceiling-clearing completion existed within **41 seconds** of the fill and
again four minutes later. We had nothing resting at any point in the residual's
life. This also settles the earlier over-reach: a one-sided book late in a
window says nothing about t+200s.

The cheap moments are **single 5-second samples** bracketed by 0.99s, so a
faster decision cadence cannot capture them. Only a resting order can.

**2000z stranded UP .385 — 115 samples, `order_resting: false` on every one:**
the pair decayed the *other* way — 1.00 at +0s, then from 20:06:42 it collapsed
through 0.92, 0.73, 0.58, and sat at **0.41-0.48** for the rest of the window.
Completing there would have locked ~+$0.58; carrying it won **+$0.615**.
Carrying was correct by ~3.5c, and only because we were right about direction.
This is the symmetric case and it is why "always complete" is not the answer
either.

## 4. Loss attribution by cause

| cause | measurement |
|---|---|
| opening selection | 7 of 12 opening legs stranded (58%) |
| complement placement | 3 of 5 pairs at >=0.98, contributing +$0.03 of +$0.15; 1745z walked its own price 0.48 -> 0.52 in 24s and filled at the worst price of the ladder |
| queue priority | position is unmeasurable (venue publishes none); 7 requotes in 24s is the measurable proxy for priority destroyed |
| decision cadence | 88 `DECIDE_COOLDOWN` in 4 minutes; cheap pairs were single 5s samples, so cadence is **not** the binding constraint |
| residual management | 101 executable 0.93-0.95 fresh-pair levels refused by `RESIDUAL_ABOVE_CAP` with held_qty 1 against a k=1 cap |

## 5. Data availability (owner item: name the exact missing fields)

| item | status | detail |
|---|---|---|
| 1 fill identity | **complete** | `L5_LIVE_LIFECYCLE` (coid, venue_order_id, state, cum_qty, avg_px, terminal) + `L5_FILL_CALIB_PRESUBMIT` (our own_px, book, `queue_ahead`) |
| 2 complement orders / mods / cancels | **one gap** | `l5f-501a8627` (1745z DOWN, submitted 17:50:26) has **no terminal lifecycle record** — last seen `ORDER_STATE_NEW` at 17:52:16, and the residual sequence 0 -> 0 across the 17:52:39 UP fill implies it filled. Same class as the 1445z phantom. |
| 2 queue position | **structurally unavailable** | venue publishes no per-order queue; only the `queue_ahead` bound |
| 3 books | **complete** | `L5_RESIDUAL_BOOK_SAMPLE`, real residual, ~5-12s, 115-125 samples/residual, 8-deep ladders, `executable_evidence` |
| 3 trades/prints | **MISSING, not recoverable** | `l5_pmus_auth_book.trades()` keeps a rolling 500/slug **in memory only**; nothing persists them. Retrospective recovery is impossible; a recorder would have to be added going forward. |
| 4 latency | **unmeasurable** | `venue_event_t: null` on all 18 fills, so `processing_lag_s` is null; the fill instant is only bracketed by surrounding frames |
| 5 L5 comparison states | **available, not yet joined** | `analysis/l5/l5_canonical_sequence.csv.gz`, 2,216 windows, has `seconds_to_settlement`, `residual_qty_signed`, `cum_*` |
| 6 settled P&L | **complete** | `L5_LIVE_SETTLED` |

## 6. Reporting defect found

`src/l5_pmus_live_runner.py:4319` passes `fill_own_px=ev.get("avg_px")`, which is
the venue's **wire** price. For DOWN fills the field named `fill_own_px` is
therefore `1 - own`. Verified on all 8 DOWN fills: `first_leg_own_px` (our
submitted `sub["own_px"]`) equals `1 - fill_own_px` exactly in every case, and
equals it for UP fills. Any analysis reading `fill_own_px` as an own price is
wrong by `1 - 2p`. Reporting-only field; no money path reads it.

## 7. Single highest-value change

**Stop letting one stranded contract consume the whole residual cap, so that
unmatched inventory does not block fresh pair formation.** Either raise `k`, or
make the cap price *net* exposure rather than gross unmatched contracts.

This is the only lever today's evidence points at. It is not the opening band,
the clip size, the depth ticks or the decision cadence — all four were measured
above and none of them is binding. It needs owner approval and is **not**
actioned here.

Expected net-P&L improvement: **not claimable from these 101 records**, because
they are displayed levels and a fresh pair needs *both* maker legs to fill. The
measurement that would produce a number is below.

### Out-of-sample validation plan (no capital, no arming, no crossing)

1. Replay the recorded ladders for Campaigns C and D through the **existing**
   queue-aware shadow model at k=1 vs k=2 vs k=3, counting **both-leg fills**,
   never level availability.
2. Report pairs formed, the pair-cost distribution, strand rate and net —
   chronologically: fit on Campaign C, test on Campaign D and on the earlier
   camp1.9f3c71a4 windows as a second held-out set.
3. Promote only if the both-leg fill rate at the 0.94-0.95 level is material
   **and** the strand rate does not rise in proportion. A higher cap that only
   buys more strands is a loss, not a gain.

Blocking precondition for (1): the shadow model's `volume_through_level` and
`queue_ahead_at_placement` are driven by trade prints, which per section 5 are
not persisted. The replay can therefore only use displayed-depth clearance as a
bound, and its fill rate must be reported as an **upper bound**, not an estimate.
