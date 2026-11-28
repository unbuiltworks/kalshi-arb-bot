# Single-window live calibration envelope — owner-authorized 2026-10-19

Authorized scope (owner, verbatim): ONE live PMUS BTC15m calibration window;
≤$5 committed exposure including outstanding orders; one BTC15m market; one
live order at a time; $5 daily realized-loss halt; existing L5-following
claim-gated execution path only; no automatic repeat or expansion to other
coins.

**Nothing in this document has been applied. No variable was changed, nothing
is armed, SHADOW_ONLY_LOCK remains engaged.**

---

## 1. Confirmations (read-only, 2026-10-19 21:29Z–22:27Z)

| check | result | evidence |
|---|---|---|
| **current deployment** | `104f1959`, SUCCESS 21:29:09Z, commit **`778222e0`** — the recorder-fixed tree | Railway + `L5_LIVE_START` |
| **loss gates** | `daily_realized_loss_halt_usd: 5`; additive refuse-only gates `max_cumulative_loss_usd: 20`, `max_cash_deployed_usd: 10`, `max_unmatched_contracts: 5` | `L5_LIVE_START`, `L5_LIVE_GATES` |
| **exposure caps** | `max_usd_per_market: 5`, `max_total_exposure_usd: 5`, `max_concurrent_markets: 1`, `max_live_orders: 1`, `worst_case_usd: 5` | `L5_LIVE_START` |
| **account-wide exposure now** | `account_total_worst_case_usd: 0`, capacity $5, 0 live, 0 parked, `quarantined: []`, `cash_deployed: []` | heartbeat 21:33:59Z |
| **available balance** | **$344.40605**, `read_ok: true`, from `/v1/account/balances` | `L5_LIVE_CASH` 21:46:19Z |
| **authorization expiry** | **EXPIRED**, `expires_in_s: -28807.8`, lapsed ≈ 13:29:00Z → `authorize_arming` step 8 refuses `AUTHORIZATION_EXPIRED` | `L5_LIVE_START` |
| **arm token** | `owner_arm_token: false`; money path reaches `ORDER:NOT_ARMED` | `L5_LIVE_START`, `L5_LIVE_DECISION` 21:46:16Z |
| **exact-order cancellation** | watchdog live, sweeping 20s, `cancel_enabled: true`, `open_orders: 0`, `halt: false`, nothing unverified, no read error | `L5_WATCHDOG_SWEEP` 22:25:13Z |
| **durable reconciliation** | `auth_ledger.durable: true`; holder identity all five true (boot_id, deployment_id, pid, replica_id, service_role) | `L5_LIVE_START` |
| **mechanism mode OFF** | the L5 trading tick is running (`L5_LIVE_DECISION`, shadow controllers), so `L5_CANCEL_MECHANISM` is not YES — it would REPLACE the trading tick | observed behaviour |
| **recorder** | 84 orders folded + appending, `errors: {}`, capturing at the wire on the L5-following path | heartbeat, `L5_FILL_CALIB_PRESUBMIT` |

### Cancellation chain, three layers

1. **Claim expiry** — `maybe_expire_authorization`: the claim expires at
   `min(TTL, window_end − close_margin)`, and at that instant any resting
   order is cancelled **by venue id and proved terminal by re-reading it**.
   `still_open`/`unverified` mark the store UNCERTAIN, which `authorize_arming`
   then refuses on, rather than being reported as success.
2. **Window end** — `close_window`: a final progress poll first (so a fill
   between the last poll and close is recorded as a fill, not lost into a
   cancel), then `deadman(reason="window_end")`, then disarm.
3. **Independent watchdog** — separate service, cancel-by-id only, no submit
   path, cannot clear a halt. `max_order_age_s: 300.0`.

## 2. The timing constraint this envelope exists to respect

The watchdog treats an order older than **300s** as overdue, and it
**asserts HALT before it cancels**. On the L5-following path the primary
deadline is claim expiry at `window_end − 60s` (T+14:00), so an order placed
early in a window could rest ~840s — four times the watchdog's limit. That
would end the probe in an `UNMANAGED_RESTING_ORDER` latch needing an owner
reset.

