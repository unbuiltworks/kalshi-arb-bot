# L5 replication — research plan, 2026-10-10

Question: **what observable conditions cause L5 to place passive orders,
acquire the opposite side, increase directional inventory, and then reduce
that exposure?**

No new pipeline. Everything below runs on data and scripts already in this
repository. Branch state established first: research head
`claude/august-l5-forensic-replay-788cml` @ `e02073f0a`; canonical docs
`L5_OPERATING_MODEL.md`, `L5_STATUS.md`, `L5_STRATEGY_FINDINGS.md`,
`analysis/gemini/GEMINI_L5_CURRENT.md` all present; fill-acquisition
modules and their committed JSON outputs present under
`analysis/l5/historical_l2/`.

---

## 1. What the existing evidence already answers

Base: 33 backfilled BTC15m windows, 3,166 L5 chain fill legs joined to the
Zenodo 1-second L2 archive by CTF token id, timed by **real block
timestamps, never interpolated**.

### 1.1 Deep passive orders reached by price movement — ANSWERED

`resting_trajectory.json`, 2,795 maker fills, distance = own best bid minus
fill price, in ticks:

| | T-30 | T-0 |
|---|---|---|
| p50 | 2 | 0 |
| p90 | 17 | 4 |
| spread | 1 tick | 1 tick |

Monotone convergence, and **64.4% of fills are at or below L5's own best
bid 30 seconds earlier**. The price came to the order. Post-fill mid moves
−0.005 at T+15 and −0.01 at T+30: he is adversely selected at the margin,
by about a cent half the time.

### 1.2 Opposite-side acquisition by repeated trading — ANSWERED

`complement_mechanism.json`, 37,436.6 matched shares:

| mechanism | share of matched | time-to-complement p50 |
|---|---|---|
| REACHED_RESTING | 57.0% | 27s |
| REACHED_MINT | 24.7% | 24s |
| CROSSED_PAR_PAIR | 17.0% | 20s |
| CROSSED_SINGLE | 0.9% | 35s |

**Reached 81.7% / crossed 17.9%.** He waits for the complement far more
often than he pays for it. Median **245 fills per market where he traded**,
and he traded **12 of 33 windows** — so the behaviour is bursty, not
continuous.

### 1.3 Complete-set minting — ANSWERED, and it is the hard constraint

`mint_contribution.json`, purchases by channel:

| channel | share | avg price | on PMUS? |
|---|---|---|---|
| maker at/below bid (RESTING_BID) | 57.9% | 0.4829 | yes |
| mint-feasible (MINT_FEASIBLE) | 24.1% | 0.4969 | **no** |
| atomic par-pair taker (ATOMIC_PAR_PAIR) | 16.4% | 0.5027 | **no** |
| taker one-sided | 0.6% | 0.7311 | yes |
| **at the ask with no mint feasible** | **0 shares** | — | — |

Minting is **confirmed, not inferred**: 145 of 2,872 transactions carry L5
on both outcomes atomically, and the pair-price histogram is `{1.00: 145}` —
every one at exactly a dollar, which is the CTF complete-set signature.
PMUS has no mint/split/merge/redeem endpoint in the captured 31-endpoint
spec; its only multi-leg primitive requires *distinct* underlyings.

**The sharpest behavioural fact in the corpus: `AT_ASK_NO_MINT = 0 shares`.
L5 never pays the ask unless a mint is feasible.** Apparent aggression is
not aggression; it is minting.

### 1.4 Maker/taker economics and residuals — ANSWERED in aggregate

From `analysis/l5/L5_ECONOMIC_DECISION_20261009.md`:

* own-fill markout late t+30s **−0.00381**/contract vs all-in incentive
  rate **+0.004554** → net **+0.00074**. The rebate is not the edge; the
  low-toxicity book is (Gemini is ~62x worse).
* pair cost below $1.00 on **63.2%** of late two-sided events (77.9%
  early); median late 0.9702, early 0.9581.
* trading cashflow ex incentives, whole corpus: **+$18,446** on 760,815
  buys. **August alone: −$537 on $4.61M notional.** Incentives were
  $71,552 lifetime, $61,022 of it in August.
* **the directional residual destroys ~74% of the gross matched-pair
  edge**: $550/day gross early → ~$144/day realized ex incentives.

**So the $480/day target is a residual-control problem, not an
edge-existence problem** — and it is explicitly *not* reachable by scaling
frequency: $/event decayed 8.48 → 4.27 → 4.27 → 2.03 → −0.17 while
frequency scaled ~120x.

