# FAVORITE ACCUMULATION / FLIP: negative at every cadence and size, for an arithmetic reason

**A separate lane.** Not the frozen .96 Gemini control, not the L5
inference model. No frozen threshold was read; results are not pooled.

Data: Zenodo 22084977 `updown_15m.parquet`, 1-second full ladders,
Polymarket BTC 15m, 2026-08-21..24, CC-BY-4.0, sha256
`d86191506e416026f460338b1b238c628b3fcee37f468e646b3a2256bd0be959`.
**33 windows, all included.** Settlement **read** from the dataset's
`is_winning_outcome`, never inferred. Fills **walk the ask ladder** — no
midpoint, no touch-equals-fill, shortfall recorded. **Hold to
settlement**, so no exit discretion.

## Results — all eight configurations lose

| cfg | windows | net/win | $/day | win% | median | p10 | worst | max DD |
|---|---|---|---|---|---|---|---|---|
| $0.50 @ 15s | 33 | −0.203 | −19.49 | 57.6 | +2.57 | −14.45 | −21.38 | −57.90 |
| $0.50 @ 30s | 33 | −0.198 | −19.03 | 54.5 | +1.02 | −7.65 | −10.07 | −28.84 |
| $0.50 @ 60s | 33 | −0.197 | −18.95 | 57.6 | +0.10 | −3.86 | −5.90 | −15.49 |
| $0.50 @ 120s | 33 | −0.156 | −15.00 | 48.5 | −0.11 | −1.82 | −3.18 | −9.47 |
| $1.00 @ 15s | 33 | −0.407 | −39.07 | 57.6 | +5.13 | −28.91 | **−42.77** | **−115.81** |
| $1.00 @ 30s | 33 | −0.397 | −38.09 | 54.5 | +2.05 | −15.30 | −20.15 | −57.69 |
| $1.00 @ 60s | 33 | −0.395 | −37.91 | 57.6 | +0.19 | −7.72 | −11.80 | −30.98 |
| $1.00 @ 120s | 33 | −0.313 | −30.00 | 48.5 | −0.22 | −3.65 | −6.36 | −18.94 |

Win rate is a coin flip (48–58%) and the **median is positive while the
mean is negative** — a fat left tail. Loss scales linearly with both the
increment and the cadence frequency: more trading is simply more loss,
and no cadence rescues it. Capital ~$14.76/window at $1 @ 60s; peak
unmatched 11.3 shares median. Depth never blocked an allocation
(**no-depth skips: 0**), so capacity is not the constraint at these sizes.

## The flip conditioning — the hypothesis fails arithmetically

$1.00 @ 60s:

| flips | windows | net/win | **matched P&L** | residual P&L | win% |
|---|---|---|---|---|---|
| 0 | 2 | **+5.01** | 0.00 | +5.01 | 100 |
| 1 | 13 | −1.26 | **−1.52** | +0.26 | 69.2 |
| 2 | 8 | +0.07 | **−1.80** | +1.86 | 50.0 |
| 3+ | 10 | −0.72 | **−2.02** | +1.30 | 40.0 |

**Matched P&L is negative in every flip bucket and becomes steadily more
negative as flips increase.** That is not bad luck, it is arithmetic:
buying whichever outcome is above 0.50 means that after a flip you hold
both legs, each bought above 0.50, so the matched pair cost exceeds
$1.00 and the matched portion is a *guaranteed* loss. **Every flip
manufactures one.**

Residual P&L is positive — the final favourite does win more often than
not — but it does not cover the matched losses. The only clearly
profitable bucket is **zero flips (n=2)**, i.e. the windows where the
strategy never had to fund a flip at all.

**So "funding the flips" is where the money goes, not where it comes
from.**

## Cadence sees only a fraction of the real flips

1-second leadership flips: median **8**, mean 8.6, max 25. Cadence flips
at 60s: median **2**. A 60-second cadence observes roughly a quarter of
the actual leadership changes — and sampling *more* of them (15s) makes
the loss worse, not better, consistent with the arithmetic above.

## Does it resemble L5? No.

Against the 12 L5-active windows in the same dataset (3,166 chain-verified
fills, 0 chunk holes):

| | L5 | the rule (60s) |
|---|---|---|
| fills per window (median) | **246** | ~15 |
| side switches per window (median) | **80** | 2 |
| share of fills on the window-dominant favourite | **48.0%** | ~100% by construction |

L5 trades ~16× more often, switches ~40× more often, and splits his
volume **essentially 50/50** between the dominant favourite and the
underdog. **He is not following the favourite.** This agrees with the
earlier out-of-sample result where "buy the long side" scored 55.3% and
"buy the cheaper leg" 56–58% — neither beat persistence.

## Fees

Base case **zero**, because this repository holds no authenticated
Polymarket fee measurement and inventing one would flatter or punish the
result arbitrarily. At a stated 2%-per-dollar sensitivity, $1 @ 60s goes
from −$0.395 to **−$0.69** per window. Fees make it worse; they are not
what makes it negative.

## A bug I found and fixed, and it is the fifth of its kind

The first run reported a **0% win rate across all 33 windows and all 8
configurations** with net exactly equal to −capital. That implausibility
is what exposed it: the dataset's `winner` column holds **`"yes"`/`"no"`**
while `outcome` holds **`"Up"`/`"Down"`**, so `qty[winner]` was always
zero and every window scored a total loss. Fixed by reading the
producer's own per-row `is_winning_outcome` flag instead of mapping its
string myself — the same consumer-guessing-a-producer's-shape failure
that has now bitten this program five times.

## Conclusion

On 33 windows of real second-level ladders, hold-to-settlement, with
honest execution: **the favourite-accumulation/flip rule has negative
expectancy at every cadence and both increments, for a structural reason
rather than a sampling one.** It also does not resemble L5's observed
behaviour.

**No live capital should follow this.** 33 windows over four days is a
small sample and the left tail is fat, but the mechanism — each flip
creating matched inventory above $1.00 — does not depend on sample size.
