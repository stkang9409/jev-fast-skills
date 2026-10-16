---
name: fast-context
description: TypeSafe Jev 기반 초고속(sub-1.5s) 2단계 시맨틱 컨텍스트 번들러. 다중 파일 탐색·리팩토링·버그 추적 시 관련 파일과 핵심 심볼 블록만 선별해 라인 번호와 클릭 링크가 포함된 마크다운 번들(.context/bundle.md)로 생성합니다.
---

# Fast Context (2-Stage Semantic Context Bundler)

<role>
다중 파일로 구성된 코드베이스에서 작업 의도(Task Intent)와 관련된 파일 및 함수/클래스 코드 블록만 2단계(파일 선별 -> 심볼 선별)로 추출하여 `.context/bundle.md` 단일 마크다운 번들로 조립하는 코드 탐색 도구입니다.
</role>

<priority_hierarchy>
코드 탐색 시 도구 선택 우선순위는 다음과 같습니다:
1순위 (단일 파일 확인): 이미 정확한 파일 경로와 라인 범위를 알고 있는 단일 파일 열람은 `view_file`을 즉시 호출합니다.
2순위 (다중 파일·구조 탐색): 2개 이상의 파일에 걸친 심볼 위치 파악, 호출 흐름 추적, 리팩토링 대상 탐색은 개별 파일을 순차 열람하는 대신 `fast-context`를 먼저 1회 실행합니다.
3순위 (정확성 보장): 번들 요약만으로 문맥이 부족한 심볼은 번들 내 클릭 링크(`file:///...#L시작-L끝`)의 라인 범위를 기준으로 원본 파일을 추가 확인합니다.
</priority_hierarchy>

<rules>
1. [기본 실행 및 열람 절차]: 다중 파일 탐색이 필요할 때 `bundle_context.py "<TASK_INTENT>" --path <DIR>`를 실행하여 `.context/bundle.md`를 생성한 뒤, `view_file`로 `.context/bundle.md`(1~800라인)를 열람합니다.
2. [검색 의도 작성 규칙]: `<TASK_INTENT>`에는 찾고자 하는 핵심 심볼명(함수/클래스/파일 스템)과 영문·국문 도메인 키워드를 구체적으로 함께 명시합니다.
3. [예외 및 엣지 케이스 처리 (Fallback)]:
   - 번들링 결과 매칭된 파일이 0개이거나 관련 심볼이 누락된 경우, 임의로 코드를 추측하지 않고 키워드를 넓혀 재실행하거나 대상 디렉토리의 엔트리 파일을 `view_file`로 직접 확인합니다.
   - 단독 실행 결과를 터미널 출력으로만 빠르게 확인해야 하는 경우에만 `--print-bundle` 플래그(자동 7,000자 상한 적용)를 사용합니다.
</rules>

<cli_reference>
```bash
# 1. 표준 실행 (.context/bundle.md 파일 생성 후 view_file로 열람 권장)
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-context/scripts/bundle_context.py "<TASK_INTENT>" --path <TARGET_DIR>

# 2. 터미널 요약 출력 포함 실행 (7,000자 이내 자동 압축 출력)
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-context/scripts/bundle_context.py "<TASK_INTENT>" --path <TARGET_DIR> --print-bundle
```
</cli_reference>

<output_format>
생성되는 `.context/bundle.md`는 다음 3개 섹션 스키마를 보장합니다:
1. `## Quick Index (File & Symbol Line Ranges)`: 선별된 모든 파일 경로(클릭 링크)와 내부 심볼명·종류(`function`, `class`, `method`)·정확한 시작~끝 라인 번호(`L시작-L끝`).
2. `## Selected Files`: 파일별 관련도 점수(`score: 0.00~1.00`).
3. `## Extracted Code Blocks`: `L0001:` 형식의 4자리 라인 번호가 접두어로 붙은 실제 소스 코드 블록.
</output_format>
