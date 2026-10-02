# Does PMUS contain an active + thin + compensated regime? — **No**

2026-10-02. Read-only. No live orders. BTC lead-signal work stopped and
preserved at `7ac01e05`; Kalshi frozen at `1ac07f69`; L5 benchmark unchanged;
Gemini audit at `70ccad63`.

## Verdict

**No such regime exists on PMUS today.** The three things the strategy needs —
an active book, thin near-touch competition, and a pool large enough to pay —
are present in pairs but never together. Each pole fails for a different and
structural reason.

| Regime | Example | Pool | Active? | Queue ahead | q10 reward | Outcome |
|---|---|---|---|---|---|---|
| **Active + thin** | Setka table tennis | **$30** | yes | 2,868–5,178 | $0.10–$0.18 | **below the $1.00 minimum → paid $0** |
| **Big pool, dormant** | NFL/CFB props | $1,350–$10,350 | **no** (6–13 states/hour) | — | modelled $30–$465 | **artifact of a dead book** |
| **Active + deep** | NFL moneyline | $11,000 | partly | **488k / 948k** | $0.02 | **unfillable and unpaid** |

## The cross-sectional scan — the finding that frames everything

Of **33 live-program markets whose event had already started**, sampled twice
60 seconds apart:

> **Only 2 changed at all. Both were Setka Cup table tennis. Both had $30
> reward pools.**

Every NFL market with a $10,350 pool was static. The large pools sit on books
that are not trading; the books that trade carry pools two to three orders of
magnitude smaller. **This inverts the snapshot ranking that started the
investigation** — the markets that looked richest were the ones that had
stopped moving.

It also explains our own history without appeal to a controller defect: the
account's credited reward rows came from exactly these `aec-setka*` and
tennis markets, median $0.04, max $1.15. The controller went where the
activity was. On PMUS the activity is in tiny-pool markets.

## The Setka shadow — the only genuinely active evidence

`shadow_setka_20261002T012813Z.jsonl`, two actively-trading markets,
15 minutes, 1 Hz nominal.

**Coverage, stated first because it bounds everything below:** 413/900 and
630/900 seconds (46% and 70%), with **306 HTTP 429s**. Gaps are holes indexed
by elapsed second, not shifted samples, but the loss is bursty rather than
random and the fractions below inherit an unquantified bias.

Books were genuinely moving: 19 and 27 distinct states, against 6 for the
dormant NFL prop and 4 for the moneyline.

### Reward economics — reported alone

| market | pool | q1 | q5 | q10 (two-sided) | paid after $1.00 minimum |
|---|---|---|---|---|---|
| `khavit-hnorom` (live program) | $30 | $0.0105 | $0.0524 | $0.1040 | **$0.00** |
| `vlaval-smymyk` (live program) | $30 | $0.0208 | $0.0958 | $0.1754 | **$0.00** |

Both sides qualified 100% of sampled seconds, so this is not a qualification
failure — it is a pool-size failure. Scaling linearly at the observed
competition, q10 would need a pool of **$171–$289 to clear the $1.00 minimum**
and **$513–$866 to reach $3.00**. The active markets pay $30.

### Queue and fill feasibility — reported alone

Median size resting **ahead of us at the touch**: 3,980 / 2,868 and
4,847 / 5,178 contracts. A q10 order joins behind three to five thousand
contracts in a match lasting tens of minutes. **Maker fills at the touch are
not realistically attainable**, which also means the rebate and the spread
below are mostly unreachable, not merely small.

### Markout / adverse selection — reported alone, per hypothetical fill

No fill is assumed anywhere; these are per-fill conditionals.

| horizon | `khavit` buy / sell | adverse frac | `vlaval` buy / sell | adverse frac |
|---|---|---|---|---|
| 1s | +0.01096 / +0.01078 | 0.01 / 0.01 | +0.00566 / +0.00712 | 0.01 / 0.01 |
| 5s | +0.01430 / +0.00688 | 0.04 / 0.07 | +0.00109 / +0.01134 | 0.08 / 0.02 |
| 15s | +0.01780 / +0.00363 | 0.14 / 0.19 | −0.00782 / +0.01963 | 0.25 / 0.07 |
| 30s | **+0.02754 / −0.00613** | 0.27 / 0.42 | **−0.02111 / +0.03166** | 0.51 / 0.14 |

The two markets are mirror images at 30s — one drifted up, the other down —
and the adverse fractions mirror too (0.27/0.42 against 0.51/0.14). That is
**directional drift within a single match, not toxicity**: two markets is far
too small a sample to characterise adverse selection, and no adverse-selection
rate should be carried forward from this.

### A metric I am withdrawing

I added a "two-sided markout sum" intending it to measure whether quoting both
sides nets out. It does not measure anything:

    (mid(t+h) − bid) + (ask − mid(t+h))  ≡  ask − bid

The future mid cancels identically, so the sum **is the spread by
construction**, at every horizon, for every market. It came out at +0.0214 and
+0.0106 against a 0.0100 median spread — the residual is only the spread
varying around its median. Withdrawn as a finding; the per-side rows above are
the informative ones.

## The compensation stack, for the only active markets that exist

For a q10 two-sided maker in Setka, totalling the three streams separately:

| stream | value | attainable? |
|---|---|---|
| liquidity reward | $0.10–$0.18 per period | **no** — below the $1.00 minimum, paid $0 |
| maker rebate | $0.0302 per fill (`0.0125 × 10 × 0.59 × 0.41`) | only on a fill behind 3–5k contracts |
| spread capture | $0.10 per round trip at a 1¢ spread | requires **both** sides to fill |
| **total if everything lands** | **≈ $0.13** | |

Against the measured cost of producing PMUS sports liquidity, **−$1.14 per
settled market** (39 markets, −$44.58, 32 of 39 losing). And against a $3
target.

**Reward + rebate + spread cannot plausibly exceed execution cost here. The
shortfall is roughly an order of magnitude, and the dominant term is that the
liquidity reward is zero — not small, zero — because the only active markets
carry $30 pools against a $1.00 minimum payout.**

## What would change the answer

A market that is simultaneously (a) trading, (b) thin enough near the touch
that q5–q10 is a material share, and (c) carrying a pool above ~$500. The
census contains 73,851 active rows and the scan found none in the intersection.
If PMUS re-points its large pools at markets that actually trade, this becomes
worth re-testing — and the activity gate plus the two-sided analyzer will
answer it in fifteen minutes. **Until then: no live probe, and no further
PMUS sports optimisation.**

## Three venues, one frame

| venue | compensation observed | trading | structure |
|---|---|---|---|
| **L5 International** | +$23,047.40 | −$6,338.70 | **proven: incentives overwhelm losses** |
| **PMUS** | **+$16.18** credited ($9.14 rebates, $7.04 rewards) | −$44.58 over 39 markets | **proven, and the wrong way round** |
| **Gemini** | **+$0.56** credited, 1.029% of maker notional | **UNMEASURABLE** — gate BLOCKED | only the incentive leg proven |

PMUS is now the one venue where both legs are measured, and it is negative.
Gemini remains the open case, and the read-only per-fill probe named in
`GEMINI_REBATE_ECONOMIC_AUDIT.md` is the cheapest remaining question in the
programme.
