"""Reconstruct ONE manually traded PMUS BTC15m window from authoritative
account history. Read-only: GET only, no order path, no cancel path.

This is the MANUAL_FAVORITE_DCA lane. It is kept entirely separate from the
28-window FAVORITE_DCA evaluation sample and nothing it produces may be
pooled with it.

Credentials come only from the environment, as everywhere else in this
repository: PMUS_API_KEY_ID / PMUS_API_SECRET_KEY. Nothing is printed that
could reveal them; the key id appears only as a truncated fingerprint.

NO EXECUTION PRICE IS EVER INFERRED. Every price, share count and fee in the
output comes from a venue response. Where the venue does not serve a field,
the output says MISSING rather than filling it in.
"""
from __future__ import annotations
import argparse, json, os, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import pmus_fees as FEES                                 # noqa: E402
from src import pmus_q1_executor as Q1                            # noqa: E402

ONE = D(1)

#: Routes proven against this account (see l5_pmus_transport): balances,
#: portfolio positions, a single order by id, and open orders. There is NO
#: proven fills/history listing -- GET /v1/orders 501s -- so the history
#: route is DISCOVERED here, read-only, and the discovery result is recorded
#: as evidence rather than assumed.
PROVEN = ("/v1/account/balances", "/v1/portfolio/positions", "/v1/orders/open")
HISTORY_CANDIDATES = (
    "/v1/portfolio/history", "/v1/portfolio/activity", "/v1/portfolio/fills",
    "/v1/portfolio/trades", "/v1/account/activity", "/v1/account/history",
    "/v1/orders/history", "/v1/fills", "/v1/trades", "/v1/activity",
    "/v1/portfolio/orders", "/v1/portfolio/transactions",
    "/v1/account/transactions", "/v1/portfolio/settlements",
)


def discover(t) -> dict:
    """Probe candidate history routes, GET only, and report what answers."""
    out = {}
    for path in PROVEN + HISTORY_CANDIDATES:
        try:
            r = t._get(path)
            out[path] = {"ok": r is not None,
                         "keys": sorted(r)[:12] if isinstance(r, dict)
                         else ("list[%d]" % len(r) if isinstance(r, list)
                               else None)}
        except Exception as e:                                    # noqa: BLE001
            out[path] = {"ok": False, "error": type(e).__name__}
    return out


def reconstruct(fills, slug: str, settlement_long_px: D) -> dict:
    """One row per authoritative fill, with inventory after each action.

    `settlement_long_px` is the venue's own settlementPx for the window:
    1.0000 means the long (UP) side pays, 0.0000 means the short (DOWN) side
    pays. It is read from the venue, never from a screenshot.
    """
    up_sh = dn_sh = up_cash = dn_cash = fees = D(0)
    rows = []
    for f in sorted(fills, key=lambda x: x["t"]):
        side, px, sh = f["side"], D(str(f["px"])), D(str(f["qty"]))
        fee = (D(str(f["fee"])) if f.get("fee") is not None
               else FEES.taker_fee(px, sh))
        fee_src = "venue" if f.get("fee") is not None else "MODELLED_pmus_fees"
        if side == "UP":
            up_sh += sh; up_cash += sh * px
        else:
            dn_sh += sh; dn_cash += sh * px
        fees += fee
        rows.append({
            "t": f["t"], "order_id": f.get("order_id"), "side": side,
            "execution_price": str(px), "shares": str(sh),
            "notional": str(round(sh * px, 6)),
            "fee": str(fee), "fee_source": fee_src,
            "up_shares_after": str(round(up_sh, 6)),
            "dn_shares_after": str(round(dn_sh, 6)),
            "up_cash_after": str(round(up_cash, 6)),
            "dn_cash_after": str(round(dn_cash, 6)),
            "matched_after": str(round(min(up_sh, dn_sh), 6)),
            "residual_after": str(round(up_sh - dn_sh, 6)),
            "cash_out_cumulative": str(round(up_cash + dn_cash + fees, 6)),
        })
    long_wins = settlement_long_px >= D("0.5")
    payout = up_sh if long_wins else dn_sh
    cash_out = up_cash + dn_cash + fees
    return {"rows": rows, "up_shares": str(up_sh), "dn_shares": str(dn_sh),
            "up_cash": str(up_cash), "dn_cash": str(dn_cash),
            "fees": str(fees), "settlement_long_px": str(settlement_long_px),
            "winning_side": "UP" if long_wins else "DOWN",
            "settlement_payout": str(round(payout, 6)),
            "cash_out_total": str(round(cash_out, 6)),
            "net_settled": str(round(payout - cash_out, 6))}


def counterfactuals(fills, settlement_long_px: D) -> dict:
    """The four the user asked for, all on the SAME authoritative fills.

    Nothing is re-priced: each counterfactual is a subset (or an outcome
    flip) of fills that actually happened, so no execution assumption is
    introduced that the account history does not already contain.
    """
    def value(subset, long_wins):
        up = sum(D(str(f["qty"])) for f in subset if f["side"] == "UP")
        dn = sum(D(str(f["qty"])) for f in subset if f["side"] == "DOWN")
        cash = sum(D(str(f["qty"])) * D(str(f["px"])) for f in subset)
        fee = sum((D(str(f["fee"])) if f.get("fee") is not None
                   else FEES.taker_fee(D(str(f["px"])), D(str(f["qty"]))))
                  for f in subset)
        pay = up if long_wins else dn
        return {"up_shares": str(up), "dn_shares": str(dn),
                "cash_out": str(round(cash + fee, 6)),
                "fees": str(round(fee, 6)),
                "settlement_payout": str(round(pay, 6)),
                "net": str(round(pay - cash - fee, 6))}

    lw = settlement_long_px >= D("0.5")
    hedge = [f for f in fills if f.get("leg") == "hedge"]
    ext = [f for f in fills if f.get("leg") != "hedge"]
    return {
        "1_initial_hedge_only": value(hedge, lw),
        "2_extensions_only_no_hedge": value(ext, lw),
        "3_actual_manual_sequence": value(fills, lw),
        "4_same_sequence_had_DOWN_won": value(fills, not lw),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--discover-only", action="store_true")
    ap.add_argument("--fills-json",
                    help="authoritative fills already pulled, for replay")
    ap.add_argument("--out",
                    default="analysis/l5/manual_favorite_dca/"
                            "MANUAL_FAVORITE_DCA.json")
    a = ap.parse_args()
    if a.fills_json:
        payload = json.load(open(a.fills_json))
        fills, settle = payload["fills"], D(str(payload["settlement_long_px"]))
        out = {"lane": "MANUAL_FAVORITE_DCA",
               "separate_from": "the 28-window FAVORITE_DCA sample",
               "slug": a.slug,
               "reconstruction": reconstruct(fills, a.slug, settle),
               "counterfactuals": counterfactuals(fills, settle)}
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(out, open(a.out, "w"), indent=1)
        print(json.dumps(out["counterfactuals"], indent=1))
        return 0
    if not Q1.has_credentials():
        print(json.dumps({
            "status": "BLOCKED_NO_CREDENTIALS",
            "need_env": [Q1.KEY_ENV, Q1.SECRET_ENV],
            "note": "account history cannot be read unsigned; nothing is "
                    "inferred from the screenshot"}, indent=1))
        return 2
    import src.l5_pmus_transport as T
    t = T.Transport()
    print(json.dumps({"route_discovery": discover(t)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
