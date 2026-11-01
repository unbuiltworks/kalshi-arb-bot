# Two-sided validation campaign — proposal for owner approval

**NOT ARMED. NOT APPROVED. No order placed, no limit changed, no variable
written.** `owner_arm_token false`, submission gate `blocked true`,
`live_orders 0`.

Uses the existing `L5_TS_TWO_ORDER_PAIR` mechanism. Nothing was built to
replace it.

---

## 1. Pre-arming verification — three of four pass, and one needs a reboot

### (a) `L5_TS_TWO_ORDER_PAIR` in the running process — **VERIFIED ON**

From `L5_EFFECTIVE_RUNTIME_CONFIG`, emitted by the deployed executor itself
(deployment `0072e385`, 2026-10-31T18:41:37Z):

```
ts_two_order_pair_enabled   TS.two_order_pair_enabled()   effective: true
max_live_orders             R.max_live_orders()           effective: 1    ok
window_strategy             env L5_WINDOW_STRATEGY        TWO_SIDED       ok
ts_opening_leg_max_contracts                              "3"
ts_min_first_leg_own_price / ts_max_first_leg_own_price    "0.20" / "0.60"
max_worst_case_deficit_usd                                "1.50"
aggregate_exposure_usd / daily_realized_loss_halt_usd      "20" / "5"
```

**It is already enabled. No variable change is needed** — correcting my last
report, which said I could not confirm it. The value is redacted to this
session, but the *running process* publishes what its own accessor returns,
which is better evidence than the variable text.

`max_live_orders` stays **1**; the second slot raises the ceiling by exactly
one, for one qualifying order. That is the mechanism, not a blanket raise.

### (b) Exact SHAs

| | deployment | commit |
|---|---|---|
| executor `l5-pmus-live` | **`0072e385`** | `00a78b50` — `src`/`deploy`/`tests` byte-identical to the approved `cbcfb118` |
| watchdog `l5-halt-watchdog` | **`5ef90355`** | `2a56a152`, carrying the cancel-all fix `ebaef2d8` |

### (c) Two-order risk accounting — **VERIFIED**, by driving the real function

| state | gate | final guard |
|---|---|---|
| leg 1 from flat, any size | exempt | exempt |
| leg 2 at **3.00** while leg 1 rests | pass | pass |
| leg 2 at **3.01** while leg 1 rests | **BINDS** | **BINDS** |

Both resting orders are priced as if each fills in full, independently; a
resting opposite leg is **never** credited as a hedge. Checked at both call
sites, which must agree and do.

**A finding worth your attention.** A *second*, older residual check inside
`limits.refuse` tests `abs(residual_after + outstanding_residual)` — a signed
**net**. For a two-sided pair that nets to **0.00**, so `MAX_ABS_RESIDUAL`
there does **not** bind. It is the same netting defect class as 1415z,
surviving in a different control. **This is not a hole:** the fixed
worst-case gate binds first and the final submit-time guard binds again, so
nothing reaches the wire. But it means `max_abs_residual` is *not* the
control doing the work, and the 3-contract bound you are preserving is
enforced by `residual_cap_decision`, not by it. Worth fixing; not a blocker.

### (d) Zero venue obligations — **VERIFIED**

Watchdog `open_orders 0`, `overdue []`, `still_open []`, `unverified []`,
`not_ours []`, `read_error null`. Executor `live_orders 0`. Ledger
−8.82910000.

---

## 2. Two conflicts in the request, surfaced rather than absorbed

### A. **10 windows silently becomes 8**

`src/l5_campaign_approval.py:63` holds `MAX_WINDOWS = 8`, and line 256 is
`eff_w = min(want_w, MAX_WINDOWS)`. **A document asking for 10 is issued with
8** — it is clamped, not rejected. I will not hand you a "10-window
approval" that is really 8.

Your choice:

- **Option 1 — run 8 windows.** No code change, no safety-control change.
  Costs some statistical power on the primary metric.
- **Option 2 — raise `MAX_WINDOWS` to 10.** A one-constant change to a
  *campaign-size* ceiling. It moves **no money bound**: $20 aggregate,
  $1.50 per-market, $5 daily and the 3-contract residual cap are untouched,
  and `MAX_CAMPAIGN_S = 4h` still covers 2.5 hours of windows.

**I recommend Option 1** and will prepare it as the default, because it needs
no safety-control change at all. Say the word and I will prepare Option 2
instead as a named, separate change.

**$20 aggregate is instantaneous, not cumulative** — `committed` is released
when a window retires (`committed_released == cost_total` on every Stage B2
record), so 8 or 10 sequential windows at ~$2.50 each never approach it.

### B. **The taker-crossing policy is ENABLED in the running process**

