import json
import math
import subprocess
from datetime import UTC, datetime

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
    # CPU는 샘플 간격이 고르지 않아도 낮게 나오지 않도록 rate가 아니라 기울기로 구한다.
    assert q["cpu_cores"] == f"sum(deriv(container_cpu_usage_seconds_total{sel}[2m]))"
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


def _at(hour: int, minute: int, second: int) -> float:
    return datetime(2026, 10, 14, hour, minute, second, tzinfo=UTC).timestamp()


HPA_WATCH = """\
<none>                 1
<none>                 1
2026-10-14T05:00:30Z   2
2026-10-14T05:00:30Z   2
2026-10-14T05:01:00Z   3
2026-10-14T05:09:00Z   2
"""

PODS_WATCH = """\
llm-a   2026-10-14T04:50:00Z   True     2026-10-14T04:50:10Z
llm-b   2026-10-14T05:00:30Z   <none>   <none>
llm-b   2026-10-14T05:00:30Z   False    2026-10-14T05:00:31Z
llm-b   2026-10-14T05:00:30Z   True     2026-10-14T05:00:42Z
llm-b   2026-10-14T05:00:30Z   False    2026-10-14T05:09:01Z
llm-c   2026-10-14T05:00:59Z   False    2026-10-14T05:01:00Z
"""


def test_parse_hpa_watch_keeps_only_rescales():
    assert kube.parse_hpa_watch(HPA_WATCH) == [
        {"timestamp": _at(5, 0, 30), "from_replicas": 1, "to_replicas": 2},
        {"timestamp": _at(5, 1, 0), "from_replicas": 2, "to_replicas": 3},
        {"timestamp": _at(5, 9, 0), "from_replicas": 3, "to_replicas": 2},
    ]
    assert kube.parse_hpa_watch("") == []


def test_parse_pods_watch_takes_first_ready_time():
    pods = kube.parse_pods_watch(PODS_WATCH)
    assert set(pods) == {"llm-a", "llm-b", "llm-c"}
    # 종료하면서 Ready가 False로 돌아가도 처음 True가 된 시각을 유지한다.
    assert pods["llm-b"] == {"created": _at(5, 0, 30), "ready": _at(5, 0, 42)}
    assert pods["llm-c"]["created"] == _at(5, 0, 59)
    assert math.isnan(pods["llm-c"]["ready"])
