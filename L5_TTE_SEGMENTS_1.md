# L5's BTC15m recycling, by first-fill TTE

**OBSERVED_RETROSPECTIVE: L5's own venue-reported Polymarket fills.** Not our
clone, not a backtest of it, and never our cost basis or realized P&L.

Source `data/l5/raw/activity.jsonl`; 527 BTC15m windows, **63,569 fills** after
dropping **3,380 duplicated** rows (the activity API paginates by offset and
the pages overlap) and **562** rows carrying no side. TTE is computed from the
event slug's start epoch — verified against the human-readable title — never
estimated.

## Where L5's fills actually happen

| | |
|---|---|
| fills | 63,562 |
| inside the clone's band (TTE ≤ 300) | 22,355 |
| **outside it (TTE > 300)** | **41,207** |
| **share outside** | **0.6483** |
| **median fill TTE** | **431.0s** |
| fills after expiry (excluded) | 7 |

**Nearly two thirds of L5's BTC15m fills occur in the interval our clone never
quotes in**, and the median fill sits at 431s — well above the 300s ceiling.

## By the TTE of each window's first fill

| bucket | windows | fills | fills/window | complement rate | par-constrained | matched/gross | median basis | median abs residual | median first-fill TTE |
|---|---|---|---|---|---|---|---|---|---|
| **>600** | **495** | **63,140** | **127.6** | **0.9838** | **0.7616** | **0.4573** | **0.9637** | 131.86 | **887.0** |
| 300–600 | 24 | 352 | 14.7 | 0.5833 | 0.5000 | 0.4480 | 0.9251 | 14.47 | 504.0 |
| 120–300 | 7 | 76 | 10.9 | 0.7143 | 0.5714 | 0.3857 | 0.9840 | 30.78 | 217.0 |
| 60–120 | 1 | 1 | 1.0 | 0.0 | 0.0 | 0.0000 | — | 5.00 | 81.0 |
| <60 | 0 | — | — | — | — | — | — | — | — |

No window's first fill landed after expiry, and no window produced a
degenerate basis.

## The answer to the question that was asked

> *Does L5's superior BTC15m recycling come predominantly from the period our
> clone currently refuses to trade?*

**Yes, and not marginally.**

- **495 of 527 windows (93.9%)** take their first fill at **TTE > 600** —
  inside the first five minutes of a fifteen-minute contract, which the clone
  never enters. Median first fill in that bucket: **887s**, i.e. thirteen
  seconds after the contract opens.
- That bucket carries **63,140 of 63,569 fills — 99.3% of everything L5 did**
  — at **127.6 fills per window**, against 10.9–14.7 in the buckets that start
  later. An order of magnitude.
- It is also where the recycling is: **complement rate 0.9838**,
  **par-constrained 0.7616**, **matched/gross 0.4573**, **median pair basis
  0.9637**.

The windows L5 *starts* late are not merely fewer; they are thinner on every
axis. The 120–300 bucket — the only one whose first fill the clone could even
have been present for — has 7 windows, 10.9 fills each and matched/gross
0.3857.

## What this does not say

It does not say the clone would achieve those numbers by quoting earlier. L5
is a different venue, a different size, a different queue position and a
counterparty set we do not have. What it says is narrower and sufficient:
**the behaviour we are trying to reproduce lives almost entirely in an
interval our clone is configured not to enter**, and that configuration came
from the length of a Gemini BTC5m contract rather than from anything measured
about L5 — see `TTE_PROVENANCE_1.md`.

L5 also carries a **median absolute residual of 131.9 contracts** in the >600
bucket. Whatever else is true, he is not running a flat book.
