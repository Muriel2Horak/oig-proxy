# Security Policy

## Supported versions

| Version | Hardened local control |
|---|---|
| `2.2.x` | Supported |
| `<=2.1.x` | Unsupported |

Version 2.2.x is the first supported release for durable, device-bound local-setting
transactions. A 2.1.1 rollback remains an operational escape hatch, but it does not
provide the 2.2.x local-control guarantees.

## Reporting a vulnerability

Report suspected vulnerabilities privately through a
[GitHub security advisory](https://github.com/Muriel2Horak/oig-proxy/security/advisories/new).
Do not open a public issue and do not submit credentials, complete captured payloads,
Home Assistant secrets, MQTT passwords, or production database files.

Include the affected version, deployment mode, preconditions, observed impact, and a
minimal redacted reproduction. The maintainers will acknowledge a complete report,
coordinate validation and remediation privately, and publish disclosure details only
after a fix or an explicit risk decision is ready.

## Local-control security boundary

Local control is disabled by default. When enabled, it accepts only the exact learned
device topic, rejects retained and malformed input, validates allowlisted Decimal
values, serializes XML safely, and durably prepares work before any BOX write. Delivery
ACK/NACK evidence is not execution confirmation; confirmed state requires an exact BOX
event. Store, migration, session, CRC, deadline, correlation, render, or write-outcome
failures prohibit the proxy-owned local mutation.

The automated release evidence is documented in
[`docs/SECURITY_TESTING.md`](docs/SECURITY_TESTING.md). It uses loopback fake endpoints;
no live BOX command, production MQTT/cloud connection, or Home Assistant deployment is
part of the release gate.

## Upgrade and rollback

Back up the add-on data directory before changing versions. Version 2.2.0 validates
schema v1 in `/data/twin_queue.db` and never downgrades or recreates an incompatible or
corrupt database. A 2.1.1 rollback ignores and preserves `/data/twin_queue.db`; local
control must remain disabled until a supported 2.2.x runtime is restored.
