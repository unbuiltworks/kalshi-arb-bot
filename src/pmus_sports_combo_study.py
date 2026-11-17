"""READ-ONLY study of PMUS combos (parlays): is quoting them worth it?

OWNER 2026-11-16: "PMUS calls them combos ... Ya" (build a read-only study
before risking anything). A combo is a user-defined instrument of 2-50 legs
that pays $1 only if every leg goes the way it was taken; it trades through
the normal order book, mostly after an RFQ answered by market makers
(docs.polymarket.us/trader-guide/combos). Combo makers pay
0.03 * C * p * (1 - p) and get no rebate; combo takers pay
C * p * [theta * (1 - p) + 0.06 * (1 - p)^4] (docs.polymarket.us/fees).

What this measures, every POLL_S, with signed GETs only:
  * RFQ flow: GET /v1/rfqs?status=RFQ_STATUS_OPEN -- how many, how big,
    how many legs, same-game or not.
  * Combo prints: GET /v1/rfqs/trades (anonymous) -- price, size, side.
  * Fair value of each printed combo: the product of its legs' current PMUS
    mids (buy leg -> mid, sell leg -> 1 - mid), and the implied maker
    margin = print price - fair, net of the maker fee. Legs are priced at
    READ time, up to POLL_S after the print: an approximation, labelled.
Independence is assumed in the product; same-game legs are flagged because
they are correlated and the naive product misprices them.

SETTLEMENT SCORING (owner 2026-11-17 "keep going"). The fair-value margin
above is an estimate (late mids, independence). The test that needs neither
is settlement: a maker who SOLD a combo at px (taker bought) makes
px - payout - maker fee per contract, payout 1 only if every leg went the
way it was taken; a maker who BOUGHT (taker sold) makes the negative of the
price part. Every combo print is persisted with its legs to STATE (JSONL,
size-capped) and each leg's PUBLIC final settlementPx (keyless gateway BBO,
state EXPIRED/RESOLVED/SETTLED, value exactly 0 or 1) scores it: any one
leg lost settles the combo at 0 early; all legs won settles it at 1; a
non-binary leg settlement (void) is excluded. A deterministic 1-in-
BACKFILL_SAMPLE sample of the last BACKFILL_HOURS of prints is backfilled so
already-finished games score at once.

SIGNED-READ BUDGET: every signed GET goes through one token bucket
(SIGNED_RPS) so the study can never crowd the trading engine's own reads;
live prints are served before backfill.

It cannot place, quote, accept or cancel anything: it is given two read
functions and nothing else. Labels: modeled study only -- nothing here is
realized P&L for this account.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

COMBO_MAKER_RATE = 0.03
COMBO_TAKER_EXTRA = 0.06
RESOLVED_STATES = ("MARKET_STATE_EXPIRED", "MARKET_STATE_RESOLVED",
                   "MARKET_STATE_SETTLED")
SIGNED_RPS = float(os.getenv("PMUS_COMBO_STUDY_SIGNED_RPS", "1.5"))
# LIVE 13:35Z: one shared bucket let the backfill starve live prints (mid
# read 700 s+ late and growing). Live gets most of the budget, backfill the
# rest, and live prints are sampled so ~0.85 new combos/s fit.
BACKFILL_SHARE = 0.2
LIVE_SAMPLE = int(os.getenv("PMUS_COMBO_STUDY_LIVE_SAMPLE", "2"))
MAX_MID_LAG_S = 60.0
BACKFILL_HOURS = float(os.getenv("PMUS_COMBO_STUDY_BACKFILL_HOURS", "24"))
BACKFILL_SAMPLE = int(os.getenv("PMUS_COMBO_STUDY_BACKFILL_SAMPLE", "20"))
MAX_STORE_MB = float(os.getenv("PMUS_COMBO_STUDY_MAX_MB", "200"))
LEG_RECHECK_S = 600.0
PAGE = 100


def maker_sign(aggressor: Any) -> int:
    """+1: the taker BOUGHT, so the maker sold the combo; -1: the reverse."""
    return -1 if "SELL" in str(aggressor or "").upper() else 1


def leg_won(leg: Mapping[str, Any], settle_px: float) -> bool:
    sell = "SELL" in str(leg.get("side") or "").upper()
    return settle_px == 0.0 if sell else settle_px == 1.0


def combo_payout(legs: List[Mapping[str, Any]],
                 settled: Mapping[str, Optional[float]]) -> Optional[float]:
    """1 / 0 once known, "void" if a leg settled non-binary, else None.
    One lost leg settles the combo at 0 without waiting for the rest."""
    pending = False
    for leg in legs:
        px = settled.get(str(leg.get("symbol")))
        if px is None:
            pending = True
            continue
        if px not in (0.0, 1.0):
            return "void"                                # type: ignore
        if not leg_won(leg, px):
            return 0.0
    return None if pending else 1.0


def in_sample(trade_id: str, k: int) -> bool:
    if k <= 1:
        return True
    h = int(hashlib.sha256(trade_id.encode()).hexdigest()[:8], 16)
    return h % k == 0


def bucket_legs(n: int) -> str:
    return "2" if n <= 2 else "3-4" if n <= 4 else "5-8" if n <= 8 else "9+"


def _ts(v: Any) -> Optional[datetime]:
    """Venue times carry nanoseconds; keep microseconds, assume UTC."""
    t = str(v or "").strip().replace("Z", "")
    if "+" in t:
        t = t.split("+")[0]
    if "." in t:
        head, frac = t.split(".", 1)
        t = head + "." + "".join(c for c in frac if c.isdigit())[:6]
    try:
        return datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


class Bucket:
    def __init__(self, rps: float, clock=time.monotonic, sleep=time.sleep):
        self.rps, self.clock, self.sleep = rps, clock, sleep
        self.next = clock()
        self.lock = threading.Lock()

    def take(self) -> None:
        if self.rps <= 0:
            return
        with self.lock:
            now = self.clock()
            wait = self.next - now
            self.next = max(now, self.next) + 1.0 / self.rps
        if wait > 0:
            self.sleep(wait)


def combo_maker_fee(p: float, qty: float = 1.0) -> float:
    return COMBO_MAKER_RATE * qty * p * (1 - p)


def combo_taker_fee(p: float, qty: float = 1.0, theta: float = 0.095) -> float:
    return qty * p * (theta * (1 - p) + COMBO_TAKER_EXTRA * (1 - p) ** 4)


def _f(v: Any) -> Optional[float]:
    if isinstance(v, Mapping):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def event_key(symbol: str) -> str:
    """aec-nba-ny-tor-2026-11-15 and asc-nba-ny-tor-2026-11-15-pos-3pt5
    share the game key nba-ny-tor-2026-11-15."""
    parts = str(symbol).split("-")
    for i in range(len(parts) - 2):
        if len(parts[i]) == 4 and parts[i].isdigit():
            return "-".join(parts[1:i + 3])
    return str(symbol)


def fair_value(legs: List[Mapping[str, Any]],
               mids: Mapping[str, Optional[float]]) -> Optional[float]:
    """Independent-legs fair price of the combo, or None if any leg is
    unpriced."""
    fv = 1.0
    for leg in legs:
        m = mids.get(str(leg.get("symbol")))
        if m is None:
            return None
        sell = "SELL" in str(leg.get("side") or "").upper()
        fv *= (1 - m) if sell else m
    return fv


def same_game(legs: List[Mapping[str, Any]]) -> bool:
    keys = [event_key(leg.get("symbol")) for leg in legs]
    return len(set(keys)) < len(keys)


class ComboStudy:
    def __init__(self, signed_get: Callable[[str], Any],
                 pm_get: Callable[[str], Any], log: Callable, *,
                 poll_s: float = 30.0, summary_s: float = 600.0,
                 clock: Callable[[], float] = time.time,
                 state_dir: Optional[Path] = None,
                 signed_rps: float = SIGNED_RPS,
                 backfill_hours: float = BACKFILL_HOURS,
                 backfill_sample: int = BACKFILL_SAMPLE,
                 live_sample: Optional[int] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime] = lambda: datetime.now(
                     timezone.utc)):
        self.pm_get, self.log = pm_get, log
        self.poll_s, self.summary_s, self.clock = poll_s, summary_s, clock
        self.sleep, self.now = sleep, now
        self.bucket = Bucket(signed_rps * (1 - BACKFILL_SHARE), sleep=sleep)
        self.bf_bucket = Bucket(signed_rps * BACKFILL_SHARE, sleep=sleep)
        self.live_sample = LIVE_SAMPLE if live_sample is None else live_sample
        self._tl = threading.local()
        self._signed = signed_get
        self.legs: Dict[str, Optional[List[Mapping[str, Any]]]] = {}
        self.seen_rfq: set = set()
        self.seen_trade: set = set()
        self.since = now() - timedelta(minutes=10)
        self.backfill_hours, self.backfill_sample = (backfill_hours,
                                                     backfill_sample)
        self.stop = False
        self.errors: Dict[str, str] = {}
        self.notes: Dict[str, Any] = {}
        self.agg = {"rfqs": 0, "rfq_legs": {}, "rfq_same_game": 0,
                    "trades": 0, "combo_trades": 0, "volume_usd": 0.0,
                    "priced": 0, "margin_sum": 0.0, "margin_net_sum": 0.0,
                    "margin_pos": 0, "same_game_trades": 0,
                    "mid_lag_s_sum": 0.0, "backfilled": 0}
        # settlement scoring
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.settled_ids: set = set()
        self.settled: List[Dict[str, Any]] = []
        self.leg_px: Dict[str, Optional[float]] = {}     # final px, None=open
        self.leg_checked: Dict[str, float] = {}
        self.dir = Path(state_dir) if state_dir else None
        self.lock = threading.Lock()
        self._load()

    # --------------------------------------------------------- persistence
    def _path(self, name: str) -> Optional[Path]:
        return self.dir / name if self.dir else None

    def _append(self, name: str, row: Mapping[str, Any]) -> None:
        p = self._path(name)
        if p is None:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if name == "trades.jsonl" and p.exists() and \
                    p.stat().st_size > MAX_STORE_MB * 1e6:
                self.notes["store_full"] = True
                return
            with p.open("a") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        except OSError as exc:
            self.errors["store"] = f"{type(exc).__name__}:{exc}"[:160]

    def _load(self) -> None:
        for name in ("trades.jsonl", "settled.jsonl"):
            p = self._path(name)
            if p is None or not p.exists():
                continue
            try:
                with p.open() as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if name == "trades.jsonl":
                            self.seen_trade.add(r["id"])
                            self.pending[r["id"]] = r
                        else:
                            self.settled_ids.add(r["id"])
                            self.settled.append(r)
            except OSError as exc:
                self.errors["load"] = f"{type(exc).__name__}:{exc}"[:160]
        for i in self.settled_ids:
            self.pending.pop(i, None)

    # ----------------------------------------------------------- reads
    def get(self, path: str) -> Any:
        (self.bf_bucket if getattr(self._tl, "backfill", False)
         else self.bucket).take()
        return self._signed(path)

    def combo_legs(self, symbol: str) -> Optional[List[Mapping[str, Any]]]:
        if symbol in self.legs:
            return self.legs[symbol]
        legs = None
        try:
            d = self.get(f"/v1/combos?symbol={symbol}") or {}
            c = (d.get("combos") or [None])[0]
            if isinstance(c, Mapping) and c.get("legs"):
                legs = [{"symbol": x.get("symbol"), "side": x.get("side")}
                        for x in c["legs"] if isinstance(x, Mapping)]
        except Exception as exc:                          # noqa: BLE001
            self.errors["combos"] = f"{type(exc).__name__}:{exc}"[:160]
            return None                    # not cached: retry next time
        self.legs[symbol] = legs          # None = a plain (non-combo) symbol
        return legs

    def _bbo(self, symbol: str) -> Mapping[str, Any]:
        return (self.pm_get(f"/v1/markets/{symbol}/bbo") or {}).get(
            "marketData") or {}

    def mid(self, symbol: str) -> Optional[float]:
        # LIVE 2026-11-16 11:46Z: one leg's BBO read raised and killed the
        # study thread after its first batch. A leg that cannot be read
        # (closed, settled, unknown) is simply unpriced.
        try:
            md = self._bbo(symbol)
        except Exception as exc:                          # noqa: BLE001
            self.errors["leg_bbo"] = f"{type(exc).__name__}:{exc}"[:160]
            return None
        b, a = _f(md.get("bestBid")), _f(md.get("bestAsk"))
        if b is None or a is None or a <= b:
            return None
        return (a + b) / 2

    def mids(self, symbols: List[str]) -> Dict[str, Optional[float]]:
        """All legs at once (keyless gateway), so the read sits as close to
        the print as possible."""
        uniq = list(dict.fromkeys(symbols))
        if len(uniq) <= 1:
            return {s: self.mid(s) for s in uniq}
        with ThreadPoolExecutor(max_workers=min(8, len(uniq))) as ex:
            return dict(zip(uniq, ex.map(self.mid, uniq)))

    def final_px(self, symbol: str) -> Optional[float]:
        """Public final settlementPx of a leg (0/1, or another value for a
        void), None while the leg is open or unreadable."""
        if self.leg_px.get(symbol) is not None:
            return self.leg_px[symbol]
        now = self.clock()
        if now - self.leg_checked.get(symbol, -1e18) < LEG_RECHECK_S:
            return None
        self.leg_checked[symbol] = now
        try:
            md = self._bbo(symbol)
        except Exception as exc:                          # noqa: BLE001
            self.errors["leg_settle"] = f"{type(exc).__name__}:{exc}"[:160]
            return None
        if md.get("marketSlug") not in (None, symbol) or \
                md.get("state") not in RESOLVED_STATES:
            return None
        px = _f(md.get("settlementPx"))
        if px is not None:
            self.leg_px[symbol] = px
        return px

    # ----------------------------------------------------------- one pass
    def poll_rfqs(self) -> None:
        try:
            d = self.get("/v1/rfqs?status=RFQ_STATUS_OPEN&limit=100") or {}
        except Exception as exc:                          # noqa: BLE001
            self.errors["rfqs"] = f"{type(exc).__name__}:{exc}"[:160]
            return
        for r in d.get("rfqs") or []:
            rid = str(r.get("id") or "")
            if not rid or rid in self.seen_rfq:
                continue
            self.seen_rfq.add(rid)
            legs = r.get("comboLegs") or self.combo_legs(str(r.get("symbol")))
            n = len(legs or [])
            self.agg["rfqs"] += 1
            self.agg["rfq_legs"][str(n)] = self.agg["rfq_legs"].get(str(n),
                                                                   0) + 1
            if legs and same_game(legs):
                self.agg["rfq_same_game"] += 1
            self.log("COMBO_STUDY_RFQ", rfq_id=rid, symbol=r.get("symbol"),
                     legs=n, same_game=bool(legs and same_game(legs)),
                     qty=r.get("qtyDecimal"), cash=r.get("cashOrderQty"),
                     label="READ-ONLY study; nothing quoted")

    def fetch_window(self, start: datetime, end: datetime,
                     depth: int = 0) -> List[Mapping[str, Any]]:
        """Every print in [start, end): a full page is split in halves so a
        busy minute is never truncated at the page limit."""
        d = self.get(f"/v1/rfqs/trades?limit={PAGE}&startTime={_iso(start)}"
                     f"&endTime={_iso(end)}") or {}
        if "trades_keys" not in self.notes:
            self.notes["trades_keys"] = sorted(d.keys())[:12]
        rows = [t for t in d.get("trades") or [] if isinstance(t, Mapping)]
        late = [t for t in rows if (_ts(t.get("executedTime")) or end) >
                end + timedelta(seconds=1)]
        if late:
            self.notes["endTime_ignored"] = True
        if len(rows) >= PAGE and depth < 8 and not late and \
                (end - start).total_seconds() > 2:
            mid = start + (end - start) / 2
            return (self.fetch_window(start, mid, depth + 1) +
                    self.fetch_window(mid, end, depth + 1))
        if len(rows) >= PAGE:
            self.notes["page_truncated"] = self.notes.get(
                "page_truncated", 0) + 1
        return rows

    def poll_trades(self) -> None:
        end = self.now()
        try:
            rows = self.fetch_window(self.since, end)
        except Exception as exc:                          # noqa: BLE001
            self.errors["trades"] = f"{type(exc).__name__}:{exc}"[:160]
            return
        for t in rows:
            self._ingest(t, live=True)
        self.since = end - timedelta(seconds=5)       # overlap; ids dedupe

    def _ingest(self, t: Mapping[str, Any], live: bool) -> None:
        tid = str(t.get("tradeId") or "")
        with self.lock:
            if not tid or tid in self.seen_trade:
                return
            self.seen_trade.add(tid)
        self.agg["trades"] += 1
        if live and not in_sample(tid, self.live_sample):
            self.agg["live_unsampled"] = self.agg.get("live_unsampled",
                                                      0) + 1
            return
        self.record_trade(t, live=live)

    def record_trade(self, t: Mapping[str, Any], live: bool = True) -> None:
        sym = str(t.get("symbol") or "")
        legs = self.combo_legs(sym)
        if not legs:
            return                                # a single-market RFQ fill
        px, qty = _f(t.get("price")), _f(t.get("qtyDecimal")) or 0.0
        self.agg["combo_trades"] += 1
        if not live:
            self.agg["backfilled"] += 1
        if px is not None:
            self.agg["volume_usd"] += px * qty
        sg = same_game(legs)
        self.agg["same_game_trades"] += int(sg)
        sign = maker_sign(t.get("aggressorSide"))
        rec: Dict[str, Any] = {
            "symbol": sym, "price": px, "qty": qty, "legs": len(legs),
            "same_game": sg, "aggressor": t.get("aggressorSide"),
            "executed": t.get("executedTime"), "fair_now": None,
            "source": "live" if live else "backfill"}
        if live:
            ex = _ts(t.get("executedTime"))
            lag = (self.now() - ex).total_seconds() if ex else None
            fv = None
            if lag is not None and lag <= MAX_MID_LAG_S:
                mids = self.mids([str(l.get("symbol")) for l in legs])
                fv = fair_value(legs, mids)
            else:
                self.agg["too_late_to_price"] = self.agg.get(
                    "too_late_to_price", 0) + 1
            rec.update(fair_now=fv, mid_lag_s=round(lag, 1) if lag else lag)
            if fv is not None and px is not None:
                margin = sign * (px - fv)            # maker's side of it
                net = margin - combo_maker_fee(px)
                self.agg["priced"] += 1
                self.agg["margin_sum"] += margin
                self.agg["margin_net_sum"] += net
                self.agg["margin_pos"] += int(net > 0)
                self.agg["mid_lag_s_sum"] += lag or 0.0
                rec.update(maker_margin=round(margin, 4),
                           maker_margin_after_fee=round(net, 4),
                           taker_fee_est=round(combo_taker_fee(px), 4))
            self.log("COMBO_STUDY_TRADE", **rec,
                     label=("READ-ONLY; fair = product of leg mids at READ "
                            "time (mid_lag_s after the print), independence "
                            "assumed; same_game legs are correlated"))
        if px is not None and qty > 0:
            row = {"id": str(t.get("tradeId")), "symbol": sym, "px": px,
                   "qty": qty, "sign": sign, "legs": legs, "sg": sg,
                   "executed": t.get("executedTime"),
                   "fair": rec["fair_now"], "src": rec["source"]}
            with self.lock:
                self.pending[row["id"]] = row
            self._append("trades.jsonl", row)

    # ------------------------------------------------------- settlement
    def settle_pass(self, max_reads: int = 150) -> int:
        """Score pending prints whose legs have settled. Returns the number
        settled this pass."""
        with self.lock:
            rows = list(self.pending.values())
        need: List[str] = []
        for r in rows:
            for leg in r["legs"]:
                s = str(leg.get("symbol"))
                if self.leg_px.get(s) is None and s not in need:
                    need.append(s)
        need = need[:max_reads]
        # a print whose legs never settle (delisted) is dropped after 5 days
        cutoff = self.now() - timedelta(days=5)
        for r in rows:
            ex = _ts(r.get("executed"))
            if ex and ex < cutoff:
                with self.lock:
                    self.pending.pop(r["id"], None)
                self.notes["expired_unsettled"] = self.notes.get(
                    "expired_unsettled", 0) + 1
        if need:
            with ThreadPoolExecutor(max_workers=4) as ex:
                list(ex.map(self.final_px, need))
        n = 0
        for r in rows:
            pay = combo_payout(r["legs"], self.leg_px)
            if pay is None:
                continue
            out = {"id": r["id"], "symbol": r["symbol"], "px": r["px"],
                   "qty": r["qty"], "sign": r["sign"], "legs": len(r["legs"]),
                   "sg": r["sg"], "fair": r.get("fair"), "src": r.get("src"),
                   "executed": r.get("executed")}
            if pay == "void":
                out["void"] = True
            else:
                out["payout"] = pay
                out["maker_pnl"] = round(r["sign"] * (r["px"] - pay)
                                         - combo_maker_fee(r["px"]), 6)
            with self.lock:
                self.pending.pop(r["id"], None)
                self.settled_ids.add(r["id"])
                self.settled.append(out)
            self._append("settled.jsonl", out)
            n += 1
        return n

    def settled_summary(self) -> Dict[str, Any]:
        """SETTLED maker P&L per contract (and per $ of premium) of the
        prints a maker took the other side of: a modeled study of OTHER
        accounts' trades, not this account's realized P&L."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in self.settled:
            if r.get("void"):
                continue
            for k in ("all", f"legs_{bucket_legs(r['legs'])}",
                      "same_game" if r["sg"] else "cross_game",
                      f"src_{r.get('src')}"):
                groups.setdefault(k, []).append(r)
        out: Dict[str, Any] = {}
        for k, v in groups.items():
            x = [r["maker_pnl"] for r in v]
            n = len(x)
            m = sum(x) / n
            sd = math.sqrt(sum((y - m) ** 2 for y in x) / (n - 1)) if n > 1 \
                else None
            prem = sum(r["px"] * r["qty"] for r in v)
            pnl = sum(r["maker_pnl"] * r["qty"] for r in v)
            fair = [r for r in v if r.get("fair") is not None]
            out[k] = {"n": n, "mean_c": round(m * 100, 3),
                      "se_c": round(sd / math.sqrt(n) * 100, 3) if sd
                      else None,
                      "usd_pnl": round(pnl, 2), "usd_premium": round(prem, 2),
                      "pct_of_premium": round(pnl / prem, 4) if prem else None,
                      "won_by_taker": sum(1 for r in v if r["payout"] == 1),
                      "sum_px": round(sum(r["px"] for r in v), 3),
                      "sum_fair": round(sum(r["fair"] for r in fair), 3),
                      "sum_payout_fair_rows": sum(r["payout"] for r in fair),
                      "n_fair": len(fair)}
        return out

    # --------------------------------------------------------- backfill
    def backfill(self) -> None:
        """A 1-in-backfill_sample sample of the last backfill_hours of
        prints, newest first, one minute at a time, at the lowest priority
        of the signed-read budget."""
        if self.backfill_hours <= 0:
            return
        end = self.since
        floor = end - timedelta(hours=self.backfill_hours)
        empty = 0
        while not self.stop and end > floor:
            start = end - timedelta(minutes=1)
            try:
                rows = self.fetch_window(start, end)
            except Exception as exc:                      # noqa: BLE001
                self.errors["backfill"] = f"{type(exc).__name__}:{exc}"[:160]
                self.sleep(5)
                continue
            if self.notes.get("endTime_ignored"):
                self.log("COMBO_STUDY_BACKFILL", status="stopped",
                         why="endTime_ignored_by_venue")
                return
            empty = 0 if rows else empty + 1
            if empty >= 120:                  # two hours of nothing: history
                break                         # ends here
            for t in rows:
                if in_sample(str(t.get("tradeId") or ""),
                             self.backfill_sample):
                    self._ingest(t, live=False)
            self.notes["backfill_reached"] = _iso(start)
            end = start
        self.log("COMBO_STUDY_BACKFILL", status="done",
                 reached=self.notes.get("backfill_reached"),
                 backfilled=self.agg["backfilled"])

    def summary(self) -> Dict[str, Any]:
        a = dict(self.agg)
        n = a["priced"]
        a["mean_margin"] = round(a["margin_sum"] / n, 4) if n else None
        a["mean_margin_after_maker_fee"] = (round(a["margin_net_sum"] / n, 4)
                                            if n else None)
        a["mean_mid_lag_s"] = round(a["mid_lag_s_sum"] / n, 1) if n else None
        a["pending_settlement"] = len(self.pending)
        a["settled"] = len(self.settled)
        a["settled_maker"] = self.settled_summary()
        a["errors"] = dict(self.errors)
        a["notes"] = dict(self.notes)
        a["label"] = ("READ-ONLY combo study of OTHER accounts' prints; "
                      "nothing quoted or traded; not realized P&L")
        return a

    def run(self) -> None:
        last_sum = last_settle = 0.0
        while not self.stop:
            try:
                self.poll_rfqs()
                self.poll_trades()
                if self.clock() - last_settle > 120:
                    self.settle_pass()
                    last_settle = self.clock()
                if self.clock() - last_sum > self.summary_s:
                    self.log("COMBO_STUDY_SUMMARY", **self.summary())
                    last_sum = self.clock()
            except Exception as exc:                      # noqa: BLE001
                # never let one bad read end the study
                self.errors["loop"] = f"{type(exc).__name__}:{exc}"[:160]
            time.sleep(self.poll_s)

    def _run_backfill(self) -> None:
        self._tl.backfill = True
        try:
            self.backfill()
        except Exception as exc:                          # noqa: BLE001
            self.errors["backfill"] = f"{type(exc).__name__}:{exc}"[:160]

    def start(self):
        th = threading.Thread(target=self.run, daemon=True,
                              name="combo-study")
        th.start()
        threading.Thread(target=self._run_backfill, daemon=True,
                         name="combo-study-backfill").start()
        return th
