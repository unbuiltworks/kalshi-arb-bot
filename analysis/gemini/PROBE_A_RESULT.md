# Probe A — BTC15m q=1 CAP4, 2 windows (2026-09-23)

Authorization `OWNER-2026-09-23-GEMINI-Q1-AB-1`. Spec `btc15m_q1_cap4_w60_880_c2`
/ `ba64b54c8711166b9515007e6835e8190dc8bfddfbae2a1ba72c4e7798cefcb4` (frozen,
unchanged). Run 35817076822, armed step 04:17:05–04:47:37Z.

## Provenance, and why this file exists

The runner's own artifact `analysis/gemini/cohort_run_A_20260923T041705Z.json`
WAS NEVER COMMITTED. The evidence-commit guard admits
`(cohort_run|cohort_window|cohort_wire|static_readiness)_[0-9TZ]+\.json`; the
`--out` name carries an `A_` infix, so the guard refused the whole staged set
and the runner was reclaimed with the file on it. The per-window ledger,
`cohort_live_status.json` and the latch's final state went with it.

Everything below is therefore reconstructed from TWO surviving sources:

1. the armed step's stdout, captured in the job log (summary lines only);
2. `analysis/gemini/fill_forensics_20260923T050022Z.json` — RAW venue order
   rows for 04:10–05:05Z, read back from the account by a later read-only run.

Source 2 is venue-authoritative and is stronger evidence than the lost
artifact. Where the two disagree, that is recorded as an open discrepancy
below rather than resolved by preference.

## Terminal state (source 1)

    state      STOPPED_WINDOWS_EXHAUSTED
    why        the authorized 2 window(s) have been observed
    windows    observed 2 / 2, traded 2
    loss       0 of 10.00  ECONOMIC, settlement-inclusive, SETTLED windows only
               turnover 2.19  payout 0  fees 0  rebates 0  implied net 0
    drawdown   0 of 5.00
    REALIZED   None
    outcome    AUTHORIZED STOP

A stopped on its own window bound. No risk stop fired.

## Orders and fills (source 2, n = 16 order rows)

All orders: `limit`, `maker-or-cancel`, `side: buy`, `quantity: 1`.

### W01 — GEMI-BTC15M2609230445-UP (open 04:30:00, expiry 04:45:00)

| created (Z) | outcome | price | status | filled |
|---|---|---|---|---|
| 04:30:37.180 | yes | 0.44 | cancelled | 0 |
| 04:30:37.269 | no  | 0.47 | **filled** | 1 |
| 04:31:01.885 | no  | 0.37 | **filled** | 1 |
| 04:31:14.070 | yes | 0.57 | cancelled | 0 |
| 04:31:42.828 | no  | 0.28 | **filled** | 1 |
| 04:31:51.892 | yes | 0.60 | cancelled | 0 |
| 04:32:34.353 | yes | 0.67 | cancelled | 0 |
| 04:33:03.790 | no  | 0.19 | cancelled | 0 |

8 orders, 3 fills, all NO. Filled notional 1.12. Order flow spans 2m37s.
Resolution: `resolutionSide: yes`, `resolvedAt 04:45:06.820Z`, settled.

### W02 — GEMI-BTC15M2609230500-UP (open 04:45:00, expiry 05:00:00)

| created (Z) | outcome | price | status | filled |
|---|---|---|---|---|
| 04:45:30.119 | yes | 0.26 | cancelled | 0 |
| 04:45:30.211 | no  | 0.57 | **filled** | 1 |
| 04:46:10.280 | yes | 0.41 | cancelled | 0 |
| 04:46:10.345 | no  | 0.50 | cancelled | 0 |
| 04:46:46.249 | yes | 0.42 | cancelled | 0 |
| 04:46:46.374 | no  | 0.50 | **filled** | 1 |
| 04:47:11.771 | no  | 0.38 | cancelled | 0 |
| 04:47:24.872 | yes | 0.54 | cancelled | 0 |

8 orders, 2 fills, both NO. Filled notional 1.07. Order flow spans 2m02s.
Resolution not present in the 05:00:22Z read (`contractStatus: Active`).

### Aggregate

| metric | numerator | denominator | value | unit |
|---|---|---|---|---|
| orders submitted | — | — | 16 | order |
| orders filled | 5 | 16 | 31.2% | fill / order |
| fills per window | 5 | 2 | 2.50 | fill / window |
| NO-side fill rate | 5 | 8 | 62.5% | fill / order |
| YES-side fill rate | 0 | 8 | 0% | fill / order |
| filled notional | — | — | 2.19 | usd |

Filled notional 2.19 reproduces the runner's independently reported
`turnover 2.19` exactly. Two sources, one figure.

## The finding: fills were entirely one-sided

`both_outcomes_filled: false` on BOTH contracts; `outcomes_FILLED: ["NO"]` on
both. Every one of the 8 YES orders was cancelled unfilled; 5 of 8 NO orders
filled.

Fisher exact, one-sided, on 5/8 versus 0/8 with 16 orders: p = 0.0128
(C(8,5)/C(16,5) = 56/4368). Caveat that binds this number: the 16 orders are
not independent draws — they are sequential quotes in two correlated markets
by one controller — so 0.0128 is a descriptive figure, not a clean inferential
one.

Consequences, stated exactly:

