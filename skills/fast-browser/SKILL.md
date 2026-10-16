---
name: fast-browser
description: TypeSafe Jev 기반의 초고속(스텝당 ~200ms) 브라우저 자동화 및 텍스트 핸드오프 도구. 시각적 점검이 불필요한 웹 탐색·폼 입력·검색을 처리하며 자유 서술형 입력 시 에이전트로 제어권을 핸드오프합니다.
---

# Fast Browser (TypeSafe Jev Browser Automation & Handoff)

<role>
TypeSafe Jev(System One AI)를 통해 웹 페이지 탐색, 버튼 클릭, 정형 슬롯 입력을 스텝당 ~200ms 속도로 자동화하고, 자유 서술형 텍스트 입력이 필요한 시점에만 에이전트(Antigravity)에게 핸드오프(`NEED_TEXT`)한 뒤 재개(`--resume`)하는 브라우저 제어 도구입니다.
</role>

<priority_hierarchy>
웹 정보 수집 및 브라우저 제어 시 도구 선택 우선순위는 다음과 같습니다:
1순위 (정적 공개 문서 조회): 자바스크립트 실행이나 폼 상호작용이 필요 없는 단순 공개 URL 텍스트 읽기는 브라우저를 띄우는 대신 `read_url_content` 또는 `search_web`을 우선 사용합니다.
2순위 (비시각적 폼 입력·빠른 탐색): 검색창 입력, 다단계 링크 이동, 폼 제출 등 동적 상호작용이 필요하지만 사용자의 실시간 화면 확인이 불필요한 경우 `fast-browser`를 사용합니다.
3순위 (복합 인증·시각적 디버깅): 로그인 세션 유지, CAPTCHA 해결, 또는 CSS/레이아웃/콘솔 에러 디버깅이 필요한 경우 `chrome-devtools` 스킬을 사용합니다.
</priority_hierarchy>

<rules>
1. [기본 탐색 및 슬롯 주입]: 정형 입력값(출발지, 도착지, 검색어 등)이 이미 파악된 경우 `--slots '{"key": "value"}'` 인자로 사전 전달하여 핸드오프 없이 원샷으로 실행합니다.
2. [텍스트 핸드오프(Handoff & Resume) 절차]:
   - 실행 결과 JSON의 `status`가 `"NEED_TEXT"`이면, 반환된 `field_label`과 `context`를 확인하여 사용자 의도에 맞는 입력 텍스트를 작성합니다.
   - 작성한 텍스트를 `--resume --text "<TEXT>"` 인자로 전달하여 열려 있는 브라우저 세션을 즉시 이어서 완료합니다.
3. [예외 및 엣지 케이스 처리 (Fallback)]:
   -2FA/CAPTCHA/로그인 장벽으로 인해 `"status": "FAILED"` 또는 `"MAX_STEPS"`가 반환되면, 무리하게 재시도 루프를 돌리는 대신 사용자에게 인증 필요 상태를 알리거나 `chrome-devtools`로 전환합니다.
   - 페이지 내 목표 요소를 찾을 수 없는 경우 반환된 `page_text` 요약을 바탕으로 목표 URL이나 검색어를 수정하여 1회 재시도합니다.
</rules>

<cli_reference>
```bash
# 1. 기본 탐색 및 슬롯 전달 실행
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-browser/scripts/fast_browse.py   --url "https://example.com"   --goal "Search and open target page"   --slots '{"query": "keyword"}'

# 2. NEED_TEXT 핸드오프 발생 시 텍스트 작성 후 재개
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-browser/scripts/fast_browse.py   --resume   --text "작성할 입력 본문"
```
</cli_reference>

<output_format>
실행 결과는 항상 다음 상태 코드 중 하나를 포함한 JSON 객체로 표준 출력됩니다:
- `{"status": "DONE", "current_url": "...", "page_text": "..."}`: 목표 달성 완료 및 최종 페이지 본문 요약.
- `{"status": "NEED_TEXT", "field_label": "...", "current_url": "...", "context": {...}}`: 자유 서술형 입력 핸드오프 요청.
- `{"status": "MAX_STEPS" | "FAILED", "reason": "..."}`: 스텝 상한 도달 또는 탐색 실패 사유.
</output_format>
