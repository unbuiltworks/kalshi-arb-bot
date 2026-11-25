"""Read-only: page the public sports event list. Raw to events_raw.json."""
import json, sys, time, urllib.request
G = "https://gateway.polymarket.us"
def get_json(path):
    r = urllib.request.Request(G + path, headers={"User-Agent": "research-client", "Accept": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as f:
        return json.load(f)
if __name__ == "__main__":
    events = []
    for off in range(0, 6000, 200):
        d = get_json("/v1/events?active=true&closed=false&categories=sports&limit=200&offset=%d" % off)
        evs = d.get("events") or []
        events += evs
        print(off, len(evs), file=sys.stderr)
        if len(evs) < 200: break
        time.sleep(0.5)
    json.dump({"t": time.time(), "events": events}, open("events_raw.json", "w"))
