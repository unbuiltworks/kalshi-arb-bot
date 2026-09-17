# L5 CURRENT PRIORITY

Operational north star, as of **2026-09-17**. Read with `L5_STATUS.md`, which
preserves evidence and history; this file states what we are doing now. Never
rewrite historical evidence to fit current priorities.

> **If you are a fresh session, read this first.** As of **2026-09-17T10:53Z
> the live executor is RUNNING and ARMED** under a current owner authorization.
> Anything below or in `L5_STATUS.md` describing it as HALTED, unauthorized, or
> blocked on an arming decision is **the superseded 2026-09-16 state**. Verify
> against Railway before acting on any of it — runtime evidence outranks these
> files. The old R1→R2→R3 rung/campaign program is still **retired**; do not
> resurrect it, and do not clear a latch or set a dual key to "unblock"
> anything.

## Destination

One continuously operating L5-style engine, not a sequence of experimental rungs:

**L5 observation → FIDELITY desired inventory → explicit ADAPTED bankroll/risk
scaling → Polymarket US sports execution → transport-level mandate → venue
reconciliation → settlement → realized accounting**, looping across many
eligible markets.

Stop expanding infrastructure unless it is required to reach that.

## P0 — Polymarket US sports

### Live money executor: RUNNING AND ARMED (2026-09-17)

`l5-r1-probe` (`6111a5d4-a2d9-4762-a057-31f02d07586f`), deployment
**`75c3cef1-6e45-4ba9-9e69-5f77de1387bf`**, serving SHA
**`2368fb6fe4c0eb71eda81467c514cb4b3799336c`** — verified from the runtime
`campaign_start` record, not from a build result. `mode: ARMED`.

**Authorization: `OWNER-2026-09-17-CP1-LIVE-SHORT-HORIZON-COHORT`**, authorized
2026-09-17T10:46:53Z, **expires 2026-09-17T22:46:53Z**.

```
cohort *      concurrency 1      total markets 10
$5.00/market  $15.00 campaign    residual 3      daily stop -$6.00
target $0.10  max leg $0.125     k 0.00619       maker/post-only
```

**LIVE_SHORT_HORIZON_SPORTS cohort.** Eligibility is fail-closed and generic:
an event must be present in the authoritative live feed, not ended, and carry a
progress token the existing rules genuinely recognise. Unknown progress, feed
unavailable, not started and ended are all refused. **`endDate` is never used as
a settlement horizon** — it is a fixed `gameStartTime + 14.00 d` contract expiry
(measured 18/18 live events) and carries no settlement information. No
per-sport duration is invented anywhere.

Cricket, esports and NPB/KBO are excluded because their progress evidence is
insufficient — **never because of their names**. They become eligible the day
the existing rules can read their tokens, with no authorization change.

Cohort refusals are **L5_ADAPTED** execution selection, counted via
`cohort_refused` / `cohort_refusal_reasons`, and are **never scored as model
WAIT**.

The safety architecture below is unchanged and still load-bearing: HALT is read
before `armed()`, an absent `live_mandate.json` still means `NO_MANDATE`, and
spending is still admitted at the **wire** by economic effect. What changed is
that a current mandate exists and HALT is absent — both reviewed commits.

### The 2026-09-16 authorization breach — happened, fixed, tested

Authorized for **one market at $0.05**; it traded **11+ markets** and committed
**$19.3295** to one. Two causes, both now structural failures rather than
judgement calls:

- the campaign chain continued on owner approvals dated **2026-09-06**
  (R2C/R2C2/R3L), lifting the per-market envelope $0.05 → $7.50;
- `counts_against_market_cap()` was an **allow-list of stage names** that
  excluded `spring_complete_*`, which on this venue is a BUY of the complement.

Fixed by `src/pmus_live_mandate.py`: an immutable per-authorization mandate
(`authorization_id`, `authorized_at`, `service`, `strategy`,
`strategy_version`, `max_markets`, `max_total_markets`, `max_spend_per_market`,
`max_total_spend`, `max_residual`, `max_daily_realized_loss`, `expiry` — all
required), enforced at the transport, with consumption read from the durable
ledger. Continuation, settlement, restart, resume, `advance_gate` and
historical rung approvals **cannot enlarge or replace it**. An approval must
postdate the mandate; undated is refused.

