# Inventory-aware controller — implementation and economic validation

Issue #192, the inventory-engine comment. Branch
`claude/l5-inventory-trading-strategy-69rtje`, based on
`claude/l5-defensive-8window-prod` (the lane that carries the replay engine,
continuous shadow and risk controls; `main` carries only the ops workflows).

**No live execution, no orders, no risk-limit change, no new service, no new
infrastructure cost.** The authorized three-window maker-only validation is
untouched. The SOL executor and the Gemini research lane are untouched.

---

## 1. What was reused, not rebuilt

| component | path | how it was used |
|---|---|---|
| replay harness | `analysis/profit_search/replay_b.py` | maker queue clearance **by volume only**, fee model, TTL cancels, crossing latency, window settlement. Arm A *is* `replay_b.run_window`, called unmodified. |
| window dataset | `analysis/profit_search/windows.json` | 130 settled 15m windows, 2026-08-30 23:30Z → 2026-09-01 12:15Z; KXETH15M 50, KXBTC15M 48, KXXRP15M 15, KXSOL15M 14, KXDOGE15M 3. BBO median cadence 6.4 s, full print tape. |
| tick tapes | `analysis/profit_search/tick/` | inspected; **4 windows only**, too few to calibrate a settlement probability against, so the estimator does not use spot/strike and says so. |
| continuous shadow | `src/gemini_execution/continuous_runner.py` | one additive field in the existing `observe_cycle` record. No second controller, no second evidence file. |
| L5 observed tape | `data/l5/raw/activity.jsonl` | 63,569 `btc-updown-15m` trades across 527 windows → arm D. |
| fee schedule | `src/fee_calc.py`, `replay_b` constants | taker θ 0.0695, maker θ −0.0125, **$0.01 per order**. |

New code, all of it small and pure:

* `src/gemini_execution/inventory_engine.py` — the evaluator (BUY UP / BUY DOWN / WAIT / CANCEL-REPRICE).
* `src/gemini_execution/inventory_prob.py` — causal probability estimator, train-split fitting, partial-oracle instrument.
* `src/gemini_execution/inventory_shadow.py` — read-only adapter onto the existing cycle.
* `analysis/profit_search/replay_inventory.py` — the four-arm replay.
* `analysis/profit_search/l5_observed_actions.py` — arm D classifier.
* `tests/test_inventory_engine.py` (27), `tests/test_inventory_shadow.py` (9).

---

## 2. The decision engine

At every frame the engine prices all four candidates — both sides × maker/taker —
against state available **at that timestamp only**, and picks the best, or WAITs.

Marginal economics per candidate, fee-inclusive and fill-probability weighted:

```
ev_if_filled = qty * (P(this side wins) - own_cost) - fee
gross_ev     = ev_if_filled * P(fill)
risk         = |residual| * (sqrt(p(1-p)) + sigma)        # matched pairs: zero
risk_adj_ev  = gross_ev - kappa * (risk_after - risk_before) * P(fill)
```

* **Extension is permitted** — the engine has no rule against being long. It is
  held to a strictly higher bar (`min_edge_extend` 0.06 vs `min_edge` 0.02)
  because adding exposure adds settlement variance, not because buy imbalance
  is bad.
* **Pairs are never force-completed.** Completion is one candidate among four.
  `test_pairs_are_not_force_completed` pins the case where the engine holds a
  one-sided position because completing it is the worse marginal trade.
* **Maker fills are never inferred from a quote being touched.** `P(fill)` is a
  function of the queue ahead of us and the horizon; an order behind the touch
  gets zero, and in replay a resting order is cleared by **volume** only.
* **Adverse selection** shifts `p` against the side we would be filled on.
* **Taker quantity is bounded by the depth of the frame we actually cross into**
  after latency, not by the depth we decided on — partial fills included.
* **Hard limits are refusals, never penalties**: per-side qty, signed residual,
  capital, worst-case settlement loss, daily loss, concurrent orders. Every one
  has a test.
* **No probability → WAIT.** Stale, too old, or `sigma` over ceiling rejects
  every buy candidate and the WAIT is recorded with *that* reason, so an absent
  signal can never be read back as a flat market.

