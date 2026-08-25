# ARB Control Plane

Last verified: 2026-08-25 00:43 UTC / 2026-08-24 20:43 ET

This is the durable ARB handoff. **Production state wins over chat history.** Before changing any workstream, verify current Railway deployment/runtime and GitHub commit. Only settled/reconciled **REALIZED** P&L counts toward the **$428/day realized-net objective**. A candidate, modeled edge, reservation, submitted order, fill, hedge, or unsettled locked pair is not realized profit.

Historical detail through earlier checkpoints remains in git history and `ARB_STATUS_HISTORY_2026-08-23.md`.

## CURRENT SCOREBOARD

`QUALIFIED -> ORDER_SUBMITTED -> FILLED -> HEDGED -> SETTLED -> REALIZED`

- **New realized P&L from tonight's repair work: $0.**
- Historical proof remains approximately **+$0.2679 realized net across seven settled-compatible Gemini<->Kalshi sports trades**. Do not count that as new P&L.
- Caleb Durbin incident: Gemini original q1 leg filled; Kalshi hedge did not. It is still **ONE_LEG_FILLED / unsettled** and therefore is not realized profit.
- Polymarket MLB has submitted real q1 maker orders and received real exchange order ids, but the latest confirmed attempts were **zero-fill** and their reservations were released.
- Same-Gemini crypto currently has **0 >=3c immediate FOK/FOK candidates** and no qualifying flow-active whole-contract maker candidate.

## WHY TWO WEEKS PRODUCED ALMOST NO REALIZED PROFIT

This was not simply a lack of scanner candidates. Production repeatedly found candidates, but the execution/control plane had defects that converted recoverable incidents into dead time:

1. **One-leg G<->K incident could globally freeze unrelated sports lanes.**
   - Caleb Durbin q1 Gemini NO filled while Kalshi rejected/did not fill.
   - The old sports guard treated any unresolved sports exposure as a global mutation halt, so that known G<->K incident also disabled independent Polymarket trading.

2. **The settlement reconciler could never clear a one-leg incident.**
   - Old reconciliation admitted only `AWAITING_SETTLEMENT` trades with `legs_filled == 2`.
   - `ONE_LEG_FILLED` was outside the reconciliation queue entirely, so a one-leg incident could remain a permanent manual-review blocker.

3. **Polymarket runtime configuration drift overrode the intended maker lifecycle.**
   - `railway.structural.json` starts `sports_poly_live_fill_conversion_hotfix_v2` even when the service-level dashboard command shows the verified supervisor.
   - That wrapper hard-coded `STICKY_LIFECYCLE_SECONDS = 6 * 60 * 60`, producing a reported **21,600-second / six-hour** maker lifecycle.
   - This could occupy the only live maker slot on a low-fill-probability quote for hours.

4. **Maker admission was too permissive after near-touch rows disappeared.**
   - Ranking preferred proximity, but a far-away mathematical frontier was still eligible when no close quote existed.
   - Production therefore attempted mathematically safe but practically unfillable quotes, including a live candidate 17c from the ask.

5. **Earlier execution transport defects were real.**
   - Polymarket private WS silence was incorrectly fatal before REST recovery was repaired.
   - G<->K concurrent submission allowed Gemini to fill while Kalshi rejected.
   - Old Gemini emergency flatten used an invalid prediction-market MARKET order.
   - Multiple pre-order/read and restart/orphan handling issues caused apparently healthy Railway services to stop producing useful execution.

The system therefore spent too much time behaving like a validation lab rather than a reliable execution machine.

## LIVE FIX — STRATEGY-SCOPED KNOWN-EXPOSURE QUARANTINE

Polymarket service:
- service: `sports-kp-live-executor` (`bdcd4d42-bfcf-469c-9bd7-46b26acd8b87`)
- branch: `feature/sports-kp-live-executor-impl`
- current source commit: `670ff1788c3b782d3cdd49c3d73ff608a42917ae`
- current deployment: `47829119-3b6c-48d2-bc64-c1ce6b6d82d4` — **SUCCESS**

