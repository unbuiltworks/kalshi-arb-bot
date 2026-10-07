# REGIME_2026-08-19_ON — what changed, and why the newest cohort is negative

**Protocol** `REGIME-AUG19-ATTRIBUTION`
**Code** `analysis/l5/regime_aug19/regime_shift.py`
**Machine-readable** `analysis/l5/regime_aug19/REGIME_SHIFT.json`
**Tests** `tests/test_regime_aug19.py` (18 passing)

> **HISTORICAL REPLAY / DESCRIPTIVE MEASUREMENT.** Every dollar below describes
> the public Polymarket activity of trader `l5zn1bwom8etsk`. Nothing here is
> shadow, submitted, filled, settled or realized. **L5's cash is never our
> realized profit.** No network, no venue write, no order path, no credentials.
> Rebates and rewards are **$0.00 by construction** in every ex-incentive
> figure; the confirmed incentive records are reported in a separate block and
> never added to trading P&L.

**Population.** The out-of-sample holdout, unchanged:
`data/l5/raw/activity_gap.jsonl.gz` → **14,785 events / 685,430 fills / 107
observed days**, plus the forward secondary `data/l5/raw/sep/l5_sep23_25.json`
→ 255 events / 5,498 fills / 2 days. The module re-derives the frozen
`OOS_RESULT.json` BASELINE **exactly** per regime (matched, residual, fees,
net, turnover all to the cent —
`tests/test_regime_aug19.py::test_artifact_per_regime_cash_reproduces_the_frozen_oos_baseline`),
which is what licenses reading the observables on the same partition.

**Partition.** The repo's own change points `['2026-04-25','2026-06-23','2026-08-19']`
(`analysis/l5/CANON_REGIMES.md`, `scripts/l5_regime_detect.py`), imported from
`analysis.l5.residual_frontier.replay.CANON_CHANGE_POINTS`. **Nothing is
re-fit, moved or added.** The module contains no date literal at all; the test
parses the change points out of `CANON_REGIMES.md` and asserts them.

---

## 0. THE ANSWER, in one block

| | R1 pre-04-25 | R2 04-25→06-22 | R3 06-23→08-18 | **R4 08-19→on** | SEP 09-24/25 |
|---|---:|---:|---:|---:|---:|
| net ex-incentives $ | +1,649.55 | +8,333.74 | +14,545.54 | **−5,851.05** | −1,823.70 |
| **net bp of turnover** | **+179.2** | **+63.3** | **+41.4** | **−30.4** | **−272.1** |
| matched leg, bp | +428.5 | +397.3 | +176.9 | **+316.3** | +369.0 |
| **residual leg, bp** | **−249.3** | **−231.0** | **−67.5** | **−284.6** | **−609.9** |
| fees, bp | 0.0 | −103.0 | −68.1 | −62.1 | −31.2 |
| **CONFIRMED incentives, bp** | **+35.6** | **+27.5** | **+73.4** | **+163.4** | **+446.0** |
| CONFIRMED incentives $/day | 82 | 76 | 561 | **3,499** | 996 |
| net ex-inc $/day | +412 | +174 | +316 | **−650** | −912 |

Three facts, in order of decision-relevance:

1. **The sign flip is a residual-leg event, not a matched-economics event.**
   R3→R4 the matched leg **improved** by +139.3 bp and fees improved by
   +6.1 bp, while the residual leg deteriorated by **−217.1 bp**. Net moved
   −71.7 bp. The matched engine got *better*; the directional residual it
   leaves behind got 4.2× more expensive per dollar of turnover.
2. **It is BEHAVIOUR, not composition.** Shift-share on asset × window-length
   cells: of the −71.7 bp move, **composition contributes +1.5 bp** (i.e. the
   market-mix change was marginally *helpful*) and **behaviour −76.1 bp**
   (106% of the move; 125% in the settlement-backed stratum). The flip happens
   *inside* `btc_15m`, which is 77% of R4 turnover and traded continuously
   through all four regimes: +44.9 bp → **−23.4 bp**, residual −117.0 → −368.1 bp.
3. **It coincides with an incentive step, and L5 is not losing money.**
   Confirmed incentive yield went **+73.4 → +163.4 bp** of turnover (+90.0 bp)
   while trading net went **+41.4 → −30.4 bp** (−71.7 bp). He gave up 71.7 bp
   of trading edge and captured 90.0 bp more subsidy: net **+18.3 bp better
   off**. In dollars/day: $877 → $2,849 all-in. The recent "negative" is the
   *unsubsidised* slice of a trade that is, for him, more profitable than
   before.

**Therefore: a clone that does not receive L5's incentive stream should not
imitate recent L5.** Copying R4 behaviour buys −30.4 bp of turnover
(−$650/day at his scale, −$2,033 over the 2-day September sample at −303 bp)
and the thing that pays for it is a venue program we have no source-confirmed
entitlement to.

---

## 1. Metric-by-metric time series, all four regimes

PRIMARY holdout. `SEP` is the forward secondary (same regime, 2 days, n=255 —
read as corroboration of direction only).

### 1.1 Scale and throughput

