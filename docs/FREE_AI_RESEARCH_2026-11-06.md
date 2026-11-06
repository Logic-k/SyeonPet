# 로그인·키 없는 무료 AI 재조사 (2026-11-06)

가상 업무 요청만 사용했습니다. 응답 시간과 품질은 시험한 시점의 관찰값입니다. 서비스의 지속성이나 정확도를 보증하지 않습니다.

## 결론

- **한국에서 만든 모델은 로그인·키 없이 쓸 수 있는 경로가 없습니다.** 확인한 모델은 EXAONE, HyperCLOVA X SEED, Kanana, A.X, Solar, Mi:dm, VARCO, Raon입니다.
  - Upstage, Hugging Face Router, FriendliAI는 실제로 호출했을 때 401(키 필요)이 나왔습니다. FriendliAI의 K-EXAONE 무료 제공은 2026-01-28에 끝났습니다.
  - Hugging Face의 ZeroGPU Space는 익명 사용 시 하루 약 2분까지만 쓸 수 있습니다. 같은 공인 IP를 쓰는 사람들이 이 한도를 함께 씁니다. 이번 시험에서는 생성 결과를 하나도 받지 못했습니다.
- 공식 문서가 키 없는 사용을 밝힌 곳은 네 곳입니다: **JarvisClaw, Kilo AI Gateway, OVHcloud AI Endpoints, Pollinations Classic.** 그 밖의 곳은 키가 필요하거나, 약관이 자동 호출을 금지하거나, 웹 화면을 역공학한 비공식 경로입니다.
- 한국어 요청문 정리 품질은 Kilo(`kilo-auto/free`)와 JarvisClaw(`auto/free`)가 가장 좋았습니다. 데이터 처리 조건은 OVH(`gpt-oss-120b`)가 가장 좋았습니다.

## 가상 업무 6종 × 2회, 같은 기준으로 채점

사례마다 채점자 한 명이 여섯 후보의 결과를 나란히 놓고 같은 기준으로 판정했습니다. 6종은 연수 안내, 회의록, 학교 문의 회신, 시설 지원, 기초학력, 공개자료 요약입니다.

| 후보 | 유용성(1~5) | 문제 없는 결과(12건 중) | 주요 문제 | 응답 시간 중앙값 |
|---|---|---|---|---|
| Kilo `kilo-auto/free` | 3.42 | 6 | 요청문 대신 결과 문서를 직접 작성 2, 원문 거의 복사 6 | 약 7초 |
| JarvisClaw `auto/free` (현재 기본값) | 3.33 | 6 | 요청문 대신 직접 작성 1, 원문 거의 복사 5 | 약 8초 |
| OVH `gpt-oss-120b` | 3.08 | 4 | 문의자·답변자 혼동 1, 미확정을 확정으로 바꿈 1, 직접 작성 2 | 약 10초 |
| Pollinations `openai-fast` | 2.83 | 2 | 문의자·답변자 혼동 2. 2회차는 캐시 응답이라 실제 표본은 6건 | 약 6초 |
| OVH `Mistral-Small-3.2` | 2.33 | 0 | 직접 작성 5, '행정지원과'를 지어냄 2, 조건 누락 4 | 약 3초 |
| OVH `Llama-3.3-70B` | 1.67 | 0 | 직접 작성 7, 역할 혼동 5, 조건 누락 8 | 약 2.5초 |

추가로 관찰한 점이 있습니다.

- JarvisClaw 기준선 3회 중 1회는 추론형 모델로 연결됐습니다. 이때 영어 사고 과정이 본문에 나오고 `finish=length`로 잘렸습니다.
- Kilo는 정체를 알 수 없는 `stealth/glyph-cluster` 모델로 연결됐습니다.

## 서비스 조건 (반증 검토 결과)

- **OVH**
  - 익명 사용은 IP당·모델당 분당 2회입니다.
  - 약관에는 입력·출력을 알지 못하고 재사용하지 않으며 저장하지 않는다고 적혀 있습니다. 서버는 유럽에 있습니다.
  - 배포 앱에 넣는 것을 허용하거나 금지하는 문구는 없습니다. 같은 문서 안에 "요청마다 키가 필요하다"는 문장도 있어 서로 어긋납니다.
  - 익명 사용자는 SLA(서비스 수준 약정)와 계약의 보호를 받지 못합니다.
- **Kilo**
  - 무료 모델만 인증 없이 쓸 수 있고, IP당 시간당 200회까지입니다.
  - 무료 경로에서는 프롬프트가 기록되고 학습에 쓰일 수 있습니다. 공식 문서도 개인정보와 기밀 데이터를 넣지 말라고 적습니다.
  - 약관은 앱에 넣어 쓰는 경우를 예정하지만, 익명 호출에 대한 문구는 없습니다.
- **JarvisClaw**
  - 무료 페이지는 키·계정 없이 쓸 수 있고 IP별로 제한한다고 설명합니다.
  - 개인정보처리방침(2026-05-26)은 프롬프트를 저장하지 않고, 메타데이터는 90일 보관하며, 학습에 쓰지 않는다고 적습니다.
  - 반면 공식 SDK 문서는 `auto/*`를 유료로 정산한다고 적어 서로 어긋납니다.
  - 준거법은 홍콩이고 SLA는 없습니다.
