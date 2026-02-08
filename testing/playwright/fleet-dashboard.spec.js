// E2E smoke test for Grafana Fleet dashboard.
//
// This is intentionally light-weight: it verifies that the Device List panel
// renders rows and includes key columns (FW, Tables Sent, IsNew Tables).
//
// Run:
//   cd testing/playwright && npm test -- fleet-dashboard.spec.js

const { test, expect } = require("@playwright/test");

const GRAFANA_URL =
  process.env.GRAFANA_URL || "http://10.0.0.160:3000";
const DASH_UID =
  process.env.GRAFANA_DASH_UID || "oig-fleet-influx-v2";
const DASH_SLUG =
  process.env.GRAFANA_DASH_SLUG || "oig-fleet-overview-v2";
const USERNAME = process.env.GRAFANA_USER || "admin";
const PASSWORD = process.env.GRAFANA_PASS || "admin";
const NEW_PASSWORD = process.env.GRAFANA_NEW_PASS;

async function grafanaDsQuery(page, datasourceUid, flux) {
  const now = Date.now();
  const from = now - 60 * 60 * 1000;
  const resp = await page.request.post(`${GRAFANA_URL}/api/ds/query`, {
    data: {
      queries: [
        {
          refId: "A",
          datasource: { uid: datasourceUid },
          queryType: "flux",
          rawQuery: true,
          resultFormat: "table",
          query: flux,
        },
      ],
      from: String(from),
      to: String(now),
    },
  });
  if (!resp.ok()) {
    throw new Error(`Grafana ds/query failed: ${resp.status()} ${resp.statusText()}`);
  }
  const body = await resp.json();
  const frames = body?.results?.A?.frames || [];
  if (!frames.length) return { fieldNames: [], frames: [] };
  const fieldNames = (frames[0]?.schema?.fields || [])
    .map((f) => f?.name)
    .filter(Boolean);
  return { fieldNames, frames };
}

async function dismissUpdatePasswordIfPresent(page) {
  const heading = page.getByRole("heading", { name: /update your password/i });
  const skip = page.getByRole("button", { name: /^skip$/i });

  // The dialog can appear a moment after navigation/login; wait briefly.
  try {
    await heading.first().waitFor({ timeout: 3_000 });
  } catch {
    return;
  }

  for (let i = 0; i < 3; i++) {
    if (!(await heading.count())) return;
    if (!(await skip.count())) return;

    await skip.click({ force: true });
    await page.waitForLoadState("domcontentloaded");
    try {
      await heading.first().waitFor({ state: "detached", timeout: 5_000 });
      return;
    } catch {
      // Keep looping.
    }
  }

  // If the instance enforces a password change, allow the test runner to opt-in
  // via env var. We do NOT change passwords implicitly.
  if (!NEW_PASSWORD) {
    throw new Error(
      "Grafana requires a password change. Set GRAFANA_NEW_PASS to let the test proceed."
    );
  }

  await page.getByRole("textbox", { name: /^new password$/i }).fill(NEW_PASSWORD);
  await page
    .getByRole("textbox", { name: /^confirm new password$/i })
    .fill(NEW_PASSWORD);
  await page.getByRole("button", { name: /^submit$/i }).click();
  await page.waitForLoadState("domcontentloaded");
}

async function loginIfNeeded(page) {
  await dismissUpdatePasswordIfPresent(page);

  // Grafana login form varies by version. Use role-based selectors.
  const userInput = page.getByRole("textbox", { name: /email or username/i });
  try {
    await userInput.first().waitFor({ timeout: 5_000 });
  } catch {
    return;
  }

  await userInput.first().fill(USERNAME);
  await page.getByRole("textbox", { name: /^password$/i }).fill(PASSWORD);
  await page.getByRole("button", { name: /log in/i }).click();
  await page.waitForLoadState("domcontentloaded");
  await dismissUpdatePasswordIfPresent(page);
}

test("fleet device list renders stable columns", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto(
    `${GRAFANA_URL}/d/${DASH_UID}/${DASH_SLUG}?orgId=1&from=now-1h&to=now`,
    { waitUntil: "domcontentloaded" }
  );
  await dismissUpdatePasswordIfPresent(page);
  await loginIfNeeded(page);
  await dismissUpdatePasswordIfPresent(page);

  // Verify panel queries via Grafana API, not DOM headers (Grafana table virtualizes columns).
  const dashResp = await page.request.get(`${GRAFANA_URL}/api/dashboards/uid/${DASH_UID}`);
  expect(dashResp.ok()).toBeTruthy();
  const dash = await dashResp.json();

  const deviceList = dash.dashboard.panels.find((p) => p.id === 6);
  expect(deviceList).toBeTruthy();
  const dsUid = deviceList.targets[0].datasource.uid;
  const dlq = deviceList.targets[0].query;
  const { fieldNames: dlFields, frames: dlFrames } = await grafanaDsQuery(page, dsUid, dlq);

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
  const { fieldNames: devFields, frames: devFrames } = await grafanaDsQuery(page, dsUid, devq);
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
  const { fieldNames: discFields } = await grafanaDsQuery(page, dsUid, discq);
  expect(discFields).toContain("device_label");
});
