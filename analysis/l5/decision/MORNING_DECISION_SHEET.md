# Morning decision sheet — 2026-10-07

One page. Sections marked **PENDING** are lanes still running at the time
of writing; each will be filled from its own verdict commit, never from my
summary of it.

---

## 1. Collector health and coverage — HEALTHY

Service `l5-book-shadow`, project `l5-book-shadow-public`, pinned by SHA to
`84e277297`. Public-data-only project: no credentials, no order-capable
module, mutation-incapable boot gate passing 4/4.

All five required live-proof items, from the deployed heartbeat:

| requirement | observed |
|---|---|
| independent two-token rows continue | `clob_rest_book` 74 → 296 → 516; `tokens_tracked` 72–74 across 36–37 markets; **`single_token_markets: []`** |
| L5 activity rows advance | **627 → 636 → 643** |
| a known public L5 activity item joins to its market/token | **`joined_any: true`**, join key = the venue's own token id |
| `http_429` remains zero | **0** |
| request pacing ≤ 240 RPM | **238.8 / 225.0 / 223.0** measured (not the cap) |

Durable storage verified read-only: `both_tokens_ok: true`,
`defective_markets: []`, and pre- and post-restart raw parts coexisting
(`tape_20261007T03Z.jsonl` + `tape_20261007T04Z.jsonl`, 39.7 MB) —
append-only across six restarts, `TapeWriter.MODE` still `"a"`.

**Honest caveat:** 37 activity rows have no recorded book. That means we
did not poll those tokens, **not** that their books were unchanged. The
collector reports this rather than dropping it.

### The one data-integrity defect you must know about

`BOOK_ONLY_ACTIVITY_UNAVAILABLE` ran **03:33:10Z → 04:17:51.286Z (44m41s)**.
The tape's own interval starts at 04:03:01.868Z, because the OPEN edge
could not be written before the code that writes it shipped. So
`derived/activity_windows.json` **understates the outage by 29m52s**.

> Exclude the TRUE bound, not the derived file's narrower one. Activity
> inside it is UNMEASURED, never a zero-fill observation.

The tape was not backfilled: manufacturing provenance for rows never
observed would be worse than the gap. Books were healthy throughout.

Root cause of the outage: `data-api` keys activity on the **proxy wallet**,
not the profile handle, and reports a non-address `user` as *absent*
(HTTP 400 `required query param 'user' not provided`) — so a wrong identity
was indistinguishable from a missing parameter. Repaired GET-only, plus a
blocking boot-gate row so it cannot recur silently.

Also built: the dual-token CLOB websocket strict-timing arm
(`src/l5_clob_ws.py`), separate schema and tape stream,
`timing_authority=websocket_strict`, both tokens on one socket for a shared
receive clock. It refuses to accumulate until a live proof shows both
tokens streaming. **Not yet proven live.** REST rows remain
reachability/same-second evidence and were not reinterpreted.

---

## 2. What happened to L5 after August — **the posture did NOT end**

**No, L5 did not abandon subsidy harvesting.** There were never one reward
program but **three**, on three separate payout clocks — and the largest
one never stopped:

| stream | paying days | last payout | post-Aug total |
|---|---|---|---|
| `REWARD` (the August liquidity pool) | 6 | **2026-09-06** | $8,229.66 |
| **`MAKER_REBATE`** (account-level) | **37** | **2026-10-07 — still paying**| **$34,822.79** |
| `TAKER_REBATE` | 37 | 2026-10-07 | $2,799.98 |

October is the clean cut: every October day observed with **no gaps**,
`REWARD` a **measured $0.00** throughout, and L5 still traded 70,056 fills
over 2,641 events. Behavior barely moved — median pair cost 0.9671 →
0.9649, completion 50.3% → 49.6%, EXTEND share 70.8% → 72.4%. **The subsidy
mix changed; the policy did not.**

Coverage: **449,340 rows / 437,827 TRADE fills**, all **37 calendar days**
2026-09-01 → 2026-10-07 with **zero unobserved days**, 0 failed envelopes,
10,468 duplicates and **0 collisions with differing fields**.

### The implication that matters for copying

Post-August, `REGIME_2026-08-19_ON` alone: matched share 85.2%, median pair
cost **0.9668**, and only **46.2% completing ≤ $0.96**. So **L5 routinely
completes pairs above our fee-safe line**, and the economics that make that
rational are a **maker rebate that is unconfirmed for our account**.

> **The latest behavior is NOT safe to copy at face value.** Spring
> discipline's `<= 0.96` target stays as-is, and near-$1 tolerance stays
> locked. This is now evidence-backed rather than conservative by default.

