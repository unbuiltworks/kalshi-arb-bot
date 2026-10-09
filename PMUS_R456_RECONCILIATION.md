# R4/R5/R6 reconciliation, collateral test, and strategy implications

Read-only throughout. No orders, no arming, no deployment, no service change.

**Label key.** `[VENUE]` = venue-confirmed via authoritative GET or an
authoritative balance snapshot. `[RECON]` = reconstructed arithmetic that
closes exactly against a `[VENUE]` figure. `[HYP]` = hypothesis. `[OPEN]` =
unresolved.

Search scope, stated so it can be checked: **336 remote branches** enumerated
with no truncation, plus Railway service logs. Committed R5/R6 *result*
artifacts do not exist on any branch — the primaries are Railway logs and the
volume journals, and that is where these figures come from.

---

## 1. R4 — `cpc-btc-updown-15m-2026-09-29-0045z`

`[VENUE]` Authorization `OWNER-2026-09-29-NATIVEQ10-R4`, journal
`/state/pmus-ws/nativeq10_r4_journal.jsonl` (235 records), ledger 5 rows /
2 commits / 2 links. Mandate consumption **$9.90**.

| | UP `CSDKAWYZCYBQ` | DOWN `CSDKDZZVRYAQ` |
|---|---|---|
| submitted | 00:46:35.675345 | 00:46:35.909277 (**+234 ms**) |
| price | 0.26 wire | 0.27 wire = 0.73 own |
| qty | 10 | 10 |
| terminal | **FILLED** 00:46:46.377 (**+10.701 s**) | **REJECTED** 00:46:36.003 (**+94 ms**) |
| cum / leaves | 10 / 0 | 0 / 0 |
| commission | −0.0200 | 0 |

`[VENUE]` **Both legs were submitted while the book was FLAT** — a
simultaneous two-sided quote, not OPEN-then-COMPLETE. The rejection therefore
cannot be caused by held inventory or a residual cap. The UP leg then rested
**10.7 s as a maker** and filled.

`[VENUE]` Settlement `settlement_px 0.0000`, `MARKET_STATE_EXPIRED`,
terminal — the long (UP) side settled worthless; **DOWN won**.

`[RECON]` Realized = −2.60 on the UP premium. With the commission as a credit
(see §4) that is **−$2.58**; as a charge, −$2.62. **Your reported −$2.62
assumes a charge; the R5 cash anchor says credit.** `[OPEN]` No pre-R4 balance
snapshot exists, so the $0.04 cannot be closed from primaries.

## 2. R5 — `cpc-btc-updown-15m-2026-09-29-1115z` — **−$8.06 CONFIRMED**

`[VENUE]` Authorization `…-R5`, session `0d12518dff114691`, journal
`nativeq10_r5_journal.jsonl` **1,637 records**, sha256
`4d395bdb…`, 557,393 bytes, ledger 50 rows / 13 commits / 13 links.

`[VENUE]` **13 submitted → 8 FILLED, 1 EXPIRED, 4 REJECTED.**
`cumulative_submitted $62.70`, `filled_commitment $38.30`, `open_reserved 0`,
`nonterminal 0`, **`clean: true`**.

`[VENUE]` Fills (`GET /v1/order/{id}`): `CSPEJNMW2YC6` up 10 @ 0.50 = 5.00;
`CSPEJ9AG8YAQ` down 2 @ 0.49 = 0.98 (+8 more later); `CSPE7YNY6YB5` up 10 @
0.45 = 4.50; `CSPENGF2MYC3` up 10 @ 0.39 = 3.90; `CSPF4H8JRYBY` down 10 @
0.50 = 5.00; `CSPF4Q6E6YBQ` down 10 @ 0.49 = 4.90; plus `CSPEJNQ68YC6` down
10 @ 0.54 and `CSPF4Q834YBQ` down 10 @ 0.47.

`[RECON]` **UP 30 / DOWN 50 = 80 contracts, cost exactly $38.30** — closes to
the cent against the `[VENUE]` mandate consumption. Your figures match.

`[VENUE]` Settlement `settlement_px 1.0000` — **UP won.**

