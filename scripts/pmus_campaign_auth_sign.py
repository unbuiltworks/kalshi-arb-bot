"""Produce a signed campaign-authorization file. OPERATOR TOOL, run locally.

Deliberately NOT in any image: it needs the campaign signing secret, and
nothing deployed should be able to authorize its own trading. The bot only
verifies.

    PMUS_CAMPAIGN_AUTH_SECRET=... python3 -m scripts.pmus_campaign_auth_sign \\
        --campaign-id 2026-11-25-a --build e6e8b4e60 \\
        --start 2026-11-25T12:00:00Z --hours 12 \\
        --competitions atp,nba,nfl,nhl --max-markets 4 --order-qty 1 \\
        > CAMPAIGN.json

Then commit that file where PMUS_CAMPAIGN_AUTH_URL points. The bot reads
it within one poll. To stop a campaign early, remove the file (or publish
the emergency-halt marker, which is a different file and a different
secret and always wins).

It refuses values above the ceilings given with --ceiling-* (defaults are
the approved development ceilings), so a mistake is caught here and not
only by the bot. It prints the file to stdout and NEVER prints the secret.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

from src import pmus_campaign_auth as CA

SECRET_ENV = "PMUS_CAMPAIGN_AUTH_SECRET"


def build_doc(a: argparse.Namespace, now: dt.datetime) -> dict:
    start = CA._t(a.start)
    if start is None:
        raise ValueError("--start needs a time with a zone, e.g. "
                         "2026-11-25T12:00:00Z")
    end = start + dt.timedelta(hours=float(a.hours))
    return {"campaign_id": a.campaign_id, "service": a.service,
            "build": a.build, "not_before": start.isoformat(),
            "expires": end.isoformat(),
            "competitions": sorted({c.strip().lower() for c in
                                    a.competitions.split(",") if c.strip()}),
            "max_markets": int(a.max_markets), "order_qty": int(a.order_qty),
            "campaign_spend_usd": str(a.campaign_spend_usd),
            "campaign_unpaired_usd": str(a.campaign_unpaired_usd),
            "per_market_spend_usd": str(a.per_market_spend_usd),
            "per_market_unpaired_usd": str(a.per_market_unpaired_usd),
            "slate": a.slate, "revokes": a.revokes or "",
            "issued_utc": now.isoformat()}


def main(argv=None, now: dt.datetime = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--campaign-id", required=True)
    ap.add_argument("--service", default="pmus-spring-l5-probe")
    ap.add_argument("--build", required=True,
                    help="the running commit (at least 9 characters)")
    ap.add_argument("--start", required=True)
    ap.add_argument("--hours", required=True, type=float)
    ap.add_argument("--competitions", required=True)
    ap.add_argument("--max-markets", required=True, type=int)
    ap.add_argument("--order-qty", required=True, type=int)
    ap.add_argument("--campaign-spend-usd", default="16")
    ap.add_argument("--campaign-unpaired-usd", default="6")
    ap.add_argument("--per-market-spend-usd", default="8")
    ap.add_argument("--per-market-unpaired-usd", default="3")
    ap.add_argument("--slate", default="both", choices=CA.SLATES)
    ap.add_argument("--revokes", default="")
    ap.add_argument("--ceiling-hours", type=float, default=12.0)
    ap.add_argument("--ceiling-markets", type=int, default=4)
    ap.add_argument("--ceiling-qty", type=int, default=2)
    ap.add_argument("--ceiling-spend-usd", default="16")
    ap.add_argument("--ceiling-unpaired-usd", default="6")
    ap.add_argument("--ceiling-per-market-spend-usd", default="8")
    ap.add_argument("--ceiling-per-market-unpaired-usd", default="3")
    ap.add_argument("--ceiling-competitions",
                    default="atp,nba,nfl,nhl,sea,epl,lal")
    a = ap.parse_args(argv)
    secret = (os.getenv(SECRET_ENV) or "").strip()
    if not secret:
        print("%s is not set; refusing to emit an unsigned file"
              % SECRET_ENV, file=sys.stderr)
        return 2
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        doc = build_doc(a, now)
    except Exception as exc:                              # noqa: BLE001
        print("cannot build the file: %s" % exc, file=sys.stderr)
        return 2
    doc["hmac"] = CA.sign(doc, secret)
    ceilings = {
        "max_window_s": a.ceiling_hours * 3600,
        "max_markets": a.ceiling_markets, "order_qty": a.ceiling_qty,
        "campaign_spend_usd": a.ceiling_spend_usd,
        "campaign_unpaired_usd": a.ceiling_unpaired_usd,
        "per_market_spend_usd": a.ceiling_per_market_spend_usd,
        "per_market_unpaired_usd": a.ceiling_per_market_unpaired_usd,
        "competitions": [c.strip() for c in
                         a.ceiling_competitions.split(",") if c.strip()]}
    raw = json.dumps(doc, sort_keys=True, indent=1)
    # the bot's own check, at the campaign's start, against these ceilings
    v = CA.evaluate(raw, secret=secret, service=a.service,
                    build=a.build + "0" * (40 - len(a.build)),
                    ceilings=ceilings, now=CA._t(doc["not_before"]))
    if not v["permit"]:
        print("refusing to emit a file the bot would refuse: %s %s"
              % (v["reason"], {k: v[k] for k in ("field", "fields",
                                                 "outside") if k in v}),
              file=sys.stderr)
        return 3
    print(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
