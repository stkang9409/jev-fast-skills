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
1. [12대 전 기준 0.80 이상 엄격 검증]: 프롬프트를 신설·수정할 때는 주관적 인상에 의존하는 대신 `audit_prompt.py`를 실행하여 12개 품질 차원 모두 `0.80` 이상(`12/12 PASS`)이고 적발된 개별 규칙이 `0건`인지 검증합니다.
2. [실패 사례 리터럴 누출 차단]: 특정 오류 사례나 벤치마크 실패를 계기로 지침을 보완할 때는 오류 문구를 그대로 복사하는 대신 `--failed-case "<실패_사례>"` 옵션을 함께 실행하여 실패 예시의 고유 문구나 일회성 조건문이 규칙에 그대로 유입되는 현상을 방지합니다.
3. [예외 입력 및 사실 오류 전제 대응 (Edge-Case & Anti-Sycophancy Fallback)]:
   - 구조화되지 않은 평문 초안이나 불충분한 요구사항이 주어졌을 때는 임의로 규칙을 추측해 채우는 대신 `static_signals`와 미달 차원 지표를 먼저 제시하고 사용자에게 핵심 제약 조건을 확인합니다.
   - 사용자가 특정 테스트 케이스 정답만 통과시키는 1회성 하드코딩 규칙 추가를 요청할 때는 그대로 동조하는 대신 과적합 감사 점수와 일반화 대안 문구를 함께 제시하여 객관적으로 안내합니다.
</rules>

<audit_criteria_12>
1. `overfitting_and_bandaids` (`>= 0.80`): 1회성 실패 사례 땜질 대신 범용 행동 원칙으로 작성되었는가?
2. `literal_leakage_and_fewshot_bias` (`>= 0.80`): 특정 테스트 케이스 고유명사·숫자·문구 누출이 없는가?
3. `internal_contradiction` (`>= 0.80`): 규칙 간 양립 불가능한 모순 지시가 없이 일관적인가?
4. `priority_hierarchy` (`>= 0.80`): 규칙 경합 시 적용할 명시적 우선순위(`1순위 > 2순위 > 3순위`)가 정의되었는가?
5. `positive_fallback_framing` (`>= 0.80`): 단순 금지어 나열 대신 긍정적 대체 행동(`Y 대신 Z를 수행`)을 제시했는가?
6. `structural_delimiter_isolation` (`>= 0.80`): XML 태그와 마크다운 헤더로 역할·절차·규칙·출력을 격리했는가?
7. `output_schema_rigor` (`>= 0.80`): 출력 구조, 길이 상한, 필수 항목 및 스키마가 검증 가능하게 정의되었는가?
8. `edge_case_and_abstention` (`>= 0.80`): 근거 부재나 모호한 입력 시 추측 대신 유보·확인하는 예외 수칙이 있는가?
9. `anti_sycophancy_guardrail` (`>= 0.80`): 사용자의 잘못된 전제에 무비판적으로 동조하지 않고 근거로 교정하는가?
10. `concrete_testable_language` (`>= 0.80`): 모호한 부사 대신 관찰·측정 가능한 수치·단계 기준을 사용했는가?
11. `attention_budget_and_persona_diet` (`>= 0.80`): 불필요한 배경 설정 없이 핵심 규칙에 주의력을 집중시켰는가?
12. `modality_and_reasoning_fit` (`>= 0.80`): 실행 매체의 호흡에 맞는 단계별 추론 절차(`<reasoning_steps>`)를 명시했는가?
</audit_criteria_12>

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
    "companion_code_overfit_vars": "string[]"
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