**The decisive unknown:** maker/taker role is not on L5's tape (the role-ish
field is `CHAIN_TOPIC_POSITION`, and `execution.isAggressor` does not
exist). Ex-incentive the matched book is therefore somewhere between
comfortably profitable (mostly maker) and nearly fee-dead (mostly taker) —
every fee figure is an explicit opposite-corner bound, $0–$110,784. That
gap is exactly what decides copyability, and we cannot close it from public
data.

Confidence: **moderate-to-high** on the stream question (read off credited
cashflow rows, gapless coverage). **LOW** on "is the latest behavior worth
copying" — deliberately stated as a separate claim.

Two overreaches the lane caught in its own work: it first wrote that
incentives were the larger term (they are not — the ex-fee matched-pair lock
$160,793 is ~3.5× the entire credited incentive stack $45,852), and it
first claimed above-$0.96 completion *proved* subsidy dependence, when it
is an inference from pricing rather than a measurement. Both corrected
visibly. Two of the seven required accounting components — directional
residual P&L and spread capture — are **UNAVAILABLE**, so the total is
unmeasured rather than estimated.

## 3. S3 verdict — **INSUFFICIENT**, and the instrument is the finding

`markets_COMPLETE = 0` against a required **25**. Run `37536917745`
finished on its own at 04:21:36Z, untouched, all 15 steps green.

Evidence integrity held, in the required order: both artifacts uploaded
`if: always()` before any git op; the fingerprint assertion passed; the
data commit `5cec58cc` is a verified ancestor of the branch head. **The old
race recurred** (`5d24217..e6a2d11` moved underneath) and the new rebase
absorbed it on attempt 1 — so the four workflow fixes earned their keep.
Green CI here does not mean the evidence disappeared successfully.

Two defects matter more than the verdict, and both say *broken instrument*,
not *absent opportunity*:

1. **The complement side was never observed** — `complement_never_observed:
   true` in all five markets. Direct cause of the gate breach
   (`two_sided_evidenced_acquisition_below`, observed 0.0 vs threshold
   0.20). A two-sided instrument fed a one-sided tape cannot produce
   matched inventory. The gate caught precisely what it was built to catch.
2. **`markets_COMPLETE` is unreachable by construction** — window spans
   537–787 s against an 840 s criterion, with healthy 4.6–6.0 s gaps. Edge
   truncation, not dropout. The 25-market bar cannot be met however long
   collection runs.

Nothing was tuned; no threshold was relaxed to reach a verdict. Zero orders
submitted or signed. Nothing realized. Unexplained and worth a check: the
tape is ~50× smaller than the lost earlier run at comparable duration,
consistent with the missing complement.

**S3's substantive question is unanswered.** This is not a negative result
about opportunity.

---

## 4. Stage-6 candidate protocol — FROZEN, TESTED, **DISARMED**

```
STAGE-6-S6P
32360e4c0441a8d7296794bd098e47ae962dabbc3fa406f98327dab40b2d96d8
```

Hard-coded literal, so `assert_frozen()` cannot be vacuous. Derived from
XR2W (`ac6318d5…2562`) by deep-copying its `SPEC` plus a literal `DIFF`;
verification chains XR2W's own check, then the fee schedule's
(`ecc240bd…1629`), then bound coherence, then ~80 named invariants, then
the fingerprint. **XR2W, its spec and its fingerprint are untouched**, and a
test asserts it.

State machine, ten declared terminals:
`ARMING_GATE → PREFLIGHT → PLACE_FIRST_LEG → OBSERVE_FIRST →
CANCEL_FIRST_LEG → PAIR_GATE → PLACE_COMPLEMENT → OBSERVE_COMPLEMENT →
WITHDRAW → DEADMAN (finally) → RECONCILE`

The parts that matter: `complement_qty()` reads `filled_qty` and nothing
else, snapped **down** to the grid — so the complement follows the actual
fill, never the intended one; fills counted once per execution id with
bounds re-checked after **every** fill; two *named* latch slots rather than
a capacity counter, which would have permitted two first legs;
`AMBIGUOUS_SUBMISSION` never retried; deadman in `finally`, ungated; one
post-cancel sweep so a fill racing a cancel is still counted.

**Tests: 50 passed** on the state machine, plus **209** confirming the
proven executor and fee schedule are unchanged. All offline — no network,
no credentials, no arming.

Three findings worth your attention:

- **A real bug in the inherited derivation pattern.** `_apply` assigned
  `DIFF`'s own nested objects into the built spec, so every derived spec
  shared mutable state with `DIFF`: a caller inspecting a copy could edit
  the frozen `SPEC` in place and the fingerprint assertion would *still
  pass*. S6P deep-copies. **This pattern is inherited, so the older specs
  should be checked** — I did not touch them tonight, since XR2W is frozen,
  deployed and independently fingerprint-verified.
