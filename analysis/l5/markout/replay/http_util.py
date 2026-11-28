"""Polite public HTTP helper: <=4 req/s, backoff on 429/5xx, on-disk cache.
Only public Polymarket international endpoints; nothing private is sent."""
import gzip, hashlib, json, os, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
os.makedirs(CACHE, exist_ok=True)
MIN_GAP = 0.26
_last = [0.0]
ALLOWED = ("https://gamma-api.polymarket.com/", "https://clob.polymarket.com/")


def get(url, use_cache=True):
    assert url.startswith(ALLOWED), url
    key = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".json")
    if use_cache and os.path.exists(key):
        with open(key) as f:
            return json.load(f)["body"]
    delay = 1.0
    for attempt in range(7):
        wait = MIN_GAP - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": "python-client/1.0", "Accept": "application/json",
            "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                body = json.loads(raw)
            with open(key, "w") as f:
                json.dump({"url": url, "t": time.time(), "body": body}, f)
            return body
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(delay); delay = min(delay * 2, 30); continue
            if e.code in (400, 404, 422):
                return None
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(delay); delay = min(delay * 2, 30)
    raise RuntimeError("gave up: " + url)
