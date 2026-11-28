# Runtime campaign authorization — design for review

23 Nov 2026. **Design only. Nothing is built, deployed or armed.** Owner:
"Eliminating redeployments for every campaign is essential." "No new
trading engine. No duplicate controller. No unnecessary infrastructure."

## The problem

Today a campaign is opened by changing settings on the service. That
restarts it (6 to 15 minutes on 23 Nov), blocks quoting until the first
audit, and makes a stop just as slow. Authorization, scope and size
should be things the running bot reads, not things it is rebuilt with.

## The idea in one paragraph

The running bot reads one signed, expiring campaign file from the private
repository, the same way the watchdog reads the halt marker. The file can
only choose values **inside** ceilings that stay in the deployed settings.
No file, a bad file, an expired file or a revoked file all mean the same
thing: no new exposure. The halt marker and the shared halt stay above it
and are untouched by it.

## Three layers, in order of precedence

| Layer | Where it lives | Who can change it | What it can do |
|---|---|---|---|
| 1. Emergency halt | Watchdog's disk, set by the signed halt marker or the watchdog's own rules | Asserted by the operator's marker or the watchdog; cleared only by the owner's reset procedure | Stops everything. A campaign file can never clear or override it |
| 2. Hard ceilings | Deployed settings of the bot and the ledger | A deployment with the owner's explicit approval | Bounds everything below: account limit, loss stop, daily halt, maximum order size, maximum markets, maximum spend, maximum window length, the set of competitions that may ever be named |
| 3. Campaign authorization | A signed file in the private repository | The operator, by committing a file | Opens a window and chooses values inside layer 2 |

The submission lock stays as it is and stays above layer 3: with the lock
at `1` no file can open anything. A deployment that sets the lock to a new
value `runtime` is what hands the decision to the file. Rolling back is
setting it to `1` again.

## The campaign file

| Field | Meaning | Bound by the hard ceiling |
|---|---|---|
| `campaign_id` | Unique name; also the nonce | used once |
| `service` | Which bot this is for | must match |
| `build` | The exact commit it authorizes | must equal the running commit |
| `not_before`, `expires` | UTC window | length at most the deployed maximum |
| `competitions` | Strict scope for this campaign | subset of the deployed permitted set |
| `max_markets` | Concurrent markets | at most the deployed ceiling |
| `order_qty` | Contracts per order, pre-game and in-play | at most the deployed ceiling |
| `per_market_unpaired_usd`, `campaign_unpaired_usd`, `campaign_spend_usd` | Inventory allocation | each at most its deployed ceiling |
| `slate` | Pre-game, in-play or both | — |
| `revokes` | Optional: a campaign id this file cancels | — |
| `issued_utc`, `hmac` | Time of signing; signature over every field above | — |

Not in the file, by design: the account limit, the loss stop, the daily
halt, the pair cap, maker-only, the depth rule, the international price
requirement, the stale-audit limit, anything about the halt. A field the
bot does not know makes the whole file invalid (so a typo cannot be
silently ignored), and so does any value above its ceiling (the bot does
not clamp: an out-of-bounds file is refused, named in the log).

## Rules

1. **Fail closed.** Missing, unreadable, unsigned, wrongly signed, wrong
   service, wrong build, expired, not yet started, revoked, unknown field,
   out of bounds: no new exposure. Each has its own reason in the log and
   the heartbeat.
2. **Unreadable is not the same as absent, and neither permits.** If the
   file cannot be fetched, the last valid file is honoured for a short
   grace (proposed 120 seconds) and then new exposure stops. A stale
   "yes" never outlives its fetch by more than that.
3. **Expiry is hard.** At `expires` the bot does what the window lock does
   today: no new exposure, own orders cancelled. Not extendable by the
   bot.
4. **Revocation.** Committing a file that names a campaign in `revokes`,
   or replacing the file with nothing, ends it within one poll.
5. **Cancels, completions and reconciliation are never blocked by this
   layer.** Whether completions continue after expiry stays a deployed
   setting (today: they stop), not a file field.
6. **Halt precedence.** The halt check runs before the authorization
   check on every order, as now. A latched halt with a valid campaign file
   means no orders. A new campaign file does not reset anything.
7. **One campaign at a time.** A second valid file while one is active is
   refused unless it revokes the first.
8. **A campaign id is used once.** Used ids are stored in the bot's book,
   so a restart or an old file cannot re-open a finished campaign.
9. **Changes inside a campaign** (scope, market count, size) are a new
   file that revokes the old one, same build, same or earlier expiry.
10. **The campaign marker for accounting** (spend, loss stop, P&L) is the
    campaign id and its start, as the window start is today.

## Signing and reading

- Same scheme as the halt marker: HMAC over a fixed list of fields,
  constant-time comparison, secret set on the service by the operator and
  never in the repository, chat or logs.
- **A separate secret from the halt marker's.** The halt secret can only
  stop trading; this one can start it. They should not be the same key,
  and the watchdog should not hold this one.
