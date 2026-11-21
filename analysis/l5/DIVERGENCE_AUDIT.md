# Behavioural divergence audit: is our code suppressing the strategy?

**Partly yes, and the suppression is protective.** Our engine would attempt
**12.0% of L5's actions and 1.00% of its contracts** in the profitable era. But
relaxing every behavioural restriction produces exactly the activity increase
we want — 0.48 → 0.95 matched contracts per window, 14 → 47 windows with ≥2
pairs — and **loses 1.9× more money**, because our fill process is adversely
selected and gets *worse* the more we quote.

Status: RESEARCH + undeployed code. HALT asserted, nothing armed, service still
on the pinned behaviour.

## 1. Restrictions that cannot be configured at all

Several accessors clamp in the restrictive direction only, so no configuration
could ever express L5:

| knob | accessor clamp | code ceiling | deployed |
|---|---|---|---|
| `entry_cutoff_s` | `_earlier_only` | **≤600s** | 480s *(env-tightened)* |
| `complete_cutoff_s` | `_earlier_only` | **≤720s** | 600s *(env-tightened)* |
| `opening_leg_max_contracts` | downward only | **≤3** | 1 |
| `order_ttl_s` | `min(120, v)` | **≤120s** | 120s |
| `completion_pair_ceiling` | clamped | **≤1.00** | 1.00 |
| `ABS_RESIDUAL_CAP_CONTRACTS` | code constant | **≤5** | k=1 → 1 |
| controller opening slots | `if resting:` | **1** | 1 |

L5's median *last* trade is t=752s (early) / 805s (August) — **beyond both
cutoff ceilings**. Its median peak |residual| is 251 contracts — **50× the
5-contract hard cap**. So the current code cannot reach L5's behaviour at all.

**Two of these are env-tightened below their own code ceiling.** Relaxing entry
480→600 and complete 600→720 needs only *removing environment variables*, no
new mode and no new ceiling.

### The one restriction tighter than the limit it claims to mirror

`maybe_controller_open` refused a new opening whenever **any** order was still
fillable:

```python
if resting:                       # truthy at >= 1
    self.stats["controller_open:ORDER_ALREADY_RESTING"] += 1
```

Its own comment says this mirrors "the executor's own MAX_LIVE_ORDERS gate",
but `max_live_orders()` is **2**. The controller could never use the second
authorized slot for an opening.

**This is the mechanical cause of Campaign E's three fills:** one slot × a 120s
TTL × a 480s entry cutoff caps openings at ⌊480/120⌋ = **4**, and Campaign E
spent OPEN 3/4. Live counters agree — `ORDER_ALREADY_RESTING` **594** against
`ENTERED` **11** over 43 minutes.

## 2. Decision-by-decision audit

Every L5 action walked through our gates in the engine's own order, recording
the **first** gate to refuse it, with that action's own settled value
`qty × ((1 if outcome resolved else 0) − price)`.

### EARLY Mar–Apr, 214 windows, 5,265 L5 actions — the era worth replicating

| first gate to refuse | actions | % | $/contract | **cf @ our clip 1** | kind | configurable? |
|---|---|---|---|---|---|---|
| `RESIDUAL_ABOVE_CAP` | 748 | 14.2% | +0.0583 | **+$43.58** | CAPITAL | HARD >5 |
| `PAST_ENTRY_CUTOFF` | 1,546 | 29.4% | +0.0256 | **+$39.63** | STRATEGY | HARD >600s |
| `OPENING_BAND` | 588 | 11.2% | +0.0133 | +$7.83 | STRATEGY | reachable |
| `PAIR_ABOVE_REDEMPTION` | 693 | 13.2% | +0.0015 | +$1.01 | ECONOMIC | HARD >1.00 |
| `PAIR_COST_ABOVE_CEILING` | 176 | 3.3% | −0.0400 | −$7.04 | STRATEGY | reachable |
| `PAST_COMPLETE_CUTOFF` | 409 | 7.8% | −0.0736 | −$30.12 | STRATEGY | HARD >720s |
| `ROUND_ALLOCATIONS_SPENT` | 475 | 9.0% | −0.0687 | −$32.65 | STRATEGY | reachable |
| *clip size forgone* | 624 | — | +0.0191 | 6,528 contracts | CAPITAL | HARD >3 |

