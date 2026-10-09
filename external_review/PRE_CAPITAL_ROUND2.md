# Pre-capital validation, round 2 — items 1–7

Scripts: `../scripts/D1_overlay_causality.py`,
`../scripts/D2_early_window_gate.py`.
Results: `D1_OVERLAY_CAUSALITY.json`, `D2_EARLY_WINDOW_GATE.json`.

**L5 is not closed. The two-mode finding is preserved as a leading
hypothesis. The Kalshi live probe is NOT recommended — my own test of it
came back negative. Live trading remains disarmed; collectors untouched.**

Two of my earlier claims fail here. Both are corrected in place.

---

## Item 1 — Kalshi authoritative settlement: tested, still blocked

I tested rather than asserted this time:

| host | result |
|---|---|
| `api.elections.kalshi.com/trade-api/v2/exchange/status` | **CONNECT tunnel failed, 403** |
| `demo-api.kalshi.co/trade-api/v2/exchange/status` | **CONNECT tunnel failed, 403** |
| `data-api.polymarket.com/trades` | **CONNECT tunnel failed, 403** |

The environment's network policy denies these hosts. **No authoritative
settlement validation is possible from this container**, so the
last-print proxy and its measured uncertainty stand unchanged:

* second independent proxy (final BBO mid) agrees on **182 of 184 (98.91%)**
* implied error ~1%; the Track B negative verdict survives to **5%** error
  and breaks at 10%

That 5-point margin remains the weakest number in the Kalshi work, and it
cannot be improved without the authoritative feed.

## Item 2 — the directional-overlay claim FAILS its placebo test

I previously reported that removing L5's taker fills costs **+2.224 per
window** and called the late aggressive mode a profitable overlay. **That
reading does not survive.**

Removal-of-trades is confounded three ways: taker fills are systematically
late, large, and additional volume. Three placebos remove the same number
of fills per window on each of those axes:

| variant | mean P&L effect | CI95 | mean residual effect |
|---|---|---|---|
| **REAL — remove taker** | **+2.287** | **[−0.150, +4.801]** | +73.3 |
| P_SIZE — remove N largest | +1.618 | [−1.236, +4.470] | +69.1 |
| P_LATE — remove N latest | +0.659 | [−1.679, +2.906] | +82.4 |
| P_RANDOM — remove N maker | −0.576 | [−4.220, +3.051] | +5.5 |

**Verdict: NOT AGGRESSION-SPECIFIC.** The real removal's CI overlaps every
placebo's, and under this stricter window filter (≥4 fills, settlement
known, n=1,359) **the real effect's own CI now spans zero.** Removing the
largest fills or the latest fills produces a statistically
indistinguishable effect.

The residual result is the same story: removing late fills (+82.4) or
large fills (+69.1) moves the residual as much as removing taker fills
(+73.3). **"Taker fills increase the residual" is really "late and large
fills increase the residual"**, which is a near-tautology given that is
how taker fills are identified.

**Retracted:** the claim that the late aggressive mode is a demonstrated
profitable directional overlay. It is not demonstrated. It remains a
hypothesis.

## Item 3 — separate mode economics, from actual fills

This is the part that does not depend on a counterfactual. Over the 72,550
labelled fills (2026-08-28..31), residual lots attributed FIFO to the mode
that bought them:

| | TAKER_OBSERVED | MAKER_INFERRED |
|---|---|---|
| fills | 11,810 | 49,929 |
| spend | $204,957 | $452,190 |
| contracts | 406,321 | 966,240 |
| **mean price** | **0.5044** | **0.4680** |
| residual contracts created | 143,582 | 75,656 |
| residual that **won** | 71,997 | 9,346 |
| residual that **lost** | 71,584 | 66,309 |
| **residual win rate** | **50.1%** | **12.4%** |
| **residual settlement P&L** | **+$2,413** | **−$5,628** |

Matched book across the same period: **677,011 pairs, +$6,077 locked**
(settlement-independent, and not attributable to one mode — a pair needs
one unit from each leg, often bought in different modes).

