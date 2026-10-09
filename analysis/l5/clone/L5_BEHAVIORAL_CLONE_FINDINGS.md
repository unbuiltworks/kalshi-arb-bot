# L5 behavioral clone from observable fills — what is and is not identifiable

**Date:** 2026-10-09
**Corpus:** 760,817 L5 TRADE fills, 17,046 markets, 2026-03-19 → 2026-08-31
**Code:** `analysis/l5/clone/{extract,train,gbm,train_gbm,fit_final}.py`
**Evidence:** `analysis/l5/clone/clone_eval.json` (linear), `clone_eval_gbm.json` (trees)
**Live risk:** unchanged. E1 stays disarmed. No order endpoint is touched by any
file in this directory.

---

## 1. What was built

Every L5 fill was replayed chronologically **per market** (`conditionId`),
reconstructing at each fill the state that was observable at that instant:
per-side share counts, matched shares, directional residual, per-side VWAP and
the resulting pair cost so far, fill index, inter-fill gap, elapsed fraction of
the market's window and seconds to expiry, the fill's own price and size, and
the same-side run length.

The label is the **next observable action in that market**, stated relative to
the side just traded:

| label | meaning | base rate |
|---|---|---|
| `EXTEND` | next fill is on the same side | 70.8% |
| `COMPLEMENT` | next fill is on the opposite side | 27.0% |
| `STOP` | no further fill in this market | 2.2% |

`EXTEND` at 0.708 reproduces the regime-invariant P(EXTEND) ≈ 0.70 already
recorded in `CANONICAL_L5_MODEL.md` from an independent code path, which is the
sanity check that the replay is reconstructing the same process.

Two models were fit: multinomial logistic regression, then a histogram
multiclass gradient-boosted tree ensemble (both written out in numpy — neither
sklearn nor pandas is installed here). Both were fit **separately per regime**
with the break at 2026-06-23, and scored on two held-out sets: 20% of markets
held out by a stable hash of `conditionId`, and the **last 20% of each regime's
calendar span**. Nothing is pooled across the break.

## 2. Headline result (late regime, held-out trailing 20% of time)

| | accuracy | macro F1 | log loss |
|---|---|---|---|
| **clone (trees)** | **0.6937** | **0.4438** | **0.6270** |
| majority class (`EXTEND`) | 0.6865 | 0.2714 | — |
| always `EXTEND` | 0.6865 | 0.2714 | — |
| prior-matching guess | 0.5657 | 0.3333 | — |
| **naive pair bot** ("complement if residual, else extend") | **0.4937** | 0.3129 | — |
| class prior | — | — | 0.7211 |

Read accuracy with care: a 68.7% base rate makes accuracy nearly useless as a
discriminator, which is why log loss, macro F1 and per-class AUC are reported
beside it. The two honest statements are:

1. the clone reduces log loss 13% below the class prior (0.721 → 0.627) out of
   sample in time, so it carries real information about the next action; and
2. it beats the simplified pair-completion bot by **20 accuracy points and 13
   macro-F1 points**, which directly answers the question of whether a pair bot
   is an adequate stand-in for L5's policy. It is not.

The by-market and by-time splits agree closely (0.713 / 0.694 accuracy, macro
F1 0.451 / 0.444), so there is no market-identity leakage and the policy is
stable going forward within the corpus.

## 3. What we can reproduce confidently

**Market exit (`STOP`) — the strongest result.** AUC 0.959 out of sample,
precision 0.518 at recall 0.344 against a 2.9% base rate: an ~18× lift. When L5
is done with a market is close to deterministic in observable state — it is
driven by `t_frac`/`tte_s`, pair cost reached, and residual size. This is the
one behavior where the clone is genuinely faithful rather than merely informed.

**The persistent-incremental shape itself.** P(EXTEND) ≈ 0.708 in both regimes,
same-side runs, `COMPLEMENT` delayed rather than immediate, and a 27% completion
rate per decision point are all reproduced directly from the data rather than
assumed. The behavioral thesis in `L5_OPERATING_MODEL.md` survives this replay
unmodified.