Evidence: `analysis/l5/live_probe/R1_CANARY_BREACH_2026-09-16.md`,
`analysis/l5/live_probe/EXECUTION_ARCHITECTURE.md`.
Tests: `tests/test_live_mandate_adversarial.py` (replays the breach step by
step), `tests/test_live_mandate_ledger_invariant.py` (accepted notional ≤
authorized envelope at every prefix, measured independently from the ledger,
with a control proving the pre-incident rule violates it),
`tests/test_live_mandate_deployment.py`. **Preserve all three.**

### Automatic rung progression is RETIRED

No R4/R5. No automatic R1→R2→R3. A market beyond `max_total_markets` requires a
**new explicit authorization**, which means a human and a commit.

### V1 continuous-inventory SHADOW: RUNNING, unchanged

`l5-v1-shadow` (`8a7bc9f4-…`), deployment `50817f1e`, commit `e429d7a5`. No
credentials, no transport, no order verb, no executor in the image. ~46 sports
markets/cycle. **Leave it running. Do not tune k, the pair target,
thresholds, the model or Envelope A during this sample.**

It rebuilt at `e429d7a5` because that commit threaded `last_rel`/`last_size`
through `V1.observe`. The defaults are the previous behaviour exactly and a
fixture pins that, so the control's *decisions* are unchanged — but the process
restarted, so its theoretical inventory walk restarts from flat at 12:58Z. The
observation history on its volume is intact.

Both state lookups are preserved on every row — naive inventory state and the
corrected `residual / k` L5-equivalent state — because the lift changes the
answer on **98%** of rows and we keep measuring that.

### ACT vs WAIT is the current fidelity question

V1 proposes on ~79.5% of observed rows; every WAIT is a risk cap, never the
model. Diagnosis in `analysis/l5/v1_shadow/ACT_VS_WAIT.md`. In short:

- **The model cannot say WAIT.** Its relation vocabulary is
  `{open, complete, extend}`; no wait/hold/none/noop token exists anywhere in
  the artifact. It is a *transition* model over 69,646 L5 **fills** — "given L5
  just acted, what next?" — so `P(act) = 1` by construction. This is the root
  cause, and it is a **missing-NO-ACTION-training-states** problem.
- **A separate implementation defect**: `observe()` never passes
  `last_rel`/`last_size`, so every lookup uses the defaults `(open, q<5)`,
  reaching **40 of 988** full keys and **202 of 69,646** transitions — 0.29% of
  L5's evidence. This degrades *which* action is predicted; it does not cause
  the over-action.
- Waiting **is** represented in the artifact, as `cadence_modal`
  (`gap<2s` / `gap2-10` / `gap10-30` / `gap>30`) — the inter-action gap. V1
  does not consult it.

### V1.1 SHADOW: DEPLOYED 2026-09-16, prospective sample collecting

`l5-v1-1-shadow` (`8597d731-…`), commit `c6c54783`. Same recorder handoff, same
volume pattern, no credentials, no transport, no order verb, no mandate — it
could not spend if it were told to. `src/l5_v1_1_shadow.py` puts ACT/WAIT in
front of the transition model as a separate stage, fixes the context defect
(full-key coverage **8.2% → 99.9%**, Stage-2 relation accuracy **0.546 →
0.688** against a 0.514 majority baseline), and scores timing separately.
Chronological OOS beats every baseline at every horizon; cadence carries most
of the signal; the value is at SHORT horizons because L5 acts within 120s 96%
of the time once active. Evidence: `analysis/l5/act_wait/FINDINGS.md`.

**H = 10s is preregistered** from that OOS and is not to be re-chosen from
prospective data while the first sample is collecting. Neither is k, the pair
target, the thresholds, the model, or any candidate envelope.

The V1 service keeps running as the control, and every V1.1 row *also* carries
`v1_would_have` — a real V1 call at the frozen `(open, q<5)` defaults on the
same observation, so the comparison is per-row and needs no clock alignment.
`vs_v1` attributes each disagreement to the change responsible: Stage-1
suppression and the corrected context are independent and are counted apart.

