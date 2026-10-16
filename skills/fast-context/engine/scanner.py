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
    "생성", "결과", "검증", "조사", "가져오기", "가져", "오기", "시간", "시간대", "규칙", "흐름",
}

GENERIC_PATH_TOKENS: Set[str] = {
    "src", "lib", "app", "packages", "electron", "core", "common", "shared", "utils",
    "helpers", "components", "pages", "api", "server", "client", "docs", "features",
    "manual", "operations", "db", "repositories", "services", "service", "adapter",
    "adapters", "index", "main", "runtime", "types", "models", "controllers",
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


def extract_intent_keywords(task_intent: str, path_hints: Optional[List[str]] = None) -> List[str]:
    """
    Domain-agnostic tokenizer: extracts alphanumeric/CJK tokens, incorporates specific
    directory tokens from `path_hints`, and splits 4+ char Korean compound nouns (excluding verbs).
    """
    raw_tokens = re.findall(r"[a-zA-Z0-9_가-힣.-]{2,}", task_intent.lower())
    seen: Set[str] = set()
    keywords: List[str] = []

    def add_kw(k: str):
        if k and len(k) >= 2 and k not in STOPWORDS and k not in GENERIC_PATH_TOKENS and k not in seen:
            seen.add(k)
            keywords.append(k)

    for t in raw_tokens:
        add_kw(t)
        for sub in re.split(r"[-_./]", t):
            add_kw(sub)
        if re.fullmatch(r"[가-힣]{4,}", t) and not t.endswith(("하기", "오기", "주기", "보기", "되기")):
            add_kw(t[:2])
            add_kw(t[2:])

    if path_hints:
        for ph in path_hints:
            for part in re.split(r"[-_./\\]", ph.lower()):
                if len(part) >= 3 and part not in GENERIC_PATH_TOKENS and part not in STOPWORDS:
                    add_kw(part)
                    if part.endswith("s") and len(part) >= 5:
                        add_kw(part[:-1])

    return keywords


def extract_domain_ascii_keywords(task_intent: str, path_hints: Optional[List[str]] = None, selected_files: Optional[Set[str]] = None) -> List[str]:
    """Extract specific ASCII/numeric domain tokens (e.g. claims, ddmd, sam, 091, hira) from intent and path hints."""
    kws = extract_intent_keywords(task_intent, path_hints=path_hints)
    ascii_kws: List[str] = []
    seen: Set[str] = set()

    def add_akw(k: str):
        if k and len(k) >= 3 and re.fullmatch(r"[a-z0-9]+", k) and k not in GENERIC_PATH_TOKENS and k not in STOPWORDS and k not in seen:
            seen.add(k)
            ascii_kws.append(k)
            if k.endswith("s") and len(k) >= 5 and k[:-1] not in seen:
                seen.add(k[:-1])
                ascii_kws.append(k[:-1])
            elif not k.endswith("s") and len(k) >= 4 and (k + "s") not in seen:
                seen.add(k + "s")
                ascii_kws.append(k + "s")

    for k in kws:
        for sub in re.split(r"[-_./]", k):
            add_akw(sub)

    if not ascii_kws and selected_files:
        token_counts: Dict[str, int] = {}
        for sf in selected_files:
            if sf.endswith(".md"):
                continue
            for part in re.split(r"[-_./\\]", Path(sf).stem.lower()):
                if len(part) >= 4 and re.fullmatch(r"[a-z0-9]+", part) and part not in GENERIC_PATH_TOKENS:
                    token_counts[part] = token_counts.get(part, 0) + 1
        for tok, cnt in sorted(token_counts.items(), key=lambda x: x[1], reverse=True)[:3]:
            if cnt >= 2:
                add_akw(tok)

    return ascii_kws


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


def _kw_in_text(kw: str, text_lower: str) -> bool:
    if kw not in text_lower:
        return False
    if re.fullmatch(r"[a-z0-9_.-]+", kw):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text_lower))
    return True


