"""Bonereaper's profit as the PLATFORM accounts for it, by component and
by period.

INPUT: platform_user_pnl_all_20261120.json -- Polymarket's own
`/v2/user-pnl` series for the wallet (cumulative, one point a day,
2026-03-26 to 2026-11-20), and incentive_credits_20261120.json -- every
MAKER_REBATE / TAKER_REBATE / REWARD credit in its activity feed.

The platform's identities, checked below on every point:
    position_pnl   = trade_pnl + fees_paid          (market P&L after fees)
    fees           = fees_paid - fees_refunded      (gross fees charged)
    wallet_income  = maker_rebate + taker_rebate + reward_income
                     + referral_income
    economic_pnl   = position_pnl + wallet_income
`trade_pnl` is the result of the trades themselves BEFORE fees.
Nothing here is estimated; a period figure is the difference of two
cumulative points.
"""
import collections
import datetime as dt
import json
import sys

HERE = "analysis/l5/bonereaper_recent/"
F = ("trade_pnl", "fees", "fees_refunded", "fees_paid", "position_pnl",
     "maker_rebate", "taker_rebate", "reward_income", "referral_income",
     "wallet_income", "economic_pnl", "volume_usdc", "trade_count")


def main():
    pts = json.load(open(HERE + "platform_user_pnl_all_20261120.json"))
    pts = sorted(pts["data"]["points"], key=lambda p: p["timestamp"])
    P = print
    bad = 0
    for p in pts:
        a = abs(p["trade_pnl"] + p["fees_paid"] - p["position_pnl"])
        b = abs(p["fees_paid"] - p["fees_refunded"] - p["fees"])
        c = abs(p["maker_rebate"] + p["taker_rebate"] + p["reward_income"]
                + p["referral_income"] - p["wallet_income"])
        d = abs(p["position_pnl"] + p["wallet_income"] - p["economic_pnl"])
        bad += (max(a, b, c, d) > 0.05)
    P("points %d   %s -> %s   identity failures: %d" % (
        len(pts), day(pts[0]), day(pts[-1]), bad))

    def show(label, a, b):
        d = {k: (b[k] or 0) - ((a[k] or 0) if a else 0) for k in F}
        v = d["volume_usdc"] or 1.0
        P("\n== %s ==" % label)
        P("   volume $%s in %s trades" % (
            f"{d['volume_usdc']:,.0f}", f"{int(d['trade_count']):,}"))
        for k, lab in (
                ("trade_pnl", "trades, before fees"),
                ("fees", "fees charged (gross)"),
                ("fees_refunded", "fees refunded"),
                ("position_pnl", "= market P&L after fees"),
                ("taker_rebate", "taker rebate"),
                ("maker_rebate", "maker rebate"),
                ("reward_income", "liquidity rewards"),
                ("referral_income", "referral income"),
                ("economic_pnl", "= TOTAL")):
            x = d[k] if k != "fees_refunded" else d[k]
            P("   %-26s %+14s   %+.3f%% of volume" % (
                lab, f"{x:,.0f}", 100.0 * x / v))
        return d

    show("LIFETIME, 2026-03-25 to %s" % day(pts[-1]), None, pts[-1])
    by = collections.OrderedDict()
    for p in pts:
        by[day(p)[:7]] = p
    P("\n== BY MONTH (USD; each row is that month's change) ==")
    P("   month     volume      trades_pre_fee  net_fees   market_pnl  "
      "taker_reb  maker_reb   TOTAL   total%vol")
    prev = None
    for m, p in by.items():
        d = {k: (p[k] or 0) - ((prev[k] or 0) if prev else 0) for k in F}
        P("   %s %11s %14s %10s %11s %10s %10s %9s  %+.3f%%" % (
            m, f"{d['volume_usdc']:,.0f}", f"{d['trade_pnl']:+,.0f}",
            f"{d['fees_paid']:+,.0f}", f"{d['position_pnl']:+,.0f}",
            f"{d['taker_rebate']:+,.0f}", f"{d['maker_rebate']:+,.0f}",
            f"{d['economic_pnl']:+,.0f}",
            100.0 * d["economic_pnl"] / (d["volume_usdc"] or 1)))
        prev = p
    last = pts[-1]

    def back(n):
        t = last["timestamp"] - n * 86400
        return min(pts, key=lambda p: abs(p["timestamp"] - t))
    for n in (30, 7):
        a = back(n)
        show("LAST %d DAYS (%s to %s)" % (n, day(a), day(last)), a, last)
    return 0


def day(p):
    return dt.datetime.fromtimestamp(p["timestamp"],
                                     dt.timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    sys.exit(main())