```
taker_policy_enabled        TCP enabled        effective: TRUE
taker_policy_max_outlay_usd                    "1.50"   per cross
taker_policy_window_outlay_usd                 "3.00"   per window
taker_policy_maker_patience_s                  90.0
```

You require maker-only. `TCP.authorize` needs **both** `enabled` *and*
`owner_authorized` (`L5_TS_COMPLETION_CROSS`), refusing
`OWNER_AUTHORIZATION_ABSENT` without the second — **and the second switch was
not reported anywhere.** The verifier's own comment says a crossing policy
visible only by reading an env var "is exactly the configuration an operator
should never have to infer", and it was reporting one of the two.

**I added the missing rows** (`taker_policy_owner_cross_authorized` and a
derived `taker_crossing_reachable`), 12 tests. Read-only reporting; no
behaviour change.

**Full-suite parity, differenced against the pre-change commit `27d75333`:**

| | pre-change | now |
|---|---|---|
| passed | 14,814 | **14,846** (+32 = 20 emergency-cancel + 12 taker-observability, exactly) |
| failed / errors | 54 / 23 | **54 / 23** |
| sha of sorted failing-test ids | `b70451e1…` | **`b70451e1…`** |

**Zero new failures, zero fixed.** Both changes in this workstream — the
emergency cancel-all and these config rows — add only their own tests and
disturb nothing. The 54+23 remain pre-existing and unrelated (mostly a missing
`httpx`).

**Pre-arming requirement: `taker_crossing_reachable` must read `false`.** The
config is emitted at boot, so this needs one disarmed restart to observe.
Stage B2's 70/70 maker orders, 0 taker and $0 fees are consistent with the
switch being off, but they are **not proof** — no opportunity may have
cleared the $0.25 margin floor. If it reads `true`, set
`L5_TS_COMPLETION_CROSS` to anything but `YES` before arming.

---

## 3. Proposed campaign scope

| field | value |
|---|---|
| series | `cpc-btc-updown-15m-` (code allowlist, cannot be widened) |
| **windows** | **8 consecutive** (Option 1) |
| **proposed start** | **2026-11-01 16:00:00Z** |
| windows covered | 1600z, 1615z, 1630z, 1645z, 1700z, 1715z, 1730z, 1745z |
| campaign ends | 18:00:00Z |
| **`L5_LIVE_AUTH_NOT_AFTER`** | **17:45:00Z** — the last eligible window's *start*, so an 1800z window cannot be absorbed by the expiry. This is the Stage B2 15:00Z hole, closed. |
| approval `starts_utc` | 16:00:00Z (now actually settable — `ASK_STARTS_UTC`) |
| aggregate exposure | **$20** (instantaneous; expected peak ~$2.50) |
| per-market worst-case deficit | **$1.50** |
| daily settled-loss halt | **$5**, applied without exception |
| residual cap | **3 contracts**, enforced by `residual_cap_decision` |
| max live orders | **2**, only via `second_leg_slot_refusal` |
| execution | **maker-only, post-only, no crossing** |
| per-leg size | `min(3.00, 1.50 / dearer own price)` ≈ **2.63 contracts** |
| rounds per window | **2** (`L5_TS_ROUND_ALLOCATIONS=2`, down from 4) |
| **submitted-order budget** | **OPEN 16 / COMPLETE 32 / REPLACE 8, backstop 56** |

### Why the budget is split that way — a third netting instance

I had this backwards first and checked rather than assumed. `submission_class`
classifies by **effect**, computed by `prospective_residual`, whose own label
is `"basis": "PROSPECTIVE_NET: realized + every live order in this market as
if it filled"` — **the same netted basis as the 1415z defect.**

For the second leg of a simultaneously-quoted pair (DOWN *q* while UP *q*
rests): netted before = +*q*, after = **0**, so `abs_after < abs_before` and
it is classified **COMPLETE** and flagged `reducing=True`. `balancing_refusal`
reads the same basis and permits it — no overshoot (after is 0, not negative)
and it does "reduce".

**Economically that is wrong.** The second leg of a two-sided quote is not
risk-reducing; if only it fills, it opens an equal residual on the other side.
It is half of an opening.

**What it does and does not affect.** It does **not** loosen exposure: the
fixed worst-case gate and the final submit-time guard both still bind at 3.01
contracts, which is what bounds the money. What it changes is **budget
accounting** — second legs are charged to COMPLETE, not OPEN — and it
**exempts them from the flat total backstop**, since
`total_backstop_binds(reducing)` returns `not reducing`.

So the budget is set to match the behaviour rather than my expectation of it:
**OPEN 16** = one first leg × 2 rounds × 8 windows; **COMPLETE 32** covers the
16 second legs *and* genuine sequential repairs of half-filled pairs; REPLACE
8 for downward-only requotes. Worst case 56 orders, every one individually
exposure-checked.