---

## 2. What is genuinely missing

### 2.1 Unobtainable, and should stop being treated as a gap

**L5's orders.** Placements, cancels, sizes and queue positions are in no
archive. The chain gives fills; Zenodo gives 1-second aggregate L2. Every
"resting" figure above is a distance between two public numbers, correctly
labelled `OBSERVED_FILLS_NOT_L5_ORDERS`. No test can recover his order
book, so the placement rule can only ever be inferred from fills and from
the *absence* of fills.

### 2.2 The highest-value missing evidence

**Does the mint-free subset have positive full-inventory expectancy?**

Every economic figure we hold mixes all four channels. ~40.5% of his
acquisition uses a primitive PMUS lacks. So the replicable strategy is the
**RESTING_BID channel alone — 57.9% of purchases, 54.4% of matched
shares** — and we have never priced that subset on its own, with residual
carried to actual settlement.

This is the decisive question for the whole program. If the mint-free
subset is negative at full inventory, no amount of execution engineering
makes PMUS replication work, and the correct answer is to stop. If it is
positive, residual control becomes the single engineering target.

### 2.3 Second-highest: the negative class

We have 21 windows in which L5 placed nothing and 12 in which he traded.
**That is the only negative evidence that exists**, and it has never been
used. Without it, every "condition" we cite is measured only on windows he
chose to trade — pure selection.

---

## 3. The smallest tests that resolve them

### TEST A — channel-conditioned full-inventory expectancy (highest value)

Reuse `mint_contribution.py`'s channel labels, which already tag every
purchased lot. For RESTING_BID lots only, per window, action by action:

1. matched-pair economics at the two confirmed fill VWAPs;
2. residual carried to **actual settlement**, not marked;
3. fees from the real schedule;
4. rebates/rewards computed but reported **separately and never netted**.

Output: $/window and $/day for the mint-free subset, with and without
incentives, plus the residual share of gross edge *within that subset*.

Cost: one script over existing JSON + the activity corpus. No new data.
Decision rule, preregistered: if mint-free full-inventory expectancy ex
incentives is **≤ 0**, PMUS L5 replication is NO-GO on economics and the
program stops regardless of execution readiness.

### TEST B — placement-condition discrimination against the negative class

Positive class: the seconds in the 12 traded windows where a maker fill
followed within 30s. Negative class: matched seconds from the 21 untraded
windows **and** idle seconds inside traded windows.

Features, all from the existing L2 snapshots at T-30: spread in ticks,
depth at and behind the touch, own-bid distance, recent print intensity,
time-to-settlement, pair cost available on both books.

Output: which features separate place from idle, reported as separation and
base rates — **not** as a fitted model. A feature that does not separate
against the negative class is removed from the behavioural thesis.

### TEST C — action-by-action comparison, not aggregate P&L

For the windows in Test A, replay our candidate policy and emit a
per-action table against L5's own next action: side, price, size, timing,
inventory state before and after, pair economics, residual. Score
divergence at the **first** differing action, not on total P&L. Positive
aggregate P&L with different behaviour is not replication.

The harness exists: `src/pmus_l5_pair_policy_study.py` (`replay_placement`,
P7–P10 continuous quoting, queue-cleared fill discipline).

---

## 4. Order of work, and what would stop it

1. **TEST A.** It can end the program, so it runs first and nothing else
   is built until it reports.
2. **TEST B**, only if A is positive — the placement rule is worth
   identifying only if the economics survive.
3. **TEST C**, only if B produces conditions that separate.

Constraints honoured throughout: no new strategy workstream, no live
trading, no infrastructure rebuild, no deployment. Rebates stay separately
labelled and are never used to make a trade look profitable. Labels stay
strict: historical replay → shadow → live probe → submitted → filled →
settled → realized.

## 5. The honest prior

L5's own trading, ex incentives, made **+$18,446 across 760,815 buys**, and
in August — 56% of his lifetime notional — **lost $537**. The $480/day came
from $61,022 of August incentives on a venue whose adverse selection is
~62x better than the alternatives we have tested, using a minting primitive
PMUS does not offer, for about 40% of acquisition.

Test A is therefore likely to come back thin or negative. It should still
be run, because it is cheap, it is decisive, and the alternative is
continuing to engineer execution for a strategy whose replicable subset has
never been priced.
