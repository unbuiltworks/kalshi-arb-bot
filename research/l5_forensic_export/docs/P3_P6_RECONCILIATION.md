# P3/P6 reconciliation — and why I did NOT make the authorized change

**Nothing was modified. No service touched, no deploy triggered, no order
placed. Trading remains disarmed.**

---

## The authorized change is already implemented and deployed

You authorized persisting the two depth fields "already computed in
`pmus_ws_evidence_recorder.py`". I verified the deployment before touching
anything, and the premise has moved on:

| check | finding |
|---|---|
| deployed branch | **`feature/l5-transfer-backtest`** (not `main`) |
| deployed commit | `ddc2195cb83db` |
| **running entrypoint** | **`src.pmus_ws_evidence_recorder_enriched`** — not the base module named in the authorization |
| latest deployment | `ed464f2d` SUCCESS, 2026-10-02T17:12Z |
| volume | `/state/pmus-ws`, 50 GB, live |
| auto-deploy | **watchPatterns include both recorder files** — any push to that branch redeploys and restarts the collector |

**The recorder never dropped the fields.** It computes, stores and serves
`bid_qty`/`ask_qty` (`pmus_ws_evidence_recorder.py:273–298, 450–451`) and
prints them in `PMUS_WS_STATUS`. The drop was in the *consumer* —
`_recorder_book` in `src/pmus_spring_l5_session.py:2488`.

**And that consumer was already fixed.** On the deployed branch there is a
block dated 2026-09-13 whose comment states the problem in exactly the
terms of your authorization — *"this row ALREADY carries bid_qty/ask_qty …
and this function has been discarding them, which is why 18.5% of the
pre-registered cross corpus stopped at 'observed depth not recorded'"* — and
it now calls `xshadow.observational_capture()`
(`src/pmus_cross_shadow_live.py:408`), which keeps, via an allowlist:

* `executable_depth`, `executable_depth_status`, `executable_depth_field`
* `status_utc`, `probe_receipt_utc`, `probe_transport_delta_s`
* `age_at_probe_receipt_s`, alongside the untouched `authenticated_age_s`

That is **both** the depth and the timestamps, additively, with every prior
key preserved and fail-soft behaviour. There is nothing left to add.

## Two further reasons not to push

**1. A concurrent session is deploying to this code right now.** Service
`bc7a1f5b` has had **eleven deployments today**, the latest at
**2026-10-09T14:55Z**, all from branch
`claude/august-l5-forensic-replay-788cml`, all authored by
`session_01UzBdAD7nfP8SBPk1bB3uDs`. That branch's HEAD moved to `7803ba82`
at **15:08Z**, minutes before this check. Pushing my own change into the
same files would collide with live work and could restart a service
mid-cycle.

**2. The change would be a no-op with a non-zero cost.** Its only effect
would be a redeploy of `pmus-ws-evidence-recorder`, i.e. an interruption of
the collection you asked me to keep running.

**If you still want a change made, the thing actually missing is not depth.**
It is that `pmus-spring-l5-probe` has no persistent volume, so the probe's
captured observations are not durable. That is a service-config change, not
a code change, and I have not made it.

---

## 1. The two P3/P6 studies — they are not the same labels

**I had this wrong in my previous report and am correcting it.** I
identified P3/P6 as section labels in the cross-shadow pre-registration.
There are two distinct studies and the labels mean different things in each.

### Study A — cross-shadow economic decomposition

| | |
|---|---|
| **what P3/P6 are** | *section* labels: P3 = economic decomposition, P6 = L5 fidelity |
| **policy definition** | cross the complement **as a taker** at the instant of a real production COMPLETE decision, at pair thresholds 0.97/0.98/0.99/1.00 × delays T0/T10/T30/T60 (16 cells) |
| **source data** | **260 real production COMPLETE decisions, 589 order rows**, from `pmus-spring-l5-probe`, 2026-09 |
| **fill assumption** | **STRICT executability.** A cross counts only with a fresh authenticated quote (≤5 s) *and* recorded observed ask depth. Missing evidence → `UNKNOWN_EXECUTABILITY`, never a fill |
| **result** | **0 supported crosses in all 16 cells** |
| **report path** | `analysis/l5/cross_shadow/RESULT.md`, `p4_p5_p6.json`, spec `PREREGISTRATION.md` (sha256 `7e31c447…`) |

Stops, first reason only: **56.2% no authenticated quote age recorded,
18.5% observed depth not recorded, 25.0% quote stale (5.1 s–826 s).**

### Study B — six-policy pair-cost replay

| | |
|---|---|
| **what P3/P6 are** | *policy* names: **P3 = BOTH_SIDES_RESTING**, **P6 = INVENTORY_AWARE** |
| **policy definition** | P3 quotes both sides from the start, independently. P6 completes whatever fits the pair budget, continuously |
| **source data** | the clone-shadow service's **own observation tape — 222 BTC up/down markets, 7,630 observations**, cycle 1620, 2026-10-09 14:26Z, commit `b8ce4b40d` |
| **fill assumption** | **queue-cleared simulation.** A touch without volume never fills; volume inside the queue never fills; only the excess over the queue **at placement** is ours; a missing ladder is never an empty queue |
| **result** | **P6 21.1% completion** at median pair cost **0.940**; **P3 50.0%** at **0.990** |
| **report path** | `analysis/l5/clone/PAIR_POLICY_SENSITIVITY.md` |

