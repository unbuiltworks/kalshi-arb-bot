# What separates L5's economics from ours — **VERDICT: VENUE BLOCKER**

PMUS simultaneous two-sided maker policy retired from live validation.
Executor, watchdog, reconciliation and risk controls preserved. No new
service, no live campaign, no threshold search.

---

## 0. A correction I owe, and it is large

I told you earlier that **"L5 made $2,088/day on BTC15m"** and that **"$480/day
is 23% of L5's own BTC15m rate."** That came from
`data/l5/l5_btc15m_realized_economics.json` (5,546 windows, mean +$62.30,
total +$345,526).

**That figure is superseded and I should not have cited it.** The canonical
accounting — `analysis/l5/fees/FEE_RECONSTRUCTION.md`, built from `usdcSize`
with a documented supersession rule — gives BTC15m **gross acquisition
$6,352,044 against redemption $6,396,030**, i.e. **+$43,986 gross** before
fees and **+$16,436 after** $27,550 of reconstructed fees. The two disagree by
**~8× on gross and ~21× after fees**, and the canonical doc explicitly
supersedes "any claim that costed fills at `price × size` rather than
`usdcSize`."

**Corrected: L5's trading-only net is ≈ $95/day across all four cohorts, not
$2,088/day. The $480/day target is roughly 5× L5's measured trading rate, not
23% of it.** My error overstated L5's trading economics by about 22×.

## 1. L5's realized economics, decomposed

| component | total | per day | bp of turnover |
|---|---|---|---|
| MAKER_REBATE + TAKER_REBATE + REWARD | **$71,551.74** | $559.00 | **94.05** |
| **trading cash** | **$15,707.45** | $122.71 | **20.6** |
| combined | $87,259.19 | $681.71 | 114.65 |

**Incentives are 82% of L5's total and 4.6× the trading leg.** Implied
turnover **$7.61M over 128 days ≈ $59,400/day**.

By cohort, fee-inclusive:

| cohort | fills | fee-bearing | gross acquisition | fees | fee bp | **trading net** |
|---|---|---|---|---|---|---|
| **BTC15m** | 693,916 | **15.4%** | $6,352,044 | $27,550 | **43** | **+$16,436** |
| BTC5m | 51,808 | 100% | $1,023,096 | $22,187 | 217 | **−$1,700** |
| ETH5m | 11,008 | 100% | $130,687 | $2,843 | 218 | +$1,225 |
| ETH15m | 4,290 | 100% | $48,713 | $957 | 196 | **−$253** |

And by month, **August — L5's largest at $4.59M gross — was −$537.** Even
L5's trading leg is not reliably positive at scale.

## 2. The one thing that transfers, and the one that does not

| | L5 | us, venue-confirmed | ratio |
|---|---|---|---|
| trading margin | **20.6 bp** | −0.0755 on $21.41 gross = −35 bp | — |
| incentive per fill | **$0.098** | **$0.0126** confirmed (earlier probe) | **7.8× lower** |
| incentives credited | $71,551.74 | **$0.00**, every campaign | — |
| incentive eligibility | — | **`UNVERIFIED_NO_VENUE_CREDIT_RECORD`** | — |

**The 82% of L5's economics that is incentives has never been credited to this
account — not estimated low, credited zero.** That is the blocker, and it is a
venue-access fact, not a policy defect.

## 3. The target against the transferable leg

Two independent routes, both already derived in the repo and both reproduced
by this campaign:

**At L5's own trading margin (20.6 bp):** $480/day needs **$2.33M/day of
turnover** — about **39× L5's own daily turnover** of $59,400. Our 7-window
campaign transacted **$21.41 gross in two hours**.

**At our own measured matched margin (+$0.017315/contract):** $480/day needs
**27,700 matched contracts/day** = **289 per 15-minute window** across 96
windows, against the **2.16 per window** this campaign achieved — a **134×
rate increase, with no help from size.**

**$480/day is not reachable from the trading leg at any margin either L5 or we
have measured.**

## 4. Observable · inferred · unavailable

**Observable (chain/venue records):**
- L5 **never sells** — 66,949 BTC15m trades, every one a buy; positions only redeem at settlement
- pair cost **emerges** 0.9968 → 0.9603 across a window; median *entry* pair is **0.9900** and only **27.9%** of his entries clear our 0.96 ceiling
- he scaled **87× in capital with 1.40× median fill size** — frequency and breadth, not size
- EXTEND fires **62.9–69.6% at every gain level** — a default, not a signal
- 33.5 windows/day, ~100 fills/window in his accumulating regime

**Inferred (model, labelled as such):**
- that the accumulation is *intended* as averaging-down rather than an artifact of continuous quoting. The trajectory is observable; the intent is not.
- that his switches are economically triggered — `pair_cost` AUC 0.6264 is the only feature that separates, and it loses to the majority baseline on accuracy

**Unavailable — and this is the decisive category:**
- **maker/taker aggressor flag: the public feed carries none.** We cannot tell whether L5's 20.6 bp came as maker or taker, so his adverse-selection exposure is unmeasurable from our data and ours (0.89) cannot be compared to his.
- **his incentive terms.** $0.098/fill is observed as *paid*; the programme, tier and eligibility that produced it are not public and are not available to this account.
- **his queue position.** Ours is `queue_ahead_multiple_of_need` **73×–537×**; his is unobservable.

## 5. Another venue?

**Within L5's own four cohorts, BTC15m is already the best**: 15.4% fee-bearing
at 43 bp against 100% at 196–218 bp for the 5m and ETH series, and it is the
only cohort with a materially positive trading leg. Moving series inside this
venue makes the fee regime worse, not better.

**Gemini is unresolved, not better.** Its own status file: *"the
faithful-transfer question is UNRESOLVED… no prospective contract has yet
produced a fill. Nothing here claims Gemini works, and nothing here claims
Gemini failed."* It carries an **open P0 venue-semantics mismatch** (signed
scalar position vs our `Inventory(yes_qty, no_qty)`), already triggered live,
and the deciding read-only test is **built but blocked on credentials**. It is
disarmed with no authorization in force.

So there is no identified venue with materially better *demonstrated*
executable economics. Gemini is the only untested candidate and its blocker is
a credential/permission, not a strategy question.

## 6. VERDICT: **VENUE BLOCKER**

**Quantified:** 82% of L5's economics is an incentive stream paying $0.098/fill
that this account has been credited **$0.00** of across every campaign, with
eligibility reading `UNVERIFIED_NO_VENUE_CREDIT_RECORD`. The remaining 18% —
the transferable trading leg — runs at **20.6 bp** for L5 and would require
**$2.33M/day of turnover** to reach $480/day, against our realized **$21.41
per 2-hour campaign**. Our own matched margin implies a **134×** rate increase.

**This is not a defect in the engine.** The engine executed 7 windows with
zero rejected orders, zero partial fills, zero unacknowledged submissions,
maker-only throughout, every residual inside the 3-contract cap, and
venue-confirmed closure at zero open orders. It did what it was told,
correctly, and the economics are not there.

**The single actionable step, and it is not a trading change:** establish
whether this account can be credited PMUS maker incentives at all, and on what
terms. That is a venue/account question answerable by reading — a credit
record, a programme term sheet, or a venue response — not by another campaign.
**If the answer is no, the L5 replication programme has no path to $480/day on
PMUS at any policy, and that should be stated rather than re-tested.**

Until that is resolved I recommend **no further live validation**, and
specifically not another threshold search, quote-depth variation, or any plan
whose arithmetic requires a rebate we have never been paid.
