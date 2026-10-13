"""Continuous LIVE SHADOW of the maker engine, driven by the collector's tape.

OWNER: "I want the existing maker engine operating continuously in LIVE SHADOW
against synchronized real market data... Find the least disruptive way to run
our maker engine alongside it. Preserve the collector's existing data and avoid
new costs wherever possible."

THE DESIGN IS CHOSEN TO BE INCAPABLE OF DISTURBING THE COLLECTOR
-----------------------------------------------------------------
This process makes NO NETWORK REQUEST OF ANY KIND. It tails the append-only
files `l5-book-shadow` is already writing under `BOOK_SHADOW_ROOT` and replays
them through `MakerEngine` in arrival order. It cannot add request load, cannot
touch the venue's rate limit (the collector has held 0 HTTP 429s for 6.5 days,
and nothing here can change that), and cannot consume a credential because it
holds none.

It is also append-only itself: it writes under its own `shadow/` subdirectory
and never opens a collector file for writing. A guard asserts this.

WHY THIS EXISTS RATHER THAN ANOTHER BACKTEST
---------------------------------------------
`analysis/l5/adverse_selection_split.json` measured the thing that invalidates
the offline work: scored with one identical statistic, L5's REAL fills move
-$0.0021/contract against us over the next 10s, while our MODELLED fills move
-$0.0408 -- a fill-model artifact of $0.0387/contract (t = 59.5) that is
almost exactly the engine's entire measured loss of 3.2-4.4c/contract. The
offline fill rule, not the market, produces the negative result. Only real
book state can settle it, which is what this records.

WHAT IT RECORDS, AND THE DISTINCTION IT NEVER COLLAPSES
--------------------------------------------------------
Three quantities per opportunity, never merged (owner item 6):
    price_eligible  the book/print reached our resting price
    queue_possible  what the stated queue assumption would allow
    modelled_fill   what we book -- MODELLED, never an execution

No order is ever submitted. There is no transport in this module, no client,
and no function that could place one.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:
    from src.l5_behavior_state import UP, DOWN, L5State
    from src.l5_maker_engine import MakerEngine, TICK
except ModuleNotFoundError:
    from l5_behavior_state import UP, DOWN, L5State
    from l5_maker_engine import MakerEngine, TICK

SCHEMA = "l5_maker_shadow/1"
ROLE = "MAKER_ENGINE_SHADOW_NO_ORDERS"

ENV_ROOT = "BOOK_SHADOW_ROOT"
ENV_OUT = "MAKER_SHADOW_OUT"
ENV_QUEUE = "MAKER_SHADOW_QUEUE_PARTICIPATION"
ENV_TICKS = "MAKER_SHADOW_REPRICE_TICKS"
ENV_SKEW = "MAKER_SHADOW_SIZE_SKEW"
ENV_CAP = "MAKER_SHADOW_MAX_USD"
ENV_POLL = "MAKER_SHADOW_POLL_S"
ENV_HB = "MAKER_SHADOW_HEARTBEAT_S"
ENV_MAXMK = "MAKER_SHADOW_MAX_MARKETS"
ENV_MAXRSS = "MAKER_SHADOW_MAX_RSS_MB"

#: A 15-minute market is dead to us once it has stopped producing rows. The
#: collector tracks ~35 live markets; this keeps a generous multiple and then
#: evicts, because an unbounded dict in a process sharing a container with the
#: collector is how an in-process shadow starved this very collector once
#: before. Memory is a SAFETY property here, not a tidiness one.
DEFAULT_MAX_MARKETS = 400
#: if we exceed this we EXIT, and the supervising loop restarts us clean. The
#: collector must never die because the shadow grew.
DEFAULT_MAX_RSS_MB = 300.0
DEFAULT_HEARTBEAT_S = 60.0

#: any import of these would mean an order could be placed from this process
FORBIDDEN = ("src.l5_live_execution", "src.pmus_micro_executor",
             "src.pmus_order_client")


class CollectorWriteViolation(RuntimeError):
    """Raised if this process would ever write a collector-owned path."""


def boot_gate(root: Path, out: Path) -> Dict[str, Any]:
    """Prove, before anything runs, that this process cannot act or clobber."""
    import importlib.util
    importable = [m for m in FORBIDDEN if importlib.util.find_spec(m) is not None]
    out = out.resolve()
    root = root.resolve()
    if out == root or root in out.parents and out.name not in ("shadow",):
        # the output must be a dedicated subdirectory, never the tape itself
        if not str(out).startswith(str(root / "shadow")):
            raise CollectorWriteViolation(
                f"output {out} is not under {root / 'shadow'}")
    return {
        "schema": SCHEMA, "role": ROLE,
        "gates": {
            "order_modules_importable": importable,
            "mutation_incapable": not importable,
            "writes_only_under": str(root / "shadow"),
            "network_requests_issued": 0,
            "credentials_held": [],
        },
        "ok": not importable,
    }


class TapeTailer:
    """Follows the collector's append-only parts without ever writing them.

    THE LAYOUT IS THE COLLECTOR'S, VERIFIED AGAINST ITS SOURCE, not guessed.
    `l5_book_shadow.TapeWriter` writes every kind into ONE hourly part:

        {root}/raw/tape_{YYYYMMDDTHHZ}.jsonl

    It opens "a", writes one line and flushes, so tailing by byte offset is
    safe: a part only ever grows, and a torn final line is simply skipped and
    picked up whole on the next poll.
    """

    #: the collector's default stream name (TapeWriter(root) -> stream="tape")
    STREAM = "tape"

    def __init__(self, root: Path, stream: str = STREAM):
        self.root = Path(root)
        self.stream = stream
        self.offsets: Dict[str, int] = {}

    def files(self) -> List[Path]:
        raw = self.root / "raw"
        if not raw.is_dir():
            return []
        return sorted(raw.glob(f"{self.stream}_*.jsonl"))

    def poll(self) -> Iterator[Dict[str, Any]]:
        """Yield rows appended since the last poll, oldest file first."""
        for f in self.files():
            key = str(f)
            start = self.offsets.get(key, 0)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size <= start:
                continue
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(start)
                data = fh.read()
                self.offsets[key] = start + len(data.encode("utf-8"))
            for line in data.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue            # a torn final line; it reappears whole


def rss_mb() -> float:
    """Our own resident set, from /proc. Returns 0.0 where unavailable."""
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as fh:
            pages = int(fh.read().split()[1])
        return pages * (os.sysconf("SC_PAGE_SIZE") / (1024.0 * 1024.0))
    except Exception:                                   # noqa: BLE001
        return 0.0


class MarketShadow:
    """One market's live engine state, its quotes, and its fill accounting."""

    def __init__(self, slug: str, engine: MakerEngine) -> None:
        self.slug = slug
        self.eng = engine
        self.st = L5State()
        self.cash = 0.0
        self.bin = 0
        self.ref: Optional[float] = None
        self.best: Dict[str, Dict[str, Optional[float]]] = {
            UP: {"bid": None, "ask": None, "bid_sz": None, "ask_sz": None},
            DOWN: {"bid": None, "ask": None, "bid_sz": None, "ask_sz": None},
        }
        self.tiers = {"price_eligible": 0.0, "queue_possible": 0.0,
                      "modelled_fill": 0.0}
        #: fills are WRITTEN to the volume, never retained -- a list here
        #: would grow without bound for the life of the process
        self.n_fills = 0
        self.l5_fills = 0
        self.last_seen = 0.0


