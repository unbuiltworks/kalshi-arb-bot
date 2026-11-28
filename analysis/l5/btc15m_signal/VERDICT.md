# BTC15m directional probability signal — NO PROMOTION

Issue #194. Research only. No service created, no live directional trading, no
exposure change, no authorization change. `L5_LIVE_HALT` untouched by this work.

**Verdict: the combined paired-plus-selective-underdog controller does NOT
deserve promotion.** On held-out windows it loses to paired-only in 17 of 18
parameter variants, it doubles-to-triples maximum drawdown in every variant,
and the one variant that is nominally positive is ten times smaller than its
own confidence interval. The variant that looked best in training reverses
sign out of sample.

---

## 1. What was asked, and what each step returned

| Step | Result |
|---|---|
| Run the new tests | 21/21 pass, no corrections needed |
| Verify baseline parity | exact: 80 failing ids, identical set; 16,803 passed on the merged tree |
| Reuse existing BTC collectors for as-of estimates | done, `analysis/l5/btc15m_asof.py` |
| Verify settlement reference, strike, timestamps | done; the published description is **wrong**, and the functional differs by stream (§3) |
| Calibrate chronologically | done, 4,095 observations over 315 windows |
| Integrate into the replay and compare | done, §6; cross-checked through the owner's gate, §6b |
| Promotion? | **NO** |

## 2. The data, and what it is not

`data/btc15m/coinbase_1m.jsonl.gz` — 194,478 Coinbase 1-minute candles,
2026-05-20 to 2026-10-02, restored to this branch from commit `7ac01e05`
(the blob already existed in the object store; nothing new was collected).
0.011% of minutes missing.

**Coinbase is a PROXY. The settlement source is Chainlink.** Every number
below inherits that. The other two BTC series in the repo were checked and
rejected for this purpose: `data/ref_lag/` is Gemini spot covering only
2026-08-31 13:28Z to 2026-09-01 14:02Z with 38 gaps over 60 s (max 58 min),
and `analysis/profit_search/tick/spot.json` is 50 minutes on 2026-10-25.
Neither spans the resolved-window corpus.

Of 526 windows with L5 prints, **312 have reference coverage** and 214 do not
(the candle tape begins 2026-05-20; the strike needs the minute before the
open). 313 of the 315 fully-covered resolved windows are in the TWAP era, so
this corpus is effectively **2026-08-28 to 08-31 — four calendar days**. That
is the single biggest limit on everything here, and no amount of statistics
fixes it.

## 3. The settlement functional, and a correction to the published description

The market description says resolution uses "the time-weighted average price
(TWAP) of Bitcoin ... **of the time range specified in the title**". That
reading is the **worst** of the candidates, in both reference regimes.

Polymarket switched streams during the corpus, which turns this from a fit
into an identification: the two groups are scored separately with the same
candidate set, and **the winner inverts**, each time landing on the functional
its named stream implies. Reproducible via
`analysis/l5/btc15m_settlement_id.py` over 1,138 resolved windows from four
captures (`data/l5/raw/markets.jsonl` plus three `data/traders/*/raw/`).

**`btc-usd`** — 400 covered windows, 2026-05-21 → 2026-08-04:

| candidate | agreement |
|---|---|
| **terminal close vs window open** | **385/400 = 96.2%** |
| terminal close vs prior minute close | 384/400 = 96.0% |
| terminal mean vs window open | 365/400 = 91.2% |
| trailing-60s mean at both endpoints | 352/400 = 88.0% |
| window-wide mean vs open *(what the words say)* | 293/400 = 73.2% |

**`btc-usd-twap-60s-streams`** — 421 covered windows, 2026-08-11 → 08-31:

| candidate | agreement |
|---|---|
| **trailing-60s mean at both endpoints** | **402/421 = 95.5%** |
| terminal close vs window open | 386/421 = 91.7% |
| terminal close vs prior minute close | 386/421 = 91.7% |
| terminal mean vs window open | 382/421 = 90.7% |
| window-wide mean vs open *(what the words say)* | 348/421 = 82.7% |