`[VENUE]` **Authoritative cash anchor: 54.58385 → 46.52385 = −$8.06000.**
Your −$8.06 is confirmed exactly.

`[RECON]` 30 UP × $1.00 = $30.00 − $38.30 = −$8.30 trading, +$0.24
commissions ⇒ −$8.06. The 20-contract DOWN over-weight is what lost the
money: 30 matched pairs plus a 20-contract losing residual.

## 3. R6 — `cpc-btc-updown-15m-2026-09-29-1815z` — **+$3.55, not +$3.35**

`[VENUE]` Authorization `…-R6`, session `f26594b086d74eb6`, journal
`nativeq10_r6_journal.jsonl` 211 records, sha256 `a3bfa6af…`, 103,621 bytes,
**re-hashed after the audit and unchanged**. Ledger 94 rows / 15 commits.

`[VENUE]` Mandate consumption **$36.50** against a $40.00 per-market cap;
the next order was refused `max_spend_per_market_exhausted` (already_spent
36.50, proposed 4.70, cap 40.00). Settlement `settlement_px 1.0000` — **UP
won.**

`[VENUE]` **Cash anchor: 46.52385 → 250.07425 with `depositReservation 200`,
i.e. displayedCash 50.07425. Both derivations give +$3.55040**
(250.07425 − 200 − 46.52385, and 50.07425 − 46.52385).

`[RECON]` 40 UP × $1.00 = $40.00 − $36.50 = +$3.50 gross, + ~$0.05
commissions ⇒ **+$3.5504**. Consistent with your 40 UP / 40 DOWN and **40
matched pairs at an average pair cost of $0.9125** — the one run that locked
genuinely sub-$1 pairs live. **Your +$3.35 is $0.20 low**; the primaries say
+$3.5504. `[OPEN]` Which of the two treatments produced $3.35 is not
determinable from the logs.

### The post-exit fill — **the deadman did NOT hold**

`[VENUE]` The journal carries `probe_error: 1`; the process exited at
18:17:27. Order **`CSWG9ZB98YCA`** (DOWN 10, own 0.69 / wire 0.31) was left
`LIVE_AT_VENUE`. A **separate read-only recovery at 18:43:53 — 26 minutes
later** — found it already `ORDER_STATE_FILLED`, avgPx 0.6900, commission
−0.0300, and booked it. Pre-recovery state: `orders_uncertain: "gross/net
divergence; incoherent book"`, `uncertainty_cleared: false`.

**This is the most important safety finding in this report.** R5's sweep was
clean — `CSPERKXEYYB5` was cancelled at window end,
`state ALREADY_TERMINAL / ORDER_STATE_EXPIRED`, `open_orders_after 0`. R6's
was not: an abnormal exit bypassed the sweep entirely and an order traded
unsupervised. `open_orders_at_venue: 0` after recovery reflects that it
**filled**, not that it was cancelled.

## 4. Collateral hypothesis — **REFUTED**

I previously said collateral was "the only hypothesis not excluded". That was
reasoning by elimination, which you told me not to do. Here is the direct
evidence.

`[VENUE]` Balance 73 minutes after the R4 rejection: `buyingPower 54.58385`,
`currentBalance 54.58385`, **`marginRequirement 0`, `balanceReservation 0`,
`openOrders 0`, `unsettledFunds 0`**.

`[RECON]` Pre-R4 balance ≈ **$57.16–57.20**. Required for both legs:
**$9.90** `[VENUE]`. **Coverage ≈ 5.8×. Insufficient collateral cannot
explain the R4 rejection.**

Answers to your three sub-questions:

- **Independent collateral for both sides?** `[VENUE]` Yes, in the sense that
  each leg reserves its own premium — but this is cash funding, not margin.
  `marginRequirement` is 0 in every snapshot; there is no margin system.
- **Offset for matched positions?** `[VENUE]` **No.** R6 paid full premium on
  both sides, $36.50 for 40 pairs. A pair costs the sum of its legs, which is
  the economics itself, not an extra constraint.
