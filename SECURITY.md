# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 5.1.x   | :white_check_mark: |
| 5.0.x   | :x:                |
| 4.0.x   | :white_check_mark: |
| < 4.0   | :x:                |

## Reporting a Vulnerability

Use this section to tell people how to report a vulnerability.

Tell them where to go, how often they can expect to get an update on a
reported vulnerability, what to expect if the vulnerability is accepted or
declined, etc.

---

## Control API Authentication

The Control API uses token-based authentication for every request. No request succeeds without a valid token.

### How the token is generated

On first startup, the add-on generates a cryptographically random token using Python's `secrets.token_urlsafe(32)`. The token is written to `/data/control_api_token` inside the add-on's data directory and persists across restarts.

If the file already exists and contains a non-empty value, that token is reused. To rotate the token, delete the file and restart the add-on.

### How to read the token

From a Home Assistant terminal (Terminal & SSH add-on):

```bash
cat /addon_configs/<addon-slug>/data/control_api_token
```

Or, if you have shell access inside the container:

```bash
cat /data/control_api_token
```

### Authorization header format

Every request must include an `Authorization` header with a `Bearer` token:

```
Authorization: Bearer <your-token>
```

Requests without this header, or with a wrong token, receive `401 Unauthorized`:

```json
{"error": "unauthorized"}
```

---

## Whitelist Validation

The Control API uses a deny-by-default whitelist. Only the tables and items listed below can be written. Any attempt to write outside the whitelist is rejected with `400 Bad Request` and logged as a warning.

### Allowed tables and items

| Table | Allowed items |
|-------|--------------|
| `tbl_batt_prms` | `FMT_ON`, `BAT_MIN` |
| `tbl_boiler_prms` | `ISON`, `MANUAL`, `SSR0`, `SSR1`, `SSR2`, `OFFSET` |
| `tbl_box_prms` | `MODE`, `BAT_AC`, `BAT_FORMAT`, `SA`, `RQRESET` |
| `tbl_invertor_prms` | `GRID_PV_ON`, `GRID_PV_OFF`, `TO_GRID` |
| `tbl_invertor_prm1` | `AAC_MAX_CHRG`, `A_MAX_CHRG` |

Rejection responses:

```json
{"error": "tbl_name not in whitelist"}
{"error": "tbl_item not in whitelist"}
```

---

## curl Examples

Replace `<TOKEN>` with the value read from `/data/control_api_token`, and `<HA_IP>` with the IP address of your Home Assistant instance.

### Health check

```bash
curl -s \
  -H "Authorization: Bearer <TOKEN>" \
  http://<HA_IP>:8099/api/health
```

Expected response (example):

```json
{"status": "ok", "mode": "ONLINE", "box_connected": true}
```

### Write a setting (JSON body)

```bash
curl -s -X POST \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"tbl_name": "tbl_box_prms", "tbl_item": "MODE", "new_value": "1"}' \
  http://<HA_IP>:8099/api/setting
```

Expected success response:

```json
{"ok": true}
```

### Write a setting (XML body)

The endpoint also accepts a minimal XML snippet:

```bash
curl -s -X POST \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/xml" \
  -d '<Setting><TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><NewValue>1</NewValue></Setting>' \
  http://<HA_IP>:8099/api/setting
```

---

## Troubleshooting

### 401 Unauthorized

The token is missing, malformed, or wrong.

- Check the header is exactly `Authorization: Bearer <token>` with no extra spaces.
- Read the stored token again: `cat /data/control_api_token`.
- If the file is empty or missing, restart the add-on to regenerate it.

### 400 tbl_name not in whitelist

The table you're trying to write is not in `CONTROL_WRITE_WHITELIST`.

- Check the table name for typos (it's case-sensitive).
- Refer to the whitelist table above for the full list of allowed tables.

### 400 tbl_item not in whitelist

The item exists in the table, but isn't permitted.

- Check the item name for typos.
- Only the items listed in the whitelist table above are writable.

### 400 missing_fields

The request body is missing at least one required field.

- All three fields are required: `tbl_name`, `tbl_item`, `new_value`.

### 409 Conflict

The setting was accepted by the API but the BOX returned an error or wasn't ready.

- Make sure the BOX is connected and actively sending data.
- Check the add-on logs for details.

### Connection refused / no response

- Confirm the Control API port (default `8099`) is listed in the add-on's network config.
- Confirm the add-on is running.
- If you're calling from outside HA, make sure the port is reachable from your network.
