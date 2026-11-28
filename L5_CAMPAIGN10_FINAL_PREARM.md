# Campaign 10 — final pre-arm verification

Running SHA **`ac27483f`** (deployment `2437e556`, SUCCESS). **Disarmed.
Nothing armed, no variable changed, no order submitted.**

---

## 1. The verified loss envelope

### I am withdrawing the $8 figure

$8.00 was my arithmetic on the halt, not a control. **No control enforces
it.** The correct hard maximum is **$9.00**, and the reason is a gap worth
stating plainly before you arm.

### How the two loss gates actually compose

Read from the code, not inferred:

- **`max_worst_case_deficit_usd = $3.00`** — prospective, **per market**. At
  every submission, `C − min(up, dn)` is maximised over which outstanding
  orders fill, and the order is refused if the resulting book could lose more
  than $3.00 on *either* settlement outcome. The book's worst case can only
  change when fills happen, and those fills were already in the
  maximisation — so **$3.00 per market is genuinely enforced**, for the whole
  window, across every accumulation round.

- **`max_campaign_additional_loss_usd = $5.00`** — `max(0, realized_now −
  realized_baseline) + open_worst_case`, refused at `>=`. It is configured
  (`CBL.limit_from_env()` → 5) and its `campaign_id` is **the approval's own
  `nonce_sid`**, which is why the running service reports `campaign_id:
  null` — no approval, so the gate currently fails closed on
  `CAMPAIGN_BASELINE_NO_CAMPAIGN_ID`.

### Measured, with the real gate function

```
one maximal opening leg (3 @ 0.60, flat):   prospective deficit  $1.87
a second maximal leg on top:                              $3.67  REFUSED
completed pair, 3 UP @0.48 + 3 DOWN @0.48:  deficit      -$0.05
```

The $3.00 cap binds after a single maximal leg. A completed pair has a
**negative** deficit — it pays on both outcomes — which is why the loss
ceiling never refuses the completions that reduce risk.

### The gap: why I cannot promise under $5

Both $5 controls read **settled** P&L:

- `realized_today()` = `realized_attributed + realized_unattributed` —
  settlement results. An unsettled position is not in it.
- the daily halt reads `realized_current_utc_day()`, also settled, and fires
  at `day <= -5`.
- the campaign gate's `open_worst_case` is computed from **this market's**
  inventory only.

BTC 15-minute windows are back-to-back: `0130z` closes exactly as `0145z`
opens. So window 1's loss will **not** have settled and booked before window
2's first order is gated. In that instant it sits in neither term — not in
`realized`, not in `open_worst_case`. The $5 gates therefore tighten
progressively as settlements land, but **they do not bound three
back-to-back windows at $5.**

What *does* see unsettled prior windows is the **$12 cash principal gate**,
which counts filled notional as unreleased cash account-wide until a market
is retired with a final settlement price.

### Final envelope

| Bound | Amount | Enforced |
|---|---|---|
| Per window, all fill combinations | **$3.00** | **Yes** — prospective, every submission |
| One maximal opening leg | $1.87 | measured |
| **3 windows × per-window cap** | **$9.00** | **Yes** — the only bound independent of settlement timing |
| Campaign: realized + open worst case | <$5.00 | Partially — blind to an unsettled prior window |
| Daily realized-loss halt | $5.00 | Partially — reads settled day P&L |
| Cash principal, account-wide | $12.00 | Yes — sees unsettled markets |
| Total exposure / per market | $20.00 | Yes |
| Cumulative loss | $18.6325 | Yes |

> **Hard maximum campaign loss: $9.00.** Expected outcome materially
> better — one maximal strand is $1.87, pairs carry negative risk, and the
> $5 gates bind as soon as a settlement books. $9.00 is the figure to
> approve against.

Maker fees are **$0.0000** on PMUS (293 real maker fills); defensive
crossing is disabled, so no taker fee can arise. The deficit figures above
already carry a $0.07 fee allowance.

---

## 2. Controls verified

**352 tests pass** across the control suites: `deadman_exact_cancel`,
`orphan_cancellation`, `emergency_cancellation_proof`, `cancel_mechanism_leg`,
`mandate_watchdog_publish`, `one_submission_claim`, `campaign_claim_identity`,
`disarmed_claim_accounting`, `outside_claim_adjudication`,
`fill_reconciliation`, `canonical_terminal_reconciliation`,
`campaign_baseline`, `not_after_status`, `arming_boundary`,
`mandate_arming_gate`.