A functional picked by overfitting would not reverse its ranking across a
venue change it was never told about. `btc-usd-twap-60s-streams` publishes a
*rolling* 60-second TWAP and the market compares that stream's value **at**
the two endpoints — not an average over the 900 s window, which loses by 12.4
to 23.0 points depending on the stream.

Confirmed separately, over all 536 windows in `markets.jsonl`:

- `slug` epoch == `eventStartTime` for all 536;
- every window start on :00/:15/:30/:45; duration exactly 900 s for all 536;
- the strike is **observed, not quoted** — no `strike` field exists anywhere;
- ties resolve **UP** ("greater than or equal to").

`analysis/l5/btc15m_asof.py` therefore dispatches on the stream, and raises on
an unrecognised one rather than guessing:

| | `btc-usd` | TWAP stream |
|---|---|---|
| strike | price at the window open | trailing-60s mean ending at the open |
| settled | price at the window close | trailing-60s mean ending at the close |
| horizon from `t` | `window_end - t` | `window_end - t - 40` |

The 40-second correction follows from
`Var(ln of a continuous average over [T1, T1+D]) ~= sigma^2 (T1 + D/3)`.
Using the naive horizon on the TWAP stream overstates variance and pulls every
probability toward 0.5.

**SUPERSEDED 2026-11-06 — the reference has since changed off Chainlink
entirely.** Read live from `gateway.polymarket.us`, the current BTC15m
markets settle on **CF Benchmarks' BRTI**, not Chainlink. The functional form
below carried over (a trailing-60s average at both endpoints) but the index
did not, so **every fidelity and calibration figure in this report describes
a reference the market no longer uses.** See
`analysis/l5/btc15m_signal/LIVE_PREREQUISITE_AUDIT.md`. The original caveat,
which proved correct, follows.

**One gap, stated plainly: the stream in force *today* is not verified from
this session.** `gamma-api.polymarket.com` is outside this environment's
network allowlist and no local capture extends past 2026-08-31. The last
observed state is the TWAP stream, so that is the module's default. Before any
future BTC15m directional work, the live window's `resolutionSource` should be
read from the executor's own telemetry — if it has moved again, the strike and
the horizon both move with it.

## 4. Proxy fidelity: a hard ceiling, worst exactly where the money is

Proxy sign vs Chainlink resolution: **97.46% ± 1.75%** over 315 windows.
8 windows disagree. The disagreement is not spread evenly:

| |proxy move| | windows | agreement |
|---|---|---|
| < 1 bp | 32 | **62.5%** |
| 1–2 bp | 37 | 70.3% |
| 2–5 bp | 99 | 82.8% |
| 5–10 bp | 74 | 93.2% |
| > 10 bp | 73 | 97.3% |

A near-the-money opinion is the case the proxy is **least** able to support,
and near-the-money is what this market mostly offers. This is a reference-basis
limit, not a modelling limit; a better model cannot remove it.

## 5. Forecasting quality: the model does not beat the price

Held-out, 126 windows, window-clustered (all 13 decision points in a window
share one outcome, so the effective n is the window count). Note this split is
126/189 while the replay's in §6 is 125/187: forecasting quality needs only an
outcome and reference coverage, the replay also needs L5 prints to price
against, so the two universes differ slightly.

| predictor | Brier | log-loss | hit |
|---|---|---|---|
| model | 0.1505 | 0.4513 | 0.769 |
| calibrated (Platt on train) | 0.1501 | 0.4498 | 0.768 |
| **market price** | 0.1538 | 0.4609 | 0.762 |
| constant 0.5 | 0.2500 | 0.6931 | 0.500 |

The model beats 0.5 decisively — it is a real probability model. Against the
**price**, which is what you must pay, the paired window-clustered difference
is not significant anywhere and changes sign across the split:

| subset | ΔBrier (model − market) | ΔLog-loss |
|---|---|---|
| train (186 win) | **+0.00079 ± 0.00567** | −0.00031 ± 0.01545 |
| test (126 win) | **−0.00545 ± 0.00679** | −0.01499 ± 0.01798 |
| all (312 win) | −0.00173 ± 0.00434 | −0.00624 ± 0.01169 |

