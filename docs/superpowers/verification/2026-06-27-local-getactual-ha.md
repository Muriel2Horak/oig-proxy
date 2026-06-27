# Local GetActual HA Verification

Date: 2026-06-27
Target: Home Assistant add-on `d7b5d5b1_oig_proxy`
Box: `2206237016` at `10.0.0.166`

## Deployment

- `./deploy_to_haos.sh` now deploys to the active Supervisor app repository path: `/mnt/data/supervisor/apps/git/d7b5d5b1/addon/oig-proxy`.
- The script no longer calls direct `docker stop` before rebuild. Supervisor rebuild manages stop/start; direct stop triggered watchdog job collisions.
- Deployed source manifest and runtime `/app/config.json` both report `version: 2.1.1`.
- Installed add-on version after `ha store apps update d7b5d5b1_oig_proxy`: `2.1.1`.
- Runtime image after update: `d7b5d5b1/amd64-addon-oig_proxy:2.1.1`.
- HA schema exposes `local_getactual_enabled` and `local_getactual_interval_s`.

## Enabled Test

Configuration set through Supervisor API:

```text
local_getactual_enabled=true
local_getactual_interval_s=10
```

After restart, runtime `/data/options.json` confirmed:

```text
{'local_getactual_enabled': True, 'local_getactual_interval_s': 10}
```

Log evidence:

```text
2026-06-27 19:28:52 FRAME direction=proxy_to_box payload=<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>
2026-06-27 19:28:52 Sent local GetActual ... interval=10s
2026-06-27 19:29:02 FRAME direction=proxy_to_box payload=<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>
2026-06-27 19:29:02 Sent local GetActual ... interval=10s
2026-06-27 19:29:12 FRAME direction=proxy_to_box payload=<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>
2026-06-27 19:29:12 Sent local GetActual ... interval=10s
```

Box response evidence included `box_to_cloud` frames after local prompts, including `tbl_actual` at `2026-06-27 19:29:16` and repeated later table frames.

Capture DB evidence from `/data/payloads.db`:

```text
(1601533, '2026-06-27T17:30:31+00:00', None, 'ACK', 'proxy_to_box', 75, '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>\r\n')
(1601526, '2026-06-27T17:30:21+00:00', None, 'ACK', 'proxy_to_box', 75, '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>\r\n')
(1601519, '2026-06-27T17:30:11+00:00', None, 'ACK', 'proxy_to_box', 75, '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>\r\n')
(1601516, '2026-06-27T17:30:01+00:00', None, 'ACK', 'proxy_to_box', 75, '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>\r\n')
```

Direction count during the enabled observation window included `proxy_to_box=10`.

## Disabled Test

Configuration set through Supervisor API:

```text
local_getactual_enabled=false
local_getactual_interval_s=10
```

After restart, runtime `/data/options.json` confirmed:

```text
{'local_getactual_enabled': False, 'local_getactual_interval_s': 10}
```

After disabled restart at `2026-06-27 19:31:05`, logs showed box reconnect and normal `box_to_cloud` frames but no `Sent local GetActual` and no `direction=proxy_to_box`.

Last captured local GetActual before disabling:

```text
(1601545, '2026-06-27T17:30:51+00:00', 'proxy_to_box', '<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>\r\n')
```

## Notes

- While testing an unreleased version, Supervisor needed `ha supervisor restart` followed by `ha store apps update d7b5d5b1_oig_proxy` to move installed metadata from `2.1.0` to `2.1.1`.
- The add-on UI/API exposes the new options only after installed metadata is `2.1.1`.
- With the option disabled, no new proxy-injected frames were observed.