### Why one reported 21%/50% and the other zero — three independent reasons

**(a) They ask different questions.** Study A asks whether a *single taker
cross* was executable *at one instant*. Study B asks how often a *resting
policy eventually completes* across a whole window — many chances, not one.

**(b) They apply opposite evidence standards, and this is the decisive
reason.** Study A refuses to count a fill it cannot *prove* was executable:
**74.7% of its stops are literally "the evidence does not exist"**
(56.2% + 18.5%). It never reached a price test. Study B *simulates* fills
from a queue model against a tape that **does** carry ladders, so it never
meets the missing-evidence problem at all.

**(c) They are different corpora from different services in different
months.** Study A: production order attempts from the spring probe, 2026-09.
Study B: an observation tape from the clone-shadow service, 2026-10-09.
Different schemas, different periods, no overlap.

**So the two results do not contradict each other.** Study A's zero is a
statement about **instrumentation**, not about the market. Study B's
21%/50% is a statement about **simulated queue-cleared completion**. Only
Study B speaks to whether PMUS can form matched inventory, and its answer
is a qualified yes — see below.

---

## 2–4, 6. Already answered, by the concurrent session, today

I am not duplicating live work. What that session has already shipped:

* **The strict decomposition rerun (item 2)** — Study B separates
  unavailable evidence from verified non-fills by construction: a touch
  without volume is a verified non-fill; a missing ladder is explicitly
  *not* an empty queue.
* **Queue and flow measurement (item 3)** — queue-ahead excess at placement,
  completion rate per policy, time-at-risk (median 614 s for P6, 448 s for
  P3), and **adverse selection at t+30 (−0.100 for P6, −0.090 for P3, −0.030
  for the immediate taker)**.
* **Settlement reconstruction (item 6)** — commit `3a5cfd6ad` (14:55Z today)
  adds `infer_settlement` and full-inventory P&L, and documents that **there
  is no BRTI history in the repository**: the only candidates are a Coinbase
  proxy self-labelled `PROXY_NOT_SETTLEMENT_INDEX` (2026-08-20..25) and
  Kaiko-RFR recordings marked `Z_STATUS: NOT_IDENTIFIED`. Neither is BRTI,
  neither covers the replay windows. So settlement is inferred from the
  market's own terminal book and labelled
  `SETTLEMENT_INFERRED_FROM_TERMINAL_BOOK`, never venue-confirmed.

**Item 4 — PMUS vs L5 matched formation, consistent definitions:**

| | L5 (Polymarket, observed fills) | PMUS (replay, Study B) |
|---|---|---|
| completion / matched formation | matched pairs form in **76.1%** of windows | **P6 21.1%**, **P3 50.0%** of entries |
| achieved pair cost | median **0.980** | **P6 0.940**, **P3 0.990** |
| residual left open | 99.2% of windows end with a residual | **P6 79%**, P3 50% |
| net on matched legs | **+1.43¢/pair** locked | **P6 +1.74¢/entry**, P3 +0.50¢ |

**This materially revises my previous claim.** I reported that PMUS "never
forms matched inventory — zero class A at every cap". That was the
*residual-cap* study, which tested whether *extra same-side extends* later
convert — and zero did. It is **not** the same question as whether a
*both-sides-resting* policy can complete a pair, and Study B shows it can,
at 21–50%, with pair costs at or below L5's. **The earlier "PMUS cannot form
matched inventory" framing was too strong and is withdrawn.**

## 5. The one-sided residual stop stays a candidate risk control

Unchanged and explicitly **not** a proven profitable strategy: gross OOS
delta +0.408 [−0.115, 0.916], CI spanning zero; the case rests on fee
robustness and variance reduction. Study B reinforces it from the other
side — its best policies leave **50–79%** of entries holding a residual,
which is precisely the exposure a one-sided stop bounds.

## 7. Minimum additional evidence to separate *no flow* from *bad placement*

Three fields, all already computed somewhere in the stack:

1. **Opposite-side flow volume while our quote rested** — prints on the
   complement at or through our price, between placement and
   cancel/expiry. **No flow ⇒ no market. Flow we did not get ⇒ placement or
   queue.** This single field separates the two hypotheses.
2. **Queue-ahead at placement *and* at each print** — Study B has it at
   placement only. Without the second, "someone traded ahead of us" is
   indistinguishable from "we were never near the front".
3. **A venue-confirmed resolution for expired crypto windows.** The closed
   market sweep finds **0 of 18** held markets and `/v1/markets/{slug}`
   returns 404, so every residual P&L is currently inferred, not confirmed.

Items 1 and 2 need no new service — they are additional fields on a tape
already being written. Item 3 needs a venue endpoint that does not appear to
exist on the public gateway, and is the one genuine external blocker.

---

## Recommended next action

**Coordinate with the concurrent session before anything else.** It is
actively deploying to the same branch and has already delivered items 2, 3
and 6 in better form than I would have. The risk now is not missing
evidence — it is two sessions working the same files.

The highest-value remaining measurement is **opposite-side flow while
resting** (item 7.1), because it is the one field that distinguishes "PMUS
has no flow" from "we place badly", and that distinction decides whether any
bounded live test is worth running.
