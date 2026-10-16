"""Quick Index Hint and Repomix-style Markdown bundler for semantic code blocks."""

from pathlib import Path
from typing import List, Tuple, Dict, Optional

from .parser import CodeBlock
from .enricher import build_enriched_metadata, promote_call_graph_neighbors


def build_hint(
    scored_blocks: List[Tuple[CodeBlock, float]],
    repo_root: Path,
    dir_groups: Optional[List[Tuple[str, Path]]] = None,
    include_links: bool = False,
) -> str:
    """
    Build a rich Structural Quick Index Hint:
    - Per-file symbols + line ranges + callers (`[← file:Lline]`) + key literals/error codes
    - Per-directory Module Map for remaining modules
    - Entry Chains (IPC / API → Handler → Preload Bridge → UI)
    - 1-Hop DB / Repository Links & SQL tables
    """
    root = repo_root.resolve()
    scored_blocks = promote_call_graph_neighbors(root, dir_groups, scored_blocks)
    enriched = build_enriched_metadata(root, dir_groups, scored_blocks)
    sym_callers = enriched.get("symbol_callers", {})
    file_literals = enriched.get("file_literals", {})
    dir_module_maps = enriched.get("dir_module_maps", {})
    entry_chains = enriched.get("entry_chains", [])
    db_links = enriched.get("db_links", [])

    by_file: Dict[Path, List[Tuple[CodeBlock, float]]] = {}
    file_max_score: Dict[Path, float] = {}
    for blk, sc in scored_blocks:
        fp = blk.file_path.resolve()
        by_file.setdefault(fp, []).append((blk, sc))
        if sc > file_max_score.get(fp, 0.0):
            file_max_score[fp] = sc

    for fp in by_file:
        by_file[fp].sort(key=lambda x: x[0].start_line)

    def format_file_line(fp: Path, items: List[Tuple[CodeBlock, float]]) -> str:
        try:
            rel_path = str(fp.relative_to(root)).replace(chr(92), "/")
        except ValueError:
            rel_path = str(fp)
        sym_parts = []
        for b, _ in items:
            callers = sym_callers.get((rel_path, b.name))
            caller_tag = f" [← {', '.join(callers)}]" if callers else ""
            sym_parts.append(f"`{b.name}` L{b.start_line}-L{b.end_line}{caller_tag}")
        syms_str = ", ".join(sym_parts)
        lit_tag = f" {file_literals[rel_path]}" if rel_path in file_literals else ""
        if include_links:
            return f"- [{rel_path}](file://{fp}): {syms_str}{lit_tag}"
        return f"- {rel_path}: {syms_str}{lit_tag}"

    out_lines: List[str] = []

    if not dir_groups:
        sorted_files = sorted(by_file.keys(), key=lambda p: file_max_score[p], reverse=True)
        for fp in sorted_files:
            out_lines.append(format_file_line(fp, by_file[fp]))
    else:
        resolved_groups = [(label, gpath.resolve()) for label, gpath in dir_groups]
        group_buckets: Dict[str, List[Path]] = {label: [] for label, _ in resolved_groups}
        unassigned: List[Path] = []

        for fp in sorted(by_file.keys(), key=lambda p: file_max_score[p], reverse=True):
            best_label = None
            best_depth = -1
            for label, gpath in resolved_groups:
                if fp == gpath or gpath in fp.parents:
                    depth = len(gpath.parts)
                    if depth > best_depth:
                        best_depth = depth
                        best_label = label
            if best_label is not None:
                group_buckets[best_label].append(fp)
            else:
                unassigned.append(fp)

        for label, _ in resolved_groups:
            fps = group_buckets.get(label, [])
            if not fps and label not in dir_module_maps:
                continue
            out_lines.append(f"## {label}")
            for fp in fps:
                out_lines.append(format_file_line(fp, by_file[fp]))
            if label in dir_module_maps:
                out_lines.append(dir_module_maps[label])

        if unassigned:
            out_lines.append("## other")
            for fp in unassigned:
                out_lines.append(format_file_line(fp, by_file[fp]))

    if entry_chains:
        out_lines.append("## Entry Chains (IPC / API → Handler → Bridge → UI)")
        out_lines.extend(entry_chains)

    if db_links:
        out_lines.append("## DB / Repository Links")
        out_lines.extend(db_links)

    return "\n".join(out_lines)


