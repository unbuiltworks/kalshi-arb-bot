# Step 1 — Opening-policy decision report

Read-only analysis of existing data. Production engine remains disarmed
(`PMUS_Q1_LIVE=NO`). Nothing deployed, no service created, no branch created.

Sources, all pre-existing:
`data/l5/l5_btc15m_realized_economics.json` (686,395 L5 series rows) ·
`L5_STRATEGY_FINDINGS.md` · `L5_STRAND_POLICY_EVALUATION.md`
(`l5_residual_state_replay.py`, 2,216 L5 windows) · `L5_FOUR_POLICY_REPLAY.md`
(`l5_strand_policy_replay.py`, 290 authenticated residual frames) ·
`analysis/gemini/complement_reachability*.json` · deployed `c39183a3` source.

---

## The reference: what L5 actually is

From its own realized Polygon activity, `btc-updown-15m`, 165.5 days:

| | |
|---|---|
| windows touched / resolved | 5,546 / 5,533 |
| mean P&L per window | **+$62.52** (median +$44.66) |
| win / loss windows | 4,056 / 1,477 → **73.3% win rate** |
| total | +$345,941 (~$2,090/day) |
| return on deployed cash | **5.70%** |
| cash deployed | $6,070,393 → **~$1,095 per window** |
| legs per window | **median 107**, mean 122.7, p95 268, max 729 |
| windows/day | 33.5 |

**L5 does not control stranded inventory. It carries it.** Balanced at expiry
is **42/1671 = 2.5%**, median residual **55 shares**. Median pair unit cost
**0.9714**, and only **60.9%** of its pairs are below $1.00 — so ~39% are
locked losses, and it still wins 73.3% of windows. Its edge is scale,
selection and two-sided presence, not pair hygiene.

Second-leg completion on 15m windows: median **28 s**, p25 12 s, p75 62.75 s.

### The reproduction gap, quantified

| | L5 | us (deployed) |
|---|---|---|
| deployed per window | ~$1,095 | **$12** committed cash |
| legs per window | median **107** | opening leg ≤ **3 contracts** |
| simultaneous quotes | two-sided, continuous | **`max_live_orders = 1`** |
| pair cost accepted | median **0.9714** | ceiling **0.96** |

`max_live_orders = 1` is structural: the executor states *"with one live-order
slot the complement can never rest beside the first leg — it is refused
MAX_LIVE_ORDERS until that leg leaves the book."* Every pair is therefore two
**sequential** maker fills. L5's edge comes from continuous two-sided presence
across ~107 legs. We cannot reproduce that mechanism at 1 slot and $12.

Note also our ceiling **0.96** sits *below* L5's median **0.9714** and *above*
our own measured completion break-even **0.9532**. We select from the left tail
of L5's own pair-cost distribution.

---

## Q1 — What conditions produced the successful live pairs

Out-of-sample, L5's own 2,216 windows with a first leg (1,610 paired, **72.7%**),
completion by **opening own price** — known at decision time:

| opening own px | n | completes | EV / contract |
|---|---:|---:|---:|
| 0.0–0.1 | 95 | 28.4% | −$0.0017 |
| 0.1–0.2 | 114 | 67.5% | +$0.0322 |
| 0.2–0.3 | 109 | 80.7% | +$0.0485 |
| 0.3–0.4 | 142 | 80.3% | +$0.0273 |
| **0.4–0.5** | **315** | **88.9%** | **+$0.0566** |
| 0.5–0.6 | 494 | 85.2% | +$0.0208 |
| 0.6–0.7 | 408 | 76.2% | **−$0.0633** |
| 0.7–0.8 | 257 | 60.7% | **−$0.2220** |
| 0.8–0.9 | 184 | 46.7% | **−$0.3971** |

`EV(p) = P(complete|p) × $0.1199 − (1 − P(complete|p)) × p`, mean pair profit
measured at **$0.1199/contract**.

Also favourable to our lane: **15-minute windows complete 93.3%** vs 5-minute
65.7%; **balanced books (0.35–0.65) complete 84.2%** vs lopsided (<0.20 or
>0.80) **48.8%**.

