"""Fast repository file scanner with domain-agnostic structural & header summarization."""

import os
import re
import subprocess
from pathlib import Path
from typing import List, Set, Dict, Tuple, Optional

IGNORE_DIRS: Set[str] = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    "dist",
    "build",
    "out",
    "coverage",
    ".context",
    ".idea",
    ".vscode",
    ".gradle",
    "target",
    "vendor",
    ".claude",
    ".cursor",
    ".turbo",
    "artifacts",
    "fixtures",
    "__fixtures__",
    "__snapshots__",
    "snapshots",
    "migrations",
    "verification",
}

CODE_EXTENSIONS: Set[str] = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".cts", ".mts",
    ".go", ".rs", ".java", ".kt", ".swift", ".c", ".cpp", ".h", ".hpp",
    ".rb", ".php", ".cs", ".sh", ".sql",
}

DOC_EXTENSIONS: Set[str] = {".md"}

TEST_PATTERNS = re.compile(
    r"(?:^|/)(?:tests?|__tests__|specs?|e2e|cypress|playwright)(?:/|$)"
    r"|\.(?:test|spec|e2e)\.[a-z0-9]+$",
    re.IGNORECASE,
)

STOPWORDS: Set[str] = {
    "the", "and", "for", "with", "from", "that", "this", "what", "where", "when",
    "how", "why", "who", "which", "into", "onto", "upon", "about", "above", "below",
    "코드", "파일", "함수", "찾기", "탐색", "관련", "로직", "구현", "위치", "확인", "전체",
}

SYMBOL_RE = re.compile(
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|def|interface|type)\s+([a-zA-Z0-9_$]{3,})"
    r"|(?:export\s+)?(?:const|let)\s+([a-zA-Z0-9_$]{3,})\s*=\s*(?:async\s*)?(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>"
    r"|(?:module\.)?exports\.([a-zA-Z0-9_$]{3,})\s*="
    r"|(?:ipcMain\.handle|ipcMain\.on|handleIpc|ipcRenderer\.invoke)\s*\(\s*['\"]([^'\"]+)['\"]"
    r"|^\s{2,4}(?:async\s+)?([a-zA-Z][a-zA-Z0-9_$]{3,})\s*\([^;]*\)\s*\{",
    re.MULTILINE,
)

SQL_TABLE_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|FROM|JOIN)\s+([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)\b",
    re.IGNORECASE,
)


def extract_intent_keywords(task_intent: str) -> List[str]:
    """
    Domain-agnostic tokenizer: extracts alphanumeric/CJK tokens and splits
    4+ character Korean compound nouns into 2-character sub-tokens for spacing tolerance.
    """
    raw_tokens = re.findall(r"[a-zA-Z0-9_가-힣.-]{2,}", task_intent.lower())
    seen: Set[str] = set()
    keywords: List[str] = []

    def add_kw(k: str):
        if k and len(k) >= 2 and k not in STOPWORDS and k not in seen:
            seen.add(k)
            keywords.append(k)

    for t in raw_tokens:
        add_kw(t)
        for sub in re.split(r"[-_./]", t):
            add_kw(sub)
        if re.fullmatch(r"[가-힣]{4,}", t):
            add_kw(t[:2])
            add_kw(t[2:])
    return keywords


def find_git_root(path: Path) -> Optional[Path]:
    """Find the repository root containing .git or docs/."""
    curr = path.resolve()
    if curr.is_file():
        curr = curr.parent
    for parent in [curr, *curr.parents]:
        if (parent / ".git").exists() or (parent / "docs").is_dir():
            return parent
    return None


def get_repo_files(
    base_dir: Optional[Path] = None,
    include_tests: bool = False,
    include_docs: bool = True,
    exclude_Subdirs: Optional[Set[Path]] = None,
) -> List[Path]:
    """Return a list of valid source and documentation files in `base_dir`."""
    root = (base_dir or Path.cwd()).resolve()
    if root.is_file():
        return [root]

    allowed_exts = CODE_EXTENSIONS | (DOC_EXTENSIONS if include_docs else set())
    valid_files: List[Path] = []
    excl_resolved = {p.resolve() for p in (exclude_Subdirs or set())}

    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        rel_paths = proc.stdout.splitlines()
        for rel in rel_paths:
            if not rel:
                continue
            norm_rel = rel.replace(chr(92), "/")
            if not include_tests and TEST_PATTERNS.search(norm_rel):
                continue
            p = Path(rel)
            if p.suffix.lower() not in allowed_exts:
                continue
            if any(part in IGNORE_DIRS for part in p.parts):
                continue
            full_p = (root / p).resolve()
            if excl_resolved and any(
                full_p == ex or ex in full_p.parents for ex in excl_resolved
            ):
                continue
            try:
                if full_p.is_file() and 0 < full_p.stat().st_size <= 350_000:
                    valid_files.append(full_p)
            except OSError:
                continue
        if valid_files:
            return valid_files
    except Exception:
        pass

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")
        ]
        curr_dir = Path(dirpath).resolve()
        if excl_resolved and any(curr_dir == ex or ex in curr_dir.parents for ex in excl_resolved):
            dirnames[:] = []
            continue
        for fname in filenames:
            fpath = curr_dir / fname
            if fpath.suffix.lower() not in allowed_exts:
                continue
            try:
                rel_str = str(fpath.relative_to(root)).replace(chr(92), "/")
            except ValueError:
                rel_str = fname
            if not include_tests and TEST_PATTERNS.search(rel_str):
                continue
            try:
                size = fpath.stat().st_size
                if 0 < size <= 350_000:
                    valid_files.append(fpath)
            except OSError:
                continue

    return valid_files


