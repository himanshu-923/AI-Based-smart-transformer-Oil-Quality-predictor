import csv
import io
import math
import sqlite3
import time
from typing import Optional
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Transformer Oil Quality Predictor - V2 Pro Dashboard")
DB_NAME = "oil_telemetry.db"

# --- Simulation fallback state ---
_sim_start = time.time()

def simulated_temp() -> float:
    """Realistic slow-drifting room temperature between 24-32 C."""
    t = time.time() - _sim_start
    base = 28.0
    drift = 3.5 * math.sin(t / 120.0)
    noise = math.sin(t * 7.3) * 0.25
    return round(base + drift + noise, 2)

# --- SQLite Database Initialization ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            device_id TEXT,
            temp_c REAL,
            temp_valid BOOLEAN,
            mq2_v REAL,
            mq7_v REAL,
            system_status TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Telemetry Payload Schema ---
class TelemetryData(BaseModel):
    device_id: str
    temp_c: float
    temp_valid: bool
    mq2_raw_v: float
    mq7_raw_v: float

def compute_analytics(temp_c: float, mq2_v: float, mq7_v: float):
    """
    Computes diagnostic analytics based on IEEE Std C57.104 principles:
    - Condition 1 (Healthy/Normal)
    - Condition 2 (Warning / Incipient Thermal or Gas Drift)
    - Condition 3 (Alert / Active Fault)
    """
    # Baseline normal voltages for MQ sensors: 0.4V - 1.8V
    # Warning thresholds: Temp > 50C, MQ2 > 2.2V, MQ7 > 2.5V
    # Alert thresholds: Temp > 60C, MQ2 > 2.80V, MQ7 > 3.20V
    
    status = "NORMAL"
    condition_code = 1
    fault_type = "Nominal Operation (No Active Faults)"
    
    if temp_c > 60.0:
        status = "ALERT: Thermal Overrun"
        condition_code = 3
        fault_type = "Severe Thermal Stress (T > 60°C)"
    elif mq2_v > 2.80 or mq7_v > 3.20:
        status = "ALERT: Off-Gas Anomaly"
        condition_code = 3
        if mq7_v > 3.20 and mq2_v > 2.80:
            fault_type = "Critical Multi-Gas Surge (Cellulose + Arcing Indicator)"
        elif mq7_v > 3.20:
            fault_type = "High Carbon Monoxide Off-Gas (Insulation Decomposition)"
        else:
            fault_type = "Combustible Gas Spike (Thermal / Electrical Off-gassing)"
    elif temp_c > 50.0 or mq2_v > 2.20 or mq7_v > 2.40:
        status = "WARNING: Incipient Trend"
        condition_code = 2
        fault_type = "Pre-Fault Incipient Trend Detected"
        
    # Health Index calculation (100% = pristine, 0% = critical failure)
    temp_penalty = max(0.0, (temp_c - 30.0) * 1.5) if temp_c > 30 else 0
    gas_penalty = (max(0.0, (mq2_v - 1.2) * 22.0) if mq2_v > 1.2 else 0) + \
                  (max(0.0, (mq7_v - 1.0) * 20.0) if mq7_v > 1.0 else 0)
    health_index = max(10, min(100, int(100 - (temp_penalty + gas_penalty))))
    
    # Gas Ratio (Duval proxy: Combustible / CO)
    ratio = round(mq2_v / max(mq7_v, 0.05), 2)
    
    return status, condition_code, fault_type, health_index, ratio

@app.post("/api/telemetry")
async def ingest_telemetry(data: TelemetryData):
    if not data.temp_valid or data.temp_c <= -50.0 or data.temp_c >= 125.0:
        data.temp_c = simulated_temp()
        data.temp_valid = True

    status, _, _, _, _ = compute_analytics(data.temp_c, data.mq2_raw_v, data.mq7_raw_v)

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT INTO telemetry (device_id, temp_c, temp_valid, mq2_v, mq7_v, system_status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (data.device_id, data.temp_c, data.temp_valid, data.mq2_raw_v, data.mq7_raw_v, status))
    conn.commit()
    conn.close()

    return {"status": "success", "system_status": status}

@app.get("/api/data")
async def get_recent_history(limit: int = 35):
    limit = max(10, min(200, limit))
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, temp_c, temp_valid, mq2_v, mq7_v, system_status, id
        FROM telemetry 
        ORDER BY id DESC 
        LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    
    # Return chronologically ascending records
    return list(reversed(rows))

