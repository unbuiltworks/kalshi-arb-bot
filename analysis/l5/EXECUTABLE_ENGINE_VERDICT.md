# EXECUTABLE ENGINE — VERDICT

**Deliverable: the second permitted outcome — a proof that the currently
available venues cannot reproduce profitable early-L5 economics.** No
candidate is advanced to OOS/shadow, because none clears the gate. Reasons
are measured on both sides, not asserted.

Historical reconstruction of another trader's public activity. No figure is
realized profit for our account. No live orders, no OOS unseal, no
threshold sweep, clone unchanged.

---

## 1. The mechanism, and every dollar of it

Attribution is a **split of measured cash** from terminal inventory —
never a model added alongside redemption, so nothing is double-counted.

| component | amount | note |
|---|---|---|
| **matched-pair economics** | **+$80,652.14** | the entire source of profit |
| directional residual settlement | **−$30,654.03** | destroys 38% of it |
| charged transaction fees | **−$29,135.82** | destroys another 36% |
| rebates / rewards | **excluded by construction** | $10,529 pre-incentive, kept out of trading edge |
| **unexplained cashflow** | **−$1,265.97** | cause tested and disproved, see §9 |
| **measured cash** | **+$19,596.32** | 6,252 events, 336,342 fills, 100 days = **$195.96/day** |

**Concentration — not outlier-driven, but not robust.** Median event
**+$5.18**, win rate **57.37%**, trimmed mean (best and worst 1% dropped)
**+$2.77/event**. By removal: dropping the top 10 events leaves 74.7% of
the total; dropping the top 63 (1%) leaves **4.3%**; dropping the top 100
turns it **negative**. Net is a **6.9% residue of $284,262 of gross flow**
(+$151,929 against −$132,333). Max drawdown **−$3,446**, longest losing
streak **11 events**, worst event −$528.77.

**Weekly / regime:** 20 weeks, **4 negative**, worst −$2,415.60. By regime
$42.17 → $184.32 → $322.64/day, with per-event *falling* after R2 —
frequency-before-size visible in cash.

**⚠ And the incentive-free window is negative.** "Pre-incentive" was
defined by the first REWARD (2026-08-08), but **MAKER_REBATE begins
2026-04-21**. Strictly before any rebate: **211 events, 17 days,
−$168.46**. Thin enough to read as indistinguishable from zero, but there
is **no clean demonstration that the mechanism paid without rebates** —
which is exactly the question that governs transfer to a venue where we
receive none.

---

## 2–3. The three candidate edges, priced separately

| explanation | cash contribution | confidence | basis |
|---|---|---|---|
| **A. Inventory edge** (two-sided accumulation → compression) | **+$80,652** | **HIGH** | measured on 6,252 independent events; pair cost median 0.9679, 81.6% of events reach matched inventory |
| **B. Execution edge** (better fills / lower adverse selection) | **NOT COMPUTABLE** | — | see §4; no Polymarket book exists on disk |
| **C. Directional edge** (terminal residual) | **−$30,654** | **HIGH, and NEGATIVE** | residual won in 3,179 of 6,252 events and still lost money |

**C is killed.** The residual is not a source of edge; it is the single
largest leak. **A is the mechanism.** B is the open question and is the
only one that could change any conclusion below.

---

## 4. Historical Polymarket L2 — IT DOES NOT EXIST. Item 4 is unanswerable.

I checked exhaustively rather than assuming. Every Polymarket artifact on
disk comes from exactly three endpoints:

```
https://data-api.polymarket.com/activity
https://data-api.polymarket.com/positions
https://data-api.polymarket.com/trades
```

**No book, no L2, no bid/ask, no depth, no quote series.** Worse, every
one is **L5-only**: `trades.jsonl` and `trades_takeronly.jsonl` each
contain exactly **one distinct trader**, and the gap file carries no wallet
field at all. There is no market-wide print tape either.

Therefore **UNAVAILABLE, not estimated**: pre-fill bid/ask/depth; queue
evidence; mark after 1/5/10/30/60s; maker/taker classification; and the
one comparison that matters —

> **L5 Polymarket fill toxicity vs Gemini maker fill toxicity: NOT
> COMPUTABLE from existing data.**

The brief's condition "do not wait for network access **if** historical L2
already covers usable L5 periods" is **not met**. It covers none. And the
proxy is blocked too: the egress proxy returns 403 to CONNECT for
`data-api`, `gamma-api` and `clob.polymarket.com`.

This is the single highest-value unmeasured quantity in the programme, and
it cannot be obtained from this container.

