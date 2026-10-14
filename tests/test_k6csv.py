import math
from pathlib import Path

import pytest

from sllmbench import k6csv

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def k6():
    return k6csv.load(FIXTURES / "synthetic_k6.csv")


def test_load_keeps_only_needed_columns(k6):
    assert list(k6.columns) == ["metric_name", "timestamp", "metric_value"]
    assert k6["timestamp"].min() == 1000


def test_bucket_stats(k6):
    b = k6csv.bucket_stats(k6, interval_s=15)
    assert list(b["timestamp"]) == [1000, 1015]
    assert list(b["requests"]) == [4, 2]
    assert b.loc[0, "error_rate"] == pytest.approx(0.25)
    # pandas 선형 보간 분위수: [100,200,300,400] 의 0.95 → 385
    assert b.loc[0, "p95_ms"] == pytest.approx(385)
    assert b.loc[1, "p95_ms"] == pytest.approx(595)
    assert b.loc[0, "tokens_per_s"] == pytest.approx(30 / 15)
    assert b.loc[1, "tokens_per_s"] == pytest.approx(40 / 15)
    assert b.loc[0, "rps"] == pytest.approx(4 / 15)


def test_bucket_stats_empty():
    import pandas as pd

    empty = pd.DataFrame(columns=["metric_name", "timestamp", "metric_value"])
    assert k6csv.bucket_stats(empty).empty


def test_run_stats(k6):
    s = k6csv.run_stats(k6)
    assert s["requests"] == 6
    assert s["error_rate"] == pytest.approx(1 / 6)
    assert s["p95_ms"] == pytest.approx(575)
    assert s["tokens_per_s"] == pytest.approx(70 / 30)
    assert s["gen_tokens_per_s_per_request"] == pytest.approx(6)
    assert not math.isnan(s["tokens_per_s"])


def test_window_stats_counts_dropped_iterations(k6):
    import pandas as pd

    dropped = pd.DataFrame(
        {
            "metric_name": ["dropped_iterations"] * 2,
            "timestamp": [1001, 1002],
            "metric_value": [1.0, 1.0],
        }
    )
    stats = k6csv.window_stats(pd.concat([k6, dropped]), 30)
    assert stats["dropped"] == 2
    assert stats["requests"] == 6
    assert k6csv.window_stats(k6, 30)["dropped"] == 0
