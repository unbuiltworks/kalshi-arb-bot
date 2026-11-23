# Immediate halt without a redeploy — enabling the existing signed marker

23 Nov 2026. **Nothing here is approved or configured.** This uses the
mechanism already in the running watchdog build (`8c9bee06f`); no new
service and no new code.

## What it is

The owner publishes a small signed file at a URL. The watchdog fetches it
on every sweep (20 s). A valid file latches the shared halt on the
watchdog's own disk. From then on:
- the sports bot reads the latch (every 15 s) and refuses every placement;
- the watchdog cancels every resting order by exact id, whatever its age,
  and re-reads each one at the venue;
- the latch survives restarts and redeploys of either service. Only the
  owner's reset procedure clears it.

Expected time from publishing to no orders at the venue: under a minute.
Not yet measured in production; the drill measures it.

## Checked against the owner's four requirements (by reading the code and
running its tests: 95 passed)

| Requirement | Finding |
|---|---|
| Authenticated | HMAC over command, time, nonce, service and reason, compared in constant time. No secret set = mechanism off. An unsigned or wrongly signed file is refused by name. |
| Replay-resistant | Each file carries a nonce; acted nonces are stored on the watchdog's disk, so a restart cannot re-act. A file older than 24 h or dated more than 2 min ahead is refused. A file for another service is refused. |
| Durable | The latch is a file on the watchdog's volume. It cannot be cleared by the watchdog, the bot or the marker: the only command that parses is HALT. |
| Works if the bot or a session dies | The watchdog acts alone: it reads the file, latches and cancels. The sports bot does not need to be alive, and no session is involved after the file is published. |

Limits to know:
- If the URL cannot be fetched, nothing happens (by design: a network
  error must not latch). The halt is only as available as the URL.
- A forged file cannot start trading; the worst a holder of the secret can
  do is stop it.
- Whoever holds the secret can halt. It should not be stored in a session.

## Decisions needed from the owner

1. **Where the file lives.** It must be fetchable by the watchdog without
   credentials and writable by the owner in seconds without a deployment.
   The file holds no secret (a reason, a time, a nonce, a signature), so a
   public location is acceptable. Hosts that cache (for example raw file
   links on a code host, often cached for minutes) would add that delay.
2. **Who holds the signing secret.** Recommended: the owner generates it,
   sets it on the watchdog in the Railway dashboard, and keeps a copy to
   sign with. This session never sees it. The signing tool is
   `scripts/l5_emergency_halt_sign.py` (run locally).
3. **Whether this session may publish a pre-signed file.** If yes, the
   owner signs one in advance and tells the session where to put it; the
   session can then halt without holding the secret. One file, one use.

## Enabling (one watchdog redeploy; no orders involved)

1. Owner sets `L5_EMERGENCY_HALT_URL`, `L5_EMERGENCY_HALT_SECRET` and
   `L5_EMERGENCY_HALT_SERVICE=pmus-spring-l5-probe` on `l5-halt-watchdog`.
2. Verify from the watchdog boot log:
   `L5_EMERGENCY_HALT_MARKER_CONFIG configured: true`, and steady sweeps
   with no marker error. With no file published the watchdog logs nothing
   more (unreachable is a quiet steady state), so step 3 matters.
3. Dry check with no risk: publish a file signed with a WRONG secret. The
   watchdog must log `MARKER_SIGNATURE_INVALID` and latch nothing. This
   proves the URL is being read. Remove the file.

## Bounded verification with a real resting order (needs its own exact
authorization: it is a live, money-capable test and it latches the halt)

Terms to authorise, all exact:
- one market, one maker order, 1 contract, priced at least 3 ticks behind
  the best bid, inside 0.30 to 0.70, on a permitted competition with an
  international price; window of 10 minutes; nothing else may be placed
  (pin setting on, death-drill switch off);
- at T: owner (or the session with a pre-signed file) publishes a valid
  marker;
- measured: T to latch (watchdog log), latch to the bot's first refused
  placement, latch to cancel sent, cancel to venue-confirmed terminal
  state, and zero open orders on the next three sweeps;
- pass: order confirmed cancelled at the venue within 90 s of T, no fill,
  no placement after the latch, latch still present after a restart of
  the sports bot;
- worst case if the order fills before the cancel: one contract, at most
  $0.70;
- afterwards the shared halt is latched. Nothing trades until the owner
  runs the reset procedure (`analysis/l5/HALT_RESET_MAINTENANCE_PROCEDURE.md`),
  which needs its own approval.

Still to do before the drill: an end-to-end test in the repository of the
sports bot's orders being cancelled through this path (the watchdog side
is tested; the sports bot's refusal on a latched halt is tested; the two
have not been run together).