def fee_maker(price: float, qty: float) -> float:
    """Our cost for a maker leg: the rebate is income, so a negative cost."""
    return -0.0125 * qty * price * (1.0 - price)


def run(root: Path, out_dir: Path, *, cfg: Dict[str, Any],
        once: bool = False, now=time.time, emit=print,
        gate_advisory: bool = False) -> int:
    """Tail the collector tape and keep a live shadow book per market."""
    gate = boot_gate(root, out_dir)
    emit("MAKER_SHADOW_BOOT " + json.dumps(gate, sort_keys=True))
    if not gate["ok"]:
        # DEFAULT IS REFUSE. The deployed image ships only what this module
        # needs, so the gate passes there; in the monorepo the order path is
        # importable, which is the gate working, not a bug. `gate_advisory`
        # exists so the data path can be smoke-tested locally -- it never
        # makes an order possible, and every record it produces is stamped.
        emit("MAKER_SHADOW_REFUSED order modules importable")
        if not gate_advisory:
            return 64
        emit("MAKER_SHADOW_GATE_ADVISORY proceeding for local test only")
        cfg = dict(cfg)
        cfg["gate"] = "ADVISORY_LOCAL_TEST_NOT_PRODUCTION"
    out_dir.mkdir(parents=True, exist_ok=True)
    tailer = TapeTailer(root)
    shadows: Dict[str, MarketShadow] = {}
    cycles = 0
    rows_seen = 0
    evicted = 0
    max_markets = int(cfg.get("max_markets", DEFAULT_MAX_MARKETS))
    max_rss = float(cfg.get("max_rss_mb", DEFAULT_MAX_RSS_MB))
    hb_every = float(cfg.get("heartbeat_s", DEFAULT_HEARTBEAT_S))
    last_hb = 0.0
    while True:
        cycles += 1
        for row in tailer.poll():
            rows_seen += 1
            _absorb(row, shadows, cfg, out_dir, emit, now())
        evicted += _evict(shadows, max_markets)
        # the collector must never die because we grew. Exiting lets the
        # supervising loop restart us clean; it cannot touch collection.
        rss = rss_mb()
        if max_rss > 0 and rss > max_rss:
            _heartbeat(shadows, cycles, rows_seen, out_dir, emit, now,
                       cfg.get("gate"), evicted, rss)
            emit("MAKER_SHADOW_EXIT_RSS " + json.dumps(
                {"rss_mb": round(rss, 1), "limit_mb": max_rss,
                 "why": "exiting so the supervisor restarts us clean; "
                        "collection is unaffected"}))
            return 0
        t = now()
        if once or (t - last_hb) >= hb_every:
            _heartbeat(shadows, cycles, rows_seen, out_dir, emit, now,
                       cfg.get("gate"), evicted, rss)
            last_hb = t
        if once:
            return 0
        time.sleep(float(cfg.get("poll_s", 2.0)))


