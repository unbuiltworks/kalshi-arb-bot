# Favorite accumulation — research gate verdict

**Issue #194 research gate. Verdict: NO-PROMOTION.**
Reproduce with `PYTHONPATH=. python3 analysis/l5/favorite_accumulation.py`.
Tests: `tests/test_l5_favorite_accumulation.py` (14 cases, no network, no corpus).

Nothing here changed live configuration, exposure or authorization.

## Corpus and method

| | |
|---|---|
| settlement truth | `data/l5/raw/markets.jsonl` — `outcomes` × `outcomePrices`, `umaResolutionStatus: resolved`, Chainlink `resolutionSource` |
| L5 trades | `data/l5/raw/activity.jsonl`, deduplicated on (tx, asset, side, price, size, ts) |
| resolved BTC15m windows | 535 |
| L5 trades joined | **63,346** across **526** windows (3,245 duplicates dropped) |
| side census | **100% BUY** — L5 never sells; he exits via redemption and settlement |

**Circularity avoided as the issue requires.** The predictor is the price L5
paid; the truth is the venue's own settlement. Price is never used as both
predictor and edge.

**The inference unit is the window, not the trade.** Every trade inside one
15-minute window shares ONE outcome, so 63,346 trades are ~526 coin flips.
All intervals below are clustered by window. Trade-level intervals were
narrower by more than an order of magnitude and would have made noise look
like edge.

## 1. There is a real favorite-longshot bias

Edge = realized settlement frequency − implied price, per contract.

| bucket | window-mean edge | 95% CI (clustered) | |
|---|---|---|---|
| favorite `px > 0.50` | **+0.04389** | [+0.02253, +0.06526] | **significant** |
| underdog `px < 0.50` | **−0.03127** | [−0.05105, −0.01150] | **significant** |
| strong favorite `px ≥ 0.70` | +0.01961 | [−0.00418, +0.04340] | not significant |
| strong favorite `px ≥ 0.80` | +0.01142 | [−0.01157, +0.03441] | not significant |
| all trades | +0.00097 | [−0.00837, +0.01032] | not significant |

Trade-level calibration showed the edge rising monotonically into the deep
favorites (+0.0280 at 0.70–0.80, +0.0372 at 0.90–0.95). **That gradient does
not survive window clustering.** The eye-catching deep-favorite numbers are
not statistically established; only the broad `px > 0.50` bucket is.

## 2. L5 does **not** accumulate the favorite — he tilts to the underdog

| | |
|---|---|
| favorite-heavy windows | **81 (15.4%)** |
| underdog-heavy windows | **210 (39.9%)** |
| balanced | 235 (44.7%) |
| favorite : underdog contracts | 571,168 : 627,425 = **0.910** |

The hypothesis's behavioural premise is **falsified by L5's own record**. He
buys 10% more underdog than favorite contracts and is underdog-heavy 2.6× as
often as favorite-heavy — which is precisely the side carrying the
significant *negative* edge. This is consistent with his measured BTC15m
trading P&L of −$1,105.09 over the captured sample.

So favorite accumulation is **an edge L5 leaves on the table**, not a
description of what he does.

## 3. The edge does not survive a constrained replay

Three predeclared policies, chronological out-of-sample (first 60% of windows
train, last 40% test = 211 windows), under the **deployed** caps: clip 1,
$12 cash, $20 exposure, residual cap, 480s entry cutoff, 600s completion
cutoff.

Out-of-sample, mean realized $/window:

| participation | A paired | B favorite | C favorite+hedge |
|---|---|---|---|
| 1.00 | **+0.0662** *(SIG)* | −0.0404 | +0.0283 |
| 0.50 | +0.0271 | −0.0999 | −0.0349 |
| 0.25 | +0.0087 | −0.0603 | −0.0082 |
| 0.10 | −0.0201 | −0.0096 | +0.0137 |

Only **A** is ever significant, and only at participation 1.0 — the
unrealistic ceiling where we are counterparty to every print. **B is negative
at three of four participation levels and never significant.**

Risk separates them far more sharply than return:

