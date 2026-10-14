"""측정 수집기.

poll       부하가 도는 동안 kubectl·Prometheus를 주기적으로 읽어 cluster.csv에 쌓는다.
timeline   cluster.csv와 k6 CSV를 시간축으로 합쳐 HPA 곡선용 timeline.csv를 만든다.
steps      HPA 계단 부하의 계단마다 한 줄씩 steps.csv를 만든다.
scale      HPA·파드 watch 기록에서 스케일 시점과 파드가 Ready가 되기까지 걸린 시간을 scale.csv로 만든다.
summarize  격자 실험 한 칸(스레드 × CPU limit)을 grid.csv에 한 줄로 덧붙인다.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import time
from pathlib import Path

import pandas as pd

from sllmbench import k6csv, kube
from sllmbench.prom import PromClient, queries

CLUSTER_COLUMNS = [
    "timestamp",
    "replicas_desired",
    "replicas_ready",
    "hpa_desired",
    "cpu_cores",
    "throttle_ratio",
    "server_tokens_per_s",
    "requests_processing",
    "requests_deferred",
]

TIMELINE_COLUMNS = [
    "elapsed_s",
    "replicas_ready",
    "hpa_desired",
    "rps",
    "p95_ms",
    "error_rate",
    "tokens_per_s",
    "server_tokens_per_s",
    "throttle_ratio",
    "cpu_cores",
]

STEP_COLUMNS = [
    "step",
    "rate_per_min",
    "start_s",
    "end_s",
    "requests",
    "dropped",
    "completed_per_min",
    "error_rate",
    "p95_ms",
    "tokens_per_s",
    "replicas_ready_start",
    "replicas_ready_end",
    "hpa_desired_max",
    "cpu_cores",
    "throttle_ratio",
    "requests_deferred_max",
]

SCALE_COLUMNS = [
    "elapsed_s",
    "direction",
    "from_replicas",
    "to_replicas",
    "pod_created_s",
    "pod_ready_s",
]

GRID_COLUMNS = [
    "threads",
    "cpu_limit",
    "qos",
    "requests",
    "error_rate",
    "p95_ms",
    "tokens_per_s",
    "gen_tokens_per_s_per_request",
    "throttle_ratio",
    "cpu_cores",
]


def parse_duration(text: str) -> float:
    """k6 형식 기간("4m", "10s", "1m30s")이나 초 단위 숫자를 초로 바꾼다."""
    text = str(text).strip()
    try:
        return float(text)
    except ValueError:
        pass
    units = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise ValueError(f"기간을 읽을 수 없다: {text!r}")
    return sum(float(n) * units[u] for n, u in parts)


def sample(prom: PromClient, namespace: str, deployment: str, window: str) -> dict:
    row = {"timestamp": time.time()}
    row.update(kube.replica_status(namespace, deployment))
    for name, q in queries(namespace, deployment, window).items():
        try:
            row[name] = prom.query(q)
        except OSError as exc:
            print(f"prometheus 질의 실패 ({name}): {exc}", file=sys.stderr)
            row[name] = math.nan
    return row


def poll(args: argparse.Namespace) -> None:
    prom = PromClient(args.prom_url)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    deadline = time.time() + args.duration if args.duration else math.inf
    with out.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CLUSTER_COLUMNS)
        if new:
            writer.writeheader()
        while time.time() < deadline:
            writer.writerow(sample(prom, args.namespace, args.deployment, args.window))
            f.flush()
            time.sleep(args.interval)


def build_timeline(cluster: pd.DataFrame, k6: pd.DataFrame, interval_s: int) -> pd.DataFrame:
    """k6 구간 통계에 그 구간 시작 시점 이전의 가장 가까운 클러스터 샘플을 붙인다."""
    buckets = k6csv.bucket_stats(k6, interval_s)
    if buckets.empty:
        return pd.DataFrame(columns=TIMELINE_COLUMNS)
    cluster = cluster.sort_values("timestamp").astype({"timestamp": "float64"})
    buckets = buckets.sort_values("timestamp").astype({"timestamp": "float64"})
    merged = pd.merge_asof(
        buckets,
        cluster.drop(columns=["replicas_desired", "requests_processing", "requests_deferred"]),
        on="timestamp",
        direction="backward",
    )
    merged["elapsed_s"] = merged["timestamp"] - merged["timestamp"].min()
    return merged[TIMELINE_COLUMNS]


def timeline(args: argparse.Namespace) -> None:
    cluster = pd.read_csv(args.cluster)
    out = build_timeline(cluster, k6csv.load(args.k6), args.interval)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)


def step_windows(
    rates: list[int], hold_s: float, ramp_s: float, cooldown_s: float
) -> list[tuple[str, int, float, float]]:
    """load/k6/stepped.js와 같은 일정으로 (이름, 분당 요청 수, 시작, 끝)을 낸다. 램프 구간은 뺀다."""
    windows = []
    t = 0.0
    for i, rate in enumerate(rates, start=1):
        windows.append((str(i), rate, t + ramp_s, t + ramp_s + hold_s))
        t += ramp_s + hold_s
    windows.append(("cooldown", rates[0], t + ramp_s, t + ramp_s + cooldown_s))
    return windows


def build_steps(
    cluster: pd.DataFrame,
    k6: pd.DataFrame,
    rates: list[int],
    hold_s: float,
    ramp_s: float,
    cooldown_s: float,
) -> pd.DataFrame:
    """계단마다 그 구간에 완료된 요청의 통계와 구간 안 클러스터 샘플을 한 줄로 묶는다."""
    t0 = float(k6["timestamp"].min())
    rows = []
    for name, rate, start, end in step_windows(rates, hold_s, ramp_s, cooldown_s):
        in_k6 = k6[(k6["timestamp"] >= t0 + start) & (k6["timestamp"] < t0 + end)]
        c = cluster[(cluster["timestamp"] >= t0 + start) & (cluster["timestamp"] < t0 + end)]
        c = c.sort_values("timestamp")
        stats = k6csv.window_stats(in_k6, end - start)
        rows.append(
            {
                "step": name,
                "rate_per_min": rate,
                "start_s": start,
                "end_s": end,
                "requests": stats["requests"],
                "dropped": stats["dropped"],
                "completed_per_min": stats["rps"] * 60,
                "error_rate": stats["error_rate"],
                "p95_ms": stats["p95_ms"],
                "tokens_per_s": stats["tokens_per_s"],
                "replicas_ready_start": c["replicas_ready"].iloc[0] if len(c) else math.nan,
                "replicas_ready_end": c["replicas_ready"].iloc[-1] if len(c) else math.nan,
                "hpa_desired_max": c["hpa_desired"].max(),
                "cpu_cores": c["cpu_cores"].mean(),
                "throttle_ratio": c["throttle_ratio"].mean(),
                "requests_deferred_max": c["requests_deferred"].max(),
            }
        )
    return pd.DataFrame(rows, columns=STEP_COLUMNS)


def steps(args: argparse.Namespace) -> None:
    out = build_steps(
        pd.read_csv(args.cluster),
        k6csv.load(args.k6),
        [int(r) for r in args.steps.split(",")],
        parse_duration(args.step_duration),
        parse_duration(args.ramp),
        parse_duration(args.cooldown),
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)


def build_scale(events: list[dict], pods: dict[str, dict], t0: float) -> pd.DataFrame:
    """스케일 시점마다 새로 뜬 파드를 짝지어 생성·Ready까지 걸린 시간을 낸다.

    HPA의 lastScaleTime과 파드의 생성·Ready 시각은 모두 초 단위라 값마다 1초 안팎의 오차가 있다.
    """
    unused = sorted(pods.values(), key=lambda pod: pod["created"])
    rows = []
    for ev in sorted(events, key=lambda e: e["timestamp"]):
        added = ev["to_replicas"] - ev["from_replicas"]
        row = {
            "elapsed_s": ev["timestamp"] - t0,
            "direction": "up" if added > 0 else "down",
            "from_replicas": ev["from_replicas"],
            "to_replicas": ev["to_replicas"],
            "pod_created_s": math.nan,
            "pod_ready_s": math.nan,
        }
        if added > 0:
            # 초 단위로 잘린 시각이라 파드 생성이 lastScaleTime보다 1초 앞서 보일 수 있다.
            new = [pod for pod in unused if pod["created"] >= ev["timestamp"] - 2][:added]
            unused = [pod for pod in unused if pod not in new]
            if len(new) == added:
                row["pod_created_s"] = max(pod["created"] for pod in new) - ev["timestamp"]
                row["pod_ready_s"] = max(pod["ready"] for pod in new) - ev["timestamp"]
        rows.append(row)
    return pd.DataFrame(rows, columns=SCALE_COLUMNS)


def scale(args: argparse.Namespace) -> None:
    out = build_scale(
        kube.parse_hpa_watch(Path(args.hpa_watch).read_text(encoding="utf-8")),
        kube.parse_pods_watch(Path(args.pods_watch).read_text(encoding="utf-8")),
        float(k6csv.load(args.k6)["timestamp"].min()),
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)


def grid_row(
    threads: int,
    cpu_limit: float,
    qos: str,
    cluster: pd.DataFrame,
    k6: pd.DataFrame,
    window_s: float = 60.0,
) -> dict:
    """클러스터 지표는 rate 창(window_s)이 부하 구간 안에 다 들어온 샘플만 평균한다.

    부하 시작 직후 샘플의 창에는 부하 전 유휴 구간이 섞여 스로틀 비율과 CPU가 낮게 나온다.
    """
    stats = k6csv.run_stats(k6)
    loaded = cluster[cluster["timestamp"] >= k6["timestamp"].min() + window_s]
    return {
        "threads": threads,
        "cpu_limit": cpu_limit,
        "qos": qos,
        **stats,
        "throttle_ratio": float(loaded["throttle_ratio"].mean()),
        "cpu_cores": float(loaded["cpu_cores"].mean()),
    }


def summarize(args: argparse.Namespace) -> None:
    row = grid_row(
        args.threads,
        args.cpu_limit,
        args.qos,
        pd.read_csv(args.cluster),
        k6csv.load(args.k6),
        parse_duration(args.window),
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    with out.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=GRID_COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow(row)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sllmbench-collect")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("poll")
    p.add_argument("--namespace", default="llm")
    p.add_argument("--deployment", default="llm-llama-server")
    p.add_argument("--prom-url", default="http://localhost:9090")
    p.add_argument("--interval", type=float, default=15.0)
    p.add_argument("--window", default="1m")
    p.add_argument("--duration", type=float, default=0, help="초. 0이면 중단할 때까지")
    p.add_argument("--out", required=True)
    p.set_defaults(func=poll)

    t = sub.add_parser("timeline")
    t.add_argument("--cluster", required=True)
    t.add_argument("--k6", required=True)
    t.add_argument("--interval", type=int, default=15)
    t.add_argument("--out", required=True)
    t.set_defaults(func=timeline)

    st = sub.add_parser("steps")
    st.add_argument("--cluster", required=True)
    st.add_argument("--k6", required=True)
    st.add_argument("--steps", default="6,12,18,24", help="계단별 분당 요청 수(k6 STEPS)")
    st.add_argument("--step-duration", default="4m")
    st.add_argument("--ramp", default="10s")
    st.add_argument("--cooldown", default="6m")
    st.add_argument("--out", required=True)
    st.set_defaults(func=steps)

    sc = sub.add_parser("scale")
    sc.add_argument("--hpa-watch", required=True)
    sc.add_argument("--pods-watch", required=True)
    sc.add_argument("--k6", required=True, help="경과 시간의 기준(부하 시작)을 읽는다")
    sc.add_argument("--out", required=True)
    sc.set_defaults(func=scale)

    s = sub.add_parser("summarize")
    s.add_argument("--threads", type=int, required=True)
    s.add_argument("--cpu-limit", type=float, required=True)
    s.add_argument("--qos", default="Guaranteed")
    s.add_argument("--window", default="1m", help="poll에 준 rate 창. 이만큼 앞 샘플을 버린다")
    s.add_argument("--cluster", required=True)
    s.add_argument("--k6", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=summarize)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
