# P0-ECONOMICS — assumptions, and what each one is NOT evidence of

Companion to `PMUS_BREAKEVEN_SURFACE.md` and
`analysis/l5/economics/breakeven_surface.py`.

Every number used is listed here with its source and its limits. **No value
in this file was chosen to make the answer positive.** The answer is "no", so
the only way a chosen value could have helped is by being chosen *against*
that conclusion, and none was.

## Labelling

| label | applies to everything here |
|---|---|
| **HISTORICAL REPLAY / SHADOW ARITHMETIC** | yes |
| submitted / filled / settled / **realized** | **no** |

No network, no venue write, no order path, no credentials. Nothing in this
workstream is realized profit. The one credited PMUS fill (A6) is **FILLED,
settlement UNRESOLVED, NOT REALIZED.**

## A. Taken as given from source — not re-derived

| value | source | note |
|---|---|---|
| `maker_theta = 0.0125` | `src/pmus_fee_schedule.py`, fp `ecc240bd…1629` | unchanged across both schedule entries |
| `taker_theta = 0.0695` eff. 2026-09-17 | same | NOT the stale 0.06 |
| half-even cent rounding | same | **called**, never reimplemented |
| 99-row published price table | `analysis/pmus/pmus_published_fee_schedule.json` | `lot = 100` |
| matched lock `+$0.07496`/contract | `STAGE6_HISTORICAL_VALIDATION.md` §5, n=16,549 | Lane 4 gate, forward fees |
| residual `−$0.27742`/contract | same, n=7,148 | loss rate **1.000** |
| matched fraction `67.72%` CI `[65.62%, 69.81%]` | same | **frozen-proxy subset only** |
| `95.58%` UNKNOWABLE | same | strict standard |
| L5 median pair cost `0.9668`, `46.2%` ≤ 0.96 | `post_august/POST_AUGUST_REPORT.md` | REGIME_2026-08-19_ON |
| L5 `85.2%` matched / `14.8%` residual | same | share of gross |
| L5 net bp `+41.4 → −30.4`, change `−71.7` | `regime_aug19/REGIME_SHIFT.md` | **bp of TURNOVER** |
| L5 R4 fees `−62.1` bp, incentives `+163.4` bp | same | |
| credited PMUS `$9.14 + $7.04 = $16.18` | PMUS account history | cash |

## B. Assumptions actually made, each with its direction of error

1. **Residual P&L scales linearly in contracts.** The observed
   `−$0.27742` is a per-1.00-share mean. Treating it as per-contract at
   C = 100 assumes no size-dependence in adverse selection.
   **Direction: OPTIMISTIC.** Larger clips are more adversely selected
   (`REGIME_SHIFT.md` §1.2 barbell), so the true large-size residual is
   probably worse. The answer is negative anyway.

2. **The matched lock and the residual loss are independent of pair cost
   `X`.** In deliverable 4 the lock is recomputed as `C·(1 − X)` from `X`
   directly, so the lock is *not* assumed; but the residual is held at the
   observed value, which was measured under the Lane 4 gate (`p₁ ≤ 0.60`,
   pair ≤ 0.96). **Applying it at `X` up to 1.00 is an extrapolation and is
   flagged as such** in the `residual_per_contract_used` field of every row.

3. **A residual leg still earns its first-leg maker rebate.** A maker fill
   is a maker fill whether or not the complement ever arrives.
   **Direction: OPTIMISTIC** (it credits the residual branch).

4. **`credited_liquidity_rewards_usd = $0.00` everywhere.** Not an
   oversight: the PMUS program census returns zero for all 239 of our slugs
   (`analysis/l5/incentives/BREAKEVEN_INCENTIVE.md`). The line is carried so
   its absence is visible rather than silently dropped. **A rebate or reward
   is not income until credited**, and none is credited against any state in
   this surface.

5. **No rebate cap is applied.** Because none exists — see §C.
   **Direction: OPTIMISTIC** by ~0.8 bp of pair turnover near p = 0.50.

6. **Both-legs-maker means both legs actually rest and fill passively.** The
   95.58% UNKNOWABLE result says we cannot demonstrate this. Every
   both-maker cell is therefore at best PLAUSIBLE-BUT-UNPROVEN.

Assumptions 1, 3 and 5 all push the answer *toward* positive. It is still
negative.

## C. Two of the brief's own reference premises, corrected

### C1. The `$0.31 per 100 contracts` "cap" does not exist — REFUTED

The capture carries `lot = 100`. The published table is
`theta·C·p·(1−p)` evaluated at **C = 100** and cent-rounded. `$0.31` appears
for every price from **0.43 to 0.57** because that is where the rounded
formula sits — not because anything truncates. **All 99 rows reproduce
exactly** from the two coefficients (`deviations_from_formula == []`), and
the word "cap" appears nowhere in the capture.

Consequence: the rebate is **UNCAPPED**. At p = 0.50 the per-contract credit
is **$0.003125**, not $0.0031; at 10,000 contracts it pays **$31.25**, not
$31.00.

The brief is **right** that the formula gives `0.003125` at p = 0.50 and
`0.003000` at p = 0.40/0.60. It is **wrong** that a cap binds there, because
there is no cap to bind. This correction is *favourable* to the strategy and
is applied anyway, which is the direction that matters for honesty.

### C2. `−71.7 bp` is a CHANGE, not a LEVEL

`REGIME_SHIFT.md`: L5's trading net moved **+41.4 → −30.4 bp** of turnover.
`−71.7 bp` is that **move**. The level a clone must cover is **−30.4 bp**.
Comparing a rebate *rate* against the *delta* overstates the hurdle by
**2.36×**. See `PMUS_BREAKEVEN_SURFACE.md` §2 for why the comparison fails
on a second, independent ground as well.

## D. What the brief's reference numbers got exactly right

Confirmed by `tests/test_pmus_breakeven_surface.py`, all 37 passing:

* **The entire cent-floor table**, all nine prices, to the tick —
  4.45 / 2.51 / 1.91 / 1.67 / **1.61** / 1.67 / 1.91 / 2.51 / 4.45. The
  exact-half cases (p = 0.20, 0.50, 0.80) do need the extra tick, because
  half-even sends an exact `$0.005` to `$0.00`.
* **The asymptotic rate** `125·(1−p)` bp on maker notional: 112.5 / 62.5 /
  12.5 bp at p = 0.10 / 0.50 / 0.90.
* **The par-pair arithmetic**: a pair at `X = 1.00`, `p = 0.50`, both legs
  maker, earns `$0.00625` against a **zero** lock — **62.5 bp of pair
  turnover, rebate-financed**. Arithmetic confirmed; the causal claim about
  L5's 0.9668 median is **not** (see §3 of the report).

## E. What would change the answer, and what would not

**Would not:** loosening the `$0.96` pair ceiling; removing S6P's
taker-on-both-legs conservatism; crediting the full both-legs-maker rebate;
trading larger size. All were tested. None produces a positive cell.

**Would:** a demonstrated matched-completion rate above **~79.6%** on
all-maker fills (vs. observed 67.72%, CI to 69.81%), **or** a residual loss
materially below the observed `−$0.27742` at a loss rate materially below
`1.000`, **or** a source-confirmed liquidity-reward entitlement of the order
of L5's `+163.4 bp`. The first two are measurements we do not hold; the
third is an entitlement we have no source for.
