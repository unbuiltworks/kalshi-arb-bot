# L5 CURRENT PRIORITY

Operational north star, as of **2026-09-17**. Read with `L5_STATUS.md`, which
preserves evidence and history; this file states what we are doing now. Never
rewrite historical evidence to fit current priorities.

> **If you are a fresh session, read this first.** As of **2026-09-18T12:00Z**
> the live money executor `l5-r1-probe` is **STOPPED and HALTED**. It ended
> itself at 2026-09-17T13:36:57Z with `CAMPAIGN_STOP:HALT`, its mandate
> **expired at 2026-09-18T00:41:20Z**, and `HALT` was restored on 2026-09-18.
> Anything below, or in `L5_STATUS.md`, describing it as RUNNING, ARMED or
> under a current authorization is **the superseded 2026-09-17 state**.
> Verify against Railway before acting on any of it — runtime evidence
> outranks these files.
>
> **Both P0 lanes are execution lanes now, and they run in parallel.** PMUS is
> past its tape blocker and is proving EVIDENCED_FILL; Gemini is past its
> transport question and is one reviewed module away from a bounded live
> probe. Gemini is **not** "P1, collecting" — that line was stale and is gone.
>
> The old R1→R2→R3 rung/campaign program is still **retired**; do not
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

### Live money executor: STOPPED AND HALTED (2026-09-18)

`l5-r1-probe` (`6111a5d4-a2d9-4762-a057-31f02d07586f`), deployment
**`490ce8ac-4a73-4776-b7fa-0a5608f8d285`**, commit `64909c5cb0b6`,
`restartPolicyType: NEVER`. From the runtime `campaign_end` record, not from a
build result:

```
ended      2026-09-17T13:36:57Z   CAMPAIGN_STOP:HALT
sessions   21    filled_markets 0    settled 10    pending 1
realized   -$0.0832               realized_confirmed_by_venue true
next       "none: this status is an owner decision or a stop, and is
            never auto-restarted"
```

**The mandate `OWNER-2026-09-17-CP1-SETTLEMENT-PENDING-COHORT` EXPIRED at
2026-09-18T00:41:20Z.** No authorization exists for this service.

**HALT was restored 2026-09-18**, and the reason matters more than the act.
`test_the_halt_and_the_authorization_are_consistent` went RED on this branch
with `HALT removed without a valid authorization: mandate_expired`. HALT had
been removed on 2026-09-17 while the mandate was live; the mandate then expired
and nothing put HALT back, so for 11.3 hours the committed state read
"un-halted, no valid authorization". The test caught exactly what it exists to
catch.

**No order was placed under an expired mandate and none could have been** — an
expired mandate is `NO_MANDATE` at the wire, the same refusal as an absent one,
and the process had already been gone for ~11 hours when it expired. The HALT
file is belt to that brace, because the serving image carries no HALT and
`PMUS_SPRING_LIVE` / `PMUS_SPRING_ARM` are still set on the service: the
process is gone, a redeploy is not.

Re-arming requires a NEW owner authorization: a new `live_mandate.json` with
its own `authorization_id`/`authorized_at`/`expiry`, committed by a human,
removing `HALT` in the same reviewed change. A session, restart, settlement or
`advance_gate` may do neither.

### P0 NOW: prove or refute EVIDENCED_FILL on live sports markets

**The tape blocker is CLOSED.** The recorder's authenticated WS trade flow is
working — measured 2026-09-18T12:00Z on `pmus-ws-evidence-recorder`
(`71b6ea99-…`, deployment `ae712b6d`): `trades_300s` 140 → 192 across
consecutive status lines, `counts.trade` 142 per connection cycle,
`books_300s` ~4,850. Do **not** reopen the old `trades_300s: 0`
investigation, and do not substitute gateway `sharesTraded` for it — that
counter is cumulative per market and cannot say a trade happened at our price.

The ladder is the whole question now:

```
PLACED -> RESTABLE -> BOOK_REACHED -> TRADE_FLOW -> EVIDENCED_FILL
```

Every stage was already implemented in `src/l5_surface/evidence.py` and
`src/pmus_l5_surface_shadow.py`. What was missing was a NETWORK source
carrying the tape: `AUTHENTICATED` needs the recorder's state in-process, and
`STATUS_HTTP` carries the authenticated book with trade AGGREGATES only, so a
deployed run stopped at `BOOK_REACHED` for want of a join rather than for want
of a ladder.

**`TRADES_HTTP` is that join** (`src/pmus_l5_surface_runner.py`): the
recorder's `/status` book joined to its `/trades` tail over Railway's private
network. It runs as the `pmus_surface` stream on `l5-exec-shadow`
(`5dced43a-…`), which is inside that network and carries **no PMUS credential
and no order transport** — zero-order isolation preserved, asserted over the
module AST by `tests/test_pmus_trades_http_join.py`.