| metric | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| events | 170 | 1,641 | 8,235 | **4,739** | 255 |
| fills | 10,399 | 158,978 | 349,160 | **166,893** | 5,498 |
| observed days | 4 | 48 | 46 | **9** | 2 |
| turnover $ | 92,041 | 1,316,566 | 3,517,048 | **1,927,289** | 67,028 |
| events / day | 42.5 | 34.2 | 179.0 | **526.6** | 127.5 |
| turnover / day $ | 23,010 | 27,429 | 76,458 | **214,143** | 33,514 |
| fills / event | 61.2 | 96.9 | 42.4 | **35.2** | 21.6 |
| notional / event $ | 541 | 802 | 427 | **407** | 263 |

**Throughput, not size, is the step.** Events/day ×2.94 and turnover/day ×2.80
at the boundary, with notional *per event* still falling. This is the
`FREQUENCY` axis moving hard — and it keeps moving after the 08-07 program
change rather than starting at it (see §3).

### 1.2 Clip behaviour — the MIX moved, and it is bimodal

| metric | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| fill size median | 20.00 | 18.81 | 15.00 | **13.16** | 20.00 |
| fill size mean | 18.93 | 17.59 | 20.33 | **24.28** | 26.54 |
| p75 | 27.63 | 20.51 | 30.00 | **40.00** | 46.06 |
| p95 | 40.00 | 45.00 | 53.81 | **80.00** | 61.08 |
| p99 | 40.00 | 60.00 | 80.00 | **80.00** | 100.00 |
| first-fill size median | 25.00 | 20.00 | 30.00 | **25.55** | 34.58 |

Share of **fills** by size bucket:

| bucket | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| <10 | 0.328 | 0.312 | 0.384 | **0.407** | 0.333 |
| 10–25 | 0.317 | 0.463 | 0.238 | **0.241** | 0.221 |
| 25–50 | 0.353 | 0.179 | 0.278 | **0.148** | 0.210 |
| 50–100 | 0.001 | 0.043 | 0.099 | **0.201** | 0.203 |
| 100–250 | 0.000 | 0.002 | 0.001 | **0.003** | 0.032 |

Share of **shares** by bucket (where the money is):

| bucket | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| <10 | 0.077 | 0.081 | 0.082 | **0.068** | 0.059 |
| 10–25 | 0.287 | 0.459 | 0.186 | **0.154** | 0.130 |
| 25–50 | 0.631 | 0.312 | 0.437 | **0.218** | 0.286 |
| 50–100 | 0.004 | 0.137 | 0.293 | **0.547** | 0.405 |
| 100–250 | 0.002 | 0.012 | 0.003 | **0.013** | 0.120 |

**The size mix moved, decisively, and the median moved the other way from the
mean.** More sub-10 clips *and* more 50–100 clips, at the expense of the 25–50
band: the share of shares in 50–100 roughly doubles (0.293 → 0.547) while the
25–50 band halves (0.437 → 0.218). So R4 is not "bigger clips" — it is a
**barbell**: many token clips plus a materially heavier large-clip leg. That
matters for §2.3, because it is the large residuals that lose.

### 1.3 Entry behaviour

| metric | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| first price mean | 0.4982 | 0.4962 | 0.5454 | **0.5243** | 0.5812 |
| first-buy-is-favourite | 0.453 | 0.446 | 0.618 | **0.622** | 0.706 |
| first TTE median (s) | 867 | 858 | 275 | **276** | 237 |

Entry is **flat** across the boundary. Whatever changed, it is not where in the
window he opens or which side he opens on. The big move in these was at
06-23 (the boundary the detector says the data insists on), not at 08-19.

### 1.4 Action mix and run lengths — essentially unchanged

| metric | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| P(EXTEND) | 0.6195 | 0.7184 | 0.7146 | **0.7060** | 0.6844 |
| P(SWITCH) | 0.1403 | 0.1104 | 0.1011 | **0.1030** | 0.1080 |
| P(COMPLETE) | 0.2239 | 0.1608 | 0.1608 | **0.1626** | 0.1611 |
| EXTEND / event | 37.9 | 69.6 | 30.3 | **24.9** | 14.8 |
| COMPLETE / event | 13.7 | 15.6 | 6.8 | **5.7** | 3.5 |
| same-side run, mean | 2.63 | 3.55 | 3.50 | **3.40** | 3.17 |
| same-side run, p90 | 5 | 8 | 8 | **8** | 7 |

**The per-decision action policy did not change at 08-19.** Three digits of
agreement on all four transition probabilities and on run length. This matches
`AUGUST8_PROGRAM_EVENT_STUDY.md`'s finding for the 08-07 event, extended to
the 08-19 boundary: the *policy* is stable, the *economics around it* are not.

### 1.5 Pair costs — the distribution, and the upper tail is where it lives

Pair cost = `VWAP_up + VWAP_down`, fee-exclusive, as the frozen replay
computes it.

| quantile | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| p10 | 0.8752 | 0.8797 | 0.8099 | **0.7456** | 0.8242 |
| p25 | 0.9261 | 0.9217 | 0.9144 | **0.8684** | 0.9006 |
| **median** | 0.9592 | 0.9577 | 0.9764 | **0.9609** | 0.9464 |
| **p75** | 0.9904 | 0.9918 | 1.0348 | **1.0593** | 1.0172 |
| p90 | 1.0478 | 1.0298 | 1.1705 | **1.2175** | 1.1320 |
| p95 | 1.0877 | 1.0585 | 1.2722 | **1.3221** | 1.3184 |
| p99 | 1.1651 | 1.1234 | 1.4703 | **1.4848** | 1.4306 |
| mean | 0.9632 | 0.9560 | 0.9820 | **0.9709** | 0.9588 |
| frac ≤ 0.96 | 0.503 | 0.516 | 0.418 | **0.497** | 0.559 |
| frac > 1.00 | 0.222 | 0.207 | 0.373 | **0.358** | 0.324 |
| **frac > 1.05** | 0.096 | 0.058 | 0.216 | **0.264** | 0.180 |
| **frac > 1.15** | 0.024 | 0.004 | 0.113 | **0.150** | 0.099 |

