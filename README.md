# ⚡ TypeSafe Jev Fast Skills Plugin (`jev-fast-skills`)

**Google Antigravity**와 **Anthropic Claude Code** 양쪽에서 즉시 사용할 수 있는 **TypeSafe Jev 기반 2대 초고속 에이전트 스킬 + 내장 POSIX 공유 메모리(RAM) 엔진 플러그인**입니다.

---

## 📦 아키텍처 및 구성 요소 (2 Skills + Internal POSIX SharedMemory Layer)

`POSIX SharedMemory + fcntl.flock`을 **플러그인 내부 공용 RAM 레이어(`core/shm.py`)**로 완전 내장하여, 스킬 실행 시 자동으로 0.06ms RAM 세션 공유·전후 비교가 작동합니다.

| 구분 | 이름 | 속도 | 역할 및 내장 RAM 연동 효과 |
| :--- | :--- | :---: | :--- |
| **Skill 1** | `fast-browser` | **~200ms/step** | TypeSafe Jev 초고속 브라우저 탐색·슬롯 입력 및 자유 서술형 텍스트 핸드오프(`NEED_TEXT` → `--resume`).<br>**[내장 RAM 연동]**: 핸드오프 세션을 디스크 파일(`/tmp`) 대신 POSIX 공유 메모리에 직접 저장·복원하며, 로그인 장벽 감지 시 `AUTH_REQUIRED`를 즉시 반환 |
| **Skill 2** | `prompt-engineer` | **~220ms** | Anthropic/OpenAI/Google 가이드 기반 **12대 직교 프롬프트 품질 기준 + 라인별 과적합 감사기**.<br>**[내장 RAM 연동]**: 직전 감사 결과를 RAM에 자동 기억하여 프롬프트 수정 후 재실행 시 **`[RAM Auto-Diff]` (점수 증감 및 해결된 결함 목록)** 자동 출력 |
| **Core** | `core/shm.py` & `hooks.json` | **~0.06ms** | 16MB POSIX SharedMemory (In-place 슬라이스 압축 지원) + 작업 종료(`Stop`) 시 RAM 100% 자동 회수 |
| **Rule** | `rules/AGENTS.md` / `CLAUDE.md` | **0ms (Zero-Turn)** | 별도 라우터 호출 없이 Turn 1에 즉시 내장 도구 및 전용 스킬을 실행하는 **Zero-Turn Fast Skill Policy** |

---

## 🚀 다른 컴퓨터(새 환경)에서 10초 설치 방법

### 1. 저장소 클론 및 원클릭 설치 (`install.sh`)
어떤 Mac / Linux 컴퓨터에서든 아래 명령 두 줄이면 **Antigravity 플러그인**과 **Claude Code 스킬/룰**이 동시에 설치됩니다:

```bash
git clone https://github.com/stkang9409/jev-fast-skills.git ~/.gemini/config/plugins/jev-fast-skills
TYPESAFE_API_KEY="your_typesafe_api_key" ~/.gemini/config/plugins/jev-fast-skills/install.sh
```

### 2. Claude Code 전용 네이티브 플러그인 명령어로 설치할 경우
```bash
/plugin marketplace add stkang9409/jev-fast-skills
/plugin install jev-fast-skills
```

---

## 🛠️ 디렉토리 구조

```text
jev-fast-skills/
├── plugin.json                  # Antigravity 공식 플러그인 매니페스트
├── .claude-plugin/
│   ├── plugin.json              # Claude Code 공식 플러그인 매니페스트
│   └── marketplace.json         # Claude Code 마켓플레이스 카탈로그
├── install.sh                   # 크로스 머신 원클릭 설치 스크립트 (Mac/Linux)
├── hooks.json                   # Stop 훅 (core/hook_teardown.py 호출로 RAM 자동 회수)
├── core/
│   ├── shm.py                   # 내장 POSIX SharedMemory 엔진 (세션 / 감사 Diff 공유)
│   └── hook_teardown.py         # 작업 종료 시 공유 메모리 세그먼트 정리
├── rules/
│   └── AGENTS.md                # Antigravity 상시 활성화 Zero-Turn 규칙
├── CLAUDE.md                    # Claude Code용 Zero-Turn 규칙 (rules/AGENTS.md 심볼릭 링크)
└── skills/
    ├── fast-browser/            # 200ms 브라우저 자동화 & RAM 세션 핸드오프
    └── prompt-engineer/         # 220ms 12대 기준 프롬프트 감사기 (RAM Auto-Diff 내장)
```
