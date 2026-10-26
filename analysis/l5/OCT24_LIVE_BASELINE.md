# October 24 live campaign — canonical execution baseline

Campaign `20261024a`. Venue-reconciled realized total **+$0.658100**.
Source of truth: `analysis/l5/residual_exit/preserved/btc15m_20261024_*.ndjson`
(label `L5_LIVE_SETTLED` for P&L, `L5_LIVE_LIFECYCLE` for fills), asserted by
`tests/test_l5_window_accounting.py` (34 tests).

This file is a derived index. The tapes and the test are authoritative; do not
edit the numbers here without re-deriving them from the tapes.

## Settled P&L per window

| window | matched qty | pair cost | matched P&L | residual | residual P&L | settlement |
|--------|------------:|----------:|------------:|----------|-------------:|-----------:|
| 0100z  | 1.04 | 0.96 | +0.0416 | —        | 0.0000  | +0.0416 |
| 0115z  | 0    | —    |  0.0000 | 0.96 DOWN | -0.2304 | -0.2304 |
| 0130z  | 1.27 | 0.85 | +0.1905 | —        | 0.0000  | +0.1905 |
| 0145z  | 0    | —    |  0.0000 | 1.03 DOWN | -0.3605 | -0.3605 |
| 0200z  | 1.22 | 0.94 | +0.0732 | —        | 0.0000  | +0.0732 |
| 0215z  | 0    | —    |  0.0000 | 1.25 UP   | +0.8625 | +0.8625 |
| 0230z  | 1.16 | 0.93 | +0.0812 | —        | 0.0000  | +0.0812 |

4 completed pairs; matched **+$0.3865**; residual **+$0.2716**; total **+$0.658100**.
Completion rate **4 of 7 settled windows = 57%**. 0030z submitted one order,
cancelled zero-fill, contributed $0.0000.

## Venue fills (terminal `ORDER_STATE_FILLED`)

The book is quoted in UP terms. A DOWN leg's own cost is `1 - wire`. Summing
wire prices across sides double-counts the YES side.

| window | leg 1 (t, side, wire px) | leg 2 (t, side, wire px) | dt between fills | qty/leg | own-terms pair |
|--------|--------------------------|--------------------------|-----------------:|--------:|---------------:|
| 0100z | 01:04:20 DOWN 0.63 `D9H2K32011N2` | 01:05:17 UP 0.59 `D9H3K2MG51MV` | **56.8 s** | 1.04 | 0.59 + 0.37 = **0.96** |
| 0130z | 01:31:43 UP 0.29 `D9HFKVYG11M3` | 01:33:49 DOWN 0.44 `D9HGJW0G11MQ` | **125.5 s** | 1.27 | 0.29 + 0.56 = **0.85** |
| 0200z | 02:02:16 UP 0.35 `D9HXJ4RG11TB` | 02:02:37 DOWN 0.41 `D9HXTBZG11M3` | **21.1 s** | 1.22 | 0.35 + 0.59 = **0.94** |
| 0230z | 02:31:34 UP 0.42 `D9JB2AJG11MC` | 02:33:39 DOWN 0.49 `D9JC1D7G51N6` | **124.7 s** | 1.16 | 0.42 + 0.51 = **0.93** |

Time between first and complement fill: **21.1 - 125.5 s, mean 82.0 s** (n=4).
In 0100z the DOWN leg filled first; in the other three the UP leg filled first.

### Stranded single legs (no complement)

| window | order | side | wire px | qty | settlement |
|--------|-------|------|--------:|----:|-----------:|
| 0115z | `D9H8V5EG11KJ` | DOWN | 0.76 | 0.96 | -0.2304 |
| 0145z | `D9HPDYMG31TB` | DOWN | 0.65 | 1.03 | -0.3605 |
| 0215z | `D9J464VG11N2` | UP   | 0.31 | 1.25 | +0.8625 |

`commission = 0.0000` on all 11 terminal fills, consistent with maker fills
throughout. No taker fee was paid in this campaign.

## Sizing reference for the current experiment

Baseline fills were **1.04 - 1.27 contracts** per leg. The authorized envelope
allows up to 3 contracts per opening leg, and the live sizer is currently
emitting **1.77** (`sized_by: CASH_ENVELOPE`). Any comparison of fill
probability or execution quality at larger size is measured against the
1.04-1.27 band above, not against a modelled expectation.
