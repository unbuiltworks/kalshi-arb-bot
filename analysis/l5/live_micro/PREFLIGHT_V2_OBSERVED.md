# LIVE-L5-MICRO-1 v2 — complete read-only preflight. **STILL DISARMED**

Fingerprint **unchanged**:
`0522b0dac3fc5dec5ec0a01c6dbbb6369fde3f7062cede16f965fdfc459d1b31`.
Risk envelope unchanged ($0.05 market cap, $0.02 directional, one resting
order, maker/post-only). **No deployment was made. No service was modified.
No credential read, moved or printed. No micro key set. No order, preview,
cancel or signature. S3 untouched.**

## How the observation was obtained — no deployment needed

I did **not** need to deploy an observer. `pmus-ws-evidence-recorder` is
already running the **same authenticated markets WebSocket, with the same
parser**, that `AuthBook` reuses by design — `restartPolicyType: ALWAYS`,
continuously live. Its existing `PMUS_WS_STATUS` stream carries the
authenticated book and stream-health telemetry directly.

So the on-host observation is **pure log reading**: zero mutation, strictly
weaker than changing a start command on a credentialed service.

## 1. Authenticated WS — genuinely realtime, 31 consecutive observations

**Window 13:47:55.509Z → 13:52:01.715Z**, cadence median **5.04 s**
(min 4.99). `auth: "ok"` and `conn: "connected"` on **all 31**.

| measure | value |
| --- | --- |
| consecutive observations | **31** |
| `messages_received` | **4,318,194 → 4,321,585** (+3,391), **strictly increasing on every step** |
| `frames_60s` | median **803** (774–870) ≈ 13 frames/s |
| `last_frame_age_s` | median **0.00**, max **0.00** |
| `last_book_age_s` | median **0.00**, max **0.00** |
| **observations with age ≤ 2.0 s** | **31 / 31 = 100.0 %** |
| fresh / stale markets | 2 / 7 |

**This is not one coincidentally fresh frame.** A strictly monotonic
message counter across 31 observations, with ~800 frames per 60 s
sustained, is a live push stream. Per the `AuthBook` contract, age is
measured from the last **frame**, not the last book change.

### Live BTC15m book, authenticated

| obs (UTC) | slug | bid | ask | own age s | two-sided |
| --- | --- | --- | --- | --- | --- |
| 13:47:55 | …1345z | **0.5300** | **0.5400** | **0.0** | yes |
| 13:48:00 | …1345z | 0.5200 | 0.5300 | 0.0 | yes |
| 13:48:05 | …1345z | 0.4700 | 0.4800 | 0.0 | yes |
| 13:48:10 | …1345z | 0.5600 | 0.5700 | 0.0 | yes |
| 13:47:55 | …1330z *(expired)* | 0.9900 | 0.9900 | **158.7** | yes |
| 13:48:10 | …1330z *(expired)* | 0.9900 | 0.9900 | **173.7** | yes |

**The expired window is the control that proves the age field is real.**
The live 1345z book moves every observation at age 0.0, while the settled
1330z book is frozen at 0.99/0.99 with its age climbing 158.7 → 173.7. A
broken or hardcoded age field could not produce both.

**No L5 activity or fill was used as freshness evidence** — freshness comes
from our own socket's frame counters and per-market update ages only.

Every observed spread is exactly **0.01**, independently confirming the
live tick grid matches the metadata.

## 2. Gateway — **NOT MEASURED**

| | |
| --- | --- |
| contemporaneous gateway timestamp / bid / ask / age | **NOT OBTAINED** |
| why | the recorder does not poll or log the gateway BBO, and this container's network policy denies `gateway.polymarket.us`. Obtaining it would require deploying a poller, which this instruction does not authorize and which I did not do |

## 3. Comparison at matched observation times — **NOT MEASURED**

I cannot report a matched-time WS-vs-gateway delta, because item 2 was not
obtained. **I will not present the historical figure as if it were this
run's measurement.** For context only, from the prior probe's own record:
gateway ~30 s stale, worst observed disagreement **0.19** (gateway YES
0.84/0.85 vs authenticated 0.65/0.66).

**The gateway remains FORBIDDEN for placement and markouts regardless**,
which is already frozen in the spec as
`bbo_source_forbidden` — so this gap does not weaken the fix. It is an
unmeasured *confirmation*, not an unmet requirement of the design.

## 4. Executor states — RUNNING / ARMED resolved, with the distinction kept

Method: variable **names** from `describe-service` (which does not return
values) plus each service's **own self-announced** arming boolean from its
logs. **Railway `list-variables` was not used** — it returns values and
these services hold `PMUS_API_SECRET_KEY`.

