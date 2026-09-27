# L5 EMPIRICAL MODEL — canonical findings

**Status of this file.** New at commit `0bf8f0ee`+. It did not previously exist;
nothing here supersedes a prior version of this document because there was none.
It is the durable register of conclusions that later work must either build on or
explicitly overturn with materially better evidence.

Companion machine-readable copy: `analysis/l5/l5_empirical_model.json`.
Experiment ledger: `L5_EXPERIMENT_REGISTRY.md`.

Behavioural source of truth remains `L5_OPERATING_MODEL.md`. This file is
**economic**, not behavioural, and does not authorise any model, cap, threshold
or lane change.

## Label discipline

`EXPECTED` (modeled) · `ELIGIBLE` (program says we qualify) · `CREDITED`
(the venue actually paid it) · `REALIZED` (settled cash in our account). These
are never interchanged. Modeled rebate is never added into net.

---

## L5_ECON_001 — SUPPORTED
**L5's historical realized economics are incentive-dominated.**

$68,208 of $81,895 total profit = **83.3%** from CREDITED incentives
(maker rebates $33,929 + liquidity rewards $26,956 + taker rebates $7,323).

- Source: `docs/L5_FULL_HISTORY_LEDGER_20260831.md`
- Population: complete Polymarket history of `l5zn1bwom8etsk`, 2026-03-19 →
  2026-08-31. 742,964 TRADE, 15,243 REDEEM, 109 MAKER_REBATE, 72 TAKER_REBATE,
  19 REWARD rows across **16,501 up/down windows**, $7,423,850 turnover.
- Reconciliation: per-market against the venue's own closed-position `cashPnl`
  over 1,459 settled markets; median abs diff $0.49, 74.8% within $1.
- Precision caveat carried from source: reconstructed grand total (+$81.9k) does
  not exactly match the profile's ~$70.3k P/L + ~$3.0k open positions.
- Derived: `analysis/pmus/L5_ADVANTAGE_DECOMPOSITION.md`, commit `0bf8f0ee`.

## L5_ECON_002 — SUPPORTED
**Matched economics are strongly positive; directional residual removes almost
all of it.**

Gross matched **+$155,076**; directional residual & timing **−$141,390** =
**91.17%** of matched destroyed; net trading cash **+$13,686**.

Per window: matched +$9.3980, residual −$8.5686, net trading +$0.8294.
Per dollar of turnover: +2.0889¢ matched, −1.9045¢ residual, +0.1844¢ net.

- Same source and population as L5_ECON_001.

## L5_ECON_003 — SUPPORTED
**Trading-only expectancy is weak relative to incentives.**

Observed **+0.1844¢/$** of turnover, weekly-block bootstrap 95% CI
**[−0.08, +0.55]¢/$**, **P(≤0) = 8.4%** — statistically indistinguishable from
zero. Total take (trading + incentives) is 1.10¢/$, CI [0.83, 1.31].

Corollary recorded in source: weekly take per dollar is flat at ~0.9–1.7¢/$ from
April to August. The $70k curve is a **turnover** story, not an improving-edge
story; ramp outran cumulative profit ~100:1 and was funded by capital injection.

- Same source and population as L5_ECON_001.

## PMUS_ECON_001 — SUPPORTED PRELIMINARY
**Frozen queue-aware PMUS economics, q=25, n=5 windows.**

matched **+$4.2825/window** · residual **−$4.0500** · fees **−$0.8007** ·
net **−$0.5682/window**.

q=50: matched +$7.9507 · residual −$8.1000 · fees −$1.5153 · net −$1.6646.

- Policy: θ=0.96, variant B, `fill_model='queue_aware'`, preregistered grid
  unchanged (`analysis/pmus/CLIP_CAPACITY_PREREG.md` + ECONOMIC_COMPLETE addendum).
- Population: 5 usable BTC15m windows of 2026-09-27 — 1830z (UP), 1845z (UP),
  1900z (DOWN), 1915z (DOWN), 1930z (UP). 1815z excluded (collector started
  mid-window, 0 two-sided states). Tape
  `data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl`, commit `19955307`.
- Completeness: each observed from first open through a terminal state with no
  bbo gap above 32s (HEARTBEAT_S 30 + one 2s poll).
- Verification: independent cashflow reconstruction
  (`scripts/audit_economic_complete.py`) reconciles **630 configurations**
  (5 windows × 3 fill models × 7 clips × 6 θ/variant) with 0 mismatches.
- **PRELIMINARY: n=5 against a preregistered checkpoint of 25.** Not an economic
  result. Do not size, tune or promote from it.

## PMUS_ECON_002 — SUPPORTED PRELIMINARY
**The largest measured PMUS-vs-L5 gaps are missing credited incentives and
trading fees, jointly 89.2% of the q=25 gap.**

Total gap L5 +$4.9630/window vs PMUS −$0.5682 = **$5.5312/window**:

| component | $/window | share |
|---|---|---|
| gross trading (matched + residual) | +0.5969 | 10.8% |
| fees (L5 pays $0) | +0.8007 | 14.5% |
| **credited incentives** | **+4.1336** | **74.7%** |

At q=50 the gap is $6.6276: trading 14.8%, fees 22.9%, incentives 62.4% = 85.3%
jointly.

- Scope caveat: "in this comparison" — L5 population is 16,501 windows over 5.5
  months on Polymarket; PMUS is 5 windows on one day on PMUS. The two are not
  matched samples and the comparison is a decomposition, not a controlled test.

## PMUS_ECON_003 — SUPPORTED
**Authoritative observed PMUS credited incentives remain $0.**

