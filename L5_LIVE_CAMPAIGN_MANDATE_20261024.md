# L5 two-sided live campaign — proposed mandate

**Status: NOT ARMED. Nothing has been submitted. No approval document has
been written and no Railway variable has been set.**

This is the scope for approval, plus one blocking finding about *how* that
approval can be made to count. Read §6 before approving: the mechanism that
would consume your approval today cannot tell your approval from mine.

---

## 1. Venue reconciliation — complete, book confirmed

From `L5_LIVE_RECONCILE` at 2026-10-24T20:54:59Z, authority `VENUE`:

| check | value |
|---|---|
| `reconciled` | **true** |
| `uncertainty_cleared` | **true** |
| `open_orders_at_venue` | **0** |
| `pending_terminal_total` | **0** |
| `live_orders_released_unacked` | none |
| `live_orders_adopted` | none |
| `quarantined` | none |
| `http_429` | 0 |
| `armed` | **false** |

Nothing is outstanding at the venue. Two unresolved items, neither blocking
but both on the record:

- **Book-versus-venue drift, 2 markets.** `2026-10-12-0030z|DOWN` ours 2 /
  venue 0, and `2026-10-14-2230z|UP` ours 2.9 / venue 0. Both are already
  named in `gross_detail_lost` and `stranded_without_stamp`. The venue's net
  is the position of record, so these are ours-but-not-theirs and cannot
  produce a surprise obligation.
- **49 legs in `leg_cost_basis_unknown`**, spanning btc/eth/sol/xrp 15m
  markets from 2026-10-14 to 10-16. A leg with no cost basis cannot have its
  realized P&L computed, which is part of why the unattributed line below is
  non-zero.

**Cash and realized position:**

| figure | value | source |
|---|---|---|
| all-time realized, account-wide | **−$11.6213** | `realized_today()` (attributed + unattributed; an **all-time** total despite the name) |
| account-wide cumulative ceiling | $20.00 | `max_cumulative_loss_usd` |
| **remaining account-wide headroom** | **$8.3787** | computed |
| daily halt figure | `realized_current_utc_day()` | the daily gate reads this, **not** the all-time total |

The daily-vs-all-time distinction matters: `realized_today()` sums all of
history, so feeding it to a limit named "daily" would make that limit a
lifetime cap. The gate at `l5_live_execution.py:5800` correctly reads
`realized_current_utc_day()`, so the −$11.6213 does **not** trip the $5 daily
halt. It *does* consume account-wide headroom, which is why the campaign
budget below is $5 and not more.

---

## 2. The two-sided policy — it already exists, nothing new was built

`src/l5_two_sided_probe.py` (99,892 chars) is a one-window two-sided BTC15m
probe, already integrated with the executor's second maker slot
(`second_leg_slot_refusal`) and the authorization layer. It is inert by
default and requires both an experiment id and a market pattern to activate.

**It quotes both sides deliberately.** It generates two intents and prices
them together through `fee_floor(up_px, dn_px)` before either is sent — it is
not "permit a second order after the first fills". With
`L5_TS_TWO_ORDER_PAIR=1` both legs may rest simultaneously, and that slot is
granted *only* to an order demonstrably on the opposite side of the same
round in the same market as the one already live. It is explicitly **not**
`max_live_orders = 2`, which would permit two independent first legs sharing
one cash envelope.

**It is fee-honest.** Both legs are priced as if they paid taker fees
(`TAKER_THETA` 0.0695); the maker rebate is used only to report upside and
never to justify an order. `MAX_PAIR_COST` 0.96 is a gate, not a side effect
of a depth setting.

No new implementation is proposed. This mandate activates existing,
tested code.

---

## 3. Proposed campaign scope

