# L5 EXTERNAL REVIEW CONTEXT

> **THIS FILE IS THE CANONICAL EXTERNAL-REVIEW HANDOFF. READ THIS BEFORE OLDER L5
> REPORTS. OLDER REPORTS ARE HISTORICAL EVIDENCE AND MAY CONTAIN SUPERSEDED
> CONCLUSIONS.**

Last updated **2026-09-20**, at commit `fd91d38e`. Branch
`claude/l5-execution-instrumentation-io6nzi`.

Durable behavioural source of truth remains `L5_OPERATING_MODEL.md`. This file is
the *state* handoff: what is established, what is dead, what is open. It is
deliberately short. Where a claim matters, the commit and the file that produced
it are named so a reviewer can go straight to the evidence.

---

## 1. Objective

Reverse-engineer Polymarket International trader `l5zn1bwom8etsk` ("L5"),
validate the reconstruction historically and out of sample, transfer it **only to
habitats that are compatible**, run prospective shadow, and reach a bounded live
probe **only** after evidence supports it.

Pipeline: `public activity → normalized history → sequential replay → behavioural
model → realtime shadow → action-by-action comparison → bounded live probe →
settlement audit → controlled scaling`.

Venues are never blended. **Polymarket International** (`gamma-api`, `clob`,
`data-api`) is L5's own venue and is **REFERENCE / FIDELITY ONLY**. **Polymarket
US** (`gateway.polymarket.us`, "PMUS") is the executable transfer target.

---

## 2. Current established L5 mechanism

Established in `81f77fea` (`analysis/l5/L5_LADDER_TRANSFER_20260920.txt`,
`analysis/l5/l5_ladder_identification.py`). Corpus:
`analysis/l5/l5_canonical_sequence.csv.gz`, 71,984 BUY fills over 2,216 windows,
`source = polymarket_public_activity`.

**L5 runs a multi-rung maker ladder, and it is proven on-chain, not inferred.**
Polymarket settles a taker against every maker order it crosses inside **one
transaction**. Two L5 fills sharing a `transaction_hash` at **different prices on
the same outcome** therefore cannot be one order cancelled and repriced — there is
no instant inside an atomic transaction at which a cancel/replace could occur.

- **3,434 atomic multi-price same-outcome maker sweeps.** All 3,434 are labelled
  `maker`. All fall in the BTC15m lane.
- **99.9% one-tick spacing** (3,431 of 3,434 sweeps span exactly 0.01).
- **Required-rung floor p50 = 4, p90 = 7**, from distinct same-side prices filling
  inside one-second buckets. This is a **floor**, not an estimate: we observe only
  rungs that *filled*.
- **Ladder sits on both sides: 51.2% extending the surplus side, 48.3% completing
  the deficit side.** This is what justified laddering the complement, and it was
  decided from L5 data before any PMUS P&L was consulted.
- **Persistence plus selective repricing.** 45.4% of consecutive same-side fills
  are at the *same* tick (rungs persist); 31.3% move one tick (they also drift).
  This supports a persistent ladder with selective repricing (H3), not a single
  repriced order (H1, falsified) and not a static ladder.
- **Sizing rises with price and is flat in inventory.** ~7 shares at price 0.15
  rising to ~24 at 0.95; median ~11 shares, unchanged across `|residual|` buckets
  from 0 to 300 shares. **Inventory-dependent sizing is NOT part of the mechanism.**

### The corpus is two execution modes and they must never be averaged

| mode | windows | fills | r:m | basis | standalone |
|---|---|---|---|---|---|
| **maker-ladder** | 314 | 58,312 | **0.182** | 0.9668 | **−0.279%** |
| **taker** | 1,902 | 13,672 | 1.079 | 0.9888 | +0.252% |
| blend | 2,216 | 71,984 | 0.347 | 0.9708 | −0.144% |

