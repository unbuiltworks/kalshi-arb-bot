"""Market-type classifier for PMUS sports markets (uses marketType,
sportsMarketType and title; the venue's sportsMarketType names are not
consistent about team vs game totals, so the title decides)."""
import json, re, datetime as dt
PER = re.compile(r"\b(1st|2nd|3rd|4th|first|second) (half|quarter|period)\b| in (the )?(first|second) half", re.I)
def mtype(m):
    mt = str(m.get("marketType") or ""); smt = str(m.get("sportsMarketType") or "")
    title = str(m.get("title") or ""); slug = str(m.get("slug") or "")
    per = bool(PER.search(title)) or any(t in smt for t in ("first_half", "second_half", "quarter", "_period"))
    if mt == "futures": return "futures"
    if mt == "moneyline": return "moneyline"
    if mt == "drawable_outcome": return "moneyline_3way"
    if mt == "spreads": return "spread_period" if per else "spread_full"
    if mt == "totals" or (mt == "props" and slug.startswith("tsc-")):
        team = not title.lower().startswith("over")
        if team: return "team_total_period" if per else "team_total_full"
        return "game_total_period" if per else "game_total_full"
    if mt == "props":
        if "_player_" in smt: return "player_prop"
        if smt.endswith("_winner") or "_winner_" in smt: return "period_winner_prop"
        return "other_game_prop"
    return "other:" + mt
ORDER = ["moneyline", "moneyline_3way", "spread_full", "spread_period", "game_total_full", "game_total_period",
         "team_total_full", "team_total_period", "player_prop", "period_winner_prop", "other_game_prop", "futures"]
def ts(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception: return None
def is_open(m):
    return bool(m.get("active") and not m.get("closed") and m.get("ep3Status") == "OPEN")
def is_cricket(slug):
    p = str(slug).split("-"); lg = p[1] if len(p) > 1 else ""
    return lg.endswith("cr") or lg in ("ipl","bbl","psl","cpl","wpl","sa20","t20","odi","t20i","ilt20","hundred","cricket","sheffield") or "cricket" in slug
def load():
    return [json.loads(l) for l in open("markets_full.jsonl")]
