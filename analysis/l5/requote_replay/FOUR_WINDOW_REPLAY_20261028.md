# Four-window replay with both corrections — 2026-10-28

Campaign `camp.429618389049c7161219dd10`, windows 0015z/0030z/0045z/0100z.
**Venue-confirmed settled net: +$0.7083.** Everything below the first
section is *replay*, never realized: no figure here is a fill, a settlement
or a realized profit.

## The two corrections do not overlap

| refusal | windows | decisions | removed by |
|---|---|---|---|
| `PRESEND_CROSS:PRESEND_WOULD_CROSS` | 0015z (23), 0030z (4) | 27 | the requote |
| `SUBMISSION_CLAIM:NO_COMPLETION_CAPACITY_RESERVED` | 0045z (3), 0100z (22) | 25 | campaign identity |

That split is the first result. The requote cannot help windows 3 and 4 —
they had no crossing refusals at all — and the identity fix cannot help
windows 1 and 2, whose budget was never short. Each correction addresses a
different window pair, so neither alone would have lifted the campaign.

## What a fill may be claimed from

A post-only order quoted two ticks **behind** the touch does not fill
because it is valid; it fills only if the market comes to it. The recorded
books are snapshots at decision times, so the only fill evidence they carry
is **traversal**: a resting UP bid at own `p` is filled when a later
snapshot inside its 120 s TTL shows the best bid strictly below `p`, because
the book cannot get there without passing through our level. A DOWN leg
rests as an ASK at wire `1 - p` and is filled when a later best ask is
strictly above it.

The rule cannot invent a fill, and it **understates** them — the touch can
dip through a level and recover between two snapshots and leave no trace.
So every fill below is traversal-supported, every "no fill" means "not
supported by this tape" rather than "proved absent", and the throughput
figures are a **floor**, not an estimate.

## Window 0015z — the requote buys one more pair and does not stop the loss

Observed touch: 0.46 → 0.47 → 0.42 → **0.36** → 0.44 → 0.48 → **0.63** →
0.54 → 0.44 → 0.43 → 0.49 → 0.10 → 0.07 → 0.14 → 0.04 → **0.01**. UP
settled worthless.

Counterfactual, one resting order at a time, COMPLETE before OPEN:

| +s | role | side | own | evidence |
|---|---|---|---|---|
| 154 | OPEN | UP | 0.4400 (was 0.6000, 16 ticks lower) | FILL — touch traversed to 0.42/0.43 |
| 421 | COMPLETE | DOWN | 0.4900 | FILL — touch traversed to 0.63/0.64 |
| 487 | OPEN | UP | 0.6100 | FILL — touch traversed to 0.54/0.55 |

* **one completed pair at 0.4400 + 0.4900 = 0.9300**, qty 1.87, gross edge
  **+$0.1309**, less two order fees → **+$0.1109**;
* then a fresh round opens UP at own 0.6100 and **strands**, because every
  later complement is above the cap (0.99 at +288 s, 1.05 at +321 s, and
  worse once the book collapses). UP settles 0, so that leg is worth about
  **−$1.14**.

The live run stranded the same leg one tick cheaper, at own 0.6000, for the
reported **−$1.1313**.

> **Superseded by `EXECUTION_UNCERTAINTY_20261028.md`.** An earlier version
> of this file reported the net effect as "roughly +$0.09", which treated a
> traversal-supported fill as a fill that would have happened. The
> complement that makes this pair profitable is conditional on a rebound to
> 0.63 that nothing in the mechanism controls; without it the same UP leg
> strands at own 0.4400 for −$0.8228. The honest range is **+$0.11 to
> −$0.82**, and the replay does not establish that the requote improves net
> economics — only that it acts.

**The requote is a throughput fix, not a loss fix.** It does not address
the stranded-leg loss, and the replay says so directly: the leg that loses
the money is one the cross guard never refused, so there was nothing for a
requote to reprice.

## Window 0030z — one order, fill unknown

The four `PRESEND_WOULD_CROSS:DOWN` refusals at +383 s (book 0.69/0.70,
plan DOWN own 0.3900, wire 0.61 ≤ bid 0.69, so the refusal was correct)
requote to **own 0.2800** — eleven ticks lower, wire 0.72, clear of the
bid. Paired against the round's UP leg at 0.56 that would be a pair of
**0.84**, well inside the cap.

The tape ends at +451 s with that order still resting. **Its fill is
unknown, not absent.** No economics are claimed for this window.

## Windows 0045z and 0100z — the identity fix is the whole story

0100z placed **zero orders**: every decision from +50 s to +150 s was
refused `NO_COMPLETION_CAPACITY_RESERVED`, because 27 claim locks from two
earlier campaigns were counted against this campaign's 40-submission
backstop (`total_max: 40, total_spent: 38`). 0045z lost its third round the
same way.

Every one of those refusals carried a plan **inside** the pair-cost ceiling:

| window | +s | UP own | DOWN own | pair | qty |
|---|---|---|---|---|---|
| 0045z | 282 | 0.4100 | 0.5400 | 0.9500 | 2.12 |
| 0100z | 50 | 0.4000 | 0.5500 | 0.9500 | 2.14 |
| 0100z | 84–150 | 0.3400 | 0.6100 | 0.9500 | 2.23 |

Three distinct fundable round plans at pair 0.95, refused on budget alone.
A plan is not a fill, so no P&L is claimed for them either; what the fix
restores is the window's **ability to trade at all**, which 0100z did not
have. At the campaign's own matched-book rate (+$0.6494 over four windows)
bringing 0100z to parity with its peers would be worth order **+$0.16** —
an extrapolation, labelled as one, not a projection to rely on.

## What this says about the next step

1. The identity fix is the larger and better-evidenced of the two: it
   restored a whole window that was authorized, funded and silent.
2. The requote adds completed pairs, cheaply and safely, but its measured
   contribution on this tape is **+$0.09 on one window** and unknown on a
   second. It is worth deploying for throughput; it must not be presented
   as addressing the campaign's losses.
3. The dominant loss remains a **first leg that fills and cannot be
   completed inside the pair-cost ceiling** because the book moves away.
   Neither correction touches it. That — not throughput — is where the next
   economic work belongs, and the evidence here (complement refused at
   0.99, 1.05, then unreachable) is the first clean measurement of it.
