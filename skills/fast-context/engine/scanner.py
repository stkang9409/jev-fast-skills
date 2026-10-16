"""Fast repository file scanner with Stage 0 bilingual IDF pre-filtering and SQL/method extraction."""

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
}

CODE_EXTENSIONS: Set[str] = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".cts",
    ".mts",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".cs",
    ".sh",
    ".sql",
}

DOC_EXTENSIONS: Set[str] = {
    ".md",
}

TEST_PATTERNS = re.compile(
    r"(?:^|/)(?:tests?|__tests__|specs?|e2e|cypress|playwright)(?:/|$)"
    r"|\.(?:test|spec|e2e)\.[a-z0-9]+$",
    re.IGNORECASE,
)

# Bilingual domain synonym dictionary so Korean intents match English symbol/file/SQL names and vice versa
# High-specificity bilingual domain compound mapping (never generic JS verbs like create/read/load/check)
KO_EN_SYNONYMS: Dict[str, List[str]] = {
    "인계": ["handoff", "ddmd-handoff"],
    "야간": ["nightsurcharge", "nightdetails", "js010"],
    "야간가산": ["nightsurcharge", "assertnightsurchargetimes", "js010"],
    "사용장려비": ["usageincentive", "assertusageincentiveprices", "js002"],
    "사용장려금": ["usageincentive", "assertusageincentiveprices", "js002"],
    "사전점검": ["preflight", "catalog-evidence"],
    "결과": ["hira-result", "hiraresult", "hira_result_file"],
    "가져오기": ["importddmdresultfiles", "hira-result-files"],
    "운영": ["operational-claim", "operation-sam", "batch-sam"],
    "노쇼": ["noshow", "no-show"],
    "폴링": ["polling"],
}

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
    r"(?:INSERT\s+INTO|UPDATE|FROM|JOIN)\s+([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)",
    re.IGNORECASE,
)


def extract_intent_keywords(task_intent: str) -> List[str]:
    """Extract clean search tokens from task_intent and expand with bilingual synonyms."""
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
        # Bilingual expansion
        if t in KO_EN_SYNONYMS:
            for syn in KO_EN_SYNONYMS[t]:
                add_kw(syn.lower())
        else:
            for ko_key, syns in KO_EN_SYNONYMS.items():
                if len(ko_key) >= 2 and ko_key in t:
                    add_kw(ko_key)
                    for syn in syns:
                        add_kw(syn.lower())
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
    max_candidates: int = 32,
) -> Dict[str, str]:
    """
    Generate compact file summaries and perform Stage 0 fast lexical pre-filtering.
    Extracts top-matching functions, class methods, IPC channels, and SQL tables.
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
        matched_kws: List[str] = []
        if keywords:
            for kw in keywords:
                in_path = kw in rel_lower
                in_body = kw in raw_lower
                if in_path:
                    lex_score += 3.5
                    matched_kws.append(kw)
                elif in_body:
                    lex_score += 1.2
                    matched_kws.append(kw)

        # Extract symbols and rank them by relevance to keywords + length (avoiding tiny top-of-file helpers)
        raw_syms = []
        seen_s: Set[str] = set()
        for m in SYMBOL_RE.finditer(raw_head):
            sym = next((g for g in m.groups() if g), None)
            if sym and sym not in seen_s and sym not in {"if", "for", "while", "switch", "catch", "function", "return"}:
                seen_s.add(sym)
                s_low = sym.lower()
                s_hits = sum(2.0 for kw in keywords if kw in s_low)
                # Prefer descriptive symbols over tiny 3-char helpers
                raw_syms.append((s_hits + min(len(sym), 25) * 0.05, sym))

        raw_syms.sort(key=lambda x: x[0], reverse=True)
        syms = [s for _, s in raw_syms[:14]]

        # Also extract SQL table names if present (e.g. claim.runtime_claim_hira_result_files)
        sql_tables = []
        seen_t: Set[str] = set()
        for m in SQL_TABLE_RE.finditer(raw_head):
            tbl = m.group(1)
            if tbl not in seen_t:
                seen_t.add(tbl)
                sql_tables.append(tbl)
                t_low = tbl.lower()
                if any(kw in t_low for kw in keywords):
                    lex_score += 2.5

        lines_out = []
        for line in raw_head.splitlines():
            s = line.strip()
            if s and not s.startswith(("//", "/*", "*", "import ", "from ")):
                lines_out.append(s[:90])
                if len(lines_out) >= 4:
                    break

        parts = []
        if matched_kws:
            parts.append(f"KeywordHits({len(matched_kws)}): {', '.join(matched_kws[:8])}")
        if syms:
            parts.append(f"Symbols: {', '.join(syms[:12])}")
        if sql_tables:
            parts.append(f"SQLTables: {', '.join(sql_tables[:6])}")
        if lines_out:
            parts.append(" | ".join(lines_out[:3]))

        summary_str = " ; ".join(parts)[:300]
        scored_entries.append((lex_score, is_doc, rel_path, summary_str))

    if len(scored_entries) <= max_candidates or not keywords:
        return {rel: summ for _, _, rel, summ in scored_entries}

    docs = [e for e in scored_entries if e[1] and e[0] > 0]
    codes = [e for e in scored_entries if not e[1]]

    docs.sort(key=lambda x: x[0], reverse=True)
    codes.sort(key=lambda x: x[0], reverse=True)

    selected_docs = docs[:5]
    remaining_slots = max(1, max_candidates - len(selected_docs))
    selected_codes = codes[:remaining_slots]

    combined = selected_codes + selected_docs
    combined.sort(key=lambda x: x[0], reverse=True)
    return {rel: summ for _, _, rel, summ in combined}
