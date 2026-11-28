# Campaign 10 — activation settings, for explicit approval

**Deployed and verified disarmed. Nothing is armed. No order has been
submitted. No variable has been changed.**

---

## Deployment result

| | |
|---|---|
| **Running SHA** | **`ac27483f4227cb64ee54e8137588a72cc32f0682`** |
| Deployment | `2437e556-fd5a-463e-b661-e4c29448d68e` — **SUCCESS** |
| Built | 2026-10-30T01:00:07Z → boot 01:02:03Z |
| Previous | `35109a3a` / `7fbf65d6` |
| Region / replicas | `us-east4-eqdc4a`, 1 |

### Why the SHA is not `421872f5`

`watchPatterns` is `["deploy/l5_pmus_live/DEPLOY_TRIGGER"]`, so the trigger
file had to change in the commit that deploys — repinning `commitSha` alone
returns SKIPPED. `ac27483f` is that commit. Verified: `421872f5..ac27483f`
touches only `deploy/l5_pmus_live/DEPLOY_TRIGGER` and one test file, and
**zero** files in the Dockerfile COPY list. Its production content is
byte-identical to the `421872f5` you compared.

### Proof the new code is actually running

`L5_EFFECTIVE_RUNTIME_CONFIG` at 01:02:04Z carries a row that **does not
exist in `35109a3a`**:

```
controller_open_enabled   source: env L5_CONTROLLER_OPEN   effective: false
```

That one line establishes both that the build is live and that the switch is
off.

---

## Boot verification

### Authorization and arming — requirement 2

| Signal | Value |
|---|---|
| `L5_LIVE_HB.armed` | **`false`** |
| `L5_LIVE_HB.owner_arm_token` | `false` |
| `L5_LIVE_HB.live_orders` | **`0`** |
| `L5_LIVE_START.owner_arm_token` | `false` |
| `controller_open_enabled` | **`false`** |
| `maker_only_forbids_defensive_crossing` | `true`, `ok: true` |
| `defensive_execution_enabled` | **`false`** |
| `ts_two_order_pair_enabled` | `false` (one resting order) |
| `mandate_signing_key_absent_from_runtime` | `true`, `ok: true` |

### I owe you a correction, and it reverses my last one

There are **two different preflights**, and I conflated them last turn:

- **`L5_LIVE_PREFLIGHT`** — the market/venue preflight. **PASSED** (attempt
  4, 01:02:51Z).
- **`L5_AUTHORIZATION_PREFLIGHT`** — the authorization preflight.
  **`ok: false`**, `fail: ["owner_arm_token", "absolute_deadline",
  "campaign_approval"]`.

So my **original** "three counts" report was right; my "correction" last turn
was wrong, because I read the market preflight and assumed it was the only
one. Both reports are accurate about different things. The three
authorization failures, from this boot:

```
owner_arm_token     FAIL   blocks OWNER_AUTHORIZATION_ABSENT (step 11)
absolute_deadline   FAIL   EXPIRED, expires_in_s -9709.0  (step 26)
campaign_approval   FAIL   approval_stale                 (step 41 area)
```

`L5_EFFECTIVE_RUNTIME_CONFIG` separately reads `PASS: false` on its eight
`NO_APPROVAL_IN_FORCE` rows — the same single root cause as
`campaign_approval`.

### Risk settings preserved — requirement 1

Identical to `35109a3a`, read from this boot:

```
L5_LIVE_START.limits: max_usd_per_market 20, max_abs_residual 10,
  max_total_exposure_usd 20, max_concurrent_markets 1, max_live_orders 1,
  daily_realized_loss_halt_usd 5
L5_LIVE_GATES: max_cash_deployed_usd 12.00, max_worst_case_deficit_usd 3.00,
  max_unmatched_contracts 5, max_l5_daily_loss_usd 5, cash NO_RECYCLING
TS: pair ceiling 0.96, first leg 0.20-0.60, 3 contracts, notional 3.00,
  entry cutoff 600s, complete cutoff 720s, ttl 120s, cancel margin 300s
```

### Boot, book, reconciliation, watchdog — requirement 4

- **Boots clean.** `L5_LIVE_START` 01:02:03.999Z, `credentials_present:
  true`, auth ledger on `/data` with `durable: true`, holder identity
  complete (boot/deployment/pid/replica/role).
- **Authenticated book is fresh.** Across three consecutive attempts:
  `stream_age_s` 0.039 → 0.048 → 0.041, `two_sided: true`, `book_updates`
  201 → 331 → 567, `MARKET_STATE_OPEN`. The REST route blocked attempts 2–3
  (`first_blocking: book_readable`) and attempt 4 PASSED with
  `book_read_attempts: ["attempt1:ok"]`. **The websocket mirror is the
  reliable book, and it is the one the controller reads.**
