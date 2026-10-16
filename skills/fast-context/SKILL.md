---
name: fast-context
description: TypeSafe Jev 기반 초고속(sub-2s) Wide-Recall 인덱스 힌트(Quick Index) 추출기. 대상 디렉터리와 문서(docs/)의 관련 파일·심볼·라인 범위(L시작-L끝)를 간결한 위치 목록으로 즉시 출력합니다.
---

# Fast Context (Wide-Recall Quick Index Hint Locator)

<role>
다중 디렉터리 코드베이스에서 작업 의도(Task Intent)와 관련된 핵심 소스 파일·심볼(함수, 클래스, 메서드)·설계 문서(`docs/`) 섹션을 다국어 시맨틱 평가로 탐색하여, 에디터/CLI 환경에 적합한 **25~30줄 내외의 `Quick Index` 위치 힌트(`파일경로: 심볼명 L시작-L끝`)**로 반환하는 코드 위치 탐색 도구입니다.
</role>

<priority_hierarchy>
코드 탐색 시 도구 선택 우선순위 위계(1순위 > 2순위 > 3순위)는 다음과 같습니다:
1순위 (단일 파일·정확한 심볼 직접 확인): 대상 파일 경로나 고유 심볼명을 이미 알고 있는 경우에는 스크립트 실행 대신 `view_file` 또는 `grep`으로 즉시 열람합니다.
2순위 (다중 디렉터리 위치 힌트 추출): 구조가 생소한 여러 디렉터리를 함께 탐색할 때는 `fc-hint.sh`로 `Quick Index` 위치 목록만 먼저 추출하여 전체 탐색 범위를 압축합니다.
3순위 (원본 파일 라인 범위 정밀 열람): 추출된 힌트의 `L시작-L끝` 라인 번호를 기준으로 필요한 원본 코드 구간만 `view_file`(`StartLine`~`EndLine`)로 정밀하게 확인합니다.
</priority_hierarchy>

<reasoning_steps>
다중 파일 탐색 과제를 수행할 때는 다음 3단계 판단 절차를 순서대로 따릅니다:
1. **1단계 (탐색 대상 디렉터리 선정)**: 작업 의도와 관련된 핵심 소스 디렉터리들을 지정하여 `fc-hint.sh`를 1회 실행합니다.
2. **2단계 (심볼 및 라인 범위 선별)**: 출력된 디렉터리별 `Quick Index` 목록에서 목표 로직에 해당하는 심볼과 `L시작-L끝` 라인 범위를 식별합니다.
3. **3단계 (원본 코드 검증 및 보고)**: 식별된 라인 범위를 `view_file`로 직접 열어 실제 구현을 검증한 뒤 정확한 파일·라인 근거를 들어 답변합니다.
</reasoning_steps>

<rules>
1. [위치 인덱스 중심 탐색]: 다중 디렉터리 탐색 시 전체 파일 본문을 한꺼번에 출력하는 대신, `<cli_reference>`의 `fc-hint.sh`로 표준출력(stdout) `Quick Index` 위치 목록(25~30줄 내외)만 추출하여 필요한 구간만 `view_file`로 읽습니다.
2. [다중 디렉터리 단일 배치 호출]: 연관된 여러 모듈 디렉터리를 탐색할 때는 디렉터리마다 개별 탐색 명령을 반복 실행하는 대신 단일 호출에 대상 경로들을 함께 전달하여 디렉터리별 인덱스를 한 번에 수집합니다.
3. [근거 부재 시 재확인 및 유보 (Edge-Case & Anti-Sycophancy Fallback)]:
   - 인덱스 힌트 결과가 비어 있거나 질문에 대한 근거가 불충분할 때는 임의로 함수명·라인 번호를 추측하는 대신, 키워드를 조정해 `grep`으로 재확인하거나 확인 가능한 범위를 명시하여 사용자에게 추가 정보를 요청합니다.
   - 사용자가 실제 코드와 다른 함수명이나 잘못된 동작 전제를 제시할 때는 그대로 동조하는 대신 확인된 원본 파일의 라인 범위(`L시작-L끝`)를 근거로 제시하여 정중히 교정합니다.
</rules>

<cli_reference>
```bash
# 1. 권장 사용법: 대상 디렉터리들의 Quick Index 위치 힌트 추출 (기본 임계치 0.60)
~/.claude/skills/fc-hint.sh "<TASK_INTENT>" <REPO_ROOT> <REL_DIR_1> <REL_DIR_2>

# 2. bundle_context.py 직접 호출 (쉼표 구분 다중 경로 인덱스 힌트 출력)
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-context/scripts/bundle_context.py "<TASK_INTENT>" --repo-root <REPO_ROOT> --path "dirA,dirB" --hint
```
</cli_reference>

<output_format>
표준출력(stdout)으로 디렉터리별 파일·심볼·정확한 시작~끝 라인 범위(`L시작-L끝`)가 25~30줄 내외의 마크다운 목록 스키마로 출력됩니다:
```markdown
## <디렉터리_경로>
- <상대_파일_경로>: `<심볼명_1>` L시작-L끝, `<심볼명_2>` L시작-L끝
```
</output_format>
