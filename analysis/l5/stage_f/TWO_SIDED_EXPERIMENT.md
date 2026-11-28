# Deployed safety evidence, and the two-sided maker-quote experiment

**Watchdog safety fix DEPLOYED. Executor untouched and DISARMED. Nothing
armed, no capital change, no live orders, no new services.**

---

# Part 1 — deployed safety evidence

## The promotion

| | |
|---|---|
| branch | `claude/gemini-btc-economic-viability-h3u7zl` (the watchdog's own) |
| code commit | **`ebaef2d8`** — cherry-picked from `f3769785`, production code and tests only, **no analysis documents** |
| deploy commit | **`2a56a152`** — the `DEPLOY_TRIGGER` entry, which is what `watchPatterns` builds |
| deployment | **`5ef90355`** — **SUCCESS**, 06:46:17 → 06:47:25Z |
| tests on that branch | **934 passed** across the watchdog, halt, reset, marker and cancel suites |

**A mistake I made and corrected before committing:** I first wrote the
trigger entry with `>` instead of `>>` and truncated the entire
183-line deployment audit log. I restored it from git and appended; the
committed diff is **+36 / −0** and every prior entry is intact. Worth
recording because that file *is* the deployment history.

## Running state, verified after the deploy

**The new code is live** — sweeps now carry fields the old build had not got:

```
L5_WATCHDOG_SWEEP 06:47:25Z  "emergency_cancel": false, "emergency_reason": null,
  "cancel_enabled": true, "open_orders": 0, "overdue": [], "not_ours": [],
  "still_open": [], "unverified": [], "read_error": null,
  "heartbeat": {"halt": false, ...}
```

**Independent operation was preserved across the handover.** The old build
(`ee4fe9aa`) kept sweeping *through* the build — 06:46:20, 06:46:40,
06:47:01 — and the new one took over at 06:47:25. **A ~24s gap, a little over
one 20s cadence.** The watchdog was never both down and needed.

**Executor untouched**, read at 06:47:41Z during the same minute:

| field | value |
|---|---|
| deployment | `0072e385` — **unchanged** |
| `armed` | **false** |
| `owner_arm_token` | **false** |
| submission gate `blocked` | **true** |
| `halt_latch` | `CLEAR` |
| `live_orders` | **0** |
| `all_time_realized_usd` | −8.82910000 |

**Zero outstanding obligations:** watchdog `open_orders 0`, `still_open []`,
`unverified []`, `not_ours []`; executor `live_orders 0`.

## Cancellation of fresh orders — what is and is not verified

**I cannot demonstrate this live, and I will not claim otherwise.** It
requires a fresh resting order; there are none (`open_orders 0`), the
executor is disarmed, and placing one to prove a point is exactly what I was
told not to do.

What **is** established:

- **The code path is live and reporting** — `emergency_cancel` /
  `emergency_reason` appear in every sweep on the new deployment, so the
  branch is reached and evaluated each cycle.
- **`cancel_enabled: true`** in production, so the capability is present.
- **20 regression tests** prove the behaviour, including the exact case:
  *a 5-second-old order under a 300s deadline is cancelled when a halt is
  latched*, and is **left alone** when none is.
- **Fail-closed preserved:** an unreadable latch is deliberately *not* an
  emergency (`HALT_LATCH_UNREADABLE_NOT_EMERGENCY`) and the age-based path is
  byte-for-byte unchanged.
- **Exact-ID cancellation and ownership preserved by construction:** one
  `_cancel_targets()` serves both paths, with an AST test asserting two
  callers and one implementation.

The honest summary: **mechanism deployed and unit-proven; live
fresh-order cancellation remains unexercised until something is resting.**
The first armed window is when it gets exercised for real.

---

# Part 2 — the experiment

## It is almost entirely already built

`src/l5_two_sided_probe.py` already implements this, gated behind
**`L5_TS_TWO_ORDER_PAIR`**, off by default. Its own comment says why:

> *"the owner approved the mechanism and withheld activation until the
> lifecycle and risk accounting are verified."*

**The risk accounting is what the deployed fix just verified.** Under the old
netting predicate, two resting opposite-side orders summed to a prospective
net of zero and the residual cap never applied — the 1415z failure exactly.
The new rule prices a resting opposite leg as **an intention, not a hedge**,
which is precisely the accounting this experiment depends on. So the
sequencing was not incidental: **the safety fix was the prerequisite.**

`second_leg_slot_refusal()` grants the second slot only when **every**
condition holds, each a named refusal: the flag is on; **exactly one** order
is live; it is in **this** market; on the **opposite** side; **claimed in
this round**; and this side is **not** already claimed. It is explicitly *not*
`max_live_orders = 2` — that would permit two independent first legs sharing
one cash envelope, the 1215z failure mode.

## The binding constraint is NOT the $20 cap

Driving the real `residual_cap_decision` with the experiment's exact states:

| order | binds? |
|---|---|
| leg 1 UP 3 / 5 / 21 from flat, nothing resting | **no** at any size (genuine opening) |
| leg 2 DOWN 2.90 while UP rests | no |
| **leg 2 DOWN 3.00 while UP rests** | **no** |
| **leg 2 DOWN 3.01 while UP rests** | **BINDS** |
| leg 2 DOWN 5 / 21 while UP rests | binds |

**Per-leg size ceiling is 3.00 contracts**, set by
`ABS_RESIDUAL_CAP_CONTRACTS`, because the worst case for the second leg is
that *only it* fills. A sequential completion against **held** inventory does
not bind at any size — correctly, since it reduces risk.

The **$1.50 per-market worst-case deficit** can bind sooner: a 3-contract leg
at own price 0.57 risks $1.71. So:

```
per-leg qty = min(3.00, 1.50 / dearer own price)
```

At the recorded depth-2 prices (UP own 0.57, DOWN own 0.38) that is **2.63
contracts**, both legs equal.

**$20 aggregate exposure is never reached.** A pair at 0.95 × 2.63 contracts
is **$2.50** of capital. This experiment uses about an eighth of the
authorized envelope, and that is as intended.

## What it can and cannot earn

**At ~2.63 contracts and a 0.95 pair, a completed pair earns $0.13. Ten
windows, every pair completing, is about $1.30.**

**This experiment cannot produce meaningful profit and is not meant to.** It
buys one number: whether simultaneous quoting converts filled first legs into
matched pairs. Judging it on P&L would be judging noise — the Stage B2
residual swung $5.30 between its best and worst outcomes on comparable size.

## Design

| | |
|---|---|
| venue / series | PMUS `cpc-btc-updown-15m-` only |
| quotes | **simultaneous UP and DOWN, both at depth 2 behind their own touch, from one book snapshot** |
| per-leg size | `min(3.00, 1.50 / dearer own price)`, both legs equal |
| live orders | **max 2**, and only via `second_leg_slot_refusal` — opposite side, same market, same round |
| exposure accounting | **both resting orders priced as if each fills in full**, independently — never netted |
| aggregate exposure | $20 (not expected to be approached) |
| per-market worst-case deficit | $1.50 |
| daily settled-loss halt | $5 |
| execution | maker-only, post-only, **no crossing** |
| cancellation | exact venue order ID, venue re-read to terminal, UNCERTAIN recorded as UNCERTAIN |
| HALT | the deployed latch; emergency cancel-all now covers fresh orders |
| windows | **≥10 eligible**, consecutive, under a fresh authorization period |

**Pair cost is fixed at quote time by the spread alone** —
`pair = 1 − spread − 2·depth·tick` — which on the 1-tick book that holds at
p10, p50 and p90 alike gives **0.95**. That is the whole price case, and it is
already established by identity; it is not what the experiment tests.

## Comparison against the sequential baseline

Baseline is Stage B1+B2 as settled. **Primary metric first:**

| metric | sequential baseline | falsified if two-sided… |
|---|---|---|
| **matched contracts per filled first leg** | **≥1.68** (11.79 matched / 7 windows with a filled leg; a lower bound — 1400z's matched qty is on the volume tape) | **does not exceed 1.68** |
| zero-pair window rate | **42.9%** (3 of 7: 1330z, 1430z, 1445z) | **is not below 42.9%** |
| residual frequency | **71.4%** (5 of 7 windows ended with residual ≠ 0) | **is not below 71.4%** |
| adverse selection | complement **+5 ticks** dearer (1430z), **+2** (1445z) | any **non-zero** adverse drift between the two legs' quote times (it should be zero by construction — both quoted off one snapshot; a non-zero value means the implementation is not simultaneous) |
| fill timing | not measured in the baseline | reported as new evidence, not a pass/fail |
| settled net P&L, matched vs residual | +$1.4155 total; matched **+$0.3895**, residual +$1.0260 | **not** a falsification criterion at this size — see above |
| mean pair cost on completed pairs | 0.96 / 0.97 / 1.00 observed | **exceeds 0.96** |

**Unfilled quotes count as unfilled.** A quoted-but-unfilled pair contributes
**zero** matched contracts and **no** pair cost — never a "planned" pair
booked as though it happened. The 1445z counter anomaly (`submitted 70`,
`acknowledged 1`) is why this is stated: the experiment reports **decision
attempts / transport submissions / venue acknowledgements / fills /
cancellations / rejections** as five separate counts, using the labels
already added to `l5_window_accounting`, and `submitted` is explicitly *not*
evidence an order reached the venue.

## Pre-registered stop rule

Investigate-and-halt, decided before any run:

**Immediate halt, no further windows:**
- **Any safety-control discrepancy.** A reported cap differing from observed
  exposure; `submitted ≠ acknowledged` without an explained cause; watchdog
  `still_open`, `unverified` or `not_ours` non-empty; any cancellation not
  venue-confirmed terminal; any order on a third side or market; any residual
  exceeding 3.00 contracts.
- Existing triggers unchanged: **$5** daily settled loss, **$1.50** per-market
  worst-case deficit, the **3-contract** residual cap.

**Halt for worsening residual losses:**
- Cumulative **settled residual** P&L ≤ **−$2.50** (half the daily halt).
- Residual frequency **above the 71.4% baseline** over ≥5 settled windows —
  i.e. the change made stranding *worse*.
- Two consecutive windows where **both** legs fill on the same side (an
  EXTEND the slot rule should make impossible).

**Not a stop:** a negative P&L run on its own. At $0.13 a pair, P&L is noise
and stopping on it would be stopping on noise — the Stage B2 lesson.

## The smallest authorization needed

**Three things, and nothing else:**

1. **Set `L5_TS_TWO_ORDER_PAIR` to `YES`** on `l5-pmus-live`. The variable
   already exists on the service; **its value is redacted to this session**
   (OAuth callers get names only) and the heartbeat does not echo it, so I
   cannot confirm its current setting — you can, in the dashboard. Only `1`,
   `YES` or `TRUE` enables it.
2. **A fresh authorization period** covering ≥10 consecutive BTC15m windows,
   with `max_live_orders` permitting the second slot. I can mint the
   proposal with `tools/make_campaign_approval.py`; **I cannot approve it.**
   `L5_LIVE_AUTH_NOT_AFTER` must be set to the last eligible window's start
   so the boundary cannot absorb an eleventh window — the 15:00Z hole from
   Stage B2.
3. **Explicit arming**, through the existing three-part mechanism
   (`PMUS_Q1_LIVE`, `PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM`).

**Not requested and not needed:** no capital increase, no new services, no
new branches, no residual-cap change, no crossing, no `max_live_orders = 2`
in the general sense. The $20/$1.50/$5 limits stay exactly as approved.

**Current state: nothing armed.** `owner_arm_token false`, gate `blocked
true`, `live_orders 0`. Nothing above happens until you do those three
things.
