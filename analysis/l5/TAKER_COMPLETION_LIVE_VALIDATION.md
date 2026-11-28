# Bounded maker + taker-completion live validation

**NOTHING WAS ARMED. NO PRODUCTION VARIABLE WAS CHANGED. NO DEPLOYMENT
WAS TRIGGERED.** All production facts are read-only Railway queries.

---

## 1. What exists already, and was preserved

No new bot, no new service, no rebuilt order submission. **Taker
execution already existed in this codebase** and was reused:

| component | state | used how |
|---|---|---|
| `pmus_q1_executor.taker_completion_ok` | **existing** | reused unchanged as the all-in pair economics, incl. the real taker fee |
| `pmus_micro_executor` taker path (`post_only=False`, crossing guards, `cancel_exact`, partial handling) | **existing, untouched** | left alone; it is a different lane |
| `l5_persistent_mode.may_submit` refusing a crossing order without `taker_allowed` | **existing** | kept as gate 2, unmodified |
| `l5_btc5m_execution._order_fault` → `TAKER_NEVER_SENT` | **existing** | kept as gate 1, now with one narrow exception |
| my repeated maker quoting / replenishment / repricing / unmatched ceiling | **from last turn** | unchanged; the taker path is additive beside it |
| watchdog, exact-id cancellation, ledger, STOP latch, reservation, capital and daily-loss ceilings | **existing** | not touched |

Branches inspected: `claude/btc5m-favorite-accumulation-wzh2xo` (this
work), `integration/l5-safe-release-candidate-20261109` (what production
tracks), plus `feat/pmus-two-sided-fast-cancel-20261115` and the
`fix/l5-*` branches. Nothing was overwritten or duplicated.

## 2. The smallest change

`_order_fault` refused every non-`post_only` order unconditionally. It
now takes `taker_ok`, **defaulting False**, so every existing caller
keeps the maker-only block byte-for-byte. `_place` sets it True only when
all of the following hold, and refuses with a named reason otherwise:

```
L5_BTC5M_TAKER_COMPLETION == "YES"        (default off)
order.reason in ("STRADDLE_COMPLETION",)  -- a taker ENTRY is never permitted
held_qty > 0                              -- from OUR ledger, not the order
order.qty <= held_qty                     -- a "hedge" may not open new risk
book present, age_s <= 3.0s               -- a stale book cannot price a cross
ask_shares >= order.qty                   -- whole-clip depth, never partial
taker_completion_ok(first_cost, worst)    -- all-in pair locks >= 1c after fee
worst * qty <= L5_BTC5M_TAKER_COMPLETION_MAX_USD   (default 0 = refuse)
```

where `worst = max(ask + slippage_ticks, our own limit)`.

Two things worth calling out, because I got both wrong on the first pass
and fixed them:

- **The first leg's cost comes from the executor's own inventory**, never
  from the order. A caller that supplied it could make any completion
  look profitable.
- **`worst` includes our own limit.** I first priced only against the
  book's ask plus slippage. A limit set above that can *fill* above it,
  so the economics would have been verified at a price we never paid.

New refusal reasons: `TAKER_COMPLETION_DISABLED`,
`TAKER_ONLY_FOR_COMPLETION`, `NOTHING_TO_COMPLETE`,
`COMPLETION_EXCEEDS_HELD`, `TAKER_BOOK_ABSENT`, `TAKER_BOOK_STALE`,
`TAKER_DEPTH_SHORT`, `TAKER_ASK_UNREADABLE`,
`TAKER_PAIR_NOT_PROFITABLE`, `TAKER_COMPLETION_NOTIONAL_UNSET`,
`TAKER_COMPLETION_NOTIONAL_CAP`.

Economics are checked **before** the notional cap, so an unprofitable
pair reports as unprofitable rather than as a notional breach — the
refusal reason is what an operator reads at 3am.

`src/l5_restriction_audit.py` was updated: its `maker_only` entry claimed
"taker disabled / 0% taker" as a blanket fact. It now states that a taker
**entry** is refused at every setting, that a taker **completion** needs
all three gates, and that realized taker share is still 0%.

