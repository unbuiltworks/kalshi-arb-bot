# Discovery scan on the FROZEN DEV cohort — no candidate rule emerged

**Nothing is frozen as a rule, because nothing qualified.** The prospective
set was not touched. No live orders. Collection continues unchanged.

## Cohort discipline

`DEV_COHORT_FROZEN.json` pins the discovery set **before** any analysis:

| | |
|---|---|
| boundary | **2026-10-03T01:50:00Z** |
| DEV windows | **48** (expiry ≤ boundary) |
| span | 20:55 → 01:45 |
| resolution split | **24 yes / 24 no** (balanced) |
| windows expiring after the boundary | **PROSPECTIVE — never read here** |

A note on a designation I had to resolve: my own documents were ambiguous.
`VALIDATION_GATE.md` line 4 called `gemini_infoedge` "sealed", but §5 of the
same file designated it *"the DEV cohort of a new adequately-powered test
rather than an OOS read"*, and `PREREG_POWERED_COHORT.md` routes
freeze→DEV→VALIDATION→prospective. I resolved it as: **current windows = DEV
discovery; windows after the freeze boundary = prospective.** That matches the
instruction that new windows become prospective validation from the freeze
moment.

## Step 1 — was the predicted side ever purchasable with depth for ~$5?

Side-symmetric (predicted UP → best UP ask; predicted DOWN → 1 − best UP bid).
Qualifying requires `size ≥ 5/(1 − price)`.

| TTE | obs | available | **avail %** | price p50 | size p50 | qty needed for $5 | **DEPTH_OK** |
|---|---|---|---|---|---|---|---|
| 120 s | 48 | 25 | 52.1% | 0.840 | 25.0 | 31.2 | **17** |
| 90 s | 48 | 15 | 31.2% | 0.890 | 25.0 | 45.5 | **7** |
| 60 s | 48 | 7 | 14.6% | 0.880 | 1250.0 | 41.7 | **7** |
| 45 s | 48 | 9 | 18.8% | 0.850 | 1363.0 | 33.3 | **9** |
| **30 s** | 48 | 0 | **0.0%** | — | — | — | **0** |
| **20 s** | 48 | 0 | **0.0%** | — | — | — | **0** |
| 15 s | 48 | 2 | 4.2% | 0.450 | 1.6 | 9.2 | **0** |
| 10 s | 48 | 2 | 4.2% | 0.450 | 1.1 | 9.2 | **0** |
| 5 s | 48 | 1 | 2.1% | 0.510 | 2.0 | 10.2 | **0** |

**40 candidate states across 18 independent windows — all at T−120 to T−45.
Zero from T−30 onward.**

### The frontier question, answered on this cohort

**There is no middle region.** Where liquidity exists (T−120…T−45) the price is
already **0.84–0.89**; by T−30 liquidity is gone entirely. The two never
coexist in these 48 windows.

## Step 2 — architecture replay

| | **A one entry/hold** | **B conditional flip** | **C extend** |
|---|---|---|---|
| independent windows | 18 | 18 | 18 |
| win rate | 55.6% | 55.6% | 55.6% |
| mean entry price | 0.797 | 0.797 | 0.797 |
| gross | −$144.88 | −$128.34 | −$292.35 |
| fees | $5.11 | $6.56 | $9.77 |
| **rebates** | **$0.00** | **$0.00** | **$0.00** |
| **NET** | **−$149.99** | **−$134.90** | **−$302.12** |
| NET per window | −$8.33 | −$7.49 | −$16.79 |
| **max single loss** | **−$120.34** | −$115.65 | **−$178.17** |
| total positive / negative | +$46.85 / −$196.84 | +$42.15 / −$177.05 | +$90.11 / −$392.23 |
| actions taken | — | 7 flips | 17 extensions |

**Ordering:** B beats A by $15 but both are deeply negative; **C is far worse
than both** (−$302 vs −$135). Under the preregistered rule — if the richer
architecture does not beat the simpler one, the simpler wins — **extension is
rejected outright, and the B-over-A margin is meaningless at n=18 when both
lose ~$8/window.** Nothing advances.

## Step 3 — ATTRIBUTION: where does the (negative) result come from?

| | |
|---|---|
| win rate of the predicted side | **55.6%** |
| mean executable price paid | **79.7¢** |
| implied break-even win rate | **79.7%** |
| **PREDICTION EDGE** | **−24.1 points** |
| rebates earned | **$0.00** — entries are taker, so no advantage can come from rebates |
| entry-price advantage | **none by construction** — we pay the executable ask/complement, never a mid or maker price |

**The loss is entirely a prediction deficit.** Where the predicted side is
purchasable, the market charges ~80¢ for something that wins ~56%.

This is coherent with the availability finding rather than a new puzzle:
**offers appear when the favourite is about to lose.** 17 of the 18 entries sit
at **T−120**, where the sign of Coinbase-minus-strike is barely informative
(**52.9%**, a coin flip) yet the contract already costs **0.789**.

### The risk asymmetry is severe

Sizing for $5 of upside at these prices demands enormous stakes:

| | |
|---|---|
| mean quantity | **82.4 contracts** |
| cash at risk per entry | p50 **$20**, p90 and max **$245** |
| worst single loss | **−$120.34** |

At 0.96 a $5 target needs ~125 contracts and risks ~$120 — the $5-upside
framing hides a ~25:1 downside on the expensive entries.

## What this does and does not establish

**Does not establish** that no executable edge exists. n = 48 windows, 18 with
any candidate, 17 of those at a single TTE. This is **discovery on a thin
cohort**, and the preregistered powered cohort (~277–300) is still accumulating.

**Does establish, for this cohort:** no candidate state was found where the
predicted side was purchasable at a price below its realised conditional win
rate, at any TTE, with depth for ~$5. The apparent advantage is **negative and
attributable to prediction, not to execution or rebates.**

## Consequence

**No rule is frozen, because none emerged.** Freezing a −24-point rule to test
forward would waste the prospective set. Collection continues to the powered
cohort; the frozen DEV boundary stands so that today's windows can never be
reused as prospective evidence.
