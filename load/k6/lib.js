import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const data = JSON.parse(open('../prompts.json'));

export const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const MAX_TOKENS = parseInt(__ENV.MAX_TOKENS || '128', 10);

// 요청 하나의 생성 속도(llama-server timings.predicted_per_second)
const genTps = new Trend('llm_gen_tokens_per_s');
// 생성 토큰 합계. 구간 처리량은 이 카운터를 시간으로 나눠 구한다.
const completionTokens = new Counter('llm_completion_tokens');

export function chat() {
  const prompt = data.prompts[Math.floor(Math.random() * data.prompts.length)];
  const body = JSON.stringify({
    messages: [
      { role: 'system', content: data.system },
      { role: 'user', content: prompt },
    ],
    max_tokens: MAX_TOKENS,
    temperature: 0,
    stream: false,
  });
  const res = http.post(`${BASE_URL}/v1/chat/completions`, body, {
    headers: { 'Content-Type': 'application/json' },
    timeout: __ENV.REQUEST_TIMEOUT || '300s',
    tags: { name: 'chat' },
  });

  const ok = check(res, { 'status is 200': (r) => r.status === 200 });
  if (!ok) {
    return;
  }
  const json = res.json();
  if (json.usage && typeof json.usage.completion_tokens === 'number') {
    completionTokens.add(json.usage.completion_tokens);
  }
  if (json.timings && typeof json.timings.predicted_per_second === 'number') {
    genTps.add(json.timings.predicted_per_second);
  }
}

// 결과 요약을 JSON으로 남긴다. 표 값은 이 파일과 수집기 CSV에서 계산한다.
export function summary(data, name) {
  const out = __ENV.SUMMARY_OUT || `${name}-summary.json`;
  return { [out]: JSON.stringify(data, null, 2), stdout: '\n' };
}
