# Multi-cycle throughput, A/B/C, and incentive reconciliation

## Workstream A — why the engine stops after one pair

**It is not the round cap. Every plan in the campaign was sized to consume the
entire $1.65 cash envelope, and matched inventory does not release that cash
until venue settlement, which lands after the window has closed.**

Every `two_sided` plan the eight-window campaign produced:

| window | qty/leg | gross cash | **cash-gate obligation** | sized_by | round | max rounds | budget headroom |
|---|---|---|---|---|---|---|---|
| 0100z | 0.98 | 0.9310 | **1.6366** | CASH_ENVELOPE | 0 | 8 | 20.74 |
| 0100z | 1.04 | 0.9880 | **1.6432** | CASH_ENVELOPE | 1 | 8 | 20.74 |
| 0115z | 0.96 | 0.9120 | **1.6416** | CASH_ENVELOPE | 0 | 8 | 20.78 |
| 0130z | 1.27 | 1.2065 | **1.6383** | CASH_ENVELOPE | 0 | 8 | 20.55 |
| 0145z | 1.03 | 0.9785 | **1.6480** | CASH_ENVELOPE | 0 | 8 | 20.74 |
| 0200z | 1.22 | 1.1590 | **1.6470** | CASH_ENVELOPE | 0 | 8 | 20.38 |

Obligation runs **1.6366–1.6480 against the $1.65 envelope — 99.2% to 99.9%
consumed by round 0 alone**, and `sized_by` is `CASH_ENVELOPE` in every case.

The three candidate constraints and what they actually did:

* **`acc_max_rounds` — not binding.** Default is **8** (`ACC_MAX_ROUNDS_DEFAULT`),
  hard cap 64, and `accumulation_max_rounds: 8` appears in every plan. Its own
  docstring says accumulation "stops on whichever of those binds first, usually
  well before this." It did.
* **Campaign worst-case budget — not binding.** `budget_headroom_usd` stayed
  between 20.38 and 20.78 of the $29.30 budget throughout.
* **Cash envelope — binding, every time.**

**Is cash locked until settlement? Yes, and the lock outlives the window.**
`cash_policy: NO_RECYCLING`, and the gate's basis is "filled (unreleased) +
still-fillable outstanding + the proposed order, ACCOUNT-WIDE". Matching does
not release exposure — only settlement does, and settlement was observed at
roughly **T+16 minutes**, i.e. after the 15-minute window had already closed
(0100z filled 01:04, settled 01:16:36; 0130z filled 01:33, settled 01:46:23).
So within one window the envelope never returns. A second cycle was
arithmetically impossible, not policy-refused.

### The smallest change that permits a second cycle

**Reserve a FRACTION of the envelope per round instead of all of it.** No change
to the round machinery (already 8), no capital increase, no new gate: one
configurable divisor applied where the planner sizes against
`cash_envelope`, so round 0 reserves `envelope / N` and leaves room for N−1
more.

**Quantified expectation, N=2.** Observed pair surplus is **+0.0865/contract**
over 4.69 matched contracts. Halving the clip halves contracts per cycle and
doubles the cycles, so **expected pair surplus is unchanged** — this does not
manufacture edge. What changes is risk: a residual event at half size
contributes a quarter of the variance, and there are two of them, so total
residual variance falls to roughly **half** of the single-full-size case
(independent events). Against that, two cycles mean two chances to strand a
leg, and smaller clips have unmeasured queue behaviour.

So the honest claim is **same expected pair profit, roughly half the residual
variance, at no extra capital** — conditional on both cycles finding a
completable pair, which 4 of 7 windows managed even once.

## Workstream C / item 1 — A/B/C on existing data, and its hard limit

**The frozen holdout cannot evaluate these policies, and I will not pretend it
can.** `dwell_validation`'s corpus is L5's Polymarket window tape: it has no
book, no queue, no resting orders and no cancellations — L5 had **zero sells
in 693,916 fills**. A/B/C is a question about *our* resting orders' lifecycle,
and that corpus contains none.

The only data containing our resting orders with queue-ahead, drift and
venue-confirmed cancellations is our own live record: **three recovery
events**. What they support:

| policy | evidence available | verdict |
|---|---|---|
| **A** current recovery, both roles | 3 firings: `rest_s` 16.62, 16.79, 44.13 | baseline |
| **B** first-leg recovery kept, complement left resting | 2 complement cancels, **both provably unfillable** by post-cancel bid path (0.70 vs min bid 0.73; 0.61 vs min 0.65); 1 first-leg cancel → replacement → **full fill** → the campaign's first pair | **directionally supportive, statistically empty** |
| **C** longer minimum rest | longest fill 73.4 s exceeds every cancel; but all fill latencies are log-derived and carry 0.03–9.64 s ingestion error at each end | **unmeasurable; the 30 s candidate is a dial, not a fix** |

