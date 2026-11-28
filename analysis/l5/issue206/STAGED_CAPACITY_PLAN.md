# $200/window capacity: design and staged validation

**PLANNING ONLY. Nothing here is authorized, armed, deployed or requested.**
No Railway variable, capital limit, budget or taker switch is changed by any
commit that carries this document.

## 1. Correctness first: what the data does and does not prove

**The review finding is correct and is now reflected in the code.** The
redemption identity — a payout cannot exceed the larger side's holding — is
**necessary and not sufficient**. A window short of rows on its *smaller*
side, or short proportionally on both, passes it and is still incomplete.
The classes are therefore renamed to assert only what is tested:

| class | windows | what it asserts |
|---|---|---|
| `REDEMPTION_CONSISTENT` | 4,720 | the identity holds; **not** that rows are complete |
| `REDEMPTION_INCONSISTENT` | 1,789 | rows are **provably** missing |
| `UNSETTLEABLE` | 46 | no redemption, one-sided, or no trades |

`completeness_evidence()` now reports each independent check separately
rather than collapsing them into one label.

**Two mechanisms were tested; one is refuted.** The captures page contiguous
offsets under a fixed `end`, then re-anchor `end` to the oldest timestamp
seen — and consecutive anchors chain *exactly*, so coverage is continuous.
Timestamp ties at those boundaries were the obvious culprit and the data
says no: a boundary falls inside **3.4%** of inconsistent windows against
**2.4%** of consistent ones. No signal. The remaining mechanism is
**unknown**; negative-risk position merging is the leading candidate, since
the positions capture exposes `negativeRisk` and `mergeable` and merging
would break per-slug redemption accounting outright.

**Correction to my own previous commit:** I wrote that
`activity_gap.jsonl.gz` paged "by time window rather than offset". It does
not — its params are `end` + `offset` + DESC, the same shape as the other
capture. It simply walked far more anchors. The claim is fixed in the module.

### The residual estimate and its uncertainty

| sample | residual P&L / contract |
|---|---|
| 186 outcome-determinable windows (old file) | **+$0.0263 … +$0.0367** |
| **4,720 redemption-consistent (repaired)** | **−$0.0236** |
| 1,789 inconsistent | +$1.1641 — *impossible for a binary* |

**The sign is not established.** The defensible range is roughly
**−$0.024 to +$0.037**, and the two credible ends disagree about direction.
The gate's prior sits at **−$0.0236**, the conservative end, which is where
it belongs. The matched edge by contrast is stable across every cut ever
measured — **+$0.0356, +$0.0359, +$0.0350** — and that stability is the
reason the repair is trusted rather than only its convenient half.

### The $1 cash gate versus the worst-case gate, on real executor state

Measured by letting the executor fill its own orders — **not** by the
double-filled simulation that produced my bogus $2.1897:

| configuration | orders | pairs | binding refusal |
|---|---|---|---|
| $1 cash + $1 risk | 2 | 1 | `CASH_COMMITMENT:1.3766>1.00` |
| **$20 cash + $1 risk** | **8** | **4** | **none — the risk gate never binds** |
| $20 + $20 | 6 | 2 | `MAX_LIVE_ORDERS` |

`committed` is incremented in `apply_fill` **only**, never on submit: there
is no double counting. It is reserved capital, correctly accounted under
`NO_RECYCLING`, and correct *as a cash bound*.

## 2. The $200 capacity design

`src/l5_capital_plan.py` tracks six quantities separately, because they
answer six different questions and one number cannot:

`cash_committed` · `outstanding_reserve` (per side via **max**, never the
sum — both sides filling *pairs*; never the signed net, which is the 1415z
error) · `unmatched_worst_case` · `fees_paid` · `realized_settled` ·
`buying_power` = envelope − committed − reserve.

**A matched pair's $1.00 redemption is reported and deliberately excluded
from buying power.** It is certain and risk-free and it is **not cash** until
the market settles. `test_buying_power_never_grows_when_a_pair_completes`
pins it.

### Capacity at each envelope, at a 0.95 pair cost

| envelope | pairs/window | pair P&L at +3.5¢ | worst case if **every round strands one leg** |
|---|---|---|---|
| $1 | 1 | +$0.035 | $0.48 |
| **$10** | 10 | +$0.35 | **$4.75** |
| **$25** | 26 | +$0.91 | **$12.35** |
| **$50** | 52 | +$1.82 | **$24.70** |
| **$200** | **210** | **+$7.35** | **$99.75** |

Cash **divides** rather than revolves inside a window, so these are floors,
not estimates.

**Two independent routes to the target agree.** At L5's measured +$0.0350
per matched contract, $200/window gives **+$7.35/window ≈ $705/day** over 96
windows. At L5's realised **2.80% of turnover**, $200 of turnover gives
**+$5.60/window ≈ $537/day**. Both bracket the $480/day target, which is
the first time any arithmetic in this program has reached it — and both rest
on *L5's* fills, not ours.

