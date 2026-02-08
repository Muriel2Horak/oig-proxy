# Grafana Dashboard Smoke Tests (Playwright)

This folder contains lightweight Playwright smoke tests for Grafana dashboards.

## Prereqs
- Node.js + npm (for `npx` / Playwright)

## Install
```bash
cd tools/monitoring/playwright
npm ci
```

## Run
```bash
cd tools/monitoring/playwright
npm test -- fleet-dashboard.spec.js
npm test -- box-dashboard.spec.js
```

## Env Vars
- `GRAFANA_URL` (default: `http://10.0.0.160:3000`)
- `GRAFANA_USER` (default: `admin`)
- `GRAFANA_PASS` (default: `admin`)
- `GRAFANA_DASH_UID` (default: `oig-fleet-influx-v2`)
- `GRAFANA_DASH_SLUG` (default: `oig-fleet-overview-v2`)
- `GRAFANA_BOX_DASH_UID` (default: `oig-box-influx`)
- `GRAFANA_BOX_DASH_SLUG` (default: `oig-box-detail-24h`)
- `GRAFANA_DEVICE_ID` (default: `2209234094`)
- `GRAFANA_INFLUX_DS_UID` (default: `afc1e5763y6f4d`)