The split by maker share and the split by the on-chain ladder signature select the
**same** windows (311 of 314 agree; zero taker-dominant windows carry the
signature), so the label is not an artefact of which sweeps happened to be large
enough to observe.

> **The 0.347 r:m figure was a BLENDED reference and is NO LONGER the correct
> maker-ladder target.** The maker-ladder target is **0.182**. Every earlier
> report quoting "L5 ≈ 0.35 r:m" is comparing against an average that describes
> neither mode.

Note also that the maker-ladder mode **loses money standalone** (−0.279%) while
the taker mode makes a little (+0.252%). The ladder is a **volume engine, not a
profit engine**.

Provenance caveat a reviewer should hold: every maker-ladder window falls in
**2026-08-28 … 08-31**. The ladder *is* the mature August incentive-era behaviour
that `CLAUDE.md` keeps locked pending source-confirmed economics for our account.

---

## 3. Economics

From `1786440f` (`analysis/l5/l5_standalone_economics.py`) and
`L5_ECONOMIC_ENGINE_RECONCILIATION.md`, reconciled against the older full-history
accounting.

- **Standalone L5 is approximately breakeven, and slightly negative.** Matched
  **+$18,912.87**, residual **−$19,963.43**, **net −$1,050.57 = −0.144% of
  $728,500 spend**. Every bootstrap CI straddles zero.
- **Matched gains are substantially cancelled by residual losses.** That
  cancellation is the central economic fact of this trader.
- **Incentives were historically the principal profit engine.** Older accounting
  puts incentives at **≈ +$68,208** against $728,500 spend = **936 bps**, roughly
  5× the standalone magnitude and of the opposite sign.
- **No statistically credible standalone edge has been established** — not on the
  blend, not on the maker-ladder mode, not on any lane.

Accounting rules that reviewers should hold us to: seven separate lines
(matched-pair P&L, directional residual P&L, spread capture, confirmed maker
rebates, confirmed other rewards, fees, settlement cashflow). **Matched-pair and
directional-residual P&L are never summed into one edge number.** An UNMEASURED
line makes the total unmeasured.

---

## 4. Falsified hypotheses — the graveyard

Do not resurrect these. Each names where it died.

| # | hypothesis | verdict | provenance |
|---|---|---|---|
| 1 | L5 is a simple "buy YES + NO below $1" arbitrage | **FALSIFIED** | `CLAUDE.md` strategy priority; L5 exceeded 0.98 pair basis in 47.5% of its own completions and 1.00 in 42.8% (`bba82ee8`, `analysis/l5/L5_COMPLETION_DISCIPLINE_AND_QUEUE_20260920.txt`) |
| 2 | Residual carries favored-side directional skill | **FALSIFIED** | Residual P&L −$19,963 on 224,899 shares = −0.0888/share; `L5_OPERATING_MODEL.md`, `1786440f` |
| 3 | A shorter settlement clock alone fixes PMUS | **FALSIFIED** | `bb6ae542`, `analysis/pmus/STEP1_CLOCK_VS_COMPLETION_20260920T1836Z.txt`. Reinforced by `81f77fea`: L5's **15-minute** lane has the *best* geometry (r:m 0.187) and its 5-minute lane is 6× worse (1.115) — window length does not order geometry |
| 4 | PMUS lacks sufficient raw liquidity | **FALSIFIED** | `4760c15c` — queue-aware reachable compatible volume 74,426 units vs 12 captured, a 0.0161% capture rate. `MARKET_LIMIT` is 3 of 97 residual units in `f724a4cd` |
| 5 | Queue preservation is the major missing mechanic | **FALSIFIED, and it was my own hypothesis** | Proposed in `c2946ba7`, **withdrawn in `bba82ee8`**: preserving queue state was neutral-to-worse (45.3% → 45.0% completion, r:m 2.41 → 2.45). Churn was *helping* — a repriced rung follows the flow |
| 6 | A universal 0.98 completion cap is L5 behaviour | **FALSIFIED** | `bba82ee8`. L5 exceeded 0.98 in 47.5% of completions. The cap is *our* spring discipline, not L5's |
| 7 | Blended 0.35 r:m is the correct maker-ladder target | **FALSIFIED** | `81f77fea`. The correct target is **0.182**; 0.347 is an average over two modes |
| 8 | More PMUS rungs / zero queue can overcome broad-market flow imbalance | **FALSIFIED** | `81f77fea`. A 20-rung ±10-tick **zero-queue** upper bound shows imbalance 0.327 against the 4-rung arm's 0.315 — reach cannot change balance |
| 9 | PMUS per-market p10 ≈ 0.029 means balanced PMUS markets exist | **FALSIFIED** *(added `fd91d38e`, after this graveyard list was specified)* | `analysis/pmus/HABITAT_SELECTOR_20260920.txt`. A fair coin measures I = 0.187 at n = 18 prints; our forward windows carry a **median of 18**. All 30 rows at or below 0.083 are also at or below what a perfectly balanced market shows at their own print count. The p10 is a **sampling artefact** |

