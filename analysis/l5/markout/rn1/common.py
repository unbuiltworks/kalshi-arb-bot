"""Shared paths/helpers. READ-ONLY public data. HISTORICAL observation of another trader on another venue - not our P&L."""
import datetime as dt, json, os, re, time, urllib.request, threading
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "/home/user/kalshi-arb-bot/analysis/l5/sports_wallets/"
ACT = SRC + "rn1_activity_20261022_20261121.jsonl.gz"
TAK = SRC + "rn1_taker_trades_20261022_20261121.jsonl.gz"
GAMMA_OLD = os.path.join(HERE, "..", "..", "rn1", "gamma_cache.json")   # cache built by fetch_gamma_markets.py (2026-11-21)
GAMMA_NEW = os.path.join(HERE, "gamma_refresh.json")
SAMPLE = os.path.join(HERE, "sample.json")
TAPE_DIR = os.path.join(HERE, "tape"); HIST_DIR = os.path.join(HERE, "hist")
HORIZONS = (5, 30, 120, 300, 900)
_lock = threading.Lock(); _last = [0.0]
def get(url, min_gap=0.25, tries=6):
    """GET json; global spacing of min_gap seconds between requests (<= 4/s); back off on 429/5xx."""
    for a in range(tries):
        with _lock:
            w = _last[0] + min_gap - time.time()
            if w > 0: time.sleep(w)
            _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "research-client"}), timeout=40) as f:
                return json.load(f)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404): return {"_error": e.code}
            time.sleep(5 * (a + 1) if e.code == 429 else 2 * (a + 1))
        except Exception:
            time.sleep(2 * (a + 1))
    return None
def iso(s):
    if not s: return None
    s = s.strip().replace(" ", "T").replace("Z", "+00:00")
    if s.endswith("+00"): s += ":00"
    try: return dt.datetime.fromisoformat(s).timestamp()
    except ValueError: return dt.datetime.fromisoformat(s[:19] + s[-6:]).timestamp()
def game_key(slug):
    m = re.match(r"^(.*?\d{4}-\d{2}-\d{2})", slug or "")
    return m.group(1) if m else (slug or "")
