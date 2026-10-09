# Is $1.08–1.12 the venue, or our rules? — six-policy replay

**Data:** this service's own observation tape, **222 BTC up/down markets,
7,630 observations**, replayed chronologically at cycle 1620 (2026-10-09
14:26Z). Split by market first-observation time: 111 in-sample / 111
out-of-sample. Commit `b8ce4b40d`. Everything is REPLAY; nothing realized.
E1 disarmed, no risk limit changed, no ceiling relaxed (still 0.96).

**Replay discipline** (tested, `tests/test_pmus_l5_pair_policy_study.py`):
chronological; a touch without volume never fills; volume inside the queue
never fills; only the excess over the queue **at placement** is ours; a
missing ladder is never an empty queue; a print between the touches is not
attributed to us; pre-entry drift reads only earlier rows; an unclosed
residual is scored to the **end of the tape**, not the last fill; fees
charged where the policy crosses; residual P&L **excluded** for want of a
settlement feed.

## Answer: the 1.08–1.12 figure is our completion rule, not the venue

A sub-$1.00 — even sub-0.96 — pair **is** attainable on this venue. It is
just rare, and the price of insisting on it is that most entries never
complete.

| rank | policy | entries | completion | median pair cost | frac <1.00 | frac ≤0.96 | residual left open | time at risk (median) | adverse sel. t+30 | fees | net/entry (matched only) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **P6 INVENTORY_AWARE** | 38 | 21.1% | **0.940** | **100%** | **100%** | 79% | 614 s | −0.100 | 0 | **+$0.0174** |
| 2 | **P3 BOTH_SIDES_RESTING** | 38 | 50.0% | 0.990 | **100%** | 0% | 50% | 448 s | −0.090 | 0 | **+$0.0050** |
| 3 | P5 SELECTIVE_ENTRY | 35 | 48.6% | 1.030 | 29% | 6% | 51% | 288 s | −0.040 | 0 | −$0.0217 |
| 4 | P2 DELAYED_MAKER | 38 | 47.4% | 1.020 | 28% | 11% | 53% | 243 s | −0.065 | 0 | −$0.0213 |
| 5 | P4 ACCUMULATE | 38 | 60.5% | 1.027 | 17% | 9% | 39% | 177 s | −0.040 | 0 | −$0.0608 |
| 6 | P1 IMMEDIATE_TAKER | 38 | **100%** | 1.050 | 8% | 5% | **0%** | **0 s** | −0.030 | $0.446 | **−$0.0833** |

Out-of-sample (same order holds, but only **6 entries** — not validation):

| policy | entries | completion | median pair cost | net/entry |
|---|---|---|---|---|
| P6 | 6 | 16.7% | **0.83** | +$0.0283 |
| P3 | 6 | 16.7% | 0.99 | +$0.0017 |
| P2 | 6 | 50% | 1.07 | −$0.0500 |
| P4 | 6 | 66.7% | 1.08 | −$0.0767 |
| P5 | 5 | 40% | 1.13 | −$0.0680 |
| P1 | 6 | 100% | 1.08 | −$0.1273 |

### What the ranking actually shows

1. **Completion cost is monotone in patience.** Cross immediately → 1.05.
   Wait 120 s as a maker → 1.02. Complete only when the budget is met →
   **0.94**. The live 1.08–1.12 reading sits exactly where a
   complete-when-you-can rule puts it, so it measured our rule.
2. **But the cheap pair is rationed.** P6 reaches ≤0.96 on **100% of the
   pairs it completes** and completes only **8 of 38 entries**; 79% of
   entries end carrying a one-sided residual. P3 doubles completion to 50%
   at the cost of landing at 0.99 — under a dollar, above the ceiling.
3. **Fees are not the problem.** P1's $0.446 over 38 entries is 1.2¢/pair;
   its 1.05 median pair cost is what makes it worst by a wide margin.
4. **Patience buys pair cost and pays in adverse selection.** The two
   cheapest policies have the *worst* markouts (−10¢ and −9¢ at t+30 vs
   −3¢ for the immediate-taker). Waiting gets you a better price on the
   fills you get, and the fills you get are the ones the market has moved
   through. The two effects are in tension, and neither is a free lunch.
5. **Selective entry did not help.** Skipping entries whose pre-entry drift
   was against us (P5) removed 3 of 38 entries and left median cost
   *worse* than plain delayed-maker (1.03 vs 1.02). A 60-second drift sign
   is not a usable adverse-selection filter on this data.
6. **Accumulation made it worse, not better.** P4's weighted average came
   to 1.027 with residuals up to 3 shares: adding same-side lots before
   completing raises the average you then have to beat.

## Does any policy produce positive net expectancy? Not demonstrably.

P6 (+1.74¢/entry) and P3 (+0.50¢/entry) are **positive on matched legs
only**, and both leave 50–79% of entries holding a directional residual
whose P&L cannot be computed — PMUS publishes no resolution for expired
crypto windows (the closed-market sweep finds **0 of 18** held markets,
`/v1/markets/{slug}` is 404). A policy that leaves residuals is not cheap,
it is **unmeasured**. Scoring those residuals at anything other than
"unknown" would be the one assumption that could manufacture a GO.

Worked arithmetic for P6 in-sample: 8 completed pairs × (1 − 0.94) ≈
+$0.48 of pair edge, against 30 entries carrying 1 unfilled share each.
Those 30 residuals need to average better than **−1.6¢/share** for the
policy to be net positive. At the measured −10¢ markout at t+30, they do
not — but a markout is not a settlement, and that is precisely the gap.

## Verdict

**The BTC NO-GO stands.** The reason has changed, and that matters:

- Before: "the pair cannot be assembled below $1.00 on this venue."
  **That was wrong as stated**, and this study corrects it — P6 assembles
  at 0.94 in-sample and 0.83 out-of-sample, 100% at or below ceiling.
- Now: *a sub-ceiling pair is attainable on roughly 1 entry in 5, and the
  other 4 leave a residual we cannot value because the venue publishes no
  resolution.* The binding blocker has moved from pair cost to
  **settlement observability**.

Out-of-sample is 6 entries. That is not validation of anything, so no
policy change is promoted and no live probe follows.

## What would change the verdict

1. **A settlement path** — the single gating item now. Options, in order of
   honesty: the authenticated position-resolution activity feed (covers our
   fills only, so it needs live trading); or deriving BTC up/down outcomes
   from the underlying reference price the repo's `btc-ref-collector`
   already records, which models the venue's own mechanical resolution rule
   and must be labelled `SETTLEMENT_INFERRED`, never venue-confirmed.
2. **≥100 out-of-sample entries** for P6 and P3, which at the observed
   rate is on the order of a day of tape rather than 25 minutes.
3. **A residual exit measurement.** P6's profile is "cheap pairs, many
   residuals", so its expectancy is decided by what a residual is worth —
   which is item 1 again.
