#!/usr/bin/env python3
"""Build frozen candidate states for the PMUS BTC15m late-favorite lane.

Two-stage by construction, which is the anti-overfitting point:
  stage 1  write every candidate state WITHOUT any outcome field, and hash it;
  stage 2  join settlement truth to the hashed file.

Read-only. Reads only DISCOVERY windows from windows.json and never a slug on
the reserved denylist. SHADOW/HISTORICAL ONLY.

Venue execution semantics (from the recorder lane, P39_AUTHORIZATION_GATE):
    buy UP   -> pay `ask`
    buy DOWN -> pay `1 - bid`
Both sides come from the single per-market book, which is also the basis for
normalizing the A/B/C payoff vectors in Phase 2.
"""
from __future__ import annotations
import collections, datetime as dt, glob, gzip, hashlib, json, math, re, statistics as st

WINDOWS = "analysis/pmus_late_favorite/windows.json"
AUTH = "data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl.gz"
PUB = "data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl"
CANDLES = "data/btc15m/coinbase_1m.jsonl.gz"
OUT_STATES = "analysis/pmus_late_favorite/states_nooutcome.json"
OUT_JOINED = "analysis/pmus_late_favorite/states_joined.json"

WINDOW_S = 900
CHECKPOINTS = [300, 180, 120, 60, 30]
STALE_MAX_S = 10.0      # preregistered: never carry a quote staler than this
VOL_LOOKBACK_MIN = 30   # trailing completed minutes for vol_1m


def wstart(slug):
    m = re.search(r"(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})z$", slug)
    return dt.datetime.fromisoformat(
        f"{m.group(1)}T{m.group(2)}:{m.group(3)}:00+00:00").timestamp()


def epoch(t):
    if isinstance(t, (int, float)):
        return float(t)
    return dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()


