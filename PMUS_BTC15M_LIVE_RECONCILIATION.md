# Reconciliation: the R4/R5/R6 BTC15m live probes my audit missed

**My "zero prior BTC15m live orders" finding was wrong. You are right. The
probes are real, and I have now located the primary records.** No orders were
placed, nothing was deployed, and the disarmed service was not touched.

---

## 1. Why my audit missed them — five specific holes

Not a judgement call that went the wrong way. Five mechanical errors, each
sufficient on its own:

1. **I searched only the working tree of the current branch.** The R4/R5/R6
   artifacts live in `analysis/l5/rung_a/` on
   `origin/claude/l5-pmus-economic-results-7c116e`,
   `origin/claude/l5-latent-quote-shadow` and
   `origin/audit/grok-cell-c-fee-cashflow` — **none of which contains my
   branch's HEAD**. I never ran `git log --all` or `git ls-tree` on another
   branch, despite your instruction naming "GitHub branches" first.
2. **My grep required the literal token `"order_id"` and `.jsonl`.** The real
   records are **Markdown tables** using `order id` (with a space) and venue
   IDs like `CSDKAWYZCYBQ`. My pattern could not match them in any branch.
3. **I truncated the file list with `head -20`.** Even within the current
   branch the candidate list was cut before I filtered it.
4. **I tested for the literal `cpc-btc` on only the six files that survived
   that truncation** — all of which were sports cohorts. The count of zero was
   arithmetically correct and substantively meaningless.
5. **Name collisions gave me false confidence.** My branch has
   `analysis/l5/r4_orders.jsonl` and `analysis/l5/rejection_forensics.json` —
   both **sports**, from 2026-09-07 — which I read as "the R4 records" and "the
   rejection analysis". The real ones are
   `analysis/l5/rung_a/R4_TRADE_LEDGER_AND_SETTLEMENT.md` and
   `R4_REJECTION_FORENSICS.md` on another branch.

**Was the 1,445-order dataset limited?** Yes, on three axes at once: one
**branch** (mine), one **file type** (`.jsonl` matching `"order_id"`), and
therefore one **market family** (sports cohort exports from 2026-09-07 and
09-13). It was never a census of live orders. I presented it as one.

## 2. What is now source-backed, and what is not

### R4 — fully source-backed ✅

`analysis/l5/rung_a/R4_TRADE_LEDGER_AND_SETTLEMENT.md` (commit `683c559af`),
`R4_CHRONOLOGY.md` (`0441f8502`), `R4_REJECTION_FORENSICS.md` (`f719f783f`).
Market `cpc-btc-updown-15m-2026-09-29-0045z`, authorization
`OWNER-2026-09-29-NATIVEQ10-R4`, session `6cde5cb556ab4d3c`, journal 234
records. Every field below carries a venue GET as its source.

| | Order 1 | Order 2 |
|---|---|---|
| id | `CSDKAWYZCYBQ` | `CSDKDZZVRYAQ` |
| side | YES / **UP** | NO / **DOWN** |
| price | 0.2600 wire | 0.2700 wire = **0.73 own** |
| quantity | 10 | 10 |
| state | **`ORDER_STATE_FILLED`** | **`ORDER_STATE_REJECTED`** |
| cum / leaves | 10 / 0 | 0 / 0 |
| avg exec px | 0.2600 | — |
| commission | **−0.0200** | 0 |
| create → terminal | 00:46:35.897 → fill 00:46:46.377 (**10.7 s**) | 00:46:36.002 → reject 00:46:36.003 (**1.2 ms**) |

Combined intended pair cost 0.26 + 0.73 = **0.99**. UP settled worthless
(`settlement_px 0.0000`, `MARKET_STATE_EXPIRED`, terminal). **Loss
2.60 + 0.02 = $2.62.** Matches your figure exactly.

### R5 — order-level fully source-backed ✅, settlement not in the repo ⚠️

`analysis/l5/rung_a/R5_PDI_REJECTION_ANALYSIS.md` (commit `d4ae6b849`), window
`1115z`, all 13 orders with authoritative `GET /v1/order/{id}` plus 54
`native_tick` rows carrying the contemporaneous authenticated book.

**13 submitted → 8 FILLED, 1 EXPIRED, 4 REJECTED.** Order IDs `CSPEJNMW2YC6`
… `CSPF4HD6TYBY`, each with own price, wire price, submit timestamp to the
millisecond, and the book at decision *and* at arrival.

Your "8 fills" matches. The 80 contracts (UP 30 / DOWN 50) and the −$8.06
settlement are **not** in any committed artifact.

### R6 — only the pre-run manifest is committed ❌

`R6_Q10_MANIFEST.md` and `R6_Q10_MANIFEST_PREPARED.md` are **"PREPARED, NOT
ARMED"** design documents. I searched every one of the repository's remote
branches for an R5 or R6 result, outcome, settlement or ledger artifact and
**none exists**. Your R6 figures (40 UP / 40 DOWN filled, 40 matched pairs,
≈ +$3.35, plus an order that filled after process exit) are therefore not yet
reconciled against a primary source *in the repository*.

### Where the missing R5/R6 records are, and that they are recoverable