**The striking result is the residual win rates.** The passive residual
loses **87.6%** of the time; the aggressive residual is a **coin flip**.
That is a clean signature of adverse selection on the passive side: when
L5 rests and keeps getting filled on one leg, it is because the market is
running away from that leg — so the inventory it is left holding is the
losing side.

So the honest decomposition over those four days is:

* matched book **+$6,077**
* passive residual **−$5,628**
* aggressive residual **+$2,413**

The profit is in the matched book. The passive mode's residual very nearly
cancels it. The aggressive residual is roughly fair, not a profit engine.

**Incentives** cannot enter this decomposition: MAKER_REBATE,
TAKER_REBATE and REWARD records carry an empty slug, so they are
account-level only and unattributable to a mode or a window.

## Item 4 — held-out mode comparison, and its confound

Mode labels exist for four days, so a proxy was calibrated on the 72,550
real labels: `size ≥ 15 AND seconds_to_settlement < 300 → late-aggressive`.
**That proxy is weak — precision 0.505, recall 0.667, F1 0.575**, barely
better than chance at identifying mode.

| policy | windows | mean P&L | CI95 | total |
|---|---|---|---|---|
| **BOTH MODES** | 17,042 | **4.161** | [3.266, 5.169] | $70,917 |
| EARLY PASSIVE only | 17,042 | 1.994 | [1.279, 2.624] | $33,980 |
| LATE AGGRESSIVE only | 17,042 | 2.167 | [1.378, 2.924] | $36,938 |

BOTH's lower bound (3.266) exceeds both single modes' upper bounds, and it
holds in every month from April onward.

**But this is confounded in exactly the way item 2 is, and I will not
present it as a mode result.** "BOTH" uses every fill; each single-mode
policy uses a subset. Removing fills removes pairing opportunities, so
BOTH wins mechanically because it has more fills to pair — not necessarily
because the modes are complementary. **Combined with the weak proxy, item 4
does not establish that the two-mode combination beats either mode alone.**
A valid test needs a volume-matched control, which this design does not
have.

## Item 5 — reviewing my own proposed probe: DO NOT RUN IT

I proposed a Kalshi early-window passive probe with expected value ≈
−$24, justified as buying execution data. Two reasons that was wrong, and
I tested the second rather than arguing it:

**(a) Negative expected value is not redeemed by data generation.** A
probe should test a configuration with plausibly positive expectancy. Mine
tested a configuration already measured as negative.

**(b) The early-window restriction — the core of my proposed design — does
not rescue Kalshi.** I had never tested it. I have now, across 40
configurations (5 time restrictions × 4 thresholds × 2 modes):

| best configurations | both legs | engaged | mean P&L | CI95 |
|---|---|---|---|---|
| ttc>420s, θ=0.50, AGG | 0.297 | 0.833 | **−0.0644** | [−0.105, −0.023] |
| ttc>600s, θ=0.50, AGG | 0.229 | 0.464 | −0.0654 | [−0.093, −0.040] |
| ttc>420s, θ=0.45, AGG | 0.188 | 0.823 | −0.0691 | [−0.115, −0.023] |

**Positive configurations found: NONE.** All 40 negative; the best has its
whole CI below zero. Restricting to the early window improves the result
slightly (−0.064 against −0.081 unrestricted) but nowhere near zero.

**Recommendation: do not run the probe.** There is no
historically-supported configuration for it to test. Running it would
purchase execution data at a measured negative expectancy, which is what
you flagged and you were right.

## Item 6 — risk envelope, reconciled

Had the probe gone ahead, my earlier "$250 stop → ≤$251 loss" was
**incomplete**. It counted only realized P&L. The full envelope:

| component | value | why it was missing |
|---|---|---|
| max simultaneous **working** exposure | 2 markets × 2 legs × 1 × $0.45 = **$1.80** | quotes that can all fill at once |
| **unresolved** positions | Kalshi 15m windows settle after close, so with 2 concurrent markets and settlement lag, **up to ~4 markets** hold premium at once ≈ **$3.60** | a realized-P&L stop cannot see these |
| **outstanding orders** during cancellation | post-only quotes keep filling for the cancel round-trip (~1–2s) ≈ **1 further market, $0.90** | assumed instantaneous stop |
| **loss before the stop takes effect** | realized stop fires at −$250, then up to **$4.50** more arrives from unresolved + in-flight | — |
| **true maximum loss** | **≈ $254.50**, not $251 | — |
| max capital **locked** (Kalshi collateralises both legs) | ≈ **$3.60** at peak | not a loss, but it is capital |