def get_file_summaries(
    files: List[Path],
    base_dir: Path,
    task_intent: str = "",
    max_candidates: int = 36,
    path_hints: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Generate compact, domain-agnostic file summaries (Doc + Symbols + SQL + Snippets).
    For broad directories (>50 files), strictly pre-filters to files matching task/path keywords.
    """
    root = base_dir.resolve()
    keywords = extract_intent_keywords(task_intent, path_hints=path_hints) if (task_intent or path_hints) else []
    domain_kws = extract_domain_ascii_keywords(task_intent, path_hints=path_hints)
    is_broad_dir = len(files) > 50
    wants_cases = "case" in task_intent.lower() or "사례" in task_intent.lower()
    wants_auto = any(w in task_intent.lower() for w in ("auto", "자보", "자동차", "산재"))

    # Pass 1: Fast lexical & path pre-filter before heavy regex extraction
    pre_candidates: List[Tuple[float, int, bool, bool, Path, str, str]] = []
    max_hit_cnt = 0

    for f in files:
        try:
            rel_path = str(f.relative_to(root)).replace(chr(92), "/")
        except ValueError:
            rel_path = str(f)

        rel_lower = rel_path.lower()
        is_doc = f.suffix.lower() in DOC_EXTENSIONS
        if is_doc and not wants_cases and "/case-" in rel_lower:
            continue
        if not wants_auto and ("auto-insurance" in rel_lower or "material-purchase" in rel_lower or "industrial-accident" in rel_lower):
            continue

        # In broad directories (>50 files like electron/ or docs/), skip reading disk if path doesn't match domain keywords when domain_kws exist
        if is_broad_dir and not is_doc and domain_kws:
            if not any(_kw_in_text(dk, rel_lower) for dk in domain_kws):
                continue

        try:
            with f.open("r", encoding="utf-8", errors="ignore") as fp:
                raw_head = fp.read(64_000)
        except Exception:
            continue

        raw_lower = raw_head.lower()
        lex_score = 0.0
        hit_cnt = 0
        path_hit = False
        if keywords:
            for kw in keywords:
                if _kw_in_text(kw, rel_lower):
                    lex_score += 3.5
                    hit_cnt += 1
                    path_hit = True
                elif _kw_in_text(kw, raw_lower):
                    lex_score += 1.0
                    hit_cnt += 1

        if hit_cnt > max_hit_cnt:
            max_hit_cnt = hit_cnt
        pre_candidates.append((lex_score, hit_cnt, path_hit, is_doc, f, rel_path, raw_head))

    if is_broad_dir and keywords and max_hit_cnt > 0:
        min_hits_required = max(2, int(max_hit_cnt * 0.55))
        filtered_pre = [
            item for item in pre_candidates
            if (item[2] and item[1] >= 2) or item[1] >= min_hits_required
        ]
        if filtered_pre:
            pre_candidates = filtered_pre
        pre_candidates.sort(key=lambda x: (x[2], x[0], x[1]), reverse=True)
        pre_candidates = pre_candidates[: min(max_candidates, 14)]
    else:
        pre_candidates.sort(key=lambda x: (x[2], x[0], x[1]), reverse=True)
        pre_candidates = pre_candidates[:max_candidates]

    scored_entries: List[Tuple[float, bool, str, str]] = []

    for lex_score, _, _, is_doc, f, rel_path, raw_head in pre_candidates:
        header_comments: List[str] = []
        for line in raw_head.splitlines()[:25]:
            s = line.strip()
            if s.startswith(("//", "/*", "*", "#")):
                c = re.sub(r"^[/\*#\s]+|[\*/\s]+$", "", s).strip()
                if len(c) >= 4 and not c.startswith(("use strict", "eslint", "@ts-")):
                    header_comments.append(c)
                    if len(" ".join(header_comments)) >= 140:
                        break

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
                l_hits = sum(1 for kw in keywords if _kw_in_text(kw, s_low))
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

    selected_docs = docs[:4]
    remaining_slots = max(1, max_candidates - len(selected_docs))
    selected_codes = codes[:remaining_slots]

    combined = selected_codes + selected_docs
    combined.sort(key=lambda x: x[0], reverse=True)
    return {rel: summ for _, _, rel, summ in combined}
