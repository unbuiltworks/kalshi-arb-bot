# Campaign 8 — unchanged strategy, and why the replay's winner does not apply

## Track 1: ready-to-authorize campaign

Strategy, limits and execution path **unchanged** from Campaign 7
(SHA 202456d3). Both candidate flags stay off: `L5_TS_TWO_ORDER_PAIR` and
`L5_DEFENSIVE_COMPLETION`.

| field | value |
|---|---|
| series / windows | BTC15m, 8 consecutive |
| max exposure / cash envelope | $20 / $12 |
| daily / cumulative loss halt | $5 / $20 |
| residual budget | $3 |
| resting orders | 1 |
| execution | maker-only, post-only |
| pair ceiling / completion ceiling | 0.96 / 0.95 unchanged |
| OPEN / COMPLETE / REPLACE | 40 / 40 / 64 unchanged |
| **total-submission backstop** | **40 -> 72** |

### Why raising the backstop cannot widen the risk envelope

Campaign 7 ended at **36 of 40** with class budgets barely touched (OPEN 15,
COMPLETE 14, REPLACE 7). The backstop, not the strategy, would have stopped
the next window.

It is safe to raise because it is not a dollar control:

1. Its enforced purpose is completion-capacity reservation -- it refuses an
   order when "the flat backstop would not leave room for this order AND the
   obligation it creates". It reserves the ability to CLOSE, it does not
   bound exposure.
2. Every dollar bound is applied independently per order and each prices a
   resting order **as though it fills in full**: max_total_exposure_usd $20,
   max_cash_deployed_usd $12, max_usd_per_market $20, the $3 residual cap,
   and both loss halts. None of them scales with submission count.
3. 72 stays below the 144 sum of the three class budgets, preserving the
   design property that no two classes can both run to their ceiling.

Operational note: `max_total_submissions` is NOT in `SCOPE_FIELDS`, so it
cannot break a scope digest mid-campaign. It is graded by the runtime-config
verifier against the deployed `L5_LIVE_AUTH_MAX_SUBMISSIONS`, so the
variable must be set to 72 BEFORE the document is installed, per the
ordering rule that deadlocked Campaign 7.

Reporting: settled net, pairs and pair costs, defensive losses, stranded
inventory, adverse selection, refusals, and -- added -- the **repair rate**
defined below. No claim that any window count proves profitability.

## Track 2: the replay's winning treatment is a no-op for us

Run out of sample on L5's own fills (`scripts/l5_treatment_oos.py`, Sep 23
fit, Sep 24-25 held out, 521 market-days):