---

## 5. Venue requirements derived from the profitable actions — and scored

Requirements are taken from the actions that actually produced the cash,
not from similarity scores.

| measured requirement | L5 achieved | Gemini BTC5M measured | verdict |
|---|---|---|---|
| completion rate (events reaching matched) | **81.6%** | complement reach **45.9–50.8%** | **FAIL** |
| simultaneous pair < $1.00 | median **0.9679** | **0 of 24,520** | **FAIL** |
| — sequential sub-$1 reachable? | yes, routinely | 11/17 windows, median $0.93 | PASS |
| own trades/minute | **~30/min** (2.0s median gap) | **~0.8 prints/min** market-wide | **FAIL** |
| first-fill → complement latency | median **49s** (p90 168s) | median **117s** | MARGINAL |
| window duration | 900s (15m dominates pre-incentive) | 300s | **FAIL** |
| two-sided depth at size | median fill **19.22 shares** | q25 both-sides 61.7% | MARGINAL |
| charged fee form | 0.07·p·(1−p), taker-only | same form | PASS |
| maker rebate ≥ hurdle | all-in **$0.004554/ct** | **$0 confirmed** | **FAIL** |

Kalshi 15M, measured: `pair_cost < 1.00` in **0 of 2,269** snapshots across
**all eight series**; complement reach **UNAVAILABLE** (prints are
anonymous); maker compensation **$0 credited**, and **0 of 383 BTC/ETH
rows** sit under any reward program. PMUS sports: gated out — 2 markets
over 12 minutes, `ask_qty` null in 232/232 rows.

### The decisive test

Running L5's own mechanism against Gemini's measured book:

| | value |
|---|---|
| Gemini completion rate required for positive EV | **71.2%** |
| Gemini measured complement reach | **45.9–50.8%** |
| L5's achieved completion rate | **81.6%** |

**L5 clears the hurdle comfortably. Gemini's book cannot supply the
fills** — a 20–25 point shortfall. The mechanism is sound; the venue
cannot run it. That is the proof.

---

## 6. Incentives, treated as additive only

| | without incentives | with sourced capturable incentives |
|---|---|---|
| L5 pre-incentive (reference) | +$195.96/day | +$291.68/day (+$95.72 rebates, kept separate) |
| Gemini BTC5M | **negative on every mechanism tested** | **$0 confirmed credited** |
| Kalshi 15M liquid | UNMEASURED (toxicity unknown) | **$0 credited**; no program over liquid series |
| Kalshi 15M subsidised | UNMEASURED | pool is a **TERM**; $1,920/day advertised against $177/window of flow |

No advertised pool is counted as captured anywhere. All $28,797.69 of
REWARD income is **post**-2026-08-01.

---

## 7. Backward from $480/day — and from $100 and $250

Measured mechanism: 56.84 events/day, median event outlay **$316.05**
(p90 $1,131), return on deployed dollar **+1.180%** capital-weighted,
+$5.99 cash/event, $27,063/day deployed, concurrency peak **2**.

| target | required deployed/day | vs observed $27,063 | required events/day | vs observed 56.84 |
|---|---|---|---|---|
| $100/day | $8,475 | 0.31× | 16.7 | achievable |
| $250/day | $21,186 | 0.78× | 41.7 | achievable |
| **$480/day** | **$40,678** | **1.50×** | **80.1** | **+23.3/day** |

Lever by lever, nothing validates: **size** — median outlay $316 → $493–
$1,096, but **depth support is UNAVAILABLE**; **concurrency** — needs
3.1–6.9 against an observed cap of **2** that L5 never lifted despite 120×
scaling *and* 74.6% idle wall-clock; **events** — the only lever with
mechanical headroom, but it assumes untaken events carry the same +1.18%,
which this corpus cannot test; **execution** — needs a **50–242%**
per-event edge improvement.

**$480/day is not reachable from the measured pre-incentive mechanism.**
Confirmed trading $307.90/day plus **every** pre-incentive rebate dollar
reaches **$403.62/day — still 16% short.** $100/day and $250/day are
within the measured envelope; $480/day is an incentive-era artifact, as the
canonical report concluded independently.

---

## 8. The clone cannot even be compared — the ENVELOPE is what blocks L5

