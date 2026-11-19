"""PHASE A -- READ-ONLY measurement of PMUS BTC5m maker fill quality.

OWNER-AUTHORIZED 2026-11-17: read-only validation only. This module
places NO orders, needs NO credentials, writes nothing outside its own
tape, and touches no executor, service or ceiling. It is a collector.

WHAT IT RECORDS, and why each field is needed:

  the full book, both sides, with per-level resting quantity
      -> queue depth ahead of a hypothetical clip AT INSERTION. A price
         touching our rung is NOT a fill; the queue ahead must be
         consumed first, and that quantity is only knowable at the
         moment we would have inserted.
  cumulative shares_traded, with last trade price
      -> traded volume, differenced between samples, is the only
         observable that can actually consume a queue. Attributed to a
         price level only when the last trade price identifies it.
  best bid / best ask every sample
      -> the price path, from which "did this side reach X" is decided.
  settlement price after close
      -> whether a leg that filled would have won. Read from
         /v1/markets/{slug}/bbo `settlementPx`, which is where it lives
         (NOT /v1/market/slug/{slug}, which does not carry it).

SAMPLING CADENCE is deliberately uneven: every second for the first 30
seconds, then every 5. The first 30 seconds are where 0x8dxd entered
(first fill at t+7s median) and where our straddle already rests, so
queue depth there is the measurement that matters most.

ONLY WINDOWS WATCHED FROM NEAR THEIR OPEN ARE RECORDED. A window picked
up late is not a weaker sample, it is a DIFFERENT and systematically
worse one: joining the book after the price has already moved fills only
the side the move is running through, which is adverse selection in its
purest form. Measured on five such windows -- insertion at a median of
t+136s -- the lone filled leg won 0 of 4 times. Recording them would
bias every quantity here, so a window is skipped unless it is picked up
within `MAX_JOIN_T` seconds of its open, and the 200-window target
counts clean windows only.

MARKET-CREATION LATENCY IS ITSELF A MEASUREMENT, AND IT MUST NOT BE
CONFLATED WITH OUR OWN RETRY COST. The venue answers an uncreated market
with HTTP 404. The first version retried every failure three times with
backoff, so each miss cost about five seconds and `first_available_t`
read 125s and 166s on windows where the real latency was unknown -- the
number was measuring our own politeness. A 404 is now a DEFINITE answer
and is never retried, so the poll costs one request and the latency is
the venue's.

Equally, WHEN WE STARTED POLLING is a different thing from WHEN THE VENUE
SERVED THE MARKET, and conflating them would discard every window for the
venue's own latency. Each sample therefore carries `join_t` (how far into
the window we picked it up, which decides whether the sample is unbiased)
alongside `t` (the instant of the sample itself).
So there is a window in which NO order can be inserted at all, and the
queue we would join is whatever exists at the first queryable instant --
not at t=0. The collector polls at 1 Hz from t=0 and records
`first_available_t` per window, because that instant is the real
insertion moment and 0x8dxd's median first fill was t+7s on a venue that
evidently did not make it wait.

SETTLEMENT IS READ ON A BACKGROUND THREAD, and that is not an
optimisation. The first version polled settlement inline: the venue
publishes it a little after close, so the poll slept up to 120 seconds
and the NEXT window's first 30 seconds were already gone by the time
sampling resumed. Measured on the first two windows -- 0 samples below
t=30 in either, one starting at t=161 and the next at t=50. Blocking the
sampler to read a settlement destroys the one measurement this collector
exists for, so settlement now runs off the critical path.

SIDE CONVENTION. The venue quotes ONE side; the complement is priced
1 - p. So a DOWN bid at X is the same order as an UP offer at 1-X, and
the queue ahead of a DOWN rung at X is the resting UP OFFER quantity at
1-X. Both are recorded so neither has to be reconstructed later.

RESUMABLE. Appends, and records the window slug on every row, so a
container restart loses at most the window in flight. Re-running
continues the same tape rather than starting a new one.
"""
import collections, json, os, sys, threading, time, urllib.error
import urllib.request, datetime

