# Polymarket CLOB execution path — survey verdict

**Verdict: `VENUE_EXECUTION_UNAVAILABLE`**

Not a bug and not an engineering gap. The venue whose behaviour `L5_ADAPTED_V1`
describes is not a venue this system holds an account on.

## The blocker, precisely

`L5_ADAPTED_V1` was derived from BTC15m windows on **Polymarket
International** (`gamma-api.polymarket.com` / `clob.polymarket.com`). Every
credential this system holds authenticates to **Polymarket US**
(`gateway.polymarket.us` / `api.polymarket.us`).

This was already established on 2026-09-09 and re-measured 2026-09-16 by
`src/l5_live_probe/venue_access.py`, which exists because a preflight once
reported ten of eleven checks PASS for a probe that could not place a single
order — market eligibility and credential presence were both true, about
DIFFERENT VENUES, and nothing compared them. Today's 404 on `fresh_bbo` for a
BTC15m slug is that same finding arrived at independently.

## Survey (as asked)

1. **Existing unused/superseded CLOB order adapter — NO.** Across ALL branches
   and full history, commits touching `ClobClient`, `py_clob_client`,
   `OrderArgs`, `MarketOrderArgs`, `signature_type`, `POLYMARKET_PRIVATE_KEY`,
   `CLOB_API_KEY`, `CLOB_API_SECRET`, `CLOB_API_PASSPHRASE`: **zero**.
   `clob.polymarket.com` appears only in READ helpers (`pm_book`, `pm_trades`,
   `pm_window`). No signer, no wallet, no order path.

2. **Any service with CLOB credential names — NO.** The only service holding
   venue credentials is `l5-r1-probe`: `PMUS_API_KEY_ID`,
   `PMUS_API_SECRET_KEY` — Polymarket US. `pmus-q1-executor` has no variables
   at all; `polymarket-wallet-shadow` has only `PYTHONUNBUFFERED`.

3. **Post-only on the CLOB — UNVERIFIED.** `participateDontInitiate` is a
   Polymarket US order field. Whether the international CLOB exposes an
   equivalent is not asserted here: it was not confirmed, and it is moot under
   the account blocker.

4. **Auth/signing for the CLOB — NOT APPLICABLE.** There is no account to
   authenticate.

5. **Order create/cancel/status endpoints — NOT ESTABLISHED.** The endpoints
   this system uses against that host are reads only.

6. **Size/tick — ESTABLISHED, from the live market object.** tick `0.01`,
   `order_min_size` 5. The frozen spec's 100 base and 33.121208 secondary both
   clear it.

7. **Fees — ESTABLISHED.** `fee_rate` 0.07, `fee_taker_only` true,
   `fee_rebate_rate` 0.2; `rewards_min_size` 50, `rewards_max_spread` 4.5.
   Taker-only fees corroborate the prospective finding that 227 of 238
   chain-collapsed parents were makers with fee 0.

8. **Eligibility — THE BLOCKER.** Polymarket US lists no BTC 15-minute
   up/down market: 0 `updown` slugs and 0 crypto markets ending within 24h
   across 30,000 open+active markets in 60 pages; all 54 PMUS BTC markets are
   long-dated targets expiring 2027-01-14. Re-confirmed today: 0 btc/updown in
   a 200-market listing, and `fresh_bbo` 404s on the BTC15m slug.
   Executing on the international venue would require an account this
   operation does not hold. That is an account and jurisdiction question, not
   an engineering one. **No workaround was attempted and none should be**: no
   proxy, no VPN, no routing around a geographic or account restriction.

## Consequence

- BTC15m stays the **fidelity/reference lane**. The frozen
  `FIDELITY_OBSERVABLE_V1` and `L5_ADAPTED_V1` remain exactly as frozen.
- The corrected surface shadow keeps running credential-free. Its first
  post-fix result — 102 distinct rungs, 102 restable, 0 trade flow, 0 fills —
  is early execution-habitat evidence and is NOT a reason to change the spec.
- Venue/category transfer becomes its own question, to be answered with
  evidence rather than by moving a BTC15m-derived surface onto sports markets
  because those happen to be the ones we can reach.
