# Is the venue's realized P&L already net of commission? — RESOLVED: **NO**

**Verdict: the venue's realized figure is GROSS of commission. The $9.14 of
credited maker rebates is therefore ADDITIVE and is NOT a double count.**
Settled arithmetically from committed artifacts, and pinned in
`tests/test_realizedpnl_commission_double_count.py`.

## First, a correction to the question

The brief (and `TRANSFER_2PT51.md`) names `trade.realizedPnl`. **That field
carries no value anywhere in our committed data:**

* `analysis/l5/r4_settlements.json` — both markets: `realized_pnl_rows: 0`,
  `realized_pnl_sum: null`, every trade row `rpnl: null`;
* `analysis/l5/live_tape_2026-09-13/r5ft_live_tape.jsonl` — 18 occurrences of
  `realizedPnl` across 7 settlement records, **every one null**;
* a repo-wide scan found non-null `realizedPnl` only in
  `data/l5/raw/positions_*.jsonl` and `data/traders/*/raw/positions_*.jsonl`,
  which are **Polymarket (crypto)** *position* rows for L5 and other target
  wallets — a different venue, a different schema, and carrying no
  `commissionNotionalCollected` at all. Not our account and not applicable.

So `trade.realizedPnl` feeds **no** figure, and the double-count risk could
not live there. The field that actually produces Ledger D's `realized_usd` is
the **POSITION_RESOLUTION activity's `afterPosition.realized`**
(`src/pmus_settlement_auditor.py::audit_market`, `realized = _amount(after.get("realized"))`,
`settled_by: position_resolution_activity`). That is what the test settles.

## The test

Two hypotheses that differ by exactly the market's credited commission:

    H_GROSS : realized == payout - cost_basis
    H_NET   : realized == payout - cost_basis ± credited_commission

A market qualifies as *discriminating* when both the venue realized figure
**and** a venue-credited commission are present and non-zero for the same
market, and the commission is large relative to the P&L.

### The discriminating case — `aec-atpcq-aleher-tundur-2026-09-07`

Sources, both committed:
`analysis/l5/r4_fidelity_decomposition.json`
(`per_market[...]settlement`) and `analysis/l5/venue_rebate_credits.json`
(`by_market[...]`, venue column `polymarket_us`).

| quantity | value | source |
|---|---|---|
| paying side | YES, $1.00/contract | venue POSITION_RESOLUTION (`POSITION_RESOLUTION_SIDE_LONG`) |
| payout | 10.41 contracts × $1.00 = **$10.4100** | ledger/venue reconciled |
| cost basis | **$8.4145** | 5 maker fills: YES 5.31@0.47, NO 5.20@0.51, NO 0.11@0.50, YES 5.10@0.49, NO 3.24@0.22 |
| **venue realized** | **$1.9955** | `afterPosition.realized` |
| **venue-credited maker rebate, same market** | **$0.0600** over 4 credited prints | negative `commissionNotionalCollected` on our passive executions |

Arithmetic:

    H_GROSS : 10.4100 - 8.4145 = 1.9955  == venue realized 1.9955   ✓ EXACT
    H_NET+  : 1.9955 + 0.0600 = 2.0555   != 1.9955                  ✗
    H_NET-  : 1.9955 - 0.0600 = 1.9355   != 1.9955                  ✗

The commission is **3.0 %** of the realized figure, and both numbers are
exact to four decimals, so the hypotheses are separated by ~600× the
resolution of the data. H_NET is excluded in either sign direction.

### Supporting case — `aec-atp-petbru-alecan-2026-09-07`

One maker fill, 5.24 @ 0.38, lost outright:

    realized = -1.9912 = -(5.24 x 0.38) exactly, to four decimals

i.e. gross, with no commission term. **Supporting, not discriminating:** this
market's own credit rows fall outside the committed credits snapshot (whose
audits stop at 2026-09-07T10:01Z, before this 17:43Z fill), so its credited
commission is unknown rather than zero. The modelled rebate on it would be
$0.02, which would have shown at the fourth decimal had it been netted in.

### Distribution across candidates

| market | venue realized | credited commission | reconciles to |
|---|---|---|---|
| `aec-atpcq-aleher-tundur-2026-09-07` | $1.9955 | $0.06 (4 prints) | **GROSS, exactly** |
| `aec-atp-petbru-alecan-2026-09-07` | −$1.9912 | not in snapshot | **GROSS, exactly** |
| `aec-atpcq-norgom-ollwal-2026-09-07` | `null` (flat at resolution, realized unconfirmed) | $0.08 (4 prints) | cannot discriminate |

**No case disagrees.** One case settles it; one supports it; one is silent.

## Why this is also structurally coherent

The two numbers live in different venue ledgers. A maker rebate is credited to
the **cash balance at the point of fill** (a negative
`commissionNotionalCollected` on our execution). `afterPosition.realized` is a
**position-accounting** field emitted at resolution. The settlement
breakdown's own `fees_usd` is `"venue_unreported"` — the venue puts no fee term
inside it. Exact gross reconciliation on both settled markets is what that
separation predicts.

## What this does and does not license

* **Does:** maker rebates may be ADDED to venue realized trading P&L without
  double counting — `all-in = venue realized + credited maker rebates +
  credited liquidity rewards`.
* **Does not:** it does not license merging the streams. Per CLAUDE.md, maker
  rebates, taker rebates and liquidity rewards stay **separate labelled
  lines** from trading P&L, and a rebate is realized only once credited and
  source-accounted.
* **Unchanged:** the all-in equation is still **not closable at a matching
  scope**. Ledger C is account-lifetime (2026-08-14 → 09-14); Ledger D is a
  partial, stale settlement set. The double-count question is closed; the
  **window-mismatch** question is not, and it needs the settlement audit over
  2026-08-14 → now. The liquidity rewards ($7.04) never carried this risk:
  they arrive as separate `ACTIVITY_TYPE_TRANSFER` rows and cannot be inside
  a trade or position row.

## Residual caveat, stated rather than buried

The verdict rests on two settled markets from the R4 cohort, both maker-only.
It is an exact arithmetic match in the discriminating case, not a sample of
many. Two reads would upgrade it from "settled on the evidence we hold" to
"settled on the venue's own statement":

1. a per-market reconciliation across a larger settled set, from the
   credentialed re-audit (`CREDENTIALED_REAUDIT_RUNBOOK.md`), joining
   `/v1/portfolio/activities` POSITION_RESOLUTION `afterPosition.realized`
   against the same market's summed `commissionNotionalCollected`;
2. whether `/v1/account/balances` moves by `realized` or by
   `realized + commission` across a settlement — the cash-side confirmation.

Neither is needed to answer the question asked; both are cheap to add to the
re-audit while the credential is in hand.

## Pinned

`tests/test_realizedpnl_commission_double_count.py` — 6 tests: the
discriminating case has both figures non-zero and separable; venue realized
reconciles to GROSS and to neither net hypothesis; the second market agrees;
the $9.14 is additive and `fees_usd` is `venue_unreported`;
`trade.realizedPnl` is null everywhere in committed data (so nobody re-opens
the question against that field by name); the two streams stay separately
labelled.