A secret-free capability test applies throughout: **a service without
`PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY` cannot submit a PMUS order at
all, however it is armed.**

| service | credentials | restart | last deployment | state |
| --- | --- | --- | --- | --- |
| `pmus-q1-executor` | **none** (zero variables) | NEVER | 2026-09-06 | **NOT RUNNING** |
| `l5-pmus-live` | yes | ALWAYS | **null — never deployed** | **NOT RUNNING** |
| `l5-live-probe-oneshot` | yes | NEVER | 2026-10-01 (one-shot, completed) | **NOT RUNNING**, and **RUNNING + DISARMED** evidence when it last ran |
| `pmus-ws-private-probe` | yes | NEVER | 2026-10-05 (one-shot, completed) | **NOT RUNNING**; no arming variable exists |
| `pmus-ws-evidence-recorder` | yes | ALWAYS | 2026-10-02 | **RUNNING + DISARMED** |
| `l5-latent-quote-shadow` | **none** | ALWAYS | 2026-10-03 | RUNNING, cannot submit |

**The distinction you asked for is preserved.** `l5-pmus-live` and
`pmus-q1-executor` are reported **NOT RUNNING**, *not* DISARMED — neither
has produced authoritative arming-state evidence, and I am not relabelling
process absence as disarmed.

Authoritative arming evidence, where it exists:
- `l5-live-probe-oneshot` self-reported, repeatedly on 2026-10-01:
  `"outcome": "REFUSED", "refusal": "NOT_ARMED: PMUS_Q1_LIVE must be
  exactly 'YES', and PMUS_Q1_SESSION_ID, PMUS_Q1_ARM and PMUS_CASE1_ARM
  must all equal the same session id"`, and earlier
  `"disarmed": {"disarmed": true, "was_armed": true}`.
- `pmus-ws-evidence-recorder` self-reports `"post_capable": false` and
  `"armed": ""`.

## 5. Contamination check — **NO concurrently running order-capable process**

Only **one** credentialed service is currently running: the recorder. It is
order-incapable on two independent grounds:

1. **Structural.** Its deploy workflow *refuses to build* any image whose
   Dockerfile references `pmus_q1_executor`, `pmus_micro_executor`,
   `pmus_spring_l5_session`, `_ARM` or `_LIVE=YES` — step name: "Refuse to
   deploy an image that could trade".
2. **Self-reported.** `post_capable: false`.

Every other credentialed service is a completed `NEVER`-restart one-shot or
has never deployed. **A future micro fill could not be misattributed**, on
this evidence.

## 6. The frozen envelope, re-stated unchanged

| | |
| --- | --- |
| fingerprint | `0522b0da…9d1b31` (**unchanged**) |
| tick | **0.01**, from venue `orderPriceMinTickSize` as a grid, fail-closed; no universal constant |
| `minimumTradeQty` | **0.01** → passes the frozen `< 1` |
| minimum executable | **$0.0046** at 0.46 (range $0.0001–$0.0099) |
| BBO source | `AuthBook`, bound **2.0 s** reused from `l5_preview_gate.BOOK_MAX_AGE_S` |
| maker/taker | OBSERVABLE (`passiveExecution`/`aggressorExecution` via `isAggressor`) |
| fees | OBSERVABLE (signed `commissionNotionalCollected`) |
| max market loss | **$0.05** |
| max directional | **$0.02** / actual first-fill quantity |
| resting orders | **1**, maker/post-only |
| proposed host | `l5-live-probe-oneshot` |
| state | **DISARMED** |

## 7. What this preflight does and does not clear

**Cleared:** the BBO defect is resolved with measured evidence — an
authenticated realtime book at **100 % ≤ 2.0 s over 31 consecutive
observations**, with a live-vs-expired control proving the age field. Tick,
minimum quantity, maker/taker and fee observability are all established.
Arming and contamination are resolved without reading a single secret.

**Not cleared:** the **gateway-vs-authenticated matched-time comparison**
(items 2 and 3). You listed it as a report item, so I am flagging it as
unmeasured rather than quietly omitting it or substituting the historical
number.

One economic note, unchanged and not acted on: every observed spread is
**0.01**, so pair cost at both touches is **1 − 0.01 = 0.99**, well above
the frozen **0.96** reservation. The probe will very likely **stand down
rather than place**. That is the frozen experiment working as designed, and
I have not touched the reservation.

**Not requesting authorization**, and not deploying or arming anything.
Returning to you.
