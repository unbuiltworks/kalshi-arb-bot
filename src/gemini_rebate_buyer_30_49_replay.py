"""Strict trade-through replay for the 30-49c rebate-buyer shadow.

A virtual quote must exist on one observation and a *later* public top-of-book
observation must move fully through it. This is conservative fill evidence, not
proof of real queue position. Each policy may fill at most once per ticker.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any

ONE = D("1")
QTY = D("10")
POLICIES = ("band_only", "fair_1c")
RESERVE = "10"


@dataclass
class Resting:
    side: str
    price: D
    maker_fee: D
    projected_rebate: D


def _rows(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("type") == "GEMINI_REBATE_BUYER_30_49_SHADOW":
            out.append(row)
    return out


def replay(path: Path) -> dict[str, Any]:
    resting: dict[tuple[str, str], Resting] = {}
    done: set[tuple[str, str]] = set()
    fills: list[dict[str, Any]] = []
    quote_updates = 0
    rows = _rows(path)
    for row in rows:
        ticker = str(row["ticker"])
        bid = D(str(row["yes_bid"])); ask = D(str(row["yes_ask"]))
        sensitivity = row["basis_reserve_sensitivity_bps"][RESERVE]
        p_up = D(str(sensitivity["probability_up"])); p_down = ONE - p_up
        for policy in POLICIES:
            key = (ticker, policy)
            prior = resting.get(key)
            if key not in done and prior is not None:
                crossed = (
                    prior.side == "YES" and ask < prior.price
                ) or (
                    prior.side == "NO" and bid > ONE - prior.price
                )
                if crossed:
                    fair_now = p_up if prior.side == "YES" else p_down
                    edge_now = (fair_now - prior.price) * QTY - prior.maker_fee
                    fills.append({
                        "ticker": ticker, "asset": row.get("asset"), "policy": policy,
                        "side": prior.side, "quote_price": str(prior.price),
                        "strict_confirmation_yes_bid": str(bid),
                        "strict_confirmation_yes_ask": str(ask),
                        "fair_probability_at_confirmation": str(fair_now),
                        "model_edge_at_strict_confirmation_before_rebate_usd": str(edge_now),
                        "projected_rebate_if_real_fill_usd_not_realized": str(prior.projected_rebate),
                        "evidence": "LATER_PUBLIC_TOP_OF_BOOK_MOVED_FULLY_THROUGH_PRIOR_RESTING_QUOTE",
                    })
                    done.add(key); resting.pop(key, None)
                    continue
            if key in done:
                continue
            d = sensitivity["policies"][policy]
            if d.get("actionable") and d.get("side") and d.get("quote_price") is not None:
                resting[key] = Resting(
                    side=str(d["side"]), price=D(str(d["quote_price"])),
                    maker_fee=D(str(d["maker_fee_usd"])),
                    projected_rebate=D(str(d["projected_rebate_usd_not_realized"])),
                )
                quote_updates += 1
            else:
                resting.pop(key, None)

    per_policy = {}
    for policy in POLICIES:
        pf = [f for f in fills if f["policy"] == policy]
        positive = [f for f in pf if D(f["model_edge_at_strict_confirmation_before_rebate_usd"]) >= 0]
        per_policy[policy] = {
            "strict_fill_count": len(pf),
            "strict_fill_model_positive_at_confirmation": len(positive),
            "fills": pf,
        }
    return {
        "type":"GEMINI_REBATE_BUYER_30_49_STRICT_REPLAY",
        "classification":"READ_ONLY_STRICT_TRADE_THROUGH_NOT_REALIZED",
        "source_rows":len(rows), "quote_updates":quote_updates,
        "basis_reserve_bps":RESERVE, "quantity":"10", "policies":per_policy,
        "order_entry":False, "credentials_loaded":False, "realized_pnl":None,
        "warning":"Strict trade-through is conservative fill evidence but does not prove real queue position. Rebates remain projected until credited by Gemini.",
    }


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("capture", type=Path); ap.add_argument("--output", type=Path)
    args = ap.parse_args(); result = replay(args.capture); text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
