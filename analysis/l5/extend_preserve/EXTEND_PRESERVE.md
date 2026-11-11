# L5_EXTEND_PRESERVE vs CONTROL — VERDICT: **KILL THE HYPOTHESIS**

**Scope.** Shadow only. Nothing was submitted, priced, repriced or cancelled.
Production is untouched and still serving `75edc4bd`; `392b5c0e` stays
deferred. No cap was raised, no band was widened, no taker path exists in any
arm. Residual cap **6**, per-market cap **$30.00**, maker-only, band
unchanged — reported at their production values in every arm and varied in
none.

---

## 0. The verdict in one paragraph

Preserving a model-selected `EXTEND` instead of converting it to `COMPLETE`
**does not recover suppressed L5 trajectories.** It buys more of the side we
already hold, that inventory never becomes matched, and it settles as
directional residual. Two independent studies, on two fill rules each, all
agree: **extra matched contracts = 0.00, matched-pair P&L delta = $0.0000.**
The `+$1.5994` the arm shows is one market's held side winning a coin flip;
the other settled market is `−$0.3944` and the third has not settled. The
hypothesis is killed on the evidence it was supposed to produce, not on
priors.

---

## 1. The arm already existed — twice — and that is the first finding

The requested change ("change exactly one thing: preserve the sampled
`EXTEND`") is already implemented and already run:

| name | where | status |
|---|---|---|
| `SHADOW_1_ENTRY_ONLY` | `src/l5_band_semantics.py` | committed, tested, re-run here |
| `band=entry_only \| cap=6` | `analysis/l5/residual_cap/` (9d6feb46) | committed 2026-09-13 22:03Z |
| `L5_EXTEND_PRESERVE` | this request | **the same gate, a third name** |

`src/l5_band_semantics.py` says it outright:

> `SHADOW_1  ENTRY-ONLY: the band gates OPEN/new inventory; a held-inventory
> decision follows its sampled action, subject to every OTHER gate unchanged
> (they are re-checked, not waived).`

and

> `Only EXTEND and OPEN are ever refused by this gate; a sampled COMPLETE was
> never blocked by it.`

Both implementations are **sequential and stateful** in the sense the request
requires: each arm carries its own `up`/`dn`/cost forward from the first
divergence, and every later gate is adjudicated against *that arm's* position,
not production's. In `analysis/l5/band_semantics/run.py` the shadow residual is
`resid + sign*extra_q + sign*q` and is re-tested against
`residual_cap_allows` and `spend_cap_allows` on every subsequent decision. An
arm that spends headroom early therefore **loses** fills production actually
took — and one does (see §4).

Building a fourth copy would have produced a third name for one experiment,
not a second piece of evidence. So this study **joins the existing arms to the
venue tape** instead, and measures the step both prior studies explicitly
declared out of scope.

---

## 2. CONTROL vs L5_EXTEND_PRESERVE — never summed across fill rules

`FILL_SUPPORTED` = `TAPE_SUPPORTED`: a counterfactual `EXTEND` counts as
filled **only** where a real `EXTEND` order filled in the same market inside
600 s. `FILL_UNKNOWN` = `FILL_ASSUMED`: every admitted `EXTEND` fills — an
**upper bound**, never a forecast. They are two readings of one arm, not two
results to add.

| arm | fill rule | opps | fills | extra capital | settlement Δ | matched Δ |
|---|---|---:|---:|---:|---:|---:|
| CONTROL | FILL_SUPPORTED | 74 | 0 | $0.0000 | $0.0000 | 0.00 |
| CONTROL | FILL_UNKNOWN | 74 | 0 | $0.0000 | $0.0000 | 0.00 |
| **L5_EXTEND_PRESERVE** | FILL_SUPPORTED | 74 | **6** | $2.2106 | **+$1.5994** | **0.00** |
| **L5_EXTEND_PRESERVE** | FILL_UNKNOWN | 74 | **10** | $6.2090 | **+$1.5994** | **0.00** |

CONTROL reproduces production exactly (0 fills, 0 refusals, $0.0000) — the arm
harness is validated before anything is read off it.

**The settlement Δ is one market.** Decomposed:

| market | fills (SUP/UNK) | extra contracts | settlement Δ | winner |
|---|---:|---:|---:|---|
| `aec-mlb-lad-mia-2026-09-13` | 3 / 3 | 3.81 | **+$1.9938** | dn (held side won) |
| `aec-setkameua-sapole-salvas-2026-09-13` | 3 / 3 | 3.38 | **−$0.3944** | up (held side lost) |
| `atc-epl-cov-bha-2026-09-13-bha` | 0 / 4 | 4.08 | **UNSETTLED** | — |

One winner, one loser, one unresolved, on n=2 settled markets. That is
variance, not expectancy, and the sign of the total is decided by a single
baseball game.

---

## 3. The cap — not the EXTEND-to-COMPLETE conversion — is what binds

Per-market blocked breakdown for L5_EXTEND_PRESERVE (FILL_UNKNOWN):

| market | opps | blocked by band | blocked by **residual cap** | blocked by spend | fills |
|---|---:|---:|---:|---:|---:|
| `aec-mlb-lad-mia` | 67 | 0 | **64** | 0 | 3 |
| `aec-setkameua-sapole-salvas` | 3 | 0 | 0 | 0 | 3 |
| `atc-epl-cov-bha` | 4 | 0 | 0 | 0 | 4 |

In the market that supplies **67 of 74** opportunities, the unchanged residual
cap of 6 refuses **64 of 67**. Removing the band's hold on held inventory
moves the refusal one gate downstream; it does not create 67 extra EXTENDs, it
creates 3. The conversion the hypothesis blames is not the binding constraint,
and the gate that *is* binding is a financial safety gate that stays where it
is.

---

## 4. Reconciliation with 9d6feb46 — the required test

9d6feb46 ran the identical gate at the identical cap under the name
`band=entry_only|cap=6`, over the same live-session corpus:

| cell | rule | extra fills | **matched Δ** | matched-pair P&L Δ | directional Δ | capital Δ | production extends the arm **refuses** |
|---|---|---:|---:|---:|---:|---:|---:|
| `band=current \| cap=6` (CONTROL) | TAPE_SUPPORTED | 0 | 0.00 | $0.0000 | $0.0000 | $0.0000 | 0 |
| `band=current \| cap=6` (CONTROL) | FILL_ASSUMED | 0 | 0.00 | $0.0000 | $0.0000 | $0.0000 | 0 |
| `band=entry_only \| cap=6` | TAPE_SUPPORTED | 7 | **0.00** | $0.1307 | +$1.4488 | $2.0305 | **1** |
| `band=entry_only \| cap=6` | FILL_ASSUMED | 11 | **0.00** | $0.1307 | +$1.4488 | $6.0289 | **1** |

**It reconciles exactly.** Same gate, same cap, same corpus, same conclusion —
7 supported fills / $2.03 then against 6 / $2.2106 now, +$1.5795 then against
+$1.5994 now, the small differences being decisions retained since 22:03Z and
one market (`cov-bha`) that had not yet appeared. Nothing in this cycle
contradicts the prior study; it re-derives it from a second direction.

And 9d6feb46 already recorded the mechanism, per market. Matched inventory is
**identical** in every market whether the arm runs or not, while terminal
residual grows in three of four:

| market | production matched → arm matched | production terminal residual → arm terminal residual |
|---|---|---|
| `aec-cs2-mzp-ezt` | 3.44 → **3.44** | 7.88 → 7.88 |
| `aec-mlb-lad-mia` | 6.08 → **6.08** | 4.05 → **7.66** |
| `aec-setkameua-sapole-salvas` | 2.79 → **2.79** | 3.49 → **6.87** |
| `atc-epl-cov-bha` | 4.67 → **4.67** | 1.00 → **5.08** |

The arm converts capital into terminal directional residual, contract for
contract, and produces zero matched inventory. Its P3 outcome classes at this
cap are **7 of 7 class B** — *"opposite opportunity appeared, no supported
fill, then C (settled directional)"*. **Zero class A.**

It is arithmetic, and 9d6feb46 stated why: `matched = min(up, dn)`, so
inventory added to the side already held cannot become matched unless the
opposite side later exceeds what the held side had at the time. It never did.

The arm also **refuses one EXTEND production actually took** (lad-mia), which
is the sequential state doing its job: headroom spent early is headroom not
available later.

---

## 5. The step both prior studies declared out of scope — now measured

Both studies say, correctly, that no `COMPLETE` may be imputed on top of a
counterfactual `EXTEND`:

> `"matched_pair_pnl_delta": "0.0000 (by construction: no completion is imputed on top of a counterfactual fill)"`
> `"eventual_complete_opportunities": "UNCHANGED -- the arms alter only whether an EXTEND is attempted"`

That is the honest modelling choice, and it is also where the hypothesis lives:
*if* the extra residual were completed at the observed rate, would it become
matched? So the rate is measured from **orders that were actually submitted**,
on the venue tape — no imputation, just what happened.

Global maker conversion across 132 live sessions:

| relation | orders | filled | rate |
|---|---:|---:|---:|
| open | 191 | 9 | 4.71% |
| extend | 138 | 20 | **14.49%** |
| **complete** | **260** | **16** | **6.15%** |

And in exactly the three markets the arm adds residual to:

| market | EXTEND | **COMPLETE** |
|---|---:|---:|
| `aec-mlb-lad-mia` | 6/16 = 37.5% | **4/125 = 3.20%** |
| `aec-setkameua-sapole-salvas` | 3/9 = 33.3% | **2/9 = 22.2%** |
| `atc-epl-cov-bha` | 1/13 = 7.7% | **4/32 = 12.5%** |

This is the finding that closes the case. The arm adds **11.27 extra residual
contracts** (FILL_UNKNOWN). Each needs a `COMPLETE` fill to become matched. In
the market supplying 90% of the opportunities, `COMPLETE` fills at **3.2%** —
125 attempts, 4 fills. The expected matched contracts from the arm's extra
residual is well under one, and the measured value in both studies, on both
fill rules, is **exactly zero**.

`EXTEND` fills at 2.4× the rate of `COMPLETE` globally (14.49% vs 6.15%). An
arm that produces more EXTENDs is pushing on the easy side of an asymmetry
whose hard side is the one that closes pairs.

---

## 6. Why the hypothesis was reasonable, and why it still fails

The UNDERWATER study (faaafa51) established that L5 `EXTEND`s 69.2% of the
time when deeply underwater, that 67.2% of his episodes recover even from
below −20c, and that our shell converted **72 sampled EXTENDs into
COMPLETEs**. The inference — "we are converting away his recovery mechanism" —
follows naturally.

It fails because L5's recovery mechanism is not the `EXTEND` on its own. It is
`EXTEND` **plus** a complement that becomes reachable, at his cadence: p50 6
fills and 2 switches per episode at 2.0 s dwell. Our shell ran 104 and 123
`COMPLETE` attempts in single episodes — ~50× the attempt intensity — at a
3.2% fill rate, against **one maker price (0.66)** with pair cost never below
1.14. Preserving the `EXTEND` while the complement stays unreachable buys the
cheap side of a pair whose expensive side never arrives.

Two findings point opposite ways and **neither is promoted**: the retry-cadence
bound failed out of sample (destroys 9 of 16 fills at K=2), and this arm
produces zero matched inventory. Both are recorded as negatives.

---

## 7. What would revive it — stated in advance, so it cannot be fitted later

The hypothesis is killed for the current configuration. It would deserve
re-testing, as a **new pre-registration**, only if all of the following hold —
and none of them is a request to change a cap:

1. `COMPLETE` maker fill rate rises materially above the observed 6.15%
   (the andfen-ryaseg market, where we repriced against a moving book, reached
   4/10 — that is the mechanism worth studying, not the cap);
2. an arm produces a **non-zero matched-contracts delta** under
   `FILL_SUPPORTED`, not merely a positive settlement delta;
3. the settlement delta is positive across **≥4 settled markets**, not one;
4. `gain_per_unit` at the extra EXTENDs is not systematically negative — our
   observed p50 is **−0.2429**, with only 2 of 35 readings in a positive-gain
   state at all.

Condition 4 is the one that matters, and it restates the standing finding: we
are not failing to act in cheap-complement states, **we are almost never in
one**. Position state, not execution mechanism, remains the binding variable.

---

## 8. Accounting labels

Everything above is **historical replay / shadow**. No figure here is
realized. `+$1.5994` is a *counterfactual settlement delta on a shadow
position*, computed against venue-confirmed winners; it is not P&L, was never
submitted, and is never added to the maker-outcome, incentive, or
venue-confirmed-realized ledgers. `cov-bha` is reported as **UNSETTLED**, never
as zero.

Reproduce: `python3 analysis/l5/extend_preserve/run.py`