OUT = os.environ.get("PHASE_A_OUT") or \
    "/home/user/kalshi-arb-bot/data/l5/phase_a/btc5m_books.jsonl"
SUMMARY = os.path.splitext(OUT)[0] + "_windows.jsonl"
TARGET_WINDOWS = int(os.environ.get("PHASE_A_WINDOWS") or 200)
BASE = "https://gateway.polymarket.us"
RUNGS = (0.40, 0.42, 0.47)
#: a window picked up later than this is skipped entirely; see the header
MAX_JOIN_T = 30


NOT_FOUND = {"__not_found__": True}

#: WHY a poll failed, counted per window. Without this the collector
#: cannot tell "the venue has not created the market" from "this
#: container lost the network", and I guessed wrong once already: a
#: window showed 75 failed polls and first availability at t+299, which I
#: read as venue latency and then as rate limiting. Neither: 15
#: consecutive 1 Hz polls of a live window all returned 200, and the
#: container was being restarted at the time. A failure reason is cheap;
#: a wrong inference is not.
FAILS = collections.Counter()


def get(url, tries=3):
    """A 404 is a DEFINITE answer -- the market does not exist yet -- and
    retrying it would charge our own backoff to the venue's latency."""
    last = None
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "research-client"}),
                timeout=12))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                FAILS["http_404"] += 1
                return NOT_FOUND
            last = "http_%d" % exc.code
            time.sleep(0.4 * (i + 1))
        except Exception as exc:
            last = type(exc).__name__
            time.sleep(0.4 * (i + 1))
    FAILS[last or "unknown"] += 1
    return None


def px(o):
    try:
        return float(o["px"]["value"])
    except Exception:
        return None


def val(d, *k):
    cur = d
    for x in k:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(x)
    if isinstance(cur, dict):
        cur = cur.get("value")
    try:
        return float(cur)
    except Exception:
        return None


