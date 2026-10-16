# Could our engine have made money on prices that actually existed?

**Answer: yes, but only with the maker rebate and selective completion, by a
margin of fractions of a cent per contract — and the fill rate that carries it
is Kalshi's, not PMUS's.**

**Verdict: CONDITIONAL GO on one bounded probe.** This is the first time the
evidence has supported one. The conditions are specific and listed at the end.

Reproduce: `python3 -I scripts/l5_retrospective_maker_opportunity.py`

## What I found that I had not used

`data/policy_d_port/` + `_ext/` + `data/ref_lag/` — **191 MB, 105 tapes,
1,364,720 rows** I had not touched: **734,769 prints**, 260,571 two-sided
BBO snapshots with depth, 33,772 settlements, 4,085 window definitions.

Decisively, every Kalshi print carries **`taker_side`**. A print with
`taker_side: "no"` is a taker selling YES into the YES bid, so it **fills a
resting YES buyer**. That is a direct observation of maker execution, not an
inference from quote movement — which is exactly what the Gemini tape lacked
and why that pass had to assume trade-through.

**There are no PMUS prints anywhere in the repo.** The venues present are
Kalshi (`KXBTC15M`/`KXETH15M`/`KXSOL15M`/`KXXRP15M`/`KXDOGE15M`) and Gemini.
I am not going to call Kalshi data a PMUS measurement.

## Level 1 — actual PMUS executed and settled

From the production cost-basis audit: 1,911 fill rows, 47 of 49 legs
cost-established, measured cost $81.3862 against $291.05 of placeholders,
account realized −$233.9854.

**Every leg is `UNKNOWN_OWNER` with `l5_owned_fills: 0`. There is no actual
PMUS maker-strategy result to report, profitable or otherwise.**

But Level 1 does settle the input that decides everything below — **the maker
rebate is real on our account, at the magnitude the model uses**:

| leg | qty | price | modelled `0.0125·q·p·(1−p)` | **venue paid** |
|---|---|---|---|---|
| `cpc-btc-…-1230z\|UP` | 5 | 0.13 | $0.00707 | **−$0.0100** |
| `cpceth-…-0330z\|UP` | 50 | 0.02 | $0.01225 | **−$0.0100** |
| `cpceth-…-1230z\|UP` | 5 | 0.18 | $0.00923 | **−$0.0100** |

831 credited-rebate rows, commission sum **−$5.88**. Θ_maker = −0.0125 is
confirmed on our own account to within the cent, and the venue rounds to the
**nearest** cent.

## Level 2 — conservative counterfactual (Kalshi, PMUS fee constants)

**4,751 decisions over 73 settled markets.** A resting maker is filled **only**
when observed opposite-side taker volume exceeds the queue that was ahead of
it **at placement**. Queue-ahead is never re-read; depth shrinking is not
advancement (a cancel ahead of us cannot be told from a fill ahead of us);
unknown depth never fills; touch is never fill. Completion crosses at the
**first book after the fill**, never the best price in the window.

Median quoted pair cost **0.9900**, median spread **0.0100**.

### Per contract, exact fees, CONSERVATIVE queue

| size | both-fill | HOLD, no rebate | COMPLETE, no rebate | COMPLETE, **with rebate** |
|---|---|---|---|---|
| 1 | **81.81%** | −$0.028543 | −$0.000284 | **+$0.002758** |
| 10 | 81.27% | −$0.002990 | −$0.001335 | **+$0.001699** |
| 100 | 77.10% | — | −$0.001616 | **+$0.001368** |

**Without the rebate the strategy is negative at every size.** With it, it is
positive at every size. The margin is the rebate, and nothing else.

Selective completion is worth far more than holding: at one contract it moves
the one-sided leg from **−$0.200336** to **−$0.044959**. That reproduces the
earlier completion finding on PMUS-shaped fees and real queues.

### The cent-rounding trap, caught again

