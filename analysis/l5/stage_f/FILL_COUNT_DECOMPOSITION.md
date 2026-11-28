# The 122.7-vs-3.2 fills/window gap, decomposed

**Finding: the fill-count gap is not a bottleneck. It is an accounting
identity — `fills = notional ÷ size` — and it is P&L-neutral at fixed
capital.**

No new service, no limit loosened, no campaign requested. `maker_pair_ceiling`
left at 0.96. Canonical BTC15m net preserved at **+$16,436**.

---

## 1. The gap closes as a product, not a list of causes

| | L5 | us | ratio |
|---|---|---|---|
| notional per window | **$1,145.34** | **$3.0579** | **374.5×** |
| fills per window | **122.735** | **3.17** | **38.8×** |
| cash per fill | **$9.1539** | **$0.9657** | **9.5×** |

**38.8 × 9.5 = 367 ≈ 374.5.** The fill-count gap and the size-per-fill gap
**multiply to the notional gap**. Fill count is not an independent lever: it
is notional divided by size, and our notional is fixed by the authorized $12
cash envelope.

## 2. Component-by-component, with the share each explains

| # | component | measured | share of the gap |
|---|---|---|---|
| 1 | eligible opportunities | touch depth p50 **757 bid / 685 ask shares**; our order is **0.264%** of it | **0%** — not binding |
| 2 | orders attempted | **6.2/window** (31 over 5 captured windows); capped by `ts_round_allocations=4` × 2 legs + replacements | downstream of §5 |
| 3 | queue position / fill probability | live fill rate **58.1%** (18 of 31); `queue_ahead_multiple_of_need` **73×–537×** | real, but see §4 |
| 4 | duration / cancellation | **13 of 31 cancelled (42%)**, `ttl 120s`, venue-observed resting **21.9s and 38.9s** | downstream of §3 |
| 5 | **inventory-dependent sizing** | `env/allocs` = $12/4 = **$3/round** → `by_cash` **2.04 contracts** | **this is the fill-count determinant** |
| 6 | market/venue liquidity | as §1 | **0%** |

**Capital explains the whole of it**, because §5 sets size and §2–§4 are
consequences of how many $3 slices a $12 envelope holds. §1 and §6 are not
binding at all — we are a quarter of one percent of the median touch.

## 3. Closing the fill gap at fixed capital is P&L-neutral — by construction

Matched contracts are bounded by `notional ÷ pair_cost`, **not** by fill count.
Holding the authorized envelope fixed and varying only fill count:

| fills/window | contracts each | total contracts | **P&L** |
|---|---|---|---|
| 3 | 1.0730 | 3.219 | **$0.0557** |
| 12 | 0.2682 | 3.219 | **$0.0557** |
| 40 | 0.0805 | 3.219 | **$0.0557** |
| 122.7 | 0.0262 | 3.219 | **$0.0557** |

**Identical at every fill count.** Reaching L5's 122.7 fills/window on our
envelope means 0.0262-contract fills and exactly the same money. More fills
does not equal more profit here — it is the same notional, sliced finer.

## 4. And scaling capital scales the wrong sign

| | 7-window actual | × 374.5 (L5's notional) |
|---|---|---|
| matched | +$0.2616 | **+$97.97** |
| residual | −$0.3371 | **−$126.25** |
| **net** | **−$0.0755** | **−$28.27** |

That is **−$4.04 per window**, i.e. **−$387.77/day** at 96 windows or
**−$135.31/day** at L5's own 33.5 windows/day.

**Capital multiplies whatever sign you already have.** Ours is negative:
matched is positive but smaller than the residual drag. L5's is positive, and
**82–85% of his economics is an incentive stream credited to us at $0.00**.
Scaling our policy to his capital reproduces his *turnover*, not his *income*.

## 5. The single highest-impact controllable difference

**There isn't one at the authorized capital**, and I would rather say that
than nominate a weak candidate.

Of the six components, five are downstream of the cash envelope and the sixth
(liquidity) is not binding. The only lever that moves the notional gap is
capital, which is an authorization decision, not a code change — and §4 shows
that pulling it makes the measured loss larger, not smaller.

**So no narrow correction is tested, because the decomposition shows there is
nothing to correct.** Testing a fill-count change against the sequential
replay would return the invariance in §3 by construction; running it would
consume effort to re-derive an identity.

## 6. The exact missing evidence

**(a) L5's attempt count and maker/taker status — the decisive gap.**
His 122.7 fills/window are equally consistent with:

| attempts/window | fill rate |
|---|---|
| 122.7 | 100% (pure taker) |
| 245 | 50% |
| 1,000 | 12.3% (deep-queue maker) |

**All three fit every observable we hold**, because the public activity feed
carries **fills only — no order records and no aggressor flag**. So his
**attempt count (component 2) and fill probability (component 3) cannot be
separated from each other at all.** Ours are 6.2 attempts at 58.1%.

Without this, "L5 gets 122.7 fills" cannot be translated into a quoting
instruction: we do not know whether he is crossing the spread 122 times or
resting 1,000 orders and filling 12% of them. **These imply opposite
corrections.**

*What would supply it:* a venue-side or chain-side record carrying an
aggressor flag or order lifecycle for his account. We have searched the
available feed; it is not there. I am not proposing to acquire it.

**(b) Whether PMUS will credit this account maker incentives, and on what
terms.** Still `UNVERIFIED_NO_VENUE_CREDIT_RECORD` at **$0.00 credited**. This
is the 82–85% term and it remains the only quantity that changes the
conclusion.

**(c) `btc-ref-collector` holds nothing** — `latestDeployment: null`, it has
never deployed. Recorded for completeness: no BTC reference series exists to
revisit the spot-aware question.

## 7. Recommendation

**No code change. No live campaign. No capital increase.**

The fill-count gap was a real observation and it decomposes cleanly, but it
decomposes into capital — and capital applied to our measured per-contract
economics produces **−$387/day at 96 windows**, not profit. The two facts that
would change this are (6a) and (6b), and both are read-only questions about
information and venue terms rather than experiments to run.
