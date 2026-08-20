"""Public read-only ARB Command Center with Today and All Time accounting."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse

import app as core

app = FastAPI(title="ARB Command Center", docs_url=None, redoc_url=None)


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
    sql = f"""
    WITH rolled AS ({core.TRADE_ROLLUP_SQL}),
    today_realized AS (
        SELECT * FROM rolled
        WHERE mode IN ('LIVE','LIVE_PROBE')
          AND realized
          AND (realized_at AT TIME ZONE '{core.ET_TZ}')::date =
              (NOW() AT TIME ZONE '{core.ET_TZ}')::date
    ),
    all_realized AS (
        SELECT * FROM rolled
        WHERE mode IN ('LIVE','LIVE_PROBE') AND realized
    ),
    all_fills AS (
        SELECT COALESCE(SUM(amount),0) AS wagered
        FROM trade_events
        WHERE mode IN ('LIVE','LIVE_PROBE') AND event_type='FILL'
    ),
    open_live AS (
        SELECT COALESCE(SUM(capital_filled),0) AS exposure
        FROM rolled
        WHERE mode IN ('LIVE','LIVE_PROBE') AND NOT realized AND capital_filled > 0
    ),
    today_exec AS (
        SELECT COALESCE(SUM(opportunity_net_profit),0) AS executable_profit
        FROM trade_events
        WHERE mode IN ('LIVE','LIVE_PROBE')
          AND event_type='EXECUTABLE_OPPORTUNITY'
          AND status='EXECUTABLE'
          AND (event_ts AT TIME ZONE '{core.ET_TZ}')::date =
              (NOW() AT TIME ZONE '{core.ET_TZ}')::date
    ),
    all_exec AS (
        SELECT COALESCE(SUM(opportunity_net_profit),0) AS executable_profit
        FROM trade_events
        WHERE mode IN ('LIVE','LIVE_PROBE')
          AND event_type='EXECUTABLE_OPPORTUNITY'
          AND status='EXECUTABLE'
    )
    SELECT
        COALESCE((SELECT SUM(net_pnl) FROM today_realized),0) AS today_realized_net_pnl,
        COALESCE((SELECT SUM(gross_pnl) FROM today_realized),0) AS today_realized_gross_pnl,
        COALESCE((SELECT SUM(fees) FROM today_realized),0) AS today_realized_trade_fees,
        COALESCE((
            SELECT SUM(amount)
            FROM trade_events
            WHERE mode IN ('LIVE','LIVE_PROBE')
              AND event_type='FILL'
              AND (event_ts AT TIME ZONE '{core.ET_TZ}')::date =
                  (NOW() AT TIME ZONE '{core.ET_TZ}')::date
        ),0) AS today_capital_wagered,
        COALESCE((SELECT COUNT(*) FROM today_realized),0) AS today_trades_realized,
        COALESCE((SELECT COUNT(*) FROM today_realized WHERE net_pnl > 0),0) AS today_wins,
        COALESCE((SELECT COUNT(*) FROM today_realized WHERE net_pnl < 0),0) AS today_losses,
        COALESCE((SELECT COUNT(*) FROM today_realized WHERE net_pnl = 0),0) AS today_pushes,
        COALESCE((SELECT executable_profit FROM today_exec),0) AS today_executable_profit,

        COALESCE((SELECT SUM(net_pnl) FROM all_realized),0) AS total_realized_net_pnl,
        COALESCE((SELECT SUM(gross_pnl) FROM all_realized),0) AS total_realized_gross_pnl,
        COALESCE((SELECT SUM(fees) FROM all_realized),0) AS total_realized_trade_fees,
        COALESCE((SELECT wagered FROM all_fills),0) AS total_capital_wagered,
        COALESCE((SELECT COUNT(*) FROM all_realized),0) AS total_trades_realized,
        COALESCE((SELECT COUNT(*) FROM all_realized WHERE net_pnl > 0),0) AS total_wins,
        COALESCE((SELECT COUNT(*) FROM all_realized WHERE net_pnl < 0),0) AS total_losses,
        COALESCE((SELECT COUNT(*) FROM all_realized WHERE net_pnl = 0),0) AS total_pushes,
        COALESCE((SELECT executable_profit FROM all_exec),0) AS total_executable_profit,
        COALESCE((SELECT exposure FROM open_live),0) AS open_exposure
    """

    with core.db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            cur.execute(
                f"""
                SELECT strategy, COALESCE(SUM(net_pnl),0) AS net_pnl, COUNT(*) AS trades
                FROM ({core.TRADE_ROLLUP_SQL}) t
                WHERE mode IN ('LIVE','LIVE_PROBE') AND realized
                  AND (realized_at AT TIME ZONE '{core.ET_TZ}')::date =
                      (NOW() AT TIME ZONE '{core.ET_TZ}')::date
                GROUP BY strategy ORDER BY net_pnl DESC
                """
            )
            today_by_strategy = cur.fetchall()
            cur.execute(
                f"""
                SELECT strategy, COALESCE(SUM(net_pnl),0) AS net_pnl, COUNT(*) AS trades
                FROM ({core.TRADE_ROLLUP_SQL}) t
                WHERE mode IN ('LIVE','LIVE_PROBE') AND realized
                GROUP BY strategy ORDER BY net_pnl DESC
                """
            )
            total_by_strategy = cur.fetchall()

    today_realized = Decimal(str(row["today_realized_net_pnl"] or 0))
    today_wagered = Decimal(str(row["today_capital_wagered"] or 0))
    total_realized = Decimal(str(row["total_realized_net_pnl"] or 0))
    total_wagered = Decimal(str(row["total_capital_wagered"] or 0))

    today = {
        "realized_net_pnl": today_realized,
        "realized_gross_pnl": row["today_realized_gross_pnl"],
        "realized_trade_fees": row["today_realized_trade_fees"],
        "capital_wagered": today_wagered,
        "open_exposure": row["open_exposure"],
        "trades_realized": row["today_trades_realized"],
        "wins": row["today_wins"],
        "losses": row["today_losses"],
        "pushes": row["today_pushes"],
        "executable_opportunity_profit": row["today_executable_profit"],
        "net_roi_pct": (today_realized / today_wagered * 100) if today_wagered else Decimal("0"),
        "by_strategy": today_by_strategy,
    }
    total = {
        "realized_net_pnl": total_realized,
        "realized_gross_pnl": row["total_realized_gross_pnl"],
        "realized_trade_fees": row["total_realized_trade_fees"],
        "capital_wagered": total_wagered,
        "open_exposure": row["open_exposure"],
        "trades_realized": row["total_trades_realized"],
        "wins": row["total_wins"],
        "losses": row["total_losses"],
        "pushes": row["total_pushes"],
        "executable_opportunity_profit": row["total_executable_profit"],
        "net_roi_pct": (total_realized / total_wagered * 100) if total_wagered else Decimal("0"),
        "by_strategy": total_by_strategy,
    }

    result = dict(today)
    result["daily_target"] = core.DAILY_TARGET
    result["target_progress_pct"] = (
        today_realized / core.DAILY_TARGET * 100 if core.DAILY_TARGET else Decimal("0")
    )
    result["remaining_to_target"] = max(core.DAILY_TARGET - today_realized, Decimal("0"))
    result["all_time"] = total
    result["as_of"] = datetime.now(timezone.utc)
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
:root{color-scheme:dark;--bg:#0b0d10;--panel:#12161b;--muted:#8f9ba8;--text:#eef2f6;--line:#222a33;--good:#47d18c;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:1500px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:22px}
h1{font-size:28px;margin:0}.sub{color:var(--muted)}.period{margin-top:22px}.period-title{font-size:15px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;margin:0 0 10px}
.cards{display:grid;grid-template-columns:repeat(6,minmax(150px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}
.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:25px;font-weight:750;margin-top:5px}.good{color:var(--good)}.bad{color:var(--bad)}
.progress{height:10px;background:#1b222a;border-radius:999px;overflow:hidden;margin-top:10px}.bar{height:100%;background:var(--good);width:0}
.section{margin-top:22px;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}.sectionhead{padding:15px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
.sectionhead h2{font-size:16px;margin:0}table{width:100%;border-collapse:collapse}th,td{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}tr:last-child td{border-bottom:0}.scroll{overflow:auto}.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#1b222a;color:#cbd5df;font-size:11px}
.toolbar{display:flex;gap:8px;align-items:center}button,a.btn{border:1px solid var(--line);background:#171c22;color:var(--text);padding:8px 10px;border-radius:9px;text-decoration:none;cursor:pointer}
.empty{padding:36px;color:var(--muted);text-align:center}.small{font-size:12px;color:var(--muted)}
@media(max-width:1100px){.cards{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.wrap{padding:16px}.cards{grid-template-columns:repeat(2,1fr)}.top{align-items:start;flex-direction:column}}
</style>
</head>
<body><div class="wrap">
<div class="top"><div><h1>ARB Command Center</h1><div class="sub">Realized means filled + settled. Shadow and paper never count toward live P&amp;L.</div></div><div class="toolbar"><span id="asof" class="small"></span><a class="btn" href="/api/export.csv">Export CSV</a><button onclick="load()">Refresh</button></div></div>

<div class="period"><h2 class="period-title">Today</h2><div class="cards">
<div class="card"><div class="label">Realized P&amp;L Today</div><div id="today-pnl" class="value">—</div><div class="progress"><div id="bar" class="bar"></div></div><div id="target" class="small"></div></div>
<div class="card"><div class="label">Capital Wagered Today</div><div id="today-wagered" class="value">—</div></div>
<div class="card"><div class="label">Realized Trades Today</div><div id="today-trades" class="value">—</div><div id="today-record" class="small"></div></div>
<div class="card"><div class="label">Net ROI Today</div><div id="today-roi" class="value">—</div></div>
<div class="card"><div class="label">Fees Today</div><div id="today-fees" class="value">—</div></div>
<div class="card"><div class="label">Executable Opportunity Today</div><div id="today-opp" class="value">—</div><div class="small">Qualified events only; not profit</div></div>
</div></div>

<div class="period"><h2 class="period-title">All Time</h2><div class="cards">
<div class="card"><div class="label">Realized P&amp;L Total</div><div id="total-pnl" class="value">—</div></div>
<div class="card"><div class="label">Capital Wagered Total</div><div id="total-wagered" class="value">—</div></div>
<div class="card"><div class="label">Realized Trades Total</div><div id="total-trades" class="value">—</div><div id="total-record" class="small"></div></div>
<div class="card"><div class="label">Net ROI Total</div><div id="total-roi" class="value">—</div></div>
<div class="card"><div class="label">Fees Total</div><div id="total-fees" class="value">—</div></div>
<div class="card"><div class="label">Open Exposure Now</div><div id="exposure" class="value">—</div></div>
</div></div>

<div class="section"><div class="sectionhead"><h2>Today by strategy</h2></div><div id="today-strategies"></div></div>
<div class="section"><div class="sectionhead"><h2>All time by strategy</h2></div><div id="total-strategies"></div></div>
<div class="section"><div class="sectionhead"><h2>Live trades</h2><div class="small">LIVE + LIVE_PROBE only</div></div><div class="scroll"><table><thead><tr><th>Time</th><th>Strategy</th><th>Market</th><th>Mode</th><th>Status</th><th>Legs</th><th>Capital</th><th>Fees</th><th>Net P&amp;L</th></tr></thead><tbody id="rows"></tbody></table><div id="empty" class="empty" style="display:none">No live/probe trade events have been ingested yet.</div></div></div>
</div>
<script>
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2}).format(Number(n||0));
const pct=n=>`${Number(n||0).toFixed(2)}%`;
const cls=n=>Number(n)>0?'good':Number(n)<0?'bad':'';
function strategyTable(items,emptyText){return items.length?`<table><thead><tr><th>Strategy</th><th>Trades</th><th>Net P&L</th></tr></thead><tbody>${items.map(x=>`<tr><td>${x.strategy}</td><td>${x.trades}</td><td class="${cls(x.net_pnl)}">${money(x.net_pnl)}</td></tr>`).join('')}</tbody></table>`:`<div class="empty">${emptyText}</div>`;}
async function load(){
 const [s,t]=await Promise.all([fetch('/api/summary').then(r=>r.json()),fetch('/api/trades?limit=200').then(r=>r.json())]);
 const total=s.all_time||{};
 const tp=document.getElementById('today-pnl');tp.textContent=money(s.realized_net_pnl);tp.className='value '+cls(s.realized_net_pnl);
 document.getElementById('today-wagered').textContent=money(s.capital_wagered);
 document.getElementById('today-trades').textContent=s.trades_realized;
 document.getElementById('today-record').textContent=`${s.wins} W / ${s.losses} L / ${s.pushes} P`;
 document.getElementById('today-roi').textContent=pct(s.net_roi_pct);
 document.getElementById('today-fees').textContent=money(s.realized_trade_fees);
 document.getElementById('today-opp').textContent=money(s.executable_opportunity_profit);
 document.getElementById('target').textContent=`${money(s.realized_net_pnl)} / ${money(s.daily_target)} • ${money(s.remaining_to_target)} remaining`;
 document.getElementById('bar').style.width=Math.max(0,Math.min(100,Number(s.target_progress_pct||0)))+'%';

 const ap=document.getElementById('total-pnl');ap.textContent=money(total.realized_net_pnl);ap.className='value '+cls(total.realized_net_pnl);
 document.getElementById('total-wagered').textContent=money(total.capital_wagered);
 document.getElementById('total-trades').textContent=total.trades_realized??0;
 document.getElementById('total-record').textContent=`${total.wins??0} W / ${total.losses??0} L / ${total.pushes??0} P`;
 document.getElementById('total-roi').textContent=pct(total.net_roi_pct);
 document.getElementById('total-fees').textContent=money(total.realized_trade_fees);
 document.getElementById('exposure').textContent=money(total.open_exposure);

 document.getElementById('asof').textContent='Updated '+new Date(s.as_of).toLocaleTimeString();
 document.getElementById('today-strategies').innerHTML=strategyTable(s.by_strategy||[],'No realized live trades today.');
 document.getElementById('total-strategies').innerHTML=strategyTable(total.by_strategy||[],'No realized live trades yet.');
 const rows=document.getElementById('rows');
 rows.innerHTML=t.map(x=>`<tr><td>${new Date(x.latest_event_ts).toLocaleString([],{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}</td><td>${x.strategy}</td><td>${x.market||x.asset||'—'}</td><td><span class="pill">${x.mode}</span></td><td>${x.realized?'REALIZED':(x.status||'OPEN')}</td><td>${x.legs_filled??'—'}/${x.legs_required??'—'}</td><td>${money(x.capital_filled)}</td><td>${money(x.fees)}</td><td class="${cls(x.net_pnl)}">${x.realized?money(x.net_pnl):'—'}</td></tr>`).join('');
 document.getElementById('empty').style.display=t.length?'none':'block';
}
load();setInterval(load,15000);
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)
