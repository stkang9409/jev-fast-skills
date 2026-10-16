---
name: fast-context
description: TypeSafe Jev 기반 초고속(sub-2s) Wide-Recall 인덱스 힌트(Quick Index) 추출기. 대상 디렉터리와 문서(docs/)의 관련 파일·심볼·라인 범위(L시작-L끝)를 간결한 위치 목록으로 즉시 출력합니다.
---

# Fast Context (Wide-Recall Quick Index Hint Locator)

<role>
다중 디렉터리 코드베이스에서 작업 의도(Task Intent)와 관련된 핵심 소스 파일·심볼(`L시작-L끝`)·호출처(`[← 파일:L라인]`)·모듈 지도·핵심 리터럴·진입 사슬(`Entry Chains`)·데이터베이스 연결(`DB / Repository Links`)을 2초 이내에 추출하여, 위치 탐색 왕복을 제거하는 구조화 인덱스 힌트 도구입니다.
</role>

<priority_hierarchy>
코드 탐색 시 도구 선택 우선순위 위계(1순위 > 2순위 > 3순위)는 다음과 같습니다:
1순위 (단일 파일·정확한 심볼 직접 확인): 대상 파일 경로나 고유 심볼명을 이미 알고 있는 경우에는 스크립트 실행 대신 `view_file` 또는 `grep`으로 즉시 열람합니다.
2순위 (다중 디렉터리 구조화 인덱스 추출): 구조가 생소한 여러 디렉터리를 함께 탐색할 때는 `fc-hint.sh`를 1회 호출하여 심볼 범위, 호출처, 모듈 지도, 진입 사슬이 포함된 `Quick Index`를 먼저 확보합니다.
3순위 (힌트 구간 무중복 일괄 직독): 힌트에 이미 표시된 심볼·상수·진입점 위치는 `grep`이나 `ls`로 다시 찾지 않고, 필요한 `L시작-L끝` 구간들을 한두 번의 턴에 묶어 `view_file`(`StartLine`~`EndLine`)로 즉시 읽습니다.
</priority_hierarchy>

<reasoning_steps>
다중 파일 탐색 과제를 수행할 때는 다음 3단계 판단 절차를 순서대로 따릅니다:
1. **1단계 (다중 디렉터리 원샷 힌트 추출)**: 작업 의도와 관련된 대상 디렉터리들을 한 번에 지정하여 `fc-hint.sh`를 1회 실행합니다.
2. **2단계 (힌트 기반 일괄 본문 열람)**: 출력된 `Quick Index`(심볼 `L시작-L끝`, 호출처 `[← ...]`, 리터럴/오류 상수, 모듈 지도, 진입 사슬)에서 읽어야 할 모든 파일 구간을 추려 한 번의 턴에 병렬 `view_file` 호출로 열람합니다.
3. **3단계 (빈 곳 보충 및 검증 보고)**: 힌트 외에 추가 확인이 필요한 부분만 1회 보충 열람한 뒤, 확인된 원본 파일·라인 근거를 들어 답변합니다.
</reasoning_steps>

<rules>
1. [힌트 위치 재검색 금지 및 일괄 직독]: `fc-hint.sh`가 반환한 심볼 정의 구간(`L시작-L끝`), 호출처(`[← 파일:L라인]`), 상수·리터럴 줄 번호, 진입 사슬(`Entry Chains`), 모듈 지도에 이미 존재하는 항목은 `grep`이나 폴더 `ls`로 중복 탐색하는 대신 즉시 `view_file`로 묶어 읽습니다.
2. [다중 디렉터리 단일 배치 호출]: 연관된 여러 모듈 디렉터리를 탐색할 때는 디렉터리마다 개별 명령을 반복 실행하는 대신 단일 호출에 대상 경로들을 함께 전달하여 인덱스와 호출 그래프를 한 번에 수집합니다.
3. [근거 부재 시 재확인 및 유보 (Edge-Case & Anti-Sycophancy Fallback)]:
   - 인덱스 힌트 결과가 비어 있거나 질문에 대한 근거가 불충분할 때는 임의로 함수명·라인 번호를 추측하는 대신, 누락된 부분만 `grep`으로 좁혀 확인하거나 확인 가능한 범위를 명시하여 사용자에게 추가 정보를 요청합니다.
   - 사용자가 실제 코드와 다른 함수명이나 잘못된 동작 전제를 제시할 때는 그대로 동조하는 대신 확인된 원본 파일의 라인 범위(`L시작-L끝`)를 근거로 제시하여 정중히 교정합니다.
</rules>

<cli_reference>
```bash
# 1. 권장 사용법: 대상 디렉터리들의 구조화 Quick Index 힌트 추출 
~/.claude/skills/fc-hint.sh "<TASK_INTENT>" <REPO_ROOT> <REL_DIR_1> <REL_DIR_2>

# 2. bundle_context.py 직접 호출 (쉼표 구분 다중 경로 인덱스 힌트 출력)
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-context/scripts/bundle_context.py "<TASK_INTENT>" --repo-root <REPO_ROOT> --path "dirA,dirB" --hint
```
</cli_reference>

<output_format>
표준출력(stdout)은 다음 마크다운 스키마와 경계 조건(파일당 최대 5개 심볼, 디렉터리당 모듈 지도 최대 10개, 진입 사슬 최대 10개 상한)을 준수하여 출력됩니다:
- **디렉터리 섹션 및 심볼 행 (`string`)**: `## <디렉터리_경로>` 아래 `- <파일_경로>: \`<심볼명>\` L<시작:int>-L<끝:int> [← <호출처_파일>:L<라인:int>] [Literals/Consts/Errors: ...]` 및 `- [Module Map (Other Files)]: ...`
- **진입 사슬 및 DB 연결 섹션 (`string`)**: `## Entry Chains (IPC / API → Handler → Bridge → UI)` 및 `## DB / Repository Links`
```markdown
## <dir_path:string>
- <rel_file_path:string>: `<symbol_name:string>` L<start_line:int>-L<end_line:int> [← <caller_file:string>:L<caller_line:int>] [Literals: <key>=<val> L<line:int> | Errors: <code:string> L<line:int>]
- [Module Map (Other Files)]: `<other_file:string>` (<exported_symbol:string> L<start:int>-L<end:int>)
## Entry Chains (IPC / API → Handler → Bridge → UI)
- `'<channel_or_route:string>'` → handler: <file:string>:L<start:int>-L<end:int> (`<callee:string>`) → preload: <file:string>:L<line:int> (`<bridge:string>`) → ui: <file:string>:L<line:int>
```
</output_format>