| parameter | proposed | ceiling that binds it |
|---|---|---|
| **Maximum total capital at risk** | **$5.00** | `max_cash_deployed_usd` $5; campaign worst-case budget $5; account headroom $8.3787 |
| Instantaneous aggregate exposure | $5.00 | approval ceiling $20 → intersected to $5 |
| Concurrent maker orders | **2** | `max_live_orders` 1, **+1** for the paired opposite leg only |
| Concurrent markets | **1** | `max_concurrent_markets` 1 |
| Order size | **≤ 3 contracts per leg** | `opening_leg_max_contracts` 3, `round_allocations` 1 |
| Execution | **maker-only, post-only both legs** | `MODE = FT.MAKER`; no action name expresses crossing |
| Pair cost cap | **≤ $0.96 per pair** | `MAX_PAIR_COST`; a pair redeems $1.00 |
| First-leg own-price floor | $0.20 | `min_first_leg_own_price` |
| Windows | **≤ 8** | `MAX_WINDOWS` 8 |
| Duration | **≤ 4 hours** | `MAX_CAMPAIGN_S` 14400 |
| Series | `cpc-btc-updown-15m-` only | `ALLOWED_SERIES` allowlist |
| **Daily realized loss halt** | **$5.00** | `MAX_DAILY_LOSS_USD`, on `realized_current_utc_day()` |
| **Cumulative account-wide** | **$20.00** (−$11.6213 used, $8.3787 left) | `max_cumulative_loss_usd` |
| **Campaign worst-case budget** | **$5.00** | `DEFAULT_BUDGET_USD`, re-scoped this session |
| Residual cap | 3 contracts | absolute cap; `which_binds: ABSOLUTE` |

**Worst case, stated plainly:** every gate prices a resting order as though
it fills in full, and an unsettled position as though it settles worthless.
The campaign budget is `realized losses + cash in unsettled positions +
outstanding order notional` and is bounded at **$5.00**, revisable only
downward by settlement. So the maximum this campaign can lose is $5.00, which
leaves $3.3787 of account-wide headroom untouched.

### Automatic stop conditions

1. **Watchdog HALT latch** — durable, over HTTP; the executor cannot clear it.
2. Daily realized loss ≤ −$5.00 → `DAILY_REALIZED_LOSS_HALT`.
3. Campaign worst-case budget exhausted → refusal from `l5_campaign_budget`.
4. Account-wide cumulative −$20.00 → refuses first, every time.
5. Residual ≥ 3 contracts → `residual_cap` refusal.
6. Authorization expiry (`L5_LIVE_AUTH_NOT_AFTER`) → `AUTHORIZATION_EXPIRED`.
7. Window budget spent (8) → no further windows reserved.
8. **Timing floors, per window** (production-verified at 21:12:05Z): entry
   cutoff 600 s after start, complete cutoff 720 s, **cancel margin 300 s**
   before close, order TTL 120 s, minimum entry runway 300 s. Defensive
   terminal 600 s, guard opens 540 s, last actionable 585 s. Entering late is
   the dangerous side, so `entry` and `complete` are raise-only in the
   *earlier* direction — neither knob can extend trading.
   *(Corrected: I first read 180 s for the cancel margin from the module
   default locally. Production is 300 s.)*
9. Unresolved accounting → blocks live authorization outright.
10. No automatic relaunch after a completed or crashed money-moving session.

### Configuration changes this campaign requires

Approving the mandate approves these three. None widens a dollar bound.

| change | from | to | why |
|---|---|---|---|
| `L5_TS_TWO_ORDER_PAIR` | unset (**off**) | `1` | **Without this the campaign is not two-sided.** Both legs rest simultaneously only when the second maker slot is enabled; the slot is still granted only to the opposite leg of the same round in the same market. |
| `L5_TS_MARKET` / experiment id | experiment present, market pattern required | the one target market | `TS.active()` is false until both an experiment id and a matching market pattern exist — there is no partial arming. |
| `submission_budget_open` | **32** | **16** | Restores the approved OPEN rate. A tightening. |

`L5_TS_OPENING_LEG_MAX_CONTRACTS` 3, `L5_TS_ROUND_ALLOCATIONS` 1,
`L5_TS_MIN_FIRST_LEG_PRICE` 0.20, `L5_TS_ORDER_TTL_S` 120 and the cutoffs are
left at the values §3 states. I added all of these to the in-container
verifier this session, so the next boot emission reports each as the running
process actually has it rather than as a module default — the figures in §3
should be re-read against that line before arming.

