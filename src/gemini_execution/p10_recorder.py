"""GEMINI_P10_RECORDER_V1 -- persistent paired book+trade capture for the
frozen decoder. READ-ONLY, ADDITIVE, ISOLATED.

WHY THIS EXISTS. The P10 decoder needs a book from the same moment as a print.
That capture has been running as a background process inside an interactive
session, and those processes are reaped when the session's turn ends: three
recorders died simultaneously at 23:32Z and again at 23:52Z on 2026-09-22,
silently, and the gate's sample stopped growing without any error. A gate that
can only collect while a human is watching is a collection failure dressed as
an evidence failure.

WHAT IT IS NOT. It places nothing, signs nothing, reads no credential and
constructs no session. Two unauthenticated public GETs per symbol per cycle.
It writes to its OWN directory and never touches the cohort evidence streams,
so the existing aggregated-flow output is byte-identical with this running or
stopped.

THE ROW FORMAT IS DELIBERATELY IDENTICAL to `analysis/gemini/p10a_capture.py`,
field for field, so `p10b_decode`, `p10e_audit_decode`, `p10h_gate` and the
audit harness consume this artifact with NO transformation. A new schema here
would mean a new decoder, and the decoder is the thing being frozen.

FRESHNESS IS BUDGETED, NOT HOPED FOR. Median book age is about
`n_symbols x 2 / rate`, so each cohort runs its own loop with its own token
bucket and its own small symbol set. BTC15m is served FIRST and the hourly
strike ladder is excluded outright: on 2026-09-22 the hourly recorder carried a
3.24s median book age against BTC15m's 1.32s and returned 8 decoded rows to
BTC5m's 55, i.e. it spent the freshness budget and bought almost nothing.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

LABEL = "GEMINI_P10_RECORDER_V1"
BASE = "https://api.gemini.com"

#: requests per second PER COHORT LOOP. Each loop owns its bucket, which is
#: why two cohorts are two loops rather than one wider loop: widening trades
#: book freshness for breadth at a fixed rate, a second bucket does not.
DEFAULT_RATE = 4.0

#: served in this order, and nothing else is served at all. The hourly ladder
#: is absent BY DECISION, not by omission.
COHORTS: Tuple[Tuple[str, str, int], ...] = (
    ("btc15m", "BTC15M", 3),
    ("btc5m", "BTC05M", 3),
)

#: a book older than this cannot be used by the decoder anyway, so a cycle
#: that cannot beat it is recorded as degraded rather than written as if fresh.
MAX_USEFUL_BOOK_AGE_S = 5.0


class _Bucket:
    """One cohort's rate limit. Deliberately not shared."""

    def __init__(self, rate: float) -> None:
        self.rate = float(rate)
        self._next = 0.0

    def take(self) -> None:
        now = time.time()
        t = max(now, self._next)
        self._next = t + 1.0 / self.rate
        d = t - time.time()
        if d > 0:
            time.sleep(d)


def _get(path: str, bucket: _Bucket, tries: int = 2):
    """(payload, t_sent, t_recv). Both clocks are the point. No credential."""
    for i in range(tries):
        bucket.take()
        t0 = time.time()
        try:
            with urllib.request.urlopen(BASE + path, timeout=15) as r:
                return json.load(r), t0, time.time()
        except Exception:                                      # noqa: BLE001
            if i == tries - 1:
                return None, t0, time.time()
    return None, 0.0, 0.0


def out_dir(state_root: str) -> str:
    """P10 lives in its OWN directory. It is never a cohort evidence path."""
    return os.path.join(state_root, "p10")


def out_path(state_root: str, cohort: str, now: Optional[float] = None) -> str:
    d = os.path.join(out_dir(state_root), cohort)
    t = time.gmtime(now if now is not None else time.time())
    return os.path.join(d, "p10_%s_%s.jsonl" % (cohort,
                                                time.strftime("%Y%m%d", t)))


def load_seen(state_root: str, cohort: str) -> set:
    """Rebuild the (symbol, tid) index FROM THE RAW ROWS on restart.

    The records are their own index. A side-car index can disagree with the
    data it describes; re-reading cannot. Every file for the cohort is scanned,
    so a restart mid-day resumes without re-writing a single print.
    """
    seen = set()
    d = os.path.join(out_dir(state_root), cohort)
    if not os.path.isdir(d):
        return seen
    for name in sorted(os.listdir(d)):
        if not name.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(d, name)) as fh:
                for ln in fh:
                    if '"trade"' not in ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except Exception:                          # noqa: BLE001
                        continue
                    if r.get("kind") == "trade" and r.get("tid") is not None:
                        seen.add((r.get("symbol"), r.get("tid")))
        except OSError:
            continue
    return seen