Every decision records the chosen action **and every rejected alternative** with
its EV, fill probability, risk before/after, worst case, inventory before/after
and refusal reason (`analysis/profit_search/inventory/decision_ledger_sample.jsonl`).

---

## 3. The probability problem — the decisive result

The engine needs `P(UP settles)`. The only causally available features in the
130-window dataset are the market's own book and tape, so the estimator is
`p = mid + tilt(book imbalance, trailing signed flow)`, with the tilt fitted by
OLS on the **chronologically earlier** split only.

**Fitted tilt, three chronological splits (train → OOS):**

| train frac | n train | b_imb (t) | b_flow (t) | resid sd |
|---|---|---|---|---|
| 0.33 | 341 | −0.0203 (−0.62) | +0.0982 (+1.12) | 0.409 |
| 0.50 | 522 | −0.0408 (−1.55) | +0.1010 (+1.51) | 0.403 |
| 0.70 | 729 | −0.0172 (−0.86) | +0.0918 (+1.99) | 0.363 |

**Out-of-sample skill against the market mid (split 0.50, Brier):**

| split | n | Brier(mid) | Brier(mid+tilt) | skill | implied α |
|---|---|---|---|---|---|
| train (in-sample) | 522 | 0.16489 | 0.16102 | **+2.35 %** | +0.012 |
| **OOS (held out)** | 528 | 0.07648 | 0.08696 | **−13.70 %** | **−0.066** |

The tilt is **worse than the market's own mid out of sample.** Book depth
imbalance predicts the **mid** (the previously measured t = +6.41 result stands)
but it does **not** predict **settlement**, and its settlement coefficient is
consistently the *opposite* sign in all three splits. There is no probability
edge in this feature set.

**How much skill would be needed.** A partial-oracle instrument resolves exactly
α of the market's settlement uncertainty (α = 0 is the mid, α = 1 is foresight).
Sweeping α with the probability gate held open, so economics and not the gate
decides:

| α | windows traded | OOS net | per window |
|---|---|---|---|
| 0.00 – 0.120 | 0 | $0.0000 | WAIT |
| **0.125** | 1 | +$6.40 | **break-even threshold** |
| 0.15 | 5 | +$104.38 | +$20.88 |
| 0.20 | 12 | +$234.53 | +$19.54 |
| 0.50 | 59 | +$814.83 | +$13.81 |

**Required ≈ α 0.125. Available: α +0.012 in-sample, −0.066 out of sample.**
A shortfall of roughly 10×, with the wrong sign where it counts. There is no
marginal zone: the engine either has edge and is immediately profitable, or has
none and waits.

---

## 4. Four-arm replay, chronological out-of-sample

65 untouched OOS windows (split 0.50). Command:

```
python3 analysis/profit_search/replay_inventory.py --train-frac 0.5 \
  --out analysis/profit_search/inventory/summary_split50.json \
  --ledger-out <ledger>.jsonl
```

| arm | elig | traded | net | per window | cost | max DD | fees | resid units |
|---|---|---|---|---|---|---|---|---|
| A pairing (policy D + defensive 1.20) | 65 | 39 | **−$2.7204** | −$0.0698 | 44.89 | 3.18 | 1.30 | 4.06 |
| **B inventory controller** | 65 | **0** | **$0.0000** | $0.0000 | 0 | **0** | 0 | **0** |
| C WAIT / no-trade baseline | 65 | 0 | $0.0000 | — | 0 | 0 | 0 | 0 |
| O inventory + **oracle** p *(bound, not a return)* | 65 | 64 | +$843.5659 | +$13.18 | 2132.14 | 0 | 44.36 | 3020.07 |
| B on the train split (in-sample) | 65 | 0 | $0.0000 | — | 0 | 0 | 0 | 0 |

OOS decisions evaluated: **3,672 — WAIT 3,672 (100 %)**. Reason census:
`PROB_UNCERTAIN` 3,599, `PROB_STALE` 73 (no trailing flow). Residual exposure at
settlement: 0 of 65 windows. Capital used: $0.