**The distribution did not shift — it spread.** Median *fell* 0.9764 → 0.9609
and the ≤0.96 share rose 0.418 → 0.497, while p75 rose 1.0348 → 1.0593, the
>1.05 share rose +22% and the **>1.15 share rose +33%** (0.113 → 0.150). Both
tails widened. The mean barely moved (0.982 → 0.971), which is exactly why the
brief insisted on the distribution: the mean hides this completely.

This reconciles fact 1 of §0: a cheaper *median* pair is the mechanism by which
matched-leg economics **improved** (+139 bp), and a fatter *upper* tail is the
same posture seen from the other side — legs that fill far from their
complement's price, i.e. the ones that end up as expensive residual.

### 1.6 Inventory and completion dynamics

| metric | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| completion rate | 0.982 | 0.994 | 0.729 | **0.743** | 0.435 |
| one-sided event frac | 0.018 | 0.006 | 0.271 | **0.257** | 0.565 |
| completion ratio, median | 0.920 | 0.906 | 0.702 | **0.603** | 0.000 |
| completion ratio, mean | 0.859 | 0.868 | 0.555 | **0.515** | 0.315 |
| **t-to-first-match median (s)** | 38 | 30 | 48 | **63** | 63 |
| t-to-first-match p90 (s) | 134 | 105 | 165 | **199** | 219 |
| t-to-first-match mean (s) | 62.0 | 45.6 | 68.4 | **93.8** | 92.6 |
| event span median (s) | 762 | 754 | 187 | **168** | 30 |
| peak \|residual\| median (sh) | 154.8 | 242.5 | 128.4 | **100.9** | 50.2 |
| peak \|residual\| mean (sh) | 171.1 | 276.7 | 166.3 | **164.7** | 136.4 |
| terminal \|residual\| median (sh) | 83.9 | 139.3 | 78.0 | **69.0** | 43.6 |
| terminal \|residual\| mean (sh) | 103.1 | 183.6 | 114.7 | **123.7** | 111.2 |
| terminal residual frac, mean | 0.141 | 0.132 | 0.445 | **0.485** | 0.686 |
| terminal residual frac, median | 0.080 | 0.094 | 0.298 | **0.397** | 1.000 |

**Completion got slower and less complete, in a shorter window.**
Time-to-first-match median +31% (48→63 s), p90 +21%, mean +37%, while the event
span median *shrank* 187→168 s. Completion *rate* is flat (0.729→0.743) — he
still touches both legs about as often — but the completion *ratio* falls
(median 0.702→0.603) and terminal residual fraction rises (0.445→0.485,
median 0.298→0.397). So: he reaches both sides, later, and finishes further
from flat.

Note the residual did **not** get much bigger in absolute shares (peak mean
166.3→164.7, flat; terminal mean 114.7→123.7, +7.8%). The residual **drag**
went 4.2×. The residual therefore got *worse*, not *larger* — §2.3.

### 1.7 Volatility — PROXY only, and the real thing is UNAVAILABLE

| metric | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| PROXY: price travel per fill, median | 0.195 | 0.153 | 0.162 | **0.211** | 0.171 |
| PROXY: within-event price range, median | 0.930 | 0.940 | 0.720 | **0.700** | 0.100 |

These are **L5's own fill prices**, an event-level descriptive, not a venue mid
and not the underlying, and not a decision-time feature. Travel per fill rises
+30% (0.162→0.211), which is weakly consistent with a faster-moving tape, but
this proxy also moves with his own quoting posture and cannot separate the two.

**Underlying realized volatility across regimes is UNAVAILABLE.**
`data/btc_spot_proxy/btc_1m.json` covers **2026-08-20 → 2026-08-25 only**, i.e.
entirely inside R4 (per-minute realized vol 5.4–10.9 bp/min over those five
days). No cross-regime volatility series can be formed, so "did volatility
change at the boundary?" is **unanswered, not answered negative**.

### 1.8 Composition — he did move markets, at token size

| | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| BTC share of events | 1.000 | 1.000 | 0.939 | **0.540** | 0.745 |
| **ETH share of events** | 0.000 | 0.000 | 0.061 | **0.460** | 0.255 |
| BTC share of turnover | 1.000 | 1.000 | 0.992 | **0.933** | 0.982 |
| **ETH share of turnover** | 0.000 | 0.000 | 0.008 | **0.067** | 0.018 |
| 15m share of events | 1.000 | 1.000 | 0.304 | **0.256** | 0.373 |
| 5m share of events | 0.000 | 0.000 | 0.696 | **0.744** | 0.627 |
| 15m share of turnover | 1.000 | 1.000 | 0.821 | **0.795** | 0.907 |

ETH goes 6.1% → 46.0% of **events** but only 0.8% → 6.7% of **turnover**: a 7.6×
breadth move financed at roughly token size, exactly the pattern
`AUGUST8_PROGRAM_EVENT_STUDY.md` predicted from the reward formula (`Q_normal`
is a *share of each market*, so adding a market pays linearly while adding size
saturates). Window-length mix barely moves. **Venue migration: UNANSWERABLE** —
both holdout sources are Polymarket-only harvests of one address, so "did he
move venues" cannot be asked of this data at all.

