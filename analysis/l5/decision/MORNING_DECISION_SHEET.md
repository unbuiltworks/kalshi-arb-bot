# Morning decision sheet — 2026-10-07

One page. Sections marked **PENDING** are lanes still running at the time
of writing; each will be filled from its own verdict commit, never from my
summary of it.

---

## 1. Collector health and coverage — HEALTHY

Service `l5-book-shadow`, project `l5-book-shadow-public`, pinned by SHA to
`84e277297`. Public-data-only project: no credentials, no order-capable
module, mutation-incapable boot gate passing 4/4.

**Overnight accumulation, 6h 8m uptime, 1,000 cycles:**

| | |
|---|---|
| two-token book rows | **69,840** |
| L5 activity rows | **2,163** (2,690 on the full tape incl. pre-restart) |
| markets / tokens | 35 / **70** — `single_token_markets: []` throughout |
| `http_429` | **0** |
| measured request rate | **197.1 RPM** against the 240 cap |
| BOOK_ONLY intervals | **one, closed** — no new outage all night |

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

## 2. What happened to L5 after August — **AUDITED**: posture survived, but it is shrinking

**The headline survives an independent source audit; three of the supporting
claims did not.** Full audit: `analysis/l5/post_august/REBATE_SOURCE_AUDIT.md`.

**What verified.** $34,822.79 is **CREDITED, $0.00 modeled** — a bare sum of
the venue's own `usdcSize` over `type=MAKER_REBATE` rows, present on 82/82
raw incentive rows, each carrying its own on-chain `transactionHash`. No
theta, price or rate arithmetic anywhere in the incentive path, so
CLAUDE.md's "not until credited and source-accounted" rule is met and the
number may be stated. An independent loader reproduced all three stream
totals and the entire 37-day per-day table **to the cent**. Identity is
*stronger* than first claimed: 459,808/459,808 rows carry both `name` and
`proxyWallet`, zero nameless, zero contradicting. `REWARD`'s post-09-06
zeros are **measured**, not gaps (184 explicit HTTP-200 `type=REWARD`
envelopes returning 0 rows). No double counting: the 09-23 harvest-seam
overlap was real and correctly handled (exactly 2 duplicates, both caught);
streams are disjoint by txHash.

**What I had wrong, and now correct:**

| claim | correction |
|---|---|
| "`MAKER_REBATE` is the **largest** stream" | **INVALID — different windows.** 37 paying days vs 6. On `REWARD`'s own 6 paying days: **REWARD $8,229.66 vs MAKER_REBATE $6,261.74** — REWARD 1.31× larger, and larger per paying day ($1,371.61 vs $1,043.62) and per notional (83.8 vs 63.8 bps). It is the **surviving** stream, not the larger one. |
| (unstated) | **It is DECAYING.** $1,043.62/day and 63.8 bps in early September → **$385.74/day and 42.7 bps in October**. Rebate/day fell **63%** against a 38% fall in notional/day, so this is not just thinner volume. Peak 09-13 $2,470.86 → 10-07 $446.27. |
| "0 truncated windows, all 37 days" | attested for **23 of 37**. The three pre-existing harvests carry no truncation record, so their fill counts are strictly **floors**. Low materiality for incentives (one credit/day for 37 consecutive days is self-checking). |
| "a maker rebate **is** what makes above-$0.96 rational" | **does not hold.** The report's own arithmetic undercuts it: the ex-fee pair leg locks ~3.32¢/pair-share, **3.5× the entire $45,852.44 incentive stack**. No rebate is *required* for the pricing to be rational. |

**Newly surfaced:** the credited $34,822.79 is **1.62× the modeled all-maker
ceiling** of $21,552.12 (maker theta 0.0125 on every fill). Not a
double-count — the two are never summed — but it means **L5's rebate rate is
not reconstructible from the frozen public schedule.** We cannot even size
what he is getting.

Also settled and now closed: the `realizedPnl`-includes-commission question
is **moot** — a full field inventory shows no `realizedPnl`, `commission` or
`fee` field exists on this feed at all.

### What this does to "not safe to copy"

**It stands, and the reason gets stronger — but drop the rebate-dependence
inference.** Keep the two measurements: (i) L5 receives a credited rebate we
have not source-confirmed for ourselves at any rate and **cannot even
size**; (ii) whether his pair leg survives fees turns on an unobservable
maker/taker split, which brackets his keep between ~$160,793 and ~$50,008.

`<= 0.96` stays as it is; near-$1 tolerance stays locked.

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

## 9. Sequential live measurement protocol — DESIGNED, **NOT AUTHORIZED**

`analysis/l5/measurement/SEQUENTIAL_MEASUREMENT_PROTOCOL.md`, with an
offline calculator and 28 passing tests. It answers the question "how
quickly could our own tiny orders resolve the 95.58% UNKNOWABLE?" — and
**recommends no size**, by design.

### Stopping gates

| estimand | method | expected n | worst case |
|---|---|---:|---:|
| E1 first-leg fill probability | Beta(1,1) posterior width 0.10 | 139 placements | **385** |
| E2 completion \| fill | truncated SPRT, 0.6772 vs 0.7873 | **81–89 fills** | 385 |
| E3 time-to-completion p90 | Kaplan–Meier + order-statistic CI, ε=0.05 | 139 | → 206 fills |
| E4 residual duration p90 | same | 139 | → **431 fills — binding** |
| E5 per-share economics ±$0.02 | — | — | 261 fills |

