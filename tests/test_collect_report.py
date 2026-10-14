import csv
import math
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
                "--window",
                "10s",
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
    # 부하 시작(1000초) + 창 10초 이후 샘플인 1010초, 1025초만 평균한다.
    assert float(rows[0]["throttle_ratio"]) == pytest.approx(0.25)
    assert float(rows[0]["cpu_cores"]) == pytest.approx(2.0)
    assert rows[0]["qos"] == "Guaranteed"


def test_grid_row_drops_samples_whose_window_precedes_load():
    cluster = pd.read_csv(FIXTURES / "synthetic_cluster.csv")
    k6 = k6csv.load(FIXTURES / "synthetic_k6.csv")
    late = collect.grid_row(2, 2.0, "Guaranteed", cluster, k6, window_s=25)
    assert late["throttle_ratio"] == pytest.approx(0.2)
    assert late["cpu_cores"] == pytest.approx(2.5)
    # 기본 창(60초)보다 짧은 실행은 쓸 샘플이 없어 빈 값으로 남는다.
    short = collect.grid_row(2, 2.0, "Guaranteed", cluster, k6)
    assert math.isnan(short["throttle_ratio"]) and math.isnan(short["cpu_cores"])
    assert short["requests"] == 6


@pytest.mark.parametrize(
    "text, seconds",
    [("4m", 240), ("10s", 10), ("1m30s", 90), ("500ms", 0.5), ("90", 90), ("1.5h", 5400)],
)
def test_parse_duration(text, seconds):
    assert collect.parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "abc", "4x", "m4"])
def test_parse_duration_rejects_unknown(text):
    with pytest.raises(ValueError):
        collect.parse_duration(text)


def test_step_windows_follow_k6_stages():
    # stepped.js: 계단마다 ramp 뒤 hold, 마지막에 ramp 뒤 cooldown
    assert collect.step_windows([6, 12], hold_s=240, ramp_s=10, cooldown_s=360) == [
        ("1", 6, 10, 250),
        ("2", 12, 260, 500),
        ("cooldown", 6, 510, 870),
    ]


def test_build_steps():
    cluster = pd.read_csv(FIXTURES / "synthetic_cluster.csv")
    k6 = k6csv.load(FIXTURES / "synthetic_k6.csv")
    # 구간: 1단 [1005, 1015), 2단 [1020, 1030), cooldown [1035, 1040)
    st = collect.build_steps(cluster, k6, [4, 8], hold_s=10, ramp_s=5, cooldown_s=5)
    assert list(st.columns) == collect.STEP_COLUMNS
    assert list(st["step"]) == ["1", "2", "cooldown"]
    assert list(st["requests"]) == [3, 1, 0]
    first = st.iloc[0]
    assert first["completed_per_min"] == pytest.approx(18)
    assert first["error_rate"] == pytest.approx(1 / 3)
    # [200, 300, 400] 의 0.95 분위수
    assert first["p95_ms"] == pytest.approx(390)
    assert first["tokens_per_s"] == pytest.approx(20 / 10)
    assert (first["replicas_ready_start"], first["replicas_ready_end"]) == (1, 1)
    assert first["hpa_desired_max"] == 2
    assert first["cpu_cores"] == pytest.approx(1.5)
    assert first["throttle_ratio"] == pytest.approx(0.3)
    assert first["requests_deferred_max"] == 1
    assert st.loc[1, "replicas_ready_end"] == 2
    assert st.loc[1, "tokens_per_s"] == pytest.approx(20 / 10)
    # 완료된 요청도 샘플도 없는 구간은 빈 값이다.
    assert math.isnan(st.loc[2, "p95_ms"]) and math.isnan(st.loc[2, "replicas_ready_end"])


def test_build_scale_pairs_new_pods_with_rescales():
    events = [
        {"timestamp": 130.0, "from_replicas": 1, "to_replicas": 2},
        {"timestamp": 160.0, "from_replicas": 2, "to_replicas": 3},
        {"timestamp": 640.0, "from_replicas": 3, "to_replicas": 2},
    ]
    pods = {
        "a": {"created": 10.0, "ready": 20.0},
        "b": {"created": 130.0, "ready": 142.0},
        # 초 단위로 잘려 lastScaleTime보다 1초 앞서 보이는 생성 시각, 끝까지 Ready가 되지 않음
        "c": {"created": 159.0, "ready": math.nan},
    }
    sc = collect.build_scale(events, pods, t0=100.0)
    assert list(sc.columns) == collect.SCALE_COLUMNS
    assert list(sc["elapsed_s"]) == [30, 60, 540]
    assert list(sc["direction"]) == ["up", "up", "down"]
    assert (sc.loc[0, "pod_created_s"], sc.loc[0, "pod_ready_s"]) == (0, 12)
    assert sc.loc[1, "pod_created_s"] == -1
    assert math.isnan(sc.loc[1, "pod_ready_s"])
    assert math.isnan(sc.loc[2, "pod_created_s"]) and math.isnan(sc.loc[2, "pod_ready_s"])


