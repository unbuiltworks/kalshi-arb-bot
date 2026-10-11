"""Narrow production entrypoint for the owner-approved R5FT q=1 canary.

The continuous campaign's re-arm assertion was deliberately pinned to R5F.
When PMUS_SPRING_RUNG was advanced to the separately approved R5FT rung, that
old assertion correctly failed closed before any order.  This wrapper changes
only the expected rung for that boot assertion.  It does not weaken or bypass
any other boot/preflight check and refuses to run for any rung other than
R5FT.
"""
from __future__ import annotations

import os

from src import pmus_l5_continuous_session as campaign


def main() -> int:
    rung = os.getenv("PMUS_SPRING_RUNG", "").strip()
    if rung != "R5FT":
        raise SystemExit(f"R5FT_ENTRY_REFUSED:rung={rung!r}")
    # Preserve the exact-rung boot assertion; update its approved expected
    # value rather than disabling the re-arm gate or trusting arbitrary env.
    campaign.REARM_RUNG = "R5FT"
    return campaign.main()


if __name__ == "__main__":
    raise SystemExit(main())