**B ties C and beats A by +$2.72 over 65 windows** — the gain is entirely from
declining trades the pairing controller took. Identical outcome at splits 0.33
(88 OOS) and 0.70 (39 OOS): WAIT 100 %.

**The oracle arm is the important control.** +$843.57 on the same windows,
through the same fee model, the same maker-queue rule, the same latency and the
same depth caps, proves the execution machinery converts a probability edge when
one exists. **The binding constraint is the forecast, not the execution.**

**Cost of fabricating the edge** (gate raised past the fitted residual sd, which
forces the controller to trade the unvalidated signal):

| split | OOS traded | OOS net | per window | max DD | worst-case loss / window | in-sample net |
|---|---|---|---|---|---|---|
| 0.33 | 4 | −$0.2715 | −$0.0679 | 1.29 | mean −$4.18 | $0 (no trades) |
| 0.50 | 37 | **−$10.3832** | −$0.2806 | **20.97** | mean −$2.88, min −$31.38 | **−$6.1830** |
| 0.70 | 2 | +$1.7000 | +$0.8500 | 0.07 | mean −$11.65 | −$1.8845 |

Negative in aggregate, 4× worse per window than the pairing controller on the
largest cell, and **negative in-sample too** — so this is an absent signal, not
an overfit one. The one positive cell is **n = 2** and carries a −$23.23
worst-case loss per window; it is noise, not a result. (The three splits share
windows and are not independent.)

---

## 5. Arm D — L5's observed actions

`python3 analysis/profit_search/l5_observed_actions.py`
527 windows, 63,569 `btc-updown-15m` trades, 33 overlapping the 48 Kalshi BTC15M
replay windows. Actions classified with the engine's own vocabulary:

| action | count | share |
|---|---|---|
| **EXTEND** | 32,611 | **51.3 %** |
| REDUCE | 28,056 | 44.1 % |
| FLIP | 2,803 | 4.4 % |
| **COMPLETE** | 99 | **0.2 %** |

Windows ending flat: **0 of 527**. Windows ending with signed residual:
**527 of 527 (100 %)**. Peak |residual| within a window: median 268, p90 596,
max 1,180 contracts. Trades per window: median 122, p90 258.

**The premise of this change in direction is confirmed.** L5 is not a pairing
bot: he increases signed exposure on half his trades and never ends a window
flat. Building an inventory controller was the right call.

**What does not transfer, measured rather than asserted.** L5 trades Polymarket
at his own prices. Replicating his *cadence* under Kalshi's schedule — 122
trades/window at his median clip of ~2.2 contracts — costs **$1.22/window in
per-order fees alone** and **≈$3.05/window all-in**, which is **≈$293/day on one
series before any P&L**, against a per-window P&L scale of cents. His
operation's economics depend on a fee structure PMUS does not have. That is a
structural finding, not a tuning problem.

---

## 6. Continuous shadow integration

One additive field in the existing cycle record
(`continuous_runner.observe_cycle` → `inventory_decision`), produced by
`inventory_shadow.shadow_decision`. It evaluates the inventory engine on the
**same state the live controller just decided on**, so the two are comparable on
identical inputs, and writes to the existing `continuous_v1.jsonl`. No new
service, no second evidence file, no second controller.

The adapter ships **uncalibrated on purpose** (`UNCALIBRATED`, `resid_sd 0.5`,
`n_train 0`), carrying the reason in the row itself: OOS skill was negative, so
the engine WAITs by gate. `test_the_shipped_calibration_asserts_no_edge_so_the_engine_waits`
pins that, and `test_a_calibrated_signal_would_be_acted_on_proving_the_gate_is_the_blocker`
pins that the wiring is live rather than dead — with a hypothetical skilled
calibration the same inputs produce a trade. `test_the_adapter_has_no_order_path`
asserts no order path exists in the module.

---

## 7. Verdict

* The inventory-aware controller **is built, tested and validated**, and it is
  wired into the existing shadow. 36 new tests pass.
