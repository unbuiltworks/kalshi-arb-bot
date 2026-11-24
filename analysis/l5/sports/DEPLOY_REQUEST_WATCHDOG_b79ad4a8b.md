# Watchdog `b79ad4a8b`: deployment request and the two production drills

24 Nov 2026. **Nothing here is approved.** Three separate approvals are
asked for, in order. None of them arms trading.

## A. Deploy the watchdog release (no orders involved)

| | |
|---|---|
| Service | `l5-halt-watchdog` |
| Branch / commit | `release/l5-halt-watchdog-b79ad4a8b` / `b79ad4a8ba54fe96853de34d112f235424e99e42` |
| Running now | `8c9bee06f` |
| Change | The emergency-halt marker can be fetched with a read-only credential, so the signed file can live in our private repository. Off unless the token is set. Nothing else changes. |
| Tests | 547 passed in the watchdog's own test files; joint test with the sports bot 9 passed |
| Rollback | reconnect to `release/l5-halt-watchdog-8c9bee06f` |

**Set by the operator on the watchdog, in the Railway dashboard** (never in
the repository, chat or logs):

| Variable | Value |
|---|---|
| `L5_EMERGENCY_HALT_SECRET` | the halt signing secret (long random string). The operator keeps a copy to sign with. Separate from the campaign signing secret. |
| `L5_EMERGENCY_HALT_BEARER` | a fine-grained GitHub token: this repository only, Contents read-only |
| `L5_EMERGENCY_HALT_URL` | `https://api.github.com/repos/unbuiltworks/kalshi-arb-bot/contents/ops/EMERGENCY_HALT.json?ref=ops/halt-marker` |
| `L5_EMERGENCY_HALT_ACCEPT` | `application/vnd.github.raw` |
| `L5_EMERGENCY_HALT_SERVICE` | `pmus-spring-l5-probe` |

The file lives on its own branch (`ops/halt-marker`), which holds nothing
else, so publishing a halt is one small commit. Signing:
`scripts/l5_emergency_halt_sign.py --service pmus-spring-l5-probe --reason "..."`,
run on the operator's own machine.

**Checks after deploying (no order, no latch):**
1. Boot line shows the marker as configured, with a read credential.
2. Sweeps continue every 20 s; nothing logged about the marker while the
   file is absent (the host answers "not found", which is quiet).
3. The operator commits a file signed with a WRONG secret. Expected within
   20 s: `MARKER_SIGNATURE_INVALID`, nothing latched. This proves the
   watchdog is reading the file from inside its own network. Remove it.

If check 3 shows nothing within a minute, the fetch is not working and
the mechanism is not relied on.

## B. Drill 1: the watchdog cancels after the bot's process dies

Needs its own exact authorization. A live order is placed.

| Term | Value |
|---|---|
| Order | one maker bid, 1 contract, three ticks behind the best bid, price between 0.30 and 0.70, on one pinned market of a permitted competition with an international price |
| Window | 10 minutes |
| Method | the bot's built-in drill switch: it places the bid, confirms it rests, then exits and stays down for the drill period |
| Pass | the watchdog cancels that exact order and the venue shows it cancelled within 90 s of the bot's exit; no fill; no other order |
| Worst case | the bid fills before the cancel: 1 contract, at most $0.70 |
| After | the watchdog's own rule latches the shared halt. Nothing trades until the owner's reset |

This is the drill that failed on 23 Nov (the watchdog's last ownership
read was 1.7 s older than the order). The fix (ownership re-read every
2 s) has been deployed since, and has not yet been proven at the venue.

## C. Drill 2: the signed marker halts and cancels

Needs its own exact authorization, and a halt reset after drill 1.

| Term | Value |
|---|---|
| Order | as in drill 1; the bot stays running |
| Trigger | the operator commits a validly signed marker at time T |
| Measured | T to latch; latch to the bot's first refused placement; latch to venue-confirmed cancel |
| Pass | order confirmed cancelled at the venue within 90 s of T; no fill; no placement after the latch; the latch still present after restarting the sports bot |
| After | halt latched; owner's reset needed before any campaign |

## Halt resets

Each drill ends with the shared halt latched. Resetting it is the owner's
procedure in `analysis/l5/HALT_RESET_MAINTENANCE_PROCEDURE.md` and needs
its own explicit approval each time; the exact request can only be
written after the latch exists, because it quotes the latch's fingerprint.

## What these prove, and what they do not

Proven by a pass: at the real venue, with a real resting order, the
watchdog cancels without the bot (drill 1) and on the operator's command
without a deployment (drill 2). Not proven: behaviour with many orders
across several markets, or with the repository host unreachable (the
marker then does nothing; the watchdog's own rules and the bot's own
stops still act).

---

## Status and follow-up — 24 Nov, 03:25 UTC

**`b79ad4a8b` is deployed** (03:13 UTC, verified: sweeping every 20 s,
zero open orders, not halted; halt marker not yet configured by the
operator). The `ops/halt-marker` branch exists with no marker on it.

**Follow-up release for the owner's new requirement** ("losing access to
the emergency-halt channel is detected and prevents new exposure after a
bounded timeout"). Not approved, not deployed.

| | Watchdog | Sports bot |
|---|---|---|
| Release candidate | `release/l5-halt-watchdog-09e94cc9d` (`09e94cc9da92b15e68ddb0ac8f75b80e872c1291`, code `092cb1c98`) | `release/pmus-sports-rn1-12d5d3daf` (`12d5d3daf78efcc919f0060c63a922561cb0dc80`, code `3146c04b7`), which replaces `0969ddadf` |
| Change | A halt channel that has not answered for a set time makes the watchdog withhold its "no halt" attestation. "No such file" counts as an answer. | How old that attestation may be before the bot stops opening exposure becomes a setting. |
| Setting (proposed) | `L5_EMERGENCY_HALT_CHANNEL_MAX_DOWN_S=120` | `PMUS_TWO_SIDED_SHARED_HALT_MAX_AGE_S=120` |
| Default in code | 0 = off (as before) | 900 (as before) |
| Tests | 10 new; 557 watchdog tests pass | 2 new; full suite 5,528 passed, same 24 failures as the running build |

**The bound, with both proposed settings:** channel lost → watchdog
withholds after 120 s → the bot's last attestation is stale 120 s later →
no new exposure. About four minutes, plus one 20-second sweep and one
15-second read. Without the bot-side setting it would be about seventeen
minutes.

**What it does and does not do**
- Stops new exposure only. Nothing is latched; cancels and completions
  continue; trading resumes by itself when the channel answers.
- The watchdog's own cancel rules are unaffected by a channel outage.
- A side effect worth having: a dead watchdog now stops the bot opening
  exposure within two minutes, not fifteen.
- A cost: an outage of the repository host, or an expired token, pauses
  new exposure. The token's 90-day expiry becomes an operational date.
- Not tested against the real host from inside the service; the
  wrong-signature read test and a deliberately broken URL would show it.

**Operator setup, simplified** (if approved): the operator sets two
secrets (`L5_EMERGENCY_HALT_SECRET`, `L5_EMERGENCY_HALT_BEARER`); this
session sets the fixed, non-secret values (`_URL`, `_ACCEPT`, `_SERVICE`,
and the channel timeout).
