# RUN MARKER — PROBE 08, the first probe that declines to deepen a position

Session `l5probe-20260926-btc15m-08`, market
`cpc-btc-updown-15m-2026-09-26-0500z`, account pinned `d51b44a3fa83`.
`L5_MAKER_TREATMENT`, `L5_MAKER_ONLY` and `L5_IMBALANCE_SUPPRESS` all on.

One treatment, and it is a refusal: while the market's REALIZED residual is
non-zero, a maker order that would increase |residual| is declined
`IMBALANCE_INCREASE_SUPPRESSED`. An opening leg from flat is permitted. The
complementary side is permitted, at any size — no threshold was authorized
and none is implemented.

No limit changed. $5 per market, $5 total, residual 10, one market,
`max_live_orders` 8, worst case $5.1738. A gate that can only refuse cannot
raise exposure.

Fresh state and journal paths, so Probe 07's uncertainty flag and its
residual cannot reach the classification the treatment reads.

Pre-arm: 35 targeted tests, full suite 26 failed / 14 errors / 9,117 passed,
failure set identical test-for-test to the stored baseline.

The endpoint is execution behaviour, not the settlement. The question is
whether REDUCE_RESIDUAL orders — the repairing side — actually fill on PMUS,
because the replay that motivated this treatment got those fills from L5's
record, where he was the one who earned them.
