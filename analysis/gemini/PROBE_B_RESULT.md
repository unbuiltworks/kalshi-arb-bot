# Probe B — BTC5m q=1 maker_improve, 4 windows (2026-09-23)

Authorization `OWNER-2026-09-23-GEMINI-Q1-AB-2`. Spec `btc5m_q1_improve` /
`bfe7ba23…` (frozen, unchanged). Run 35851903528, armed step 11:35:13–11:53:52Z.
Terminal state `STOPPED_WINDOWS_EXHAUSTED`, "the authorized 4 window(s) have
been observed". No preregistered stop fired.

**The evidence persisted this time.** `cohort_run_B_20260923T113513Z.json`,
the latch, `cohort_live_status.json(.jsonl)` and the ledgers are all committed
— the allowlist fix verified end to end on the path that lost Probe A's
artifact.

**The terminal settlement sweep did real work:** `windows_settled_by_this_sweep:
1`. Under the old code that window would have ended PENDING and the loss would
have read lower than it is.

## Per-window

| | W01 `…1140` | W02 `…1145` | W03 `…1150` | W04 `…1155` |
|---|---|---|---|---|
| budget consumed | **12/12** | 7/12 | 0/12 | 8/12 |
| mutations (NEW/CANCEL) | 7 / 4 | 3 / 1 | 0 / 0 | 4 / 1 |
| participation | 11:35:43→11:39:40 (237 s) | 11:42:10→11:43:25 (75 s) | — | 11:51:08→11:52:29 (81 s) |
| fills | 2 | 1 | 0 | **3** |
| first fill | YES 0.30 @11:37:18.6 | NO 0.21 @11:43:08.7 | — | YES 0.47 @11:51:32.8 |
| subsequent fills | YES 0.05 @11:39:02.5 | — | — | NO 0.27 @11:52:16.5; NO 0.03 @11:53:23 |
| **opposite acquired** | **no** | **no** | n/a | **YES — latency 43.7 s** |
| trajectory | OPEN→EXTEND (YES×2) | OPEN only | none | OPEN→EXTEND→**COMPLETE**→EXTEND |
| matched inventory | 0 | 0 | 0 | **1 pair** |
| effective pair cost | undefined | undefined | — | **0.62** (YES 0.47 + NO avg 0.15) |
| terminal residual | +2 YES | −1 NO | 0 | −1 NO |
| resolution | no | yes | yes | yes |
| settlement-implied | **−0.35** | **−0.21** | 0 | **+0.23** |
| termination | window withdrawn tte 12.1 (budget also 12/12) | window withdrawn tte 28.8 | **book unreadable 5 cycles / 63.8 s** | **risk gate: "a fill would take NO to 3, past 2"** |

Every one of the six fills reconciles against the public tape at the same
second and price (W04: 11:51:32 UP 0.47, 11:52:16 UP 0.73 = NO 0.27,
11:53:23 UP 0.97 = NO 0.03).

## Cohort economics

Gross fill spend **1.33**. Settlement-implied net, all four now settled:
**−0.33** (−0.35 −0.21 +0 +0.23). Loss stop 0.56 of 10.00 at exit, drawdown
0.56 of 5.00 — neither approached. `fees_usd` recorded 0 but **unverified**;
rebates `None` — none credited, none assumed.

**Nothing here is REALIZED.** No attributable cash movement has been read.

## For every first-side fill

### W01 — first fill YES 0.30 @11:37:18.6

- Opposite desired immediately after: **NO, action COMPLETE, 0.63** (cycle 6,
  tte 156.7) — submitted 11:37:27.585, **9.0 s** after the first fill.
- Submitted opposite prices and lifetimes: 0.63 live **39.4 s** (cancelled
  11:38:06.963) → 0.70 live **40.5 s** (cancelled 11:38:53.103) → 0.77 placed
  11:39:40.286, live to withdrawal (~8 s).
- **Gap with no opposite resting at all: 11:38:53.1 → 11:39:40.3 (47.2 s)**,
  while budget ran down.
- Pair cost of each desired opposite: YES vwap 0.175 + 0.63/0.70/0.77 =
  **0.805 / 0.875 / 0.945 — all ≤0.96.**
- **≤0.96 prevented nothing.** **CAP/risk prevented nothing.**
- Controller chose **COMPLETE** on NO from cycle 6 to cycle 13 — it sought the
  complement continuously. EXTEND was chosen on the YES leg only.
- Opposite public print while participating? **None.** The contract carried 3
  prints total, 2 of them ours; the only other (11:36:23) predates our first
  fill. No counterparty appeared at any price while we quoted NO at 0.63–0.77.
- Termination: withdrawn at tte 12.1 — the natural window end, with budget
  simultaneously at 12/12.

### W02 — first fill NO 0.21 @11:43:08.7 (rested 58.6 s before filling)