**Not fixed here, deliberately.** You said use the existing mechanism and
build no replacement, and changing risk classification immediately before a
campaign is the wrong moment. I recommend aligning `submission_class` and
`balancing_refusal` onto the worst-case basis as a **separate** change after
this campaign, and I will not touch them without your say. Logged as the
third surviving instance of the netting defect, after `limits.refuse` in
§1(c).

**The approval must be minted no earlier than 14:00Z**, because
`eff_expires = min(expires, issued + 4h)`. An approval issued now would
expire at 11:00Z, before the campaign starts. Any `:00/:15/:30/:45` start
works if you prefer a different time; tell me and I will re-mint.

## 4. Metrics

**Primary mechanism metric — matched contracts per filled first leg**, against
the sequential baseline of **≥1.68** (11.79 matched contracts / 7 windows with
a filled leg; a lower bound, since 1400z's matched qty is on the volume tape).

**Required economic metrics**, every one settlement-only:

| metric | sequential baseline |
|---|---|
| completed-pair realized P&L | **+$0.3895** |
| residual realized P&L | +$1.0260 |
| total settled net P&L | +$1.4155 |
| fill rate (filled / acknowledged, per leg and per pair) | 70 acked, 7 filled in 1315z; not aggregated in the baseline |
| pair cost on completed pairs | 0.96 / 0.97 / 1.00 observed |
| adverse selection | complement **+5 ticks** dearer (1430z), **+2** (1445z) |

**Unfilled quotes count as unfilled.** A quoted-but-unfilled pair contributes
zero matched contracts and no pair cost — never a planned pair booked as
though it happened. Five counts reported separately: decision attempts,
transport submissions, venue acknowledgements, fills, cancellations/rejections.
1445z reported `submitted 70` against `acknowledged 1`, which is why.

**Inter-leg adverse drift should be zero by construction** — both legs quoted
off one snapshot. A non-zero value means the implementation is not actually
simultaneous, and that is a finding about the code, not the market.

### What this cannot establish

**A matched-fill improvement alone will not be called profitability.** At
~2.63 contracts and a 0.95 pair a completed pair earns **$0.13**; eight
windows all completing is about **$1.05**. That is inside the noise of a
single Stage B2 residual, which swung **$5.30** between best and worst on
comparable size. The campaign buys one mechanism number. Any P&L it produces
is an observation, and the all-time ledger stays **−$8.83** either way.

## 5. Stop rule

**Immediate halt, campaign over:**
- **Any safety-control discrepancy** — a reported cap differing from observed
  exposure, `submitted ≠ acknowledged` without an explained cause, watchdog
  `still_open` / `unverified` / `not_ours` non-empty, any cancellation not
  venue-confirmed terminal, any third order, any order outside the series.
- **Unexpected exposure** — residual above 3.00 contracts, per-market
  worst-case deficit above $1.50, aggregate above $20, or any same-side
  second leg (an EXTEND the slot rule should make impossible).
- **Any failure of emergency cancellation** — a latched HALT that does not
  produce `emergency_cancel: true` and a venue-confirmed terminal state for
  every known order.
- **The $5 daily settled-loss halt, without exception.**

**Halt for worsening residual losses:** cumulative settled residual P&L
≤ **−$2.50**, or residual frequency above the **71.4%** baseline over ≥5
settled windows.

**Not a stop:** a negative P&L run on its own. At $0.13 a pair that is noise,
and stopping on noise is the Stage B2 mistake.

## 6. Arming procedure, and the race it must avoid

**Arming** requires all three to agree:
`PMUS_Q1_LIVE=YES`, `PMUS_Q1_SESSION_ID=<id>`, `PMUS_Q1_ARM=<the same id>`.

**Disarming is where Stage B2 lost 5m53s.** Clearing `PMUS_Q1_ARM` takes
effect only at restart: the write landed and window 8 armed **52 seconds
later** anyway, trading until the restart. So:

1. **To stop fast, latch the HALT** — do not clear a variable. The latch is
   read every tick and now drives emergency cancel-all.
2. Clear `PMUS_Q1_ARM` **after** the latch, for the next boot.
3. Verify `armed false`, `live_orders 0`, watchdog `open_orders 0`.

## 7. Exactly what I need from you

1. **Choose 8 windows (Option 1, recommended) or authorize `MAX_WINDOWS` → 10.**
2. **Confirm the start time** — 2026-11-01 16:00:00Z proposed, or name another.
3. **Authorize one disarmed restart** so `taker_crossing_reachable` can be
   read, and confirm it reads `false` before arming.
4. **Explicit approval to mint the authorization** (I cannot approve it), to
   be issued no earlier than 14:00Z.
5. **Explicit arming**, separately, after 1–4.

Nothing happens until all five. No capital increase, no new services, no new
branches, no crossing, no residual-cap change is requested.