- Fetch: the bot polls the file through the repository's authenticated
  API with a read-only token (the same kind of token as the watchdog's),
  on its own thread like the account-risk reader, never on the trading
  loop. Proposed poll: 20 seconds.
- Signing tool: a local script, like `scripts/l5_emergency_halt_sign.py`,
  that refuses to sign values above the ceilings it is given.

## What this deliberately does not do

- Raise any ceiling, the account limit or a loss limit. Those stay
  deployments with explicit approval.
- Reset a halt, a safety latch or a daily halt.
- Add a service, an endpoint or a second controller. It is one reader
  thread and one gate in the existing engine, next to the lock check.
- Remove the owner's approval. The operator's signature on a file is the
  approval, recorded in the repository history with the file.

## What it changes for a 12-hour rolling campaign

- One file per 12 hours, committed by the operator; it expires by itself.
- "Rolling" then means committing the next file, not redeploying. Whether
  a standing instruction may let a session commit the next file is a
  separate decision for the owner; the design does not assume it.
- Independent shutdown: expiry in the bot; halt marker and watchdog
  outside it; none needs a session.

## Risks and how they are covered

| Risk | Cover |
|---|---|
| The signing secret leaks | Damage is bounded by layer 2: the holder can open a campaign only inside the deployed ceilings, on the running build, for the maximum window. The halt marker still stops it. Rotate by redeploying the secret. |
| An old file is replayed | Used ids are refused; `expires` is in the past; `build` must match. |
| The repository is unreachable | No new exposure after the grace; existing protections unchanged. |
| A file with a mistake | Unknown field or out-of-bounds value refuses the whole file; the signing tool checks first. |
| The bot's clock is wrong | Same exposure as the window lock today; `issued_utc` far in the future is refused. |
| Someone with repository write access but no secret | Can delete or corrupt the file, which only stops trading. |

## Build plan (after review; each deployable step needs approval)

1. Pure module: parse, verify, bound-check, decide. Exhaustive tests of
   every refusal, of precedence under a latched halt, of expiry and
   revocation, of restart.
2. Reader thread and the gate in the engine; `runtime` lock value;
   heartbeat block showing the campaign in force and why not, if not.
3. Signing tool with ceiling checks.
4. Locked deployment with the lock still at `1`: the reader runs and
   reports what it would decide, permitting nothing (a non-trading
   preflight, as for the account limit).
5. First runtime-authorized campaign: a short one, then the 12-hour one.

## Questions for the owner

1. Is a separate signing secret for campaign files, held by the operator,
   acceptable (two secrets in total)?
2. Hard ceilings to deploy for the first runtime campaigns. Proposed, all
   equal to today's approved values: 4 markets, 2 contracts, $16 spend,
   $6 unpaired, $8 / $3 per market, 12-hour maximum window, competitions
   ATP, NBA, NFL, NHL, Serie A, Premier League, La Liga.
3. Should completions continue after expiry for a bounded time, or stop
   as today? (Completion-only mode is still unverified and stays off
   until it is.)

---

## Built — 23 Nov 2026, 23:15 UTC (development only; not deployed)

Owner's decisions applied: a separate signing secret for campaign files;
the proposed ceilings; all placements stop at expiry; **no grace**; and
restart-safe recovery. Where the text above differs, this section is what
exists.

| | |
|---|---|
| Code | `f94a0513e` on `dev/pmus-rn1-replica` |
| Release candidate | `release/pmus-sports-rn1-5b1571a0b` (`5b1571a0b28ee4fbcaa77b98634a5cfd1eb20dc1`), **not approved, not deployed** |
| Tests | full suite 5,468 passed, 24 failed (the same 24 as the running build); 117 new tests for this feature |
| Inactive unless | the lock value is `runtime`, or `PMUS_CAMPAIGN_AUTH_REPORT=1` (report only) |

**Differences from the design above**
- **No grace.** A refresh that fails locks at once and the bot cancels its
  own orders. An outage does not end the campaign: when the same file can
  be read again it continues with its original window. It can never extend
  it.
- **Not permitted always means fully locked**, completions included
  (completion-only mode is still unverified and is not used anywhere).
- **Restart-safe.** The campaign in force is stored in the bot's book by
  name and by the fingerprint of its exact contents, with its start and
  expiry. The same file resumes; spend, inventory and losses carry on
  because they are counted from the original start. Different contents
  under the same name stop the campaign. A name that has ended (expired,
  revoked, halted, replaced) is never accepted again, across restarts.
- **Halt.** A latched emergency halt ends the campaign in force for good,
  and also any campaign published while the halt is latched. After the
  owner's reset, only a new campaign name can trade. An unreadable halt
  state blocks without ending the campaign.
- **A loop that stops evaluating stops permitting**: the lock opens only
  on a decision at most 15 seconds old.
- **Per-market spend** is also a file field (ceiling $8).
- **Report-only mode** for a non-trading preflight: the heartbeat shows
  what the file would decide while the lock stays at `1`.

**Settings (all set by the operator on the service; none in the repository)**
`PMUS_CAMPAIGN_AUTH_URL`, `PMUS_CAMPAIGN_AUTH_SECRET` (the campaign
signing secret, separate from the halt marker's),
`PMUS_CAMPAIGN_AUTH_BEARER` (read-only repository token),
`PMUS_CAMPAIGN_AUTH_ACCEPT`, `PMUS_CAMPAIGN_AUTH_SERVICE`. The hard
ceilings are the existing deployed settings; the 12-hour maximum needs
`PMUS_TWO_SIDED_WINDOW_MAX_S=43200`.

**Signing tool:** `scripts/pmus_campaign_auth_sign.py`, run locally by the
operator, not in the image; refuses values above the ceilings and never
prints the secret.

**Each safeguard was switched off in turn and tests failed each time:**
halt precedence, decision freshness, expiry of the active campaign, file
removal, the reader dropping its bytes on failure, ended names, ceilings,
changed contents, strict scope.

**Not covered by tests:** the real fetch from the repository host from
inside the service (first seen in a report-only deployment), and behaviour
under live quote traffic.

**Next steps, each needing approval:** deploy `5b1571a0b` locked with
report-only on and the operator's three values set; check the heartbeat
verdict against a test file; then the first runtime-authorized campaign.
