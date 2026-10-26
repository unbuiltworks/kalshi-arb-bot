# Maker-only pair-cost gate (0.99) — economics, placement policy, GO/NO-GO

**2026-10-26.** No live gate changed. Nothing armed. No capital change. No new
service. The gate ships **disabled** behind `L5_MAKER_PAIR_GATE`, and the
deployed `MAKER_COMPLETE_CEILING = 0.96` and the taker gate are untouched.

---

## Verdict first

| question | answer |
|---|---|
| Is 0.96 the wrong ceiling for a maker? | **Yes.** It reserves margin for a taker fee a maker never pays. |
| Is a 0.99 maker pair net-positive? | **Yes**, with the rebate valued at **zero**. Break-even is **1.0000**. |
| Does raising it to 0.99 make money? | **No measurable difference.** On the holdout it is **exactly neutral**; on 5 untouched windows the band is the only available completion in **0–1 of 5**. |
| Should it be activated live? | **NO-GO.** Not because it is unsound — because it is **inert**, and the binding constraint is elsewhere. |
| What is the actual blocker? | **Completion rate.** 66.0% required, 62.5% observed. One strand costs 1.94 completed pairs. |

**Recommendation: promote the code disabled; do not spend a bounded live probe
on this gate.** The same probe spent on completion fill-rate would be worth
roughly an order of magnitude more.

---

## 1. The rebate — formula, eligibility, rounding, both legs

| | |
|---|---|
| **Formula** | `rebate = theta_maker * C * p * (1 - p)`, `theta_maker = 0.0125` |
| **Provenance** | `https://docs.polymarket.us/fees`. All **99 published rows** (what a 100-lot taker pays and a 100-lot maker *receives*, every price $0.01–$0.99) reproduce **exactly**, which pins the coefficient *and* the rounding (`tests/test_pmus_fees.py` against `analysis/pmus/pmus_published_fee_schedule.json`). |
| **Did the coefficient move?** | No. The 2026-09-17 republication moved only the taker coefficient (0.06 → 0.0695). |
| **Eligibility / tiers** | **Not tiered.** `TAKER_REBATE_TIERS` starts at $250,000 of prior-month taker notional and this account has never reached it, so the *taker* rebate is counted at zero. **No tier condition is published for the maker rebate.** |
| **Does a maker pay a fee?** | **No.** The published table's maker column is `maker_receives`. There is no maker charge. |
| **Both legs?** | **Yes, in principle** — both legs of a post-only pair are maker fills and both are schedule-eligible. **But see the rounding.** |
| **Credited, or merely expected?** | **Both exist, and they are different.** This account has had **$0.42 credited across 25 prints** (`src/l5_profitability.py:observed_rebate_rate`, rate 0.00749/filled $). |

### The rounding is what actually decides it

The venue credits **per print**, with **banker's rounding to the cent**. Our own
history records the floor biting: *"earlier cohorts (< 1.62 shares per print)
were credited 0.00."*

At the deployed **$0.80 opening notional / 2-contract leg cap**:

| own price | q at caps | raw rebate | **credited** |
|---|---|---|---|
| 0.10 | 2.00 | 0.0022 | **0.00** |
| 0.18 | 2.00 | 0.0037 | **0.00** |
| 0.25 | 2.00 | 0.0047 | **0.00** |
| 0.30 | 2.00 | 0.0056 | 0.01 |
| 0.38 | 2.00 | 0.0059 | 0.01 |
| 0.45 | 1.78 | 0.0050 | 0.01 |
| 0.50 | 1.60 | 0.0050 | **0.00** |
| 0.62 | 1.29 | 0.0038 | **0.00** |
| 0.75 | 1.07 | 0.0025 | **0.00** |
| 0.90 | 0.89 | 0.0010 | **0.00** |

**The credit is $0.00 at every price except a narrow p ∈ [0.30, 0.45] band.**
L5's own maker p10 (0.08) and p25 (0.18) are both in the dead zone.

**Therefore this gate is justified with the rebate valued at zero**, and
`CEILING` is set strictly below the **no-rebate** break-even. An uncredited
accrual is never margin.

