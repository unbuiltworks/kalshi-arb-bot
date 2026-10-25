# PMUS BTC15m: the 0.96 completion cap refuses 100% of available completions

**VENUE: PMUS.** Only PMUS. Labelled in the filename, this header and every line
of output, because both sessions working this repo have now been bitten by an
unlabelled venue.

Read-only. 19 venue-native `cpc-btc-updown-15m` windows, 407 book frames. No
trade tape needed, no new data, no capital, no order. $90 probe still unarmed;
nothing touched on `l5-pmus-live`.

---

## The result

A maker completion costs `pair_cost = bid + (1 − ask) = 1 − spread`, because
buying YES as a maker rests at the bid and buying NO as a maker rests at the NO
bid, which is `1 − ask`.

| band | frames | share | |
|---|---|---|---|
| ≤ 0.960 | **0** | **0.0 %** | the only completions the controller accepts |
| 0.960 – 1.004 | **407** | **100.0 %** | **refused by the cap, inside empirical break-even** |
| 1.004 – 1.008 | 0 | 0.0 % | the empirical break-even range |
| > 1.008 | 0 | 0.0 % | correctly refused |

Pair cost: min 0.9700, median **0.9900**, max 0.9900.
**0 of 19 windows had even one frame clearing 0.96** — in THIS sample. See the
correction below: that does not generalise.

Against two thresholds that are not the same number:

* the controller's configured ceiling, **0.96** (`TS.max_pair_cost`, read from
  the live process at boot);
* the **empirical** break-even, **1.004–1.008**, from 25 real credited maker
  rebates and the venue's cent rounding
  (`analysis/l5/PMUS_INCENTIVE_BREAKEVEN.md`).

**The cap refuses the overwhelming majority of completions PMUS offers, and
credited economics say they were worth taking.** PMUS's tick is a uniform 0.01,
so the modal spread is one cent, so the modal pair cost is 0.99 — above 0.96.

### Correction: "every" and "permanently" were too strong

A peer session ran the same computation on a different slice
(`data/pmus_btc15m_tape`, 42 two-sided frames, 1 market, 2026-09-27) and found
**4 frames at pair cost exactly 0.9600 — spread exactly 0.0400 — which the cap
accepts**, 9.5% of that sample. Both samples are small and from different
slices, so neither 0% nor 9.5% is the rate.

The defensible statement is: **the cap binds on roughly 90–100% of frames, so it
is the dominant constraint, and it is configuration rather than market.** It is
reachable when the spread widens to four cents, which is rare and not never. An
independent gamma universe scan over 37,116 markets gives crypto
`median_spread 0.010` with `share_spread_le_0_01 = 0.5769`, so ~42% of crypto
markets do sit above a cent. The conclusion survives; the absolutism did not.

Note also what the finding reduces to, which makes it harder to attack:
**"pair-cost distribution" IS the spread distribution.** `bid + (1−ask) ≡ 1 −
spread` algebraically, so the finding is simply *PMUS BTC15m spreads are a cent,
and a 0.96 cap requires four.*

## This resolves the contradiction I raised

`possible_misses: 0` alongside 299 of 355 windows matching nothing looked
impossible. It is not, and both figures are honest:

* the controller **evaluated** complements and **refused them on price** —
  confirmed directly in the 1945z lifecycle, `COMPLEMENT_PAIR_ABOVE_CAP: 12`,
  with `order_resting: FALSE` at close;
* so nothing was *missed*. Everything was *declined*, by configuration.

`matched == 0` is therefore **our doing, not the market's**. It is the one
explanation of the three I set out that neither of us had isolated, and it is
the one that makes completion raisable.

## Why this is not the `1 − spread` trap I withdrew earlier

In `L5_VENUE_SELECTION_RANKING.md` I withdrew a ranking built on
`maker_pair_cost == 1 − spread`, because a 0.29-wide book mechanically shows a
0.71 "cheap pair" that is unfillable — an identity flattering an empty market.
The direction of the error matters:

