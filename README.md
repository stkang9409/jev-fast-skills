# ⚡ TypeSafe Jev Fast Skills Plugin (`jev-fast-skills`)

**Google Antigravity**와 **Anthropic Claude Code** 양쪽에서 즉시 사용할 수 있는 **TypeSafe Jev 기반 4대 초고속 에이전트 스킬 플러그인 번들**입니다.

---

## 📦 포함된 구성 요소 (4 Skills + 1 Rule + 1 Stop Hook)

| 구분 | 이름 | 속도 | 역할 및 핵심 효과 |
| :--- | :--- | :---: | :--- |
| **Skill 1** | `fast-context` | **~1.3s** | 2단계(파일 → 심볼) 시맨틱 컨텍스트 번들러. 다중 파일 탐색 시 도구 호출 -76.5%, 누적 토큰 -76.1%, 소요 시간 -39.8% 단축 |
| **Skill 2** | `fast-memory` | **~0.3ms** | POSIX `SharedMemory` + `fcntl.flock` 기반 16MB RAM 워킹 메모리. 디스크 임시 파일(`scratch/`, `/tmp/`) 생성 0건 보장 |
| **Skill 3** | `fast-browser` | **~200ms/step** | TypeSafe Jev 초고속 브라우저 탐색·슬롯 입력 및 자유 서술형 텍스트 핸드오프(`NEED_TEXT` → `--resume`) |
| **Skill 4** | `prompt-engineer` | **~220ms** | Anthropic/OpenAI/Google 가이드 기반 **12대 직교 프롬프트 품질 기준 + 라인별 과적합 감사기** |
| **Rule** | `rules/AGENTS.md` / `CLAUDE.md` | **0ms (Zero-Turn)** | 별도 라우터 호출 없이 Turn 1에 즉시 스킬을 실행하는 **Zero-Turn Fast Skill Policy** |
| **Hook** | `hooks.json` (`Stop`) | **~1.0ms** | 작업 완료 시 `fast-memory` POSIX 공유 메모리를 자동 회수하고 즉시 종료 |

---

## 🚀 다른 컴퓨터(새 환경)에서 10초 설치 방법

### 1. 저장소 클론 및 원클릭 설치 (`install.sh`)
어떤 Mac / Linux 컴퓨터에서든 아래 명령 한 줄이면 **Antigravity 플러그인**과 **Claude Code 스킬/룰**이 동시에 설치됩니다 (`$HOME` 상대 경로를 사용하여 사용자 계정명이 달라도 100% 호환됩니다):

```bash
git clone https://github.com/stkang9409/jev-fast-skills.git ~/.gemini/config/plugins/jev-fast-skills
TYPESAFE_API_KEY="your_typesafe_api_key" ~/.gemini/config/plugins/jev-fast-skills/install.sh
```

`install.sh`가 자동으로 수행하는 작업:
1. `~/.gemini/config/plugins/jev-fast-skills/.venv` 파이썬 가상환경 생성 및 의존성(`httpx[http2]`, `python-dotenv`, `playwright`) 자동 설치
2. **Antigravity 등록**: `~/.gemini/config/plugins/jev-fast-skills` (`plugin.json`, `rules/AGENTS.md`, `hooks.json`, `skills/*`)
3. **Claude Code 등록**: `~/.claude/skills/{fast-context,fast-memory,fast-browser,prompt-engineer}`, `~/.claude/plugins/jev-fast-skills` (`.claude-plugin/plugin.json`), `~/.claude/CLAUDE.md`
4. `~/.gemini/.env`에 `TYPESAFE_API_KEY` 설정

### 2. Claude Code 전용 네이티브 플러그인 명령어로 설치할 경우
Claude Code CLI 내부에서도 `.claude-plugin/marketplace.json`을 통해 직접 마켓플레이스/플러그인으로 로드할 수 있습니다:
```bash
# 로컬 경로 또는 GitHub 저장소 등록
/plugin marketplace add ~/.gemini/config/plugins/jev-fast-skills
/plugin install jev-fast-skills
```

---

## 🛠️ 디렉토리 구조 (Dual-Manifest Architecture)

```text
jev-fast-skills/
├── plugin.json                  # Antigravity 공식 플러그인 매니페스트
├── .claude-plugin/
│   ├── plugin.json              # Claude Code 공식 플러그인 매니페스트
│   └── marketplace.json         # Claude Code 마켓플레이스 카탈로그
├── install.sh                   # 크로스 머신 원클릭 설치 스크립트 (Mac/Linux)
├── requirements.txt             # Python 의존성
├── pyproject.toml               # 패키지 메타데이터
├── hooks.json                   # Stop 훅 (fast-memory RAM 자동 회수)
├── rules/
│   └── AGENTS.md                # Antigravity 상시 활성화 Zero-Turn 규칙
├── CLAUDE.md                    # Claude Code용 Zero-Turn 규칙
├── assets/
│   └── logo.svg                 # 플러그인 카드 아이콘
└── skills/
    ├── fast-context/            # 1.3s 시맨틱 코드 번들러
    ├── fast-memory/             # 0.3ms POSIX 공유 메모리
    ├── fast-browser/            # 200ms 브라우저 자동화 & 핸드오프
    └── prompt-engineer/         # 220ms 12대 기준 프롬프트 감사기
```
