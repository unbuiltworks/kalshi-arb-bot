# Stage 1 accumulation audit — 2026-11-26T23:45Z to 2026-11-27T01:13Z

**Status label:** MEASURED FROM LOGS (live probe, read-only audit). Nothing here is realized P&L. Fills are
venue-reported fills; pairs are locked pair economics awaiting settlement.

- Release: `31ddf1b06` (deployment `7bc972bf`), sports service. Window measured: 23:45:00 to 01:13:00Z (88.0 min, 5,280 s). The campaign was still running when this was written.
- Sources: Railway deploy logs, `PMUS_TWO_SIDED` JSON lines (ORDER, FILL, RN1_QUOTE, ORDER_NOT_SENT, place_refused_*,
  reactive_pull, jump_pause, VOL_PAUSE, SELECTED/RESELECTED, released_flat_market, heartbeat, ACCOUNT_RISK, PILOT_PNL).
  Heartbeat lines were read only for the fields named below.
- Method: I replayed the release's RN1 reconcile rule (`_rn1_step`, ts.py 6181-6300) against the logged
  RN1_QUOTE targets to get open-order intervals. The rule keeps a bid that matches a target within 1 tick and holds no more than the target size. It also keeps
  bids on a side in `refill_cooldown` (when nothing blocks the market) and keeps every opening during `NET_HOLD_S=300` after `net_not_positive`.
  RN1_QUOTE is logged only when the decision changes (ts.py 6156-6163), so the decision state is constant between lines.

## Data gaps (read first)

1. **Routine cancels are not logged in this release.** `_cancel_legs` emits nothing per order, so cancels are
   inferred from the reconcile rule above: 51 at a target change or block, 30 at NET_HOLD expiry, plus 14 from reactive_pull lines.
   As a check, 3 of the 16 FILLs hit orders the replay had already cancelled. Each was a fill that raced a cancel (timleg YES ×2 at 00:47:52, paryur NO at 00:48:35).
2. Order TTL expiry (`ORDER_TTL_S`) and venue-side closes are not visible in these logs. Resting time may be overstated.
3. Environment values were **not** read (no list-variables). Caps and constants come from the brief plus source defaults. The heartbeat
   shows `campaign: {"mode":"off"}` (no runtime campaign reader) and a `caps` block of `per_market 1.50 / qty 1`. Those are the
   legacy two-sided caps, not the RN1 caps.
4. Headroom comes from 83 heartbeats (about 60 s apart). Lane/at-risk figures are approximations (see §3).
5. **Unresolved slot-accounting anomaly.** With `PMUS_RN1_SLOTS_COUNT_INVENTORY=1` and `MAX_MARKETS=2`, any market should be
   `max_markets`-blocked while two or more *other* markets carry unpaired legs (ts.py 5214-5245). From 00:57 to the end, 4–5 markets
   carried unpaired legs. Even so, 27 opening/extension decisions passed the slot check, among them kobvit 00:59:46 and kuzdmy 00:59:48, which placed
   extensions. Over the same minutes, paryur and savvol were refused with `max_markets`. A "resting-bid-only" slot model does not fit these
   decisions either. The bot's in-memory `done`/order-state is needed to explain it; it is not in the logs.

## Summary (measured)

| Item | Value |
|---|---|
| Markets touched | 12 (7 placed orders) |
| ORDERs | 110 — rn1_open 73 (110 ct), rn1_extend 17 (20 ct), two_sided_complete 20 (24 ct) |
| FILLs | 16 rows, 24.39 ct — open 9 (14.91 ct), extend 3 (4.48 ct), completion 4 (4.00 ct) |
| Pairs locked | timleg-karpol 4/4 (avg YES 0.485 + avg NO 0.470 = 0.955 before fees). Awaiting settlement, NOT realized |
| Unpaired at 01:13 | kobvit YES 1, paryur NO 7.20, savvol NO 5.19, kuzdmy YES 2; heartbeat unpaired $7.18 |
| Wall time with zero resting opening/extension bids on any market | 416 s (7.9%) |
| Mean market-sides with a resting add bid | 2.14 |
| Mean active markets | 3.51; mean markets with an unpaired leg 1.75 (≥2 for 30% of the window) |

