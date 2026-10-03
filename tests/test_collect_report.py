import csv
from pathlib import Path

import pandas as pd
import pytest

from sllmbench import collect, k6csv, report

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_timeline_aligns_previous_cluster_sample():
    cluster = pd.read_csv(FIXTURES / "synthetic_cluster.csv")
    tl = collect.build_timeline(cluster, k6csv.load(FIXTURES / "synthetic_k6.csv"), 15)
    assert list(tl.columns) == collect.TIMELINE_COLUMNS
    assert list(tl["elapsed_s"]) == [0, 15]
    # 1000초 구간 ← 995초 샘플, 1015초 구간 ← 1010초 샘플
    assert list(tl["hpa_desired"]) == [1, 2]
    assert list(tl["throttle_ratio"]) == [0.1, 0.3]
    assert tl.loc[0, "p95_ms"] == pytest.approx(385)


def test_timeline_cli(tmp_path):
    out = tmp_path / "timeline.csv"
    collect.main(
        [
            "timeline",
            "--cluster",
            str(FIXTURES / "synthetic_cluster.csv"),
            "--k6",
            str(FIXTURES / "synthetic_k6.csv"),
            "--out",
            str(out),
        ]
    )
    assert len(pd.read_csv(out)) == 2


def test_summarize_appends_grid_rows(tmp_path):
    out = tmp_path / "grid.csv"
    for threads in (2, 4):
        collect.main(
            [
                "summarize",
                "--threads",
                str(threads),
                "--cpu-limit",
                "2",
                "--cluster",
                str(FIXTURES / "synthetic_cluster.csv"),
                "--k6",
                str(FIXTURES / "synthetic_k6.csv"),
                "--out",
                str(out),
            ]
        )
    rows = list(csv.DictReader(out.open()))
    assert [r["threads"] for r in rows] == ["2", "4"]
    assert list(rows[0]) == collect.GRID_COLUMNS
    assert float(rows[0]["throttle_ratio"]) == pytest.approx(0.2)
    assert float(rows[0]["cpu_cores"]) == pytest.approx(1.5)
    assert rows[0]["qos"] == "Guaranteed"


def test_poll_writes_rows(tmp_path, monkeypatch):
    row = {c: 0 for c in collect.CLUSTER_COLUMNS}
    monkeypatch.setattr(collect, "sample", lambda *a: row)
    # 마감 계산 1번 + 루프 검사 4번(0, 1, 2초는 기록, 99초에서 멈춤)
    clock = iter([0.0, 0.0, 1.0, 2.0, 99.0])
    monkeypatch.setattr(collect.time, "time", lambda: next(clock))
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    out = tmp_path / "cluster.csv"
    collect.main(["poll", "--duration", "10", "--out", str(out)])
    assert len(pd.read_csv(out)) == 3


def test_markdown_table_blanks_nan():
    df = pd.DataFrame({"a": [1.0, float("nan")], "b": ["x", "y"]})
    assert report.markdown_table(df).splitlines() == [
        "| a | b |",
        "|---|---|",
        "| 1.00 | x |",
        "|  | y |",
    ]


def test_build_report(tmp_path):
    tl = tmp_path / "timeline.csv"
    collect.main(
        [
            "timeline",
            "--cluster",
            str(FIXTURES / "synthetic_cluster.csv"),
            "--k6",
            str(FIXTURES / "synthetic_k6.csv"),
            "--out",
            str(tl),
        ]
    )
    out = tmp_path / "report"
    path = report.build(FIXTURES / "synthetic_grid.csv", tl, out, "synthetic")
    text = path.read_text(encoding="utf-8")
    assert "### Guaranteed" in text and "### Burstable" in text
    assert "hpa_timeline.png" in text
    for name in [
        "grid_tokens_per_s_guaranteed.png",
        "grid_throttle_ratio_burstable.png",
        "grid_p95_ms_guaranteed.png",
        "hpa_timeline.png",
    ]:
        assert (out / name).stat().st_size > 0


def test_report_cli_requires_input(tmp_path):
    with pytest.raises(SystemExit):
        report.main(["--out", str(tmp_path)])