- **Pollinations Classic**
  - 문서는 "기존 앱을 위해 안정적으로 유지한다"고 하지만, 새 앱에는 키 사용을 권장합니다.
  - 한 IP에서 한 번에 한 요청만 되고, 다음 요청까지 30~45초를 기다려야 합니다.
  - 응답을 최대 30일 캐시합니다.

## 앱에 반영할 점

- 빈 응답과 `finish=length`는 실패로 처리합니다(이미 그렇게 동작함). 앱은 응답 본문(`content`)만 쓰므로 별도 `reasoning` 필드는 결과에 들어가지 않습니다. 시험에서 모은 응답 565건 중 영어 사고 과정이 섞인 2건은 모두 `finish=length`로 끝나 이미 실패 처리됐고, `<think>` 태그는 없었습니다. 그래서 별도 감지 규칙은 넣지 않았습니다.
- v0.2.9부터 AI가 결과에 새로 넣은 U+202F·U+00A0 같은 특수 공백을 일반 공백으로 바꾼 뒤 사실 보존 여부를 검사합니다. 사용자가 원문에 직접 넣은 특수 공백은 그대로 둡니다. 이전에는 '30명 이내'나 따옴표 속 문구에 특수 공백이 들어가면 원문 전체가 결과 뒤에 다시 붙었습니다.
- 모든 무료 경로는 IP별 한도가 있어 사무실의 공용 IP에서 한도를 함께 씁니다. 429 응답을 안내하는 문구가 필요합니다. 다른 서비스로 자동 전송하지 말고 사용자 확인을 거칩니다.
- 모든 후보가 요청문을 다시 쓰지 않고 공문을 직접 쓰거나 원문을 거의 그대로 옮기는 경향을 보였습니다. 지시문 구조(목적·대상·조건·금지·출력 형식)와 예시를 보강할 여지가 큽니다.

## 앱 경로 실측: Kilo·OVHcloud 추가 후 (v0.2.9 개발본)

앱의 실제 요청 경로(`optimize_ai`)로 기존 가상 사례 11개(일반 6, 역할 5)를 공급자마다 한 번씩 보냈습니다. 지시문은 v0.2.8과 같습니다. 결과는 출처를 가린 채 사례마다 채점자 2명이 판정했습니다.

| 공급자 | 성공 | 응답 시간 | 평균 품질(1~10) | 사실·역할 등이 틀린 비율 | 사례별 1위 |
|---|---|---|---|---|---|
| Kilo `kilo-auto/free` | 11/11 | 7~25초 | 7.50 | 9% | 10 |
| JarvisClaw `auto/free` | 11/11 | 13~50초 | 4.89 | 41% | 0 |
| OVHcloud `gpt-oss-120b` | 11/11 | 14~23초 | 4.57 | 45% | 1 |

- **OVHcloud:** 요청하지 않은 제한(12건)과 역할 혼동(10건)이 많았습니다. 원문에 없는 요일(“11월 7일(월)”)을 지어낸 사례도 있습니다. 30초 간격을 지켜 429 응답은 없었습니다.
- **JarvisClaw:** 오늘은 응답이 느렸고, 문의 회신 사례에서 문의 관계를 잃었습니다.
- **Kilo:** 품질은 가장 좋았지만 무료 경로가 요청을 기록·학습에 쓰는 제공자로 보낼 수 있습니다. 그래서 기본값은 JarvisClaw로 두고 선택지로만 추가했습니다.
- 공급자마다 한 번씩 보낸 작은 표본이고, 공유 모델의 연결 대상과 부하는 시간대마다 바뀝니다.

## 탈락한 후보

- **키나 로그인이 필요함:** LLM7(익명 사용이 문서에 없음), mlvoca(상업 사용 금지, 소형 모델), ApiFreeLLM, Hack Club, gen.pollinations, Groq, Cerebras, Gemini, Mistral, Cloudflare, SambaNova, DeepInfra, api.airforce, Puter(사용자 로그인 필요).
- **약관에 걸리거나 비공식:** duck.ai(약관이 자동 호출 금지), gpt4free 제공자 전체와 OllamaFreeAPI(역공학이거나 남의 서버 사용), OpenCode Zen(OpenCode 앱 전용).
- **공공기관에 부적합:** AI Horde(자원봉사자 서버로 전달되고 품질이 낮음).

## 근거 링크

- [OVH AI Endpoints 기능 문서](https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-endpoints-capabilities)
- [Kilo Gateway 인증 문서](https://kilo.ai/docs/gateway/authentication), [Kilo 약관](https://kilo.ai/terms)
- [JarvisClaw 무료 LLM 안내](https://jarvisclaw.ai/en/free-llm-api), [JarvisClaw 개인정보처리방침](https://api.jarvisclaw.ai/api/privacy-policy)
- [Pollinations APIDOCS](https://github.com/pollinations/pollinations/blob/master/APIDOCS.md)
- [ZeroGPU 문서](https://huggingface.co/docs/hub/spaces-zerogpu), [Friendli K-EXAONE 안내](https://friendli.ai/blog/k-exaone-on-serverless)
- [0xzr/freellmpool](https://github.com/0xzr/freellmpool)(키 없는 경로 목록과 상태 확인 방식 참고), [mnfst/awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis)
