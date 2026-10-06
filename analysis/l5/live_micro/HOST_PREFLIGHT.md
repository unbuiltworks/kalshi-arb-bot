# LIVE-L5-MICRO-1 — existing-environment preflight. **READ-ONLY. STOP.**

Fingerprint unchanged: `69904fd4e64bcc62093ce70c1b63b5d825548f5d495eda63744fc91169919118`.
Probe 1 stands classified **INFRASTRUCTURE SKIP / NO SUBMISSION**. Nothing
armed, nothing deployed, no credential moved, printed, rotated or recreated.
No order submitted. S3 untouched and collecting.

## Requested readout

| item | finding |
| --- | --- |
| **gateway reachable** | **YES** from the existing Railway host — but see the staleness defect below |
| **tick** (`orderPriceMinTickSize`) | **0.01** |
| **minimumTradeQty** | **0.01** |
| **minimum executable dollars** | **$0.0001 – $0.0099**; **≈$0.0046** at the observed 0.46 price |
| **existing credential presence** | **YES** (names only; values never read) |
| **proposed host** | Railway `arb-ev-wallet-shadow` / `production` → service **`l5-live-probe-oneshot`** (`84aa9999`) |
| **other executors disarmed** | **NOT CONFIRMABLE BY ME** — see below |

Live metadata, verbatim from the recorder's own stream at
**2026-10-06T13:47:55Z** (a BTC15m market closing 14:30Z):

```
"market_state": { "active": true, "closed": false,
  "status": "MARKET_STATUS_OPEN", "endDate": "2026-10-06T14:30:00Z",
  "minimumTradeQty": 0.01, "orderPriceMinTickSize": 0.01 }
```

---

# STOP — three blocking findings

## 1. The tick fails my own frozen gate, and the fault is MINE not the venue's

- Venue serves **tick 0.01** on live BTC15m.
- The authorized fingerprint says `"tick_required": "0.005"` with
  `"fail_closed_on_other_tick": true`.
- **0.01 ≠ 0.005 → fail closed → SKIP.**

**I must be blunt about the cause.** I derived "0.005 required" from
`pmus_q1_executor.TICK = D("0.005")`. But the real micro executor
**deliberately does not require 0.005** — its own comment records that a
hardcoded equality check *"REFUSED EVERY SPORTS MARKET with
MICRO_TICK_UNSUPPORTED:0.01 — the live path could not have placed a sports
order at all"*, so it was replaced with "accept the venue's grid and
validate the price against **that** rather than against a constant."

So I wrote a requirement into the spec that the production code had already
abandoned as wrong, you authorized it in good faith, and **it is
unsatisfiable on the target market.** Correcting it changes the fingerprint,
which voids your authorization. **I have not changed it.** Per your
instruction I am stopping and reporting rather than altering the
requirement.

`minimumTradeQty = 0.01` **passes** the `< 1` requirement cleanly.

## 2. The gateway BBO is ~30 s stale — the probe's placement rule would misfire

The repository's **own prior live probe** on this exact host recorded:

> "X.fresh_bbo reads gateway.polymarket.us, which this repository's own
> recorder documents as **~30s stale**. At 16:37:52Z it reported YES
> 0.84/0.85 while the authenticated WS book read 0.65/0.66 seconds later — a
> **19 cent disagreement**. The preregistered rule is 'one tick below the
> own-terms touch'; we actually rested about **TWENTY ticks** below it."

That probe **submitted, rested, and was cancelled unfilled**, P&L $0.00,
`cumQuantity 0`. Its own verdict was **PROBE_FAILURE — an instrument
artifact, not evidence.**

My frozen spec sets `offset_from_touch_ticks: 0` but **never names the BBO
source**. On this host the default path is the stale gateway, so
LIVE-L5-MICRO-1 would reproduce that failure exactly: nominally "at the
touch", actually ~20 ticks away. Naming the authenticated WS book as the
source is a spec change → new fingerprint.

## 3. Two of my required measurements are currently unobtainable

That same commit records three defects **deliberately left unfixed**:

| defect | consequence for this probe |
| --- | --- |
| `aggressor` is a **boolean** (venue sent `false`), so "the orientation rule could never have resolved a role" | my ledger **requires** venue maker/taker and **raises** otherwise — the probe would halt rather than mis-record |
| `extract_private` skips `Mapping` values, so `lastPx` and `commissionNotionalCollected` read **UNAVAILABLE** | my **fees** line cannot be populated from the venue |
| the BBO source above | finding 2 |

My ledger refusing to invent a maker/taker value is correct behaviour, but
it means the probe **cannot complete its required reporting** on this host
as built. Fixing these changes the instrument.

---

## What the environment *can* do

Genuinely available, no new stack needed:

- **Credentials already exist** on `l5-live-probe-oneshot` and
  `pmus-ws-evidence-recorder` (`PMUS_API_KEY_ID`, `PMUS_API_SECRET_KEY`;
  Ed25519 request signing). **Values were never read.**
- **`l5-live-probe-oneshot` is the right host**: credentials present,
  `restartPolicyType: NEVER` (one-shot, matching the frozen no-auto-restart
  requirement), a 50 GB volume at `/state/pmus-ws` for a durable ledger, and
  it is the service that already executed a real authenticated PMUS order.
- **The four micro keys CAN be supplied independently.** That service
  carries **no `PMUS_MICRO_*` variable**, so setting them arms micro only,
  and the code-level dual-key isolation is tested in both directions.

### Do NOT host it on the recorder
`pmus-ws-evidence-recorder` holds credentials but its deploy workflow
**refuses any image referencing `pmus_micro_executor`** — the exact module
needed. That refusal is a deliberate safety boundary ("Refuse to deploy an
image that could trade"). Hosting the probe there means defeating it. I am
not proposing that.

### Why I cannot confirm other executors are disarmed
`l5-live-probe-oneshot` carries arming variable **names** for four other
executors — `PMUS_Q1_*`, `PMUS_CANARY_ARM`, `PMUS_CASE1_ARM`,
`PMUS_SCHEMA_PROBE_ARM`, plus `PMUS_LIVE_MANDATE`. Arming depends on their
**values**, and reading them would pull `PMUS_API_SECRET_KEY` into this
transcript. **I did not read them.**

What I can state: `l5-pmus-live` (`87f1834e`) has q1 arming names configured
but **`latestDeployment: null`** — nothing is running there. And
`pmus-q1-executor` (`fbd8eab5`) has **no variables at all** and last
deployed 2026-09-06.

**You must verify the other four arming families yourself.** Starting the
micro probe on that service does not disarm them, and its start command
currently runs `scripts.pmus_q1_settlement_audit` — changing it is a deploy.

---

## What I need from you

Nothing is armed and nothing proceeds until you decide. The options, stated
without a recommendation disguised as a finding:

1. **Re-authorize a corrected fingerprint** that (a) accepts the venue's own
   tick grid instead of requiring 0.005, (b) names the authenticated WS book
   as the BBO source, and (c) states how maker/taker and fees are obtained
   or explicitly tolerates `UNRESOLVED`. That is a new fingerprint and a new
   authorization; I will not self-authorize it.
2. **Fix the three recorded instrument defects first**, then re-preflight
   against an unchanged risk envelope.
3. **Hold the probe** and let S3's zero-capital cohort answer the execution
   question first — it needs no credentials, no signing and no venue
   mutation.

I have not altered the fingerprint, the risk envelope, the minimum-quantity
requirement, the market family, or any execution behaviour. Probe 1 remains
INFRASTRUCTURE SKIP / NO SUBMISSION.
