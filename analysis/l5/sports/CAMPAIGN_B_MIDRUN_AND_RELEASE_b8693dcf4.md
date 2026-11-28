# Campaign B mid-run (00:10–00:37 UTC 26 Nov) and integrated release b8693dcf4

Read-only campaign figures come from production logs, saved in
`scratchpad/campB`. Modelled figures are THEORETICAL.

## 1. Productivity, first 27 minutes

| | |
|---|---|
| Fills | 0. The 23:45–23:49 window had 4 (`quioli` 2 YES @ 0.46/0.45, 2 NO @ 0.49/0.46), paired at 0.95 and 0.91: +$0.14 locked, not yet settled |
| Markets quoted | 2 in 27 min: `atpdb-delanedelane` (pregame; 16 orders in 4 ladders) and `setkameua-kovvea-misole` (pregame; 8 orders). `garcar` completion only |
| Released while quoting | `delanedelane` 00:16 (reselected 00:28), `kovvea-misole` 00:37, `kimdmy-elfiva` 00:12 |
| Qualifying resting | pregame 1.08 contract-hours of 1.19 resting (91%); live 0 of 0.45 |
| Capital in use | $1.27 resting on average, about 10% of the ~$12.3 room; $0 resting 00:14–00:27 |
| Blocks (RN1_QUOTE lines) | jump pause 32, volatility pause 17, net not positive 12, max markets 2 |
| Requests | one order deferred by the request budget; no 429; latch null; reconciliation clean; unexplained cash 0 |

**Main cause of idle time:** the estimate flips sign once our own bids
rest. For example, `delanedelane` was +$0.34/h before its bids rested and
−$0.01/h one pass after. `kovvea-misole` flipped four times between
+$0.007 and −$0.027. The bid at the candidate price was counted as
competition against itself, so the market was blocked and then
released. This is the self-competition defect reported in issue #251.

## 2. Reward-scoring corrections (dev/chatgpt-rn1-net-read-guard, 16aecb0fb) — reviewed and integrated

| Change | Review |
|---|---|
| A failed or unexpected net read returns not-qualified when net ranking is on (it used to return qualified) | Correct: fail-closed for openings. Completions are unaffected (the net block stops openings and extensions only). Side effect: a flat market can be released after 120 s of failed reads; acceptable |
| `quote_estimate` takes separate YES and NO quantities; the resting estimators pass each side's actual remaining size | Correct. It used to apply the larger side's size to both |
| `_net_rows` deducts our own remaining size at the candidate price, counting only owned, open, non-orphan orders, capped at the visible level | Correct. The cap handles a cancel or fill the public book already shows. Other ladder levels stay in the denominator (conservative) |
| **Added here:** an order younger than `PMUS_RN1_NET_OWN_LAG_S` (5 s) is not deducted | Covers the latency gap the review flagged: a just-acknowledged order may not be in the public snapshot, so deducting it would remove a competitor's size (optimistic) |

Ownership is checked against `own_order_ids`, the same set the bot uses
for foreign-order detection. Partial fills: remaining = qty − filled,
capped at the visible size. Cancel race: there is no pending-cancel state
(cancels are confirmed in the same pass), and the visible-size cap covers
a cancel the book already reflects.

Tests: 265 targeted tests pass. Full suite: 5,711 passed, the same 24
unrelated failures (`regression/full_suite_integrated_16aecb0fb.txt`). New
tests: failed and stale reads fail closed (2, from the branch); own-size
deduction with partial fill and foreign and closed orders (1, from the
branch); a young order is not deducted; deduction capped at the visible
level; the reward estimate uses each side's own size (an existing test was
updated).

Code `2618113e7`. Release
`b8693dcf4bc269f2d4c8576f99e123377e6fabbf`, branch
`release/pmus-sports-rn1-b8693dcf4`. Not deployed.

## 3. Is the running campaign worth continuing?

Yes, to 02:10 as authorized. It costs almost nothing: no fills, worst
case bounded by the $10 loss stop, $1–4 resting. It adds qualifying time
on the doubles and Setka programmes (rows post 1–7 days later). It does
not show profitability: at about $1.3 resting, modelled reward is roughly
$0.1–0.3 an hour. The binding defect is fixed in the release; deploying
it mid-window is a money-capable change, so it needs authorization.

## 4. Next release for continuous accumulation

Release `b8693dcf4` with the FINAL TERMS settings unchanged. Expected
effect: markets stop flipping and being released once our bids rest, so
resting and qualifying time should rise towards the full two-market
allowance. No limits change.
