# Gemini BTC5m — L5 SETTINGS / ASSUMPTIONS AUDIT

**Baseline:** `2a7ba86bc68b60ec61fbb3b7e9cca338bd00c97c`
**The frozen conclusion is NOT reopened or modified:**
*GEMINI BTC5M PASSIVE BACK-OF-QUEUE L5-STYLE MAKER HABITAT: REJECTED ON
CURRENT EVIDENCE* — it stands for the execution policy it tested.

**The question here:** were we failing because our Gemini execution model did
not represent L5's behavioral mechanics?

## HEADLINE: PARTLY YES — AND THE CORRECTION DOES NOT SAVE THE LANE

**We did test our own simplified implementation and call it L5.** Three
concrete mis-specifications are confirmed below, one of which invalidates a
*supporting plank* of the frozen verdict. But after correcting all of them,
the rejection survives — and now rests on arithmetic that is stronger and
better-sourced than what the frozen gate offered.

No live orders. No parameter fitting. No profitability threshold search. No
PMUS or SOL changes.

---

## THE GOVERNING CONSTRAINT: L5's PLACEMENT IS UNOBSERVABLE

`L5_OPERATING_MODEL.md` §2, verbatim:

> **Unobservable and never imputed:** the historical book at any instant;
> queue position; L5's unfilled, resting or cancelled orders — so *"L5
> waited"* and *"L5 quoted and was not hit"* are the same observation in this
> data; maker vs taker per fill; per-market incentive credits.

**This settles dimension 1 before any measurement.** L5's BBO-joining,
inside-spread, away-from-touch and multi-level behaviour **cannot be
established from L5 evidence at all** — only his *fills* are visible, never
his orders. So our BBO-join assumption was never *contradicted* by L5
evidence; it was **unconstrained** by it. Any claim that "L5 rests away from
the touch" is unfalsifiable on this data and must not be asserted.

What *is* documented is one readable maker fill of our own spring probe, and
it is the most important sentence in this audit:

> "the YES bid held 0.31 while the ask walked 0.58 → 0.43 → 0.38 → 0.35 →
> 0.33 down to meet it, and the ladder at our own price read `0.01` — our
> order, alone on the bid, **after the 483.0 that had been there left**."

Three mechanics, none of which the frozen model possessed.

---

## CONFIRMED MIS-SPECIFICATION #1 — WE DESTROYED THE PRIORITY WE WERE MEASURING

The frozen `p3_hifi_capture.py` repriced on every touch move, each reprice a
cancel+new forfeiting queue position. Measured on the cohort:

| policy | placements | reprices | order lifetime p50 | queue discarded | fills | capture |
|---|---|---|---|---|---|---|
| **A_REPRICE** (frozen) | 7,970 | 1,859 | **1.0 s** | **2,148,068 contracts** | 32 | 0.06% |
| **B_STICKY** | 102 | **0** | **296.7 s** | **0.0** | 26 | 0.05% |
| C_INVALIDATED (structural) | 4,947 | 0 | 2.0 s | 1,607,146 | 15 | 0.03% |

On the legacy cohort the frozen policy ran **102 reprices per fill**. It was
cancelling its own queue position roughly a hundred times for every fill it
claimed to measure.

## CONFIRMED MIS-SPECIFICATION #2 — THE TOXICITY PLANK WAS A POLICY ARTIFACT

The frozen verdict's decisive new finding was that defensible fills are
systematically adverse. **That result is policy-dependent, and the frozen
policy was the adverse one.** Same frozen 5/15/30 s horizons, nothing fitted:

| policy | 5 s | 15 s | 30 s |
|---|---|---|---|
| **A_REPRICE** (frozen) | **+0.0300** | **+0.0475** | **+0.1000** |
| **B_STICKY** | **+0.0050** | **−0.0050** | **+0.0450** |
| C_INVALIDATED | +0.0375 | +0.0500 | +0.0550 |

A sticky order's fills are **roughly benign** — 15 s markout is *favourable*
— against ~3 cents adverse at 5 s and 10 cents at 30 s for chasing the touch.

**I withdraw the claim that "the only capturable flow is the toxic flow."**
It was true of the policy tested, not of Gemini BTC5m. The frozen document's
§5 reasoning — that through-price dominance *necessarily* implies adverse
selection — overstated a policy artifact as a structural law.

## CONFIRMED MIS-SPECIFICATION #3 — THE WALL STORY WAS WRONG

The frozen gate said: *"One participant rests ~74,000 contracts at a single
price while takers nibble 2.4 contracts at a time. Joining that queue is not
a strategy."* That instance was real but **not representative.**

Wall forensics over 17,429 in-window levels:

