# AI-Based Smart Transformer Oil-Quality Predictor
### Using Dissolved-Gas Sensor Trends

**Engineering Clinics Project | B.Tech 3rd Semester | CSE**  
Chandigarh Engineering College, Jhanjeri (CGC University, Mohali)  
Guide: Ms. Aparna Sikarkar | September 2026

---

## Team
| Name | Roll No | Role |
|---|---|---|
| Himanshu Kumar | 2546128 | Team Leader |
| Md Saklain Shahid | 2546170 | Member |
| Nav Kumar Mahato | 2546184 | Member |
| Paras | 2546195 | Member |

---

## Overview
An IoT system for **continuous real-time transformer oil condition monitoring** using an ESP32-C3 microcontroller with MQ-2, MQ-7, and DS18B20 sensors. Sensor readings are posted every 2 seconds to a FastAPI backend that runs anomaly detection and displays a live dashboard.

**4,179 real readings collected** across 3 sessions (Oct–Nov 2026).  
**1,391 anomaly events** automatically detected.  
**Hardware cost: ~₹1,500**

---

## System Architecture

```
ESP32-C3 Edge Node
  ├── DS18B20 (GPIO 4)  — Oil temperature
  ├── MQ-2    (GPIO 0)  — Combustible gas
  ├── MQ-7    (GPIO 1)  — Carbon monoxide
  ├── Green LED (GPIO 7) — NORMAL
  └── Red LED   (GPIO 8) — ALERT
       │
       │ HTTP POST every 2 seconds (Wi-Fi)
       ▼
FastAPI Backend (Python)
  ├── POST /api/telemetry — ingest + anomaly detection
  ├── GET  /api/data      — last 25 readings
  ├── GET  /api/stats     — totals, alerts, device info
  └── GET  /             — live Chart.js dashboard
       │
       ▼
SQLite Database (oil_telemetry.db)
```

---

## Files
| File | Description |
|---|---|
| `server.py` | FastAPI backend v1 — telemetry, anomaly engine, dashboard |
| `improv/server.py` | FastAPI backend v2 — adds stats endpoint, alert log, session min/max |
| `simulate.py` | Python ESP32 simulator for testing without hardware |
| `sketch_oct20a/sketch_oct20a.ino` | ESP32-C3 Arduino firmware |
| `oil_telemetry.db` | SQLite database with 691 real readings (Nov 2026) |
| `oil_monitor.db` | SQLite database with 2,731 real readings (Oct 2026) |

---

## Anomaly Detection Logic
```python
if temp_c > 60.0:
    status = "ALERT: Thermal Overrun"
elif mq2_v > 2.80 or mq7_v > 3.20:
    status = "ALERT: Off-Gas Anomaly"
else:
    status = "NORMAL"
```

---

## Real Data Results
| Session | Dates | Readings | Normal | Alerts |
|---|---|---|---|---|
| oil_monitor.db | Oct 20–22, 2026 | 2,731 | 1,455 (53%) | 1,276 (47%) |
| oil_telemetry.db | Nov 1–4, 2026 | 691 | 619 (90%) | 72 (10%) |
| improv/oil_telemetry.db | Nov 4, 2026 | 757 | 714 (94%) | 43 (6%) |
| **TOTAL** | | **4,179** | **2,788** | **1,391** |

---

## How to Run

### 1. Backend Server
```bash
pip install fastapi uvicorn pydantic
python server.py
# Dashboard at http://localhost:8000
```

### 2. Simulator (no hardware needed)
```bash
pip install requests
python simulate.py
```

### 3. Hardware (ESP32-C3)
- Open `sketch_oct20a/sketch_oct20a.ino` in Arduino IDE
- Set your Wi-Fi SSID, password, and server IP in the config section
- Upload to ESP32-C3

---

## Hardware Components
| Component | Purpose | GPIO |
|---|---|---|
| ESP32-C3 | Main microcontroller | — |
| DS18B20 | Temperature sensor (1-Wire) | GPIO 4 |
| MQ-2 | Combustible gas (via 10kΩ/10kΩ divider) | GPIO 0 (ADC) |
| MQ-7 | Carbon monoxide (via 10kΩ/10kΩ divider) | GPIO 1 (ADC) |
| Green LED | NORMAL status | GPIO 7 |
| Red LED | ALERT status | GPIO 8 |

---

## Future Work
- [ ] Train Random Forest classifier on labelled DGA datasets
- [ ] Implement Duval Triangle gas-ratio logic
- [ ] Replace SQLite with InfluxDB + Grafana
- [ ] Add GSM/4G for remote field deployment
- [ ] Calibrate MQ sensors to ppm concentrations

---

## References
1. IEEE Std C57.104-2019 — Guide for the Interpretation of Gases in Oil-Immersed Transformers
2. IEC 60599:2015 — Guidance on interpretation of dissolved and free gases analysis
3. M. Duval, IEEE Electrical Insulation Magazine, vol. 32, no. 3, 2016
