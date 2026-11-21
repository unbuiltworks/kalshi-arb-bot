# Stranded-leg recovery: the five actions, and which one survives evidence

**Verdict: keep the defensive exit, make its clock continuous, and never
reprice the complement. The gain is tail, not mean, and this must never be
sold as edge.**

Scope: what to do after a first leg fills and its complement does not. Built
on `src/l5_defensive_completion.py`; no new service, no live change.

---

## 1. Five actions are three orders and a do-nothing

On a single-instrument venue "buy the complement" and "sell the held leg" are
the **same wire order** — `l5_defensive_completion` verification 2, proved by
cashflow: probe 05's eight `ORDER_INTENT_BUY_SHORT` orders credited $1.2432,
exactly their wire notional. So the requested actions 3 and 4 are not two
choices, and actions 1 and 2 differ only in limit price:

| asked | is |
|---|---|
| 1 keep complement resting / 2 reprice closer | **LIMIT SELL** of the held leg, at the old or a new price |
| 3 acquire complement / 4 close exposure directly | **MARKET SELL** of the held leg — one order, one price, one fee |
| 5 hold | no order |

Prices are wired in YES terms throughout (`l5_authoritative_ledger_repair.py:29`).

## 2. The arithmetic, before any data

From the decision state the first leg's cost `c` is **sunk**. With the
complement's executable own price `m` and the market-implied probability of the
held side winning `1 − m`:

```
HOLD        q*(1-m)                expected
MARKET SELL q*(1-m) - q*fee        certain
difference  -q*fee
```

Exiting equals holding minus the fee, **at every price, in every regime**. This
is exactly what `improves_expected_value: False` has always asserted. There is
no price at which crossing earns expectation; it can only buy variance.

A limit sell at `m' < m` adds `P(fill) * q * (m - m')` — the only term that can
be positive, and §5 shows `P(fill)` is not independent of the outcome, which
destroys it.

## 3. A lookahead trap, recorded because it nearly shipped

A first pass labelled the stranded side from the **end-of-window** state and
then priced it at T+120. It produced a −0.18 calibration gap and an apparent
**+0.1585/unit** for crossing. It was an artifact: the as-of-T+120 residual side
differs from the end-of-window side in **42.4%** of windows, so labelling by the
end selects windows where that side lost.

Re-run lookahead-free — side, size and cost basis read from the as-of-tick
cumulative columns, only the settlement outcome taken from the end — the
calibration gap collapses to **−0.029..+0.011** across every decision time. The
book is honest and the edge was never there.

The same warning applies to **our own venue's** residual census: 258 windows,
mean cost basis 0.5087, realized win rate **23.6%**. That is a true description
of realized residuals and it is **not** usable as a decision input, because it
too is conditioned on the end-of-window state.

## 4. Held-out evidence

Path and outcome from L5's Polymarket window corpus (same BTC/ETH up-down
instrument family). **Fees are PMUS's own curve**, `θ·p·(1−p)` with
`TAKER_THETA 0.0695`; the cross is charged a full 1-tick spread penalty.
Polymarket fill economics are **not** transferred.

`CROSS − HOLD` by moneyness, pooled over decision times (15m n=2094, 5m n=3670):

| band `o − c` | 15m mean | t | 5m mean | t |
|---|---|---|---|---|
| < −0.20 | −0.0109 | −0.45 | +0.0101 | 0.41 |
| −0.20..−0.10 | −0.0151 | −0.77 | −0.0336 | −1.19 |
| −0.10..−0.03 | +0.0278 | 1.09 | −0.0183 | −0.82 |
| −0.03..+0.03 | −0.0263 | −1.06 | −0.0484 | **−5.45** |
| +0.03..+0.10 | −0.0184 | −0.71 | +0.0033 | 0.15 |
| > +0.10 | −0.0303 | **−1.98** | −0.0138 | −0.82 |

**No band, at either window length, shows a significantly positive mean.** The
only two significant results are negative. §2 is confirmed empirically.

The tail is a different story. `HOLD` p05 sits at −0.59..−0.69 in *every* band;
`CROSS` p05 runs −0.39 (deeply underwater) to +0.08 (well up). **The protection
is largest while the leg is still near or above the money** — once deeply
underwater the loss has already happened and crossing recovers little.

