# The entry band and the held-inventory band are two rules

**Production unchanged.** `f7a3e224` / deployment `86eb63ff` serving, 160
recorder seats, watch limit 160, risk envelope untouched. Nothing here was
submitted, priced, or repriced; no armed module imports any of it.

---

## P0 — the two concepts, and which one is actually firing

They are already two rules in the live shell and nobody had written it down.
`entry_band_left` fires when

```
not q1.entry_ok(bid)   OR   (flat AND bid outside the observation band)
```

so with **held inventory only the LEGAL-PRICE band (0.20–0.80)** can fire,
while **flat** is additionally gated by the observation band (0.25–0.75). A
rule whose name, comment and justification are all about *admitting a new
market* is, in the held case, adjudicating whether to add to a position we
already own.

75 firings, 13:00–21:10Z, 650 decisions, 37 markets:

| | n |
|---|---|
| **HELD_INVENTORY_BAND** (B) | **74** |
| ENTRY_BAND (A, flat) | 1 |
| sampled EXTEND | 70 |
| sampled OPEN | 5 |

**Where the bid was when it fired:** above 0.80 — **70**; below 0.20 — **5**;
inside the legal band — **0**. The held-inventory band never fires on a
marginal price. It fires when the market has already resolved in one
direction.

**And it is one market.** `aec-mlb-lad-mia-2026-09-13` accounts for **67 of
75**; `atc-epl-cov-bha` 4, `aec-setkameua-sapole-salvas` 3,
`aec-setkameua-buihen-kruvol` 1. Any conclusion drawn from this corpus is a
conclusion about one baseball game.

Per-decision rows — market, category, TTE, price, distance outside band, held
side/qty/avg cost, matched, residual qty and $, sampled probabilities, shell
action, tape support — are in `band_rows.json` (75 rows).

### The case: lad-mia

OPEN at 18:23, builds a **DOWN-only** position to 8.79 contracts / $3.7071
(avg $0.4217). From 18:36 the YES bid climbs 0.85 → 0.87 → 0.91 → 0.945,
i.e. the market runs *against* the held side. The band fires 67 times, each
time converting a sampled EXTEND into a COMPLETE. At 20:37 the market
reverses violently (bid 0.925 → 0.19 → 0.015), the engine buys UP at ~1.5c,
and DOWN settles as the winner. **Venue-confirmed realized: +$6.0034.**

So the naive reading is "the band blocked 67 chances to buy the eventual
winner at 5–15c." That reading is wrong, and the reason is P2.

---

## P1/P2 — three arms, with every other gate left in force

CONTROL is the shell exactly. SHADOW_1 gates the band on OPEN only; held
inventory follows its sampled action **subject to every other gate**.
SHADOW_2 keeps entry unchanged and widens the *continuation* band by fixed
±5c / ±10c / ±15c, plus unrestricted. **The widths are fixed, not fitted.**

Two economics columns, never mixed: **TAPE_SUPPORTED** counts a
counterfactual EXTEND as filled only where a real EXTEND order filled in the
same market within 10 minutes; **FILL_ASSUMED** fills every admitted extend
and is labelled an upper bound. Settlement is anchored to our own
venue-confirmed realized numbers (all three settled markets reconcile
exactly — see `settlement_reconciliation`).

| arm | shadow fills | extra capital | settlement Δ | blocked by band | blocked by **residual cap** |
|---|---|---|---|---|---|
| CONTROL | 0 | $0.00 | 0.0000 | 74 | 0 |
| **SHADOW_1 entry-only** | **6** | $2.21 | **+1.5994** | 0 | **64** |
| SHADOW_2 ±5c | 1 | $0.18 | +0.9945 | 70 | 3 |
| SHADOW_2 ±10c | 5 | $0.72 | +1.5919 | 29 | 40 |
| SHADOW_2 ±15c | 5 | $0.72 | +1.5919 | 6 | 63 |
| SHADOW_2 unrestricted | 6 | $2.21 | +1.5994 | 0 | 64 |

### The finding: the band is not the binding constraint

Remove the band entirely and lad-mia's 67 refused extends become **3 fills,
not 67** — because **64 are stopped by the residual cap** (R5FT rung,
`residual_units = 6`). The position was already 4.58 units imbalanced; two
more extends exhaust the bound, and since no completion may be imputed on
top of a counterfactual fill, the shadow residual only ever grows and the
cap binds permanently.