| Control | Verified |
|---|---|
| **Watchdog** | `L5_LIVE_HB` emitting from 01:02:08Z. `maybe_halt_deadman`, `maybe_cancel_orphans`, `maybe_expire_authorization` all dispatched every `tick`. |
| **Exact-order cancellation** | `deadman(store, transport, reason=, only=[coids])` cancels by **venue** id — not client id, which "would silently cancel nothing" — then **proves it by re-reading** the open orders. |
| **Authorization expiry** | `AUTH.authorize_arming` is called **per window** from `try_arm`, and step 26 re-reads `L5_LIVE_AUTH_NOT_AFTER` on each call. Expiry re-binds every window; it is not a one-time check. Currently EXPIRED by 9,709s. |
| **Restart reconciliation** | Verified live on this boot: `reconciled: true`, `open_orders_at_venue: 0`, nothing adopted, pending or released-unacked; ingest cursor `durable: true` with a `seeded_at` from a **previous** boot, which is the proof a restart resumed rather than discarding the feed page. |
| **Window single-use** | `campaign_window_is_usable` is checked **before** the single-use claim, so a refusal spends no window. |

---

## 3. The three windows

| | Market slug | Starts |
|---|---|---|
| 1 | `cpc-btc-updown-15m-2026-10-30-0130z` | 01:30Z |
| 2 | `cpc-btc-updown-15m-2026-10-30-0145z` | 01:45Z |
| 3 | `cpc-btc-updown-15m-2026-10-30-0200z` | 02:00Z |

Future, consecutive, none expired. Entry runway: `ts_min_entry_runway_s =
300`, entry cutoff `T+600`, so arming must land at least 5 minutes before a
window's cutoff.

**These slide if you approve after ~01:25Z.** The rule: *the first three
consecutive `cpc-btc-updown-15m-` windows beginning after arming and closing
before `L5_LIVE_AUTH_NOT_AFTER`.* On your go I name the actual three from
the clock and set `NOT_AFTER` to match — I will not reuse an expired window.

---

## 4. Fresh approval document

Graded through the real `CA.approval_gate()`: **`approved: true`, `why:
null`, `clamped: false` on every field.** Extra fields are carried and do
not disturb grading.

```json
{"approved":true,"nonce":"camp10-<16 hex chars YOU generate>","series":"cpc-btc-updown-15m-","issued_utc":"<install time, UTC>","expires_utc":"2026-10-30T02:30:00+00:00","max_windows":3,"max_total_exposure_usd":"20","max_daily_loss_usd":"5","max_open_submissions":12,"max_complete_submissions":12,"max_replace_submissions":6,"max_total_submissions":27,"execution_mode":"MAKER_ONLY","defensive_crossing":false,"autonomous_maker_openings":true,"controller_open_enabled":true,"controller_provenance":"CONTROLLER_GENERATED","windows":["cpc-btc-updown-15m-2026-10-30-0130z","cpc-btc-updown-15m-2026-10-30-0145z","cpc-btc-updown-15m-2026-10-30-0200z"]}
```

**Generate the nonce yourself.** It is claimed single-use before anything
arms, so a nonce I publish in a chat log is a nonce that has been written
down.

### The scope fields are a RECORD, not an enforcement — be clear on this

`autonomous_maker_openings`, `controller_open_enabled`, `execution_mode` and
`defensive_crossing` are **not** in `CA.REQUIRED_FIELDS`. The gate ignores
them. They exist so the authorization you sign states on its face what it
permits, and so the document and the runtime can be compared after the fact.

What actually enforces each one:

| Recorded in the document | Enforced by |
|---|---|
| `autonomous_maker_openings: true` | `L5_CONTROLLER_OPEN=YES` **and** `TS.active(market)` |
| `controller_open_enabled: true` | the same env var; reported at boot as `controller_open_enabled` |
| `execution_mode: MAKER_ONLY` | `AUTH.EXECUTION_MODE`, a **code constant** |
| `defensive_crossing: false` | `DEF.enabled()` false + the graded `maker_only_forbids_defensive_crossing` row |
| `max_windows: 3` | the approval gate **and** `L5_LIVE_AUTH_MAX_WINDOWS=3` |
| the submission budgets | the durable claim ledger |

So: **approving this document is approving autonomous maker-only openings**,
and the env var is what makes it so. Deliberate, not inferred.

---

## 5. Effective configuration

### Variables to set — eight, every one a reduction or a switch

| Variable | Now | Set to |
|---|---|---|
| `L5_CAMPAIGN_APPROVAL` | stale | the document above |
| `L5_LIVE_AUTH_NOT_AFTER` | EXPIRED (−9709s) | `2026-10-30T02:25:00Z` |
| `L5_LIVE_AUTH_MAX_WINDOWS` | 8 | **3** |
| `L5_LIVE_AUTH_MAX_OPEN` | 24 | **12** |
| `L5_LIVE_AUTH_MAX_COMPLETE` | 32 | **12** |
| `L5_LIVE_AUTH_MAX_REPLACE` | 16 | **6** |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | 72 | **27** |
| `L5_CONTROLLER_OPEN` | unset | **`YES`** |

