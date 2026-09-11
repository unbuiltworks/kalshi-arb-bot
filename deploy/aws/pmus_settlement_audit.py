#!/usr/bin/env python3
"""Read-only PMUS settlement/activity reconciliation for one market."""
import json
import sys

from src.pmus_q1_executor import Transport

slug = sys.argv[1]
t = Transport()

out = {
    "status": "PMUS_SETTLEMENT_AUDIT_OK",
    "slug": slug,
    "order_entry": False,
    "settlement": None,
    "settlement_error": None,
    "target_position": None,
    "position_expired": None,
    "position_realized": None,
    "position_cash_value": None,
    "resolutions": [],
    "trades": [],
    "balances": [],
}

try:
    out["settlement"] = t.request("GET", f"/v1/markets/{slug}/settlement")
except Exception as exc:
    out["settlement_error"] = f"{type(exc).__name__}:{getattr(exc, 'code', '')}"

pos = t.request("GET", "/v1/portfolio/positions")
positions = pos.get("positions") if isinstance(pos, dict) else None
p = positions.get(slug) if isinstance(positions, dict) else None
if isinstance(p, dict):
    out["target_position"] = p.get("netPositionDecimal", p.get("netPosition", p.get("quantity")))
    out["position_expired"] = p.get("expired")
    out["position_realized"] = p.get("realized")
    out["position_cash_value"] = p.get("cashValue")

# Sign only the documented endpoint path. Filter the returned activity page
# locally so query-string canonicalization can never invalidate auth.
acts = t.request("GET", "/v1/portfolio/activities")
for a in (acts.get("activities") if isinstance(acts, dict) else []) or []:
    if not isinstance(a, dict):
        continue
    typ = a.get("type")
    if typ == "ACTIVITY_TYPE_POSITION_RESOLUTION":
        r = a.get("positionResolution") or {}
        if isinstance(r, dict) and r.get("marketSlug") == slug:
            out["resolutions"].append({k: r.get(k) for k in ("marketSlug", "side", "tradeId", "updateTime") if k in r})
    elif typ == "ACTIVITY_TYPE_TRADE":
        tr = a.get("trade") or {}
        if isinstance(tr, dict) and tr.get("marketSlug") == slug:
            def amount(v):
                if isinstance(v, dict): return v.get("value")
                return v
            out["trades"].append({
                "id": tr.get("id"),
                "marketSlug": tr.get("marketSlug"),
                "state": tr.get("state"),
                "price": amount(tr.get("price")),
                "qty": tr.get("qtyDecimal", tr.get("qty")),
                "isAggressor": tr.get("isAggressor"),
                "costBasis": amount(tr.get("costBasis")),
                "realizedPnl": amount(tr.get("realizedPnl")),
                "createTime": tr.get("createTime"),
                "updateTime": tr.get("updateTime"),
            })

bal = t.request("GET", "/v1/account/balances")
for b in (bal.get("balances") if isinstance(bal, dict) else []) or []:
    if isinstance(b, dict):
        out["balances"].append({k: b.get(k) for k in (
            "currency", "currentBalance", "buyingPower", "assetNotional",
            "assetAvailable", "openOrders", "unsettledFunds", "marginRequirement",
            "lastUpdated") if k in b})

print(json.dumps(out, separators=(",", ":"), default=str))
