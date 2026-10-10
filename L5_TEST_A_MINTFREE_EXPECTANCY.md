# TEST A — mint-free full-inventory expectancy: **ECONOMIC NO-GO**

Script `analysis/l5/historical_l2/mintfree_expectancy.py`, output
`mintfree_expectancy.json`. Reuses the existing channel classifier
(`mint_contribution.channel`, `bbo_offset.placement_consistency`,
`mint_match.complements`) and the existing backfill. No pipeline rebuilt.

**Settlement is venue-sourced**, from the Zenodo parquet's own
`is_winning_outcome` — not inferred from a terminal book. All 12 markets
have it; none was skipped.

## 1. Method, and why subtraction would have been wrong

Mint fills are removed **from the share path before matching**, then the
remaining fills are replayed in block order with inventory, FIFO matching
against the opposite outcome, residual and settlement all recomputed.

This matters more than it sounds. The mint channel was not only buying
shares, **it was supplying the other side.** A RESTING_BID purchase of UP
was frequently paired against a DOWN share that arrived by mint; remove the
mint fills and that UP share has no complement at all — it becomes
directional residual carried to settlement, not a matched pair with its
profit deducted. Subtracting mint-channel profit from aggregate P&L would
have missed this entirely and produced a far kinder number.

## 2. Scope included

| | |
|---|---|
| markets with RESTING_BID fills | **12** (of 33 backfilled; L5 traded 12) |
| fills replayed | **2,045** |
| shares acquired | **45,510.1** |
| cost basis | **$21,752** |
| matched shares | 20,359.0 |
| residual shares | 4,792.1 |

Channel census over all 3,166 fills: RESTING_BID 2,045 / 45,510.1 sh ·
MINT_FEASIBLE 737 / 18,935.2 · ATOMIC_PAR_PAIR 350 / 12,802.6 ·
TAKER_SINGLE 21 / 461.7 · UNCLASSIFIED 12 / 758.1 · **AT_ASK_NO_MINT 1
fill / 0.9 shares.** Cross-checks against `mint_contribution.json` to
within 1% on purchased shares and 0.05% on matched shares.

## 3. The result

**Full-inventory net P&L, excluding incentives: −$695.42**

| | |
|---|---|
| per market | −$57.95 |
| return on notional | **−3.20%** |
| markets negative | **10 of 12** |
| 2026-08-21 (8 markets, 1,466 fills) | −$363.74 |
| 2026-08-22 (4 markets, 579 fills) | −$331.68 |

Both days negative, and the loss is not concentrated in one market: the
worst is −$305.51 and the best is +$275.15, with ten of twelve below zero.

## 4. Matched-pair contribution versus directional residual

Under unbounded FIFO: matched-pair −$275.86, residual −$419.60.

**But the split is a decomposition, not an economic fact, and the headline
is invariant to it.** Full-inventory P&L is settlement cash received minus
cost paid; the matching rule only decides how that total is *attributed*:

| matching rule | total | matched | residual |
|---|---|---|---|
| unbounded FIFO | **−695.42** | −275.86 | −419.60 |
| complement within 60s | **−695.42** | +198.23 | −893.67 |
| complement within 120s | **−695.42** | +343.18 | −1038.61 |
| complement within 300s | **−695.42** | −212.30 | −483.16 |

The total does not move by a cent. Any claim that "the pairs are profitable
and the residual destroys it" is an artefact of which rule you pick — which
is why the verdict rests on the total.

The economically real statement: **the mint-free matched pairs cost more
than a dollar.** Average pair cost exceeds $1.00 in 10 of 12 markets (up to
1.1141). The corpus-level finding that pair cost is below $1.00 on 63–78%
of two-sided *events* does not survive mint removal, because the sub-$1
pairs depended on both legs arriving together — and for ~40% of volume
that co-arrival **was** the mint.

## 5. Control: is the method manufacturing the loss?

| channels replayed | fills | shares | total |
|---|---|---|---|
| RESTING_BID only (PMUS-executable) | 2,045 | 45,510 | **−$695.42** |
| + MINT_FEASIBLE | 2,782 | 64,445 | +$34.37 |
| all channels | 3,166 | 78,469 | **−$70.60** |

The all-channel replay returns **−$70.60**, the same sign and order of
magnitude as the known aggregate (L5's August trading ex incentives was
−$537 on $4.61M notional). So the method is not biased toward loss — and
the mint-free subset is **≈10× worse than the full path**.

The one positive arm adds MINT_FEASIBLE, which is (a) explicitly **not
confirmed** as minting, (b) **not executable on PMUS** either way, and
(c) +$34.37 on ~$31k of notional — inside noise.

## 6. Sensitivity

**Channel classification.** MINT_FEASIBLE is *feasible*, not confirmed, so
it is the uncertain label. Treating all 18,935 of its shares as ordinary
maker fills moves the result from −$695.42 to +$34.37 — i.e. the sign of
the answer depends entirely on shares we cannot execute on PMUS regardless
of how they are labelled. ATOMIC_PAR_PAIR needs no such caveat: 145 atomic
two-sided transactions with pair-price histogram `{1.00: 145}`.

**Fees and execution.** L5's venue charged zero trading fee on these
markets, so the observed figures need no adjustment. Applying PMUS's own
coefficients (maker 0.0125, taker 0.0695, fee = coef·qty·p·(1−p)) to the
same share path — **hypothetical, never what L5 paid** — costs a further
$87.89 and gives **−$783.31**. Every realistic execution assumption makes
it worse: queue position, partial fills and adverse selection on PMUS are
all unfavourable relative to a replay that assumes L5's own fills.

**Observed versus hypothetical** is kept apart throughout: sections 2–5 are
L5's measured economics on L5's venue; only the fee arm is a PMUS
projection, and it is labelled as one.

## 7. Verdict: **ECONOMIC NO-GO for PMUS replication of this mechanism**

The only part of L5's acquisition PMUS can execute loses money at
full inventory, on L5's own venue, at L5's own fills, with zero fees and
venue-confirmed settlement: **−$695.42 over 45,510 shares, −3.20% on
notional, negative in 10 of 12 markets and on both days.** The result is
invariant to the matching rule and gets worse under PMUS fees.

This is not a residual-control problem that better engineering fixes. The
pairs themselves cost more than a dollar once the minting channel is
removed, and minting is the thing PMUS does not offer.

**No minimum executable PMUS test is proposed**, because the branch
condition for one was a positive result.

### What this does and does not rule out

Ruled out: replicating *this* mechanism — passive two-sided accumulation in
BTC15m — on PMUS, at this size, in this regime.

Not ruled out, and not evidence either way: L5's early (pre-2026-06-23)
regime, where pair costs were materially better (median 0.9581, 51.3% below
$0.96) and realized trading ex incentives ran ~$144/day. These 12 windows
are **August, the late regime**, and they are selected for Zenodo L2
overlap rather than at random. Testing the early regime would need L2
coverage we do not hold — that is the next evidence question, not a
conclusion from this one.

What remains firmly established from prior work: incentives, not trading,
produced L5's headline numbers ($71,552 lifetime, $61,022 in August), and
no equivalent incentive accrues to our account.
