"""Is L5's matched/residual split a fact or a pairing convention?

The TOTAL (redeem - buy) is convention-free. The SPLIT is not: it depends on
which lots the pairing consumes. Three conventions, same data.
"""
import sys
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from collections import deque
from decimal import Decimal as D
import importlib.util

spec = importlib.util.spec_from_file_location(
    "auth", "/home/user/kalshi-arb-bot/analysis/l5/l5_authoritative_reconstruction.py")
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

trades, redeem, slug_of, incent, sells = A.load()


def split(rows, mode):
    lots = {True: deque(), False: deque()}
    up = D(0); dn = D(0); matched = D(0); mcost = D(0); spend = D(0)
    ups_q = D(0); ups_c = D(0); dns_q = D(0); dns_c = D(0)
    for r in rows:
        if r["side"] != "BUY":
            continue
        q = r["qty"]
        if q <= 0:
            continue
        unit = r["usd"] / q
        is_up = r["up"]
        if is_up: ups_q += q; ups_c += r["usd"]
        else:     dns_q += q; dns_c += r["usd"]
        if mode in ("fifo", "lifo"):
            other = lots[not is_up]
            rem = q
            while rem > 0 and other:
                lot = other[0] if mode == "fifo" else other[-1]
                take = lot[0] if lot[0] <= rem else rem
                matched += take
                mcost += take * (unit + lot[1])
                lot[0] -= take
                rem -= take
                if lot[0] <= 0:
                    other.popleft() if mode == "fifo" else other.pop()
            if rem > 0:
                lots[is_up].append([rem, unit])
        if is_up: up += q
        else:     dn += q
        spend += r["usd"]
    if mode == "avg":
        matched = min(up, dn)
        au = (ups_c/ups_q) if ups_q > 0 else D(0)
        ad = (dns_c/dns_q) if dns_q > 0 else D(0)
        mcost = matched * (au + ad)
    return matched, mcost, spend, up - dn


print("convention        MATCHED pnl      RESIDUAL pnl        sum      matched-pairs")
for mode in ("fifo", "lifo", "avg"):
    tm = D(0); tr = D(0); tmq = D(0)
    for cid, rows in trades.items():
        rd = redeem.get(cid)
        if rd is None:
            continue
        matched, mcost, spend, resid = split(rows, mode)
        m_pnl = matched - mcost
        r_cost = spend - mcost
        r_red = rd - matched
        if r_red < 0: r_red = D(0)
        tm += m_pnl; tr += r_red - r_cost; tmq += matched
    print("  %-6s      %+14.2f   %+14.2f   %+12.2f   %14.2f"
          % (mode, tm, tr, tm + tr, tmq))
print()
print("CONVENTION-FREE TOTAL (redeem - buy, resolved only): the sum column above")