| policy | matched | match_pnl | residual_pnl | TOTAL | vs NATIVE |
|---|---|---|---|---|---|
| NATIVE (L5's own sequence) | 154,685 | +657.8 | **-4,587.6** | **-2,342.4** | - |
| RESIDUAL_CAP_k1 | 86,197 | +505.8 | -492.1 | +812.9 | +3,155.3 |
| RESIDUAL_AGE_120s | 79,838 | +617.3 | -358.8 | **+1,013.3** | +3,355.8 |
| COMPLEMENT_REPAIR | 65,742 | **+703.1** | -336.9 | +985.4 | +3,327.9 |

**L5's own action sequence, rated at our fee schedule, loses $2,342 over two
held-out days, and the entire loss is residual** -- matched P&L is positive.
Suppressing residual accumulation flips it positive. The mechanism is
confirmed at scale.

**But both winners only ever block a fill that DEEPENS an existing
imbalance, and Campaign 7 had none.** All 18 fills were either a new
position from flat or a repair:

    fills that deepen an existing imbalance: 0 of 18

So `COMPLEMENT_REPAIR` and `RESIDUAL_AGE` would have changed nothing. They
correct L5's extend-heavy behaviour; our engine already behaves as they
prescribe, because the $3 residual cap and the 0.96 ceiling already suppress
exactly what they suppress. **The headline +$3,355 does not transfer.**

### Our actual failure mode, measured

    9 positions opened from flat -> 5 repaired, 4 stranded = 56% repair rate

The four strandings opened at T+321, T+91, T+156 and T+496; two had over
500 s of runway and still never repaired, so runway is not the
discriminator. The cause is the complement becoming unaffordable after our
leg fills -- all four ceiling episodes priced 1.02-1.30, correctly refused.

**Our residual is a completion-fill problem, not an accumulation problem.**
No treatment in the existing replay addresses it, and I am not proposing one
without measured expectancy. The metric to move is the repair rate; Campaign
8 will record it so there is a baseline to improve against.

### Caveats on the replay, stated

Fill-for-fill counterfactual: a taken action fills as recorded and a
suppressed one does not happen, so suppression is free and taking is
guaranteed -- our orders may not fill. Incentives are MODELLED for the
treatments and real only for NATIVE; a modelled rebate is never realized.

---

# Authorization document and verification plan (for approval)

## 1. OOS replay accounting, reconciled

The columns compose exactly:

    match_pnl + residual_pnl = trading          (true for every row)
    trading   + incentives   = TOTAL

| policy | trading (hard) | incentive | basis | modelled share of TOTAL |
|---|---|---|---|---|
| NATIVE | -3,929.8 | +1,587.4 | **REAL** (Sep24 +1,023.5, Sep25 +563.8) | 0% |
| COMPLEMENT_REPAIR | +366.2 | +619.2 | MODELLED | 63% |
| RESIDUAL_AGE_120s | +258.5 | +754.8 | MODELLED | 74% |
| RESIDUAL_AGE_60s | +159.5 | +708.5 | MODELLED | 72% |
| RESIDUAL_CAP_k1 | +13.6 | +799.3 | MODELLED | 98% |
| RESIDUAL_AGE_300s | **-21.4** | +892.7 | MODELLED | >100% |

Only NATIVE's incentive is real -- the venue reports it as one daily lump
with no conditionId, so the treatments' share is modelled by scaling it
with the fraction of quantity still taken. **`RESIDUAL_AGE_300s` is
positive ONLY through a modelled rebate; its hard trading P&L is negative.**
Stripping incentives entirely, the treatments still beat NATIVE by
+$3,908 to +$4,296, so the mechanism survives -- but no treatment TOTAL may
be quoted as realized.

## 2. The 40 -> 72 backstop cannot bypass any limit, including after restart

- **Durable across restart.** `submissions_spent` globs
  `l5_auth_claim_*.lock` on the volume -- filesystem state, not memory -- so
  a redeploy cannot reset the count. It counts BOTH `sub1.` and `acc1.`
  claim kinds, so the budget binds whichever strategy chose the order, and
  an unreadable budget refuses.
- **Orthogonal to every dollar gate.** Raising the cap only moves when
  `SUBMISSION_BUDGET_EXHAUSTED` fires. Exposure $20, cash $12, per-market
  $20, residual $3, daily $5 and cumulative $20 are each evaluated per
  order and each prices a resting order as though it fills in full. None
  reads the submission count.
- **Class structure preserved.** 72 < 144, the sum of OPEN 40 + COMPLETE 40
  + REPLACE 64, so no two classes can both reach their ceiling.
- **Completion capacity still reserved.** The backstop's own refusal --
  "would not leave room for this order AND the obligation it creates" --
  continues to apply at the higher number with `completion_reserve 2`.

## 3. Exact Campaign 8 authorization

Mint parameters (nothing else changes):

    ASK_WINDOWS=8  ASK_EXPOSURE_USD=20  ASK_DAILY_LOSS_USD=5
    ASK_DURATION_S=14400
    ASK_OPEN=40  ASK_COMPLETE=40  ASK_REPLACE=64  ASK_TOTAL=72

Document scope: series `cpc-btc-updown-15m-`, strategy TWO_SIDED,
pair_cost_ceiling 0.96, max_live_orders 1, completion_reserve 2,
backstop_exempts_risk_reducing true, cancellations UNBUDGETED.
Flags remain OFF: `L5_TS_TWO_ORDER_PAIR`, `L5_DEFENSIVE_COMPLETION`.

## 4. Deployment verification plan, in order

1. Set variables BEFORE the document (the ordering rule that deadlocked
   Campaign 7): `L5_LIVE_AUTH_MAX_SUBMISSIONS=72`,
   `L5_LIVE_AUTH_MAX_WINDOWS=8`, `L5_LIVE_AUTH_NOT_AFTER=<document expiry>`.
2. Redeploy; confirm SHA `202456d3` and both flags read false in the live
   decision telemetry.
3. Confirm the runtime-config verifier grades the document's
   `submission_budgets` equal to the deployed variables.
4. Confirm watchdog present, halt latch CLEAR, venue state clean, inventory
   reconciled, loss headroom full, `unresolved_accounting` zero.
5. Install the document; confirm the claim's scope digest records
   max_windows 8 and the matching not_after, and `windows_spent` 0 of 8.
6. Arm only on explicit go-ahead, and only into a window with
   `subscribed_before_open: true`.

## 5. Preregistered settled metrics, fixed before the campaign runs

Primary: realized net P&L from the authoritative cash series.
Secondary, per window and pooled:

- matched contracts per window;
- **initial positions** -- fills that open from flat;
- **repair rate** = repaired / opened-from-flat (Campaign 7 baseline: 5/9 = 56%);
- **stranded positions**, count and cost basis;
- residual settlement losses, attributed per window by cash timing;
- and the diagnostic this analysis motivates: **queue_ahead at placement
  for every completion, and its ratio to the quantity needed**
  (Campaign 7: 600x-1,300x).

No window count will be claimed to prove profitability.

## 6. The four strandings: a profitable quote was placed every time

| window | held @ own | profitable completion placed | pair | queue ahead | need | ratio |
|---|---|---|---|---|---|---|
| 2000z | DOWN 0.2863 avg | UP 0.42, then 0.32 | 0.71, **0.61** | 1,805 / 1,867 | 1.85 | ~1,000x |
| 2015z | UP 0.37 | DOWN 0.55 | 0.92 | 2,518 | 2.18 | 1,155x |
| 2100z | UP 0.45 | DOWN 0.26, then 0.34 | **0.68**, 0.76 | 1,283 / 987 | 2.06 | ~600x |
| 2115z | UP 0.62 | DOWN 0.26 at T+554 | 0.88 | 2,425 | 1.85 | 1,311x |

**The earlier claim that the complement became unaffordable is withdrawn.**
Affordable complements were quoted in every case, several far below the
ceiling. They did not fill because we rest 600x-1,300x deeper than our own
size. Equally, these were NOT missed executable profitable fills: a
displayed price we sit a thousand times behind is not executable for us.

Two secondary observations, not yet proposals:

- **Requoting resets queue priority.** Position is frozen at placement and
  only prints advance it. In 2115z the engine held a 642-deep slot at own
  0.33 (pair 0.79, profitable) and requoted to 0.47 -- a worse price AND a
  worse queue position (2,179).
- **2115z's third position opened at T+496** and its only completion was
  placed at T+554, 46 s before the T+600 terminal.

Both point at completion PROBABILITY rather than completion price, which is
where the next economic improvement should be sought.
