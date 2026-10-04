# MANUAL_FAVORITE_DCA — window identified and settled; fills blocked on credentials

**This lane is separate from the 28-window FAVORITE_DCA evaluation sample
and must never be pooled into it.** It is one manually traded window, not an
evaluation sample, and it is not out-of-sample evidence for A/Am/B/C/D0/D2/D3.

## What is established authoritatively right now

| | | source |
|---|---|---|
| window | **`cpc-btc-updown-15m-2026-10-04-1315z`** | *provisional* — see below |
| state | `MARKET_STATE_EXPIRED` | venue gateway |
| **settlementPx** | **`1.0000`** → the **long (UP)** side pays | venue gateway `/v1/markets/{slug}/bbo` |
| last trade / last sample | `0.9900` long, `0.01` short, `2026-10-04T13:30:16.642Z` | venue gateway |
| shares traded in the window | `96,637.75` | venue gateway |
| authenticated socket at expiry | bid `0.9900` / ask `0.9900`, 7,008 book updates | our own recorder, live |

So **UP settling as the winner is confirmed from the venue**, not from the
screenshot.

**The window identity is provisional.** It rests on the clock: the screenshot
reads 9:28 with fills 4–11 minutes old, and 9:28 EDT is 13:28 UTC, which puts
the fills at ~13:17–13:24 UTC inside the 1315z window — and 1315z is the
BTC15m window that settled UP. That is a strong match, but it is an
inference from a timestamp, and this lane's whole point is not to infer. The
authoritative fills will confirm or replace it.

## What is blocked, and what unblocks it

Account order/trade/settlement history cannot be read unsigned, and this
session has no PMUS credentials: `PMUS_API_KEY_ID` and
`PMUS_API_SECRET_KEY` are both unset. Every account route in this repository
(`/v1/account/balances`, `/v1/portfolio/positions`, `/v1/order/{id}`,
`/v1/orders/open`) is signed.

There is also **no proven history-listing route** on this venue: `GET
/v1/orders` returns 501, and only a single order by id has ever answered for
us. So the pull has two steps, and the harness does both:

1. **route discovery** — read-only GETs over 15 candidate history paths
   (`/v1/portfolio/history`, `/v1/portfolio/fills`, `/v1/orders/history`,
   `/v1/fills`, `/v1/activity`, …), recording which answer and with what
   keys, so the route we use is evidence rather than a guess;
2. **reconstruction** of the window from whatever route serves fills.

Until then, **no execution price, share count or fee is stated**. The
screenshot's `$0.10`/`$0.50` amounts are notionals shown by the app, and this
lane will not turn them into prices.

## The harness, ready to run

`scripts/pmus_manual_window_reconstruct.py` — GET only, no order path, no
cancel path, credentials only from env, key id never printed except as a
fingerprint.

It produces, per fill: timestamp, order id, side, **execution price**,
**shares**, **notional**, **fee** (venue-served where available, otherwise
labelled `MODELLED_pmus_fees` and never silently), and UP/DOWN shares, cash,
matched and residual **after each action**, plus cumulative cash out.

Then, for the window: settlement payout at the venue's own `settlementPx`,
total cash out, and **filled → settled net**. Starting and ending cash
attributable to the window come from `/v1/account/balances` read either side
of the sequence, reported as a separate cash-confirmation line rather than
assumed equal to the settlement arithmetic.

It also computes the four counterfactuals **on the same authoritative
fills**, re-pricing nothing:

1. the initial ~$0.50 UP + ~$0.50 DOWN only;
2. the ~$0.09–$0.10 extensions only, with no initial hedge;
3. the actual manual sequence;
4. the identical sequence had DOWN won (the settlement flipped, the fills
   untouched).

## Two things that need a decision

**1. Credentials.** There is no connector for this venue; a key stored in
the environment lets me call its API directly. Add `PMUS_API_KEY_ID` and
`PMUS_API_SECRET_KEY` in the cloud environment's settings (title-bar
environment menu → Edit), under API credentials if that section is offered,
otherwise as environment variables under exactly those names. A new session
picks them up. Please don't paste a key into the chat.

Note they are the **same credentials the executor uses**, so a session
holding them can in principle place orders. This script cannot — it has no
POST path — but the environment-level grant is not script-scoped. If a
read-only key can be minted for this, that is the better option.

**2. The contemporaneous tape is perishable.** Our recorder is live and
still holds 1315z in its log buffer (7,008 book updates), which is what
would answer *"was UP actually favored at every extension"* from
authenticated state rather than from the app's green "Up" label. Harvesting
it through this session's log reader is context-prohibitive — a single
5-second emission is thousands of tokens and the window is ~200 of them. The
options are: harvest a narrow slice around the fill timestamps once the
fills are known (cheap, targeted, and the right order of operations), or
persist the recorder's rows to its volume so this stops being a race. I have
not done either unprompted.

## Labels

Nothing here is realized P&L. When the fills arrive the labels will be
`filled → settled → cash-confirmed`, in that order, and the cash
confirmation will be a balance delta from the venue, not arithmetic.
