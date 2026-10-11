# Concurrency-aware replay — does the cap bound inventory?

The replay now models what we actually trade: an accepted order **rests** until
its observed fill, cancel or expiry, and later decisions can be made to see it.
The previous replay applied every accepted action to inventory instantly, which
is why it could not produce the 1130z overshoot and must not have been used to
pick a cap.

**No live rule is proposed here. k, the floors and the live policy are unchanged
and the executor is DISARMED.**

## 1. Gate: does it reproduce 1130z?

**Yes — 56/56 decisions, exactly.**

The 1130z order tape was rebuilt from the captured `L5_LIVE_LIFECYCLE` stream:
38 submissions with client order ids, 253 lifecycle events, and the two orders
that resolved through the adoption path under their venue ids
(`CRDMDRXHTYCA` 0.55, `CRDM3GM6TYC6` 0.33, both DOWN, both filled 11:41:46).

| | replay | live |
|---|---|---|
| eligible decisions | 56 | 56 |
| submitted | 38 | 38 |
| `RESIDUAL_ABOVE_CAP` | 18 | 18 |
| decisions matching | **56/56** | — |
| final realized residual | **−1.15** | −1.15 (settled record) |
| booked fills | UP 6.59 / DOWN 7.74 | UP 6.59 / DOWN 7.74 |

Commission is `0.0000` on every one of the 253 lifecycle events, corroborating
the zero-fee finding independently of the cash reconciliation.

### It also resolves the question left open by the probe

**Peak realized `|residual|` was 3.24 against a cap of 1.80 — an 80% overshoot.**
The post-probe estimate of −1.71…−2.17 was built from leg-cost totals assuming
uniform fill prices and was too low.

| time | side | fill | residual |
|---|---|---|---|
| 11:41:39 | DOWN | 0.90 | −1.46 |
| 11:41:42 | DOWN | 0.90 | **−2.36** |
| 11:41:46 | DOWN | 0.33 | **−2.69** |
| 11:41:46 | DOWN | 0.55 | **−3.24** peak |

## 2. The parameter the old replay was missing

From the 38 live orders: submit → first fill **median 36s** (p25 17s, p75 69s,
max 281s); 30 of 38 filled; **0.858** of submitted contracts filled. The old
replay assumed that interval was zero.

## 3. What 1130z can and cannot decide

It validates the reproduction. It **cannot score alternative arms**: any arm
that *takes* an action the live run refused has no observed fills for it, so
its inventory is under-counted and its peak is meaningless. An arm comparison
on this tape would be measuring the absence of data. That is the D/U/V boundary
in its hardest form, and it is why the arm comparison below is run on the
corpus, where taking an action yields L5's own recorded fill.

## 4. Arms over the frozen corpus, concurrency-aware

2,216 settled windows, chronological halves, 5 seeds on the latency draw.
Repair asymmetry is absolute in every arm: a leg that reduces `|realized
residual|` is never refused, at any depth, whatever is outstanding.

### Held-out half

| arm | act ret | ct ret | mk ret | matched$ | resid$ | net$ | maxDD | vs NAT |
|---|---|---|---|---|---|---|---|---|
| D NATIVE | 1.000 | 1.000 | 1.000 | +3726.5 | −2524.4 | −876.1 | 2279 | +0.0 |
| A k2 realized-only | 0.831 | 0.798 | 0.819 | +1877.8 | −693.6 | −646.1 | 1523 | +230.0 |
| B k2 prospective (side) | 0.615 | 0.609 | 0.582 | +1495.8 | −106.0 | −212.6 | 1315 | +663.5 |
| **B' k2 prospective (net)** | 0.802 | 0.783 | 0.791 | +2088.9 | **+32.7** | **+351.2** | 1103 | **+1227.3** |
| C k3 prospective (side) | 0.678 | 0.665 | 0.643 | +1742.4 | −1071.0 | −1092.0 | 1936 | −215.8 |

### Against the state-blind control at each arm's own take rate

| arm | DEV vs its control | OOS vs its control |
|---|---|---|
| A k2 realized-only | **+725** | −199 |
| B k2 prospective (side) | −477 | −545 |
| **B' k2 prospective (net)** | **+584** | **+551** |
| C k3 prospective (side) | +812 | −534 |