> **Correction to my own earlier figure.** I previously reported a pure-maker
> break-even pair cost of **1.00620** and concluded 43.8% of L5's pairs fell in
> a profitable band above 0.96. That used an **unrounded** rebate our clip size
> does not earn. The correct no-rebate break-even is **1.0000**, and the
> correct band shares are below. The 1.00620 figure is withdrawn.

### Why L5 can do what we cannot

L5's account-level incentives are **$71,581.68** (MAKER_REBATE **$35,258.26**,
REWARD $28,797.69, TAKER_REBATE $7,525.73) against realized btc15m cashflow of
+$345,525.64. His pair tolerance is **partly rebate-subsidised at a size that
clears the cent floor**. Ours does not clear it. Copying his pair-cost
tolerance without his rebate is copying the cost and not the income.

---

## 2. Pair economics at the deployed caps

A maker pays no fee, so a completed maker pair is `q * (1 - pair)`:

| legs (up/dn) | q at caps | gross | credited rebate | **net** | net/contract |
|---|---|---|---|---|---|
| 0.50 / 0.49 | 1.60 | 0.0160 | 0.00 | **0.0160** | 0.0100 |
| 0.45 / 0.54 | 1.48 | 0.0148 | 0.00 | **0.0148** | 0.0100 |
| 0.38 / 0.61 | 1.31 | 0.0131 | 0.00 | **0.0131** | 0.0100 |
| 0.18 / 0.81 | 0.99 | 0.0099 | 0.00 | **0.0099** | 0.0100 |

**Break-even is exactly 1.0000 regardless of the price split.** A 0.99 pair
earns **1 cent per contract**; at our caps that is **$0.0090–$0.0160 per pair**.

### L5's matched-pair distribution against the corrected break-even

66,581 observations, `matched_pair_unit_cost` where `matched_qty > 0`
(mean 0.9927, median 0.9876, p25 0.9605, p75 1.0150):

| ceiling | share of L5 pairs admitted |
|---|---|
| **0.96** (deployed) | **24.6%** |
| 0.98 | 42.4% |
| **0.99** (proposed) | **52.5%** |
| 1.0000 (true break-even) | 62.8% |
| *above 1.0000* | *37.2% — net-negative for us* |

The band **(0.96, 0.99] is 28.0%** of everything L5 does. That is the headline
case for the change, and §4 is why it does not cash out.

---

## 3. The causal placement and cancellation policy

`src/l5_maker_placement_replay.py`. The policy sees **one book snapshot, its own
inventory, and the clock**. It never reads an L5 fill.

**Why that matters:** 83.4% of L5's classifiable fills are **maker** fills, so
his public print is the moment his order was *hit* — an unknown interval after
he placed it. Triggering our placement on his print would backdate our own
entry and model an order we could not have had resting.

**Placement price (own terms).** The book is quoted in UP terms.
- Buy UP as maker → rest a bid → own cost = `bid`
- Buy DOWN as maker → rest an offer on YES → own cost = `1 - ask`

Both are cheaper than crossing by the spread.

**Fill model.** A resting order fills only when someone crosses to it:
`own_taker_cost(side, bid, ask) <= resting_price`. `STRICT` requires it to come
strictly **through** our price — the conservative reading of queue priority,
since at exactly our price the size ahead of us may absorb the whole cross.
Queue depths in these books run 870–3,872 contracts and our position within a
level is `UNKNOWN`, so `STRICT` is the headline and `AT_TOUCH` is the optimistic
bound. **Neither is a venue-confirmed fill.**

**Cancellation.** Time cutoff first (600 s opening / 720 s completion), then
drift: if the book moves more than `REPRICE_DRIFT = 0.02` from our resting
price we cancel and repost. One outstanding order, always.

**The asymmetry that motivates the whole exercise.** A resting bid fills exactly
when the market falls to it. On an **opening** leg that is adverse by
construction — we buy as the price leaves us — which is why the measured
opening-leg edge is **−$0.0827/contract with 91.63% of fills adverse**. But the
*same* move is **favourable** for a **completion**: once long UP, a falling
market makes DOWN cheaper, and DOWN is what completes the pair. **Maker
completion is favourably selected by the mechanism that makes maker opening
adversely selected.**

