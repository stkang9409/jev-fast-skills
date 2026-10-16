---
name: fast-memory
description: POSIX 공유 메모리(SharedMemory) 기반의 요청 단위(Request-Scoped) 초고속(~0.3ms) RAM 워킹 메모리 엔진. 디스크 임시 파일 생성 없이 중간 분석 데이터와 바이너리 버퍼를 RAM에서 공유하고 작업 종료 시 자동 회수합니다.
---

# Fast Memory (Request-Scoped POSIX SharedMemory Engine)

<role>
단일 사용자 요청을 처리하는 동안 발생하는 일시적 중간 데이터(JSON 분석 결과, AST 맵, DOM 스냅샷, 바이너리 버퍼)를 디스크 임시 파일 대신 RAM 공유 메모리(16MB 세그먼트: 1MB 인덱스 + 15MB 데이터 버퍼)에 저장·공유하는 워킹 메모리 도구입니다.
</role>

<priority_hierarchy>
데이터 저장 수단 선택 시 다음 우선순위를 따릅니다:
1순위 (영구 산출물 보존): 사용자가 명시적으로 요청한 프로젝트 코드 파일이나 영구 보고서 아티팩트(`brain/<id>/*.md`)는 `write_to_file` / `replace_file_content`로 디스크에 기록합니다.
2순위 (일시적 중간 상태의 RAM 격리): 작업 도중 잠깐 쓰고 버릴 중간 계산값·임시 메모는 디스크(`scratch/`, `/tmp/`)에 파일을 생성하는 대신 반드시 `fast-memory` RAM에 저장하여 작업 트리 오염을 방지합니다.
3순위 (용량 한계 준수): 단일 요청의 총 RAM 슬라이스 사용량은 15MB 데이터 버퍼 한도를 초과하지 않도록 유지합니다.
</priority_hierarchy>

<rules>
1. [키 명명 규칙]: 다중 작업 간 충돌을 방지하기 위해 키 이름은 `도메인:식별자`(예: `ast:summary`, `browser:dom_snapshot`) 형식으로 지정합니다.
2. [원자적 읽기·쓰기]: JSON 또는 텍스트 데이터는 `fast-memory set <key> <value>`(또는 표준 입력 파이프)로 저장하고, `fast-memory get <key> [--json]`으로 조회합니다.
3. [예외 및 엣지 케이스 처리 (Fallback)]:
   - 조회한 키가 존재하지 않아 빈 응답이 반환되면 이전 상태를 추측하지 않고 원본 데이터를 재추출합니다.
   - 15MB를 초과하는 초대용량 바이너리 파일은 RAM 슬라이스 할당 실패(`MemoryError`)가 발생하므로, 이 경우에만 예외적으로 아티팩트 `scratch/` 경로를 사용합니다.
   - 작업 완료 시 `Stop` 훅(`hook_teardown.py`)이 자동으로 메모리를 회수하므로 수동 삭제 명령을 별도 호출할 필요가 없습니다.
</rules>

<cli_reference>
```bash
# 1. JSON/텍스트 저장 및 조회
fast-memory set analysis:step1 '{"files": 4, "status": "ok"}'
fast-memory get analysis:step1 --json

# 2. 파이프 기반 대용량 텍스트 저장
cat payload.json | fast-memory set api:dump

# 3. 바이너리 버퍼 입출력 (Zero-Copy)
fast-memory set-bytes browser:shot /path/to/shot.png
fast-memory get-bytes browser:shot /path/to/restored.png
```
</cli_reference>

<output_format>
- `fast-memory get <key> --json`: 저장된 JSON 객체를 표준 출력(stdout)으로 직렬화하여 반환합니다.
- `fast-memory list`: 현재 활성 키 목록과 각 슬라이스의 바이트 크기(`offset`, `length`)를 출력합니다.
</output_format>