| | A paired | B favorite | C favorite+hedge |
|---|---|---|---|
| max drawdown (OOS) | **−2.94** | **−24.45** | −12.74 |
| mean worst-case $/window | **−0.0049** | **−1.6802** | −0.6304 |

A's worst case is ≈0 because a matched pair redeems $1.00 regardless of
outcome. B's is −$1.68 per window because it is directional.

### An earlier version of this replay was invalid, and is recorded here

The first implementation sorted each side by price and greedily matched
cheapest-to-cheapest. Over the same 211 windows it reported **+$2,257 on
9,514 pairs** — 45 pairs/window against the **6** our live engine actually
completed in 4 windows. It was hindsight cherry-picking: minute-2 prices
matched to minute-13 prices, no clip, no cash cap, no chronology. It is
documented because CLAUDE.md names the failure exactly: *"Positive aggregate
replay P&L with materially different behaviour is not sufficient evidence."*

## 4. Why a real edge is still unharvestable at our capital

| | |
|---|---|
| favorite edge per contract | +0.04389 |
| mean favorite price | 0.7246 |
| sd of one contract's payoff | 0.4467 |
| **contracts for a 2σ result** | **414** |
| windows needed at ~3 contracts/window | **138** |
| expected $/window at 3 contracts | +0.1317 |
| sd $/window at 3 contracts | 0.7737 |
| **signal : noise per window** | **0.170** |

The edge is ~4.4¢ against a ±$1 payoff. At clip 1 and a residual cap of 1 the
position never gets large enough for the edge to dominate variance. Over an
8-window campaign the directional sd is ≈$2.19, so the **$5 daily-loss halt
sits about 2.3σ away** — the halt would be reached on variance well before
the edge compounded. More capital is not an available lever here.

## 5. Stress cases

**Favorite reversal** (favorites lose in every window): B **−0.6708/window,
−$141.53 total, max drawdown −$141.53** — monotone bleed, no recovery. A
**+0.4880/window** and indifferent, because pairs do not care which side
wins. C ≈ 0.

**Price-uninformative control** (outcomes reshuffled across windows): the
favorite edge inverts to −0.27415, so this is *not* a clean no-edge world — it
makes high-priced contracts systematically overpriced, and it is labelled
accordingly rather than presented as a null. It produced one informative
result: **A improves** (+0.2842/window vs +0.0662 on real outcomes). A's
residual does *better* against random outcomes than against real ones, which
means **the paired controller's real residual is adversely selected** — the
same finding as section 6: an 81% live lose-rate for us against 72.0% for L5
over this corpus.

## 6. Action-by-action against L5 (out-of-sample, 211 windows)

| | |
|---|---|
| L5 actions | **40,083 (190.0 per window)** |
| A paired | 1,078 (5.11/win) — **2.69%** of L5's actions |
| B favorite | 608 (2.88/win) — **1.52%** |
| C favorite+hedge | 1,462 (6.93/win) — **3.65%** |

We reproduce 1.5–3.7% of his action count. And on side selection, policy B
would **disagree with 58.5% of L5's actions by construction**, because that
is the share he places on the underdog (favorite 40.4%).

Timing, against our deployed cutoffs: his median trade is at 469s, p90 795s;
**48.6% fall after our 480s entry cutoff** and **35.0% after our 600s
completion cutoff**.

### The decisive number, and a correction to an earlier claim

L5's own net residual sat on the **losing** side in **152 of 211** windows —
a **72.0%** lose-rate, against our live-measured 81%.

I have previously cited "L5 57%" in this program. That figure came from the
earlier *profitable-era* subset; across this 526-window resolved corpus,
which includes his loss-making period, it is **72.0%**. The honest gap
between us and him is therefore **81% vs 72%**, about 9 points — not the
24-point gap implied by comparing against 57%.

This reframes the whole question. A directional BTC15m residual loses roughly
three times in four **for L5 too**. That is the signature of adverse
selection on a resting order: the residual is what remains precisely when the
market has moved against the quote. It is a property of the instrument, not
of our execution quality — and it is the strongest single argument against
deliberately accumulating one.

## Verdict

**NO-PROMOTION. Favorite accumulation does not deserve bounded live
validation.**