* **there**: wide spread, ~zero flow, cheap-looking pair that cannot fill;
* **here**: one-cent spread, heavy flow, pair cost 0.99 — the identity is simply
  reporting that the spread is a cent.

The identity is not inflating anything in this direction. A tight book is the
case where `1 − spread` is closest to honest.

## The break-even ceiling depends on CLIP SIZE — and there is no per-order fee

The same peer session raised that the ceiling is a function of clip size,
because the rebate is a step. That is right, and important for the sweep design.
Their table, however, charged **$0.02 per pair in per-order fees**, and
concluded a 0.99 completion LOSES $0.0044 at the authorized clip and needs
q ≥ 2.0 to pay. **That is the Kalshi `PER_ORDER = $0.01` constant from
`replay_b`, which PMUS does not charge** — the exact error corrected in
`569c42d8`. Verified two independent ways:

1. `pmus_fees._fee` is purely `theta × C × p × (1−p)`. No additive term, no flat
   component anywhere in the module.
2. The committed 1.004–1.008 ceiling **reproduces exactly with per-pair fees of
   $0.00** (1.004 from a one-cent-a-leg credit, 1.008 from two cents). With a
   $0.01-per-order fee those same inputs would give 1.000 and 1.004 instead.

Corrected, using the **credited** cent-rounded rebate that the 25 real credits
establish:

| q / leg | credited rebate / pair | break-even ceiling | **net at pair 0.99** |
|---|---|---|---|
| **1.56** (authorized) | $0.00 | **1.0000** | **+$0.0156** |
| 2.00 | $0.02 | 1.0100 | +$0.0400 |
| 2.70 | $0.02 | 1.0074 | +$0.0470 |
| 5.00 | $0.04 | 1.0080 | +$0.0900 |
| 10.00 | $0.06 | 1.0060 | +$0.1600 |

**A 0.99 completion is profitable at every clip, including the authorized 1.56.**
The clip-dependence is real but weaker than stated: it sets how much headroom
exists *above* 1.00, not whether 0.99 pays. The $0.0044 loss was entirely the
phantom fee.

The 2-D sweep remains the right design — ceiling × clip — because the ceiling
genuinely moves with the clip. The conclusion it should be tested against is
"profitable at all clips, headroom above 1.00 needs q ≥ 2", not "needs q ≥ 2 to
pay at all".

## What it is worth, stated before anyone gets excited

A 0.99 pair redeems 1.00, so the gross margin is **≈ 1 cent per completed
pair**, maker fee zero on PMUS, rebate rounding to about nothing at our clip
sizes. That is a real positive, and it is small.

**Realising it still requires BOTH legs to fill.** Measured P(fill) is 0.4325
per leg at 30 s on 965 real orders, and completion is sequential rather than
simultaneous, so the completion rate — not the pair cost — becomes the binding
quantity the moment the cap stops binding. And at 1 cent per pair, $480/day
needs ~48,000 completed pairs/day, which the capacity table in
`L5_VENUE_SELECTION_RANKING.md` already puts three to four orders of magnitude
beyond the authorized envelope. **This finding changes the diagnosis and the
next action. It does not produce the target.**

## Next action, and the one number that decides it

Re-run the inventory controller and the pairing controller on the venue-native
PMUS windows with the completion ceiling swept **0.96 → 0.99 → 1.004 → 1.008**,
chronologically out of sample, with the measured per-leg fill probability
applied and the residual settled properly. The question is whether the extra
completions earn their ~1 cent faster than the extra unpaired legs lose their
settlement value.

Supporting evidence that the mechanism moves: on **Kalshi** tape a peer session
measured the ceiling 0.96 → 1.00 cutting stranded windows 23 → 7 and the match
rate 60 % → 87 %, still net negative. That is a **mechanism** result on the
wrong venue and is held as such — but it moves hard in the predicted direction,
and the predicted direction is now explained.

**I am not proposing a controller change on this.** The cap is a risk control on
a money-capable service owned by another session, the sweep is the test that
should decide it, and the test has not been run.
