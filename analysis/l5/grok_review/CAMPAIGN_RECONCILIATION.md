# Grok's L5 hypotheses against the PMUS three-window campaign

Read-only analysis. No strategy change is proposed here, and the narrow
completion repair shipped alongside it does not depend on any claim below.

Existing material reused rather than rebuilt: `ADJUDICATION.md` (this
directory), `analysis/gemini/P13_COMPLEMENT_FIDELITY.md`,
`analysis/l5/incentives/L5_INCENTIVE_CLASSIFICATION_V1.md`,
`analysis/gemini/audit/p16_grok_repro.py`, and the settled campaign tape.

## The claims, each against evidence already in this repository

**1. Persistent multi-level two-sided maker liquidity, not sequential
buy-one-side-then-the-other. — SUPPORTED, and already measured here.**
`ADJUDICATION.md` reports for BTC15m over 356 markets: **128 fills and 30
side switches at the median, active 88.6% of market life, 90.67% matched,
Up share 0.4844**, entering at a median **13 s** after open with **98.9%**
market coverage. That is a continuous two-sided maker, and our own
adjudication reached that conclusion independently of Grok. The 5m cohorts
(2-3 fills, 0 switches) are not the same business.

**2. 82.7% maker-to-maker, 95.5% maker complements, 2.64:1 pre-resting,
38.5% of complements filled before the first leg was visible. — NOT
VERIFIABLE, and the repository already says why.** `ADJUDICATION.md`
establishes that L5's public record carries no resting-order data at all:
incentive rows have empty `conditionId` and empty `slug`, and attribution is
impossible "beyond timestamp -> transaction -> amount". A pre-resting ratio
and a "filled before the first leg was visible" fraction both require order
placement times, which the public tape does not contain. Grok's own framing
concedes these are inferences. Treat the direction as a hypothesis and the
numbers as unusable as parameters.

**3. Extends one side, acquires the opposite, overshoots, recycles; 84.3% of
imbalance episodes crossed zero; a forced-flat pair-and-stop model does not
reproduce it. — SUPPORTED, and it is already the operating thesis.** Root
`CLAUDE.md` names same-side `EXTEND` a first-class action and forbids
forcing flat on a timer. The 30 median side switches in item 1 are the same
fact measured here. Our campaign is the counter-example: 21 orders across
three windows, **7 pairs, and at most one resting order at any instant.**

**4. September trading P&L approximately -$5,047.88 against incentives
+$20,490.13. — DIRECTIONALLY CONSISTENT WITH OUR OWN AUDIT, and this is the
material finding.** Our independent audit over 2026-04-21..08-31 found
confirmed **MAKER_REBATE $35,228.32, TAKER_REBATE $7,525.73, REWARD
$28,797.69 = $71,551.74**, against **trading-only +$15,707.45**. Incentives
are **94.05 bp of turnover and 82% of total economics, ~4.6x trading P&L**.
Grok's September has trading negative; ours has it positive but small. Both
say the same thing: **the trading leg is a thin residual and the incentive
leg is the business.**

**5. Median pair cost ~0.9665, 76.8% below $1.00. — PLAUSIBLE AND
COHORT-SPECIFIC; do not use as a parameter.** Our settled break-even is
0.9532 against an achieved quantity-weighted 0.9530, measured on our own
BTC15m cohort, and the definitions (own-terms vs wire, per-pair vs
quantity-weighted, fees in or out) are not stated for Grok's figure. Note
the direction it implies: a median of 0.9665 is **above** our 0.9532 target,
which is consistent with the repair shipped here. It is not evidence for
widening the 0.96 opening cap.

## The campaign against the thesis, especially 1445z

| | L5, BTC15m median | our 3 windows |
|---|---|---|
| fills per market | 128 | 5.0 |
| side switches per market | 30 | ~2 |
| active share of market life | 88.6% | partial |
| matched fraction | 0.9067 | 0.871 (13.73 of 15.76) |
| both sides resting at once | continuous | **never** |

Matched fraction is the one measure where we are close. Everything about
*rate* is one to two orders of magnitude short, which is what the root
instruction "favor increasing frequency before size" already anticipates.

## Does one resting order prevent L5-style two-sided management?

**Yes, structurally — and the engine already measures the consequence.** The
service heartbeat carries, for the live configuration,
`windows_with_opposite_side_want: 393` against
**`windows_with_both_sides_resting: 0`** over 923 settled windows. The
engine wanted the other side in 43% of windows and never once had both
sides working. A different parameterisation in the same heartbeat reaches
`windows_with_both_sides_resting: 482` of 541.

So the restriction is not a tuning detail; it converts two-sided making into
**reactive sequential completion**, which is precisely the shape that
stranded 1445z: first leg fills, then and only then is a complement sought,
by which time the complement must be bought at whatever the book has become.
L5 does not have this problem because his complement is already resting.

**The mechanism to fix it already exists and is disarmed.**
`l5_two_sided_probe.second_leg_slot_refusal` grants the second maker slot
only when every one of these holds: the flag `L5_TS_TWO_ORDER_PAIR` is on,
**exactly one** order is live, it is in **this** market, on the **opposite**
side, in the **same round**, and this side is not already claimed. Default
is off, and the docstring is explicit that returning None "grants no other
allowance": every cash, exposure, residual, per-market and loss gate still
prices both orders as if each filled in full.

That bounds the added risk precisely: a bounded pair, never a third order,
never a same-side extend using the slot. But it does raise resting orders to
two, and the approved campaign scope caps them at **one**. So it needs
separate authorization and is NOT enabled here.

## What of L5's profitability is transferable to PMUS?

| component | L5 | ours, settled |
|---|---|---|
| confirmed incentive per fill | **$0.098** | **$0.0000 credited** |
| incentives as share of economics | **82%** | **0%** |
| trading cash | 20.6 bp of turnover | +$0.2416 pair creation, -$0.0774 defensive, -$0.7067 stranded |

Our campaign estimated $0.0177 of rebate in 1445z and was **credited $0**.
The earlier probe measured $0.0126 confirmed per fill, **7.8x below** L5's
$0.098.

**So the 82% of L5's economics is not currently transferable at all, and the
$480/day target cannot be reached by reproducing his trading leg.** At 20.6
bp of turnover, $480/day of trading cash implies ~$2.3M of daily turnover.
Our three windows turned over about $14.27. The gap is not a threshold, a
side-selection rule or a residual cap; it is volume and credited incentive.

This does not argue against the completion repair. It sets the repair's
ceiling honestly: the repair stops a -$0.7067 leak and makes matched pairs
reachable at non-negative cost. It does not create an incentive stream.

## Immediate repair vs long-term replication

**Immediate (shipped, disarmed):** the completion gate no longer refuses a
non-negative pair; completion depth is separated from opening depth; the
granted attempt reaches the client order id so a cancelled complement is
re-placed instead of abandoned; route attribution follows inventory origin.
All of it is about not stranding inventory we already hold. None of it
changes what we open, how much, or how often.

**Long-term (not started, needs authorization):** simultaneous two-sided
resting quotes via the existing second-slot mechanism, then frequency, then
size, then concurrency — the order root `CLAUDE.md` already fixes. And,
separately and more importantly, establishing whether any PMUS maker
incentive is actually creditable to this account, since that is 82% of what
makes L5's book work.

**The honest sequencing:** the repair is worth shipping because a stranded
leg costs 28x per unit what a defensive completion costs. Two-sided quoting
is the next structural change. Neither reaches $480/day, and no arrangement
of them does without credited incentives or orders of magnitude more volume.
