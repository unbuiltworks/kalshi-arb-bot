# GO LIVE — stochastic action selection verified in production

Authorization: **OWNER-2026-09-17-CP1-STOCHASTIC-SELECTOR-COHORT**
Deployment `200e9cf1-3550-4aa7-bb44-3c4f9833b045` · serving SHA
`325a6dec55c31deb87532656ea2c091ce774fbfa` (the process logs `325a6dec55c3`).

## The eight pre-activation steps

| # | Step | Result |
|---|------|--------|
| 1 | Serving state established | prior deploy `75c3cef1` on `2368fb6f`; campaign ended `CAMPAIGN_STOP:HALT` 11:35:02Z, 13 sessions, 3 settled, **0 pending** |
| 2 | Smallest artifact change | **one** executable line, and not in `src/`: `PMUS_SPRING_ACTION_SELECTION=stochastic` |
| 3 | Pinned in the image | in the R1 ENV block; `list-variables` confirms **no service variable of that name**, so nothing shadows it and a restart cannot silently return to modal |
| 4 | Deployment test | 3 tests; one resolves the value the way the runtime does rather than grepping for the string |
| 5 | Lineage + targeted + full regression | lineage **14 passed**, `ACCEPTANCE: True`; targeted **33 passed**; full suite **5068 passed, 27 skipped, 1 xfailed, 0 failed** — run again *after* the re-pin |
| 6 | Re-pin through the normal procedure | `analysis/l5/authorization/` `3baae6d3 → 870163b1`, class DOCUMENTATION, new reason. `REVIEWED` **deliberately not advanced** (no COPYed file moved). `FORBIDDEN_CLASSES` untouched |
| 7 | Zero unresolved exposure | `campaign_end` pending 0; both markets `position_net 0`, `afterNet 0.0000` |
| 8 | Serving SHA **and** runtime selector | SHA `325a6dec55c3` in every campaign record; decision rows carry `"action_selection":"stochastic"` |

## The defect is fixed, and the evidence is one decision

First session, `aec-itfme-artkuk-egople-2026-09-17`, 11:57:26–11:58Z:

| # | modal | u | sampled | acted | p(sampled) | probability vector | inventory_before |
|---|-------|---|---------|-------|-----------|--------------------|------------------|
| 0 | open | 0.782317 | open | open | 1.0 | open=1.0 | flat |
| 1 | open | 0.009586 | open | open | 1.0 | open=1.0 | flat |
| 2 | extend | 0.819481 | extend | extend | 0.6732 | extend=0.6732, complete=0.3268 | up=0 dn=0.17 |
| **3** | **extend** | **0.061254** | **complete** | **complete** | **0.3214** | **extend=0.6786, complete=0.3214** | **up=0 dn=0.34** |
| 4 | complete | 0.060439 | complete | complete | 0.7205 | complete=0.7205, extend=0.2795 | up=0.17 dn=0.34 |
| 5 | complete | 0.680988 | complete | complete | 0.7205 | complete=0.7205, extend=0.2795 | up=0.17 dn=0.34 |
| 6 | complete | 0.228255 | complete | complete | 0.7205 | complete=0.7205, extend=0.2795 | up=0.17 dn=0.34 |

**Decision 3 is the whole point.** The modal selector said `extend` at p=0.6786.
The seeded draw `u = 0.061254` fell in the lower 0.3214 of the CDF and returned
`complete`, and the controller executed `complete`. Under the old selector this
was decision 77 of an unbroken EXTEND run.

**Matched inventory was created** — `up=0.17` against `dn=0.34` at decision 4.
This is the first matched pair the new engine has ever held. It is **not**
realized P&L and is not called that anywhere.

EXTEND run length so far: **1**. The previous campaign's was **76**.

## What the venue did next, recorded and not repaired

Three subsequent COMPLETEs at YES 0.57 (fresh opposing ask 0.5900, so
non-crossing and post-only-safe) came back `ORDER_STATE_REJECTED` /
`ORDER_REJECTED_ZERO_FILL`, and the complement-acceptance proof was revoked with
`revoked:complete_rejected_at_fresh_non_crossing_price`.

That is a **venue-side rejection after submission**, not a shell refusal and not
a model choice. It is recorded as itself. No sampled COMPLETE was converted into
an EXTEND at any point: `sampled` and `acted` agree on all seven decisions and
`divergence_class` is `no disagreement` on all four COMPLETEs.

Per the authorization, this is **not tuned during the cohort**.

## Two known, deliberately unrepaired items

1. **Residual-cap overshoot** — filed separately in
   `SAFETY_RESIDUAL_CAP_OVERSHOOT_2026-09-17.md`. The owner asked that its
   repair not be combined with this deployment.
2. **Stale label.** The stochastic block still reads
   `"SHADOW draw from the executed distribution; not acted on"`. The draw *is*
   now acted on. A record-keeping inaccuracy, not a behavioral one — `acted`,
   `refusal` and `divergence_class` are all correct — but a later reader of the
   evidence could be misled by it. Not touched during the cohort.

## Accounting, unchanged and not re-labelled

| Lifecycle | Matched-pair | Directional residual | Confirmed realized |
|---|---|---|---|
| 1 `aec-itfwo-erishi-jiahan` | $0.00 | −$0.1836 | **−$0.1836** |
| 2 `aec-itfme-adrbar-fautab` | $0.00 | −$0.3783 | **−$0.3783** |
| cumulative | **$0.00** | **−$0.5619** | **−$0.5619** |

`aec-itfme-conbra-andpet-2026-09-16` settled `SETTLED_REALIZED_UNCONFIRMED` —
flat at the venue (`position_net 0`) but the venue never returned `realizedPnl`
rows, so it is **not** counted as realized and belongs to an earlier
authorization regardless.

Nothing in the current cohort is realized. There is an open position.

---

## Settlement evidence — the market resolved with no auditor running

Observed read-only at 12:29Z from the **public** venue record, no credentials used:

```
slug           aec-itfme-artkuk-egople-2026-09-17
status         MARKET_STATUS_RESOLVED
ep3Status      EXPIRED
outcomes       ["Artur Kukasian", "Egor Pleshivtsev"]
outcomePrices  ["0", "1"]
closed         true
updatedAt      2026-09-17T12:24:39Z
bbo            state MARKET_STATE_EXPIRED, settlementPx 0.0000, no bid/ask
```

The long/YES leg (Kukasian) settled to **0**; the opposing leg to **1**.

**The timing is the finding.** The campaign halted at **12:00:30Z**. The market
resolved at approximately **12:24:39Z** — 24 minutes later. The settlement audit
is performed by the probe process, and the probe was already gone. So:

- There is **no `POSITION_RESOLUTION` activity row**, no `realized_confirmed`,
  and **no realized figure** for this position.
- The ledger still carries it at full downside against the daily stop.

This is precisely the condition the runtime alarm named at 12:00:09Z:

> "if the market did resolve, we are over-stating loss and withholding realized
> cashflow until the settlement audit records it."

**Nothing here is realized and no figure is computed from the 0.92 cost basis.**
The public record says what the *market* did. It does not say what *our account*
received, and only the authenticated settlement audit can establish that.

The first matched pair this engine ever held has therefore resolved **unmeasured**
— which is the one thing the position existed to measure. Recording it requires
running the probe's settlement audit, and that is an owner decision, not a
watch action: the probe cannot be started read-only, because the live mandate
runs to 23:45:17Z and a started probe would immediately select markets and place
orders. Not taken.
