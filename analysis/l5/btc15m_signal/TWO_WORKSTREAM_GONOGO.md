# WS1 BRTI + WS2 paired-only — both NO-GO, one blocker each

Issue #194, 2026-11-06. **No orders. Nothing armed, deployed or changed. No
service added. No SOL executor touched. No cost incurred. No self-approval.**
`btc_ref_recorder` is unchanged and no Coinbase or Chainlink substitution was
made. Tree identical to `8ec72883`, so that commit's parity stands: 80 failing
ids, identical set, 16,843 passed.

---

# WS1 — BRTI feed: **NO-GO**

### Single blocker: licence permissibility for *our* use. Not mine to clear.

CF Benchmarks' own position is that "use and distribution of the CF Benchmarks
data requires a license from CF Benchmarks or its authorized licensing
agents", and its BRTI page directs anyone wanting real-time or historic data
"to power a product or service" to its licensing team. CME, which distributes
the same benchmarks, requires a signed **Market Data License Agreement**, and
states that **creation and distribution of derived works requires a separate
Derived Data License Agreement**.

Our plausible access route (below) would obtain BRTI under a **Kalshi**
entitlement and use it to price trades on **Polymarket**. That is a
derived-data, cross-venue use. I cannot establish that it is permitted, and
guessing in our favour on a benchmark administrator's licence terms is not a
call I should make. **Owner/legal decision.**

### What I did identify, and it is genuinely promising

The venue's own settlement text (read live last cycle) names BRTI with
60 observations averaged in the last minute before each endpoint. The repo
already recorded that rule at `src/l5_venue_adapter.py:379-387`, sourced from
`GET /v1/events?series_id=375`.

**Kalshi operates a CF Benchmarks REST passthrough**, and it is reachable from
this environment today:

| probe | result |
|---|---|
| `GET /trade-api/v2/cfbenchmarks/values?id=BRTI` | **401 `token_authentication_failure`** |
| `GET /trade-api/v2/cfbenchmarks/indices` | **401 `token_authentication_failure`** |
| `GET /trade-api/v2/nonexistent-path-probe` | **404 `page not found`** |
| `GET /trade-api/v2/exchange/status` | 200, `exchange_active: true` |

401 rather than 404 on both `/cfbenchmarks/` paths means **the route exists on
Kalshi production**. Per Kalshi's docs it is authenticated with existing
Kalshi credentials — **no separate CF Benchmarks key** — everything after
`/cfbenchmarks/` is forwarded upstream, historical values are available
through the same passthrough, and there is a matching authenticated websocket
value feed. The host `api.elections.kalshi.com` is **already on this
environment's allowlist**, so no network change is needed.

### What remains unverified, and needs one authenticated call

| question | status |
|---|---|
| Our account's entitlement | **UNKNOWN** — docs say "available only to accounts with the appropriate entitlement". 403 would mean no; 200 means yes. Credentials live on Railway, not in this session. |
| Publication cadence | **UNVERIFIED.** The venue samples 60/minute ⇒ ~1 Hz. One third-party project implements a *5 Hz* BRTI observer, so the native rate may be higher. Reproducing the venue's average needs the exact rate. |
| History depth | **UNVERIFIED** — docs say historical values exist; depth unmeasured. |
| Timestamp alignment & gaps | **UNMEASURED** — this is the whole point and cannot be done until a feed is readable. |
| Cost | **No dollar cost and no new service.** The passthrough consumes Kalshi rate-limit tokens (community reports ~50 per read), not money. We run no Kalshi trading, so that budget is idle. |

Direct CF Benchmarks access is *not* an option from here: `cfbenchmarks.com`,
`api.cfbenchmarks.com` and `docs.cfbenchmarks.com` are all proxy-blocked
(`http=000`), and direct access is licensed-only regardless.

### Narrowest fix, in order

1. **Owner clears the licence question** — whether BRTI obtained via our Kalshi
   entitlement may be used to price Polymarket trading. This is the blocker;
   nothing below matters until it is answered.
2. If cleared: one authenticated `GET /trade-api/v2/cfbenchmarks/values?id=BRTI`
   from the executor's environment to establish entitlement, cadence, history
   depth and timestamp fields. Read-only, one call, no collector change.
3. Only then: point `btc_ref_recorder` at the passthrough and collect
   continuously. **~200 settled windows (~50 hours)** before any calibration
   can claim anything.
