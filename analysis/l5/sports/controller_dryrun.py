"""NO-CREDENTIALS DRY RUN of the continuous controller (development code)
against live public data, across pre-game, kickoff and in-game.
Real: the venue's event lists, books and market specs, the international
resolver and books. Fake: the account (empty) and order placement (recorded,
never sent). No Transport is constructed, so nothing here can reach the
venue's order API.
FILL MODEL (4th argument "through"; default none): a recorded bid counts as
filled in full, at its own price, as soon as the venue's best price on its
side has moved THROUGH it (best bid below a YES bid; best offer above the
level a NO bid implies). That is the adverse case only -- a bid is never
"filled" by ordinary trading at its level -- so the results are a lower
bound on fills and a worst case on their quality. Nothing is realized.
usage (from a checkout of the sports code):
  controller_dryrun.py <out.jsonl> <until_epoch> <allow_sports_csv> [through]"""
import json, os, sys, tempfile, time, urllib.request, pathlib
OUT, UNTIL, ALLOW = sys.argv[1], float(sys.argv[2]), sys.argv[3]
FILLS = sys.argv[4] if len(sys.argv) > 4 else "none"
ENV = {  # "Option A" of CONTINUOUS_CONTROLLER_20261122.md -- a proposal only
    "PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS": ALLOW, "PMUS_TWO_SIDED_ENABLED": "YES",
    "PMUS_TWO_SIDED_RN1": "1", "PMUS_TWO_SIDED_MAX_MARKETS": "5",
    "PMUS_TWO_SIDED_OPEN_OFFSET_PX": "0", "PMUS_TWO_SIDED_DEPTH_TICKS": "1",
    "PMUS_TWO_SIDED_REWARD_FIRST": "0", "PMUS_TWO_SIDED_OPEN_EV_GATE": "1",
    "PMUS_TWO_SIDED_SLATE": "both", "PMUS_TWO_SIDED_PREGAME_MIN_LEAD_S": "600",
    "PMUS_TWO_SIDED_AT_RISK_BUDGET_USD": "50", "PMUS_TWO_SIDED_TOTAL_USD": "30",
    "PMUS_RN1_PREGAME_QTY": "10", "PMUS_RN1_LIVE_QTY": "2",
    "PMUS_RN1_PER_MARKET_USD": "10", "PMUS_RN1_RESIDUAL_CAP_USD": "8",
    "PMUS_RN1_TOTAL_SPEND_USD": "50", "PMUS_RN1_TOTAL_UNMATCHED_USD": "30",
    "PMUS_TWO_SIDED_VOL_PAUSE_USD": "0.03", "PMUS_TWO_SIDED_VOL_SHRINK_USD": "0.02",
    "PMUS_RN1_RANK_NET": "1"}
# optional overrides for side-by-side runs, e.g.
# DRYRUN_OVERRIDES='{"PMUS_RN1_GAP_HOLD_S": "20"}'
ENV.update(json.loads(os.getenv("DRYRUN_OVERRIDES") or "{}"))
os.environ.update(ENV)
os.environ.pop("PMUS_TWO_SIDED_SUBMISSION_LOCK", None)
for k in ("PMUS_RN1_QTY", "PMUS_RN1_PAIR_CAP"): os.environ.pop(k, None)
sys.path.insert(0, ".")
from decimal import Decimal as D
from src import pmus_sports_two_sided as M
from src import pmus_sports_xv as XV
from src import pmus_rn1_replica as RN1
from src import pmus_q1_executor_v4 as q1
from src import pmus_spring_gateway_candidates as gw
assert RN1.ON and M.OPEN_EV_GATE and RN1.EDGE_GATE and M.slate() == "both"
G = "https://gateway.polymarket.us"
def pub(path):
    r = urllib.request.Request(G + path, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=15) as f:
        return json.load(f)