New unresolved-exposure policy:
- unknown/ambiguous sports mutation => **global halt**;
- Polymarket's own unresolved mutation => **Polymarket halt**;
- exact known G<->K `ONE_LEG_FILLED`, `legs_filled=1`, known capital => **G<->K quarantine only**;
- the known one-leg risk still consumes the shared **$5** ceiling; it is not forgiven or released;
- unknown other unresolved states remain fail-closed.

For Caleb, unresolved risk is conservatively charged at about **$0.97**, leaving about **$4.03** of the shared $5 ceiling before any new Polymarket q1 reservation.

Verified production evidence:
- Polymarket startup now reports `MLB_STARTUP_RECOVERY_CLEAR` even while Caleb remains quarantined in G<->K.
- Caleb no longer globally freezes independent Polymarket MLB execution.

## LIVE FIX — ONE-LEG SETTLEMENT RECONCILIATION

Settlement service:
- service: `sports-trade-settlement-reconciler` (`43c52248-e3cc-433e-8d5d-e6a0d70e3701`)
- branch: `feature/sports-trade-settlement-reconciler`
- source commit: `98e96317f836a514823b43572f3bf556c33ec2ab`
- deployment: `b61628aa-9dd3-40e0-b783-5ddd8dc1923e` — **SUCCESS**
- targeted predeploy: **17 passed**.

New behavior:
- original two-leg settlement path is preserved;
- exact `ONE_LEG_FILLED`, `legs_filled=1` incidents now enter reconciliation;
- only the actually filled venue's authoritative resolution is needed for a one-leg settlement;
- successful flatten evidence prevents accidental double-realization;
- P&L uses actual filled capital, recorded fees and actual settlement payout — never modeled edge;
- terminal status becomes `SETTLED_ONE_LEG` only after authoritative resolution.

Verified production log:
- `open_reconcilable=1`
- `one_leg_open=1`
- Caleb currently: `SPORTS_ONE_LEG_SETTLEMENT_PENDING ... venue=gemini gemini=None`

Gemini has not yet published a definitive resolution, so Caleb remains unsettled and unrealized.

## LIVE FIX — POLYMARKET FILL-CONVERSION BOUNDS

The hidden six-hour lifecycle is removed in current source.

Current production behavior:
- maximum maker lifecycle: **180 seconds**;
- live maker admission requires the safe post-only quote to be **within 2 reported ticks of the ask**;
- distance-to-ask ranks before activity; activity only improves ordering among similarly fillable prices;
- exact after-fee floor remains **>= $0.03**;
- q=1 remains fixed;
- participation/rule certification remains mandatory;
- one maker at a time;
- hedge book/economics continue to be rechecked while the maker rests;
- account-authoritative startup cleanup remains mandatory.

Rollout evidence at ~20:42 ET:
- startup saw **1 real open far-away Polymarket order** from the old six-hour process;
- canceled: 1;
- unresolved: 0;
- partial flattened: 0;
- partial unresolved: 0;
- exact reservation was released as confirmed zero-fill;
- verification read then found **0 account open orders**;
- process reached `LIVE_LOOP_READY`.

First bounded scan:
- strict MLB markets: **1,737**;
- strict MLB pairs: **1,749**;
- participation-ready pairs: **621**;
- raw safe maker-frontier rows after book availability: **59**;
- rows within <=2 ticks while preserving >=3c: **0**;
- far-gap rows suppressed: **59**;
- order entry for that scan: **none**.

This is the correct current negative result: there are mathematical maker frontiers, but none are presently close enough to the ask to be a credible fill-conversion attempt at the >=3c floor.

## LAST CONFIRMED POLYMARKET ORDER LIFECYCLE

Keibert Ruiz COL-WSH H+R+RBI ladder:
- participation certified;
- maker quote 49c;
- hedge ask 43c;
- modeled locked profit at entry: **7c**;
- real exchange maker order id: `C2WHM71BEDJR`;
- terminal: **MAKER_CANCELED_EDGE_GONE** after hedge book disappeared;
- confirmed maker quantity: zero;
- capital reservation released;
- no hedge, no settlement, no realized P&L.

