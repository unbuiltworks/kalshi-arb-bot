# Candidate completion-priority patch — **VERDICT: REJECT**

Campaign closed and reconciled. Nothing deployed, production code unchanged,
the nine tests untouched, the patch still in the scratchpad.

---

## 1. The seven $0.13 History entries — identified

**They are our DOWN legs, displayed at their wire price.** The venue audit
(read-only, `post_blocked: true`, 26 pages, `ended: eof`, **`complete: true`**,
2,505 activity rows, zero errors) returns order rows like:

```
DF0S10R012WA  side: ORDER_SIDE_SELL   outcomeSide: OUTCOME_SIDE_NO
              limit_price: 0.56   qty 1.98   FILLED
              manualOrderIndicator: AUTOMATIC   commission: 0.0000
```

Window `1345z`'s DOWN leg had own cost **0.44**, and **1 − 0.56 = 0.44**. A
DOWN buy is submitted as a **SELL** at `1 − own_cost`, so a DOWN completion at
own 0.87 appears in History at **0.13**. The engine's own label states the
convention: *"wire is YES/UP; own cost of DOWN at wire p is 1 − p."* In
`1445z` the deployed quote sat at `wire_px 0.2300 / own_px 0.7700` — the same
mechanism at a different book level.

**E1 placed nothing.** A search of the complete audit for `"quantity": 0.13`
— the E1 probe's frozen first-leg size, per the audit's own attribution table
— returns **zero rows**. Because the walk reports `complete: true`, absence is
readable here (the tool's own rule is that absence may only be read from a
complete walk). Every order in the campaign carries
`ATTRIBUTED_TO_BY_SIZE: UNRECOGNISED_SIZE` at sizes 0.65–2.25, matching no
legacy strategy.

**I was wrong twice on the way here** and both are worth recording: I first
read "$0.13" as a *price* and proposed the wire-semantics explanation, then
read the audit's attribution table and proposed it was a *quantity* (E1). The
venue says it is a **price**, and the first reading was right — but I could
not have known that without asking the venue, which is why I did.

## 2. Venue-confirmed closure

| | |
|---|---|
| venue `open_orders` | **`{"orders": []}`** |
| BTC15m slugs with owned fills, 2026-11-01 | **exactly 7**: 1330z…1500z, **no 1515z** |
| rejected / partial fills / unacknowledged | **0 / 0 / 0** every window |
| commissions | **0.0000** on every order |
| `manualOrderIndicator` | **AUTOMATIC** on every order |

The venue independently confirms window 8 never traded.

## 3. Every risk-increasing purchase, and what the gate would refuse

Decomposed from venue quantities and costs, tying exactly to `up_qty`/`dn_qty`:

| window | UP legs (qty@own) | DOWN legs (qty@own) | unmatched |
|---|---|---|---|
| `1330z` | 2.08@0.44 · 2.25@0.42 · **2.14@0.40** | 2.08@0.51 · 2.25@0.62 | 2.14 UP |
| `1345z` | 1.98@0.55 · **2.05@0.46** | 1.98@0.44 | 2.05 UP |
| `1400z` | matched 2.14 · **2.09 extra** | 2.14 | 2.09 UP |
| `1415z` | **2.08@0.44** | — | 2.08 UP |
| `1430z` | — | **2.02@0.47** | 2.02 DOWN |
| `1445z` | **2.02@0.48** | — | 2.02 UP |
| `1500z` | 2@0.50 · 0.65@0.58 · 1.97@0.58 · 2.04@0.47 | 2@0.45 · 0.65@0.45 · 1.97@0.43 · 2.04@0.52 · **2.00@0.45** | 2.00 DOWN |

**Paired legs are equal quantity on both sides by construction**, so the book
is balanced after every completed pair. Each window's unmatched leg is a
*single* extra leg, so at its placement the book was **flat** or **balanced**:

| window | book at the unmatched leg | gate |
|---|---|---|
| 1330z / 1345z / 1400z / 1500z | BALANCED (4.33/4.33, 1.98/1.98, 2.14/2.14, 6.66/6.66) | **not triggered** |
| 1415z / 1430z / 1445z | FLAT (0/0) | **not triggered** |

**The gate fires on `|held_up − held_down| > 0.01` with the order on the
increasing side. There was no imbalance at any stranding leg. It would have
refused nothing, in any window.**

