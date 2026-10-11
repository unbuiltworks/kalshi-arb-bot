# B' (RESIDUAL_CAP_k2 / PROSPECTIVE_NET) — FROZEN four-market result

**Status: CLOSED. Disarmed 2026-09-27 16:53:39Z. These figures are frozen and
are not to be recomputed, re-selected, or re-scoped.**

Owner instruction on close: *"B' successfully bounded inventory but FAILED the
economic objective because residual losses overwhelmed positive matched
economics."*

## Headline (owner-confirmed)

| line | value |
|---|---|
| matched P&L | **+$1.027880** |
| residual P&L | **−$2.580880** |
| realized net | **−$1.553000** |
| gating `realized_today` | **−$4.04440000** |
| remaining research-loss budget | **$0.95560000** |

The gating counter is `InventoryStore.realized_today()` =
`realized_attributed + realized_unattributed`, which is what
`Limits.refuse()` tests against `daily_realized_loss_halt_usd`.
`legacy_realized_today` (−5.06910000) is the superseded pre-decomposition
scalar, retained for audit only, with **no gating authority**. An earlier
report in-session quoted the legacy scalar as the halt counter; that was
wrong and is corrected here.

## The four informative markets

Informative = the residual cap actually bound in that window.

| # | market | matched P&L | residual P&L | settlement P&L | gross cash | yes_value |
|---|---|---|---|---|---|---|
| 1 | 1515z | +$0.382982 | −$0.201482 | **+$0.181500** | $10.588500 | 1.0000 |
| 2 | 1530z | +$0.352837 | −$1.408137 | **−$1.055300** | $12.215300 | 0.0000 |
| 3 | 1615z | $0 (no pair) | −$0.550300 | **−$0.550300** | $0.550300 | 1.0000 |
| 4 | 1630z | +$0.292061 | −$0.420961 | **−$0.128900** | $7.168900 | 0.0000 |
| | **total** | **+$1.027880** | **−$2.580880** | **−$1.553000** | **$30.523000** | |

Return on gross cash deployed: **−5.088%**.

### Per-market detail

**1515z** — 10 NATIVE-eligible = 6 submitted + 4 binds; action retention 0.600.
UP 10.77 @ $8.880100, DOWN 12.21 @ $1.708400. Matched 10.77 @ pair cost
0.96444. Residual 1.44 DOWN. Realized after: −1.46110000. Residual peaked 1.44
against a 1.26 cap — asynchronous overshoot, pre-authorised: the invariant is
on admission of residual-increasing orders, not on realized residual after
fills land.

**1530z** — UP 16.05 @ $4.621800, DOWN 11.16 @ $7.593500. Matched 11.16 @
matched cost $10.807163. Residual 4.89 UP. Realized after: −2.51640000.

**1615z** — UP **0 filled**, DOWN 1.47 @ $0.550300. **No pair ever completed.**
78+ cap binds. Pure naked residual, settled worthless. Realized after:
−3.91550000. The cap is what held naked exposure to 1.47 contracts; it bounded
the loss rather than causing it.

**1630z** — UP 10.22 @ $1.352900, DOWN 7.04 @ $5.816000. Matched 7.04 @ matched
cost $6.747939. Residual 3.18 UP. Realized after: −4.04440000.

## Excluded window

**1600z** — settled −$0.848800 (matched +$0.105050, residual −$0.953850, gross
cash $1.775800, `yes_value` **0.9900**, not binary). **NOT counted.** A redeploy
carrying the arm-retry fix was pushed mid-window while the market held six live
orders and a position; one order remained open at the venue, preflight's
`no_unexpected_open_orders` gate then correctly refused, and the window ran its
last seven minutes unarmed with its bind counter restarted at the process
boundary. The handover itself was clean — state persisted, `gross_net_agrees`
true, `gross_detail_lost` empty, no envelope breach — but it is not a clean
observation of the policy over a whole window. Recorded, not counted.

**1545z** — ran fully disarmed on the arm-retry defect; zero orders, zero fills.
Not informative. See `ARM_RETRY_DEFECT_20260927.md`.

**1445z, 1500z** — zero eligible actions, zero binds. Not informative.

**1645z** — armed 16:45:46, disarmed mid-window 16:53:39 on owner instruction
("Do not trade 1645z"). Gross cash $5.302400 at the moment of disarm. **Not
part of this evidence and not counted.** It settles through the ordinary sweep
and is reconciled separately.

## Structural findings

**Matched pair production worked.** Weighted matched pair cost **0.96452** per
contract across the three markets that completed pairs (1515z 0.96444, 1530z
0.96838, 1630z 0.95851), i.e. a locked margin of **+3.548% per matched
contract**. Matched P&L was positive in every window that produced a pair.

**Residual is the entire loss.** 10.98 residual contracts against 28.97 matched
— residual is **27.5%** of all contracts acquired — at an average cost basis of
$0.23505. Residual settled **worthless in all four markets, 0 for 4**.

On the 0-for-4: the mechanism is a priori plausible (a maker-only book fills
the resting leg precisely when the market is moving against it, so the leg that
fails to complete is the adversely-selected one), but under a null of 50/50 per
window four losses has probability 1/16 ≈ 6%. **Suggestive, not established.
Adverse selection is NOT claimed as proven on n=4.**

**Inventory bounding worked.** The cap bound 118 times across the session. No
envelope breach, no quarantine, no unresolved orders, no accounting damage at
any point, including across two process restarts.

**Rebates: none credited.** Commission observed 0.0000 on every row. A zero
commission is not a rebate. No rebate is assumed anywhere in these figures.

**Fees:** no commission charged on any fill in these four windows.

## Why the probe failed its economic objective

A clip-size increase scales the **+$1.027880** matched line and the
**−$2.580880** residual line in the same proportion. On this evidence larger
clips buy a proportionally larger loss. Size is not the binding constraint;
the residual share (27.5% of contracts) and its settlement outcome are.

This is the fact that sends the next task to residual repair rather than to
scaling.
