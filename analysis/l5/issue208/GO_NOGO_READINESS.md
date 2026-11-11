# Go / no-go: what would make a capped live window unsafe or uninterpretable

Expedited readiness check. **This is not a request for authorization and
not an authorization.** It lists only the conditions that would make a
1–2-window live test unsafe, or make its result impossible to read.

Verdict: **CONDITIONAL GO on three blockers, all of which are deploy or
framing decisions rather than new research.** Nothing found makes a capped
maker-only probe *unsafe*. Three things would make it *uninterpretable*.

---

## 1. Safety: no blocker found. Every gate is in force, read live

From the running heartbeat on `3620e232f`, read-only:

| gate | live value | verdict |
|---|---|---|
| `submission_gate.blocked` | **true**, `SHADOW_ONLY_LOCK` | no order can reach the venue |
| `submission_gate.armed` | **false** | — |
| `halt_latch` | `CLEAR`, digest present, `kind: PRESENT` | readable, fails closed if not |
| `authorization_preflight.ok` | **false** on `shadow_lock`, `absolute_deadline`, `campaign_approval` | three independent blocks |
| `campaign.approval_state` | `approval_stale`, `permits_this_window: null` | — |
| `max_cash_deployed_usd` | `12`, `cash_policy: NO_RECYCLING` | the mandate holds |
| `max_worst_case_deficit_usd` | `1.00`, `worst_case_deficit_check: **ACTIVE**` | the check I added is running |
| `max_unmatched_contracts` | `5` | — |
| `day_realized` | `utc_day_realized_usd 0`, `halt_usd 5`, `room_usd 5`, `would_halt false` | — |
| `completion_reserve` | `submissions_per_obligation 2`, `may_open true` | capacity to close is reserved |
| cancellation | explicitly **not** gated by any of the above | available while blocked |

`auth_book`: `connected true`, `stall_reconnects 0`, `gap_suspected false`,
venue→receipt lag median **0.039 s** / p90 0.058 / max 0.1. The feed is fit
for a live window.

**To run a window the owner must clear three separate things** — shadow
lock, absolute deadline, campaign approval. None of them is clearable by
me and none is clearable by accident.

---

## 2. Blocker B1 — the calibration-eligibility fix is not deployed

The running image is `3620e232f`. The eligibility fix is `012b34017`.

So the **deployed recorder can still mark a record
`usable_for_calibration: true`** on future-dated queue evidence, on a
single observation, or after a failed durable write. Those are exactly the
three conditions the probe's own pre-flight gates test
(`precapture_ok`, `usable_for_calibration`).

A window spent on a recorder that can say "usable" when it is not is a
window that cannot answer the queue-position question it was spent on.

**Resolution:** deploy a commit containing it. Deploy, not research.

## 3. Blocker B2 — the deployed latency metric is wrong

Also fixed after `3620e232f`, also not deployed. The running image stamps
a fill detection on a **flat** market, so `detection_to_decision_s` reads
`n 256, median 451.744 s` while every decision in the same record says
`NO_INVENTORY`. That figure is time since the window's first tick.

If the probe is meant to measure first-leg-fill → second-leg-quote, the
deployed metric would report a number that is not that quantity. Same
deploy fixes it, and adds `detection_to_first_decision_s`, which is the
figure that actually answers the question.

## 4. Blocker B3 — cost-basis attestation is still accruing unknowns

This one is new and it is the most important of the three, because it
decides whether the probe's **settled P&L** means anything.

From the live heartbeat:

```
accounting.unknown_unattributed_realized      -88.42580000
accounting.verified_l5_realized                -3.410300
realized.verified_l5_status      UNPROVEN_LEGACY_ATTRIBUTION
```

and `post_baseline_settlements_not_attested` is **not only legacy**:

| market | pnl | provenance |
|---|--:|---|
| 2026-11-02-2300z | −1.68000000 | `UNKNOWN_COST_UPPER_BOUND` |
| 2026-11-04-1445z | −1.00000000 | `UNKNOWN_COST_UPPER_BOUND` |
| 2026-11-05-2215z | 0E-8 | `UNKNOWN_COST_UPPER_BOUND` |
| 2026-11-05-2300z | −0.400… | `UNKNOWN_COST_UPPER_BOUND` |

Markets have continued to settle with an **unknown cost basis priced at the
$1/share upper bound**, as recently as 2026-11-05. A leg lands there when
it arrives as a venue net position this executor never booked, so no order
of ours exists to ask about.

**If the probe's own market lands in `unknown_basis_keys`, its settled net
is an upper bound and not a measurement** — the probe would answer nothing
while having spent real money.

**Resolution, and it is a pre-registration rather than a code change:**

1. Score the probe on **its own market's settlement**, never on
   account-wide realized. The −$1.27 bridge line and the eight legacy
   `UNPROVEN_LEGACY_ATTRIBUTION` markets would otherwise contaminate it.
2. **Pre-register the attestation check**: after settlement, the probe's
   market must **not** appear in `realized.unknown_basis_keys` and must
   not carry `UNKNOWN_COST_UPPER_BOUND`. If it does, the probe is declared
   **uninterpretable** and not repeated until basis booking is fixed.
3. Record `orders_straddling_a_restart` with the result. A record spanning
   a restart is still evidence but may not be read as one continuous
   observation.

---

## 5. What I withheld from the probe deliberately

The provisional-side repair (`PROVISIONAL_PRICE_UNREADABLE`) is correct but
**changes when the controller opens**, and has never run in production. A
capped probe has one opening to spend, and at `ask 0.01` the forced side is
DOWN at own 0.99 — legal, gated downstream, and a poor use of that entry.

So it is behind `L5_PROVISIONAL_FORCED_SIDE`, **default OFF**, and the
deployed default is byte-identical to the behaviour already observed
running. It should be evaluated on its own, not inside a probe.

---

## 6. Framing: the one thing that would make the probe uninterpretable by construction

**A 1–2-window probe cannot measure expectancy, and must not be scored as
if it could.** The gap to profitable matched-pair execution is
**+0.0651/pair — 6.5 ticks**. Two windows of a one-contract maker probe
cannot distinguish that from zero.

What a probe **can** answer, and no replay can:

1. Do both legs of a simultaneous quote actually rest at the same time?
2. When one fills alone, what was our real queue position?
3. **What is the book age when the completion controller evaluates?** —
   still never measured in production, because no decision has yet reached
   the freshness check. With a real first-leg fill there is finally
   inventory and the check is finally evaluated. This is the probe's
   single most valuable output.
4. First-leg fill → second-leg quote, on a correct metric.

A pre-registered refusal, independent of economics: if the evaluations
show **p95 book age > 2.0 s**, the completion controller cannot be trusted
to act on the book, and that is a blocker on its own.

**The synthetic 0.4 s / 6.8 s figures are a test result, not a production
measurement** — the owner's correction, and it stands. We have **zero**
production measurements of book age at decision.

---

## 7. Summary

| | blocker | kind | resolution |
|---|---|---|---|
| B1 | eligibility fix not deployed | uninterpretable | deploy |
| B2 | latency metric wrong in the running image | uninterpretable | same deploy |
| B3 | cost basis still accruing `UNKNOWN_COST_UPPER_BOUND` | uninterpretable | pre-register per-market scoring + attestation check |
| — | safety gates | **none found** | all in force, three independent blocks |
| — | #209 rolled back | degrading, not a probe blocker | deploy a branch with #209 |
| — | expectancy | out of reach at n=1–2 | score executability, not profit |

No infrastructure created. No order placed. No limit changed. Production
disarmed throughout.
