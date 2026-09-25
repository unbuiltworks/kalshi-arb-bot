# RUN MARKER — OBSERVE-ONLY, maker-treatment shadow

`L5_OBSERVE_ONLY=YES`, live authorization cleared, nothing armed,
NullTransport in the executor. This run cannot send an order.

What it demonstrates over one complete BTC15m window: the actions the frozen
rule refuses `STRICT_NOT_EVALUABLE` now produce genuine resting-order
INTENTS — a side, L5's own price, a lambda-scaled quantity floored to the
venue increment, and the post-only flag — instead of producing nothing.

Probe 06 executed 6 of 52 actions and refused 43 on that reason. Every
execution was a TAKER and every refusal was a MAKER, so the clone currently
reproduces only L5's aggressive behaviour.

INTENTS ONLY. Nothing is placed, nothing rests, nothing fills. The report
says so in its own labels. `L5_NATIVE` 030d8bf is untouched: the treatment
reads its output and adds nothing to its decision.
