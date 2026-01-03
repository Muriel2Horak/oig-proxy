#!/usr/bin/env python3
import base64
import json
import os
import sys
from pathlib import Path
from urllib import error, parse, request


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT_DIR / ".env"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] in ("'", '"') and value[-1] == value[0]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def api_request(
    method: str,
    base_url: str,
    path: str,
    auth_header: str,
    params: dict[str, str] | None = None,
) -> dict:
    url = base_url.rstrip("/") + path
    data = None
    if params:
        encoded = parse.urlencode(params)
        if method == "GET":
            url = f"{url}?{encoded}"
        else:
            data = encoded.encode("utf-8")
    req = request.Request(url, data=data, method=method)
    req.add_header("Authorization", auth_header)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with request.urlopen(req, timeout=30) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def main() -> int:
    load_env(ENV_PATH)

    base_url = os.getenv("SONAR_HOST_URL") or os.getenv("SONAR_URL")
    if not base_url:
        print("Missing SONAR_HOST_URL or SONAR_URL for quality gate config.", file=sys.stderr)
        return 2
    project_key = os.getenv("SONAR_PROJECT_KEY", "oig_proxy")
    gate_name = os.getenv("SONAR_QUALITY_GATE_NAME", "Security A +0")

    token = os.getenv("SONAR_TOKEN")
    login = os.getenv("SONAR_LOGIN")
    password = os.getenv("SONAR_PASS")
    if token:
        auth_raw = f"{token}:"
    elif login and password:
        auth_raw = f"{login}:{password}"
    else:
        print("Missing Sonar credentials. Set SONAR_TOKEN or SONAR_LOGIN/SONAR_PASS.", file=sys.stderr)
        return 2

    auth_header = "Basic " + base64.b64encode(auth_raw.encode("utf-8")).decode("ascii")

    try:
        metrics_resp = api_request("GET", base_url, "/api/metrics/search", auth_header, {"ps": "500"})
        metrics = {m.get("key") for m in metrics_resp.get("metrics", [])}

        qg_list = api_request("GET", base_url, "/api/qualitygates/list", auth_header)
        gate = next((g for g in qg_list.get("qualitygates", []) if g.get("name") == gate_name), None)
        if gate is None:
            created = api_request("POST", base_url, "/api/qualitygates/create", auth_header, {"name": gate_name})
            gate_id = created.get("id") or created.get("qualityGate", {}).get("id")
        else:
            gate_id = gate.get("id")

        if not gate_id:
            print(f"Failed to resolve quality gate id for '{gate_name}'.", file=sys.stderr)
            return 2

        gate_detail = api_request("GET", base_url, "/api/qualitygates/show", auth_header, {"id": str(gate_id)})
        existing_conditions = {
            c.get("metric"): c for c in gate_detail.get("conditions", []) if c.get("metric")
        }

        desired = [
            ("security_rating", "GT", "1"),
            ("new_security_rating", "GT", "1"),
            ("new_vulnerabilities", "GT", "0"),
        ]
        if "new_security_hotspots_reviewed" in metrics:
            desired.append(("new_security_hotspots_reviewed", "LT", "100"))
        elif "security_hotspots_reviewed" in metrics:
            desired.append(("security_hotspots_reviewed", "LT", "100"))

        desired_metrics = {metric for metric, _, _ in desired}
        for metric, op, error_value in desired:
            if metric not in metrics:
                print(f"Skipping metric '{metric}' (not available).", file=sys.stderr)
                continue
            existing = existing_conditions.get(metric)
            if existing:
                existing_error = str(existing.get("error"))
                existing_op = existing.get("op")
                if existing_error == error_value and existing_op == op:
                    continue
                api_request(
                    "POST",
                    base_url,
                    "/api/qualitygates/update_condition",
                    auth_header,
                    {
                        "id": str(existing.get("id")),
                        "metric": metric,
                        "op": op,
                        "error": error_value,
                    },
                )
            else:
                api_request(
                    "POST",
                    base_url,
                    "/api/qualitygates/create_condition",
                    auth_header,
                    {
                        "gateId": str(gate_id),
                        "metric": metric,
                        "op": op,
                        "error": error_value,
                    },
                )

        for metric, existing in existing_conditions.items():
            if metric in desired_metrics:
                continue
            condition_id = existing.get("id")
            if not condition_id:
                continue
            api_request(
                "POST",
                base_url,
                "/api/qualitygates/delete_condition",
                auth_header,
                {"id": str(condition_id)},
            )

        api_request(
            "POST",
            base_url,
            "/api/qualitygates/select",
            auth_header,
            {"projectKey": project_key, "gateId": str(gate_id)},
        )
        print(f"Quality gate '{gate_name}' applied to project '{project_key}'.")
        return 0
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        print(f"Sonar API error: {exc.code} {exc.reason}", file=sys.stderr)
        if body:
            print(body, file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