- **Reconciliation healthy.** `reconciled: true`,
  `open_orders_at_venue: 0`, `live_orders_pending_terminal: []`,
  `live_orders_released_unacked: []`, `live_orders_adopted: []`,
  `pending_terminal_total: 0`, `venue_net: {}`.
- **Watchdog operating.** `L5_LIVE_HB` emitting from 01:02:08Z. The ingest
  cursor loaded `durable: true` with a `seeded_at` from a **previous** boot,
  which is the proof a restart resumed rather than discarding the feed page.
  `feed_errors: 0`.
- **Campaign ledger readable.** `camp1.camp-9OuJSAHK...`, `closed: false`,
  `peak_usd 0`, `remaining_usd 29.30`, `incremental_room_usd 19.7088`,
  `journal_entries 34`, `journal_errors 0`.

### No unexpected orders or positions — requirement 6

`open_orders_at_venue: 0` and `live_orders: 0`. `venue_net: {}` — the venue
holds nothing. The only ledger drift is two **long-settled** markets,
`2026-10-12-0030z|DOWN` (ours 2, venue 0) and `2026-10-14-2230z|UP` (ours
2.9, venue 0), resolved toward the venue and listed under
`stranded_without_stamp`. Both predate this deployment and neither is an
open obligation.

### Autonomous decisions are observable without submitting — requirement 5

Two facts, and they are in tension, so I am stating both rather than
choosing:

**In production right now, there is nothing to observe.** With
`L5_CONTROLLER_OPEN` unset — which requirement 2 mandates —
`maybe_controller_open` returns before it reads a book. That is the correct
reading of requirement 2, and it means requirement 5 cannot also be
satisfied in production at this instant.

**The mechanism is verified, against the real executor.** A disarmed
transport that refuses the way `PMUSTransport` refuses produces:

```
sent:   []                                  nothing reached the venue
ORDER:  submitted false, reason NOT_ARMED, detail "transport is DISARMED"
        intent: side DOWN, price 0.37, qty 3, mode MAKER,
                action_key CONTROLLER|OPEN|m|r0|DOWN
claims: {UP: False, DOWN: False}            NO claim consumed
```

The full autonomous decision, the book it was gated against and the
two-sided plan that priced it are all in the record; the campaign budget is
untouched. Six tests pin this.

**Where the guarantee lives, measured rather than assumed.**
`PMUSTransport.submit()` calls `_require_armed()` as its first act and
raises `NotArmed` without attempting a POST; `arm()` demands **both** an
in-process preflight PASS **and** the owner arm token. Separately, the block
consuming the submission claim is wrapped in `if getattr(self.transport,
"armed", False)`, so a disarmed run spends nothing.

**Two defects found while verifying this, both pre-existing, neither fixed:**

1. `disarmed_refusal()` is checked *inside* that same `if ... armed` guard,
   so within it `armed` is always truthy and the call always returns `None`
   — **the branch is unreachable**. Resolved by AST, not by reading. It is
   harmless, because the enclosing condition is strictly stronger and
   achieves the same end, and nothing relies on it. A test now pins the fact
   so it is not rediscovered as a safety claim. Out of scope to fix here.
2. `L5_LIVE_START` emits `"frozen_rule": "030d8bf"` as a **hardcoded string
   label**, not a computed check — so it cannot halt, but it now misstates
   what is running, since the translation was deliberately re-pinned to
   `dda950a57331cba8`. Cosmetic but it is a provenance label, so it should
   be corrected in the next deployment rather than left to mislead. I did
   not redeploy for a label.

### Other services — requirement 7

Untouched. Only `l5-pmus-live`'s source pin changed. No variable was set, no
service created, no infrastructure added.

---

## PROPOSED ACTIVATION SETTINGS — require your explicit approval

### A. The owner approval document

Install as `L5_CAMPAIGN_APPROVAL`, as one line of JSON. Graded through the
real `CA.approval_gate()`: **`approved: true`, `why: null`, `clamped: false`
on every field.**

```json
{"approved":true,"nonce":"camp10-<16 hex chars you generate>","series":"cpc-btc-updown-15m-","issued_utc":"2026-10-30T01:20:00+00:00","expires_utc":"2026-10-30T02:30:00+00:00","max_windows":3,"max_total_exposure_usd":"20","max_daily_loss_usd":"5","max_open_submissions":12,"max_complete_submissions":12,"max_replace_submissions":6,"max_total_submissions":27}
```

