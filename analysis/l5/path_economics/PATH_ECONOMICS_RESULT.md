# Sequential path-economics of PMUS BTC15m

Does movement through time create profitable sequential acquisition? BUY one
outcome → WAIT → BUY the opposite when it becomes cheap enough → lock → EXTEND →
SETTLE, with neither leg required to be a static mispricing.

Run `37242459801` (study), artifact `path-economics-37242459801`. Input: the
reconstructed post-cutoff tape, 178 windows / 157,530 causal per-second rows,
whose derivation was calibrated against the development tape at recall 1.000
(`analysis/l5/oos/OOS_ECONOMIC_RESULT.md`). Taker economics, venue cent fees, no
maker assumption. The rejected `q − executable price − fee` signal is never
computed anywhere in this study.

---

## The four numbers

### 1. How often does a first purchase subsequently have an executable all-in pair below $1?

**Very often — about two thirds to seven tenths of all eligible seconds.**

| | UP-first | DOWN-first |
|---|---|---|
| eligible first legs | 157,530 | 157,530 |
| **sub-$1.00 complement exists** | **70.25%** | **65.13%** |
| excluding the venue's final 60s | 68.19% | 62.07% |
| fresh book only (≤2.0s) | 70.36% | 65.20% |

This is **not** a null result. The surface is large and it is not an artifact of
stale quotes or of the settlement window.

### 2. How far below $1 do those sequential pairs get?

**Deep — the median successful pair locks about 22–23 cents of gross per unit.**

| best achievable all-in | UP-first | DOWN-first |
|---|---|---|
| < 1.00 | 70.25% | 65.13% |
| < 0.95 | 58.75% | 53.50% |
| < 0.90 | 50.85% | 44.96% |
| < 0.85 | 44.91% | 39.02% |
| < 0.80 | 38.78% | 34.01% |
| < 0.70 | 25.86% | 25.93% |
| < 0.60 | 16.04% | 19.22% |

Hindsight gain per unit: UP mean 0.26283, median 0.23, max 0.98; DOWN mean
0.27752, median 0.22, max 0.98. Median time to the best complement is 398s (UP)
and 348s (DOWN); median touch depth at that complement is 8,710 and 5,592 shares,
so **displayed liquidity is not the binding constraint** at probe size. The sub-$1
state stays open a median 241s / 177s in total, with a longest unbroken run of
177s / 127s.

**This is an upper bound, not a strategy.** It is the minimum over the remainder
of the window, chosen with hindsight; no controller can reach it.

### 3. How much do first legs lose when the profitable complement never arrives?

| | UP-first | DOWN-first |
|---|---|---|
| failed first legs | 46,868 (29.75%) | 54,932 (34.87%) |
| naked hold-to-settlement, mean | **−0.20101** | **−0.20978** |
| median | −0.09 | −0.12 |
| p10 | −0.56 | −0.54 |
| worst | −0.99 | −0.99 |

Failures lose about 20 cents per unit on average and up to the full stake.

### 4. Does the frequency/magnitude of successes plausibly outweigh the failures after fees?

**On the naive arithmetic, yes — overwhelmingly. Causally, no.**

Combining 1–3 directly:

- UP-first: `0.7025 × 0.26283 + 0.2975 × (−0.20101)` = **+0.12484 per unit**
- DOWN-first: `0.6513 × 0.27752 + 0.3487 × (−0.20978)` = **+0.10760 per unit**

That number is **wrong as a strategy estimate**, and the study was built to show
why rather than to report it. See below.

---

## Why the naive arithmetic is wrong: three decisive diagnostics

A matched UP+DOWN pair pays exactly $1.00 whichever side wins. So the complement
is cheapest precisely when it is about to be **worthless** — that is, when the
first leg is already winning.

**1. Availability is conditioned on the outcome.**

| sub-$1 complement exists | first leg WON | first leg LOST |
|---|---|---|
| UP-first | **87.66%** (n=84,960) | **49.86%** (n=72,570) |
| DOWN-first | **88.91%** (n=72,570) | **44.82%** (n=84,960) |

The complement is reachable ~88% of the time when you are already right and ~45–50%
when you are wrong. Insurance that is twice as available when you do not need it
is not insurance.

**2. Pairing beats simply holding in only about a third of cases.**

| | UP-first | DOWN-first |
|---|---|---|
| mean locked pair gain | 0.26283 | 0.27752 |
| mean naked hold P&L on the same legs | 0.06067 | 0.08886 |
| mean advantage of pairing | +0.20216 | +0.18866 |
| **share where pairing beats holding** | **32.70%** | **37.11%** |

The mean advantage is positive only because pairing truncates the bad tail; in
two thirds of cases completing gives up more than it protects. And this uses the
hindsight-best completion price, so even the 32.7% is flattered.

**3. The cheap complement arrives at the end of the window.**