### 1.9 Maker/taker mix and spread — UNAVAILABLE, not unchanged

- **Maker/taker role: UNAVAILABLE.** Role requires `execution.isAggressor`
  from the data-api `/trades` shape. Neither holdout source carries it:
  `activity_gap.jsonl.gz` records hold exactly
  `{conditionId, eventSlug, outcome, outcomeIndex, price, side, size, slug,
  timestamp, transactionHash, type, usdcSize}`, and the September file adds
  only profile fields. `CANON_REGIMES.md` records the same gap. **Not
  inferred**, and the bare boolean `aggressor` is read nowhere
  (`tests/...::test_module_never_infers_a_liquidity_role` proves it at the AST
  level). The only role-bearing artifacts on disk are the
  `takerOnly=true/false` harvest pair `data/l5/raw/trades*.jsonl`, which span
  **2026-08-28 → 2026-08-31 and 2026-08-30 only** — inside R4, so they cannot
  form a cross-regime series either.
- **Spread: UNAVAILABLE.** Spread needs a resting book.
  `analysis/l5/BOOK_ACQUISITION.md` establishes that the only historical
  Polymarket L2 anywhere on disk or in git history is the Zenodo capture
  **2026-08-21 → 2026-08-24** — 3.5 of 166 days (2.1%), *entirely inside R4*.
  Pair cost (§1.5) is reported in its place and is a different quantity: it is
  his two execution VWAPs summed, not a contemporaneous bid/ask.
- Also UNAVAILABLE, per `CANON_REGIMES.md` §0: order placements and cancels,
  hence patience-in-book and quote-width — the very things a reward-driven
  explanation would most want to measure directly.

---

## 2. Composition vs behaviour

### 2.1 Shift-share, asset × window-length cells

`d_total = d_composition + d_behaviour + d_interaction`, in bp of turnover,
where composition holds the old within-cell rates and applies the new mix.

**POOLED / unbiased stratum:**

| transition | d_total | composition | behaviour | interaction |
|---|---:|---:|---:|---:|
| R1 → R2 | −115.9 | 0.0 | **−115.9** | 0.0 |
| R2 → R3 | −21.9 | −11.3 | **−18.4** | +7.8 |
| **R3 → R4** | **−71.7** | **+1.5** | **−76.1** | +2.9 |

**SETTLEMENT-CONDITIONAL (REDEEM-backed) stratum:**

| transition | d_total | composition | behaviour | interaction |
|---|---:|---:|---:|---:|
| R1 → R2 | −118.9 | 0.0 | **−118.9** | 0.0 |
| R2 → R3 | +43.1 | −11.3 | −19.8 | +74.2 |
| **R3 → R4** | **−92.8** | **+10.1** | **−116.5** | +13.6 |

**Behaviour explains 106% (pooled) / 125% (conditional) of the R3→R4 move;
composition contributes a small positive.** Entering ETH and shifting slightly
toward 5m was, on R3's own within-cell rates, mildly *helpful*. The decay is
not a market-family artifact.

### 2.2 The decisive within-cell check: `btc_15m`

`btc_15m` is 82.1% of R3 turnover and 77.0% of R4 turnover, and L5 traded it in
every one of the four regimes.

| cell | R2 net bp | R3 net bp | **R4 net bp** | R3 resid bp | **R4 resid bp** | R3 match bp | **R4 match bp** |
|---|---:|---:|---:|---:|---:|---:|---:|
| **btc_15m** | +63.3 | +44.9 | **−23.4** | −117.0 | **−368.1** | +198.3 | **+361.5** |
| btc_5m | — | +20.7 | **−95.8** | +152.8 | **−142.0** | +81.2 | +263.6 |
| eth_15m | — | — | **−53.5** | — | +350.1 | — | −210.5 |
| eth_5m | — | +116.5 | **+111.8** | +298.9 | +317.9 | +31.5 | +6.3 |

Three of four cells go negative, including the dominant continuously-traded
one. The one cell that stays positive, `eth_5m`, is 4.2% of turnover. **Same
market, same window length, same action policy — worse result.** That is a
behaviour/environment change, not a mix change.

### 2.3 What exactly got worse: residual exposure × residual quality

Normalised by turnover so the ×2.8 scale change cannot confound. `resid_cost_bp`
and `resid_entry_vwap` are contemporaneous; `resid_payout_bp` and
`share_weighted_hit_rate` are settlement-derived **outcomes**.

**POOLED / unbiased:**

| metric | R1 | R2 | R3 | **R4** | SEP |
|---|---:|---:|---:|---:|---:|
| residual shares per $ turnover | 0.190 | 0.229 | 0.269 | **0.304** | 0.423 |
| residual **cost** bp of turnover | 689.2 | 819.7 | 1,362.0 | **1,263.7** | 1,569.6 |
| residual **payout** bp of turnover | 439.9 | 588.7 | 1,294.5 | **979.1** | 959.7 |
| residual **net** bp | −249.3 | −231.0 | −67.5 | **−284.6** | −609.9 |
| residual entry VWAP (his own price) | 0.362 | 0.358 | 0.507 | **0.416** | 0.371 |
| share-weighted hit rate (outcome) | 0.231 | 0.257 | 0.482 | **0.322** | 0.227 |
| **hit − entry price** | −0.131 | −0.101 | **−0.025** | **−0.094** | −0.144 |

