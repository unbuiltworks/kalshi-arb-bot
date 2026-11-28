# Feed-source audit — the collector is correct; L5 genuinely stopped

Deployed SHA **`35109a3add87d427ad3e3d6cac81557ee3b62f23`**, deployment
**`7fbf65d6`** SUCCESS, audit emitted **23:37:10Z**. Service still
**disarmed**. No mandate prepared — see §5.

## The four comparisons, from the service's own network access

| source | result |
|---|---|
| **legacy `/activity?type=TRADE`** | 5 rows, newest **1793296490 = 17:54:50Z** |
| **documented `/v2/activity`** | returns a **dict, not a list** → `not_a_list: "dict"` |
| `/v2/activity` (no type filter) | same: **dict, not a list** |
| **legacy `/activity`, NO type filter** | 5 rows, newest **1793296907 = 18:01:47Z** |
| gamma `/profiles?address=` | **HTTP 401** (auth required) |
| data `/profile?address=` | **HTTP 404** |

### Wallet attribution: confirmed correct, from the data itself

**Every returned row carries
`proxyWallet: 0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68`** — our exact
hard-coded address. The identity APIs were unreadable (401/404), but they
are not needed: the activity rows self-attribute to the wallet we poll.
The proxy-wallet hypothesis is **closed**, matching your profile
confirmation.

### The newest activity that exists, of any type

| | timestamp | UTC | type | slug |
|---|---|---|---|---|
| last **TRADE** | 1793296490 | **17:54:50Z** | TRADE (BUY) | `btc-updown-15m-1793295900` → the **17:45z** window |
| last **any type** | 1793296907 | **18:01:47Z** | **REDEEM** | `btc-updown-5m-1793296500` → the **17:55z** window |

Against `now = 23:38:57Z`: **5.74 h since the last trade, 5.62 h since any
activity at all.**

Recent trade detail, verbatim, with transaction hashes:

```
17:54:50Z  BUY  btc-updown-15m-1793295900  0x6d12ee64d3b516ff1322b1142a44c8192fdb8df8bd527c0ccac6855dc45d2978
17:54:41Z  BUY  btc-updown-15m-1793295900  0x808ab7e0ef159c0e8948fdb70e4a84b67ce9120eb5487478d2baa15ee2e669e8
17:54:40Z  BUY  btc-updown-15m-1793295900  0xe5f71913273a8b41d3bbc4bd2c704f0f56d0aebeb4735f0b96af7a7896d6855b
18:01:47Z  REDEEM btc-updown-5m-1793296500 0x2d646d6bd99db1c17ce6a7d12f0c2b37701f65e1fe23d9bb8f954a3823c71ea9
18:01:34Z  REDEEM btc-updown-15m-1793295900 0x86c7311860a065452be25bc3bdc55d529c5cf27260cee296086f5c5e110b2352
```

That is a coherent end-of-session shape: trades into the 17:45z window,
then the redemptions that settle them, then nothing.

## 1. Which authoritative source returns newer activity? None

**No source returns a TRADE after 17:54:50Z.** The only thing newer is
settlement (REDEEM, to 18:01:47Z), and it is newer by seven minutes, not by
five hours.

**So the feed is not stale — the wallet is idle.** The collector's frozen
`newest_row_event_utc` of 17:54:50Z is the *correct* answer to the question
it asks, and has been throughout. Combined with the earlier results, every
mechanical explanation is now excluded by direct evidence:

- caching — falsified by per-request cache-busting
- wallet/proxy attribution — falsified by `proxyWallet` on every row
- endpoint — the documented `/v2` returns no list at all
- dedup, pagination, timestamps, `type=TRADE` — excluded from code semantics

I was wrong earlier to treat the stall as presumptively ours. It is not.

## 2. Two incidental findings worth keeping

**(a) `type=TRADE` hides settlement.** The collector never sees REDEEM, so
it cannot observe its own positions settling through this feed. Not the
cause of anything here, and I am **not** changing it — the decision engine
is driven by fills, and widening the filter would change what reaches it.
Recorded as a known blind spot.

**(b) `/v2/activity` returns a dict, not a list.** `l5_activity` ends with
`return rows if isinstance(rows, list) else None`, so if the collector were
ever pointed at `/v2` it would silently report **every poll as a feed
failure**. Anyone migrating must unwrap the envelope first. Recorded before
it bites.

## 3. No collector correction made

You asked for the smallest read-only correction **if a verified source
provides fresh trades**. None does. Changing the collector now would be
exactly the speculative patch you ruled out: it is returning the truth.

## 4. Fresh input to the decision engine: not demonstrated, and cannot be

`ingest:admitted` remains at zero and `last_admitted_utc` null, because
there is nothing to admit. The admission path cannot be exercised by a feed
with no new rows. It will exercise itself the moment L5 trades again — the
pre-committed criteria are unchanged: cursor `keys` > 5,556,
`ingest:new` > 0, `ingest:admitted` present, `newest_row_event_utc`
> 17:54:50Z.

## 5. No mandate returned

Your instruction was to prepare the corrected hybrid mandate **once fresh
input is confirmed**. It is not confirmed — the input is absent because the
trader is idle, not because we cannot read it. Returning a mandate now
would hand you an approval for three windows that would again submit
nothing, which is the outcome this whole investigation existed to avoid.

The mandate design is settled and waiting: maker openings **plus**
authorized defensive completion, 3 windows, $20 exposure, $5 daily, $3
worst-case per window, ceiling **1.00**, defensive budget $2.00 — and it
requires the `EXECUTION_MODE` code change, since `execution_scope` is not a
field the gate reads and crossing cannot be granted by a document.

## 6. Current state

- deployed `35109a3a`, deployment `7fbf65d6` SUCCESS, service healthy
- preflight `ok: false` on three counts: `owner_arm_token`,
  `absolute_deadline` EXPIRED, `campaign_approval` stale
- `L5_DEFENSIVE_COMPLETION` off · 1.20 default not enabled · **1.00 ceiling
  stands** · no mandate installed · no execution permission changed
- no new service, no new infrastructure, no further cache experiments

## 7. What actually unblocks live evidence now

The blocker is no longer technical. It is that **L5 has not traded for 5.7
hours**, and our engine opens only on L5's actions.

Two options, both yours:

- **Wait for L5 to resume.** The collector will admit the first new action
  automatically; nothing more is needed from me. L5 historically trades
  ~33.5 windows/day, so idleness this long is unusual but it is what the
  data says.
- **Decouple openings from L5.** That is the autonomous opening path, which
  its own evidence currently refuses: autonomous ARGMAX is **−$3.1400** over
  38 windows and negative in every opening-price band.