Of UP-first successes, **78.6%** have their best complement with <150s remaining
(74.1% for DOWN-first). Excluding only the final 60s barely moves the headline
(70.25% → 68.19%), so this is the last ~2.5 minutes rather than the settlement
print itself — but it is still the window collapsing, not mid-window oscillation.

---

## Phase 2 — the causal mechanics

One deterministic mechanism, no tuned parameter: enter at the first eligible
second, complete whenever the all-in cost against that lot locks below $1.00,
repeat. FIFO lots, matched tranches never re-paired, depth consumed.

| variant | matched shares | tranches/window (med) | locked net | residual P&L | **TOTAL NET** | net/window | % positive |
|---|---|---|---|---|---|---|---|
| UP-first | 1171.55 | 9.0 | **+22.68** | **−54.29** | **−31.61** | −0.178 | 53.9% |
| DOWN-first | 925.55 | 5.5 | **+19.66** | **−57.73** | **−38.07** | −0.214 | 46.1% |
| cheaper-side-first | 498.03 | 2.0 | **+26.99** | **−80.29** | **−53.30** | −0.299 | 12.9% |

**The central finding is the sign split.** The matched tranches are genuinely
profitable in every variant — the locked column is positive, +22.68 on 1,171
matched shares is about +1.9 cents per matched share, after venue cent fees.
**The mechanism really does manufacture profitable locked inventory.** It is the
unmatched residual that destroys it, losing 2.4× to 3.0× what the locks earn.

The reason is the same asymmetry as diagnostic 1: a lot completes when the
opposite side gets cheap, which happens when that lot is winning. Lots that never
complete are therefore **adversely selected** — they are disproportionately the
losers, held naked into settlement. The mechanism systematically locks its
winners and keeps its losers.

Repeated oscillation is real: a median of 9 matched tranches per window on
UP-first, up to 16. So the EXTEND limb of the mechanism is supported by the data.
It extends a profitable matched book and a worse residual at the same time.

**Scale does not fix a sign.** Peak capital is $7.30/window (UP-first), so against
the $200 ceiling there is ~27× headroom and touch depth is ample. Scaling the
observed per-window figure by that headroom gives roughly **−$4.86/window**
(UP-first), −$7.28 (DOWN-first), −$19.34 (cheaper-first). The target is +$5.

---

## Answer to the question asked

> Does the raw PMUS BTC15m path structure contain enough sequential two-sided
> movement to justify preregistering a BUY → WAIT → BUY OTHER SIDE → EXTEND
> controller?

**No — not the controller as specified. But the path structure is not the
reason, and that distinction matters.**

The movement is abundantly there. 70% of eligible seconds see a sub-$1 all-in
complement, half of them below $0.90, with thousands of shares of displayed depth
and a sub-$1 state open for minutes. Matched tranches really do lock positive
economics, ~1.9 cents per matched share net of venue fees, and oscillation really
does support repeated extension.

What fails is the **completion rule as the whole controller**. "Complete when the
all-in locks below $1.00" is a rule whose trigger is correlated with being right,
so it harvests winners into locks and leaves losers naked. Every causal variant
loses, and the loss is the residual, not the pairs.

So the thing that is not yet justified for preregistration is a controller whose
only decision is *when to complete*. The evidence points at a different and
currently unanswered question: **what to do with a first leg whose complement has
not arrived.** That is where all the loss sits (−54 to −80 against +20 to +27),
and this study deliberately does not answer it — no exit rule, stop, time limit or
residual cap was tested, because testing one here would have meant fitting the
controller this study was told not to fit.

A preregistration worth writing would isolate residual management, not completion
timing, and it would have to be specified before being measured.

---

## Limits of this study, stated plainly

- **Phase 1 is hindsight.** Its frequencies and gains bound what the path offered;
  they are not attainable. Phase 2 is the causal number and it is negative.
- **Phase 2's absolute level is not a performance estimate.** The entry rule
  ("first eligible second") is arbitrary and deliberately unoptimized, and the
  three variants span −0.178 to −0.299 per window. The robust claim is the
  **sign** and the **locked/residual split**, not the magnitude. That entry also
  puts first purchases near 885s remaining — the same degenerate timing the
  rejected controller showed.
- **Depth is touch-only.** The recorder's fold keeps level [0], so capacity beyond
  the displayed touch is unknown and was never guessed. Touch depth exceeded probe
  size by orders of magnitude, so this limits size claims, not these results.
  Note this means the tape is **not** full-ladder, despite being described that
  way in the request.
- **UP-first and DOWN-first are separate hypotheticals** over the same displayed
  liquidity and are never summed.
- **One venue regime, 46 contiguous hours.** 178 windows is 6.4× the development
  sample but it is one stretch of one market's behaviour.
- **No live order was placed, no threshold was swept, no controller was fitted,
  and the recorder was untouched.**