def current_window():
    n = datetime.datetime.now(datetime.timezone.utc)
    n = n.replace(minute=(n.minute // 5) * 5, second=0, microsecond=0)
    return "cpc-btc-updown-5m-" + n.strftime("%Y-%m-%d-%H%Mz"), int(n.timestamp())


def sample(slug, epoch, fh, join_t=None):
    b = get("%s/v1/markets/%s/book" % (BASE, slug))
    q = get("%s/v1/markets/%s/bbo" % (BASE, slug))
    if not (b and isinstance(b.get("marketData"), dict)
            and q and isinstance(q.get("marketData"), dict)):
        return False
    md, qd = b["marketData"], q["marketData"]
    bids = [[px(o), float(o.get("qty") or 0)] for o in (md.get("bids") or [])]
    offs = [[px(o), float(o.get("qty") or 0)] for o in (md.get("offers") or [])]
    rec = {
        "slug": slug, "epoch": epoch, "ts": int(time.time()),
        "t": int(time.time()) - epoch, "join_t": join_t,
        "bids": [x for x in bids if x[0] is not None],
        "offers": [x for x in offs if x[0] is not None],
        "best_bid": val(qd, "bestBid"), "best_ask": val(qd, "bestAsk"),
        "last": val(qd, "lastTradePx"), "cur": val(qd, "currentPx"),
        "shares_traded": val(qd, "sharesTraded"),
        "open_interest": val(qd, "openInterest"),
        "settlement_px": val(qd, "settlementPx"),
        "bid_depth": qd.get("bidDepth"), "ask_depth": qd.get("askDepth"),
    }
    fh.write(json.dumps(rec) + "\n")
    fh.flush()
    return True


def settle_read(slug, epoch):
    """After close, read settlement. Retried over several minutes because
    the venue publishes it a little after the window ends. Called on a
    background thread so it never delays sampling."""
    out = {"slug": slug, "epoch": epoch, "settlement_px": None,
           "read_utc": None, "attempts": 0}
    for i in range(10):
        q = get("%s/v1/markets/%s/bbo" % (BASE, slug))
        out["attempts"] = i + 1
        s = val((q or {}).get("marketData") or {}, "settlementPx")
        #: 0.0 is a real settlement (the UP side lost), so "is not None"
        #: is the test, never truthiness. But the venue also returns 0.0
        #: BEFORE settling, so a zero is only trusted once the window has
        #: been closed for a while and the book is gone.
        if s is not None and (s > 0 or i >= 4):
            out["settlement_px"] = s
            out["read_utc"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat()
            break
        time.sleep(30)
    return out


def _settle_bg(slug, epoch, sfh, lock, first_t=None, unavailable=0,
               fail_reasons=None):
    try:
        out = settle_read(slug, epoch)
    except Exception as exc:
        out = {"slug": slug, "epoch": epoch, "settlement_px": None,
               "error": "%s: %s" % (type(exc).__name__, exc)}
    #: the real insertion moment, and how long the venue refused to serve
    #: the market at all
    out["first_available_t"] = first_t
    out["unavailable_polls"] = unavailable
    #: 404 means the venue had not created the market; anything else is
    #: OUR problem and must never be reported as venue latency.
    out["fail_reasons"] = fail_reasons or {}
    with lock:
        sfh.write(json.dumps(out) + "\n")
        sfh.flush()
    print("    settled %s -> %s" % (slug, out.get("settlement_px")), flush=True)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    lock = threading.Lock()
    done = set()
    if os.path.exists(SUMMARY):
        for line in open(SUMMARY):
            try:
                done.add(json.loads(line)["slug"])
            except Exception:
                pass
    print("phase A: target %d windows, %d already recorded"
          % (TARGET_WINDOWS, len(done)), flush=True)
    fh = open(OUT, "a")
    sfh = open(SUMMARY, "a")
    while len(done) < TARGET_WINDOWS:
        slug, epoch = current_window()
        if slug in done:
            time.sleep(5)
            continue
        join_t = int(time.time()) - epoch
        if join_t > MAX_JOIN_T:
            #: wait out the rest of this window rather than record a
            #: late-join sample that would bias the measurement
            print("skip %s (joined at t+%ds > %ds)"
                  % (slug, join_t, MAX_JOIN_T), flush=True)
            time.sleep(max(2, 300 - join_t + 1))
            continue
        ok_any = False
        first_t = None
        unavailable = 0
        FAILS.clear()
        #: first 30s at 1Hz, then 5s, until the window closes
        while True:
            t = int(time.time()) - epoch
            if t >= 300:
                break
            got = sample(slug, epoch, fh, join_t)
            if got and first_t is None:
                first_t = t
            if not got:
                unavailable += 1
            ok_any = got or ok_any
            time.sleep(1.0 if t < 30 else 5.0)
        if ok_any:
            done.add(slug)
            #: OFF THE CRITICAL PATH. The sampler must be free to start the
            #: next window within the second; settlement arrives when it
            #: arrives. `lock` serialises the summary writes only.
            why = dict(FAILS)
            threading.Thread(target=_settle_bg,
                             args=(slug, epoch, sfh, lock, first_t,
                                   unavailable, why),
                             daemon=False).start()
            print("[%3d/%d] %s first_available_t=%s (%d failed polls: %s)"
                  % (len(done), TARGET_WINDOWS, slug, first_t, unavailable,
                     why or "none"), flush=True)
        else:
            #: never silently count a window we could not read
            print("skip (unreadable) %s" % slug, flush=True)
            time.sleep(5)
    fh.close()
    sfh.close()
    print("phase A complete: %d windows" % len(done), flush=True)


if __name__ == "__main__":
    #: CLAUDE.md: "Read-only collectors/recorders should self-recover and
    #: run 24/7." A crash must not end a 200-window capture, and the tape
    #: is append-only and resumable, so the loop simply re-enters. (A
    #: CONTAINER restart still kills it and has done so twice; that needs
    #: an external relaunch, not a wider except.)
    while True:
        try:
            main()
            break
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print("collector crashed: %s: %s -- resuming in 10s"
                  % (type(exc).__name__, exc), flush=True)
            time.sleep(10)
