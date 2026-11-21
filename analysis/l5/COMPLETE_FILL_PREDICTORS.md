# What predicts an accepted COMPLETE filling

**716 accepted COMPLETEs** (127 filled, **17.7%**). Rejections are excluded: a rejection is a failure to transmit an order, not a failure to fill one.

## Strata that cannot be computed

**distance_from_authenticated_touch** — opp_ask_authenticated is on 8 of 716 accepted completes (1.1%). UNAVAILABLE. The authenticated book is attached only to the #93 control rows, so the one book we trust is not recorded on the orders whose pricing we most want to explain.

**event_progress_tte** — DEGENERATE: tte>10m 592. The state key's TTE buckets were cut for 5- and 15-minute crypto windows; on multi-hour sports events they saturate, so the clone cannot distinguish early from late.

**rest_time** — NOT A PREDICTOR. rest_s is determined by the outcome -- a fill ends the rest, no fill runs to the deadline -- so its apparent power is the deadline, not a cause.

## Placement features (could inform behaviour)

### attempt number

| bucket | n | filled | fill rate |
|---|---|---|---|
| 1 | 227 | 50 | 22.0% |
| 2 | 162 | 41 | 25.3% |
| 3 | 114 | 18 | 15.8% |
| 4+ | 213 | 18 | 8.5% |

### price band

| bucket | n | filled | fill rate |
|---|---|---|---|
| p10-25c | 32 | 4 | 12.5% |
| p25-45c | 220 | 35 | 15.9% |
| p45-55c | 101 | 32 | 31.7% |
| p55-75c | 132 | 28 | 21.2% |
| p75-90c | 98 | 7 | 7.1% |
| p>90c | 9 | 1 | 11.1% |

### inventory imbalance

| bucket | n | filled | fill rate |
|---|---|---|---|
| imb1-10 | 247 | 32 | 13.0% |
| imb10-50 | 333 | 74 | 22.2% |
| imb>50 | 12 | 1 | 8.3% |

### residual units

| bucket | n | filled | fill rate |
|---|---|---|---|
| a <2 | 260 | 43 | 16.5% |
| b 2-5 | 117 | 17 | 14.5% |
| c 5-10 | 113 | 34 | 30.1% |
| d 10+ | 102 | 13 | 12.8% |

### pair at touch

| bucket | n | filled | fill rate |
|---|---|---|---|
| a <0.95 | 64 | 29 | 45.3% |
| b 0.95-1.00 | 83 | 38 | 45.8% |
| c 1.00-1.05 (above par) | 96 | 13 | 13.5% |
| d >=1.05 (well above par) | 257 | 25 | 9.7% |

## Realized features (describe, never select)

observed while the order rested. Describes an outcome, never selects one.

### closest book gap ticks

| bucket | n | filled | fill rate |
|---|---|---|---|
| a <=0 | 19 | 11 | 57.9% |
| b 1 | 275 | 64 | 23.3% |
| c 2-3 | 115 | 32 | 27.8% |
| d 4-8 | 95 | 8 | 8.4% |
| e 9+ | 98 | 4 | 4.1% |

## Ranked by fill-rate spread

| feature | spread |
|---|---|
| pair_at_touch | 36.0% |
| price_band | 24.5% |
| residual_units | 17.3% |
| attempt_number | 16.9% |
| inventory_imbalance | 13.9% |
## The dominant split: above par

70.6% of our completion attempts are placed at a pair cost at or above $1.00, and those fill 10.8% of the time against 45.6% below par. That single split is most of the 17.7% headline fill rate.

- L5 for comparison — the PMUS shadow's complete counterfactual puts L5's own pair>1.00 share at 35.7% over 284,920 complete draws with inventory. Different denominator, so not a like-for-like ratio -- but our attempts sit above par about twice as often as his distribution implies.
- The gate already says this — the live fidelity gate fails action_distribution_relation and post_fill_complete_gap on exactly this: our ACTED complete share is 0.3959 against L5's 0.2858, and post-fill 0.6104 against 0.4375, a 17.3pp gap. The SAMPLED distribution matches L5 to L1 0.017 and PASSES -- the model is drawing correctly and the safety shell is converting other relations into completes (cap_fallback_attribution: ALL_CAP_FORCED).
- What that means, and does not — the over-completion is a consequence of caps that exist for good reasons, and this module does NOT propose changing any of them. It reports that the shell's forced completes land in the regime where completes do not fill, so the cost of those caps is now measured rather than assumed. What to do about it is an owner decision.