Two places, both named in the committed docs:

- **Volume journals** `/state/pmus-ws/nativeq10_r5_journal.jsonl` and the R6
  equivalent, on the probe service's volume.
- **Railway deploy logs.** I verified retention reaches that far by pulling
  real 2026-09-29T21:25 log lines from `l5-live-probe-oneshot`. The R4 doc
  names deployments `2d8a29dd` and `aa1f1ba2`; I have not yet identified which
  of the 59 services hosted the R5/R6 money path.

I did not brute-force all 59 services' logs unasked. Say the word and I will.

## 3. The rebuilt BTC15m execution audit

Replacing the sports-only audit. 15 real BTC15m orders across R4 and R5:
**9 filled, 1 expired, 5 rejected.**

**Fill latency is real and slow.** R4's filled order took **10.7 s** from
create to fill. Our replay has no latency model at all.

**Rejection is the dominant failure mode and has two distinct causes:**

- **Post-only cross from staleness (R5, 4 of 13).** Arrival-book PDI-cross
  predicts **all 13 outcomes exactly**; decision-book PDI-cross predicts
  **none** of the four rejections. The market moved 2–4 ticks in the ~2 s
  between our decision and the order's arrival. This is a *measurable,
  predictable* rejection mechanism and our replay models it nowhere.
- **Synchronous rejection at insert (R4).** 1.2 ms after creation, with
  **no reason exposed anywhere** by the venue (`/v1/order/{id}/status`,
  `/fills`, `/events` all 404). Five hypotheses were disproved from the raw
  records — outcome mapping, the NO↔wire inversion, IOC/FOK semantics, tick
  and minimum-size validation, market state. **Only insufficient collateral
  remains unexcluded**: the second leg needed $7.30 on top of the first's
  $2.60, and no balance snapshot was taken at submission.

**That last point is the most important execution finding in this document.**
A two-sided pair acquisition appears to require collateral for **both legs
simultaneously**, un-netted. If so, pair completion can fail for reasons that
have nothing to do with flow, queue position or placement — and in R4 the
complement **never reached the book at all.**

**Complement completion:** R4 0 of 1. R5 filled on both sides but ended
unbalanced (your UP 30 / DOWN 50). R6 is the only run claiming clean matched
pairs, and is the one with no committed record.

**Cancellation safety: verified sound.** R4's window-end sweep reported
`CANCEL_FAILED` on both orders — which is the correct venue response to
cancelling an already-FILLED and an already-REJECTED order. Close-out
reconciled against the venue (`reconciled: true`, `authority: VENUE`,
`open_orders_at_venue: 0`). The deadman behaved.

## 4. Conclusions that must be reassessed

| conclusion | status now |
|---|---|
| "Zero prior BTC15m live orders; the execution model has never been validated on this market" | **WITHDRAWN.** 15 orders across two runs, plus R6. |
| "The replay models no rejections" | **Strengthened, and worse than I said.** Live BTC15m rejection rate is 5/15 = 33%, against 18.7% in sports. Two mechanisms, neither modelled. |
| "The live translation produces TAKER orders, so the engine would place ~zero maker orders on BTC15m" | **Narrowed.** True of the *currently deployed, maker-only-overlaid* `l5-pmus-live`. The R4/R5 probes ran a different path (`l5_pmus_live_oneshot` / lock5) and placed real resting GTC maker orders. My claim was about one service, stated as though about the venue. |
| "Queue-ahead was never recorded at placement" | **Holds, and matters more.** R5 captured the book at decision *and* arrival, which is what made the PDI diagnosis possible — but still no queue position. |
| PMUS BTC15m NO-GO (persistent queue, no deep edge) | **Not overturned by this, but its weight drops.** It rests on book structure, which these probes do not contradict. However, R4/R5 show the binding live constraints were **rejection and staleness**, not queue position — so the NO-GO was answering a question that was not the live bottleneck. |
| "Phase 2 should be preceded by six logging additions" | **Holds, with one removed and one added.** R5 already captures decision-and-arrival books. Add: a **balance snapshot at submission**, without which the R4 collateral hypothesis stays unresolvable. |

## 5. What I recommend now

1. **Recover the R5/R6 primaries** from the volume journals and the Sept-29
   service logs, so the −$8.06 and +$3.35 are source-backed rather than
   recalled. This is read-only.
2. **Resolve the R4 collateral hypothesis.** If a two-sided pair needs
   un-netted collateral for both legs, that changes the economics of every
   pair-completion policy we have modelled, and it is testable from account
   balance history with no orders.
3. **Re-aim Phase 2.** The live bottleneck is decision-to-arrival staleness
   and rejection, not placement depth. A probe measuring fill rate by tick
   offset answers the wrong question; the right one measures rejection rate
   against book age at submission.

I will not propose a probe design until 1 and 2 are done.

## Labels

No order was submitted. No credential value appears here. `l5-pmus-live`
remains disarmed and unmodified. R4 figures are venue-confirmed and realized;
R5 order-level outcomes are venue-confirmed; R5 settlement totals and all R6
figures are **user-reported and not yet reconciled to a primary source** and
are labelled as such wherever they appear above.