- **Generate the nonce yourself** (≥8 chars; 16 hex recommended). It is
  claimed single-use before anything arms, so a nonce I pick and publish
  here is a nonce that has been written down in a chat log.
- Set `issued_utc` to install time. It must be within
  `MAX_CAMPAIGN_S` (4h) of use, and the effective expiry is clamped to
  `issued + 4h`.

### B. Expiry

| | |
|---|---|
| `expires_utc` in the document | **2026-10-30T02:30:00Z** |
| `L5_LIVE_AUTH_NOT_AFTER` | **2026-10-30T02:25:00Z** |

The absolute deadline sits 5 minutes **inside** the document expiry so the
hard stop fires first, and 10 minutes past the last window's close so
settlement and audit still fall inside the authorization.

### C. Variable changes — seven, every one a reduction or a switch

| Variable | Now | Set to |
|---|---|---|
| `L5_CAMPAIGN_APPROVAL` | stale | the document in A |
| `L5_LIVE_AUTH_NOT_AFTER` | EXPIRED (−9709s) | `2026-10-30T02:25:00Z` |
| `L5_LIVE_AUTH_MAX_WINDOWS` | 8 | **3** |
| `L5_LIVE_AUTH_MAX_OPEN` | 24 | **12** |
| `L5_LIVE_AUTH_MAX_COMPLETE` | 32 | **12** |
| `L5_LIVE_AUTH_MAX_REPLACE` | 16 | **6** |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | 72 | **27** |
| `L5_CONTROLLER_OPEN` | unset | **`YES`** |

Then the arm token, which is yours alone: `PMUS_Q1_LIVE`,
`PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` (with `ARM == SESSION_ID`).

**Unchanged, and already at the figures you asked for:**
`L5_MAX_TOTAL_EXPOSURE_USD=20`, `L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD=5`,
`L5_MAX_WORST_CASE_DEFICIT_USD=3.00`, `L5_MAX_CASH_DEPLOYED_USD=12.00`,
`L5_MAX_LIVE_ORDERS=1`, `L5_DEFENSIVE_COMPLETION` off, `L5_TS_*` as
deployed.

**Verified:** with A and C applied, against production's own values for
everything else — `PASS: True, checked: 26, failed: []`. A measured result.

### D. The three windows

Three **consecutive** BTC 15-minute windows:

| | Market slug |
|---|---|
| 1 | `cpc-btc-updown-15m-2026-10-30-0130z` |
| 2 | `cpc-btc-updown-15m-2026-10-30-0145z` |
| 3 | `cpc-btc-updown-15m-2026-10-30-0200z` |

Span 01:30Z → 02:15Z, 45 minutes, inside both expiries.

**If you approve after 01:25Z, these slide.** Entry needs
`ts_min_entry_runway_s = 300`, so arming must land at least 5 minutes before
a window's entry cutoff. The rule, not the literal times: *the first three
consecutive `cpc-btc-updown-15m-` windows that begin after arming and end
before `L5_LIVE_AUTH_NOT_AFTER`.* Give me a go and I will name the actual
three from the clock at that moment and move `NOT_AFTER` to match.

### E. Expected maximum loss

| Bound | Amount | Source |
|---|---|---|
| Worst case per window | **$3.00** | `max_worst_case_deficit_usd` — prospective: no order may leave the book able to lose more than this on *either* settlement outcome |
| Structural cap, 3 windows | $9.00 | 3 × $3.00 |
| Daily realized loss halt | $5.00 | `R.daily_realized_loss_halt()` — stops new windows |
| **Binding worst case** | **$8.00** | the halt stops new windows at $5 cumulative, and one window already on the book can add at most $3 |
| Exposure ceiling | $20.00 | never approached: a 3-contract pair at ≤0.96 costs ≤$2.88 |
| Cash principal ceiling | $12.00 | `L5_MAX_CASH_DEPLOYED_USD` |

**Expected maximum loss: $8.00**, hard-capped at $9.00 by window count.

Maker fees are **$0.0000** on PMUS, confirmed over 293 real maker fills, so
a stranded maker leg costs its principal and nothing more. Defensive
crossing is disabled, so no taker fee can arise.

---

## What the next step measures

Real PMUS maker fills, completed pairs, residual inventory and **settled**
net P&L — labelled `submitted → filled → settled → realized`, never
modelled. Three windows is an executability and safety test. ARGMAX is
**−$3.14** over 38 windows and every opening-price band measured
net-negative, so the honest expectation for three windows is a small loss;
what makes it worth running is whether an autonomous opening fills, pairs,
and settles through the real money path at all.

**Nothing arms until you say so.**