**Where in state space L5 completes more often.** `pair_cost_sofar`,
`residual_shares`, `price` and `t_frac` are the top splitters in every fit, and
the `COMPLEMENT` probability sweep is monotone in precision: 0.416 precision at
threshold 0.30 (49% recall) rising to 0.540 at threshold 0.50 (4.8% recall)
against a 28.4% base rate. A 1.9× precision lift on completion is usable for
*prioritizing* completion attempts; it is not a reliable per-event predictor.

**Inter-fill timing, partially.** Out-of-sample R² on log inter-fill gap is
0.207 (late regime, time split) — material, and it means the clone can pace
itself roughly like L5 rather than firing on an arbitrary timer, which the
operating model explicitly asks for.

## 4. What is uncertain

**The `EXTEND` vs `COMPLEMENT` choice at any single decision point.** AUC 0.655.
The argmax recovers only 5.6% of actual completions at 52.6% precision. There is
real signal — log loss and macro F1 both beat every baseline — but anyone
treating a single predicted `COMPLEMENT` as "L5 would complete here" is
over-reading it. Deploy this as a ranked preference over candidate actions, not
as a hard decision rule.

**Next fill price.** R² 0.160, mean absolute error 0.199 USD/share. Weak. L5's
price *selection* is the behavior least recoverable from fills (see §5).

**Next fill size.** R² 0.094, and the constant-median baseline is competitive on
median error. Essentially unidentified. This matters less than it looks, because
the operating model already prefers **frequency before size**, and the clone can
take size as a risk parameter rather than a cloned behavior.

**Whether either regime's policy transfers to PMUS.** Nothing in this section is
evidence about our venue. The identified policy is L5's on Polymarket; the
economics of running it on PMUS remain governed by
`L5_ECONOMIC_DECISION_20261009.md`, where PMUS-specific maker adverse selection
is still **unmeasured** (the in-repo PMUS book data is a single 15-minute window,
132 BBO rows, 36% error rate, zero fills).

## 5. What is impossible to identify from public fills alone

These are not modelling failures; the information is absent from the corpus and
no feature here substitutes for it. Stating them is the point.

1. **Resting orders that never filled.** Quote placement, resting duration,
   cancel/replace behavior, how often he quoted and was not hit, and his true
   order-to-fill ratio. A fill is the only trace; an unfilled quote leaves none.
2. **Quote price relative to the book at placement time.** Whether a fill was
   maker or taker, where in the book his order sat, and his queue position are
   not in this data. Everything §4 says about price is *fill-conditional*, which
   is strictly weaker than price *selection* — the observed price distribution
   is the distribution of prices **at which he was filled**, which is selected
   against him by construction.
3. **Intended but unexecuted sequences.** A market where he quoted and got
   nothing is invisible, so the `STOP` label means "no further fill", never "he
   decided to stop". This selection bias inflates `STOP` identifiability to an
   unknown degree.
4. **Reason for any single action.** Signals, external hedges, inventory held off
   this venue, manual intervention, and any state not expressible in the 14
   observable features.
5. **Fee and rebate treatment per action.** Realized incentive credits are
   reconciled separately and must stay separate from trading P&L.

Historical order-book data was deliberately **not** joined into these features:
the only PMUS book tape in-repo does not overlap L5's markets or timestamps, and
current books do not reconstruct missing historical quotes. Where matched
Polymarket L2 archive windows exist, that join is the correct next increment,
and it is the only route to narrowing item 2.

## 6. Next step, and what it cannot be

The next increment is **continuous realtime shadow**: run the persisted late
regime policy (`analysis/l5/clone/models/l5_policy_late.pkl`) against the live
PMUS slate, emit intended orders with full state provenance, and measure what
fraction of them could realistically have executed given the book that actually
existed — queue position, time at touch, and fills that other participants took.
That measurement, not replay profitability, is what decides whether this policy
has positive realized expectancy on our venue.

It cannot be a live probe. No live order follows from this document.