### The stale-price trap, measured

Measured against the most recent print before the decision (median age 6 s,
p90 36 s), the gross edge looks tradable. Measured against the first print
**at or after** the decision — the only price a forward order could meet —
it does not:

| price basis | train | test |
|---|---|---|
| most recent prior print | +0.0496 ± 0.0477 | +0.0957 ± 0.0534 |
| prior print, age ≤ 5 s | +0.0208 ± 0.0576 | +0.0594 ± 0.0750 |
| **first print at or after the decision** | **−0.0258 ± 0.0439** | **+0.0306 ± 0.0565** |

Opposite signs across the split, both intervals crossing zero. The stale basis
is the "stale/public BBO opportunity" `CLAUDE.md` forbids counting as profit,
so the forward basis is the one used in the replay. This is all **before**
fees, queue position, adverse selection and the 2.5% proxy error.

## 6. The replay: paired-only vs paired + selective underdog

Same windows, same caps, same cutoffs, same ceiling; the only difference is
one permitted extra action. `tests/test_l5_btc15m_signal_replay.py` asserts
that the combined simulator with the signal switched off reproduces
`policy_paired` exactly, window by window, at three participation rates.

Universe 312 windows → train 187, test 125. Recalibration fitted on train only.

| | A paired-only (test, part=1.0) | B best-on-train variant (test) |
|---|---|---|
| net | **+$5.5230** | +$1.4230 |
| net/window | +$0.04418 ± 0.06987 | +$0.01138 ± 0.09907 |
| **max drawdown** | **−$2.31** | **−$4.82** |
| pairs | 283 | 292 |
| residual lose rate | 0.906 | 0.874 |
| directional legs | 0 | 61, winning 24.6% |

The variant `thr=0.10, margin=0.03, part=1.0` was the best in training
(+$0.05548 ± 0.05455 per window, nominally just significant). Held out it is
**−$0.03280 ± 0.05255**. Its directional legs go from +$0.0277/leg in training
to **−$0.0485/leg** held out.

**Paired per-window delta (B − A), held out, 125 windows:**

- **17 of 18 variants negative.** The one positive is +$0.00496 ± $0.05445.
- **Every interval brackets zero.**
- Three held-out variants are nominally significant and **all three are
  negative** (−$0.0352, −$0.0465, −$0.0455 per window, all at `thr=0.10`).
- **Drawdown is worse in all 18 variants**, 2–3× at `part=1.0`.
- Adverse selection does not improve reliably: residual lose rate 0.868–0.944
  against A's 0.906.

### Two mechanisms worth keeping

**Higher confidence does worse.** Raising the threshold from 0.03 to 0.10 —
demanding a *bigger* mispricing — drops the held-out directional win rate from
28–39% to 18–25% and the per-leg result to −$0.058 … −$0.099. A signal whose
most confident calls are its worst is not a weak signal; it is being selected
against. Large model-vs-price disagreement is where the price is most likely
right and the proxy most likely wrong.

**The threshold alone gates nothing.** An underdog is priced below $0.50 by
construction, so a completely uninformative `p_up = 0.5` already clears
`threshold` on every underdog cheaper than `0.50 − threshold − margin`. The
model only earns its keep through departures from 0.5, and the margin is the
only parameter that makes the gate require real confidence. This is pinned by
`test_a_flat_half_probability_is_not_a_no_op_so_threshold_is_not_a_skill_gate`.

## 6b. The owner's gate, and the one lead worth recording

`analysis/l5/btc15m_calibration.py` (owner, `0ff3b381`) is a stricter
chronological gate: exactly one prediction per window, its own frozen split.
My report now runs it per decision offset with the **market price as the
baseline**, so it answers "does the model beat the price out of sample"
rather than "does it beat a coin flip". It agrees with my own
window-clustered statistic in sign — model better on both scores at 9 of 13
offsets — and it surfaces structure the pooled number hid.

Held-out paired ΔBrier, market minus model, positive = model better, one
observation per window:

