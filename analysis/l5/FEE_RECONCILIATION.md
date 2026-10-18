# Fee reconciliation: the rate is identified, and it inverts my own finding

READ-ONLY. No order, service, deployment, limit or latch touched. Sources:
the live `L5_COST_BASIS_AUDIT` emitted by deployment `b356e35a` (walk:
`107 pages, ended eof, complete true`), `analysis/l5/venue_rebate_credits.json`,
and `data/l5_master/btc15m_sequences.jsonl`.

## 1. Account-wide census -- 1,911 venue fill rows

```
commission_signs: negative_a_credited_rebate 831
                  zero                       975
                  positive_a_charge          105
                  unreadable                   0
commission_sum_usd: -5.8800     <- the account is NET CREDITED
rebates_ever_credited: true
our_side_selectable_via_isAggressor: 1911
```

**94.5% of our fills paid nothing or were credited.** This CORRECTS
`FEE_QUANTUM_FINDINGS.md` section 0, which reported "1.401% of cost, 4.8x
L5's entire edge" and read it as the account's fee load. Those 47 legs are
the cost-basis REPAIR population -- selected because their basis was
unknown -- and they are not representative. The error was mine.

## 2. But the crypto 15m lane does pay

The 47 established legs: notional **$50.6148**, fees **$1.1400** =
**2.2523%**. By sign: **38 charged**, 6 zero, 3 credited. Against 105
charged out of 1,911 account-wide, this lane is where the fees are. The
September PMUS sports ladder is the opposite: all 25 passive executions in
`venue_rebate_credits.json` carry `our_side: passiveExecution` and a
NEGATIVE commission, -$0.42 credited over 119.49 shares.

## 3. The rate and the rounding, IDENTIFIED

Restricting to the 28 fee-bearing legs the audit confirms are SINGLE-FILL,
using the venue's own quantity and average price:

| rounding of `0.0695 * q * p * (1-p)` | exact matches |
|---|---|
| **round-half-up (nearest)** | **28 / 28** |
| ceiling | 17 / 28 |
| floor | 11 / 28 |

No exceptions. So the fee is the **taker** Theta 0.0695 rounded to the
**NEAREST** cent. Two consequences:

- every fee-bearing crypto leg was **TAKER**-priced;
- the repo's `fee_worst` rounds UP, overstating by up to a cent per leg --
  safe for a risk bound, wrong for expectancy. `l5_fee_quantum_gate` now
  defaults to the identified rounding and keeps ceiling as an explicit
  worst case.

Worked example: `cpc-btc-updown-15m-2026-10-15-2230z|DOWN`, qty 1.27 at
0.7700 in one fill, charged $0.0200;
`0.0695 * 1.27 * 0.77 * 0.23 = 0.015626 -> 0.02`.

## 4. Classification, with maker NEVER inferred from a low fee

| class | n (single-fill legs) | basis |
|---|---|---|
| TAKER | 28 | exact formula match, 28/28 |
| ZERO | 4 | charged nothing |
| REBATE (credit) | 2 | negative commission |

The 4 zero and 2 credited legs are **not** rounding artifacts: their raw
fees are $0.0057-$0.0174, which round to a cent, and nothing was charged.
That is genuine maker treatment.

**UNKNOWN remains unknown.** The venue states our side per fill as
`isAggressor`, and `l5_cost_basis_audit._exec_objects` already uses it
(`true` -> aggressorExecution, `false` -> passiveExecution, absent ->
nothing, "never a coin flip"). The audit reports
`our_side_selectable_via_isAggressor: 1911`, so the authoritative
classification is available for every fill -- but it is NOT yet
cross-tabulated against commission. My TAKER label above is a formula
identification, not the venue's own flag. That join is section 8.

## 5. L5's fees: his zero rows are NOT evidence he paid zero

His feed carries no fee row of any kind. Three readings, and the data does
not separate them:

1. genuinely fee-free;
2. **fees embedded in the executed price** -- in which case his
   `redeem - cost` net is already net-of-fees;
3. the feed simply omits fee rows -- in which case his true net is **lower**
   than measured.

