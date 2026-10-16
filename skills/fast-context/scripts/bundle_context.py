#!/usr/bin/env python3
"""CLI entry point for fast-context semantic bundler."""

import argparse
import json
import re
import sys
import time
from pathlib import Path

# Add skill root to path
SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from engine.scanner import get_repo_files, get_file_summaries
from engine.parser import parse_file, CACHE_STATS
from engine.scorer import score_files, score_symbols
from engine.bundler import build_bundle


def slugify_intent(intent: str, max_len: int = 60) -> str:
    """Generate a clean, readable filename slug from task intent."""
    # Replace special characters except korean, alphanumeric, space, hyphen, underscore
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
    parser.add_argument("--path", type=str, default=".", help="Root repository path to scan")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output markdown file path (default: .context/<intent-slug>.md)",
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
        default=0.40,
        help="Relevance threshold for stage 2 symbol filtering (0.0 - 1.0)",
    )
    parser.add_argument("--json", action="store_true", help="Print summary JSON to stdout")
    parser.add_argument("--print-bundle", action="store_true", help="Print entire bundle to stdout")
    args = parser.parse_args()

    start_time = time.perf_counter()
    root_path = Path(args.path).resolve()

    # Step 1: Scan files
    candidate_files = get_repo_files(root_path)
    if not candidate_files:
        print(f"[fast-context] No valid source files found in {root_path}", file=sys.stderr)
        sys.exit(1)

    file_summaries = get_file_summaries(candidate_files, root_path, task_intent=args.task_intent)

    # Step 2: Stage 1 Scoring (Files)
    t_f0 = time.perf_counter()
    scored_files = score_files(args.task_intent, file_summaries, threshold=args.file_threshold)
    t_f1 = time.perf_counter()

    if not scored_files:
        print(f"[fast-context] No files met threshold {args.file_threshold}", file=sys.stderr)
        sys.exit(1)

    # Step 3: Parse symbols in high-scoring files
    all_blocks = []
    for rel_path, file_score in scored_files:
        fpath = root_path / rel_path
        blocks = parse_file(fpath)
        all_blocks.extend(blocks)

    # Step 4: Stage 2 Scoring (Symbols/Signatures)
    t_s0 = time.perf_counter()
    scored_symbols = score_symbols(args.task_intent, all_blocks, threshold=args.symbol_threshold)
    t_s1 = time.perf_counter()

    elapsed_ms = round((time.perf_counter() - start_time) * 1000)

    # Step 5: Build Bundle
    bundle_md = build_bundle(
        task_intent=args.task_intent,
        scored_blocks=scored_symbols,
        base_dir=root_path,
        total_files_scanned=len(candidate_files),
        elapsed_ms=elapsed_ms,
    )

    # Determine output file path
    if args.output:
        out_path = root_path / args.output
        rel_out = args.output
        bundle_alias_path = None
    else:
        slug = slugify_intent(args.task_intent)
        rel_out = f".context/{slug}.md"
        out_path = root_path / rel_out
        bundle_alias_path = root_path / ".context" / "bundle.md"

    # Save to output file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(bundle_md, encoding="utf-8")

    # Keep .context/bundle.md updated as a convenient alias
    if bundle_alias_path and bundle_alias_path != out_path:
        bundle_alias_path.write_text(bundle_md, encoding="utf-8")

    if args.json:
        result = {
            "task_intent": args.task_intent,
            "total_files_scanned": len(candidate_files),
            "files_selected": [
                {"file": f, "score": round(s, 3)} for f, s in scored_files
            ],
            "symbols_selected_count": len(scored_symbols),
            "output_file": str(rel_out),
            "bundle_alias": ".context/bundle.md" if bundle_alias_path else None,
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
            f"({len(candidate_files)} scanned -> {len(scored_files)} files -> {len(scored_symbols)} blocks | RAM AST Cache: {CACHE_STATS['hits']} hits, {CACHE_STATS['misses']} parsed)"
        )
        print(f"📄 Saved to: {rel_out}")
        if bundle_alias_path:
            print(f"🔗 Alias updated: .context/bundle.md")


if __name__ == "__main__":
    main()
