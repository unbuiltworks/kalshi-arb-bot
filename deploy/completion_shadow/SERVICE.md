# completion-shadow — 24/7 non-trading completion economics

Records what a proposed COMPLETE would cost on the live sports universe, so
the economic completion rule can be decided from evidence rather than from
the single accidental armed run of 2026-09-09 (pair cost 1.0527, -0.89).

**Physically non-trading.** The image copies no order transport, no auth
module and no credentials; the process issues GET only against the public
gateway. `places_orders` is `false` on every heartbeat, and tests assert the
property against the module's AST rather than its prose.

Emits `PMUS_COMPLETION_SHADOW {json}` per proposed completion, carrying the
fields the owner asked for: complement price and side, marginal pair cost,
inventory-weighted pair cost before, resulting pair cost if filled, residual
before/after, the pricing regime, and whether `pair_cost_max` gates that
regime at all. A heartbeat row closes every cycle.

Restart policy ALWAYS: a shadow that dies quietly is worse than none.