Under (3) the measured **+0.2935% of cost is an UPPER BOUND on his net.**
It is never treated here as proof of a zero-fee venue.

## 6. Like-for-like against L5's sequential decisions

Decomposed on the calibrated corpus (cost $6,376,853.08):

| line | L5 | ours at pair 0.96 |
|---|---|---|
| matched-pair economics | **+2.5563%** of cost | **+4.00%** gross |
| fee | none recorded (see 5) | **-3.3013%** TAKER |
| residual / adverse selection | **-2.2773%** of cost | not yet measured |
| **net** | **+0.2935%** (upper bound) | **+0.70pp** pre-residual |

**Our pair SELECTION beats his** (+4.00% gross vs his +2.5563% matched,
because we refuse the >= $1 pairs he accepts). **The taker fee is the
entire gap**: it consumes 83% of our gross and leaves us below his matched
line. And his residual drag of -2.2773% of cost, if ours resembles it,
takes the taker configuration to **-1.49pp** -- negative.

## 7. Executable net expectancy, and the capital for $5/window

Gross and fee are measured; the residual column applies L5's own
-2.2773% as the only available estimate of adverse selection. Partial
fills and queue position are NOT priced here -- see the caveat below.

| config | fee regime | adverse sel. | NET | pair cost/window for $5 |
|---|---|---|---|---|
| pair 0.97 | TAKER (identified) | no | **-0.29pp** | impossible |
| pair 0.97 | TAKER | yes | -2.50pp | impossible |
| pair 0.96 | TAKER (identified) | no | +0.70pp | $715.56 |
| pair 0.96 | TAKER | yes | **-1.49pp** | **impossible** |
| pair 0.96 | MAKER zero-fee | no | +4.00pp | $125.00 |
| pair 0.96 | MAKER zero-fee | yes | **+1.81pp** | **$275.67** |
| pair 0.96 | MAKER + observed rebate (1.2165%) | no | +5.17pp | $96.75 |
| pair 0.96 | MAKER + rebate | yes | **+2.98pp** | **$167.69** |

Account available **$344.41255**; authorized cap **$4.65/window**.

**QUEUE AND PARTIAL FILLS ARE NOT IN THESE NUMBERS AND THEY DOMINATE.** The
shadow record is 3 windows, all `outcome BOTH`, with `queue_ahead` of
511-3419 shares against a 1.25-1.43 share clip. A fill rate cannot be
estimated from n=3, and section 90.4 of L5_CANONICAL already names
decision-level queue depth as the missing measurement. A maker-only
configuration earns the maker column ONLY to the extent it actually fills.

## 8. Verdict, and the single next action

**Is there a defensible path to positive realized net?** Conditionally yes,
and the condition is now precise and narrow: **maker-only acquisition at
pair cost <= 0.96.** As a taker the configuration is negative once L5's own
adverse-selection drag is charged, at every size and every capital level --
no amount of money fixes a negative margin. Maker treatment is PROVEN on
this account (831 credited and 975 zero-fee fills; 6 of 47 crypto legs),
so this is not a hypothesis about the venue, only about our fill rate.

**Capital for $5/window**, maker-only at 0.96, charging L5's residual drag:
**$167.69-$275.67 of pair cost per window**. That is inside the $344.41
account but is 49-80% of it deployed EVERY window, which `NO_RECYCLING`
forbids -- so the cash policy, not the balance, is the binding constraint
at that target. Against the authorized $4.65 cap the ceiling is
**$0.0338-0.1386/window**.

**SINGLE NEXT ACTION.** Add one read-only cross-tab to the existing
`fee_and_notional_evidence` in `src/l5_cost_basis_audit.py`: commission
sign and magnitude, and notional, **broken down by the venue's own
`isAggressor`**, per lane. `_exec_objects` already selects our execution
with that flag and the audit already walks all 1,911 rows, so this is a
reporting change to code that runs on every boot -- no new service, no
deployment of its own, no order. It returns the one number this whole
verdict hangs on: **our realized MAKER share and maker rate in the BTC15m
lane.** Everything above is a sensitivity analysis around it.
