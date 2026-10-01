# Grok's audit of 4ca8cad2: reproduced, and my interpretation was wrong

Every number recomputed from source. **I am not defending the earlier
interpretation — the audit is substantially correct and the repo already held
stronger, contradicting evidence I had not reconciled.**

## 1. Grok's claims, independently recomputed

Source: `data/l5/sep2026/*.json.gz`, 144,042 BTC15m TRADE rows.

| claim | Grok | recomputed | verdict |
|---|---|---|---|
| grouping on `(slug,ts,outcome,str(price))` **before** snapping | yes | `l5_behavior_census.py:47` keys on `str(p["price"])`; my snap runs later in `own()` | **CORRECT** |
| actions, raw grouping | 117,683 | **117,683** | **exact** |
| actions, snap-before-group | 107,133 | **107,133** | **exact** |
| same-price share falls | 23.7% → 17.0% | **23.7% → 17.1%** | **confirmed** |
| same-second collapse A/B/D | 37.1/20.4/42.5 | **36.6/20.9/42.5** | **confirmed** (D exact) |
| next actions with `gap_s==0` | 27.8% | **27.8%** | **exact** |
| same-second same-price repeats vanish after snapping | yes | **0 remain** | **confirmed** |
| `(slug,second)` pairs containing both outcomes | 5,955 | **5,955** | **exact** |
| transaction hashes with >1 distinct price | 6,670 | **6,670** (of 137,365) | **exact** |

**One claim does not survive as stated.** Sort order *can* manufacture a
direction within a second, but the aggregate effect is negligible: forcing
Down-first gives D = 32.9%, Up-first gives D = 32.8%. The reason is structural —
either ordering still yields a D transition, so the **count** is insensitive while
the **direction** is unknowable. Grok's mechanism is real; its aggregate impact on
A/B/D is 0.1 pt. Any *directional* reading ("he switches to the cheaper side") is
unsupported; the class counts are robust.

## 2. The proportions do not survive the definition

| definition | actions | A% | B% | D% | gap0% |
|---|---|---|---|---|---|
| raw current grouping | 117,683 | 43.4 | 23.7 | 32.9 | 27.8 |
| snap before group | 107,133 | 46.7 | 17.1 | 36.1 | 20.6 |
| transaction-hash grouping | 144,042 | 37.3 | 35.9 | 26.8 | 41.1 |
| same-second collapse | 91,241 | 36.6 | 20.9 | 42.5 | 6.6 |

A spans 36.6–46.7, **B spans 17.1–35.9**, D spans 26.8–42.5. **No proportion
survives.** The "smallest rule" I stated with specific proportions is withdrawn.

**What survives all four:** same-side next fills are the majority (57.5–73.2%),
and A > B in every definition (narrowly under tx-hash grouping: 37.3 vs 35.9).
Nothing finer than that is definition-independent.

## 3. The repo evidence Grok surfaced — verified, and stronger than claimed

### 82.7% maker→maker: CONFIRMED
`analysis/l5/pair_formation/roles_btc15m.json`: 313 both-roles-known sequences,
**259 maker→maker, rate 0.8275**. All 313 are **August**; March and April are
193 `unknown→unknown`. Two points Grok understated: the **complement leg is maker
in 299 of 313 (95.5%)**, and an independent feed-flag cross-check agrees
**626/626**, with `ambiguous_attributions: 0`.

### 2.6:1 — CORRECT, but it is the FLOOR, and Grok mixed two rows
`pre_resting.py`, re-run. It reports bounds at three latency assumptions:

| assumed reactive latency | pre-resting | reactive | ratio | unresolved |
|---|---|---|---|---|
| **2.1 s (structural floor)** | 37 | 14 | **2.64:1** | 455/506 = **89.9%** |
| 5.0 s | 74 | 14 | **5.29:1** | 418/506 = 82.6% |
| 10.0 s | 123 | 14 | **8.79:1** | 369/506 = 72.9% |

So 2.6:1 is real but is the **most conservative** row; Grok's "84% unresolved" is
approximately the 5 s row (82.6%), not the floor's 89.9%. Quoting 2.6:1 together
with 84% mixes rows. **The ratio only grows as the latency assumption loosens** —
the evidence is stronger than Grok stated, not weaker.

The floor is structural, not a guess: a reactive complement needs a *later block*
than the first leg, and Polygon's ~2.1 s block time bounds it.

### The 84/90% unresolved population
It means exactly: **both stories fit.** `CONSISTENT_WITH_PRE_RESTING` means a
reaction is *ruled out*; `UNRESOLVED` means the latest admissible placement time
falls after the first fill, so pre-resting and reaction are both possible. It is
**not** evidence for reaction. It is the honest majority, and nothing may be
concluded from it in either direction.

