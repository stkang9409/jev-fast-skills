---
name: prompt-engineer
description: 프롬프트 작성·수정 시 LLM이 자주 저지르는 12대 실수(사후 과적합, 리터럴 누출, 내적 모순, 우선순위 위계 부재, 대안 없는 부정어, 구조적 경계 미비, 출력 스키마 모호성, 예외/환각 방어 누락, 무조건적 동조, 모호한 추상어, 주의력 희석, 매체 부조화)를 TypeSafe Jev 단일 배치 호출(~250ms)로 감사하는 스킬.
---

# Prompt Engineer (Universal 12-Criteria LLM Prompt Auditor)

<role>
시스템 프롬프트, 스킬 지침(`SKILL.md`), 에이전트 규칙(`GEMINI.md`, `AGENTS.md`)을 작성하거나 수정할 때, Anthropic(Claude 4)·OpenAI(GPT-4.1)·Google(Gemini) 공식 가이드에 기반한 12대 직교(Orthogonal) 품질 기준과 개별 규칙(Line-level) 과적합 여부를 단일 TypeSafe Jev 호출(~250ms)로 감사(Audit)하는 도구입니다.
</role>

<priority_hierarchy>
프롬프트 감사 및 교정 시 다음 우선순위를 적용합니다:
1순위 (일반화 및 사실 안전성): 특정 실패 사례의 문구를 하드코딩하는 대신 보편적 행동 원칙으로 추상화하고, 근거 없는 추측을 막는 유보(Abstention) 규칙을 최우선으로 확보합니다.
2순위 (내적 일관성 및 긍정 대안): 규칙 간 충돌을 제거하고 우선순위 위계를 명시하며, 단순 금지어 대신 구체적인 대체 행동(Positive Fallback)을 제시합니다.
3순위 (간결성 및 구조화): XML 태그나 마크다운 섹션으로 경계를 분리하고 불필요한 배경 설정을 줄여 핵심 제약의 주의력을 보존합니다.
</priority_hierarchy>

<rules>
1. [프롬프트 감사 실행]: 프롬프트 파일을 신설·수정한 직후 `audit_prompt.py --file <PATH>`(또는 `--text "<PROMPT>"`)를 실행하여 12대 품질 기준 통과 여부를 검증합니다.
2. [실패 사례 리터럴 누출 점검]: 특정 실패 대화나 오답 사례를 계기로 프롬프트를 수정할 때는 반드시 `--failed-case "<실패 예시>"` 인자를 함께 전달하여 테스트 케이스 문구가 규칙에 그대로 유입되지 않았는지 확인합니다.
3. [교정 절차]: 감사 결과 `NEEDS_REVISION`이거나 `Flagged Rules`가 검출되면, 출력된 `처방(fix_guide)`에 따라 해당 라인을 일반화·구체화한 뒤 재검사하여 `PASS`(통과 기준 10/12 이상 및 결함 규칙 0건)를 달성합니다.
4. [예외 및 엣지 케이스 처리 (Fallback)]:
   - 불릿 규칙이 없는 평문 프롬프트는 감사를 생략하는 대신 전역 12대 차원 점수와 정적 신호(`static_signals`)를 기준으로 XML 구조화 및 수치 기준을 보강합니다.
   - 일시적 통신 장애나 모호한 입력이 발생하면 임의로 판정 결과를 추측하는 대신 자동 재시도 후 정적 분석 지표와 확인 필요 사항을 명확히 보고합니다.
</rules>

<audit_criteria_12>
1. `overfitting_and_bandaids`: 1회성 실패 사례 표적 기소 대신 보편적 행동 원칙으로 작성되었는가?
2. `literal_leakage_and_fewshot_bias`: 테스트 케이스의 고유명사·숫자·문구 하드코딩 및 퓨샷 편향이 없는가?
3. `internal_contradiction`: 양립 불가능한 모순 지시가 공존하지 않는가?
4. `priority_hierarchy`: 규칙 경합 시 적용할 명시적 우선순위(`1순위 > 2순위`)가 정의되었는가?
5. `positive_fallback_framing`: 단순 금지어 나열 대신 긍정적 대체 행동(`X 대신 Y를 수행`)을 제시했는가?
6. `structural_delimiter_isolation`: XML 태그나 마크다운 헤더로 역할·규칙·컨텍스트·출력을 격리했는가?
7. `output_schema_rigor`: 출력 구조, 길이 상한, 필수 항목 또는 스키마가 검증 가능하게 정의되었는가?
8. `edge_case_and_abstention`: 입력 모호성이나 근거 부재 시 추측하지 않고 유보·확인하는 예외 수칙이 있는가?
9. `anti_sycophancy_guardrail`: 사용자의 사실 오류 전제에 무비판적으로 동조하지 않는 객관성 가드레일이 있는가?
10. `concrete_testable_language`: 주관적 수식어에 의존하는 대신 관찰·측정 가능한 행동 기준(문장 수, 글자 수, 단계)을 사용했는가?
11. `attention_budget_and_persona_diet`: 불필요한 연극적 배경을 덜어내고 핵심 규칙을 상단/하단에 구조화했는가?
12. `modality_and_reasoning_fit`: 타겟 채널의 호흡에 맞고 복합 판단 시 필요한 중간 판단 단계를 명시했는가?
</audit_criteria_12>

<cli_reference>
```bash
# 1. 프롬프트 파일 감사
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/prompt-engineer/scripts/audit_prompt.py --file <PROMPT_FILE>

# 2. 실패 사례(Failed Case) 리터럴 누출 동시 감사
$HOME/.gemini/config/plugins/jev-fast-skills/.venv/bin/python $HOME/.gemini/config/plugins/jev-fast-skills/skills/prompt-engineer/scripts/audit_prompt.py --file <PROMPT_FILE> --failed-case "실패했던 대화 예시"
```
</cli_reference>

<output_format>
감사 결과는 다음 항목을 포함하여 출력됩니다 (`--json` 사용 시 동일 구조의 JSON 반환):
- `status`: `PASS` (통과 차원 10/12 이상 및 결함 규칙 0개) 또는 `NEEDS_REVISION`
- `health_score`: 12대 차원 평균 점수 (`0~100`) 및 소요 시간(`latency_ms`)
- `static_signals`: 라인 수, 규칙 수, 부정어/긍정 대안 수, 섹션 태그 수, 모호 표현 목록
- `dimensions`: 12대 기준별 `PASS`/`FAIL`, 신뢰도 점수(`0.00~1.00`), 미통과 시 구체적 교정 처방(`fix_guide`)
- `flagged_rules`: 과적합 또는 대안 부재로 적발된 개별 규칙의 정확한 라인 번호와 사유
</output_format>
