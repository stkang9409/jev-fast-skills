import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import hashlib
import sys
import httpx
from dotenv import load_dotenv

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
try:
    from core.shm import RequestMemory
    _SHM = RequestMemory()
except Exception:
    _SHM = None

load_dotenv(dotenv_path=Path(__file__).resolve().parents[3] / ".env")
load_dotenv(dotenv_path=Path.home() / ".gemini" / ".env")
load_dotenv()

CLIENT = httpx.Client(
    http2=True,
    timeout=10.0,
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)

# 12 Orthogonal Prompt Quality & Anti-Pattern Criteria
# Synthesized from Anthropic (Claude 4), OpenAI (GPT-4.1), Google (Gemini), and academic LLM failure taxonomies.
CRITERIA_SPECS: List[Dict[str, str]] = [
    {
        "id": "overfitting_and_bandaids",
        "category": "1. 일반화 & 과적합 방지 (Generalizability)",
        "name": "사후 표적 기소 / 1회성 땜질 방지 (No Post-hoc Overfitting)",
        "source": "Anthropic & OpenAI Eval Best Practices",
        "question": (
            "Are all rules in the [target_prompt] written as universal, generalizable operational principles "
            "rather than narrow post-hoc band-aids targeting specific benchmark repositories, domain-specific "
            "business terms, one-off words/prices, or reactive excuses from a single failed test run? "
            "(Yes = universal & generalizable principles, No = contains narrow 1-off band-aids)"
        ),
        "fix_guide": (
            "특정 실패 사례에서 나온 지엽적 상황·단어·숫자를 콕 집어 막는 조건문('~라고 물으면 ~하지 마라')을 제거하고, "
            "근본 원인을 해결하는 상위 행동 원칙(Why & General Principle)으로 추상화하세요."
        ),
    },
    {
        "id": "literal_leakage_and_fewshot_bias",
        "category": "1. 일반화 & 과적합 방지 (Generalizability)",
        "name": "테스트 리터럴 누출 & 퓨샷 편향 방지 (No Literal Leak / Few-Shot Bias)",
        "source": "Prompt Overfitting & Few-Shot Contamination Literature",
        "question": (
            "Are the rules in [target_prompt] abstracted into general categories rather than hardcoding specific "
            "test-case numbers, specific prices, domain-specific business examples, or verbatim user quotes leaked "
            "from failed test cases? "
            "(Note: standard CLI invocation syntax and fallback templates are allowed. "
            "Yes = properly abstracted, No = leaks test-case literals)"
        ),
        "fix_guide": (
            "규칙 본문에 하드코딩된 특정 테스트 문구·금액·고유명사를 일반 범주(예: '구체적 비용 언급', '민감 정보')로 치환하고, "
            "예시(Few-shot)가 본문 규칙과 모순되지 않는지 점검하세요."
        ),
    },
    {
        "id": "internal_contradiction",
        "category": "2. 일관성 & 우선순위 위계 (Coherence & Hierarchy)",
        "name": "규칙 간 내적 모순 부재 (Zero Internal Contradiction)",
        "source": "OpenAI GPT-4.1 & Anthropic Claude 4 Conflict Guides",
        "question": (
            "Are all instructions in the [target_prompt] mutually consistent without any contradictory demands "
            "(such as demanding a 1-sentence short reply while simultaneously requiring exhaustive detailed explanations, "
            "or forbidding questions while requiring follow-up questions)? (Yes = 100% consistent, No = has contradictions)"
        ),
        "fix_guide": (
            "서로 충돌하는 두 지시문(예: '질문 금지' vs '역질문 필수', '한 문장 단답' vs '모든 과정 상세 설명')을 찾아 통합하거나, "
            "상황별 분기 조건(When X -> A, When Y -> B)을 명시하세요."
        ),
    },
    {
        "id": "priority_hierarchy",
        "category": "2. 일관성 & 우선순위 위계 (Coherence & Hierarchy)",
        "name": "충돌 해결 우선순위 위계 명시 (Explicit Priority Hierarchy)",
        "source": "Instruction Hierarchy & Prompt Debt Management",
        "question": (
            "Does the [target_prompt] explicitly define a priority hierarchy or tie-breaking precedence rule "
            "(e.g., 'Safety > Accuracy > Brevity' or 'Rule A supersedes Rule B when they conflict') so the model "
            "knows which constraint wins under tension? (Yes = explicit priority hierarchy defined, No = no hierarchy)"
        ),
        "fix_guide": (
            "규칙들이 경합할 때 어느 가치가 우선하는지 명시적 우선순위 절(예: '최우선 원칙: 안전성 > 사실 정확성 > 간결성 순으로 적용하며, "
            "하위 규칙이 충돌할 경우 상위 원칙을 따른다')을 추가하세요."
        ),
    },
    {
        "id": "positive_fallback_framing",
        "category": "3. 부정 제약 & 대안 행동 (Constraint Framing)",
        "name": "부정어 도배 방지 & 긍정 대안 제시 (Positive Fallback over Negative Stacking)",
        "source": "Anthropic Claude 4 & Ironic Process ('Pink Elephant') Effect",
        "question": (
            "Are the instructions in [target_prompt] phrased affirmatively with concrete positive actions "
            "(and whenever a restriction is stated, is it paired with a clear '대신 / Instead, do X' fallback) "
            "rather than stacking bare negative prohibitions ('절대 ~하지 마', '금지')? "
            "(Yes = affirmative & constructive with fallbacks, No = bare negative prohibitions)"
        ),
        "fix_guide": (
            "'절대 ~하지 마라/금지'만 나열하면 리터럴 추종 모델(Claude 4/GPT-4.1)이 과도하게 얼어붙거나 금지어에 주의를 빼앗깁니다. "
            "모든 금지 규칙을 'X 대신 Y 방식으로 응답하라'는 긍정적 대체 행동(Positive Fallback)으로 재작성하세요."
        ),
    },
    {
        "id": "structural_delimiter_isolation",
        "category": "4. 구조적 경계 & 출력 스키마 (Structure & Schema)",
        "name": "지시문·컨텍스트·입력의 구조적 격리 (Structural Delimiters & Isolation)",
        "source": "Anthropic XML Tagging & OpenAI Prompt Injection Defense",
        "question": (
            "Is the [target_prompt] clearly structured using distinct XML tags (like <role>, <rules>, <context>, "
            "<output_format>) or clean Markdown headers that isolate instructions from reference data and user input? "
            "(Yes = well-delimited sections/tags, No = flat unseparated text or bullet list)"
        ),
        "fix_guide": (
            "역할(<role>), 핵심 규칙(<rules>), 참조 데이터(<context>), 출력 형식(<output_format>)을 XML 태그나 명확한 마크다운 헤더로 "
            "분리하여 지시문과 데이터 간 혼동(Instruction-Data Confusion)을 차단하세요."
        ),
    },
    {
        "id": "output_schema_rigor",
        "category": "4. 구조적 경계 & 출력 스키마 (Structure & Schema)",
        "name": "출력 형식·스키마·경계 조건의 엄밀성 (Rigorous Output Specification)",
        "source": "Google Gemini & OpenAI Structured Output Guides",
        "question": (
            "Does the [target_prompt] define the expected output format, structure, length bounds, or schema "
            "with objective, verifiable precision? (Yes = rigorous output specification, No = vague or contradictory output spec)"
        ),
        "fix_guide": (
            "출력의 구조(문단 수, 문장 길이 상한, 필수 포함/제외 항목, 또는 JSON/Markdown 스키마와 타입)를 모호함 없이 정의하세요."
        ),
    },
    {
        "id": "edge_case_and_abstention",
        "category": "5. 예외 처리 & 환각 방어 (Edge-Case & Epistemic Safety)",
        "name": "엣지 케이스 & 환각 방어(Abstention) 규칙 (Edge-Case & Anti-Hallucination Fallback)",
        "source": "Anthropic Epistemic Humility & Grounding Guidelines",
        "question": (
            "Beyond the happy path, does the [target_prompt] explicitly instruct the model on how to handle "
            "ambiguous, missing, out-of-scope, or unverified information (e.g., abstaining or stating uncertainty "
            "rather than guessing/hallucinating)? (Yes = explicit edge-case/abstention rule, No = missing)"
        ),
        "fix_guide": (
            "입력이 모호하거나 주어진 컨텍스트에 근거가 없을 때 억지로 추측(Hallucination)하지 않고 "
            "'확인 가능한 정보가 부족합니다'라고 답하거나 확인을 요청하는 예외 처리(Fallback) 수칙을 명시하세요."
        ),
    },
    {
        "id": "anti_sycophancy_guardrail",
        "category": "5. 예외 처리 & 환각 방어 (Edge-Case & Epistemic Safety)",
        "name": "무조건적 동조(Sycophancy) 및 거짓 전제 방어 (Anti-Sycophancy Guardrail)",
        "source": "LLM Sycophancy & Premise Validation Research",
        "question": (
            "Does the [target_prompt] avoid demanding blind sycophancy ('always agree 100% with the user') and "
            "ensure the model maintains factual objectivity when the user's premise is wrong? "
            "(Yes = objective & safe, No = demands unconditional agreement or lacks guardrail)"
        ),
        "fix_guide": (
            "'무조건 맞장구쳐라/100% 동의해라' 식의 지시는 사용자의 잘못된 사실 전제까지 추종하게 만듭니다. "
            "'감정에는 공감하되 사실 오류가 있거나 위험한 전제에는 정중히 사실을 바로잡는다'는 가드레일을 두세요."
        ),
    },
    {
        "id": "concrete_testable_language",
        "category": "6. 명확성, 주의력 배치 & 매체 적합성 (Clarity & Attention)",
        "name": "모호한 추상어 배제 & 검증 가능한 지시 (Concrete, Testable Language)",
        "source": "Google Gemini & OpenAI Determinism Guidelines",
        "question": (
            "Does the [target_prompt] rely on concrete, observable, and testable behavioral/structural constraints "
            "(such as sentence counts, character limits, or explicit step sequences) rather than relying solely on "
            "vague filler adverbs like '자연스럽게', '센스있게', '적당히', '알아서 잘'? "
            "(Yes = concrete & testable criteria, No = relies on vague filler adverbs)"
        ),
        "fix_guide": (
            "'자연스럽게/센스있게/적당히/잘' 같은 주관적 부사를 삭제하고, "
            "구체적인 행동 지표(예: '2~3문장 이내, 전문 용어 사용 시 괄호 안 쉬운 풀이 병기')로 바꾸세요."
        ),
    },
    {
        "id": "attention_budget_and_persona_diet",
        "category": "6. 명확성, 주의력 배치 & 매체 적합성 (Clarity & Attention)",
        "name": "주의력 희석(Lost in the Middle) & 과잉 페르소나 방지 (Attention Budget & Persona Diet)",
        "source": "Lost-in-the-Middle & Instruction Attenuation Research",
        "question": (
            "Is the [target_prompt] free from unnecessary theatrical persona backstory (e.g., childhood dreams, "
            "fictional nicknames) and organized cleanly so critical constraints are not diluted? "
            "(Yes = lean persona & organized attention budget, No = theatrical backstory bloat or ungrouped wall)"
        ),
        "fix_guide": (
            "작업 수행과 무관한 연극적 배경 설정(어린 시절 꿈, 가상의 별명 등 Persona Theatre)을 덜어내고, "
            "규칙들을 핵심 카테고리로 묶어 가장 중요한 제약을 상단/하단에 배치하세요."
        ),
    },
    {
        "id": "modality_and_reasoning_fit",
        "category": "6. 명확성, 주의력 배치 & 매체 적합성 (Clarity & Attention)",
        "name": "매체 호흡 & 추론 단계 적합성 (Modality & Reasoning Fit)",
        "source": "Anthropic Extended Thinking & Medium-Specific Prompting",
        "question": (
            "Do the workflow steps, output format, and length/style constraints in [target_prompt] coherently "
            "match the target execution medium (such as CLI/IDE agent execution, structured report, or chat) "
            "and provide clear step-by-step decision sequencing? "
            "(Yes = coherent medium fit & explicit decision steps, No = mismatched medium or missing steps)"
        ),
        "fix_guide": (
            "타겟 채널(메신저 단문 대화 vs 공식 보고서)의 실제 호흡에 맞게 문장 길이·어조 제약을 맞추고, "
            "복합 판단이 필요한 작업이라면 답변 생성 전 거칠 판단 순서(Step-by-step 기준)를 명시하세요."
        ),
    },
]


