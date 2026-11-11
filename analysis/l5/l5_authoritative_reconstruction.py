"""L5 BTC15m: one authoritative reconstruction, per-trade, FIFO-paired.

POPULATION AND FORMULA ARE THE ARTEFACT'S, NOT A NEW CHOICE. Reproduced to
the cent: 686,395 rows, buy_out 6,070,392.86, redeem_in 6,415,918.50,
net +345,525.64 over 5,546 windows.

  identity   (transactionHash, asset, timestamp, size, type, side)
  wallet     proxyWallet == WALLET
  series     "btc-updown-15m" as a SUBSTRING of r["slug"]
  cash       usdcSize for every leg, never size*price
  incentives MAKER_REBATE / TAKER_REBATE / REWARD excluded from P&L
  pnl        redeem_in + sell_in - buy_out

WHY `price` IS NOT IN THE IDENTITY, which is the whole reconciliation: the
fetch repeated 18 passes over the early offsets, and a re-fetched trade comes
back with a differently-rounded float price. Keying on price keeps both
copies -- 702,565 rows instead of 686,395, +327,737 of phantom spend, and a
net of +17,789 instead of +345,526. `side` is in the identity instead because
one transactionHash carries many fills.

CLASSIFICATION BY |IMBALANCE|, NOT BY "HOLDS THE OTHER SIDE". Buying the
short side when already net short that side INCREASES imbalance; a test of
`held_other > 0` calls that a complement and inflates the complement share.
The test here is the change in |up - dn|.

FIFO PAIRING gives an EXACT decomposition rather than a convention: each
newly matched contract is costed against the specific opposite-side lot it
consumes, so matched_pnl + residual_pnl == redeem - buy identically.
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict, deque
from decimal import Decimal as D

RAW = "data/l5/raw/activity.jsonl"
GAP = "data/l5/raw/activity_gap.jsonl.gz"
SERIES = "btc-updown-15m"
WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
INCENTIVE = {"MAKER_REBATE", "TAKER_REBATE", "REWARD"}
KNOWN = {"TRADE", "REDEEM"} | INCENTIVE
WINDOW_S = 900

OPEN, EXTEND, REDUCE, FLATTEN = "OPEN", "EXTEND", "REDUCE", "FLATTEN"


def row_id(r):
    return (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
            r.get("size"), r.get("type"), r.get("side"))


def _raw_rows():
    for path in (RAW, GAP):
        op = gzip.open if path.endswith(".gz") else open
        try:
            with op(path, "rt") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        d = json.loads(ln)
                    except ValueError:
                        continue
                    for r in (d.get("payload") or ()):
                        yield r
        except FileNotFoundError:
            continue


def load():
    """Authoritative, deduped, series- and wallet-filtered rows."""
    seen = set()
    trades = defaultdict(list)          # conditionId -> [rows]
    redeem = defaultdict(lambda: D(0))
    slug_of = {}
    incent = defaultdict(lambda: D(0))
    sells = 0
    for r in _raw_rows():
        rid = row_id(r)
        if rid in seen:
            continue
        seen.add(rid)
        t = r.get("type")
        if t not in KNOWN:
            continue
        if r.get("proxyWallet") not in (None, WALLET):
            continue
        slug = r.get("slug") or ""
        if SERIES not in slug:
            continue
        if t in INCENTIVE:
            incent[t] += D(str(r.get("usdcSize") or 0))
            continue
        cid = r.get("conditionId")
        slug_of.setdefault(cid, slug)
        usd = D(str(r.get("usdcSize") or 0))
        if t == "REDEEM":
            redeem[cid] += usd
            continue
        if r.get("side") == "SELL":
            sells += 1
        trades[cid].append({
            "t": r.get("timestamp"),
            "up": str(r.get("outcome") or "").lower().startswith("up"),
            "side": r.get("side"),
            "qty": D(str(r.get("size") or 0)),
            "usd": usd,
        })
    for cid in trades:
        trades[cid].sort(key=lambda x: (x["t"] or 0))
    return trades, redeem, slug_of, dict(incent), sells


def window_start(slug):
    try:
        return int(str(slug).rsplit("-", 1)[-1])
    except (ValueError, AttributeError):
        return None


def walk(rows):
    """Per-trade ledger with FIFO pairing. Returns (events, summary)."""
    lots = {True: deque(), False: deque()}   # side -> [[qty, unit_px]]
    up = D(0); dn = D(0)
    matched = D(0); matched_cost = D(0); spend = D(0)
    acts = defaultdict(int)
    events = []
    pair_costs = []
    for r in rows:
        if r["side"] != "BUY":
            continue
        q = r["qty"]
        if q <= 0:
            continue
        unit = (r["usd"] / q)
        is_up = r["up"]
        imb_before = up - dn
        held_before = (up, dn)
        # classify on |imbalance|
        if up == 0 and dn == 0:
            act = OPEN
        else:
            imb_after = (up + q - dn) if is_up else (up - dn - q)
            act = REDUCE if abs(imb_after) < abs(imb_before) else EXTEND
        # FIFO match against the opposite side's open lots
        newly = D(0); newly_cost = D(0)
        other = lots[not is_up]
        rem = q
        while rem > 0 and other:
            lot = other[0]
            take = lot[0] if lot[0] <= rem else rem
            newly += take
            newly_cost += take * (unit + lot[1])
            lot[0] -= take
            rem -= take
            if lot[0] <= 0:
                other.popleft()
        if rem > 0:
            lots[is_up].append([rem, unit])
        if is_up:
            up += q
        else:
            dn += q
        matched += newly
        matched_cost += newly_cost
        spend += r["usd"]
        if newly > 0:
            pair_costs.append(float(newly_cost / newly))
        if up == dn:
            act = FLATTEN
        acts[act] += 1
        events.append({
            "t": r["t"], "act": act, "is_up": is_up, "qty": q, "unit": unit,
            "held_before": held_before, "held_after": (up, dn),
            "imb_before": imb_before, "imb_after": up - dn,
            "newly_matched": newly,
            "newly_matched_cost": newly_cost,
            "marginal_pair_cost": (newly_cost / newly) if newly > 0 else None,
            "unmatched_after": abs(up - dn),
        })
    return events, {
        "up": up, "dn": dn, "matched": matched, "matched_cost": matched_cost,
        "residual": up - dn, "spend": spend, "acts": dict(acts),
        "pair_costs": pair_costs,
    }


def main():
    trades, redeem, slug_of, incent, sells = load()
    cids = sorted(trades, key=lambda c: window_start(slug_of.get(c)) or 0)
    tot_buy = D(0); tot_red = D(0)
    tot_m_pnl = D(0); tot_r_pnl = D(0)
    tot_matched = D(0); tot_resid = D(0)
    acts = defaultdict(int)
    bucket = {n: defaultdict(int) for n in
              ("T-15..10m", "T-10..5m", "T-5..2m", "T-2..0.5m", "T-30s..0")}
    all_pair = []
    marg_pair = []
    per_window = []
    n_resolved = 0
    legs_total = 0
    for cid in cids:
        ev, s = walk(trades[cid])
        legs_total += len(ev)
        ws = window_start(slug_of.get(cid))
        for e in ev:
            acts[e["act"]] += 1
            if e["marginal_pair_cost"] is not None:
                marg_pair.append(float(e["marginal_pair_cost"]))
            if ws and e["t"]:
                tte = (ws + WINDOW_S) - e["t"]
                for hi, lo, nm in ((900, 600, "T-15..10m"),
                                   (600, 300, "T-10..5m"),
                                   (300, 120, "T-5..2m"),
                                   (120, 30, "T-2..0.5m"),
                                   (30, -10**9, "T-30s..0")):
                    if hi >= tte > lo:
                        bucket[nm][e["act"]] += 1
                        break
        rd = redeem.get(cid)
        tot_buy += s["spend"]
        tot_matched += s["matched"]; tot_resid += abs(s["residual"])
        all_pair.extend(s["pair_costs"])
        if rd is None:
            per_window.append({"cid": cid, "ws": ws, "resolved": False,
                               "m_pnl": None, "r_pnl": None,
                               "spend": s["spend"], "matched": s["matched"],
                               "residual": abs(s["residual"])})
            continue
        n_resolved += 1
        tot_red += rd
        m_pnl = s["matched"] * D(1) - s["matched_cost"]
        resid_cost = s["spend"] - s["matched_cost"]
        resid_redeem = rd - s["matched"]
        if resid_redeem < 0:
            resid_redeem = D(0)
        r_pnl = resid_redeem - resid_cost
        tot_m_pnl += m_pnl; tot_r_pnl += r_pnl
        per_window.append({"cid": cid, "ws": ws, "resolved": True,
                           "m_pnl": m_pnl, "r_pnl": r_pnl,
                           "spend": s["spend"], "matched": s["matched"],
                           "residual": abs(s["residual"])})

    print("=== POPULATION (reproduces the artefact) ===")
    print("  windows (conditionIds) :", len(cids))
    print("  legs (BUY trades)      :", legs_total)
    print("  SELL rows              :", sells)
    print("  buy_out                : %14.2f" % tot_buy)
    print("  redeem_in              : %14.2f" % tot_red)
    print("  net (redeem - buy)     : %+14.2f" % (tot_red - tot_buy))
    print("  incentives (series)    :", {k: str(v) for k, v in incent.items()} or "{} -- none")
    print()
    tot = sum(acts.values()) or 1
    print("=== ACTION MIX, classified by |imbalance| ===")
    for k in (OPEN, EXTEND, REDUCE, FLATTEN):
        print("  %-8s %7d  %5.2f%%" % (k, acts.get(k, 0),
                                       100.0 * acts.get(k, 0) / tot))
    print()
    print("=== BY TIME TO EXPIRY ===")
    for nm in ("T-15..10m", "T-10..5m", "T-5..2m", "T-2..0.5m", "T-30s..0"):
        m = bucket[nm]; t2 = sum(m.values()) or 1
        print("  %-11s n=%6d  OPEN %5.2f%%  EXTEND %5.2f%%  REDUCE %5.2f%%  FLATTEN %5.2f%%"
              % (nm, t2, 100.0*m.get(OPEN,0)/t2, 100.0*m.get(EXTEND,0)/t2,
                 100.0*m.get(REDUCE,0)/t2, 100.0*m.get(FLATTEN,0)/t2))
    print()
    print("=== DECOMPOSITION (FIFO, exact: components sum to net) ===")
    print("  resolved windows       :", n_resolved)
    print("  MATCHED  pnl           : %+14.2f" % tot_m_pnl)
    print("  RESIDUAL pnl           : %+14.2f" % tot_r_pnl)
    print("  sum                    : %+14.2f" % (tot_m_pnl + tot_r_pnl))
    print("  matched pairs          : %14.2f" % tot_matched)
    print("  |residual| contracts   : %14.2f" % tot_resid)
    print("  residual per pair      : %.4f" % (float(tot_resid/tot_matched) if tot_matched else 0))
    if marg_pair:
        mp = sorted(marg_pair)
        print("  MARGINAL pair cost (per matching trade): median %.4f  p05 %.4f  p95 %.4f"
              % (mp[len(mp)//2], mp[int(len(mp)*0.05)], mp[int(len(mp)*0.95)]))
        for cap in (0.96, 0.97, 0.98, 1.00):
            print("     <= %.2f : %5.1f%% of matching trades"
                  % (cap, 100.0*sum(1 for x in mp if x <= cap)/len(mp)))
    # chronological OOS
    res = [w for w in per_window if w["resolved"]]
    cut = int(len(res)*0.7)
    print()
    print("=== CHRONOLOGICAL OUT-OF-SAMPLE ===")
    for nm, part in (("IN-SAMPLE (first 70%)", res[:cut]),
                     ("OUT-OF-SAMPLE (last 30%)", res[cut:])):
        mp_ = sum(w["m_pnl"] for w in part)
        rp_ = sum(w["r_pnl"] for w in part)
        mq = sum(w["matched"] for w in part)
        rq = sum(w["residual"] for w in part)
        n = len(part) or 1
        wins = sum(1 for w in part if (w["m_pnl"]+w["r_pnl"]) > 0)
        print("  %-26s n=%4d  matched %+11.2f  residual %+11.2f  net %+11.2f  (%+.3f/win)  resid/pair %.4f  win%% %.1f"
              % (nm, len(part), mp_, rp_, mp_+rp_, float((mp_+rp_)/n), float(rq/mq) if mq else 0, 100.0*wins/n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
