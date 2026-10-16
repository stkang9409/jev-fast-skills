"""Repository file scanner for fast-context with IDF lexical pre-filtering & IPC/symbol previews."""

import math
import os
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple

IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".idea", ".vscode", "dist", "build", "out", "coverage", ".next",
    ".system_generated", "scratch", ".evals", ".context", ".claude", "fixtures", "__snapshots__"
}

IGNORED_EXTENSIONS = {
    ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".mp3", ".mp4",
    ".mov", ".avi", ".lock", ".bin", ".wasm", ".dylib", ".so", ".dll",
    ".json", ".jsonl", ".csv", ".tsv", ".sql", ".map", ".snap", ".log", ".ttf", ".woff", ".woff2"
}

MAX_FILE_BYTES = 180_000  # Skip huge generated/seed files > 180KB

SYMBOL_RE = re.compile(
    r"(?:^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:def|class|function|interface|type)\s+([A-Za-z0-9_$]+)"
    r"|^(?:const|let)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>"
    r"|(?:exports|module\.exports)\.([A-Za-z0-9_$]+)\s*="
    r"|ipcMain\.(?:handle|on)\(\s*['\"]([^'\"]+)['\"]"
    r"|ipcRenderer\.(?:invoke|send)\(\s*['\"]([^'\"]+)['\"])",
    re.MULTILINE,
)

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "how", "what", "where",
    "when", "find", "show", "file", "files", "code", "function", "method", "class",
    "line", "lines", "range", "skill", "skills", "fast", "engine", "scripts", "md",
    "works", "work", "implementation", "component",
    "관련", "함수", "메서드", "코드", "라인", "범위", "찾아", "분석", "에서", "스킬", "구현", "확인", "구조",
}


def extract_intent_keywords(task_intent: str) -> List[str]:
    """Extract meaningful technical keywords/identifiers from task_intent."""
    if not task_intent:
        return []
    tokens = re.findall(r"[A-Za-z0-9_가-힣.:/-]{2,}", task_intent)
    seen = []
    for t in tokens:
        tl = t.lower().strip(".-_:/")
        if len(tl) >= 2 and tl not in STOPWORDS and tl not in seen:
            seen.append(tl)
    return seen


def _is_test_or_fixture_path(rel_path: str) -> bool:
    rl = rel_path.lower()
    return any(
        tok in rl
        for tok in (".test.", ".spec.", "__tests__/", "/test/", "/tests/", "/fixtures/", "/mocks/", "test_")
    )


def get_repo_files(base_dir: Optional[Path] = None, include_tests: bool = False) -> List[Path]:
    """Return candidate source & architecture doc files in the repository."""
    root = (base_dir or Path.cwd()).resolve()
    files: List[Path] = []

    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        for line in res.stdout.splitlines():
            rel = line.strip()
            if not rel:
                continue
            p = root / rel
            if p.suffix.lower() in IGNORED_EXTENSIONS:
                continue
            if any(part in IGNORED_DIRS for part in Path(rel).parts):
                continue
            if not include_tests and _is_test_or_fixture_path(rel):
                continue
            if p.is_file():
                try:
                    if p.stat().st_size <= MAX_FILE_BYTES:
                        files.append(p)
                except Exception:
                    pass
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
            p = Path(dirpath) / f
            try:
                rel = str(p.relative_to(root))
            except ValueError:
                rel = str(p)
            if not include_tests and _is_test_or_fixture_path(rel):
                continue
            try:
                if p.stat().st_size <= MAX_FILE_BYTES:
                    matched.append(p)
            except Exception:
                pass
    return matched