B's mechanism is sound — after a complement is cancelled, the replacement must
still clear the 0.96 pair cap against a moved book, which was impossible in 2
of 2 cases, so the cancel spends the per-market budget for nothing. But the
same two cancels cost nothing either, because neither order could have filled.
**n=3 supports no economic verdict**, and the per-order history at scale lives
in `/data/l5-live-20261012z/decisions.jsonl`, which is authoritative, already
written, and not reachable from outside the container.

## Workstream B / item 3 — rebate estimates, strictly unrealized

Every one of the campaign's 11 live orders carries an estimate and **no
actual**:

| | |
|---|---|
| orders with a maker-rebate estimate | **11** |
| sum of estimates | **$0.0337** |
| orders with `venue_actual_status: PENDING` | **11** |
| orders with a venue ACTUAL rebate | **0** |
| campaign realized net (settlement only) | **+$0.6581** |
| estimate as a share of realized | **5.1%** |

**Nothing has ever been credited, and nothing has been booked.** The estimate
and the actual are separate columns that are never summed, and no estimate is
promoted. `$0.0337` is not profit; it is the size of a claim we have not
verified exists. Per-order estimates ran $0.0022–$0.0039 — on this clip size
the entire incentive question is worth a third of one cent per order, which
bounds how much engineering it deserves until eligibility is confirmed.

The local model computes `0.0125 · q · p · (1−p)` with the schedule validated
in `tests/test_pmus_fees.py` ("all 99 published price rows reproduce
exactly"). **That the model computes a rebate says nothing about
collectability.** Eligibility, quote-size floors, uptime, volume tiers and
payment mechanics are all terms of programs this container cannot read:
`docs.polymarket.us`, `polymarket.us` and `www.polymarket.us` all return
`000` under the network policy.

## Item 4 — the exact records needed to reconcile actual credits

Nothing below can be inferred from execution or settlement data; each is an
account-level record:

1. **The current fee schedule as published**, with its effective date, to
   confirm maker rebate θ and whether BTC15m is in scope.
2. **The four notices** — Oct 2 maker-rebate amendment (effective Oct 19),
   Oct 8 sequential incentive amendment, Sep 30 series-based market-maker
   program, Oct 12 fee notification — for eligibility conditions, minimum
   quote size, two-sided presence requirements, uptime windows, volume tiers
   and the payment cadence.
3. **Account enrollment status** in any market-maker or liquidity program:
   enrolled or not, since when, and for which series.
4. **An account statement or ledger of credits** covering 2026-10-23/24, with
   per-fill or per-day rebate lines, so each credit can be matched to a
   `client_order_id` or venue order id.
5. **The payment basis** — per fill at trade time, or accrued and paid on a
   cycle — which decides whether a missing credit is late or absent.
6. **Prior-month taker notional**, since our own provenance records
   `taker_rebate_available_to_this_account: false` on the basis that tiers
   start at $250,000 and this account is below the first tier.

With (1)-(5) the reconciliation is mechanical: for each of the 11 orders,
match estimate against credit, and promote only credited amounts into a
separate rebate line that is never summed into `settlement_pnl`.

## Item 5 — bounded validation proposal, nothing deployed

**Change:** one configurable per-round cash divisor, default **1** so
behaviour is byte-identical until set. No round-cap change, no gate change, no
capital change, no new service.

**Pre-registered measures**, declared before any run: completed pairs per
window; realized pair surplus per window; residual events per window and their
settled P&L; cash utilisation per round; and refusal reasons when a second
round is declined.

**Acceptance:** a second completed pair in at least some windows **without**
an increase in total residual settlement loss per window. Not fill count, and
not a rebate estimate.

**Authorization this would need, stated exactly, and not requested yet:**
same $1.65 cash envelope, same $2 account-wide exposure, same $5 daily and $20
cumulative halts, same 0.96 pair cap, same maker-only execution, same one
market, same absolute 3-contract residual ceiling — with `max_live_orders`
still **1** and the divisor set to **2**, over a bounded window count with a
fresh nonce and explicit UTC expiry. That is strictly less capital at risk per
order than the completed campaign, and it is the only new parameter.

Nothing in this document is deployed. The expired campaign is not reactivated.