## 1. Replacement latency (FILL → next ORDER, same market and side)

| Fill (UTC) | Market | Side | Qty | Stage | Next same-side ORDER | Reason if never replaced (following decisions) |
|---|---|---|---|---|---|---|
| 23:51:56 | kobvit | YES | 1 | open | 23.6 s (extend) | — (cooldown 10 s, then 6 s request-budget deferral) |
| 00:46:34 | paryur | NO | 2 | open | 87.8 s (extend) | — (jump_pause, then max_markets, then refill) |
| 00:46:34 | paryur | NO | 1.72 | open | 87.8 s (extend) | — |
| 00:47:52 | timleg | YES | 2 | open (raced cancel) | never | jump_pause 660 s, vol_pause 520 s, late:set_S3 213 s, account_risk 104 s |
| 00:47:52 | timleg | YES | 2 | open (raced cancel) | never | same |
| 00:48:15 | paryur | NO | 1.73 | extend | never | max_markets 1,310 s; account_risk 104 s; wanted 58 s but deferred_by_request_budget ×2 / ACCOUNT_DRAWDOWN_UNREADABLE |
| 00:48:35 | paryur | NO | 1.75 | extend (raced cancel) | never | same |
| 00:57:03 | savvol | NO | 2 / 2 / 1.19 | open ×3 | never | max_markets 871 s; account_risk 39 s; jump_pause 33 s |
| 00:57:33 | kuzdmy | YES | 1 | open | 135.1 s (extend) | — (net_not_positive, then account_risk) |
| 01:00:07 | kuzdmy | YES | 1 | extend | never | net_not_positive 719 s; jump_pause 38 s |
| 00:48:48 / 01:01:14 / 01:01:42 | timleg | NO | 1 each | complete | 0.9 / 1.0 / 10.7 s (complete) | — |
| 01:02:41 | timleg | NO | 1 | complete | none needed (4/4 paired) | — |

- All replaced fills (n=7): **median 23.6 s, p90 106.5 s, max 135.1 s**.
- Opening/extension fills (n=12): 4 replaced (23.6, 87.8, 87.8, 135.1 s; median 87.8 s). **8 of 12 never replaced.**
- Completion fills: re-placed within 0.9–10.7 s.

## 2. Time without qualifying resting liquidity (per active market and side)

"Unblocked" means RN1_QUOTE `block` is null. The gap is unblocked time with no resting rn1_open/rn1_extend bid on that side.

| Market | Active s | Unblocked s | Unblocked gap YES / NO s | No add bid while active, YES / NO s |
|---|---|---|---|---|
| hnorom-abaspa | 1,857 | 676 | 34 / 37 | 914 / 917 |
| kobvit-khaser | 4,878 | 270 | 27 / 36 | 3,821 / 3,497 |
| kolval-volyur | 3,172 | 2,409 | 29 / 30 | 192 / 194 |
| timleg-karpol | 2,750 | 62 | 27 / 43 (26 intl_guard) | 2,700 / 2,716 |
| paryur-stoole | 2,102 | 113 | 60 / 66 | 2,049 / 2,054 |
| kuzdmy-kurole | 1,159 | 47 | 4 / 5 | 1,116 / 377 |
| savvol-hryevh | 1,081 | 35 | 16 / 18 | 1,063 / 1,065 |
| lucsil, rehdmy, sydvik, danswe, khaser | 328 / 244 / 441 / 395 / 123 | 0 | — | all active time |
| **Total (side-s)** | 18,530 per side | 3,611 | **198 / 234** | 13,386 / 12,350 |

