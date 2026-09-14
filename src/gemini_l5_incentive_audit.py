"""Public Gemini Predictions incentive audit for the L5 transfer.

This is economics provenance, not a trading rule. It records the current public
maker-rebate schedule, liquidity-rewards configuration, and the documented
program-wide qualification constraints that matter for a q=1 L5 canary.
Estimated rewards are never booked as realized P&L.

No credentials and no order endpoints are used.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/l5/gemini_l5_incentive_audit.json"
BASE = "https://api.gemini.com"
RATE_PATH = "/v1/prediction-markets/maker-rebate/rates?category=Crypto"
LIQ_CFG_PATH = "/v1/prediction-markets/liquidity-rewards/config"
LIQ_EVENTS_PATH = "/v1/prediction-markets/liquidity-rewards/events?category=Crypto&limit=100"
LIQ_PROGRAM_DOC = "https://developer.gemini.com/prediction-markets/liquidity-rewards-program"
TAKER_RATE = D("0.07")
Q1 = D("1")
REPRESENTATIVE_PRICES = (D("0.20"), D("0.40"), D("0.50"), D("0.60"), D("0.80"))

# Gemini's current program-wide documented qualification rules. These are kept
# explicit because the public config endpoint only exposes max spread and payout
# threshold, not all eligibility conditions.
DOC_MIN_SIZE_CONTRACTS = 10
DOC_SIZE_CAP_CONTRACTS = 250
DOC_TWO_SIDED_MULTIPLIER = D("1.5")
DOC_UPTIME_THRESHOLD_PCT = D("50")
DOC_POST_ONLY_EXCLUDED = True


def _get(path: str) -> dict:
    req = Request(BASE + path, headers={"User-Agent": "l5-research/1"})
    with urlopen(req, timeout=10) as resp:  # nosec - fixed Gemini HTTPS host
        return json.loads(resp.read().decode("utf-8"))


def _active_multiplier_bps(rates: dict, now: datetime) -> int | None:
    rules = rates.get("rate_rules") or []
    active = []
    for r in rules:
        try:
            start = datetime.fromisoformat(str(r.get("effective_from")).replace("Z", "+00:00"))
            end_s = r.get("effective_to")
            end = (datetime.fromisoformat(str(end_s).replace("Z", "+00:00"))
                   if end_s else None)
        except Exception:
            continue
        if start <= now and (end is None or now < end):
            active.append(r)
    if not active:
        return None
    active.sort(key=lambda r: str(r.get("effective_from") or ""), reverse=True)
    return int(active[0].get("rebate_multiplier_bps") or 0)


def _floor_cent(x: D) -> D:
    return x.quantize(D("0.01"), rounding=ROUND_DOWN)


def _rebate(qty: D, px: D, multiplier_bps: int) -> D:
    multiplier = D(multiplier_bps) / D("10000")
    raw = multiplier * TAKER_RATE * qty * px * (D("1") - px)
    return _floor_cent(raw)


def run() -> dict:
    now = datetime.now(timezone.utc)
    rates = _get(RATE_PATH)
    liq_cfg = _get(LIQ_CFG_PATH)
    liq_events = _get(LIQ_EVENTS_PATH)
    mult = _active_multiplier_bps(rates, now)
    q1_examples = []
    if mult is not None:
        for px in REPRESENTATIVE_PRICES:
            q1_examples.append({
                "price": str(px),
                "qty": "1",
                "maker_rebate_usd_after_cent_floor": str(_rebate(Q1, px, mult)),
            })
    events = liq_events.get("events") or []
    short_crypto = [e for e in events if any(token in str(e.get("event_ticker") or "")
                                             for token in ("05M", "15M"))]
    q1_size_eligible = Q1 >= D(DOC_MIN_SIZE_CONTRACTS)
    q1_post_only_eligible = not DOC_POST_ONLY_EXCLUDED
    report = {
        "generated_utc": now.isoformat(),
        "status": "PUBLIC_INCENTIVE_AUDIT",
        "labels": "public program configuration only; rebates/rewards are not realized until paid and confirmed",
        "maker_rebate": {
            "public_path": RATE_PATH,
            "crypto_active_rebate_multiplier_bps": mult,
            "taker_rate_used_from_published_program": str(TAKER_RATE),
            "q1_rebate_examples": q1_examples,
            "q1_canary_expected_rebate_counted_in_gate_usd": "0.00",
            "reason": "published maker rebates floor to cents; q=1 examples floor to zero",
            "raw_rate_rules": rates.get("rate_rules") or [],
        },
        "liquidity_rewards": {
            "public_config_path": LIQ_CFG_PATH,
            "public_events_path": LIQ_EVENTS_PATH,
            "program_doc": LIQ_PROGRAM_DOC,
            "config": liq_cfg,
            "documented_qualification_rules": {
                "minimum_size_contracts": DOC_MIN_SIZE_CONTRACTS,
                "size_cap_contracts": DOC_SIZE_CAP_CONTRACTS,
                "two_sided_multiplier": str(DOC_TWO_SIDED_MULTIPLIER),
                "uptime_threshold_pct": str(DOC_UPTIME_THRESHOLD_PCT),
                "post_only_orders_excluded": DOC_POST_ONLY_EXCLUDED,
                "max_spread_cents_from_public_config": liq_cfg.get("max_spread_cents"),
            },
            "crypto_events_returned": len(events),
            "short_duration_05m_15m_events_returned": len(short_crypto),
            "short_duration_events": [
                {"event_ticker": e.get("event_ticker"),
                 "daily_pool_usd": e.get("daily_pool_usd"),
                 "qualifying_maker_count": e.get("qualifying_maker_count"),
                 "ends_at": e.get("ends_at")}
                for e in short_crypto[:25]
            ],
            "q1_canary_reward_counted_in_gate_usd": "0.00",
            "q1_canary_qualification": {
                "qty": "1",
                "maker_or_cancel_post_only": True,
                "meets_minimum_size": bool(q1_size_eligible),
                "post_only_order_eligible": bool(q1_post_only_eligible),
                "qualifies_for_liquidity_rewards": bool(q1_size_eligible and q1_post_only_eligible),
            },
            "reason": "the q=1 makerOrCancel canary is below the documented 10-contract minimum and post-only orders are excluded; liquidity rewards therefore cannot rescue q=1 canary economics",
        },
        "accounting_policy": {
            "maker_rebate": "separate from matched pair/residual P&L; q1 gate counts zero",
            "liquidity_rewards": "separate; q1 gate counts zero",
            "realized": "only after authenticated payout/cashflow confirmation",
        },
    }
    OUT.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    return report


def main() -> int:
    print("GEMINI_L5_INCENTIVE_AUDIT " + json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
