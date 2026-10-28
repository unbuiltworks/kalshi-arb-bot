# Proposed next bounded live campaign — completed-pair throughput

Not an authorization. Nothing here is deployed or armed; it is the scope I
would ask the owner to approve.

## What this campaign is for, and what it is not for

The last campaign proved execution: it submitted, filled, paired, settled
and reconciled, +$0.7083 venue-confirmed. **That question is closed and
this campaign must not re-ask it.** The open question is throughput:

> With the claim namespace isolated per campaign and the maker requote
> enabled, how many **completed pairs** does a window produce, and does the
> matched book grow without the stranded-inventory tail growing with it?

Success and failure are both measured in completed pairs, stranded
inventory, adverse selection on requoted fills, and confirmed settled net.
Order count is an input, never a result.

## Why four windows and not fewer

The two corrections reach different windows — the requote 0015z/0030z, the
identity fix 0045z/0100z — so a two-window campaign could easily exercise
only one of them and prove nothing about the other. Four is the smallest
run that gives each correction at least two independent windows while
keeping every ceiling exactly where it already is. It is not an increase
in anything: it is the same four windows as last time, with the two
refusal classes that dominated them removed.

## Scope — every limit unchanged from the approved campaign

| limit | value | change |
|---|---|---|
| eligible windows | 4 | unchanged |
| max total exposure | $20 | unchanged |
| max cash deployed | $12 | unchanged |
| max daily loss | $5 | unchanged |
| cumulative loss cap | $20 | unchanged |
| worst-case deficit | $3 | unchanged |
| round allocations / max rounds | 4 / 8 | unchanged |
| simultaneous resting orders | 1 | unchanged |
| maker-only | yes | unchanged |
| two-order exception | OFF | unchanged |
| OPEN / COMPLETE / REPLACE | 40 / 40 / 64 | unchanged |
| series | `cpc-btc-updown-15m-` | unchanged |

**The only two changes are code, not limits:**

1. deploy `c668cc3a` — campaign-scoped claim namespace, the requote's
   snapshot fix and its staleness gate;
2. set `L5_PRESEND_REQUOTE=YES`, which is the one new behaviour.

No new service, no infrastructure change, no loosened risk control, and no
reset of loss accounting: the cumulative-loss and obligation ledgers carry
forward untouched, as does venue reconciliation.

## Pre-arm checklist — including the one I missed last time

1. deployed SHA is exactly `c668cc3a` and the runtime reports it;
2. `L5_PRESEND_REQUOTE` reads YES and `presend_requote_enabled()` is true
   in the runtime config verifier;
3. approval fingerprint, expiry and nonce verified against the owner's
   message;
4. **the submission-claim ledger under the new `experiment_id` is read and
   shown EMPTY** — the fix makes this automatic, which is exactly why it
   must be verified rather than assumed. Last time I verified the campaign
   ledger and the nonce and passed over the claim ledger, and window 4
   traded nothing;
5. campaign window ledger shows 0 of 4 spent for the new nonce;
6. prior campaign's obligations resolved, residual flat, no unresolved
   accounting;
7. independent watchdog, cancellation, reconciliation and halt latch all
   confirmed live;
8. `L5_SHADOW_ONLY` lifted **last**, only after 1–7 pass.

If any check fails, the lock stays engaged and the specific blocker is
reported. No check is bypassed.

## What must be recorded, so the result is interpretable

Per window: completed pairs with their pair cost, stranded quantity and its
settlement, and for every requoted order its `ticks_lower`, whether it
filled, and what that fill settled at. **Requoted fills must be accounted
separately from unrequoted ones**, because the requote's known hazard is
adverse selection: it quotes behind a falling touch, and a fill there is
cheap precisely when the market is moving against the side being bought.
The replay's 0015z case is the shape to watch — a pair bought at 0.93 in
the same window as a leg that stranded worthless.

## Stop conditions

Window limit reached, approval expired, daily or cumulative loss cap hit,
watchdog halt, or any unreconciled obligation. No automatic relaunch after
a completed or crashed session, and no scope expansion or renewal without
a fresh owner decision.

## Standing recommendation, independent of this campaign

`L5_SHADOW_ONLY` is currently **0** on the running service while the
campaign it was lifted for has spent all four windows. Arming is already
structurally blocked — the campaign window ledger is keyed on the nonce and
reads 4 of 4, which no code change here touches — so there is no open
exposure. But the lock is the outermost guard and it should be re-engaged
rather than left relying on the ledger alone. I have not changed it: it is
a write to a live service's variables, which restarts it, and the next
action on this service will need variable writes anyway.