class DryVenue(M.Venue):
    def __init__(self):
        self.q1, self.gw = q1, gw
        self._spec = {}; self.states = {}; self.orders = {}; self.n = 0
    def gget(self, path): return pub(path)
    def account(self): return {"positions": {}, "open_orders": []}
    def place(self, slug, side, px, qty, stage, post_only=True):
        self.n += 1; oid = "DRY%d" % self.n
        self.orders[oid] = {"slug": slug, "side": side, "px": px, "qty": qty, "open": True}
        emit("DRY_ORDER", slug=slug, side=side, px=px, qty=qty, stage=stage, post_only=post_only,
             label="recorded only; never sent")
        return oid
    _bbo_at = {}
    def _bbo_cached(self, slug):
        hit = self._bbo_at.get(slug)
        if hit and time.time() - hit[0] < 2.0: return hit[1]
        try: b = self.bbo(slug)
        except Exception: b = None
        self._bbo_at[slug] = (time.time(), b)
        return b
    def status(self, oid):
        o = self.orders[oid]
        if o.get("filled"):
            return {"qty": D(str(o["qty"])), "px": D(str(o["px"])), "open": False, "state": "FILLED", "t": o["filled"]}
        if FILLS == "through" and o["open"]:
            b = self._bbo_cached(o["slug"])
            if b and b.get("bid") is not None and b.get("ask") is not None:
                side_bid = D(str(b["bid"])) if o["side"] == "YES" else D("1") - D(str(b["ask"]))
                if side_bid < D(str(o["px"])):
                    o["filled"], o["open"] = time.time(), False
                    emit("DRY_FILL", slug=o["slug"], side=o["side"], px=o["px"], qty=o["qty"],
                         side_bid_now=side_bid, label="modelled: the price moved through the bid")
                    return {"qty": D(str(o["qty"])), "px": D(str(o["px"])), "open": False, "state": "FILLED", "t": o["filled"]}
        return {"qty": D(0), "px": None, "open": o["open"], "state": ""}
    def cancel(self, oid):
        if self.orders[oid]["open"]:
            o = self.orders[oid]; emit("DRY_CANCEL", slug=o["slug"], side=o["side"], px=o["px"])
        self.orders[oid]["open"] = False; return True
    def cancel_fast(self, oid, slug):
        self.cancel(oid); return self.status(oid)
KEEP = {"SELECTED", "RN1_QUOTE", "KICKOFF_HOLD", "KICKOFF_RESUME", "VOL_PAUSE", "RN1_NET_RANK", "RN1_REWARD_RANK",
        "place_refused_at_risk_budget", "released_flat_market", "market_closed", "late_cutoff", "discovery_error",
        "market_error", "DRY_ORDER", "DRY_CANCEL", "DRY_FILL", "FILL", "FILL_MARKOUT", "PAIR_LOCKED", "SUMMARY"}
fh = open(OUT, "a")
def emit(kind, **kw):
    if kind in KEEP or kind.startswith("place_refused"):
        fh.write(json.dumps({"kind": kind, "t": time.time(), **kw}, default=str) + "\n"); fh.flush()
M.log = emit
v = DryVenue()
g = XV.IntlGuard(v.gget, XV.gamma_get, margin_ticks=1, max_age_s=20.0, require=True)
v.guard = g
eng = M.Engine(v, M.Book(pathlib.Path(tempfile.mkdtemp()) / "book.json"))
eng.open_gate_stats = None
# only the sports named on the command line (in production the remaining
# sports are decided by the bot's own results history, which a dry run lacks)
from src import pmus_sports_open_gate as OG
_allow = {x.strip() for x in ALLOW.split(",") if x.strip()}
eng.open_gate = lambda slug: {"allow": OG.sport_of(slug) in _allow,
                              "why": "dry_run_allow_list", "sport": OG.sport_of(slug)}
def feed():
    for slug, tok in list(g.resolved.items()):
        if not tok or slug not in eng.guard_tracked(): continue
        try:
            r = urllib.request.Request("https://clob.polymarket.com/book?token_id=" + tok, headers={"User-Agent": "research-client"})
            bk = json.load(urllib.request.urlopen(r, timeout=15))
            g.intl.ingest({"event_type": "book", "asset_id": tok, "bids": bk.get("bids") or [], "asks": bk.get("asks") or []})
        except Exception as e:
            emit("market_error", slug=slug, err="intl:" + type(e).__name__)
last_sel = 0.0
last_sum = time.time()
def summary():
    emit("SUMMARY", keep_tol_usd=str(M.RN1_KEEP_TOL_USD), fills_model=FILLS,
         gap_hold_s=getattr(M, "RN1_GAP_HOLD_S", None),
         gap_holds=getattr(eng, "_gap_holds", 0),
         depth_ticks=str(getattr(M, "DEPTH_TICKS", None)), phases=eng.phase_summary(),
         markout=eng.markout_summary(),
         positions={s: {"pos": m["pos"], "cost": m["cost"]} for s, m in eng.b.d["markets"].items()
                    if D(str(m["pos"]["YES"])) or D(str(m["pos"]["NO"]))})
while time.time() < UNTIL:
    t0 = time.time()
    if t0 - last_sum > 600:
        last_sum = t0; summary()
    try:
        if t0 - last_sel >= 20:
            last_sel = t0
            eng.select(v.account())
        for _ in range(40):
            if not g._pending: break
            g._resolve_one(g._pending.pop(0))
        g.track(eng.guard_tracked())
        for _ in range(40):
            if not g._pending: break
            g._resolve_one(g._pending.pop(0))
        feed()
        for slug in list(eng.active()):
            try:
                eng.step_market(slug)
            except Exception as e:
                emit("market_error", slug=slug, err=type(e).__name__ + ":" + str(e)[:120])
    except Exception as e:
        emit("market_error", slug=None, err="loop:" + type(e).__name__ + ":" + str(e)[:120])
    time.sleep(max(1.0, 5.0 - (time.time() - t0)))
summary()