def get_file_summaries(
    files: List[Path],
    base_dir: Path,
    task_intent: str = "",
    max_candidates: int = 140,
) -> Dict[str, str]:
    """
    Generate compact, domain-agnostic file summaries (Doc + Symbols + SQL + Snippets)
    without echoing query keywords into the summary text.
    """
    root = base_dir.resolve()
    keywords = extract_intent_keywords(task_intent) if task_intent else []

    scored_entries: List[Tuple[float, bool, str, str]] = []

    for f in files:
        try:
            rel_path = str(f.relative_to(root))
        except ValueError:
            rel_path = str(f)

        rel_lower = rel_path.lower()
        is_doc = f.suffix.lower() in DOC_EXTENSIONS

        try:
            with f.open("r", encoding="utf-8", errors="ignore") as fp:
                raw_head = fp.read(64_000)
        except Exception:
            continue

        raw_lower = raw_head.lower()

        lex_score = 0.0
        if keywords:
            for kw in keywords:
                if kw in rel_lower:
                    lex_score += 3.0
                elif kw in raw_lower:
                    lex_score += 1.0

        # Extract top-of-file header comments / module docstring (first 25 lines)
        header_comments: List[str] = []
        for line in raw_head.splitlines()[:25]:
            s = line.strip()
            if s.startswith(("//", "/*", "*", "#")):
                c = re.sub(r"^[/\*#\s]+|[\*/\s]+$", "", s).strip()
                if len(c) >= 4 and not c.startswith(("use strict", "eslint", "@ts-")):
                    header_comments.append(c)
                    if len(" ".join(header_comments)) >= 140:
                        break

        # Extract symbols: prioritize exported symbols, query-matching symbols, and bottom-of-file main entrypoints
        all_matches = list(SYMBOL_RE.finditer(raw_head))
        total_m = max(1, len(all_matches))
        raw_syms = []
        seen_s: Set[str] = set()
        for idx_m, m in enumerate(all_matches):
            sym = next((g for g in m.groups() if g), None)
            if not sym or sym in seen_s or sym in {"if", "for", "while", "switch", "catch", "function", "return"}:
                continue
            seen_s.add(sym)
            match_text = m.group(0)
            is_exp = match_text.lstrip().startswith(("export", "module.exports", "exports."))
            s_low = sym.lower()
            s_hits = sum(2.5 for kw in keywords if kw in s_low)
            pos_bonus = (idx_m / total_m) * 0.8
            exp_bonus = 1.5 if is_exp else 0.0
            raw_syms.append((s_hits + exp_bonus + pos_bonus + min(len(sym), 25) * 0.03, sym))

        raw_syms.sort(key=lambda x: x[0], reverse=True)
        syms = [s for _, s in raw_syms[:10]]

        sql_tables = []
        seen_t: Set[str] = set()
        for m in SQL_TABLE_RE.finditer(raw_head):
            tbl = m.group(1)
            if tbl not in seen_t:
                seen_t.add(tbl)
                sql_tables.append(tbl)
                if any(kw in tbl.lower() for kw in keywords):
                    lex_score += 2.0

        matched_snippets = []
        if keywords:
            line_candidates = []
            for line in raw_head.splitlines():
                s = line.strip()
                if len(s) < 6 or s.startswith(("import ", "from ")):
                    continue
                s_low = s.lower()
                l_hits = sum(1 for kw in keywords if kw in s_low)
                if l_hits > 0:
                    line_candidates.append((l_hits, s[:85]))
            line_candidates.sort(key=lambda x: x[0], reverse=True)
            for _, snip in line_candidates[:2]:
                if snip not in matched_snippets:
                    matched_snippets.append(snip)

        parts = []
        if header_comments:
            parts.append("Doc: " + " ".join(header_comments)[:140])
        if syms:
            parts.append("Symbols: " + ", ".join(syms[:10]))
        if sql_tables:
            parts.append("SQL: " + ", ".join(sql_tables[:4]))
        if matched_snippets:
            parts.append("Snippets: " + " | ".join(matched_snippets))

        summary_str = " ; ".join(parts)[:280]
        scored_entries.append((lex_score, is_doc, rel_path, summary_str))

    scored_entries.sort(key=lambda x: x[0], reverse=True)
    if len(scored_entries) <= max_candidates or not keywords:
        return {rel: summ for _, _, rel, summ in scored_entries}

    docs = [e for e in scored_entries if e[1] and e[0] > 0]
    codes = [e for e in scored_entries if not e[1]]
    if not codes:
        return {rel: summ for _, _, rel, summ in docs[:max_candidates]}

    selected_docs = docs[:6]
    remaining_slots = max(1, max_candidates - len(selected_docs))
    selected_codes = codes[:remaining_slots]

    combined = selected_codes + selected_docs
    combined.sort(key=lambda x: x[0], reverse=True)
    return {rel: summ for _, _, rel, summ in combined}