## 5. Repricing the complement is the worst of the five

15-minute windows, decision at T+600, n=538:

| action | mean | p05 | fill |
|---|---|---|---|
| A5 hold | −0.0104 | −0.5960 | — |
| A3/A4 market sell now | −0.0385 | **−0.3245** | — |
| A1/A2 limit sell +0t | −0.0461 | −0.5007 | 64.5% |
| A1/A2 limit sell +1t | −0.0440 | −0.5141 | 59.9% |
| A1/A2 limit sell +2t | −0.0439 | −0.5162 | 54.8% |
| A1/A2 limit sell +3t | −0.0419 | −0.5162 | 51.5% |

Despite fill rates of 48–84%, every reprice is **worse than holding on the mean
and keeps holding's tail**. The reason is structural: a limit sell above the
market fills when the market rises to it — which is when the held side was
going to win anyway. It **sells the winners and keeps the losers**: adverse
selection in the opposite direction from the entry fill. `P(fill)` is
positively correlated with the held side winning, so the
`P(fill) * (m − m')` term of §2 is paid for out of the cases it gives up.

Action 2 is therefore **not implemented**, and pinned in
`tests/test_l5_stranded_leg_recovery.py` so it is not re-proposed.

## 6. The production defect: a 45-second trigger sampled every 32–131s

`DEFENSIVE_ARM_S` (60) less `DEFENSIVE_MIN_EXECUTION_S` (15) leaves an
actionable interval of **[T+540, T+585) — 45 seconds**. The executor's
evaluation cadence is irregular; gaps of 32s, 63s and 131s all occurred inside
a single window on 2026-10-23:

| window | evaluations | result |
|---|---|---|
| 0230z | T+512 (outside) → **T+544 (inside)** | exited, realized −0.0468 |
| 0300z | T+492 → T+524 → **T+587** | `INSUFFICIENT_EXECUTION_TIME`, held, realized −0.3580 |

0300z straddled the whole interval, missing its close by **2 seconds** after a
63-second gap. A trigger narrower than its own sampling jitter fires by luck,
and the 7.65× difference in realized loss between two otherwise identical
sequences is attributable to that, not to the market.

The shadow could not see this either: `_defensive_eval` returned early on
`arm_window_open`, so every cell reported `defensive_evals: 0`.

## 7. The gates already contain a stop; the clock hid it

Solved for `o`, the held side's own price, the two existing gates are a **band**:

```
priority one reachable   c + (1-o) <= 0.96   <=>   o >= c + 0.04   keep complement
pair ceiling             c + (1-o) <= 1.20   <=>   o >= c - 0.20   exit allowed
                         exit permitted only while  c - 0.20 <= o < c + 0.04
```

**The 1.20 pair ceiling is a −0.20/unit stop-loss.** It was correctly specified
all along. The arm window is what kept it from being reachable until the
position had usually already fallen out the bottom of the band.

## 8. The policy

> **Continuous bounded exit.** Keep every existing economic gate. Replace the
> arm window with a **dwell of 30s measured from the moment the leg became
> unmatched**, evaluated continuously to the terminal. Never reprice the
> complement. The new gate is a strict **superset** of the old one — dwell
> satisfied *or* arm window open — so no position that would have been exited
> before is left carrying now.

Why 30s: accepted COMPLETEs that fill rest a **median 8.61s**, against 30.85s
for those that die unfilled (`COMPLETION_ECONOMICS.md`), so by 30s a complement
that was going to fill overwhelmingly has. The sweep is flat from 15s to 120s,
so this is not a knife edge.

### Head-to-head, 15-minute held-out windows, n=555

| policy | mean | p05 | p25 | worst | exit% | **< −0.30/unit** |
|---|---|---|---|---|---|---|
| A5 hold always | −0.0570 | −0.5740 | −0.3346 | −0.8256 | 0% | 29.5% |
| **EXISTING** arm window | −0.0699 | −0.5007 | −0.2592 | −0.8200 | 32.1% | 20.0% |
| **CANDIDATE** dwell 30s | **−0.0407** | **−0.1470** | **−0.0707** | −0.8256 | 92.6% | **1.6%** |

