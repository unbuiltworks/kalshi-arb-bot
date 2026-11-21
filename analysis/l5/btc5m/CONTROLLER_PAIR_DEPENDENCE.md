# Where the BTC5m controller decides by pairs or balance (2026-11-20)

Read from `src/l5_btc5m_controller.py` at the working branch. "Live" =
the configuration of the 17:05Z window.

| Decision | Where | Driven by | Live? |
|---|---|---|---|
| Entry gate: a leg is worth `p_pair_natural x (1 - pair cost)`; adverse selection charged "on the lone branch only: a filled pair is locked" | `SEV.entry_ev` call in `decide` | PAIR. Contradicted by replay: pairs cost 1.04 on average, 58% of the loss | gate present; waived in both live windows |
| Pair ceiling: the two bids together must not exceed 0.99 | `straddle_pair_ceiling`, `PAIR_ABOVE_CEILING` | SYMMETRIC QUOTING. Bounds the quoted pair, not the pair actually built | yes |
| Light side placed first | `_order = sorted(SIDES, key=inv qty)` | BALANCE (which order gets a scarce slot) | yes |
| Imbalance repricing: heavy side bids lower, light side higher | `_repriced_rung`, `straddle_reprice_ticks_per_clip` | BALANCE | off (0) |
| Completion ranking, maker/taker completion, completion reserve | `_rank_inventory`, `_completion_cross`, `completion_reserve` | PAIR COMPLETION | off (`straddle_completion_pair_cost` unset, reserve NONE) |
| Completed-pair cost cap | `straddle_max_completed_pair_cost` | PAIR | off |
| One-sided control | `one_sided_verdict` | asks a lone leg to justify itself when the partner is closed: position-aware, but defined relative to a partner | off |
| Side cap, unmatched ceiling, window-loss ceiling, exposure room | `SIDE_CAP_REACHED`, `UNMATCHED_CEILING`, `WINDOW_LOSS_CEILING` | RISK LIMITS on the position. Not balance goals; kept | yes |
| Heavy-underdog stop | `straddle_stop_adding_heavy_underdog` | POSITION and market direction | off |
| Position basis for the entry gate | `straddle_ev_basis = "POSITION"`, `position_order_economics` | WHOLE POSITION: each leg on its own measured worth, no pair term | off |

Settled P&L is linear in shares, so the expected worth of one more share
is the same whether it opens, extends or completes. Pairing changes the
spread between the two outcomes, which the three risk ceilings already
bound. The only decision that used a pair to justify buying was the entry
gate; the position basis replaces it.