**The worst case is the number a stage gate must be set against, not the
edge.** At $200, half the pairs stranding costs **$99.75** in a single
window. The edge is 7% of the worst case per window.

## 3. Offline stage gates — status

| gate | where | status |
|---|---|---|
| simultaneous + partial fills | `test_l5_multi_round_lifecycle` | ✅ |
| multiple completed rounds | same | ✅ |
| cancel/fill races, restarts | `test_l5_exposure_sufficiency` | ✅ |
| unmatched inventory in sharp moves | `test_l5_multi_round_lifecycle` | ✅ |
| capital exhaustion + settlement recovery | same | ✅ |
| repricing vs preserving a complement | `test_l5_own_order_fill_evidence` | ✅ |
| daily loss halt | `test_l5_first_round_risk_controls` | ✅ |
| envelope correct at $1/$10/$25/$50/$200 | `test_l5_capital_plan` + executor runs | ✅ |
| **fee and rebate uncertainty** | — | ⚠️ fees modelled, **no rebate ever confirmed credited** |
| **watchdog intervention** | — | ⚠️ exercised in deploy verification, not offline |

### Metrics available offline

Completed pairs, unmatched residual qty and basis, **fill latency** (median
17.1s, KM P(fill) 81.8% by 73.4s from our own 34 orders), realized
settlement P&L (**−$15.6109 all-time, ours**), capital utilization
(committed / envelope, per the accounting above). **Drawdown is not
measurable**: 6 settled windows is not a drawdown series.

## 4. Staged live validation — design only

**No stage is requested. Each requires its own separate approval, and a
profitable single window is explicitly insufficient to advance.**

| | **Stage 1** | **Stage 2** | **Stage 3** | **Stage 4** |
|---|---|---|---|---|
| cash per window | **$10** | $25 | $50 | $200 |
| windows | 2 consecutive BTC15m | 4 | 8 | 20 |
| worst case per window (half strand) | **$4.75** | $12.35 | $24.70 | $99.75 |
| aggregate cash limit | $10 | $25 | $50 | $200 |
| daily realized-loss halt | **$5** | $12 | $25 | $100 |
| maker/taker | **maker only** | maker only | maker only | maker only |
| max live orders | 2 | 4 | 6 | 8 |
| expected pairs/window | ~10 | ~26 | ~52 | ~210 |
| expected edge/window at +3.5¢ | +$0.35 | +$0.91 | +$1.82 | +$7.35 |

**Stopping rules, identical at every stage:** stop immediately on any
unresolved cancel, any `BOOK_UNCERTAIN`, any watchdog trip, any unmatched
residual above the stage's worst case, or the daily halt. Disarm
automatically at the end of the authorized windows. No automatic relaunch
after a crash or a completed stage.

**Required settlement evidence to consider the next stage:** every order
reconciled by venue id to a terminal state; `L5_LIVE_SETTLED` with
`VENUE_SETTLEMENT_PX` for every window; matched-pair and residual P&L
reconciled **separately**; fees recorded separately from P&L and no rebate
counted until credited; and **positive aggregate settled realized net
across all the stage's windows** — not one good window.

## 5. Readiness

**Stage 1 is NOT READY.** Three blockers, none of them code:

1. **The residual sign is unresolved** (−$0.024 … +$0.037). At $10/window
   with ~10 pairs, a residual at the pessimistic end costs more than the
   pair edge earns. This is the single number that decides whether the
   design works, and only our own fills can settle it.
2. **No confirmed rebate**, so every figure above is gross of an incentive
   that may not exist for our account.
3. **The $1 → $10 step is a capital increase**, which is the owner's
   decision and is not requested here.

**Our confirmed settled realized net remains −$15.6109 all-time.** Every
positive figure in this document is L5's money or arithmetic, never ours.

---

# Addendum: pair completion, residual risk, and the loss-at-risk budget

## A1. Why every unmatched leg we have ever carried existed

Reconstructed from the venue tapes. Each traces to **one** of four
mechanisms, and which one matters because each has a different guard:

| window | role | own | cancelled at | rest at recovery | mechanism |
|---|---|---|---|---|---|
| 0100z | first leg | 0.28 | 44.8s | **44.13s** | recovery gate |
| 0115z | **complement** | 0.70 | 21.0s | **16.62s** | recovery gate |
| 0145z | **complement** | 0.61 | 13.6s | **16.79s** | recovery gate |
| 0100z 11-07 | complement | 0.4600 | — | — | completion requote → 0.51 target (a **1.0000** pair, worth $0.0000) |
| 0100z 11-07 | complement | 0.4900 | — | — | completion requote, same |
| 1230z | complement | 0.4900 | 124.0s | — | ACC deadline TTL (120s) |
| 2015z | first leg | 0.5700 | 120.9s | — | ACC deadline TTL |

