# Code-interference audit of stranded legs

**Date:** 2026-11-01
**Audited SHA:** `4d52345d` — the commit the `l5-pmus-live` service is
actually running (deployment `6e861476` SUCCESS), **not** the branch tip.
**Evidence:** 501 Railway log entries from the closed campaign, 13:28–15:20Z
on 2026-11-01: 194 `L5_LIVE_DECISION`, 191 `L5_TS_COMPLETION`, 59 heartbeats.

## Verdict

**Code interference accounts for $0.00 of the residual loss.** No instance was
found where a software defect prevented an economically justified completion.

**One real correctness defect was found and is fixed** (reporting only, no
behaviour change): `COMPLETE_CUTOFF_S = 720` is **unreachable dead code** at
the deployed cancel margin, so risk-reducing completion stops at the same
instant as opening — the exact defect the two-cutoff design exists to prevent.

**The residual loss is realized variance on EV-neutral positions, not forgone
profit.** Every completion the engine priced was **variance-reducing and
EV-negative by ~$0.03** — the taker fee plus half the spread. A
time-respecting completion rule would have *realized* +$2.19 more across the
four residual windows, but its *expected* value is about **−$0.12**. The
difference is the variance we eliminated, which in a four-window sample broke
our way. Treating that +$2.19 as an edge is the error the owner warned about.

---

## 1. The three premises, checked

| premise | verdict |
|---|---|
| `ENTRY_CUTOFF_S = 600`, `COMPLETE_CUTOFF_S = 720`, window 900s | **confirmed** at `4d52345d:src/l5_two_sided_probe.py:791-792` |
| 21,161 opposite-side decisions blocked under `already_resting` | **confirmed, and it is SHADOW-ONLY** — see §2 |
| zero windows with both sides resting | **confirmed, and explained** — see §4 |
| executor unarmed, stale campaign approval | **confirmed**: `armed false`, `blocked true`, `approval_state approval_stale` |

## 2. `already_resting` is shadow-only (request item 5)

At the deployed SHA, `already_resting` appears in **exactly one module**:

```
$ git grep -l already_resting 4d52345d -- src/ scripts/
4d52345d:src/l5_shadow_controller.py
```

It appears **nowhere** in `l5_two_sided_probe.py`, `l5_live_execution.py` or
`l5_pmus_live_runner.py`. The module documents it as a scenario parameter and
classifies it as **JUSTIFIED, by design** (`l5_shadow_controller.py:240`):

> `already_resting` — one outstanding order per scenario, by design

and it is the mechanical expression of `max_live_orders = 1` in the shadow
arms (`:266`). The live path is different: `l5_live_execution.py:691` computes
`ceiling = self.max_live_orders + (1 if pair_second_leg_ok else 0)`, so live
permits two resting legs.

**So the 21,161 refusals are a shadow scenario artifact and not a live
constraint. They cost nothing.** The honest caveat is in the module's own
words (`:275`): the `TWO_SIDED` arm *"PERMITS a second resting side; it does
not go looking for one"* — which is exactly why its telemetry reports zero
windows with both sides resting. Nothing is broken; the arm does not test what
its name suggests.

## 3. No opening gate blocks a completion (request item 4)

`second_leg_slot_refusal` (`4d52345d:src/l5_two_sided_probe.py:420`) is the
only gate that could plausibly interfere, and it **cannot**: its second
condition is

```python
if len(rows) == 0:
    return "NO_FIRST_LEG_RESTING"
```

Once the first leg has **filled** there are zero live orders, so this
predicate is not consulted and the ordinary `max_live_orders` slot covers the
completion order. The "claimed in this round" condition that looked dangerous
governs only the *second simultaneous maker quote*, never a complement against
filled inventory.

Checked and cleared the same way: `first_leg_entry_refusal` is reached only on
an opening leg; `WORST_CASE_DEFICIT` refusals in the tape all carry
`PAIR_DOWN`/`PAIR_UP` opening keys (`controller_open:…`,
`TWO_SIDED_PROBE:PAIR_DOWN:…`), never a completion key.

