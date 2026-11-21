# Campaign 10 — the $9.00 envelope, proven; and the live authorization

Running SHA `ac27483f`, deployment `2437e556`. **Disarmed. Nothing armed,
no variable changed, no order submitted.**

## 1. The four properties of the $3.00 per-market cap

`max_worst_case_deficit_usd` computes `C − min(up, dn)` for the book an
order would create, **maximised over which outstanding orders fill**. All
four properties verified by measurement; 29 tests in
`tests/test_l5_campaign10_loss_envelope.py`, plus the 61 existing
deficit/corner tests.

### Resting orders, counted as if filled — YES, and exactly

Not conservatively: **exactly**. The maximum over every subset of
outstanding fills is attained at one of four corners (nothing, UP only,
DOWN only, everything) because within one side the choice is
all-or-nothing. All four are evaluated and the worst returned.

```
order 3 @0.60 alone                     deficit $1.87   admitted
  + one resting UP 3 @0.60              deficit $3.76   REFUSED
  corners: none 1.87  up_only 3.74  dn_only 1.87  all 3.74  slack 0.02
```

Fail-closed, all measured: an unreadable resting **side** →
`DEFICIT_OUTSTANDING_SIDE_UNKNOWN` (tested against `GARBAGE`, `""`, `None`,
`ORDER_INTENT_SELL`, `SIDEWAYS`); a resting order with **no price** is
charged at `UNKNOWN_PRICE_WORST_CASE = 1.00`; quantity without cost →
`DEFICIT_QUANTITY_WITHOUT_COST`; unreadable inventory →
`DEFICIT_INVENTORY_UNREADABLE`. Every `ok: False` becomes
`WORST_CASE_DEFICIT:<reason>` at the gate — a failed read is never a pass.

### Partial fills — YES, explicitly, with a proven allowance

`outstanding_for_gates` reads **`remaining`**, so the filled part sits in
inventory and only the unfilled remainder is adversarial. No double count,
no gap.

And the authors found the hard part themselves: **cent rounding breaks
convexity**, so the corner maximum alone is *not* an upper bound — a small
partial fill on an expensive leg is net harmful because a whole cent of
rounding outweighs the `(1 − price)` benefit. Brute force over partial fills
found exactly that. The excess is bounded per order by one cent plus the
per-order allowance, and that much is charged per still-fillable order to
restore a genuine upper bound. Pinned by
`test_the_four_corners_equal_the_exhaustive_worst_case`.

```
1 UP filled + 2 UP resting + completing 1 DOWN   deficit $1.26
rounding slack: $0.02 for one resting order, $0.04 for two
```

### Unsettled inventory — YES for the market being traded

`up_qty/dn_qty/up_cost/dn_cost` are this market's positions, which persist
until the market is retired with a final settlement price.

```
3 UP unsettled + 3 more UP        deficit > $3.00   REFUSED
completed pair 3@0.48 + 3@0.48    deficit -$0.05   (pays on BOTH outcomes)
```

The negative figure is why the loss ceiling can never refuse a
risk-reducing completion.

### Cancellation races — YES

