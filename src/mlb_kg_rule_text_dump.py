"""Read-only operator audit dump for exact K/G MLB rule text.

Public rule evidence only. No credentials, orders, approvals, or live arming.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from src.mlb_kg_game_total_rule_fingerprint import gemini_rule_text, kalshi_rule_text
from src.mlb_kg_game_total_rule_fingerprint_probe_v2 import fetch_rule_pair


async def _run(args: argparse.Namespace) -> dict:
    k_market, g_contract, diagnostics = await fetch_rule_pair(
        kalshi_ticker=args.kalshi_ticker,
        gemini_symbol=args.gemini_symbol,
    )
    return {
        "kalshi_ticker": args.kalshi_ticker,
        "kalshi_rule_text": kalshi_rule_text(k_market),
        "gemini_symbol": args.gemini_symbol,
        "gemini_rule_text": gemini_rule_text(g_contract),
        "gemini_terms_url": str(g_contract.get("termsAndConditionsUrl") or ""),
        "diagnostics": diagnostics,
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--kalshi-ticker", required=True)
    p.add_argument("--gemini-symbol", required=True)
    args = p.parse_args()
    print(json.dumps(asyncio.run(_run(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