def discover(prefix: str, n_symbols: int, bucket: _Bucket) -> List[Dict[str, Any]]:
    """The nearest live contracts of ONE family. Never another family."""
    d, _a, _b = _get("/v1/prediction-markets/events?limit=60&category=Crypto",
                     bucket, tries=3)
    out = []
    for e in ((d or {}).get("data") or []):
        if str(e.get("status")) != "active":
            continue
        for c in (e.get("contracts") or []):
            sym = c.get("instrumentSymbol")
            if not sym or c.get("status") != "active":
                continue
            core = sym.split("-")[1] if "-" in sym else sym
            if not core.startswith(prefix):
                continue
            out.append({"ticker": e.get("ticker"), "symbol": sym,
                        "expiry": c.get("expiryDate")})
    #: soonest expiry first: that is where the flow is, and a far-dated
    #: contract of the same family prints almost nothing.
    out.sort(key=lambda r: str(r.get("expiry") or "~"))
    return out[:n_symbols]


def _poll_once(sym: Dict[str, Any], bucket: _Bucket, seen: set,
               poll: List[int]) -> Tuple[List[dict], float]:
    """One symbol: a book row and its new trade rows. Returns (rows, rtt)."""
    rows: List[dict] = []
    ev, t0, t1 = _get("/v1/prediction-markets/events/%s" % sym["ticker"],
                      bucket)
    poll[0] += 1
    book_poll = poll[0]
    for c in ((ev or {}).get("contracts") or []):
        if c.get("instrumentSymbol") != sym["symbol"]:
            continue
        pr = c.get("prices") or {}
        rows.append({
            "kind": "book", "poll_id": book_poll, "symbol": sym["symbol"],
            "category": "Crypto", "subcategory": None,
            "t_sent": t0, "t_recv": t1, "rtt": t1 - t0,
            "bid": pr.get("bestBid"), "ask": pr.get("bestAsk"),
            "last": pr.get("lastTradePrice"),
            "buy_yes": (pr.get("buy") or {}).get("yes"),
            "buy_no": (pr.get("buy") or {}).get("no"),
            "sell_yes": (pr.get("sell") or {}).get("yes"),
            "sell_no": (pr.get("sell") or {}).get("no"),
            "expiry": c.get("expiryDate"), "start": c.get("effectiveDate"),
            "resolution": c.get("resolutionSide")})
    tp, t2, t3 = _get("/v1/trades/%s?limit_trades=50" % sym["symbol"], bucket)
    poll[0] += 1
    trade_poll = poll[0]
    if isinstance(tp, list):
        for rank, t in enumerate(tp):
            key = (sym["symbol"], t.get("tid"))
            if t.get("tid") is None or key in seen:
                continue
            seen.add(key)
            rows.append({
                "kind": "trade", "poll_id": trade_poll,
                "rank_in_response": rank, "n_in_response": len(tp),
                "new_in_response": None, "symbol": sym["symbol"],
                "category": "Crypto", "subcategory": None,
                "tid": t.get("tid"), "t": t.get("timestamp"),
                "tms": t.get("timestampms"), "price": t.get("price"),
                "amount": t.get("amount"), "type": t.get("type"),
                "t_sent": t2, "t_recv": t3, "rtt": t3 - t2,
                "first_seen": time.time()})
    return rows, (t1 - t0)


def run_cohort_cycle(cohort: str, prefix: str, n_symbols: int, *,
                     state_root: str, bucket: _Bucket, seen: set,
                     poll: List[int],
                     syms: Optional[Sequence[dict]] = None) -> Dict[str, Any]:
    """ONE pass over one cohort's symbols. Returns a health record.

    Separated from the loop so a test can drive it without a scheduler.
    """
    if not syms:
        syms = discover(prefix, n_symbols, bucket)
    path = out_path(state_root, cohort)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n_book = n_trade = 0
    rtts: List[float] = []
    started = time.time()
    with open(path, "a") as fh:
        for s in syms:
            rows, rtt = _poll_once(s, bucket, seen, poll)
            rtts.append(rtt)
            for r in rows:
                fh.write(json.dumps(r) + "\n")
                n_book += (r["kind"] == "book")
                n_trade += (r["kind"] == "trade")
        elapsed = time.time() - started
        #: THE FRESHNESS RECORD. Median book age is about one full cycle of
        #: the symbol set, so it is measured here and written beside the data
        #: rather than inferred later. A cycle that cannot beat the decoder's
        #: staleness window is marked degraded IN THE ARTIFACT.
        est_age = elapsed / 2.0 if syms else None
        health = {
            "kind": "p10_health", "label": LABEL, "cohort": cohort,
            "t": time.time(), "symbols": [s["symbol"] for s in syms],
            "n_symbols": len(syms), "cycle_s": elapsed,
            "est_median_book_age_s": est_age,
            "rtt_p50": (sorted(rtts)[len(rtts) // 2] if rtts else None),
            "rtt_max": (max(rtts) if rtts else None),
            "book_rows": n_book, "trade_rows": n_trade,
            "seen_keys": len(seen),
            "degraded": (est_age is not None
                         and est_age > MAX_USEFUL_BOOK_AGE_S),
            "mutations_sent": 0,
            "labels": ("READ-ONLY public GETs. No credential, no session, no "
                       "order path. Rows are schema-identical to "
                       "p10a_capture.py so the frozen decoder consumes them "
                       "with no transformation."),
        }
        fh.write(json.dumps(health) + "\n")
    return health