An order leaves the envelope **only** on a venue-authoritative terminal
state. All three removal sites are venue-confirmed: `terminate_order` ("a
venue-authoritative terminal state releases the envelope"),
`apply_order_progress` under `if prog.get("terminal")` ("the only place in
the money path that learns an order's end FROM THE VENUE"), and the
pending-terminal resolution, which routes through the same call. **Nothing
is removed optimistically**, so a cancelling order keeps its remaining
quantity in the deficit maximisation until the venue confirms.

Three further protections: `orders_uncertain` refuses **every** submission
while set and is "cleared ONLY by an authoritative venue reconciliation";
`deadman` cancels by **venue** id ("passing the latter would silently cancel
nothing") and proves it by re-reading; and a fill during cancellation stops
the tick (`DEFENSIVE_FILLED_DURING_CANCEL`) rather than resizing in flight.

## 2. Can three markets exceed $9.00? No — and here is why

**The composition argument.** At every submission into a market, that
market's book worst case is ≤ $3.00, counting all resting orders
adversarially. After the last submission into it, the book can only change
through fills of orders that were already in that maximisation. So each
market's final loss is ≤ $3.00. The campaign cannot trade more than three
markets. Therefore **≤ $9.00**.

Measured, searching for the ceiling rather than assuming it:

```
largest admissible single UP leg at 0.60:  4.83 contracts, worst case $2.9980
three markets at that ceiling:                              $8.9940
3 x cap:                                                    $9.0000
```

(The probe's own `opening_leg_max_contracts = 3` is tighter still than what
the deficit gate would admit for one leg; accumulation across rounds is what
can reach the cap.)

**The window count is enforced in two independent places:** the approval
gate intersects (`eff_w = min(want_w, MAX_WINDOWS)`, never widens) **and**
`L5_LIVE_AUTH_MAX_WINDOWS=3` can only tighten further. Windows are reserved
single-use, and the usability check runs **before** the claim so a refusal
spends no window.

**One direction of conservatism worth naming:** the gate's `inventory` is
per-market but its `outstanding` is **account-wide**, so another market's
resting order is charged into this market's book. That can only add
refusals.

### Why the claim is $9.00 and not $5.00

Both $5 controls read **settled** P&L. `realized_today()` is
`realized_attributed + realized_unattributed`; the daily halt reads
`realized_current_utc_day()`; and the campaign gate adds
`max(0, realized − baseline) + open_worst_case`, where the second term is
*this market's* book. BTC15m windows are back-to-back, so window 1's loss
has not settled when window 2's first order is gated — in that instant it is
in neither term. The $5 gates tighten as settlements land; they do not bound
three consecutive windows at $5.

> **Conservative maximum: $9.00.** Not $5, not $8. $8 was my arithmetic on
> the halt and no control enforces it.

Also enforced per market, from the live preflight: `residual_ceiling`
`effective_cap_contracts: 3`, `which_binds: ABSOLUTE` (the separate
SafetyLimits outer bound is 10 and is not the approved figure).

## 3. The live authorization

### The approval document — graded `approved: true`, `clamped: false` on every field

```json
{"approved":true,"nonce":"camp10-<16 hex chars YOU generate>","series":"cpc-btc-updown-15m-","issued_utc":"<install time, UTC>","expires_utc":"2026-10-30T02:45:00Z","max_windows":3,"max_total_exposure_usd":"20","max_daily_loss_usd":"5","max_open_submissions":12,"max_complete_submissions":12,"max_replace_submissions":6,"max_total_submissions":27,"execution_mode":"MAKER_ONLY","defensive_crossing":false,"autonomous_maker_openings":true,"controller_open_enabled":true,"controller_provenance":"CONTROLLER_GENERATED","worst_case_envelope_usd":"9.00","worst_case_basis":"3 markets x $3.00 max_worst_case_deficit_usd","windows":["cpc-btc-updown-15m-2026-10-30-0145z","cpc-btc-updown-15m-2026-10-30-0200z","cpc-btc-updown-15m-2026-10-30-0215z"]}
```

The scope fields (`execution_mode`, `defensive_crossing`,
`autonomous_maker_openings`, `controller_open_enabled`,
`worst_case_envelope_usd`, `windows`) are **not** in `CA.REQUIRED_FIELDS`:
the gate ignores them and they are carried as the record of what you
authorized. Enforcement is `L5_CONTROLLER_OPEN` + `TS.active(market)` for
the openings, `AUTH.EXECUTION_MODE` (a code constant) for maker-only, and
`DEF.enabled()` false for crossing.

### Windows — future, consecutive, none expired

| | Slug | Starts |
|---|---|---|
| 1 | `cpc-btc-updown-15m-2026-10-30-0145z` | 01:45Z |
| 2 | `cpc-btc-updown-15m-2026-10-30-0200z` | 02:00Z |
| 3 | `cpc-btc-updown-15m-2026-10-30-0215z` | 02:15Z |

Entry runway 300 s, entry cutoff T+600 s. **These slide if you approve after
~01:40Z** — the rule is *the first three consecutive
`cpc-btc-updown-15m-` windows beginning after arming and closing before
`L5_LIVE_AUTH_NOT_AFTER`*. I will recompute on your go and never reuse an
expired window or approval.

### Expiry

| | |
|---|---|
| `expires_utc` | **2026-10-30T02:45:00Z** (last window closes 02:30Z) |
| `L5_LIVE_AUTH_NOT_AFTER` | **2026-10-30T02:40:00Z** — 5 min inside, so the hard stop fires first |

### Variables — eight, every one a reduction or a switch

| Variable | Now | Set to |
|---|---|---|
| `L5_CAMPAIGN_APPROVAL` | stale (`ac855ce3…`) | the document above |
| `L5_LIVE_AUTH_NOT_AFTER` | EXPIRED (−9709 s) | `2026-10-30T02:40:00Z` |
| `L5_LIVE_AUTH_MAX_WINDOWS` | 8 | **3** |
| `L5_LIVE_AUTH_MAX_OPEN` | 24 | **12** |
| `L5_LIVE_AUTH_MAX_COMPLETE` | 32 | **12** |
| `L5_LIVE_AUTH_MAX_REPLACE` | 16 | **6** |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | 72 | **27** |
| `L5_CONTROLLER_OPEN` | unset | **`YES`** |

Then your arm token: `PMUS_Q1_SESSION_ID` and `PMUS_Q1_ARM` equal, and
`PMUS_Q1_LIVE=YES`.

Preserved unchanged: `$20` exposure, `$5` daily realized-loss halt, `$3.00`
per-market deficit, `$12.00` cash principal, 1 live order, pair ceiling
0.96, first-leg band 0.20–0.60, 3 contracts, `$3.00` opening notional,
cutoffs 600/720 s, ttl 120 s, cancel margin 300 s,
`L5_DEFENSIVE_COMPLETION` off. No new signing system, no new service.

## 4. Final preflight

**Now (pre-arm), from the running service:**

```
L5_AUTHORIZATION_PREFLIGHT  ok: false
  fail: owner_arm_token, absolute_deadline (EXPIRED -9709s), campaign_approval (approval_stale ac855ce3…)
  PASS: shadow_lock, claim_ttl_s, market_series, role_binding, session_id,
        window_budget_env, halt_gate, residual_ceiling (cap 3, ABSOLUTE)
  reserved_nothing: true
L5_LIVE_PREFLIGHT           PASSED   (market/venue)
L5_EFFECTIVE_RUNTIME_CONFIG PASS: false — 8 rows NO_APPROVAL_IN_FORCE
armed: false   live_orders: 0   controller_open_enabled: false
```

**Under the proposed configuration, measured:**

```
CA.approval_gate().state()   approved: true, why: null, clamped: none
L5_EFFECTIVE_RUNTIME_CONFIG  PASS: True, checked: 26, failed: []
  budgets 12/12/6/27 · exposure 20 · daily loss 5 · windows 3 · live orders 1
  maker_pair_ceiling 0.96 · maker_only_forbids_defensive_crossing True
  mandate_signing_key_absent_from_runtime True
  controller_open_enabled True · defensive_execution_enabled False
  ts_two_order_pair_enabled False · deficit 3.00 · band 0.20–0.60 · 3 contracts
```

The three authorization failures are exactly the three the eight variables
and your arm token clear.

## On your go

I verify `L5_AUTHORIZATION_PREFLIGHT ok: true` and
`L5_EFFECTIVE_RUNTIME_CONFIG PASS: true`, recompute the three windows
against the clock, run them without pauses, and report venue-confirmed
orders, fills, completed pairs, residual inventory, settlement and
**settled** realized net P&L.

**Approve the $9.00 envelope.** ARGMAX is −$3.14 over 38 windows; expect a
small loss. What is being bought is whether an autonomous maker opening
reaches PMUS, fills, pairs and settles — which has never happened.