And the structural reason outlives this sample: **under simultaneous two-sided
quoting both legs are quoted while the book is balanced.** The imbalance
appears only *after* one leg fills, and the engine's next action is then the
complement — the reducing side, which the gate permits. The patch was built
for the **sequential** failure mode (1245z: *"nine minutes quoting DOWN — the
side it ALREADY HELD"*). Simultaneous quoting does not exhibit it.

## 4. Economics — fixed-path and sequential, kept apart

**A. Fixed-path subtraction** (limited counterfactual, not a replay). The
patch refuses nothing, so the subtraction set is empty:

| | historical | with patch | Δ |
|---|---|---|---|
| matched P&L | **+$0.261610** | +$0.261610 | **$0.000000** |
| residual P&L | **−$0.337110** | −$0.337110 | **$0.000000** |
| **total settled** | **−$0.075500** | −$0.075500 | **$0.000000** |
| completed volume | 15.11 contracts | unchanged | 0 |
| residual frequency | 7/7 | unchanged | 0 |
| worst-case exposure | ≤2.14 contracts | unchanged | 0 |

**B. Sequential replay.** With an empty refusal set there is no divergence to
propagate: inventory, budget and order state are identical at every decision,
so the replay is the historical path. **No fill was invented and none needed
to be** — which is the one methodological advantage of a change that only
refuses.

**Sensitivity to fill assumptions: none.** A patch that refuses nothing is
invariant to any fill model.

## 5. Independent out-of-sample evidence, and it agrees

`L5_SHADOW_TWO_SIDED`, 384 chronologically-controlled common windows, verdict
**`TWO_SIDED_NOT_BETTER`**:

| | control (one-sided) | treatment (two-sided) |
|---|---|---|
| net | −68.96 | **−110.46** |
| **incremental net** | | **−41.50 (−$0.1081/window)** |
| matched units | 158 | **369** (+211) |
| residual net | −112.39 | **−210.18** |
| windows with residual ≥3 | 172 | **254** |
| adverse rate / per fill | 0.8907 / 0.0907 | 0.8901 / **0.0927** |
| incremental net per incremental $ | | **−0.2019** |

Scenario sweep: CONSERVATIVE / BASE / OPTIMISTIC incremental net
**−41.50 / −37.65 / −37.65**. **The conclusion does not depend on the fill
assumption.**

**Two-sided makes more pairs and nets worse**, by moving windows into the
≥3-residual stratum where the loss lives. Our live campaign reproduced it:
**−$0.0755 over 7 windows = −$0.0108/window**, same sign as −$0.1081, scaled
to our much smaller size.

## 6. VERDICT: **REJECT**

The patch would have changed nothing in the campaign that motivated it, on
venue-confirmed data, with no fill model required. There is no economic case
to weigh against its risk, because there is no economic effect.

**I am also not recommending the fill-aware policy** (requirements 1–4 of the
brief) as the next step. `adverse_rate 0.89` means 89% of maker fills are
adverse; a complementary-fill estimator would have to beat that to help, and
the shadow already modelled three fill regimes — two-sided loses under all
three, including optimistic. Building an estimator to rescue a policy that
loses under favourable assumptions is the broad rewrite the brief rules out.

## 7. What the campaign did establish

The pre-registered criteria **failed**: residual frequency **100% (7/7)**
against a <71.4% threshold, and mean effective pair cost **0.987 / 0.944 /
1.005 / 0.984** against a ≤0.96 threshold. Three windows produced zero pairs.
Matched contracts per window ~2.2 against a 1.68 baseline is the one
criterion met, and it is marginal.

**The real defect is not inventory management. It is that one leg of a
simultaneously-quoted pair fills and the other does not — 7 times out of 7.**
That is adverse selection, measured at 0.89 across 3,675 shadow quotes, and
it is a property of resting behind the touch in a market that moves, not of
which side the engine chooses next.

## 8. NOT_AFTER regression — added

`tests/test_l5_not_after_boundary.py`, **11 tests passing**, 223 passing
across the authorization suites.

It pins that `L5_LIVE_AUTH_NOT_AFTER` bounds the **arming instant**, not the
window's start: `authorize_arming` step 8 is `if now > deadline`, and arming
runs seconds into a window (13:30:11.747Z for the 13:30:00Z window). So a
deadline at the last window's own start refuses that window **with
certainty** — which forfeited window 8. The safe convention is pinned too:
strictly after (last start + arming latency), strictly before the next window
start. Stage B2's 14:58:00Z against a 14:45z last window is included as the
pattern that worked.

**Semantics unchanged**, per the brief. One discrepancy found and recorded
rather than fixed: at exactly `now == deadline` the boot diagnostic
`not_after_status` reports EXPIRED (`expires_in_s 0.0`) while the gate's
`now > deadline` would permit. It errs in the safe direction and arming never
lands on that instant; changing authorization semantics for it is not
warranted.

## 9. If a next live validation is justified

The brief's own framing is the right one — whether completion-priority
materially reduces stranded inventory without destroying matched
profitability. **On this evidence that test is not yet justified**, because
the mechanism it would test did not fire in seven windows. What would justify
it is a policy change that acts on **adverse selection** (quote depth, timing,
or complement runway), measured first in the existing shadow against its
384-window control. No new campaign, no limit change, and fresh owner
authorization in any case.
