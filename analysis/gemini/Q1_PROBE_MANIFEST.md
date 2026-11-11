# Deliverable A — bounded q=1 live-probe manifest — **DRAFT, NOT AUTHORIZED**

**Status: STAGED, NOT ISSUED.** Nothing here arms anything. The system is
disarmed as it stands and stays disarmed: `AUTHORIZED_BUNDLE is None` and
`arming.EXPECTED_AUTH_ID == "NO-AUTHORIZATION-IN-FORCE"`, an identifier no
environment value matches, so the transport cannot arm even if every flag were
set by accident.

This file exists so that the half of deliverable A which does **not** depend on
the decoder gate or the evaluation — the bounds, the arming procedure, the
rollback — is written and reviewable in advance. The half that does depend on
them is marked `PENDING` and stays empty until the gate passes and the single
pre-rebate evaluation has run. **If the gate fails, this file is deleted
unused and deliverable B is issued instead.**

## Preconditions — every one must hold before this manifest is issued at all

| # | precondition | state |
|---|---|---|
| 1 | decoder gate G1–G5 all PASS (`p10h_gate.py BTC`, exit 0) | **PENDING** |
| 2 | decoder frozen by content hash, recorded below | PENDING |
| 3 | BTC5m/BTC15m reconstructed with the frozen decoder | PENDING |
| 4 | ONE pre-rebate evaluation on causally available information, positive | PENDING |
| 5 | that evaluation's accounting independently recomputed, agreeing | PENDING |
| 6 | rebate economics added **afterwards**, on their own line, never in selection | PENDING |
| 7 | owner approval, in writing, against the exact SHAs below | **NOT REQUESTED** |

Precondition 4 is a stop, not a hurdle: three independent pre-rebate tests have
already come back negative on this lane, and a fourth negative is deliverable B.

## PENDING — everything the evaluation decides

* frozen decoder SHA — PENDING
* `bundle_sha` at authorization — currently
  `6fe90a9f24e9f72da43e5e346d561473c6b9c401762aae707c8bea4ce72e2e7d`, and it
  will change, because the decoder modules are **not** bundle members today
* the policy, stated as side/price/size/timing rules — PENDING
* the evidence: DEV and validation P&L, turnover, completion rate, terminal
  residual, maxDD, worst market and worst day — PENDING
* expected per-window economics, pre-rebate, with rebate on a separate line — PENDING

## NOT PENDING — the bounds, which are already defined in code

The envelope is `live_envelope.LIVE_Q1_BTC15M_CAP4`
(`GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_CAP4_V1`). These are not new numbers
written for this manifest; they are the owner-authorized 2026-09-21 envelope,
cited so the manifest cannot quietly widen them.

| bound | value |
|---|---|
| max dollars per market (`max_total_spend_usd`) | **$4.00** |
| max committed (filled + resting + in flight) | **$4.00** |
| residual cap (`max_residual_usd`) | **$4.00** |
| max residual / imbalance, units | **4** |
| action size | **1 contract** |
| max concurrent markets | **1** |
| max working orders | **2** |
| mutation / action cap per window (`max_actions`) | **12** |
| cohort | `GEMI-BTC15M` only |

Worst case is $4.00: four binary contracts cannot cost more, and a contract
that resolves against pays nothing back. **Daily realized loss stop: $4.00**,
i.e. one window. There is no second window without a fresh authorization, so
the daily stop and the per-window worst case are deliberately the same number.

## Cancel / deadman behaviour

* every mutation calls `arming.require()` first and raises `NotArmed` otherwise;
* the window emits `cancel_all` on withdrawal and at window end, so a resting
  order does not outlive the window that justified it;
* `order.cancel` sends the venue `orderId` (not the `clientOrderId` the venue
  rejected `-1013` during CONTINUOUS-2) — this seam is why CONTINUOUS-3's build
  hash differs;
* a crashed or completed money-moving session **does not relaunch itself**;
  read-only recorders self-recover, money-moving sessions fail closed.

## Arming procedure — three independent keys, all human-installed

1. a new `EXPECTED_AUTH_ID` is written into `arming.py` (a reviewable commit);
2. `AUTHORIZED_BUNDLE` is set to the `bundle_sha` printed at review time;
3. at run time the operator supplies `GEMINI_CANARY_LIVE=YES`,
   `GEMINI_CANARY_AUTH=<that identifier>` and `GEMINI_CANARY_AUTH_EXPIRES=<ISO
   instant>`. An absent expiry is refused: an authorization with no end is not
   a one-probe authorization.

An authorization is **spent on use** and never arms again; the identifier moves
rather than the latch being cleared. Four identifiers are already spent
(GEMINI-CANARY-1, CONTINUOUS-1, -2, -3).

## Rollback / disarm — exact procedure

Fastest first. Each step alone is sufficient; they are listed so that whichever
is reachable can be used.

1. **Unset the environment.** Clear `GEMINI_CANARY_LIVE` (or set it to anything
   other than `YES`). `armed()` is false on the next check and every mutation
   raises `NotArmed`. No deploy needed.
2. **Let it expire.** `GEMINI_CANARY_AUTH_EXPIRES` is mandatory, so an
   unattended probe disarms itself at a known instant.
3. **Burn the identifier.** Set `arming.EXPECTED_AUTH_ID` back to
   `"NO-AUTHORIZATION-IN-FORCE"` and redeploy. No environment value matches it,
   so the transport cannot arm at all.
4. **Break the bundle.** Set `AUTHORIZED_BUNDLE = None`. `authorized()` is
   false and the run refuses at preflight.
5. **Cancel what is resting.** The window's own `cancel_all` runs on withdrawal
   and at window end; if the process is gone, cancel by venue `orderId` from
   the outstanding ledger.

Post-probe, regardless of outcome: settle, audit against `/v1/mytrades` and the
cash ledger, and label realized P&L **settlement-only**. A fill is not realized,
a mark is not realized, and an estimated rebate is not realized.

## What this manifest still does not claim

The venue position representation is **UNRESOLVED**. Matched inventory, matched
pairs, pair cost, total spend and two-leg P&L are not reported as Gemini venue
state or Gemini economics anywhere in this probe's accounting. Nothing is
REALIZED without an attributable cash movement.
