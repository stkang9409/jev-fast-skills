#!/usr/bin/env python3
"""CLI entry point for fast-context semantic index hint & bundler (Zero-Worktree-Pollution edition)."""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Tuple, Dict, Set

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from engine.scanner import get_repo_files, get_file_summaries, find_git_root
from engine.parser import parse_file, CACHE_STATS
from engine.scorer import score_files, score_symbols
from engine.bundler import build_bundle, build_hint

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
        description="TypeSafe Jev-powered fast semantic index-hint & context locator"
    )
    parser.add_argument("task_intent", type=str, help="Task description or goal")
    parser.add_argument(
        "--path",
        type=str,
        default=".",
        help="Comma-separated target directories to scan (e.g. 'electron/claims,packages/hira-claim-core/src')",
    )
    parser.add_argument(
        "--repo-root",
        type=str,
        default=None,
        help="Optional explicit repository root path for relative directory grouping",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional markdown file path to save full bundle (default: /tmp/fast-context/bundle.md)",
    )
    parser.add_argument(
        "--file-threshold",
        type=float,
        default=0.60,
        help="Relevance threshold for stage 1 file filtering (default: 0.60)",
    )
    parser.add_argument(
        "--symbol-threshold",
        type=float,
        default=0.60,
        help="Relevance threshold for stage 2 symbol filtering (default: 0.60)",
    )
    parser.add_argument("--include-tests", action="store_true", help="Include test/spec files in scan")
    parser.add_argument("--no-docs", action="store_true", help="Disable automatic docs/features & docs/manual discovery")
    parser.add_argument("--hint", action="store_true", help="Print compact Quick Index hint only (no code body)")
    parser.add_argument("--links", action="store_true", help="Include clickable file:// links in hint output")
    parser.add_argument("--json", action="store_true", help="Print summary JSON to stdout")
    parser.add_argument("--print-bundle", action="store_true", help="Print full code bundle to stdout")
    args = parser.parse_args()

    start_time = time.perf_counter()
    raw_paths = [p.strip() for p in args.path.split(",") if p.strip()]

    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
        resolved_paths = [(repo_root / p).resolve() if not Path(p).is_absolute() else Path(p).resolve() for p in raw_paths]
    else:
        resolved_paths = [Path(p).resolve() for p in raw_paths]
        git_root = find_git_root(resolved_paths[0]) if resolved_paths else None
        if git_root:
            repo_root = git_root
        else:
            repo_root = (
                Path(os.path.commonpath([str(p) for p in resolved_paths]))
                if len(resolved_paths) > 1
                else resolved_paths[0]
            )
            if repo_root.is_file():
                repo_root = repo_root.parent

    include_tests = args.include_tests or any(w in args.task_intent.lower() for w in ("test", "spec", "테스트"))

    # Build directory groups and deduplicate overlapping subdirectories (e.g. `electron/claims` inside `electron`)
    dir_groups: List[Tuple[str, Path]] = []
    for rp in resolved_paths:
        if not rp.exists():
            continue
        try:
            label = str(rp.relative_to(repo_root))
            if label == ".":
                label = rp.name
        except ValueError:
            label = rp.name
        dir_groups.append((label, rp))

    # Automatically include docs/features and docs/manual if scanning subdirectories of a repo with docs/
    auto_doc_dirs: List[Path] = []
    if not args.no_docs and repo_root.is_dir():
        dp = (repo_root / "docs").resolve()
        if dp.is_dir() and not any(dp == gp or gp in dp.parents for _, gp in dir_groups):
            auto_doc_dirs.append(dp)
            dir_groups.append(("docs", dp))

    # Stage 0: Scan each target directory with fair per-directory candidate quota and child-dir exclusion
    all_candidate_files: List[Path] = []
    combined_summaries: Dict[str, str] = {}
    seen_files: Set[Path] = set()

    # Sort more specific (deeper) directories first so `electron/claims` claims its files before `electron`
    ordered_scan_dirs = sorted(
        [(label, rp) for label, rp in dir_groups if label != "docs"],
        key=lambda x: len(x[1].parts),
        reverse=True,
    )

    per_dir_quota = max(60, 140 // max(1, len(ordered_scan_dirs)))
    for label, rp in ordered_scan_dirs:
        child_dirs = {other_p for _, other_p in ordered_scan_dirs if other_p != rp and rp in other_p.parents}
        d_files = get_repo_files(
            rp,
            include_tests=include_tests,
            include_docs=(len(ordered_scan_dirs) == 1 and rp == repo_root),
            exclude_Subdirs=child_dirs,
        )
        unique_d_files = [f for f in d_files if f.resolve() not in seen_files]
        for f in unique_d_files:
            seen_files.add(f.resolve())
        all_candidate_files.extend(unique_d_files)
        d_summaries = get_file_summaries(
            unique_d_files,
            repo_root,
            task_intent=args.task_intent,
            max_candidates=per_dir_quota,
        )
        combined_summaries.update(d_summaries)

    # Also scan auto_doc_dirs for high-matching feature/manual markdown docs
    if auto_doc_dirs:
        doc_files: List[Path] = []
        for dp in auto_doc_dirs:
            doc_files.extend(get_repo_files(dp, include_tests=False, include_docs=True))
        all_candidate_files.extend(doc_files)
        doc_summaries = get_file_summaries(
            doc_files,
            repo_root,
            task_intent=args.task_intent,
            max_candidates=16,
        )
        combined_summaries.update(doc_summaries)

    if not combined_summaries:
        print(f"[fast-context] No valid source files found in {repo_root}", file=sys.stderr)
        sys.exit(1)

    # Stage 1: Score candidate files across all directories in parallel (wide recall: up to 24 files)
    t_f0 = time.perf_counter()
    max_files_limit = 24 if (args.hint or not args.print_bundle) else 12
    scored_files = score_files(
        args.task_intent,
        combined_summaries,
        threshold=args.file_threshold,
        max_files=max_files_limit,
    )
    t_f1 = time.perf_counter()

    if not scored_files:
        print(f"[fast-context] No files met threshold {args.file_threshold}", file=sys.stderr)
        sys.exit(1)

    # Stage 2: Parse symbols in selected files (RAM SharedMemory AST Cache backed)
    all_blocks = []
    for rel_path, _ in scored_files:
        fpath = repo_root / rel_path
        all_blocks.extend(parse_file(fpath))

    t_s0 = time.perf_counter()
    max_blocks_limit = 75 if (args.hint or not args.print_bundle) else 22
    scored_symbols = score_symbols(
        args.task_intent,
        all_blocks,
        threshold=args.symbol_threshold,
        max_blocks=max_blocks_limit,
        max_per_file=4,
    )
    t_s1 = time.perf_counter()

    elapsed_ms = round((time.perf_counter() - start_time) * 1000)

    # Build both Hint (index-only) and Bundle (if requested via --output or --print-bundle)
    hint_text = build_hint(
        scored_blocks=scored_symbols,
        repo_root=repo_root,
        dir_groups=dir_groups if len(dir_groups) > 1 else None,
        include_links=args.links,
    )

    bundle_md = build_bundle(
        task_intent=args.task_intent,
        scored_blocks=scored_symbols,
        base_dir=repo_root,
        total_files_scanned=len(all_candidate_files),
        elapsed_ms=elapsed_ms,
        compact_stdout=True,
        max_stdout_chars=24000,
    )

    if args.output:
        out_path = Path(args.output) if Path(args.output).is_absolute() else (repo_root / args.output)
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
            "total_files_scanned": len(all_candidate_files),
            "stage0_candidates": len(combined_summaries),
            "files_selected": [{"file": f, "score": round(s, 3)} for f, s in scored_files],
            "symbols_selected_count": len(scored_symbols),
            "hint_lines": len(hint_text.splitlines()),
            "bundle_lines": bundle_line_count,
            "output_file": str(out_path),
            "bundle_alias": str(bundle_alias_path) if bundle_alias_path else None,
            "elapsed_ms": elapsed_ms,
            "file_scoring_ms": round((t_f1 - t_f0) * 1000),
            "symbol_scoring_ms": round((t_s1 - t_s0) * 1000),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.hint:
        print(hint_text)
    elif args.print_bundle:
        print(bundle_md)
    else:
        # Default stdout output: print the compact Quick Index Hint directly so 1 tool call gives all locations!
        print(
            f"# ⚡ fast-context Index Hint ({elapsed_ms}ms | {len(all_candidate_files)} scanned -> "
            f"{len(scored_files)} files, {len(scored_symbols)} symbols | "
            f"RAM Cache: {CACHE_STATS['hits']} hits, {CACHE_STATS['misses']} parsed)"
        )
        print(hint_text)


if __name__ == "__main__":
    main()
