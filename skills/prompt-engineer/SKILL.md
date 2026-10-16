---
name: prompt-engineer
description: 프롬프트 작성·수정 시 LLM이 자주 저지르는 12대 품질 결함(과적합, 리터럴 누출, 내적 모순, 우선순위 부재, 대안 없는 금지어, 구조 미비, 스키마 모호성, 환각 방어 누락, 무비판적 동조, 추상어 남발, 주의력 희석, 매체 부조화)을 TypeSafe Jev 단일 배치 호출(~250ms)로 감사하는 스킬.
---

# Prompt Engineer (Universal 12-Criteria LLM Prompt Auditor)

<role>
시스템 프롬프트, 스킬 지침(`SKILL.md`), 에이전트 규칙(`AGENTS.md`, `GEMINI.md`)을 작성하거나 수정할 때, Anthropic(Claude 4)·OpenAI(GPT-4.1)·Google(Gemini) 공식 가이드에 기반한 12대 직교(Orthogonal) 품질 기준(전 항목 `0.80` 이상 필수)과 개별 규칙의 과적합·대안 제시 여부를 단일 TypeSafe Jev 호출(~250ms)로 정량 감사(Audit)하는 도구입니다.
</role>

<priority_hierarchy>
프롬프트 감사 및 교정 시 다음 우선순위 위계(1순위 > 2순위 > 3순위)를 적용합니다:
1순위 (보편적 일반화 및 사실 안전성): 특정 실패 사례의 단어나 도메인 경로를 그대로 넣는 대신 보편적 행동 원칙으로 추상화하고, 근거 부재 시 추측을 유보(Abstention)하는 안전 규칙을 최우선으로 확보합니다.
2순위 (내적 일관성 및 긍정적 대체 행동): 규칙 간 모순을 해소하고 우선순위 위계를 명시하며, 단순 금지어 대신 구체적인 대체 행동(`X 상황에서는 Y 대신 Z를 수행`)을 제시합니다.
3순위 (구조적 격리 및 주의력 최적화): XML 태그와 마크다운 헤더로 지시문·컨텍스트·출력 스키마를 분리하고 핵심 제약을 명확한 단계로 배치합니다.
</priority_hierarchy>

<reasoning_steps>
프롬프트 품질 감사 및 개선 작업은 다음 3단계 절차로 수행합니다:
1. **1단계 (정량 감사 및 변화 추적 실행)**: 대상 프롬프트 파일을 수정한 직후 `audit_prompt.py --file <PATH>`(실패 사례 교정 시 `--failed-case` 병기)를 실행하여 12대 기준 점수와 내장 RAM 전후 비교(`RAM Auto-Diff`)를 확인합니다.
2. **2단계 (미달 차원 및 결함 규칙 교정)**: `0.80` 미만인 차원이나 `Flagged Rules`가 존재하면 출력된 `처방(fix_guide)`을 근거로 해당 문장을 보편적 원칙과 측정 가능한 긍정 대안 문장으로 재작성합니다.
3. **3단계 (전 항목 0.80 이상 최종 검증)**: 재감사를 실행하여 12개 전 차원 통과(`12/12`, 각 `>= 0.80`) 및 결함 규칙 `0건`(`status: PASS`) 달성을 확인한 뒤 결과를 보고합니다.
</reasoning_steps>

<rules>
1. [정량 품질 기준 검증 및 수용]: 지침 문서를 작성하거나 개정한 직후에는 `audit_prompt.py`를 실행하여 12개 품질 차원 모두 기준치(`0.80`) 이상을 충족하고 개별 결함 규칙이 `0건`(`status: PASS`)인지 확인합니다.
2. [보편적 행동 원칙 추상화]: 오류 사례나 평가 피드백을 반영할 때는 `--failed-case` 옵션으로 리터럴 유입 여부를 점검하고, 개별 사례를 상위 판단 기준과 긍정적 대체 행동(`X 조건에서는 Y 대신 Z를 수행`)으로 일반화합니다.
3. [불충분한 입력 및 전제 오류 대응 (Edge-Case & Anti-Sycophancy Fallback)]:
   - 입력 초안의 요구사항이나 적용 범위가 불분명할 때는 확인된 정적 지표(`static_signals`)와 미달 차원 점수를 먼저 제시하고, 사용자에게 우선순위와 출력 형식 기준을 질문하여 확정한 뒤 교정합니다.
   - 사용자가 보편성을 해치는 일회성 예외 조항 추가를 요청할 때는 감사 지표의 일반화 점수 변화와 재사용 가능한 원칙형 대안 문구를 함께 제시하여 객관적으로 안내합니다.
</rules>

<cli_reference>
```bash
# 1. 프롬프트 파일 12대 기준 감사 (전 항목 >= 0.80 통과 여부 및 RAM Auto-Diff 출력)
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/prompt-engineer/scripts/audit_prompt.py --file <PROMPT_FILE>

# 2. 실패 사례(Failed Case) 리터럴 누출 동시 감사
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/prompt-engineer/scripts/audit_prompt.py --file <PROMPT_FILE> --failed-case "<FAILED_EXAMPLE>"
```
</cli_reference>

<output_format>
감사 결과는 다음 타입과 경계 조건이 엄밀히 정의된 6개 필수 필드의 정형 리포트 및 JSON 스키마로 출력됩니다:
```json
{
  "status": "PASS | NEEDS_REVISION (string, 12/12 전 차원 >= 0.80 및 flagged_rules == 0일 때만 PASS)",
  "health_score": "0~100 (int, 12개 차원 평균 백분율 점수)",
  "passed_dimensions": ""<통과수>/12" (string)",
  "latency_ms": "float (실행 소요 밀리초)",
  "static_signals": {
    "line_count": "int",
    "extracted_rule_count": "int",
    "negative_marker_count": "int",
    "positive_fallback_marker_count": "int",
    "vague_expressions_found": "string[]",
    "structural_section_count": "int"
  },
  "dimensions": [
    {"id": "string", "score": "0.00~1.00 (float)", "passed": "boolean (score >= 0.80)", "fix_guide": "string"}
  ],
  "flagged_rules": [
    {"line": "int (1-indexed)", "rule": "string", "general_score": "float", "constructive_score": "float", "issues": "string[]"}
  ]
}
```
</output_format>
