# Proposal — agent-managed Railway configuration and arming, within explicit limits

**Status: proposal only. Nothing here is built.** You asked for this after
the campaign; it is written now so the campaign's evidence is still fresh.

## What this has to replace

Today arming requires two owner-held objects: the campaign approval
(`EnvApprovalStore.put` refuses by design, so I cannot write one) and the Q1
handshake. You have delegated the second to me, and the honest consequence
is already on the record: **"armed" no longer proves a human acted at that
moment.**

A durable mechanism should not pretend to restore that. It should replace it
with something that bounds me *in advance* and makes what I did *legible
afterwards*. Those are different properties from human-presence, and they
are achievable without a new service, a signing system, or a second
authorization architecture.

## The shape: a standing policy, not a standing permission

One owner-set document, `L5_AGENT_POLICY`, in the same pattern as
`L5_CAMPAIGN_APPROVAL` — JSON in an env var, graded against **code
constants** the agent cannot raise:

```json
{
  "approved": true,
  "nonce": "policy-<owner-generated>",
  "issued_utc": "...",
  "expires_utc": "...",
  "series": "cpc-btc-updown-15m-",
  "execution_mode": "MAKER_ONLY",
  "defensive_crossing": false,
  "max_windows_per_utc_day": 12,
  "max_campaigns_per_utc_day": 4,
  "max_windows_per_campaign": 3,
  "max_total_exposure_usd": "20",
  "max_daily_realized_loss_usd": "5",
  "max_worst_case_deficit_usd": "3.00",
  "agent_may_arm": true,
  "agent_may_set": ["L5_CAMPAIGN_APPROVAL", "L5_LIVE_AUTH_*",
                    "L5_CONTROLLER_OPEN", "PMUS_Q1_*", "L5_LIVE_HALT"]
}
```

It **expires**, so it must be renewed deliberately rather than drifting into
permanence. Everything in it is intersected with the existing `CA.MAX_*`
code constants, so it can only ever tighten them — a policy asking for $50
gets $20.

## What makes it safe without a human at the moment of arming

**1. The ceilings stay in code.** `CA.MAX_TOTAL_EXPOSURE_USD`,
`CA.MAX_DAILY_LOSS_USD`, `CA.MAX_WINDOWS`, `CA.ALLOWED_SERIES` and
`AUTH.EXECUTION_MODE` are constants. I cannot widen them from a variable;
widening them needs a code change, which trips the frozen-file guards, the
runtime config verifier and your review. That is the backstop, and it
already exists.

**2. A durable per-day ledger, in the pattern already used.**
`WindowLedger` and `CampaignBudgetLedger` already count claims from lock
files on the volume and fail closed when they cannot read. A
`DailyAgentLedger` would count **windows and campaigns opened per UTC day**
against the policy, so my total activity is bounded even across many
campaigns — the gap a per-campaign approval cannot close.

**3. A kill switch I cannot reach.** `L5_HALT_URL` and the independent
`l5-halt-watchdog` service already provide this, and `authorize_arming`
already refuses `HALT_GATE_ABSENT` when no independent halt is configured.
The policy should additionally name a variable I am **not** on the
`agent_may_set` list for — so you retain one lever I cannot touch even
while holding the rest. (Today I could set `L5_LIVE_HALT` myself, which is
convenient for standing down and wrong as a safety boundary.)

**4. Provenance in the record.** A distinct `L5_AGENT_ARMED` event carrying
the policy's digest, the day's counts and the reason, so the tape
distinguishes an agent-armed campaign from an owner-armed one. This is what
replaces the lost assurance: not proof a human acted, but an unambiguous,
auditable statement that one did not.

**5. A daily report to you**, unprompted: windows used, orders, fills,
settled P&L, refusals by reason, and the policy's remaining headroom.

## What I would still bring to you

- Raising any ceiling, or the policy itself.
- Anything beyond `MAKER_ONLY`, or enabling defensive crossing.
- A new market series.
- Resuming after a daily-loss halt, rather than treating the next UTC day as
  automatic re-entry.
- Any change to the authorization or money path — including the two defects
  in this campaign, which I brought to you rather than fixing on my own.

## Scope of work, honestly

Roughly: one new graded document type (mirroring `l5_campaign_approval`), one
durable daily ledger (mirroring `WindowLedger`), the policy check inside
`authorize_arming` beside the campaign check, the new audit event, and the
report. No new service, no signing system, no change to the entry strategy,
pairing logic or risk limits.

**I would not build it until the current experiment has produced at least
one real fill and settlement.** Automating the arming of a path that has
never completed a trade would be automating an unknown. The defects this
campaign surfaced are the argument for that ordering: both were invisible
until a real order hit the venue.