- Opposite desired after: **YES, action COMPLETE, 0.69** (cycle 3, tte 105.1)
  — submitted 11:43:24.799, **16.1 s** after the fill.
- Live from 11:43:24.8 to withdrawal at tte 28.8 (~66 s). No reprice.
- Pair cost: 0.21 + 0.69 = **0.90 ≤0.96.** Not blocked. CAP not reached.
- Controller chose **COMPLETE** on YES, EXTEND on NO (0.01, then 0.12).
- Opposite public print while participating? **Yes — and it moved away.**
  11:43:27 (17.17 @ UP 0.88) and 11:44:07 (41 @ UP 0.88) printed while our YES
  0.69 rested. The market traded YES at 0.88, 19 cents *above* our bid.
- Termination: natural window end, **5 of 12 slots unspent** — not budget.

### W04 — first fill YES 0.47 @11:51:32.8, and the complement

- The NO leg was **already resting at 0.27 from the OPEN** (11:51:07.793).
- It **FILLED at 11:52:16.5 — 43.7 s after the first fill.** This is Probe B's
  one opposite-side acquisition, and it required no reprice: the complement
  order placed at OPEN was still live when the market came to it.
- After the YES fill the controller repriced YES 0.47 → 0.31 (EXTEND).
- A second NO filled at 0.03 (11:53:23), taking NO to 2.
- Matched 1 pair at **0.62**; residual −1 NO.
- **≤0.96 prevented nothing.** **CAP/risk DID bind:** the window ended on
  "risk gate refused the rung: a fill would take NO to 3, past 2" — `max_no`
  = 2 from the Q1/CAP2 envelope, with 4 slots still unspent.
- Settlement `yes`: 1 YES pays 1.00 against 0.77 spend → **+0.23**.

## maker_improve versus the unmodified passive price — UNRESOLVED

**Every submitted price equals the book bid exactly**, confirmed by the
venue's own acks: 0.2500 vs bid 0.25, 0.4300 vs 0.43, 0.3000 vs 0.30, 0.0500
vs 0.05, 0.6300 vs 0.63, 0.7000 vs 0.70, 0.7700 vs 0.77. Across all 26+
placement rows, `from_bid = 0.00` and `inside_spread = False`.

`ab_arms.apply_A` records `native_price` and `improved` on every action it
touches — True *or* False. **The artifact contains zero occurrences of either
key.** The recorded decision therefore predates the placement step, so absence
of the marker does not prove the rule did not run.

Two readings remain, and this evidence cannot separate them:

1. the controller's native price was the bid, improvement was skipped or
   no-oped, and `maker_improve` did nothing; or
2. the native price was bid − 1 tick and improvement moved it **to** the bid —
   a real improvement, from behind the touch to at the touch.

**So Probe B cannot answer its own headline question.** This is an
instrumentation gap, not a strategy finding: `native_price`/`improved` are
produced and then dropped before capture. No fix is implemented here.

What *is* determinable: **no fill required a price better than the prevailing
bid.** All five fills occurred at the touch.

## Against the preregistered mechanical hypothesis

The question was whether `maker_improve` increases opposite-side acquisition
and recycling, or merely increases one-sided exposure.

- **Opposite-side acquisition occurred in 1 of 3 traded windows**, at 43.7 s,
  producing 1 matched pair at 0.62 — the first matched inventory this
  programme has produced live.
- It did **not** come from repricing. The complement that filled was the
  original OPEN order, untouched, still resting when flow arrived.
- One-sided exposure also occurred (W01 +2 YES, W02 −1 NO), so the answer is
  not "purely one-sided".
- **The mechanism credited cannot be `maker_improve`**, because whether any
  price was improved is unresolved above. The one complement is attributable
  to *an order still being alive when a counterparty arrived* — persistence,
  not placement.

**No A-versus-B ranking is drawn from six windows,** and none is offered.

## Venue state after B

`orders_live_after_stop` = 0 for all four windows. A credentialed
`fill_forensics` + `static_readiness` reconcile (run 35858423640) is in flight
and will confirm zero working orders and zero unexplained position from the
account side; until it lands, the zero-working-orders claim rests on the
driver's own withdrawal record.

## Two accounting observations, neither an over-spend

1. **A window that spends nothing returns its ledger to the pool.** W03 was
   granted `…-W03`, spent 0, and recorded "forfeited 12"; W04 was then handed
   the *same* `…-W03` ledger and spent 8. `…-W04` remains untouched at 0/12.
   No market ever received a partial ledger and total consumption is 27 of 48,
   so this is conservative — but it contradicts the artifact's own
   "forfeited" note and the documented "the unspent slots die with this market".
2. **Event-derived mutation count is 11 for W01 against the ledger's 12.**
   The ledger is authoritative; the twelfth action is not visible in the
   recorded event list. Recorded rather than reconciled away.
