// E2E smoke test for Grafana Box Detail dashboard.
//
// Validates that for a known active device the dashboard panel queries
// compile and return data for key panels (sessions, tables sent, errors trend).
//
// Run:
//   cd tools/monitoring/playwright && npm test -- box-dashboard.spec.js

const { test, expect } = require("@playwright/test");
const {
  dismissUpdatePasswordIfPresent,
  loginIfNeeded,
  grafanaDsQuery,
  getColumnValues,
} = require("./grafana_helpers");

const GRAFANA_URL = process.env.GRAFANA_URL;
const DASH_UID = process.env.GRAFANA_BOX_DASH_UID || "oig-box-influx";
const DASH_SLUG = process.env.GRAFANA_BOX_DASH_SLUG || "oig-box-detail-24h";
const USERNAME = process.env.GRAFANA_USER;
const PASSWORD = process.env.GRAFANA_PASS;
const NEW_PASSWORD = process.env.GRAFANA_NEW_PASS;
const DEVICE_ID = process.env.GRAFANA_DEVICE_ID || "2209234094";
const INFLUX_DS_UID = process.env.GRAFANA_INFLUX_DS_UID || "afc1e5763y6f4d";

test("box detail dashboard returns data for key panels", async ({ page }) => {
  test.setTimeout(240_000);
  if (!GRAFANA_URL) throw new Error("Missing GRAFANA_URL (Grafana base URL).");

  await page.goto(
    `${GRAFANA_URL}/d/${DASH_UID}/${DASH_SLUG}?orgId=1&from=now-24h&to=now&var-device_id=${DEVICE_ID}`,
    { waitUntil: "domcontentloaded" }
  );

  await dismissUpdatePasswordIfPresent(page, NEW_PASSWORD);
  await loginIfNeeded(page, GRAFANA_URL, USERNAME, PASSWORD, NEW_PASSWORD);
  await dismissUpdatePasswordIfPresent(page, NEW_PASSWORD);

  const dashResp = await page.request.get(`${GRAFANA_URL}/api/dashboards/uid/${DASH_UID}`);
  expect(dashResp.ok()).toBeTruthy();
  const dash = await dashResp.json();

  const panels = dash.dashboard.panels;
  const byId = (id) => panels.find((p) => p.id === id);

  const dsUid = INFLUX_DS_UID;

  // Layout sanity: panel rows should be contiguous (no big empty gaps).
  const ys = [...new Set(panels.map((p) => p.gridPos?.y).filter((v) => typeof v === "number"))]
    .sort((a, b) => a - b);
  expect(ys).toEqual([0, 6, 14, 22, 30, 38, 46]);

  // 1) Top tables over time must return some points.
  const topTables = byId(11);
  expect(topTables).toBeTruthy();
  const q11 = topTables.targets[0].query.replaceAll("${device_id}", DEVICE_ID);
  const r11 = await grafanaDsQuery(page, GRAFANA_URL, dsUid, q11, "now-24h", "now");
  expect(r11.frames.length).toBeGreaterThan(0);

  // 2) Tables Sent KPI must be numeric (may be 0 if truly no data).
  const tablesSent = byId(5);
  expect(tablesSent).toBeTruthy();
  const q5 = tablesSent.targets[0].query.replaceAll("${device_id}", DEVICE_ID);
  const r5 = await grafanaDsQuery(page, GRAFANA_URL, dsUid, q5, "now-24h", "now");
  const v5 = getColumnValues(r5.frames[0], "_value");
  expect(v5.length).toBeGreaterThan(0);
  expect(typeof v5[0]).toBe("number");

  // 3) Box Sessions table should have at least one row in last 24h for an active box.
  const boxSessions = byId(18);
  expect(boxSessions).toBeTruthy();
  const q18 = boxSessions.targets[0].query.replaceAll("${device_id}", DEVICE_ID);
  const r18 = await grafanaDsQuery(page, GRAFANA_URL, dsUid, q18, "now-24h", "now");
  const dur = getColumnValues(r18.frames[0], "duration_s");
  expect(dur.length).toBeGreaterThan(0);

  // 4) Cloud Sessions table should have at least one row (hybrid/online boxes typically try cloud).
  const cloudSessions = byId(19);
  expect(cloudSessions).toBeTruthy();
  const q19 = cloudSessions.targets[0].query.replaceAll("${device_id}", DEVICE_ID);
  const r19 = await grafanaDsQuery(page, GRAFANA_URL, dsUid, q19, "now-24h", "now");
  const cdur = getColumnValues(r19.frames[0], "duration_s");
  expect(cdur.length).toBeGreaterThan(0);

  // 5) Errors trend query should compile (may be empty).
  const errTrend = byId(21);
  expect(errTrend).toBeTruthy();
  const q21 = errTrend.targets[0].query.replaceAll("${device_id}", DEVICE_ID);
  await grafanaDsQuery(page, GRAFANA_URL, dsUid, q21, "now-24h", "now");

  // 6) Ensure remaining core panels compile and return frames where expected.
  //
  // Notes:
  // - Some tables can be legitimately empty in a 24h window (e.g. modem resets, debug logs).
  // - We still assert the Flux compiles and the datasource query succeeds.
  // "Must have frames" should be limited to signals that are expected for any active box.
  // Counters like timeouts/modem resets can be legitimately absent (Grafana will show "No data"),
  // so we only require them to compile.
  const mustReturnFrames = new Set([1, 2, 7, 8, 9, 11, 18, 19]);
  const shouldCompile = [3, 4, 5, 6, 10, 12, 13, 14, 15, 16, 17, 20, 21];
  for (const pid of [...mustReturnFrames, ...shouldCompile]) {
    const p = byId(pid);
    expect(p).toBeTruthy();
    const t = (p.targets || [])[0];
    expect(t).toBeTruthy();
    const q = (t.query || "").replaceAll("${device_id}", DEVICE_ID);
    let r;
    try {
      r = await grafanaDsQuery(page, GRAFANA_URL, dsUid, q, "now-24h", "now");
    } catch (e) {
      const msg = e && e.message ? e.message : String(e);
      throw new Error(`panel_id=${pid} title="${p.title}" query_len=${q.length} :: ${msg}`);
    }
    if (mustReturnFrames.has(pid)) {
      expect(r.frames.length).toBeGreaterThan(0);
    }
  }

  // Quick UI check: variable should be applied.
  await expect(page.locator(`text=${DEVICE_ID}`).first()).toBeVisible();
});
