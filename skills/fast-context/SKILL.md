---
name: fast-context
description: TypeSafe Jev 기반 초고속(sub-2s) 3단계(IDF 사전 필터 -> 파일 선별 -> 심볼 선별) 시맨틱 컨텍스트 번들러. 작업 트리를 오염시키지 않고 /tmp/fast-context/bundle.md에 700줄 이내의 핵심 코드·문서 번들을 생성합니다.
---

# Fast Context (Zero-Worktree-Pollution 3-Stage Semantic Bundler)

<role>
다중 파일 코드베이스에서 작업 의도(Task Intent)와 관련된 아키텍처 문서 섹션 및 핵심 심볼(Python AST, TS/JS/CJS 함수·타입, Electron IPC/Preload 채널)만 선별하여, 저장소 작업 트리를 더럽히지 않고 `/tmp/fast-context/bundle.md`(700줄 이내)로 조립하는 코드 탐색 도구입니다.
</role>

<priority_hierarchy>
코드 탐색 시 도구 선택 우선순위는 다음과 같습니다:
1순위 (아키텍처 문서·정확한 심볼 확인): 저장소에 `docs/`, `README.md`, `AGENTS.md` 등 설계 지도 문서가 있거나 이미 정확한 파일 경로·심볼명을 알고 있다면, 번들러를 실행하는 대신 해당 문서를 `view_file`로 읽거나 `grep`/`rg`를 먼저 사용합니다.
2순위 (하위 경로 타겟팅 다중 파일 탐색): 위치가 불분명한 여러 파일의 구조와 심볼을 한 번에 파악해야 할 때는 `--path`를 관련 하위 디렉토리(쉼표 구분 다중 경로 지원: `--path "dirA,dirB"`)로 좁혀 `fast-context`를 1회 실행합니다.
3순위 (원본 라인 점프): 생성된 `/tmp/fast-context/bundle.md`의 `Quick Index` 링크(`file:///...#L시작-L끝`)를 통해 필요한 원본 파일의 정확한 라인 범위만 추가 열람합니다.
</priority_hierarchy>

<rules>
1. [작업 트리 무오염 실행]: `bundle_context.py "<TASK_INTENT>" --path "<DIR1,DIR2>"`를 실행하면 대상 저장소 내부가 아닌 `/tmp/fast-context/bundle.md`에 번들이 저장되어 Git Worktree를 오염시키지 않습니다. 생성 직후 `view_file`로 `/tmp/fast-context/bundle.md`를 열람합니다.
2. [대규모 모노레포 하위 경로 지정]: 파일이 수백 개 이상인 저장소에서는 루트(`.`) 전체를 지정하는 대신, 탐색 대상 패키지나 모듈 경로(예: `--path "electron/claims,packages/hira-claim-core"`)를 지정하여 정밀도와 속도를 높입니다.
3. [예외 및 엣지 케이스 처리 (Fallback)]:
   - 기본적으로 테스트·픽스처(`.test.`, `fixtures/`, `.json`)는 노이즈 방지를 위해 자동 제외되며, 테스트 코드 자체를 분석해야 할 때만 `--include-tests` 플래그를 추가합니다.
   - 매칭된 심볼이 부족할 경우 임의로 코드를 추측하는 대신 키워드를 구체화하거나 `grep`으로 정확한 식별자를 확인합니다.
</rules>

<cli_reference>
```bash
# 1. 단일 또는 다중 하위 디렉토리 지정 실행 (/tmp/fast-context/bundle.md 생성)
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-context/scripts/bundle_context.py "<TASK_INTENT>" --path "packages/core,electron/claims"

# 2. 테스트 파일까지 포함하여 탐색할 때
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/fast-context/scripts/bundle_context.py "<TASK_INTENT>" --path <TARGET_DIR> --include-tests
```
</cli_reference>

<output_format>
생성되는 `/tmp/fast-context/bundle.md`는 단일 `view_file` 호출(800줄 한도) 내에 들어오도록 최대 10개 파일·18개 핵심 블록(약 300~650줄)으로 자동 예산 관리되며 다음 스키마를 따릅니다:
1. `## 📑 Quick Index`: 선별된 파일 경로(클릭 링크)와 내부 심볼/IPC/문서 섹션의 시작~끝 라인 번호(`L시작-L끝`).
2. `## 💻 Extracted Code Context`: 4자리 라인 번호가 포함된 코드 및 문서 섹션 본문.
</output_format>
