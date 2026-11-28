"""NO-CREDENTIALS DRY RUN of the deployed engine code against live public data.
Real: venue live events, books, market specs, international resolver and books.
Fake: account (empty), order placement (recorded, never sent). No Transport is
ever constructed, so nothing here CAN reach the venue's order API."""
import json, os, sys, tempfile, time, urllib.request
ALLOW = sys.argv[1]
os.environ["PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS"] = ALLOW
os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
os.environ["PMUS_TWO_SIDED_RN1"] = "1"
os.environ["PMUS_TWO_SIDED_MAX_MARKETS"] = "2"
os.environ["PMUS_TWO_SIDED_OPEN_OFFSET_PX"] = "0"
os.environ["PMUS_TWO_SIDED_DEPTH_TICKS"] = "1"
os.environ["PMUS_TWO_SIDED_REWARD_FIRST"] = "0"
os.environ["PMUS_TWO_SIDED_OPEN_EV_GATE"] = "1"
os.environ.pop("PMUS_TWO_SIDED_SUBMISSION_LOCK", None)
for k in ("PMUS_RN1_QTY","PMUS_RN1_PAIR_CAP"): os.environ.pop(k, None)
sys.path.insert(0, ".")
from decimal import Decimal as D
from src import pmus_sports_two_sided as M
from src import pmus_sports_xv as XV
from src import pmus_rn1_replica as RN1
from src import pmus_q1_executor_v4 as q1
from src import pmus_spring_gateway_candidates as gw
assert RN1.ON and M.OPEN_EV_GATE, (RN1.ON, M.OPEN_EV_GATE)
G = "https://gateway.polymarket.us"
def pub(path):
    r = urllib.request.Request(G + path, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=15) as f:
        return json.load(f)
class DryVenue(M.Venue):
    def __init__(self):
        self.q1, self.gw = q1, gw
        self._spec = {}; self.states = {}; self.orders = {}; self.n = 0; self.sent = []
    def gget(self, path): return pub(path)
    def account(self): return {"positions": {}, "open_orders": []}
    def place(self, slug, side, px, qty, stage, post_only=True):
        self.n += 1; oid = "DRY%d" % self.n
        self.orders[oid] = {"slug": slug, "side": side, "px": px, "qty": qty, "open": True}
        self.sent.append((time.strftime("%H:%M:%S", time.gmtime()), slug, side, str(px), str(qty), stage, post_only))
        return oid
    def status(self, oid): return {"qty": D(0), "px": None, "open": self.orders[oid]["open"], "state": ""}
    def cancel(self, oid): self.orders[oid]["open"] = False; return True
    def cancel_fast(self, oid, slug):
        self.cancel(oid); return self.status(oid)
logs = []
M.log = lambda kind, **kw: logs.append((kind, kw))
v = DryVenue()
g = XV.IntlGuard(v.gget, XV.gamma_get, margin_ticks=1, max_age_s=20.0, require=True)
v.guard = g
import pathlib
eng = M.Engine(v, M.Book(pathlib.Path(tempfile.mkdtemp()) / "book.json"))
eng.open_gate_stats = None
def feed():
    for slug, tok in list(g.resolved.items()):
        if not tok: continue
        r = urllib.request.Request("https://clob.polymarket.com/book?token_id=" + tok, headers={"User-Agent": "research-client"})
        bk = json.load(urllib.request.urlopen(r, timeout=15))
        g.intl.ingest({"event_type": "book", "asset_id": tok, "bids": bk.get("bids") or [], "asks": bk.get("asks") or []})
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 4
for i in range(rounds):
    eng.select(v.account())
    while g._pending:                       # resolve synchronously (no thread)
        g._resolve_one(g._pending.pop(0))
    g.track(eng.active())
    while g._pending:
        g._resolve_one(g._pending.pop(0))
    feed()
    for slug in list(eng.active()):
        eng.step_market(slug)
    time.sleep(4)
print("allowlist:", ALLOW, "| safeguards:", json.dumps(M.safeguards_cfg()["min_touch_qty"]), "| rn1:", json.dumps(RN1.cfg()))
print("tracked markets:", eng.active())
print("resolved intl:", {s: bool(t) for s, t in g.resolved.items() if s in eng.active()})
print("would-be orders (never sent):")
for o in v.sent: print("   ", o)
print("open at end:", [(o["slug"], o["side"], str(o["px"]), str(o["qty"])) for o in v.orders.values() if o["open"]])
kinds = {}
for k, kw in logs: kinds[k] = kinds.get(k, 0) + 1
print("log kinds:", kinds)
for k, kw in logs:
    if k == "RN1_QUOTE":
        print("    RN1_QUOTE", kw.get("slug"), "bid/ask", kw.get("bid"), kw.get("ask"), "block", kw.get("block"), "want", kw.get("want"), "why", kw.get("why"))
        for sd, ev in (kw.get("edge") or {}).items():
            print("        edge", sd, {x: ev.get(x) for x in ("role","fair","px","adverse","trade_edge","risk","net_edge","why")})
    if k.startswith("place_refused") or k in ("select", "selected", "open_gate_block", "late_cutoff", "skip_foreign_exposure", "market_error"):
        print("   ", k, {a: str(b)[:90] for a, b in kw.items() if a in ("slug", "reason", "why", "side", "stage", "err")})