## 3. Why the $1.60 ceiling holds unconditionally

The economics gate means a completed pair locks ≥1¢ — but **that argument
assumes the gate is right.** The notional cap exists so the ceiling holds
even if it is not:

- A matched UP+DOWN pair **always** has exactly one winner, so it always
  pays $1.00 a contract. That is structural, not modelled.
- `COMPLETION_EXCEEDS_HELD` means a completion can only ever match
  inventory we already hold — it cannot open fresh risk.
- With a 0.08 first leg and a **$1.00** completion notional cap, the
  dearest pair reachable is about **$0.75** a pair. Even with the
  economics gate removed entirely, such a pair still pays $1.00.
- So the only way to lose is the **unmatched maker residual**, bounded by
  the `$0.40` unmatched ceiling, plus taker fees actually paid. Under the $1.00 notional cap the fee
  maximum is $0.08, computed across the price range rather than assumed:
  $0.08 at p=0.20 (5.00 contracts), $0.05 at p=0.50, $0.03 at p=0.70.

| worst case component | amount |
|---|---|
| unmatched maker residual (the unmatched ceiling) | **$0.40** |
| taker fee on a completion (0.095·C·p(1−p)) | **≤ $0.08** |
| **total worst-case additional loss** | **≤ $0.48** |
| of the owner-approved ceiling | **$1.60** |

**Maximum aggregate committed capital: $2.60** ($1.60 maker across both
sides + $1.00 completion), inside the unchanged $5.00 per-market and
$10.00 account-inclusive aggregate.

I am **not** sizing up to consume the approved $1.60. The configuration
that answers the question is the small one.

## 4. Account-wide exposure — three live-capable services, read live

| service | state at 03:42–03:43Z | overlap risk |
|---|---|---|
| `l5-btc5m-live` | **EXITED AT BOOT**, `ENVELOPE_EXPIRED`. SHA `241f7432d`. | none — not running |
| `l5-pmus-live` | **running, `armed=False`**, `preflight=PASSED`, `owner_arm_token=True`, market `cpc-btc-updown-15m-...-0330z`, **0 live orders**, 0 quarantine, 0 pending-terminal | **different series (BTC15m)**; disarmed |
| `pmus-spring-l5-probe` | **running and LIVE: `"enabled": "YES"`**, caps `per_market $1.50 / total $12 / markets 8`, `recovery.taker: false` — but **currently flat**: `active: []`, `open.notional_usd: "0"`, `unmatched_cost_total: "0"` | sports/Kalshi slugs (`aec-…`), **no BTC5m overlap** |
| `l5-halt-watchdog` | running, 20s sweeps, `open_orders: 0`, `read_error: null` | — |

Account cash, read live: **`available_cash_usd = 336.1756`** (`read_ok`,
age 0s).

**Two honest qualifications on "no overlapping live campaigns":**

1. **`pmus-spring-l5-probe` is a live campaign that is enabled right
   now**, with a $12 aggregate cap over 8 markets. It is flat at this
   instant and trades different series, so it does not overlap the BTC5m
   market — but our $1.60 is *additional* to whatever it does during the
   window. If you want the window strictly isolated, that probe should be
   paused for it; I have not touched it.
2. **`l5-pmus-live` reports a phantom leg**, in its own words: *"This
   service's own exposure book still counts 1.0000 in
   `aec-testcr-afg-zim-2026-11-13`, which the venue says the account does
   not hold: a phantom leg adopted and never cleared."* An unreconciled
   1-contract discrepancy between a service's book and the venue. It is
   not BTC5m and not ours, but it means account-wide exposure accounting
   is not perfectly clean going in.

Also noted: that service's heartbeat shows `halt = True` while its
`L5_ARM_SWITCH_READINESS` shows `halt_asserted: False, halt_latch:
CLEAR`. Those two may measure different things (remote fetch vs local
latch), and I am reporting the discrepancy rather than picking a reading.

