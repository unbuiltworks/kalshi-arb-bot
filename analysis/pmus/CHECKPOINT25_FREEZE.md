# CHECKPOINT-25 FREEZE — recorded 2026-09-28, n=14 of 25

Owner instruction: *"Continue exactly as-is. No strategy changes. Do not optimize
the remaining 11 observations. Let the experiment finish."*

## Frozen through n=25 — nothing in this list may move

| parameter | frozen value |
|---|---|
| θ | **0.96** |
| variant | **B** |
| fill model | **queue_aware** |
| q grid | **{25, 50}** |
| PROFIT_LOCK_5 threshold | **$5.00** |
| `floor_guard` | **OFF, untuned** |
| `complement_bias_c` | **OFF** — falsified transfer, do not retry |
| completion rules, residual cap, hold_s | unchanged |
| completeness rule | COMPLETENESS_RULE_AMENDMENT_001 (`fea39051`) |
| `DRAIN_S` | **240s** — frozen. 0015z showed 240s is not always enough (it needed >241s while 2315z needed 124s). That is a recorded finding, **not** a reason to change it. |

## The single primary criterion

> **≥ $5.00 per window MEAN `shadow_trading_net` — AFTER fees, BEFORE rebates.**

Verified to be measured exactly that way, not merely named it:
`matched_gross − fees + residual_pnl = shadow_trading_net` holds to 1e-9 per
window and at every rolling checkpoint (`tests/test_pmus_shadow_net_identity.py`),
`fees` is a positive cost magnitude that is subtracted, and the rebate appears only
in the separate `shadow_modeled_net_including_expected_rebate` line. The expected
maker rebate is **upside, not part of the criterion**.

**No additional pass/fail criterion exists.** The distribution diagnostics — median,
trimmed mean (drop 1 each end), lock-hit rate, positive-window rate, worst, best,
best window's share of total P&L, mean excluding best — are **permanent reporting,
not thresholds**. They are published beside every mean so a mean one observation can
move is visible as such, and none of them gates anything.

## Challenger moratorium

**No new challenger before checkpoint 25.** The single exception is genuinely new L5
evidence that independently reveals a mechanism — and explicitly **not** because a
later window hurts the result. A window that damages the current numbers is data,
not a reason to build.

CHANNEL_A remains **UNRESOLVED** (`analysis/l5/p1/P1_CHANNEL_A.md`). The
distinction found there is real and L5-supported; it reached +4.2227 at q=50 and
reversed sign at q=25, and it is left exactly where it is.

## Concentration diagnostics are DESCRIPTIVE ONLY

The best window's share of total P&L has fallen 0.5464 (n=14) → 0.5216 (n=16) →
0.4609 (n=17), and the mean excluding it has risen +3.4489 → +3.3027 → +3.9485,
with the trimmed mean at +4.9219. **This is description, not a trend to
extrapolate, not evidence, and not a criterion.** It may reverse with the next
window. Nothing reads it, nothing is selected from it, and it must never be
presented as progress toward the target.

## 0015z stays excluded

`drain_end` at 00:34:31 recorded `terminal_recorded: FALSE` after the full 240s.
The venue later published UP (1.0000 vs 0.9900). **That later settlement information
must never be used to recover the window.** Permanently excluded under the frozen
protocol.

## At n=25: stop and produce the full frozen comparison

P0 (frozen arm) vs PROFIT_LOCK_5, at q=25 **and** q=50, reporting for each:

* the eight-row waterfall — EC matched gross → PASSIVE matched gross → TOTAL
  matched gross → fees → residual P&L → SHADOW TRADING NET → expected maker
  rebate → SHADOW MODELED NET;
* mean, median, trimmed mean, mean excluding best window, concentration (best
  window's share of total P&L);
* lock-hit rate, positive-window rate, worst window, best window;
* peak directional exposure for both arms;
* the complete per-window distribution.

Then **stop**, and **do not begin another challenger after n=25 until the owner has
reviewed the result** — the post-checkpoint moratorium is unconditional and is not
lifted by the numbers being good, bad, or close. No live advancement in the same
run. Any live authorization is
additionally gated on REPO_TESTS_001 (the EXP-010 execution-path failures resolved
or proven irrelevant to the authorized artifact).

## Standing prohibitions, unchanged

No live orders. No deployment. No q increase. No new capital authorization. `HALT`
present. Mandate expired and stays expired. `PMUS_Q1_ARM` empty. The $0.49320
remaining live-research loss budget untouched.

## Where n stands

**n = 14 of 25; 11 observations remain.** Excluded and not to be rehabilitated
unless their own contemporaneous tapes pass all six elements: 1815z (no two-sided
state), 2015z, 2100z, 2130z, 2145z, 2200z, 2230z (no terminal row on tape at all).
