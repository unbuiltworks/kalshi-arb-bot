# Proposal: bound the economics, keep the behaviour

Written after the R5F halt on `asc-mlb-chc-mil-2026-09-09-pos-2pt5`, 2026-09-10.
PROPOSAL ONLY. No code changes. Numbers are from the live orders we paid for,
in `analysis/l5/evidence/R5F_MIL_2PT5_2026-09-10/`.

Owner's plain-English requirement: *"Follow the market dynamically only while
completing improves the position economically. Don't spend $1.24 to manufacture
a $1.00 matched pair."*

---

## 0. First, an uncomfortable fact about that completion

The 0.880 fill was not obviously irrational. Write the position as `U` UP
contracts, `D` DN contracts, `S` dollars spent. Exactly one side pays $1:

```
P&L(UP wins) = U - S        P&L(DN wins) = D - S
```

Before the fill: U=20.34, D=12.43, S=18.3975 -> branches **+1.94 / -5.97**.
After  the fill: U=20.34, D=16.06, S=21.5919 -> branches **-1.25 / -5.53**.

Buying `q` of the deficient side at price `p` costs `qp` in the rich branch and
gains `q(1-p)` in the poor branch. At p=0.880 that is -0.88q / +0.12q, so the
FLOOR ROSE by 3.63 x 0.12 = **+$0.44**. The completion improved the worst case.
It destroyed the best case.

This matters because it means **"improves the position" is not self-evident**,
and a rule chosen carelessly will justify almost anything: maximising the floor
alone licenses completing at p = 0.99, since 0.01q > 0. That is precisely the
behaviour we just halted. The boundary has to be economic, not minimax.

---

## 1. Completion boundary: a par ceiling on the MARGINAL pair

**Rule.** A complement order may be priced at `p` only if

```
a_first_leg + p  <=  PAIR_CEILING
```

where `a_first_leg` is the running average cost of the side being paired
against. No order may be submitted or re-quoted above it. The re-quote follows
the touch **only inside** this ceiling; when the touch leaves it, we stop
following and the residual is held to settlement.

**What PAIR_CEILING should be, from credited cents and nothing else.** Total
maker rebate credited to date: **$0.42 over 119.49 contracts = $0.0035 per
contract**, ~0.8% of filled dollars, validated print-by-print against 25 credits
and 6 zeros. PMUS liquidity-program rewards credited: **$0.00**. So the
economically defensible ceiling is

```
PAIR_CEILING = 1.00 + 0.0035  ~=  1.003
```

Not 1.02. Not 1.03. Those numbers came from L5's incentive era, and PMUS has
credited us none of it. Recommend **1.00** for the next probe and treat 1.003 as
the absolute upper bound justified by measured cash.

Against the live case: `0.3612 + p <= 1.00` implies `p <= 0.6388`. The 0.880
fill would have been refused. Correct outcome.

**The cost of this rule, stated plainly.** It does not rescue a position that
has moved against us; it stops us paying to make the loss certain. The residual
stays directional and is held to settlement. On the live case the rule would
have left us at **+1.94 / -5.97** instead of **-1.25 / -5.53** -- a wider range
with a worse floor and a surviving winning branch.

**And it pulls directly against the turnover goal.** Un-completable residual
occupies the market, `residual_cap_units` then blocks new first legs, and the
lane stalls until settlement. A par ceiling buys economic safety with
throughput. That trade-off is the owner's to price, not mine to assume.

---

## 2. Split the limits, and make the displayed number the real one

Today `PER_MARKET_FIRST_LEG_CAP` bounds the first leg only, while
`worst_case_usd` reports `"7.50 per filled market"`. Live outcome: first leg
$7.34 of $7.50 (correct), complement $14.25 (unbounded), all-in **$21.59 =
2.88x the number we displayed**.

Three explicit limits, no implicit ones:

| limit | binds | live value would have been |
|---|---|---|
| `first_leg_cap_usd` | OPEN + EXTEND notional | $7.50 (held) |
| `completion_cap_usd` | complement notional | needs a number |
| `market_all_in_cap_usd` | total cash in one market, hard | needs a number |

`market_all_in_cap_usd` is a submit-time gate: refuse any order that would take
total market cash over it. And an import-time assertion, in the style of the
existing duplicate-rung-id check, that

```
worst_case_usd == market_all_in_cap_usd
```

so the reported worst case can never again drift from the enforced one. The
displayed number being wrong is what let this run for 40 minutes without anyone
noticing the shape of the risk.

**Not proposed: raising $7.50 to fit what happened.**

---

## 3. An unsettled worst-case stop, alongside the realized one

The -$15.00 daily stop reads `q1.realized_today()` -- **settled** realized. It
could not see this position at all, and never would have. A stop blind to
unsettled exposure did not protect us.

Add a companion computed from venue positions and ledger cost basis:

```
unsettled_worst_case = SUM over held markets of ( min(U, D) - S )
```

**This is not mark-to-market.** It uses no price and no book -- only contract
counts and cash actually paid. It is the arithmetic floor of what the position
can settle to, so it survives the "never infer P&L from mark" rule intact.

Live value at halt: `min(20.34, 16.06) - 21.5919 = -$5.53`, i.e. 37% of the
-$15.00 daily stop, on one market, invisible to every gate we had.

Behaviour: breach halts new first legs and completion, preserves held inventory
to settlement, and triggers the verified shutdown path (`PMUS_SPRING_SHUTDOWN`,
shipped as `f53aeb41`).

---

## 4. What is NOT proposed

- Raising the per-market cap.
- Changing the 0.25-0.75 band. It did its job: it created activity and the L5
  sequence fired.
- Changing concurrency.
- Removing COMPLETE re-quoting. It worked -- it saw the touch move 0.875 ->
  0.880 and that order filled. Keep it; bound it.
- Any taker behaviour, or the 1.00-1.03 rescue band. The rebate arithmetic in
  section 1 says the justified ceiling is ~1.003, which retires the 1.03 band
  on measured grounds rather than by preference.

## 5. Open question for the owner, needed before any of this is coded

Section 1 sets the ceiling at par and accepts stranded residual. Section 3
halts on unsettled worst case. Together they mean the lane will sometimes sit
on directional risk it cannot close, in a market it cannot leave, while
`residual_cap_units` blocks new first legs.

The alternative -- allowing completion above par to free the lane -- is exactly
what we just halted. There is no third option that is both economically bounded
and always able to flatten. Which one we want is a risk-appetite decision.
