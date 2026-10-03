"""측정 수집기.

poll       부하가 도는 동안 kubectl·Prometheus를 주기적으로 읽어 cluster.csv에 쌓는다.
timeline   cluster.csv와 k6 CSV를 시간축으로 합쳐 HPA 곡선용 timeline.csv를 만든다.
summarize  격자 실험 한 칸(스레드 × CPU limit)을 grid.csv에 한 줄로 덧붙인다.
"""

from __future__ import annotations

import argparse
import csv
import math
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


def grid_row(
    threads: int, cpu_limit: float, qos: str, cluster: pd.DataFrame, k6: pd.DataFrame
) -> dict:
    stats = k6csv.run_stats(k6)
    return {
        "threads": threads,
        "cpu_limit": cpu_limit,
        "qos": qos,
        **stats,
        "throttle_ratio": float(cluster["throttle_ratio"].mean()),
        "cpu_cores": float(cluster["cpu_cores"].mean()),
    }


def summarize(args: argparse.Namespace) -> None:
    row = grid_row(
        args.threads, args.cpu_limit, args.qos, pd.read_csv(args.cluster), k6csv.load(args.k6)
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

    s = sub.add_parser("summarize")
    s.add_argument("--threads", type=int, required=True)
    s.add_argument("--cpu-limit", type=float, required=True)
    s.add_argument("--qos", default="Guaranteed")
    s.add_argument("--cluster", required=True)
    s.add_argument("--k6", required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=summarize)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