## 4. What actually happened: the mechanism

Across the four windows that ended with residual inventory, **every single one
of the 191 completion evaluations reports `still_fillable_orders: 0`** — in
both the MAKER and COMPLETION phases. No maker complement was ever resting.

The reason is in the decision records: **168 of 194** carry

```
two_sided_round.plan_unexecutable = LEG_WOULD_CROSS:UP   (128)
                                    LEG_WOULD_CROSS:DOWN  (40)
```

This is the whole mechanism, and it is a logical consequence rather than a
bug. Holding UP bought at 0.44, an economic complement needs DOWN at ≤ 0.52
(to stay under the 0.96 pair ceiling). But DOWN was trading at **0.23–0.35**,
because the market had moved in our favour. A bid at 0.52 is *through* the
book: it would be a taker, `post_only` rejects it, so nothing is placed.

> **Maker-only completion is structurally impossible for a stranded leg whose
> market has moved in our favour.** The complement is cheap *because* the
> market moved; resting as a maker means waiting for it to come back, and if
> it comes back the gain that made completion attractive has gone.

So the attribution to "maker execution and queue position" is **$0.00 from
queue position**: zero decisions carried `two_sided_queue` telemetry and no
order ever rested to hold a queue place. The binding constraint is the side of
the book the required price sits on.

## 5. What the engine priced, and what it was worth

With no maker route, the engine evaluated **crossing** completions every
10–15s and logged each as `PROPOSED`. All 191 were refused:

| refusal | n |
|---|---|
| `OWNER_AUTHORIZATION_ABSENT` (`L5_TS_COMPLETION_CROSS` not YES) | **191** |
| `NO_INVENTORY_TO_COMPLETE` | 29 |
| `ALREADY_MATCHED` | 5 |

Worked example, window `1415z` at 14:20:15 (T+315), holding **UP 2.08 @
0.4400**, DOWN offered at **0.2300** with **302.95 contracts of displayed
depth** against a need of 2.08 — **145× cover, so genuinely executable, not a
hindsight minimum**:

| | |
|---|---|
| hold, UP wins | **+$1.1648** |
| hold, UP loses | **−$0.9152** |
| market-implied P(UP) = 1 − 0.23 | **0.77** |
| **EV(hold)** | **+$0.6864** |
| complete — certain, after $0.03 taker fee | **+$0.6564** |
| **EV(complete) − EV(hold)** | **−$0.0300** |

**The crossing completion is EV-negative and variance-eliminating.** The
engine's `worst_improvement_usd` field is honestly named — it is the
*worst-case* improvement, which completing always improves by construction. It
is not an expected-value gain, and the +$1.5716 it reports here is not $1.57
of forgone profit.

This also resolves the apparent contradiction with the taker identity
`pair_taker = 1 + spread`: that identity assumes completing at the touch right
after a maker fill at the touch. Here the pair is 0.44 + 0.23 = **0.67** only
because we were already holding an unrealized directional gain. Crossing
converts that gain from unrealized to realized and pays the spread and fee to
do it.

## 6. Time-respecting counterfactual (request item 7)

Rules fixed **before** looking at outcomes; each takes the **first** qualifying
evaluation, so nothing is selected with hindsight.

| window | realized (held) | R1: first `worst_after > 0` | R2: first evaluation |
|---|---|---|---|
| 1415z | **+1.1648** | 14:20:00 → +0.4916 | 14:20:00 → +0.4916 |
| 1430z | −0.9494 | 14:31:07 → +0.0210 | 14:30:46 → −0.0800 |
| 1445z | −0.9696 | 14:51:06 → +0.1018 | 14:46:08 → −0.0598 |
| 1500z | −0.8188 | 15:01:39 → +0.0065 | 15:01:12 → ±0.0000 |
| **total** | **−1.5730** | **+0.6209** (Δ **+2.1939**) | **+0.3518** (Δ **+1.9248**) |

