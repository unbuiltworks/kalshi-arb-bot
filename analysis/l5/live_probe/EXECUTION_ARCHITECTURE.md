# Live execution architecture after the 2026-09-16 breach

The R1 canary was authorized for **one market at $0.05** and traded **11+
markets**, committing **$19.3295** to one of them. Nothing was bypassed and no
gate was defeated: the system did what it was built to do, and what it was
built to do was wrong in two places. This is what replaced them.

## The two failures, stated as design faults

**The campaign could rewrite its own limits.** The dual key only *arms*. Once
armed, the campaign followed its own stored ladder, and when market #1 settled
it loaded three owner approvals dated **2026-09-06** (`R2C`, `R2C2`, `R3L`) and
applied their rungs: per-market $0.05 → **$7.50**, daily stop −$1.00 →
**−$15.00**. Authorization to *start* was being read as authorization to
*continue*, on someone else's ten-day-old decision.

**The spend counter was an allow-list of stage names.**
`counts_against_market_cap()` admitted `spring_open*` and `spring_extend*` and
excluded `spring_complete_*`. A COMPLETE on this venue is a **BUY of the
complement** — cash out. The counter read $7.4476 against its $7.50 cap while
real money-moving notional reached $19.3295. The `<= 1.00` pair invariant that
was supposed to bound completes bounds the **price per pair**, never the
**dollar volume**: 20.44 pairs at 0.9457 honours it perfectly.

## Four layers, each independently sufficient to stop a spend

```
  committed HALT file ──► shutdown_requested()  [before armed() is consulted]
                              │
  committed mandate ─────► load_mandate()       [absent = NO_MANDATE = no orders]
                              │
  every campaign pass ───► expiry + latch re-read  [stops itself, unattended]
                              │
  every order ───────────► GuardedTransport      [POST /v1/orders, at the wire]
```

### 1. The mandate is the authorization, and it is immutable

`src/pmus_live_mandate.py`. A frozen dataclass carrying `authorization_id`,
`authorized_at`, `service`, `strategy`, `strategy_version`, `max_markets`,
`max_total_markets`, `max_spend_per_market`, `max_total_spend`, `max_residual`,
`max_daily_realized_loss`, `expiry`.

**Every field is required.** A mandate missing one is not a weaker mandate, it
is not a mandate — because a limit that falls back to a default is precisely how
$0.05 became $7.50.

`frozen=True` is not decoration: the failure was a live limit being replaced at
runtime, so the object carrying the limit cannot accept a new value.

Nothing in the running system can enlarge or replace it. Continuation,
settlement, restart, resume, `advance_gate` and historical rung approvals all
run *underneath* it. A market beyond `max_total_markets` needs a new
`authorization_id`, which means a human.

### 2. Spending is defined by economic effect, never by a name

`order_cash_effect()` reads the **order body** and prices what it does:

- it undoes the venue's long-side wire convention, so a NO leg at 0.43 is
  charged **$0.43 × qty**, not the 0.57 that goes on the wire;
- a SELL is charged at its worst case, not treated as a refund;
- an action it does not recognise is charged at **full contract value**;
- an unpriceable body is **refused**, never estimated.

`counts_against_market_cap()` now returns `True` for every stage, including ones
nobody has invented yet. There is no list left to forget to extend.

### 3. Enforcement is on the wire

Every order in this codebase becomes `POST /v1/orders` — four call sites today
(`pmus_q1_executor`, `pmus_q1_executor_v4`, `pmus_micro_executor`,
`pmus_migration_close_live`). `GuardedTransport` wraps the transport, so a
retry, a resumed session, an acceptance probe or an executor written next year
**inherits** the envelope instead of having to remember it. That is the
difference that matters: the old rule had to be opted into, and one stage
forgot.

Admission takes an exclusive `flock`, re-reads consumption from the durable
ledger, decides, and writes its commitment **inside the same critical
section** — which is what makes a restart unable to forget and a race unable to
double-spend.

A commitment is charged **in full** until a venue terminal row proves a smaller
fill. So an ambiguous submission never frees budget, and a partial fill keeps
its filled portion charged forever; cancels and retries release only the
unfilled remainder of their own reservation.

### 4. Safety does not depend on anyone reading a message

On 2026-09-16 the only thing between a running campaign and eleven markets was a
20-minute chat reminder that stopped being re-armed at 08:04. Between then and
10:28 nothing was watching, and that is exactly when the $19.33 market traded.