**SETTLEMENT-CONDITIONAL (REDEEM-backed):**

| metric | R1 | R2 | R3 | **R4** |
|---|---:|---:|---:|---:|
| residual cost bp | 684.8 | 818.1 | 1,306.6 | **1,225.3** |
| residual payout bp | 440.1 | 588.8 | 1,303.1 | **983.6** |
| residual net bp | −244.8 | −229.3 | **−3.5** | **−241.7** |
| hit − entry price | −0.130 | −0.100 | **−0.001** | **−0.085** |

The mechanism, stated plainly:

- **Residual exposure grew monotonically all four regimes** (shares per dollar
  of turnover 0.190 → 0.229 → 0.269 → 0.304; cost basis at risk 689 → 820 →
  1,362 → 1,264 bp). This is the one metric that decays monotonically with
  net, and it is the standing risk question in `CLAUDE.md` showing up in L5's
  own tape.
- **Residual quality (hit minus his own entry price, share-weighted) was
  −13.0, −10.0, −0.1, −8.5 pp.** R3 is the outlier: in R3 the residual settled
  essentially *at fair value*, so a 1,307 bp exposure cost him only 3.5 bp. R4
  reverts to the long-run −8.5 pp — and at 1,225 bp of exposure that reversion
  alone is worth ≈ −240 bp.
- Residual cost per dollar of turnover is **flat** R3→R4 (−6%); the payout
  collapses **−24%**. He put the same money at directional risk and got a
  quarter less back.
- Note the asymmetry between event-weighted and share-weighted hit rates: the
  *unweighted* per-event residual hit rate in R4 exceeds his entry price
  (0.538 vs 0.431), while the *share-weighted* rate is 8.5 pp below it. **The
  big residuals are the losers.** Combined with §1.2's heavier 50–100 clip
  band, that is a size-conditional adverse-selection signature.

**So the honest reading of "monotone decay then sign flip" is not one decay.**
It is two superimposed series: matched-leg economics decaying monotonically
R1→R3 (428 → 397 → 177 bp) against monotonically rising residual exposure, with
**R3's headline positive held up by an unusually benign residual outcome** that
did not repeat. R4 is the residual reverting to normal at the largest exposure
yet, partly offset by a genuinely better matched leg.

### 2.4 The self-selection stratum is NOT the explanation

Honouring the OOS lesson — a `REDEEM` exists only if something was redeemable,
and 99.61% of one-sided no-REDEEM events lost:

| stratum | R3 | **R4** |
|---|---:|---:|
| REDEEM coverage | 0.907 (768 uncovered) | **0.886 (538 uncovered)** |
| SETTLEMENT_CONDITIONAL net $ | +37,781.88 | **+2,945.00** |
| SETTLEMENT_CONDITIONAL net bp | +108.1 | **+15.4** |
| NO_REDEEM (loss-enriched) net $ | −23,236.34 | **−8,796.06** |
| POOLED net $ | +14,545.54 | **−5,851.05** |

The loss-enriched no-REDEEM stratum is **smaller in R4**, in both count (768 →
538) and dollars (−23,236 → −8,796). **The degradation is fully visible inside
the source-backed stratum** (+108.1 → +15.4 bp; residual −3.5 → −241.7 bp), so
it is not an artifact of unredeemed losers. The three strata are reported
separately throughout and are never averaged (`REGIME_SHIFT.json`
`outcome_pnl.by_stratum`). The September secondary is the one place where
coverage is materially worse (0.788, 54 of 255 uncovered) and its conditional
figure (−98.0 bp) is much milder than its pooled figure (−272.1 bp) — read both,
never the average.

---

## 3. Incentives — and where the boundary actually sits

Confirmed records only. A dollar is counted iff a record exists; nothing is
modelled, imputed, or inferred per-contract. **These are L5's credits and are
not evidence that equivalent incentives would accrue to us.**

| regime | MAKER_REBATE | TAKER_REBATE | REWARD | total $ | $/day | bp of turnover |
|---|---:|---:|---:|---:|---:|---:|
| R1 | 4 / $327.81 | 0 / $0 | 0 / $0 | 327.81 | 82 | 35.6 |
| R2 | 49 / $3,615.23 | 1 / $9.86 | 0 / $0 | 3,625.09 | 76 | 27.5 |
| R3 | 45 / $14,383.13 | 60 / $4,441.63 | 8 / $6,976.35 | 25,801.11 | 561 | 73.4 |
| **R4** | **9 / $13,022.79** | **9 / $2,486.92** | **9 / $15,979.54** | **31,489.25** | **3,499** | **163.4** |
| SEP | 3 / $2,838.32 | 3 / $151.16 | **0 / $0** | 2,989.48 | 996 | 446.0 |

Cadence: **173 of 194** records land at hour 0 UTC — the daily-midnight payout
the published program terms specify. First-ever records: MAKER_REBATE
2026-04-21, TAKER_REBATE 2026-06-20, **REWARD 2026-08-08**.

### 3.1 Does the boundary coincide with an incentive-program change? Not exactly — and the mismatch is informative

