"""Prometheus HTTP API 질의와 측정용 PromQL.

cAdvisor(kubelet)의 container_cpu_* 지표와 llama-server /metrics 의 llamacpp:* 지표를 쓴다.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request

CONTAINER = "llama-server"


def _sel(namespace: str, deployment: str, container: bool = True) -> str:
    parts = [f'namespace="{namespace}"', f'pod=~"{deployment}-.*"']
    if container:
        parts.append(f'container="{CONTAINER}"')
    return "{" + ",".join(parts) + "}"


def queries(namespace: str, deployment: str, window: str = "1m") -> dict[str, str]:
    c = _sel(namespace, deployment)
    s = _sel(namespace, deployment, container=False)
    return {
        # 스로틀된 CFS 주기 / 전체 CFS 주기
        "throttle_ratio": (
            f"sum(rate(container_cpu_cfs_throttled_periods_total{c}[{window}]))"
            f" / sum(rate(container_cpu_cfs_periods_total{c}[{window}]))"
        ),
        "cpu_cores": f"sum(rate(container_cpu_usage_seconds_total{c}[{window}]))",
        "server_tokens_per_s": f"sum(rate(llamacpp:tokens_predicted_total{s}[{window}]))",
        "requests_processing": f"sum(llamacpp:requests_processing{s})",
        "requests_deferred": f"sum(llamacpp:requests_deferred{s})",
    }


def parse_scalar(payload: dict) -> float:
    """instant query 응답에서 첫 값을 꺼낸다. 결과가 없거나 NaN이면 nan."""
    if payload.get("status") != "success":
        return math.nan
    result = payload.get("data", {}).get("result", [])
    if not result:
        return math.nan
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return math.nan


class PromClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def query(self, promql: str, at: float | None = None) -> float:
        params = {"query": promql}
        if at is not None:
            params["time"] = f"{at:.3f}"
        url = f"{self.base_url}/api/v1/query?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=self.timeout) as resp:
            return parse_scalar(json.load(resp))