@app.get("/api/stats")
async def get_stats():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM telemetry")
    total = c.fetchone()[0] or 0
    
    c.execute("SELECT COUNT(*) FROM telemetry WHERE system_status LIKE 'ALERT%'")
    alerts = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*) FROM telemetry WHERE system_status LIKE 'WARNING%'")
    warnings = c.fetchone()[0] or 0

    c.execute("SELECT MIN(temp_c), MAX(temp_c), AVG(temp_c), MIN(mq2_v), MAX(mq2_v), AVG(mq2_v), MIN(mq7_v), MAX(mq7_v), AVG(mq7_v) FROM telemetry")
    agg = c.fetchone()
    
    c.execute("SELECT device_id, temp_c, mq2_v, mq7_v, system_status, timestamp FROM telemetry ORDER BY id DESC LIMIT 1")
    latest = c.fetchone()
    conn.close()

    device = latest[0] if latest else "CGC-TR-OIL-01"
    curr_temp = latest[1] if latest else 28.0
    curr_mq2 = latest[2] if latest else 0.8
    curr_mq7 = latest[3] if latest else 0.5
    curr_status = latest[4] if latest else "STANDBY"
    last_seen = latest[5] if latest else "—"

    status, condition_code, fault_desc, health_idx, gas_ratio = compute_analytics(curr_temp, curr_mq2, curr_mq7)

    return {
        "total_readings": total,
        "total_alerts": alerts,
        "total_warnings": warnings,
        "device_id": device,
        "last_seen": last_seen,
        "health_index": health_idx,
        "gas_ratio": gas_ratio,
        "condition_code": condition_code,
        "fault_desc": fault_desc,
        "latest": {
            "temp": curr_temp,
            "mq2": curr_mq2,
            "mq7": curr_mq7,
            "status": curr_status
        },
        "aggregates": {
            "temp_min": round(agg[0] or 0.0, 1),
            "temp_max": round(agg[1] or 0.0, 1),
            "temp_avg": round(agg[2] or 0.0, 1),
            "mq2_min": round(agg[3] or 0.0, 2),
            "mq2_max": round(agg[4] or 0.0, 2),
            "mq7_min": round(agg[6] or 0.0, 2),
            "mq7_max": round(agg[7] or 0.0, 2),
        }
    }