## This corrects my own earlier proposal

**this weakens the COMPLETE re-quote proposal I made earlier, and it should be read as a correction to it.** the re-quote replay found 66.7% of its counterfactual completions would land above par. Above-par completes fill at 10.8%. So the re-quote would concentrate new attempts in precisely the regime with the worst fill rate, which means the conversion assumption in that replay -- even at its 25% rung -- was probably too generous.

*the replay's SIGN argument stands on the per-unit comparison (-3.2 cents locked against -33.1 cents stranded), not on the conversion rate. This lowers the expected magnitude, not the sign, and it strengthens the case for settling it with the bounded A/B rather than the replay.*


## Against L5's own completion behaviour

- **L5's strongest:** TIME TO WORK IT. L5 pairs 93.3% of 15-minute windows against 65.7% of 5-minute ones, and within a window 82.9% when he opens with 80-100% of it left against 38.0% with under 20% left -- monotone across five buckets.
- **Ours on that axis:** UNMEASURABLE. All 871 acted decisions sit in one TTE bucket.
- **L5's second:** IMBALANCE. A balanced book (0.35-0.65) pairs 74.6% on 5-minute windows and 97.7% on 15-minute; lopsided (<0.20 or >0.80) pairs 49.7% and 33.3%.
- **Ours on that axis:** AGREES IN DIRECTION. Our fill rate peaks in the p45-55c band and falls away on both sides, which is the same shape measured on a different quantity (our own fill, not L5's completion).

*src/l5_residual_state_replay.py over 2,216 L5 windows*

## Candidate fidelity correction

**1. the completes we place are concentrated above par, where they do not fill** — BEHAVIORAL-FIDELITY FIX (evidence; the remedy is an owner decision)

pair-at-touch is the strongest measured predictor on the tape by a wide margin -- a 36-point fill-rate spread with a sharp cliff exactly at par -- and unlike TTE it is measurable, and unlike rest time it is known at placement.

Evidence:

- Below par the fill rate is 45.6%; at or above par it is 10.8%.
- 70.6% of our completes are placed above par, so the bad regime is where most of our attempts live.
- The live fidelity gate independently fails on the same thing: acted complete share 0.3959 vs L5's 0.2858, post-fill gap 17.3pp, while the SAMPLED distribution passes at L1 0.017.
- The gate attributes the acted-vs-sampled gap to the safety shell (ALL_CAP_FORCED), not to the model.

**Why fidelity, not strategy.** the model already draws L5's relation mix correctly. The clone diverges when the shell converts a draw into a completion. Bringing acted behaviour back toward sampled behaviour is cloning L5 more closely, not inventing anything.

**What this does not claim.** it does not claim the caps are wrong, and it proposes no change to any of them. Caps exist for reasons that predate this measurement. What is new is the COST: forced completes land where completes fill at 10.8%. That is now measured rather than assumed, and the trade-off is the owner's to make.

**The confound to rule out first.** above-par pair cost and thin far-side liquidity are the same condition seen twice: the far side is expensive BECAUSE nobody is there. So the low fill rate may be the market's shape rather than our pricing, in which case attempting fewer of them changes nothing except the count. Separating those needs the authenticated book on ordinary completes, which is why the instrumentation item below is not merely bookkeeping.

**2. record the authenticated book on every COMPLETE, not only on #93 control rows** (INSTRUMENTATION) — the owner asked for distance from the authenticated touch and the tape cannot answer it at 1.1% coverage. It is the cheapest item here, it unblocks the question rather than answering it, and it is what would separate 'our price was wrong' from 'the far side was empty' above.

**3. restore TTE resolution to the live state key for sports** (BEHAVIORAL-FIDELITY FIX, blocked on translation) — 871 of 871 acted decisions sit in one TTE bucket, so the clone cannot see the axis that is L5's STRONGEST completion predictor (82.9% to 38.0% across five buckets). Ranked third only because its effect here is UNKNOWN by construction -- an unmeasured axis cannot show a measured gain. L5's buckets are fractions of a 5- or 15-minute window; sports events run for hours, so this is a translation and not a re-cut. The shadow event-progress translator is where it would be measured, offline, before anything live.

## Read-only

No live treatment changes. The candidate below is offered for review, not applied.