| size ≥ | walls | size at first sight p50 | **atomic** | **lifetime p50** | cancelled | executed |
|---|---|---|---|---|---|---|
| 100 | 6,944 | 282.0 | **93.9%** | **1.9 s** | 100.1% | **0.8%** |
| 1,000 | 761 | 1,828.0 | **94.6%** | **2.0 s** | 100.0% | **1.2%** |
| 10,000 | 29 | 15,000.0 | 89.7% | 7.1 s | 99.3% | 3.4% |

For walls ≥1,000 contracts: **99.1% were NEVER executed against at all**, and
99.1% were cancelled with zero execution. Prices cluster in the tails
(p10 0.02, **p50 0.09**, p90 0.97), not mid-book. 20.1% have an opposite-side
wall within 30 s.

### WHAT IS THE LARGE MAKER DOING THAT WE ARE NOT?

**Posting large size atomically at tail prices for ~2 seconds and cancelling
it, almost never trading.** That is not liquidity provision competing with us
for fills — 99% of it never fills. It is consistent with display-based
incentive farming or quote flickering.

**So the blocking queue is largely ephemeral noise at prices that never
trade.** The binding constraint is *not* that a wall sits ahead of us. It is
that there are only **~4 trades per window (p50)** and we can occupy one
price.

---

## DIMENSIONS 3–6, 9 — MEASURED, NONE RESCUES CAPTURE

**3. Multi-level / away-from-touch** (fixed structural grid, no spacing
optimized). Capture falls monotonically with distance from the touch:

| ticks away | 0 | 1 | 2 | 3 | 5 |
|---|---|---|---|---|---|
| B_STICKY capture | 0.05% | 0.05% | 0.05% | 0.04% | 0.04% |
| `price_came_to_us` contracts | 0.00 | 2.00 | 3.00 | 1.00 | 2.00 |

The "price walked to our resting order" mechanic **does occur and is now
measured for the first time** — and it is **1–5 contracts** across 40
windows. Real, and immaterial.

**4. Sequential inventory** (two-sided sticky, inventory carried, nothing
forced flat):

| clip | windows with fills | both sides filled | matched | \|residual\| | pair cost p50 | matched P&L | residual P&L (proxy) |
|---|---|---|---|---|---|---|---|
| q1 | 11/40 | **2/40** | 2.0 | 13.0 | **0.8750** | **+$0.25** | −$1.36 |
| q5 | 11/40 | 2/40 | 10.0 | 65.0 | 0.8750 | +$1.25 | −$6.80 |
| q10 | 11/40 | 2/40 | 17.0 | 118.0 | 0.8750 | +$2.23 | −$10.03 |
| q25 | 11/40 | 2/40 | 32.0 | 223.2 | 0.8750 | +$4.63 | −$9.92 |

**Matched pairs earn; residual loses** — reproducing L5's own result (August
BTC15m matched +$104,065 vs residual −$102,687 "almost exactly cancelling";
carried residual −$12,971 OOS). Pair cost p50 **0.8750**, well inside the
≤0.96 spring ceiling. **But both sides fill in only 2 of 40 windows**, so
there is no inventory cycle to harvest — the same conclusion
`FIRST_DATASET.md` reached by a different route.

*Residual P&L uses the last observable mid as a settlement PROXY. True
settlement is 0 or 1 and is not in this tape; L5 evidence says true
settlement makes residual worse, not better.*

**5. Complement behaviour** (second leg given its own defensible treatment,
no touch=fill): of the defensible first fills, only **6** had any later
opposite-side print. Time-to-complement p50 **20.3 s**. Best gross pair cost
p50 **1.0350**; **2 of 6 (33.3%)** below 1.00 — and the same 2 below 0.96.
L5's own frozen finding is that attainable pair sits at **0.9900 in every
|Δp| bin**, i.e. more opportunity at an unchanged price. Consistent, and not
enough.

**6. Size.** L5 shows repeated small additions, median size ~10,
P(EXTEND)=0.66, frequency-before-size, and concurrency up to ~8 markets
(mature only; spring concurrency was 1). Our q1/q5/q10/q25 single clips
**do** differ representationally. But capture is **clip-capped, not
flow-capped** — every fill is limited by our clip, never by available flow —
so splitting the same clip into repeated smaller additions accesses the same
flow in smaller pieces. **Representational mismatch: yes. Material to
accessibility: no.**

**9. Time-in-window** (structural phases, no fitting), fills by phase:

| policy | 0–60 s | 60–120 s | 120–180 s | 180–240 s | 240–300 s |
|---|---|---|---|---|---|
| A_REPRICE | 11.0 | 12.0 | 4.0 | 3.0 | 2.0 |
| B_STICKY | 6.0 | 13.0 | 3.0 | 3.0 | 2.0 |