The campaign continued after this terminal result instead of hard-halting on harmless skips/zero fills.

## GEMINI <-> KALSHI SPORTS

Service:
- `sports-gk-autonomous-supervisor` (`0238eada-ced4-4927-8150-0ccce2e1a0a4`)
- branch: `feature/sports-gk-autonomous-supervisor`
- current commit: `94ee5da4ed98dd13c237879224173bebaabbcffd`
- deployment: `68a3975a-9a05-45e7-bf33-315288bec2ae` — **SUCCESS**.

Transport hardening after Caleb:
- exact Kalshi market/tradeability preflight;
- **Kalshi q1 FOK first**;
- Gemini may submit only after confirmed Kalshi fill;
- fresh Gemini revalidation before second mutation;
- Gemini emergency reduction uses valid LIMIT+FOK, never MARKET;
- exact position/flatten reconciliation is fail-closed;
- cumulative live risk ceiling restored/pinned to **$5** after a stray source-controlled $50 value was discovered.

G<->K new mutation remains quarantined by the unresolved Caleb incident. Do not rearm that strategy until Caleb is authoritatively settled or safely closed.

Current capital telemetry continues to refresh successfully; last observed Gemini available was about **$61.80**, Kalshi about **$3,405.71**, with the existing $5 free buffer policy.

## SAME-GEMINI CRYPTO THRESHOLD LADDERS

Service:
- `crypto-maker-live-probe-q1` (`6e0dffb2-8309-47cf-8196-9106eda284d8`)
- q=1, >=3c after-fee floor.

Current verified condition:
- complete safe q1 monotone pair scans continue;
- roughly 2,400+ q1 pairs are checked per cycle;
- current **>=3c FOK/FOK candidates = 0**;
- whole-contract maker rows lacking qualifying exact recent flow remain suppressed;
- fractional resting-maker path remains closed because worst-case 0.01 partial fills plus cent-rounded fees destroy the >=3c/contract guarantee.

Do not force orders into dead books and do not revive the old Gemini<->Kalshi 15-minute basis lane.

## OPERATING PRIORITY FROM THIS CHECKPOINT

1. **Execution reliability before more scanner breadth.** A qualified row matters only if it can become a confirmed order, fill, hedge and settlement.
2. Polymarket may submit only genuinely fillable <=2-tick q1 makers at >=3c. Do not occupy the maker slot with far-away mathematical frontiers.
3. Keep one-leg settlement polling Caleb until Gemini publishes a definitive result. Do not count it as realized early.
4. Keep G<->K quarantined until Caleb settles. The hardened Kalshi-first transport is the only allowed next G<->K mutation path.
5. Same-Gemini crypto remains continuously armed for immediate >=3c FOK/FOK; no order merely for activity.
6. Maintain the shared **$5** risk/loss ceiling. Never silently reset or increase it.
7. Do not lower the 3c floor, bypass settlement/rule/participation certification, release ambiguous reservations, count unsettled/modelled P&L as realized, or mix this infrastructure with the unrelated SOL executor.
8. Every future checkpoint must report the funnel explicitly: `QUALIFIED -> SUBMITTED -> FILLED -> HEDGED -> SETTLED -> REALIZED`.

## BOTTOM LINE

The two-week problem was a combination of **real execution/control-plane bugs plus poor fill conversion**, not simply an absence of theoretical candidates. The global one-leg freeze, missing one-leg reconciliation, six-hour hidden maker lifecycle, and far-away maker admission have now been corrected in production.

At the latest verified instant, the remaining blocker is **executable opportunity quality**: Polymarket has 59 mathematically safe frontier rows but 0 within two ticks at the >=3c floor; same-Gemini crypto has 0 >=3c immediate FOK/FOK rows; G<->K is correctly quarantined until Caleb settles.

That means the current $0 new realized result must be reported as $0. The next milestone is not another candidate count; it is the first clean `SUBMITTED -> FILLED -> HEDGED -> SETTLED -> REALIZED` lifecycle under the corrected production architecture.
