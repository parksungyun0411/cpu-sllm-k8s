"""kubectl JSON 출력에서 레플리카 상태를 읽는다."""

from __future__ import annotations

import json
import math
import subprocess


def _kubectl_json(*args: str) -> dict:
    out = subprocess.run(
        ["kubectl", *args, "-o", "json"], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)


def parse_deployment(obj: dict) -> dict:
    status = obj.get("status", {})
    return {
        "replicas_desired": obj.get("spec", {}).get("replicas", math.nan),
        "replicas_ready": status.get("readyReplicas", 0),
    }


def parse_hpa(obj: dict) -> dict:
    status = obj.get("status", {})
    return {"hpa_desired": status.get("desiredReplicas", math.nan)}


def replica_status(namespace: str, deployment: str) -> dict:
    row = parse_deployment(_kubectl_json("-n", namespace, "get", "deployment", deployment))
    try:
        row.update(parse_hpa(_kubectl_json("-n", namespace, "get", "hpa", deployment)))
    except subprocess.CalledProcessError:
        # HPA를 끈 격자 실험에서는 HPA 객체가 없다.
        row["hpa_desired"] = math.nan
    return row
