"""Public command-center entrypoint with a maker-shadow research page."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.responses import HTMLResponse

import app as core
from public_app import app


@app.get("/api/maker-shadow/summary")
def maker_shadow_summary() -> dict[str, Any]:
    with core.db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE event_type='SHADOW_MAKER_FILL') AS fills,
                    COUNT(*) FILTER (WHERE event_type='SHADOW_MAKER_HEDGE_UNAVAILABLE') AS hedge_unavailable,
                    COALESCE(SUM(opportunity_net_profit) FILTER (
                        WHERE event_type='SHADOW_MAKER_FILL'
                    ),0) AS immediate_simulated_profit,
                    MAX(event_ts) AS last_event_ts
                FROM trade_events
                WHERE mode='SHADOW' AND strategy='maker_triggered_hedge_shadow'
                """
            )
            overall = cur.fetchone()

            cur.execute(
                """
                SELECT
                    COALESCE(metadata->>'asset', asset, 'NA') AS asset,
                    COALESCE(metadata->>'direction', side, 'NA') AS direction,
                    COALESCE(metadata->>'policy', 'NA') AS policy,
                    COALESCE(metadata->>'target_profit', 'NA') AS target_profit,
                    COUNT(*) FILTER (WHERE event_type='SHADOW_MAKER_FILL') AS fills,
                    COUNT(*) FILTER (WHERE event_type='SHADOW_MAKER_HEDGE_UNAVAILABLE') AS hedge_unavailable,
                    COALESCE(SUM(opportunity_net_profit) FILTER (
                        WHERE event_type='SHADOW_MAKER_FILL'
                    ),0) AS simulated_profit
                FROM trade_events
                WHERE mode='SHADOW'
                  AND strategy='maker_triggered_hedge_shadow'
                  AND event_type IN ('SHADOW_MAKER_FILL','SHADOW_MAKER_HEDGE_UNAVAILABLE')
                GROUP BY 1,2,3,4
                ORDER BY simulated_profit DESC, fills DESC
                """
            )
            policies = cur.fetchall()

            cur.execute(
                """
                SELECT
                    (metadata->>'delay_ms')::int AS delay_ms,
                    COUNT(*) AS observations,
                    COUNT(*) FILTER (WHERE metadata->>'survives_positive'='true') AS positive_survived,
                    COUNT(*) FILTER (WHERE metadata->>'survives_target'='true') AS target_survived,
                    COUNT(*) FILTER (WHERE metadata->>'hedge_depth_ok'='true') AS q1_depth_ok,
                    COALESCE(AVG(opportunity_net_profit),0) AS avg_stressed_profit
                FROM trade_events
                WHERE mode='SHADOW'
                  AND strategy='maker_triggered_hedge_shadow'
                  AND event_type='SHADOW_MAKER_HEDGE_STRESS'
                  AND metadata ? 'delay_ms'
                GROUP BY 1 ORDER BY 1
                """
            )
            stress = cur.fetchall()

    fills = int(overall["fills"] or 0)
    unavailable = int(overall["hedge_unavailable"] or 0)
    attempts = fills + unavailable
    immediate_hedge_success_pct = (fills / attempts * 100) if attempts else 0.0

    stress_rows = []
    for row in stress:
        obs = int(row["observations"] or 0)
        item = dict(row)
        item["positive_survival_pct"] = (
            int(row["positive_survived"] or 0) / obs * 100 if obs else 0.0
        )
        item["target_survival_pct"] = (
            int(row["target_survived"] or 0) / obs * 100 if obs else 0.0
        )
        item["q1_depth_ok_pct"] = (
            int(row["q1_depth_ok"] or 0) / obs * 100 if obs else 0.0
        )
        stress_rows.append(item)

    result = {
        "shadow_only": True,
        "counts_as_realized_pnl": False,
        "fills": fills,
        "hedge_unavailable": unavailable,
        "fill_attempts": attempts,
        "immediate_hedge_success_pct": immediate_hedge_success_pct,
        "immediate_simulated_profit": overall["immediate_simulated_profit"],
        "last_event_ts": overall["last_event_ts"],
        "policies": policies,
        "latency_stress": stress_rows,
        "as_of": datetime.now(timezone.utc),
    }
    return core.jsonable(result)