4. Promotion still requires calibration **and** held-out replay showing net
   executable improvement. The last held-out replay said NO-PROMOTION on
   Chainlink data; BRTI data does not inherit that result in either direction.

---

# WS2 — paired-only four-window campaign: **NO-GO**

### Single blocker: there is no owner-signed approval document.

`L5_LIVE_GATES` reports `campaign_id: null`; the previous approval reads
`approval_state: approval_expired`. The authorizing instruction states the
limits in prose but carries **no nonce, no SHA256, no window starts and no
expiry**, so there is nothing for `ApprovalGate` to parse or for me to verify a
digest against. Minting one myself would be self-approval, which is forbidden
and which I will not do.

### First: the loss arithmetic is wrong, and the real number is tighter

The premise was a $20 cumulative cap with $13.7795 used, leaving $6.2205. **The
deployed cap is not $20.** Read from `L5_LIVE_GATES` at 2026-11-06T01:16:37Z:

```
max_cumulative_loss_usd : 18.6325     <-- not 20
max_l5_cumulative_loss_usd : 20
max_cash_deployed_usd : 12
max_worst_case_deficit_usd : 1.50
max_l5_daily_loss_usd : 5
max_unmatched_contracts : 5
cash_policy : NO_RECYCLING
```

The gate (`l5_live_validation_gates.py:516-520`) is

    loss = max(0, -cumulative_realized) + max(0, cumulative_unrealized_loss)
    refuse when loss >= max_cumulative_loss_usd

and `cumulative_realized` is fed `store.realized_today()`
(`l5_live_execution.py:6824, 8168`), which is the **lifetime** figure —
**−$13.7795**. So:

| quantity | value |
|---|---|
| deployed cumulative cap | **$18.6325** |
| lifetime realized loss already counted | **$13.7795** |
| **remaining headroom** | **$4.8530** (not $6.2205) |
| 4 windows × $1.50 per-market worst case | **$6.0000** |
| **shortfall** | **−$1.1470** |

**The owner's instinct is right and the margin is smaller than stated.** Three
windows fit the worst case ($4.50 < $4.8530); the **fourth would be refused**
with `CUMULATIVE_LOSS_LIMIT`. And the binding constraint is neither the $5
daily halt nor the $20 exposure cap — it is this lifetime gate.

To be fair to the other direction: the *expected* case fits easily. The last
campaign settled **−$1.53 over 6 attested windows** (≈ −$0.26/window), so four
windows project to roughly **−$1.00**. The campaign would very probably
complete. But its worst case does not fit, and the instruction was explicitly
not to assume a daily cap makes it permissible.

### Second: an open budget ledger that should self-release — verify, don't assume

```
campaign_budget: campaign_id camp1.4a09bc22bdbba1bedc3a15200ef013cd
                 closed false   peak_usd 4.9892   budget_usd 5
                 remaining_usd 0.0108   owns_budget false
```

`refuse_arming` returns `CAMPAIGN_BUDGET_LOCKED:<open_id>` when a different
campaign id arrives while a ledger is open — which a new campaign would hit.
But `release_stale` is wired **ahead** of it
(`l5_live_authorization.py:2538`) and releases a provably finished
predecessor on venue evidence: nothing outstanding, every committed leg
settled. Both conditions hold — the venue reports **0 open orders** and
**0 pending terminal**, and the two drift markets
(`2026-10-12-0030z`, `2026-10-14-2230z`) are both `retired: true`,
`venue_state: MARKET_STATE_EXPIRED` in the settled harvest. So this should
clear itself. **It is not the blocker, but it must be observed as
`released: true` on the first arm, not assumed.**

### Third: a gate looser than the authorization, worth naming

`max_unmatched_contracts` is **5** in the additive validation gates, while the
authorization specifies **one-contract residual inventory**. The k=1 cap is
carried separately (`PMUS_RESIDUAL_CAP_K`, whose value is redacted to an OAuth
caller like me; it read k=1 previously). So the authorization is tighter than
one of the gates enforcing it — not unsafe, but the $1.50 deficit bound rather
than this gate is what actually binds residual size.

### Safety gates re-verified, not assumed

