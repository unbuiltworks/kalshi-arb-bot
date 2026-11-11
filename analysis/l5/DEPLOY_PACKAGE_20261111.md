# Disarmed deployment package — BTC15m and BTC5m (prepared 2026-11-11, NOT executed)

Nothing in this file has been deployed. Nothing here arms anything. Every
step that changes production needs the owner's explicit approval first.

## 1. What is running now (read 2026-11-11 23:02Z)

| service | commit | mode | orders |
|---|---|---|---|
| `l5-pmus-live` (BTC15m) | `ff4b5291a` | disarmed: `L5_LIVE_HALT=1`, `L5_SHADOW_ONLY=1` | 0 live |
| `l5-halt-watchdog` | unchanged | independent, sees 0 venue orders | - |
| `l5-btc5m-public-recorder` | `bdd0c3a78` | read-only, no credentials | cannot place |

There is no BTC5m trading service.

## 2. BTC15m — candidate prepared

Branch `candidate/l5-btc15m-isolation-origin-20261111`, built on the
deployed commit. The 15-minute image copies an explicit file list; of the
files this candidate changes, the image ships seven:

| file | what changes when every switch is as deployed |
|---|---|
| `l5_live_execution.py` | an untracked venue order in a 5-minute market (or with a `b5m-` id) is listed as foreign: never adopted, never cancelled, counted in account-wide exposure at $1 a contract. Settled-market rows get an origin label. Nothing else. |
| `l5_origin.py` (new) | the registry of strategy id prefixes and series; pure. |
| `l5_pmus_live_runner.py` | one read-only `origin` block on the heartbeat. |
| `l5_campaign_approval.py` | `cpc-btc-updown-5m-` is an allowed series with a $5 ceiling. A document names one series; `reserve_window` refuses another's. |
| `l5_live_authorization.py` | a claim refuses a market outside the approved document's series; the 5-minute claim exists (unused by this service). |
| `l5_runtime_config_verify.py` | the `series_allowlist` row expects both series. |
| `l5_two_sided_probe.py` | completion band fix; inert unless `L5_TS_COMPLETION_RESTS_AT_CEILING=ON`, which is off. |

No variable changes. No new variable is required. The image does not ship
the 5-minute adapter or controller, and a test asserts that.

What does NOT change: limits, the daily and lifetime loss halts, the
watchdog, the owner handshake, maker-only, the one-window authorization,
the conservative ledger entries.

### If the owner approves a disarmed deploy

1. Merge the candidate PR into `integration/l5-safe-release-candidate-20261109`
   (owner's merge; I do not merge).
2. Deploy that exact commit with no variable write
   (`serviceInstanceDeployV2(serviceId, environmentId, commitSha)`), after
   confirming no staged Railway change is pending.
3. Verify, read-only, before anything else:
   - deployment SUCCESS and the commit hash is the approved one;
   - `L5_LIVE_HALT=1`, `L5_SHADOW_ONLY=1`; submission gate `blocked`,
     reason `OPERATOR_HALT`; `armed` false; 0 live orders; watchdog 0 orders;
   - runtime config `PASS` including `series_allowlist`;
   - heartbeat `origin.ok` true, and `origin.realized.account_wide` equal
     to the realized lines it already reported (a label moved no number);
   - `day_realized` and all-time realized unchanged from before the deploy;
   - no traceback, no `L5_LIVE_ALARM`.
4. Rollback: deploy `ff4b5291afc8a272cd5e8ae5e873120a1d4b67d6` by commit,
   same call. No state migration is involved: the new ledger fields are
   additive and the old code ignores them.

Scripts: `analysis/l5/live_20261111_1900z/bal_common.py` (client, ids) and
`bal_verify.py` (the read-only checks above, minus the `origin` lines).

## 3. BTC5m — code is authorization-complete; there is no service, and I do not recommend one yet

What exists, tested, in `src/`:
- `l5_btc5m_controller.py` — pure decision, default off.
- `l5_btc5m_execution.py` — the adapter. Every order spends
  `claim_btc5m_submission` before the wire.
- the claim itself, in `l5_live_authorization.py`.

What a BTC5m service would still need (none of it built):
- a runner: one venue transport per window (its own preflight and the
  owner's arm token), the public book, the index probability
  (`l5_btc_reference`, already in the repo), the adapter's
  `owns_venue_order_id` installed as the transport's cancel-ownership test,
  settlement from the venue's own result, a heartbeat;
- its own Railway service, volume and variables (owner-created, as the
  recorder was);
- watchdog coverage that knows two services share the account.

Why I recommend not building or deploying it now: on 42 recorded 5-minute
windows the controller loses in replay at every setting, with the book mid
and with the independent index probability; and where the index
probability disagrees with the book, the book was right. A disarmed
5-minute service would not measure anything the recorder plus the offline
replay does not already measure. Stage gate 3 of `analysis/l5/CLAUDE.md`:
negative evidence is BLOCKED, not something to deploy around.

## 4. Simultaneous execution — what still blocks it

1. **Account-wide loss accounting across two services.** The 15-minute
   service's ledger is the account-wide ledger. It never sees a 5-minute
   fill's price, so it books every 5-minute position at $1 a contract and
   every matched 5-minute pair as a loss of its full quantity at
   settlement. That is conservative, and it would trip the $5 daily halt
   within a few 5-minute windows. Running both needs the 5-minute
   strategy's authenticated fills to reach the account-wide ledger (one
   shared record, or one process), or an account-level limit both read
   from the venue. This is a design decision, not made.
2. **The account is already past its lifetime limit** (account-wide
   -$21.0406 against $18.6325; includes the manual trade at its $5 bound).
   Restating the manual trade needs the venue trade export.
3. **Neither strategy has positive expected net return in replay.**
4. **The watchdog covers one service.** It cancels only venue ids the
   15-minute executor's ownership registry names, so it would not cancel
   a 5-minute order -- and nothing would cancel a wedged one. Whether an
   aged 5-minute order makes it assert the shared halt is not verified.
5. **A BTC5m runner and service do not exist** (section 3).

## 5. Approval wording, if the owner wants the BTC15m candidate deployed disarmed

> I authorize a DISARMED deployment of commit `<SHA of the merged
> candidate>` to `l5-pmus-live`. No variable changes. `L5_LIVE_HALT` and
> `L5_SHADOW_ONLY` stay engaged. No live trading, no ledger writes, no
> taker execution and no change to any limit is authorized.

No live window is proposed for either strategy.
