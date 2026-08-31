"""Sept 2 subsidy-stack verification harness (READ-ONLY).

The decisive Gemini measurement: what does the venue ACTUALLY credit per
dollar of two-sided maker turnover — maker rebates (30% of raw taker
formula, floored/cent, 20-80c fills), the Sept 2+ maker fee waiver, and
Liquidity Rewards — measured from the live account, never from formulas.
(Standing rule: nobody scales on modeled rebates.)

This script only READS. It uses the recovered auth helper
(src/gemini_prediction_auth.private_rest_request), which contains no order
transport. Run it on the machine that holds GEMINI_API_KEY/SECRET env vars
(the AWS host) — it fails closed with NO_CREDENTIALS elsewhere.

Because Gemini's docs don't pin exactly which endpoint carries prediction
rebate/LR credits, the harness probes every plausible read surface and
reports per-endpoint availability, so the first Wednesday run tells us
where the credits live even if some probes 404:

  /v1/balances                 account cash (before/after anchor)
  /v1/mytrades?symbol=...      our fills incl. fee fields, per instrument
  /v1/transfers                credits often post as transfer rows
  /v1/notionalvolume           fee-tier / volume view
  /v1/prediction-markets/...   speculative rewards/rebates paths (probed,
                               failures reported not fatal)

Output: analysis/gemini/subsidy_verify_<UTCdate>.json plus a stdout
summary: credited_subsidy_usd, maker_turnover_usd (from mytrades), and
credited cents per turnover dollar where computable.

    python3 -m src.gemini_maker.subsidy_verify [--symbols GEMI-...,...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from src.gemini_prediction_auth import has_credentials, private_rest_request

OUT_DIR = Path("analysis/gemini")

DEFAULT_SYMBOL_PREFIXES = ("GEMI-BTC", "GEMI-ETH", "GEMI-SOL", "GEMI-XRP")

PROBE_PATHS = (
    "/v1/balances",
    "/v1/transfers",
    "/v1/notionalvolume",
    # speculative — availability itself is the finding:
    "/v1/prediction-markets/rewards",
    "/v1/prediction-markets/rebates",
    "/v1/prediction-markets/liquidity-rewards",
)


async def probe(path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    try:
        resp = await private_rest_request("POST", path, timeout=10.0,
                                          **({"payload": payload} if payload else {}))
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:500]
        return {"status": resp.status_code, "body": body}
    except TypeError:
        # older helper signature without payload kwarg
        resp = await private_rest_request("POST", path, timeout=10.0)
        try:
            return {"status": resp.status_code, "body": resp.json()}
        except Exception:
            return {"status": resp.status_code, "body": resp.text[:500]}
    except Exception as exc:
        return {"status": None, "error": repr(exc)}


async def main_async(symbols: List[str]) -> int:
    if not has_credentials():
        print(json.dumps({"verdict": "NO_CREDENTIALS",
                          "note": "run on the host holding the Gemini env keys"}))
        return 2
    out: Dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "probes": {},
        "mytrades": {},
    }
    for path in PROBE_PATHS:
        out["probes"][path] = await probe(path)

    turnover = 0.0
    fees = 0.0
    fills = 0
    for sym in symbols:
        r = await probe("/v1/mytrades", {"symbol": sym, "limit_trades": 500})
        rows = r.get("body") if isinstance(r.get("body"), list) else []
        out["mytrades"][sym] = {"status": r.get("status"), "n": len(rows)}
        for t in rows:
            try:
                px = float(t.get("price", 0)); q = float(t.get("amount", 0))
            except (TypeError, ValueError):
                continue
            turnover += px * q
            fees += float(t.get("fee_amount", 0) or 0)
            fills += 1

    out["summary"] = {
        "fills_seen": fills,
        "maker_turnover_usd_approx": round(turnover, 2),
        "fees_charged_usd": round(fees, 2),
        "note": ("credited rebate/LR dollars must be identified from the "
                 "probe bodies above (transfers/rewards rows); compute "
                 "credited_cents_per_turnover_dollar = 100*credits/turnover "
                 "once the carrying endpoint is confirmed"),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = OUT_DIR / f"subsidy_verify_{stamp}.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({"wrote": str(path),
                      "probe_status": {k: v.get("status")
                                       for k, v in out["probes"].items()},
                      "summary": out["summary"]}, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="",
                    help="comma-separated instrument symbols; default probes "
                         "none (turnover section empty) — pass the session's "
                         "actual instruments")
    args = ap.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    return asyncio.run(main_async(symbols))


if __name__ == "__main__":
    raise SystemExit(main())