# ---------- BTC reference (1-minute proxy) ----------
def load_candles():
    """minute_start_epoch -> (low, high, open, close). Coinbase order is
    [time, low, high, open, close, volume]."""
    c = {}
    for line in gzip.open(CANDLES, "rt"):
        r = json.loads(line)
        c[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
    return c


def ohlc4(c, minute):
    v = c.get(int(minute))
    if v is None:
        return None
    low, high, op, cl = v
    return (op + high + low + cl) / 4.0


def ref_at(c, mark):
    """Reference as of `mark` using ONLY completed minutes -- never lookahead.
    Returns (price, staleness_seconds)."""
    m = int(mark // 60) * 60
    # the minute starting at m is still in progress at `mark`; last completed one ended at m
    prev = m - 60
    v = c.get(prev)
    if v is None:
        return None, None
    return v[3], mark - m          # close of [prev, m) observed at m


def vol_1m(c, mark):
    """Trailing realized 1-minute volatility as a fraction, from completed
    minutes strictly before `mark`."""
    m = int(mark // 60) * 60
    closes = []
    for k in range(VOL_LOOKBACK_MIN + 1):
        v = c.get(m - 60 * (VOL_LOOKBACK_MIN - k + 1))
        closes.append(v[3] if v else None)
    cl = [x for x in closes if x]
    if len(cl) < 10:
        return None
    rets = [math.log(b / a) for a, b in zip(cl, cl[1:]) if a and b]
    if len(rets) < 8:
        return None
    s = st.pstdev(rets)
    return s if s > 0 else None


# ---------- PMUS book states ----------
def auth_books():
    out = collections.defaultdict(list)
    for line in gzip.open(AUTH, "rt"):
        d = json.loads(line)
        if d.get("kind") != "book":
            continue
        out[d["slug"]].append((epoch(d["t"]), d.get("bid"), d.get("ask"),
                               d.get("bid_qty"), d.get("ask_qty"), d.get("status")))
    for v in out.values():
        v.sort()
    return out


def pub_books():
    out = collections.defaultdict(list)
    for line in open(PUB):
        d = json.loads(line)
        if d.get("kind") != "bbo":
            continue
        s = d.get("slug")
        if not s:
            continue
        f = lambda x: None if x in (None, "") else float(x)
        out[s].append((epoch(d["t"]), f(d.get("bid")), f(d.get("ask")),
                       d.get("bid_shares"), d.get("ask_shares"), d.get("state")))
    for v in out.values():
        v.sort()
    return out


def latest_at(rows, mark):
    best = None
    for r in rows:
        if r[0] <= mark:
            best = r
        else:
            break
    if best is None or mark - best[0] > STALE_MAX_S:
        return None
    return best


def num(x):
    try:
        return float(x)
    except Exception:
        return None


def main():
    W = json.load(open(WINDOWS))
    deny = set(W["reserved_denylist"]["named_explicitly"])
    disc = [w for w in W["windows"] if w["klass"] == "ENGINEERING/DISCOVERY"]
    withtrade = set(W["discovery_with_trade_tape"])

    c = load_candles()
    A, P = auth_books(), pub_books()

    states, drops = [], collections.Counter()
    for w in disc:
        slug = w["slug"]
        if slug in deny:
            drops["reserved_denylist"] += 1
            continue
        T = wstart(slug)
        rows = A.get(slug) or P.get(slug)
        src = "authenticated_ws" if slug in withtrade else "public_gateway"
        if not rows:
            drops["no_book_rows"] += 1
            continue
        p0 = ohlc4(c, T)
        if p0 is None:
            drops["no_p0_candle"] += 1
            continue
        for cp in CHECKPOINTS:
            mark = T + WINDOW_S - cp
            bk = latest_at(rows, mark)
            if bk is None:
                drops[f"stale_or_missing_book_T-{cp}"] += 1
                continue
            _, bid, ask, bq, aq, status = bk
            bid, ask = num(bid), num(ask)
            ref, refstale = ref_at(c, mark)
            v = vol_1m(c, mark)
            if ref is None or v is None:
                drops[f"no_ref_T-{cp}"] += 1
                continue
            dist_frac = (ref - p0) / p0
            mins = cp / 60.0
            z = dist_frac / (v * math.sqrt(mins))
            fav_up = dist_frac >= 0            # tie -> UP, matching settlement
            # buy UP at ask; buy DOWN at 1-bid
            up_ask = ask
            dn_ask = None if bid is None else 1.0 - bid
            fav_ask = up_ask if fav_up else dn_ask
            opp_ask = dn_ask if fav_up else up_ask
            r30, _ = ref_at(c, mark - 30)
            r60, _ = ref_at(c, mark - 60)
            states.append({
                "slug": slug, "source": src, "checkpoint_s_remaining": cp,
                "mark_utc": dt.datetime.fromtimestamp(mark, dt.timezone.utc).isoformat(),
                "day": slug[19:29],
                "seconds_remaining": cp,
                "btc_ref": ref, "ref_staleness_s": refstale, "strike_p0": p0,
                "dist_usd": ref - p0,
                "dist_bps_signed": 1e4 * dist_frac,
                "dist_bps_abs": abs(1e4 * dist_frac),
                "vol_1m": v, "z": z, "abs_z": abs(z),
                "btc_move_30s_bps": None if r30 is None else 1e4 * (ref - r30) / p0,
                "btc_move_60s_bps": None if r60 is None else 1e4 * (ref - r60) / p0,
                "fav_side": "UP" if fav_up else "DOWN",
                "book_bid_up": bid, "book_ask_up": ask,
                "fav_ask": fav_ask, "opp_ask": opp_ask,
                "spread": None if (bid is None or ask is None) else ask - bid,
                "bid_qty": num(bq), "ask_qty": num(aq),
                "one_sided": (bid is None) != (ask is None),
                "two_sided": bid is not None and ask is not None,
                "book_status": status,
            })

    blob = {"lane": "pmus-btc15m-late-favorite", "stage": "1_STATES_NO_OUTCOME",
            "shadow_historical_only": True,
            "venue_execution_semantics": "buy UP at ask; buy DOWN at 1 - bid",
            "reference_class": "COINBASE 1-MINUTE PROXY, NOT BRTI SETTLEMENT",
            "stale_max_s": STALE_MAX_S, "vol_lookback_min": VOL_LOOKBACK_MIN,
            "checkpoints": CHECKPOINTS,
            "n_states": len(states), "drops": dict(drops), "states": states}
    txt = json.dumps(blob, indent=1, sort_keys=True)
    open(OUT_STATES, "w").write(txt)
    h = hashlib.sha256(txt.encode()).hexdigest()
    print(f"stage 1: {len(states)} states over {len({s['slug'] for s in states})} markets")
    print(f"  sha256(states_nooutcome.json) = {h}")
    for k, v in sorted(drops.items()):
        print(f"  drop {k}: {v}")

    # ---------- stage 2: join settlement to the hashed file ----------
    outc = {}
    for slug in {s["slug"] for s in states}:
        T = wstart(slug)
        p0, fin = ohlc4(c, T), ohlc4(c, T + 840)
        outc[slug] = None if (p0 is None or fin is None) else ("UP" if fin >= p0 else "DOWN")
    for s in states:
        s["settlement_winner"] = outc[s["slug"]]
        s["favorite_won"] = None if outc[s["slug"]] is None else int(outc[s["slug"]] == s["fav_side"])
    blob.update(stage="2_SETTLEMENT_JOINED",
                states_nooutcome_sha256=h,
                settlement_class="DERIVED FROM 1-MINUTE PROXY (OHLC4 of first/last minute); tie -> UP")
    json.dump(blob, open(OUT_JOINED, "w"), indent=1, sort_keys=True)
    nw = sum(1 for s in states if s["favorite_won"] == 1)
    print(f"stage 2: joined. favorite won {nw}/{len(states)} state-observations")


if __name__ == "__main__":
    main()
