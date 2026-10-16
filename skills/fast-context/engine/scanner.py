"""Repository file scanner for fast-context with symbol & lexical hit previews."""

import os
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Optional

IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".idea", ".vscode", "dist", "build", ".system_generated", "scratch", ".evals", ".context"
}

IGNORED_EXTENSIONS = {
    ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".mp3", ".mp4",
    ".mov", ".avi", ".lock", ".bin", ".wasm", ".dylib", ".so", ".dll"
}

SYMBOL_RE = re.compile(
    r"^(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type)\s+([A-Za-z0-9_]+)",
    re.MULTILINE,
)

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "how", "what", "where",
    "when", "find", "show", "file", "files", "code", "function", "method", "class",
    "line", "lines", "range", "skill", "skills", "fast", "engine", "scripts", "md",
    "works", "work", "implementation", "component",
    "관련", "함수", "메서드", "코드", "라인", "범위", "찾아", "분석", "에서", "스킬", "구현",
}


def extract_intent_keywords(task_intent: str) -> List[str]:
    """Extract meaningful technical keywords/identifiers from task_intent."""
    if not task_intent:
        return []
    tokens = re.findall(r"[A-Za-z0-9_가-힣.-]{3,}", task_intent)
    seen = []
    for t in tokens:
        tl = t.lower().strip(".-_")
        if len(tl) >= 3 and tl not in STOPWORDS and tl not in seen:
            seen.append(tl)
    return seen


def get_repo_files(base_dir: Optional[Path] = None) -> List[Path]:
    """Return all source and configuration files in the repository."""
    root = (base_dir or Path.cwd()).resolve()

    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        files = []
        for line in res.stdout.splitlines():
            p = root / line.strip()
            if p.is_file() and p.suffix.lower() not in IGNORED_EXTENSIONS:
                if not any(part in IGNORED_DIRS for part in p.parts):
                    files.append(p)
        if files:
            return files
    except Exception:
        pass

    matched = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for f in filenames:
            ext = Path(f).suffix.lower()
            if ext in IGNORED_EXTENSIONS or f.startswith("."):
                continue
            matched.append(Path(dirpath) / f)
    return matched


def get_file_summaries(
    files: List[Path],
    base_dir: Optional[Path] = None,
    max_preview_lines: int = 5,
    task_intent: str = "",
) -> Dict[str, str]:
    """Generate rich, compact file summaries combining header docstring, symbol names, and intent keyword hits."""
    root = (base_dir or Path.cwd()).resolve()
    keywords = extract_intent_keywords(task_intent)
    summaries = {}

    for f in files:
        try:
            rel = str(f.relative_to(root))
        except ValueError:
            rel = str(f)

        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            raw_lines = content.splitlines()

            header_lines = []
            for line in raw_lines[:max_preview_lines]:
                s = line.strip()
                if s and not s.startswith(("import ", "from ", "export ", "'use ")):
                    header_lines.append(s.lstrip("#/* \"'")[:80])
                if len(header_lines) >= 2:
                    break

            symbols = SYMBOL_RE.findall(content)[:10]
            sym_part = f"Symbols: {', '.join(symbols)}" if symbols else ""

            hit_part = ""
            if keywords:
                content_lower = content.lower()
                hits = [
                    kw for kw in keywords
                    if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", content_lower)
                ]
                if hits:
                    hit_part = f"KeywordHits({len(hits)}): {', '.join(hits[:8])}"

            parts = [p for p in [hit_part, sym_part, " | ".join(header_lines)] if p]
            summaries[rel] = " ; ".join(parts)[:240] if parts else "Source file"
        except Exception:
            summaries[rel] = "Source file"
    return summaries