---

## 5. PMUS transfer

### Progression

| stage | completion | r:m | commit |
|---|---|---|---|
| single-shot | ~11.2% | ~16 | `5a13f0b0` |
| persistent two-sided (B2) | 37.1% | 3.5 | `5a13f0b0` |
| + historical L5 completion behaviour | 65.4% (69.2% settled) | 1.06 (0.89 settled) | `bba82ee8`, `f724a4cd` |
| **+ proven ladder (arm4b, frozen R=4)** | **74.1%** | **0.70** | `81f77fea` |

### Latest faithful transfer — arm4b

Ladder on both sides, R = 4 and band −2..+1 **frozen on L5 TRAIN before any PMUS
outcome was consulted**. Settled-market subset:

- completion **74.1%**
- residual : matched **0.70** *(in that replay; 0.919 on the whole replay cohort — the floor comparison below uses the cohort figure)*
- median pair basis **1.0200**
- matched **−$5.41**, residual **−$19.97**, fees **$0.00**
- **standalone −$25.38 on $292.38 capital = −8.68%**
- queue audit: away-rung depth ×2 → −11.29%, ×4 → −13.24%
- attributable maker incentive **$0.00 confirmed** (`/v1/transfers` carries no
  order id, so fill-level attribution does not exist)
- break-even needs **868–1324 bps** against the **97.59 bps** this venue has been
  observed to pay: short by **9× to 14×**

### The structural flow floor — why this stops

With acquisitions a ≥ b, matched M = b, residual R = a − b, and imbalance
I = (a − b)/(a + b):

```
R/M = 2I/(1 − I)     exactly
```

Every arm sits on its own floor to three decimals, i.e. **our execution already
captures 100% of the pairing its markets offer**:

| | I | r:m floor | r:m achieved |
|---|---|---|---|
| L5 maker-ladder habitat | 0.083 | 0.182 | 0.182 |
| PMUS arm4b (frozen ladder) | 0.315 | 0.919 | 0.919 |
| PMUS upper bound, 20 rungs ±10 ticks, **zero queue** | 0.327 | 0.973 | 0.973 |

Reach, rung count and queue position move both sides together and cannot change
their balance. The identity is locked by a regression test
(`tests/test_l5_ladder.py::test_flow_imbalance_floor_identity`).

> **Broad PMUS transfer has therefore reached STOP CONDITION B: the remaining gap
> is structurally impossible on these markets, not an unimplemented fidelity
> defect. DO NOT RESUME GENERIC PMUS EXECUTION TUNING.**

L5 did not have better execution than us. It had I = 0.083 against our 0.315.
Sports and esports books are directional; a two-sided maker cannot pair flow that
never arrives.

---

## 6. Current P0 — prospective habitat selection

**Question: can information available BEFORE trading identify markets whose
SUBSEQUENT flow imbalance resembles L5's maker-ladder habitat?**