The fix is **not** to widen the watchdog (its margin can be widened, never
narrowed, and weakening it is out of scope). It is to **arm late**: lift the
lock at **T+11:00**, so claim expiry at T+14:00 bounds any order's rest at
**≤180s**, leaving 120s of margin under the watchdog. Arming after T+14:00 is
refused anyway (`WINDOW_CLOSING`, 60s pre-close margin).

## 3. Candidate windows (pick one)

| slug | window | owner sets by | arm band | `L5_LIVE_AUTH_NOT_AFTER` |
|---|---|---|---|---|
| `cpc-btc-updown-15m-2026-10-19-2300z` | 23:00:00Z–23:15:00Z | 22:55:00Z | 23:11:00Z–23:14:00Z | `2026-10-19T23:14:00Z` |
| `cpc-btc-updown-15m-2026-10-19-2315z` | 23:15:00Z–23:30:00Z | 23:10:00Z | 23:26:00Z–23:29:00Z | `2026-10-19T23:29:00Z` |

`NOT_AFTER` set to the window's T+14:00 makes the envelope **structurally
single-window**: no claim can be made after that instant, so a second window
cannot arm even if every other variable were left in place.

## 4. Owner steps — Railway dashboard

Service **`l5-pmus-live`**, environment **production**, Variables tab. Set
these **three together** and deploy once:

```
L5_LIVE_AUTH_NOT_AFTER = 2026-10-19T23:14:00Z     (or the 2315z value)
PMUS_Q1_SESSION_ID     = <a fresh opaque id you choose>
PMUS_Q1_ARM            = <the SAME value>
PMUS_Q1_LIVE           = YES
```

`armed()` requires `PMUS_Q1_LIVE == "YES"` AND a non-empty
`PMUS_Q1_SESSION_ID` AND `PMUS_Q1_ARM` **exactly equal** to it. Do not share
the value with me; I will verify only the boolean `owner_arm_token: true` in
the boot diagnostic.

Equivalent CLI, from a linked checkout:

```
railway variables --service l5-pmus-live --environment production \
  --set 'L5_LIVE_AUTH_NOT_AFTER=2026-10-19T23:14:00Z' \
  --set 'PMUS_Q1_SESSION_ID=<fresh id>' \
  --set 'PMUS_Q1_ARM=<same fresh id>' \
  --set 'PMUS_Q1_LIVE=YES'
```

**This handshake alone cannot place an order.** `SHADOW_ONLY_LOCK` is step 1
of the gate chain and `transport.submit()` raises `NotArmed` while it is
engaged, whatever the arm state. That is the point of the staging.

## 5. What I do after the handshake, in order

1. Verify from `L5_LIVE_START` on the new deployment: `owner_arm_token: true`
   and `auth_not_after.state: "OPEN"` with a positive `expires_in_s`.
2. Re-verify every row of §1: limits unchanged, exposure $0, balance read_ok,
   watchdog sweeping with `cancel_enabled: true` and `open_orders: 0`,
   reconciliation not UNCERTAIN, no HALT latch.
3. Set, in ONE change: `L5_LIVE_PROBE_MARKET` and `PMUS_Q1_GATE_EVIDENCE` to
   the chosen slug (`gate_asserted` requires exact equality), a fresh
   `L5_EXPERIMENT_ID`, and **`L5_SHADOW_ONLY=0` last**, timed so the restart
   completes by T+10:30.
4. Watch the window. If no qualifying L5 action occurs, **no order is placed
   and nothing is forced.**
5. At claim expiry (T+14:00) cancel any outstanding order **by exact venue
   id**, with terminal state proved by re-read.
6. `L5_SHADOW_ONLY=1` immediately after the window, before any next window
   can arm.
7. Continue settlement monitoring until final cashflows are known, then
   report: orders, fills, partial fills, prices, unmatched inventory, fees,
   credited rebates, and realized net P&L — settlement-only labelling, with
   rebates as a separate credited line, never netted into trading P&L.

## 6. Open item for the owner

`L5_HALT_RESET_AT_BOOT` is still **present** on the `l5-halt-watchdog`
service (value not readable by me). It was set to YES for the single
Authorization-A deployment on 2026-10-19T02:06Z. If it is still YES, a
watchdog restart would attempt the gated HALT clear at boot. The reset id is
single-use and was spent, so the gates should refuse — but during a live
window I would rather it read `NO`. Your call; I have not touched the
watchdog's configuration.