- **matched inventory: none.** No pair was ever completed.
- **pair cost: undefined.** There is no pair to cost. Quoting a pair cost here
  would be inventing one.
- **residual: entirely one-sided.** W01 3x NO, W02 2x NO, no YES leg anywhere.
- **EXTEND/COMPLETE trajectory:** OPEN -> EXTEND -> EXTEND, and COMPLETE never
  happened in either window. The opposite leg was quoted eight times and
  filled zero times.

The YES quotes were priced where a pair would have been good: W01's first pair
attempt was NO 0.47 filled against YES 0.44 resting, a 0.91 combined cost,
inside the <= 0.96 spring-discipline target. The economics were not the
constraint. The opposite leg simply never filled.

This is the preregistered H2 (passive-placement starvation) appearing live on
BTC15m. It motivates Probe B's `maker_only_improve` placement hypothesis; it
does not substitute for it.

## CAP counterfactual

Maximum fills reached in any window: 3 (W01). CAP4 binds at 4.

**CAP4 did not bind and could not have bound.** This is another observation at
inventory depth below the cap, and it does not license any statement about
CAP4's safety. The standing label holds: UNTESTED AT RELEVANT INVENTORY DEPTH.
CAP3 would have bound on W01's third fill — that is a reachability note, not a
measured effect.

## Why order flow stopped after ~2 minutes

Both windows ceased placing orders roughly two minutes in, far inside the
authorized [60, 880] band, which permits ~13 minutes of work. Eight orders in
each. The 12-action per-window ledger is the obvious candidate for the binding
constraint rather than the time band.

**This is not established.** The ledger file that would settle it was lost with
the failed commit, and 8 orders does not map to 12 actions without assuming how
cancels and replaces are counted. Recorded as an open question.

## Economics — settlement-implied, NOT realized

W01 resolved `yes`. All three filled contracts were NO. Settlement-implied
outcome: the three contracts pay 0 against a 1.12 cost, i.e. **-1.12
settlement-implied, pre-fee, pre-rebate**.

W02's resolution was not in the 05:00:22Z read; its 2 NO contracts cost 1.07.

Nothing above is REALIZED. No attributable cash movement has been read back.
The venue position representation remains UNRESOLVED, so none of this is
reported as Gemini venue state.

Fees: the raw rows carry no fee field. The runner reported `fees 0`. Unverified.
Rebates: none credited, none assumed, and none enters any selection figure.

### OPEN DISCREPANCY — highest-priority follow-up

The runner reported `payout 0 fees 0 implied net 0` and `loss 0 of 10.00`,
while W01's raw rows plus its `resolutionSide: yes` imply -1.12. W01 resolved
at 04:45:06, before the runner's 04:47:37 exit, and the loop settles what has
settled before binding again — so W01 plausibly should have been counted.

I cannot reconcile these without the lost artifact, and I am not choosing
between them. This matters beyond bookkeeping: the economic-loss stop reading
0 when settlement-implied is -1.12 means the stop may not be measuring what
the authorization assumed it measures.

## Venue state after A — clean

- `fill_forensics` `positions_now`: `rows: []` at 05:00:22Z.
- `static_readiness_20260923T050021Z.json`: `n_rows_on_account: 0`, status 200,
  `mutations_sent: 0`, verdict PASS, `why_not: []`.
- All 11 unfilled orders carry `status: cancelled` with a `cancelledAt`; the
  last is 04:47:32.504Z. Zero working orders survived the run.
- No order rows exist in 04:10–05:05Z outside the 16 above. No stray activity.

## Probe B is structurally blocked under this authorization

`src/gemini_execution/cohort_runner.py:_consume_cohort_latch` enforces ONE
AUTHORIZATION, ONE COHORT. `latch.consumed()` keys on the authorization id
alone:

    def consumed(auth_id, directory=DEFAULT_DIR) -> bool:
        return os.path.exists(_path(directory, auth_id))

Probe A spent the latch at 04:17:08Z. It is committed at c4c10fc as
`analysis/gemini/canary_latch/latch_OWNER-2026-09-23-GEMINI-Q1-AB-1.json`, so
a fresh checkout sees it.

Probe B is a DIFFERENT cohort (`COHORT-2026-09-23-BTC5M-AB-B-W01..W04`) under
the SAME authorization id. On dispatch it would take the consumed branch with
its own fresh state, `windows_observed = 0`, and hit:

    if observed <= 0 or remaining <= 0:
        return {"ok": False, ...
                "why": "authorization %s is already consumed and its cohort
                        has %d of %d windows observed; this is reconciliation
                        work, never a re-run"}

The caller sets `state = STOPPED_NO_LATCH` and returns BEFORE any mutation.
Probe B would place nothing.

Neither readiness check could have caught this: readiness passes
`readiness=True`, and `_consume_cohort_latch` is called only under
`if not readiness:`. The latch path is never exercised by a readiness run, and
readiness B ran at 04:02Z, before A spent the latch. This was a static
reasoning miss, not a gate failure.

Running B requires a SECOND authorization identifier. That is an owner value
and is not minted here.

## Post-probe engineering items

1. The evidence-commit allowlist does not admit `cohort_run_A_*.json` or
   `cohort_run_B_*.json`. It cost this run its primary artifact.
2. The loss-accounting discrepancy above.
3. The slow historical-ledger reconciliation scan (~6m18s of every run).
4. Whether the 12-action ledger, not the time band, bounds a BTC15m window.
