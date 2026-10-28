# Authoritative reconciliation and loss headroom — 2026-10-28 11:13Z

Read from the running `ea67b2c5` process. Figures sourced from the venue
are marked; figures from durable local accounting are marked separately,
because the difference is the whole point of this document.

## Venue, authoritative

| fact | value | source |
|---|---|---|
| available cash | **$341.79875** | `/v1/account/balances`, `age_s: 0.0` |
| open orders | **0** | `live_orders` |
| unverified cancellations / still-fillable | **0** | watchdog sweep 11:13Z |
| unresolved accounting records | **0** | `unresolved_accounting.count`, `blocks_live_authorization: false` |
| committed cash | **$0** | `committed_usd: {}` |
| halt latch | **CLEAR**, `halt_why: null`, `would_halt: false` | halt gate |

## Loss headroom, authoritative

The engine states which figure each ceiling reads: *"the DAILY ceiling reads
`utc_day_realized_usd`; the separate cumulative ceilings read
`all_time_realized_usd`. None is UNKNOWN and refuses at the gate — it is
never read as zero."* Both are present and finite.

| ceiling | reads | value | cap | headroom |
|---|---|---|---|---|
| daily realized loss | `utc_day_realized_usd` | **+$0.7083** (a profit) | $5 | **$5.7083** (engine's own `room_usd`) |
| cumulative | `all_time_realized_usd` | **−$11.0086** | $20 | **$8.9914** |
| campaign additional loss | nonce-keyed baseline | fresh epoch | $5 | **$5.00** |

**Binding headroom: $5.00**, the campaign additional-loss bound — which is
exactly the requested $5 daily cap, so the request fits with nothing to
spare on that one bound and $8.99 behind it on the cumulative.

## What is NOT reconciled, and why it does not block

You were right that the watchdog sweep is not a complete reconciliation.
Precisely what is missing:

* **8 post-baseline settlements not attested**, every one
  `provenance: UNKNOWN_COST_UPPER_BOUND`, totalling **−$5.61**:

  | market | pnl |
  |---|---|
  | `cpc-btc-updown-15m-2026-10-16-0330z` | −0.16 |
  | `cpc-btc-updown-15m-2026-10-16-1230z` | −0.65 |
  | `cpc-btc-updown-1d-2026-10-15-2100z` | 0 |
  | `cpceth-eth-updown-15m-2026-10-16-0330z` | −1.00 |
  | `cpceth-eth-updown-15m-2026-10-16-1230z` | −0.90 |
  | `cpcsol-sol-updown-15m-2026-10-16-0330z` | −1.50 |
  | `cpcsol-sol-updown-15m-2026-10-16-1230z` | −0.40 |
  | `cpcxrp-xrp-updown-15m-2026-10-16-0330z` | −1.00 |

  All dated **2026-10-15/16** and spanning BTC, ETH, SOL and XRP — legacy,
  predating this engine's campaigns, and not all BTC15m;
* **`verified_l5_realized_net: null`**, `verified_l5_status:
  UNPROVEN_LEGACY_ATTRIBUTION`. The audited **L5-only** net is not
  established; the nearest figure is `verified_l5_realized: −$2.6138`;
* **`unknown_unattributed_realized: −$84.62`** and
  `pre_baseline_total_l5_share_unknown: −$79.01`, with ~30
  `unknown_basis_keys` all dated 2026-10-14 → 10-16.

**Why this does not block the gates.** The daily and cumulative ceilings
read `utc_day_realized_usd` and `all_time_realized_usd`; both are present,
so both ceilings *can* be evaluated, and `unresolved_accounting` is empty
with `blocks_live_authorization: false`. The unattested items sit in the
account-wide realized figure already — they make the *attribution* unknown,
not the *total*.

**What it does mean.** A strategy-level "confirmed settled net" cannot be
stated account-wide. Only **per-campaign** settlement can be — which is how
the +$0.7083 was reported and how the next campaign must be reported too.
Any claim about L5's lifetime contribution would be unfounded while
`verified_l5_status` reads `UNPROVEN_LEGACY_ATTRIBUTION`.

## The approval document

| | |
|---|---|
| **fingerprint (sha256)** | **`46d883c5779eac68e4989fc0fb98a66ada230578c69e2f164dc793b527f6b923`** |
| issued | 2026-10-28T11:16:51Z |
| expires | 2026-10-28T15:16:51Z (4 h, the code ceiling) |
| series | `cpc-btc-updown-15m-` |
| windows | 4 |
| max total exposure | $20 |
| max daily loss | $5 |
| submission budgets | OPEN 40 / COMPLETE 40 / REPLACE 64 / total 40 |
| gate verdict | **approved**, with **nothing clamped** |

The nonce exists only in `…/scratchpad/camp5/approval.json` (mode 700). It
is not in the repository, not in any log, and not in this document.

**Predicted runtime config: `PASS = True`, 26 of 26 rows, none failing** —
run through the deployed verifier against the deployed variable set plus
this document, using the verifier's own prediction mode so production was
not touched. Every budget row grades `effective 40/40/64/40` against
`required 40/40/64/40` *from the document*.

**Fresh campaign identity:**
`l5-btc15m-prog24-c1-3bf1daa5.camp.dfc6556ce3a0a2de52cbaf78` — 58
characters, inside the 62-character bound, with **0 submission claims, 0
OPEN/COMPLETE/REPLACE spend, and no completion-capacity refusal**. That is
the condition whose absence silenced window 4 last time.

### It is NOT installed, deliberately

Installing it would restart a healthy shadow-locked service for no trading
benefit and start the 4-hour clock before you have approved. So the
document is minted and verified by prediction, and installation is the
first step of the activation sequence rather than a step taken now.

## Eligible windows

The document is valid 11:16:51Z → 15:16:51Z. The campaign takes the **first
four BTC15m windows that open after the lock is lifted and pass preflight**,
and the nonce-keyed window ledger enforces exactly four regardless of what
else happens. `MIN_ENTRY_RUNWAY_S = 300` means a window joined with under
five minutes of entry runway is skipped rather than half-traded.

If approved at, say, 11:25Z the eligible windows are **11:30z, 11:45z,
12:00z, 12:15z**. Approved later, they shift accordingly; I will name the
exact four in the arming report before the lock is lifted.

## Activation sequence, on your explicit approval

1. install the document into `L5_CAMPAIGN_APPROVAL`;
2. verify the in-process fingerprint equals `46d883c5…f6b923`;
3. verify `L5_EFFECTIVE_RUNTIME_CONFIG` reads `PASS: true` on the running
   process;
4. **read** the submission-claim ledger under the new `experiment_id` on the
   production volume and show it empty;
5. verify the window ledger reads 0 of 4 for the new nonce;
6. re-verify `live_orders: 0`, `unresolved_accounting.count: 0`,
   `halt_latch: CLEAR` immediately before arming;
7. lift `L5_SHADOW_ONLY` **last**, and name the four eligible windows.

Any failure keeps the lock engaged and is reported as the specific blocker.

## Shadow validation against live market data — window 1115z on `ea67b2c5`

| observation | value |
|---|---|
| market | `cpc-btc-updown-15m-2026-10-28-1115z` |
| book source | `authenticated_ws`, `stream_age_s` **0.079 – 0.095** |
| planner output | `two_sided.pair_cost: "0.9500"` — inside the 0.96 ceiling, on real books |
| every decision | `TWO_SIDED_PROBE:PAIR_UP:CAMPAIGN_IDENTITY_UNAVAILABLE` |
| `armed` | **false** on every decision |
| orders submitted | **0** |
| `two_sided_round` | **null** — the round lifecycle is not reached |
| old deadlock signature | **0 occurrences** |
| decision latency | `latency_s` 7.85 – 24.7 s |

What this establishes: the corrected SHA is running, consuming
authenticated books under 0.1 s stale, and its planner is producing
fundable pairs at 0.9500 on live data — while refusing every one of them at
`CAMPAIGN_IDENTITY_UNAVAILABLE`, which is the correct refusal with no
approval in force, and arming nothing.

What it cannot establish, as set out above: the round-retirement transition.
The campaign-identity gate refuses upstream of the round lifecycle, and
even past it shadow mode fills nothing, so no round can hold the matched
pair the correction acts on. `two_sided_round: null` on every tick is the
expected reading, not a silent failure.

**One thing worth noting for the campaign:** `latency_s` runs 7.85 – 24.7 s
between decision and send. That is the same decision-to-send latency that
makes post-only rejection a real and unmodelled risk, and it is the most
likely explanation for a quote that looks executable in telemetry but never
rests. It should be recorded per order next to the fill outcome.
