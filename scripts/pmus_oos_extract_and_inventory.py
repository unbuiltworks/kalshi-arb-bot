"""OOS retrieval from the recorder's persisted volume, then inventory.

SAFETY POSTURE, fixed here so it cannot drift:
  * the recorder is NOT modified, NOT redeployed and NOT exposed publicly;
  * no second service mounts its volume;
  * extraction uses Railway's own supported read path, `railway volume
    files`, which reads the volume without touching the service.

Stage 1 (`--list`) inventories the volume. Stage 2 (`--download`) copies the
tape parts into data/pmus_ws_tape_oos/. Stage 3 (`--inventory`) reports
coverage against the ALREADY-ESTABLISHED replay requirements and labels
every window DEVELOPMENT or OOS by date.

The 28 development windows stay frozen: any window whose start is at or
before the last development window is refused, not relabelled.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util                                           # noqa: E402

_s = importlib.util.spec_from_file_location(
    "dca", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pmus_favorite_dca.py"))
DCA = importlib.util.module_from_spec(_s)
_s.loader.exec_module(DCA)

VOLUME_ROOT = "/state/pmus-ws"
OOS_DIR = "data/pmus_ws_tape_oos"
#: the last DEVELOPMENT window. Anything starting after this is OOS and may
#: never be used to tune a parameter before it has been scored.
DEV_CUTOFF_SLUG = "cpc-btc-updown-15m-2026-10-02-2045z"


def dev_cutoff_epoch() -> int:
    return DCA.window_start(DEV_CUTOFF_SLUG)


def run(cmd: list) -> tuple:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def need_token() -> dict:
    return {
        "status": "BLOCKED_NO_RAILWAY_TOKEN",
        "cli": "installed",
        "missing_env": ["RAILWAY_TOKEN"],
        "why": "`railway volume files` is the only supported read path that "
               "leaves the recorder untouched and unexposed; it needs a "
               "Railway token.",
        "commands_once_unblocked": [
            "railway volume files list %s/raw" % VOLUME_ROOT,
            "railway volume files download %s/raw/<part>.jsonl %s/<part>.jsonl"
            % (VOLUME_ROOT, OOS_DIR),
        ],
    }


def inventory(paths) -> dict:
    """Coverage and usability of whatever has been downloaded."""
    by = collections.defaultdict(lambda: {"book": [], "trade": []})
    rows = provenance = 0
    for p in paths:
        with open(p) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                rows += 1
                k = r.get("kind")
                if k in ("book", "trade") and r.get("slug"):
                    by[r["slug"]][k].append(r)
                elif k in ("provenance", "session"):
                    provenance += 1
    cut = dev_cutoff_epoch()
    out = {"files": [os.path.basename(p) for p in paths], "rows": rows,
           "provenance_rows": provenance, "windows": {},
           "dev_cutoff_slug": DEV_CUTOFF_SLUG, "oos": [], "development": [],
           "rejected": {}}
    for slug, w in sorted(by.items()):
        if "btc-updown-15m" not in slug:
            continue
        for s in ("book", "trade"):
            w[s].sort(key=lambda r: r["t"])
        ok, oc, why = DCA.eligible(slug, w)
        start = DCA.window_start(slug)
        rec = {"slug": slug,
               "start_utc": dt.datetime.utcfromtimestamp(start).isoformat(),
               "books": len(w["book"]), "trades": len(w["trade"]),
               "span_s": round(w["book"][-1]["t"] - w["book"][0]["t"], 1)
               if w["book"] else 0,
               "max_book_gap_s": (round(max(
                   (b - a for a, b in zip([x["t"] for x in w["book"]],
                                          [x["t"] for x in w["book"]][1:])),
                   default=0), 2) if len(w["book"]) > 1 else None),
               "settlement_outcome": oc, "usable": ok, "reason": why,
               "label": "OOS" if start > cut else "DEVELOPMENT"}
        out["windows"][slug] = rec
        (out["oos"] if rec["label"] == "OOS"
         else out["development"]).append(slug)
        if not ok:
            out["rejected"][slug] = why
    out["n_oos_usable"] = sum(1 for s in out["oos"]
                              if out["windows"][s]["usable"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--inventory", action="store_true")
    a = ap.parse_args()
    if a.list or a.download:
        if not os.getenv("RAILWAY_TOKEN", "").strip():
            print(json.dumps(need_token(), indent=1))
            return 2
        rc, so, se = run(["railway", "volume", "files", "list",
                          VOLUME_ROOT + "/raw"])
        print(so or se)
        if rc or not a.download:
            return rc
        os.makedirs(OOS_DIR, exist_ok=True)
        for name in [x.strip() for x in so.splitlines()
                     if x.strip().endswith(".jsonl")]:
            run(["railway", "volume", "files", "download",
                 "%s/raw/%s" % (VOLUME_ROOT, os.path.basename(name)),
                 os.path.join(OOS_DIR, os.path.basename(name))])
        return 0
    if a.inventory:
        paths = sorted(os.path.join(OOS_DIR, f) for f in os.listdir(OOS_DIR)
                       if f.endswith(".jsonl")) if os.path.isdir(OOS_DIR) \
            else []
        if not paths:
            print(json.dumps({"status": "NO_OOS_FILES_DOWNLOADED_YET",
                              "dir": OOS_DIR, **need_token()}, indent=1))
            return 2
        out = inventory(paths)
        os.makedirs("analysis/l5/oos", exist_ok=True)
        json.dump(out, open("analysis/l5/oos/OOS_INVENTORY.json", "w"),
                  indent=1)
        print(json.dumps({k: v for k, v in out.items()
                          if k != "windows"}, indent=1))
        return 0
    print(json.dumps(need_token(), indent=1))
    return 2


if __name__ == "__main__":
    sys.exit(main())