def build_bundle(
    task_intent: str,
    scored_blocks: List[Tuple[CodeBlock, float]],
    base_dir: Path,
    total_files_scanned: int,
    elapsed_ms: int,
    compact_stdout: bool = True,
    max_stdout_chars: int = 24000,
) -> str:
    """Format filtered code blocks into a single consolidated Markdown document."""
    root = base_dir.resolve()

    active_blocks = scored_blocks
    omitted_blocks: List[Tuple[CodeBlock, float]] = []
    if compact_stdout and scored_blocks:
        sorted_by_score = sorted(scored_blocks, key=lambda x: x[1], reverse=True)
        selected = []
        est_chars = 900
        for blk, sc in sorted_by_score:
            blk_lines = blk.content.splitlines()
            preview_lines = blk_lines[:65] if len(blk_lines) > 70 else blk_lines
            blk_cost = sum(len(l) + 10 for l in preview_lines) + 180
            if est_chars + blk_cost <= max_stdout_chars or not selected:
                selected.append((blk, sc))
                est_chars += blk_cost
            else:
                omitted_blocks.append((blk, sc))
        active_blocks = selected

    blocks_by_file: Dict[str, List[Tuple[CodeBlock, float]]] = {}
    for block, score in active_blocks:
        try:
            rel_path = str(block.file_path.relative_to(root))
        except ValueError:
            rel_path = str(block.file_path)
        blocks_by_file.setdefault(rel_path, []).append((block, score))

    all_by_file: Dict[str, List[Tuple[CodeBlock, float]]] = {}
    for block, score in scored_blocks:
        try:
            rel_path = str(block.file_path.relative_to(root))
        except ValueError:
            rel_path = str(block.file_path)
        all_by_file.setdefault(rel_path, []).append((block, score))
    for rel_path in all_by_file:
        all_by_file[rel_path].sort(key=lambda x: x[0].start_line)

    for rel_path in blocks_by_file:
        blocks_by_file[rel_path].sort(key=lambda x: x[0].start_line)

    lines = []
    lines.append("# 📦 Semantic Context Bundle")
    lines.append(f"> **Task Intent**: `{task_intent}`")
    lines.append(
        f"> **Metrics**: Generated in **{elapsed_ms}ms** via TypeSafe Jev | "
        f"Scanned **{total_files_scanned}** files $\\rightarrow$ "
        f"Selected **{len(all_by_file)}** files (**{len(scored_blocks)}** relevant blocks)"
    )
    lines.append("")
    lines.append("## 📑 Quick Index (All Matched Symbols & Line Ranges)")

    for rel_path, items in all_by_file.items():
        first_block = items[0][0]
        abs_path = str(first_block.file_path.resolve())
        symbols_str = ", ".join(
            f"`{b.name}` L{b.start_line}-L{b.end_line} ({round(s, 2)})" for b, s in items
        )
        lines.append(f"- [{rel_path}](file://{abs_path}): {symbols_str}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 💻 Extracted Code Context")
    lines.append("")

    for rel_path, items in blocks_by_file.items():
        first_block = items[0][0]
        abs_path = str(first_block.file_path.resolve())
        suffix = first_block.file_path.suffix.lstrip(".") or "text"
        if suffix in {"py", "pyw"}:
            lang = "python"
        elif suffix in {"js", "jsx", "ts", "tsx", "mjs", "cjs", "cts", "mts"}:
            lang = "typescript" if "ts" in suffix else "javascript"
        elif suffix in {"json", "yaml", "yml", "sh", "bash", "html", "css", "md"}:
            lang = suffix
        else:
            lang = "text"

        lines.append(f"### 📄 [{rel_path}](file://{abs_path})")
        lines.append(f"**Absolute Path**: `{abs_path}`")
        lines.append("")

        for block, score in items:
            range_link = f"[L{block.start_line}-L{block.end_line}](file://{abs_path}#L{block.start_line}-L{block.end_line})"
            lines.append(
                f"#### 🔹 `{block.name}` ({block.kind}) | Range: {range_link} | Relevance: **{round(score, 2)}**"
            )
            if block.summary:
                lines.append(f"*Summary: {block.summary}*")
            lines.append("")

            block_lines = block.content.splitlines()
            if compact_stdout and len(block_lines) > 70:
                shown_lines = block_lines[:65]
                numbered_content = [
                    f"{block.start_line + offset:4d} | {line_text}"
                    for offset, line_text in enumerate(shown_lines)
                ]
                numbered_content.append(
                    f" ... | [+{len(block_lines) - 65} more lines up to L{block.end_line}]"
                )
            else:
                numbered_content = [
                    f"{block.start_line + offset:4d} | {line_text}"
                    for offset, line_text in enumerate(block_lines)
                ]

            lines.append(f"```{lang}")
            lines.append("\n".join(numbered_content))
            lines.append("```")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)