**ATTEMPTED: 630 of 5,265 actions (12.0%), 627 of 62,868 contracts (1.00%).**

The actions we *do* attempt earn **+$0.0220/contract** against L5's overall
**+$0.0049/contract** — our selection is 4.5× better. We simply take almost
none of them.

Three restrictions block L5-profitable decisions (residual cap, entry cutoff,
opening band). **Four are protective** — blocking them *saved* money: round
allocations (+$32.65 saved), the complete cutoff (+$30.12), the pair-cost
ceiling (+$7.04). Extending the completion deadline would have *lost* money in
the profitable era, which is the opposite of my earlier instinct.

### The single-order slot, priced separately

Fill latency is unmeasurable from our records (`venue_event_t` is null on every
fill), so the slot is reported at assumed hold times rather than baked in:

| slot held for | attempts | blocked | blocked L5 P&L |
|---|---|---|---|
| 0s (fills instantly) | 630 | 0 | — |
| 30s | 531 | 712 | +$148.37 |
| 120s (the TTL) | 367 | 1,783 | +$184.74 |

## 3. Counterfactual with OUR fill process — every relaxation loses

Section 2 says what **L5 earned** on decisions we refuse. This says what **we**
would earn taking them, which differs because our residual lands on the losing
side 81% of the time against L5's 57%.

| arm | matched | resid | windows ≥2 pairs | lose% | **NET$ (early)** | NET$ (all 522w) |
|---|---|---|---|---|---|---|
| **A today** (480/600, 1 slot, 4 rounds) | 94 | 110 | 14 | 76% | **−12.84** | **−35.19** |
| B +2nd slot *(already authorized)* | 136 | 103 | 32 | 83% | −15.12 | −52.92 |
| C B +entry 600 *(code ceiling)* | 146 | 114 | 40 | 81% | −14.85 | −51.53 |
| D C +complete 720 *(code ceiling)* | 155 | 105 | 43 | 85% | −15.74 | −56.06 |
| E D +rounds 12 | 166 | 113 | 43 | 86% | −18.63 | −62.08 |
| F E +cutoffs 840/870 | 186 | 141 | 47 | 87% | −24.90 | −80.32 |
| G F +clip 3, k 3 | 507 | 429 | 105 | 87% | **−83.07** | **−264.10** |

**Monotone, and the mechanism is visible in the `lose%` column: 76% → 87%
(early), 81% → 92% (all).** The more present we are, the more of our fills come
from informed sellers. Matched profit does rise (+$10.62 → +$52.31) and the
activity target is met (14 → 105 windows with ≥2 pairs) — the residual loss
simply rises faster.

### The number that settles it

Fully relaxed, we reach **0.95 matched contracts per window. L5's early era ran
128.** The restrictions move us 0.48 → 0.95, a 2× effect; the gap to L5 is
**135×**. Behavioural restrictions account for well under 1% of the divergence.

L5 was a different kind of participant: 24–170 trades/window, $140–$1,593 of
capital per window, continuous two-sided presence. The thing we cannot
replicate is not a cutoff or a cap — it is being the venue's resting liquidity
at size, which is also what made its residual merely random (57%) rather than
adversely selected (81%).

## 4. Ranked list, by economically meaningful divergence

Ranked by **our-size counterfactual**, with the honest verdict on each:

| # | restriction | L5-economics cf | our-economics effect | verdict |
|---|---|---|---|---|
| 1 | `ABS_RESIDUAL_CAP` / k | +$43.58 | **worse** (G: −$83) | do NOT relax — the +$43 is L5's 57% adverse selection, not our 81% |
| 2 | entry cutoff 480→600 | +$39.63 | −$2.0 (A→C net of slot) | free to relax; modelled slightly negative |
| 3 | one-order slot 1→2 | +$148 @30s hold | **−$2.3** | the clearly arbitrary one; still modelled negative |
| 4 | opening band | +$7.83 | tested: ~$0.005/window | immaterial at scale |
| 5 | clip 1→3 | 6,528 contracts | **−$70** | do NOT relax |
| 6 | pair ceiling 0.96 | −$7.04 | protective | keep |
| 7 | complete cutoff 600→720 | −$30.12 | protective | keep |
| 8 | round allocations | −$32.65 | protective | keep |
| 9 | completion ceiling >1.00 | +$1.01 early / +$366 August | guaranteed loss by no-arbitrage | keep, permanently |