| rule | blocked | % applicable | cash blocked |
|---|---|---|---|
| `MAX_TOTAL_COST = 12.00` | 331,157 | **98.46%** | +$19,180 |
| `QUANTITY = 1` | 327,307 | **97.31%** | +$20,847 |
| `MAX_ACTIONS = 8` | 294,508 | 87.56% | +$15,551 |
| `MAX_RESIDUAL = 6` | 176,829 | 99.10% | +$20,260 |
| **`EXTEND_MIN_AVG_IMPROVEMENT = 0.05`** | **96,637** | **56.12%** | **+$5,448** |
| **`MAX_PAIR_COST = 0.96`** | 80,557 | 51.02% | **−$2,238** |

**≥87% of L5's activity is unreproducible by construction**, so
action-by-action comparison cannot currently run at all. The five biggest
suppressors are size/budget caps, not behavioural rules.

**Two results worth separating:** the **0.96 pair-cost ceiling is
endorsed by measurement** — the cash it blocks is **−$2,238**, i.e. the
loss-making completions, so spring discipline stays frozen. The **5¢
EXTEND gate is contradicted** — it blocks 56% of EXTENDs across 90.85% of
events carrying **+$5,448**, on L5's most frequent action.

Minimal empirically-entailed changes (**not applied, and not optimized**):
1. scale the OPEN TTE band by actual window duration — `OPEN_TTE_HI = 840`
   is *unreachable* on a 300s window, so the band is undefined there; a
   correctness fix;
2. demote the 5¢ EXTEND gate from a hard gate to a ranking input, letting
   risk caps bind — **the 5¢ number is restated in four live authorization
   bounds (`arming.py:112,143,173,198`), so this requires a NEW OWNER
   GRANT, not an edit**;
3. reconcile `L5_EXTEND_BUDGET = 1` against `MAX_EXTENDS_BEFORE_COMPLEMENT
   = 2` — they contradict each other and both contradict L5;
4. separate the bounded-probe **envelope** from the **policy** so the
   policy can be replayed at L5's observed scale while the live envelope
   stays exactly as frozen.

---

## SCOREBOARD

| mechanism | cash contribution | evidence / events | causal | executable | EV | capacity | incentives | projected $/day | status | next gate |
|---|---|---|---|---|---|---|---|---|---|---|
| **A: inventory compression (L5, Polymarket)** | **+$80,652** | 6,252 events | **yes** | on Polymarket, historically | +$5.18 median/event | $316 median outlay, concurrency 2 | excluded | $195.96 observed | **MECHANISM CONFIRMED** | needs a venue that supplies the fills |
| C: directional residual | **−$30,654** | 6,252 events | yes | yes | negative | — | — | — | **KILLED** | none |
| B: execution / low toxicity | **NOT COMPUTABLE** | 0 | — | — | — | — | — | — | **BLOCKED** | Polymarket book data; proxy 403s |
| A on Gemini BTC5M | negative | 61 windows / 24,520 obs | yes | yes | reach 45.9% vs 71.2% needed | q25 61.7% | $0 | negative | **KILLED** | none |
| A on Kalshi 15M liquid | UNMEASURED | 2,269 snaps / 83 min | — | — | pair <1.00 in 0/2,269 | UNAVAILABLE | $0 credited | — | **GATED** | Kalshi-native toxicity + a program over a liquid series |
| Gemini maker supply | −$0.238/fill | 10,698 fills / 46 windows | yes | yes | zero non-toxic states | — | $0 | negative | **KILLED** (stays killed) | none |
| $480/day target | — | — | — | — | — | 1.50× deployed | +$95.72/day max | $403.62 with all rebates | **UNREACHABLE pre-incentive** | — |

---

## Verdict

**The mechanism is identified and sound: matched-pair compression,
+$80,652 of matched economics, 81.6% completion, median pair cost
0.9679.** It is not an outlier artifact — the median event makes money.

**No currently available venue can execute it.** Gemini fails on
completion rate (45.9–50.8% against 71.2% needed), trade density (0.8/min
against ~30/min), window length and simultaneous pair cost. Kalshi matches
L5's window and prices pairs tighter, but **never below $1.00 in any of
eight series**, pays **$0 credited** compensation, carries **no program
over any liquid series**, and its maker toxicity is **unmeasured**.

**Nothing is advanced to freeze/fingerprint/OOS/shadow**, because the gate
requires executable fill assumptions and no venue provides them.

**The one thing that could overturn this** is explanation B — that L5's
edge is venue execution quality rather than the inventory pattern. It is
consistent with everything observed (L5's rebate is $0.004554/contract
against Gemini's −$0.238 toxicity, a **52×** gap that rebates cannot
explain), and it is **not measurable from any data we hold**. Obtaining
Polymarket book data — by network policy change or an out-of-container
pull — is the single highest-value action available, and it is a
measurement, not a trade.