### Patience is not optional

The first version of this policy completed at the **first** acceptable pair.
Raising the ceiling 0.96 → 0.99 then produced the **same five completions for
$0.04 less** — it rescued no strand and merely let a completion that still had
runway settle for less. That is **dilution**, and it is a defect in an eager
rule, not an argument about the ceiling.

So the loose ceiling is a **last resort**: the tight 0.96 bound holds while the
window has runway, and only after `LOOSE_AFTER_S = 600 s` may a completion pay
up to the maker ceiling. This makes the change **weakly positive by
construction** — it can only act where the tight bound was going to strand the
leg anyway.

---

## 4. Chronological out-of-sample replay

One shared cash/inventory ledger, one outstanding order, actual settlement
outcomes, no lookahead. Holdout corpus `l5_sequential_oos_corpus_v1.json`
(314 rows, 5 windows, deployment `47920949`, 13:31:29Z–14:42:46Z), which is
disjoint from the three derivation windows **by name** at the extraction
boundary.

| policy | fill | completed | stranded | net | rebate | net+rebate | drawdown |
|---|---|---|---|---|---|---|---|
| tight 0.96 | STRICT | 5 | 3 | **−0.1612** | 0.04 | −0.1212 | −0.7992 |
| **patient → 0.99** | STRICT | 5 | 3 | **−0.1612** | 0.04 | −0.1212 | −0.7992 |
| patient → 0.98 | STRICT | 5 | 3 | −0.1612 | 0.04 | −0.1212 | −0.7992 |
| *eager 0.99* | STRICT | 5 | 3 | *−0.2012* | 0.04 | −0.1612 | −0.7992 |
| tight 0.96 | AT_TOUCH | 6 | 3 | −0.4864 | 0.04 | −0.4464 | −0.9984 |
| **patient → 0.99** | AT_TOUCH | 6 | 3 | **−0.4864** | 0.04 | −0.4464 | −0.9984 |
| taker-open 0.96 | STRICT | 8 | 4 | −1.0412 | 0.06 | −0.9812 | −1.6573 |

**The patient 0.99 gate is exactly neutral — to the cent — on the holdout.** It
never fires. The eager version is strictly worse.

Maker-open beats taker-open by **+$0.88** on the same opportunities, which is
the one clearly-signed result here.

### Rescue frequency on genuinely untouched data

`data/l5_strand_replay/residual_books_20261025.jsonl` — **290 full-ladder
snapshots taken while a residual was actually held**, 5 windows, 2026-10-25,
a different date from the holdout. The first-leg cost is not in that file, so it
is **swept** rather than assumed:

| assumed first leg | pair ≤ 0.96 | (0.96, 0.99] | > 0.99 | **windows where the band is the only completion** |
|---|---|---|---|---|
| 0.30 | 188 | 18 | 84 | **0 of 5** |
| 0.40 | 149 | 5 | 136 | **0 of 5** |
| 0.45 | 120 | 18 | 152 | **1 of 5** |
| 0.50 | 85 | 13 | 192 | **0 of 4** |
| 0.60 | 43 | 16 | 231 | **0 of 4** |
| 0.65 | 18 | 17 | 255 | **1 of 4** |
| 0.75 | 4 | 3 | 283 | 2 of 3 |

At plausible first-leg costs the band is the **only** available completion in
**0–1 of 5 windows**. A ≤0.96 completion is available in the large majority of
snapshots, so **the tight ceiling is rarely the binding constraint.**

---

## 5. First-leg-only losses and adverse selection

Maker-open, patient 0.99, STRICT, holdout:

| | |
|---|---|
| 5 completed pairs | **+$0.9670** (mean +$0.1934) |
| 3 stranded legs | **−$1.1282** (mean **−$0.3761**) |
| net | **−$0.1612** |
| one strand costs | **1.94 completed pairs** |
| **required completion rate** | **66.0%** |
| **observed completion rate** | **62.5%** (5 of 8) |

