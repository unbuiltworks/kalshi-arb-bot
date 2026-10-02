# Status of the directional-vs-choppy reading: UNTESTABLE as things stand

## The BTC leg is absent for 9 of 11 windows

`WINDOW_CHARACTER_NEW.json` reports `{"status": "INSUFFICIENT"}` for the BTC block of
**every one of the nine new windows**. The reference feed was never harvested for
18:00–21:00 — that query exceeded the log server's message limit and still needs
narrower ranges — so realized volatility, displacement and range do not exist for
those windows.

Only **1730z and 1745z** have BTC descriptors, which are the two windows that
generated the reading in the first place. **The directional-vs-choppy hypothesis
therefore cannot be tested at all yet.** It is neither holding nor breaking; there is
no evidence either way.

### A zero that was not data

My first pass at tabulating this printed `BTCdisp 0.00` for all nine windows, because
the ad-hoc printer did `d.get("displacement_bps") or 0` and turned a missing value
into a zero. The saved JSON was correct — it says `INSUFFICIENT` — but the table I
derived from it would have shown "median |BTC displacement| = 0.00 bps" in both groups
and invited the conclusion that displacement does not separate them. That conclusion
would have been an artifact of my own defaulting, not a measurement.

This is the same error class as the earlier ones: an empty book read as a free pool,
a hit cap read as absent data, a trimmed set read as fresh rows. Missing must stay
missing.

## What the PMUS-only descriptors do show

These are available for all 11 windows, and are PMUS-internal — they are not the BTC
test and must not be reported as if they were.

| | ΔNET positive (n=6) | ΔNET negative (n=5) |
|---|---|---|
| median PMUS total absolute path | **4.73** | **7.95** |
| median peak concurrent open lots | 76 | 69 |
| median forced-exit share | **0.389** | **0.176** |
| median flow same-direction share | 0.740 | 0.735 |

Two of these separate and two do not.

**Path separates in the same direction as the original reading.** Windows where
cancellation helped had roughly 40% less PMUS price path than those where it hurt
(4.73 vs 7.95). That is the PMUS-side analogue of "choppier windows punish
cancellation", and it survives on nine fresh windows. It is suggestive and it is not
the BTC claim.

**Forced-exit share separates, but it is close to a tautology.** Cancellation helped
where 38.9% of lots were force-exited and hurt where only 17.6% were. Forced exits are
where the baseline's losses live, so this largely restates the pattern already visible
in the table: the signal helps windows that were losing badly and hurts windows that
were near flat. It is not independent evidence of a regime.

**Concurrency and flow persistence do not separate** — 76 vs 69 peak lots, 0.740 vs
0.735 same-direction share. Whatever distinguishes these windows, it is not how much
inventory piled up or how persistent the taker direction was on average.

## What this means for the next round

1. **Harvest the BTC reference in narrow ranges** so the external descriptors exist for
   the nine windows already collected. Until then criteria 6 and 7 are also pending and
   those windows stay provisional.
2. The PMUS-path relationship is the one coarse descriptor that has survived contact
   with new data. It remains **descriptive**: no filter, and nothing selected on it.
3. Nothing about the signal changes — threshold, horizon, ceiling, latency and feature
   definition are untouched.
