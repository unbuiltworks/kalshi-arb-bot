> **RETRACTED 2026-10-24.** Its L5 figures came from a corrupt dedup (+$45,751 instead of the authoritative +$345,525.64), its action mix from a broken classifier (96.3% COMPLEMENT instead of 44.91% REDUCE), its decomposition from a convention it did not test, and its 66/66 claim from treating a later complement as impossible. See
> `analysis/l5/CRITICAL_REVIEW_RECONCILIATION_20261024.md`
> for the reconciliation and the retractions.

# One recommendation: complete on the fill event, at the price the fill makes available

**2026-10-24.** Stage 1 lost **−$3.7194** venue-confirmed. Live trading is
unarmed and nothing here rearms it. No capital or risk limit is changed.

All L5 figures below come from `data/l5/raw/activity.jsonl` +
`activity_gap.jsonl.gz` — the same sha256-pinned sources as the
realized-economics artefact — reconstructed by
`analysis/l5/l5_sequential_reconstruction.py` and
`analysis/l5/l5_pnl_decomposition.py` in this commit.

---

## 1. What L5 actually does (reconstructed from source, not inferred)

696,860 TRADE rows over 5,546 BTC15m windows, **zero SELL rows**. Actions
classified by INVENTORY, not index:

| Action | Count | Share |
|---|---|---|
| OPEN | 5,546 | 0.8% — **exactly one per window** |
| EXTEND | 19,688 | 2.8% |
| **COMPLEMENT** | **671,172** | **96.3%** |
| FLATTEN | 454 | 0.1% |

**This supersedes the operating model's stated thesis of "EXTEND 66.2% /
SWITCH 18.3%".** By inventory, L5 is not an accumulator who occasionally
completes — he opens once and then spends 96.3% of his actions reducing
imbalance.

**Behaviour by time to expiry** — he stops opening early and completes to the
bell:

| Bucket | n | OPEN | EXTEND | COMPLEMENT |
|---|---|---|---|---|
| T−15..10m | 196,617 | 2.7% | 9.5% | 87.5% |
| T−10..5m | 234,597 | 0.1% | 0.3% | 99.6% |
| T−5..2m | 165,219 | 0.0% | 0.1% | 99.9% |
| T−2..0.5m | 85,435 | 0.0% | 0.0% | **100.0%** |
| T−30s..0 | 14,992 | 0.0% | 0.0% | **100.0%** |

**Cadence:** median **110 legs per window** (p05 30, p95 275, max 756); first
action at median **16s** into the window; median gap between actions **2
seconds** (p95 26s). He never ends flat (1 window in 5,546) and his
**residual/matched ratio is 0.1827** — he matches ~84.5% of the volume he
buys.

### The P&L decomposition, and the out-of-sample split

| | matched | residual | net/window | median pair cost | win rate |
|---|---|---|---|---|---|
| All (n=5,533) | +$188,155 | −$139,125 | +$8.27 | 0.9684 | — |
| In-sample (first 70%) | +$91,043 | −$47,606 | **+$11.23** | 0.9676 | 58.3% |
| **Out-of-sample (last 30%)** | **+$97,112** | **−$91,518** | **+$3.36** | **0.9697** | 52.0% |

Three things follow, and they drive everything below:

1. **His pair price is a stable structural feature, not a decaying edge.**
   Median pair cost 0.9676 in-sample, 0.9697 out-of-sample. The matched
   component *grew* out of sample.
2. **What decayed 3.3× is the residual drag** (−$47.6k → −$91.5k). Carrying
   unmatched inventory is the liability; completing is the durable edge.
3. **No incentives are involved.** `incentives_series` is empty — zero maker
   rebate, taker rebate or reward attributable to this series. His edge is
   pure pair economics.

---

## 2. The precise existing behaviour causing the greatest economic loss

**Our engine commits a first leg on a pair price that the act of filling
destroys, then looks for the complement ~33 seconds later, by which time the
book has gone.**

The arithmetic is an identity, not an average. With bid `b`, a one-tick
spread, and both legs resting `d` ticks behind the touch:

