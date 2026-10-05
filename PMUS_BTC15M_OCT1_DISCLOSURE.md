# PMUS BTC15m — 2026-10-01 Holdout Inspection Disclosure Ledger

**Purpose.** The 2026-10-01 07:00Z–23:xxZ executable tape is the quarantined
holdout for `pmus-btc15m-underdog-v1` (see
`PMUS_BTC15M_UNDERDOG_PREREGISTRATION.md` §6). Any Oct. 1 window that was
already inspected during earlier calibration work is contaminated and must be
**excluded** from the one-shot holdout evaluation.

This ledger is the record of those windows. **It gates the holdout run: while
it is marked INCOMPLETE, the holdout must not be unsealed.**

## Status: INCOMPLETE

## In-repository search (2026-10-05, outcome-blind)

Searched: all tracked markdown and source in `unbuiltworks/kalshi-arb-bot`,
across the branches present in this checkout (`main`,
`claude/pmus-btc15m-preregister-44yivc`), and the commit log, for references to
`2026-10-01`, `10-01`, `Oct 1`, `October 1`.

**Result: no record found of any 2026-10-01 window having been inspected.**

This is an absence of evidence in *this tree only*. It is not a clearance. No
tape was opened to produce this finding.

## Still required before the holdout may be unsealed

- [ ] Confirm from the concurrent PMUS WS tape archiving session's own logs
      which Oct. 1 windows, if any, were read for calibration. *(Must be
      obtained from that session's records after its archival completes — this
      pre-registration deliberately did not touch that recorder, service,
      volume, deployment, or archive branch.)*
- [ ] Confirm from any out-of-repo calibration notes / chat transcripts
      covering Oct. 1.
- [ ] List each contaminated window below (UTC start, UTC end, what was
      inspected, by whom/what, date).
- [ ] Flip **Status** to COMPLETE and record the resulting eligible-holdout
      window set.

## Contaminated windows

| UTC start | UTC end | What was inspected | Source | Date |
|---|---|---|---|---|
| _(none recorded yet)_ | | | | |

## Eligible holdout set after exclusions

_To be filled when Status is COMPLETE._
