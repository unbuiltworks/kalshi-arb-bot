"""ARB Command Center with explicit Sports / Crypto / Other lane accounting.

The existing public_app remains the compatibility layer for canonical trade
rollups and ingestion. This app adds lane-level realized accounting without
changing the ledger schema or the definition of realized P&L.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse

import app as core
import public_app as legacy

app = FastAPI(title="ARB Command Center", docs_url=None, redoc_url=None)

LANE_CASE = """
CASE
  WHEN lower(strategy) LIKE 'sports_%' THEN 'sports'
  WHEN lower(strategy) LIKE '%crypto%' OR lower(strategy) LIKE 'crypto-%' THEN 'crypto'
  ELSE 'other'
END
"""


@app.on_event("startup")
def startup() -> None:
    core.startup()


@app.get("/health")
def health() -> dict[str, str]:
    return core.health()


@app.post("/api/events")
def ingest_event(
    event: core.TradeEvent,
    _: None = Depends(core.require_ingest_token),
) -> dict[str, Any]:
    return core.ingest_event(event, _)


@app.get("/api/trades")
def trades(
    limit: int = Query(default=100, ge=1, le=1000),
    include_shadow: bool = False,
) -> list[dict[str, Any]]:
    return core.trades(limit=limit, include_shadow=include_shadow, _="public")


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    result = legacy.summary()
    sql = f"""
    WITH rolled AS ({core.TRADE_ROLLUP_SQL}),
    tagged AS (
      SELECT *, {LANE_CASE} AS lane
      FROM rolled
      WHERE mode IN ('LIVE','LIVE_PROBE')
    )
    SELECT
      lane,
      COALESCE(SUM(net_pnl) FILTER (
        WHERE realized
          AND (realized_at AT TIME ZONE '{core.ET_TZ}')::date =
              (NOW() AT TIME ZONE '{core.ET_TZ}')::date
      ),0) AS today_pnl,
      COALESCE(SUM(net_pnl) FILTER (WHERE realized),0) AS all_pnl,
      COALESCE(SUM(capital_filled) FILTER (
        WHERE realized
          AND (realized_at AT TIME ZONE '{core.ET_TZ}')::date =
              (NOW() AT TIME ZONE '{core.ET_TZ}')::date
      ),0) AS today_capital,
      COALESCE(SUM(capital_filled) FILTER (WHERE realized),0) AS all_capital,
      COUNT(*) FILTER (
        WHERE realized
          AND (realized_at AT TIME ZONE '{core.ET_TZ}')::date =
              (NOW() AT TIME ZONE '{core.ET_TZ}')::date
      ) AS today_trades,
      COUNT(*) FILTER (WHERE realized) AS all_trades,
      COUNT(*) FILTER (
        WHERE realized AND net_pnl > 0
          AND (realized_at AT TIME ZONE '{core.ET_TZ}')::date =
              (NOW() AT TIME ZONE '{core.ET_TZ}')::date
      ) AS today_wins,
      COUNT(*) FILTER (
        WHERE realized AND net_pnl < 0
          AND (realized_at AT TIME ZONE '{core.ET_TZ}')::date =
              (NOW() AT TIME ZONE '{core.ET_TZ}')::date
      ) AS today_losses,
      COUNT(*) FILTER (WHERE realized AND net_pnl > 0) AS all_wins,
      COUNT(*) FILTER (WHERE realized AND net_pnl < 0) AS all_losses,
      COALESCE(SUM(capital_filled) FILTER (
        WHERE NOT realized AND capital_filled > 0
      ),0) AS open_exposure
    FROM tagged
    GROUP BY lane
    ORDER BY lane
    """
    strategy_sql = f"""
    WITH rolled AS ({core.TRADE_ROLLUP_SQL}),
    tagged AS (
      SELECT *, {LANE_CASE} AS lane
      FROM rolled
      WHERE mode IN ('LIVE','LIVE_PROBE') AND realized
    )
    SELECT
      lane,
      strategy,
      COALESCE(SUM(net_pnl),0) AS all_pnl,
      COUNT(*) AS all_trades,
      COALESCE(SUM(net_pnl) FILTER (
        WHERE (realized_at AT TIME ZONE '{core.ET_TZ}')::date =
              (NOW() AT TIME ZONE '{core.ET_TZ}')::date
      ),0) AS today_pnl,
      COUNT(*) FILTER (
        WHERE (realized_at AT TIME ZONE '{core.ET_TZ}')::date =
              (NOW() AT TIME ZONE '{core.ET_TZ}')::date
      ) AS today_trades
    FROM tagged
    GROUP BY lane, strategy
    ORDER BY lane, all_pnl DESC, strategy
    """
    with core.db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            lane_rows = cur.fetchall()
            cur.execute(strategy_sql)
            strategy_rows = cur.fetchall()

    lanes: dict[str, dict[str, Any]] = {}
    for lane in ("sports", "crypto", "other"):
        lanes[lane] = {
            "today_pnl": Decimal("0"),
            "all_pnl": Decimal("0"),
            "today_capital": Decimal("0"),
            "all_capital": Decimal("0"),
            "today_trades": 0,
            "all_trades": 0,
            "today_wins": 0,
            "today_losses": 0,
            "all_wins": 0,
            "all_losses": 0,
            "open_exposure": Decimal("0"),
            "strategies": [],
        }
    for row in lane_rows:
        lane = str(row["lane"])
        if lane in lanes:
            lanes[lane].update(dict(row))
    for row in strategy_rows:
        lane = str(row["lane"])
        if lane in lanes:
            lanes[lane]["strategies"].append(dict(row))

    result["lanes"] = lanes
    result["lane_rules"] = {
        "sports": "strategy starts with sports_",
        "crypto": "strategy contains crypto or starts with crypto-",
        "other": "all remaining ARB live/probe strategies",
    }
    return core.jsonable(result)


@app.get("/api/export.csv")
def export_csv() -> StreamingResponse:
    return core.export_csv(_="public")


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARB Command Center</title>
<style>
:root{color-scheme:dark;--bg:#0b0d10;--panel:#12161b;--muted:#8f9ba8;--text:#eef2f6;--line:#222a33;--good:#47d18c;--bad:#ff6b6b;--accent:#73a7ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:1500px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:22px}h1{font-size:28px;margin:0}.sub,.small{color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:12px}.card,.lane{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}.label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:25px;font-weight:750;margin-top:5px}.good{color:var(--good)}.bad{color:var(--bad)}
.progress{height:9px;background:#1b222a;border-radius:999px;overflow:hidden;margin-top:10px}.bar{height:100%;background:var(--good);width:0}.period{margin-top:22px}.period-title{font-size:15px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;margin:0 0 10px}
.lanes{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.lane h2{margin:0 0 13px;font-size:20px}.lanegrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.metric{background:#0e1216;border:1px solid var(--line);border-radius:10px;padding:11px}.metric .value{font-size:19px}.lane-note{margin-top:12px;padding-top:10px;border-top:1px solid var(--line)}
.section{margin-top:22px;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}.sectionhead{padding:15px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:12px}.sectionhead h2{font-size:16px;margin:0}.scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}tr:last-child td{border-bottom:0}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}button,a.btn,select{border:1px solid var(--line);background:#171c22;color:var(--text);padding:8px 10px;border-radius:9px;text-decoration:none;cursor:pointer}.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#1b222a;font-size:11px}.empty{padding:30px;color:var(--muted);text-align:center}
@media(max-width:1050px){.cards{grid-template-columns:repeat(3,1fr)}.lanes{grid-template-columns:1fr}}@media(max-width:650px){.wrap{padding:16px}.cards{grid-template-columns:repeat(2,1fr)}.top{align-items:start;flex-direction:column}.lanegrid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body><div class="wrap">
<div class="top"><div><h1>ARB Command Center</h1><div class="sub">Sports, Crypto, and Other are accounted separately. Only filled + settled LIVE / LIVE_PROBE trades count as realized P&amp;L.</div></div><div class="toolbar"><span id="asof" class="small"></span><a class="btn" href="/api/export.csv">Export CSV</a><button onclick="load()">Refresh</button></div></div>

<div class="cards">
<div class="card"><div class="label">Realized P&amp;L Today</div><div id="today-pnl" class="value">—</div><div class="progress"><div id="bar" class="bar"></div></div><div id="target" class="small"></div></div>
<div class="card"><div class="label">Realized P&amp;L All Time</div><div id="total-pnl" class="value">—</div></div>
<div class="card"><div class="label">Realized Trades Today</div><div id="today-trades" class="value">—</div></div>
<div class="card"><div class="label">Open Exposure Now</div><div id="exposure" class="value">—</div></div>
<div class="card"><div class="label">Executable Opportunity Today</div><div id="today-opp" class="value">—</div><div class="small">Qualified opportunity, not realized profit</div></div>
</div>

<div class="period"><h2 class="period-title">Lanes</h2><div class="lanes" id="lanes"></div></div>

<div class="section"><div class="sectionhead"><h2>Realized performance by strategy</h2><select id="strategy-filter" onchange="renderStrategies()"><option value="all">All lanes</option><option value="sports">Sports</option><option value="crypto">Crypto</option><option value="other">Other</option></select></div><div id="strategies" class="scroll"></div></div>
<div class="section"><div class="sectionhead"><h2>Live / probe trades</h2><select id="trade-filter" onchange="renderTrades()"><option value="all">All lanes</option><option value="sports">Sports</option><option value="crypto">Crypto</option><option value="other">Other</option></select></div><div class="scroll"><table><thead><tr><th>Time</th><th>Lane</th><th>Strategy</th><th>Market</th><th>Status</th><th>Legs</th><th>Capital</th><th>Fees</th><th>Net P&amp;L</th></tr></thead><tbody id="rows"></tbody></table><div id="empty" class="empty" style="display:none">No matching live/probe trades.</div></div></div>
</div>
<script>
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:4}).format(Number(n||0));
const cls=n=>Number(n)>0?'good':Number(n)<0?'bad':'';
const laneOf=s=>String(s||'').toLowerCase().startsWith('sports_')?'sports':(String(s||'').toLowerCase().includes('crypto')||String(s||'').toLowerCase().startsWith('crypto-'))?'crypto':'other';
const label=s=>s.charAt(0).toUpperCase()+s.slice(1);
let S=null,T=[];
function laneHtml(name,x){const roi=Number(x.all_capital||0)?100*Number(x.all_pnl||0)/Number(x.all_capital):0;return `<div class="lane"><h2>${label(name)}</h2><div class="lanegrid"><div class="metric"><div class="label">Today P&L</div><div class="value ${cls(x.today_pnl)}">${money(x.today_pnl)}</div></div><div class="metric"><div class="label">All-time P&L</div><div class="value ${cls(x.all_pnl)}">${money(x.all_pnl)}</div></div><div class="metric"><div class="label">Today Trades</div><div class="value">${x.today_trades||0}</div><div class="small">${x.today_wins||0}W / ${x.today_losses||0}L</div></div><div class="metric"><div class="label">All-time Trades</div><div class="value">${x.all_trades||0}</div><div class="small">${x.all_wins||0}W / ${x.all_losses||0}L</div></div><div class="metric"><div class="label">Open Exposure</div><div class="value">${money(x.open_exposure)}</div></div><div class="metric"><div class="label">All-time ROI</div><div class="value ${cls(roi)}">${roi.toFixed(2)}%</div></div></div><div class="lane-note small">${(x.strategies||[]).length} realized strategy${(x.strategies||[]).length===1?'':'ies'} in ledger</div></div>`}
function renderStrategies(){if(!S)return;const f=document.getElementById('strategy-filter').value;let a=[];for(const [lane,x] of Object.entries(S.lanes||{})){for(const r of x.strategies||[])a.push({...r,lane})}if(f!=='all')a=a.filter(r=>r.lane===f);const el=document.getElementById('strategies');if(!a.length){el.innerHTML='<div class="empty">No realized strategies in this lane.</div>';return}el.innerHTML=`<table><thead><tr><th>Lane</th><th>Strategy</th><th>Today P&L</th><th>Today Trades</th><th>All-time P&L</th><th>All-time Trades</th></tr></thead><tbody>${a.map(r=>`<tr><td><span class="pill">${label(r.lane)}</span></td><td>${r.strategy}</td><td class="${cls(r.today_pnl)}">${money(r.today_pnl)}</td><td>${r.today_trades}</td><td class="${cls(r.all_pnl)}">${money(r.all_pnl)}</td><td>${r.all_trades}</td></tr>`).join('')}</tbody></table>`}
function renderTrades(){const f=document.getElementById('trade-filter').value;const rows=document.getElementById('rows');let a=T.filter(t=>f==='all'||laneOf(t.strategy)===f);rows.innerHTML=a.map(t=>{const pnl=t.net_pnl;const legs=`${t.legs_filled||0}/${t.legs_required||0}`;return `<tr><td>${new Date(t.event_ts).toLocaleString()}</td><td><span class="pill">${label(laneOf(t.strategy))}</span></td><td>${t.strategy||''}</td><td>${t.market||''}</td><td>${t.status||''}</td><td>${legs}</td><td>${money(t.capital_filled)}</td><td>${money(t.fees)}</td><td class="${cls(pnl)}">${pnl==null?'—':money(pnl)}</td></tr>`}).join('');document.getElementById('empty').style.display=a.length?'none':'block'}
async function load(){const [s,t]=await Promise.all([fetch('/api/summary').then(r=>r.json()),fetch('/api/trades?limit=250').then(r=>r.json())]);S=s;T=t;document.getElementById('today-pnl').textContent=money(s.realized_net_pnl);document.getElementById('today-pnl').className=`value ${cls(s.realized_net_pnl)}`;document.getElementById('total-pnl').textContent=money(s.all_time.realized_net_pnl);document.getElementById('total-pnl').className=`value ${cls(s.all_time.realized_net_pnl)}`;document.getElementById('today-trades').textContent=s.trades_realized||0;document.getElementById('exposure').textContent=money(s.open_exposure);document.getElementById('today-opp').textContent=money(s.executable_opportunity_profit);document.getElementById('bar').style.width=`${Math.max(0,Math.min(100,Number(s.target_progress_pct||0)))}%`;document.getElementById('target').textContent=`${Number(s.target_progress_pct||0).toFixed(2)}% of ${money(s.daily_target)} daily target · ${money(s.remaining_to_target)} remaining`;document.getElementById('asof').textContent=`As of ${new Date(s.as_of).toLocaleTimeString()}`;document.getElementById('lanes').innerHTML=['sports','crypto','other'].map(n=>laneHtml(n,(s.lanes||{})[n]||{})).join('');renderStrategies();renderTrades()}
load();setInterval(load,30000);
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)
