"""Startup-order safety entrypoint for the strict 20 bp micro-live executor.

The basis module installs ``hot.collect_all`` lazily.  A live-executor wrapper added
before that installation would be silently replaced later.  This entrypoint freezes
the correct order: readiness -> basis -> executor, then adds two fail-closed guards:

* always clean the 20 bp entry-state cache after an exact probe; and
* pass the live payload validator the hard 50 ms ceiling after the runtime gate has
  already proven both actual skews are <= 50 ms. This also handles a legitimate 0 ms
  measured skew without Python truthiness turning it into a sentinel value.

No authorization is relaxed. The underlying executor still requires --live, the
ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES environment gate, and a single-use arm token.
"""
from __future__ import annotations

import argparse
import logging
import sys

from src import gemini_kalshi_micro_live_executor_20bp as executor
from src.gemini_kalshi_live_probe_guard import MAX_QUOTE_SKEW_MS

readiness = executor.readiness
basis = executor.basis
exact = executor.exact
logger = logging.getLogger("kalshi_arb.gemini_kalshi_micro_live_executor_20bp_safe")
_INSTALLED = False


def install_safe_executor() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Critical ordering: basis.install() must happen before executor wraps
    # hot.collect_all, otherwise basis can overwrite the execution-session wrapper.
    readiness.install()
    basis.install()
    executor.install_executor()

    # The pure strict gate has already required both actual entry/current skews to be
    # <= 50 ms. Pass the validator the hard ceiling rather than re-deriving max() with
    # truthiness, so an exact 0 ms skew remains valid and conservative.
    original_validate = executor.validate_exact_probe_payloads

    def validate_at_live_ceiling(*args, **kwargs):
        kwargs["quote_skew_ms"] = MAX_QUOTE_SKEW_MS
        return original_validate(*args, **kwargs)

    executor.validate_exact_probe_payloads = validate_at_live_ceiling

    # The read-only readiness probe normally owns this cache cleanup. Because the
    # executor replaces that probe, make the ownership explicit here.
    installed_probe = exact._probe

    async def probe_with_cleanup(*, feed, plan, active_episodes, delays_ms=(50, 100)):
        key = (plan.asset, plan.direction, int(plan.opened_ms))
        try:
            return await installed_probe(
                feed=feed,
                plan=plan,
                active_episodes=active_episodes,
                delays_ms=delays_ms,
            )
        finally:
            readiness._PLAN_ENTRY_20BP.pop(key, None)

    exact._probe = probe_with_cleanup
    _INSTALLED = True


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--live", action="store_true")
    live_args, remaining = parser.parse_known_args()
    executor._LIVE_REQUESTED = bool(live_args.live)
    sys.argv = [sys.argv[0], *remaining]

    install_safe_executor()
    logger.info(
        "CRYPTO_MICRO_LIVE_EXECUTOR_SAFE_ENABLED strict_delay_ms=100 "
        "min_double_boundary_bps=20 entry_and_current_max_skew_ms=50 "
        "quantity=1 min_profit=0.03 live_requested=%s env_gate=%s",
        executor._LIVE_REQUESTED,
        executor.live_probe_authorized(executor._LIVE_REQUESTED),
    )
    readiness.guarded.main()


if __name__ == "__main__":
    main()
