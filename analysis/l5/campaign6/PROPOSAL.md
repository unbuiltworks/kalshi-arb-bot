# Four-window live validation — completion repricing

Status: **awaiting owner authorization.** Production is shadow-locked on
`59eaae19`; `L5_COMPLETION_REQUOTE` defaults OFF. Nothing is armed.

## What changed, and what it is for

The four windows of 2026-10-28 settled **-$2.0463**: matched surplus
**+$0.8571** against stranded residual losses **-$2.9034**. Every window
stranded UP and UP settled worthless in all four. The whole loss is
stranding, so the change targets completion execution and nothing else.

Three defects, each fixed and each with its own tests:

**P0** Completion eligibility was a ROUND fact, not a MARKET fact. After a
cancelled completion the probe advanced to a fresh round, `_entering` read
True, and the gate used `may_enter` (T+600) instead of `may_complete`
(T+720). 1245z lost 120s of authorized runway with 2.29 UP unmatched.

**P1/P2** A resting completion was placed `depth_ticks` behind the touch and
then FROZEN while the market walked 12-18 ticks away. `completion_requote`
cancels such a quote by exact venue id so the ordinary complement path
re-places it, charged to the REPLACE budget (64 authorized, 0 ever used).

**P3** The gate that actually decided was `pair_cost_refusal` at the
SUBMISSION boundary, judging the REALISED basis
`eff = (cost_up + cost_down + px*qty) / matched_after`. On 1245z that is
0.9699 and 0.96 refused it there, so P1 could not reach the wire. Openings
now keep 0.96; completions of confirmed residual answer to the completion
economics, bounded by `eff <= 1.00` and the approved residual deficit.

## Evidence

From 284 preserved live book samples (220 with a readable two-sided book):

|window|samples|eligible|replacements|maker-only|
|---|---|---|---|---|
|1215z|57|35|1|yes|
|1230z|75|73|2|yes|
|1245z|88|82|1|yes|
|total|220|190|**4**|yes|

190 eligible snapshots are **not** 190 executable opportunities, and the 4
replacements are the number the policy would actually attempt: the quote
moves to `min(touch, ceiling, 1 - realised basis)` and then stops.

Marketable time, current versus proposed, with **no fill claimed**:

|window|current (frozen)|proposed|
|---|---|---|
|1215z|5/57 (9%)|36/57 (63%)|
|1245z|3/88 (3%)|12/88 (14%)|

1215z's original level WAS reached in 8 of the 11 samples of the order's
life and still did not fill, because the venue publishes no per-order queue
position and we were behind the size already there. Marketable time is a
necessary condition, not a sufficient one.

## Honest expectation

This should reduce stranding in MODERATE moves and do little in collapses.
1245z's late residual was unrescuable at any price under a dollar, and
carrying it was correct. No reduction in the -$2.9034 is demonstrated; that
requires live fills. The $2.0562 worst-case figure is conditional on fills
and is **not** realized profit.

## Scope requested — unchanged risk limits

- BTC15m only (`cpc-btc-updown-15m-`), four eligible windows
- Maximum total exposure **$20**; cash envelope **$12**
- Daily realized-loss halt **$5**; cumulative loss cap **$20**
- Worst-case directional deficit **$3**
- Four round allocations, eight rounds per window
- **One** simultaneous resting order; maker-only; no two-order exception
- OPEN 40 / COMPLETE 40 / REPLACE 64; total backstop 40
- Independent watchdog and authoritative settlement reconciliation
- Fresh approval document, new nonce, expiry within four hours

**The only behavioural change is `L5_COMPLETION_REQUOTE=1`.** No capital
increase is requested: cash headroom ran $9.35-$16.70 against the $12
envelope and peak exposure ~$3.03 against $20, so dollars are not the
binding constraint.

## Per-window comparison, on actual records

For each window, against the 2026-10-28 baseline: submitted, filled,
cancelled, stranded quantity and cost, completed pairs, realised pair cost,
and confirmed settled net from `L5_LIVE_SETTLED`. Plus, new to this run:
requotes issued, each requote's from/to price and ticks gained, and every
opportunity refused by the cash envelope, exposure cap, residual limit or
order-count limit priced by `l5_capital_block` -- expected matched surplus,
additional obligation, completion distance in ticks, downside if stranded.

Those refusal records are what would later justify a limit change. None is
requested now.

## Promotion

Isolated parity against the deployed baseline `ea67b2c5`: **34 failing node
ids on both, identical sets**, 13860 -> 14039 passes. The promotion carries
three same-subsystem prerequisites (`cb22bb47`, `fa682b80`, `e51f17ff`) and
two promotion-only fixes: two refusal sites the prerequisite added without
registering, and a fourteenth tick stage to declare.

Deploy shadow-locked first, verify SHA and runtime config, confirm the
watchdog and a clean venue reconciliation, and only then lift the lock.
