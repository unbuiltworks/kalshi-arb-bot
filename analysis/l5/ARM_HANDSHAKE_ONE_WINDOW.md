# One-window arm handshake, $5 mandate

Prepared 2026-10-20T11:13Z against running commit **`3a604e65`**
(deployment `e028ec26`, SUCCESS 06:04:40Z — still the latest, so the process
has been up ~5h). **Nothing is armed. No order has been submitted.**

## Preflight, all verified just now

| check | result |
|---|---|
| running commit | `3a604e65`, boot lines 06:04:39Z / 06:06:09Z |
| shadow lock | `L5_LIVE_SHADOW_LOCK {"locked":true,"blocked":["submit"]}` |
| account positions | **FLAT** -- `markets_counted: []`, `account_total_worst_case_usd: "0"`, `capacity_usd: "5"` |
| live / parked orders | `live_orders: 0`, `parked_orders: 0`, `pending_terminal: []`, `quarantined: []` |
| committed cash | `committed_usd: {}`, `cash_deployed: []` |
| unresolved accounting | `count: 0`, `blocks_live_authorization: false` |
| available cash | `$344.44015` from `/v1/account/balances` |
| risk gates | `max_usd_per_market 5`, `max_total_exposure_usd 5`, `daily_realized_loss_halt_usd 5`, `max_abs_residual 10`, `max_concurrent_markets 1`, `max_live_orders 1` |
| cash gate | `max_cash_deployed_usd 4.65`, `max_unmatched_contracts 5`, `NO_RECYCLING` |
| independent watchdog | sweeping every 20s, `cancel_enabled: true`, `max_order_age_s: 300`, `halt: false`, `open_orders: 0`, `read_error: null`, nothing overdue or unverified |
| venue health | 2,894 requests, `http_429: 0`, not in cooloff |
| accounting | account-wide `-$8.3672`; verified L5 `+$0.0276`; `-$84.62` unattributable PRESERVED as UNKNOWN, `identity_holds: false` as documented |
| B5 crossing | `L5_TS_COMPLETION_CROSS` **unset** -- disabled for this test, as instructed |

**No gate needs raising.** The $5 mandate fits the deployed envelope exactly;
this is why $5 was the right call over $7.

## L5_TS_DEPTH_TICKS: left at 2, and why

Your condition was "only if the pair-cost cap, post-only requirements and
exposure protections remain intact". They do -- `d=1` keeps post-only on both
legs, keeps every exposure gate, and the cap simply refuses more. But the
consequence defeats the window:

| spread | `d=1` | `d=2` |
|---|---|---|
| 1c | pair 0.97 -> **REFUSED `PAIR_COST_ABOVE_CAP`** | pair 0.95, quotes |
| 2c | pair 0.96, quotes (qty 3.29) | pair 0.94, quotes (qty 3.32) |
| 3c | pair 0.95, quotes | pair 0.93, quotes |

The 0215z shadow record's spread histogram is `{"0.01": 246, "0.02": 6}` --
**97.6% of two-sided ticks are one-cent**. At `d=1` the engine would refuse on
~98% of ticks and most likely submit nothing at all. So `d=2` stands for this
window. Say the word and I will set `d=1`, but it costs the measurement.

The better answer is adaptive: quote the SMALLEST offset whose pair still
clears 0.96 -- `d=1` on a 2c+ book, `d=2` on a 1c book. That is a one-function
change with its own tests, and it is the next batch, not this window.

## What YOU set, in one Railway change

```
PMUS_Q1_LIVE            YES
PMUS_Q1_SESSION_ID      <a fresh private id you choose>
PMUS_Q1_ARM             <the same value, byte for byte>
L5_LIVE_AUTH_NOT_AFTER  <from the table below, for the window you pick>
L5_TS_MARKET            <the EXACT slug from the table -- not a pattern>
L5_TS_EXPERIMENT_ID     tsw1-<the slug's HHMM>z-<any 6 random hex>
L5_LIVE_AUTH_MAX_WINDOWS 1
```

I do not read, generate, overwrite or log `PMUS_Q1_SESSION_ID` or
`PMUS_Q1_ARM`. Setting an EXACT slug rather than the `cpc-btc-updown-15m-*`
pattern is what makes this one window and not a campaign: the authorization
goes inert the moment the window rolls, with no further action from either of
us.

### Window table (recompute if you arm later; the arithmetic is fixed)

`entry` closes at T+9:00 on `TOO_LATE_TO_WITHDRAW`; `terminal` is T+10:00 and
nothing may rest past it; `NOT_AFTER` is terminal + 1 minute so cancellation
and reconciliation finish inside the authorization.

```
cpc-btc-updown-15m-2026-10-20-1200z  entry 12:00-12:09  terminal 12:10  NOT_AFTER 2026-10-20T12:11:00Z
cpc-btc-updown-15m-2026-10-20-1215z  entry 12:15-12:24  terminal 12:25  NOT_AFTER 2026-10-20T12:26:00Z
cpc-btc-updown-15m-2026-10-20-1230z  entry 12:30-12:39  terminal 12:40  NOT_AFTER 2026-10-20T12:41:00Z
cpc-btc-updown-15m-2026-10-20-1245z  entry 12:45-12:54  terminal 12:55  NOT_AFTER 2026-10-20T12:56:00Z
```

**Allow at least 10 minutes before the window opens.** A Railway variable
change redeploys the service (~2 min), and my shadow-lock removal is a SECOND
redeploy (~2 min). Durable state lives on the volume, so a restart loses
nothing.

## Then, in order, mine

1. Confirm `armed()` is true, the deadline is in the future, and the market
   binding is the exact slug.
2. Re-reconcile: positions, live orders, committed cash, unresolved
   accounting, available cash.
3. Re-confirm the watchdog is sweeping and the loss gates read the L5 line.
4. Remove `L5_SHADOW_ONLY` -- **for this window only**.
5. Let the engine place ONE post-only maker leg inside the entry band.
6. On an exchange-confirmed fill, let the complement reprice from the live
   book against the realised cost and the unmatched remainder.
7. Cancel by exact venue order id at the per-order TTL (120s) or the terminal,
   proved terminal by re-read.
8. Restore `L5_SHADOW_ONLY` at the terminal. **No roll into another window**
   -- the exact-slug binding makes that impossible anyway, and
   `L5_LIVE_AUTH_MAX_WINDOWS=1` is the second stop.
9. Reconcile again, then report.

## What I will report

First-leg fills and partials (exchange-confirmed only), second-leg fills and
partials, **time between first and second fill**, cancellations with their
venue ids and proved terminal states, stranded inventory, settlement, and
settlement-only realized net P&L.

A submitted complement will be reported as **submitted**, never as a hedge.