**Two limits of this tape, stated up front rather than discovered in the
numbers.** L5 trades Polymarket.com and this is Polymarket US sports, so
"did L5 act next" is **not observable** here — it is emitted as null with a
reason. And the recorder's `/status` carries no settlement time, so TTE is
constant in shadow. Between them, four of the seven prospective questions are
answerable here (proposal rate, lookup tiers, inventory trajectories,
divergence attribution) and three are not (was the ACT right, was the WAIT
right, Stage-2 accuracy vs L5) — those are answered by the chronological OOS.

Known gap, only for OPEN: we have no negatives for markets L5 declined to
enter, so `p_act` at `last_rel=open` is unvalidated (PR-AUC 0.21 vs 0.72 for
extend/complete). The collector for it is now **running**: V1.1 persists the
recorder's eligible universe every cycle to `universe.jsonl` with the
recorder's own timestamp beside our receipt time. Collection only — it labels
nothing entered or declined and infers no negative.

## Scaling hypothesis — frequency and breadth, not size

From 763,966 unique historical fills. L5's continuous-inventory shape is
already present in the early **2026-03-19 → 04-19** regime: ~218 fills/day,
~$1,139 notional/day, ~$128.69 median window notional, ~28 fills/window,
~10-share typical fill, ~$32.90 median peak residual.

Across the later 87× capital increase, median fill size moved only ~1.4× while
fills/day moved ~44.5× and markets/day ~24×.

**Working hypothesis, supported by that historical comparison and not a
universal rule:** L5 scaled primarily by frequency and breadth. Production
direction: *one L5 controller → explicit bankroll envelope → many eligible
markets → more repetitions as realized evidence earns expansion.* Favour more
eligible markets before materially larger orders.

**No live scaling authorization currently exists.** Candidate envelopes are in
`analysis/l5/v1_shadow/ACT_VS_WAIT.md`; they are unranked and unarmed.

## Accounting

`historical → shadow → submitted → filled → settled → realized`, kept separate.
Cohorts stay separate (`_cohort` is stamped per service on every ledger row).

- R1 cohort venue-confirmed realized: **−$0.0916** across five markets.
- Legacy PMUS cohort: **−$44.4898** across 54 markets. The two never merge.
- `aec-itfme-conbra-andpet-2026-09-16` is **SETTLED_REALIZED_UNCONFIRMED** and
  stays out of every net. Its ~$1.1105 matched-pair figure is **derived
  arithmetic, not realized profit**; its $0.1200 incentive is venue-credited
  and stays separate from trading P&L.

Rebates, rewards and incentives remain separate and attributable. Pending
incentives are accrued, never income.

## Everything else

- **Legacy PMUS** `pmus-spring-l5-probe` (`d84d9a68-…`): stays **HALTED**. Do
  not clear the latch. Its startCommand rewrites source with `sed -i` at boot,
  so the running code is not the reviewed artifact.
- **Grok incentive findings — PRESERVED, DEFERRED, do not start now.**
  `analysis/l5/grok_review/ADJUDICATION.md` (committed 2026-09-15) holds the
  audited incentive record and is the input to a later
  **pre-August / August / post-August** analysis. Owner instruction 2026-09-16:
  preserve it, but do not let that work interrupt the prospective V1.1 run.
  Headline, carried here so it is not rediscovered: over 2026-04-21 → 08-31 the
  public record shows MAKER_REBATE 110 / TAKER_REBATE 73 / REWARD 20, totalling
  $35,228.32 / $7,525.73 / $28,797.69 — **~94 bp of turnover, ~82% of L5's total
  economics and ~4.6× its trading P&L**. The "incentives ended 2026-08-31" claim
  was a cache artefact and is **false**. Incentive rows are their own on-chain
  transfers with no market or motif linkage, so they cannot be attributed to a
  specific trade. None of this is realized profit for us and none of it unlocks
  mature incentive-era behaviour; it stays separate from trading P&L.
- **Gemini**: P1, collecting, untouched.
- **Polymarket.com**: observation/reference only.
- No generic arbitrage, no R/S/D or cap5/8/13 revivals, no new rung strategies.

## What would actually move us forward

A. V1 ACT/WAIT — done, diagnosed; controller unchanged pending a decision.
B. Deployed mandate boundary — verified on commit `9fc7f32d`.
C. Smallest shape-preserving continuous envelope — candidates offered, owner
   chooses.
D. Open question: whether a single $0.05 plumbing regression adds anything the
   adversarial and ledger-prefix tests do not already establish.