**The design flaw that matters more than the number:** a stop on *realized*
P&L is the wrong stop for this strategy, because the exposure lives in
unresolved positions it cannot observe. A correct stop would trigger on
`realized + unrealized-at-mark`, or more simply on a **fill count cap**
(e.g. halt after 200 filled legs regardless of P&L), which is observable
instantly and needs no mark.

## Item 7 — the external research package: files and workflow

**Directly accessible** at `/home/user/kalshi-arb-bot/export/package/`
(inside the working directory, so openable from the app), and published as
orphan branch **`l5-blind-package`** with no ancestry to any analysis
branch. 118 files, ~115 MB, `SHA256SUMS` written, all integrity checks
passing.

### Grok workflow — repository route

1. Paste into Grok:
   > Read only the `l5-blind-package` branch of `unbuiltworks/kalshi-arb-bot`.
   > Do not read any other branch, the commit history, the issues or the
   > pull requests — they contain a prior analysis of this same data.
   > Start with `BRANCH_SCOPE.md`.
2. Then paste the full text of **`PROMPT_GROK.md`** in the same message.
3. Point it at plain-text data: `plain/l5_window_summary.csv` (all 17,046
   windows), `plain/l5_fills_sample.csv` (16,964 fills / 370 whole
   windows), `case_studies/CASE_STUDIES_ALL.md`, `CORPUS_PROFILE.json`,
   `DATA_DICTIONARY.md`, `LIMITS_OF_PUBLIC_FILLS.md`.
4. Full 760,817-fill table is `dataset/fills/l5_fills_enriched_part01..14`
   (gzipped, ≤6 MB each) — upload directly if it asks for it.

### Perplexity workflow — upload route (no repo access)

Upload these seven files, then paste **`PROMPT_PERPLEXITY.md`** as the
message:

1. `LIMITS_OF_PUBLIC_FILLS.md`
2. `DATA_DICTIONARY.md`
3. `CORPUS_PROFILE.json`
4. `case_studies/CASE_STUDIES_ALL.md`
5. `plain/cashflow.csv` (4.0 MB uncompressed — central to its incentive questions)
6. `plain/l5_window_summary.csv` (5.1 MB)
7. `plain/settlement.csv` (0.9 MB)

**Do not** paste Grok's answers or any scorecard into Perplexity. Run them
independently; that is the whole design.

### Round 2, after both have answered

Save both answers, then send
`../verification_HOLD_BACK/OUR_HYPOTHESES_UPLOAD_LAST.md` to each with the
round-two prompt at the top of that file. Not before.

---

## Where this leaves the objective

The objective is to reproduce a profitable decision process, not to
generate fills. On that standard:

**Preserved as the leading hypothesis:** L5 runs two execution modes —
passive, small, early, cheap (81% of fills, mean price 0.468) and
aggressive, large, late, dearer (19%, mean price 0.504). The mode
structure is directly evidenced from the takerOnly feed and corroborated
by the book join. What is **not** established is that the aggressive mode
causes the profit, or that the combination beats either mode alone.

**What the decomposition actually supports:** the profit is in the
**matched book** (+$6,077 locked over four days), and the **passive
residual is a major drag** (−$5,628, losing 87.6% of the time). Any clone
must therefore address residual management, which is the component L5
visibly does *not* manage — it has no closing rule and its residual grows
into expiry.

**What blocks progress, in order of cost to fix:**

1. **Network egress to Kalshi and Polymarket** — free, no trading, and it
   fixes item 1's robustness margin and would give our own account's
   maker/taker-flagged fills. The policy denies these hosts today.
2. **A volume-matched test design** for the two-mode question — pure
   analysis, no capital, and it is what item 4 needs to become valid.
3. **Only then** any live probe, and only for a configuration with
   historically positive expectancy. None currently exists.
