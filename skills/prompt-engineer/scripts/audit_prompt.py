import sys
import os
import json
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from engine.auditor import PromptAuditor


def main():
    parser = argparse.ArgumentParser(
        description="Universal LLM Prompt Quality & Anti-Pattern Auditor (12 Orthogonal Criteria via TypeSafe Jev)"
    )
    parser.add_argument("--file", type=str, help="Path to prompt file (.md, .txt, etc.)")
    parser.add_argument("--text", type=str, help="Inline prompt text to audit")
    parser.add_argument(
        "--failed-case",
        type=str,
        default=None,
        help="Optional failed example/test-case to check for literal keyword leakage",
    )
    parser.add_argument(
        "--goal",
        type=str,
        default=None,
        help="Optional description of the prompt's intended goal",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            prompt_text = f.read()
    elif args.text:
        prompt_text = args.text
    elif not sys.stdin.isatty():
        prompt_text = sys.stdin.read()
    else:
        parser.error("Provide --file <path>, --text '<prompt>', or pipe via stdin.")
        return

    auditor = PromptAuditor()
    target_key = os.path.abspath(args.file) if args.file else (args.goal or 'inline')
    result = auditor.audit(prompt_text, failed_case=args.failed_case, task_goal=args.goal, target_key=target_key)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(
        f"=== Prompt Engineering 12-Criteria Audit [{result['status']}] "
        f"(Score: {result['health_score']}/100 | Passed: {result['passed_dimensions']} | {result['latency_ms']}ms) ==="
    )
    if result.get("ram_delta"):
        d = result["ram_delta"]
        sign = "+" if d["score_diff"] >= 0 else ""
        print(f"[RAM Auto-Diff] Score: {d['prev_score']} -> {result['health_score']} ({sign}{d['score_diff']}) | "
              f"Passed: {d['prev_passed']} -> {result['passed_dimensions']} | "
              f"Flagged Rules: {d['prev_flagged_rules']} -> {len(result['flagged_rules'])}")
        if d["resolved_dimensions"]:
            print(f"  -> Resolved ({len(d['resolved_dimensions'])}): {', '.join(d['resolved_dimensions'])}")
        if d["regressed_dimensions"]:
            print(f"  -> Regressed ({len(d['regressed_dimensions'])}): {', '.join(d['regressed_dimensions'])}")
    sig = result["static_signals"]
    print(
        f"[Static Signals] Lines: {sig['line_count']} | Rules: {sig['extracted_rule_count']} | "
        f"Negations: {sig['negative_marker_count']} | Positive Fallbacks: {sig['positive_fallback_marker_count']} | "
        f"Sections/Tags: {sig['structural_section_count']}"
    )
    if sig["vague_expressions_found"]:
        print(f"[Vague Words Detected] {', '.join(sig['vague_expressions_found'])}")

    print("\n[12 Orthogonal Quality Criteria Breakdown]")
    current_cat = None
    for d in result["dimensions"]:
        if d["category"] != current_cat:
            current_cat = d["category"]
            print(f"\n  ■ {current_cat}")
        mark = "PASS" if d["passed"] else "FAIL"
        print(f"    - [{mark} | {d['score']:.2f}] {d['name']} ({d['source']})")
        if not d["passed"]:
            print(f"      -> 처방: {d['fix_guide']}")

    if result["flagged_rules"]:
        print("\n[Line-Level Flagged Rules (규칙별 정밀 적발)]")
        for r in result["flagged_rules"]:
            print(f"  * Line {r['line']}: {r['rule']}")
            for issue in r["issues"]:
                print(f"    -> {issue}")


if __name__ == "__main__":
    main()