Plus a fourth class — legs **never replaced** once the round was spent,
fixed by the round-retirement work.

**The TTL cancellations cost nothing.** Past the last observed fill our own
Kaplan–Meier curve puts the conditional chance of filling at **0.0%**, so
the mandatory deadline is not what cost us pairs.

## A2. The rest floors are not what saves the costly cancels

**0115z and 0145z were cancelled at rest 16.62s and 16.79s — above *both*
floors** (`recover_min_rest_s` 15.0, `REQUOTE_MIN_REST_S` 14.0). So the
minimum-rest logic does **not** reach the two cancellations that actually
cost us pairs. It stops the sub-7s churn and nothing more.

**PRESERVE is what prevents them**, for its own reason: a resting quote has
the same worst case as holding and strictly better upside, so cancelling it
is never an improvement. Verified at all three historical prices.

**And it does not become blind patience.** Above redemption, preserving is
refused outright:

| resting pair | PRESERVE |
|---|---|
| 0.94 (0115z) · 0.96 (0145z) · 0.97 (2015z) · 1.00 | **feasible, selected** |
| 1.05 · 1.20 | **infeasible — `RESTING_PAIR_ABOVE_REDEMPTION`** |

So a quote is held while it can still lock a gain and never while it would
lock a loss. Both floors and PRESERVE are needed; neither substitutes.

## A3. The loss-at-risk budget — it already exists, and it is not cash

`ValidationGates.max_worst_case_deficit_usd` enforces `C − min(up, dn)`:
the most the book can lose **however the window resolves**, maximised over
the four corners of which outstanding orders fill (nothing / UP only / DOWN
only / everything) — exact rather than conservative — with fees included and
never netted, and failing closed as `WORST_CASE_DEFICIT_NO_INVENTORY` when
the book cannot be read.

**No second budget is added.** A second opinion beside it could drift from
the one enforced, which is the hazard this program has already paid for
twice. What is now pinned by test is that it is **independent of cash** and
**binds first when set below it** — at $200 cash with a $0.01 deficit bound
the engine places **nothing**.

**That is what makes "$200 of cash is not permission to risk $200"
enforceable**, and it gives the honest answer to *maximum modelled loss per
window*: **it equals the deficit bound by construction**, because no order
may leave the book able to lose more than it.

| stage | cash | **loss-at-risk budget** | if the bound were absent | daily halt |
|---|---|---|---|---|
| 1 | $10 | **$1.00** | $4.75 | $5 |
| 2 | $25 | **$2.50** | $12.35 | $12 |
| 3 | $50 | **$5.00** | $24.70 | $25 |
| 4 | $200 | **$20.00** | $99.75 | $100 |

The fourth column is what a window could lose with cash as the only bound.
The third is what it can lose with the deficit bound set. **The ratio of
worst case to expected edge is ~13.6× at every scale** — it is
scale-invariant, since both terms are linear in the pair count — so a gate
set against the edge would be set 13× too loose.

## A4. Completed-pair rate versus unmatched-leg rate

| source | pairs | unmatched | note |
|---|---|---|---|
| **our own 34 venue orders** | **8 of 11 rounds (72.7%)** | 3 (27.3%) | and **all three** self-inflicted |
| L5, repaired union | 5,089,010 ct matched | 1,240,600 ct (**19.6%**) | 4,720 windows |

Our pair rate is *higher* than L5's contract-weighted ratio, on a far
smaller sample, and every residual we carried was created by our own
cancellation — not by the market refusing us.

## A5. Proven, assumed, and unknown — kept apart

**Proven (venue-confirmed):** our 34 orders and their terminal states; 8 of
11 rounds paired; median fill 17.1s; the four cancellation mechanisms; our
settled realised net **−$15.6109**; that PRESERVE holds ≤1.00 pairs and
refuses above; that the deficit bound binds before cash.

**Assumed (measured on someone else's fills):** the +$0.0350 matched edge
and 2.80% of turnover, from L5's repaired union. Our own matched sample is 8
pairs.

**Unknown:** the residual's **sign** (−$0.024 … +$0.037 across credible
subsamples); whether any **rebate** is credited to our account — none ever
has been, so every figure here is gross of it; whether our 72.7% pair rate
survives at L5's quote frequency; and the mechanism behind the 1,789
redemption-inconsistent windows.

## A6. Remaining blocker to a separately authorized $10 test

**One, and it is not code.** The residual sign is unresolved, and at Stage 1
size it dominates: ~10 pairs earn **+$0.35** at the measured matched edge,
while the loss-at-risk budget allows **$1.00**. A residual at the
pessimistic end of its range costs more than the pairs earn. Only our own
fills can settle it, which is what a Stage 1 window would measure — so the
blocker is also the reason to run it, under the $1.00 deficit bound rather
than the $10 of cash.

`test_stage1_readiness_is_blocked_on_the_residual_SIGN_not_on_code` pins
this so it cannot be quietly dropped.
