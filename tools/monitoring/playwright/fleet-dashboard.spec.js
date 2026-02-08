// E2E smoke test for Grafana Fleet dashboard.
//
// This is intentionally light-weight: it verifies that the Device List panel
// renders rows and includes key columns (FW, Tables Sent, IsNew Tables).
//
// Run:
//   cd testing/playwright && npm test -- fleet-dashboard.spec.js

const { test, expect } = require("@playwright/test");
const {
  dismissUpdatePasswordIfPresent,
  loginIfNeeded,
  grafanaDsQuery,
} = require("./grafana_helpers");

const GRAFANA_URL =
  process.env.GRAFANA_URL || "http://10.0.0.160:3000";
const DASH_UID =
  process.env.GRAFANA_DASH_UID || "oig-fleet-influx-v2";
const DASH_SLUG =
  process.env.GRAFANA_DASH_SLUG || "oig-fleet-overview-v2";
const USERNAME = process.env.GRAFANA_USER;
const PASSWORD = process.env.GRAFANA_PASS;
const NEW_PASSWORD = process.env.GRAFANA_NEW_PASS;

test("fleet device list renders stable columns", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto(
    `${GRAFANA_URL}/d/${DASH_UID}/${DASH_SLUG}?orgId=1&from=now-1h&to=now`,
    { waitUntil: "domcontentloaded" }
  );
  await dismissUpdatePasswordIfPresent(page, NEW_PASSWORD);
  await loginIfNeeded(page, GRAFANA_URL, USERNAME, PASSWORD, NEW_PASSWORD);
  await dismissUpdatePasswordIfPresent(page, NEW_PASSWORD);

  // Verify panel queries via Grafana API, not DOM headers (Grafana table virtualizes columns).
  const dashResp = await page.request.get(`${GRAFANA_URL}/api/dashboards/uid/${DASH_UID}`);
  expect(dashResp.ok()).toBeTruthy();
  const dash = await dashResp.json();

  const deviceList = dash.dashboard.panels.find((p) => p.id === 6);
  expect(deviceList).toBeTruthy();
  const dsUid = deviceList.targets[0].datasource.uid;
  const dlq = deviceList.targets[0].query;
  const { fieldNames: dlFields, frames: dlFrames } = await grafanaDsQuery(
    page,
    GRAFANA_URL,
    dsUid,
    dlq,
    "now-1h",
    "now"
  );

  // Required stable fields in Device List data frame.
  for (const k of [
    "device_id",
    "mode",
    "box_status",
    "cloud_status",
    "last_seen_ts",
    "version",
    "fw",
    "tables_sent_1h",
    "isnew_tables_1h",
  ]) {
    expect(dlFields).toContain(k);
  }

  // There must be at least one device id link (drilldown to box detail).
  await expect(page.locator('a[href*=\"var-device_id=\"]').first()).toBeVisible();

  // FW value itself can be off-screen in the virtualized table. Assert via ds/query output.
  const f0 = dlFrames[0];
  const names = (f0?.schema?.fields || []).map((f) => f?.name);
  const fwIdx = names.indexOf("fw");
  expect(fwIdx).toBeGreaterThanOrEqual(0);
  const fwVals = f0?.data?.values?.[fwIdx] || [];
  expect(fwVals.some((v) => typeof v === "string" && v.startsWith("v."))).toBeTruthy();

  // Deviation panel must be configured as a bar chart and return string labels.
  const dev = dash.dashboard.panels.find((p) => p.id === 12);
  expect(dev).toBeTruthy();
  expect(dev.type).toBe("barchart");
  const devq = dev.targets[0].query;
  const { fieldNames: devFields, frames: devFrames } = await grafanaDsQuery(
    page,
    GRAFANA_URL,
    dsUid,
    devq,
    "now-1h",
    "now"
  );
  expect(devFields).toContain("device_label");
  expect(devFields).toContain("tables_dev_pct");
  const devF0 = devFrames[0];
  const devNames = (devF0?.schema?.fields || []).map((f) => f?.name);
  const labelIdx = devNames.indexOf("device_label");
  expect(labelIdx).toBeGreaterThanOrEqual(0);
  const labels = devF0?.data?.values?.[labelIdx] || [];
  expect(labels.some((v) => typeof v === "string" && v.startsWith("id:"))).toBeTruthy();

  // Disconnects panel must use a non-numeric x field so it doesn't render as "2.21 Bil".
  const disc = dash.dashboard.panels.find((p) => p.id === 8);
  expect(disc).toBeTruthy();
  expect(disc.type).toBe("barchart");
  expect(disc.options?.xField).toBe("device_label");
  const discq = disc.targets[0].query;
  const { fieldNames: discFields } = await grafanaDsQuery(
    page,
    GRAFANA_URL,
    dsUid,
    discq,
    "now-1h",
    "now"
  );
  expect(discFields).toContain("device_label");
});
