# Cancellation rule: REJECTED. The real divergence is the match rate.

Dataset: `analysis/adverse_entry/windows.json`. TRAIN = 82 non-BTC windows (threshold fitting).
HELD-OUT = 48 BTC15m windows, untouched until the single evaluation below.
Replay: `analysis/adverse_entry/replay_c.py` (cancellation), `replay_d.py` (defensive probe).

## 1. Definition audit — the two adverse rates were NOT comparable

| | population | horizon | rate |
|---|---|---|---|
| ours (as previously reported) | all modelled fills | placement -> window close | 77.5% |
| L5 | openings from flat (n=321) | next action, median 3-30s | 30.8% |

`src/l5_maker_fill_model.py::adverse_selection` measures placement->close; the L5 figure was
next-action. Re-measured ours at matched horizons on TRAIN (n=104 fills): **+10s 45.2%,
+30s 47.1%, +60s 45.2%, close 51.9%**.

The real gap is **45.2% vs 30.8% = 1.47x, not the 2.5x previously reported.** The 77.5%
production figure does not reproduce on this dataset (51.9% at the same horizon) and must
not be set against L5's number. Correction stands against my own earlier claim.

## 2. Cancellation rule — rejected on held-out

Signal: book imbalance against our side >= threshold; 1.0s signal+submit+ack latency;
pending-cancel state; fill-before-cancel races counted. Threshold fitted on TRAIN **only**
(selected 0.10; TRAIN net -1.3812 vs baseline -3.3419). Evaluated once on held-out:

| | baseline (maker) | cancel @0.10 |
|---|---|---|
| eligible windows | 48 | 48 |
| traded (participation) | 25 | **8** |
| modelled fills | 48 | 16 |
| adverse rate +10s | 41.7% | **50.0% (worse)** |
| cancels triggered | 0 | 44 |
| fill-before-cancel races | 0 | 4 |
| max drawdown | $1.213 | $0.128 |
| **net** | **-$1.2018** | **-$0.1002** |
| $ / eligible window | -0.0250 | -0.0021 |

Paired on identical windows: 17 differ, mean +$0.0648, SE 0.0368, t=+1.76,
**better/worse 7/10, sign p=0.63**.

Ledger: 32 of 48 entry fills removed; avoided losses -$1.1956; missed profitable fills
+$0.0940; 4 cancellation failures; remaining exposure 2 stranded windows -> 0.

**Why it is rejected, not adopted:**
1. The adverse rate gets **worse** (41.7% -> 50.0%). The rule does not avoid adverse fills.
2. On the windows it keeps it changes nothing (-0.1002 both). Its entire effect was deleting
   17 windows that happened to sum to -1.1015. Selection test: baseline mean on kept
   -0.0125 vs killed -0.0648, **t=+1.35** — not significant.
3. Both arms are negative, so **"do not trade" at $0.0000 dominates both.** A rule whose only
   mechanism is reduced participation can only approach zero from below.
4. This is the same abstention signature that inverted out of sample on the earlier entry
   filters (-0.0229 TRAIN -> -0.0982 TEST).

## 3. Where the loss actually is

Net decomposed by outcome, identical config both sets:

| | HELD-OUT BTC15m | TRAIN non-BTC |
|---|---|---|
| completed-pair windows | n=23, -$0.3102, mean -0.0135, t=-1.52, 14/23 win | n=50, -$1.3194, mean -0.0264, t=-3.31, 21/50 |
| **stranded windows** | **n=2, -$0.8916, mean -0.4458, t=-7.33, 0/2** | **n=4, -$2.0224, mean -0.5056, t=-11.35, 0/4** |

Strands are **7-8% of traded windows but 61-74% of the loss**, win-rate **0/6**.
The live 1945z strand (-$0.6864) sits inside that replay distribution.

## 4. Strands are not a completion-logic failure — completion is EV-neutral

All six strands are the same shape: one cheap-side maker fill (own 0.30-0.40), complement
never acquired, settles worthless. Diagnosis of every armed frame:

- depth was **never** the blocker (0 frames with no depth; 5,501-6,906 contracts available)
- entry spread was **0.01 — the tightest possible — in all four TRAIN strands** (clean windows
  averaged 0.0144), so an entry-spread filter separates nothing. Swept 0.03-0.20: no effect.
- the complement's taker price had moved to **0.77-0.95** against a held leg costing 0.30-0.40,
  so all-in was 1.11-1.36

Raising the defensive ceiling from 1.20 to unlimited:

