"""Unbuffered fail-closed bootstrap for the bounded crypto live campaign."""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    print(f"CRYPTO_LIVE_CAMPAIGN_BOOT_START ts={_stamp()} pid={os.getpid()} python={sys.version.split()[0]}", flush=True)
    try:
        from src import gemini_kalshi_crypto_live_campaign as campaign
        print(f"CRYPTO_LIVE_CAMPAIGN_BOOT_IMPORT_OK ts={_stamp()}", flush=True)
        campaign.main()
    except BaseException as exc:
        print(
            f"CRYPTO_LIVE_CAMPAIGN_BOOT_FATAL ts={_stamp()} type={type(exc).__name__} error={str(exc)[:500]}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
