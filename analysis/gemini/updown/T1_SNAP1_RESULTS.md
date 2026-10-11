# T1 (snapshot 1): gate passed at 16 windows -- and I was wrong about the recorder

## CORRECTION FIRST: there was no recorder defect. My census was the defect.

I reported that the recorder failed to capture the authoritative strike and
proposed fixing it. That was wrong, and the evidence is unambiguous:

* the metadata poll **already re-reads the contract every cycle** and already
  persists the venue's `strike.value` the moment it appears;
* it writes the first read as `kind: "window"` and **every subsequent re-read
  as `kind: "window_poll"`** -- 62 `window` rows against **9,200
  `window_poll` rows** on this tape;
* my census read only `kind == "window"`, i.e. the pre-`availableAt` read,
  which is null by definition. So I measured the one row guaranteed not to
  have the field and concluded the recorder never captured it.

**No recorder change was needed or made.** The running long recorder is
untouched, and so is the snapshot recorder's code. What changed is the
census, which now reads the re-read rows and keeps the venue value exactly
as supplied, with provenance: `strike_type`, the row kind it came from, the
row's own `t_ns`, and `strike_inferred: false`.

## The proof asked for

On completed windows, the post-`availableAt` re-read returns a strike that
agrees **to the cent** with the venue's own subsequent settlement values via
the chaining relation: **17 of 17 pairs agree**. Examples:

| window | authoritative strike (re-read) | previous window settlementValue |
|---|---|---|
| …1755 | 85245.52 | 85245.51933333333 |
| …1800 | 85359.29 | 85359.2855 |
| …1805 | 85426.51 | 85426.51416666666 |
| …1810 | 85395.53 | 85395.5275 |

Chaining is reported **only as a validation check**. It is not the strike
source, no eligibility decision uses it, and a window without an
authoritative strike **fails closed**.

## Census: 16 GENUINELY COMPLETE WINDOWS (gate >= 12 PASSED)

53 tickers seen; 16 with all five requirements. Remaining gaps: 36 lack the
early-checkpoint count (discovered too late in their life), 35 lack a strike
(same reason), 1 lacks settlement.

## (1) Frozen early-window analyzer, f2800e3e...960e90b, no --unseal-oos

16 completed, **7 sealed OOS and not read**, **9 used in discovery**, 81
observations, all structurally TWO_SIDED. Economic two-sidedness: 100% at
cumulative q1, 82.7% at q5-q100, collapsing to 6-7% at touch q50-q100.

Pooled T+15..T+120, cumulative q1, **9 windows**:

| side | trades | distinct windows | win % | avg VWAP | breakeven w/ fees | win - breakeven | EV/trade | EV per all windows |
|---|---|---|---|---|---|---|---|---|
| UP | 54 | 9 | 77.78% | 0.6848 | 69.99% | **+7.79pp** | +0.08023 | +0.48137 |
| DOWN | 54 | 9 | 22.22% | 0.6009 | 61.77% | -39.55pp | -0.39295 | -2.35772 |

By D_t sign: D>0 -> UP wins 96.67% (30 trades, 7 windows), +14.88pp,
EV/trade +0.14962. D<0 -> UP wins 54.17% (24 trades, 5 windows), -0.78pp.

### THE APPARENT EDGE IS THE BASE RATE, AND HERE IS THE PROOF

**UP's win rate is 77.78% at EVERY ONE of the nine checkpoints** -- T+5,
T+15, T+30, T+45, T+60, T+90, T+120, T+150, T+180, identical. That is
because the policy buys UP in all nine windows at every checkpoint, and
**7 of those 9 windows settled UP**. 7/9 = 77.78%. The win rate is not
responding to state; it cannot, because the trade set is the same nine
windows every time. Only the PRICE moves, which is why win-breakeven swings
from +12.38pp at T+15 to -4.51pp at T+180.

The D_t sign split is the same artifact one level down: the D>0 and D<0
buckets contain 7 and 5 of the same 9 windows (they overlap, because D flips
sign within a window across checkpoints), so the 96.67% is a handful of
windows counted six times each.

**This is not evidence of an edge.** Nine windows, non-independent
observations, in-sample, with the OOS partition deliberately unread. A
sample where UP happens to settle 7 of 9 will show a positive UP edge at any
price below ~0.78 regardless of whether a signal exists.

## (2) Frozen chained-D study, b2f6d50a...142f9bc6, discovery only

**0 trades across all 32 predeclared candidates. 0 clear the $0.01
EV-per-all-windows bar, so the predeclared selection rule names no candidate
and the OOS partition stays sealed.** Correct outcome under the rule.

But that result is **not a test of the hypothesis**, and the reason is the
same read I just got wrong: the frozen analyzer's own `windows()` loader
reads the strike from `kind == "window"` rows only. Under its own rule it
finds a strike for **2 of 18** tickers; 16 more have one available solely on
`window_poll`. So it ran on ~1-2 windows and 8 observations, and its
incorporation-lag correlations are all `None` for want of pairs (n=1 per
checkpoint).

Its capacity curve is the one usable output, and it is consistent with
everything prior: economic two-sidedness 100% at q1 and 87.5% from q5 to
q100, with the two sides' combined cost **1.28-1.31** -- the pair always
costs well over a dollar.

**I did not modify it.** It is frozen, the freeze is the point, and
unfreezing it to fix a loader is a decision for you, not me. Until then the
chained-D hypothesis is untested rather than rejected.

## State

Nothing else was changed: no strategy rule, threshold, OOS boundary, maker
study or live execution behaviour. No OOS was unsealed. No live orders. The
long recorder run (37325663925) is untouched and still collecting.