- **The pair gate is tighter than $0.96**: it prices *both* post-only legs
  at the **taker** coefficient and counts the rebate as zero, costing ~4¢
  of complement headroom at every price. Reported by `headroom_report()`,
  not hidden. Near-$1 tolerance has no branch at all — it cannot be flagged
  on, only written.
- **The TTE 510 s handover is structural, not measured.** Something must end
  the first leg's turn because both legs share the single outstanding-order
  slot. An equal split of the 780 s placeable stretch is defensible, but it
  is not evidence that 390 s is right for either leg.

`python3 -m analysis.l5.stage6.runner` prints the fingerprint, the lineage
diff, every bound, arming and latch state, the headroom table, the scope
limits, and the exact sentence of what arming would authorize — then exits
on `DISARMED: the owner has not set the four PMUS_S6P_* keys`.

### What this protocol cannot establish, by its own declaration

Profitability (one pair is one sample); that L5 would have acted the same
way (no action-by-action fidelity comparison); that $480/day is reachable
(no capacity, frequency or concurrency measurement); rebate economics
(structurally $0.00 at 1.00 share); residual *skill* (it bounds the
residual, it cannot evaluate it); whether a complement would have filled
better later (one patience path, not the distribution); or that post-only
guaranteed a maker role (absent `isAggressor` stays UNRESOLVED).

## 4b. FORENSIC — where did `qty = 1.00` come from?

Asked and answered from source. **It is an implementation design choice for
observability, bounded by a sub-dollar loss ceiling.** Specifically *not*
any of the other three candidates:

| candidate origin | verdict | evidence |
|---|---|---|
| preregistered economic constraint | **NO** | `BOUND_PROVENANCE["FIRST_LEG_QTY"]` carries `fitted: False` and says in its own words *"Chosen for observability of partial fills, **not for expectancy**."* |
| venue constraint | **NO** | the venue minimum is **0.01** and its grid is 0.01; nothing at the venue requires or suggests 1.00 |
| historical L5 sizing | **NO** | `analysis/l5/stage6/bounds.py` contains **no reference to L5's own sizes at all** — grep for `L5` in it returns nothing, and no size-distribution study is cited |
| arbitrary implementation choice | **partly — but reasoned, not arbitrary** | the stated reason is that 1.00 share yields **100 distinguishable partial-fill states** on the 0.01 grid, where 0.01 share yields one, so a partial fill cannot be told from a full one |

The documented reasoning, verbatim from `bounds.py`: XR2W's 0.01 *"was
right for a one-leg wire test. It is the WRONG size for this experiment,
and not because bigger is better"* — at 0.01 every rebate figure is
structurally $0.00 and partial fills are unobservable.

### The sharp observation this forensic produces

**1.00 share sits in a dead zone.** It is deliberately *below* the rebate
cent floor of **1.62** shares (`REBATE_IS_ASSUMED_ZERO`), so it is:

- large enough to observe partial fills (100 states), **but**
- too small to observe rebate economics **at all** ($0.00 by rounding), and
- 100× the loss exposure of 0.01

So the chosen size buys partial-fill resolution and pays for it in dollars,
while still forgoing the rebate measurement that **1.62** would supply for
roughly 62% more risk. Whether that trade is right is a judgment about
*what we are trying to learn*, which is exactly the question §9's
measurement protocol frames.

**Per instruction, the size is NOT changed tonight.** The frozen
1.00-share candidate and its INCONCLUSIVE / DO-NOT-AUTHORIZE verdict stand
exactly as they are. This is a finding for your review, not an action.

---

## 5. Historical / shadow evidence — **DOES NOT SUPPORT AUTHORIZATION**