## 5. Latest settled realized P&L

| scope | figure | label |
|---|---|---|
| **BTC5m lane** | **$0.00** | **no order has ever filled on this lane.** `L5_BTC5M_PERSISTENT_WINDOW` reports `settlement: {considered: 0, settled: [], pending: [], deferred: []}`; the authorized four-window campaign produced zero orders; the current deploy exits at boot. |
| `l5-pmus-live` attributed | **−$13.30** | `realized_attributed`, `realized_unattributed: 0`, `realized_total: -13.30` — settlement-based |
| `l5-pmus-live` legacy | −$91.98 | `legacy_realized_today`, explicitly labelled legacy by the service; **not** the same accounting |
| cash anchor | $338.3456 → $338.3456 | pre-arm and at-disarm reads identical, `delta 0` |
| `pmus-spring-l5-probe` | `matched_pairs 511.97`, `locked_edge_usd 7.5069` | **modelled locked edge, NOT settled realized** — and its own label says "program rewards THEORETICAL; not in P&L" |

**So there is no settled realized profit to report on BTC5m, and I am
not presenting the spring probe's $7.51 locked edge as realized.**

## 6. Regression

**Full suite, same seed and ordering, against the `70915b4ed` baseline:**

| | failed | errors | passed |
|---|---|---|---|
| baseline | 34 | 14 | 22,157 |
| **after this work** | **34** | **14** | **22,280** |

The failing and erroring SETS are identical in both directions: nothing
introduced, nothing incidentally fixed. +123 passes are the new tests.
The 34+14 are the pre-existing debt catalogued in
`PREFLIGHT_EVIDENCE_AND_ONE_WINDOW_REQUEST.md` -- **the suite is not
clean and is not called clean.**

**Six failures were introduced on the first pass and all six were real
signal:**

1. `pmus_q1_executor` is imported lazily on the live path and was **NOT
   in the BTC5m Dockerfile** -- a runtime `ImportError` in production.
   `test_l5_btc5m_image_deps` caught it. Shipped now, and it imports only
   `pmus_fees`, which was already shipped.
2-5. My gate intercepted **every** crossing order, changing the refusal
   reason for SELLs, exits and flips from `TAKER_NEVER_SENT`. Fixed by
   narrowing it so only a COMPLETION enters the new path; every other
   crossing order keeps its original refusal exactly. A test now pins
   that.
6. I had renamed `ONLY_MAKER_BUY_SENT` to `ONLY_BUY_SENT` for no reason.
   Reverted.

New coverage: taker entry never permitted at any setting; partial fills
(partial first leg completes only that much); stale books; absent book;
depth short and absent depth not read as unlimited; duplicate orders and
pending cancellations still applying on the crossing path (asserted by
source ordering: permission → `_order_fault` → `DUPLICATE_INTENT` →
`CANCEL_PENDING` → `MAX_LIVE_ORDERS`); fee calculation at this account's
Tier-1 coefficient; inventory limits; the notional cap judged at the
worst price; and restart recovery, including that a restart does **not**
inherit a crossing permission from a previous process.

The existing adapter harness (`Rig`, `Venue`) was imported rather than
reimplemented; `Venue` raises on any transport call the adapter did not
declare, so nothing reaches a venue in these tests.

## 7. Production watchdog preflight

| | |
|---|---|
| watchdog alive | **PASS** — heartbeat 03:21:30Z→03:43Z, ~20s cadence |
| `cancel_enabled` | **PASS** — `true` |
| registry readable | **PASS** — `read_error: null`, the listed lane reads `:OK:0` |
| no lane exempt from the age latch | **PASS** — `managed_lane_exempt: []` |
| outstanding orders | **PASS** — `open_orders: 0` |
| **BTC5m lane in the ownership registry** | **FAIL** — `managed_lanes` lists only `pmus-spring-l5-probe.railway.internal:8090`. For BTC5m the watchdog would **halt without cancelling**. |
| **standing envelope** | **FAIL** — `ENVELOPE_EXPIRED` |
| **proposed code deployed** | **FAIL** — production is `241f7432d` on the integration branch |