### Reconciliation during the campaign

Venue-authoritative reconcile on every uncertainty and periodically;
exact-order cancellation by venue id; an order absent from the venue's open
orders is treated as terminal but **not released until an authoritative
`/v1/order` read books its final `cumQuantity`**, because releasing it
unbooked is how 2.25 DOWN shares once arrived with no cost basis.

---

## 3b. Effective runtime configuration, read inside the container

`L5_EFFECTIVE_RUNTIME_CONFIG` at **2026-10-24T21:12:05Z** — 23 graded rows,
each the value the money path's own accessor returns, not the text of a
variable. **`PASS: false`, 2 failed.**

| row | effective | expected | verdict |
|---|---|---|---|
| `aggregate_exposure_usd` | **$5** | $20 | FAIL — **not a risk** |
| `submission_budget_open` | **32** | 16 | **FAIL — real** |
| `daily_realized_loss_halt_usd` | $5 | $5 | ok |
| `max_live_orders` | 1 | 1 | ok |
| `maker_pair_ceiling` | 0.96 | 0.96 | ok |
| `max_windows_authorization` | 8 | 8 | ok |
| `campaign_duration_ceiling_s` | 14400 | 14400 | ok |
| `series_allowlist` | `cpc-btc-updown-15m-` | same | ok |
| `maker_only_forbids_defensive_crossing` | true | true | ok |
| `mandate_signing_key_absent_from_runtime` | **true** | true | ok |

- **`aggregate_exposure_usd` $5 vs $20 is the restored envelope, not a
  breach.** The runtime is *tighter* than the figure the verifier carries;
  the verifier's expected value is stale against your $5 restoration. No
  action needed, and it is the number this mandate proposes.
- **`submission_budget_open` 32 vs 16 is real.** The OPEN submission class is
  running at twice its approved daily rate. It is a *rate* ceiling, not a
  capital one, so no dollar bound is breached — the $5 cash gate, the $5
  campaign budget and the $5 daily halt all still bind. But it is looser than
  approved and should be corrected to 16 before or with this campaign. **I
  have not changed it**: it is an approved figure, so moving it is yours.

Observed, not graded:

| row | effective |
|---|---|
| `shadow_only` | **false** |
| `window_strategy` | **TWO_SIDED** |
| `experiment_id` | `l5-1w-20261022T191823Z` |
| `mandate_trusted_key_ids` | **(none)** |
| `mandate_verdict` | **`MANDATE_NO_TRUSTED_KEY`** |
| `mandate_enforcing` | **false** |

The last three confirm by observation what §6 establishes by construction.

---

## 4. What I changed to make this mandate meaningful

Three prerequisites, all committed, none of which arms anything:

1. **The effective-config verifier now runs in-container.**
   `l5_runtime_config_verify` was written to be run inside the deployed
   container — "the only configuration that matters is the one the running
   process actually has" — and nothing ever ran it there. It now emits
   `L5_EFFECTIVE_RUNTIME_CONFIG` once at boot: 23 rows, 4,348 chars. Without
   it the only way to establish the live limits was reading secret-bearing
   variables.
2. **`src/l5_mandate_signature.py` is now in the image.** The verifier
   imports it lazily, so without the COPY the boot check would have reported
   its own `ImportError` in place of the configuration. Verification only —
   `MANDATE_PUBKEYS` ships empty.
3. **The probe's own scope is now readable in-container** — order size,
   round allocations, the price floor, the TTL, the three timing cutoffs and
   `two_order_pair_enabled`, the switch that decides whether both legs may
   rest at once. None was visible before, so a campaign's stated size could
   only be asserted from module defaults.