def _evict(shadows: Dict[str, "MarketShadow"], cap: int) -> int:
    """Drop the least recently seen markets once over the cap."""
    if len(shadows) <= cap:
        return 0
    order = sorted(shadows.items(), key=lambda kv: kv[1].last_seen)
    drop = len(shadows) - cap
    for slug, _ in order[:drop]:
        shadows.pop(slug, None)
    return drop


def _num(v):
    """The CLOB reports prices and sizes as STRINGS. Parse, never assume."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _absorb(row, shadows, cfg, out_dir, emit, seen_at=0.0) -> None:
    """Dispatch one collector row. Unknown kinds are ignored, not guessed.

    The collector's own envelope carries `kind`; the book payload sits under
    `row["book"]` and the activity payload under `row["raw"]`. Both verified
    against src/l5_book_shadow.py rather than inferred from a sample.
    """
    kind = row.get("kind")
    if kind not in ("book", "l5_activity"):
        return                      # discovery, heartbeat, errors: not ours
    slug = row.get("market")
    outcome = row.get("outcome")
    if not slug or outcome not in (UP, DOWN):
        return
    sh = shadows.get(slug)
    if sh is None:
        sh = shadows[slug] = MarketShadow(slug, MakerEngine(
            max_usd_per_market=float(cfg.get("max_usd", 5.0)),
            size_skew=float(cfg.get("size_skew", 1.1413)),
            reprice_ticks=float(cfg.get("reprice_ticks", 1.0)),
            queue_participation=float(cfg.get("queue", 0.5))))
    sh.last_seen = seen_at
    if kind == "book":
        book = row.get("book")
        if isinstance(book, dict):
            _on_book(sh, book, outcome, row.get("recv_utc"), out_dir, emit)
    else:
        raw = row.get("raw")
        if isinstance(raw, dict) and raw.get("type") == "TRADE":
            _on_print(sh, raw, outcome, row.get("recv_utc"), out_dir, emit)


def _on_book(sh: MarketShadow, book, side, recv, out_dir, emit) -> None:
    bids, asks = book.get("bids") or [], book.get("asks") or []

    def lvl(xs, hi):
        lv = [(_num(x.get("price")), _num(x.get("size")))
              for x in xs if isinstance(x, dict)]
        lv = [(p, q) for p, q in lv if p is not None and q is not None]
        if not lv:
            return None, None
        return max(lv) if hi else min(lv)
    b, bs = lvl(bids, True)
    a, asz = lvl(asks, False)
    sh.best[side] = {"bid": b, "ask": a, "bid_sz": bs, "ask_sz": asz}
    up_mid = None
    if side == UP and b is not None and a is not None:
        up_mid = (b + a) / 2.0
    elif side == DOWN and b is not None and a is not None:
        up_mid = 1.0 - (b + a) / 2.0
    if up_mid is not None:
        sh.ref = up_mid
        acted = sh.eng.manage(sh.st, sh.bin, sh.cash, sh.ref, fee_maker)
        if acted["placed"] or acted["cancelled"]:
            _write(out_dir, "intents", {
                "schema": SCHEMA, "slug": sh.slug, "recv": recv,
                "ref_up": round(sh.ref, 5), "placed": acted["placed"],
                "cancelled": acted["cancelled"], "declined": acted["declined"],
                "residual": sh.st.residual, "cash": round(sh.cash, 4),
                "LABEL": "INTENDED_ORDERS_NOT_SUBMITTED"})
    sh.bin += 1


def _on_print(sh: MarketShadow, raw, side, recv, out_dir, emit) -> None:
    sh.l5_fills += 1
    size, price = _num(raw.get("size")) or 0.0, _num(raw.get("price")) or 0.0
    if size <= 0 or not (0.0 < price < 1.0):
        return
    before = sh.eng.booked_qty
    fills = sh.eng.match(sh.bin, {side: (size, price)})
    sh.bin += 1
    for f in fills:
        sh.cash += f["qty"] * f["price"]
        sh.st.apply([{"side": f["side"], "qty": f["qty"],
                      "price": f["price"]}], sh.bin)
        rec = {
            "schema": SCHEMA, "slug": sh.slug, "recv": recv,
            "side": f["side"], "modelled_qty": round(f["qty"], 6),
            "our_price": f["price"], "print_price": price,
            "print_size": size, "partial": f["partial"],
            "book_at_fill": sh.best.get(f["side"]),
            "residual_after": sh.st.residual,
            "matched_after": sh.st.matched,
            "LABEL": "MODELLED_FILL_NOT_AN_EXECUTION",
        }
        sh.n_fills += 1
        _write(out_dir, "modelled_fills", rec)
    sh.tiers["price_eligible"] = sh.eng.price_eligible_qty
    sh.tiers["queue_possible"] = sh.eng.queue_possible_qty
    sh.tiers["modelled_fill"] = sh.eng.booked_qty
    if sh.eng.booked_qty > before:
        pass


def _write(out_dir: Path, stream: str, obj) -> None:
    d = out_dir / stream
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{stream}_{time.strftime('%Y%m%d', time.gmtime())}.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=True, default=str) + "\n")


def _heartbeat(shadows, cycles, rows_seen, out_dir, emit, now,
               cfg_gate=None, evicted=0, rss=0.0) -> None:
    tot = {"price_eligible": 0.0, "queue_possible": 0.0, "modelled_fill": 0.0}
    for sh in shadows.values():
        for k in tot:
            tot[k] += sh.tiers[k]
    hb = {
        "schema": SCHEMA, "role": ROLE,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now())),
        "cycles": cycles, "tape_rows_consumed": rows_seen,
        "markets_tracked": len(shadows),
        "l5_fills_observed": sum(s.l5_fills for s in shadows.values()),
        "EXECUTION_TIERS_contracts": {k: round(v, 3) for k, v in tot.items()},
        "orders_intended": sum(s.eng.places for s in shadows.values()),
        "modelled_fills_written": sum(s.n_fills for s in shadows.values()),
        "markets_evicted": evicted,
        "rss_mb": round(rss, 1),
        "cancels_intended": sum(s.eng.cancels for s in shadows.values()),
        "network_requests_issued": 0,
        "orders_submitted": 0,
        "LABEL": "SHADOW_ONLY -- modelled fills are not executions",
    }
    if cfg_gate:
        hb["gate"] = cfg_gate
    emit("MAKER_SHADOW_HEARTBEAT " + json.dumps(hb, sort_keys=True))
    _write(out_dir, "heartbeat", hb)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.environ.get(ENV_ROOT, "/state/l5-book-shadow"))
    ap.add_argument("--out", default=os.environ.get(ENV_OUT, ""))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--gate-advisory", action="store_true",
                    help="local smoke test only; records the gate failure")
    a = ap.parse_args(argv)
    root = Path(a.root)
    out = Path(a.out) if a.out else root / "shadow" / "maker_engine"
    cfg = {
        "queue": float(os.environ.get(ENV_QUEUE, "0.5")),
        "reprice_ticks": float(os.environ.get(ENV_TICKS, "1")),
        "size_skew": float(os.environ.get(ENV_SKEW, "1.1413")),
        "max_usd": float(os.environ.get(ENV_CAP, "5")),
        "poll_s": float(os.environ.get(ENV_POLL, "2")),
        "heartbeat_s": float(os.environ.get(ENV_HB, DEFAULT_HEARTBEAT_S)),
        "max_markets": int(os.environ.get(ENV_MAXMK, DEFAULT_MAX_MARKETS)),
        "max_rss_mb": float(os.environ.get(ENV_MAXRSS, DEFAULT_MAX_RSS_MB)),
    }
    return run(root, out, cfg=cfg, once=a.once,
               gate_advisory=a.gate_advisory)


if __name__ == "__main__":
    raise SystemExit(main())
