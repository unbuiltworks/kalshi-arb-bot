# How L5 actually manages stranded inventory — and why copying it would hurt

**Bottom line: L5's stranded-inventory behaviour is well characterised,
replicates as a predictor of his own actions, and predicts his realized
outcomes not at all. At our position size his demonstrated behaviour is the
opposite of what our economics support. The one change worth making is the one
already measured on our own venue.**

Cohort: BTC15m, 5,546 windows, 693,916 fills, **zero sells**, 2026-03-19 →
08-31, **38,180 residual episodes**. TRAIN/OOS is **chronological**
(2026-03-19..08-10 vs 08-10..08-31), never shuffled — the regime demonstrably
changed, see §5.

Reuses `analysis/l5/residual_exit/forensics.py` unchanged. New in this pass:
`analysis/l5/residual_exit/inventory_outcomes.py` (+ `_btc15m.json`) and
`tests/test_l5_stranded_inventory_behaviour.py`. Shadow lock untouched, no
capital change, no new service.

## Work already done, not rebuilt

| question | where it was already answered |
|---|---|
| episode reconstruction, 4 transitions, durations, exit modes | `residual_exit/RESIDUAL_EXIT_FORENSICS_V1.md` |
| which states predict his next **action** | same, §2 (run length ρ=1.00 OOS) |
| does refusing an adverse EXTEND help | `ADVERSE_EXTEND_RESULT.md` — **falsified**, 97.8% of adverse positions recovered (n=158,294) |
| does he ever pair a leg, and what predicts it | `L5_RESIDUAL_STATE_REPLAY.md` — 72.7% overall, 93.3% on 15m |
| is his 96.24¢ pair cost acquired or emergent | `inventory_trajectory/TRAJECTORY_ANSWER.md` — **emergent**; only 27.9% of his entries clear our 0.96 ceiling |
| is our imbalance state a scale artefact | `L5_INVENTORY_TRANSLATION.md` — 32.48 fills/window vs our 4.52 |
| did post-imbalance management cause our losses | `RESIDUAL_REPAIR_FINDINGS.md` — no; 0 of 359 states missed an available repair |

This pass adds the **outcome** test those leave open, and the translation.

---

## 1. What L5 demonstrably does

Zero sells in 693,916 fills, so inventory only ever grows and the vocabulary is
exactly four transitions. Per-episode next action:

| action | n | share |
|---|---|---|
| **EXTEND** (adds to the stranded side) | 25,264 | **66.2%** |
| SWITCH (complement buy large enough to cross zero) | 6,976 | 18.3% |
| COMPLETE_PARTIAL | 5,296 | 13.9% |
| NONE_CARRIED | 343 | 0.9% |
| COMPLETE_FLAT | 301 | 0.8% |

Episode exits: **FLIPPED 84.3%**, CARRIED_TO_SETTLEMENT 14.5%, COMPLETED_FLAT
1.2%. **Exactly one of 5,546 windows ended flat.**

So: *he adds to the stranded side about two thirds of the time, pairs by
overshooting past zero rather than to zero, and almost never flattens.*

Window ledger, reconciled: matched **+$162,256.93**, residual
**−$144,065.00**, net **+$18,191.93**. Carrying stranded legs to settlement
consumed **88.8%** of everything his pairs earned — his largest single loss
line.

### What is NOT observable, and is therefore not claimed

* **"L5 waited" is not in this data.** BTC15m has no book and no sells, so a
  quote that never filled leaves no trace. `NONE_CARRIED` (0.9%) means only
  that no fill followed; it is indistinguishable from quoting and not being
  hit. Nothing here treats the absence of a trade as a decision.
* **BTC distance from strike is not computable for this corpus.** Spot coverage
  is 2026-08-31/09-01 (a 3-hour bounded recorder) against a corpus starting
  2026-03-19. Strike distance, volatility and time remaining therefore enter
  only as the market price already combines them, plus realized print-to-print
  movement (`dp_up_60s`). This is a data gap, not an estimate.
* Book state, queue position, maker/taker per fill, and his rebates.

## 2. When he does it — the transition matrix

P(action | state), TRAIN → OOS. Only one feature is a large, monotone,
replicating brake, and it is **absolute residual size**:

| \|residual\| | extend TRAIN→OOS | complement TRAIN→OOS |
|---|---|---|
| ≤1 | 0.689 → 0.749 | 0.004 → 0.004 |
| 1–5 | 0.709 → 0.740 | 0.023 → 0.040 |
| 5–20 | 0.662 → 0.698 | 0.119 → 0.127 |
| 20–100 | **0.621 → 0.606** | **0.251 → 0.280** |

The complement rate spans **7×**. Also replicating: run length (4+ → 0.719 /
0.733, the maximum in both splits) and price movement **by magnitude, not
sign** — extend is highest at both Δp extremes and lowest when flat. Not
replicating: `window_progress` (inverts), and `residual_is_favored` has no
effect at all (0.662 vs 0.659).

### Adding to an already-overweight side — the premise corrected

P(extend) does look much higher when the book is lopsided: **0.745 / 0.761** at
`residual_rel ≥ 0.50` against 0.643 / 0.652 below, replicating across the
split. But **94.9% of those episodes hold only one side**, with a median of
**1 fill so far**, **1.9%** of the window elapsed and **zero** matched
inventory. It is the window's *opening fill*, not a double-down on a large
adverse position.

Restricted to the genuine case — lopsided **and** a complement already held —
there are only **301 episodes** (217 TRAIN, 84 OOS) and he extends **0.535 /
0.488** while complementing **0.272 / 0.440**. In the real overweight state he
leans *away* from adding, not into it.

## 3. Which behaviours improve realized outcomes — **none that replicate**