**The profitable condition is narrow and identifiable before opening: a
balanced book, 15-minute window, opening own price 0.20–0.60, best 0.40–0.50.**

## Q2 — How stranding differs

Two distinct failure modes, both measured:

**(a) Opening too expensive.** EV turns negative **above 0.60**. The campaign's
one strand, 0130z, opened at own **0.62** — the 0.6–0.7 bucket, 23.8% strand
probability on 408 out-of-sample windows. *"It was not unlucky; it was the
second-worst-selected leg of the campaign."* The existing
`MIN_FIRST_LEG_PRICE = 0.20` guards the cheap end; **there is no guard at the
expensive end, and that gap is exactly what 0130z walked through.**

**(b) Complement coverage, not absence of flow.** In the autonomous shadow
arms: complement reach rate **1.35%** (1 reaching print per 74 quotes),
`extend_to_complement_fill_ratio` **7–8** (it adds inventory 7–8× per
completion), and **87.66% of complement-direction notional arrives after our
last cycle**. Verdict across all 7 arms:
`BROAD_LEAKAGE_UNCHANGED_IN_EVERY_ARM`,
`any_arm_materially_changes_leakage = False`.
(That file is `QUEUE_BIASED / INVALID_FOR_FILL_ECONOMICS` — an **upper bound**
on fills — and is Gemini, not PMUS. Reach/flow/print counts are unaffected by
the bias; fill economics from it are not usable.)

On PMUS the same mechanism shows as `COMPLEMENT_PAIR_ABOVE_CAP`: window 1945z
logged *"twelve COMPLEMENT_PAIR_ABOVE_CAP refusals, nothing ever posted, an
exit worth −$0.0144 at 204 s taken at 600 s for −$0.5348."*

So: we open, then the 0.96 ceiling refuses the complement the book offers,
and the residual strands.

## Q3 — Can the existing planner identify those conditions before opening?

**Partly, and the missing half is one symmetric rule.**