* Its out-of-sample economics are **$0.00 / WAIT 100 %**, which **dominates the
  pairing controller (−$2.72 / 65 windows) and ties the no-trade baseline**.
  That is the correct output of a correct engine on a dataset with no
  probability edge — not a failed run.
* **NO-GO for live.** Nothing here qualifies for live authorization, and none is
  requested.
* **Biggest blocker to realized profit: the settlement forecast, not execution,
  not inventory policy, not the controller.** The oracle arm (+$843.57 on the
  same windows, same frictions) proves the execution path monetises an edge it
  is given. The requirement is now a number: **α ≈ 0.125** of settlement
  uncertainty resolved. Against that, book imbalance and trade flow deliver
  **−0.066**.
* The next useful work is therefore a **settlement-probability estimator built
  on the reference index** — strike vs `GRR-KAIKO_RFR_*_60S`, whose 60-second
  rolling mean makes (60−τ)/60 of the settlement value already determined at τ
  before expiry (≈83 % at τ = 10 s). It is the only candidate with a mechanical
  reason to clear α 0.125, and the engine is already built to consume it: hand
  it a calibrated `Prob` and it trades; hand it nothing and it waits.
  **No authorization is needed for that step** — see the correction below.

### Correction to checkpoint 1/4's infrastructure claims

Both were wrong, and they change the stated blocker.

1. **`pmus-btc15m-tape` has no volume mounted** (`volumes: []`), and runs
   `--out-dir /state/pmus-btc15m` onto ephemeral container disk. The
   "2,191,558-row file on a Railway volume this session cannot mount" therefore
   **does not exist as a durable file**; it is not the blocker, and volume
   access would not retrieve it. `btc-ref-collector` likewise has no volume.
2. **It already runs `--interval 2`, not ~30 s.** Measured on the live echoed
   stream: **median gap 10.30 s, p90 18.57 s, max 34.19 s** over 61 rows in
   10 minutes, at full depth. The proposed `--interval 1` change, and the
   ~15–30× log-volume cost it carried, **is withdrawn as unnecessary.**

Consequently the estimator can be built and validated **read-only from what is
already running**, bounded by log retention rather than instrumentation. The
only approval worth coming back for is *durable* persistence (a volume on an
existing collector) if retention proves too short for the ≈200 settled windows
the calibration needs. None has been requested or created.
* Parameter sweeps ran on `kappa` and the probability gate. Nothing was tuned on
  the held-out windows, no 48-window BTC held-out set was fitted, and the
  reported arms come from a single pre-declared configuration.

---

# Addendum — PMUS, and three errors in the above

Owner direction: *"Use live tests when possible. The shadow does not reliably
show accurate results as we've proven it wrong getting fills. Don't worry about
Kalshi. Focus on PMUS."*

Both points were right, and the second one invalidated part of the work above.
Everything in sections 1–7 was computed with **Kalshi's** constants out of
`replay_b`, on **Kalshi** `KX*` windows, with a **modelled** fill probability.

## The three errors, and what each cost

| # | error | truth | effect |
|---|---|---|---|
| 1 | charged `PER_ORDER = $0.01` per order | PMUS's published schedule (`src/pmus_fees.py`, reproducing all 99 rows of `docs.polymarket.us/fees`) has **no per-order fee** | taxed every decision. **The "≈$293/day in fees at L5's cadence" figure is WITHDRAWN** — it was an artefact of a fee PMUS does not levy |
| 2 | netted the maker rebate as `theta = −0.0125` into cost | the rebate is a **credit**, income only when the venue credits it; `pmus_fees` never subtracts it | flattered every maker candidate with money not yet received |
| 3 | invented `P(fill)` from displayed depth — returned **~0.05** at the touch | **0.43** measured on **965 of our own submitted orders**, cancels CENSORED not counted as permanent no-fills (`analysis/l5/pmus_fill_model.json`) | **wrong by ~9×, biased toward suppressing maker trading.** This is the error the live campaign exposed |

Error 3 is the one that mattered: a shadow that underestimates its own fills by
an order of magnitude cannot be trusted about fills, exactly as observed.

## What was fixed