Stranded legs, maker-open: `1330z UP @0.71 → −$0.7952`, `1400z UP @0.63 →
+$0.4662`, `1415z UP @0.54 → −$0.7992`. All three stranded **mid-priced**, which
is why each risks close to full notional.

**With rebates:** +$0.04 credited across the whole run, which does not change
any sign. **Without rebates:** identical to the headline, because the headline
already excludes them.

**This is the finding that decides the recommendation.** The ceiling moves the
pair term by at most $0.03/contract. The strand term is **−$0.3761 per event**.
**The ceiling cannot reach it.** Independently, the deployed shadow controller
measured the same thing at scale: pair economics **+$29.94**, residual
settlement **−$85.23**, net **−$55.29** over 475 windows, with **WAIT dominating
by +$52.25**.

---

## 6. Protections preserved

Nothing in the money path changed. Verified unchanged: `MAKER_COMPLETE_CEILING
= 0.96`; the taker gate; `MAX_CASH_DEPLOYED_USD = 4.64` and the $5 fee-inclusive
ceiling; `$0.90` worst-case deficit; $5 daily / $20 cumulative loss; one
outstanding order; maker-only execution mode; exact-cancellation verification;
the independent halt watchdog; durable reconciliation; `L5_DEFENSIVE_COMPLETION
= 0`; `L5_TS_COMPLETION_CROSS` disabled; campaign nonce, expiry and window
count. The new gate is **additive and refuse-only**, defaults to the taker-era
ceiling, and **refuses to return the maker ceiling for anything that could
cross** — a taker pair at 0.99 is net-negative and the gate will not reach it.

---

## 7. Narrow release and bounded live validation

**Release contents** — two new modules and two test files, no money-path edit:
`src/l5_maker_pair_gate.py`, `src/l5_maker_placement_replay.py`,
`tests/test_l5_maker_pair_gate.py`, `tests/test_l5_maker_placement_replay.py`,
this document. Ships **disabled**; `L5_MAKER_PAIR_GATE` is not set on any
service. Rollback is deleting the env var, because the default path is the
current one.

**Bounded live validation — NOT REQUESTED.** On the evidence above the gate
cannot produce a measurable result at the authorized size: it fires in 0–1 of 5
windows, and when it fires it earns 1 cent per contract, which is below the
noise of a single strand. Asking for a probe would spend an authorization on an
inert change.

**What a probe should measure instead** (for separate authorization, when
wanted): the **completion fill rate**. The gap is 3.5 points of completion rate
(66.0% required vs 62.5% observed), and the levers are the completion order's
resting price and reprice cadence — not the ceiling. That is also where L5's
observed behaviour differs most from ours: he reprices within seconds.

---

## 8. Corrections to my own earlier claims

| claim | status |
|---|---|
| "Pure-maker break-even is 1.00620; 43.8% of L5's pairs sit in a profitable band above 0.96." | **Withdrawn.** Assumed an unrounded rebate our size does not earn. Break-even is **1.0000**; the band (0.96, 0.99] is **28.0%**. |
| "No maker rebate has ever been credited to this account." | **Wrong.** **$0.42 across 25 prints** has been credited. |
| "Raising the ceiling raises the required completion rate, so it is a NO-GO on arithmetic." | **Withdrawn.** That assumed the set of completions was fixed. The ceiling's actual effect is to rescue legs that would otherwise strand; the reason it is still a NO-GO is **measured inertness**, not arithmetic. |

## Evidence grades

Venue-confirmed: the fee/rebate schedule (99 rows), the $0.42 credited rebate,
L5's on-chain maker classification, L5's 66,581 matched pairs. **Modelled, not
venue-confirmed:** every fill in §4 and §5, the −$0.0827 adverse-selection
figure (6 of 956 fills were venue-confirmed), and all settlement in §4
(`TERMINAL_BOOK_INFERRED`). **Nothing here is realized profit.**

**Sample caveat, stated plainly:** §4 rests on 5 windows and 8 sequences, and
three stranded legs decide its sign. It is sufficient to show the gate is
**inert** and that maker-open beats taker-open; it is **not** sufficient to
establish an edge in either direction.
