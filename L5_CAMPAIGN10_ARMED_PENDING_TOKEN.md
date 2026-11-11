# Campaign 10 — configured and verified; waiting on the owner arm token

Everything within my control is applied and verified from the running
service. **The service is still disarmed, by design: the one remaining
check is the owner arm token, which I have deliberately not supplied.**

## Deployed SHA

| | |
|---|---|
| Running SHA | **`4f45a73cb19e4059da58fdbf7f77b9e2be525c67`** |
| Deployment | `1bf34e36-fe2e-4718-a1a7-ebff1ab65f89` — SUCCESS, boot 01:31:50Z |
| Previously verified | `ac27483f` |

The variable change triggered a rebuild from the branch tip rather than the
pin, so the SHA moved. **Production content is byte-identical:**
`ac27483f..4f45a73c` touches four files — three documents and one test file
— and **zero** files in the Dockerfile COPY list. All five production
modules verified identical hash-for-hash.

## Both preflights

**`L5_AUTHORIZATION_PREFLIGHT` — one failure, and it is the owner's:**

```
ok: false    fail: ["owner_arm_token"]          <-- the only blocker
  owner_arm_token     FAIL   OWNER_AUTHORIZATION_ABSENT (step 11)
  absolute_deadline   PASS   OPEN, expires_in_s 7689.5
  campaign_approval   PASS   approved, max_windows 3,
                             exposure "20", daily_loss "5",
                             document_sha256 244e303f...
  window_budget_env   PASS   max_windows_env 3
  residual_ceiling    PASS   effective_cap_contracts 3, ABSOLUTE
  shadow_lock, claim_ttl_s, market_series, role_binding,
  session_id, halt_gate                            all PASS
  reserved_nothing: true
```

The `document_sha256` matches the document I graded locally before
installing — the service is grading exactly what I verified.

**`L5_LIVE_PREFLIGHT` (market/venue):** the websocket book is fresh and
two-sided throughout — `bid 0.2600 / ask 0.2700`, `stream_age_s` 0.015 →
0.032, `book_updates` 200 → 334, `MARKET_STATE_OPEN`. The REST route blocks
(`first_blocking: book_readable`) as it has all session; the authenticated
socket is the book the controller reads.

## Effective configuration — `PASS: true, checked: 26, failed: []`

```
submission_budget_open            12  = 12    ok
submission_budget_complete        12  = 12    ok
submission_budget_replace          6  =  6    ok
submission_backstop               27  = 27    ok
aggregate_exposure_usd            20  = 20    ok
aggregate_exposure_in_force_usd   20  = 20    ok
daily_realized_loss_halt_usd       5  =  5    ok
max_windows_authorization          3  =  3    ok
max_live_orders                    1  =  1    ok
maker_pair_ceiling              0.96  = 0.96  ok
campaign_additional_loss_usd       5  = 5.00  ok
maker_only_forbids_defensive_crossing  true   ok
mandate_signing_key_absent_from_runtime true ok
... 26 of 26 rows ok

observed: controller_open_enabled      TRUE   <-- autonomous openings ON
          defensive_execution_enabled  false  <-- crossing OFF
          ts_two_order_pair_enabled    false  <-- one resting order
          max_worst_case_deficit_usd   3.00
          first-leg band 0.20-0.60, 3 contracts, notional 3.00
          entry/complete cutoffs 600/720s, ttl 120s, cancel margin 300s
```

The experiment id is now
`l5-btc15m-prog24-c1-3bf1daa5.camp.5c1c9d675840e9cf8de2fa6e` — the campaign
suffix changed, so this campaign has a **fresh claim namespace** and no
prior claim carries into it.

## The $9.00 envelope, and nothing invalidates it

Verified on this boot:

| Precondition | Observed |
|---|---|
| no outstanding order at the venue | `open_orders_at_venue: 0` |
| no venue position | `venue_net: {}` |
| ledger owns no live order | `live_orders: 0` |
| no unknown order holding envelope | `orders_uncertain` absent |
| nothing adopted or pending-terminal | all empty, `pending_terminal_total: 0` |
| reconciled on boot | `reconciled: true` |
| per-market deficit cap | `max_worst_case_deficit_usd: 3.00` |
| window budget | 3, enforced in both places |

So each of the three markets starts from an empty book, and the envelope is
`3 x $3.00 = $9.00` exactly as proven.

The only ledger drift is two **long-settled** markets, `2026-10-12-0030z`
(ours 2, venue 0) and `2026-10-14-2230z` (ours 2.9, venue 0), resolved
toward the venue. Neither is an open obligation and neither consumes
envelope.

**Watchdog:** `L5_LIVE_HB` emitting from 01:31:54Z; `maybe_halt_deadman`,
`maybe_cancel_orphans` and `maybe_expire_authorization` dispatched every
tick. The independent `l5-halt-watchdog` service is running and untouched.

## What is NOT yet captured, and why that is correct

The **campaign baseline** has not been written. That is by design, not a
gap: `capture_campaign_baseline` runs *immediately after the first arm
succeeds*, idempotently — "the first window of a campaign writes the
baseline and every later one reads it back unchanged." Until then, with the
bound configured and no baseline, `ValidationGates` refuses every order with
`CAMPAIGN_BASELINE_ABSENT`. So the $5 campaign gate becomes live at the same
instant trading does.

## What I changed, and what I did not

