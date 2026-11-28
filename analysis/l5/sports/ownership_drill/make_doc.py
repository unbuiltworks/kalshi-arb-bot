"""Sports side: the REAL sports engine and fills feed (release candidate
code), a fake venue filled with the day's twelve fills. Prints the document
the bot would serve, and the venue fill rows, as JSON."""
import json, os, sys, pathlib, tempfile
from decimal import Decimal as D
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue
FILLS = json.loads(sys.argv[1])
v = FakeVenue()
e = M.Engine(v, M.Book(pathlib.Path(tempfile.mkdtemp()) / "s.json"))
out = []
for i, (slug, side, q, px) in enumerate(FILLS):
    oid = v.place(slug, side, D(px), D(q), "open")
    mk = e.b.m(slug)
    mk["orders"][oid] = {"side": side, "px": px, "qty": q, "filled": "0",
                         "open": True, "stage": "open"}
    v.orders[oid]["filled"] = D(q)
    v.orders[oid]["open"] = False
    out.append((slug, side, q, px, oid))
for slug in {f[0] for f in FILLS}:
    e.refresh_fills(slug)
e.b.save()
print(json.dumps({"doc": M.fills_doc(M.Book(e.b.path)), "venue": out}, default=str))