| ceiling | 1.20 | 1.26 | 1.30 | 1.40 | 2.00 |
|---|---|---|---|---|---|
| strands | 4 | 3 | 2 | 1 | 1 |
| net | -3.3419 | -3.2603 | -3.1780 | -3.3249 | -3.3249 |

Strands fall 4 -> 1 and the money does not move (+$0.017 on $3.34 = **0.5%, noise**).
Holding a leg worth `m` that cost `c` has expected value `q(m-c)`; completing at the market
pays `q(1-c-(1-m))` = the same. **Completion converts an uncertain loss into an equal certain
one. The loss is incurred at the adverse move, and no completion, cancellation or spread rule
can recover it.**

## 5. The match rate is the single binding constraint

Independent confirmation from the live two-sided shadow (`L5_SHADOW_TWO_SIDED`, 97 common
windows, CONSERVATIVE queue-aware scenario) — a different instrument, same conclusion:

| | control (one-sided) | treatment (two-sided) |
|---|---|---|
| matched pair net | **+$9.3151 / 33 u = +$0.282/u** | **+$21.6271 / 75 u = +$0.288/u** |
| unmatched residual net | -$21.4851 / 189 u = -$0.114/u | -$51.9171 / 212 u = -$0.245/u |
| net per window | -$0.1255 | -$0.3123 |
| incremental net | — | **-$18.12 (-$0.1868/window)** |

**The matched-pair edge is real and stable at ~+$0.28/unit across both arms.** It is reachable
on only 14.9% (control) / 26.1% (treatment) of acquired inventory. Break-even match rates are
28.8% and 46.0% — we are **1.76x-1.94x short**. Two-sided quoting raises the match rate
15% -> 26% but doubles the residual cost per unit, so net gets worse: resting both sides gets
you filled on whichever side the market is leaving.

Required maker rebate to break even: **2.28 c/contract (held-out), 3.82 c/contract (TRAIN)**.
Maximum credit under the modelled schedule (`MAKER_THETA=-0.0125`) is **0.313 c/contract** at
p=0.50 — **7.3x to 12.2x short**. No rebate assumption in the current fee schedule closes it,
and none is venue-credited (`eligibility: UNVERIFIED_NO_VENUE_CREDIT_RECORD`).

Separately, at our 1.5-contract order size the $0.01 per-order fee costs 0.667 c/contract and
**exceeds the entire maker credit of 0.312 c/contract** — net -0.354 c/contract before any
trading edge. Order size must reach ~3 contracts to break even on fees alone, ~5 to be
positive. Our bounded-probe size is structurally fee-negative.

## 6. Verdict

**NO-GO on maker-only BTC15m single-leg entry at any size tested.** Rejected, in order:
cancellation rule (this experiment), defensive completion (EV-neutral), entry-spread filter
(no separation), order-size scaling (edge negative at every size), two-sided quoting
(-$0.1868/window on 97 windows), taker-open (previously: worse in 85/90 paired windows).

The per-contract edge is **negative before fees**. That is the finding: there is no fee,
rebate, sizing or lifecycle configuration that rescues a negative gross edge.

No shadow-only implementation change is proposed, because the experiment that would justify
one failed. The next strongest divergence is **not** an entry or cancellation rule: it is the
**match rate**, and the smallest actionable blockers are, cheapest first:

1. ~~**Order size vs the per-order fee**~~ — **TESTED AND REJECTED.** Swept opening notional
   $0.80 -> $25 on both sets. Net/contract stays ~-$0.02 (TRAIN) and -$0.008..-$0.019
   (held-out) at every size, and `net_ex_fee` stays strongly negative throughout, so the
   per-order fee is **not** the explanation — the per-contract edge itself is negative and the
   loss scales with size. The single positive cell (held-out @ $25, +$12.34) is an artifact:
   **one window is 223% of the total, net excluding it is -$15.1453, t=+0.38**, and it deploys
   $50.86 peak capital against a $5 aggregate limit. Same shape as the dissolved XRP05M
   false positive.
2. **A venue-credited maker rebate >= 2.3 c/contract** — currently unverified and 7-12x short.
   Needs a source-confirmed credit record, not an estimate.
3. **A market where the match rate exceeds ~29%** — the pair edge (+$0.28/unit) is real; BTC15m
   simply does not let us reach it. This is a discovery/selection question, which the existing
   PMUS full-slate screening already addresses.

Nothing here is realized profit. Replay and shadow figures are modelled; the only realized
number in this document is the live -$0.6864 settlement.
