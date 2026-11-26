# Sports release candidate: final-gate loss stops (minimal, for Stage 1)

26 Nov 2026. **Not deployed.**

- **Release:** `31ddf1b06341d15712fe655693acd028a5077e9f`, branch
  `release/pmus-sports-loss-backstop-31ddf1b06`.
- **Contents:** code `f956b2d6a` (`dev/pmus-final-gate-loss-stops`) on the
  running `5cbca4d3c`, plus one deploy-trigger line. It shares nothing with
  the marginal-EV-without-fair-value work.
- **Diff:** `src/pmus_sports_two_sided.py` +37 lines;
  `tests/test_pmus_sports_final_gate.py` +126 lines (10 tests).

## What changes

1. **Loss stops at the final gate.** Immediately before any opening or
   extension is sent, the gate now re-evaluates the campaign loss stop and
   the bot's daily stop. Both are the same functions the decision layer
   already uses, with no new number.
   - **Campaign stop** (`pilot_block`), refusing on any of: the latched
     stop, the at-risk book, an unavailable meter, or unconfirmed fills.
   - **Bot daily stop** (`daily_stopped`).
   - Completions are never refused by either check.
2. **In-flight orders across markets.** The campaign spend and unpaired
   ceilings now count in-flight or uncertain submissions in other markets,
   including a first order in a market not yet booked. The at-risk budget
   already counted them.

## Two markets cannot bypass the limits: why

- All orders are sent from one trading thread. Every order passes the full
  `place()` sequence, then `final_gate()`, and is reserved before it is
  sent.
- **Already checked per order before this change, across all markets:**
  - account-wide daily and lifetime realized limits and the $20 drawdown,
    counting the bot's own resting worst case and this order
    (`account_risk_blocks`, from the ledger's published record);
  - the at-risk budget (unpaired filled cost plus every resting order plus
    every reservation plus this order);
  - lock, shared halt, scope and depth;
  - per-market spend and unpaired;
  - campaign spend and unpaired over all unsettled markets;
  - the campaign stop counting resting bids.
- **Added here:** the two loss stops at the gate, and other markets'
  reservations in the campaign ceilings.
- **Tests:**
  - existing: campaign ceilings span markets; two orders that each fit
    cannot both be sent; eight concurrent submissions cannot exceed a
    ceiling together; partial fills count at what is left;
  - new: the loss-stop refusals; completions still go; an uncertain send
    elsewhere counts; a first reserved order in an untracked market counts;
    two markets filling together stay inside the caps.
- Five of the new tests fail on `5cbca4d3c` and pass here.

## Regression

- Targeted RN1/sports suite (`tests/test_pmus_rn1_*.py`,
  `tests/test_pmus_sports_*.py`, `tests/test_pmus_l5_ramp_ladder.py`):
  1,204 passed, 1 skipped. That is `5cbca4d3c`'s 1,194 plus the 10 new
  tests.
- Full suite: see the update below.

## Effective limits for Stage 1 (unchanged values)

| Limit | Value | Source / scope |
|---|---|---|
| Account drawdown, venue equity against the anchor 312.4102 | $20 | ledger; whole account |
| Ledger daily realized halt | $50 | ledger; whole account, manual included |
| Ledger cumulative ceiling | $75 (room $12.69) | ledger; whole account |
| Sports lane cap | $20 | bot |
| At-risk (fills plus resting plus in flight) | $20 | bot, all markets |
| Campaign loss stop, counting resting bids | $10 | bot, this campaign |
| Bot daily loss stop | $10 | bot, UTC day |
| Unpaired total / per market | $10 / $5 | bot |
| Per market | $12 | bot |
| Campaign spend | $60 (settled markets stop counting) | bot |
| Recovery unmatched halt | $15 | bot |
| Maker-only, lock, latch, watchdog cancel | on | — |

## The $10 and $50 daily halts

They are two different limits, and both apply.

- **$10, the sports bot's own daily stop** (`PMUS_TWO_SIDED_DAILY_LOSS_USD`).
  - It marks the bot's own book every step: pairs at $1, lone legs at their
    bid.
  - If today's change is below −$10, no new opening until the next UTC day.
    Completions continue.
  - It is a mark-to-market estimate. For the bot it binds first.
- **$50, the account ledger's daily realized halt.**
  - It is computed from settled realized P&L of every market on the
    account, manual trades included. The bot reads it through the
    account-risk record.
  - Today: −$24.81, of which −$20.63 is the owner's manual UConn trade.
- The campaign's own $10 loss stop sits beside them.