def get_file_summaries(
    files: List[Path],
    base_dir: Optional[Path] = None,
    max_preview_lines: int = 5,
    task_intent: str = "",
    max_candidates: int = 28,
) -> Dict[str, str]:
    """
    Generate compact file summaries and perform Stage 0 IDF-weighted lexical pre-filtering
    so large monorepos (500~2,000+ files) never send more than `max_candidates` files to Jev HTTP calls.
    """
    root = (base_dir or Path.cwd()).resolve()
    keywords = extract_intent_keywords(task_intent)
    intent_lower = task_intent.lower()
    raw_records: List[Tuple[str, str, List[str], List[str]]] = []
    doc_freq: Dict[str, int] = {kw: 0 for kw in keywords}

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
                if s and not s.startswith(("import ", "from ", "export ", "'use ", "const path =", "const fs =")):
                    header_lines.append(s.lstrip("#/* \"'")[:80])
                if len(header_lines) >= 2:
                    break

            sym_matches = SYMBOL_RE.findall(content)
            all_syms = []
            for tup in sym_matches:
                val = next((x for x in tup if x), None)
                if val and val not in all_syms:
                    all_syms.append(val)
            # Prioritize symbols matching intent keywords so deep functions (L400+) surface in Stage 1 summary
            if keywords:
                matched_syms = [s for s in all_syms if any(kw in s.lower() for kw in keywords)]
                unmatched_syms = [s for s in all_syms if s not in matched_syms]
                symbols = (matched_syms + unmatched_syms)[:12]
            else:
                symbols = all_syms[:12]

            hits = []
            if keywords:
                content_lower = content.lower()
                rel_lower = rel.lower()
                for kw in keywords:
                    if kw in rel_lower or kw in content_lower:
                        hits.append(kw)
                        doc_freq[kw] = doc_freq.get(kw, 0) + 1

            raw_records.append((rel, " | ".join(header_lines), symbols, hits))
        except Exception:
            raw_records.append((rel, "", [], []))

    # Stage 0 IDF ranking when len(raw_records) > max_candidates
    total_docs = max(len(raw_records), 1)
    scored_records: List[Tuple[float, str, str]] = []

    for rel, header_str, symbols, hits in raw_records:
        sym_part = f"Symbols: {', '.join(symbols)}" if symbols else ""
        hit_part = f"KeywordHits({len(hits)}): {', '.join(hits[:8])}" if hits else ""
        parts = [p for p in [hit_part, sym_part, header_str] if p]
        summary_str = " ; ".join(parts)[:240] if parts else "Source file"

        # Compute local IDF lexical score
        lex_score = 0.0
        rel_lower = rel.lower()
        stem = Path(rel).stem.lower()
        fname = Path(rel).name.lower()

        if fname not in {"index.ts", "index.js", "index.cjs", "page.tsx", "readme.md"}:
            if fname in intent_lower or (len(stem) >= 4 and stem in intent_lower):
                lex_score += 5.0

        for kw in hits:
            df = max(doc_freq.get(kw, 1), 1)
            idf = math.log(1.0 + (total_docs / df))
            if kw in rel_lower:
                lex_score += 2.2 * idf
            elif any(kw in s.lower() for s in symbols):
                lex_score += 1.8 * idf
            else:
                lex_score += 1.0 * idf

        # Boost architecture/manual markdown docs that contain hits
        if rel_lower.endswith(".md") and hits and ("docs/" in rel_lower or "manual/" in rel_lower or "architecture" in rel_lower):
            lex_score += 2.5

        scored_records.append((lex_score, rel, summary_str))

    scored_records.sort(key=lambda x: x[0], reverse=True)
    if len(scored_records) > max_candidates:
        doc_records = [r for r in scored_records if r[1].lower().endswith((".md", ".mdx"))]
        code_records = [r for r in scored_records if not r[1].lower().endswith((".md", ".mdx"))]
        max_docs = min(4, len(doc_records))
        max_code = max_candidates - max_docs
        selected = code_records[:max_code] + doc_records[:max_docs]
        selected.sort(key=lambda x: x[0], reverse=True)
        scored_records = selected

    return {rel: summary_str for _, rel, summary_str in scored_records}