The SPRT is the elegant part: it tests the *actual* decision — is completion
above or below the 78.73% break-even — and under either truth it expects
**~85 fills**, against 1,537 for a blind ±0.025 interval.

### Worst-case dollars, all four sizes

Per placement, `qty × $0.60`: **$0.0060 / $0.0600 / $0.6000 / $0.9720**.
To reach **all** gates, cascaded through the unknown fill rate:

| `p_fill` | placements | 0.01 | 0.10 | **1.00** | 1.62 |
|---|---:|---:|---:|---:|---:|
| 0.10 | 4,310 | $25.86 | $258.60 | **$2,586.00** | $4,189.32 |
| 0.25 | 1,724 | $10.34 | $103.44 | **$1,034.40** | $1,675.73 |
| 0.50 | 862 | $5.17 | $51.72 | **$517.20** | $837.86 |
| 0.87 | 496 | $2.98 | $29.76 | **$297.60** | $482.11 |

The spread *is* the headline: the cost of the full programme is uncertain by
more than an order of magnitude, because it depends on the very quantity
E1 exists to measure. The 0.87 row is the cheapest and **least credible** —
it is the biased proxy's rate.

### The rebate cliff is PRICE-DEPENDENT — sharper than we assumed

`ROUND_HALF_EVEN` sends exactly $0.005 to **$0.00**, so the floor is strict.
And the minimum rebate-bearing size *moves with price*:

| `p` | min contracts |
|---|---:|
| 0.50 | 1.61 (global minimum) |
| 0.45 / 0.55 | **1.62** ← the figure `bounds.py` quotes |
| 0.60 | **1.67** |
| 0.30 | **1.91** |

So **1.62 earns a rebate only for `p ∈ [0.45, 0.55]`**. Covering the whole
first-leg range `p ≤ 0.60` needs **≥ 1.91 shares**. Your "first
rebate-bearing size ≥ 1.62" is right at the money but not across the band —
worth knowing before choosing.

**0.01 and 0.10 are structurally rebate-blind at every price** — $0.00
always, not "measured imprecisely". And 0.01 is the venue grid itself, so it
has exactly one fill state: a partial fill is indistinguishable from a full
one.

### The honest framing

Every size resolves the UNKNOWABLE problem — owning the order is what does
that, not its size. Every size measures E1–E5 per share. **0.01 buys 100×
the observations per dollar at risk.** What the small sizes cannot buy is
*partial-fill structure* and *rebate economics*.

So the question is not "which size" but **"which questions, in which
order"** — and a two-stage design (cheap sizes for the probabilities, a
rebate-bearing size only once the probabilities justify it) is available
without being recommended here.

### What it cannot establish

Queue-position *mechanism* (only outcomes); that our fill rate equals L5's;
anything about sizes we do not run; and — the one I would weigh heaviest —
**a tiny order's fill probability need not transfer to a realistic one.**
Queue priority is price-then-time, so size should not affect *whether* we
are filled, but it does affect how much of the level must be consumed. The
document states where transfer holds and where it does not, and labels its
one extrapolation a model rather than a measurement.

---

### Standing boundaries — verified held, all night

| boundary | status |
|---|---|
| live orders placed | **ZERO** |
| dollars at risk overnight | **$0.00** |
| Stage-6 | **DISARMED**, `armed: False`, no owner value set, no order-capable image deployed |
| XR2W executor, spec, fingerprint | **untouched** (asserted by a test) |
| A6 | **FILLED but NOT REALIZED** — settlement unresolved |
| evidence deleted | **none** |
| parameters tuned or fitted against a result | **none** |
| S3 | untouched until it terminated on its own |

### Open items for you, in priority order

1. **The freeze bug — audited, and less severe than I said.** The aliasing
   is real in **5 specs**, but `assert_frozen()` **FAILS on all five** after
   an in-place mutation: the alias is between `SPEC` and `DIFF`, and `SPEC`
   is what `fingerprint()` serializes, so corrupting `DIFF` necessarily
   moves the sha off the literal. **Every fingerprint in the repo is a
   hard-coded literal** — that discipline is what contains the bug, and my
   "assertion still passes" claim was wrong.
   The real silent finding is **`diff_report()`**: it reads `DIFF` under
   **no assertion**, and the runners print it as the authoritative
   derivation record. A fingerprint mismatch is loud; a falsified
   `keys_added`/`keys_changed` is not — and that is precisely what an
   operator reads to find out *why* a freeze failed.
   Exposure is **latent**: nothing mutates a spec today, but the runners
   hand aliased objects out **by reference** (`pre["wire_serialization"] =
   X.SPEC[...]` in three scripts), so one future line annotating `pre`
   would be a live runner writing into a frozen spec *after* its check
   passed. Nothing deployed is at risk. Recommended fix, unapplied:
   deep-copy at the three runner embed sites — touches scripts not specs,
   changes no fingerprint, closes the only live route. **Do not touch
   XR2W**: deployed, independently verified, carries A6, and inert.
2. **The `qty = 1.00` decision** — §4b. A dead zone between partial-fill
   observability and rebate observability. Unchanged per instruction.
3. **Prove the websocket** from the deployed host; my first hostname did not
   exist, and the endpoint cannot be settled from this container.
4. **Choose the measurement question order** — §9 — which implies the size.