**FILL_ASSUMED and TAPE_SUPPORTED give the same 3 fills in lad-mia.** The
ceiling is the risk bound, not the fill rate and not the band. That is the
most robust thing in this report.

My first version of this counterfactual dropped the other gates and produced
**+$62.31 of "upside" on a market with a $30 cap** — arithmetically correct
and completely wrong. A regression now re-checks the rung's residual and
spend caps against the *shadow* inventory.

### Does it improve realized expectancy? Not on this evidence.

The +$1.5994 is six fills in two settled markets pointing opposite ways:

* lad-mia: 3 fills, $1.82 capital, **+$1.9938** (DOWN won)
* sapole-salvas: 3 fills, $0.39 capital, **−$0.3944** (DOWN lost)
* cov-bha: 4 opportunities, **UNSETTLED** — contributes to neither column,
  and is reported as absent rather than as zero

Three winning fills and three losing fills is not an expectancy measurement.
The dose–response is also flat from ±10c onward (+1.5919 vs +1.5994), which
is what you get when the residual cap, not the width, is doing the work.

### The decomposition, and its honest shape

Because no COMPLETE may be imputed after a counterfactual EXTEND:

| line | value |
|---|---|
| matched-pair P&L Δ | **0.0000 by construction** |
| matched inventory produced | **0 contracts**, same reason |
| directional residual P&L Δ | +1.5994 (tape-supported) |
| terminal residual frequency | **1.00** — every counterfactual fill is still held at settlement |
| eventual COMPLETE opportunities | unchanged; the arms alter only whether an EXTEND is attempted |
| eventual COMPLETE fills | UNKNOWN |
| confirmed incentives | **not attributable** — PMUS credits rewards at account level with an empty `conditionId` |
| fees | **UNKNOWN** (venue does not report them per fill) |

**Every one of these arms buys pure directional exposure.** That is not a
side effect of the method; it is what relaxing a band on EXTEND does when
the complement is not reachable.

### Context: EXTEND fills far better than COMPLETE, and that has not changed

Observed per-order fill rates in the same corpus — lad-mia extends 6/16
(37.5%) against completes 4/121 (3.3%). Across all markets, extend 14.07%,
complete 6.78%, open 4.71%. The action the shell suppresses is the one that
fills; the action it substitutes is the one that does not.

---

## P3 — path dependence

70 shell-forced EXTEND → COMPLETE rows, each in `band_arms.json`
(`path_dependence`).

* **OBSERVED** — a real EXTEND filled in the same market inside the support
  window: **28 of 70**.
* **UNKNOWN** — no such support: **42 of 70**. No fill is credited for these
  in the strict column.
* **UNKNOWN for all 70** — what the book and other participants would have
  done *after* a hypothetical fill. **No completion is imputed on top of any
  counterfactual extend**, so no arm ever manufactures matched inventory.

The actual trajectory for all 70: the forced COMPLETE was posted and did not
fill. Forced completions filled 3/74 (4.05%) against 13/162 (8.02%) for
completions the model chose — a difference that is **not statistically
separable** at that n, stated as such.

---

## P4 — provenance of COMPLETE **+7.6905** / EXTEND **−2.7402**

Source: `analysis/l5/R5F_DAY1_SEQUENCE.md`, "Band quantification (item 5)",
run R5F-RUN-2026-09-07.

**They are not two halves of one band study.** They come from two different
gates:

| | COMPLETE +7.6905 | EXTEND −2.7402 |
|---|---|---|
| gate | `no_legal_maker_price` (0.20–0.80) | `entry_band_left` |
| refused decisions | 20 | 33 |
| L5 action refused | complete ×20 | extend ×33 |
| intended notional | $63.8095 | $14.2102 |
| contracts | 71.50 | 121.34 |

* **Sample:** day 1 of one campaign — 16 markets (11 with fills, 11 settled),
  314 decisions. Venue realized over settled markets: **−8.8243**.
* **P&L definition:** "counterfactual delta vs actual" — the refused order is
  assumed to **fill at the refused price**, then valued at the venue's
  **actual resolution**. So: settlement-anchored, **fill-assumed**.
