# Arming runbook for the approved one-window probe

**For ChatGPT.** Every step is an existing mechanism; nothing new is
introduced. I cannot perform any of it — the approval must come from an
object the executor cannot write, which is the whole security property.

## Why the three preflight checks fail, and what each needs

| check | why it fails | what clears it | who |
|---|---|---|---|
| `shadow_lock` | `L5_SHADOW_ONLY` is set — deliberately | owner flips it off at arming time | **owner** |
| `absolute_deadline` | `L5_LIVE_AUTH_NOT_AFTER` is **EXPIRED** | in **single-window** mode with a valid campaign this env is *optional*; clearing a stale value is a legitimate fix, not a widening. Either clear it or set a fresh one past the window | owner |
| `campaign_approval` | no document in `L5_CAMPAIGN_APPROVAL` | the nine-field document below | **owner** |

`owner_arm_token` already reads PASS.

## Step 1 — compute the window and the document (read-only)

```bash
python3 analysis/l5/issue208/run_probe_preflight.py window
```

Prints the next eligible slug with ≥10 minutes of lead, both env values,
and a one-line approval document. Use `--lead-min N` for more lead.

**Do not hand-compute the timestamp.** `window_starts` is an exact-match
allowlist: one wrong minute refuses *every* window with
`WINDOW_NOT_IN_AUTHORIZED_SET`.

The window must have ≥ ~8 minutes left when arming completes — entry
closes 480 s in, completion 600 s in.

## Step 2 — the owner sets three variables

```
L5_CAMPAIGN_APPROVAL   = <the one-line document from step 1>
L5_TS_EXPERIMENT_ID    = <the nonce from step 1>
L5_TS_MARKET           = <the slug from step 1>
```

plus **clear or refresh** `L5_LIVE_AUTH_NOT_AFTER`, and **turn off**
`L5_SHADOW_ONLY`.

**Leave unset:** `L5_TS_DEPTH_TICKS` (default is already 2),
`L5_PROVISIONAL_FORCED_SIDE` (proven unnecessary), both taker switches.

The document's nine fields:

```json
{"approved":true,
 "nonce":"probe-depth2-YYYYMMDD-HHMMz",
 "series":"cpc-btc-updown-15m-",
 "issued_utc":"<now, Z>",
 "expires_utc":"<window start + 30m, Z>",
 "max_windows":1,
 "max_total_exposure_usd":"1.00",
 "max_daily_loss_usd":"5.00",
 "window_starts":["<window start, Z>"]}
```

The ninth field — `window_starts` (or `last_open_utc`) — is **required**.
Without it the document parses as approved and then refuses every window
with `CAMPAIGN_OPENING_BOUND_ABSENT`.

The document can only *narrow*. One asking 40 windows / $50 / $500 is
clamped to 8 / $20 / $5. `series` must be exactly `cpc-btc-updown-15m-`.

## Step 3 — preflight, and expect PASS

```bash
L5_TS_EXPERIMENT_ID=<nonce> L5_TS_MARKET=<slug> \
python3 analysis/l5/issue208/run_probe_preflight.py preflight \
    --heartbeat hb.json --market <slug> \
    --bid <live bid> --ask <live ask> --depth 2
```

Expect `PASS  ok=True`, exit 0, all eighteen checks `ok`. Any `FAIL`, any
`?`, or exit 1 is **NO-GO**.

Then confirm on the live heartbeat: `authorization_preflight.ok: true`
with an empty `fail` list, and `campaign.permits_this_window: true`.

## Step 4 — what to watch during the window

| record | what it tells you |
|---|---|
| `L5_CONTROLLER_OPEN_TRIGGER` | the entry was decided; `provisional` is advisory |
| two submissions, 1 contract each | simultaneous UP/DOWN quoting |
| `venue_order_id` on each | **acknowledged**, not filled |
| `fill_count` rising | **filled** — the only state that creates inventory |
| `L5_BOOK_COMPLETION_TRIGGER` | a second leg was *approved* — absent means no approval, not a fault |
| `completion_latency.book_age_at_decision_s` | **the number production has never produced** |
| `detection_to_first_decision_s` | first fill → second-leg decision |

A completed pair leaves no unmatched inventory, so the completion
controller never evaluates. **The lone-leg outcome is the informative one.**

## Step 5 — after settlement

```bash
python3 analysis/l5/issue208/run_probe_preflight.py audit \
    --heartbeat hb-after.json --inventory <state file> --market <slug>
```

First line is the verdict: **MEASURED** (basis positively evidenced, the
figure may be quoted) / **UNINTERPRETABLE** (basis unknown, upper-bounded,
not built by our fills, or replaced by adoption — *the absence of a
result*) / **INCOMPLETE** (nothing filled).

## Automatic disarm — already in force, none of it new

shadow lock re-asserted · halt latch · daily realized halt −$5.00 ·
worst-case deficit > $1.00 · unmatched > 5 · cash envelope exhausted ·
campaign budget exhausted · entry cutoff 480 s · completion cutoff 600 s ·
deadline cancel by exact venue id · **unverifiable cancellation disarms
the engine rather than guessing** · independent watchdog on 120 s
staleness. Cancellation is never gated by these.

The campaign's own `max_windows: 1` plus the `window_starts` allowlist mean
a second window cannot be opened under this approval even if the process
tried.

## Maximum financial exposure

1 contract per leg × 2 legs at pair 0.95 → **$0.95 cash out**, worst
single-leg loss **−$0.60**, with the **$1.00** worst-case deficit ceiling
binding above that. Daily halt −$5.00. No limit is changed.
