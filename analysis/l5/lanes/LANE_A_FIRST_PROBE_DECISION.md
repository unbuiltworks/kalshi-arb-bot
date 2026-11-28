# LANE_A_FIRST_PROBE_DECISION

Evidence **FROZEN** at `2026-09-16T01:41:44Z`, sha256 `43da9ac56cc082db…`
(`artifacts/l5_lane_a/EVIDENCE_SNAPSHOT_FROZEN.json`, 75 watch rows).
No further collection before this decision. No strategy logic changed.

## The frozen evidence, in full

| | |
|---|---|
| strict spring completions | **13**, across 11 markets |
| pair-cost distribution | **0.9800 × 11, 0.9750 × 2** |
| **≤ 0.96** | **0** |
| **≤ 0.98** | **13** |
| cheapest observed | **0.9750** |
| category bar | **MET** — mlb, 7 distinct markets |
| completion latency p50 | 0.9s – 286.6s across markets |

## The framing that decides this, stated before the options

**Option B changes no constant.** `SPRING_PAIR_TARGET` is **already 0.98** with
a hard 1.00 ceiling, and has been for the deployed life of this code. The
~0.95–0.96 figure is the **operating model's spring-discipline preference**;
CLAUDE.md says "roughly ≤ 0.96 *where the venue implementation uses that
target*" — and this implementation does not, it uses 0.98.

So the question is **not** "should we loosen a threshold". It is: **do we hold a
first bounded probe to a bar stricter than the code it would run?**

---

## A — WAIT_FOR_LE_096

**Historical support — strong.** L5's window-level effective matched pair cost
has **median 0.9648**. The operating model records spring patience targeting
~0.95–0.96. Our own earlier PMUS **posted maker pair p50 was 0.9627** over 48
contemporaneous authenticated candidates.

**Historical contradiction.** Those same records carry an explicit
**SURVIVORSHIP WARNING, classified UNKNOWN**: n=9 decomposed pairs, our *fills*
landing at pair p50 **0.949** and our *no-fills* at **0.9952**. The cheap pairs
are the ones that filled. 0.96 may describe **selection**, not attainable cost.

**Prospective support — none.** **0 of 13** strict completions are ≤ 0.96.

**Order behavior.** None. The probe does not run.

**Max first-market spend / max loss / residual / cancel / settlement.** N/A —
nothing is placed.

**What it resolves.** Nothing yet.

**Cost of choosing.** The arrival rate of a ≤0.96 strict sports completion is
**unmeasured**. 0-of-13 yields no rate, only an upper bound. This is an
open-ended wait during which the execution, queue and accounting questions stay
unresolved — and those are the questions a first probe exists to answer.

**Stop conditions.** Continue collecting; revisit when a ≤0.96 strict completion
appears in ≥2 distinct same-category markets, or when the owner sets a bounded
wait.

---

## B — ALLOW_LE_098_FOR_FIRST_BOUNDED_PROBE

> **Not the new optimal threshold.** No sweep supports 0.98 and none was run. It
> is a **narrowly scoped first-probe eligibility envelope**, justified only by
> the strict sports completions presently observed, and it **expires with the
> probe**.

**Historical support.** L5's switch pair price median is **0.99**, with **47.2%
above 1.00** and **36.2% of his two-sided windows ending above par**. A 0.98
ceiling still refuses the top third to half of what he paid.

**Historical contradiction — strong and explicit.** The operating model holds
near-$1 pair tolerance to be **incentive-era behavior**, locked "unless
equivalent economics are source-confirmed on our account." We *do* hold
source-confirmed credited rebates — **$9.67 across 790 of 1,186 fills**, plus
$7.04 rewards PAID — but those were earned at fill sizes **orders of magnitude
above R1**. At **0.02 contracts the rebate does not rescue a 0.98 pair.** The
incentive support that would justify near-$1 tolerance **is not present at this
size**. This is the strongest argument against B and it is not resolved by it.

**Prospective support.** 13 of 13 strict completions ≤ 0.98, across 7 distinct
MLB markets, **all fee-safe** by the recorder's own accounting.