MAKER_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Maker Shadow — ARB Command Center</title>
<style>
:root{color-scheme:dark;--bg:#0b0d10;--panel:#12161b;--muted:#8f9ba8;--text:#eef2f6;--line:#222a33;--good:#47d18c;--bad:#ff6b6b;--warn:#f4c95d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:1500px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;gap:20px;align-items:end}h1{margin:0;font-size:28px}.sub{color:var(--muted)}.banner{margin:18px 0;padding:13px 15px;border:1px solid #6d5b17;background:#1f1b0d;border-radius:12px;color:var(--warn);font-weight:700}.cards{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:12px}.card,.section{background:var(--panel);border:1px solid var(--line);border-radius:14px}.card{padding:16px}.label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:25px;font-weight:750;margin-top:5px}.section{margin-top:18px;overflow:hidden}.sectionhead{padding:14px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}.sectionhead h2{font-size:16px;margin:0}.scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}.good{color:var(--good)}.bad{color:var(--bad)}a{color:#9fc5ff;text-decoration:none}.small{font-size:12px;color:var(--muted)}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}}
</style></head>
<body><div class="wrap">
<div class="top"><div><h1>Maker Shadow</h1><div class="sub">Maker-triggered hedge research across BTC / ETH / SOL / XRP</div></div><div><a href="/">← Live Command Center</a></div></div>
<div class="banner">SHADOW ONLY — none of the numbers on this page count as realized profit.</div>
<div class="cards">
<div class="card"><div class="label">Simulated fills</div><div id="fills" class="value">—</div></div>
<div class="card"><div class="label">Immediate hedge success</div><div id="hedge" class="value">—</div></div>
<div class="card"><div class="label">Immediate simulated P&amp;L</div><div id="profit" class="value">—</div></div>
<div class="card"><div class="label">100ms target survival</div><div id="s100" class="value">—</div></div>
<div class="card"><div class="label">250ms target survival</div><div id="s250" class="value">—</div></div>
</div>
<div class="section"><div class="sectionhead"><h2>Quote policy results</h2><span class="small">Profit is simulated, not realized</span></div><div class="scroll"><table><thead><tr><th>Asset</th><th>Direction</th><th>Policy</th><th>Target</th><th>Fills</th><th>Unhedgeable</th><th>Sim P&amp;L</th></tr></thead><tbody id="policies"></tbody></table></div></div>
<div class="section"><div class="sectionhead"><h2>Post-fill hedge latency stress</h2><span class="small">Uses later Gemini books</span></div><div class="scroll"><table><thead><tr><th>Delay</th><th>Obs</th><th>Positive survives</th><th>Target survives</th><th>Q1 depth</th><th>Avg stressed P&amp;L</th></tr></thead><tbody id="stress"></tbody></table></div></div>
<div id="asof" class="small" style="margin-top:14px"></div>
</div><script>
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2}).format(Number(n||0));
const pct=n=>`${Number(n||0).toFixed(1)}%`; const cls=n=>Number(n)>0?'good':Number(n)<0?'bad':'';
function stressAt(rows,ms){return rows.find(r=>Number(r.delay_ms)===ms)||null}
async function load(){const r=await fetch('/api/maker-shadow/summary');const d=await r.json();
 document.getElementById('fills').textContent=d.fills;
 document.getElementById('hedge').textContent=pct(d.immediate_hedge_success_pct);
 const p=document.getElementById('profit');p.textContent=money(d.immediate_simulated_profit);p.className=`value ${cls(d.immediate_simulated_profit)}`;
 const s100=stressAt(d.latency_stress,100),s250=stressAt(d.latency_stress,250);
 document.getElementById('s100').textContent=s100?pct(s100.target_survival_pct):'—';document.getElementById('s250').textContent=s250?pct(s250.target_survival_pct):'—';
 document.getElementById('policies').innerHTML=d.policies.map(x=>`<tr><td>${x.asset}</td><td>${x.direction}</td><td>${x.policy}</td><td>${money(x.target_profit)}</td><td>${x.fills}</td><td>${x.hedge_unavailable}</td><td class="${cls(x.simulated_profit)}">${money(x.simulated_profit)}</td></tr>`).join('')||'<tr><td colspan="7">No maker fills yet.</td></tr>';
 document.getElementById('stress').innerHTML=d.latency_stress.map(x=>`<tr><td>${x.delay_ms}ms</td><td>${x.observations}</td><td>${pct(x.positive_survival_pct)}</td><td>${pct(x.target_survival_pct)}</td><td>${pct(x.q1_depth_ok_pct)}</td><td class="${cls(x.avg_stressed_profit)}">${money(x.avg_stressed_profit)}</td></tr>`).join('')||'<tr><td colspan="6">No latency observations yet.</td></tr>';
 document.getElementById('asof').textContent=`Updated ${new Date(d.as_of).toLocaleString()}`;}
load();setInterval(load,15000);
</script></body></html>
"""


@app.get("/maker", response_class=HTMLResponse)
def maker_page() -> str:
    return MAKER_HTML