`src/gemini_execution/pmus_execution_model.py` — one object answering both
questions the engine asks of a venue: what does this cost, and will it fill.
PMUS fees (no per-order, banker's rounding, rebate reported not netted) and
`CalibratedFillModel`, which serves the measured Kaplan-Meier curve and
**returns `None` past 32.2 s** — the longest any of our orders has ever rested.
The engine turns `None` into a refusal, `FILL_UNESTIMABLE`, rather than an
extrapolation. `pmus_config()` caps `maker_rest_s` and `order_ttl_s` to that
horizon. The shadow adapter now uses this profile by default. 20 new tests.

## PMUS data, built because none existed

`pmus-btc15m-tape` has **no volume**, so its `--out-dir` is ephemeral and the
echoed log stream is the only durable source.
`analysis/profit_search/pmus_windows_from_logs.py` assembles real
`cpc-btc-updown-15m-*` windows from it, settled from the venue's **own terminal
print after `MARKET_STATE_EXPIRED`** (`last ≥ 0.90` → YES won, `≤ 0.10` → lost;
anything else dropped as UNRESOLVED, 11 of 30 were).

**19 settled PMUS windows, 2026-10-25 11:45Z → 18:30Z, median 23 frames each,
8 up / 11 down.** This is a small sample and is reported as such.

## PMUS replay — fills drawn from the measured curve, 40 seeds

`analysis/profit_search/replay_pmus_inventory.py`. A resting order fills by a
Bernoulli draw at the **measured** probability; every arm runs over 40 seeds and
is reported as mean ± sd with its worst seed, so no result can be a lucky path.

| arm | traded | net mean | sd | worst seed | per window | WAIT % |
|---|---|---|---|---|---|---|
| **B inventory (PMUS fees + measured fills)** | 0 | **$0.0000** | 0 | 0 | $0.0000 | **100 %** |
| C WAIT baseline | 0 | $0.0000 | — | — | — | 100 % |
| O inventory + oracle p *(bound, not a return)* | 10 | +$198.47 | 1.10 | +196.88 | +$19.85 | 56.7 % |
| B on the train split (in-sample) | 0 | $0.0000 | — | — | — | 100 % |

**Arm A (pairing controller) is NOT RUN and is not reported as zero.**
`replay_b` clears its maker queue from a trade tape; this quote-only stream has
none. Substituting a fill rule it never had would be inventing its result.

**Break-even requirement on PMUS: α ≈ 0.13** (α 0.125 → no trades, α 0.130 →
+$4.90). Against **α ≈ 0.125 on Kalshi**.

## The verdict survives the corrections

This is the part worth stating plainly: **fixing all three errors moved the
break-even requirement from 0.125 to 0.13 — i.e. not at all.** Removing a fee
PMUS never charged and raising the fill probability 9× did not rescue the
economics, because the requirement is set by the **settlement variance of a
near-coin-flip binary**, not by execution frictions. The engine still WAITs
100 % of the time, and still for the right reason: on the PMUS quote-only
stream there is no trade flow at all, so no tilt can even be fitted
(`n_train = 0`, `resid_sd = 0.5`), and the gate refuses.

So: the fee claim was wrong and is withdrawn; the fill model was badly wrong
and is replaced with measured data; **and the conclusion that the binding
constraint is the settlement forecast is unchanged and now rests on PMUS's own
fees, PMUS's own windows and our own real fill outcomes.**

## What only a live test can answer

One thing in this system is structurally unmeasurable from any shadow or
replay, and it is not the probability:

> **P(fill) is unestimable past 32.2 s. L5's median complement gap is 47 s.**

We have never once rested an order at the horizon his behaviour lives at. The
engine returns `FILL_UNESTIMABLE` there and refuses — correctly, but that means
the single most load-bearing execution parameter for an inventory strategy that
rests orders is **outside our evidence, and no replay can extend it.** Resting
real orders longer is the only instrument that measures it.

That is a maker-only, bounded, directional-exposure-free measurement, and it is
the next thing worth doing live. It is **proposed, not armed** — see the issue
comment for the concrete envelope. Nothing in this addendum arms, deploys or
authorizes anything.