**Read this correctly.** Holding won in 1 of 4 windows (1415z, by +$0.55) and
lost in 3. The +$2.19 is the payoff of removing variance from a book that drew
three losses out of four. Per-crossing EV is −$0.03, so the *expected* delta
of R1 is approximately **4 × −$0.03 = −$0.12**. On n=4 with a ±$1 per-window
payoff, +$2.19 is well inside noise: per-window sd is ≈1.05, so the standard
error of a 4-window total is ≈$2.10.

**Crossing is not a profit measure. It is insurance, priced at ~$0.03/window.**

## 7. The confirmed correctness defect

`COMPLETE_CUTOFF_S = 720` is **unreachable at the deployed configuration.**

`may_complete` (`4d52345d:src/l5_two_sided_probe.py:966-979`) tests the two
bounds **in this order**:

```python
if now >= float(t_start) + complete_cutoff_s():   # 720
    return "PAST_COMPLETE_CUTOFF"
if now >= cancel_cutoff(t_end):                    # t_end - cancel_margin_s()
    return "PAST_TERMINAL"
```

The deployed `cancel_margin_s` is **300** — the runtime verifier's own
approved value (`l5_runtime_config_verify.py:77`, corroborated by
`defensive_terminal_s: 600.0` at `:78`). So `cancel_cutoff(900) = T+600` and
the terminal always fires **120s before** the 720 cutoff can.

**The live tape proves it:**

| gate | n | offsets observed |
|---|---|---|
| `TWO_SIDED_PROBE:PAST_TERMINAL` | 39 | **T+601 … T+714** |
| `TWO_SIDED_PROBE:PAST_COMPLETE_CUTOFF` | 155 | **T+725 … T+880** |

That ordering is the signature of a 720 that never binds. And **all 39
dead-zone decisions carried a live two-sided book** (e.g. T+601: bid 0.81 /
ask 0.82, 124 contracts of ask depth; T+613: bid 0.17 / ask 0.18, 509
contracts), so complements were displayed and executable while the engine
refused to look.

The code's own comment at `:780-786` states the stakes exactly:

> ENTERING a first leg late is the dangerous one … COMPLETING is the safe one
> — it REMOVES exposure — so it must stay available later than opening.
> **Collapsing them meant the engine stopped reducing risk at the same instant
> it stopped taking it.**

**The deployed configuration re-collapses them, to exactly T+600 =
`ENTRY_CUTOFF_S`.** The defect was fixed in code and reintroduced by
configuration, and nothing in the heartbeat said so.

### The fix applied

`binding_completion_deadline_s()` in `src/l5_two_sided_probe.py`, plus three
observed verifier rows. At the deployed margin it now reports:

```
binding_completion_deadline_s          = 600.0
complete_cutoff_reachable              = False
completion_collapsed_onto_entry_cutoff = True
ts_complete_cutoff_s                   = 720.0   <- reported beside it
```

**Read-only; it moves no deadline and gates nothing.** A test pins that it
does not. The misleading 720 is deliberately still reported *next to* the
binding 600, because the point is to make the discrepancy visible rather than
quietly paper over it.

**Deliberately NOT graded.** Grading it would fail the current deployment, and
the remedy is an owner decision between two options, not something a verifier
row should force:

- lower `L5_TS_CANCEL_MARGIN_S` to 180 → terminal T+720, completion outlives
  opening by 120s. But `cancel_margin_s()` is `max(CANCEL_MARGIN_S, env)` —
  **raise-only** — so 180 is the floor and this is the only reachable value;
- or lower `COMPLETE_CUTOFF_S` to 540 so the configured cutoff is honest and
  completion still outlives nothing. This is worse.

Neither is a behaviour change I am authorized to deploy, and **neither is
worth money** on the evidence in §5–6: the extra 120s of completion window can
only be used by crossing (EV −$0.03) or by resting an order the cancel margin
exists to protect.

## 8. Classification of every residual

