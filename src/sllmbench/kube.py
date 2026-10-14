"""kubectl 출력에서 레플리카 상태와 스케일 시점을 읽는다."""

from __future__ import annotations

import json
import math
import subprocess
from datetime import UTC, datetime


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


def _epoch(text: str) -> float:
    """kubectl이 찍는 RFC 3339 시각(UTC, 초 단위)을 unix 초로 바꾼다. <none>이면 nan."""
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return math.nan


def parse_hpa_watch(text: str) -> list[dict]:
    """`kubectl get hpa --watch`가 남긴 (lastScaleTime, desiredReplicas) 줄에서 스케일 시점을 뽑는다.

    HPA는 메트릭이 바뀔 때마다 상태를 갱신하므로 같은 줄이 여러 번 나온다. lastScaleTime이 바뀐 줄만 센다.
    """
    events: list[dict] = []
    prev: tuple[float, int] | None = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        at, desired = _epoch(parts[0]), int(parts[1])
        if prev is not None and not math.isnan(at) and at != prev[0] and desired != prev[1]:
            events.append({"timestamp": at, "from_replicas": prev[1], "to_replicas": desired})
        prev = (at, desired)
    return events


def parse_pods_watch(text: str) -> dict[str, dict]:
    """`kubectl get pods --watch`가 남긴 (이름, 생성 시각, Ready 상태, 전환 시각) 줄을 파드별로 모은다.

    ready는 Ready가 처음 True가 된 시각이다. 끝까지 Ready가 되지 않은 파드는 nan으로 남는다.
    """
    pods: dict[str, dict] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        name, created, status, since = parts
        pod = pods.setdefault(name, {"created": _epoch(created), "ready": math.nan})
        if status == "True" and math.isnan(pod["ready"]):
            pod["ready"] = _epoch(since)
    return pods
