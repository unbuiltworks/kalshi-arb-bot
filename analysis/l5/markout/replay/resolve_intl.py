"""Step 2: map each of the 241 PMUS markets to its Polymarket-international
moneyline market and establish which international token is PMUS YES.

Methods (fail closed: anything ambiguous -> no match):
  exact   aec-<x> -> intl slug <x>; YES = intl outcome[0] (same slug order)
  atc     atc-<lg>-<h>-<a>-<date>-<o> -> intl market slug without 'atc-', outcomes Yes/No
  names   tennis / table tennis / doubles: unique intl event in the league's series,
          slug date within +-1 day, both PMUS player codes match the intl player names;
          YES = the intl outcome whose name matches the FIRST PMUS code
  codes   esports: intl team codes equal PMUS codes after stripping trailing digits
Writes market_map.json. Orientation is then checked against PMUS settled winners."""
import json, os, re, sys, unicodedata, collections
from datetime import date
from http_util import get, HERE

G = "https://gamma-api.polymarket.com"
PNL = "/home/user/kalshi-arb-bot/analysis/l5/rewards/sports_market_pnl_20261114_20261118.json"
SERIES_OF = {"atp": ["atp"], "wta": ["wta"], "itfme": ["itf"], "itfwo": ["itf"],
             "atpdb": ["atp-doubles"], "wtadb": ["wta-doubles"], "wtt": ["wtt"],
             "setkameua": ["setkameua"], "setkamemd": ["setkamemd"], "setkamecz": ["setkamecz"],
             "cs2": ["cs2"], "dota2": ["dota2"]}
DOUBLES = ("atpdb", "wtadb")
ESPORTS = ("cs2", "dota2")
ATC_LEAGUE_ALIAS = {"lal2": ["lal2", "es2"], "engnl": ["engnl", "enl"]}


def jl(v):
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except (TypeError, ValueError):
        return []


def toks(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z]+", s) if t]


def code_matches(code, name, doubles=False):
    t = toks(name)
    if doubles:  # 'Pavlasek/Rikl' -> pavlas+rikl
        parts = [toks(x) for x in str(name).split("/")]
        if len(parts) != 2 or not all(parts):
            return False
        return code == "".join(p)[:6] + "".join(parts[1])[:6] if False else \
            code == "".join(parts[0])[:6] + "".join(parts[1])[:6]
    for i in range(len(t)):
        for j in range(len(t)):
            if i != j and code == t[i][:3] + t[j][:3]:
                return True
    return False


def sdate(slug):
    m = re.search(r"(\d{4})-(\d\d)-(\d\d)", slug)
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def winner_idx(m):
    pr = jl(m.get("outcomePrices"))
    try:
        pr = [float(x) for x in pr]
    except (TypeError, ValueError):
        return None
    if len(pr) == 2 and m.get("closed") and max(pr) >= 0.999 and min(pr) <= 0.001:
        return 0 if pr[0] > pr[1] else 1
    return None


def pack(slug, m, yes_idx, method, note=""):
    tk = jl(m.get("clobTokenIds"))
    if len(tk) != 2:
        return None
    w = winner_idx(m)
    return {"pmus_slug": slug, "intl_slug": m.get("slug"), "method": method, "note": note,
            "intl_outcomes": jl(m.get("outcomes")), "yes_idx": yes_idx,
            "yes_token": str(tk[yes_idx]), "no_token": str(tk[1 - yes_idx]),
            "intl_closed": bool(m.get("closed")), "outcomePrices": jl(m.get("outcomePrices")),
            "intl_winner": None if w is None else ("YES" if w == yes_idx else "NO"),
            "gameStartTime": m.get("gameStartTime"), "closedTime": m.get("closedTime"),
            "intl_volume": m.get("volumeNum")}


def by_slug(base):
    for q in (f"/markets?slug={base}&closed=true", f"/markets?slug={base}"):
        rows = get(G + q) or []
        rows = [r for r in rows if r.get("slug") == base]
        if rows:
            return rows[0]
    return None