def test_steps_and_scale_cli(tmp_path):
    steps_out, scale_out = tmp_path / "steps.csv", tmp_path / "scale.csv"
    k6 = str(FIXTURES / "synthetic_k6.csv")
    collect.main(
        [
            "steps",
            "--cluster",
            str(FIXTURES / "synthetic_cluster.csv"),
            "--k6",
            k6,
            "--steps",
            "4,8",
            "--step-duration",
            "10s",
            "--ramp",
            "5s",
            "--cooldown",
            "5s",
            "--out",
            str(steps_out),
        ]
    )
    assert list(pd.read_csv(steps_out)["rate_per_min"]) == [4, 8, 4]
    hpa, pods = tmp_path / "hpa-watch.txt", tmp_path / "pods-watch.txt"
    # 1970-01-01T00:16:40Z = 1000초(k6 픽스처의 부하 시작)
    hpa.write_text("<none> 1\n1970-01-01T00:16:50Z 2\n", encoding="utf-8")
    pods.write_text(
        "p 1970-01-01T00:16:51Z <none> <none>\np 1970-01-01T00:16:51Z True 1970-01-01T00:17:01Z\n",
        encoding="utf-8",
    )
    collect.main(
        ["scale", "--hpa-watch", str(hpa), "--pods-watch", str(pods), "--k6", k6]
        + ["--out", str(scale_out)]
    )
    row = pd.read_csv(scale_out).iloc[0]
    assert (row["elapsed_s"], row["pod_created_s"], row["pod_ready_s"]) == (10, 1, 11)


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


def test_markdown_table_per_column_digits_and_counts():
    df = pd.DataFrame(
        {
            "p95_ms": [1234.567, 1.0],
            "tokens_per_s": [28.164835, 1.0],
            "replicas_ready": [float("nan"), 2.0],
        }
    )
    assert report.markdown_table(df, 3, report.DIGITS).splitlines()[2:] == [
        "| 1235 | 28.16 |  |",
        "| 1 | 1.00 | 2 |",
    ]


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


def test_build_report_with_steps_scale_and_conditions(tmp_path):
    k6 = k6csv.load(FIXTURES / "synthetic_k6.csv")
    cluster = pd.read_csv(FIXTURES / "synthetic_cluster.csv")
    tl, steps, scale, cond = (tmp_path / n for n in ("tl.csv", "st.csv", "sc.csv", "cond.md"))
    collect.build_timeline(cluster, k6, 15).to_csv(tl, index=False)
    collect.build_steps(cluster, k6, [4, 8], 10, 5, 5).to_csv(steps, index=False)
    collect.build_scale(
        [{"timestamp": 1010.0, "from_replicas": 1, "to_replicas": 2}],
        {"p": {"created": 1011.0, "ready": 1021.0}},
        t0=1000.0,
    ).to_csv(scale, index=False)
    cond.write_text("## 실행 조건\n\n- 합성 데이터\n", encoding="utf-8")
    report.main(
        ["--timeline", str(tl), "--steps", str(steps), "--scale", str(scale)]
        + ["--conditions", str(cond), "--out", str(tmp_path / "report")]
    )
    text = (tmp_path / "report" / "REPORT.md").read_text(encoding="utf-8")
    # 실행 조건은 표보다 앞에 그대로 들어간다.
    assert text.index("- 합성 데이터") < text.index("## HPA 계단별 요약")
    assert (
        "| 1 | 4 | 5 | 15 | 3 | 18.0 | 0.333 | 390 | 2.00 | 1 | 1 | 2 | 1.50 | 0.300 | 1 |" in text
    )
    assert "| cooldown | 4 | 35 | 40 | 0 | 0.0 |  |  | 0.00 |  |  |  |  |  |  |" in text
    assert "| 10 | up | 1 | 2 | 1 | 11 |" in text
    assert text.index("## 스케일 시점") < text.index("## HPA 계단 부하 곡선")


def test_scale_section_says_when_nothing_scaled():
    empty = pd.DataFrame(columns=collect.SCALE_COLUMNS)
    assert "이 실행에서는 HPA가 레플리카 수를 바꾸지 않았다." in report.scale_section(empty)


def test_report_cli_requires_input(tmp_path):
    with pytest.raises(SystemExit):
        report.main(["--out", str(tmp_path)])