- **Is reservation per-order and transient?** `[VENUE]` Yes. The engine logs
  "releases only the UNFILLED remainder of this order's own reservation; a
  fill is permanent", and R5 submitted **$62.70 cumulative against ~$54.8 of
  cash** precisely because reservations released on terminal.

### So what did reject R4's DOWN leg? `[OPEN]`

`[VENUE]` R5 established the mechanism for *its* four rejections: post-only
cross from decision-to-arrival staleness. **Arrival-book cross predicts all
13 R5 outcomes exactly; decision-book cross predicts none of the four
rejections.** `[HYP]` The same mechanism is the leading candidate for R4.

`[OPEN]` It cannot be tested. R4's 215 `native_tick` rows carry the
contemporaneous book, but they live in the volume journal and the Railway logs
echo only record *counts*, not book contents. Reading them needs volume
access, which would mean deploying — out of scope here. **R4's rejection cause
is UNRESOLVED, with collateral now refuted and PDI untested.**

## 5. Strategy implications

| area | what changes |
|---|---|
| **Pair-completion feasibility** | **Demonstrated live.** R6 achieved 40 matched pairs at $0.9125. Pair completion on PMUS BTC15m is not infeasible — it has been done. |
| **Capital per market** | `[VENUE]` ~$36–38 of cash per window at clip 10 for ~40 pairs. Reservations are transient, so cumulative submission can exceed cash ($62.70 vs ~$54.8). The binding limit was our own $40 per-market cap, not the venue. |
| **Maker-only acceptance** | `[VENUE]` Resting maker orders ARE accepted and DO fill: R4's UP rested 10.7 s; 8 of 13 R5 orders filled. My earlier claim that the engine "would place ~zero maker orders" was about the one maker-only-overlaid service and is wrong as a venue statement. |
| **Rejection rate and latency** | `[VENUE]` 5 of 28 orders rejected (17.9%) across R4+R5+R6. Fill latency 10.7 s (R4) and seconds-scale (R5). Rejection is ~94 ms, synchronous. Decision-to-arrival staleness of ~2 s is the measured cause in R5. |
| **Residual exposure** | `[VENUE]` R5's 20-contract DOWN over-weight caused the entire −$8.06. R6 at 40/40 made +$3.55. **Residual imbalance, not pair cost, is what decides the outcome.** |
| **Actual net economics** | `[RECON]` R4 −$2.58, R5 −$8.06, R6 +$3.55 ⇒ **net ≈ −$7.09 over three runs.** |
| **Validity of the BTC replay conclusions** | **Weakened further.** The replay models neither rejection nor staleness nor latency, and all three are live first-order effects. The queue-structure NO-GO is not contradicted, but R6 proves sub-$1 matched pairs are achievable, which the replay's 14–40% completion rates understated. |

## 6. Recommended next bounded step

**Not a placement-depth probe.** The live binding constraints are
decision-to-arrival staleness, rejection, and an exit path that leaked a live
order. In priority order, none requiring new strategy:

1. **Fix the R6 deadman gap first — before any further live order.** An
   abnormal exit must cancel or quarantine outstanding orders. This is a
   safety defect with a demonstrated live instance, and it is the only item I
   would treat as blocking.
2. **Recover R4's arrival book** from the volume journal (read-only) to close
   the last rejection cause. It needs volume access; say if you want that.
3. **Then** a staleness-calibration probe: fixed small clip, measure rejection
   rate against book age at submission. That tests the mechanism the evidence
   actually implicates, with the R5 arrival-book rule as the preregistered
   predictor.

## Corrections to my own prior statements

- "Zero prior BTC15m live orders" — **withdrawn**; 28 orders across three runs.
- "Insufficient collateral is the only unexcluded hypothesis" — **refuted** by
  balance evidence; coverage was 5.8×.
- "R4 loss $2.62 matches exactly" — **−$2.58** if commissions are credits,
  which R5's cash anchor implies.
- "The live engine would place ~zero maker orders on BTC15m" — true only of
  the one overlaid service; **false as a venue claim.**
- "Cancellation safety verified sound" — **true for R5, false for R6.**