Dwell sweep (15m): 15s → 1.6%, 30s → 1.6%, 60s → 2.2%, 120s → 2.7%, 480s → 11.0%.

**The honest claim.** Paired on n=555, candidate − existing is **+0.0171 at
t = +1.42** — *not* significant, and it must not be reported as earning more.
The tail is the claim: **9 windows below −0.30/unit instead of 111**, which at
p=0.20 on n=555 is z ≈ −10.8. The existing arm window is meanwhile *worse than
holding* on the mean (−0.0699 vs −0.0570) while capturing only a third of the
available tail reduction. **Mean-neutral, tail-transforming.**

Worst case stays −0.8256: a gap between ticks can outrun any policy, and no
clock change bounds that.

### Window length matters

On 5-minute windows the same sweep **costs** mean to buy tail (dwell 15s
−0.0081 / 7.5% vs dwell 120s +0.0117 / 12.5%). `DEFENSIVE_DWELL_S` is calibrated
for **BTC15m**, which is what we trade, and must be re-measured before it is
applied to any other window length.

## 9. The two live windows — illustration, not evidence

Replayed on the authenticated book from the production decision log:

| window | existing policy | dwell 30s | hold |
|---|---|---|---|
| 0230z | −0.0468 at T+544 | **+0.0057** at T+318 (sell DOWN at 0.08, pair 0.99) | −0.0819 |
| 0300z | −0.3580 (never fired) | **+0.0145** at T+334 (sell UP at 0.22, pair 0.98) | −0.3580 |
| total | **−0.4048** | **+0.0202** | −0.4399 |

A +$0.4250 swing on **n=2**. This is an illustration of the mechanism, and
proves nothing about the rate; §8's n=555 is the evidence.

## 10. Exact changes

**Done, inert until a caller opts in:**

1. `src/l5_defensive_completion.py` — added `DEFENSIVE_DWELL_S = 30.0` and the
   `stranded_since_t` / `dwell_s` parameters. Supplying `stranded_since_t`
   selects the dwell gate; omitting it keeps the arm window exactly, so no
   existing caller changes behaviour. Reports `timing_gate` and
   `stranded_for_s`. Every economic gate is untouched.
2. `src/l5_two_sided_shadow.py` — removed the `arm_window_open` early return so
   the shadow can observe the decision it exists to measure; added a per-band
   stranding clock; records `stranded_for_s` and `arm_window_open` on each
   verdict; timing-only refusals are counted in `defensive_timing_skips`
   instead of stored, so the payload does not grow by ~200 rows a window.
3. `tests/test_l5_stranded_leg_recovery.py` — 59 tests pinning the dwell gate,
   the superset property, every economic gate, the moneyness band, the
   reprice negative result, and the 0230z/0300z offsets.

**Required to go live, NOT done here and needing approval:**

4. `src/l5_live_execution.py` — the two `DEF.decide` call sites (`:4564`,
   `:5364`) must pass `stranded_since_t`. This needs `InventoryStore` to record
   the timestamp at which a market's residual first became non-zero, and clear
   it on return to flat — a new persisted field, written where fills are
   applied, fail-closed to `None` (which falls back to the arm window) if
   unreadable.
5. `DEFENSIVE_LOSS_BUDGET_USD` is $2.00 and the exit rate rises from 32% to
   ~93%. At ~0.04/unit on 1–2 units that is ~0.06/window, so the budget covers
   ~33 windows instead of ~100. It should be re-sized, with the owner, before
   any live run.
6. Order of operations: shadow-measure in production → compare the shadow's
   `timing_gate: DWELL` verdicts against the arm-window verdicts on the same
   ticks → only then propose a bounded live run.

## 11. What this does not claim

- It does not earn edge. Mean difference is not significant, and §2 says it
  cannot be positive by construction.
- It does not justify a larger position, a looser ceiling, or a higher budget.
- It does not transfer to 5-minute windows at this dwell.
- Two live windows prove nothing about the rate.