**Exact proposed order behavior.** Unchanged deployed policy: maker/post-only,
**one outstanding order**, one market at a time, sequential
OPEN → EXTEND → COMPLETE → HOLD under the frozen v2 model
(`model_sha bbb55bf90638d2bd`), completion at ≤0.98 with **≥1c after known
fees**, residual **held to settlement, never liquidated**.

**Maximum first-market spend.** **$0.05** cumulative OPEN/EXTEND (R1
`per_market_cap_usd`). At a 0.27 touch the executable order is **0.02 contracts
= $0.0054**, so the cap admits ~9 entries; `max_actions_per_session` is 6.

**Maximum loss.** **$0.05 for the first market** (worst case: every dollar lands
one-sided and settles worthless). **$0.15/day** at 3 markets × 1 campaign.

**Residual bound.** 3 behavioral units; concurrency 1; no new first leg while
unrelated residual exists.

**Cancellation / deadman.** Cancel + deadman inherited unchanged; 900s session
deadline; **stop 120s before settlement**; **restart NEVER** after a completed
or crashed probe.

**Settlement / accounting path.** Venue-confirmed — the only lane where this is
already proven on real money: **Ledger D, 34 venue-confirmed settled markets**,
nested settlement extraction verified, `SETTLED_REALIZED_UNCONFIRMED` held out
of the net. Full chain: order → submitted → fill → matched → residual → fees →
rebate → reward → settlement → realized net.

**What the first probe would resolve.**
1. Does our maker quote actually rest and fill on a live PMUS sports book, at a
   known queue position?
2. Does the complement complete, and at what latency?
3. What does the venue actually charge and credit on **our** fills at R1 size,
   joined to the exact fill?
4. Does `filled → settled → realized` close end-to-end on a market this probe
   itself opened?

**What it would NOT resolve — profitability.** At 0.98 the locked edge is
**$0.0004** on the executable size. That is statistical noise. Any P&L from this
probe is a **measurement artefact** and must never be read as expectancy.

**Stop conditions.** Daily realized-loss stop; deadman; spend cap; one market;
6 actions; stop 120s pre-settlement; no automatic repeat; **the ≤0.98
eligibility expires with this probe and does not become policy.**

---

## Hypothetical preflight — option B, deployed artifact `6adcc4c43e67`

| gate | state |
|---|---|
| CATEGORY_BAR | **PASS** |
| SIZE_EXECUTABLE | **PASS** |
| SERVING_ARTIFACT | **PASS** |
| BUDGET_PERMITS | **PASS** |
| SETTLEMENT_ACCOUNTING | **PASS** |
| SHUTDOWN_LATCH_CLEARED | **FAIL — owner-controlled, not cleared** |
| DUAL_KEY_ARMED | **FAIL — owner-controlled, not armed** |

`evidence_gates_all_pass: true` · `only_owner_gates_remain: true`

**Nothing was cleared, armed, deployed or ordered.** Passing every evidence gate
is not an instruction to trade.

---

## The decision, stated plainly

The evidence gate is satisfied and the envelope is identical under both options —
**$0.05 first market, $0.15/day, residual 3 units, one market, hold to
settlement.** What separates A from B is not risk; it is **which bar governs a
first probe**, and what that probe is *for*.

If the first probe is meant to establish **economics**, A is right: at R1 size
no pair cost we have observed produces measurable expectancy, and 0.98 without
incentive support is exactly the behavior the operating model locks.

If the first probe is meant to establish **execution and accounting** — whether
our quote fills, completes, credits and settles end-to-end — then the pair cost
is nearly irrelevant at $0.0004 of edge, and B buys that answer now instead of
waiting on an event whose frequency we have never measured.

**The two facts that should carry the most weight, in tension:** 0 of 13
completions reach 0.96, so A has no prospective evidence at all; and the rebate
that would justify 0.98 does not exist at R1 size, so B proceeds without the
economic support its own historical precedent requires.

This is an owner decision. No recommendation is encoded in the code, and neither
option is armed.
