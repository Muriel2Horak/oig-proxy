#!/usr/bin/env python3
"""Test Grafana queries directly via API."""

import requests
import json

GRAFANA_URL = "http://10.0.0.160:3000"
AUTH = ("oigadmin", "oig123")

# Test Mode query
response = requests.post(
    f"{GRAFANA_URL}/api/ds/query",
    auth=AUTH,
    json={
        "queries": [{
            "refId": "A",
            "datasource": {"type": "influxdb", "uid": "afc1e5763y6f4d"},
            "query": 'from(bucket: "telemetry") |> range(start: -10m) |> filter(fn: (r) => r.device_id == "2303234502") |> filter(fn: (r) => r._field == "mode_value") |> last()'
        }]
    }
)

print("=== MODE QUERY TEST ===\n")
print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    if 'results' in data and 'A' in data['results']:
        frames = data['results']['A'].get('frames', [])
        if frames:
            frame = frames[0]
            print(f"✓ Got {len(frame.get('data', {}).get('values', [[]])[0])} data points")
            
            # Get the mode value
            if len(frame['data']['values']) > 1:
                mode_values = frame['data']['values'][1]
                if mode_values:
                    print(f"✓ Mode value: {mode_values[0]}")
                else:
                    print("✗ No mode values in response")
            else:
                print("✗ No data values in frame")
        else:
            print("✗ No frames in response")
            print(json.dumps(data, indent=2))
    else:
        print("✗ Unexpected response structure")
        print(json.dumps(data, indent=2))
else:
    print(f"✗ Error: {response.text}")

# Test Version query
print("\n=== VERSION QUERY TEST ===\n")

response = requests.post(
    f"{GRAFANA_URL}/api/ds/query",
    auth=AUTH,
    json={
        "queries": [{
            "refId": "A",
            "datasource": {"type": "influxdb", "uid": "afc1e5763y6f4d"},
            "query": 'from(bucket: "telemetry") |> range(start: -10m) |> filter(fn: (r) => r.device_id == "2303234502") |> filter(fn: (r) => r._field == "version_value") |> last()'
        }]
    }
)

print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    if 'results' in data and 'A' in data['results']:
        frames = data['results']['A'].get('frames', [])
        if frames:
            frame = frames[0]
            if len(frame['data']['values']) > 1:
                version_values = frame['data']['values'][1]
                if version_values:
                    print(f"✓ Version value: {version_values[0]}")
                else:
                    print("✗ No version values")
                    print(json.dumps(frame, indent=2))