At one contract, cent-rounding the rebate inflates it **3.2× at p=0.50 and
8.9× at p=0.10**, and the entire matched edge on a 1¢ spread is $0.01 per
pair — so the rounding is the same size as the thing being measured. The
cent-rounded run reads **+$0.016851** per attempt; exact reads
**+$0.002758**, a 6× difference. Every figure above is exact.

## Level 3 — the minimum fill probability required

| policy | rebate | **required both-fill** | measured | |
|---|---|---|---|---|
| HOLD | no | 95.41% | 81.81% | NOT MET |
| COMPLETE | no | **82.33%** | 81.81% | **NOT MET by 0.5pp** |
| HOLD | yes | 93.85% | 81.81% | NOT MET |
| **COMPLETE** | **yes** | **76.93%** | **81.81%** | **MET by 4.9pp** |

At 10 contracts the requirement with rebate and completion falls to **69.26%**
against 81.27% measured — **MET by 12pp**, the widest clearance in the study.

## What this does and does not establish

**Does:** on real two-sided books with real prints, real queue positions and
PMUS fee constants, a patient two-sided maker with selective completion and
the credited rebate clears its break-even requirement by 5–12 points. The
rebate magnitude is confirmed on our own account. Completion beats holding by
~16¢ per one-sided contract.

**Does not:** transfer the fill rate to PMUS. Gemini measured **37.59%** and
Kalshi **81.81%** on the same discipline — a **44-point spread**, which proves
the rate is a property of venue liquidity, not a constant. PMUS's own live
heartbeat (`bid_shares` 511–523, `ask_shares` 5,578–7,642, `book_updates`
838–1,449 per window) looks far closer to Kalshi than to Gemini, but that is a
qualitative read, not a measurement.

**Three further limits, stated:**

1. **Outcome is terminal market consensus, not venue settlement.** 73 of 182
   markets resolved unambiguously (yes_bid ≥ 0.99 or the complement); the rest
   stayed UNKNOWN and were excluded rather than guessed.
2. **It degrades with size.** Both-fill falls 81.81% → 77.10% and per-contract
   edge falls +$0.00276 → +$0.00137 as size goes 1 → 100. Deeper size means
   longer queues.
3. **The scale is brutal.** At +$0.0017/contract, **$480/day needs ~282,000
   contracts/day.** The historical target is not reachable by this mechanism
   at any capital we have discussed. The honest ceiling is single-digit
   dollars per day at our current caps.

## Verdict

**CONDITIONAL GO on one bounded probe**, whose purpose is to measure the PMUS
fill rate, not to make money. Conditions:

1. **Maker-only on both legs**, one market, one pair at a time.
2. **Selective completion enabled** (`L5_TS_COMPLETION_CROSS`), because
   holding is negative in every cell above. Completion crosses only when the
   avoided loss exceeds the executable cost, which
   `completion_decision` already enforces.
3. **≥10 contracts per leg** if the cash cap allows, so the rebate is not a
   cent-rounding artifact and the requirement sits at 69% rather than 77%.
   Current `max_cash_deployed_usd` is **$1**, which does not permit this —
   raising it is your decision and I am not proposing a number.
4. **Measure, do not evaluate P&L.** The probe's output is the observed
   both-fill rate against the 69–77% requirement. At our caps the P&L is
   fractions of a cent and tells us nothing.
5. **Abort conditions unchanged:** HALT latch, deadman cancellation by venue
   id, the four-bucket accounting, and
   `L5_MAX_UNRESOLVED_UNKNOWN_USD`.

**I am not authorizing this and have not changed anything.** HALT stays
latched, `L5_SHADOW_ONLY` stays set, `L5_TS_COMPLETION_CROSS` stays unset, all
limits unchanged, no order submitted. The 47-leg cost-basis correction stays
prepared and unapplied.

If you would rather not spend capital at all: the same number can be obtained
read-only by setting `PMUS_WS_LADDER_MARKETS` on the existing recorder and
replaying this script's logic against retained PMUS ladders and prints. That
costs nothing and risks nothing. It is the cheaper path to the same answer.