| gate | state |
|---|---|
| Deployed SHA | `e7111211`, deployment `8a709fc8` SUCCESS |
| Armed | **false**, `submission_gate.blocked: true`, `NOT_ARMED` |
| Watchdog (service `fb038b8c`) | sweeping every 20 s, `read_error: null`, `cancel_enabled: true`, heartbeat `halt: false`, 0 open orders, nothing overdue or unverified |
| Reconciliation | `reconciled: true`, `authority: VENUE`, `uncertainty_cleared: true`, `ALERT: null`, 0 open orders at venue, 0 pending terminal, `quarantined: []` |
| Exact-order cancellation | by venue order id, `POST /v1/order/<id>/cancel` with `{"marketSlug": slug}`, and **refuses** an absent slug rather than the empty body that drew HTTP 400 for 530 s on 2026-10-18 |
| Venue state | flat |
| `L5_LIVE_HALT` | **still CLEAR** — unchanged by me; see the ask below |

### Paired-only expected executable economics

From the 148 attested venue-confirmed settled windows:

| component | per unit |
|---|---|
| profit pairs (cost ≤ $0.96) | **+$0.1032** |
| rescue pairs (cost > $0.96) | **+$0.0175** |
| residual (unmatched) | **−$0.2685** |

Breakeven needs ≈2.6 profit pairs per residual unit; we run 0.66. **Expected
four-window outcome is negative, roughly −$1.00.** This campaign is an
execution and accounting test, not a profit expectation, and should not be
authorized as one.

---

## The exact approval artifact requirements

From `src/l5_campaign_approval.py`. `REQUIRED_FIELDS` (line 74) — a document
missing any of these is `approval_incomplete`:

| field | constraint |
|---|---|
| `approved` | literally `true`, else `approval_not_asserted` |
| `nonce` | long enough, else `approval_nonce_too_short`. `nonce_sid(nonce)` derives the `camp1.…` id |
| `series` | must be `"cpc-btc-updown-15m-"` (`ALLOWED_SERIES`) |
| `issued_utc` | not in the future, not stale beyond the campaign window |
| `expires_utc` | in the future; campaign span ≤ `MAX_CAMPAIGN_S` = 4 h |
| `max_windows` | ≥ 1, clamped by `MAX_WINDOWS` = 8 — **use 4, or 3 per the headroom above** |
| `max_total_exposure_usd` | clamped by `MAX_TOTAL_EXPOSURE_USD` = $20 |
| `max_daily_loss_usd` | clamped by `MAX_DAILY_LOSS_USD` = $5 |

**Plus an opening bound, or every window is refused** with
`CAMPAIGN_OPENING_BOUND_ABSENT` — a legacy document parses as approved but is
inert by design ("PARSING a document as approved cannot trade; OPENING a
window can"). Supply **`window_starts`** (the explicit list, ≤ `max_windows`,
each < `expires_utc`) and optionally `starts_utc` / `last_open_utc`.

**And the digest:** the gate records `sha256` of the raw document bytes. The
authorization must quote that SHA256 so the installed document can be verified
against what was approved.

So the artifact I need from you is: **nonce, SHA256, series
`cpc-btc-updown-15m-`, `issued_utc`, `expires_utc`, `max_windows`,
`max_total_exposure_usd: 20`, `max_daily_loss_usd: 5`, and the explicit
`window_starts` list.** I will verify the digest against the installed
document before anything arms, and I will not mint or sign it.

## Two decisions that are yours

1. **Window count: 3 or 4.** Four does not fit the worst case in $4.8530 of
   remaining cumulative headroom; three does. I recommend **3**, which needs no
   limit change. Raising `L5_MAX_CUMULATIVE_LOSS_USD` would make 4 fit and I am
   **not** proposing it.
2. **Re-assert `L5_LIVE_HALT=1`** in the meantime. It is clear; only
   `armed=false` and the expired approval are holding submissions. Risk-reducing,
   one variable write, ~4 min rebuild. Recommended, and still not done unasked.

## GO/NO-GO

| workstream | verdict | single blocker |
|---|---|---|
| **WS1 BRTI** | **NO-GO** | Licence permissibility: may BRTI obtained via our Kalshi entitlement be used to price Polymarket trading? Owner/legal, not mine. |
| **WS2 paired-only** | **NO-GO** | No owner-signed approval document — no nonce, SHA256, window starts or expiry exists to verify. |

**submitted 0 · filled 0 · settled 0 · realized $0.00**
