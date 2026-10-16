---
name: fast-browser
description: TypeSafe Jev 기반의 초고속(스텝당 ~200ms) 브라우저 자동화 및 텍스트 핸드오프 도구. 비시각적 웹 탐색·폼 입력·검색을 처리하며 자유 서술형 입력 시 에이전트로 제어권을 핸드오프합니다.
---

# Fast Browser (TypeSafe Jev Browser Automation & Handoff)

<role>
TypeSafe Jev(System One AI)를 통해 웹 페이지 탐색, 버튼 클릭, 정형 슬롯 입력을 스텝당 ~200ms 속도로 자동화하고, 자유 서술형 텍스트 작성이 필요한 시점에만 에이전트에게 핸드오프(`NEED_TEXT`)한 뒤 내장 RAM 세션으로 즉시 재개(`--resume`)하는 CLI 브라우저 제어 도구입니다.
</role>

<priority_hierarchy>
웹 정보 수집 및 브라우저 제어 시 다음 우선순위 위계(1순위 > 2순위 > 3순위)를 따릅니다:
1순위 (정적 공개 문서 조회): 동적 스크립트 실행이나 폼 입력이 불필요한 정적 웹 페이지 조회에서는 헤드리스 브라우저 실행 대신 `read_url_content` 또는 `search_web` 도구를 우선 호출합니다.
2순위 (비시각적 동적 탐색 및 폼 자동화): 검색창 입력, 다단계 페이지 이동, 정형 폼 제출처럼 동적 상호작용이 필요한 작업에서는 `fast-browser`를 호출하여 최소 턴으로 처리합니다.
3순위 (복합 인증 및 시각적 UI 디버깅): 2단계 인증(2FA), CAPTCHA 해결, 또는 레이아웃·콘솔 에러의 시각적 점검이 필수적인 작업에서는 `chrome-devtools` 스킬로 전환합니다.
</priority_hierarchy>

<reasoning_steps>
웹 자동화 과제를 수행할 때는 다음 3단계 판단 및 실행 절차를 순서대로 적용합니다:
1. **1단계 (입력 슬롯 사전 구조화)**: 목표 URL(`--url`), 달성 목표(`--goal`), 이미 확정된 정형 키-값 쌍(`--slots`)을 JSON 형식으로 정리하여 1차 실행합니다.
2. **2단계 (상태 코드 판별 및 핸드오프 대응)**: 반환된 JSON의 `status` 값을 확인하여 `"DONE"`이면 결과를 요약하고, `"NEED_TEXT"`이면 `field_label`과 `context`에 맞는 서술형 텍스트를 작성해 `--resume --text`로 세션을 재개합니다.
3. **3단계 (결과 검증 및 예외 전환)**: 최종 반환된 `current_url`과 `page_text`가 요청 의도와 일치하는지 검증하고, 인증 장벽이나 요소 부재 시 지정된 대체 경로로 전환합니다.
</reasoning_steps>

<rules>
1. [정형 슬롯 일괄 주입]: 정형 입력 필드 값이 사전에 파악된 경우에는 단계별로 세션을 멈추는 대신 `--slots '{"key": "value"}'` JSON 인자로 일괄 전달하여 단일 호출로 탐색을 완료합니다.
2. [서술형 텍스트 핸드오프 및 재개]: 실행 결과 JSON에서 `"status": "NEED_TEXT"`가 반환되면, 세션을 처음부터 다시 여는 대신 반환된 `field_label`과 `context`에 맞춰 텍스트를 작성한 뒤 `--resume --text "<TEXT>"`로 즉시 이어서 완료합니다.
3. [인증 장벽 및 탐색 실패 시 대체 행동 (Edge-Case & Anti-Sycophancy Fallback)]:
   - 인증(2FA/CAPTCHA) 요구나 요소 미발견으로 `"status": "FAILED"` 또는 `"MAX_STEPS"`가 반환되면, 무리한 반복 호출이나 임의 추측 대신 `reason`과 `page_text` 근거를 확인하여 검색어를 1회 보정하거나 사용자에게 인증 전환(`chrome-devtools`)을 요청합니다.
   - 사용자가 존재하지 않는 웹 페이지 요소나 잘못된 URL 전제를 지시할 때는 그대로 추종하는 대신 실제 조회된 `current_url`과 `page_text` 요약을 근거로 제시하여 객관적으로 안내합니다.
</rules>

<cli_reference>
```bash
# 1. 기본 탐색 및 정형 슬롯 일괄 전달 실행
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-browser/scripts/fast_browse.py --url "<TARGET_URL>" --goal "<TASK_GOAL>" --slots '{"field": "value"}'

# 2. NEED_TEXT 핸드오프 발생 시 텍스트 작성 후 세션 재개
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-browser/scripts/fast_browse.py --resume --text "<GENERATED_TEXT>"
```
</cli_reference>

<output_format>
실행 결과는 다음 3가지 상태 스키마 중 하나를 포함한 단일 JSON 객체로 표준출력(stdout)됩니다:
- `{"status": "DONE", "current_url": "<최종_URL>", "page_text": "<페이지_요약_본문>"}`: 목표 달성 완료 및 페이지 본문 요약(최대 2,000자).
- `{"status": "NEED_TEXT", "field_label": "<입력_필드명>", "current_url": "<현재_URL>", "context": {...}}`: 자유 서술형 텍스트 핸드오프 요청.
- `{"status": "MAX_STEPS" | "FAILED", "reason": "<실패_또는_중단_사유>"}`: 스텝 상한(기본 10회) 도달 또는 명시적 중단 사유.
</output_format>