The documented venue change is **2026-08-07**: Polymarket moved crypto up/down
markets to TWAP settlement and attached a **$1,000,000 August liquidity-reward
pool**, scored on *resting, two-sided, per-minute-sampled* quotes
(`analysis/l5/AUGUST8_PROGRAM_EVENT_STUDY.md`, with the published scoring
formula). L5's first REWARD credit is 2026-08-08. The detected boundary is
**2026-08-19 — twelve days later.** The repo's own detector *tested* a break at
2026-08-08 and **rejected it** (gain 245.3, p = 0.01995 vs α = 0.01;
`CANON_REGIMES.md` change-point trace). I do not move the boundary. But the
near-miss is worth recording, because the metric that flips the sign starts
deteriorating at the program date, not at the boundary.

A **disclosed diagnostic split** of the canonical third regime at the published
program date (the canonical partition is untouched; A + B = R3 exactly, C = R4
exactly):

| window | days | events | turnover $ | net bp | matched bp | **residual bp** | fees bp | net ex-inc $/day | **incentive bp** | incentive $/day |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **A** R3 before 08-07 | 35 | 4,883 | 1,818,942 | **+54.8** | +158.9 | **−11.1** | −93.1 | +285 | 42.7 | 222 |
| **B** R3 on/after 08-07 | 11 | 3,352 | 1,698,107 | **+27.0** | +196.2 | **−127.9** | −41.4 | +417 | 106.2 | 1,639 |
| **C** R4 (08-19 on) | 9 | 4,739 | 1,927,289 | **−30.4** | +316.3 | **−284.6** | −62.1 | **−650** | 163.4 | **3,499** |

**The residual drag ramps −11 → −128 → −285 bp and the incentive yield ramps
43 → 106 → 163 bp, in lockstep, beginning at the documented program date.**
The 08-19 boundary is where that ramp crosses zero and where throughput takes
its second step (events/day 179 → 527), not where the cause begins. On this
evidence the causal date is **2026-08-07**, and the canonical 08-19 change
point is the point at which the response became a distinguishable operating
regime.

### 3.2 Daily shape across the boundary (PRIMARY)

| day | regime | events | net bp | matched bp | residual bp | pair p90 | compl. | t2match med | incentive $ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 08-14 | R3 | 426 | −27.2 | 168.7 | −169.2 | 1.303 | 0.601 | 51.5 | 1,076 |
| 08-15 | R3 | 289 | +8.4 | 202.4 | −164.1 | 1.130 | 0.740 | 36 | 1,648 |
| 08-16 | R3 | 37 | +309.3 | 73.1 | +265.5 | 1.145 | 0.676 | 36 | 1,836 |
| 08-17 | R3 | 425 | +56.4 | 176.4 | −68.5 | 1.192 | 0.835 | 49 | 344 |
| 08-18 | R3 | 662 | +89.2 | 189.4 | −32.9 | 1.179 | 0.884 | 46 | 3,259 |
| **08-19** | **R4** | 571 | +15.3 | 237.1 | −165.4 | 1.208 | 0.793 | 53 | 4,742 |
| 08-20 | R4 | 721 | +4.8 | 265.8 | −194.0 | 1.230 | 0.786 | 72 | 4,186 |
| 08-21 | R4 | 698 | −86.2 | 279.5 | −308.1 | 1.210 | 0.726 | 69 | 3,814 |
| 08-22 | R4 | 197 | −27.1 | 384.5 | −349.3 | 1.184 | 0.736 | 75 | 3,696 |
| 08-24 | R4 | 476 | −31.7 | 418.6 | −394.1 | 1.176 | 0.725 | 66 | 0 |
| 08-25 | R4 | 702 | +43.2 | 426.9 | −314.1 | 1.213 | 0.751 | 58 | 2,891 |
| 08-26 | R4 | 554 | −125.0 | 321.0 | −374.9 | 1.192 | 0.700 | 52 | 4,222 |
| 08-27 | R4 | 654 | −59.9 | 310.1 | −309.7 | 1.245 | 0.725 | 66 | 3,062 |
| 08-28 | R4 | 166 | −23.2 | 256.4 | −232.1 | 1.338 | 0.681 | 51 | 3,778 |

**Shape finding, reported not re-cut:** the throughput step **leads** the
boundary (events 289 → 425 → 662 on 08-15/17/18, i.e. inside R3), and the
residual step **lags** it (−165, −194 on 08-19/20, then a sustained −308 to
−394 from 08-21). Matched bp ramps up monotonically 237 → 427 across the same
days. So at the daily grain this is a **two-to-three-day transition centred on
08-20/21**, not a single-day discontinuity at 08-19, embedded in a ramp that
began 08-07. The canonical change point remains the repo's; this is the shape
of the thing it found. Note 08-23 and 08-29→31 are absent (holdout span and
leak-guard exclusions), so no day-level claim rests on them.

### 3.3 What the September secondary adds — and subtracts

The forward holdout (2026-09-24/25, same regime, n=255) agrees on sign and is
worse: −272.1 bp pooled, −98.0 bp conditional. Two differences matter:

- **REWARD records are ZERO in September** (3 MAKER_REBATE + 3 TAKER_REBATE,
  $2,989.48 over three credit days). The August $1M pool was an August
  program. The reward leg that paid for R4's posture is **gone**, while the
  negative trading leg **persists**.
- Throughput collapsed: turnover/day $214,143 → $33,514 (−84%), completion rate
  0.743 → 0.435, terminal residual fraction median 0.397 → **1.000**.

Two readings, and the data cannot separate them on 2 days / 255 events: either
he wound the posture down when the pool ended, or September is simply a small,
unrepresentative sample. Either way, **the September evidence does not support
"the subsidy makes it fine" as a forward statement**, because the subsidy
component we can confirm there is maker rebate only.

