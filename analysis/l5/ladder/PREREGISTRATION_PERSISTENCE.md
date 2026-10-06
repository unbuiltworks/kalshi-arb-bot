# Preregistration — persistent resting vs dynamic reposting

**Fixed before any L5 return behaviour is computed.** S3 untouched and
frozen at `8b20a259…87df`. No live orders.

## 0. The confound that makes the control the entire test

The prior frozen study established that **L5 places at the touch** (median
offset 0 ticks at every level rank 1–8, `rho(rank, offset) = 0.138`), and
that apparent ladder depth is largely accumulated touch travel.

Therefore, when the touch **returns** to a price `p`, an L5 fill at `p` is
expected under **both** hypotheses:

| | |
| --- | --- |
| **H-PERSIST** | the earlier exposure was still sitting at `p` |
| **H-REPOST** | L5 simply places at the touch again, and the touch is now at `p` |

**A high return-fill rate alone cannot discriminate.** Only the **control** —
levels with an equivalent excursion/return path but **no prior L5
consumption** — separates them: under H-REPOST prior use is irrelevant, so
treated and control return-fill rates must match.

This is recorded first because it determines the whole design, and because
a result reported without the control would be meaningless.

## 1. Episode definition — fixed, and blind to subsequent L5 fills

Episode eligibility is determined from the **touch path** and the **initial**
fill only. Nothing downstream of the excursion enters the definition.

1. **Anchor.** Price level `p` is first associated with an L5 maker fill
   while the touch is within the **frozen near-touch set `{−3 … +4}`
   ticks** of `p` (the T_SET band from `c65bf29f`, 80.1 % coverage).
2. **Excursion.** The touch subsequently moves **≥ D = 6 ticks** away from
   `p`, and stays ≥D away for a **minimum excursion duration of ≥ 10 s**.
3. **Return.** The touch later comes back to within the **return tolerance
   of ±1 tick** of `p`.

### Frozen parameters and their provenance

| parameter | value | provenance |
| --- | --- | --- |
| near-touch anchor set | `{−3 … +4}` ticks | T_SET, frozen study `c65bf29f` |
| **D** (excursion distance) | **6 ticks** | the S2/S3 displacement threshold (0.06 in price), and cleanly outside the near-touch set |
| minimum excursion duration | **10 s** | above the archive's 1 s resolution, below L5's 18 s inter-fill p95 |
| return tolerance | **±1 tick** | strictest available on a $0.01 grid |
| adjacent-return band | **±2 ticks** | reported separately, never pooled — see §0 |
| measurement horizon after return | **60 s** | matches the markout horizon set already in use |

### Eligible-window rules

- all 12 BTC15m windows; **bids only** (the crossed-book defect is
  ask-side, and the archive's own `crossed` column is read and reported);
- the touch must be archive-resolvable throughout the episode, with no
  unresolvable gap exceeding `MAX_CARRY_S`;
- **≥ 60 s of window must remain after the return**, else the episode is
  classified `INSUFFICIENT_SUBSEQUENT_OPPORTUNITY` and excluded from rate
  denominators;
- maker fills only; atomic par-pair legs excluded.

## 2. Outcome classification — one per episode

| class | definition |
| --- | --- |
| `SAME_EXACT_PRICE_RETURN` | L5 maker fill at exactly `p` within 60 s of return |
| `ADJACENT_PRICE_RETURN` | L5 maker fill within ±2 ticks of `p`, but not at `p` |
| `OPPOSITE_OUTCOME_FILL` | L5's next fill is on the complement outcome |
| `NO_L5_FILL` | no L5 maker fill in the band within 60 s |
| `INSUFFICIENT_SUBSEQUENT_OPPORTUNITY` | window ended too soon (excluded) |

**Primary measurement: time from market return to first L5 fill at the old
level.** Reported as a distribution, not only a rate.

## 3. Controls

For each treated episode at `p`, control levels `p'` are drawn from the
**same window and same outcome** such that:

- the touch was within the near-touch set of `p'` at some earlier point,
- the touch then excursed **≥D** from `p'` for **≥10 s**,
- the touch returned within ±1 tick of `p'`,
- **and L5 never filled at `p'` before that excursion**;
- matched on excursion distance within **±2 ticks** and on return time
  within **±120 s** of the treated episode's return.

The ±120 s return-time match is deliberate: it prevents comparing an active
L5 phase against an inactive one, which would manufacture an effect from
L5's own duty cycle rather than from level persistence.

## 4. Primary endpoint and bars

**P1.** Is a previously used L5 level disproportionately likely to produce
another L5 maker fill when the market revisits it?

*Bar:* `P(same-exact-price return fill | treated)` ÷ the matched control
rate **≥ 2.0**, with **n ≥ 30** treated episodes, and the same direction in
**≥ 9/12** windows that contribute ≥2 episodes.

**P2 — the especially important prediction.** If a latent 80-share clip was
only **partially** consumed before the excursion, `p` should be more likely
to fill on return than if the inferred clip was **fully** consumed.

*Bar:* `P(return fill | partial) − P(return fill | full) ≥ 0.10`, with
**n ≥ 30** pooled in each arm, and consistent sign in **≥ 8** of the windows
where both arms have ≥3 episodes.

"Full" = inferred clip total ≥ `80 − 0.75` shares. "Remaining latent
quantity" = `80 − consumed`, defined only where the clip total < 80, and
treated throughout as an **inference from the consumption lattice, not an
observed order size**.

## 5. Covariate dependence

Return-fill probability against: prior partial vs full consumption ·
estimated remaining latent quantity · excursion distance · excursion
duration · inventory state · TTE. Reported as Cliff's delta with
per-window sign agreement. No covariate threshold is searched.

## 6. Verdict classes

Exactly one of:

- **CONSISTENT_WITH_PERSISTENCE** — P1 passes **and** P2 passes
- **CONSISTENT_WITH_REPOSTING** — P1 fails (treated ≈ control), i.e.
  repeated near-touch exposure is the simpler description
- **NOT_DISCRIMINATED** — P1 passes but P2 fails, or samples are too small
  to evaluate either bar
- **UNIDENTIFIABLE** — the episode construction yields too few eligible
  episodes, or the archive cannot resolve the touch paths

## 7. Standing limit, stated before any result

**A returning fill is never proof that the original order remained
resting.** Historical data cannot establish order identity: no order id, no
placement event, no cancel is observable. Every positive result here is
*consistent with* persistence and does not demonstrate it. The verdict
names will carry that wording.
