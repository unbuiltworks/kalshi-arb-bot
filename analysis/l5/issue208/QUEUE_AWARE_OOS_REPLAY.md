# Queue-aware OOS replay: does the edge survive queue position?

PR #208 item 3. Harness `analysis/l5/issue208/queue_scenarios.py`, tests
`tests/test_l5_queue_scenarios.py` (12). Production disarmed at
`6116675ed`, untouched. Settled realized net unchanged: **−$15.6109**,
**−$13.4809** rebate-adjusted.

**Every fill below is a bound, not a fill.** `src/l5_queue_depletion`
establishes — verified by a log query returning zero `prints_at_our_price`
records — that **there is no trade-print series** for these recordings. All
P&L here is settled-**equivalent**: modelled fills, a measured residual
constant, a published fee schedule. Nothing is realized and no pair is
credited as completed.

---

## 1. The three scenarios are queue positions, not probabilities

A probability of filling is not in this data, so none is manufactured. What
varies is the one unmeasured quantity — how much of the displayed size at
our price sat ahead of us — together with which arm of the bracket counts:

| scenario | arm | queue assumed ahead |
|---|---|---|
| **pessimistic** | `PROVEN` only — the opposite touch reached our price. Sufficient, so a genuine lower bound, and **independent of queue position** | n/a |
| **base** | `POSSIBLE` — enough displayed size left the at-or-better region | **all** of it |
| **optimistic** | `POSSIBLE` | **none** of it, but still requiring our own size to have left the level, so "at the front" is not "filled for free" |

`PROVEN` cannot move with queue position, which is the first invariant the
tests pin — and the one a first version of this harness broke. It returned at
the first condition met, so an early `POSSIBLE` masked a later `PROVEN`, and
the optimistic assumption reported **fewer** proven fills than the
pessimistic one. Impossible, and the signal the walk was wrong. Both arms are
now tracked to the end of the walk independently. **Fixing it changed the
sign of most cells**, so the first table this harness produced was wrong.

## 2. Out of sample, both legs rested simultaneously

Two recordings (`residual_books_20261025`, `oos_shadow_residual_samples_20261106_1130z_1145z`),
every frame a candidate entry, 1 contract a leg, no rebate.

### Held to the end of the recording

| d | scenario | entries | pairs | residual | no fill | matched | residual P&L | **net/entry** |
|--:|---|--:|--:|--:|--:|--:|--:|--:|
| 0 | pessimistic | 652 | 426 | 223 | 3 | +4.3200 | −5.2628 | **−0.001446** |
| 0 | base | 652 | 601 | 48 | 3 | +6.1000 | −1.1328 | +0.007618 |
| 0 | optimistic | 652 | 627 | 25 | 0 | +6.3600 | −0.5900 | +0.008850 |
| 2 | pessimistic | 627 | 329 | 265 | 33 | +16.5000 | −6.2540 | **+0.016341** |
| 2 | base | 627 | 564 | 48 | 15 | +28.2900 | −1.1328 | +0.043313 |
| 2 | optimistic | 627 | 605 | 21 | 1 | +30.3400 | −0.4956 | +0.047599 |

### With a 60-second TTL

| d | scenario | pairs | residual | **net/entry** | vs held to end |
|--:|---|--:|--:|--:|---|
| 0 | pessimistic | 290 | 354 | **−0.008304** | worse |
| 0 | base | 580 | 67 | +0.006609 | slightly worse |
| 0 | optimistic | 621 | 29 | +0.008613 | ~same |
| 2 | pessimistic | 148 | 410 | **−0.003614** | **sign flips** |
| 2 | base | 503 | 103 | +0.036378 | worse |
| 2 | optimistic | 600 | 24 | +0.047087 | ~same |

### In sample, for comparison

| d | scenario | net/entry |
|--:|---|--:|
| 0 | pessimistic | +0.001703 |
| 2 | pessimistic | +0.023719 |