`B'` — prospective, netting all outstanding orders — is the **only** arm that
beats trading-less in both halves, and the only arm with positive net and
positive residual economics out of sample. `C` (k3) is worse than `B'` on every
axis out of sample and is not a replacement.

### D/U/V, held-out half

| arm | Δ determinate | Δ uncertain |
|---|---|---|
| A | +168.8 | −439.6 |
| B | +353.7 | +4.2 |
| **B'** | **+543.8** | +502.3 |
| C | +49.2 | −1258.7 |

`B'`'s advantage is determinate-positive; `A`'s and `C`'s depend on uncertain
fills and fail that test.

## 5. The result that matters more than the ranking

**No arm bounds inventory. Not one.**

| arm | windows | breached | % | worst |
|---|---|---|---|---|
| D NATIVE | 2183 | 917 | 42.0% | 9.93× cap |
| A k2 realized-only | 2180 | 720 | 33.0% | 7.42× |
| B k2 prospective (side) | 2169 | 425 | 19.6% | 6.66× |
| B' k2 prospective (net) | 2171 | 581 | 26.8% | 6.83× |
| C k3 prospective (side) | 2169 | 214 | 9.9% | 5.40× |

"Breached" = peak realized `|residual|` exceeded `k ×` the **largest** clip
that ever filled in that window, i.e. the most generous cap ever in force.
Prospective accounting roughly halves the breach rate. It does not eliminate it.

### Why — a traced window, arm B

| t+s | side | qty | realized | prospective | clip | cap | verdict |
|---|---|---|---|---|---|---|---|
| 0 | DOWN | 50.00 | 0.00 | 0.00 | 50.00 | 100.00 | take — **FLAT_OPEN** |
| 0 | DOWN | 55.26 | 0.00 | −50.00 | 55.26 | 110.53 | take — **FLAT_OPEN** |
| 15 | DOWN | 31.16 | 0.00 | −105.26 | 31.16 | 62.32 | take — **FLAT_OPEN** |
| 18 | DOWN | 50.00 | 0.00 | −136.42 | 50.00 | 100.00 | take — **FLAT_OPEN** |

Peak realized 186.42 against a final cap of 110.53 — 1.69×. Two compounding
causes, both structural:

1. **An opening leg is exempt by construction, and it is classified on
   *realized* residual.** During an opening burst realized residual is still
   0, so every order in the burst is `FLAT_OPEN` and none is ever measured.
   "Flat" is a lie while four orders rest.
2. **When nothing has filled, the cap is defined by the candidate's own size**
   (`clip = max(max_clip, qty)`, and `max_clip` is 0). So each new order
   re-authorises itself: two 50-lot opens give a 100-lot cap and 105 lots of
   exposure.

Classifying on prospective residual instead of realized (tested) cuts the
`FLAT_OPEN` exemption from 7.6% to 3.5% of actions and breaches from 33% to
24% — real, and not a fix. A monotone cap (`clip` = largest *filled* clip only)
was also tested and does not help: 30.9% / 20.4% / 27.0%.

Two hypotheses tested and **rejected** rather than asserted: repair legs
inverting the residual past the far-side cap accounts for **0%** of first
breaches (100% are same-side accumulation), and cap non-monotonicity is not
the driver either.

## 6. The questions that were asked

**Should outstanding heavy-side orders consume capacity available to new
residual-increasing orders?** Yes. It is necessary — breaches roughly halve,
and the net-prospective arm is the only one that beats trading-less in both
halves with determinate-positive economics. It is **not sufficient**: the
opening burst escapes any such rule while realized residual is still zero.

**Does repair asymmetry survive?** Yes, and it costs nothing here: it is not a
breach path (0% of first breaches). A repairing leg was never refused in any
arm, at any depth, with any amount outstanding.

**Side-only or net prospective?** Net (`B'`). Side-only (`B`) is far more
suppressive (0.615 action retention vs 0.802) and loses to its control in both
halves. Netting pending repairs against pending heavy-side adds retains more
and scores better.

## 7. Limits of this comparison

The latency model is a draw from 30 live observations in a single market, and
the corpus trades clips ~50× our live size, so dollar magnitudes are
corpus-scale, not probe-scale. Five seeds; the seed spread is not a confidence
interval on live behaviour. Suppressing an action removes its fills and leaves
every other fill as recorded — the standing assumption, and the reason the
D/U/V split is reported separately.

Nothing here is a recommendation to change k, the floors, or the live policy.