1. Its behavioural premise is false — L5 tilts underdog, 0.910 ratio.
2. The favorite edge is real and significant, but small (+4.4¢/contract).
3. It is never significant out-of-sample and negative at 3 of 4 participation
   levels, with 8× A's drawdown and a −$1.68/window worst case.
4. Signal:noise is 0.170 per window; 414 contracts are needed for 2σ, which
   our clip, residual cap and $12 cash cannot reach.
5. A favorite-reversal regime costs it $141.53 over 211 windows.

**What the evidence does support**, and it points the opposite way: the
residual is where the money is lost — live, matched surplus was **+$0.1933**
against residual **−$1.4333** over 4 confirmed windows. This replay, the reshuffle control and
L5's own 72.0% residual lose-rate all say the same thing: a directional
BTC15m residual is adversely selected for everyone who holds one. The indicated
direction is **fewer unmatched contracts**, not more directional ones.

## Addendum 2026-11-10 — late-window 10-point bands (60c/70c/80c/90c)

Separate offline experiment, owner-requested. Reproduce with
`PYTHONPATH=. python3 analysis/l5/favorite_band_experiment.py`; tests in
`tests/test_l5_favorite_band_experiment.py` (14 cases, no network, no corpus).
Nothing under `src/` or the executor image references it.

**Test applied.** Window-clustered edge on L5's actual BUY fills, using the
**lower 95% bound** (not the mean), net of fee (maker 0.0000, taker 0.01), and
required positive in **both** chronological halves (315 IS / 211 OOS windows).

| band | timing | windows | mean | lower | IS lower | OOS lower | maker |
|---|---|---|---|---|---|---|---|
| 60c | all | 447 | +0.0617 | +0.0294 | +0.0061 | +0.0267 | SUPPORTED |
| 60c | ≥600s | 177 | +0.0445 | −0.0139 | −0.0794 | +0.0206 | NOT SUPPORTED |
| 60c | ≥720s | 120 | +0.0221 | −0.0530 | −0.1183 | −0.0491 | NOT SUPPORTED |
| 70c | all | 430 | +0.0545 | +0.0227 | −0.0112 | +0.0310 | NOT SUPPORTED |
| 70c | ≥600s | 210 | +0.0524 | +0.0027 | −0.0615 | +0.0290 | NOT SUPPORTED |
| 70c | ≥720s | 127 | +0.0094 | −0.0613 | −0.1231 | −0.0622 | NOT SUPPORTED |
| 80c | all | 410 | +0.0279 | −0.0016 | −0.0333 | +0.0054 | NOT SUPPORTED |
| 80c | ≥600s | 258 | +0.0215 | −0.0174 | −0.0863 | +0.0088 | NOT SUPPORTED |
| 80c | ≥720s | 155 | −0.0025 | −0.0571 | −0.1162 | −0.0419 | NOT SUPPORTED |
| 90c | all | 403 | +0.0202 | +0.0043 | −0.0089 | +0.0044 | NOT SUPPORTED |
| 90c | ≥600s | 351 | +0.0180 | +0.0018 | −0.0134 | +0.0021 | NOT SUPPORTED |
| 90c | ≥720s | 266 | +0.0239 | +0.0073 | −0.0016 | +0.0022 | NOT SUPPORTED |

Taker is NOT SUPPORTED in all twelve cells.

**Reading.** No **late-window** cell passes in any band. The edge that exists
is in the 60c band over the *whole* window and it decays the later the fill:
at ≥720s the mean is ≤ +0.024 in every band and the lower bound is negative
in three of four. "Late-window favorite accumulation" is the part of the
favorite edge the data supports least.

**Our own money disagrees with even the supported cell.** Venue-settled
stranded legs: 0.60–0.80 lost −0.6170/unit on 4 legs; ≥0.80 lost −0.7125/unit
on 2 legs. Underpowered (376 legs needed for 2σ, 2 held), so it is a veto on
promotion, not a measurement of the edge.

**Verdict: NO PROMOTION.** Not enabled, no exposure change. It would need
PMUS fill-conditioned outcomes at these prices, an authorized directional
action class, and separate owner approval.
