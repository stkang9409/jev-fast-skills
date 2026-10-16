"""Repomix-style Markdown bundler for semantic code blocks."""

import time
from pathlib import Path
from typing import List, Tuple, Dict, Any

from .parser import CodeBlock


def build_bundle(
    task_intent: str,
    scored_blocks: List[Tuple[CodeBlock, float]],
    base_dir: Path,
    total_files_scanned: int,
    elapsed_ms: int,
    compact_stdout: bool = False,
    max_stdout_chars: int = 7000,
) -> str:
    """Format filtered code blocks into a single consolidated Repomix-style Markdown document.
    When compact_stdout=True, budgets total output to <= max_stdout_chars so Antigravity's
    run_command (~8KB tail window) never truncates the top Quick Index or highest-scored blocks.
    """
    root = base_dir.resolve()

    # If compact_stdout is enabled, select top-scoring blocks that fit within max_stdout_chars
    active_blocks = scored_blocks
    omitted_blocks: List[Tuple[CodeBlock, float]] = []
    if compact_stdout and scored_blocks:
        # Sort by descending score first to prioritize highest relevance within stdout budget
        sorted_by_score = sorted(scored_blocks, key=lambda x: x[1], reverse=True)
        selected = []
        est_chars = 900  # Reserve space for header + Quick Index + footer
        for blk, sc in sorted_by_score:
            blk_lines = blk.content.splitlines()
            # Cap extremely long single blocks in stdout mode to 65 lines so multiple top blocks fit
            preview_lines = blk_lines[:65] if len(blk_lines) > 70 else blk_lines
            blk_cost = sum(len(l) + 10 for l in preview_lines) + 180
            if est_chars + blk_cost <= max_stdout_chars or not selected:
                selected.append((blk, sc))
                est_chars += blk_cost
            else:
                omitted_blocks.append((blk, sc))
        active_blocks = selected

    # Group active blocks by file
    blocks_by_file: Dict[str, List[Tuple[CodeBlock, float]]] = {}
    for block, score in active_blocks:
        try:
            rel_path = str(block.file_path.relative_to(root))
        except ValueError:
            rel_path = str(block.file_path)
        blocks_by_file.setdefault(rel_path, []).append((block, score))

    # Also build full index across ALL scored_blocks so Quick Index is always 100% complete
    all_by_file: Dict[str, List[Tuple[CodeBlock, float]]] = {}
    for block, score in scored_blocks:
        try:
            rel_path = str(block.file_path.relative_to(root))
        except ValueError:
            rel_path = str(block.file_path)
        all_by_file.setdefault(rel_path, []).append((block, score))
    for rel_path in all_by_file:
        all_by_file[rel_path].sort(key=lambda x: x[0].start_line)

    # Sort blocks within each file by start_line
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
        elif suffix in {"js", "jsx", "ts", "tsx", "mjs"}:
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
                    f" ... | [+{len(block_lines) - 65} more lines up to L{block.end_line} in .context/bundle.md]"
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

    if compact_stdout and omitted_blocks:
        lines.append(
            f"> 💡 **Stdout Budget Note**: Top {len(active_blocks)} highest-scoring blocks shown inline above "
            f"(zero truncation). All {len(scored_blocks)} full blocks are saved in `.context/bundle.md` "
            f"(use `view_file` on `.context/bundle.md` if you need the remaining {len(omitted_blocks)} lower-scoring blocks)."
        )

    return "\n".join(lines)