### 26.4% at/above ask, 99.86% mint-feasible — CONFIRMED, with a precision fix
`ZENODO_OVERLAP_FINDINGS.md` + `mint_match.json`, 2,795 overlap maker fills:
**738 of 2,795 = 26.4%** price at or above the best ask 1 s earlier, which a
resting bid cannot do. Of those 738, **99.86%** are mint-feasible; of the 2,014
resting-bid-consistent fills only **1.49%** are.

Grok's "99.86% are mint-feasible" is imprecise: it applies to the at/above-ask
**subset**, not to all maker fills. The actual finding is better — 1.49% vs 99.86%
is a **near-perfect discriminator between two distinct fill mechanisms**.

The mistimed-fill hypothesis was already tested and rejected: real block
timestamps differ from interpolation by p50 = 0.0 s.

## 4. What mint-feasibility does and does not establish

**Establishes:** the fill *could* have been matched by a complete-set mint — two
buyers of opposite outcomes matched by minting $1 — which requires **no seller and
no offer on the buyer's own book**, only a counterparty bidding the complement at
≥ 1−p. That is the only known mechanism by which L5's maker **buy** can print
above the ask, and it is why 26.4% of his maker fills are otherwise impossible.

**Does NOT establish:** that a mint actually occurred; that any order belonged to
any trader (`best_bid(complement)` is a public price level, and the L2 dataset
attributes no order to anyone); or anything about L5's own pair cost — `pair_sum`
is his fill price plus the *complement's* best bid, the joint mint cost, **not**
his Up cost plus his Down cost. The repo already states this; I am restating it
because it is the easiest thing here to over-read.

## 5. Could mint mechanics explain the two-sided fills? Partly — be precise

**Yes, for the central anomaly.** A mint needs no seller, so each side of L5's
book can be filled independently by an opposite-outcome *buyer*. That removes the
requirement for sellers to exist on both sides and explains both legs filling
quickly without him acting twice.

**No, not as a single event.** A mint matches L5 against a *different* trader
taking the opposite outcome, so it delivers L5 **one** leg, not both. Two L5 legs
in the same second remain two matches. Mint lowers the barrier to both sides
filling; it does not collapse them into one event. Claiming otherwise would
overstate it.

## 6. THE STRONGEST SUPPORTED MODEL, given everything in the repo

### OBSERVED
1. L5 is ~**95% maker** across history (55,639 maker / 2,665 taker); **88.3%** in
   the L2 overlap.
2. **26.4%** of overlap maker fills print at/above the ask 1 s earlier —
   impossible for a resting bid; **99.86%** of those are mint-feasible against
   **1.49%** of the rest.
3. **The touch falls toward his order.** 61.6% of fills sit beyond −4 ticks at
   T−30, only **12.7%** at T−1.
4. **96%** of BTC15m windows become two-sided, median **54 s** and **2 fills** in.
5. **82.75%** maker→maker; complement maker **95.5%**.
6. At the structural floor, pre-resting **outnumbers** proven reaction **2.64:1**,
   rising to 8.79:1 at a 10 s assumption; the majority is unresolved.
7. **38.5%** (195/506) of his complements filled **before the venue published his
   first leg** — unreachable by any reactive clone of ours at our 15.16 s lag.

### INFERRED
L5 maintains **two-sided resting liquidity**, and the market decides which parts
of it execute. On this reading his near-permanent "imbalance" is **a consequence
of asymmetric hits, not a chosen directional stance**; his same-side "persistence"
is **consumption of several standing levels**, not repeated decisions; and both
outcomes filling seconds apart is unremarkable because both were already resting.
Hypotheses 1 and 2 (ladder, and ladder + mint) are better supported than
"a directional decision every ~2 seconds", which is now the weakest of the three.

### UNKNOWN
Whether he cancels or replaces, and how often. Whether any individual fill was a
mint or a book trade (no order attribution exists in the dataset). His actual
placement times, as-placed sizes, and unfilled intent. Whether the ~85–90%
unresolved population behaves like the resolved minority. Whether a small fill is
a partial of a larger resting order — which is why `size`, the strongest predictor
I found, may be an **output of liquidity consumption rather than a decision
variable**.

## 7. My own error, stated plainly

I concluded from fill-only data that *"L5 re-prices within a tick; he does not
park quotes."* The repo's own 1-second L2 evidence says close to the opposite:
**he is a patient passive buyer whose resting order is reached as the touch falls
to him.** My conclusion was an artifact of reading a sequence of **executions** as
a sequence of **decisions** — the precise error the unit-of-analysis audit names.

The practical consequence: making a sequential decision engine faster was never
going to reproduce L5, because we were reproducing the output without the
mechanism. The engine correction I proposed ("re-price on market movement") is
**withdrawn pending the synchronized L2 evidence**, which now has a sharper job:
not *does he reprice*, but *how much two-sided liquidity is standing before the
move, and does it move at all*.