```
planned pair  = 1 − t − 2·d·t = 0.95   (what the entry test prices)
pair AT FILL  = 1 − t −   d·t = 0.97   (what the fill can deliver)
pair ceiling  = 1 − 4·t       = 0.96   (sits exactly between them)
```

`b` cancels in both. Confirmed four times on production records — every
stranded window logged a frozen complement summing with its first leg to
exactly **0.9500** (1500z 0.44+0.51, 1530z 0.40+0.55, 1545z 0.19+0.76).

**Consequence, measured:** in 4 of 5 fill-bearing windows **no complement
order was ever submitted**, because 0.97 > 0.96. The entire **−$3.9594**
residual line is therefore a directional bet the strategy never intended to
take. It is 106% of the campaign's gross loss.

### Proof that OPEN is unconditionally worse than WAIT today

`analysis/l5/inventory_action_scorer.py` scores OPEN / COMPLEMENT / REDUCE /
WAIT in expected net **settlement** dollars, with the two unknowns swept
rather than assumed, and with the conditional structure made explicit:
`P(complete | filled)` and `q` (the held leg's true win probability) are both
conditioned on the fill, because a maker bid `d` ticks under the touch fills
*only* when the market comes down through it — so the fill is itself evidence
the book is leaving, and `q < F`.

Sweeping `P(complete|fill)` over 0…1 and the adverse haircut on `q` over
0…25% — 66 cells — for each of Stage 1's five actual first legs:

| config | OPEN beats WAIT | best EV/contract |
|---|---|---|
| deployed (d=2, spread=1, **cap 0.96**) | **0 of 66 cells** | **+0.0000** |
| same but **cap 0.97** | 31–40 of 66 cells | +0.0300 |

At the deployed ceiling the complement is not submittable, so
`P(complete) = 0` **by construction** and OPEN collapses to a naked
adversely-selected directional bet. **No fill-probability assumption can
rescue it.** Stage 1's four stranded legs settled worthless 4 times out of 4,
against ~1.25 expected at their fill prices — directionally consistent,
though n=4 proves nothing on its own.

---

## 3. The recommendation (ONE change)

> **Make the complement an event-driven response to the first-leg fill —
> priced and submitted at the fill instant — and apply the pair ceiling to
> the achievable at-fill pair (0.97) rather than the snapshot pair (0.95).**

One behavioural change: *complete at the fill, at the price the fill makes
available.* It replaces a 33-second poll against an unreachable ceiling with
an immediate response at a reachable one.

**Both halves are jointly necessary, and this is why no single-parameter
change fixes Stage 1:**

- Event-driven alone, ceiling 0.96 → at the fill the pair is 0.97, still
  refused. Zero improvement.
- Ceiling 0.97 alone, 33s cadence → the first post-fill observation was
  already 1.0900 (1500z), 1.0400 (1530z), 0.9800 (1545z). Never qualifies.
  **Zero improvement.**

**A correction to my own earlier analysis.** I previously rejected
event-driven evaluation on the grounds that "no fee-safe instant existed",
citing minimum observed pairs of 0.98/1.04/1.04. That reasoning was wrong:
those observations are 33 seconds apart and **the fill instant itself was
never among them**. The arithmetic puts the pair at exactly 0.97 at the fill.
The conclusion "no 0.96 instant existed" stands; the inference "therefore
speed cannot help" does not.

**Ship the fail-safe in the same expression.** Refuse OPEN when
`1 − (d + spread)·t > pair_ceiling`. With the 0.97 ceiling and a one-tick
spread this is non-binding, but it is the interlock that prevents the engine
ever again opening a leg it cannot complete — which is precisely what cost
−$3.7194.

### Why this and not the alternatives

- **Two concurrent orders (C).** Directionally right — L5 runs ~110
  legs/window at 2-second spacing, which is only possible with many resting
  orders. But at 1–3 legs/window a single unmatched leg dominates, and
  resting both sides doubles the directions in which we can be adversely
  selected: −$0.3062 per stranded contract against +$0.03 per pair is ~10:1
  against. L5 survives that because of volume, and volume is not available at
  a $5 envelope. **Not the smallest change, and not safe at our size.**
- **Deeper resting (d→4).** Makes the at-fill pair 0.95, but the complement
  then rests 4 ticks behind and needs a 4-tick retrace, while deeper entry
  selects harder for large adverse moves. It trades a known problem for an
  unmeasured one.
- **Smaller legs.** Scales the pair term and the strand term together; the
  shortfall ratio is untouched.

### Evidence it improves expected net dollars, not completion rate

I am **not** claiming Stage 1 would have profited — that would require
counting a hypothetical maker fill, which this evidence standard forbids.
The case is:

1. **Parameter-space**: OPEN is positive-EV in 31–40 of 66 cells at a 0.97
   ceiling and in **0 of 66** today. The change moves the policy from
   provably-negative to conditionally-positive.
2. **Break-even completion rate**, from the scorer:
   `p_c* = F·a / ((1−pair) + F·a)`. At the mean Stage 1 fill price F≈0.31 and
   a 5–20% adverse haircut, break-even needs **35–67%** completion.
3. **That rate is source-confirmed achievable at this exact pair price**: L5
   matches **84.5%** of his volume at a median pair cost of **0.9684**, with
   **no incentives**, and the pair economics **persist out-of-sample**
   (0.9697, matched component growing). Stage 1 achieved **20%**.
4. **Per-dollar framing**: L5's return on deployed cash is **+5.69%** per
   window-cycle at that pair price. Ours was ≈−94%.

The claim is therefore: the change is necessary for positive expectancy and
is calibrated against a trader whose economics at the same price are
source-confirmed and out-of-sample stable. Sufficiency is what Stage 0
measures.

---

## 4. Next bounded validation step

**Stage 0 — shadow only, $0 at risk, no new service, no new branch.**

Already deployed: the book-evidence recorders now run on the shadow
controller's modelled fills and modelled inventory
(`L5_SHADOW_FILL_BOOK_EVIDENCE`, `L5_SHADOW_RESIDUAL_BOOK_SAMPLE`), on a
separate sampler instance, with `venue_event_t=None` so a modelled fill never
borrows our clock.

Run the unchanged baseline against **one** candidate arm — event-driven
complement at a 0.97 ceiling — chronologically, over **≥ 200 BTC15m windows**
(~6 days at 33.5 windows/day), scored queue-aware with **no touch counted as
a fill**, fees included, **confirmed rebates only (currently exactly
$0.00)**, and residuals priced at the venue's own resolution.

Report per arm: total P&L, **residual losses**, **max drawdown**, **turnover**
(legs/window), **opportunity cost** (justified refusals vs possible misses),
completion rate, adverse-fill fraction and $/unit, and queue-ahead at
placement with the unknown-queue fraction.

**Pre-registered promotion rule** — promote only if all four hold:

- (a) `P(complete | fill)` ≥ **60%** (the break-even at F≈0.31 with a 10%
  adverse haircut, chosen before the run);
- (b) net per window positive and above the baseline arm;
- (c) residual/matched ratio below **0.5** (baseline Stage 1: 2.15; L5: 0.18);
- (d) adverse-fill fraction not worse than baseline.

Any one failing → do not promote, and report which. **If (a) fails, the
correct action is to stop trading this policy rather than tune it**, and the
interlock in §3 makes that the default rather than a decision.

---

## 5. Authorization

**Yes, owner authorization is required before any live deployment** — the
recommendation changes completion timing and the pair ceiling, both trading
policy.

Nothing in this document is deployed to the money path. What *is* deployed is
shadow instrumentation. Specifically **not** done: no rearm, no capital
change, no risk-limit change, no new service, no new branch, and
`L5_TS_DEPTH_TICKS`, `L5_MAX_LIVE_ORDERS` and the 0.96 ceiling are all
untouched in production.

Stage 0 needs no further authorization. Promotion to a bounded live probe
needs explicit written authorization and should reuse Stage 1's exact
envelope — $5 cash, $5 exposure, 3-contract opening cap, maker-only, one
market, one live order, 8 windows, $5 daily and $20 cumulative halts — with
**no capital increase**.

The $480/day objective is not addressed here and should not be, until a
shadow arm demonstrates positive net expectancy. For scale context only: L5
clears that target at ~33.5 windows/day and ~110 legs/window, which is two
orders of magnitude more participation than our current envelope permits.