**`l5_live_safety.check()` still cannot be fully executed from this
session**: Railway returns variable **names only** to this session, so
the envelope contents, `FOREIGN_EXPOSURE`, `ACCOUNT_LANES`, the
reservation path and account-risk freshness are unverifiable here, and
the lane has no public domain. It must be run from inside the lane at
arming time, and I will arm nothing if it returns any failure.

## 8. The authorization request

| | |
|---|---|
| **Code SHA** | **`5d3a6b27a`** on `claude/btc5m-favorite-accumulation-wzh2xo` |
| **Prerequisite** | not on the branch production tracks; activation needs a merge into `integration/l5-safe-release-candidate-20261109` **and** a deploy |
| Market | BTC5m, **one** market, **ONE** window |
| Maker | `post_only`, rung **0.08**, clip **$0.40/side**, side cap **$0.80**, unmatched ceiling **$0.40**, repricing **1 tick/clip** |
| **Taker** | **COMPLETION ONLY.** `L5_BTC5M_TAKER_COMPLETION=YES`, notional cap **$1.00**, slippage **1 tick**, min locked edge **1¢** after the real fee, whole-clip depth, book ≤3s |
| Taker entries | **refused by the adapter at every setting** |
| **Max aggregate committed** | **$2.60** (inside $5.00 per-market, $10.00 aggregate) |
| **Max additional loss** | **≤$0.48** (unmatched $0.40 + fees ≤$0.08) of the approved **$1.60** |
| Envelope | must carry `taker_allowed: true` — otherwise gate 2 refuses every cross |
| Disarm | `L5_BTC5M_NOT_AFTER` at the window's close; `restartPolicyType: NEVER`; one window only; STOP latch; envelope `expires_utc` at the close |

### Exact UTC window

A campaign approval naming a past window **refuses** rather than sliding
forward, and I cannot know when you will authorize. So:

> **The window is the first 5-minute boundary at least 25 minutes after
> you authorize** — time to merge, deploy, confirm the image healthy and
> run `l5_live_safety.check()` from inside the lane. I will state the
> exact slug and both UTC timestamps back to you **before** arming, and
> arm nothing if the check returns any failure.

Concretely: authorized at 04:30Z → `cpc-btc-updown-5m-2026-11-17-0500z`,
**05:00:00Z → 05:05:00Z**, `L5_BTC5M_NOT_AFTER = 2026-11-17T05:05:00Z`.

### Two decisions I need from you

1. **The ownership-registry gap.** (a) add the BTC5m lane's internal URL
   to the watchdog's `L5_ORDER_REGISTRY_URL` first (a variable change on
   the watchdog plus a restart), or (b) proceed accepting
   halt-without-cancel for one window bounded at $0.48.
2. **The spring probe.** Pause `pmus-spring-l5-probe` for the window to
   isolate account-wide exposure, or leave it live (it is flat now, $12
   cap, different series).

### The statement I need

> I authorize merging `5d3a6b27a` into
> `integration/l5-safe-release-candidate-20261109`, deploying it to
> `l5-btc5m-live`, and ONE live BTC5m window: maker-only entries at rung
> 0.08 with a $0.40 per-side clip, $0.80 per-side cap, $0.40 unmatched
> ceiling and 1-tick repricing, PLUS taker pair completion capped at
> $1.00 notional requiring ≥1¢ locked after fees and whole-clip depth,
> with an envelope granting `taker_allowed: true`, inside the unchanged
> $5.00 per-market / $10.00 aggregate / $5.00 daily-loss ceilings and a
> maximum additional loss of $1.60, with `L5_BTC5M_NOT_AFTER` at that
> window's close and no continuation. Registry gap: (a)/(b). Spring
> probe: pause/leave.

Until that statement is given: no variable is set, no merge, no deploy,
nothing armed.