| offset | windows | ΔBrier ± 95% | deployed gate at that offset |
|---|---|---|---|
| 60 | 122 | +0.00259 ± 0.00948 | OPEN allowed |
| 120 | 124 | +0.00566 ± 0.01045 | OPEN allowed |
| 180 | 124 | +0.00061 ± 0.00998 | OPEN allowed |
| 240 | 124 | −0.00393 ± 0.01054 | OPEN allowed |
| 300 | 124 | +0.00927 ± 0.01039 | OPEN allowed |
| 360 | 124 | −0.00347 ± 0.00930 | OPEN allowed |
| 420 | 124 | −0.00140 ± 0.01083 | OPEN allowed |
| 480 | 125 | −0.00138 ± 0.01014 | OPEN allowed |
| 540 | 125 | +0.00857 ± 0.01254 | completion only |
| 600 | 125 | +0.01071 ± 0.01308 | completion only |
| 660 | 125 | +0.01241 ± 0.01497 | **no action** |
| 720 | 126 | +0.01599 ± 0.01988 | **no action** |
| 780 | 126 | +0.01774 ± 0.02053 | **no action** |

Pooled: offsets ≤480 (entry allowed) **+0.00099 ± 0.00542**; offsets ≥540
(entry forbidden) **+0.01301 ± 0.01370**.

Two things are true at once and both belong in the record.

**It is a coherent pattern.** The advantage rises monotonically from offset
180 to 780 — not the scatter you would expect from noise — and it has an
obvious mechanism: as the horizon shrinks the lognormal gets easy and the
reference tape is more informative than a thin late book.

**It is not established, and it is not harvestable.** **No individual offset
is significant and neither pooled group is**; the late group's interval still
crosses zero on 126 windows. And the offsets where the advantage is largest
are exactly the ones where the deployed controller may not open: the entry
cutoff is 480 s, and the ≤480 s region where we *can* act is where the model
has **no** measurable advantage (+0.00099 ± 0.00542, essentially zero).

So this is recorded as a lead, not acted on. Harvesting it would require
extending the entry cutoff past 480 s, which is a change the owner has
explicitly reserved, and I am not relaxing a cutoff to chase a result that is
not significant in the first place. If it is ever pursued, the prerequisite
is not more modelling: it is the Chainlink reference history (§8), because a
2.5% proxy sign error swamps a 0.013 Brier difference.

## 7. What this does not claim

- These are **modelled replay fills** against L5's own prints, never
  confirmed fills, and nothing here is realized profit.
- Fees are maker 0.0 as deployed. B buys more contracts than A in all 18
  variants (e.g. 695–736 actions against A's 630 at `part=1.0`), so any
  per-contract fee costs B strictly more than A and cannot reverse the
  verdict in any variant.
- Four calendar days of TWAP-era reference coverage, 125 held-out windows.
  Even a true edge of the size claimed in training could not be established
  from this corpus.
- Consistent with the prior independent findings at `7ac01e05`
  ("ZERO of 12,959 windows qualify ... fully priced") and issue #192
  ("NO EDGE ... WAIT DOMINATES EVERY IMPLEMENTED STRATEGY").

## 8. Recommendation

1. **Do not promote the combined controller.** Keep paired-only.
2. **Do not spend more on this hypothesis from proxy data.** The binding
   constraint is now identified and it is not the model: it is that the
   settlement reference is Chainlink and we have measured a 2.5% sign
   disagreement concentrated under 2 bp, which is where the tradable cases
   live. A directional BTC15m programme would need the Chainlink BTC/USD and
   `btc-usd-twap-60s-streams` histories themselves, over far more than four
   days. That is a data-acquisition question for the owner, not an
   engineering one, and it is **not** recommended on this evidence.
3. **Keep the modules.** `btc15m_asof.py` is the settlement-correct strike and
   horizon for any future BTC15m work, including the 40-second horizon
   correction and the identified functional, and it is worth keeping even
   though the signal it feeds is being rejected.
4. **The residual problem is unchanged and still the real one.** Residual lose
   rate is 0.845–0.914 in replay against our live 81%. The signal does not fix
   it. Reducing residual, not predicting it, remains the profitability lever.
