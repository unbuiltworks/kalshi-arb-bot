# Final authorization packet: 22:15Z campaign, 28 Nov 2026

**This is a request for explicit authorization. Nothing in it has been deployed or armed.**

## Release (preferred: B)

| | |
|---|---|
| Release SHA | **`010af772943dd96d34bc67f5de5c30a070f7aba1`** |
| Branch | `release/pmus-sports-tf-evict-req-010af7729` |
| Contents | `9e6c2039f` (armed now), plus the eviction fix, the reselection backoff, request counters and default-off request reductions. Soccer flags off |
| Regression | 6,375 passed, 26 failed. The failure set is identical to the baseline |
| Image | Built from scratch at 18:40Z (`sha256:9b4d8109…`). 64/64 modules import. The four changed source files are byte-identical to the commit |
| Read inside the image | Trading-first on. Lock = 1 reads as locked. `SHADOW_POLL_S` = 60 and `QUOTE_BOOK_TTL_S` = 10. `ACCT_SEEDS_FILL_LIST` = False and `LIVE_EVENTS_TTL_S` = 0 (the other two reductions are off). Soccer off |

## Configuration

Identical to the campaign armed now (packet §5 of `CAMPAIGN_PACKET_EXPANDED_20261129.md`, plus `PMUS_RN1_NET_RANK=0`), with these changes only:

| Variable | Now | Proposed |
|---|---|---|
| Image | `9e6c2039f` | `010af7729` |
| `PMUS_COMBO_SHADOW_POLL_S` | unset | `60` |
| `PMUS_COMBO_QUOTE_BOOK_TTL_S` | unset | `10` |
| `PMUS_TWO_SIDED_SUBMISSION_LOCK` | window 16:15–19:15Z | `1` at close-out, then `window:2026-11-28T22:15:00Z/2026-11-29T01:15:00Z` |
| `PMUS_RN1_PILOT_LOSS_USD` | 6.50 | the lower of 6.50 and the reconciled lifetime room, rounded down to $0.25 **(filled in at close-out)** |

- **Left off:** `PMUS_TWO_SIDED_ACCT_SEEDS_FILL_LIST`, `PMUS_TWO_SIDED_LIVE_EVENTS_TTL_S`, the soccer flags, `PMUS_RN1_TF_SELECT_MIN_EDGE`.
- **Unchanged:** NBA, NHL, CBB and CFB moneylines only; maker-only; 2 markets; 2-contract orders; 0.97 pair cap; $12 per market; $60 per campaign; $10 daily stop; $20 drawdown limit; independent-fair-only selection; 2¢ edge; hedge at fair + 1¢; 2¢ falling-fair filter; watchdog; crypto disarmed; combos in shadow.

## Sequence and gates

1. **21:17Z close-out** of the current campaign: lock = 1, reconcile fills, settlements, rewards and open orders, watchdog 0 orders, lifetime room read from the ledger. The loss stop is written into this packet.
2. **Disarmed deploy** of `010af7729`, with the lock at 1 and the two combo variables. *(Needs authorization.)*
3. **Verify locked:**
   - boot and `why: locked`;
   - watchdog OK; reconcile 0/0; audit fresh;
   - guard connected; `RN1_TF_SELECT with_independent_fair` > 0;
   - **no `released_unqualified_market`** events;
   - the new request counters are present in the heartbeat;
   - lifetime room ≥ the stop.
   - Any failure means no arming.
4. **Arm at 22:15Z** for 22:15Z–01:15Z, completions-only grace to 03:15Z, no automatic repeat. *(Needs authorization.)* Fair coverage must be at least 80% by 22:35Z, otherwise relock.

## Reporting

Against today's campaign: order rest time (median 335 s today), fills per order (3 of 87), reward-only evictions (125), reselections (128), rate-limit errors per route, same-side additions, hedges and their cost against fair, and settled trading P&L before incentives. Rewards and rebates are reported separately.

## To authorize

Reply with: the disarmed deploy of `010af772943dd96d34bc67f5de5c30a070f7aba1` after close-out, the two combo variables, the 22:15Z–01:15Z window plus 2 h grace, and the loss-stop rule.