Then the arm token, yours alone: `PMUS_Q1_LIVE`, `PMUS_Q1_SESSION_ID`,
`PMUS_Q1_ARM` with `ARM == SESSION_ID`.

### Preserved unchanged — requirement 5

```
L5_MAX_TOTAL_EXPOSURE_USD          20       $20 maximum exposure
R.daily_realized_loss_halt()        5       $5 daily realized-loss halt
L5_MAX_WORST_CASE_DEFICIT_USD    3.00       $3 per-window worst-case deficit
L5_MAX_CASH_DEPLOYED_USD        12.00       cash principal ceiling
L5_MAX_LIVE_ORDERS                  1       one outstanding order
L5_MAX_UNMATCHED_CONTRACTS          5
TS.max_pair_cost()               0.96       maker pair ceiling
TS first-leg band          0.20 - 0.60
TS.opening_leg_max_contracts()      3
TS.opening_notional_budget_usd()  3.00
entry / complete cutoffs      600 / 720 s
order ttl / cancel margin     120 / 300 s
L5_DEFENSIVE_COMPLETION           off       defensive crossing disabled
```

**Verified:** with the document and these eight applied, against
production's own values for everything else —
`PASS: True, checked: 26, failed: []`. A measured result, not a prediction.

---

## 6. Duplicate and unauthorized submission prevention

| Mechanism | Evidence |
|---|---|
| Deterministic key | `CONTROLLER\|OPEN\|<market>\|r<n>\|<LEG>` → same `client_order_id` every tick. Ten ticks against one order slot produce **one** order. |
| Duplicate suppression | A ledger reporting the id as seen yields `ORDER.reason = DUPLICATE_SUPPRESSED`, `submitted: false`. |
| Round and leg in the key | A complement is never mistaken for a duplicate of its opening leg; distinct rounds get distinct ids. |
| No L5 collision | An L5 `action_key` is `txhash\|asset\|side\|price\|size\|timestamp` and cannot take the controller form. Both paths claim from the **same** accumulation state, so neither can re-place the other's claimed leg. |
| Per-order authorization | `claim_accumulation_leg(market, round, leg)` — `O_CREAT\|O_EXCL`, durable, taken **before** the wire, never returned by a cancel, partial, zero fill, restart, rejection or ambiguous ack. Make it refuse and **nothing reaches the wire**. |
| Provenance | `rec["provenance"] = CONTROLLER_GENERATED`, a `decision_source`, the `CONTROLLER_BOOK` it was gated against, and a `MAKER_TREATMENT` record that never writes `rested_at_l5_price` for an autonomous decision. |
| Fail closed | No pending leg → `CONTROLLER_INTENT:CONTROLLER_NO_PROBE_LEG`; the placeholder key is never submitted under. |

64 tests in `tests/test_l5_controller_open_trigger.py`, 51 parity tests.

---

## 7. Maker-only, crossing disabled

`maker_only_forbids_defensive_crossing: true, ok: true` ·
`defensive_execution_enabled: false` · `ts_two_order_pair_enabled: false` ·
`mandate_signing_key_absent_from_runtime: true`. `EXECUTION_MODE` is a code
constant and unchanged. Every order the autonomous path can produce is
post-only MAKER, asserted on the wire body (`participateDontInitiate: true`)
across both sides and multiple rounds.

---

## Authorization instructions

1. Generate a 16-hex-character nonce.
2. Set `L5_CAMPAIGN_APPROVAL` to the one-line document, with `issued_utc` at
   install time and your nonce.
3. Set `L5_LIVE_AUTH_NOT_AFTER=2026-10-30T02:25:00Z`.
4. Set the five budget variables: windows 3, open 12, complete 12,
   replace 6, submissions 27.
5. Set `L5_CONTROLLER_OPEN=YES`.
6. Set `PMUS_Q1_SESSION_ID` and `PMUS_Q1_ARM` to the same value, and
   `PMUS_Q1_LIVE=YES`.
7. Tell me to go. I verify `L5_AUTHORIZATION_PREFLIGHT ok: true` and
   `L5_EFFECTIVE_RUNTIME_CONFIG PASS: true`, confirm the three windows
   against the clock, then run the campaign without pauses and report
   venue-confirmed orders, fills, completed pairs, residual inventory,
   settlement and **settled** realized net P&L.

If you would rather I set the eight non-secret variables myself, say so and
I will — but the arm token and the nonce should be yours.

**Nothing arms until you explicitly approve.**

---

### Honest expectation

ARGMAX is **−$3.14** over 38 windows and every opening-price band measured
net-negative. The honest expectation for three windows is a small loss. What
makes it worth running is whether an autonomous opening reaches PMUS, fills,
pairs and **settles** through the real money path — none of which has ever
happened. Approve the **$9.00** envelope, not an expectancy claim.
