import json
import math
import subprocess

import pytest

from sllmbench import kube, prom


def test_queries_select_namespace_pod_and_container():
    q = prom.queries("llm", "llm-llama-server", window="2m")
    assert set(q) == {
        "throttle_ratio",
        "cpu_cores",
        "server_tokens_per_s",
        "requests_processing",
        "requests_deferred",
    }
    sel = '{namespace="llm",pod=~"llm-llama-server-.*",container="llama-server"}'
    assert f"container_cpu_cfs_throttled_periods_total{sel}[2m]" in q["throttle_ratio"]
    assert f"container_cpu_cfs_periods_total{sel}[2m]" in q["throttle_ratio"]
    # llama-server 지표에는 container 라벨 조건을 걸지 않는다.
    assert (
        'llamacpp:tokens_predicted_total{namespace="llm",pod=~"llm-llama-server-.*"}'
        in (q["server_tokens_per_s"])
    )


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"status": "success", "data": {"result": [{"value": [1, "0.25"]}]}}, 0.25),
        ({"status": "success", "data": {"result": []}}, math.nan),
        ({"status": "error"}, math.nan),
        ({"status": "success", "data": {"result": [{"value": [1, "NaN"]}]}}, math.nan),
    ],
)
def test_parse_scalar(payload, expected):
    got = prom.parse_scalar(payload)
    if math.isnan(expected):
        assert math.isnan(got)
    else:
        assert got == expected


def test_prom_client_builds_query_url(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"status": "success", "data": {"result": [{"value": [0, "3"]}]}}
            ).encode()

    def fake_urlopen(url, timeout):
        seen["url"] = url
        return Resp()

    monkeypatch.setattr(prom.urllib.request, "urlopen", fake_urlopen)
    assert prom.PromClient("http://p:9090/").query("up", at=10) == 3.0
    assert seen["url"].startswith("http://p:9090/api/v1/query?query=up")
    assert "time=10.000" in seen["url"]


def test_parse_deployment_and_hpa():
    d = kube.parse_deployment({"spec": {"replicas": 3}, "status": {"readyReplicas": 2}})
    assert d == {"replicas_desired": 3, "replicas_ready": 2}
    # 준비된 파드가 없으면 readyReplicas 필드가 빠진다.
    assert kube.parse_deployment({"spec": {"replicas": 1}, "status": {}})["replicas_ready"] == 0
    assert kube.parse_hpa({"status": {"desiredReplicas": 4}}) == {"hpa_desired": 4}


def test_replica_status_without_hpa(monkeypatch):
    def fake(*args):
        if "hpa" in args:
            raise subprocess.CalledProcessError(1, "kubectl")
        return {"spec": {"replicas": 1}, "status": {"readyReplicas": 1}}

    monkeypatch.setattr(kube, "_kubectl_json", fake)
    row = kube.replica_status("llm", "x")
    assert row["replicas_ready"] == 1
    assert math.isnan(row["hpa_desired"])