So: **the mandate expires**, and the campaign loop re-reads it and the halt latch
on *every pass*, not once at boot. An unattended process stops itself when its
authorization runs out.

Monitoring now **detects and reports**. It is never what prevents. A test pins
that the guard makes its decision with no network, no scheduler and no sleep.

## Halt and authorization are files, and they are separate decisions

`analysis/l5/authorization/` is COPYed into the image.

| file | meaning | current state |
|---|---|---|
| `HALT` | present ⇒ `safe_shutdown`, before `armed()` is consulted | **present** |
| `live_mandate.json` | the authorization to spend; **absence is safe** | **absent** |

Removing `HALT` does not authorize trading; it only un-halts the process.
Orders additionally need a valid, unexpired mandate naming this service. Two
decisions, deliberately not sharing a switch.

**Why files and not Railway variables.** When the probe had to be stopped, the
only lever that worked was a Railway `startCommand` exporting
`PMUS_SPRING_SHUTDOWN` — the same class of runtime override that condemned the
legacy probe — because `set-variables` was not offered to the session and
`redeploy` had no snapshot to copy. A halt that depends on a platform API being
reachable is not a halt. A variable also has no diff and no history, and "how
many markets, how many dollars, until when" is exactly the decision that should
be reviewable afterwards.

That emergency `startCommand` is removed once this image is verified halting
from the file alone. It was a stop, not a configuration.

## What is tested

`tests/test_live_mandate_adversarial.py` — 21 tests replaying the incident:
authorized one market at $0.05 → market #1 settles → stale R2/R3 approvals exist
→ continuation attempted → **refused** → a COMPLETE after $0.042 spent gets only
the remaining authorized dollars → partial fills, retries and cancels cannot
replenish spent cash → a restart cannot reset consumption → 16 threads race a
$0.05 envelope and **exactly two** fit.

`tests/test_live_mandate_ledger_invariant.py` — the stronger invariant: for every
market, at every prefix of the replay, **accepted money-moving notional ≤
authorized envelope**, measured from the venue/order ledger by a *deliberate
second implementation* that never calls the code under test. A control test runs
the same replay under the pre-incident rule and asserts it **violates** the
invariant, so the check is known to have teeth rather than merely being green.

`tests/test_live_mandate_deployment.py` — the halt and the authorization are
durable artefacts; shutdown is decided before arming; expiry is re-read every
pass; enforcement lives in the executor.

## Proposed fresh authorization envelope — NOT ARMED

Offered for decision. Nothing is armed and no mandate file exists.

```json
{
  "authorization_id": "R1B-2026-09-17-01",
  "authorized_at": "<when you approve it>",
  "service": "l5-r1-probe",
  "strategy": "LANE_A_EXECUTION_MEASUREMENT_PROBE",
  "strategy_version": "V0",
  "max_markets": 1,
  "max_total_markets": 1,
  "max_spend_per_market": "0.05",
  "max_total_spend": "0.05",
  "max_residual": "0.06",
  "max_daily_realized_loss": "1.00",
  "expiry": "<authorized_at + 6h>"
}
```

**Why these numbers.** They are the envelope that was actually authorized on
2026-09-16 and never honoured, so re-running it is the only way to find out
whether the plumbing works under a mandate that binds. `max_total_markets: 1`
means the campaign physically stops after one market rather than reselecting.
`max_total_spend` equals `max_spend_per_market` because one market is the whole
authorization. `max_residual: 0.06` is the R1 residual cap expressed in worst-
case dollars on unmatched shares.

**The expiry is the new part.** Six hours is chosen so a single sports market
can open, fill and settle inside it, while an unattended process cannot outlive
the decision that authorized it — the property whose absence turned a missed
check-in into eleven markets. If it expires mid-market, held inventory is
preserved to settlement and no new order is admitted; nothing is liquidated.

**To arm it** two separate acts are needed, and both are commits: remove
`analysis/l5/authorization/HALT`, and add `live_mandate.json`. The dual key on
the service is unchanged and is no longer sufficient on its own.

Outstanding first: `aec-itfme-conbra-andpet-2026-09-16` is still
`PENDING_SETTLEMENT` with $19.3295 committed and 20.44/20.44 matched. Its
venue-confirmed settlement is recorded under the **`r1-probe` cohort** — the
ingest stamps `_cohort` per service, so it cannot merge into the legacy
−$44.4898 net. That result should land before any new authorization.