The motivating hypothesis was that PMUS per-market imbalance has p10 ≈ 0.029, so
some markets might carry compatible habitat even though the broad cohort does not.
**That specific hypothesis was tested and failed** — see graveyard #9; the p10 is
a sampling artefact of thin forward windows. The question survives in corrected
form: the target is **excess imbalance over the per-row noise floor**, never raw I.

The habitat *gap* is real and survives the correction. At matched n = 18:

| | p10 | p50 | p90 | mean |
|---|---|---|---|---|
| noise floor, a truly balanced market | 0.000 | 0.111 | 0.333 | 0.186 |
| L5 maker-ladder, resampled | 0.000 | 0.111 | 0.444 | 0.188 |
| PMUS markets, measured | 0.108 | 0.469 | 0.927 | 0.506 |

L5's habitat is statistically indistinguishable from a perfectly balanced market.
PMUS is far above it.

**Selector** (`analysis/pmus/habitat_selector.py`, frozen artefact
`analysis/pmus/habitat_selector_frozen.json`, sha256 `f9dcd886549a2865`): three
features and an average of ranks — `mid_dist_half`, `imb_cnt_lb`,
`min_side_share` — chosen because their TRAIN signs are mechanistically
explicable and agree. `drift_abs` scored −0.213 on TRAIN and is **excluded**: its
sign says more price movement predicts better balance and no mechanism explains
that. Cutoff is the best TRAIN tercile, preregistered before any economics.

**Uses only pre-entry information; never P&L, settlement, our own fills, or any
post-decision observation.** Two regression tests assert this mechanically
(`tests/test_habitat_selector.py`).

Chronological TRAIN/OOS, every straddling market dropped entirely (7 of 62):

| | n | I_excess p50 | I_fwd p50 | two-sided/min |
|---|---|---|---|---|
| TRAIN selected | 56 | +0.170 | 0.365 | 0.20 |
| TRAIN rejected | 111 | +0.419 | 0.607 | 0.17 |
| **OOS selected** | 29 | **+0.092** | 0.227 | 0.63 |
| **OOS rejected** | 34 | **+0.284** | 0.450 | 0.45 |

OOS Spearman **0.315** against TRAIN's 0.292 — above train, so not an overfit.

**Honest limit: selection does not reach L5.** Selected OOS I_fwd 0.227 implies
r:m 0.587 against L5's 0.182, on 63 rows and 12 markets. Real signal on a thin
sample, not a solved problem.

**Fresh prospective cohort opened 2026-09-20T19:52:46Z**; every observation in it
is collected after the selector was frozen. Success condition, the not-permitted
list, and the if-NO branch are written into the artefact itself.

- **If it succeeds** → apply the frozen L5 maker-ladder mechanics to the selected
  cohort and report economics line by line.
- **If it fails** → **kill PMUS as an L5 deployment venue** and move this exact
  habitat test to other executable venues and categories, **without changing the
  L5 strategy**.

The L5 ladder engine is **not** applied to the selected cohort yet.

---

## 7. Current safety and execution state

- **No live authorization in force.** No C9 prepared or to be prepared.
- **No orders armed. No PMUS probe running.** `l5-r1-probe` ended itself
  2026-09-17T13:36:57Z with `CAMPAIGN_STOP:HALT`; mandate expired
  2026-09-18T00:41:20Z; `analysis/l5/authorization/HALT` restored. A stopped,
  silent R1 is the expected state.
- Legacy `pmus-spring-l5-probe` (`d84d9a68`) **must stay halted** — no unlatch,
  restart, redeploy or in-place sanitise.
- **Gemini is disarmed.** `REQUIRED_AUTH_ID`, `AUTHORIZED_SHA`,
  `AUTHORIZED_BUNDLE` all `None`; `EXPECTED_AUTH_ID` is the
  `NO-AUTHORIZATION-IN-FORCE` sentinel; nine latch files; five cohorts. Gemini is
  an unchanged **negative control** and is not to be modified for L5 work.