Lane 5's verdict, from `analysis/l5/stage6_validation/
STAGE6_HISTORICAL_VALIDATION.md`:

**The primary answer is INCONCLUSIVE.** Under Stage-6 the first leg is a
passive maker order, and in **95.58%** of evaluable historical states
whether it would have filled is **UNKNOWABLE** from the data we hold. The
archive is a *book* archive — aligned ladders written on change, **no trade
prints, no sequence numbers**. A resting bid at `p` placed when size `Q`
already sat at `p` joins *behind* `Q`, and a book-only feed cannot reveal
whether our own quantity traded. On L5's own coins the unknowable fraction
is 95.40%.

The repo's own frozen fill standard (`EVIDENCED_LEVEL_CONSUMED`) collapses
that to 8.49% — but `S2_FINDINGS.md` §2 already documents that proxy as a
**selection bias, not a solution**. Both bucketings are carried separately
and never merged.

**And inside the proxy's own adverse-selected subset, the economics fail
decisively:**

| | needed to break even | achieved | per first leg |
|---|---|---|---|
| all coins (n = 24,439) | **78.73%** matched | **67.72%** (95% CI 65.62–69.81) | **−$0.0372** |
| L5's own coins (btc, eth) | 78.73% | **57.90%** | **−$0.0879** |

So the two standards disagree about *measurability* but **agree on the
verdict**: on the generous, biased-favorable standard the protocol loses
money; on the strict standard it cannot be evaluated at all. Neither is a
case for risking capital.

## 6. Maximum dollars at risk

**Overnight: $0.00.** No live order placed, no owner value set, no
order-capable image deployed. **A6 remains FILLED but NOT REALIZED** —
settlement unresolved.

**If a Stage-6 cohort were authorized** — note the size, which is NOT the
0.01 of A6:

| bound | value |
|---|---|
| quantity | **1.00 share** (Lane 4's frozen `bounds.py`, not 0.01) |
| first-leg price ceiling | 0.60 |
| pair ceiling | 0.96 fee-inclusive |
| worst residual, one session | **$0.60** notional, **−$0.60** at settlement — Lane 4's arithmetic bound, confirmed empirically |
| worst drawdown, one probe per market serially (n = 727) | **max DD $13.22**, final −$5.71, longest losing streak 3 |
| maker rebate | **$0.00** by structure (cent floor at 1.00 share) |

`MAX_DAILY_REALIZED_LOSS_USD = $0.60` holds only on "no automatic repeat" —
which is the first assumption a *cohort* retires. That is the bound to
scrutinize before authorizing repeats, not the per-session one.

## 7. Exact live authorization proposed — **NONE. I recommend against it.**

I am not proposing a first matched-pair cohort, and I would decline to
authorize one on tonight's evidence.

The reasoning is not caution for its own sake. Stage-6 is a *matched-pair*
protocol: its entire economic case rests on the first leg becoming matched
cheaply and often. Lane 5 says we cannot measure how often that happens
(95.58% unknowable), and that where we can construct a measurement at all,
it comes out **below break-even** — 67.72% against the 78.73% required, and
57.90% on the coins L5 actually trades. Authorizing live dollars would not
be testing the thesis; it would be paying to find out what the data already
declines to support.

**Two caveats I will not let my own verdict overstate**, both raised by the
lane itself:

1. The selection bias runs **against** the policy, so this does **not**
   prove Stage-6 unprofitable. "We cannot measure it" forbids "it loses
   money" from being stated that strongly.
2. The result is **cross-venue**: the archive is Polymarket international
   while the fee schedule, rebate floor and grid are PMUS.

Also: all 28,062 evaluable states fall inside `REGIME_2026-08-19_ON`, and
the three earlier canonical regimes have **zero** evaluable states — so
pre-08-19 Stage-6 behavior cannot be validated with the data we hold at
all. The whole evaluable universe is **2.1% of L5's 166-day span**, a
ceiling rather than a sampling choice.

What would change my answer: a **queue-aware** fill standard rather than a
book-only one. Historical L2 replay is exhausted — more of it will not move
this. Only owning the orders, or a print-bearing archive, can supply it.
See §8.

## 8. The single highest-value thing to do next

**Close the queue-position blind spot, because it is the binding constraint
on every matched-pair question we have.**

Two independent lanes failed tonight for the *same underlying reason*, and
that convergence is the night's real finding:

- **S3** could not evaluate because its tape never carried the complement
  side (`complement_never_observed: true` in all five markets).
- **Lane 5** could not evaluate because a book-only archive carries no trade
  prints or sequence numbers, so passive fills are unknowable in 95.58% of
  states.

Both are instrument limits, not statements about opportunity. And both are
addressed by the same thing: **the dual-token CLOB websocket built tonight**
(`src/l5_clob_ws.py`). It is the strict-timing arm — both tokens on one
socket sharing one receive clock — and it is the only path we have toward
per-event ordering that a REST snapshot fundamentally cannot give.

Concretely, in priority order:
1. **Prove the websocket live** (both tokens streaming, `SubscriptionProof`
   PROVEN). Until then its gaps are not quiet books.
2. **Accumulate a tape that carries trade prints and arrival order**, then
   re-ask Lane 5's four questions against it. That converts INCONCLUSIVE
   into an answer.
3. **Re-run S3's question against the new two-token collector**, which
   demonstrably does *not* have S3's defect (`single_token_markets: []`,
   `both_tokens_ok: true`).

Nothing about Stage-6's code needs changing. What it needs is a measurement
instrument good enough to judge it.

### Standing boundaries honored overnight

No live orders. No parameter tuning. No evidence deleted. S3 untouched
until it terminated on its own. XR2W, its spec and its fingerprint
untouched. A6 untouched.
