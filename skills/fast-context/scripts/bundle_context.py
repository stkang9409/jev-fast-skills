import os
#!/usr/bin/env python3
"""CLI entry point for fast-context semantic bundler (Zero-Worktree-Pollution edition)."""

import argparse
import json
import re
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from engine.scanner import get_repo_files, get_file_summaries
from engine.parser import parse_file, CACHE_STATS
from engine.scorer import score_files, score_symbols
from engine.bundler import build_bundle

DEFAULT_TMP_DIR = Path("/tmp/fast-context")


def slugify_intent(intent: str, max_len: int = 60) -> str:
    cleaned = re.sub(r"[^\w\s가-힣-]", "-", intent)
    slug = re.sub(r"[\s_-]+", "-", cleaned).strip("-")
    if not slug:
        return "bundle"
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug


def main():
    parser = argparse.ArgumentParser(
        description="TypeSafe Jev-powered fast semantic context bundler"
    )
    parser.add_argument("task_intent", type=str, help="Task description or goal")
    parser.add_argument("--path", type=str, default=".", help="Target directory path to scan (prefer specific subdirectories in large repos)")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output markdown file path (default: /tmp/fast-context/bundle.md to keep git worktrees 100%% clean)",
    )
    parser.add_argument(
        "--file-threshold",
        type=float,
        default=0.45,
        help="Relevance threshold for stage 1 file filtering (0.0 - 1.0)",
    )
    parser.add_argument(
        "--symbol-threshold",
        type=float,
        default=0.42,
        help="Relevance threshold for stage 2 symbol filtering (0.0 - 1.0)",
    )
    parser.add_argument("--include-tests", action="store_true", help="Include test/spec files in scan")
    parser.add_argument("--json", action="store_true", help="Print summary JSON to stdout")
    parser.add_argument("--print-bundle", action="store_true", help="Print compact bundle to stdout")
    args = parser.parse_args()

    start_time = time.perf_counter()
    raw_paths = [p.strip() for p in args.path.split(",") if p.strip()]
    resolved_paths = [Path(p).resolve() for p in raw_paths]
    root_path = Path(os.path.commonpath([str(p) for p in resolved_paths])) if len(resolved_paths) > 1 else resolved_paths[0]
    if root_path.is_file():
        root_path = root_path.parent
    include_tests = args.include_tests or any(w in args.task_intent.lower() for w in ("test", "spec", "테스트"))

    # Step 1: Scan files across target path(s) + Stage 0 Stratified IDF Pre-Filter
    candidate_files = []
    for rp in resolved_paths:
        candidate_files.extend(get_repo_files(rp, include_tests=include_tests))
    if not candidate_files:
        print(f"[fast-context] No valid source files found in {root_path}", file=sys.stderr)
        sys.exit(1)

    file_summaries = get_file_summaries(
        candidate_files,
        root_path,
        task_intent=args.task_intent,
        max_candidates=28,
    )

    # Step 2: Stage 1 Scoring (Files, capped at top 10)
    t_f0 = time.perf_counter()
    scored_files = score_files(args.task_intent, file_summaries, threshold=args.file_threshold, max_files=10)
    t_f1 = time.perf_counter()

    if not scored_files:
        print(f"[fast-context] No files met threshold {args.file_threshold}", file=sys.stderr)
        sys.exit(1)

    # Step 3: Parse symbols in top-scoring files (RAM AST Cache backed)
    all_blocks = []
    for rel_path, _ in scored_files:
        fpath = root_path / rel_path
        all_blocks.extend(parse_file(fpath))

    # Step 4: Stage 2 Scoring (Symbols, capped at top 18 blocks)
    t_s0 = time.perf_counter()
    scored_symbols = score_symbols(args.task_intent, all_blocks, threshold=args.symbol_threshold, max_blocks=18)
    t_s1 = time.perf_counter()

    elapsed_ms = round((time.perf_counter() - start_time) * 1000)

    # Step 5: Build Bundle (strictly budgeted to <= 24KB / ~550 lines so 1 view_file call reads 100% of it)
    bundle_md = build_bundle(
        task_intent=args.task_intent,
        scored_blocks=scored_symbols,
        base_dir=root_path,
        total_files_scanned=len(candidate_files),
        elapsed_ms=elapsed_ms,
        compact_stdout=True,
        max_stdout_chars=24000,
    )

    # Save outside git worktree by default (/tmp/fast-context/bundle.md) so git status is never dirtied!
    if args.output:
        out_path = Path(args.output) if Path(args.output).is_absolute() else (root_path / args.output)
        bundle_alias_path = None
    else:
        DEFAULT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        slug = slugify_intent(args.task_intent)
        out_path = DEFAULT_TMP_DIR / f"{slug}.md"
        bundle_alias_path = DEFAULT_TMP_DIR / "bundle.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(bundle_md, encoding="utf-8")
    if bundle_alias_path and bundle_alias_path != out_path:
        bundle_alias_path.write_text(bundle_md, encoding="utf-8")

    bundle_line_count = len(bundle_md.splitlines())

    if args.json:
        result = {
            "task_intent": args.task_intent,
            "total_files_scanned": len(candidate_files),
            "stage0_candidates": len(file_summaries),
            "files_selected": [{"file": f, "score": round(s, 3)} for f, s in scored_files],
            "symbols_selected_count": len(scored_symbols),
            "bundle_lines": bundle_line_count,
            "output_file": str(out_path),
            "bundle_alias": str(bundle_alias_path) if bundle_alias_path else None,
            "elapsed_ms": elapsed_ms,
            "file_scoring_ms": round((t_f1 - t_f0) * 1000),
            "symbol_scoring_ms": round((t_s1 - t_s0) * 1000),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.print_bundle:
        stdout_md = build_bundle(
            task_intent=args.task_intent,
            scored_blocks=scored_symbols,
            base_dir=root_path,
            total_files_scanned=len(candidate_files),
            elapsed_ms=elapsed_ms,
            compact_stdout=True,
            max_stdout_chars=7000,
        )
        print(stdout_md)
    else:
        print(
            f"✅ [fast-context] Bundle created in {elapsed_ms}ms! "
            f"({len(candidate_files)} scanned -> {len(file_summaries)} pre-filtered -> {len(scored_files)} files -> "
            f"{len(scored_symbols)} blocks, {bundle_line_count} lines | "
            f"RAM AST Cache: {CACHE_STATS['hits']} hits, {CACHE_STATS['misses']} parsed)"
        )
        print(f"📄 Saved to: {bundle_alias_path or out_path} (Zero worktree files modified)")


if __name__ == "__main__":
    main()