CREDITED rebate is $0, confirmed zero three independent ways, never credited.
Modeled maker rebate (θ_maker = 0.0125) is reported on its own line and is
**never** added into net. **Do not substitute modeled or raw rebates for
credited rebates.**

- Source: `analysis/pmus/CLIP_CAPACITY_PREREG.md` scoring rules; enforced in
  `src/pmus_completion_shadow.py` (`credited_rebate` hard-wired to D(0)).

## RESIDUAL_001 — SUPPORTED
**Residual losses are structural to L5 behaviour, not by themselves evidence of
clone failure.**

L5 loses 91.17% of matched economics to residuals; PMUS loses 94.57% at q=25 and
101.88% at q=50. Nearly identical. L5 never sells ($2.18 of sell proceeds across
742,964 trades), so residual is present in essentially every window at ~100%
frequency and is a deliberate accepted cost.

Independently, the canonical complement-response corpus
(`src/l5_complement_response_curve.py`, 71,354 inventory-changing fills) shows
L5 leans to the **complement** after the held side rises (+2..+10c → 63.5–67.7%
complement), so our post-completion complement acquisition is **L5-faithful**.
Inventory imbalance is the weakest predictor and OOS it *hurts* (−0.28%).

## RESIDUAL_002 — UNCERTAIN / IMPORTANT
**PMUS residual loss per unit is substantially worse than L5's historical
distribution. Mechanism not yet identified.**

PMUS observed **−$0.4050/unit**, 100% of it in the 25–45c acquisition band.
L5 weighted **−$0.0889/unit** across 224,688 residual units, with **36.2% in
positive-expectancy bands** (55–75c +0.0508, 75–90c +0.1382, >90c +0.0694).
Within the same 25–45c band L5 is −0.2487/unit, so PMUS is **1.63× worse in the
same band and 4.6× worse overall**.

Open: is the gap band composition, clip sizing, window length (15m vs his 5m
era), venue microstructure, or n=1 residual episode? **n=1 terminal residual
episode.** This is the highest-value open diagnostic and is NOT yet explained.

## THETA_001 — FROZEN
**θ=0.96 remains preregistered. The favourable θ=1.02 n=5 observation must not
cause selection or tuning.**

Recorded sweep (queue-aware, variant B, per window, n=5) — **observation only**:

| θ | q25 net | q50 net |
|---|---|---|
| **0.96 (frozen)** | **−0.5682** | **−1.6646** |
| 0.98 | +2.4305 | +4.3303 |
| 1.00 | +0.8916 | +2.0167 |
| 1.02 | **+4.3304** | **+7.6877** |
| 1.05 | +3.5620 | +6.6714 |

At n=2 the ordering was the reverse: θ=0.96 was the only positive setting. A
sign flip between n=2 and n=5 is the signature of noise, not of a better
threshold. Additionally, L5's own tolerance of >$1 pairs (30–45% of his matched
pairs, median cost 0.975) is a deliberate purchase of **incentives** with
guaranteed trading losses — a trade that is simply a loss while PMUS credited
incentives are $0 (PMUS_ECON_003). Loosening θ is therefore unsupported on two
independent grounds.

---

## Superseded conclusions

| superseded | by | why |
|---|---|---|
| ECONOMIC_COMPLETE beats/loses to B′ by the pre-audit numbers (EC net q25 +3.6830/window) | `1ea8760e` | The completion taker fee was subtracted twice in `net`. Sign of the B′-vs-EC delta reversed after the fix. |
| "EC carries zero residual, so it is not directionally exposed at settlement" | `3a92c583` | True only under the optimistic fill model. Under queue-aware fills 1830z ends 50 contracts net DOWN and loses the residual outright. EC does not guarantee a flat book. |
| Queue-ahead = whole-ladder `bid_shares`/`ask_shares` | `3a92c583` | Whole-ladder sum (~20,000) is not queue-ahead; the touch level holds ~100–1,600. Replaced by exact-price-level displayed quantity. |
| Pooled median completion edge ≈ 13.67¢ | `1ea8760e` | Was a median-of-medians. Pooled median over all 24 completion events is **8.57¢**. |
| "The completion edge is fee-safe pair economics" | `1ea8760e` | Paying n·(1−bid) and collecting n·$1 is identically n·(bid − basis): EC is **selling the aged leg into the bid**. Fresh two-sided pair cost is 1.01–1.03 at every completion instant, never below $1. The edge is directional carry. |
| ATOMIC_RECYCLE as a candidate state-consistency fix | `7c11bc23` | Falsified and no-op. No stale passive orders exist to cancel; residual is 100% from new post-completion decisions. |
| Queue-aware q25 −6.9442 / q50 −13.9588 (n=2) | `19955307` | n=2, both windows UP. At n=5 (3 UP / 2 DOWN): −0.5682 / −1.6646. |
| θ=0.96 is the only positive point of the sweep (n=2) | `19955307` | Reversed at n=5. Neither reading is acted on; θ stays frozen (THETA_001). |

## Commit lineage

`5edd2fab` runner import fix (authoritative pre-audit HEAD) →
`b62acf3e` tape scrape 1830z tail + 1845z →
`1ea8760e` **double-fee fix + conservation audit** →
`3a92c583` **level-accurate queue model, three fill bounds** →
`7c11bc23` completion state semantics, ATOMIC_RECYCLE falsified →
`19955307` 1900z/1915z/1930z scraped and scored, n=5 →
`0bf8f0ee` L5 advantage decomposition.