**Acquisition concentrates in the first 120 s** (19–23 of 27–32 fills); the
last 60 s is nearly dead. This corroborates L5's own
`NO_NEW_OPEN_EXTEND_TTE_S = 60`. **No hidden phase of accessible L5-like
inventory acquisition exists** — the aggregate was not masking one.

---

## 7. REBATE ECONOMICS — THE CALCULATION NOW SUPPORTS THE REJECTION

Authoritative, from account records in `GEMINI_ECON_VIABILITY_AUDIT_1.md`:

> The published programme is **`0.30 × 0.07 × C × P × (1−P)`**, capped at 5%
> of notional, eligible band **$0.20–0.80**, **rounded DOWN**, paid daily at
> 17:00 ET. Seven confirmed `AdminCredit` rows, purpose "Predictions maker
> rebate", total **$0.51**; maker notional $52.2573; effective **≈97.6 bp**.

Maximum possible compensation per contract is at P=0.50:
`0.30 × 0.07 × 0.25 = $0.00525` = **0.525 cents/contract**.

Break-even compensation required by this cohort (matched + residual-proxy):

| clip | filled contracts | matched + residual (proxy) | **break-even compensation** |
|---|---|---|---|
| q1 | 17.0 | −$1.1100 | **+6.53 ¢/contract** |
| q5 | 85.0 | −$5.5500 | **+6.53 ¢/contract** |
| q10 | 152.0 | −$7.8000 | **+5.13 ¢/contract** |
| q25 | 287.2 | −$5.2914 | **+1.84 ¢/contract** |

**The break-even requirement is 3.5× to 12.4× the maximum the programme can
pay**, before the cent-floor and the $0.20–0.80 band reduce it further — and
the measured effective rate (97.6 bp of maker notional ≈ 0.49 ¢ at P=0.50)
matches the theoretical maximum, so there is no hidden headroom.

**This is the calculation the frozen gate declined to do, and it supports the
conclusion the frozen gate asserted without it.** Streams kept separate:
trading P&L, maker rebates, fees, matched-pair P&L and directional residual
P&L are never summed into one figure anywhere above.

## 8. UP/DOWN SEMANTICS — PROVEN CONSISTENT, PLUS ONE REAL HAZARD

`a3_semantics_test.py`: **11 identity tests and 5 mutation tests all pass.**
A BTC05M window lists exactly one instrument (`…-UP`); the economic
complement of +1 UP is −1 UP, not a separate DOWN instrument, so **the
historical NO-side bug class cannot arise here in the form it did on a
two-instrument venue.** Verified: `pair_cost = p_buy + (1 − p_sell)`,
`matched P&L = 1 − pair_cost`, matched settlement path-independence, residual
settlement exposure, and the `buyer_is_maker` → passive-side mapping.
Mutations caught: wrong pair-cost formula, sign-flipped matched P&L, inverted
passive-side mapping, round-to-nearest rebate, settlement ignoring the short
leg.

**Hazard found and confirmed, not hypothetical:** a float implementation of
the rebate formula with floor-to-cent **breaks the formula's P ↔ 1−P
symmetry** — `C=1000` yields $4.40 at P=0.30 but $4.41 at P=0.70, which are
the same quantity. **Any production rebate accrual must use Decimal.** Both
implementations are kept in the test file so the hazard stays demonstrated.

---

## 11. L5 BEHAVIORAL GAP TABLE

| L5 behavior / evidence | Current Gemini assumption | Tested? | Evidence | Material mismatch? | Next falsification test |
|---|---|---|---|---|---|
| Price placement is **UNOBSERVABLE** for L5 | BBO join | n/a | `L5_OPERATING_MODEL` §2 | **Unfalsifiable from L5 side** | none possible — do not assert |
| Inside-spread placement | not modelled | **YES** (33.1% of flow prints inside spread) | `p2_reconstruct` | No — that is competitors quoting tighter; joining forfeits the spread | none |
| Away-from-touch resting | touch only | **YES** (grid 0/1/2/3/5 ticks) | A1 | No — capture falls monotonically | none |
| Multiple simultaneous levels | one price | **YES** (fixed grid) | A1 | No — same flow, smaller pieces | none |
| "Price comes to us" (ask walks down) | not modelled | **YES, first time** | A1 `price_came_to_us` | Real but **1–5 contracts/40 windows** | none |
| Orders resting before price arrives | reprice-chases | **YES** (B_STICKY) | A1 | **YES — fixed; markout benign** | applied, not pending |
| Queue gained by **cancellation ahead of us** | explicitly excluded | **YES** (FIFO_ATTRITION) | A1 | **YES — fixed; fills 26→27** | applied, not pending |
| Same-side EXTEND first-class, P=0.66 | single clip, refill after full fill | **YES** | A1/A2 | Representational only — clip-capped not flow-capped | none |
| Inventory oscillation, no flat completion | independent fills | **YES** (A2 sequential) | A2 | **YES — fixed; 2/40 windows two-sided** | applied, not pending |
| Matched earns, residual loses | not separated | **YES** | A2 | **YES — fixed; matched +, residual −** | applied, not pending |
| Complement at ≤0.96 / ~0.99 attainable | no second leg | **YES** (defensible 2nd leg) | A2 | **YES — fixed; 6 opportunities, p50 1.035** | applied, not pending |
| Markout ≠ realized economics | markout = toxicity verdict | **YES** | A1/A2 | **YES — the frozen toxicity plank is withdrawn** | applied, not pending |
| Rebates additive, never rescuing | asserted insufficient | **YES, now calculated** | §7 | No — 3.5–12.4× short by arithmetic | none |
| `NO_NEW_OPEN_EXTEND_TTE_S=60` | no phase model | **YES** | A1 phases | No — corroborated; last 60 s dead | none |
| Concurrency ~3.1, max 8 (mature) | one market | **No** | `L5_OPERATING_MODEL` §epochs | **No** — multiplies capacity, not per-window capture; spring concurrency was 1 | none warranted |
| Giant wall = durable competing queue | assumed durable | **YES** | A4 | **YES — walls live 2.0 s, 99.1% never fill** | applied, not pending |

