// HPA 곡선용 계단 부하. 도착률(분당 요청 수)을 계단식으로 올렸다가 내린다.
//   k6 run -e BASE_URL=http://localhost:8080 -e STEPS=6,12,18,24 -e STEP_DURATION=4m \
//     --out csv=results/run.csv load/k6/stepped.js
// 값들은 부하 모양을 정하는 입력이며, 처리 가능량에 대한 예상치가 아니다.
import { chat, summary } from './lib.js';

const steps = (__ENV.STEPS || '6,12,18,24').split(',').map((s) => parseInt(s, 10));
const hold = __ENV.STEP_DURATION || '4m';
const ramp = __ENV.RAMP || '10s';
const cooldown = __ENV.COOLDOWN || '6m';

const stages = [];
for (const rate of steps) {
  stages.push({ target: rate, duration: ramp });
  stages.push({ target: rate, duration: hold });
}
// HPA scaleDown 안정화 시간(기본 300초)보다 길게 낮은 부하를 유지해 축소 구간까지 기록한다.
stages.push({ target: steps[0], duration: ramp });
stages.push({ target: steps[0], duration: cooldown });

export const options = {
  discardResponseBodies: false,
  scenarios: {
    stepped: {
      executor: 'ramping-arrival-rate',
      startRate: steps[0],
      timeUnit: '1m',
      preAllocatedVUs: parseInt(__ENV.PRE_VUS || '20', 10),
      maxVUs: parseInt(__ENV.MAX_VUS || '200', 10),
      stages,
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'max'],
};

export default function () {
  chat();
}

export function handleSummary(data) {
  return summary(data, 'stepped');
}