When unblocked, the gaps are short: 5–7% of unblocked time, and nearly all of it is the place sequence after a decision (about 2 s per order) plus request-budget deferrals.
Almost all missing liquidity comes from **blocks** (§5), not from slow re-quoting.

## 3. Unused capital (time-weighted over heartbeats, 5,229 s covered)

| Cap | Used (TW mean) | Max used | TW headroom |
|---|---|---|---|
| Spend $60 (resting + unpaired + paired) | $7.16 | $17.17 | **$52.84 (88%)** |
| At-risk $20 (resting + unpaired, approx.) | $6.55 | $15.76 | $13.46 |
| Unpaired total $10 | $2.22 | $8.64 | $7.78 |
| Account drawdown $20 (ledger figure) | $2.56 | $8.68 | $17.45 |
| Per-market unpaired $5 | never bound (max: paryur about $3.3) | | |

TW resting bids were $4.33 and paired-awaiting-settlement $0.61. Two heartbeats (00:48:49, 00:59:24) reported `ACCOUNT_DRAWDOWN_UNREADABLE`.

Headroom attribution. Each 5 s slice's headroom is split equally across active market-sides by the reason that side had no add target, or as
"quoting" when it had one:

| Reason | Share of at-risk headroom | Share of spend headroom |
|---|---|---|
| net_not_positive (incl. NET_HOLD) | 35.1% | 34.7% |
| quoting, resting but unfilled | 26.4% | 24.2% |
| max_markets (slots full) | 20.1% | 21.7% |
| jump_pause | 8.2% | 8.6% |
| vol_pause | 5.9% | 6.1% |
| account_risk (drawdown unreadable) | 1.8% | 2.1% |
| late:set_S3 | 1.1% | 1.2% |
| wanted, not yet resting (place sequence / rate-limit deferral) | 1.1% | 1.1% |
| refill_cooldown, intl_guard | 0.2% | 0.2% |

These caps never bound: residual_cap, per_market_cap, total_spend_cap, at-risk budget (no `place_refused_at_risk_budget`), pilot meter, audit
stale (`stale_blocks_new` false throughout), submission lock (unlocked). `total_unmatched_cap` appeared once, at kuzdmy YES 01:00:30, under a net_not_positive block.

## 4. Same-side accumulation vs opposite-side completion

| | Orders (ct) | Fills (ct) |
|---|---|---|
| rn1_extend (same-side add on the heavier side) | 17 (20) | 3 (4.48) |
| same-side rn1_open after a position on that side | 0 | 0 |
| two_sided_complete (opposite side) | 20 (24) | 4 (4.00) |
| rn1_open, flat or lighter side | 73 (110) | 9 (14.91) |

Ladder-level openings behind completions (kobvit/kuzdmy NO 0.47/0.45) are counted as rn1_open on the lighter side.

On the heavier side of an imbalanced market (side-seconds, 9,221 s in total):

| State | s |
|---|---|
| block net_not_positive | 5,312 |
| block max_markets | 2,228 |
| block jump_pause | 575 |
| block account_risk | 379 |
| block vol_pause | 347 |
| extension wanted and resting | 253 |
| extension wanted, not resting | 105 |
| refill_cooldown | 20 |

Extensions wanted but not sent:

| Decision | Market / side | Price(s) | Outcome |
|---|---|---|---|
| 23:52:10 | kobvit YES | 0.47 | deferred_by_request_budget. Re-placed at 0.45/0.43 at 23:52:19 after the target moved |
| 00:48:28 | paryur NO | 0.46, 0.44, 0.42 | `place_refused_account_risk` ACCOUNT_DRAWDOWN_UNREADABLE (0.44, 0.42); next decision account_risk |
| 00:56:39 | paryur NO | 0.47, 0.45 | deferred_by_request_budget |
| 00:57:04 | paryur NO | 0.47 | deferred_by_request_budget; next decision max_markets |

