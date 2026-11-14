# Security Policy

## Reporting a Vulnerability

Use the repository's [private vulnerability reporting form](https://github.com/Muriel2Horak/oig-proxy/security/advisories/new). Include the OIG Proxy version or commit, deployment type, affected behavior, and minimal reproduction steps. Redact credentials, tokens, device identifiers, and any payload or capture data unrelated to the report.

## Local MQTT Control

OIG Proxy v2 receives local controls through MQTT. The proxy has no HTTP Control API or bearer-token file. The main broker authenticates clients and controls which topics each account may publish or subscribe to. The proxy uses `mqtt_username` and `mqtt_password` when a username is configured. Its MQTT connection does not enable TLS by setting a port, so keep it within a trusted network or protected transport.

Require authentication on the broker and give OIG Proxy a dedicated account. Limit command publication to trusted HA or automation accounts and the specific device topics they need. The current command paths are `oig/<device_id>/control/set` and `oig_local/<device_id>/set/<table>/<key>` with the default namespace. Grant the proxy the access it needs to receive those commands and publish state, availability, and discovery. Prevent other clients from spoofing those topics. [Application configuration](docs/v2/configuration.md) · [MQTT control behavior](docs/v2/twin.md)

Box writes are off by default. When enabled, the proxy checks the target, supported parameter, value constraints, and current device profile. It rejects retained and duplicate control deliveries and requires delivery at QoS 0. Broker access remains an independent control, since a client allowed to publish commands can request permitted changes. [Current parameter constraints](addon/oig-proxy/settings_constraints.py)

## Network and Diagnostic Data

Keep the TCP proxy and built-in DNS listener on the required local network. The Box TCP port defaults to 5710 and DNS uses port 53. Avoid exposing these services or the MQTT broker to the public internet. [Deployment](DEPLOYMENT.md)

Protect configuration, backups, logs, SQLite captures, and PCAP files. Configuration can contain broker credentials. Captures and detailed logs can contain device identifiers and operational data. External telemetry uses a separate broker and can be disabled with `telemetry_enabled`. Review the [telemetry and capture options](docs/v2/configuration.md) before collecting or sharing evidence.

See [security testing](docs/SECURITY_TESTING.md) for the current automated checks and local test commands.