def main():
    pnl = json.load(open(PNL))
    events = json.load(open(os.path.join(HERE, "intl_events.json")))
    by_series = collections.defaultdict(list)
    for e in events.values():
        by_series[e["series"]].append(e)
    out = {}
    for slug in pnl:
        p = slug.split("-")
        lg = p[1]
        rec = {"pmus_slug": slug, "league": lg, "match": None, "why": ""}
        out[slug] = rec
        if p[0] not in ("aec", "atc"):
            rec["why"] = "not a sports match market (crypto)"
            continue
        if p[0] == "atc":
            for alt in ATC_LEAGUE_ALIAS.get(lg, [lg]):
                base = "-".join([alt] + p[2:])
                m = by_slug(base)
                if m and jl(m.get("outcomes")) == ["Yes", "No"]:
                    rec["match"] = pack(slug, m, 0, "atc", "league alias " + alt if alt != lg else "")
                    break
            if not rec["match"]:
                rec["why"] = "no international market under the same (or aliased) slug"
            continue
        base = slug[4:]
        m = by_slug(base)
        if m and str(m.get("sportsMarketType") or "moneyline") == "moneyline" and len(jl(m.get("outcomes"))) == 2:
            rec["match"] = pack(slug, m, 0, "exact")
            continue
        if lg not in SERIES_OF:
            rec["why"] = "no international market under the same slug; league has no mapped international series"
            continue
        c1, c2, when = p[2], p[3], sdate(slug)
        hits = []
        for s in SERIES_OF[lg]:
            for e in by_series[s]:
                d = sdate(e["slug"])
                if d is None or abs((d - when).days) > 1:
                    continue
                mm = [x for x in e["markets"] if x.get("slug") == e["slug"]
                      and str(x.get("sportsMarketType") or "") == "moneyline"]
                if len(mm) != 1:
                    continue
                x = mm[0]
                io = jl(x.get("outcomes"))
                if len(io) != 2:
                    continue
                if lg in ESPORTS:
                    ep = e["slug"].split("-")
                    if len(ep) < 6:
                        continue
                    a, b = re.sub(r"\d+$", "", ep[1]), re.sub(r"\d+$", "", ep[2])
                    if (a, b) == (c1, c2):
                        hits.append((x, 0, d))
                    elif (b, a) == (c1, c2):
                        hits.append((x, 1, d))
                else:
                    db = lg in DOUBLES
                    f0, f1 = code_matches(c1, io[0], db), code_matches(c2, io[1], db)
                    r0, r1 = code_matches(c1, io[1], db), code_matches(c2, io[0], db)
                    if f0 and f1 and not (r0 and r1):
                        hits.append((x, 0, d))
                    elif r0 and r1 and not (f0 and f1):
                        hits.append((x, 1, d))
        if len(hits) > 1:  # prefer the exact slug date if that makes it unique
            same = [h for h in hits if h[2] == when]
            hits = same if len(same) == 1 else hits
        if len(hits) == 1:
            x, yi, d = hits[0]
            rec["match"] = pack(slug, x, yi, "codes" if lg in ESPORTS else "names",
                                f"date {d}" + (" (reversed order)" if yi else ""))
        else:
            rec["why"] = ("ambiguous: %d international candidates" % len(hits)) if hits else \
                "no international event with both names/codes within +-1 day"
    json.dump(out, open(os.path.join(HERE, "market_map.json"), "w"), indent=1)
    # report
    tot, ok = collections.Counter(), collections.Counter()
    for r in out.values():
        tot[r["league"]] += 1
        ok[r["league"]] += bool(r["match"])
    for lg in sorted(tot, key=lambda k: -tot[k]):
        print(f"{lg:11s} {ok[lg]:3d}/{tot[lg]:3d}")
    print("matched", sum(ok.values()), "of", len(out))
    # orientation check against PMUS settled winners (markets with unpaired residual)
    agree = dis = unk = 0
    for slug, v in pnl.items():
        if not v.get("res_qty") or v.get("settle_px") is None:
            continue
        pm_w = "YES" if float(v["settle_px"]) >= 0.5 else "NO"   # settle_px = YES settlement
        mt = out[slug]["match"]
        if not mt:
            continue
        if mt["intl_winner"] is None:
            unk += 1
        elif mt["intl_winner"] == pm_w:
            agree += 1
        else:
            dis += 1; print("DISAGREE", slug, mt["intl_slug"], mt["method"], mt["note"], v["res_side"], v["settle_px"], mt["outcomePrices"])
        out[slug]["pmus_winner"] = pm_w
    print("orientation check on PMUS-settled residual markets: agree", agree, "disagree", dis, "intl unresolved", unk)
    json.dump(out, open(os.path.join(HERE, "market_map.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