---

## 4. Ranked explanation of the sign flip, with effect sizes

R3 → R4, in bp of turnover. The accounting identity is exact:
**−71.7 = +139.3 (matched) − 217.1 (residual) + 6.1 (fees)**.

| rank | explanation | effect size | composition or behaviour | confidence |
|---|---|---|---|---|
| **1** | **Residual-leg quality reverted to its long-run adverse level at the largest exposure yet.** Share-weighted hit minus entry price −0.1 pp → −8.5 pp (conditional), on residual cost basis of 1,225 bp of turnover. R1/R2 ran at −13.0/−10.0 pp, so R3 was the anomaly, not R4. | **−217.1 bp**, i.e. 303% of the move (offset by matched) | **behaviour × environment**; exposure growth is behaviour, the reversion is environment | **high** — exact, reproduces the frozen OOS to the cent, visible in the source-backed stratum alone |
| **2** | **Residual exposure grew monotonically across all four regimes** — 0.190 → 0.229 → 0.269 → **0.304** residual shares per $ of turnover. This is the amplifier that turns a −8.5 pp quality slip into −240 bp. | exposure +13% R3→R4, **+60% since R1** | **behaviour** | **high** — monotone, large n |
| **3** | **Pair-cost dispersion widened at both tails**: median 0.9764 → 0.9609 (matched leg better, +139.3 bp) while frac>1.05 0.216 → 0.264 and frac>1.15 0.113 → **0.150**. One posture, two signs. | matched **+139.3 bp**; upper-tail mass +33% | **behaviour** | **high** |
| **4** | **Incentive capture roughly doubled per dollar of turnover**, 73.4 → 163.4 bp (+90.0 bp), REWARD $152/day → $1,775/day. This is the payoff that makes rank 1–3 rational for L5 and is the single most decision-relevant fact for a clone. | **+90.0 bp**, 125% of the trading loss | **external payoff change** | **high** on the records; **medium** on causality — the detected boundary is 12 days after the documented 08-07 program date, and the detector rejected 08-08 at α=0.01 |
| **5** | **Throughput ×2.8–2.9** (events/day 179 → 527; turnover/day $76k → $214k) with notional per event *falling* — breadth, which is the gradient of the published reward formula. | ×2.80 turnover/day | **behaviour** (composition in the breadth sense) | **high** |
| **6** | **Completion got slower and less complete**: t-to-first-match median 48 → 63 s (+31%), p90 165 → 199 s, completion ratio median 0.702 → 0.603, terminal residual frac 0.445 → 0.485 — in a *shorter* window (span 187 → 168 s). The proximate cause of the residual sitting on the book longer. | +31% patience, −14% completion ratio | **behaviour** | **high** |
| **7** | **Clip MIX became a barbell** — share of shares in the 50–100 band 0.293 → **0.547**, 25–50 halved, median clip *down* 15.0 → 13.2. Relevant because share-weighted residual loses while event-weighted does not. | 50–100 band share ×1.87 | **behaviour** | **high** on the mix; **medium** on the causal link to residual loss |
| **8** | **Market-family composition** — ETH 6.1% → 46.0% of events but 0.8% → 6.7% of turnover; 5m 69.6% → 74.4% of events. | **+1.5 bp pooled / +10.1 bp conditional**, i.e. ~2% of the move, *favourable* | **composition** | **high** — and it is a non-explanation |
| 9 | Fees | **+6.1 bp** (−68.1 → −62.1) | neutral | high |
| — | Volatility of the underlying | **UNAVAILABLE** cross-regime (5 days of BTC 1m, all inside R4); L5-fill proxy travel/fill +30% | — | — |
| — | Maker/taker mix | **UNAVAILABLE** (no `execution.isAggressor` in either source) | — | — |
| — | Spread / quote width / cancels | **UNAVAILABLE** (3.5 days of L2, all inside R4; no order-event feed) | — | — |
| — | Venue migration | **UNANSWERABLE** from these sources (Polymarket-only harvests of one address) | — | — |

### Composition vs behaviour, final answer

**Behaviour, decisively.** 106% of the pooled move and 125% of the conditional
move is within-cell; composition is +1.5 / +10.1 bp, i.e. *helpful*. The flip
occurs inside `btc_15m` — 77% of R4 turnover, traded in all four regimes —
which goes +44.9 → −23.4 bp with its residual going −117 → −368 bp. Three of
four asset × duration cells go negative.

### Does it coincide with an incentive-program change?

**Partially, and the mismatch is the finding.** The documented change is
2026-08-07 (TWAP + $1M August pool, rewards for *resting two-sided* quotes
sampled per minute); the canonical boundary is 2026-08-19. The repo's own
detector tested 08-08 and rejected it at α=0.01. But every metric that drives
the sign flip ramps monotonically across A (pre-08-07) → B (08-07..08-18) → C
(08-19 on): residual −11 → −128 → −285 bp, incentives 43 → 106 → 163 bp. The
best-supported causal story is therefore:

> **The 08-07 program change paid L5 for a quoting posture whose by-product is
> an adversely-selected directional residual. He adopted it, scaled breadth
> into it, and by 08-19 the posture's trading cost exceeded its trading
> benefit — while its subsidy exceeded both.**

