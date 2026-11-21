# OPEN FINDING — `event_slug_for()` conflicts with a documented venue rule

Status: **recorded, not yet actioned.** Do not treat as done.

## The rule

Polymarket US, Trader Guide → Crypto Schema
(https://docs.polymarket.us/trader-guide/crypto-schema), states twice:

> **Do not parse the instrument symbol or slug** — symbol formats are not
> part of the public contract and may change. Identify markets using the
> fields below.

and under Best Practices:

> **Never parse the symbol or slug** to determine the market type, window,
> or strike. Symbol formats are not part of the public contract.

## What we do

`src/l5_pmus_transport.py::event_slug_for()` derives the event slug from the
market slug by stripping the `cpc-` product prefix:

    cpc-btc-updown-15m-2026-09-24-1245z  ->  btc-updown-15m-2026-09-24-1245z

That is slug parsing, and it is the pattern the venue explicitly says is not
part of its public contract.

Introduced in 313cdc9, deployed in a52255b.

## Why it has not broken anything yet

It is currently inert: the public gateway 404s this series under both the
market and the event resource, so the derived slug is never the difference
between a pass and a fail. It must not become load-bearing.

## What replaces it

The authoritative path is the Polymarket Exchange Reference Data API
(`POST /v1/refdata/instruments`, `/v1/refdata/metadata`, scope
`read:instruments`), where markets are identified by typed metadata fields --
`event_category`, `event_series`, `crypto_market_type`, `interval_start` /
`interval_end`, `clearing_sym` -- rather than by slug shape. Blocked on
credentials; see the credential-acquisition findings.

Refs #167
