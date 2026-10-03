// 격자 실험용 고정 동시성 부하. 스레드 × CPU limit 조합마다 같은 조건으로 돌린다.
//   k6 run -e BASE_URL=http://localhost:8080 -e VUS=2 -e DURATION=3m \
//     --out csv=results/run.csv load/k6/constant.js
import { chat, summary } from './lib.js';

export const options = {
  discardResponseBodies: false,
  scenarios: {
    constant: {
      executor: 'constant-vus',
      vus: parseInt(__ENV.VUS || '2', 10),
      duration: __ENV.DURATION || '3m',
      gracefulStop: '60s',
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'max'],
};

export default function () {
  chat();
}

export function handleSummary(data) {
  return summary(data, 'constant');
}