The signature matches the mechanism rather than merely co-occurring with it:
resting both sides tightly produces *cheaper matched pairs* when both legs fill
(matched +139 bp, median pair down) and an *adversely-selected residual* when
only one leg fills (the side the market moved away from) — which is exactly the
observed pair-cost double-tail plus residual-quality collapse. The action
policy (P(EXTEND)/P(SWITCH)/P(COMPLETE), run lengths, entry price, entry TTE)
is flat to three digits across the boundary, so the change is in *quoting and
completion*, not in the decision alphabet.

**What would settle it, and is currently UNAVAILABLE:** order placements and
cancels (quote width, resting time, two-sidedness) and a contemporaneous book.
Without those, the mechanism above is a strongly-consistent inference, not a
measurement. Stated as such.

---

## 5. Should a clone imitate recent L5?

**No — not the R4 posture, and not on the strength of the pooled number.**

1. **Ex-incentive, R4 is the only forward-relevant cohort and it is
   −30.4 bp / −$650 per observed day (pooled), −$912/day on the September
   secondary.** The pooled +$174.56/observed-day figure spans R1–R4 and must
   not be advertised as current expectancy; §0 of this document is the current
   expectancy.
2. **The thing that makes R4 rational for L5 is a venue program, not an edge.**
   +163.4 bp of confirmed incentive against −30.4 bp of trading. Per
   `CLAUDE.md`: do not assume a rebate or liquidity reward until it is credited
   and source-accounted, and do not unlock mature incentive-era behaviour
   because L5 used it later. R4 *is* mature incentive-era behaviour.
   **It stays locked.**
3. **The one component that improved is the one our spring discipline already
   targets.** Matched-leg economics went +176.9 → +316.3 bp, driven by a
   *cheaper median pair* (0.9764 → 0.9609), which sits inside the `<= 0.96`
   spring target. The component that broke it is the residual we already bound.
   So the correct lesson from R4 is **"the matched leg is real; the residual is
   what kills it"**, which is the existing residual-frontier finding reaching
   the same conclusion from the other direction.
4. **But the residual frontier says we cannot cheaply separate them.** The OOS
   maximum exchange rate between avoided residual loss and sacrificed matched
   economics is 1.1094, and the pre-committed candidates sit at 1.01–1.04. So
   no contemporaneous residual cap buys R4's matched leg without its residual
   leg at a worthwhile rate. **R4 is not a regime a residual cap rescues.**
5. **Imitable, if anything, is R3's early sub-window A** (pre-08-07): +54.8 bp,
   residual −11.1 bp, incentive yield only 42.7 bp — a regime whose positive
   does not depend on a reward pool. Caveat loudly: A's residual is near-fair
   *in outcome*, and R1/R2/R4 all ran −8 to −13 pp of share-weighted residual
   adverse selection, so A's residual benignity is itself a candidate luck
   artifact on 35 days. It is a hypothesis for a pre-registered test, not a
   target.
6. **Residual exposure per dollar of turnover is the single monotone series in
   this whole document** (0.190 → 0.229 → 0.269 → 0.304). It is the risk bound
   `CLAUDE.md` already mandates, and L5's own tape prices its drift: +60% of
   exposure growth converted a benign residual into the thing that flipped his
   sign.

**Nothing here changes the live probe envelope.** No action model, residual-cap
grid, scorecard or execution path was read for tuning or modified by this work.

---

## 6. Provenance, limits, and what is UNAVAILABLE

- **Regime assignment** — the repo's own change points, imported, asserted
  against `CANON_REGIMES.md` by test, agreeing day-for-day with
  `replay._regime`. No change point fitted, moved or added; the module contains
  no date literal.
- **No hindsight in any contemporaneous metric** — `contemporaneous_metrics()`
  takes only `EventObservables`, a frozen `__slots__` dataclass whose field set
  is exactly `CONTEMPORANEOUS_FIELDS` and which **has no settlement field**.
  Settlement lives on a separate `EventOutcome`. Proved structurally, at the
  AST level (neither `contemporaneous_metrics` nor `_observe` may even *name* a
  settlement field or the outcome type), and behaviourally: flipping every
  REDEEM to the opposite outcome, and deleting every REDEEM, leaves every
  contemporaneous metric byte-identical, while the outcome block moves.
- **Cash labelling** — every cash figure is tagged `SETTLEMENT_CONDITIONAL`
  (REDEEM-backed, upward-biased by self-selection), `NO_REDEEM` (loss-enriched)
  or `POOLED` (unbiased, absent REDEEM scored $0). The three are never
  averaged. Rebates/rewards are $0 in every ex-incentive figure and are
  reported CONFIRMED-ONLY in their own block.
- **UNAVAILABLE, not unchanged:** maker/taker role (no `execution.isAggressor`
  in either holdout; the bare boolean `aggressor` is read nowhere); spread and
  quote width (3.5 days of L2, all inside R4); order placements and cancels;
  underlying volatility across regimes (5 days of BTC 1m, all inside R4);
  venue composition (single-address Polymarket harvests only).
- **Weak-evidence cohorts:** R1 is 170 events / 4 days in this holdout; R4 is
  9 observed days; the September secondary is 2 days / 255 events and its one
  low-volume day (08-16-style, 2 events) should not be read as a data point.
  08-23 and 08-29→31 are absent from the holdout.
- **Market-family scope:** the population is the OOS adapter's —
  `<asset>-updown-<5m|15m>-<epoch>` only. 3,245 records on bare `conditionId`s
  were excluded rather than given an invented `tte_s`, as in the frozen OOS.
