#!/usr/bin/env bash
# macOS 호스트가 실행 구간에 잠자기에 들어가지 않았는지 남긴다. 잠자기에 들어가면 VM과 k6가 함께 멈춰
# 지연·HPA 반응에 멈춘 시간이 섞인다. pmset -g log에서 시작 1시간 전부터 끝까지의
# Sleep·Wake·DarkWake·ThermalEvent 줄을 뽑는다(Wake Requests 줄은 뺀다).
#   scripts/host_power.sh 2026-10-14T05:55:00 2026-10-14T06:19:30 > reports/<run-id>/host-power.txt
set -euo pipefail

START="${1:?실행 시작 시각(UTC, ISO 8601)}"
END="${2:?실행 끝 시각(UTC, ISO 8601)}"

pmset -g log | python3 -I -c '
import datetime as dt
import re
import subprocess
import sys

start, end = (dt.datetime.fromisoformat(a).replace(tzinfo=dt.timezone.utc) for a in sys.argv[1:3])
model = subprocess.run(["sysctl", "-n", "hw.model"], capture_output=True, text=True).stdout.strip()
print("# 호스트 전원 상태 (pmset -g log 발췌)\n")
print(f"hw.model: {model}")
print(f"실행 구간(UTC): {sys.argv[1]} ~ {sys.argv[2]}")
print("발췌 범위: 실행 시작 1시간 전부터 끝까지의 Sleep·Wake·DarkWake·ThermalEvent 줄, Wake Requests 제외 (시각은 기록 그대로)\n")
found = False
for line in sys.stdin:
    m = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ([+-]\d{4}) (\S+)", line)
    if not m or m.group(3) not in ("Sleep", "Wake", "DarkWake", "ThermalEvent") or "Wake Requests" in line:
        continue
    t = dt.datetime.strptime(m.group(1) + m.group(2), "%Y-%m-%d %H:%M:%S%z")
    if start - dt.timedelta(hours=1) <= t <= end:
        print(re.sub(r"\s+", " ", line.strip())[:200])
        found = True
if not found:
    print("(해당 줄 없음)")
' "$START" "$END"
