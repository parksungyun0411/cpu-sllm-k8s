"""k6 `--out csv` 결과를 구간별·실행 전체 통계로 바꾼다.

k6 CSV는 지표 하나가 한 줄이다(metric_name, timestamp[unix 초], metric_value, ...).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DURATION = "http_req_duration"
FAILED = "http_req_failed"
TOKENS = "llm_completion_tokens"
GEN_TPS = "llm_gen_tokens_per_s"


def load(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=["metric_name", "timestamp", "metric_value"])
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df["metric_value"] = pd.to_numeric(df["metric_value"], errors="coerce")
    return df.dropna(subset=["timestamp", "metric_value"])


def _p95(values: pd.Series) -> float:
    return float(values.quantile(0.95)) if len(values) else float("nan")


def window_stats(df: pd.DataFrame, seconds: float) -> dict:
    """seconds 길이 구간 안에 완료된 요청의 수·오류율·p95·생성 토큰 처리량."""
    dur = df.loc[df["metric_name"] == DURATION, "metric_value"]
    failed = df.loc[df["metric_name"] == FAILED, "metric_value"]
    tokens = df.loc[df["metric_name"] == TOKENS, "metric_value"].sum()
    return {
        "requests": len(dur),
        "rps": len(dur) / seconds,
        "error_rate": float(failed.mean()) if len(failed) else float("nan"),
        "p95_ms": _p95(dur),
        "tokens_per_s": tokens / seconds,
    }


def bucket_stats(df: pd.DataFrame, interval_s: int = 15) -> pd.DataFrame:
    """완료 시각 기준 interval_s 초 구간마다 요청 수·오류율·p95·생성 토큰 처리량을 낸다."""
    if df.empty:
        return pd.DataFrame(
            columns=["timestamp", "requests", "rps", "error_rate", "p95_ms", "tokens_per_s"]
        )
    start = int(df["timestamp"].min())
    df = df.assign(bucket=((df["timestamp"] - start) // interval_s).astype(int))
    rows = []
    for bucket, g in df.groupby("bucket"):
        rows.append({"timestamp": start + bucket * interval_s, **window_stats(g, interval_s)})
    return pd.DataFrame(rows)


def run_stats(df: pd.DataFrame) -> dict:
    """실행 전체 한 줄 요약. 처리량은 첫 완료부터 마지막 완료까지의 시간으로 나눈다."""
    dur = df.loc[df["metric_name"] == DURATION, "metric_value"]
    failed = df.loc[df["metric_name"] == FAILED, "metric_value"]
    tokens = df.loc[df["metric_name"] == TOKENS, "metric_value"].sum()
    gen = df.loc[df["metric_name"] == GEN_TPS, "metric_value"]
    span = float(df["timestamp"].max() - df["timestamp"].min() + 1) if len(df) else float("nan")
    return {
        "requests": len(dur),
        "error_rate": float(failed.mean()) if len(failed) else float("nan"),
        "p95_ms": _p95(dur),
        "tokens_per_s": tokens / span if span else float("nan"),
        "gen_tokens_per_s_per_request": float(gen.mean()) if len(gen) else float("nan"),
    }
