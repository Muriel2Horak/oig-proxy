// Shared helpers for Grafana smoke tests.
//
// Intentionally minimal: these tests are "local monitoring" and should not
// require CI secrets. Callers must provide credentials via env vars.

async function dismissUpdatePasswordIfPresent(page, newPassword) {
  const heading = page.getByRole("heading", { name: /update your password/i });
  const skip = page.getByRole("button", { name: /^skip$/i });

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
      // retry
    }
  }

  if (!newPassword) {
    throw new Error(
      "Grafana requires a password change. Set GRAFANA_NEW_PASS to let the test proceed."
    );
  }

  await page.getByRole("textbox", { name: /^new password$/i }).fill(newPassword);
  await page
    .getByRole("textbox", { name: /^confirm new password$/i })
    .fill(newPassword);
  await page.getByRole("button", { name: /^submit$/i }).click();
  await page.waitForLoadState("domcontentloaded");
}

async function loginIfNeeded(page, grafanaUrl, username, password, newPassword) {
  await dismissUpdatePasswordIfPresent(page, newPassword);

  const userInput = page.getByRole("textbox", { name: /email or username/i });
  try {
    await userInput.first().waitFor({ timeout: 5_000 });
  } catch {
    return;
  }

  if (!username || !password) {
    throw new Error("Missing Grafana credentials: set GRAFANA_USER and GRAFANA_PASS.");
  }

  await userInput.first().fill(username);
  await page.getByRole("textbox", { name: /^password$/i }).fill(password);
  await page.getByRole("button", { name: /log in/i }).click();
  await page.waitForLoadState("domcontentloaded");
  await dismissUpdatePasswordIfPresent(page, newPassword);
}

async function grafanaDsQuery(
  page,
  grafanaUrl,
  datasourceUid,
  flux,
  from = "now-1h",
  to = "now"
) {
  const resp = await page.request.post(`${grafanaUrl}/api/ds/query`, {
    data: {
      queries: [
        {
          refId: "A",
          datasource: { uid: datasourceUid },
          queryType: "flux",
          rawQuery: true,
          resultFormat: "table",
          // Avoid truncation errors for 1m windows over 24h.
          maxDataPoints: 5000,
          intervalMs: 60_000,
          query: flux,
        },
      ],
      from,
      to,
    },
  });

  if (!resp.ok()) {
    let detail = "";
    try {
      detail = ` body=${JSON.stringify(await resp.json())}`;
    } catch {
      // ignore
    }
    throw new Error(`Grafana ds/query failed: ${resp.status()} ${resp.statusText()}${detail}`);
  }

  const body = await resp.json();
  const result = body?.results?.A;
  if (result?.error) {
    throw new Error(`Grafana ds/query Flux error: ${result.error}`);
  }
  const frames = result?.frames || [];
  if (!frames.length) return { fieldNames: [], frames: [] };
  const fieldNames = (frames[0]?.schema?.fields || []).map((f) => f?.name).filter(Boolean);
  return { fieldNames, frames };
}

function getColumnValues(frame, colName) {
  const names = (frame?.schema?.fields || []).map((f) => f?.name);
  const idx = names.indexOf(colName);
  if (idx < 0) return [];
  return frame?.data?.values?.[idx] || [];
}

module.exports = {
  dismissUpdatePasswordIfPresent,
  loginIfNeeded,
  grafanaDsQuery,
  getColumnValues,
};

