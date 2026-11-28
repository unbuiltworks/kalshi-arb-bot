# L5 — Outstanding accounting issues

Opened 2026-10-24 at owner instruction: *"Do not reset historical losses or
silently reclassify the 49 unknown-cost-basis legs. Record the unreconciled
historical attribution discrepancy as an outstanding accounting issue."*

Nothing in this file has been resolved, worked around, or netted off. These are
open items recorded so they cannot be forgotten, and so that no later reader
mistakes a measured-but-unexplained figure for a settled one.

---

## A1 — The historical attribution identity does not close

**Status: OPEN. Not blocking the bounded campaign; blocking any claim about
whose losses these were.**

The executor reports four realized populations and an identity relating them.
In production on 2026-10-24T22:32Z the identity does **not** hold:

```
identity        : account_wide = verified_l5 + manual + unknown_unattributed
identity_holds  : false

account_wide_realized          :  -11.6213   <- the figure every loss gate reads
verified_l5_realized           :   -3.2265
unknown_unattributed_realized  :  -84.6200
  pre_baseline_total_l5_share_unknown : -79.0100
  post-baseline settlements not attested: the remainder
manual_realized                :  null   (UNATTRIBUTED_RESIDUAL_NONZERO)
legs_priced_at_one_dollar_upper_bound : 49
```

### What is actually wrong

`unknown_unattributed_realized` (−$84.62) is **larger in magnitude** than the
anchored account-wide total (−$11.6213) that the loss gates enforce against.
For the identity to close, `manual_realized` would have to be approximately
**+$76.23** — i.e. other activity on this account would have to have made
money. That residual is not computable, because:

- this is a **shared account** and the venue does not report who placed a
  historical fill;
- `manual` is defined as a residual and is only meaningful once nothing is
  unattributable, so the code correctly refuses to compute it and returns
  `null` with `UNATTRIBUTED_RESIDUAL_NONZERO`;
- 49 legs are priced at a **$1/share upper bound**, which the code labels "an
  estimate, not a verified realized loss";
- of those, `establishable: 47` and **`unestablished: 2`** — two legs have no
  measured cost at all, and the audit explicitly refuses to treat unknown as
  zero.

### Why it is not blocking the bounded campaign

Every loss gate reads `account_wide_realized`, which is derived from an
authoritative cash anchor (`baseline_cash_usd $350.05255` at
2026-10-16T02:45:12Z) rather than from the sum of these sub-populations. That
is the conservative choice available: it is account-wide rather than
L5-attributed, so an unattributable loss is still counted as a loss.

The new campaign bound (§A3 below, now implemented) measures *additional* loss
from a captured starting point, so the campaign's own $5 limit does not depend
on this discrepancy being resolved. The historical figure sets where the
lifetime $20 ceiling stands, and that ceiling is unchanged.

### What would close it

An authenticated per-fill ownership and cost walk for the 49 legs, reconciled
against the cash anchor, producing either a computable `manual` residual or a
documented statement that ownership is unrecoverable for a named subset. The
cost-basis audit already does the cost half (`establishable: 47`) and runs
read-only (`writes: "NONE"`); the ownership half is the missing piece.

**Explicitly NOT a resolution:** applying the $294.09 of placeholder costs.
`overstatement_usd` is $240.4352 against $50.6148 of measured actual cost, so a
naive application would book a quarter of a thousand dollars of loss that did
not occur. The audit's own label says why: the overstatement figure covers
established legs only, "because the unestablished legs have no measured cost to
subtract -- their cost stays UNKNOWN and is never treated as zero."

---

## A2 — `require_cash_evidence` is implemented, tested, and called by nothing

**Status: OPEN. Found 2026-10-24 while capturing the campaign baseline.**

`ValidationGates.require_cash_evidence` refuses `NO_AUTHORITATIVE_CASH_READING`
on an absent or empty balance reading, and its docstring states it "refuses to
arm". Two comments in `l5_pmus_live_runner` repeat that claim:

> "None is NOT unlimited: the cash gate then binds on its configured ceiling
> alone and require_cash_evidence() refuses to arm."

A search across `src/` and `scripts/` finds **no call site**. The only
references are the definition, those comments, and three tests that exercise
the method directly. So the stated arming protection is not in force.

**Mitigating fact, established today:** an authoritative reading *is* being
obtained. `L5_LIVE_CASH` reports `available_cash_usd: "341.18605"`,
`read_ok: true`, `source: "/v1/account/balances via transport.account()"`,
refreshed every 60s, and it is installed on the executor — so
`INSUFFICIENT_AVAILABLE_CASH` inside `refuse()` *is* live and does bind. What is
missing is only the arm-time precondition, and the figure it would check is
currently present and healthy.

**Not fixed here deliberately.** Wiring it would add a new arming blocker to a
money-capable service that the owner has not approved, immediately before a
bounded campaign. It is recorded for an explicit decision instead. The campaign
baseline now persists the starting cash from this same authoritative read, so
the starting figure is captured either way.

---

## A3 — The campaign's additional-loss bound (CLOSED 2026-10-24)

Recorded here because it was the owner's answer to A1's consequence: the
lifetime $20 ceiling's remaining headroom ($8.3787) is an artefact of the
unreconciled history above, not a decision about the next campaign.

`src/l5_campaign_baseline.py` now persists the campaign's starting cash,
inventory, open orders and realized loss, and bounds

```
max(0, baseline_realized - current_realized) + max(0, open book worst case)
```

at a **$5 code constant**, independently of the lifetime gate and of the
campaign worst-case budget. It includes outstanding obligations by computing
the book's worst-case deficit over this order plus the worst subset of
still-fillable resting orders. Unset, it is inert; set, it may only tighten.

It resets nothing: the baseline records what realized *was*, and the lifetime
ceiling still reads the full figure on every evaluation.

---

## Index

| ID | Item | Status |
|---|---|---|
| A1 | Historical attribution identity does not close (`identity_holds: false`) | **OPEN** |
| A2 | `require_cash_evidence` implemented and tested but never called | **OPEN** |
| A3 | Campaign additional-loss bound measured from a persisted baseline | CLOSED 2026-10-24 |
