fills_context.json - 17 fills (8 campaign A, NBA 22 Nov 2026; 9 campaign B, ATP 23 Nov 2026) from Railway sports-bot PMUS_TWO_SIDED log lines. Read-only extraction; values copied as logged.
fill_time/side/qty/px/pos_after_fill/phase/seen_late_s: FILL line (payload "t"). 22 Nov FILL lines carry no phase or seen_late_s (null); markout_seen_after_s (FILL_MARKOUT) is the nearest equivalent.
role, markouts (h.5/30/120/300 .markout, per contract), markout_book_before, markout_fair_before, unpaired_before: FILL_MARKOUT line with the same order id.
order_placed_time, stage, order_qty: ORDER line with the same order id (all 17 matched by id; order_id_suffix = last 5 chars only). rest_s = fill_time - order_placed_time (derived, not logged).
quote_at_order: last RN1_QUOTE for the slug at or before order_placed_time; fair/edge/why kept as logged, want = want[side]. 22 Nov quotes have no top-level fair or range (null); per-side fair sits inside edge.
quotes_before_fill: last up-to-6 RN1_QUOTE in the 90 s before the fill; quotes_after_fill: first up-to-6 in the 120 s from the fill. Compact fair = mid/lo/hi/settled/calm/range/span_s; compact edge = ok/why/fair/px/net_edge/role per side.
pulls_near: reactive_pull, jump_pause, VOL_PAUSE and intl_guard_pull lines for the slug within +/-30 s of the fill, all logged fields. No VOL_PAUSE lines exist on 22 Nov. pair_locked_near: PAIR_LOCKED within +/-5 s.
Not found / caveats: 22:10:23Z NO 0.23 fill has no 5s/30s markout and was seen ~89 s late, so its fill_time and rest_s are detection-based. RN1_QUOTE is logged on change, so some fills have fewer than 6 quotes (0 before the 22:10:23 fill, 1 before 22:26:39).
A few log entries held two concatenated messages; they were split on the PMUS_TWO_SIDED prefix. Lines containing L5_LIVE_HB were skipped unread.
reward_rows.json: fields copied from the latest REWARD_CHECK with ok=true (source_time 2026-11-23T22:57:01Z); rows_brief holds 50 of the 187 rows, as logged.