| window | residual | class | basis |
|---|---|---|---|
| 1415z | UP 2.08 @ 0.44 | **C — ECONOMICALLY UNAVAILABLE** | best crossing EV −$0.03; holding won by +$0.55 |
| 1430z | DOWN @ ~0.49 | **C** | 79 evaluations, every one EV-negative after fee |
| 1445z | UP 2.08 @ 0.48 | **C** | 55 evaluations; maker route blocked by `LEG_WOULD_CROSS` |
| 1500z | mixed | **C** | 29 evaluations; `worst_after` ≤ +0.0065 at best |
| 1330z, 1345z, 1400z | none | n/a | completed; +1.1268 / +1.2127 and −0.842 |

**Zero class A (CODE-BLOCKED). Zero class B (QUEUE-MISSED)** — no order ever
rested to miss a queue. **Zero class D** — the order, book, decision, refusal
and settlement records were all present; no new live windows are needed.

## 9. The five answers

**How much residual loss is code interference?** **$0.00.** No defect blocked
a completion. The one real defect (§7) is a 120s reporting/configuration
collapse whose closure is not worth money.

**How much is maker execution and queue position?** **$0.00 from queue
position.** No complement ever rested, so no queue place was lost. The binding
fact is `LEG_WOULD_CROSS` on 168 decisions: the economic complement price sits
on the far side of the book, which maker-only cannot express.

**How much is adverse selection at entry?** **Effectively all of it.** First
legs filled and the market then moved against the position in 3 of 4 windows,
consistent with the independently measured **−$0.2315/unit over 384 windows**,
`adverse_rate 0.89` and `swept_share 0.98`. The −$1.5730 is that selection
realizing, not a missed repair.

**Single smallest code change that would most improve expected net profit?**
**None of the candidates improves expected net profit, and I am not proposing
one.** Crossing costs $0.03/window in EV; closing the dead zone requires
crossing or unsafe resting; the maker route is structurally unavailable. The
change I did make is a correctness and observability fix worth $0.00 in EV.

If the owner wants the *variance* removed rather than the EV improved, the
smallest change is `L5_TS_COMPLETION_CROSS=YES` — **no code change at all**,
one variable. It converts a ±$1.05/window book into roughly +$0.15 certain, at
a cost of ~$0.03/window. That is a risk-of-ruin decision, not a profit
decision: against the **$5 daily loss halt**, eliminating ±$1 swings over 8
windows/day materially changes the chance of halting a campaign mid-run. I am
flagging it as an owner choice with its price attached, and **not
recommending it as a profit measure** — the owner's standing instruction
against forcing taker completion stands, and the EV arithmetic supports it.

**Does it survive chronological out-of-sample replay?** The *correctness* fix
is outcome-independent, so the question does not apply to it. For crossing:
**no, not as a profit claim.** It is EV-negative by construction at every
observed price, so no replay can make it profitable; what replay would measure
is variance reduction, which it delivers by arithmetic. The +$2.19 realized
in-sample is one draw with a standard error of ≈$2.10, i.e. statistically
indistinguishable from the −$0.12 its EV predicts.

## 10. Limitations

1. The 501-entry log page covers 13:28–15:20Z and all 7 campaign windows'
   decisions, but Railway paginates at 500: some intra-window decisions for
   1330z–1400z (the three completed windows) are not in this page. Those
   windows carried **no residual**, so they contribute nothing to this audit.
2. `worst_before`/`worst_after` are the engine's own figures. I re-derived the
   1415z case independently from `up_qty`, `up_cost_px`, price and fee and
   reproduced `worst_after` to within the $0.03 fee, so the field is trusted
   for the other three.
3. Market-implied P(UP) is taken as `1 − DOWN price`, which ignores the bid/ask
   on DOWN. Using the bid instead would make holding look slightly better and
   crossing slightly worse, so the conclusion is conservative in the
   direction that matters.
4. No change here is deployed. `DEPLOY_TRIGGER` is untouched and the executor
   remains disarmed with `owner_arm_token` still reading `true` (§6a of
   `CONTINUOUS_OPERATION.md`).
