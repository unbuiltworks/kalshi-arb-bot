# Raw tapes for the sports spread x depth census

2026-11-13. Public gateway (`gateway.polymarket.us`), GET only, no
credentials, no order path. Nothing here was produced by trading.

| file | rows | what it is |
|---|---:|---|
| `live_slate_tape_20261113.jsonl` | 634 | the live in-game sports slate polled on a ~95s cycle; `bbo` + `book` a market. The adverse-selection and pair-completion figures come from this. |
| `wide_screen_20261113.jsonl` | 1,140 | a random 900 of the 4,030 wide-spread markets, one `/book` call each; 195 of them got a second read, which is where the flow *rate* comes from. |
| `sports_enum_20261113.jsonl.gz` | 9,102 | every open+active sports market with its inline quote. The spread distribution and the event-clock result come from this. |
| `wide_stratified_sweep_20261113.log` | 194 | depth reads on a spread-stratified sample of wide markets, with the discovery and strata lines kept for audit. |

Reproduce:

```
python3 -I analysis/l5/sports_spread_depth/census_report.py \
    --tape analysis/l5/sports_spread_depth/data/live_slate_tape_20261113.jsonl
python3 -I analysis/l5/sports_spread_depth/wide_report.py \
    analysis/l5/sports_spread_depth/data/wide_stratified_sweep_20261113.log
zcat analysis/l5/sports_spread_depth/data/sports_enum_20261113.jsonl.gz > /tmp/e.jsonl
python3 -I analysis/l5/sports_spread_depth/spread_vs_event_clock.py /tmp/e.jsonl
```

`spread_vs_event_clock.py` measures days-to-game against "today", so
reproducing it against this tape needs the as-of date 2026-11-13 to get the
published figures; run from the stored enum rather than a fresh one if you
want the exact table.

A row with `readable: false` is a read that failed, usually `RATE_LIMITED`.
Those are counted and excluded by every analyzer here and are never treated
as a market with no depth or no volume -- the distinction between "we could
not see it" and "there was nothing there" is the one this programme has most
often got wrong.