Two truncation holes were closed on the way, because the HTTP path defeats the
in-process coverage rule: the recorder serves the newest **200** rows of a
**500**-deep ring, so `len(trades) >= RING_CAPACITY` can never fire over the
wire and a wrapped ring would have read as complete coverage. `ring_full` and
`served_cap` now travel with the data and either one makes coverage UNVOUCHED.

**UNREADABLE IS NOT EMPTY**, at every level: a market with no tape carries
`trades: None`, never `[]`. `None` stops the ladder at `BOOK_REACHED`; `[]`
asserts nobody traded and would manufacture a confident no-fill on exactly the
busy markets where it is most wrong.

The open question is now genuinely empirical, and either answer is a result:
**do our own passive rungs clear the queue ahead of them on live sports
markets, or do they not?** Flow that does not clear the queue is not a fill and
is never to be reported as one.

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

## P0 — Gemini BTC5m continuous inventory

**Not P1, and not collecting.** The transport question is CLOSED and is not to
be reopened: the authenticated WebSocket `order.place` path is the one every
accepted order on these credentials was placed through, recovered into
`src/gemini_execution/ws_session.py` from commit `2bc6c04c`, with
venue-confirmed FILLED order `145828835409069189` behind it. Do not rediscover
whether Gemini trading works and do not rebuild the transport. No REST order
has ever succeeded on these credentials; that lane stays closed.

**What exists and is reviewed** (`src/gemini_execution/continuous_probe.py`,
frozen at `6342171e`): the L5 surface bounded to one BTC5m window — the
OPEN/EXTEND/COMPLETE classifier driven by inventory only, the hard envelope
(action 0.01, max 12 actions, residual 0.04, imbalance 0.04, YES/NO 0.10,
spend $0.15), the end-of-window floors derived from the lane's own
`minimum_rest_time` (no new OPEN/EXTEND under 60s, everything withdrawn under
30s), `WindowGuard` binding exactly one contract with a terminal STOP,
`recover()` that treats an unreadable read as UNREADABLE and never as flat, and
`settlement_report()` keeping matched-pair and directional-residual apart with
`realized` withheld until venue-confirmed cash.

**What does not exist: the driver.** Measured by AST walk over the frozen
blobs, `continuous_probe` declares no coroutine, has no entry point, imports
neither `asyncio` nor the session, and is imported by nothing. Its two
transport-shaped calls, `build_ws_order` and `sanitized_ws_frame`, are
RENDERERS — they return the frame "as it would go on the socket" and hand it
back. The only order-capable module, `ws_canary.py`, is hard-bounded
`MAX_ORDERS = 1` / `MAX_RESIDUAL = "0.01"` with one send call and no loop:
both **below** the continuous envelope, so arming it would execute a
different, smaller thing under the wrong latch identity.

So the controller can decide a whole window and cannot place one order of it.
`analysis/gemini/CONTINUOUS_PROBE_1_FAIL_CLOSED.md` records the fail-closed
stop against that gap, with the fresh preflight that passed beside it.

**The arm is structurally narrow and stays that way.** `arming.EXPECTED_AUTH_ID`
is the constant `OWNER-2026-09-17-GEMINI-CANARY-1` — already spent, state
`REJECTED`. `armed()` requires an exact match, so this build **cannot** arm for
a continuous probe by setting an environment variable. A new authorization
means a human editing that constant in a reviewed commit. Preserve this.

**Next step, and it is a build, not a button-press:** write
`src/gemini_execution/ws_continuous.py` UNARMED — fresh preflight compared
against the frozen assumptions rather than merely required to succeed, durable
latch consumed before the first mutation, authoritative exposure read,
`WindowGuard`-bound loop, reconciliation by `clientOrderId`, ambiguity recorded
UNKNOWN and never as "no order", end-of-window cancel, residual CARRIED and
labelled with no taker cross to flatten it. Then a NEW owner authorization
naming the SHA that contains it. Probe #1's authorization named `6342171e` and
expired against a terminal STOP state that `6342171e` cannot reach.

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
- **Gemini**: **P0, execution lane** — see the section above. The old
  "P1, collecting, untouched" line was stale and is retired.
- **Polymarket.com**: observation/reference only.
- No generic arbitrage, no R/S/D or cap5/8/13 revivals, no new rung strategies.

## What would actually move us forward

Two execution lanes, in parallel, and nothing else. No new strategy
workstreams: the goal is one continuous L5 system, not more experiments.

**PMUS.** Let the `pmus_surface` stream run against live sports markets and
read the ladder it produces. The result is `EVIDENCED_FILL` counts against
`TRADE_FLOW` counts per side — do our passive rungs clear the queue ahead of
them, or not? A refutation is as useful as a confirmation and is to be reported
as one; flow that does not clear the queue is not a fill.

**Gemini.** Build `ws_continuous.py` unarmed, review it, then authorize against
its SHA. Until it exists there is nothing to authorize.

Carried over, unchanged: V1 ACT/WAIT is diagnosed and the controller stays
unchanged pending an owner decision; the deployed mandate boundary is verified
on commit `9fc7f32d`.