In sample is **more** favourable than out of sample at both depths, which is
the direction that should be expected and the reason the OOS column is the
one quoted.

## 3. Break-even completion rate

Given one leg filled, the rate at which the second must fill for the window
to be expected non-negative, against the measured **−0.0236/contract** cost
of carrying the leg instead:

| pair cost | gross edge/contract | **q\*** |
|---|--:|--:|
| 0.8700 | +0.1300 | 15.4% |
| 0.9300 | +0.0700 | 25.2% |
| 0.9500 | +0.0500 | 32.1% |
| 0.9600 | +0.0400 | 37.1% |
| **0.9800** | +0.0200 | **54.1%** |
| 0.9900 | +0.0100 | **70.2%** |

**What this is not.** It is *not* the rate at which completing beats
carrying. Attempting a completion costs nothing extra when it fails — we
carry either way — so attempting weakly dominates at any rate. This is the
rate at which a window that has already taken one leg ends non-negative.

## 4. The answer to the decision criterion

> *Does the strategy remain economically attractive under conservative,
> realistic fill assumptions?*

**At two ticks behind the touch, held patiently: yes.** OOS pessimistic
**+0.0163/entry**, with a 55.4% proven completion rate against a 32.1%
requirement.

**At the touch: no.** OOS pessimistic **−0.0014/entry**. The pair is
`1 − spread` = 0.99, so it needs **70.2%** and the proven arm delivers 65.6%
— just under. The touch fails the conservative arm by a small margin, and
passes the two more generous ones.

**With a 60-second TTL: no, at either depth.** The d=2 pessimistic cell flips
from +0.0163 to **−0.0036**. Resting two ticks behind takes longer than 60 s
to be proven filled, so cancelling at a minute destroys exactly the pairs
that make it work. **Patience is doing more of the work here than price is.**

### What this says about ROUTE B's `depth_ticks = 0`

On this evidence, resting the completion **at the touch** is the setting that
fails the conservative arm, and the setting to question — not the pair
ceiling. But two caveats bound that conclusion and neither is small:

1. **This harness rests both legs simultaneously.** ROUTE B's situation is
   different: one leg is *already filled*, and resting the complement behind
   the touch risks never completing and carrying the residual — which is the
   trade the rescue branch exists to price. A sequential version of this
   harness is needed before `completion_depth_ticks()` is changed, and that
   is the next piece of work, not a conclusion of this one.
2. **The ≤0.96 / >0.96 split in these tables is arithmetic, not evidence.**
   `pair = 1 − spread − 2·d·tick`, so on a one-tick book the touch always
   buys 0.99 and two ticks always buys 0.95. The **0.98 rescue case** from
   item 2 arises only in the sequential setting, at a *realized* first-leg
   price, and does not appear here at all. A test pins this so the split is
   not misread.

## 5. Everything the numbers do not include

- **No trade prints.** `PROVEN` needs no prints and is sound; `POSSIBLE`
  counts cancellations as depletion and is an upper bound for that reason.
- **Entries overlap.** Every frame is a candidate, so 652 entries come from
  ~500 frames and are heavily correlated. The **net totals are not money**;
  only net/entry is meaningful, and even that is not an independent sample.
- **5-second frame granularity.** A fill inside a frame gap is invisible, and
  latency between decision and rest is below the sampling resolution.
- **Rebate held at zero.** The confirmed credited rate is $2.13/293 fills =
  **$0.00727/fill**, available in the harness and deliberately not in these
  tables until it is credited for the fills in question. At d=2 it would add
  roughly +0.0145/entry on two filled legs — material against a +0.0163
  pessimistic figure, which is precisely why it is not quietly included.
- **Partial fills are absent from the venue record** (11 of 11 confirmed
  fills were single-step), so the harness fills a leg whole or not at all.
- **One series, four recordings, four dates.** Not a fill curve for an
  arbitrary quote or regime.