4. **The campaign budget was stale by $2.72.** It was $11.10, computed as the
   unspent part of the $20 account-wide gate when all-time realized was
   −$8.8988. It is now −$11.6213, leaving $8.3787 — so $11.10 *exceeded* the
   headroom and had become decoration, the account-wide gate refusing first
   every time. Now $5.00, pinned by a test to the proposed envelope rather
   than to a remembered balance.

---

## 5. What I am *not* proposing

- No increase to any limit. Every figure above is the intersection of what is
  proposed and what the code already permits.
- No reuse of the expired mandate. The prior authorization is `EXPIRED`
  (−9594.9 s at boot) and the prior campaign approval is `approval_stale`.
  Neither is revived.
- No new service, no new branch, no new infrastructure.
- No shadow campaign.

---

## 6. BLOCKING: the approval mechanism cannot authenticate you

I need to be direct about this, because it decides whether your approval is a
control or a formality.

The only mechanism that can authorize a live campaign today is
`l5_campaign_approval`, which reads the approval document from a **Railway
variable**. Its own docstring claims "Claude cannot write it, so Claude cannot
approve a campaign." **That claim is false, and the codebase says so.**
`src/l5_mandate_signature.py` records the correction verbatim:

> *"That is false in practice: anyone holding a Railway token can set the
> variable, and in session 012Mo8AP three were set that way."*

`012Mo8AP` is **this session**. I hold Railway `set-variables`. So if I write
that document, the system cannot distinguish your approval from my own — and
a $5 live money campaign would be running on an authorization I minted.

**I will not write that variable.** Not because it would fail, but because it
would succeed.

The control designed to fix exactly this is the Ed25519 signed mandate, where
the private half never leaves your machine and writing the bytes is
insufficient. **It is not in force:**

- `MANDATE_PUBKEYS` has **0 entries** — the shipped state, which refuses
  everything;
- `l5_mandate_signature` has **0 references** in `l5_live_authorization.py`,
  `l5_live_execution.py` and `l5_pmus_live_runner.py` — it gates nothing;
- `docs/OWNER_MANDATE_SIGNING.md` Step 5 says so outright: *"install (phase 5
  only, not yet) ... nothing in the arming path consults it."*

### Your two options

**Option A — you write the approval document yourself.** Fastest. Publish
`campaign_approval.json` with the §3 figures; it goes stale after 3600 s, so
it must be fresh at arming. This still does not *authenticate* you — it only
means I did not write it. If you choose this, say so explicitly and I will
proceed without touching the variable.

The document needs exactly these fields (`REQUIRED_FIELDS`), money as
**strings**, timestamps with an **explicit offset**:

```json
{
  "approved": true,
  "nonce": "<fresh unique string>",
  "series": "cpc-btc-updown-15m-",
  "issued_utc": "<now, offset-aware>",
  "expires_utc": "<now + up to 4h, offset-aware>",
  "max_windows": 8,
  "max_total_exposure_usd": "5",
  "max_daily_loss_usd": "5"
}
```

Code constants cap every field, so a document asking for more yields less.

**Option B — wire the signed mandate into the arming path first.**
Recommended. You generate the keypair offline per the existing procedure,
give me the 64-hex **public** half, I commit it to `MANDATE_PUBKEYS` and wire
verification into `authorize_arming` as a refusal. Then your signature is
required and mine is impossible. This is one bounded change to code that
already exists and is already tested; it is the difference between an
approval I could forge and one I could not.

I recommend B, and I recognise it costs a round trip before any real money
moves. For a $5 test the delay is cheap; the precedent is not.

---

## Accounting labels

Nothing in this document is a profit claim. The only venue-confirmed realized
result in this program remains **Stage 1: −$3.7194 over 8 windows**, six
confirmed fills. Every figure in §3 is a *limit*, not an expectation. On
present shadow evidence the one-sided policy's modelled edge is **negative**
(−$0.0827 per filled contract, 91.6% adverse over 972 modelled fills), and
the two-sided ladder has **no** economic result yet — one common settled
window that traded nothing.

**This campaign is a bounded test of whether two-sided quoting changes that,
not a bet that it does.**
