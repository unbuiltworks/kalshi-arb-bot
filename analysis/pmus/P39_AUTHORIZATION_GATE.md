# PMUS BTC15m faithful-L5 probe — owner authorization gate

**Status: DISARMED. Nothing has been transmitted. Arming is an owner act.**

Supersedes the transport/identity gaps recorded in `P38_LIVE_PROBE_MANIFEST.md`
§8 ("no account-identity proof is possible today"): that proof now exists and
runs every window, below.

## What would trade

| | |
|---|---|
| Railway service | `l5-pmus-live` `87f1834e-530e-4fc4-a310-91c8d3add196` (project `arb-ev-wallet-shadow`) |
| branch | `claude/polymarket-us-btc15m-l5-0l1xw9` |
| path | L5 activity → authenticated BTC15m book (markets WS) → L5_NATIVE → PMUS_TRANSLATION (frozen `030d8bf`) → SAFETY_OVERLAY → `PmusTransport` |
| credentials | Railway reference variables to `pmus-ws-evidence-recorder` (no value copied) |
| account | venue answers `/v1/account/balances`; key fingerprint `sha256:d51b44a3fa83`; buying power 63.05 at 23:28Z |
| instrument | Polymarket US `cpc-btc-updown-15m-<date>-<hhmm>z`, the window open NOW, one at a time |

This is the same process that has been running DISARMED; arming changes only
whether `PmusTransport.arm()` succeeds.

## Hard limits (account protection only)

| control | value |
|---|---|
| max committed per market | $5.00 |
| max total exposure, **probe lifetime** | $5.00 — committed never resets, so the first market to use it ends the probe |
| max directional residual | 10 shares |
| concurrent markets | 1 |
| daily realized-loss halt | $5.00 configured, but **not yet fed by venue settlement**; the binding loss bound is the $5 lifetime exposure cap above |
| resting maker orders | full notional reserved against the caps while resting |
| stale action | 30 s from receipt; book staleness per frozen rule (2.0 s on a live socket) |
| price band | none (behavioural preference, not protection) |

**Worst case: $5.1738** — $5 principal (every share can settle worthless) +
$0.1738 max taker fee (10 contracts at p = 0.5, venue fee implementation). An
action whose notional alone exceeds the remaining cap is refused whole, never
resized.

## Execution semantics (what the frozen rule produces live)

* **Crossing action** → TAKER at the authenticated touch (UP at ask, DOWN at
  1 − bid), capped at displayed depth: `ORDER_TYPE_LIMIT`, IOC, fill read back
  from `GET /v1/order/{id}`.
* **Restable action** → MAKER at L5's own price, post-only GTC. Life ends at
  L5's next side/price change or window end (cancel), or venue fill. STRICT is
  the replay crediting standard and is not applied as a live refusal
  (`4f68e3a`). Provenance carries both.
* Size `l5_size × λ`, λ = 0.04518752824220514525, floored to the venue's 0.01
  quantity grid; below minimum is refused, never rounded up.
* At most once: intent persisted before the mutation; an ambiguous response is
  never replayed and its notional is reserved.

## Evidence on record (DISARMED, live venue data)

| gate | result |
|---|---|
| authenticated book from window open | **PASS**, build `72ee0ad` (fresh connection at window open, retried every 3 s): every full window 04:45Z–08:00Z had its first two-sided book at +0.2 s to +5.2 s, before the first trade. History: a subscription placed before open is silent ~3 min (+184 s); same-connection re-subscribe does not fix it (`ec19b6b`, 0115z); a fresh connection does |
| preflight | **PASS** every window, 0 blocking failures: credentials, signed balances (buying power 63.05 ≥ 5.1738), market state OPEN (authenticated stream) + published contract terms, UP=long, tick 0.01, min qty 0.01, book two-sided/authenticated/fresh, no positions, no open orders |
| frozen rule | **PASS**, `030d8bf` hash guard |
| maker semantics | **correct** (`4f68e3a`): restable actions rest post-only at L5's price; provenance carries `frozen_rule_output: STRICT_NOT_EVALUABLE` and `live_execution: REST_AT_L5_PRICE`; crossing actions take the authenticated touch capped at displayed depth |
| disarmed end-to-end, **3 consecutive windows with L5 active** | **PASS**: 0730z 71 NOT_ARMED / 0 NO_BOOK; 0745z 46 NOT_ARMED / 3 STALE / 0 NO_BOOK; 0800z 60 NOT_ARMED / 1 STALE / 0 NO_BOOK. Book before L5's first action every window. STALE = frozen rule's own 2.0 s bound on a silent socket (0800z: 08:14:20, last 40 s, book 0.01/0.01, stream silent 2.8 s): refused, never priced against an unproven book. Nothing transmitted, committed or resting |
| venue maintenance | Thursday 06:00–08:00 ET (10:00–12:00 UTC) the venue is in weekly maintenance. 2026-09-24 10:00Z: the authenticated stream delivered nothing on both the runner and the independent recorder; the runner's preflight FAILED (so it cannot arm) and all L5 actions were refused NO_BOOK. Fail-closed as designed; reconnects now back off after 10 fruitless tries (`RESUB_FAST_MAX`) |
| L5 dormancy note | L5 made no trades 01:17:52Z–~07:30Z; windows in that span carried no actions and are not counted |

## To authorize (owner only)

On `l5-pmus-live`, set all three, with `<SID>` any fresh string:

```
PMUS_Q1_LIVE=YES
PMUS_Q1_SESSION_ID=<SID>
PMUS_Q1_ARM=<SID>
```

The service redeploys; at the next window whose preflight PASSES the runner
logs `L5_LIVE_ARMED` and acts. Optionally also set
`L5_LIVE_EXPECT_ACCOUNT=sha256:d51b44a3fa83` so a different key refuses.

## To stop

* **Now:** set `L5_LIVE_HALT=1` — deadman cancels every open order in the
  market, every new action is refused OPERATOR_HALT, and it never re-arms.
* **Disarm:** unset `PMUS_Q1_ARM`.
* Budget exhaustion stops it by itself ($5 lifetime).

Realized P&L is labelled only from venue settlement
(`GET /v1/markets/{slug}/settlement`); nothing modelled is called realized.
