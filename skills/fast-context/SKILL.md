---
name: fast-context
description: TypeSafe Jev 기반 초고속(sub-2s) Wide-Recall 인덱스 힌트(Quick Index) 추출기. 번들 본문 없이 대상 디렉터리와 문서(docs/)의 관련 파일·함수·SQL·IPC 위치(L시작-L끝)를 30줄 안쪽 힌트로 즉시 출력합니다.
---

# Fast Context (Wide-Recall Quick Index Hint Locator)

<role>
다중 파일 코드베이스에서 작업 의도(Task Intent)와 관련된 모든 파일·함수·클래스 메서드·SQL 쿼리·Electron IPC 채널·정본 문서(`docs/`) 섹션을 누락 없이 탐색하여, 무거운 코드 본문 대신 **30줄 안쪽의 `Quick Index` 위치 힌트(`파일경로: 심볼명 L시작-L끝`)**로 즉시 반환하는 초고속 인덱스 탐색 도구입니다.
</role>

<priority_hierarchy>
코드 탐색 시 도구 선택 우선순위는 다음과 같습니다:
1순위 (단일 파일·정확한 심볼 확인): 이미 정확한 파일 경로나 고유 심볼명을 알고 있다면 `view_file` 또는 `grep`/`rg`로 즉시 확인합니다.
2순위 (초기 다중 디렉터리 인덱스 힌트 추출): 탐색 과제를 시작할 때 `fc-hint.sh`로 대상 디렉터리 3~4곳의 `Quick Index`만 뽑아(기준값 `0.60`, 번들 본문 미사용) 30줄 안쪽 위치 힌트를 확보합니다.
3순위 (원본 파일 라인 범위 직접 열람): 출력된 힌트의 정확한 `L시작-L끝` 범위를 보고 필요한 원본 파일만 `view_file`(`StartLine`~`EndLine`)로 즉시 열람합니다.
</priority_hierarchy>

<rules>
1. [인덱스 힌트 전용 실행 (번들 본문 미사용)]: 탐색 과제를 시작할 때 `$HOME/.claude/skills/fc-hint.sh "<TASK_INTENT>" <REPO_ROOT> <DIR1> <DIR2> ...` (또는 `bundle_context.py "<TASK_INTENT>" --path "dir1,dir2" --hint`)를 실행하여 표준출력(stdout)으로 30줄 안쪽의 `Quick Index`만 바로 받아 사용하며, 토큰과 시간을 낭비하는 번들 코드 본문은 읽지 않습니다.
2. [Wide-Recall 다중 경로 및 자동 문서 탐색]: 대상 디렉터리 3~4곳(예: `electron/claims packages/hira-claim-core/src components/claims electron`)을 함께 지정하면, 하위 디렉터리 중복이 자동 제거되고 저장소의 `docs/features`·`docs/manual` 내 관련 설계 문서까지 한 번에 인덱싱됩니다.
3. [예외 및 엣지 케이스 처리 (Fallback)]:
   - 기본적으로 테스트·픽스처(`.test.`, `fixtures/`, `.json`)는 자동 제외되며, 테스트 코드를 찾아야 할 때만 `--include-tests` 플래그를 추가합니다.
   - 힌트 출력 후 세부 상수나 정규식 리터럴을 추가로 확인해야 할 때는 확보된 파일 범위를 대상으로 `grep`을 보조로 사용합니다.
</rules>

<cli_reference>
```bash
# 1. 권장 사용법: fc-hint.sh로 대상 디렉터리 3~4곳의 Quick Index만 30줄 안쪽 힌트로 추출 (기본 임계치 0.60)
~/.claude/skills/fc-hint.sh "<TASK_INTENT>" <REPO_ROOT> <REL_DIR_1> <REL_DIR_2> <REL_DIR_3>

# 2. bundle_context.py 직접 호출 (쉼표 구분 다중 경로 인덱스 힌트 출력)
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-context/scripts/bundle_context.py "<TASK_INTENT>" --repo-root <REPO_ROOT> --path "dirA,dirB,dirC" --hint
```
</cli_reference>

<output_format>
표준출력(stdout)으로 코드 본문 없이 디렉터리별 파일·심볼·정확한 시작~끝 라인 범위(`L시작-L끝`)만 25~30줄 내외의 마크다운 목록 스키마로 출력됩니다:
```markdown
## <디렉터리_경로>
- <상대_파일_경로>: `<심볼명_1>` L시작-L끝, `<심볼명_2>` L시작-L끝
```
</output_format>