The window is the only honest unit: an EXTEND books no matched P&L and a
COMPLETE does, so per-episode net is mechanically biased toward completing and
the extend's value lands in a later episode of the same window. All episodes in
a window share one settlement.

Window net by behavioural feature, 3,882 TRAIN / 1,664 OOS windows:

| feature | ρ(train,oos) | TRAIN spread | OOS spread |
|---|---|---|---|
| max_run_len | 0.4 | 4.84 | 29.23 |
| median_episode_dur_s | −0.6 | 4.80 | 27.08 |
| complement_share | 0.1 | 8.47 | 22.41 |
| **overweight_extend_share** | **−1.0** | 9.11 | 21.48 |
| extend_share | 0.1 | 10.21 | 21.23 |
| switch_share | 0.4 | 11.04 | 19.52 |

**Every OOS spread exceeds its TRAIN spread** — the signature of noise. No
feature reaches a usable rank correlation; `overweight_extend_share` inverts
perfectly. Scale-controlling to net-per-fill does not rescue it (best ρ = 0.8
on `switch_share`, which was never a hypothesis; the rest run −0.6 to 0.5).

The same features that predict his *action* at ρ = 1.00 predict his *money* at
between −1.0 and +0.5. **Behavioural fidelity and outcome improvement are
different things here, and only the first is established.**

## 4. Is any of it executable and beneficial on PMUS — **no, and at our size it inverts**

**(a) His "never sells" is a venue artefact, not a preference.** Polymarket is
two-token; PMUS is single-instrument, where buying the complement *is* selling
the leg — one order, one price, one fee
(`l5_defensive_completion` verification 2). So his exit vocabulary does not
constrain ours.

**(b) His entry economics are outside our ceiling.** Median pair cost at first
formation **0.9900**; only **27.9%** of his own entries clear our 0.96 ceiling.
A gate requiring ≤0.96 rejects 72% of the mechanism. His 0.9740 comes from
averaging down over dozens of fills, not from acquiring pairs cheaply.

**(c) At our size his behaviour is the opposite of our economics.** Our
campaign residuals — 1.17, 1.79, 3.36 shares — sit at the **5.7th, 8.4th and
15.3rd percentile** of his (median 13.71, p75 25.52). In the 1–5 share bucket
(n=6,459) his demonstrated behaviour is **EXTEND 0.713, COMPLEMENT 0.027**: he
adds, and essentially never pairs.

He can afford that because he has **32.48 fills per window** and will reach
20–100 shares, where he complements 28% of the time. We get **4.52**. *His
small-residual behaviour is the opening phase of a trajectory we cannot
complete* — and extending a residual we cannot pair is precisely what produced
our 0300z −$0.3580 and his −$144,065.

**(d) The thing he does most with stranded legs is his biggest loss.** Carrying
14.5% of episodes to settlement cost −$144,065. Out of sample even the
*favored-side* carried residual lost money (−$12,971, n=432). Our already-built
continuous bounded exit attacks exactly this line.

## 5. His own edge decayed inside the corpus

| | window | n | sum | mean/window | fills | **net/fill** |
|---|---|---|---|---|---|---|
| TRAIN | 03-19 → 08-10 | 3,882 | **+$17,837.22** | +4.595 | 365,287 | **+0.04883** |
| OOS | 08-10 → 08-31 | 1,664 | **+$354.70** | +0.213 | 328,629 | **+0.00108** |

**Net per fill fell 45×.** 98.0% of his BTC15m net was earned before the
holdout; in the last three weeks he earned $354.70 on 328,629 fills while fill
volume held at 90% of TRAIN. He kept trading and stopped earning. BTC15m also
never produced the $480/day target — TRAIN runs to **~$124/day** across 144
days.

This is why the chronological split matters and why a shuffled one would have
been self-deception.

## 6. The single highest-value implementation change

**Wire the dwell gate into the live executor** — item 4 of
`STRANDED_LEG_RECOVERY.md`, already built and inert in
`l5_defensive_completion` and already measured on PMUS economics (n=555
held-out 15-minute windows: windows below −0.30/unit fall from 20.0% to 1.6%,
mean-neutral at t=+1.42).

It is the highest-value change because it is the **only** one with held-out
support on our own venue's economics, and it targets the line that cost L5
88.8% of his matched earnings. It needs: a persisted per-market stranding
timestamp in `InventoryStore`, fail-closed to `None` (which falls back to the
arm window), passed at the two `DEF.decide` call sites.

**What this investigation says NOT to do:**

* Do not raise the 0.96 ceiling to reach his 0.99 entries. That ceiling is why
  72% of his mechanism is unavailable to us, and his entry economics only work
  through accumulation we cannot fund or fill.
* Do not copy EXTEND at our size. His 0.713 extend rate at 1–5 shares is the
  start of a 30-fill trajectory, not a standalone rule.
* Do not adopt any residual-size brake tuned to his buckets. Our entire size
  range sits inside his smallest bucket, where his own behaviour says add.
* Do not treat his inventory management as validated by outcome. On his own
  data, out of sample, it is not.

## 7. Limitations

* **Observational.** Within-state comparison reduces confounding; it does not
  remove it. He chose using a book we cannot see.
* **n=301** for the genuinely-overweight case, and 84 of those are OOS.
* Strike distance is **absent**, not estimated (§1).
* The outcome test has 1,664 OOS windows spanning only three weeks, in a regime
  where his per-fill edge had already collapsed 45×. A null result there is
  partly a statement about that regime.
* `max_residual_rel` and `mean_abs_dp60` are degenerate at window level (every
  window opens one-sided; almost every window exceeds 0.05 movement), so they
  carry no information in the outcome table and are reported as such.
* Polymarket fill economics, rebates and incentives are **not** transferred;
  every PMUS figure quoted here comes from PMUS measurement.
