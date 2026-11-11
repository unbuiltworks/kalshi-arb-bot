# Smallest faithful live-probe manifest — PREPARED, NOT AUTHORIZED

**No order has been transmitted and none can be.** The engine's default
transport is `NullTransport`, which refuses every submission; no venue client,
credential or HTTP call exists in the execution module, and nothing in it can
construct a real transport. This document is for owner review only.

## 1. Exact branch / commit / deployment

| | |
|---|---|
| branch | `claude/gemini-btc-economic-viability-h3u7zl` |
| execution path commit | `a4af9a6` |
| **frozen faithful rule** | **`030d8bf`** — unchanged, hash-guarded, guard passing |
| observer service | `l5-pmus-observer` `9ce058ce-1cbc-423b-80b1-7fd07544f499` |
| observer deployment serving | `c08129de` (SUCCESS); `a80c494a` building for `a4af9a6` |
| authenticated recorder | `pmus-ws-evidence-recorder` `71b6ea99-…`, deployment `1dde7924-…` |
| tests | 216 green |

## 2. Prospective out-of-sample fidelity accumulated so far

**ZERO windows scored.** This is the gating number and it is empty.

The freeze was set at 20:16Z; the first prospective window opens 20:30Z. No
prospective window has yet both settled and carried authenticated tape.
Development-sample figures (46.1% reproduction, 91.6% taker, zero-cross 3/5,
opposite acquisition 5/5, venue-only −$2.88) are **in-sample** and are not
evidence for this manifest.

**On the evidence available today this probe should not be authorized.** The
manifest is prepared because the engineering is ready, not because the
validation is.

## 3. Exact sizing translation

`λ = 1 / median(l5_size)` over the preserved corpus = **0.04518752824220514525**

The median L5 action (22.13 shares) maps to **1.00 share**. Every action is
`l5_size × λ`, so L5's relative geometry is preserved exactly; only the scale
is bounded. λ is computed from L5's sizes alone — no price, fill, outcome or
P&L enters it.

Capital is bounded by the limits in §5, not by distorting the geometry.

## 4. Expected maker/taker behaviour

**Predominantly taker.** In the development sample 120 of 131 reproduced
actions (91.6%) required crossing, because by the time an L5 trade reaches us
his price is usually at or through our touch. This is an execution
consequence of the frozen rule, not a preference: maker is used whenever
L5's price can rest without crossing, and that was possible for 11 of 131.

Taker fee is charged on every crossed fill (Θ = 0.0695); maker rebate
(Θ = −0.0125) accrues on rested fills. Both are recorded on separate lines
and are never netted into trading P&L.

## 5. Hard limits (account protection, outside L5_NATIVE)

| control | value |
|---|---|
| max dollars per market | **$5.00** |
| max directional residual | **10 shares** |
| max total exposure | **$5.00** |
| max concurrent markets | **1** |
| daily realized-loss halt | **$5.00** |
| stale-action cutoff | **30 s** from our receipt timestamp |
| price band | **none** — a band is a behavioural preference, not account protection |

Every refusal names the limit it was and is filed against `SAFETY_OVERLAY`,
never against L5 or the venue.

## 6. Exact maximum possible probe loss

**$5.00 of principal, plus fees.**

Every share bought can settle worthless, so committed dollars are the
exposure, and total exposure is capped at $5.00 in one market. Worst-case
taker fees on that notional are bounded by roughly **$0.35** (Θ·C·p(1−p) is
maximised at p = 0.5; $5 at $0.50 is 10 contracts → $0.1738, and the cap
cannot fund more than ~$0.35 of fees across re-entry). Settlement cannot go
below zero and no leverage or borrowing exists on this venue.

**Bounded worst case: ≈ $5.35.**

## 7. Restart, idempotency and deadman protections

- **Idempotency is derived, not generated**: client order id = hash(action key + attempt). A crash between submit and save, replayed, yields a byte-identical id and the venue rejects the duplicate. Tested through our store and with the store wiped.
- **The venue is authoritative**: on restart, positions are replaced by the venue's and the drift is reported, never averaged or assumed.
- **Atomic inventory writes**: tmp + replace, so there is never a torn file.
- **Deadman**: cancels everything believed live; safe to call when nothing is armed.
- **Stale-action cutoff**: 30 s, so a replay after downtime cannot price against a book that has moved.

## 8. Proof the intended PMUS account and market are addressed — **NOT SATISFIED**

I cannot provide this. The execution engine holds no credential and has no
transport, so there is nothing in it that identifies an account. The
authenticated credentials live only on the Railway recorder service and are
not readable from here, and I have not requested them.

**This requirement must be met before authorization, not after.** The
acceptance step should be an authenticated read-only preflight, run with the
transport that would actually be used, that prints the account identifier and
the exact market slug and matches both against the intended values — with the
first live order refused until that check passes. That preflight cannot be
written honestly until a transport exists, and no transport exists.

## 9. Proof shadow/observer services remain mutation-free

Observer `l5-pmus-observer`, heartbeat 2026-09-23T21:08:28Z:
`mutations_sent: 0`, `order_path: false`, `feed_errors: 0`,
`poll_exceptions: 0`, uptime 6958 s, `hydrated_at_boot: 461`.

The observer image is stdlib-only with a ten-file COPY set walked from its
import closure; no executor, venue client, credential or order path is copied,
asserted by test. `gemini-l5-faithful` reports `mutations_sent: 0` on both
cohorts and `P10_HB degraded: false`.

## What I recommend

Do not authorize yet. Two things are missing and neither is cosmetic: **zero
prospective out-of-sample windows**, and **no account-identity proof**. The
engineering is ready; the evidence and the safety proof are not.
