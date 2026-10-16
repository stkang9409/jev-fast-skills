---
name: fast-context
description: TypeSafe Jev 기반 초고속(sub-2s) 코드베이스 구조화 인덱스 추출기. 다중 디렉터리의 핵심 파일·심볼 구간(L시작-L끝)과 참조 그래프를 간결한 마크다운 목록으로 출력합니다.
---

# Fast Context (Structured Codebase Index & Call-Graph Locator)

<role>
다중 디렉터리 코드베이스에서 작업 의도(Task Intent)와 관련된 소스 파일·심볼 구간(`L시작-L끝`)과 계층 간 참조 그래프를 2초 이내에 추출하여, 탐색 단계의 시행착오를 줄이는 정적·시맨틱 인덱싱 도구입니다.
</role>

<priority_hierarchy>
코드 탐색 시 도구 선택 우선순위 위계(1순위 > 2순위 > 3순위)는 다음과 같습니다:
1순위 (단일 대상 직접 열람): 대상 파일 경로나 고유 심볼명을 이미 알고 있는 경우에는 별도 인덱싱 스크립트를 실행하는 대신 `view_file`로 해당 파일을 즉시 열람합니다.
2순위 (다중 디렉터리 단일 인덱싱): 구조가 생소한 여러 디렉터리를 함께 파악해야 할 때는 `fc-hint.sh`를 1회 실행하여 파일별 핵심 심볼 구간과 참조 관계를 먼저 확보합니다.
3순위 (확인된 구간 병렬 열람): 인덱스에 표시된 심볼과 참조 위치는 중복 탐색을 생략하고 `view_file`(`StartLine`~`EndLine`) 병렬 호출로 본문을 확인합니다.
</priority_hierarchy>

<reasoning_steps>
다중 디렉터리 탐색 과제를 수행할 때는 다음 3단계 판단 절차를 순서대로 따릅니다:
1. **1단계 (탐색 대상 디렉터리 지정 및 인덱싱)**: 작업 의도와 연관된 하위 디렉터리들을 한 번의 `fc-hint.sh` 호출로 전달하여 구조화 인덱스를 추출합니다.
2. **2단계 (인덱스 기반 본문 병렬 확인)**: 출력된 인덱스에서 검증이 필요한 파일 경로와 라인 범위(`L시작-L끝`)를 선별하여 단일 턴에 병렬 `view_file`로 열람합니다.
3. **3단계 (미포함 영역 보완 및 사실 기반 보고)**: 인덱스 외부의 세부 구현이 추가로 필요한 경우에만 표적 탐색을 1회 수행한 뒤, 확인된 원본 파일·라인 근거를 들어 답변합니다.
</reasoning_steps>

<rules>
1. [인덱스 확인 구간 즉시 열람]: `fc-hint.sh` 출력에 이미 포함된 파일 경로와 심볼 라인 범위(`L시작-L끝`)는 재탐색 명령을 반복하는 대신 `view_file`의 `StartLine`~`EndLine` 인자로 지정하여 직접 열람합니다.
2. [다중 경로 단일 배치 전달]: 연관된 여러 패키지나 모듈 디렉터리를 조사할 때는 경로별로 스크립트를 나누어 실행하는 대신 단일 호출의 인자로 함께 전달하여 교차 참조 관계를 한 번에 수집합니다.
3. [근거 부재 시 재확인 및 전제 교정 (Edge-Case & Anti-Sycophancy Fallback)]:
   - 인덱스 출력만으로 질문에 대한 근거가 충분하지 않을 때는 임의로 동작이나 라인 번호를 추측하는 대신, 부족한 심볼만 추가 확인하거나 확인된 사실 범위를 제시하고 사용자에게 보충 정보를 요청합니다.
   - 사용자가 실제 코드와 다른 함수명이나 잘못된 아키텍처 전제를 제시할 때는 무비판적으로 동조하는 대신 확인된 원본 파일의 라인 범위(`L시작-L끝`)를 근거로 제시하여 정중히 교정합니다.
</rules>

<cli_reference>
```bash
# 1. 권장 사용법: 대상 디렉터리들의 구조화 인덱스 추출
~/.claude/skills/fc-hint.sh "<TASK_INTENT>" <REPO_ROOT> <REL_DIR_1> <REL_DIR_2>

# 2. bundle_context.py 직접 호출 (쉼표 구분 다중 경로 인덱스 출력)
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-context/scripts/bundle_context.py "<TASK_INTENT>" --repo-root <REPO_ROOT> --path "dirA,dirB" --hint
```
</cli_reference>

<output_format>
표준출력(stdout)은 다음 마크다운 스키마와 상한 조건(파일당 심볼 최대 5개, 디렉터리당 보조 요약 최대 10개, 교차 참조 항목 각 최대 10개)을 준수하여 출력됩니다:
- **디렉터리별 심볼 및 참조 행 (`string`)**: `## <dir_path>` 아래 `- <rel_file_path>: \`<symbol_name>\` L<start:int>-L<end:int> [← <caller_ref>] [<metadata_tags>]`
- **교차 계층 참조 섹션 (`string`)**: 외부 진입 경로 및 데이터 계층 참조 목록
```markdown
## <dir_path:string>
- <rel_file_path:string>: `<symbol_name:string>` L<start_line:int>-L<end_line:int> [← <caller_file:string>:L<caller_line:int>] [<tag_kind:string>: <tag_value:string> L<line:int>]
- [<aux_summary_label:string>]: `<other_file:string>` (<exported_symbol:string> L<start:int>-L<end:int>)
## <cross_layer_section:string>
- <source_ref:string> → <target_file:string>:L<start:int>-L<end:int> (`<target_symbol:string>`)
```
</output_format>