Rate limiting: 15 ORDER_NOT_SENT (14 deferred_by_request_budget, 1 failed_before_send). Where a same-side order followed, the median delay
was 8.6 s (n=11; excluding one hnorom deferral whose next order came 43 min later after a release). The heartbeat `http_429` counter went from 0 to 20 at about 23:53 and stayed there, with 36 critical requests held in cooldown.

### Did the controller wait for pair completion before adding same-side inventory?

**Not as an explicit rule, but in practice yes.** In 115 decisions where a completion was pending on the lighter side, the heavier
side was refused in 106 (jump_pause 36, vol_pause 27, account_risk 17, net_not_positive 12, max_markets 12, refill_cooldown 2). It was quoted
alongside the completion in only 9.

In `git show 31ddf1b06:src/pmus_rn1_replica.py`, `quotes()` sets the two sides independently. The lighter side gets role `complete`
(line 340), and the heavier or equal side gets `open`/`extend` (line 360). Neither waits on the other. The extension is refused only by
refill_cooldown (363), residual_cap (415), per_market_cap (432), or the both-bids per-market cap. That last cap keeps the completion and drops the non-completion
bid (529-562).

The waiting comes from the engine in `src/pmus_sports_two_sided.py`. Every market block, such as account_risk (5955), jump/vol pause, max_markets (6004-6005)
and net_not_positive (6018/6115/6141), removes non-completion targets (6057: `if tg and tg["role"] != "complete" and new_block: tg = None`)
and strips the ladder to completions (6153). `rn1_campaign_caps` also drops non-completion targets first (5341-5390).
Completions are exempt from all of these. So while any block is active, only the pair-completing bid rests, and same-side accumulation waits
until every block clears.

## 5. Ranked reasons additional inventory was blocked

Market-seconds count wall time a market was in that block. Episodes count entries into the block per market. Side-seconds cover both sides with no add target.

| Rank | Reason | Market-s | Episodes | Side-s, no add target | RN1_QUOTE count |
|---|---|---|---|---|---|
| 1 | net_not_positive (net-rank estimate ≤ 0; NET_HOLD 300 s, then 30 add orders cancelled) | 6,237 | 16 | 12,474 | 34 |
| 2 | max_markets (slots full; drives 26 release/reselect cycles, e.g. hnorom ×8, paryur ×4) | 4,979 | 28 | 9,958 | 60 |
| 3 | jump_pause | 1,609 | 70 | 3,217 | 170 |
| 4 | vol_pause | 1,188 | 61 | 2,375 | 140 |
| 5 | account_risk (ACCOUNT_DRAWDOWN_UNREADABLE; 4 episodes, all markets at once) | 589 | 20 | 1,177 | 48 |
| 6 | late:set_S3 (tennis late-set cutoff) | 317 | 2 | 634 | 4 |
| 7 | rate-limit deferral (deferred_by_request_budget) | about 177 s of delay (11 later placed) | 14 | — | — |
| 8 | intl_guard: intl_says_not_cheap | — | 1 | 26 | 1 |
| 9 | refill_cooldown (10 s) | — | 2 | 20 | 2 |
| 10 | reactive_pull (7 pulls: 2 jump pulls of all 6 legs, 5 single threatened legs) | — | 7 | — | — |
| — | residual_cap, per_market_cap, campaign unpaired cap, total spend cap, audit stale, pilot meter unavailable, submission lock, released_unqualified | 0 | 0 | — | — |

Notes:
- **kobvit-khaser** held YES 1 ($0.48) all window. It sat in net_not_positive for 4,484 s and flipped to block None 4 times. Each time it placed
  3 extension and 2 opening bids, then cancelled them at NET_HOLD expiry about 5 min later: 23 add orders and 1 fill.
- **timleg-karpol** (tennis, live) spent 2,299 s in jump/vol pause. It still completed 4/4 through completion-only bids.
- **kolval-volyur** rested a full pregame ladder unblocked for 2,409 s and got 0 fills.
