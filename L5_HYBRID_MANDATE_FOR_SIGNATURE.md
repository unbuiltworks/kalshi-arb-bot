# Hybrid maker/taker completion — mandate for signature

**Not active. Not installed. Nothing is armed.** The owner signs §4; I
install nothing until then.

## 1. The eight requirements are met by existing code, not by new code

Each is implemented in the deployed executor and is now pinned by
`tests/test_l5_hybrid_completion_safety_verification.py` (27 tests), so a
regression in any of them fails a test rather than reaching the venue.

| # | requirement | mechanism | pinned |
|---|---|---|---|
| 1 | preserve maker entries / continuous pairing | defensive path arms only in the last **60 s** before the binding terminal, with a **30 s** dwell; maker ceiling 0.96 and the 0.20–0.60 opening band untouched | ✓ |
| 2 | favourable completion from authenticated executable depth | `DEF.decide(exit_price, displayed_depth, taker_fee_per_contract, …)`, priced on a walk **sized to the whole residual** | ✓ |
| 3 | taker vs holding vs selling the leg | `DEF.alternatives` prices `HOLD_TO_SETTLEMENT` (best `1−p`, worst `−p`), `MAKER_COMPLETE_AT_CEILING`, `DEFENSIVE_UNWIND` in dollars | ✓ |
| 4 | partial completion, no over-hedge | *"OVERHEDGING IS REFUSED, NOT RESIZED IN FLIGHT"* | ✓ |
| 5 | cancel + reconcile before replacing | cancel → **prove gone by the venue's own re-read** → re-read our inventory → decide again → charge budget → mandate one order | ✓ |
| 6 | no new opening while unmatched eats the budget | `RESIDUAL_CAP` on **prospective** net | ✓ |
| 7 | watchdog, registry, idempotency, exact-ID cancel, loss limits, halts | unchanged; 12-gate `GATE_ORDER` pinned | ✓ |
| 8 | reuse existing services | **still exactly two** claim-gated send sites | ✓ |

### Requirement 6, which I had previously assumed rather than verified

The cap reads **prospective** net — realized residual **plus every live
order as if it filled** — not realized inventory. `exempt_open` is
`b_abs == 0`: nothing held **and** nothing resting. So an opening while
unmatched inventory exists is **never exempt**, and is refused once
`|prospective after| > min(k × clip, 3)`.

That hardening has a cost attached to it in the source: on 1130z realized
residual sat inside the cap while four orders filled within seven seconds
to **3.24 against a cap of 1.80**. Reading the realized book is what let it
through.

Residual-reducing legs are never refused at any depth, so this cannot
strand a position it could have completed.

## 2. The ceiling must be 1.00, not the code default

`DEFENSIVE_PAIR_CEILING` defaults to **1.20**. At 1.20 the 38-window bound
arithmetic gives **−8.0931** — worse than doing nothing — because a matched
pair redeems exactly $1.00 and PMUS has **no maker-fee term**, so there is
nothing above $1.00 to win. The mandate sets **1.00**. A test asserts the
default is 1.20 and that a caller can tighten it.

## 3. Verification status

- **Commit:** `dbfbd3ee` (`claude/compassionate-allen-wic64u`)
- **New tests:** 137 passing across the four files added this session
  (hybrid safety verification 27, shadow defensive decision 14, residual
  sampler 13, first-leg ceiling 83)
- **Full suite:** FAILED+ERROR set is **77, byte-identical to the pristine
  `06f7b27c` baseline** — no regressions
- **Venue preflight** (`a9bbbd02`, commit `c39183a3`): `ok: false`,
  `fail: ["owner_arm_token"]`; every other row PASS — halt gate permitted,
  shadow lock clear, residual ceiling 3, reconciled, `journal_errors 0`
- **Venue state:** `open_orders_at_venue 0`, `pending_terminal_total 0`,
  adopted and quarantined empty
- **Gates in force:** cash $12.00, worst-case deficit $3.00, daily $5,
  L5 cumulative $20, account-wide $18.6325, `NO_RECYCLING`
- **Prior approval `ac855ce3…`: EXPIRED** 22:20:15Z — will not be reused

## 4. The mandate to sign

Owner-minted, owner-installed. Scope is completion only; it does **not**
grant autonomous opening.

```json
{
  "approved": true,
  "nonce": "<owner-minted, unique>",
  "series": "cpc-btc-updown-15m-",
  "issued_utc": "<at install>",
  "expires_utc": "<issue + up to 4h>",
  "max_windows": 3,
  "max_total_exposure_usd": "20",
  "max_daily_loss_usd": "5",
  "max_worst_case_deficit_usd": "3.00",
  "execution_scope": "DEFENSIVE_COMPLETION_ONLY",
  "defensive_pair_ceiling": "1.00",
  "defensive_loss_budget_usd": "2.00",
  "max_live_orders": 1,
  "experiment_id": "<new; the spent one must not be reused>"
}
```

Owner settings, in this order, `PMUS_Q1_LIVE` **last**:
`L5_DEFENSIVE_COMPLETION=1` · `L5_LIVE_AUTH_NOT_AFTER` = campaign end ·
new experiment id · then `PMUS_Q1_LIVE=YES`.

**Automatic halts:** halt after the **first** defensive crossing and
reconcile before a second · existing halt latch · defensive budget $2.00 ·
daily $5 · account-wide $18.6325 · worst case $3.00/window.

## 5. Reporting, strictly separated

Against the live maker-only baseline — matched surplus **+$0.1933/window**
(sd 0.2400, t=3.320), 23 completed pairs over 15 reconstructable windows,
settled realized **+$3.8861** over 17 windows:

| to report | definition |
|---|---|
| submitted | venue-acked orders, with coids |
| filled | venue-confirmed fills, qty and price |
| settled net | after settlement only |
| residual losses | unmatched legs, separately from pair surplus |
| completed pairs/window | vs the baseline above |

No shadow or modelled figure will be reported as any of these.

## 6. The precondition that still blocks execution

Stated once, because it decides whether signing produces evidence.
**There is nothing to complete.** Live at 22:48–22:49Z, every ~13 s:

```
L5_TS_COMPLETION {"reason":"NO_INVENTORY_TO_COMPLETE",
                  "up_qty":"0","dn_qty":"0","still_fillable_orders":0}
```

Completion is downstream of opening; openings come from `handle(row)`, one
call per admitted L5 action; `ingest:admitted` has never incremented and
the feed has served the identical page since **17:54:50Z**.

So signing §4 today spends 3 windows and submits nothing. The hybrid path
is **ready and verified**; the input is not. The unblock is unchanged: allow
`data-api.polymarket.com` so the proxy-wallet / venue-move / stale-view
question can be settled, after which openings resume and this mandate
becomes live-meaningful.