- **Healthy collectors must not be modified.** Read-only collectors/recorders run
  24/7 and self-recover.
- **Live `l5-exec-shadow` deployment: `11e4d2b7-c892-4f17-8244-4b8bbb54847b`**
  (service `5dced43a-b6b2-4ba9-8126-c9918072ac73`). The previously pinned
  `23fc0a11-aa61-4660-95a5-90cc65da2d80` is **STALE** — superseded
  2026-09-19T18:56Z by commit `6b295980`. Reading the stale id shows a tape
  stopping at 2026-09-19T16:19Z and looks dead when the service is healthy.

Confirmed realized, strict labels: **PMUS −$0.5619**, venue-confirmed, entirely
directional residual; matched-pair realized **$0.00** and the first matched pair's
economics **UNMEASURED**. **Gemini realized trading P&L $0.00.** Maker rebates are
a separate confirmed line: seven credits totalling **$0.51** on `/v1/transfers`,
window-level attribution only, **never** merged into trading P&L.

---

## 8. Open questions

0a. **PMUS habitat experiment: REPAIRED, and its historical basis is compromised.**
   `habitat_selector.observations()` was bridging collector outages — building a window
   from data on either side of a hole and presenting it as continuous. Fixed by
   `continuous()` (no observation gap above 120 s across the whole window), five
   regression tests. The audit: **76.5% of the historical dataset (199 of 260 rows,
   56 of 62 markets) is invalid**. TRAIN collapses 167 → 7 rows, so **the frozen
   selector's reference distributions and cutoff were fitted on a TRAIN set that is 96%
   bridged**; its OOS behaviour does survive on valid rows only (Spearman +0.319 →
   +0.245, separation −0.191 → −0.172 on 43 rows / 7 markets), but the fit behind it
   does not rest on data we can stand behind. The failed cohort is preserved as
   `analysis/pmus/inadequate_cohort_20260920/`, labelled INADEQUATE — INFRASTRUCTURE /
   DATA CONTINUITY, explicitly **not** a selector PASS/FAIL. Artefact untouched.
   **BLOCKED:** re-baselining onto the durable Railway tape needs a read path that does
   not exist — that service writes rows to its volume and logs only heartbeats, and its
   own docstring records the volume is not readable from outside.

0b. **Kalshi Step 1 done: KXCRYPTOLEAD15M is not the answer.** See
   `analysis/kalshi/KXCRYPTOLEAD15M_STEP1_README.md`. It is a **5-way race**, not a
   binary up/down. Pooled flow is the most balanced we have measured (I by volume
   **0.0032**, r/m floor 0.006) but per-market flow is ~14 trades, below readability.
   Maker drag on real settled flow is **+$0.0413/contract headline but median −$0.0100**,
   with 77.9% of P&L in 50 of 2,542 trades and per-market swings −$344 to +$512 — **15–25×
   the $20 reward pool**. The reward cannot overcome that because it is not the same
   order of magnitude as the variance. Next candidate: the **commodity 15m series**
   (GOLD/WTI/SILVER/NATGAS/COPPER), same $20 pool, target size 300, genuine binaries.
   Credited rewards remain **$0.00**; no order has ever been placed on Kalshi.