* **Settled vs modelled:** settlement real, **fills modelled**. 3 of the 20
  COMPLETE rows and 6 of the 33 EXTEND rows took the paying side from the
  venue's public settlement price and are flagged **UNCONFIRMED**.
* **Per order or per market:** **per decision/order**, summed.
* **Incentives:** **not included** — no rebate or reward term anywhere in the
  delta.
* **Residual settlement:** **included** — the delta is taken against the
  venue's actual resolution, so held residual settles.
* **Selection bias:** **yes, and material.** 15 of the 20 COMPLETEs are one
  market (`tomvoj-ticjir`); the EXTENDs are 12+9+6+3+3 across five. The
  original report says it outright: the refused extends "mostly stopped
  buying a side the venue then settled against … which is a selection fact
  (the price ran through the range in minutes)". The tape shows that price
  collapsing 0.16 → 0.12 → 0.05 → 0.03 inside 90 seconds. And the owner's
  suspicion is correct: the COMPLETE refusals are all **above-par pairs** —
  the gate fires precisely where completion is already attractive.

**Conclusion on provenance: −2.7402 must not be used as the argument against
relaxing the held-inventory band, in either direction.** It is 33 fill-assumed
decisions from one day, dominated by two markets, and its sign is set by
which way those two markets settled. Today's corpus is the mirror image — 67
refusals in one market whose held side *won* — and it produces the opposite
sign for the same reason. Two single-market samples with opposite outcomes
are not evidence about expectancy; they are evidence about variance.

---

## P5 — durable export

### Can a second, non-executing service mount the volume read-only? **No.**

Railway's own documentation: **"A volume can be attached to one service."**
(Infrastructure-as-Code reference.) There is no read-only attach, no second
mount, and no shared mode. Related, and worth stating because it bounds any
re-attach idea: **"services with an attached volume cannot run two
deployments at once"**, so detaching or moving the volume costs the probe
downtime. The architecture the owner asked about is not available on this
platform.

### But there is a better path than the log exporter, and it needs no production change at all

The **Railway CLI exposes first-party volume file access**:

```
railway volume files list /
railway volume files download /state/pmus-spring/ledger.jsonl ./ledger.jsonl
```

I installed the CLI in this research environment to check: **`railway
5.54.1`, `railway volume files` is present.** This reads the ledger through
Railway's authenticated control plane — **no second writer, no second
executor, no change to ledger ownership, no probe redeploy, nothing added to
the armed image.**

It requires one thing: the CLI must be authenticated (`railway login` or a
token in the research environment). **That is a credential decision and it is
yours, not mine — I have created no token and read no secret.** For the
record on scope: a Railway token is project-control, not trading capability;
it cannot place an order. It can change services, which is why I am not
minting one unilaterally.

**Recommendation: prefer this over the stdout exporter.** The exporter stays
built and tested (`src/pmus_ledger_export.py`, 16 isolation regressions,
default OFF) as the fallback if you would rather not authenticate a CLI, but
it is now the second-best option — it requires a new probe image and this
does not.

---

## Against the decision rule you set

> If ENTRY-ONLY materially improves fidelity AND supported economic
> counterfactuals improve expectancy → candidate for realtime shadow.
> If fidelity improves but economics worsen → keep current band.
> If evidence is insufficient → keep collecting.

**Fidelity: materially improves.** 74 of 77 sampled→acted conversions in the
current corpus disappear.

**Economics: insufficient, not favourable.** +$1.5994 total, from **six**
supported fills across **two** settled markets with opposite signs, on $2.21
of capital. Every dollar of it is directional residual with zero matched
inventory produced.

**And the constraint is somewhere else.** With the band removed, 64 of 67
extends are stopped by the **residual cap**, not the band. Relaxing the band
without touching the cap changes 67 refusals into 3 fills. That makes the
band a smaller lever than the P1 fidelity arithmetic implied, and it means a
band change alone cannot deliver the missing EXTEND mass.

**→ EVIDENCE INSUFFICIENT. Keep collecting. No live band change.**

---

## Files

* `band_rows.json` — the 75 P0 rows.
* `band_arms.json` — arms, economics, path dependence, settlement
  reconciliation, observed extend fill rates.
* `run.py` — the replay. `src/l5_band_semantics.py` — the primitives.
* `tests/test_l5_band_semantics.py` — 23 regressions.