class PromptAuditor:
    """Sub-250ms Universal LLM Prompt Mistake Auditor powered by TypeSafe Jev (12 Orthogonal Criteria)."""

    def __init__(self):
        self.api_key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY") or ""
        self.model = os.environ.get("TYPESAFE_MODEL", "jev-latest")
        self.endpoint = "https://api.typesafe.ai/v1/systemone"


    @staticmethod
    def _scan_companion_code_overfitting(target_key: Optional[str]) -> List[str]:
        """If auditing a SKILL.md file, scan sibling engine/*.py & scripts/*.py for hardcoded domain dictionaries, symbol blacklists, or benchmark-specific strings."""
        if not target_key:
            return []
        p = Path(target_key)
        if not p.is_file() or p.name.upper() != "SKILL.MD":
            return []
        skill_dir = p.parent
        warnings: List[str] = []
        overfit_var_re = re.compile(
            r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:SYNONYM|TRIVIAL|LOW_SIGNAL|BLACKLIST|SKIP_HEADING|HARDCODED|wants_auto|wants_cases)[A-Za-z0-9_]*)\s*[:=]",
            re.MULTILINE,
        )
        overfit_lit_re = re.compile(
            r"['\"](?:auto-insurance|material-purchase|industrial-accident|formProfileId|hira-claim-core|MediOnEMR|/case-)['\"]"
        )
        for py_file in sorted(skill_dir.rglob("*.py")):
            try:
                code = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in overfit_var_re.finditer(code):
                warnings.append(f"{py_file.relative_to(skill_dir)}:{m.group(1)}")
            for m in overfit_lit_re.finditer(code):
                warnings.append(f"{py_file.relative_to(skill_dir)}:{m.group(0)}")
        return warnings

    @staticmethod
    def _extract_rules(prompt_text: str) -> List[Dict[str, Any]]:
        """Extract behavioral rules (skipping code blocks and non-rule reference/schema tags)."""
        rules = []
        in_code_block = False
        in_ignored_tag = False
        ignored_open = re.compile(r"^<(output_format|cli_reference|audit_criteria_12|examples|priority_hierarchy|reasoning_steps)>", re.IGNORECASE)
        ignored_close = re.compile(r"^</(output_format|cli_reference|audit_criteria_12|examples|priority_hierarchy|reasoning_steps)>", re.IGNORECASE)
        for idx, raw_line in enumerate(prompt_text.splitlines(), start=1):
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            if ignored_open.match(stripped):
                in_ignored_tag = True
                continue
            if ignored_close.match(stripped):
                in_ignored_tag = False
                continue
            if in_ignored_tag or not stripped:
                continue
            if re.match(r"^([-*•]|\d+[.)])\s+", stripped) and len(stripped) >= 12 and not stripped.endswith(":"):
                rules.append({"line": idx, "text": stripped})
        return rules

    @staticmethod
    def _analyze_static_signals(prompt_text: str, rules: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Sub-0.5ms deterministic static regex analysis for supplementary hard metrics."""
        lines = prompt_text.splitlines()
        neg_pattern = re.compile(
            r"(절대\s*[^\s]*|하지\s*마|하지\s*말\s*것|금지|삼가|피하세요|불가|"
            r"\bnever\b|\bdo\s+not\b|\bdon't\b|\bprohibit|\bforbid|\bmust\s+not\b)",
            re.IGNORECASE,
        )
        pos_fallback_pattern = re.compile(
            r"(대신|경우에는\s*[^\s]+하|~할\s*때는|\binstead\b|\brather\b|\bwhen\b.*\bdo\b)",
            re.IGNORECASE,
        )
        vague_pattern = re.compile(
            r"(자연스럽게|센스\s*있게|적절히|적절하게|적당히|성의\s*있게|성의껏|전문가답게|친절하고\s*자연스럽게|알아서\s*잘|"
            r"\bnaturally\b|\bappropriately\b|\bproperly\b|\bbe\s+professional\b|\bwrite\s+well\b)",
            re.IGNORECASE,
        )
        xml_or_headers = re.findall(r"(<[a-zA-Z0-9_-]+>|^#{1,4}\s+.+)", prompt_text, flags=re.MULTILINE)

        prompt_without_meta = re.sub(r"<audit_criteria_12>[\s\S]*?</audit_criteria_12>", "", prompt_text)
        neg_matches = neg_pattern.findall(prompt_without_meta)
        pos_matches = pos_fallback_pattern.findall(prompt_text)
        # Exclude meta-examples where vague words are quoted as examples of what NOT to use
        scrubbed_for_vague = re.sub(r"['\"`][^'\"`\n]{1,50}['\"`]\s*(같은|등의|식의|류의|대신)", "", prompt_text)
        vague_matches = sorted(set(m.group(0) for m in vague_pattern.finditer(scrubbed_for_vague)))

        return {
            "char_count": len(prompt_text),
            "line_count": len(lines),
            "extracted_rule_count": len(rules),
            "negative_marker_count": len(neg_matches),
            "positive_fallback_marker_count": len(pos_matches),
            "vague_expressions_found": vague_matches,
            "structural_section_count": len(xml_or_headers),
        }

    def audit(
        self,
        prompt_text: str,
        failed_case: Optional[str] = None,
        task_goal: Optional[str] = None,
        pass_threshold: float = 0.80,
        target_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Audits a prompt across 12 orthogonal prompt-engineering quality dimensions + per-rule checks
        in a single batched TypeSafe Jev call (~200-260ms).
        """
        if not self.api_key:
            raise ValueError("TYPESAFE_API_KEY is not set in environment or .env")

        t0 = time.perf_counter()
        rules = self._extract_rules(prompt_text)
        static_signals = self._analyze_static_signals(prompt_text, rules)
        companion_overfit = self._scan_companion_code_overfitting(target_key)
        static_signals["companion_code_overfit_vars"] = companion_overfit

        state_payload: Dict[str, Any] = {
            "target_prompt": prompt_text.strip(),
            "static_signals": static_signals,
        }
        if task_goal:
            state_payload["intended_task_goal"] = task_goal.strip()

        questions: Dict[str, Dict[str, str]] = {}
        for spec in CRITERIA_SPECS:
            questions[spec["id"]] = {
                "type": "noul",
                "instructions": spec["question"],
            }

        if failed_case:
            fc_snip = failed_case.strip()[:300]
            questions["no_failed_case_leak"] = {
                "type": "noul",
                "instructions": (
                    f"Is the [target_prompt] abstracted into universal principles rather than copy-pasting "
                    f"specific test case entities, numbers, or verbatim benchmark sentences from this failed case: \"{fc_snip}\"? "
                    f"(Yes = abstracted into general principles, No = verbatim test literals leaked)"
                ),
            }

        capped_rules = rules[:15]
        for i, r in enumerate(capped_rules):
            r_text = r["text"][:140]
            questions[f"rule_{i}_general"] = {
                "type": "noul",
                "instructions": (
                    f"Evaluate rule #{i+1} in the context of [target_prompt]: \"{r_text}\". "
                    f"Is this rule a generalizable, domain-agnostic operational rule for this tool/agent "
                    f"(note: referencing the tool's own CLI flags/JSON schema is valid) rather than a narrow band-aid "
                    f"hardcoding a specific external repository, domain business term, or failed test literal? "
                    f"(Yes = generalizable operational rule, No = overfitted to a specific test/repo)"
                ),
            }
            questions[f"rule_{i}_constructive"] = {
                "type": "noul",
                "instructions": (
                    f"Evaluate rule #{i+1}: \"{r_text}\". Is this rule phrased with concrete, testable criteria "
                    f"(and if it prohibits something, does it provide a clear positive alternative instead of a bare '절대 ~하지 마' or vague '잘/센스있게')? "
                    f"(Yes = constructive & concrete, No = bare prohibition or vague)"
                ),
            }

        body = {
            "model": self.model,
            "state": state_payload,
            "questions": questions,
        }

        answers: Dict[str, Any] = {}
        last_err = None
        for attempt in range(3):
            try:
                resp = CLIENT.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                )
                if not resp.is_error:
                    answers = resp.json().get("answers", {})
                    break
                last_err = RuntimeError(f"TypeSafe Jev returned {resp.status_code}: {resp.text}")
            except Exception as e:
                last_err = e
            time.sleep(0.1 * (attempt + 1))

        if not answers and last_err is not None:
            raise RuntimeError(f"Jev API call failed: {last_err}")

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

        dimension_results: List[Dict[str, Any]] = []
        mistakes_detected: List[Dict[str, Any]] = []

        for spec in CRITERIA_SPECS:
            cid = spec["id"]
            score = round(float(answers.get(cid, {}).get("noul", 0.5)), 2)

            # Combine with deterministic static signals where appropriate
            if cid == "overfitting_and_bandaids" and static_signals.get("companion_code_overfit_vars"):
                score = min(score, 0.40)
            if cid == "literal_leakage_and_fewshot_bias" and failed_case:
                generic_terms = {
                    "그리고", "또는", "대신", "바로", "읽기", "위치", "코드", "파일", "함수",
                    "핵심", "모듈", "호출", "진입", "오류", "상수", "문자열", "관계", "목록",
                }
                fc_tokens = [
                    t for t in re.findall(r"[a-zA-Z0-9_가-힣]{3,}", failed_case.lower())
                    if t not in generic_terms
                ]
                fc_bigrams = [
                    f"{w1} {w2}"
                    for w1, w2 in zip(
                        re.findall(r"[a-zA-Z0-9_가-힣]{2,}", failed_case.lower()),
                        re.findall(r"[a-zA-Z0-9_가-힣]{2,}", failed_case.lower())[1:],
                    )
                ]
                prompt_low = prompt_text.lower()
                matched_fc = {t for t in fc_tokens if t in prompt_low}
                matched_bg = {bg for bg in fc_bigrams if bg in prompt_low}
                if len(matched_fc) >= 3 or len(matched_bg) >= 2:
                    leak_score = round(float(answers.get("no_failed_case_leak", {}).get("noul", 0.5)), 2)
                    det_penalty = round(max(0.35, 0.85 - (len(matched_fc) * 0.08 + len(matched_bg) * 0.12)), 2)
                    score = min(score, leak_score, det_penalty)
            elif cid == "concrete_testable_language" and static_signals["vague_expressions_found"]:
                score = min(score, 0.35)
            elif cid == "structural_delimiter_isolation" and static_signals["structural_section_count"] == 0:
                score = min(score, 0.40)

            passed = score >= pass_threshold
            dim_entry = {
                "id": cid,
                "category": spec["category"],
                "name": spec["name"],
                "source": spec["source"],
                "score": score,
                "passed": passed,
                "fix_guide": spec["fix_guide"] if not passed else "",
            }
            dimension_results.append(dim_entry)
            if not passed:
                mistakes_detected.append(
                    {
                        "id": cid,
                        "category": spec["category"],
                        "name": spec["name"],
                        "score": score,
                        "source": spec["source"],
                        "fix": spec["fix_guide"],
                    }
                )

        flagged_rules: List[Dict[str, Any]] = []
        for i, r in enumerate(capped_rules):
            gen_score = round(float(answers.get(f"rule_{i}_general", {}).get("noul", 0.5)), 2)
            con_score = round(float(answers.get(f"rule_{i}_constructive", {}).get("noul", 0.5)), 2)
            issues = []
            if gen_score < pass_threshold:
                issues.append(f"사후 표적 기소 / 1회성 땜질 의심 (Generalizability: {gen_score:.2f})")
            if con_score < pass_threshold:
                issues.append(f"대안 없는 단순 금지어 또는 모호한 수식어 (Constructiveness: {con_score:.2f})")
            if issues:
                flagged_rules.append(
                    {
                        "line": r["line"],
                        "rule": r["text"],
                        "general_score": gen_score,
                        "constructive_score": con_score,
                        "issues": issues,
                    }
                )

        avg_score = round(
            (sum(d["score"] for d in dimension_results) / max(len(dimension_results), 1)) * 100
        )
        passed_count = sum(1 for d in dimension_results if d["passed"])
        total_dims = len(dimension_results)

        status_str = "PASS" if (passed_count == total_dims and not flagged_rules) else "NEEDS_REVISION"
        ram_delta = None
        if _SHM is not None:
            shm_key = f"audit:prev:{target_key or 'inline'}"
            prev = _SHM.get(shm_key)
            current_failed_ids = [d["id"] for d in dimension_results if not d["passed"]]
            if isinstance(prev, dict):
                prev_failed = set(prev.get("failed_ids", []))
                curr_failed = set(current_failed_ids)
                ram_delta = {
                    "prev_score": prev.get("health_score", 0),
                    "score_diff": avg_score - prev.get("health_score", 0),
                    "prev_passed": prev.get("passed_dimensions", ""),
                    "prev_flagged_rules": prev.get("flagged_count", 0),
                    "resolved_dimensions": sorted(list(prev_failed - curr_failed)),
                    "regressed_dimensions": sorted(list(curr_failed - prev_failed)),
                }
            _SHM.set(
                shm_key,
                {
                    "health_score": avg_score,
                    "passed_dimensions": f"{passed_count}/{total_dims}",
                    "failed_ids": current_failed_ids,
                    "flagged_count": len(flagged_rules),
                },
            )

        return {
            "status": status_str,
            "health_score": avg_score,
            "passed_dimensions": f"{passed_count}/{total_dims}",
            "latency_ms": elapsed_ms,
            "ram_delta": ram_delta,
            "static_signals": static_signals,
            "dimensions": dimension_results,
            "mistakes_detected": mistakes_detected,
            "flagged_rules": flagged_rules,
        }
