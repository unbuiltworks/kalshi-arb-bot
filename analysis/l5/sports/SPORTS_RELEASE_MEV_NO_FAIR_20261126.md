# Sports release candidate: marginal EV without a fair value (not deployed)

26 Nov 2026. **Not deployed and not activated.** It needs the owner's
authorization.

- **Release:** `e72cf329e6b0b090d2df7d9387a961b32bc802ab`, branch
  `release/pmus-sports-mev-no-fair-e72cf329e`.
- **Contents:** code `cf19ef86a` (`dev/pmus-rn1-marginal-ev-no-fair`) on
  `9b28e6bfe`, on the running `1b496656c` line; plus one deploy-trigger
  line. The diff against the running `5cbca4d3c` is `src/pmus_rn1_replica.py`,
  `src/pmus_sports_two_sided.py` and `tests/test_pmus_rn1_marginal_ev_no_fair.py`
  (18 tests).

## Configuration to activate

| Variable | Value | Note |
|---|---|---|
| `PMUS_RN1_MARGINAL_EV` | `1` | already set |
| `PMUS_RN1_MARGINAL_EV_NO_FAIR` | `1` | new |
| `PMUS_RN1_FIT_ALL_LEVELS` | `1` | already set |
| `PMUS_RN1_EDGE_GATE` | `0` | required. With the gate on, a market with no fair value still gets no bid |

Everything else stays unchanged: every cap, the loss stop and daily halt,
maker-only, the lock and the watchdog.

## The rule for a market with no fair value, per bid, per resting hour

> reward − fills per hour × contracts × (fill loss + maker fee +
> concentration charge)

- **Reward:** the same-snapshot marginal ladder reward, counted only when
  the day's projection clears the venue's minimum payout, then multiplied
  by 0.5.
- **Fill loss:** the existing setting, 0.05 at the best bid and 0.035
  behind. For doubles it is 0.08 and 0.06.
- **Rebates:** 0.
- **Keeping a bid:** the total must be positive.
- **No trade-edge term:** there is no fair value and none is substituted.

## Regression

- **Full suite, flags off:** the same 24 failures by name as `9b28e6bfe`.
  Targeted RN1 and sports tests: 1,212 passed, 1 skipped.
- **Targeted suites, all four settings on:**
  - 145 failed and 7 errors, against 49 for `5cbca4d3c` with the same
    settings;
  - with test-only fill loss 0: 109 failed and 7 errors.
- **No exception is raised in `src/`.** Every exception is in a test
  reaching for an order that was never placed. Almost none of the existing
  test markets carry a reward programme, and with zero reward the total is
  never positive. So no bid is placed, and the later steps (latch, window
  lock, halt, fill list) are never reached.
- **Coverage gap:** those safety rules are exercised under this mode only by
  the 18 new tests, not by the older safety suites. Closing it needs
  fixtures with a reward programme.

## What it means live

In a market with no fair value it can only remove bids: those whose reward
does not beat the expected fill cost. A market with no paying programme gets
no bid at all.
