# One approval, many windows: the mandate workflow

**The smallest secure change is a configuration change. No new code, no new
service.** `l5_live_authorization` already implements a two-level mandate and
`authorize_arming` already uses it. 127 tests pass across
`test_l5_stage_b_mandate`, `test_live_mandate_deployment`,
`test_l5_window_bound_authorization` and `test_latch_readiness` on the
deployed commit `059cdc7d`. It has simply never been switched on.

## How it already works

**Level 1, the mandate claim** is keyed on the session id, exactly as a
single-window claim is, and carries the scope digest and holder identity. It
is taken once and never cleared, so one owner approval stays single-use:
"a second mandate needs a new session id."

**Level 2, the window claim** is keyed on mandate-plus-market through the
same `claim_once`, so the filesystem decides one winner per window and a
spent window stays spent forever. From the source: "The market ticker is the
window identity -- BTC15m tickers are unique per window -- so there is no
counter or clock to drift or replay."

And critically: "Nothing here relaxes a Stage A check: every one above has
already run for THIS window, including venue reconciliation, the halt gate,
uncertain orders and the capital envelope. A mandate only lets the next
window ASK."

| your requirement | already provided by |
|---|---|
| keep two slots, $5 caps, watchdog, exact-ID cancel, reconciliation | untouched; a mandate relaxes no Stage A check |
| auto-select next window, fresh bindings and deadlines | window identity IS the market ticker; claim expiry auto-derived as `min(now+TTL, window_end-60s)`; `L5_TS_MARKET` accepts a pattern; accumulation claims keyed per (market, round, leg) |
| no per-test editing of the three variables | one mandate sid covers `L5_LIVE_AUTH_MAX_WINDOWS` windows; `NOT_AFTER` set once to the campaign end |
| one authenticated, single-use, auditable approval | the dual-key handshake is the authentication; Level-1 claim is single-use; every claim is written to the durable ledger BEFORE the wire with scope digest and holder identity |
| never reuse consumed authorization, never auto-renew | `AUTHORIZATION_ALREADY_USED` on the mandate sid; window locks are permanent; `claim_accumulation_leg` "Never auto-resets"; an expired `NOT_AFTER` refuses, it does not renew |
| auto-restore shadow-only | `maybe_expire_authorization` cancels by venue id and DISARMS the transport; the HALT latch is the independent backstop |
| no redeploy per window | each window self-authorizes inside the mandate |
| reuse existing infrastructure | nothing new |

## What you do ONCE per campaign

Service `l5-pmus-live`, production. Five values, one deploy:

```
PMUS_Q1_LIVE              = YES
PMUS_Q1_SESSION_ID        = <one fresh campaign id>
PMUS_Q1_ARM               = <the same value>
L5_LIVE_AUTH_NOT_AFTER    = <campaign end, ISO-8601, e.g. +4h>
L5_LIVE_AUTH_MAX_WINDOWS  = <window budget, e.g. 8>
L5_LIVE_AUTH_MARKET_SERIES= cpc-btc-updown-15m-
```

And **clear** `L5_LIVE_PROBE_MARKET` (empty). An exact-slug binding refuses
every other market with `AUTHORIZATION_BOUND_TO_MARKET` and would defeat the
mandate.

That is the whole ceremony, once. Every window inside the budget and inside
the deadline then authorizes itself with its own permanent single-use claim.

## The four things to know before switching it on

1. **The budget and series are read from the ENVIRONMENT, not from any
   caller.** From the source: "a caller that could widen it by argument would
   be a second way to authorize windows, and the whole point of this module is
   that there is one." Changing either AFTER approval invalidates the mandate
   via the scope digest rather than extending it, so set both with the
   approval, not after.
2. **A new campaign still needs one Railway edit**, because the dual-key
   handshake IS the owner authentication. `pmus_q1_executor`'s header states
   the invariant: "Claude never arms this; the operator does." Removing that
   edit would mean I could arm, which is the one thing the control exists to
   prevent. Everything else is automated; this stays yours by design.
3. **Auto-restore is capability-level, not variable-level.** On expiry, a
   failed risk gate or completion, the transport is DISARMED and resting
   orders are cancelled by venue id -- so nothing can submit. The
   `L5_SHADOW_ONLY` variable itself stays as set until someone changes it. I
   will still flip it back to 1 at campaign end; the disarm is what makes the
   gap safe in the meantime.
4. **The budget is a ceiling on windows that may ASK, not a promise of
   trades.** Each window still has to pass every gate, and the $5 per-market,
   $5 account-wide and $5 daily-realized-loss limits are cumulative across
   the campaign, not per window.