Already in force in `TS.build_orders` / `first_leg_entry_refusal`:
`min_first_leg_own_price` **0.20** · `opening_leg_max_contracts` **3** ·
`opening_notional_budget_usd` (applied to the **dearer** leg, *"the one whose
solo fill is the strand"*) · `max_pair_cost` **0.96** · `fee_floor` ·
`may_enter` / `entry_cutoff_s` 600 / `min_entry_runway_s` 300 ·
`round_allocations` 4.

Missing: **no upper bound on opening own price.** The planner cannot currently
refuse the 0.6–0.9 band where measured EV is −$0.06 to −$0.40.

Also identifiable and unused: book balance. Completion is 84.2% in 0.35–0.65
versus 48.8% lopsided, and the planner already reads the authenticated
bid/ask/depth it would need.

## Q4 — Realistically executable completed pairs per window

| basis | value |
|---|---|
| L5 out-of-sample, windows with a first leg that pair | **72.7%** (15m: 93.3%) |
| our live C6–C8 | **23 pairs / 15 reconstructable windows = 1.53/window** |
| configured ceiling | `round_allocations` **4** |
| hard structural limit | `max_live_orders` **1** → 2 sequential fills per pair |
| complement reach (shadow, upper bound) | **1.35%** per quote |

**Realistic expectation: 1–2 completed pairs per 15-minute window.** The
configured ceiling is 4; no measurement supports more than ~1.5. The stated
goal of *multiple* profitable completed pairs per window is **not supported by
any existing measurement**, and with one live-order slot it is structurally
constrained, not merely untuned.

## Expected net per window

| component | value | power |
|---|---|---|
| matched surplus | **+$0.1933/window**, sd 0.2400 | **t = 3.320** (significant) |
| residual settlement | +$0.0353/window | t = 0.225 (**not** significant) |
| settled realized net | +$3.8861 / 17 windows = **+$0.2286/window** | observed |
| best-band EV | +$0.0566/contract → ~+$0.17/pair at 3 contracts | 315 windows |

So **~+$0.19 to +$0.23 per window** as configured. At $5–20 exposure the
campaign cannot measure its own edge: signal ÷ residual noise ≈ **0.087**.

---

## Q5 — Smallest change that improves expected settled net

Two candidates, from two independent analyses that must be read together.

**(i) Opening notional budget — already implemented, mis-set.**
`L5_TS_OPENING_NOTIONAL_BUDGET_USD` is **3.00** in production. The
timestamp-causal replay's measured curve:

| budget | net | vs baseline |
|---|---:|---:|
| $0.50 | +$0.3929 | +$0.2673 |
| $0.60 | +$0.4286 | +$0.3030 |
| $0.70 | +$0.4471 | +$0.3215 |
| **$0.80** | **+$0.4503** | **+$0.3247** |
| $0.90 | +$0.3850 | +$0.2594 |
| $1.00 | +$0.2850 | +$0.1594 |
| no cap (baseline) | +$0.1256 | — |

*"Improves at every budget tested. Smooth, broad optimum, no cliff, no
threshold to overfit."* The largest opening notional in that campaign was
**$1.1594**, so **at $3.00 the cap never binds — it is inert.** Setting 0.80
bounds worst-case single-leg strand to $0.80 deterministically and preserves
**all seven** profitable delayed completions. **This is a variable, not code.**

**(ii) Upper opening-price guard — not implemented.** EV sign flips at 0.60 on
2,216 out-of-sample L5 windows; the 0.6–0.7 bucket turns positive only if mean
pair profit exceeds **$0.2030/contract, 69.3% above the measured $0.1199** — so
the sign is robust.

**The tension, stated honestly:** the four-policy replay **rejected** a price
cap *as fitted to our 8 PMUS windows* — non-monotonic in its own threshold,
worse than nothing at 0.65, with 1 of 8 episodes setting the parameter — and
noted the 2,216-window curve *"remains unvalidated for PMUS and for our depth-2
maker ladder."* The out-of-sample support is strong; the PMUS validation is
absent. They are not contradictory: (i) is price-agnostic sizing, (ii) is
selection. The strand evaluation scored the combination at **+$1.1490 with
worst-case residual $0.60**.

### Exact rules recommended

```
OPENING (all must hold, refuse otherwise):
  window is 15m, authorized series, may_enter() true,
    entry runway >= 300s, not past entry cutoff (600s)
  0.20 <= own_px(first leg) <= 0.60          # 0.20 in force; 0.60 IS THE NEW RULE
  book balanced: 0.35 <= wire_mid <= 0.65    # optional second-stage filter
  q = min(by_loss, by_cash, by_budget,
          opening_leg_max_contracts=3,
          L5_TS_OPENING_NOTIONAL_BUDGET_USD / dearer_own_px)   # set budget 0.80
  quantise DOWN to grid; shrink until fee fits; refuse if < venue minimum

COMPLETION (unchanged):
  maker-only, post-only, never cross
  pair_cost <= max_pair_cost 0.96
  completion and replace legs are NEVER throttled by the opening caps
  past entry cutoff: only the residual-REDUCING leg may be placed
```

### The single highest-impact code change

**Add a symmetric upper bound to `TS.first_leg_entry_refusal`:
`MAX_FIRST_LEG_OWN_PRICE = 0.60`.**

- Same function that already enforces the 0.20 floor, consulted in exactly one
  place, and it **can only refuse an OPENING** — it already permits
  unconditionally when the claim state or held quantities say "completion".
- Refuse-only: adds no action type, no taker path, raises no limit, touches no
  completion logic, no TTL, no cutoff, no complement pricing.
- Removes the band carrying −$0.0633 to −$0.3971 EV/contract on 849
  out-of-sample windows.
- ~5 lines plus tests plus a graded row in `l5_runtime_config_verify.py`.

**Zero-code companion:** `L5_TS_OPENING_NOTIONAL_BUDGET_USD` 3.00 → **0.80**.
Already implemented, currently inert, only ever reduces size.

---

## GO / NO-GO

**GO — bounded live validation of (i) + (ii) on the EXISTING L5-followed
path.** That path has demonstrated 23 completed pairs and a statistically
significant +$0.1933/window matched surplus (t = 3.320). Both changes are
reduce-only/refuse-only, add no permission, and bound worst-case strand to
$0.80. Requires: the code change above, its tests, the graded verifier row, one
variable change, a **new experiment id**, and a **fresh bounded approval** —
the installed `ac855ce3…` must not be reused.

**NO-GO — do not build the live intent adapter for the autonomous opening
path yet.** The evidence does not support it:

- The autonomous shadow has produced **0 matched pairs** in every policy
  sampled (1830z: 0 matched / negative modelled nets −$1.34, −$0.48, −$1.01),
  and `PAIR_AVAILABLE:complete` fired **3 times in ~862 decisions**.
- Complement coverage fails by **87.66%** of complement notional arriving after
  we stop quoting, and **none of 7 arms changed it**.
- The adapter is a money-path change requiring the registry, reconciler and
  watchdog to understand a new submission provenance. Building it before the
  shadow forms a single matched pair would be spending money-path risk on an
  unvalidated policy.

**Resume condition for the autonomous path:** the continuous shadow (already
running, no new service needed) must show matched pairs forming at a pair cost
**below the 0.9532 break-even**, at a rate separable from the current ~0.35%,
before the adapter is worth building.

### One correction to the premise

Waiting for L5 is a smaller constraint than it appeared. L5 trades **33.5
windows/day** at a median of **107 legs/window** and acts roughly every 34 s
when active; today's ~2-hour silence is anomalous, not normal. The binding
constraints on pairs-per-window are **`max_live_orders = 1`** and the **0.96
pair ceiling** — not L5's cadence.

---

# IMPLEMENTED — the $0.60 first-leg opening-price guard

`src/l5_two_sided_probe.py` · `src/l5_runtime_config_verify.py` ·
`tests/test_l5_first_leg_price_ceiling.py` (83 tests)

Production remains **disarmed** (`PMUS_Q1_LIVE=NO`). Nothing deployed, no
live authorization requested, and the $3.00 opening-notional cap was **not**
touched.

## What was added

`MAX_FIRST_LEG_OWN_PRICE = D("0.60")`, env `L5_TS_MAX_FIRST_LEG_PRICE`, and
`max_first_leg_own_price()`, consulted in exactly one place: inside
`first_leg_entry_refusal`, after every unconditional permit. New refusal
reason `FIRST_LEG_ABOVE_MAX_PRICE`. The permitted opening band is now
**0.20 <= own_px <= 0.60**, both bounds inclusive.

Strictly refuse-only, and each property is a test:

- **Applies to new opening inventory only.** A claimed leg, or any held
  imbalance (`up != dn`), returns `None` before the ceiling is read —
  asserted by AST, not just by behaviour. A dear *complement* (0.83–0.99) is
  never refused.
- **Does not block completing existing inventory.** The same early permits
  the floor uses, unchanged.
- **Changes no sizing, execution or risk limit.** Asserted unchanged: pair
  cap 0.96, opening contract cap 3, `OPENING_NOTIONAL_MAX_USD` 5, depth
  ticks 2, cutoffs 600/720, runway 300, residual cap 3, round-allocation cap
  4. The predicate calls nothing resembling submit/cancel/arm/save/post.
- **Does not affect unrelated strategies.** Confined to the two-sided
  probe's opening predicate plus one observed telemetry row.

Two design points worth recording:

1. **Neither bound disables the other.** The floor's old `<= 0` escape
   returned before the price was parsed, so routing the ceiling through it
   would have let `L5_TS_MIN_FIRST_LEG_PRICE=0` silently switch off a bound
   the owner never mentioned. Each bound now applies only if itself enabled,
   and the band collapses to "permit" only when both are off.
2. **The config fallback is deliberately asymmetric.** A ceiling below 0.50
   falls back to 0.60 rather than being honoured, because 0.4–0.5 is the best
   measured bucket in the curve (88.9% completion, +$0.0566/contract, n=315)
   and a typo must not be able to refuse it.

## Verification against historical decisions

### In-sample — 20261025 PMUS campaign, through the real predicate

| episode | own px | verdict | outcome |
|---|---|---|---|
| 0030z | 0.55 | OPEN | completed |
| 0045z | 0.50 | OPEN | completed |
| 0115z | 0.48 | OPEN | completed |
| 0100z-B | 0.75 | **REFUSED** | completed **+$0.0344** |
| 0130z | 0.62 | **REFUSED** | stranded **−$1.1594** |

- **openings excluded: 2 of 5 (40%)**
- **profitable pairs lost: 1, −$0.0344**
- **stranded losses avoided: 1, +$1.1594**
- **net economic impact: +$1.1250**, reconciling exactly with the replay's
  own baseline → capped figures (+$0.1256 → +$1.2506).

### Out-of-sample — L5's residual-state replay, 2,118 bucketed windows

| region | windows | share | aggregate EV/contract | mean |
|---|---:|---:|---:|---:|
| refused by **ceiling** (>0.60) | **849** | 40% | **−$155.95** | −$0.1837 |
| retained band 0.20–0.60 | 1,060 | 50% | **+$37.27** | +$0.0352 |
| refused by floor (<0.20) | ~209 | 10% | +$3.51 | +$0.0168 |

Of the 849 the ceiling refuses, **553 would have completed** (pairs
declined) and **296 would have stranded** (avoided). The expected-value
swing from the ceiling alone is **+$155.95/contract across 849 windows**,
and the retained band's mean EV (**+$0.0352**) is 5.2× better than the
refused region's (**−$0.1837**), in the right direction.

## Honest limits on this result

- **The in-sample +$1.1250 is knife-edge and is NOT the justification.** The
  single strand sat at own 0.62, so 0.60 catches it while **0.65 refuses only
  the winner and is $0.0344 worse than doing nothing.** One observation sets
  the parameter in-sample, and `L5_FOUR_POLICY_REPLAY.md` rejected a price cap
  on exactly that ground. The 849-window out-of-sample curve is the support.
- **The floor is not free out-of-sample.** By midpoint bucketing it also
  declines the 0.1–0.2 bucket, which is positive-EV (+$0.0322, n=114), for a
  net +$3.51 of EV declined. That figure is approximate because the 0.1–0.2
  bucket straddles the 0.20 boundary; the ceiling's 849 does not straddle and
  is exact.
- **Unvalidated for PMUS depth-2 maker placement specifically.** The curve is
  L5's own BTC/ETH 5m+15m Polymarket history, not our ladder.
- **Nothing here is realized.** No order has been placed under this guard.

## Test and regression status

- `tests/test_l5_first_leg_price_ceiling.py`: **83 passed**.
- Focused suite (floor, imbalance, ceiling, round allocation, marker leak,
  e2e window, intent gate, adapters, opening caps, notional caps, runtime
  config): **511 passed**.
- Full suite: **14,085 passed / 54 failed / 23 errors**, and the
  FAILED+ERROR set is **byte-identical** to a pristine `06f7b27c` baseline
  run with the same command — so this change introduced **zero** regressions.
  The pre-existing failures are mostly
  `ModuleNotFoundError: No module named 'httpx'`.
- One stale expectation corrected, not weakened:
  `test_at_or_above_the_floor_opens` asserted `0.99` opens. "Above the floor"
  is no longer the same as "permitted", so that case became `0.60` (the far
  edge of the band) with a comment pointing at the ceiling file, which walks
  every cent 0.00–1.00 and asserts the band exactly.

## Next concrete step toward autonomous live trading

Mine the already-running continuous shadow for **matched-pair formation
inside the 0.20–0.60 band**. The shadow records every policy's decision with
its opening price, so the guard's effect on *autonomous* openings is
measurable without deploying it and without a new service.

The gate to clear before the live intent adapter is worth building:
**matched pairs forming at a pair cost below the 0.9532 break-even, at a
rate separable from the current ~0.35%** (`PAIR_AVAILABLE:complete` fired 3
times in ~862 decisions).
