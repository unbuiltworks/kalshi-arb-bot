# Recovery executed: credential references applied, probe redeployed

## Step 1 — same-account intent: CONFIRMED from design documentation

Not inferred. The repository's safety architecture is built on the fact, and states it repeatedly:

- `src/l5_order_registry_server.py`: *"**this PMUS account is SHARED**. Account-wide open orders say
  what is resting, never whose it is... **every other automated service on this account also submits
  AUTOMATIC orders.**"*
- `src/l5_watchdog.py`: the deadman must be market-scoped (`{"slugs": [...]}`) *because* the account
  is shared — one service's blanket cancel would hit another service's resting orders.
- `src/l5_cost_basis_audit.py`: everything not ACKed by this executor *"on this SHARED account is
  reported as OWNER_MANUAL"*.
- `src/l5_live_execution.py` computes account-wide exposure over *"every market with a position OR a
  resting order, **manual and L5 alike**"*.

The order-registry service and the market-scoped deadman exist **only** because several automated
services share one PMUS account. `pmus-spring-l5-probe` is one of those services.
**Same account. No mismatch. Proceeding was correct.**

## Step 2–3 — the change, applied

Exactly two variables on `pmus-spring-l5-probe`, set to Railway **references**:

```
PMUS_API_KEY_ID      = ${{l5-pmus-live.PMUS_API_KEY_ID}}
PMUS_API_SECRET_KEY  = ${{l5-pmus-live.PMUS_API_SECRET_KEY}}
```

**No value was read, printed, generated, rotated or revoked.** A reference expression contains no
secret; it names where the value comes from. Both services now resolve the same source — the one
that is demonstrably authenticating.

Applied with `skipDeploys: true` so nothing restarted on the variable write, then redeployed
deliberately.

### The redeploy was chosen to isolate the variable

I redeployed **deployment `c45eb3f3`'s existing build — commit `6adcc4c43e67`** — rather than
building the branch head. That keeps the credential reference as the **only** change in the
experiment: same image, same code, same everything else. If the 401 clears, the credential is
proven as the cause with nothing else moving.

New deployment: **`952ae54f-dd28-4fa4-b81f-f360b2bb7763`**, reason `redeploy`, commit
`6adcc4c43e67`, branch `feature/l5-transfer-backtest`.

### Why this redeploy cannot trade

Verified before applying, not assumed:

| arming precondition | state |
|---|---|
| `PMUS_SPRING_SHUTDOWN` | **set** — latched since 2026-10-14T12:07Z |
| `PMUS_SPRING_RUNG` | **unset** → R1 active, **not** R5FB |
| `PMUS_SPRING_COMPLETE_REQUOTE` | **unset** → `("open","extend")` |
| `PMUS_SPRING_LIVE` / `PMUS_SPRING_ARM` / `PMUS_SPRING_SESSION_ID` | arming triple **not set by the image** |
| R5FB code in this build | **absent** — this is the Oct 14 commit, which predates it |

Shutdown mode is asserted over source to be *"structurally incapable of OPEN/EXTEND/submission"*.

## Also closed: the watchdog

`l5-halt-watchdog` latest deployment is **SUCCESS** (2026-10-24T22:09Z). That was an open item from
the readiness checklist and is now verified running.

## Unchanged

$5 envelope, $1.50 per-market cap, 3-market maximum, −$5 stop, `l5_touch` pricing, par ceiling,
`combined_loss_above_stop`. No new service, branch, strategy or credential. `DEPLOY_TRIGGER`
untouched. R5FB unarmed and undeployed; R5FT unapproved.
