"""grid.csv·timeline.csv를 그림과 표로 바꿔 reports/<run>/ 아래에 둔다.

  sllmbench-report --grid results/<run>/grid.csv --conditions results/<run>/conditions.md \
      --out reports/<run>
  sllmbench-report --timeline results/<run>/timeline.csv --steps results/<run>/steps.csv \
      --scale results/<run>/scale.csv --conditions results/<run>/conditions.md --out reports/<run>
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

INK = "#1f2933"
MUTED = "#616e7c"
GRID = "#e4e7eb"
# 레플리카(실선)와 HPA 목표(점선)는 같은 배포를 가리키므로 한 색을 쓰고 선 모양으로 구분한다.
LINE = "#2563eb"


def _style(ax) -> None:
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)


def _fmt(value, digits: int = 2) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


# 표의 열별 소수 자릿수. 여기에 없는 열은 markdown_table의 기본 자릿수를 쓴다.
DIGITS = {
    "cpu_limit": 1,
    "tokens_per_s": 2,
    "throttle_ratio": 3,
    "p95_ms": 0,
    "error_rate": 3,
    "cpu_cores": 2,
    "rps": 3,
    "completed_per_min": 1,
    "start_s": 0,
    "end_s": 0,
    "elapsed_s": 0,
}
# 빈 값이 섞여 부동소수로 읽히는 개수 열. 정수로 되돌려 찍는다.
COUNTS = [
    "replicas_ready",
    "hpa_desired",
    "replicas_ready_start",
    "replicas_ready_end",
    "hpa_desired_max",
    "requests_deferred_max",
]


def markdown_table(df: pd.DataFrame, digits: int = 2, per_column: dict | None = None) -> str:
    per_column = per_column or {}
    df = df.astype({c: "Int64" for c in COUNTS if c in df.columns})
    places = [per_column.get(c, digits) for c in df.columns]
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    rows = [
        "| " + " | ".join(_fmt(v, d) for v, d in zip(r, places)) + " |"
        for r in df.itertuples(index=False)
    ]
    return "\n".join([header, sep, *rows])


def heatmap(pivot: pd.DataFrame, title: str, unit: str, path: Path, digits: int = 2) -> None:
    """한 색상(Blues) 순차 척도. 칸마다 값을 같이 적어 색에만 의존하지 않는다."""
    fig, ax = plt.subplots(figsize=(1.4 * len(pivot.columns) + 2.5, 1.0 * len(pivot.index) + 1.8))
    im = ax.imshow(pivot.values, cmap="Blues", aspect="auto", origin="lower")
    ax.set_xticks(range(len(pivot.columns)), [_fmt(c, 1) for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)), [str(i) for i in pivot.index])
    ax.set_xlabel("CPU limit (cores)", color=INK)
    ax.set_ylabel("threads (-t)", color=INK)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    vmax = pd.Series(pivot.values.ravel()).max()
    for (i, j), v in pd.DataFrame(pivot.values).stack().items():
        dark = pd.notna(vmax) and vmax and v > vmax * 0.6
        ax.text(
            j,
            i,
            _fmt(float(v), digits),
            ha="center",
            va="center",
            fontsize=9,
            color="white" if dark else INK,
        )
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label(unit, color=MUTED)
    cbar.ax.tick_params(colors=MUTED, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def grid_section(grid: pd.DataFrame, out: Path) -> list[str]:
    lines = ["## 스레드 × CPU limit 격자", ""]
    for qos, g in grid.groupby("qos", sort=True):
        slug = str(qos).lower()
        for col, title, unit, digits in [
            ("tokens_per_s", f"generated tokens/s ({qos})", "tokens/s", 2),
            ("throttle_ratio", f"CFS throttled period ratio ({qos})", "ratio", 3),
            ("p95_ms", f"p95 latency ({qos})", "ms", 0),
        ]:
            pivot = g.pivot_table(index="threads", columns="cpu_limit", values=col, aggfunc="mean")
            name = f"grid_{col}_{slug}.png"
            heatmap(pivot, title, unit, out / name, digits)
            lines += [f"![{title}]({name})", ""]
        cols = [
            "threads",
            "cpu_limit",
            "requests",
            "tokens_per_s",
            "throttle_ratio",
            "p95_ms",
            "error_rate",
            "cpu_cores",
        ]
        lines += [
            f"### {qos}",
            "",
            markdown_table(g.sort_values(["threads", "cpu_limit"])[cols], 3, DIGITS),
            "",
        ]
    return lines


def timeline_figure(tl: pd.DataFrame, path: Path) -> None:
    """서로 다른 단위는 축을 겹치지 않고 위아래 패널로 나눈다."""
    x = tl["elapsed_s"] / 60
    panels = [
        ("pods", [("replicas_ready", "ready replicas", "-"), ("hpa_desired", "HPA desired", "--")]),
        ("req/s", [("rps", "completed req/s", "-")]),
        ("ms", [("p95_ms", "p95 latency", "-")]),
        ("tokens/s", [("tokens_per_s", "generated tokens/s", "-")]),
        ("ratio", [("throttle_ratio", "CFS throttled ratio", "-")]),
    ]
    fig, axes = plt.subplots(len(panels), 1, figsize=(9, 2.0 * len(panels)), sharex=True)
    for ax, (unit, series) in zip(axes, panels):
        for col, label, ls in series:
            if col in tl and tl[col].notna().any():
                ax.plot(
                    x,
                    tl[col],
                    ls,
                    color=LINE,
                    linewidth=2,
                    label=label,
                    drawstyle="steps-post" if col in ("replicas_ready", "hpa_desired") else None,
                )
        ax.set_ylabel(unit, color=MUTED)
        if len(series) > 1:
            ax.legend(frameon=False, fontsize=8, loc="upper left")
        else:
            ax.set_title(series[0][1], color=INK, fontsize=10, loc="left")
        _style(ax)
    axes[-1].set_xlabel("elapsed (min)", color=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def timeline_section(tl: pd.DataFrame, out: Path) -> list[str]:
    timeline_figure(tl, out / "hpa_timeline.png")
    cols = [
        "elapsed_s",
        "replicas_ready",
        "hpa_desired",
        "rps",
        "p95_ms",
        "tokens_per_s",
        "throttle_ratio",
        "error_rate",
    ]
    return [
        "## HPA 계단 부하 곡선",
        "",
        "![HPA timeline](hpa_timeline.png)",
        "",
        markdown_table(tl[cols], 2, DIGITS),
        "",
    ]


def steps_section(steps: pd.DataFrame) -> list[str]:
    return [
        "## HPA 계단별 요약",
        "",
        "램프 구간을 뺀 계단 구간(start_s~end_s, 부하 시작 기준 초)에 완료된 요청으로 계산했다.",
        "dropped는 k6가 VU를 제때 못 구해 보내지 않고 건너뛴 요청 수(dropped_iterations)다.",
        "레플리카 수는 그 구간의 첫 샘플과 마지막 샘플, cpu_cores와 throttle_ratio는 구간 안 샘플의",
        "평균(전 파드 합)이다.",
        "",
        markdown_table(steps, 2, DIGITS),
        "",
    ]


def scale_section(scale: pd.DataFrame) -> list[str]:
    lines = [
        "## 스케일 시점",
        "",
        "elapsed_s는 부하 시작부터 HPA의 lastScaleTime까지, pod_created_s와 pod_ready_s는 그 시점부터",
        "새 파드가 만들어지고 Ready가 되기까지 걸린 초다. 원본 시각이 초 단위라 1초 안팎의 오차가 있다.",
        "",
    ]
    if scale.empty:
        return lines + ["이 실행에서는 HPA가 레플리카 수를 바꾸지 않았다.", ""]
    return lines + [markdown_table(scale, 0), ""]


def build(
    grid: Path | None,
    timeline: Path | None,
    out: Path,
    title: str,
    steps: Path | None = None,
    scale: Path | None = None,
    conditions: Path | None = None,
) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# {title}", "", f"생성: {now}", ""]
    sources = [str(p) for p in (grid, timeline, steps, scale) if p]
    lines += ["입력: " + ", ".join(f"`{s}`" for s in sources), ""]
    if conditions:
        lines += [conditions.read_text(encoding="utf-8").strip(), ""]
    if grid:
        lines += grid_section(pd.read_csv(grid), out)
    if steps:
        lines += steps_section(pd.read_csv(steps))
    if scale:
        lines += scale_section(pd.read_csv(scale))
    if timeline:
        lines += timeline_section(pd.read_csv(timeline), out)
    report = out / "REPORT.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sllmbench-report")
    parser.add_argument("--grid", type=Path)
    parser.add_argument("--timeline", type=Path)
    parser.add_argument("--steps", type=Path, help="sllmbench-collect steps 결과")
    parser.add_argument("--scale", type=Path, help="sllmbench-collect scale 결과")
    parser.add_argument(
        "--conditions", type=Path, help="REPORT.md에 그대로 넣을 실행 조건 Markdown"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default="cpu-sllm-k8s 측정 리포트")
    args = parser.parse_args(argv)
    if not args.grid and not args.timeline:
        parser.error("--grid 또는 --timeline 중 하나는 필요하다")
    print(
        build(
            args.grid,
            args.timeline,
            args.out,
            args.title,
            steps=args.steps,
            scale=args.scale,
            conditions=args.conditions,
        )
    )


if __name__ == "__main__":
    main()