0. **Objective changed 2026-09-20: rebate-first.** The target is confirmed rewards
   minus realized drag, not trading P&L — the engine L5 actually ran. Kalshi pays
   rewards for RESTING and drag is paid only on FILLING, so the two terms decouple;
   that is structurally different from the L5 ladder, which maximised fills. Break-even
   pool-share table, measured inputs and the three steps that close it are in
   `analysis/kalshi/REBATE_FIRST_ECONOMICS_20260920.md`. Live pools measured today
   ($20/market/15min, KXCRYPTOLEAD15M BTC/ETH/SOL/HYPE). Credited to us: **$0.00**.
   Caution: our 693,112-print Kalshi habitat reading (I = 0.0212, r/m floor 0.043 —
   four times better balanced than L5's habitat) is on KXBTC15M/KXETH15M, whose
   programs were **WITHDRAWN 2026-05-12**. It must never be quoted as the incentivised
   series. PMUS serves sports only (no crypto); Gemini BTC up/down trades 1.36
   times/minute in total and has no usable flow.

1. Does the frozen habitat selector retain predictive power on the fresh
   prospective cohort opened 2026-09-20T19:52:46Z? First read scheduled
   2026-09-20T22:01Z. The selector is frozen: features, signs, weights, tercile
   cutoff, cohort definition, success condition and exclusion rules are not to be
   modified now that prospective data has begun. Reporting requirements and both
   preregistered branches are in
   `analysis/pmus/HABITAT_PROSPECTIVE_REPORTING_SPEC.md`.
2. If it does: does the frozen L5 maker-ladder engine, applied only to selected
   markets, produce materially different economics from the broad-cohort −8.68%?
3. Does any executable venue or category available to us exhibit I ≈ 0.083
   habitat at all? Untested outside PMUS sports/esports.
4. Can a maker rebate ever be attributed at fill level on PMUS? Today it cannot
   — `/v1/transfers` carries no order id — which caps what any incentive claim
   can be worth.

*Remove entries here when resolved. Do not accumulate stale TODOs.*

---

## 8a. Canonical measurement rule — permanent

> **RAW IMBALANCE CANNOT BE COMPARED ACROSS UNEQUAL PRINT COUNTS.
> `I_excess`, OR MATCHED-SAMPLE INFERENCE, IS CANONICAL.**

A perfectly balanced market still measures non-zero imbalance from finite flow:
E[I] = 0.246 at n=10, 0.185 at n=18, 0.112 at n=50, 0.079 at n=100, 0.003 at
n=58,312. L5's 0.083 came from 58,312 fills and is therefore nearly all real; the
same 0.083 in an 18-print window sits *below* the floor and is evidence of
nothing. This rule is what killed graveyard #9, and it applies to **every venue
and category** this framework is pointed at, not just PMUS.

Implemented in `analysis/pmus/imbalance_sampling.py`, locked by
`tests/test_imbalance_sampling.py`. `compare_raw()` raises rather than returning a
number when the print counts differ. Uncertainty on any arm-to-arm separation uses
a **cluster bootstrap over markets**, never over rows: rows from one market share
its book, its flow and its crowd.

---

## 9. Instructions for external reviewers

- **Challenge the conclusions.** Especially the ones this document states most
  confidently. The flow-floor identity and the two-mode split are the load-bearing
  claims; if either is wrong, most of section 5 and 6 collapses.
- **Inspect the underlying code and data** where accessible, rather than trusting
  the summary. Named modules and commits are given for that purpose.
- **Distinguish evidence from adaptation.** A number measured from L5's public
  history is evidence. A number produced by our replay under assumptions we chose
  is an adaptation, and every such assumption should carry an explicit bound.
- **Never infer profitability from modeled fills.** Theoretical fills, shadow
  inventory, mark-to-market gains, modeled pair economics, cost bases,
  reconstructions and estimated or accrued rebates are **not realized profit** and
  must never be labelled as such.
- **Never optimize thresholds against PMUS settlement P&L.** Parameters are frozen
  on L5 history *before* PMUS outcomes are consulted. A parameter chosen because
  it improved PMUS P&L is not a transfer, it is a fit.
- **Cite exact files and commits.** A finding without provenance cannot be acted on.
- **Identify contradictions with this document.** If an older report disagrees
  with this file, this file wins *and the contradiction should be reported* — it
  usually means a superseded conclusion is still being quoted somewhere.

---

## Maintenance rule

**Every material L5 research commit must update this handoff in the same commit,
or state explicitly in its commit message why no update is required.** A finding
that changes the mechanism, the economics, the graveyard, the transfer status, the
P0 or the safety state is material.