**Every mis-specification in this table has been corrected and re-measured
inside this audit. None remains pending.**

---

## 12. DECISION

# A. CLOSE GEMINI BTC5M L5 REPLICATION

**No material untested L5 mechanic remains with plausible execution
relevance.**

Choosing A requires that the corrections *were made and judged*, not merely
identified — and they were. Every mechanic listed in dimensions 1–10 is now
measured on the event-level cohort. After correcting all three confirmed
mis-specifications:

1. **Capture is invariant at 0.03%–0.06% of passive-side flow** across every
   policy (reprice / sticky / structurally-invalidated), every placement
   (0–5 ticks from touch), every clip (q1–q25) and every queue tier (strict /
   attrition). It did not move for any L5 mechanic we restored.
2. **Capacity, not toxicity, is binding.** p50 **4 trades per window**. At
   0.05% of 1,251 passive-side contracts/window that is ~0.6 contracts per
   window; at the venue's maximum 0.525 ¢/contract the rebate line is
   ~$0.003/window.
3. **The economics gap is arithmetic, not estimated:** break-even needs
   1.84–6.53 ¢/contract against a programme maximum of 0.525 ¢.
4. **The one genuinely favourable finding — sticky fills are benign rather
   than toxic — does not help**, because benign fills at 0.05% capture and
   ~0.5 ¢ compensation are still negligible. It corrects *why* we reject, not
   *whether*.

**B is rejected** because no candidate mechanic has evidence of a concrete
execution opportunity our (now-corrected) model structurally excludes.
Multi-market concurrency is the only untested item, and it scales *capacity*
by replicating a per-window result that is ~0.6 contracts — it cannot change
a per-window capture fraction. "Could theoretically work" is explicitly not
sufficient.

**C is rejected** because the mis-specifications, though real and serious,
have already been corrected and the corrected replay has been run. C would be
correct only if a correction were still *pending before Gemini could be
judged*. It is not.

### SO: DID WE TEST L5, OR OUR OWN SIMPLIFIED IMPLEMENTATION?

**Both, in sequence, and the distinction matters.**

The frozen decision gate tested **our own simplified implementation** —
reprice-chasing, single-level, independent fills judged by instantaneous
markout — and its *supporting narrative* (toxicity is structural; durable
walls block us) was substantially wrong on both counts. That is a real
failure of mine and it is corrected above rather than defended.

This audit tested **the L5 mechanics the evidence actually documents**, and
Gemini BTC5m still rejects — on trade-count capacity and on rebate
arithmetic, which are properties of the venue, not of our model.

**The frozen conclusion stands. Its reasoning is amended, and the amendments
are preserved above rather than folded away.**

---

## PRESERVED NEGATIVE AND UNRESOLVED RESULTS

- The frozen `DECISION_GATE.md` is **unmodified**; its §5 toxicity reasoning
  is amended **here**, not edited there.
- **300 / 1,390** remains **UNRESOLVED AND UNREPRODUCED** — not deleted, not
  averaged in, not disproven. Its data remains on the Railway volume
  `/state/gemini-5m`, which I did not prove is gone.
- **Displayed queue ahead ≠ proven queue position** throughout. Every
  `q_ahead` here is displayed size, a proxy and an upper bound on the visible
  queue only.
- Residual P&L uses a **settlement proxy**; true 0/1 settlement is not in
  this tape.
- Scope unchanged and narrow: **BTC05M only**. Not ETH5m (collected, not
  analysed), not other Gemini series, not other Gemini strategies, not the
  venue.