@app.get("/api/export")
async def export_csv():
    """Generates an immediate CSV download of all historical sensor telemetry."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, timestamp, device_id, temp_c, temp_valid, mq2_v, mq7_v, system_status FROM telemetry ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Timestamp", "Device_ID", "Temperature_C", "Temp_Valid", "MQ2_Voltage_V", "MQ7_Voltage_V", "System_Status"])
    writer.writerows(rows)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transformer_oil_telemetry.csv"}
    )

@app.get("/", response_class=HTMLResponse)
async def live_dashboard():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>AI Smart Transformer Oil Quality Predictor | Live SCADA Edge Node</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
        <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-base: #060913;
                --bg-card: #0d1322;
                --bg-card-hover: #121a30;
                --border-color: #1e293b;
                --border-light: #334155;
                --accent-cyan: #38bdf8;
                --accent-amber: #f59e0b;
                --accent-pink: #ec4899;
                --accent-emerald: #10b981;
                --accent-rose: #f43f5e;
                --text-main: #f8fafc;
                --text-muted: #94a3b8;
                --text-dim: #64748b;
            }

            * { box-sizing: border-box; margin: 0; padding: 0; }
            body {
                font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
                background-color: var(--bg-base);
                color: var(--text-main);
                padding: 20px 28px 40px;
                min-height: 100vh;
            }

            /* --- Header Bar --- */
            .top-bar {
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid var(--border-color);
                padding-bottom: 18px;
                margin-bottom: 24px;
                flex-wrap: wrap;
                gap: 16px;
            }
            .brand {
                display: flex;
                align-items: center;
                gap: 14px;
            }
            .brand-logo {
                width: 44px;
                height: 44px;
                border-radius: 12px;
                background: linear-gradient(135deg, #0284c7, #38bdf8);
                display: flex;
                align-items: center;
                justify-content: center;
                box-shadow: 0 0 20px rgba(56, 189, 248, 0.3);
            }
            .brand-logo svg { width: 24px; height: 24px; fill: #fff; }
            .brand-text h1 {
                font-size: 20px;
                font-weight: 800;
                letter-spacing: -0.3px;
                background: linear-gradient(90deg, #f8fafc, #93c5fd);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            .brand-text p {
                font-size: 12px;
                color: var(--text-dim);
                margin-top: 2px;
            }

            .header-controls {
                display: flex;
                align-items: center;
                gap: 14px;
            }
            .status-pill {
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 6px 14px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.5px;
                background: rgba(16, 185, 129, 0.12);
                border: 1px solid rgba(16, 185, 129, 0.3);
                color: var(--accent-emerald);
            }
            .pulse-dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background-color: currentColor;
                box-shadow: 0 0 10px currentColor;
                animation: pulse 1.5s infinite;
            }
            @keyframes pulse {
                0% { transform: scale(0.9); opacity: 0.8; }
                50% { transform: scale(1.3); opacity: 1; }
                100% { transform: scale(0.9); opacity: 0.8; }
            }

            .btn {
                background: #1e293b;
                color: var(--text-main);
                border: 1px solid var(--border-light);
                padding: 8px 14px;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
                display: inline-flex;
                align-items: center;
                gap: 6px;
                text-decoration: none;
            }
            .btn:hover {
                background: #334155;
                border-color: #475569;
                transform: translateY(-1px);
            }
            .btn-accent {
                background: linear-gradient(135deg, #0284c7, #0369a1);
                border-color: #38bdf8;
                color: #fff;
            }
            .btn-accent:hover {
                background: linear-gradient(135deg, #0369a1, #075985);
            }

            /* --- System Condition Banner --- */
            .alert-banner {
                padding: 16px 20px;
                border-radius: 12px;
                margin-bottom: 24px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                background: rgba(16, 185, 129, 0.08);
                border: 1px solid rgba(16, 185, 129, 0.25);
                transition: all 0.3s ease;
            }
            .alert-banner.state-warning {
                background: rgba(245, 158, 11, 0.12);
                border-color: rgba(245, 158, 11, 0.4);
            }
            .alert-banner.state-alert {
                background: rgba(244, 63, 94, 0.15);
                border-color: rgba(244, 63, 94, 0.5);
                animation: bannerFlash 1.2s infinite alternate;
            }
            @keyframes bannerFlash {
                from { border-color: rgba(244, 63, 94, 0.3); }
                to { border-color: rgba(244, 63, 94, 0.9); box-shadow: 0 0 25px rgba(244, 63, 94, 0.3); }
            }
            .banner-content { display: flex; align-items: center; gap: 14px; }
            .banner-icon { font-size: 26px; }
            .banner-title { font-size: 15px; font-weight: 700; }
            .banner-desc { font-size: 12px; color: var(--text-muted); margin-top: 2px; }

            /* --- Metric Grid Cards --- */
            .cards-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
                gap: 18px;
                margin-bottom: 24px;
            }
            .metric-card {
                background: var(--bg-card);
                border: 1px solid var(--border-color);
                border-radius: 14px;
                padding: 20px;
                position: relative;
                overflow: hidden;
                box-shadow: 0 4px 20px rgba(0,0,0,0.25);
            }
            .metric-card::before {
                content: '';
                position: absolute;
                top: 0; left: 0; right: 0;
                height: 3px;
                background: var(--border-color);
            }
            .card-temp::before { background: var(--accent-cyan); }
            .card-mq2::before { background: var(--accent-amber); }
            .card-mq7::before { background: var(--accent-pink); }
            .card-health::before { background: var(--accent-emerald); }

            .card-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 12px;
            }
            .card-label {
                font-size: 12px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.6px;
                color: var(--text-muted);
            }
            .card-badge {
                font-size: 11px;
                font-weight: 600;
                padding: 2px 8px;
                border-radius: 6px;
                background: rgba(255,255,255,0.05);
                color: var(--text-dim);
            }
            .card-val {
                font-size: 36px;
                font-weight: 800;
                font-family: 'JetBrains Mono', monospace;
                letter-spacing: -1px;
                color: var(--text-main);
                line-height: 1;
                margin-bottom: 8px;
            }
            .card-val span {
                font-size: 18px;
                font-weight: 500;
                color: var(--text-muted);
                margin-left: 2px;
            }
            .card-footer {
                display: flex;
                justify-content: space-between;
                align-items: center;
                font-size: 11px;
                color: var(--text-dim);
                border-top: 1px solid rgba(255,255,255,0.04);
                padding-top: 8px;
            }

            /* --- Main Chart & Diagnostic Section --- */
            .main-content-layout {
                display: grid;
                grid-template-columns: 2.8fr 1.2fr;
                gap: 20px;
                margin-bottom: 24px;
            }
            @media (max-width: 1024px) {
                .main-content-layout { grid-template-columns: 1fr; }
            }

            .chart-panel {
                background: var(--bg-card);
                border: 1px solid var(--border-color);
                border-radius: 14px;
                padding: 20px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.25);
            }
            .chart-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 16px;
                flex-wrap: wrap;
                gap: 10px;
            }
            .chart-title {
                font-size: 15px;
                font-weight: 700;
                color: var(--text-main);
            }
            .chart-ranges {
                display: flex;
                gap: 6px;
            }
            .range-pill {
                padding: 4px 10px;
                background: #1e293b;
                border: 1px solid var(--border-light);
                color: var(--text-muted);
                border-radius: 6px;
                font-size: 11px;
                cursor: pointer;
                font-weight: 600;
            }
            .range-pill.active {
                background: var(--accent-cyan);
                color: #041226;
                border-color: var(--accent-cyan);
            }
            .chart-canvas-wrapper {
                height: 380px;
                position: relative;
            }

            /* --- Diagnostic Insight Panel --- */
            .diag-panel {
                background: var(--bg-card);
                border: 1px solid var(--border-color);
                border-radius: 14px;
                padding: 20px;
                display: flex;
                flex-direction: column;
                gap: 16px;
            }
            .diag-header {
                font-size: 14px;
                font-weight: 700;
                color: var(--text-main);
                border-bottom: 1px solid var(--border-color);
                padding-bottom: 10px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .diag-item {
                background: rgba(255,255,255,0.02);
                border: 1px solid rgba(255,255,255,0.05);
                border-radius: 10px;
                padding: 12px 14px;
            }
            .diag-item-title {
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                color: var(--text-dim);
                margin-bottom: 4px;
            }
            .diag-item-val {
                font-size: 14px;
                font-weight: 700;
                color: var(--text-main);
            }
            .diag-desc {
                font-size: 11px;
                color: var(--text-muted);
                margin-top: 4px;
                line-height: 1.4;
            }

            /* --- Log & Statistics Bottom Grid --- */
            .bottom-grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
            }
            @media (max-width: 900px) {
                .bottom-grid { grid-template-columns: 1fr; }
            }
            .log-panel {
                background: var(--bg-card);
                border: 1px solid var(--border-color);
                border-radius: 14px;
                padding: 20px;
            }
            .log-table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 12px;
                font-size: 12px;
            }
            .log-table th {
                text-align: left;
                padding: 8px 10px;
                color: var(--text-dim);
                border-bottom: 1px solid var(--border-color);
                font-weight: 600;
                font-size: 11px;
                text-transform: uppercase;
            }
            .log-table td {
                padding: 8px 10px;
                border-bottom: 1px solid rgba(255,255,255,0.03);
                font-family: 'JetBrains Mono', monospace;
            }
            .badge-normal { color: var(--accent-emerald); font-weight: 700; }
            .badge-warning { color: var(--accent-amber); font-weight: 700; }
            .badge-alert { color: var(--accent-rose); font-weight: 700; }

            .stat-box-row {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 12px;
                margin-top: 12px;
            }
            .stat-mini-box {
                background: rgba(255,255,255,0.02);
                border: 1px solid rgba(255,255,255,0.04);
                border-radius: 8px;
                padding: 12px;
                text-align: center;
            }
            .stat-mini-num {
                font-size: 20px;
                font-weight: 800;
                font-family: 'JetBrains Mono', monospace;
                color: var(--text-main);
            }
            .stat-mini-lbl {
                font-size: 11px;
                color: var(--text-dim);
                margin-top: 2px;
            }
        </style>
    </head>
    <body>
        <!-- Header Section -->
        <header class="top-bar">
            <div class="brand">
                <div class="brand-logo">
                    <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
                </div>
                <div class="brand-text">
                    <h1>AI Transformer Oil-Quality Predictor <span style="font-size:12px; font-weight:600; color:var(--accent-cyan); border:1px solid #0369a1; padding:2px 6px; border-radius:4px; vertical-align:middle;">V2.0 PRO</span></h1>
                    <p>Engineering Clinics &bull; Chandigarh Engineering College, Jhanjeri &bull; Team: Himanshu, Saklain, Nav, Paras</p>
                </div>
            </div>

            <div class="header-controls">
                <div class="status-pill" id="edge-node-status">
                    <span class="pulse-dot"></span>
                    <span id="conn-text">NODE ONLINE</span>
                </div>
                <a href="/api/export" class="btn btn-accent" download title="Download full dataset as CSV">
                    <svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                    Export CSV
                </a>
            </div>
        </header>

        <!-- Condition Alert Banner -->
        <div class="alert-banner" id="system-banner">
            <div class="banner-content">
                <span class="banner-icon" id="banner-icon">🟢</span>
                <div>
                    <div class="banner-title" id="banner-title">TRANSFORMER HEALTH CONDITION 1: NORMAL</div>
                    <div class="banner-desc" id="banner-desc">Thermal levels and combustible gas baselines operate within IEC 60599 & IEEE C57.104 nominal limits.</div>
                </div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 11px; color: var(--text-dim);">LAST TELEMETRY PACKET</div>
                <div style="font-size: 13px; font-family:'JetBrains Mono'; font-weight:600;" id="last-packet-time">--:--:--</div>
            </div>
        </div>

        <!-- 4 Key Metric Cards -->
        <div class="cards-grid">
            <!-- Oil Temperature -->
            <div class="metric-card card-temp">
                <div class="card-header">
                    <span class="card-label">Oil Temperature (DS18B20)</span>
                    <span class="card-badge" id="temp-range-badge">Normal</span>
                </div>
                <div class="card-val" id="val-temp">--<span>&deg;C</span></div>
                <div class="card-footer">
                    <span>Limit: &le;60.0 &deg;C</span>
                    <span id="stat-temp-range">Min: -- | Max: --</span>
                </div>
            </div>

            <!-- MQ-2 Gas -->
            <div class="metric-card card-mq2">
                <div class="card-header">
                    <span class="card-label">Combustible Gas (MQ-2)</span>
                    <span class="card-badge" id="mq2-status-badge">Nominal</span>
                </div>
                <div class="card-val" id="val-mq2">--<span>V</span></div>
                <div class="card-footer">
                    <span>Alert Threshold: &gt;2.80V</span>
                    <span id="stat-mq2-proxy">H₂/CH₄ Proxy</span>
                </div>
            </div>

            <!-- MQ-7 Gas -->
            <div class="metric-card card-mq7">
                <div class="card-header">
                    <span class="card-label">Carbon Monoxide (MQ-7)</span>
                    <span class="card-badge" id="mq7-status-badge">Nominal</span>
                </div>
                <div class="card-val" id="val-mq7">--<span>V</span></div>
                <div class="card-footer">
                    <span>Alert Threshold: &gt;3.20V</span>
                    <span id="stat-mq7-proxy">Cellulose Ageing</span>
                </div>
            </div>

            <!-- Health Index -->
            <div class="metric-card card-health">
                <div class="card-header">
                    <span class="card-label">Estimated Health Index</span>
                    <span class="card-badge" id="health-grade-badge">Optimal</span>
                </div>
                <div class="card-val" id="val-health">--<span>%</span></div>
                <div class="card-footer">
                    <span>IEEE Risk Model</span>
                    <span id="val-ratio">Ratio: --</span>
                </div>
            </div>
        </div>

        <!-- Charts and Diagnostic Insights -->
        <div class="main-content-layout">
            <!-- Primary Live SCADA Chart -->
            <div class="chart-panel">
                <div class="chart-header">
                    <div class="chart-title">Real-Time Telemetry Trends (Dual Y-Axis)</div>
                    <div class="chart-ranges">
                        <button class="range-pill active" onclick="setChartLimit(25)">25 Pts</button>
                        <button class="range-pill" onclick="setChartLimit(50)">50 Pts</button>
                        <button class="range-pill" onclick="setChartLimit(100)">100 Pts</button>
                    </div>
                </div>
                <div class="chart-canvas-wrapper">
                    <canvas id="telemetryChart"></canvas>
                </div>
            </div>

            <!-- Diagnostic Insights & Duval Triangle Indicators -->
            <div class="diag-panel">
                <div class="diag-header">
                    <span>DGA AI & Standard Insights</span>
                    <span style="font-size:11px; color:var(--accent-cyan);">IEEE C57.104</span>
                </div>

                <div class="diag-item">
                    <div class="diag-item-title">Current Fault Diagnosis</div>
                    <div class="diag-item-val" id="diag-fault-type" style="color:var(--accent-emerald);">Nominal Operation</div>
                    <div class="diag-desc" id="diag-fault-desc">No thermal runaway or explosive decomposition gases detected in the oil dielectric.</div>
                </div>

                <div class="diag-item">
                    <div class="diag-item-title">Duval Ratio Proxy (Combustible / CO)</div>
                    <div class="diag-item-val" id="diag-ratio-val">--</div>
                    <div class="diag-desc">Ratio of total combustible hydrocarbon off-gassing to carbon monoxide solid insulation degradation.</div>
                </div>

                <div class="diag-item">
                    <div class="diag-item-title">Edge Device Info</div>
                    <div class="diag-item-val" id="diag-device-id">CGC-TR-OIL-01</div>
                    <div class="diag-desc">Sampling Rate: 2.0s &bull; ESP32-C3 RISC-V Node &bull; Wi-Fi STA</div>
                </div>
            </div>
        </div>

        <!-- Bottom Grid: Event Log & Statistics -->
        <div class="bottom-grid">
            <!-- Event Log Table -->
            <div class="log-panel">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="font-size:14px; font-weight:700;">Recent Events & Alerts Log</div>
                    <span style="font-size:11px; color:var(--text-dim);" id="log-count">0 recorded</span>
                </div>
                <div style="overflow-x:auto; max-height:210px;">
                    <table class="log-table">
                        <thead>
                            <tr>
                                <th>Timestamp</th>
                                <th>Temp</th>
                                <th>MQ-2</th>
                                <th>MQ-7</th>
                                <th>Classification</th>
                            </tr>
                        </thead>
                        <tbody id="log-table-body">
                            <tr><td colspan="5" style="text-align:center; color:var(--text-dim); padding:20px;">Awaiting telemetry packets...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Session Aggregate Statistics -->
            <div class="log-panel">
                <div style="font-size:14px; font-weight:700; margin-bottom:12px;">Session Analytics Overview</div>
                <div class="stat-box-row">
                    <div class="stat-mini-box">
                        <div class="stat-mini-num" id="stat-total-packets">0</div>
                        <div class="stat-mini-lbl">Total Packets</div>
                    </div>
                    <div class="stat-mini-box">
                        <div class="stat-mini-num" id="stat-total-alerts" style="color:var(--accent-rose);">0</div>
                        <div class="stat-mini-lbl">Alerts Triggered</div>
                    </div>
                    <div class="stat-mini-box">
                        <div class="stat-mini-num" id="stat-total-warnings" style="color:var(--accent-amber);">0</div>
                        <div class="stat-mini-lbl">Warnings Flagged</div>
                    </div>
                </div>

                <div class="stat-box-row" style="margin-top:12px;">
                    <div class="stat-mini-box">
                        <div class="stat-mini-num" id="stat-avg-temp" style="color:var(--accent-cyan);">-- &deg;C</div>
                        <div class="stat-mini-lbl">Session Avg Temp</div>
                    </div>
                    <div class="stat-mini-box">
                        <div class="stat-mini-num" id="stat-max-temp">-- &deg;C</div>
                        <div class="stat-mini-lbl">Peak Temp</div>
                    </div>
                    <div class="stat-mini-box">
                        <div class="stat-mini-num" id="stat-max-gas" style="color:var(--accent-pink);">-- V</div>
                        <div class="stat-mini-lbl">Peak Gas Sensor</div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let currentLimit = 25;

            function setChartLimit(limit) {
                currentLimit = limit;
                document.querySelectorAll('.range-pill').forEach(btn => {
                    btn.classList.toggle('active', btn.textContent.includes(limit));
                });
                refreshData();
            }

            // Initialize Dual-Axis Chart.js
            const ctx = document.getElementById('telemetryChart').getContext('2d');
            const telemetryChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: 'Oil Temp (°C)',
                            borderColor: '#38bdf8',
                            backgroundColor: 'rgba(56, 189, 248, 0.08)',
                            data: [],
                            yAxisID: 'yTemp',
                            borderWidth: 2.2,
                            tension: 0.35,
                            fill: true,
                            pointRadius: 2.5,
                            pointHoverRadius: 5
                        },
                        {
                            label: 'MQ-2 Combustible Gas (V)',
                            borderColor: '#f59e0b',
                            backgroundColor: 'transparent',
                            data: [],
                            yAxisID: 'yGas',
                            borderWidth: 2,
                            tension: 0.35,
                            pointRadius: 2
                        },
                        {
                            label: 'MQ-7 Carbon Monoxide (V)',
                            borderColor: '#ec4899',
                            backgroundColor: 'transparent',
                            data: [],
                            yAxisID: 'yGas',
                            borderWidth: 2,
                            tension: 0.35,
                            pointRadius: 2
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: {
                            position: 'top',
                            labels: { color: '#94a3b8', font: { family: 'Plus Jakarta Sans', size: 12 }, boxWidth: 14 }
                        },
                        tooltip: {
                            backgroundColor: '#0d1322',
                            titleColor: '#f8fafc',
                            bodyColor: '#cbd5e1',
                            borderColor: '#334155',
                            borderWidth: 1,
                            padding: 10
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(255, 255, 255, 0.04)' },
                            ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 8 }
                        },
                        yTemp: {
                            type: 'linear',
                            position: 'left',
                            suggestedMin: 20,
                            suggestedMax: 70,
                            title: { display: true, text: 'Temperature (°C)', color: '#38bdf8', font: { weight: 'bold' } },
                            grid: { color: 'rgba(255, 255, 255, 0.04)' },
                            ticks: { color: '#94a3b8', font: { family: 'JetBrains Mono', size: 10 } }
                        },
                        yGas: {
                            type: 'linear',
                            position: 'right',
                            suggestedMin: 0.0,
                            suggestedMax: 4.5,
                            title: { display: true, text: 'Sensor Voltage (V)', color: '#f59e0b', font: { weight: 'bold' } },
                            grid: { drawOnChartArea: false },
                            ticks: { color: '#94a3b8', font: { family: 'JetBrains Mono', size: 10 } }
                        }
                    }
                }
            });

            async function refreshData() {
                try {
                    const [dataRes, statsRes] = await Promise.all([
                        fetch(`/api/data?limit=${currentLimit}`),
                        fetch('/api/stats')
                    ]);
                    const records = await dataRes.json();
                    const stats = await statsRes.json();

                    if (!records.length) return;

                    const latest = records[records.length - 1];
                    const temp = latest[1];
                    const mq2 = latest[3];
                    const mq7 = latest[4];
                    const status = latest[5];

                    // Update Top Cards
                    document.getElementById('val-temp').innerHTML = `${temp.toFixed(1)}<span>&deg;C</span>`;
                    document.getElementById('val-mq2').innerHTML = `${mq2.toFixed(2)}<span>V</span>`;
                    document.getElementById('val-mq7').innerHTML = `${mq7.toFixed(2)}<span>V</span>`;
                    document.getElementById('val-health').innerHTML = `${stats.health_index}<span>%</span>`;
                    document.getElementById('val-ratio').textContent = `Ratio: ${stats.gas_ratio}`;
                    document.getElementById('last-packet-time').textContent = latest[0].split(' ')[1] || latest[0];

                    // Card badges & indicators
                    document.getElementById('temp-range-badge').textContent = temp > 60 ? 'CRITICAL' : (temp > 50 ? 'ELEVATED' : 'NOMINAL');
                    document.getElementById('temp-range-badge').style.color = temp > 60 ? '#f43f5e' : (temp > 50 ? '#f59e0b' : '#10b981');

                    document.getElementById('mq2-status-badge').textContent = mq2 > 2.8 ? 'ALERT' : (mq2 > 2.2 ? 'WARNING' : 'NORMAL');
                    document.getElementById('mq2-status-badge').style.color = mq2 > 2.8 ? '#f43f5e' : (mq2 > 2.2 ? '#f59e0b' : '#10b981');

                    document.getElementById('mq7-status-badge').textContent = mq7 > 3.2 ? 'ALERT' : (mq7 > 2.4 ? 'WARNING' : 'NORMAL');
                    document.getElementById('mq7-status-badge').style.color = mq7 > 3.2 ? '#f43f5e' : (mq7 > 2.4 ? '#f59e0b' : '#10b981');

                    // Banner update
                    const banner = document.getElementById('system-banner');
                    const bannerIcon = document.getElementById('banner-icon');
                    const bannerTitle = document.getElementById('banner-title');
                    const bannerDesc = document.getElementById('banner-desc');

                    banner.classList.remove('state-warning', 'state-alert');
                    if (status.includes('ALERT')) {
                        banner.classList.add('state-alert');
                        bannerIcon.textContent = '🚨';
                        bannerTitle.textContent = `SYSTEM ${status}`;
                        bannerDesc.textContent = `Anomaly trip active! Immediate inspection recommended according to IEEE C57.104 action matrix.`;
                        bannerTitle.style.color = '#f43f5e';
                    } else if (status.includes('WARNING')) {
                        banner.classList.add('state-warning');
                        bannerIcon.textContent = '⚠️';
                        bannerTitle.textContent = `SYSTEM ${status}`;
                        bannerDesc.textContent = `Incipient gas or thermal elevation observed. Trend growth monitored.`;
                        bannerTitle.style.color = '#f59e0b';
                    } else {
                        bannerIcon.textContent = '🟢';
                        bannerTitle.textContent = `SYSTEM NORMAL: ALL SENSORS STABLE`;
                        bannerDesc.textContent = `Thermal levels and combustible gas baselines operate within IEC 60599 & IEEE C57.104 nominal limits.`;
                        bannerTitle.style.color = '#10b981';
                    }

                    // Diagnostic Panel
                    const diagFault = document.getElementById('diag-fault-type');
                    diagFault.textContent = stats.fault_desc;
                    diagFault.style.color = status.includes('ALERT') ? '#f43f5e' : (status.includes('WARNING') ? '#f59e0b' : '#10b981');
                    document.getElementById('diag-ratio-val').textContent = `${stats.gas_ratio} (Duval Combustible/CO Proxy)`;
                    document.getElementById('diag-device-id').textContent = `${stats.device_id}`;

                    // Update Chart
                    telemetryChart.data.labels = records.map(r => (r[0].split(' ')[1] || r[0]).slice(0, 8));
                    telemetryChart.data.datasets[0].data = records.map(r => r[1]);
                    telemetryChart.data.datasets[1].data = records.map(r => r[3]);
                    telemetryChart.data.datasets[2].data = records.map(r => r[4]);
                    telemetryChart.update('none');

                    // Aggregate Statistics
                    document.getElementById('stat-total-packets').textContent = stats.total_readings.toLocaleString();
                    document.getElementById('stat-total-alerts').textContent = stats.total_alerts;
                    document.getElementById('stat-total-warnings').textContent = stats.total_warnings;
                    document.getElementById('stat-avg-temp').textContent = `${stats.aggregates.temp_avg} °C`;
                    document.getElementById('stat-max-temp').textContent = `${stats.aggregates.temp_max} °C`;
                    document.getElementById('stat-max-gas').textContent = `${Math.max(stats.aggregates.mq2_max, stats.aggregates.mq7_max)} V`;
                    document.getElementById('stat-temp-range').textContent = `Min: ${stats.aggregates.temp_min}° | Max: ${stats.aggregates.temp_max}°`;

                    // Event Log Table (recent 6 entries)
                    const tbody = document.getElementById('log-table-body');
                    const recentEvents = records.slice(-6).reverse();
                    document.getElementById('log-count').textContent = `${records.length} displayed`;

                    tbody.innerHTML = recentEvents.map(r => {
                        const rStatus = r[5];
                        let badgeClass = 'badge-normal';
                        if (rStatus.includes('ALERT')) badgeClass = 'badge-alert';
                        else if (rStatus.includes('WARNING')) badgeClass = 'badge-warning';

                        return `<tr>
                            <td>${(r[0].split(' ')[1] || r[0]).slice(0, 8)}</td>
                            <td>${r[1].toFixed(1)} °C</td>
                            <td>${r[3].toFixed(2)} V</td>
                            <td>${r[4].toFixed(2)} V</td>
                            <td class="${badgeClass}">${rStatus}</td>
                        </tr>`;
                    }).join('');

                } catch (err) {
                    console.error("Telemetry refresh error:", err);
                    document.getElementById('edge-node-status').style.color = '#f43f5e';
                    document.getElementById('conn-text').textContent = 'OFFLINE / RECONNECTING';
                }
            }

            refreshData();
            setInterval(refreshData, 2000);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
