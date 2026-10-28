# reports/

측정 실행 하나마다 폴더 하나를 둔다.

```
reports/
└── <run-id>/              # 예: grid-guaranteed-20261014T0334Z, hpa-20261014T0700Z
    ├── REPORT.md          # sllmbench-report가 만든 실행 조건·표·그림 목록
    ├── grid_*.png         # 스레드 × CPU limit 히트맵 (tokens/s, 스로틀 비율, p95)
    └── hpa_timeline.png   # 레플리카·처리량·p95·스로틀 비율의 시간 축 그림
```

- `host-power.txt`: 실행 구간 앞뒤의 macOS 잠자기·깨어남 기록. `scripts/host_power.sh <시작 UTC> <끝 UTC>`가 `pmset -g log`에서 뽑는다. 실행 중 호스트가 멈추지 않았음을 확인하는 근거로 둔다.
- 원자료(k6 CSV, 수집기 CSV, watch 기록)는 `results/<run-id>/`에 쌓이고 커밋하지 않는다.
- 실행 조건(클러스터·노드·이미지·VM 크기·배포 값·실험 입력)은 실험 스크립트가 `scripts/run_conditions.sh`로
  `results/<run-id>/conditions.md`에 남기고, `sllmbench-report --conditions`가 `REPORT.md` 머리에 그대로 넣는다.
  손으로 옮겨 적지 않는다.
- `tests/fixtures/`의 합성 CSV로 만든 리포트는 이 폴더에 두지 않는다.

## 실행 목록

모두 kind on macOS(colima)에서 2026-10-14에 실행했다. 실행 조건은 각 `REPORT.md` 머리에 있다.

| run-id | 내용 |
|---|---|
| [grid-guaranteed-20261014T0334Z](grid-guaranteed-20261014T0334Z/REPORT.md) | 격자 12칸, Guaranteed |
| [grid-burstable-20261014T0413Z](grid-burstable-20261014T0413Z/REPORT.md) | 격자 12칸, Burstable(requests 500m) |
| [hpa-20261014T0555Z](hpa-20261014T0555Z/REPORT.md) | HPA 계단 부하, 기본 STEPS 6,12,18,24 |
| [grid-guaranteed-repeat-20261014T0619Z](grid-guaranteed-repeat-20261014T0619Z/REPORT.md) | 격자 4칸 재실행(threads 2·4 × limit 2·4), Guaranteed |
| [hpa-steps20-20261014T0634Z](hpa-steps20-20261014T0634Z/REPORT.md) | HPA 계단 부하, STEPS 20,40,60,80 |

`hpa-20261014T0452Z`는 실행 도중 호스트가 잠자기에 들어가 결과에서 뺐다(근거: `hpa-20261014T0555Z/host-power.txt`에 있는 14:06~14:53(+0900)의 잠자기 기록. 그중 14:28:43부터 900초는 'Thermal Emergency Sleep'이다).

결과에 넣지 않고 중단한 실행이 두 번 더 있고, 둘 다 원자료를 지워 남아 있지 않다.

- `grid-guaranteed-repeat-20261014T0543Z`: `caffeinate` 없이 시작한 격자 재실행. 첫 칸(threads 2 · limit 2) 도중 중단했고, 이어서 HPA 실행과 격자 재실행을 `caffeinate`를 붙여 다시 돌렸다(`hpa-20261014T0555Z`, `grid-guaranteed-repeat-20261014T0619Z`).
- `hpa-steps20-20261014T0632Z`: 직전 격자 재실행의 CPU 사용량이 메트릭에 남아 HPA가 레플리카를 2개로 늘려 둔 상태에서 시작한 실행. 부하 시작 직후 중단했고, `scripts/run_hpa.sh`에 레플리카 대기 단계(884ddee)를 넣은 뒤 `hpa-steps20-20261014T0634Z`로 다시 돌렸다.
