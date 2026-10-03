# reports/

측정 실행 하나마다 폴더 하나를 둔다. **아직 측정을 하지 않아 실행 폴더가 없다.**

```
reports/
└── <run-id>/              # 예: grid-20261015T0300Z, hpa-20261015T0500Z
    ├── REPORT.md          # sllmbench-report가 만든 표와 그림 목록
    ├── grid_*.png         # 스레드 × CPU limit 히트맵 (tokens/s, 스로틀 비율, p95)
    └── hpa_timeline.png   # 레플리카·처리량·p95·스로틀 비율의 시간 축 그림
```

- 원자료(k6 CSV, 수집기 CSV)는 `results/<run-id>/`에 쌓이고 커밋하지 않는다.
- `REPORT.md`에는 입력 파일 경로와 생성 시각이 적힌다. 실행 조건(노드 타입, 이미지 태그, 모델, `STEPS` 등)은 실행할 때 `REPORT.md` 위에 손으로 덧붙인다.
- `tests/fixtures/`의 합성 CSV로 만든 리포트는 이 폴더에 두지 않는다.
