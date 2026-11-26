# Sports release candidate: RN1 marginal EV and fit-every-level (not deployed)

26 Nov 2026. Prepared on owner instruction (authorization file §67).
**Not deployed and not activated.**

- **Release:** `5cbca4d3cf609d4ecf2af9016d91b4481ec37104`, branch
  `release/pmus-sports-marginal-ev-5cbca4d3c`.
- **Contents:** code `9b28e6bfe` (`dev/pmus-rn1-marginal-ev`) on the
  running `1b496656c`, plus one deploy-trigger line.
- **Changes:** `src/pmus_rn1_replica.py`, `src/pmus_sports_two_sided.py`
  and the new `tests/test_pmus_rn1_marginal_ev.py` (26 tests).
- **Full suite:** fails the same 24 tests by name as `1b496656c`, with 26
  new passes. The targeted RN1 and sports tests give 1,194 passed and 1
  skipped.

## Behaviour with both flags unset

It is identical to `1b496656c`, so deploying the release changes nothing
until a flag is set.

## Effective configuration to enable (a later, separately approved step)

| Variable | Set to | Effect |
|---|---|---|
| `PMUS_RN1_MARGINAL_EV` | `1` | Each opening, extension and deeper ladder level is judged on its own value per hour (see below). Under capital limits, the lowest-EV bid is removed first, but only when removing either bid alone would fit. Bids that pass on their own are not cancelled by the hold expiry or the release of a market whose estimate is unqualified. |
| `PMUS_RN1_FIT_ALL_LEVELS` | `1` | The campaign caps and the two-bid per-market cap size a bid down by whole contracts to the venue minimum before dropping it. Ladder levels size down too, and a level that cannot fit is skipped instead of ending the side's ladder. |
| `PMUS_RN1_MARGINAL_BOOK_TTL_S` | leave unset (60) | How long the public book snapshot used for the reward term is reused. |

The value per hour is:

> fills per hour × (fair value − price − maker fee − adverse selection − concentration charge) + incremental reward

- **Reward:** counted only when the day's projection clears the venue's
  minimum payout, then multiplied by 0.5.
- **Rebates:** 0, because none is sourced.
- **Keeping a bid:** the total must be positive and the trade edge must not
  be negative.

### Prerequisites

These are unchanged and already set for Campaign C:

- `PMUS_TWO_SIDED_RN1=1`
- `PMUS_RN1_NET_RANK=1`
- `PMUS_RN1_EDGE_GATE` unset (default on), which supplies the fair value

With no fresh fair value, the new logic evaluates nothing and behaviour is
as before. Before enabling, check from the live logs that the fair value is
present for the pinned event.

### Unchanged

- `PMUS_RN1_FIT_QTY` (current setting kept)
- every cap: per market 12, unpaired 5 and 10, spend 60, at-risk 20, lane 20
- the loss stop 10 and daily halt 10
- maker-only, the lock and the watchdog

## Risks (from the review)

- Fill rates and fill losses are estimated settings, and depth beyond the
  touch uses one rate.
- With the EV flag on, a failed market-level estimate read no longer
  blocks on its own. Bids are then judged with zero reward.
- Ladder sizing can leave gaps between levels.
- Market selection still requires a positive market-level estimate.
- **Completions are not changed.** Judging them by incremental portfolio
  economics needs separate approval.

## To deploy and activate (both need the owner's approval)

1. Set the sports service source to `5cbca4d3c`, with trading locked.
2. Set both flags to `1` for a named window.