Applied (eight variables, every one a reduction or a switch):
`L5_CAMPAIGN_APPROVAL` (fresh document, installed from a file so its nonce
never passed through a tool argument), `L5_LIVE_AUTH_NOT_AFTER`
= `2026-10-30T03:40:00Z`, `MAX_WINDOWS` 3, `MAX_OPEN` 12,
`MAX_COMPLETE` 12, `MAX_REPLACE` 6, `MAX_SUBMISSIONS` 27,
`L5_CONTROLLER_OPEN=YES`.

**Not touched:** `PMUS_Q1_LIVE`, `PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM`, the
API credentials, every risk limit, every other service. No new service, no
signing system, no taker execution, no limit increased.

## The arm token — your action

`armed()` requires all three, and I am not supplying them:

```
PMUS_Q1_LIVE        = YES
PMUS_Q1_SESSION_ID  = <a fresh value you choose>
PMUS_Q1_ARM         = <the same value>
```

Set them on the **`l5-pmus-live`** service in the Railway dashboard
(project `arb-ev-wallet-shadow`, environment `production`, service
`l5-pmus-live`, Variables). **Do not paste the value into this chat** — I
do not need to know it, and the design is that arming is a separate owner
act. The service's own preflight says so: "arming is a separate owner act;
preflight passing does NOT arm."

Setting a variable restarts the service (~2 minutes), after which the next
window's `try_arm` will succeed on its own and the campaign runs.

## Windows

The three windows are whichever three consecutive windows follow arming
within `NOT_AFTER` — the authorization has 126 minutes of runway, and the
budget of 3 is what caps it, not the clock.

- Armed before **01:35Z** → `0130z`, `0145z`, `0200z`
- Armed in the 0145z window → `0145z`, `0200z`, `0215z`
- and so on

The current window `0130z` has entry runway until 01:40:00Z (entry cutoff
T+600 s, minimum runway 300 s).

## Then

I report per window: submitted orders, venue-confirmed fills, completed
pairs, residual inventory, unsettled exposure — and after settlement, the
confirmed **settled** realized net P&L for the campaign. Automatic halts are
honoured throughout; if any safety check fails the system stays disarmed and
I report the precise blocker.

A note on expectation, unchanged: ARGMAX is −$3.14 over 38 windows. Expect a
small loss. The result being bought is whether an autonomous maker opening
reaches PMUS, fills, pairs and settles at all.

---

## Addendum 01:39Z — approval validity and the window-claim question

### The approval is still valid. No fresh one is needed.

```
now                 2026-10-30T01:38:58Z
NOT_AFTER  03:40:00Z   +121.0 min
expires    03:45:00Z   +126.0 min
approval age  9.0 min  (staleness bound 240 min)
VALID: True
```

**Six eligible future windows** for a budget of three — a window is eligible
when its entry is still reachable (300 s runway before the T+600 s entry
cutoff) and it closes before `NOT_AFTER`:

```
0145z  entry open until 01:50:00Z
0200z  entry open until 02:05:00Z
0215z  entry open until 02:20:00Z
0230z  entry open until 02:35:00Z
0245z  entry open until 02:50:00Z
0300z  entry open until 03:05:00Z
```

`0130z` is no longer eligible — its entry floor passed at 01:35Z.

### "Armed indefinitely" is structurally impossible

Three independent per-tick disarm paths, all verified in code:

1. **`close_window`** — at every window end: a final order-progress read,
   then `deadman(reason="window_end")` cancelling every resting order by
   venue id, then `t.disarm("window_end")`. Arming is strictly per-window.
2. **`maybe_expire_authorization`** — every tick: if the claim is no longer
   valid, `deadman(reason="authorization_expired:…")` then
   `t.disarm("authorization_expired")`. "A CLAIM THAT HAS EXPIRED MUST ALSO
   CANCEL, not merely stop arming." The claim's expiry is the **earlier** of
   its TTL and the venue close less the margin.
3. **`maybe_halt_deadman`** — the independent halt latch, written by a
   process we do not control and surviving our restart.

### Expired windows are skipped without cost

`campaign_window_is_usable` is checked **twice** — once before
`authorize_arming` and again after it returns a claim. Both refusals spend
no window: *"refused BEFORE the single-use claim was taken: no campaign
window is spent and the authorization is still available to a later
window"*, and post-claim, *"the window crossed the entry floor between the
pre-claim check and the claim. No campaign window is spent."* So the budget
rolls forward to the next eligible window rather than being burned.

### But the budget counts ARMED windows, not TRADED ones

Worth stating plainly before arming. In `try_arm` the order is:

```
pre-claim usability  ->  authorize_arming  ->  post-claim usability
  ->  reserve_window  (THE WINDOW IS SPENT HERE)
  ->  arm  ->  capture baseline  ->  L5_LIVE_ARMED
```

`reserve_window` is deliberately last — the commit record shows two windows
lost on 2026-10-20 to reserving too early, one to a shadow-locked
verification run and one to an arm that `MANDATE_SCOPE_CHANGED` then
refused, "neither of which could ever have traded". Moving the spend after
the claim is as far as it can go.

So a window with adequate runway that arms and then simply never sees an
admissible pair — the first-leg band is 0.20–0.60 and the pair ceiling 0.96
— **is consumed with zero orders**. It is idempotent per slug, so a restart
inside an already-reserved window resumes it rather than spending another.

Consequence for this campaign: three armed windows are guaranteed, three
*traded* windows are not. With six eligible and a budget of three there is
no way to widen that without raising the budget, which is not authorized and
I am not proposing it. If the three windows arm and produce no order, the
honest result is zero fills and the campaign ends having spent its budget —
and that is itself a finding about the opening policy, not a failure of the
machinery.

### No strategy logic changed, and none will be

Per the owner's recommendation: complete the authorized experiment,
reconcile every fill and settlement, then decide.
