# CP1 three-market live probe — COMPLETE, zero fills

**Authorization:** `OWNER-2026-09-16-CP1-CONTINUOUS-PROBE`, consumed in full.
**Outcome:** the probe ran its entire authorized envelope and stopped itself at
the boundary. No money moved.

## ARMED → SUBMITTED → FILLED → SETTLED → REALIZED

| Label | Result |
|---|---|
| **ARMED** | 22:14:33Z, deployment `9bd90901`, commit `84974f38ad7f`, shell `CP1/cap5.00/residual3/conc1/maker-only/fee-safe0.98/rest30s/action0.10` |
| **SUBMITTED** | 13 post-only maker orders across 3 markets (6 + 6 + 1) |
| **FILLED** | **0** |
| **SETTLED** | nothing from this probe (the 2 settlements in the campaign report are pre-existing markets being reconciled) |
| **REALIZED** | **NONE.** `realized_pnl: null`, `realized_confirmed_by_venue: false`, `$0.00` moved |

Final inventory: `up 0, dn 0, matched 0, residual 0, spent 0, unmatched_usd 0.0000`.

## The three markets

| # | Slug | Orders | Fills |
|---|---|---|---|
| 1 | `aec-dota2-yes-tm-2026-09-16` (21:13Z run) | 6 @ qty 0.22 | 0 |
| 2 | `atc-lib-lqu-pal-2026-09-16-pal` (soccer) | 6 @ qty 0.17, YES $0.27 | 0 |
| 3 | `aec-county-nor-der-2026-09-15` | 1 @ qty 0.19, $0.37 | 0 |

At 22:18:35Z the campaign selected a **fourth** market
(`aec-setkamemd-plevit-uzuale-2026-09-16`) and the wire guard refused:
`MandateRefusal:max_total_markets_exhausted`, `markets_touched 3`, `max 3`.
`CAMPAIGN_STOP:HALT` at 22:18:45Z; the process exited and did not restart
(`restartPolicyType: NEVER`).

**Nothing came near any other limit.** Per-market spend `$0.00` of `$5.00`,
campaign `$0.00` of `$15.00`, residual 0 of 3 units, realized `$0.00` against
the `-$6.00` stop.

## What this run proves

**The concurrency fix works in production.** The campaign advanced across three
markets. Before the fix it could not leave the first one: a zero-fill market
never settles, so it held `max_concurrent_markets=1` permanently. The fix let
the concurrency slot release while `markets_touched` kept its claim — which is
exactly why the run ended at 3 markets rather than scanning indefinitely. Both
halves of the invariant were exercised by the same run.

**The `CAMPAIGN_STOP:HALT` label is now visible in its correct form.** The
campaign genuinely finished its authorization, and the status still reads
`HALT`. That is the imprecision reported in
`FINDING_REFUSAL_CLASSIFICATION_2026-09-16.md` — the behaviour is right
(stopping rather than touching a fourth market) and only the name is wrong.
Still not changed, for the reason given there.

**The authorized sizing reached the wire correctly every time.** qty 0.22 at a
~$0.45 touch, 0.17 at $0.27, 0.19 at $0.37, 0.17 at $0.41. Where the first leg
was cheap, the `$0.125` per-leg bound bound on the **more expensive complement
leg** (0.125/0.71 = 0.176 → 0.17), keeping pair completion inside the per-leg
limit at the 0.98 fee-safe ceiling.

## Zero fills is the result, not a fault

Thirteen post-only orders, roughly 30 seconds of rest each, no fills. That is
information about queue position and venue liquidity at this size, not a
controller defect. The owner's instruction was explicit: do not force fills and
do not modify the controller because the first orders fail to fill. Nothing was
changed.

The fidelity gate's `size` row reads `median_executed_over_l5_p50: 0.002` — our
executed size is 0.2% of L5's median. That is the declared safety override, and
the ledger labels it as such (`"owner size cap $5.00/market + venue minimum;
declared safety override, not L5 behavior"`), not a model deficiency. The gate
reports `GATE_FEASIBILITY_CONFLICT`: *"the frozen ACTED thresholds are
unreachable under the current safety shell — gate and caps untouched."* That is
the honest reading, and neither the gate nor the caps were adjusted to improve
it.

## Flat, with one precise caveat

`may_assert_flat_all: false`, `contradictions: 0`, `recovered_qty: []`,
`ledger_qty: 0`. The ledger says flat. The venue returned **no quantity to
reconcile against** (`"no venue quantity to reconcile against"`) because there
were no fills, so the code declines to call it *venue-verified* flat. That is
the accounting discipline working as intended — an unknown is reported as
unknown and never as a zero. There is no evidence of any position.

## Why HALT was re-committed

The campaign will not auto-restart, but the serving image carried no HALT and
the mandate stays valid until `2026-09-17T20:36:35Z`. A redeploy or restart
could re-quote on an already-touched market. Standing discipline is that a
money-moving bounded session does not automatically repeat after a completed
probe, so the halt closes that door. Halting is always the fail-closed
direction and needs no new authorization.

**Re-arming does.** The three-market allowance is spent; reusing the mandate
would be a second probe under a consumed authorization, not a reuse.