**Fill uncertainty.** Every counterfactual in column 3 assumes we are filled at
L5's own traded price, which needs strict price priority or a crossing. Column
4 uses our measured fill process. The two disagree by design and the gap is the
adverse selection; neither is a fill, and no number here is realized P&L.

## 5. The fidelity mode (implemented, not deployed)

`src/l5_fidelity_mode.py` separates **behavioural policy** from
**non-negotiable safety**, as a testable registry:

- **6 policy knobs** with bounded widenings: entry cutoff →840s, complete
  cutoff →870s, clip →10, TTL →300s, controller slots →2, rounds →12.
- **25 safety controls** that may never widen here, including every item the
  owner named: reconciliation, exact-order cancellation, watchdog/deadman,
  exposure, cash, worst-case deficit, daily and cumulative loss halts, campaign
  budget, `max_live_orders`, `L5_LIVE_HALT`, halt latch, shadow lock, owner arm
  token, authorization expiry, and `max_abs_residual` / `ABS_RESIDUAL_CAP`.
- `completion_pair_ceiling` is **safety, not policy**, on purpose: paying above
  redemption is a guaranteed loss, and the L5 era that did it on 18% of pairs
  is the era that lost $1,507 in four days.
- The two lists are asserted **disjoint at import**.

Two guarantees, each tested against literal values so a constant change cannot
silently satisfy them:

1. **Mode off is bit-identical to today**, even when the environment asks for
   something wider — which is what makes the wiring landable while the service
   stays pinned.
2. **Mode on raises ceilings, never defaults.** An unset knob is unchanged even
   in fidelity mode, so headroom must be asked for explicitly.

Fail-closed: only the exact token `ON` enables it. `on`, `1`, `true`, `yes`,
`ON ` all read as off.

78 tests. Full-suite parity re-run on the wired tree.

## 6. Recommended campaign — a measurement, not a profit attempt

I cannot recommend a more active campaign *to make money*: every relaxation is
modelled negative, and recommending one would be asking you to fund a result
the evidence already predicts.

What is worth buying is **the one number replay cannot settle**: our real fill
adverse-selection rate. Everything above pivots on it, it is modelled at 81%
against L5's measured 57%, and it can only be measured from our own fills.

**Proposal — adverse-selection measurement campaign.**

- **Config: today's exact limits.** k=1, clip 1, band 0.30–0.60, ceiling 0.96,
  480/600 cutoffs, maker-only, $20 exposure, $12 cash, $1.50 deficit. Fidelity
  mode **OFF**. The point is to measure the process we actually run.
- **Scale: 40–60 windows** — against Campaign E's one. This is the
  "substantially more active" part, bought with *windows*, not with size or
  relaxed caps.
- **Modelled expected cost: −$2.64 to −$3.95** (at the measured −$0.066/window),
  inside the existing $5 campaign budget. State it as the price of the
  measurement, not as an expected gain.
- **Success criterion is statistical, not P&L:** ≥30 residual observations
  estimates the losing-side rate to about ±8pp, enough to separate 81% from
  57% at ~2.9σ. A result near 57% would reopen every relaxation above; near
  81% closes this policy family for good at our size.
- **Blocker to flag:** `CA.MAX_WINDOWS = 8`, so 40–60 windows needs five to
  eight sequential 8-window campaigns, or an explicit raise of that ceiling.
  The $5 **daily** halt also binds, so it must span 2–3 days.

Only if that measurement comes back materially better than 81% does relaxing
restriction #2 and #3 (entry cutoff, the slot) become worth authorizing — and
those two are the cheapest, since one needs no code ceiling change and the
other uses a slot we already authorized.

## 7. Not supported by this audit

- Raising k or the residual cap (ranked #1 by L5's economics, **worst** by ours).
- Raising clip (−$70 modelled).
- Extending the completion deadline, round allocations, or the pair ceiling
  (all three are protective in the profitable era).
- Paying above redemption, ever.
- Any claim that removing restrictions reaches L5's rate: fully relaxed we
  reach 0.95 matched/window against L5's 128.
